"""The object graph, its lifecycle, and the one health answer (§1.23).

Every collaborator is constructed here and nowhere else, so the wiring is auditable in one read and
no module can reach for a peer it was not given. Three properties of this file are load-bearing:

* **``build`` does almost no I/O.** The gateway calls ``register_routes`` inside its enable path and
  treats an exception there as "this app has no routes at all" — a broken payload manifest must
  therefore degrade the app rather than remove its ``/health`` endpoint. So ``build`` reads
  ``payload/manifest.json`` and nothing else, and a manifest it cannot parse becomes a sentinel that
  every install path refuses against.
* **``start`` is bounded.** ``on_startup`` overrunning the host's budget detaches the task and makes
  the gateway refuse later lifecycle operations for this app, so reconciliation gets
  ``HOST_STARTUP_HOOK_BUDGET_SECS`` and whatever is left over continues inside the background loop.
* **The registry is keyed by app name, not by context identity.** The context object handed to
  ``on_shutdown`` is a *fresh* instance (01 §5); keying by identity would leak a live Services with an
  open SQLite handle on every disable.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import secrets
import shutil
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import constants as C
from . import security
from .activity import ActivityProjector
from .actions import HumanActionBroker, MachineActionBroker
from .advisor import AdvisorBroker
from .aidlc_reader import AidlcReader
from .consistency import ConsistencyEngine
from .engine import BUN_SOURCE_EXPLICIT, BunLocation, EngineRunner, find_bun, probe_bun_version, probe_explicit_bun
from .errors import StudioError
from .estimates import EstimateService
from .events import EventLog
from .git_observer import GitObserver
from .installer import Installer, PayloadManifest, PayloadStatus
from .leases import RepoScheduler
from .migration import MigrationService
from .plan import PlanService
from .projection import Projection
from .reconciler import Reconciler
from .repo_registry import RepoRegistry
from .sessions import HostBridge, SessionBinder, busy_reasons
from .settings import SettingsService
from .storage import Storage

_LOG = logging.getLogger("kirocrew.app.aidlc-studio")

#: Aliased rather than inlined so the pool size and the boot-id width read the same in this file as in
#: ``constants.py``, which is where §1.1 puts them.
BOOT_ID_BYTES: int = C.BOOT_ID_BYTES
SCAN_POOL_WORKERS: int = C.SCAN_POOL_WORKERS

#: ``payload/manifest.json`` relative to the app root.
PAYLOAD_DIRNAME = "payload"
MANIFEST_FILENAME = "manifest.json"

#: Digest of the sentinel manifest a failed load produces. Deliberately not a hex digest: every
#: install path compares a computed digest against this value, and a token that cannot be a sha256
#: can never accidentally match one.
_UNAVAILABLE_DIGEST = "payload-unavailable"


# --------------------------------------------------------------------------- #
# production implementations of the injectable protocols (§0.2)
# --------------------------------------------------------------------------- #


class SystemClock:
    """The real clock. The only place in the backend that reads wall time."""

    __slots__ = ()

    def now(self) -> float:
        return time.time()

    def iso(self) -> str:
        return C.iso_from_epoch(time.time())

    def monotonic(self) -> float:
        return time.monotonic()


class RandomIdFactory:
    """``a_<16 hex>``-style ids from ``secrets``.

    Unguessable rather than sequential because an ``action_id`` appears in a deep link and in a Slack
    notification: a predictable id would let anyone who saw one enumerate the others.
    """

    __slots__ = ()

    def new(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(6 if prefix == 'r' else 8)}"


# --------------------------------------------------------------------------- #
# the graph
# --------------------------------------------------------------------------- #


@dataclass
class Services:
    """Every collaborator, one per process, plus the lifecycle that owns them."""

    ctx: Any
    clock: C.Clock
    ids: C.IdFactory
    storage: Storage
    settings: SettingsService
    repos: RepoRegistry
    reader: AidlcReader
    consistency: ConsistencyEngine
    projection: Projection
    engine: EngineRunner
    scheduler: RepoScheduler
    host: HostBridge
    sessions: SessionBinder
    actions: HumanActionBroker
    machine: MachineActionBroker
    reconciler: Reconciler
    installer: Installer
    plan: PlanService
    estimates: EstimateService
    git: GitObserver
    activity: ActivityProjector
    advisor: AdvisorBroker
    notifications: Any
    events: EventLog
    migration: MigrationService
    payload: PayloadManifest
    boot_id: str
    scan_pool: concurrent.futures.ThreadPoolExecutor
    app_root: Path
    bun_path: str | None
    git_path: str | None
    #: How ``bun`` was found and where it was looked for (``engine.find_bun``), so ``/health`` can tell
    #: the user whose gateway cannot see their bun something better than "bun is not installed". Nothing
    #: was searched when the caller named the binary itself, and ``explicit`` says so rather than lying
    #: about a search that never happened.
    bun_source: str | None = BUN_SOURCE_EXPLICIT
    bun_searched: tuple[str, ...] = ()
    #: The ``bun --version`` probe, kept so ``/health`` can report the version of the binary Studio
    #: would actually use without this module owning a child process of its own.
    bun_version_probe: Callable[[str], str | None] = probe_bun_version
    #: ``None`` until ``start`` has verified the payload; a failed *load* is reported separately,
    #: because "the manifest is unreadable" and "the files no longer match it" are different faults.
    payload_status: PayloadStatus | None = None
    payload_error: str | None = None
    started_at: str | None = None
    background: list[asyncio.Task] = field(default_factory=list)
    #: Populated by ``start``; surfaced by ``/health`` so a deferred startup step is visible.
    startup_issues: tuple[str, ...] = ()
    _stopped: bool = False
    _bun_version: str | None = None
    _bun_probed: bool = False
    _git_version: str | None = None
    _git_probed: bool = False
    _bun_configured_path: str | None = None
    _bun_config_lock: Any = field(default_factory=threading.RLock, repr=False)

    # ---- construction ----------------------------------------------------- #

    @classmethod
    def build(
        cls,
        ctx: Any,
        *,
        clock: C.Clock | None = None,
        ids: C.IdFactory | None = None,
        bun_path: str | None = None,
        git_path: str | None = None,
        app_root: Path | None = None,
        bun_version: Callable[[str], str | None] | None = None,
        boot_id: str | None = None,
    ) -> "Services":
        """Wire everything. No event loop required, and no disk access beyond the payload manifest and
        the walk that locates ``bun`` (a few ``stat``s and at most one ``bun --version`` per candidate).

        That walk happens here because ``bun_path`` becomes argv[0] of every engine invocation, so it has
        to be settled once, before anything can ask the engine a question.

        ``bun_version`` is additive to §1.23: ``RepoRegistry`` takes the probe as an injected callable
        precisely so that module can be grepped clean of child processes, which means *somebody* has to
        supply it, and the container is the only place that knows both halves. Tests pass a stub so a
        preflight assertion never depends on a real bun.

        ``boot_id`` is additive for the same reason ``clock`` and ``ids`` are: it is stamped on every
        ``Delivering`` row, so a test that has to tell "this process" from "the process before the
        restart" needs to name both. Production never passes it.
        """
        clock = clock or SystemClock()
        ids = ids or RandomIdFactory()
        root = Path(app_root) if app_root is not None else Path(__file__).resolve().parents[2]
        # A PATH search alone cannot find bun under launchd (``engine.find_bun`` carries the
        # measurement), so the search is the engine's. An explicit ``bun_path`` still wins and skips it:
        # tests hand over a stand-in that must never be run with ``--version``.
        located = None if bun_path is not None else find_bun()
        bun = bun_path if located is None else located.path
        git_bin = git_path if git_path is not None else shutil.which("git")
        probe = bun_version if bun_version is not None else probe_bun_version

        manifest, payload_error = _load_manifest(root)
        boot_id = boot_id or secrets.token_hex(BOOT_ID_BYTES)
        scan_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=SCAN_POOL_WORKERS, thread_name_prefix="aidlc-studio-scan"
        )

        storage = Storage(Path(ctx.data_dir) / C.DB_FILENAME, clock=clock, ids=ids)
        host = HostBridge(clock, _LOG)
        settings = SettingsService(storage, host, clock)
        git = GitObserver(git_bin, clock)
        repos = RepoRegistry(storage, git, clock, ids, bun_version=lambda path: probe(path))
        reader = AidlcReader(clock)
        consistency = ConsistencyEngine()
        projection = Projection(reader, consistency, clock)
        activity = ActivityProjector(storage, clock)
        events = EventLog(storage, getattr(ctx, "events", None), clock)
        engine = EngineRunner(bun_path=bun, clock=clock, activity=activity)
        scheduler = RepoScheduler(
            storage, host, settings, clock, activity, boot_id=boot_id, busy_reasons=busy_reasons
        )
        sessions = SessionBinder(storage, host, projection, clock, activity)
        notifications = _build_notifications(host, storage, settings, activity, clock)
        actions = HumanActionBroker(
            storage,
            projection,
            reader,
            consistency,
            scheduler,
            sessions,
            engine,
            host,
            events,
            activity,
            notifications,
            settings,
            clock,
            ids,
            boot_id,
            scan_pool=scan_pool,
        )
        machine = MachineActionBroker(actions, activity, clock)
        installer = Installer(
            storage,
            repos,
            reader,
            engine,
            scheduler,
            activity,
            events,
            clock,
            ids,
            payload_dir=root / PAYLOAD_DIRNAME,
            manifest=manifest,
            data_dir=Path(ctx.data_dir),
            settings=settings,
            boot_id=boot_id,
        )
        estimates = EstimateService(storage, clock)
        plan = PlanService(
            reader, engine, scheduler, repos, projection, storage, estimates, activity, events, clock, ids
        )
        advisor = AdvisorBroker(
            ctx,
            storage,
            host,
            projection,
            actions,
            reader,
            activity,
            events,
            clock,
            ids,
            settings,
            scan_pool=scan_pool,
            plan=plan,
        )
        migration = MigrationService(ctx, storage, repos, clock, ids)

        services = cls(
            ctx=ctx,
            clock=clock,
            ids=ids,
            storage=storage,
            settings=settings,
            repos=repos,
            reader=reader,
            consistency=consistency,
            projection=projection,
            engine=engine,
            scheduler=scheduler,
            host=host,
            sessions=sessions,
            actions=actions,
            machine=machine,
            reconciler=None,  # type: ignore[arg-type] - assigned below; it needs the container
            installer=installer,
            plan=plan,
            estimates=estimates,
            git=git,
            activity=activity,
            advisor=advisor,
            notifications=notifications,
            events=events,
            migration=migration,
            payload=manifest,
            boot_id=boot_id,
            scan_pool=scan_pool,
            app_root=root,
            bun_path=bun,
            git_path=git_bin,
            bun_source=BUN_SOURCE_EXPLICIT if located is None else located.source,
            bun_searched=() if located is None else located.searched,
            bun_version_probe=probe,
            payload_error=payload_error,
        )
        # The reconciler spans every layer and takes the container itself (§1.13), so it is the one
        # collaborator that cannot be constructed before the container exists. The broker's back
        # reference is what `resolve("reconcile")` calls, and it is declared late for the same reason.
        services.reconciler = Reconciler(services)
        actions.reconciler = services.reconciler
        return services

    # ---- lifecycle -------------------------------------------------------- #

    async def start(self) -> None:
        """Open the store, verify the payload, attach the host, reconcile, then go to the background.

        The order is the contract's and each step is a precondition of the next: nothing may install
        before the payload is proven, nothing may be reconciled before the store is open, and the
        background loops must not start before startup reconciliation has judged the rows the previous
        process left behind — a tick racing that judgement would decide against a stale status.

        Every step after the store is best-effort: an app that cannot verify its payload or reach the
        host is *degraded* (it still serves reads and still reports why), and raising here would cost
        the user the whole app.
        """
        issues: list[str] = []
        await asyncio.to_thread(self.storage.open)
        await asyncio.to_thread(self.settings.load)
        await asyncio.to_thread(self.restore_bun_configuration)

        if self.payload_error is not None:
            issues.append("payload_manifest_unreadable")
        else:
            try:
                self.payload_status = await asyncio.to_thread(self.installer.verify_payload)
            except StudioError as exc:
                issues.append(f"payload:{exc.code}")
            else:
                if not self.payload_status.ok:
                    issues.append("payload_degraded")

        self.attach_host(_module_dashboard_state())

        try:
            report = await self.reconciler.startup()
        except Exception as exc:  # a startup that cannot reconcile must still serve /health
            _LOG.exception("aidlc-studio startup reconciliation failed")
            issues.append(f"startup_failed:{type(exc).__name__}")
        else:
            issues.extend(report.issues)

        self.background.append(
            asyncio.create_task(self.reconciler.run_forever(), name="aidlc-studio-reconciler")
        )
        self.started_at = self.clock.iso()
        self.startup_issues = tuple(dict.fromkeys(issues))
        for issue in self.startup_issues:
            with contextlib.suppress(AttributeError):
                self.ctx.health.mark_degraded(f"aidlc-studio: {issue}")

    async def stop(self) -> None:
        """Cancel the background loops, then close the store. Idempotent.

        The store is closed last: a loop cancelled mid-transaction still has to unwind through its own
        ``finally``, and closing the connection underneath it would turn a clean shutdown into a
        ``storage_error`` in the log of every disable.
        """
        if self._stopped:
            return
        self._stopped = True
        # A copy: awaiting a task lets its done-callback run, and an install task removes itself from
        # this list when it finishes — mutating the list under the loop would skip a task.
        pending = list(self.background)
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.background.clear()
        self.scan_pool.shutdown(wait=False, cancel_futures=True)
        with contextlib.suppress(Exception):
            self.storage.close()

    def attach_host(self, state: object) -> None:
        """Bind the ``DashboardState`` if there is one. Called by every request wrapper (C08).

        ``on_startup`` has no ``web.Application``, so the host arrives on the first request; until then
        Studio runs disk-only, which is a reduced mode rather than a failure. Attaching is cheap and
        idempotent, and a raising host must not cost the request.
        """
        if state is None:
            return
        try:
            self.host.attach(state)
        except Exception:  # pragma: no cover - defensive: attach is all getattr
            _LOG.debug("aidlc-studio: host attach failed", exc_info=True)

    # ---- health ----------------------------------------------------------- #

    def bun_tool(self) -> dict[str, Any]:
        """Current tool configuration; explicit probes are exposed separately from this read."""
        with self._bun_config_lock:
            return {
                "found": bool(self.bun_path), "path": self.bun_path,
                "configured_path": self._bun_configured_path,
                "version": self._bun_tool_version(), "source": self.bun_source,
                "searched": list(self.bun_searched),
            }

    def _apply_bun(self, located: BunLocation, configured_path: str | None) -> dict[str, Any]:
        self._bun_configured_path = configured_path
        self.bun_path, self.bun_source, self.bun_searched = located.path, located.source, located.searched
        self._bun_version, self._bun_probed = located.version, True
        self.engine.set_bun_path(located.path)
        self.repos.set_bun_location(located)
        return self.bun_tool()

    def configure_bun(self, path: str | None) -> dict[str, Any]:
        """Validate before persisting, then update both runtime and preflight consumers."""
        with self._bun_config_lock:
            located = find_bun(probe=self.bun_version_probe) if path is None else probe_explicit_bun(
                path, probe=self.bun_version_probe
            )
            if path is not None and not located.found:
                raise StudioError("bun_missing", "the selected bun executable could not be run",
                                  details={"path": path, "searched": list(located.searched)})
            self.storage.pref_set("runtime.bun", {"path": path})
            return self._apply_bun(located, path)

    def reprobe_bun(self) -> dict[str, Any]:
        with self._bun_config_lock:
            stored = self.storage.pref_get("runtime.bun")
            path = stored.get("path") if isinstance(stored, dict) else None
            located = find_bun(probe=self.bun_version_probe) if path is None else probe_explicit_bun(
                path, probe=self.bun_version_probe
            )
            return self._apply_bun(located, path)

    def restore_bun_configuration(self) -> None:
        """A missing configured binary degrades health, while keeping its path available to fix."""
        if self.storage.pref_get("runtime.bun") is None:
            return
        try:
            self.reprobe_bun()
        except StudioError:
            # Malformed local preferences must not prevent the app's health and settings routes.
            with self._bun_config_lock:
                self._apply_bun(BunLocation(None, None, BUN_SOURCE_EXPLICIT, ()), None)

    def health(self) -> dict:
        """The ``GET /health`` body (§2.1). Sync — the handler runs it in a worker thread.

        Sync because it reads SQLite (five counts and a ``quick_check``), and §0.6 forbids that on the
        event loop. The host reads it does perform are ``getattr``s over already-probed capability
        records, so no gateway code runs on the worker thread.
        """
        issues = list(self.startup_issues)
        integrity = "ok"
        storage_block: dict[str, Any] = {
            "path_hash": security.sha256_text(str(self.storage.path))[:16],
            "schema_version": 0,
            "integrity": "error",
            "wal": False,
        }
        counts = {
            "repos": 0,
            "intents": 0,
            "live_actions": 0,
            "execution_leases": 0,
            "admin_leases": 0,
        }
        try:
            pragmas = self.storage.pragmas()
            problems = self.storage.integrity_check()
            integrity = "ok" if not problems else "error"
            storage_block.update(
                {
                    "schema_version": int(pragmas.get("schema_version") or 0),
                    "integrity": integrity,
                    "wal": str(pragmas.get("journal_mode") or "").lower() == "wal",
                }
            )
            if problems:
                issues.append("storage_integrity")
            counts = {
                "repos": len(self.storage.select("repos")),
                # Intent directories the last scan found — not binding rows, which outlive a deleted
                # intent on purpose (they keep its archive flag and session binding for when it returns).
                "intents": self.reconciler.intents_on_disk(),
                "live_actions": len(
                    self.storage.select("actions", {"status": list(C.LIVE_ACTION_STATUS)})
                ),
                "execution_leases": len(self.storage.select("execution_leases")),
                "admin_leases": len(self.storage.select("admin_leases")),
            }
        except StudioError as exc:
            integrity = "error"
            issues.append(f"storage:{exc.code}")

        payload_ok = bool(self.payload_status.ok) if self.payload_status is not None else False
        mismatches = 0
        if self.payload_status is not None:
            mismatches = len(self.payload_status.mismatches) + len(self.payload_status.missing)
        if self.payload_error is not None and "payload_manifest_unreadable" not in issues:
            issues.append("payload_manifest_unreadable")

        if not self.host.attached():
            issues.append("host_unattached")
        if not self.bun_path:
            issues.append("bun_missing")
        if not self.git_path:
            issues.append("git_missing")

        status = "error" if integrity == "error" else ("degraded" if issues else "healthy")
        return {
            "app": C.APP_NAME,
            "version": C.APP_VERSION,
            "bundled_engine_version": self.payload.engine_version,
            "min_kirocrew_version": C.MIN_KIROCREW_VERSION,
            "boot_id": self.boot_id,
            "host_version": self.settings.versions().get("host"),
            "started_at": self.started_at,
            "status": status,
            "issues": list(dict.fromkeys(issues)),
            "storage": storage_block,
            "payload": {
                "ok": payload_ok,
                "engine_version": self.payload.engine_version,
                "file_count": self.payload.file_count,
                "mismatches": mismatches,
            },
            "host": {
                "attached": self.host.attached(),
                "capabilities": {
                    name: {"available": cap.available, "reason": cap.reason}
                    for name, cap in sorted(self.host.capabilities().items())
                },
            },
            "tools": {
                "bun": self.bun_tool(),
                "git": {
                    "found": bool(self.git_path),
                    "path": self.git_path or None,
                    "version": self._git_tool_version(),
                },
            },
            "reconciler": self.reconciler.status(),
            "counts": counts,
            "machine_lane": {"available": False, "reason": "machine_lane_unavailable"},
        }


    def _bun_tool_version(self) -> str | None:
        """``bun --version`` once per process. Called only from ``health`` (a worker thread).

        Cached because ``/health`` is polled by the dashboard: a subprocess per poll would be a
        measurable cost for a value that cannot change while the gateway runs.
        """
        if not self._bun_probed:
            self._bun_probed = True
            if self.bun_path:
                self._bun_version = self.bun_version_probe(self.bun_path)
        return self._bun_version

    def _git_tool_version(self) -> str | None:
        """``git --version`` once per process, for the same reason as ``bun``'s."""
        if not self._git_probed:
            self._git_probed = True
            if self.git_path:
                self._git_version = self.git.version()
        return self._git_version


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _load_manifest(app_root: Path) -> tuple[PayloadManifest, str | None]:
    """The bundled manifest, or a sentinel that every install path refuses against.

    A manifest this build cannot parse decides nothing about a user's repository, so it must not be
    treated as an empty-but-valid payload: the sentinel carries a digest that is not a sha256, so the
    installer's ``plan_digest`` comparison can never match and the preview routes answer
    ``payload_degraded`` instead of writing zero files and calling it an install.
    """
    path = Path(app_root) / PAYLOAD_DIRNAME / MANIFEST_FILENAME
    try:
        return PayloadManifest.load(path), None
    except StudioError as exc:
        _LOG.error("aidlc-studio: payload manifest unusable (%s): %s", exc.code, exc.message)
        sentinel = PayloadManifest(
            schema="",
            harness="",
            engine_version=C.BUNDLED_ENGINE_VERSION,
            source={},
            compatible_state_versions=(),
            stage_count=0,
            payload_digest=_UNAVAILABLE_DIGEST,
            file_count=0,
            merge_targets={},
            files=(),
        )
        return sentinel, exc.code


def _build_notifications(host: Any, storage: Any, settings: Any, activity: Any, clock: Any) -> Any:
    """The notification adapter, or an inert stand-in while ``notifications.py`` does not exist.

    Notifications are the one collaborator whose absence costs nothing but a toast: the durable record,
    the queue and the deep link all exist without it. Importing it optionally keeps the rest of the
    graph constructible, and the stand-in records the same shape so no caller needs a null check.
    """
    try:
        from . import notifications as module  # type: ignore[attr-defined]
    except ImportError:
        return _InertNotifications()
    return module.NotificationAdapter(host, storage, settings, activity, clock)


class _InertNotifications:
    """Answers the ``NotificationAdapter`` calls the broker makes, and sends nothing."""

    __slots__ = ()

    def deep_link(self, action_id: str) -> str:
        return f"{C.DEEP_LINK_BASE}?view=actions&action={action_id}"

    async def notify_action(self, card: Any) -> dict:
        return {
            "dashboard": False,
            "slack": "unavailable",
            "slack_ts": None,
            "deep_link": self.deep_link(str(C.attr(card, "action_id", ""))),
            "dedupe_hit": False,
        }

    async def notify_completion(self, summary: Any) -> dict:
        return {
            "dashboard": False,
            "slack": "unavailable",
            "slack_ts": None,
            "deep_link": C.DEEP_LINK_BASE,
            "dedupe_hit": False,
        }

    def slack_quick_action_eligible(self, card: Any) -> tuple[bool, str]:
        return (False, "host_seam_unavailable")


def _module_dashboard_state() -> object | None:
    """The gateway's ``DashboardState`` if a module-level accessor happens to expose one (C08).

    ``on_startup(ctx)`` gets no ``web.Application``, and the only module-level accessor in the host is
    in the Slack handler — which is populated only when Slack is configured. So this is a best-effort
    early attach; the authoritative one is ``request.app["state"]`` on the first request.
    """
    try:  # pragma: no cover - depends on the host's Slack configuration
        from kiro_crew.slack.handler import get_dashboard_state  # type: ignore
    except Exception:
        return None
    try:  # pragma: no cover
        return get_dashboard_state()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# process registry
# --------------------------------------------------------------------------- #

_REGISTRY: dict[str, Services] = {}


def get_or_build(ctx: Any) -> Services:
    """The one ``Services`` for this app name, building it on first use.

    ``register_routes`` and ``on_startup`` are two separate host calls with the same context; they must
    share one container or the routes would answer from a store nobody opened.
    """
    name = str(getattr(ctx, "name", "") or C.APP_NAME)
    existing = _REGISTRY.get(name)
    if existing is not None:
        return existing
    services = Services.build(ctx)
    _REGISTRY[name] = services
    return services


def discard(name: str) -> Services | None:
    """Remove and return the container for ``name``, so ``on_shutdown`` can stop it.

    Keyed by name because the context object at disable is a fresh instance (01 §5) — an identity key
    would leave the old container, and its open SQLite handle, in this dict forever.
    """
    return _REGISTRY.pop(str(name), None)


def register(services: Services) -> Services:
    """Put an externally built container in the registry (tests, dev harnesses)."""
    _REGISTRY[str(getattr(services.ctx, "name", "") or C.APP_NAME)] = services
    return services

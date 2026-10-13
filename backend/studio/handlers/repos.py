"""Repository registry and installer routes (§2.2).

* **A 2xx never means AI-DLC changed.** A transaction answers ``202`` with its id; the writing happens in a
  background task and the truth is the transaction row and its ``transaction.updated`` events, so a request
  timeout is never the user's failure mode.
* **Nothing is written without a digest the user saw.** ``POST …/transactions`` takes the ``plan_digest`` of
  its own preview, and the installer re-derives the plan and refuses ``install_conflict{preview_changed}``
  when the repository moved in between.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any, Sequence

from aiohttp import web

from .. import constants as C
from ..directories import browse_directory
from ..errors import StudioError
# The installer's own version rule ("unparseable is not older" decides whether an upgrade is offered).
from ..installer import payload_refresh_available, version_tuple
from ..leases import lease_view
from ..source_changes import intent_written_paths
from .common import (
    body_choice, intent_view, json_ok, query_bool, read_json, repo_or_404, summarize_intent, without_archived,
)

_LOG = logging.getLogger("kirocrew.app.aidlc-studio")

#: How much install history ``GET /repos/{id}`` carries (§2.2 "last 5").
DETAIL_TRANSACTIONS = 5

#: Every installer transaction; ``uninstall`` and ``rollback`` (and a receipt-driven recovery) are replayed
#: from a receipt and need no bundled payload.
TRANSACTION_KINDS = ("install", "upgrade", "recovery", "uninstall", "rollback")


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


async def list_repos(services: Any, request: web.Request) -> web.Response:
    """No git and no intent snapshots: a 64-repository list would otherwise be 64 subprocesses and hundreds
    of file reads. Install state comes from the last scan; ``POST …/rescan`` is how a user asks for fresh."""
    repos = await asyncio.to_thread(services.repos.list, include_archived=query_bool(request, "include_archived"))
    leases = await services.scheduler.list()
    bodies = [await _repo_json(services, repo, leases=leases, fresh=False) for repo in repos]
    return json_ok({"repos": bodies, "totals": {
        "repos": len(bodies),
        "unavailable": sum(1 for body in bodies if body["availability"] != "available"),
        "open_actions": sum(int(body["counts"]["open_actions"]) for body in bodies),
    }})


async def get_repo(services: Any, request: web.Request) -> web.Response:
    """Freshly observed: this is the page a user opens because something looks wrong."""
    repo = await repo_or_404(services, request.match_info["repo_id"])
    summaries, findings = await _intent_summaries(services, repo)
    body = await _repo_json(services, repo, leases=await services.scheduler.list(), fresh=True,
                            summaries=summaries, findings=findings)
    transactions = await asyncio.to_thread(services.installer.transactions, repo.repo_id, DETAIL_TRANSACTIONS)
    return json_ok({"repo": body, "intents": [s.to_json() for s in summaries],
                    "transactions": [tx.to_json() for tx in transactions]})


async def repo_git(services: Any, request: web.Request) -> web.Response:
    """Drift in files Studio installed blocks an upgrade; the user's unrelated work must not, so the two
    are counted apart (FR-GIT-004). A receipt-owned file Git calls changed but whose bytes still match the
    receipt is Studio's own output not committed yet (a fresh install is all untracked), not a local change."""
    repo = await repo_or_404(services, request.match_info["repo_id"])
    root = Path(repo.canonical_path)
    observation = await asyncio.to_thread(services.git.observe, root)
    await asyncio.to_thread(services.repos.remember_git, repo.repo_id, observation.to_json())
    receipt = await asyncio.to_thread(services.installer.current_receipt, repo.repo_id)
    owned = [f.path for f in receipt.files] if receipt is not None else []
    split = await asyncio.to_thread(services.git.dirty_split, root, owned)
    drifted = ({entry.path for entry in await asyncio.to_thread(services.installer.drift, repo, receipt)}
               if receipt is not None else set())
    owned_dirty = [path for path in split["owned_dirty"] if path in drifted]
    # A dirty file an intent's source manifest lists is that intent's code waiting for a commit, not unrelated
    # work: named with its intent (an untracked directory when it holds one). The rest stays a count.
    written = await asyncio.to_thread(intent_written_paths, services.reader, root)
    intent_dirty = []
    for row in split.get("unrelated_changes") or ():
        rel = row["relpath"]
        owner = written.get(rel) or (next((written[p] for p in sorted(written) if p.startswith(rel)), None)
                                     if rel.endswith("/") else None)
        if owner is not None:
            intent_dirty.append({"relpath": rel, "status": row["status"], "intent_dir": owner})
    return json_ok({"git": observation.to_json(), "owned_dirty": owned_dirty,
                    "unrelated_dirty": int(split["unrelated_dirty"]) - len(intent_dirty),
                    "intent_dirty": intent_dirty[:C.MAX_SOURCE_CHANGE_FILES],
                    "owned_uncommitted": len(split["owned_dirty"]) - len(owned_dirty)
                    + len(split.get("owned_untracked_dirs") or ())})


async def get_transaction(services: Any, request: web.Request) -> web.Response:
    repo = await repo_or_404(services, request.match_info["repo_id"])
    tx = await asyncio.to_thread(services.installer.transaction, request.match_info["transaction_id"])
    if tx.repo_id != repo.repo_id:
        raise StudioError("transaction_not_found", "no such transaction for this repository")
    return json_ok({"transaction": tx.to_json()})


async def cancel_transaction(services: Any, request: web.Request) -> web.Response:
    """202 while the worker has yet to notice the request."""
    await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    tx = await services.installer.cancel(repo, transaction_id=request.match_info["transaction_id"])
    return json_ok({"ok": True, "transaction": tx.to_json()}, status=202 if tx.error == "cancel_requested" else 200)



# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #


async def preflight(services: Any, request: web.Request) -> web.Response:
    """Executes nothing from the candidate (C35): the one child process is ``bun --version``."""
    body = await read_json(request, required=("path",))
    report = await asyncio.to_thread(services.repos.preflight, str(body["path"]), probe_tools=True)
    return json_ok({"preflight": report.to_json()})


async def add_repo(services: Any, request: web.Request) -> web.Response:
    """201 with a preflight taken *after* the insert, so ``duplicate_of``/``existing_receipt`` describe the
    registration that now exists, and a refusal from ``add`` is the answer rather than a report."""
    body = await read_json(request, required=("path",))
    label = body.get("label")
    record = await asyncio.to_thread(services.repos.add, str(body["path"]), None if label is None else str(label))
    report = await asyncio.to_thread(services.repos.preflight, record.canonical_path, probe_tools=True)
    await services.activity.record(kind="repo.added", severity="info", repo_id=record.repo_id,
                                   params={"label": record.label, "availability": record.availability})
    await services.events.publish("repo.updated", {"repo_id": record.repo_id})
    return json_ok({"ok": True, "repo": await _repo_json(services, record, leases=await services.scheduler.list(),
                                                         fresh=True), "preflight": report.to_json()}, status=201)


async def directories(services: Any, request: web.Request) -> web.Response:
    return json_ok(await asyncio.to_thread(browse_directory, (await read_json(request)).get("path")))


async def update_metadata(services: Any, request: web.Request) -> web.Response:
    body = await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    # Archiving changes scan and queue visibility, so it is serialised with turns and installer work: an
    # operation in flight must not vanish from the registry while it is still executing.
    grant = None
    if body.get("archived") is True and repo.resolved_identity:
        grant = await services.scheduler.acquire_admin(repo, operation_type="repo_metadata", transaction_id=None)
    try:
        record = await asyncio.to_thread(services.repos.update_metadata, repo.repo_id, body)
    finally:
        if grant is not None:
            await services.scheduler.release(grant)
    await services.activity.record(kind="repo.updated", severity="info", repo_id=record.repo_id,
                                   params={"label": record.label, "archived": record.archived})
    await services.events.publish("repo.updated", {"repo_id": record.repo_id})
    return json_ok({"ok": True, "repo": await _repo_json(services, record, leases=await services.scheduler.list(),
                                                         fresh=False)})


async def remove_repo(services: Any, request: web.Request) -> web.Response:
    """Unregister; the repository is never touched (FR-REP-007). Refused under a live lease: bindings and
    actions cascade away with the row, and a decision still on the wire would become unaccountable."""
    repo = await repo_or_404(services, request.match_info["repo_id"])
    held = await services.scheduler.get(repo.resolved_identity) if repo.resolved_identity else None
    if held is not None:
        raise StudioError("repo_busy", "this repository has a live lease; wait for it to finish",
                          details=lease_view(held))
    await asyncio.to_thread(services.repos.remove, repo.repo_id)
    await services.activity.record(kind="repo.removed", severity="attention", repo_id=repo.repo_id,
                                   params={"label": repo.label})
    await services.events.publish("repo.removed", {"repo_id": repo.repo_id})
    return json_ok({"ok": True, "removed": repo.repo_id})


async def rebind(services: Any, request: web.Request) -> web.Response:
    """Point a moved registration at its new directory. The ``repo_id`` survives (C01): bindings, actions
    and history are keyed on it."""
    body = await read_json(request, required=("path",))
    repo = await repo_or_404(services, request.match_info["repo_id"])
    record = await asyncio.to_thread(services.repos.rebind, repo.repo_id, str(body["path"]))
    await services.activity.record(kind="repo.rebound", severity="info", repo_id=record.repo_id,
                                   params={"availability": record.availability})
    await services.events.publish("repo.updated", {"repo_id": record.repo_id})
    return json_ok({"ok": True, "repo": await _repo_json(services, record, leases=await services.scheduler.list(),
                                                         fresh=True)})


async def rescan(services: Any, request: web.Request) -> web.Response:
    await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    scan = await services.reconciler.scan_repo(repo)
    await services.activity.record(kind="repo.rescanned", severity="info", repo_id=repo.repo_id,
                                   params={"availability": scan.availability, "took_ms": scan.took_ms})
    return json_ok({"ok": True, "scan": scan.to_json()})


async def doctor(services: Any, request: web.Request) -> web.Response:
    """The engine's own health check (C09/A12). ``confirm`` because it appends ``HEALTH_CHECKED`` rows to
    AI-DLC's audit trail — the one allowlisted verb that writes, so never run as a side effect."""
    body = await read_json(request)
    if body.get("confirm") is not True:
        raise StudioError("invalid_decision", "doctor appends audit rows and requires confirm: true",
                          details={"reason": "confirm_required"})
    repo = await repo_or_404(services, request.match_info["repo_id"])
    if services.engine.bun_path is None:
        raise StudioError("bun_missing", "bun is required to run AI-DLC's own tools")
    grant = await services.scheduler.acquire_admin(repo, operation_type="doctor", transaction_id=None)
    try:
        # `explicit_user_only`: the runner refuses the verb without `_confirmed`, set only after the check above.
        result = await services.engine.run("utility.doctor", Path(repo.canonical_path),
                                           repo.engine_dir or C.STUDIO_HARNESS_DIR, repo_id=repo.repo_id,
                                           lease_generation=grant.generation, _confirmed=True)
    finally:
        await services.scheduler.release(grant)
    return json_ok({"ok": True, "result": result.to_json()})


# --------------------------------------------------------------------------- #
# installer
# --------------------------------------------------------------------------- #


async def preview_transaction(services: Any, request: web.Request) -> web.Response:
    """``{"kind"}`` → the plan and its digest, after the refusals a transaction of that kind would raise."""
    _body, kind, repo, plan = await _transaction_request(services, request)
    if plan is None:
        plan = await asyncio.to_thread(services.installer.preview, repo, kind)
    _assert_plan_offerable(services, plan, kind, repo=repo)
    return json_ok({"plan": plan.to_json(), "plan_digest": plan.digest()})


async def begin_transaction(services: Any, request: web.Request) -> web.Response:
    """``{"kind", "plan_digest"}`` → 202. The row exists before the answer, so the id handed back is already
    pollable, and the background task continues that transaction rather than starting another."""
    body, kind, repo, _plan = await _transaction_request(services, request, required=("plan_digest",))
    digest = str(body["plan_digest"])
    tx = await services.installer.begin(repo, kind, plan_digest=digest)
    task = asyncio.create_task(_run_transaction(services, repo, kind, digest, tx.transaction_id),
                               name=f"aidlc-studio-install-{tx.transaction_id}")
    services.background.append(task)
    task.add_done_callback(lambda done: _forget(services, done))
    return json_ok({"ok": True, "transaction_id": tx.transaction_id, "status": tx.status}, status=202)


async def _transaction_request(services: Any, request: web.Request,
                               required: Sequence[str] = ()) -> tuple[dict, str, Any, Any]:
    """Body, kind, repository and (for a recovery) its plan, refused in the same order for both routes."""
    body = await read_json(request, required=("kind", *required))
    kind = body_choice(body, "kind", TRANSACTION_KINDS)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    _require_available(repo)
    plan = await asyncio.to_thread(services.installer.preview, repo, kind) if kind == "recovery" else None
    if kind not in {"uninstall", "rollback"} and not getattr(plan, "recovery_transaction_id", None):
        await _require_payload(services)
    return body, kind, repo, plan


async def _run_transaction(services: Any, repo: Any, kind: str, digest: str, transaction_id: str) -> None:
    """Recorded, never raised: no request is left to answer, the installer has already rolled the repository
    back, and the transaction row is what the UI is watching."""
    try:
        await services.installer.run(repo, kind, plan_digest=digest, transaction_id=transaction_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOG.exception("aidlc-studio: install transaction %s failed", transaction_id)


def _forget(services: Any, task: Any) -> None:
    with contextlib.suppress(ValueError):
        services.background.remove(task)


async def _require_payload(services: Any) -> None:
    """Checked here, not only in the installer: a manifest that failed to *load* leaves a sentinel the
    installer would compute an empty plan from, and writing nothing is not an install."""
    if services.payload_error is not None:
        raise StudioError("payload_degraded", "the bundled payload manifest is unreadable",
                          details={"reason": services.payload_error})
    if services.payload_status is None:
        services.payload_status = await asyncio.to_thread(services.installer.verify_payload)
    if not services.payload_status.ok:
        raise StudioError("payload_degraded", "the bundled payload does not match its manifest", details={
            "mismatches": list(services.payload_status.mismatches[:10]),
            "missing": list(services.payload_status.missing[:10]),
        })


def _require_available(repo: Any) -> None:
    """A readable repository with a provable identity: the admin lease is keyed on it."""
    if repo.availability == "identity_unprovable" or not repo.resolved_identity:
        raise StudioError("identity_unprovable",
                          "this repository's identity cannot be proven, so no lease can be held for it",
                          details={"repo_id": repo.repo_id})
    if repo.availability != "available":
        raise StudioError("repo_unavailable", "this repository cannot be read right now",
                          details={"repo_id": repo.repo_id, "availability": repo.availability})


def _assert_plan_offerable(services: Any, plan: Any, kind: str, *, repo: Any = None) -> None:
    """The per-kind refusals a preview raises early (``Installer._preflight_transaction`` applies the same):
    a preview of a plan the transaction would refuse teaches users to click through a dialog that fails."""
    if kind in {"uninstall", "rollback"} or getattr(plan, "recovery_transaction_id", None):
        return  # receipt-driven plans report their own conflicts, including target compatibility
    installed = plan.engine_from
    if kind == "install" and installed:
        raise StudioError("already_installed", "AI-DLC is already installed in this repository",
                          details={"engine_version": installed})
    # Recovery exists for an install that did not finish, which can leave `.kiro` absent or its version file
    # unreadable — the very state `engine_from` reads — so refusing it would make `recovery_required` a dead end.
    recovering = kind == "recovery" and str(getattr(repo, "install_status", "")) == "recovery_required"
    if kind in ("upgrade", "recovery") and not installed and not recovering:
        raise StudioError("not_installed", "AI-DLC is not installed in this repository",
                          details={"repo_id": plan.repo_id})
    if plan.state_version_blocked:
        raise StudioError("state_version_migration_unconfirmed",
                          "this repository holds AI-DLC state this payload cannot write", details={
                              "state_versions_found": list(plan.state_versions_found),
                              "compatible": list(services.payload.compatible_state_versions),
                              "unreadable": list(plan.state_versions_unreadable),
                          })
    if kind == "upgrade" and plan.newer_installed:
        raise StudioError("newer_installed", "the installed engine is newer than the bundled payload",
                          details={"installed": installed, "bundled": plan.engine_to})
    if kind == "upgrade" and plan.same_version:
        raise StudioError("same_version_installed", "the bundled engine is already installed",
                          details={"engine_version": plan.engine_to})


# --------------------------------------------------------------------------- #
# the RepoRecord body
# --------------------------------------------------------------------------- #


async def _repo_json(services: Any, repo: Any, *, leases: Sequence[Any], fresh: bool,
                     summaries: Sequence[Any] | None = None, findings: Sequence[Any] | None = None) -> dict[str, Any]:
    """The §2.2 ``RepoRecord``: the row plus install, counts, leases, git and findings."""
    body = dict(repo.to_json())
    cached = await asyncio.to_thread(services.storage.get, "repos", repo.repo_id)
    cached_scan = (cached or {}).get("scan_json") or {}

    if not fresh and isinstance(cached_scan.get("install"), dict):
        install = dict(cached_scan["install"])
    else:
        install = (await asyncio.to_thread(services.repos.detect_install, repo)).to_json()
    receipt = await asyncio.to_thread(services.repos.current_receipt_summary, repo.repo_id)
    current = (await asyncio.to_thread(services.storage.get, "install_receipts", receipt.receipt_id)
               if receipt is not None else None)
    body["install"] = _install_json(services, install, receipt, repo=repo, current_receipt=current)
    body["install"]["rollback_target"] = None
    if receipt is not None:
        prior_id = (current or {}).get("prior_receipt_id")
        prior = await asyncio.to_thread(services.storage.get, "install_receipts", prior_id) if prior_id else None
        current_version = version_tuple(receipt.engine_version)
        prior_version = version_tuple((prior or {}).get("engine_version"))
        if (prior and prior["repo_id"] == repo.repo_id and prior["status"] == "superseded"
                and prior.get("resolved_repo_identity") == repo.resolved_identity
                and current_version and prior_version and prior_version < current_version):
            body["install"]["rollback_target"] = {"receipt_id": prior_id, "engine_version": prior["engine_version"]}

    live = await without_archived(services, await services.actions.list_live(repo_id=repo.repo_id))
    body["counts"] = {
        "intents": len(summaries) if summaries is not None else await _count_intents(services, repo),
        "in_flight": sum(1 for rec in live if rec.status in ("Delivering", "Delivered")),
        "open_actions": sum(1 for rec in live if rec.status in C.AWAITING_PERSON_STATUS),
        "blocking_findings": sum(1 for finding in (findings or ()) if C.attr(finding, "severity") == "blocking"),
    }
    body["leases"] = {
        kind: next((lease_view(rec) for rec in leases
                    if rec.kind == kind and rec.resolved_repo_identity == repo.resolved_identity), None)
        for kind in ("execution", "admin")
    }
    body["git"] = None
    if fresh and repo.availability == "available":
        body["git"] = (await asyncio.to_thread(services.git.observe, Path(repo.canonical_path))).to_json()
        await asyncio.to_thread(services.repos.remember_git, repo.repo_id, body["git"])
    elif isinstance(cached_scan.get("git"), dict):
        body["git"] = cached_scan["git"]
    body["findings"] = [f.to_json() for f in (findings or ())]
    body["scanned_at"] = cached_scan.get("scanned_at")
    return body


def _install_json(services: Any, install: dict[str, Any], receipt: Any, *, repo: Any,
                  current_receipt: Any) -> dict[str, Any]:
    """Every payload number read from the manifest (C21).

    ``engine_version`` is the harness Studio *reads* (``.kiro``, else a foreign ``.claude``/``.codex``/…),
    ``own_engine_version`` is ``.kiro`` alone. Upgrade affordances come from the latter: Studio can only
    upgrade what Studio installed, and offering it for a foreign tree would promise a write no receipt covers
    (FR-INST-003, FR-INST-014). A different payload digest on a trusted receipt also offers a refresh.
    """
    manifest = services.payload
    own = version_tuple(install.get("own_engine_version"))
    bundled = version_tuple(manifest.engine_version)
    state_version = install.get("engine_state_version")
    compatible = tuple(manifest.compatible_state_versions)
    return {
        "status": install.get("status"),
        "engine_dir": install.get("engine_dir"),
        "engine_version": install.get("engine_version"),
        "own_engine_version": install.get("own_engine_version"),
        "engine_state_version": state_version,
        "stage_count": install.get("stage_count"),
        "harness_dirs": install.get("harness_dirs") or [],
        "receipt": receipt.to_json() if receipt is not None else None,
        "bundled_engine_version": manifest.engine_version,
        "upgrade_available": bool((own and bundled and own < bundled) or payload_refresh_available(
            repo, current_receipt, install.get("own_engine_version"), manifest)),
        "newer_installed": bool(own and bundled and own > bundled),
        "state_version_blocked": bool(state_version is not None and compatible
                                      and int(state_version) not in compatible),
        "drift_count": int(install.get("drift_count") or 0),
    }


async def _count_intents(services: Any, repo: Any) -> int:
    """A directory listing, never a parse."""
    if repo.availability != "available":
        return 0
    root = Path(repo.canonical_path)
    total = 0
    with contextlib.suppress(StudioError, OSError):
        for space in await asyncio.to_thread(services.reader.list_spaces, root):
            total += len(await asyncio.to_thread(services.reader.list_intent_dirs, root, space))
    return total


async def _intent_summaries(services: Any, repo: Any) -> tuple[list[Any], list[Any]]:
    """Every intent's summary and finding, deriving and persisting no card. An intent that cannot be read is
    left out rather than costing the user the whole repository page."""
    if repo.availability != "available":
        return [], []
    root = Path(repo.canonical_path)
    leases = await services.scheduler.list()
    install = await asyncio.to_thread(services.repos.detect_install, repo)
    summaries: list[Any] = []
    findings: list[Any] = []
    for space in await asyncio.to_thread(services.reader.list_spaces, root):
        for intent_dir in await asyncio.to_thread(services.reader.list_intent_dirs, root, space):
            try:
                snap = await asyncio.to_thread(services.projection.snapshot, repo, space, intent_dir)
                view = await intent_view(services, repo, snap, install=install, leases=leases)
                summaries.append(await summarize_intent(services, snap, view))
            except StudioError as exc:
                _LOG.debug("intent %s/%s skipped in read model: %s", space, intent_dir, exc.code)
                continue
            findings.extend(view["findings"])
    return summaries, findings

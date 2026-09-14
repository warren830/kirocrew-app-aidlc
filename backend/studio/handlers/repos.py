"""Repository registry and installer routes (§2.2).

Two rules run through the whole file.

* **A 2xx never means AI-DLC changed.** ``install``/``upgrade``/``recovery`` answer ``202`` with a
  transaction id; the writing happens in a background task and the truth is the transaction row and
  the ``transaction.updated`` events. Answering 200 after a synchronous install would make the request
  timeout the user's failure mode.
* **Nothing is written without a digest the user saw.** Every mutation that touches a repository takes
  the ``plan_digest`` from its own preview, and the installer re-derives the plan and refuses
  ``install_conflict{preview_changed}`` when the repository moved in between.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
from pathlib import Path
from typing import Any, Sequence

from aiohttp import web

from .. import constants as C
from ..errors import StudioError
from ..directories import browse_directory
# The version comparison is the installer's own: "unparseable is not older" is a rule with
# consequences (it decides whether an upgrade is offered), and two spellings of it would eventually
# disagree.
from ..installer import payload_refresh_available, version_tuple
from ..leases import lease_view
from .common import (
    intent_view,
    json_ok,
    query_bool,
    read_json,
    repo_or_404,
    route,
    summarize_intent,
)

_LOG = logging.getLogger("kirocrew.app.aidlc-studio")

#: How much install history ``GET /repos/{id}`` carries (§2.2 "last 5").
DETAIL_TRANSACTIONS = 5


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


async def list_repos(services: Any, request: web.Request) -> web.Response:
    """``GET /repos`` — every registration with its install state and queue counts.

    Deliberately *not* a full scan: git is not invoked and no intent is snapshotted, because a
    64-repository list would then be 64 subprocesses and a few hundred file reads on one request. The
    install block comes from the last persisted scan when there is one, which is the same value the
    Repos page would have shown a moment ago, and ``POST …/rescan`` is how a user asks for fresh.
    """
    include_archived = query_bool(request, "include_archived", False)
    repos = await asyncio.to_thread(
        functools.partial(services.repos.list, include_archived=include_archived)
    )
    leases = await services.scheduler.list()
    bodies = []
    for repo in repos:
        bodies.append(await _repo_json(services, repo, leases=leases, fresh=False))
    return json_ok(
        {
            "repos": bodies,
            "totals": {
                "repos": len(bodies),
                "unavailable": sum(1 for body in bodies if body["availability"] != "available"),
                "open_actions": sum(int(body["counts"]["open_actions"]) for body in bodies),
            },
        }
    )


async def get_repo(services: Any, request: web.Request) -> web.Response:
    """``GET /repos/{repo_id}`` — one repository, freshly observed, with its intents.

    Fresh here (git observation, install detection, per-intent summaries) because this is the page a
    user opens *because* something looks wrong, and a cached answer is exactly what would keep it
    looking wrong.
    """
    repo = await repo_or_404(services, request.match_info["repo_id"])
    leases = await services.scheduler.list()
    summaries, findings = await _intent_summaries(services, repo)
    body = await _repo_json(
        services, repo, leases=leases, fresh=True, summaries=summaries, findings=findings
    )
    transactions = await asyncio.to_thread(
        functools.partial(services.installer.transactions, repo.repo_id, DETAIL_TRANSACTIONS)
    )
    return json_ok(
        {
            "repo": body,
            "intents": [summary.to_json() for summary in summaries],
            "transactions": [tx.to_json() for tx in transactions],
        }
    )


async def get_transaction(services: Any, request: web.Request) -> web.Response:
    """``GET /repos/{repo_id}/transactions/{transaction_id}`` — progress after a 202."""
    repo = await repo_or_404(services, request.match_info["repo_id"])
    tx = await asyncio.to_thread(
        services.installer.transaction, request.match_info["transaction_id"]
    )
    if tx.repo_id != repo.repo_id:
        raise StudioError("transaction_not_found", "no such transaction for this repository")
    return json_ok({"transaction": tx.to_json()})

async def cancel_transaction(services: Any, request: web.Request) -> web.Response:
    await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    tx = await services.installer.cancel(repo, transaction_id=request.match_info["transaction_id"])
    return json_ok({"ok": True, "transaction": tx.to_json()},
                   status=202 if tx.error == "cancel_requested" else 200)


async def get_receipts(services: Any, request: web.Request) -> web.Response:
    """``GET /repos/{repo_id}/receipts`` — install history; the file list only for the current one.

    Superseded receipts keep their digests in the database but not on the wire: the list is a history
    view, and a whole payload's worth of file entries per historical install would dwarf the answer.
    """
    repo = await repo_or_404(services, request.match_info["repo_id"])
    receipts = await asyncio.to_thread(services.installer.receipts, repo.repo_id)
    return json_ok(
        {
            "receipts": [
                receipt.to_json(include_files=receipt.status == "current") for receipt in receipts
            ]
        }
    )


async def get_repo_git(services: Any, request: web.Request) -> web.Response:
    """``GET /repos/{repo_id}/git`` — the observation plus the owned/unrelated dirty split.

    The split is FR-GIT-004: an upgrade must be blocked by drift in files Studio installed and must
    *not* be blocked because the user has unrelated work in progress, so the two are counted apart.
    """
    repo = await repo_or_404(services, request.match_info["repo_id"])
    root = Path(repo.canonical_path)
    observation = await asyncio.to_thread(services.git.observe, root)
    receipt = await asyncio.to_thread(services.installer.current_receipt, repo.repo_id)
    # `ReceiptFile.path`, not `.relpath`: this line raised AttributeError for every repository that HAS
    # a receipt, so the panel 500'd the moment an install succeeded and worked only before one.
    owned = [f.path for f in receipt.files] if receipt is not None else []
    split = await asyncio.to_thread(services.git.dirty_split, root, owned)
    return json_ok(
        {
            "git": observation.to_json(),
            "owned_dirty": split["owned_dirty"],
            "unrelated_dirty": int(split["unrelated_dirty"]),
        }
    )


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #


async def preflight_repo(services: Any, request: web.Request) -> web.Response:
    """``POST /repos/preflight`` — is this directory registrable, and would an install be safe?

    Executes nothing from the candidate directory (C35): the only child process is ``bun --version``,
    whose argv names no repository. Repo-resident TypeScript on an unregistered path is untrusted data.
    """
    body = await read_json(request, required=("path",))
    report = await asyncio.to_thread(
        functools.partial(services.repos.preflight, str(body["path"]), probe_tools=True)
    )
    return json_ok({"preflight": report.to_json()})


async def add_repo(services: Any, request: web.Request) -> web.Response:
    """``POST /repos`` — register a directory (201), with the preflight that justified it.

    The preflight is taken *after* the insert so it reports the registration that now exists (its
    ``duplicate_of`` and ``existing_receipt`` fields are relative to the registry), and so a refusal
    from ``add`` is the answer rather than a report about a repository Studio declined to store.
    """
    body = await read_json(request, required=("path",))
    label = body.get("label")
    record = await asyncio.to_thread(
        functools.partial(services.repos.add, str(body["path"]), None if label is None else str(label))
    )
    report = await asyncio.to_thread(
        functools.partial(services.repos.preflight, record.canonical_path, probe_tools=True)
    )
    await services.activity.record(
        kind="repo.added", severity="info", repo_id=record.repo_id,
        params={"label": record.label, "availability": record.availability},
    )
    await services.events.publish("repo.updated", {"repo_id": record.repo_id})
    leases = await services.scheduler.list()
    return json_ok(
        {
            "ok": True,
            "repo": await _repo_json(services, record, leases=leases, fresh=True),
            "preflight": report.to_json(),
        },
        status=201,
    )


async def directories(services: Any, request: web.Request) -> web.Response:
    body = await read_json(request)
    return json_ok(await asyncio.to_thread(browse_directory, body.get("path")))


async def update_repo_metadata(services: Any, request: web.Request) -> web.Response:
    body = await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    # Archive changes scan/queue visibility. Serialize it with turns and installer work so an
    # in-flight operation cannot disappear from the normal registry while it is still executing.
    grant = None
    if body.get("archived") is True and repo.resolved_identity:
        grant = await services.scheduler.acquire_admin(
            repo, operation_type="repo_metadata", transaction_id=None,
        )
    try:
        record = await asyncio.to_thread(services.repos.update_metadata, repo.repo_id, body)
    finally:
        if grant is not None:
            await services.scheduler.release(grant)
    await services.activity.record(kind="repo.updated", severity="info", repo_id=record.repo_id,
                                   params={"label": record.label, "archived": record.archived})
    await services.events.publish("repo.updated", {"repo_id": record.repo_id})
    return json_ok({"ok": True, "repo": await _repo_json(
        services, record, leases=await services.scheduler.list(), fresh=False,
    )})


async def remove_repo(services: Any, request: web.Request) -> web.Response:
    """``DELETE /repos/{repo_id}`` — unregister. Never touches the repository (FR-REP-007).

    Refused while a lease is live: bindings and actions cascade away with the row, and dropping the
    record of a decision that is still on the wire would leave a turn nobody can account for.
    """
    repo = await repo_or_404(services, request.match_info["repo_id"])
    if repo.resolved_identity:
        held = await services.scheduler.get(repo.resolved_identity)
        if held is not None:
            raise StudioError(
                "repo_busy",
                "this repository has a live lease; wait for it to finish",
                details=lease_view(held, orphaned=services.scheduler.is_stale(held)),
            )
    await asyncio.to_thread(services.repos.remove, repo.repo_id)
    await services.activity.record(kind="repo.removed", severity="attention", repo_id=repo.repo_id,
                                  params={"label": repo.label})
    await services.events.publish("repo.removed", {"repo_id": repo.repo_id})
    return json_ok({"ok": True, "removed": repo.repo_id})


async def rebind_repo(services: Any, request: web.Request) -> web.Response:
    """``POST /repos/{repo_id}/rebind`` — point a moved registration at its new directory.

    The ``repo_id`` survives (C01), which is the whole point: its bindings, actions and history are
    keyed on it, and a rebind that minted a new id would orphan all of them.
    """
    body = await read_json(request, required=("path",))
    repo = await repo_or_404(services, request.match_info["repo_id"])
    record = await asyncio.to_thread(services.repos.rebind, repo.repo_id, str(body["path"]))
    await services.activity.record(kind="repo.rebound", severity="info", repo_id=record.repo_id,
                                  params={"availability": record.availability})
    await services.events.publish("repo.updated", {"repo_id": record.repo_id})
    leases = await services.scheduler.list()
    return json_ok({"ok": True, "repo": await _repo_json(services, record, leases=leases, fresh=True)})


async def rescan_repo(services: Any, request: web.Request) -> web.Response:
    """``POST /repos/{repo_id}/rescan`` — re-establish availability, install health and the intents."""
    await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    scan = await services.reconciler.scan_repo(repo)
    await services.activity.record(kind="repo.rescanned", severity="info", repo_id=repo.repo_id,
                                   params={"availability": scan.availability, "took_ms": scan.took_ms})
    return json_ok({"ok": True, "scan": scan.to_json()})


async def doctor_repo(services: Any, request: web.Request) -> web.Response:
    """``POST /repos/{repo_id}/doctor`` — the engine's own health check, on explicit request (C09/A12).

    ``confirm`` is required because ``doctor`` appends ``HEALTH_CHECKED``/``GUARDRAIL_LOADED`` rows to
    AI-DLC's audit trail. It is the one allowlisted verb that writes anything, so it is never run
    automatically and never as a side effect of a read.
    """
    body = await read_json(request)
    if body.get("confirm") is not True:
        raise StudioError(
            "invalid_decision",
            "doctor appends audit rows and requires confirm: true",
            details={"reason": "confirm_required"},
        )
    repo = await repo_or_404(services, request.match_info["repo_id"])
    if services.engine.bun_path is None:
        raise StudioError("bun_missing", "bun is required to run AI-DLC's own tools")
    grant = await services.scheduler.acquire_admin(repo, operation_type="doctor", transaction_id=None)
    try:
        result = await services.engine.run(
            "utility.doctor",
            Path(repo.canonical_path),
            repo.engine_dir or C.STUDIO_HARNESS_DIR,
            repo_id=repo.repo_id,
            lease_generation=grant.generation,
            # The verb is `explicit_user_only`: the runner refuses it without this flag, and the flag is
            # only ever set where a `confirm: true` body was checked one call earlier.
            _confirmed=True,
        )
    finally:
        await services.scheduler.release(grant)
    return json_ok({"ok": True, "result": result.to_json()})


# --------------------------------------------------------------------------- #
# installer
# --------------------------------------------------------------------------- #


async def _preview(services: Any, request: web.Request, kind: str) -> web.Response:
    """The shared body of the three preview routes.

    One function because the three differ only in ``kind``: the refusals that distinguish an install
    from an upgrade (``already_installed``, ``not_installed``, ``newer_installed``,
    ``same_version_installed``, ``state_version_migration_unconfirmed``) are the installer's, decided
    from the plan it just computed, and re-deciding them per route would be three chances to disagree.
    """
    await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    _require_available(repo)
    plan = await asyncio.to_thread(services.installer.preview, repo, kind) if kind == "recovery" else None
    if kind not in {"uninstall", "rollback"} and not getattr(plan, "recovery_transaction_id", None):
        await _require_payload(services)
    if plan is None:
        plan = await asyncio.to_thread(services.installer.preview, repo, kind)
    _assert_plan_offerable(services, plan, kind, repo=repo)
    return json_ok({"plan": plan.to_json(), "plan_digest": plan.digest()})


async def _begin(services: Any, request: web.Request, kind: str) -> web.Response:
    """The shared body of the three transaction routes: refuse, insert, 202, then run in the background.

    The transaction row exists before the 202 returns so the id the client is handed is already
    pollable; the background task then continues *that* transaction rather than starting a second one.
    """
    body = await read_json(request, required=("plan_digest",))
    repo = await repo_or_404(services, request.match_info["repo_id"])
    _require_available(repo)
    recovery = await asyncio.to_thread(services.installer.preview, repo, kind) if kind == "recovery" else None
    if kind not in {"uninstall", "rollback"} and not getattr(recovery, "recovery_transaction_id", None):
        await _require_payload(services)
    digest = str(body["plan_digest"])
    tx = await services.installer.begin(repo, kind, plan_digest=digest)
    task = asyncio.create_task(
        _run_transaction(services, repo, kind, digest, tx.transaction_id),
        name=f"aidlc-studio-install-{tx.transaction_id}",
    )
    services.background.append(task)
    task.add_done_callback(lambda done: _forget(services, done))
    return json_ok({"ok": True, "transaction_id": tx.transaction_id, "status": tx.status}, status=202)


async def _run_transaction(
    services: Any, repo: Any, kind: str, digest: str, transaction_id: str
) -> None:
    """Run the installer after the 202. Never raises out of the task.

    A failure here is *recorded* rather than raised: there is no request left to answer, and the
    installer has already rolled the repository back to its previous complete install. The transaction
    row and its ``transaction.updated`` events are what the UI is watching.
    """
    try:
        await services.installer.run(repo, kind, plan_digest=digest, transaction_id=transaction_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOG.exception("aidlc-studio: install transaction %s failed", transaction_id)


def _forget(services: Any, task: Any) -> None:
    with contextlib.suppress(ValueError):
        services.background.remove(task)


async def install_preview(services: Any, request: web.Request) -> web.Response:
    return await _preview(services, request, "install")


async def install_apply(services: Any, request: web.Request) -> web.Response:
    return await _begin(services, request, "install")


async def upgrade_preview(services: Any, request: web.Request) -> web.Response:
    return await _preview(services, request, "upgrade")


async def upgrade_apply(services: Any, request: web.Request) -> web.Response:
    return await _begin(services, request, "upgrade")


async def recovery_preview(services: Any, request: web.Request) -> web.Response:
    return await _preview(services, request, "recovery")


async def recovery_apply(services: Any, request: web.Request) -> web.Response:
    return await _begin(services, request, "recovery")

async def uninstall_preview(services: Any, request: web.Request) -> web.Response:
    return await _preview(services, request, "uninstall")


async def uninstall_apply(services: Any, request: web.Request) -> web.Response:
    return await _begin(services, request, "uninstall")

async def rollback_preview(services: Any, request: web.Request) -> web.Response:
    return await _preview(services, request, "rollback")


async def rollback_apply(services: Any, request: web.Request) -> web.Response:
    return await _begin(services, request, "rollback")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


async def _require_payload(services: Any) -> None:
    """Refuse every install path while the bundled payload is not provably intact.

    Checked in the route rather than only inside the installer because a manifest that failed to *load*
    leaves the installer with a sentinel: it would compute an empty plan and call writing nothing an
    install. ``payload_degraded`` is the honest answer.
    """
    if services.payload_error is not None:
        raise StudioError(
            "payload_degraded",
            "the bundled payload manifest is unreadable",
            details={"reason": services.payload_error},
        )
    if services.payload_status is None:
        services.payload_status = await asyncio.to_thread(services.installer.verify_payload)
    if not services.payload_status.ok:
        raise StudioError(
            "payload_degraded",
            "the bundled payload does not match its manifest",
            details={
                "mismatches": list(services.payload_status.mismatches[:10]),
                "missing": list(services.payload_status.missing[:10]),
            },
        )


def _require_available(repo: Any) -> None:
    """An install needs a readable repository and a provable identity (the admin lease is keyed on it)."""
    if repo.availability == "identity_unprovable" or not repo.resolved_identity:
        raise StudioError(
            "identity_unprovable",
            "this repository's identity cannot be proven, so no lease can be held for it",
            details={"repo_id": repo.repo_id},
        )
    if repo.availability != "available":
        raise StudioError(
            "repo_unavailable",
            "this repository cannot be read right now",
            details={"repo_id": repo.repo_id, "availability": repo.availability},
        )


def _assert_plan_offerable(services: Any, plan: Any, kind: str, *, repo: Any = None) -> None:
    """The per-kind refusals a *preview* must raise (§2.2's error columns).

    Previewing a plan the transaction would refuse teaches users to click through a dialog that always
    fails; each of these is the same check ``Installer._preflight_transaction`` applies, raised early
    where there is still a useful answer to give.
    """
    if kind in {"uninstall", "rollback"} or getattr(plan, "recovery_transaction_id", None):
        return  # receipt-driven plans report their own conflicts, including target compatibility
    installed = plan.engine_from
    if kind == "install" and installed:
        raise StudioError(
            "already_installed",
            "AI-DLC is already installed in this repository",
            details={"engine_version": installed},
        )
    # Recovery is the exception: it exists precisely for a repository whose install did not finish, and a
    # transaction that died between `backup` and `write` can leave `.kiro` absent or its version file
    # unparseable — the very state `engine_from` reads. Refusing there made `recovery_required` a
    # dead end, because Recover is the only action such a repository is offered and the transaction
    # preflight (`Installer._plan_for_run`) does not apply this check at all.
    recovering = kind == "recovery" and str(getattr(repo, "install_status", "")) == "recovery_required"
    if kind in ("upgrade", "recovery") and not installed and not recovering:
        raise StudioError(
            "not_installed",
            "AI-DLC is not installed in this repository",
            details={"repo_id": plan.repo_id},
        )
    if plan.state_version_blocked:
        raise StudioError(
            "state_version_migration_unconfirmed",
            "this repository holds AI-DLC state this payload cannot write",
            details={
                "state_versions_found": list(plan.state_versions_found),
                "compatible": list(services.payload.compatible_state_versions),
                "unreadable": list(plan.state_versions_unreadable),
            },
        )
    if kind == "upgrade":
        if plan.newer_installed:
            raise StudioError(
                "newer_installed",
                "the installed engine is newer than the bundled payload",
                details={"installed": installed, "bundled": plan.engine_to},
            )
        if plan.same_version:
            raise StudioError(
                "same_version_installed",
                "the bundled engine is already installed",
                details={"engine_version": plan.engine_to},
            )


async def _repo_json(
    services: Any,
    repo: Any,
    *,
    leases: Sequence[Any],
    fresh: bool,
    summaries: Sequence[Any] | None = None,
    findings: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """The §2.2 ``RepoRecord`` JSON: the row plus install, counts, leases, git and findings."""
    body = dict(repo.to_json())
    cached = await asyncio.to_thread(services.storage.get, "repos", repo.repo_id)
    cached_scan = (cached or {}).get("scan_json") or {}

    health = None
    if not fresh and isinstance(cached_scan.get("install"), dict):
        install = dict(cached_scan["install"])
    else:
        health = await asyncio.to_thread(services.repos.detect_install, repo)
        install = health.to_json()
    receipt = await asyncio.to_thread(services.repos.current_receipt_summary, repo.repo_id)
    current = (
        await asyncio.to_thread(services.storage.get, "install_receipts", receipt.receipt_id)
        if receipt is not None else None
    )
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
            body["install"]["rollback_target"] = {
                "receipt_id": prior_id, "engine_version": prior["engine_version"],
            }

    live = await services.actions.list_live(repo_id=repo.repo_id)
    intent_count = len(summaries) if summaries is not None else await _count_intents(services, repo)
    body["counts"] = {
        "intents": intent_count,
        "in_flight": sum(1 for rec in live if rec.status in ("Delivering", "Delivered", "Processing")),
        "open_actions": len(live),
        "blocking_findings": sum(
            1 for finding in (findings or ()) if C.attr(finding, "severity") == "blocking"
        ),
    }
    body["leases"] = {
        kind: next(
            (
                lease_view(rec, orphaned=services.scheduler.is_stale(rec))
                for rec in leases
                if rec.kind == kind and rec.resolved_repo_identity == repo.resolved_identity
            ),
            None,
        )
        for kind in ("execution", "admin")
    }
    body["git"] = None
    if fresh and repo.availability == "available":
        observation = await asyncio.to_thread(services.git.observe, Path(repo.canonical_path))
        body["git"] = observation.to_json()
    elif isinstance(cached_scan.get("git"), dict):
        body["git"] = cached_scan["git"]
    body["findings"] = [f.to_json() for f in (findings or ())]
    body["scanned_at"] = cached_scan.get("scanned_at")
    return body


def _install_json(
    services: Any, install: dict[str, Any], receipt: Any, *, repo: Any, current_receipt: Any
) -> dict[str, Any]:
    """The ``install`` block, with every payload-derived number read from the manifest (C21).

    Two versions, and the difference is the whole point. ``engine_version`` is the harness Studio
    *reads* — ``.kiro`` when it is there, otherwise whichever foreign harness (``.claude``, ``.codex``,
    ``.cursor``, ``.aidlc``) the repository has — because that is what drives dispatch, the stage graph
    and the state reads. ``own_engine_version`` is ``.kiro`` alone, ``null`` when Studio has nothing
    installed here. ``upgrade_available`` and ``newer_installed`` are computed from the latter because
    they drive the Upgrade affordance, and Studio can only upgrade what Studio installed: offering
    "upgrade" for a ``.claude`` tree older than the payload would promise a write into files no receipt
    covers and no plan touches (FR-INST-003, FR-INST-014).
    A different payload digest on a trusted current receipt also offers a same-version refresh.
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
        "upgrade_available": bool(
            (own and bundled and own < bundled)
            or payload_refresh_available(
                repo, current_receipt, install.get("own_engine_version"), manifest
            )
        ),
        "newer_installed": bool(own and bundled and own > bundled),
        "state_version_blocked": bool(
            state_version is not None and compatible and int(state_version) not in compatible
        ),
        "drift_count": int(install.get("drift_count") or 0),
    }


async def _count_intents(services: Any, repo: Any) -> int:
    """How many intent records the repository holds. A directory listing, never a parse."""
    if repo.availability != "available":
        return 0
    root = Path(repo.canonical_path)
    total = 0
    with contextlib.suppress(StudioError, OSError):
        for space in await asyncio.to_thread(services.reader.list_spaces, root):
            total += len(await asyncio.to_thread(services.reader.list_intent_dirs, root, space))
    return total


async def _intent_summaries(services: Any, repo: Any) -> tuple[list[Any], list[Any]]:
    """Every intent's summary and every finding, without deriving or persisting a single card."""
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
                summaries.append(await summarize_intent(services, repo, snap, view))
            except StudioError as exc:
                _LOG.debug("intent %s/%s skipped in read model: %s", space, intent_dir, exc.code)
                continue
            findings.extend(view["findings"])
    return summaries, findings


def ROUTES(services: Any) -> list[Any]:
    return [
        route("GET", "/repos", list_repos, services=services),
        route("POST", "/repos", add_repo, owner=True, services=services),
        route("POST", "/repos/preflight", preflight_repo, owner=True, services=services),
        route("POST", "/directories", directories, owner=True, services=services),
        route("GET", "/repos/{repo_id}", get_repo, services=services),
        route("PUT", "/repos/{repo_id}/metadata", update_repo_metadata, owner=True, services=services),
        route("DELETE", "/repos/{repo_id}", remove_repo, owner=True, services=services),
        route("POST", "/repos/{repo_id}/rebind", rebind_repo, owner=True, services=services),
        route("POST", "/repos/{repo_id}/rescan", rescan_repo, owner=True, services=services),
        route("POST", "/repos/{repo_id}/doctor", doctor_repo, owner=True, services=services),
        route("POST", "/repos/{repo_id}/install/preview", install_preview, owner=True, services=services),
        route("POST", "/repos/{repo_id}/install", install_apply, owner=True, services=services),
        route("POST", "/repos/{repo_id}/upgrade/preview", upgrade_preview, owner=True, services=services),
        route("POST", "/repos/{repo_id}/upgrade", upgrade_apply, owner=True, services=services),
        route("POST", "/repos/{repo_id}/uninstall/preview", uninstall_preview, owner=True, services=services),
        route("POST", "/repos/{repo_id}/uninstall", uninstall_apply, owner=True, services=services),
        route("POST", "/repos/{repo_id}/rollback/preview", rollback_preview, owner=True, services=services),
        route("POST", "/repos/{repo_id}/rollback", rollback_apply, owner=True, services=services),
        route(
            "POST", "/repos/{repo_id}/install/recovery/preview", recovery_preview,
            owner=True, services=services,
        ),
        route(
            "POST", "/repos/{repo_id}/install/recovery", recovery_apply, owner=True, services=services
        ),
        route(
            "GET", "/repos/{repo_id}/transactions/{transaction_id}", get_transaction, services=services
        ),
        route("POST", "/repos/{repo_id}/transactions/{transaction_id}/cancel",
              cancel_transaction, owner=True, services=services),
        route("GET", "/repos/{repo_id}/receipts", get_receipts, services=services),
        route("GET", "/repos/{repo_id}/git", get_repo_git, services=services),
    ]

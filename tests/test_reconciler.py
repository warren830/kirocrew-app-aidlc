"""Reconciliation tests: the guarantees that survive a crash, a lie and a slow disk.

The reconciler is the only component that cannot answer "no". Once ``Delivering`` is durable a decision
may already be in the model's mouth, so every test here is written as "given exactly this evidence, what
is Studio allowed to say?" — and the negative cases carry the weight:

* ``NotDelivered`` is reachable **only** with four independent proofs, and a gateway restart between the
  browser's send and its report makes it unreachable by construction (review R02/C24).
* ``DeliveryUncertain`` always continues to ``ReconciliationRequired`` — a status with no
  reconciler-driven exit is a boundary nobody can act on (C04/review R07).
* a gate that approved itself without a ``HUMAN_TURN``, or with two, is not a resolution: it is
  ``human_presence_anomaly`` for a human to look at (FR-SES-010/C23/review P04).
* every status Studio writes is an edge of PRD §11.1. The transition table is enforced by the broker
  double below, which validates against ``constants.ALLOWED_TRANSITIONS`` exactly as ``actions.py``
  must — so a test that "passes" by inventing an edge cannot.
* the advisor pass (§1.13 step 6, A24) is housekeeping and is held to housekeeping's rules: it drafts
  only where the owner granted it, it settles before it starts anything, and a broker that raises — or
  is simply absent — costs the tick nothing else.

``actions.py`` and ``services.py`` are peer modules being written in parallel, so this file supplies a
faithful double for the broker's five reconciler-facing methods (over the **real** ``Storage``, the real
CAS and the real dedupe index) and a ``Services``-shaped namespace of otherwise real modules. Nothing
inside ``reconciler.py`` is patched.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Sequence

import fixtures as F
import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
GATE_DIR = "260904-gate-demo"
GATE_STAGE = "requirements-analysis"
QUESTIONS_REL = "inception/requirements-analysis/requirements-analysis-questions.md"
BOOT = "boot-test-0001"
OTHER_BOOT = "boot-test-9999"
HUMAN_TURN_MARKER = ".aidlc-human-turn"


# --------------------------------------------------------------------------- #
# module fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def R(studio):
    module = getattr(studio, "reconciler", None)
    assert module is not None, getattr(studio, "reconciler__error", "reconciler.py did not import")
    return module


@pytest.fixture
def C(studio):
    return studio.constants


# --------------------------------------------------------------------------- #
# the broker double (real Storage, real transition table)
# --------------------------------------------------------------------------- #


class FakeBroker:
    """The five ``HumanActionBroker`` methods the reconciler calls, over the real store.

    Deliberately *not* permissive: ``transition`` validates against ``ALLOWED_TRANSITIONS`` and CASes on
    ``status_generation``, because the property most of these tests assert is "the reconciler only ever
    asks for a legal edge". A double that accepted anything would make every one of them vacuous.
    """

    def __init__(self, studio_ns: Any, storage: Any, clock: Any, ids: Any) -> None:
        self._m = studio_ns
        self._st = storage
        self._clock = clock
        self._ids = ids
        self.failures: list[dict] = []
        self.retired: list[tuple[str, str, str, set[str]]] = []
        #: The ``reason`` each ``retire_stale`` call carried, in call order (the vanished-intent sweep
        #: passes ``intent_missing``; the per-intent scan passes the default).
        self.retire_reasons: list[str] = []
        self.transitions: list[tuple[str, str, str]] = []

    # ---- reads ----

    def _rec(self, row: dict) -> SimpleNamespace:
        return SimpleNamespace(
            action_id=str(row["action_id"]),
            type=row.get("type"),
            risk_class=row.get("risk_class"),
            repo_id=row.get("repo_id"),
            resolved_repo_identity=row.get("resolved_repo_identity"),
            intent_uuid=row.get("intent_uuid"),
            intent_dir=row.get("intent_dir"),
            space=row.get("space"),
            intent_key=self._m.constants.intent_key_for(row.get("space"), row.get("intent_dir")),
            stage=row.get("stage"),
            unit=row.get("unit"),
            captured={
                "state_hash": row.get("captured_state_hash"),
                "boundary_token": row.get("boundary_token"),
                "question_digest": row.get("question_digest"),
                "stage_attempt": row.get("stage_attempt"),
                "evidence_digest": row.get("evidence_digest"),
                "is_active": None
                if row.get("is_active_captured") is None
                else bool(row.get("is_active_captured")),
            },
            payload=row.get("payload_json") or {},
            payload_digest=row.get("payload_digest"),
            human_text=row.get("human_text"),
            wire_text=row.get("wire_text"),
            status=row.get("status"),
            status_generation=int(row.get("status_generation") or 0),
            session_key=row.get("session_key"),
            slot_key=row.get("slot_key"),
            delivery_id=row.get("delivery_id"),
            lease_generation=row.get("lease_generation"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            delivering_at=row.get("delivering_at"),
            delivered_at=row.get("delivered_at"),
            resolved_at=row.get("resolved_at"),
            deadline_at=row.get("deadline_at"),
            evidence=dict(row.get("evidence_json") or {}),
            resolution=row.get("resolution_json"),
            cursor_readback=row.get("cursor_readback_json"),
            presence_baseline=row.get("presence_baseline_json"),
            boot_id=row.get("boot_id"),
            retry_fingerprint=row.get("retry_fingerprint"),
            source=row.get("source"),
            waiting_since=row.get("waiting_since"),
            dedupe_key=row.get("dedupe_key"),
        )

    async def get(self, action_id: str) -> SimpleNamespace:
        row = await asyncio.to_thread(self._st.get, "actions", action_id)
        if row is None:
            raise self._m.errors.StudioError("action_not_found", "no such action")
        return self._rec(row)

    async def list_live(
        self,
        *,
        repo_id: str | None = None,
        space: str | None = None,
        intent_dir: str | None = None,
        include_revision: bool = False,
    ) -> list[SimpleNamespace]:
        where: dict[str, Any] = {"status": list(self._m.constants.LIVE_ACTION_STATUS)}
        if repo_id:
            where["repo_id"] = repo_id
        if space:
            where["space"] = space
        if intent_dir:
            where["intent_dir"] = intent_dir
        rows = await asyncio.to_thread(self._st.select, "actions", where)
        recs = [self._rec(r) for r in rows]
        if not include_revision:
            recs = [r for r in recs if r.type != "revision"]
        return recs

    # ---- writes ----

    async def transition(
        self,
        action_id: str,
        to_status: str,
        *,
        expected_generation: int,
        reason: str,
        evidence: dict,
    ) -> SimpleNamespace:
        row = await asyncio.to_thread(self._st.get, "actions", action_id)
        current = str(row["status"])
        if to_status not in self._m.constants.ALLOWED_TRANSITIONS.get(current, ()):
            raise self._m.errors.IllegalTransition(
                f"{current} -> {to_status}", details={"from": current, "to": to_status}
            )
        merged = dict(row.get("evidence_json") or {})
        merged.update(evidence or {})
        payload: dict[str, Any] = {"status": to_status, "evidence_json": merged}
        # The broker composes the terminal resolution record from the reason and evidence it is handed
        # (``actions._transition``); mirrored here so the reconciler's half of that contract is asserted.
        kinds = {
            "StateChanged": "state_changed",
            "ResolvedNoTransition": "no_transition",
            "Failed": "failed",
            "Cancelled": "cancelled",
        }
        if to_status in kinds:
            payload["resolved_at"] = self._clock.iso()
            payload["resolution_json"] = {
                "kind": kinds[to_status],
                "resolved_at": self._clock.iso(),
                "evidence": dict(evidence) or None,
                "reason": reason,
            }
        await asyncio.to_thread(
            self._st.cas_update, "actions", action_id, expected_generation, payload
        )
        await asyncio.to_thread(
            self._st.insert,
            "action_transitions",
            {
                "action_id": action_id,
                "from_status": current,
                "to_status": to_status,
                "generation": expected_generation + 1,
                "at": self._clock.iso(),
                "reason": reason,
                "evidence_json": merged,
            },
        )
        self.transitions.append((action_id, to_status, reason))
        return await self.get(action_id)

    async def record_failure(
        self, rec: Any, *, fingerprint: str, fingerprint_class: str, summary: str, evidence: dict
    ) -> tuple[SimpleNamespace, bool]:
        self.failures.append(
            {
                "action_id": rec.action_id,
                "fingerprint": fingerprint,
                "class": fingerprint_class,
                "summary": summary,
            }
        )
        moved = await self.transition(
            rec.action_id,
            "Failed",
            expected_generation=int(rec.status_generation),
            reason=fingerprint,
            evidence=dict(evidence, fingerprint=fingerprint, fingerprint_class=fingerprint_class),
        )
        opened = fingerprint_class in self._m.constants.DETERMINISTIC_FINGERPRINT_CLASSES
        return moved, opened

    async def upsert_derived(self, seeds: Sequence[Any]) -> list[SimpleNamespace]:
        live = set(self._m.constants.DEDUPE_LIVE_ACTION_STATUS)
        for seed in seeds:
            rows = await asyncio.to_thread(
                self._st.select, "actions", {"dedupe_key": seed.dedupe_key}
            )
            if any(str(r["status"]) in live for r in rows):
                continue
            await asyncio.to_thread(
                self._st.insert,
                "actions",
                {
                    "action_id": self._ids.new("a"),
                    "type": seed.type,
                    "risk_class": seed.risk_class,
                    "repo_id": seed.repo_id,
                    "intent_dir": seed.intent_dir,
                    "space": seed.space,
                    "stage": seed.stage,
                    "status": "Queued",
                    "created_at": self._clock.iso(),
                    "updated_at": self._clock.iso(),
                    "waiting_since": seed.waiting_since,
                    "source": "studio",
                    "dedupe_key": seed.dedupe_key,
                    "evidence_json": {},
                },
            )
        return await self.list_live(
            repo_id=seeds[0].repo_id if seeds else None, include_revision=True
        )

    async def retire_stale(
        self,
        repo_id: str,
        space: str,
        intent_dir: str,
        live_keys: set[str],
        *,
        reason: str = "boundary_superseded",
    ) -> None:
        self.retired.append((repo_id, space, intent_dir, set(live_keys)))
        self.retire_reasons.append(reason)
        rows = await asyncio.to_thread(
            self._st.select, "actions", {"repo_id": repo_id, "space": space, "intent_dir": intent_dir}
        )
        for row in rows:
            key = row.get("dedupe_key")
            if not key or key in live_keys:
                continue
            if str(row["status"]) in ("Queued", "NotDelivered"):
                await asyncio.to_thread(
                    self._st.cas_update,
                    "actions",
                    str(row["action_id"]),
                    int(row.get("status_generation") or 0),
                    {"status": "Cancelled"},
                )
            elif str(row["status"]) == "Failed":
                merged = dict(row.get("evidence_json") or {})
                merged["superseded"] = True
                await asyncio.to_thread(
                    self._st.cas_update,
                    "actions",
                    str(row["action_id"]),
                    int(row.get("status_generation") or 0),
                    {"evidence_json": merged},
                )


# --------------------------------------------------------------------------- #
# the Services-shaped namespace
# --------------------------------------------------------------------------- #


@pytest.fixture
def svc(studio, fake_ctx, fake_host, clock, ids, fake_git, R):
    """Real modules, one double (`actions`), no ``scan_pool`` (so scans use the default executor)."""
    storage = studio.storage.Storage(
        Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME, clock=clock, ids=ids
    )
    storage.open()
    host = studio.sessions.HostBridge(clock, logging.getLogger("test.host"))
    host.attach(fake_host)
    reader = studio.aidlc_reader.AidlcReader(clock)
    consistency = studio.consistency.ConsistencyEngine()
    projection = studio.projection.Projection(reader, consistency, clock)
    activity = studio.activity.ActivityProjector(storage, clock)
    events = studio.events.EventLog(storage, fake_ctx.events, clock)
    settings = studio.settings.SettingsService(storage, host, clock)
    settings.load()
    git = studio.git_observer.GitObserver(fake_git, clock)
    repos = studio.repo_registry.RepoRegistry(storage, git, clock, ids)
    scheduler = studio.leases.RepoScheduler(storage, host, settings, clock, activity)
    sessions = studio.sessions.SessionBinder(storage, host, projection, clock, activity)
    actions = FakeBroker(studio, storage, clock, ids)

    services = SimpleNamespace(
        ctx=fake_ctx,
        clock=clock,
        ids=ids,
        boot_id=BOOT,
        storage=storage,
        settings=settings,
        repos=repos,
        reader=reader,
        consistency=consistency,
        projection=projection,
        scheduler=scheduler,
        host=host,
        sessions=sessions,
        actions=actions,
        activity=activity,
        events=events,
        git=git,
        installer=None,
        scan_pool=None,
    )
    services.reconciler = R.Reconciler(services)
    yield services
    storage.close()


# --------------------------------------------------------------------------- #
# scenario helpers
# --------------------------------------------------------------------------- #


def _record(root: Path, intent_dir: str = GATE_DIR, space: str = "default") -> Path:
    return root / "aidlc" / "spaces" / space / "intents" / intent_dir


def touch_human_turn(root: Path, *, mtime_ns: int | None = None) -> int:
    """Stamp ``.aidlc-human-turn`` the way the AI-DLC adapter does on a real user prompt.

    Written by hand rather than through the engine because the marker's *mtime* is the fact the presence
    invariant reads, and a test that could not move it could not tell a real human turn from an audit row
    an agent wrote.
    """
    path = _record(root) / HUMAN_TURN_MARKER
    path.write_text("2026-09-04T10:00:00Z\n")
    stamp = mtime_ns if mtime_ns is not None else time.time_ns()
    os.utime(path, ns=(stamp, stamp))
    return stamp


@pytest.fixture
def scene(svc, gate_repo, fake_host):
    """One registered repo, one bound slot, and a factory for actions in any status.

    The action rows are written straight to the store with the exact columns ``submit`` commits, because
    what is under test is the reconciler's reading of a committed row — not the submit path (that is
    ``test_actions.py``). ``captured``, ``evidence`` and ``presence_baseline`` all come from a real
    snapshot, so the staleness and presence comparisons are the production ones.
    """
    root = Path(gate_repo)
    repo = svc.repos.add(str(root))
    slot_key = f"aidlc-studio-{repo.repo_id}-{GATE_DIR}"
    fake_host.add_slot(slot_key, project=str(root), agent="aidlc")
    # Bound, because the scan path reads the slot through the binding while the action path reads it off
    # the row: an unbound scene would silently exercise only half the host observation.
    asyncio.run(svc.sessions.bind(repo, "default", GATE_DIR, slot_key=slot_key, intent_uuid=None))

    def snapshot(**over: Any):
        return svc.projection.snapshot(svc.repos.get(repo.repo_id), "default", GATE_DIR, **over)

    def make(
        *,
        status: str = "Processing",
        action_type: str = "gate",
        decision: str = "approve",
        risk_class: str = "human_lane",
        stage: str | None = GATE_STAGE,
        wire_text: str | None = "Approve",
        payload: dict | None = None,
        delivering_at: str = "2026-09-04T09:59:00Z",
        delivered_at: str | None = "2026-09-04T09:59:05Z",
        deadline_at: str | None = "2026-09-04T12:00:00Z",
        boot_id: str | None = BOOT,
        presence: bool = True,
        lease_generation: int | None = None,
        evidence_extra: dict | None = None,
        snap: Any = None,
    ) -> str:
        snap = snap if snap is not None else snapshot()
        captured = svc.projection.captured(snap, stage)
        evidence = svc.projection.evidence(snap).to_json()
        baseline = svc.reader.presence_baseline(
            root, "default", GATE_DIR, snap.audit, snap.state_sha256
        ).to_json()
        action_id = svc.ids.new("a")
        body = dict(payload or {"decision": decision})
        svc.storage.insert(
            "actions",
            {
                "action_id": action_id,
                "type": action_type,
                "risk_class": risk_class,
                "repo_id": repo.repo_id,
                "resolved_repo_identity": repo.resolved_identity,
                "intent_uuid": snap.row.uuid if snap.row else None,
                "intent_dir": GATE_DIR,
                "space": "default",
                "stage": stage,
                "captured_state_hash": captured["state_hash"],
                "boundary_token": captured["boundary_token"],
                "question_digest": captured["question_digest"],
                "stage_attempt": captured["stage_attempt"],
                "evidence_digest": captured["evidence_digest"],
                "is_active_captured": int(bool(captured["is_active"])),
                "payload_json": body,
                "wire_text": wire_text,
                "human_text": "the human's own words",
                "status": status,
                "session_key": f"dashboard:{slot_key}",
                "slot_key": slot_key,
                "delivery_id": svc.ids.new("dl"),
                "lease_generation": lease_generation,
                "created_at": "2026-09-04T09:58:00Z",
                "updated_at": "2026-09-04T09:59:00Z",
                "delivering_at": delivering_at,
                "delivered_at": delivered_at,
                "deadline_at": deadline_at,
                "evidence_json": dict(evidence, **(evidence_extra or {})),
                "presence_baseline_json": baseline if presence else None,
                "boot_id": boot_id,
                "source": "studio",
                "waiting_since": "2026-09-04T09:00:00Z",
            },
        )
        return action_id

    return SimpleNamespace(
        root=root,
        repo=repo,
        repo_id=repo.repo_id,
        slot_key=slot_key,
        slot=fake_host.get_slot(slot_key),
        snapshot=snapshot,
        make=make,
    )


def approve_on_disk(root: Path, *, human_turns: int = 1, ts: str = "2026-09-04T10:00:00Z") -> None:
    """What the engine leaves behind after a real gate approval, with the human turn count dialled.

    ``human_turns=0`` is the ``audit-gate-approved`` variant with its ``HUMAN_TURN`` block removed and
    ``2`` is the two-row variant — the exact pair §1.13 pins as ``human_presence_anomaly``.
    ``GATE_APPROVED`` and ``STAGE_COMPLETED`` land in the same second as the turn, which is the
    same-second case review P23 exists for.
    """
    blocks = []
    for index in range(human_turns):
        blocks.append(F.audit_block("HUMAN_TURN", ts))
    blocks.append(F.audit_block("GATE_APPROVED", ts, Stage=GATE_STAGE, User_Input="Approve"))
    blocks.append(
        F.audit_block("STAGE_COMPLETED", ts, Stage=GATE_STAGE, Details=f"Stage {GATE_STAGE} approved")
    )
    shard = _record(root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(shard.read_text() + F.audit_text(*blocks))
    state = _record(root) / "aidlc-state.md"
    state.write_text(
        F.state_text(
            marks={GATE_STAGE: "x"},
            fields={"Current Stage": "user-stories", "Status": "Running", "Next Stage": "domain-model"},
        )
    )
    touch_human_turn(root)


def reject_on_disk(root: Path, *, ts: str = "2026-09-04T10:00:00Z") -> None:
    blocks = [
        F.audit_block("HUMAN_TURN", ts),
        F.audit_block("GATE_REJECTED", ts, Stage=GATE_STAGE, Feedback="Spell out the criteria."),
        F.audit_block("STAGE_REVISING", ts, Stage=GATE_STAGE, Revision_count="1"),
    ]
    shard = _record(root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(shard.read_text() + F.audit_text(*blocks))
    state = _record(root) / "aidlc-state.md"
    state.write_text(
        F.state_text(
            marks={GATE_STAGE: "R"},
            fields={"Current Stage": GATE_STAGE, "Status": "Running", "Revision Count": "1"},
        )
    )
    touch_human_turn(root)


def finish_turn(fake_host, slot_key: str, *, wire_text: str = "Approve",
                user_ts: str = "2026-09-04T09:59:30Z", reply_ts: str = "2026-09-04T10:00:10Z") -> None:
    """The transcript a completed turn leaves in the host's memory: the user row, then a reply.

    Both rows matter. The user row is the delivery evidence ``find_delivery_row`` matches; the assistant
    row behind it is what lets ``_turn_ended`` distinguish "the turn finished" from "the slot is idle
    because nothing ever started" — and only the second belongs to the deadline path.
    """
    slot = fake_host.get_slot(slot_key)
    slot.messages.append(
        {"role": "user", "content": wire_text, "cls": "msg msg-u", "ts": user_ts, "meta": {}}
    )
    slot.messages.append(
        {"role": "assistant", "content": "done", "cls": "msg msg-a", "ts": reply_ts, "meta": {}}
    )
    slot.running = False


def status_of(svc, action_id: str) -> str:
    return str(svc.storage.get("actions", action_id)["status"])


def evidence_of(svc, action_id: str) -> dict:
    return dict(svc.storage.get("actions", action_id).get("evidence_json") or {})


def finding_codes(svc, action_id: str) -> set[str]:
    return {
        str(f.get("code"))
        for f in evidence_of(svc, action_id).get("findings", [])
        if isinstance(f, dict)
    }


# --------------------------------------------------------------------------- #
# 1. startup ordering
# --------------------------------------------------------------------------- #


def test_startup_reevaluates_every_inflight_action_before_leases_or_scans(svc, scene, C):
    """Every mid-flight row is re-read before the lease sweep and the first scan run.

    The order is the point (§1.13): a reclaim decided against a stale status can hand a repository to a
    second dispatch, and a card derived before a delivered decision is re-read duplicates a boundary
    that is already closed.
    """
    order: list[str] = []
    original_reconcile = svc.reconciler.reconcile_action
    original_sweep = svc.scheduler.startup_sweep
    original_scan = svc.reconciler.scan_repo

    async def note_reconcile(rec, snap=None):
        order.append(f"reconcile:{rec.action_id}")
        return await original_reconcile(rec, snap)

    async def note_sweep():
        order.append("sweep")
        return await original_sweep()

    async def note_scan(repo):
        order.append("scan")
        return await original_scan(repo)

    svc.reconciler.reconcile_action = note_reconcile
    svc.scheduler.startup_sweep = note_sweep
    svc.reconciler.scan_repo = note_scan

    a = scene.make(status="Delivering")
    b = scene.make(status="Delivered", evidence_extra={"queued_at": "2026-09-04T09:59:05Z"})
    c = scene.make(status="Processing")

    report = asyncio.run(svc.reconciler.startup())

    assert order.index(f"reconcile:{a}") < order.index("sweep") < order.index("scan")
    assert {f"reconcile:{a}", f"reconcile:{b}", f"reconcile:{c}"} <= set(order)
    assert report.actions_reevaluated == 3
    assert report.integrity == ()


def test_startup_stamps_boot_id_mismatch_and_never_reaches_not_delivered(svc, scene, C, clock):
    """A gateway restart between the send and the ack can never yield ``NotDelivered`` (review R02).

    The host's transcript is process memory, so after a restart the strongest available evidence — "no
    user row" — is exactly what a lost transcript looks like. The ``boot_id`` stamp is what keeps the two
    apart, and it is written before the row is reconciled so nothing can be proven on evidence this
    process cannot have.
    """
    action_id = scene.make(status="Delivering", boot_id=OTHER_BOOT, deadline_at="2026-09-04T09:59:00Z")
    svc.host.attach(None)
    svc.host._state = None  # the restarted process has an empty slot table

    report = asyncio.run(svc.reconciler.startup())
    assert report.boot_id_mismatches == 1
    assert evidence_of(svc, action_id)["boot_id_unchanged"] is False

    for _ in range(4):
        clock.advance(10)
        asyncio.run(svc.reconciler.tick())
        assert status_of(svc, action_id) != "NotDelivered"
    assert status_of(svc, action_id) == "ReconciliationRequired"


def test_startup_report_publishes_health_updated(svc, scene):
    """The report is published, not returned only: `/health` and the UI read it from the event ring."""
    asyncio.run(svc.reconciler.startup())
    rows = svc.storage.select("events", {"type": "health.updated"})
    assert rows and "startup" in rows[0]["payload_json"]
    kinds = {row["kind"] for row in svc.storage.select("activity")}
    assert "health.startup" in kinds


# --------------------------------------------------------------------------- #
# 2. the ack deadline path
# --------------------------------------------------------------------------- #


def test_ack_deadline_with_user_row_found_lands_on_reconciliation_required(svc, scene, fake_host, clock):
    """The browser sent and never reported: the row is on disk, so the answer is "we cannot tell yet".

    Two ticks and two legal §11.1 edges — ``Delivering → DeliveryUncertain`` then
    ``DeliveryUncertain → ReconciliationRequired`` — with ``delivery_confirmed`` set from the transcript
    row. ``Delivering → NotDelivered`` is never asked for on this path (§1.13).
    """
    action_id = scene.make(status="Delivering", deadline_at="2026-09-04T09:59:00Z")
    finish_turn(fake_host, scene.slot_key)

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "DeliveryUncertain"
    ev = evidence_of(svc, action_id)
    assert ev["transcript_row"]["slot_key"] == scene.slot_key
    assert ev["transcript_row_absent"] is False

    clock.advance(5)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert evidence_of(svc, action_id)["delivery_confirmed"] is True

    kinds = [t for _, t, _ in svc.actions.transitions]
    assert "NotDelivered" not in kinds


def test_ack_deadline_matches_by_delivery_meta_before_content(svc, scene, fake_host):
    """``meta.studio_delivery_id`` is the primary key, so a decision whose text was edited still matches.

    Content matching alone would be defeated by the host redacting or normalising the message; the meta
    the browser sends on ``POST /api/chat`` is persisted on the host's own user row (review R08).
    """
    action_id = scene.make(status="Delivering", deadline_at="2026-09-04T09:59:00Z")
    delivery_id = svc.storage.get("actions", action_id)["delivery_id"]
    slot = fake_host.get_slot(scene.slot_key)
    slot.messages.append(
        {
            "role": "user",
            "content": "something else entirely",
            "cls": "msg msg-u",
            "ts": "2026-09-04T09:59:30Z",
            "meta": {"studio_delivery_id": delivery_id},
        }
    )

    asyncio.run(svc.reconciler.tick())
    assert evidence_of(svc, action_id)["transcript_row"]["ts"] == "2026-09-04T09:59:30Z"


def test_ack_deadline_with_no_row_and_all_four_proofs_reaches_not_delivered(svc, scene, clock):
    """``NotDelivered`` needs all four proofs, and only ever from ``ReconciliationRequired``.

    Row absent, slot never ran, the presence baseline byte-identical, same ``boot_id``. Any one of them
    missing and the exit is the human's, because a decision declared undelivered can be re-queued — and
    re-queueing one that was in fact delivered is the at-most-once violation the whole design is built
    to prevent (C24).
    """
    action_id = scene.make(status="Delivering", deadline_at="2026-09-04T09:59:00Z")

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "DeliveryUncertain"

    clock.advance(5)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ReconciliationRequired"
    ev = evidence_of(svc, action_id)
    assert ev["transcript_row_absent"] is True
    assert ev["slot_ran_since"] is False
    assert ev["disk_baseline_unchanged"] is True
    assert ev["boot_id_unchanged"] is True
    assert ev["delivery_confirmed"] is False

    clock.advance(5)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "NotDelivered"


@pytest.mark.parametrize("break_proof", ["slot_ran", "disk_moved"])
def test_not_delivered_refused_when_any_proof_is_missing(svc, scene, clock, fake_host, break_proof):
    action_id = scene.make(status="Delivering", deadline_at="2026-09-04T09:59:00Z")
    if break_proof == "slot_ran":
        slot = fake_host.get_slot(scene.slot_key)
        slot.messages.append(
            {"role": "assistant", "content": "hi", "cls": "a", "ts": "2026-09-04T09:59:40Z", "meta": {}}
        )
    else:
        touch_human_turn(scene.root)

    for _ in range(3):
        asyncio.run(svc.reconciler.tick())
        clock.advance(5)
    assert status_of(svc, action_id) == "ReconciliationRequired"


def test_delivery_uncertain_always_escalates(svc, scene):
    """No dead ends: ``DeliveryUncertain`` continues even with no new evidence at all (review R07)."""
    action_id = scene.make(status="DeliveryUncertain", deadline_at="2026-09-04T09:59:00Z")
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ReconciliationRequired"


# --------------------------------------------------------------------------- #
# 3. deadlines, queued deliveries and replaced sessions
# --------------------------------------------------------------------------- #


def test_queued_delivery_waits_in_delivered_until_the_turn_starts(svc, scene, fake_host, clock):
    """``{ok:true, queued:true}`` is delivered but not processing yet (review P22).

    Starting the turn timeout at the ack would make every busy-slot delivery time out early, so the
    action waits in ``Delivered`` and the deadline begins when the turn is *observed* to start.
    """
    action_id = scene.make(
        status="Delivered",
        deadline_at="2026-09-04T09:59:00Z",
        evidence_extra={"queued_at": "2026-09-04T09:59:05Z"},
    )
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "Delivered"

    fake_host.set_running(scene.slot_key, True)
    clock.advance(5)
    asyncio.run(svc.reconciler.tick())
    row = svc.storage.get("actions", action_id)
    assert row["status"] == "Processing"
    assert row["deadline_at"] > "2026-09-04T10:00:00Z"


def test_queued_delivery_gives_up_after_the_wait_factor(svc, scene, clock, C):
    action_id = scene.make(
        status="Delivered",
        deadline_at="2026-09-04T09:59:00Z",
        evidence_extra={"queued_at": "2026-09-04T09:59:05Z"},
    )
    clock.advance(C.DELIVERED_QUEUED_WAIT_FACTOR * svc.host.turn_timeout_secs() + 60)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "DeliveryUncertain"


def test_session_replaced_makes_the_action_uncertain(svc, scene, fake_host):
    """A slot whose effective session changed is not the session the decision was dispatched into.

    ``linked_session_key`` moving means the host now runs this slot's turns somewhere else, so the audit
    rows Studio would read could belong to another conversation entirely.
    """
    action_id = scene.make(status="Processing")
    fake_host.get_slot(scene.slot_key).linked_session_key = "channel:other"
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "DeliveryUncertain"


def test_slot_gone_makes_the_action_uncertain(svc, scene, fake_host):
    action_id = scene.make(status="Processing")
    fake_host._slots.pop(scene.slot_key)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "DeliveryUncertain"


def test_turn_past_its_deadline_becomes_uncertain(svc, scene, clock):
    action_id = scene.make(status="Processing", deadline_at="2026-09-04T10:00:00Z")
    clock.advance(120)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "DeliveryUncertain"


def test_detached_host_does_not_mistake_a_missing_slot_for_a_dead_session(svc, scene):
    """Disk-only is a reduced mode, not a terminated session (C08).

    Without this distinction a Studio that started before the gateway attached its state would push every
    in-flight decision to ``DeliveryUncertain`` on its first tick.
    """
    action_id = scene.make(status="Processing")
    svc.host._state = None
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "Processing"


# --------------------------------------------------------------------------- #
# 4. resolution contracts
# --------------------------------------------------------------------------- #


def test_gate_approve_resolves_state_changed_from_same_second_rows(svc, scene, fake_host):
    """``gate_approved_audit`` + a `[x]` row = ``StateChanged`` (review P23 same-second ordering)."""
    action_id = scene.make(status="Processing")
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key)

    asyncio.run(svc.reconciler.tick())
    row = svc.storage.get("actions", action_id)
    assert row["status"] == "StateChanged"
    assert row["resolution_json"]["kind"] == "state_changed"
    assert row["resolution_json"]["evidence"]["presence"]["ok"] is True


def test_gate_request_changes_needs_rejected_revising_and_the_r_row(svc, scene, fake_host):
    action_id = scene.make(status="Processing", decision="request_changes",
                           wire_text="Request Changes: Spell out the criteria.")
    reject_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key, wire_text="Request Changes: Spell out the criteria.")

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "StateChanged"


def test_gate_approve_without_the_audit_row_stays_processing(svc, scene, fake_host):
    """A finished turn is not a resolution. Only the engine's own ``GATE_APPROVED`` closes a gate."""
    action_id = scene.make(status="Processing")
    finish_turn(fake_host, scene.slot_key)
    touch_human_turn(scene.root)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "Processing"


def test_question_answers_resolve_no_transition(svc, scene, fake_host):
    """Answering a question moves the conversation, not the lifecycle → ``ResolvedNoTransition``."""
    snap = scene.snapshot()
    pending = [q.index for q in snap.questions.questions if not q.answered]
    action_id = scene.make(
        status="Processing",
        action_type="question",
        decision="answers",
        wire_text="Yes",
        payload={"decision": "answers", "answers": [{"index": pending[0], "option_letters": ["A"]}]},
        snap=snap,
    )
    path = _record(scene.root) / QUESTIONS_REL
    path.write_text(F.real_questions())
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(
        shard.read_text()
        + F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z"),
            F.audit_block("QUESTION_ANSWERED", "2026-09-04T10:00:00Z", Stage=GATE_STAGE, Details="A"),
        )
    )
    touch_human_turn(scene.root)
    finish_turn(fake_host, scene.slot_key, wire_text="Yes")

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_confirm_summary_resolves_on_the_audit_row(svc, scene, fake_host):
    action_id = scene.make(
        status="Processing",
        action_type="question",
        decision="confirm_summary",
        wire_text="Looks correct",
        payload={"decision": "confirm_summary", "choice": "looks_correct"},
    )
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(
        shard.read_text()
        + F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z"),
            F.audit_block(
                "SUMMARY_CONFIRMATION_RECORDED",
                "2026-09-04T10:00:00Z",
                Stage=GATE_STAGE,
                Checkpoint="Consolidated Summary Confirmation",
            ),
        )
    )
    touch_human_turn(scene.root)
    finish_turn(fake_host, scene.slot_key, wire_text="Looks correct")
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_approve_plan_needs_the_checkpoint_answer_in_the_file(svc, scene, fake_host, C):
    """The plan-approval checkpoint resolves from the file, not from an audit row.

    ``plan-approval-guard`` is a hook, not an audit event, so the only durable trace of the approval is
    the ``## Plan Approval`` tag holding ``Approve Plan`` (C34/FORMAT-NOTES §7).
    """
    questions = (
        "# Code Generation — Questions\n\n---\n\n"
        "## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n\n"
    )
    path = _record(scene.root) / QUESTIONS_REL
    path.write_text(questions)
    action_id = scene.make(
        status="Processing",
        action_type="question",
        decision="approve_plan",
        wire_text=C.WIRE_APPROVE_PLAN,
        payload={"decision": "approve_plan"},
    )
    path.write_text(questions.replace("[Answer]:", f"[Answer]: {C.WIRE_APPROVE_PLAN}"))
    touch_human_turn(scene.root)
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(shard.read_text() + F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z"))
    finish_turn(fake_host, scene.slot_key, wire_text=C.WIRE_APPROVE_PLAN)

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_request_plan_changes_resolves_when_the_tag_is_cleared_for_revision(svc, scene, fake_host, C):
    """Either shape counts: the tag now holds ``Request Changes…``, or it was cleared for the revision.

    Both are what the engine really leaves behind (FORMAT-NOTES §7 records a plan-approval answer that
    was cleared and re-approved after revision), and a rule that demanded only one of them would leave
    half of the real rejections unresolvable.
    """
    answered = (
        "# Code Generation — Questions\n\n---\n\n"
        "## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]: Approve Plan\n\n"
    )
    path = _record(scene.root) / QUESTIONS_REL
    path.write_text(answered)
    action_id = scene.make(
        status="Processing",
        action_type="question",
        decision="request_plan_changes",
        wire_text=C.WIRE_REQUEST_CHANGES_PREFIX + "Split the plan.",
        payload={"decision": "request_plan_changes", "feedback": "Split the plan."},
    )
    path.write_text(answered.replace("[Answer]: Approve Plan", "[Answer]:"))
    touch_human_turn(scene.root)
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(shard.read_text() + F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z"))
    finish_turn(fake_host, scene.slot_key, wire_text=C.WIRE_REQUEST_CHANGES_PREFIX + "Split the plan.")

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_resolution_events_mirror_the_bundled_engines_gate_resolution_set(C):
    """``RESOLUTION_EVENTS`` is read out of the engine, not maintained by hand (constants.py:535).

    The engine grew ``PLAN_APPROVAL_RECORDED`` into ``GATE_RESOLUTION_EVENTS`` in 2.7.1 and Studio's copy
    did not notice, because nothing compared them. This does: the bundled harness is the authority, so the
    next member the engine adds fails here instead of quietly narrowing "did the boundary move".
    ``AUTONOMY_MODE_SET`` is the one documented non-mirror — it is conditional on ``Mode: autonomous``,
    which Studio never produces — so it is subtracted rather than expected.
    """
    lib = (APP_ROOT / "payload" / "aidlc-kiro" / ".kiro" / "tools" / "aidlc-lib.ts").read_text("utf-8")
    start = lib.index("GATE_RESOLUTION_EVENTS = new Set([")
    engine = set(re.findall(r'"([A-Z_]+)"', lib[start : lib.index("]);", start)]))
    assert engine, "the engine's own set could not be read; the mirror below would be vacuous"
    assert set(C.RESOLUTION_EVENTS) == engine - {"AUTONOMY_MODE_SET"}


def test_a_plan_approval_row_counts_as_a_gate_resolution(svc, scene, fake_host, clock):
    """2.7.1 answers a Plan Approval checkpoint with its own row, and that row resolves a boundary.

    The card itself still resolves from the questions file (the test above), so what is at stake here is
    the evidence a human reconciling a timed-out turn is handed: ``resolution_event_seen`` is how they are
    told the engine did record an answer, and an unclassified resolution reads as "nothing on disk".
    """
    action_id = scene.make(status="Processing", deadline_at="2026-09-04T10:00:00Z")
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(
        shard.read_text()
        + F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z"),
            F.audit_block(
                "PLAN_APPROVAL_RECORDED",
                "2026-09-04T10:00:00Z",
                Stage=GATE_STAGE,
                Checkpoint="Plan Approval",
            ),
        )
    )
    touch_human_turn(scene.root)
    clock.advance(120)

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "DeliveryUncertain"
    assert evidence_of(svc, action_id)["disk_movement"]["resolution_event_seen"] is True


def test_a_plan_approval_row_is_movement_for_a_run_action(svc, scene, fake_host, C):
    """A run whose only trace is ``PLAN_APPROVAL_RECORDED`` moved the workflow, not merely ended.

    Compare the test above it: with ``HUMAN_TURN`` alone the same run is ``ResolvedNoTransition``. The
    difference is entirely whether the plan-approval row is classified, and the user-visible stake is a
    Code Generation plan they approved being reported as "nothing happened".
    """
    action_id = scene.make(
        status="Processing",
        action_type="run",
        decision="run",
        stage=GATE_STAGE,
        wire_text=C.WIRE_RUN,
        payload={"decision": "run"},
    )
    touch_human_turn(scene.root)
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(
        shard.read_text()
        + F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z"),
            F.audit_block(
                "PLAN_APPROVAL_RECORDED",
                "2026-09-04T10:00:01Z",
                Stage=GATE_STAGE,
                Checkpoint="Plan Approval",
            ),
        )
    )
    finish_turn(fake_host, scene.slot_key, wire_text=C.WIRE_RUN)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "StateChanged"
    assert svc.storage.get("actions", action_id)["resolution_json"]["reason"] == "workflow_moved"


def test_provide_input_scope_resolves_only_when_the_state_records_the_scope(svc, scene, fake_host, C):
    """``missing_input``/``provide_input`` with a scope resolves on the field, not on the turn.

    The turn ending proves nothing here: the conductor may have asked a follow-up question instead of
    writing the scope, and calling that ``StateChanged`` would report a scope AI-DLC never recorded.
    """
    action_id = scene.make(
        status="Processing",
        action_type="missing_input",
        decision="provide_input",
        stage=None,
        wire_text=C.WIRE_SCOPE_PREFIX + "bugfix",
        payload={"decision": "provide_input", "kind": "scope", "scope": "bugfix"},
    )
    touch_human_turn(scene.root)
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(shard.read_text() + F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z"))
    finish_turn(fake_host, scene.slot_key, wire_text=C.WIRE_SCOPE_PREFIX + "bugfix")

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "Processing"

    (_record(scene.root) / "aidlc-state.md").write_text(
        F.state_text(marks={GATE_STAGE: "?"}, fields={"Scope": "bugfix", "Current Stage": GATE_STAGE})
    )
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "StateChanged"


def test_resume_resolves_like_run(svc, scene, fake_host, C):
    action_id = scene.make(
        status="Processing",
        action_type="resume",
        decision="resume",
        stage=GATE_STAGE,
        wire_text=C.WIRE_RESUME,
        payload={"decision": "resume"},
    )
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key, wire_text=C.WIRE_RESUME)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "StateChanged"


def test_run_resolves_state_changed_when_the_workflow_moved(svc, scene, fake_host, C):
    action_id = scene.make(
        status="Processing",
        action_type="run",
        decision="run",
        stage=GATE_STAGE,
        wire_text=C.WIRE_RUN,
        payload={"decision": "run"},
    )
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key, wire_text=C.WIRE_RUN)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "StateChanged"


def test_run_resolves_no_transition_when_the_turn_ended_without_movement(svc, scene, fake_host, C):
    """The engine asked a question or parked: a completed turn with no movement is still a resolution."""
    action_id = scene.make(
        status="Processing",
        action_type="run",
        decision="run",
        stage=GATE_STAGE,
        wire_text=C.WIRE_RUN,
        payload={"decision": "run"},
    )
    touch_human_turn(scene.root)
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(shard.read_text() + F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z"))
    finish_turn(fake_host, scene.slot_key, wire_text=C.WIRE_RUN)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_prepare_commit_resolves_on_the_turn_alone(svc, scene, fake_host, C):
    action_id = scene.make(
        status="Processing",
        action_type="prepare_commit",
        decision="prepare_commit",
        stage=GATE_STAGE,
        wire_text=C.WIRE_PREPARE_COMMIT,
        payload={"decision": "prepare_commit"},
    )
    touch_human_turn(scene.root)
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(shard.read_text() + F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z"))
    finish_turn(fake_host, scene.slot_key, wire_text=C.WIRE_PREPARE_COMMIT)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_force_stop_resolves_when_the_slot_goes_idle_and_records_the_interruption(svc, scene, fake_host):
    """A resolved force stop writes ``interrupted_at`` and nothing clears it here (C28/review P17).

    ``Interrupted → ReconciliationRequired → Queued|Paused`` must stay the only way back: PRD §11.2
    requires manual takeover after a mid-stage interruption, so run/resume never clear the flag.
    """
    action_id = scene.make(
        status="Processing",
        action_type="force_stop",
        decision="force_stop",
        risk_class="host_control",
        stage=None,
        wire_text=None,
        payload={"decision": "force_stop"},
        presence=False,
    )
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ResolvedNoTransition"

    binding = asyncio.run(svc.sessions.get(scene.repo_id, "default", GATE_DIR))
    assert binding.interrupted_at is not None
    kinds = {row["kind"] for row in svc.storage.select("activity")}
    assert "intent.force_stop" in kinds


def test_force_stop_stays_pending_while_the_stop_is_in_progress(svc, scene, fake_host):
    action_id = scene.make(
        status="Processing",
        action_type="force_stop",
        decision="force_stop",
        risk_class="host_control",
        stage=None,
        wire_text=None,
        payload={"decision": "force_stop"},
        presence=False,
    )
    fake_host.get_slot(scene.slot_key)._stop_state = "killing"
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "Processing"


# --------------------------------------------------------------------------- #
# 5. the presence invariant, per dispatch class
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("human_turns", [0, 2])
def test_gate_approved_without_exactly_one_human_turn_is_an_anomaly(svc, scene, fake_host, human_turns, clock):
    """Zero or two ``HUMAN_TURN`` rows → ``human_presence_anomaly``, never a resolution (review P04).

    Zero means AI-DLC recorded an approval nobody gave — the FR-SES-010 case. Two means the boundary
    cannot be attributed to *this* decision. The route out is ``DeliveryUncertain`` then
    ``ReconciliationRequired``, because ``Processing → ReconciliationRequired`` is not an edge of PRD
    §11.1 and inventing it would put a state outside the graph into the record.
    """
    action_id = scene.make(status="Processing")
    approve_on_disk(scene.root, human_turns=human_turns)
    finish_turn(fake_host, scene.slot_key)

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "DeliveryUncertain"
    assert "human_presence_anomaly" in finding_codes(svc, action_id)

    clock.advance(5)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert "human_presence_anomaly" in finding_codes(svc, action_id)


def test_presence_requires_the_marker_to_advance_too(svc, scene, fake_host):
    """One new ``HUMAN_TURN`` with a stale ``.aidlc-human-turn`` is not a human turn.

    The audit row is written by a hook an agent can trigger; the marker mtime is stamped by the adapter
    on a real user prompt. Requiring both is what makes the invariant a presence check rather than a
    log check (C23).
    """
    marker = _record(scene.root) / HUMAN_TURN_MARKER
    marker.write_text("2026-09-04T09:00:00Z\n")
    os.utime(marker, ns=(1_000_000_000, 1_000_000_000))
    action_id = scene.make(status="Processing")

    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(
        shard.read_text()
        + F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z"),
            F.audit_block("GATE_APPROVED", "2026-09-04T10:00:00Z", Stage=GATE_STAGE, User_Input="Approve"),
        )
    )
    (_record(scene.root) / "aidlc-state.md").write_text(
        F.state_text(marks={GATE_STAGE: "x"}, fields={"Current Stage": "user-stories"})
    )
    os.utime(marker, ns=(1_000_000_000, 1_000_000_000))
    finish_turn(fake_host, scene.slot_key)

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "DeliveryUncertain"
    assert "human_presence_anomaly" in finding_codes(svc, action_id)


def test_host_control_dispatch_has_no_presence_requirement(svc, scene):
    """``force_stop`` puts nothing in front of the model, so it needs no ``HUMAN_TURN`` (§1.12)."""
    action_id = scene.make(
        status="Processing",
        action_type="force_stop",
        decision="force_stop",
        risk_class="host_control",
        stage=None,
        wire_text=None,
        payload={"decision": "force_stop"},
        presence=False,
    )
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_cursor_moved_between_dispatch_and_resolution_is_an_anomaly(svc, scene, fake_host, clock):
    """The conductor resolves the record from the repo-shared cursor, so a moved cursor invalidates the
    reading (review R01/C22): the audit rows Studio is about to trust may belong to another intent."""
    action_id = scene.make(status="Processing")
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key)
    (scene.root / "aidlc" / "spaces" / "default" / "intents" / "active-intent").write_text("someone-else\n")

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "DeliveryUncertain"
    assert "cursor_moved" in finding_codes(svc, action_id)


def test_row_found_in_another_slot_is_wrong_slot_and_waits_for_a_human(svc, scene, fake_host, clock):
    """A row in a different slot of the same repository is not a delivery Studio may resolve (review R08).

    The decision reached the host but not the session it was captured against, so neither
    ``NotDelivered`` (it was delivered) nor a disk resolution (it may have run against another cursor) is
    safe. The finding is stored and the card waits.
    """
    action_id = scene.make(status="DeliveryUncertain", deadline_at="2026-09-04T09:59:00Z")
    fake_host.add_slot("hand-made", project=str(scene.root), agent="aidlc")
    fake_host.get_slot("hand-made").messages.append(
        {"role": "user", "content": "Approve", "cls": "u", "ts": "2026-09-04T09:59:30Z", "meta": {}}
    )

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert evidence_of(svc, action_id)["wrong_slot"] == "hand-made"

    clock.advance(5)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert "wrong_slot" in finding_codes(svc, action_id)


# --------------------------------------------------------------------------- #
# 6. stable boundary, retirement, reclaim
# --------------------------------------------------------------------------- #


def test_stable_boundary_is_recorded_and_names_every_unmet_condition(svc, scene, fake_host):
    """PRD §12.2 has five conditions and the record has to say which of them failed.

    An in-flight action alone makes the boundary unstable — that is what stops any automation from
    treating a boundary as safe while a decision Studio itself sent is still open.
    """
    asyncio.run(svc.reconciler.tick())
    binding = asyncio.run(svc.sessions.get(scene.repo_id, "default", GATE_DIR))
    assert binding.last_stable_boundary is not None
    assert binding.last_stable_boundary.marker == "[?]"
    assert binding.last_stable_boundary.stable is True

    scene.make(status="Processing")
    asyncio.run(svc.reconciler.tick())
    binding = asyncio.run(svc.sessions.get(scene.repo_id, "default", GATE_DIR))
    assert binding.last_stable_boundary.stable is False
    assert "action_in_flight" in binding.last_stable_boundary.reasons


def test_busy_slot_makes_the_boundary_unstable(svc, scene, fake_host):
    fake_host.set_running(scene.slot_key, True)
    asyncio.run(svc.reconciler.tick())
    binding = asyncio.run(svc.sessions.get(scene.repo_id, "default", GATE_DIR))
    assert "session_busy" in binding.last_stable_boundary.reasons


def test_stale_card_retires_while_the_live_boundary_keeps_its_card(svc, scene):
    """A card whose boundary is gone is cancelled; the one still on disk is not touched (FR-ACT-007).

    The dedupe key is the hinge: retiring by anything looser would dismiss a ``Queued`` gate card while
    its `[?]` row is still on disk, which is the one thing a queue must never do.
    """
    asyncio.run(svc.reconciler.tick())
    live = asyncio.run(svc.actions.list_live(repo_id=scene.repo_id, include_revision=True))
    gate = next(a for a in live if a.type == "gate")

    stale_id = svc.ids.new("a")
    svc.storage.insert(
        "actions",
        {
            "action_id": stale_id,
            "type": "gate",
            "risk_class": "human_lane",
            "repo_id": scene.repo_id,
            "intent_dir": GATE_DIR,
            "space": "default",
            "stage": "user-stories",
            "status": "Queued",
            "created_at": "2026-09-04T09:00:00Z",
            "updated_at": "2026-09-04T09:00:00Z",
            "waiting_since": "2026-09-04T09:00:00Z",
            "source": "studio",
            "dedupe_key": "gate:vanished:boundary",
            "evidence_json": {},
        },
    )
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, stale_id) == "Cancelled"
    assert status_of(svc, gate.action_id) == "Queued"


def test_failed_cards_are_marked_superseded_and_never_cancelled(svc, scene):
    """``Failed`` is terminal (review P07). Retirement marks it, it does not move it.

    Moving a ``Failed`` row to ``Cancelled`` would erase the fingerprint and the breaker context that are
    the only things the user can act on.
    """
    failed_id = svc.ids.new("a")
    svc.storage.insert(
        "actions",
        {
            "action_id": failed_id,
            "type": "run",
            "risk_class": "human_lane",
            "repo_id": scene.repo_id,
            "intent_dir": GATE_DIR,
            "space": "default",
            "status": "Failed",
            "created_at": "2026-09-04T09:00:00Z",
            "updated_at": "2026-09-04T09:00:00Z",
            "waiting_since": "2026-09-04T09:00:00Z",
            "source": "studio",
            "dedupe_key": "run:vanished:boundary",
            "evidence_json": {},
        },
    )
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, failed_id) == "Failed"
    assert evidence_of(svc, failed_id)["superseded"] is True


def _hide_intent(root: Path, intent_dir: str = GATE_DIR) -> Path:
    """Move the intent directory out of the space — a branch switch or a hand deletion as the scan sees it."""
    record = _record(root, intent_dir)
    hidden = root / f".hidden-{intent_dir}"
    record.rename(hidden)
    return hidden


def test_cards_of_an_intent_that_left_the_disk_are_retired_after_the_grace(svc, scene, clock, C):
    """§1.13 step 1b (FR-ACT-007). ``retire_stale`` runs per intent the scan *finds*, so an intent that
    disappeared whole used to keep every Queued card it had, forever: a gate card for a boundary that
    no longer exists, a health count that kept counting the intent. The sweep is graced: absence has
    to be observed for ``INTENT_MISSING_GRACE_SECS`` before it is believed, because a listing can be
    empty for one tick while a checkout is mid-flight.
    """
    asyncio.run(svc.reconciler.tick())
    live = asyncio.run(svc.actions.list_live(repo_id=scene.repo_id, include_revision=True))
    gate = next(a for a in live if a.type == "gate")
    assert svc.reconciler.intents_on_disk() == 1
    failed_id = svc.ids.new("a")
    svc.storage.insert(
        "actions",
        {
            "action_id": failed_id,
            "type": "failure",
            "risk_class": "studio_only",
            "repo_id": scene.repo_id,
            "intent_dir": GATE_DIR,
            "space": "default",
            "status": "Failed",
            "created_at": "2026-09-04T09:00:00Z",
            "updated_at": "2026-09-04T09:00:00Z",
            "waiting_since": "2026-09-04T09:00:00Z",
            "source": "studio",
            "dedupe_key": "failure:gone:fp",
            "evidence_json": {},
        },
    )

    _hide_intent(scene.root)
    asyncio.run(svc.reconciler.tick())
    # Inside the grace: nothing moves, but the count already says what the disk says.
    assert status_of(svc, gate.action_id) == "Queued"
    assert "intent_missing" not in svc.actions.retire_reasons
    assert svc.reconciler.intents_on_disk() == 0

    clock.advance(C.INTENT_MISSING_GRACE_SECS + 1)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, gate.action_id) == "Cancelled"
    assert svc.actions.retire_reasons[-1] == "intent_missing"
    # ``Failed`` is terminal (P07): marked, never moved — the same rule as the per-intent retirement.
    assert status_of(svc, failed_id) == "Failed"
    assert evidence_of(svc, failed_id)["superseded"] is True
    # Nothing left to remember: a second pass finds no live card for the vanished intent.
    asyncio.run(svc.reconciler.tick())
    assert svc.actions.retire_reasons.count("intent_missing") == 1


def test_an_intent_that_comes_back_inside_the_grace_keeps_every_card(svc, scene, clock, C):
    """A checkout that briefly hides the directory must not cost the human their gate card."""
    asyncio.run(svc.reconciler.tick())
    live = asyncio.run(svc.actions.list_live(repo_id=scene.repo_id, include_revision=True))
    gate = next(a for a in live if a.type == "gate")

    hidden = _hide_intent(scene.root)
    asyncio.run(svc.reconciler.tick())
    clock.advance(C.INTENT_MISSING_GRACE_SECS // 2)
    hidden.rename(_record(scene.root))
    asyncio.run(svc.reconciler.tick())
    # The timer is dropped the moment the directory is seen again, so a later absence starts afresh.
    clock.advance(C.INTENT_MISSING_GRACE_SECS)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, gate.action_id) == "Queued"
    assert "intent_missing" not in svc.actions.retire_reasons
    assert svc.reconciler.intents_on_disk() == 1


def test_an_unavailable_repository_never_sweeps_its_cards(svc, scene, clock, C):
    """An unmounted repository says nothing about its intents — every directory "vanished" at once.

    The sweep only believes a listing the repository actually produced, and it forgets its timers while
    the repository is away, so a remount cannot retire cards on evidence gathered while it was gone.
    """
    asyncio.run(svc.reconciler.tick())
    live = asyncio.run(svc.actions.list_live(repo_id=scene.repo_id, include_revision=True))
    gate = next(a for a in live if a.type == "gate")

    parked = scene.root.parent / f"{scene.root.name}-parked"
    scene.root.rename(parked)
    try:
        asyncio.run(svc.reconciler.tick())
        clock.advance(C.INTENT_MISSING_GRACE_SECS * 2)
        asyncio.run(svc.reconciler.tick())
        assert svc.repos.get(scene.repo_id).availability != "available"
        assert status_of(svc, gate.action_id) == "Queued"
        assert "intent_missing" not in svc.actions.retire_reasons
    finally:
        parked.rename(scene.root)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, gate.action_id) == "Queued"
    assert svc.reconciler.intents_on_disk() == 1


def test_a_truncated_listing_never_sweeps_cards(svc, scene, clock, C, monkeypatch):
    """A listing cut at ``MAX_INTENTS_PER_REPO`` is a lower bound, never proof that an intent is gone."""
    asyncio.run(svc.reconciler.tick())
    live = asyncio.run(svc.actions.list_live(repo_id=scene.repo_id, include_revision=True))
    gate = next(a for a in live if a.type == "gate")

    # A second intent sorts before the gate's directory, so a cap of one lists only the newcomer and
    # the gate intent falls off the end of the listing without leaving the disk.
    other = _record(scene.root, "260901-earlier")
    other.mkdir(parents=True)
    (other / "aidlc-state.md").write_text((_record(scene.root) / "aidlc-state.md").read_text())
    monkeypatch.setattr(C, "MAX_INTENTS_PER_REPO", 1)
    asyncio.run(svc.reconciler.tick())
    clock.advance(C.INTENT_MISSING_GRACE_SECS * 2)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, gate.action_id) == "Queued"
    assert "intent_missing" not in svc.actions.retire_reasons


def test_resolution_releases_the_execution_lease(svc, scene, fake_host):
    """A resolved action gives its repository back; nothing else may."""
    action_id = scene.make(status="Processing")
    grant = asyncio.run(
        svc.scheduler.acquire_execution(
            scene.repo, intent_uuid=None, session_key=f"dashboard:{scene.slot_key}", action_id=action_id
        )
    )
    svc.storage.cas_update("actions", action_id, 0, {"lease_generation": grant.generation})
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key)

    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "StateChanged"
    assert asyncio.run(svc.scheduler.get(scene.repo.resolved_identity)) is None


def test_lease_for_a_never_dispatched_action_is_reclaimed(svc, scene, clock):
    """The crash window between ``acquire_execution`` and the ``Delivering`` CAS (review P15/C31).

    Nothing was handed to the host, so the reclaim needs no boundary — but it still needs the *proof*
    that nothing was dispatched, never the lease's age.
    """
    action_id = svc.ids.new("a")
    svc.storage.insert(
        "actions",
        {
            "action_id": action_id,
            "type": "run",
            "risk_class": "human_lane",
            "repo_id": scene.repo_id,
            "intent_dir": GATE_DIR,
            "space": "default",
            "status": "Queued",
            "created_at": "2026-09-04T09:00:00Z",
            "updated_at": "2026-09-04T09:00:00Z",
            "waiting_since": "2026-09-04T09:00:00Z",
            "source": "studio",
            "evidence_json": {},
        },
    )
    asyncio.run(
        svc.scheduler.acquire_execution(
            scene.repo, intent_uuid=None, session_key=f"dashboard:{scene.slot_key}", action_id=action_id
        )
    )
    asyncio.run(svc.reconciler.tick())
    assert asyncio.run(svc.scheduler.get(scene.repo.resolved_identity)) is not None, (
        "a fresh lease must never be reclaimed on age alone"
    )

    clock.advance(300)
    asyncio.run(svc.reconciler.tick())
    assert asyncio.run(svc.scheduler.get(scene.repo.resolved_identity)) is None


# --------------------------------------------------------------------------- #
# 7. failure classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "message,expected",
    [
        ("ACP stream closed unexpectedly", "transport"),
        ("connection reset by peer", "transport"),
        ("ECONNRESET", "transport"),
        ("Rate limit exceeded, retry later", "rate_limit"),
        ("HTTP 429 Too Many Requests", "rate_limit"),
        ("request was throttled", "rate_limit"),
        ("turn exceeded the 7200s ceiling", "acp_timeout"),
        ("TimeoutError: no response", "acp_timeout"),
        ("Refusing to record this answer: a real human has not acted", "guard"),
        ("Unknown intent: nope", "validation"),
        ("Ambiguous intent", "validation"),
        ("Invalid scope: banana", "validation"),
        ("Cannot use --resume here", "validation"),
        ("EACCES: permission denied, open '/x'", "permission"),
        ("bun: command not found", "dependency"),
        ("Cannot find module 'aidlc-state.ts'", "dependency"),
        ("something else entirely", "config"),
    ],
)
def test_failure_classification_table(svc, message, expected):
    fingerprint, cls, summary = svc.reconciler.classify_failure(host_error=message, audit_errors=[])
    assert cls == expected
    assert fingerprint.startswith(f"{expected}.")
    assert summary == f"template.failure.{expected}"


def test_fingerprints_are_stable_across_volatile_detail(svc):
    """The same failure must fingerprint the same twice, or the breaker never reaches its threshold."""
    a, _, _ = svc.reconciler.classify_failure(
        host_error="EACCES: permission denied, open '/Users/a/repo/x.md' (pid 1234)", audit_errors=[]
    )
    b, _, _ = svc.reconciler.classify_failure(
        host_error="EACCES: permission denied, open '/Users/b/other/y.md' (pid 99)", audit_errors=[]
    )
    assert a == b


def test_error_burst_fails_the_action_and_a_single_row_does_not(svc, scene, fake_host, clock):
    """Three ``ERROR_LOGGED`` rows are a failure; one is AI-DLC correcting itself (FORMAT-NOTES §6)."""
    action_id = scene.make(status="Processing")
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(
        shard.read_text()
        + F.audit_block(
            "ERROR_LOGGED", "2026-09-04T09:59:30Z", Tool="aidlc-orchestrate.ts", Error="ACP stream closed"
        )
    )
    finish_turn(fake_host, scene.slot_key)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "Processing"

    # The error text is given explicitly: `error_burst_audit`'s default ("ACP transport closed") is not
    # one of the strings §1.13.1 pins for the `transport` class, so it classifies as `config` — see the
    # module report.
    shard.write_text(
        shard.read_text()
        + F.error_burst_audit(ts="2026-09-04T09:59:40Z", count=3, error="ACP stream closed")
    )
    clock.advance(5)
    asyncio.run(svc.reconciler.tick())
    assert status_of(svc, action_id) == "Failed"
    assert svc.actions.failures[-1]["class"] == "transport"


@pytest.mark.parametrize("initial_status", ["Processing", "ReconciliationRequired"])
def test_running_question_can_recover_from_tool_errors_before_it_finishes(svc, scene, fake_host, initial_status):
    action_id = scene.make(
        status=initial_status, action_type="question", decision="answers", wire_text="Yes",
        evidence_extra={"delivery_confirmed": True},
        payload={"decision": "answers", "answers": [{"index": 1, "option_letters": ["A"]}]},
    )
    shard = _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md"
    shard.write_text(shard.read_text()
                     + F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z")
                     + F.error_burst_audit(ts="2026-09-04T10:00:01Z", count=3,
                                           error="Missing --details <text>"))
    touch_human_turn(scene.root)
    scene.slot.running = True
    asyncio.run(svc.reconciler.reconcile_now(action_id))
    assert status_of(svc, action_id) == initial_status
    assert svc.actions.failures == []

    shard.write_text(shard.read_text() + F.audit_block(
        "QUESTION_ANSWERED", "2026-09-04T10:00:09Z", Stage=GATE_STAGE, Details="Yes"
    ))
    finish_turn(fake_host, scene.slot_key, wire_text="Yes")
    asyncio.run(svc.reconciler.reconcile_now(action_id))
    assert status_of(svc, action_id) == "ResolvedNoTransition"
    assert svc.actions.failures == []


# --------------------------------------------------------------------------- #
# 8. scan pool, budget and retention
# --------------------------------------------------------------------------- #


def test_scan_fanout_bounded(svc, studio, tmp_path, repo_builder, monkeypatch, R, C):
    """At most ``SCAN_CONCURRENCY`` ``scan_repo`` calls run at once (review R11/C32).

    Asserted by counting concurrency inside the *bounded* part of the call, because the invariant that
    matters is how many repository walks are in flight — not how many tasks were created.
    """
    concurrent = 0
    peak = 0
    original = R.Reconciler._scan_repo_locked

    async def counted(self, repo):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        try:
            await asyncio.sleep(0.01)
            return await original(self, repo)
        finally:
            concurrent -= 1

    monkeypatch.setattr(R.Reconciler, "_scan_repo_locked", counted)

    repos = []
    for index in range(6):
        root = tmp_path / f"repo{index}"
        root.mkdir()
        (root / "aidlc").mkdir()
        repos.append(svc.repos.add(str(root)))

    async def drive():
        return await asyncio.gather(*(svc.reconciler.scan_repo(r) for r in repos))

    asyncio.run(drive())
    assert peak <= C.SCAN_CONCURRENCY
    assert peak >= 1


def test_tick_honours_the_scan_budget_and_rotates(svc, studio, tmp_path, monkeypatch, R):
    """A tick stops starting scans once its budget is spent; the remainder leads the next tick.

    Without the rotation the tail of a large registry would never be scanned at all: the same first N
    repositories would consume the budget every five seconds.
    """
    seen: list[list[str]] = []

    async def slow(self, repo):
        seen[-1].append(repo.repo_id)
        self._s.clock.advance(3.0)
        return None

    monkeypatch.setattr(R.Reconciler, "_scan_bounded", slow)
    for index in range(6):
        root = tmp_path / f"budget{index}"
        root.mkdir()
        svc.repos.add(str(root))

    seen.append([])
    asyncio.run(svc.reconciler.tick())
    first = list(seen[-1])
    seen.append([])
    asyncio.run(svc.reconciler.tick())
    second = list(seen[-1])

    assert 0 < len(first) < 6
    assert second and second[0] != first[0]


def test_tick_never_blocks_the_loop(svc, scene, R, monkeypatch):
    """Disk work goes to a worker thread; a tick must not stall the gateway loop (§0.6).

    Measured rather than reasoned about: a heartbeat coroutine scheduled alongside the tick has to get
    its turns while a two-second snapshot is in progress, and the gateway's watchdog kills the process
    after ~25 s of a blocked loop.
    """
    beats = 0

    async def beat():
        nonlocal beats
        for _ in range(20):
            await asyncio.sleep(0)
            beats += 1

    real_snapshot = svc.projection.snapshot

    def slow_snapshot(*a, **k):
        time.sleep(0.05)
        return real_snapshot(*a, **k)

    svc.projection.snapshot = slow_snapshot

    async def drive():
        await asyncio.gather(svc.reconciler.tick(), beat())

    asyncio.run(drive())
    assert beats == 20


def test_human_text_is_purged_past_retention(svc, scene, clock):
    """Both homes of the verbatim decision text are cleared, or the promise is not kept."""
    action_id = scene.make(status="Processing")
    asyncio.run(
        svc.activity.record(
            kind="action.submitted",
            repo_id=scene.repo_id,
            action_id=action_id,
            params={},
            human_text="the human's own words",
        )
    )
    assert svc.storage.get("actions", action_id)["human_text"] == "the human's own words"

    clock.advance((svc.settings.human_text_retention_days() + 1) * 86400)
    asyncio.run(svc.reconciler.tick())

    assert svc.storage.get("actions", action_id)["human_text"] is None
    rows = svc.storage.select("activity", {"kind": "action.submitted"})
    assert all(row.get("human_text") is None for row in rows)


def test_expired_advisor_drafts_are_marked_not_deleted(svc, scene, clock, advisor):
    """Retention retires the row through the broker, so the tab watching the card hears about it.

    The ``advisor`` fixture is what makes this a real assertion: expiry is the broker's write, and a
    container without one expires nothing rather than reaching into the table behind its back.
    """
    svc.storage.insert(
        "advisor_drafts",
        {
            "draft_id": svc.ids.new("d"),
            "action_id": scene.make(status="Processing"),
            "kind": "gate_analysis",
            "status": "ready",
            "request_json": {},
            "created_at": clock.iso(),
            "updated_at": clock.iso(),
            "expires_at": "2026-09-04T09:00:00Z",
        },
    )
    asyncio.run(svc.reconciler.tick())
    rows = svc.storage.select("advisor_drafts")
    assert rows and rows[0]["status"] == "expired"


def test_reconcile_now_answers_for_one_action(svc, scene, fake_host):
    action_id = scene.make(status="Processing")
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key)
    rec = asyncio.run(svc.reconciler.reconcile_now(action_id))
    assert rec.status == "StateChanged"


def test_heartbeat_only_refreshes_leases_whose_action_is_in_flight(svc, scene, fake_host):
    """A lease whose action already settled must not be kept warm: that hides it from the reclaim rules."""
    action_id = scene.make(status="Processing")
    grant = asyncio.run(
        svc.scheduler.acquire_execution(
            scene.repo, intent_uuid=None, session_key=f"dashboard:{scene.slot_key}", action_id=action_id
        )
    )
    assert asyncio.run(svc.reconciler.heartbeat_once()) == 1

    svc.storage.cas_update("actions", action_id, 0, {"status": "StateChanged"})
    assert asyncio.run(svc.reconciler.heartbeat_once()) == 0
    lease = asyncio.run(svc.scheduler.get(scene.repo.resolved_identity))
    assert lease is not None and lease.generation == grant.generation


def test_reconciler_never_writes_aidlc_state(svc, scene, fake_host):
    """The whole product in one assertion: reconciliation reads AI-DLC and writes only Studio's rows."""
    watched = [
        _record(scene.root) / "aidlc-state.md",
        _record(scene.root) / "audit" / "fixture-host-0f1e2d3c4b5a.md",
        _record(scene.root) / QUESTIONS_REL,
        scene.root / "aidlc" / "spaces" / "default" / "intents" / "active-intent",
        scene.root / "aidlc" / "active-space",
    ]
    before = {p: p.stat().st_mtime_ns for p in watched if p.exists()}
    scene.make(status="Processing")
    scene.make(status="Delivering", deadline_at="2026-09-04T09:59:00Z")
    asyncio.run(svc.reconciler.startup())
    asyncio.run(svc.reconciler.tick())
    after = {p: p.stat().st_mtime_ns for p in watched if p.exists()}
    assert before == after


def test_an_idle_tick_writes_nothing_and_publishes_nothing(svc, scene, studio, clock):
    """Two ticks over an unchanged repository must be indistinguishable from one.

    `recorded_at` on the stable boundary used to be the time of the current look, so the binder's
    "skip a write that would change nothing" comparison and `_publish_intent`'s dedupe both compared two
    values that could never be equal. The result, with `synchronous=FULL`, was one fsync'd
    `intent_bindings` CAS and one `intent.updated` ring row per bound intent per five seconds, forever,
    on an app where nothing was happening.
    """
    asyncio.run(svc.reconciler.tick())

    def state() -> tuple[Any, ...]:
        binding = svc.storage.select("intent_bindings")[0]
        events = [row for row in svc.storage.select("events") if row["type"] == "intent.updated"]
        return (
            binding["binding_generation"],
            binding["last_stable_boundary_json"],
            len(events),
        )

    first = state()
    assert first[2] >= 1, "the first tick should publish the intent it just learned about"

    # The clock has to move: a tick five seconds later is what production does, and a frozen clock hid
    # the churn entirely because `recorded_at` happened to come out the same.
    for _ in range(3):
        clock.advance(studio.constants.RECONCILE_INTERVAL_SECS or 5)
        asyncio.run(svc.reconciler.tick())
    assert state() == first, "an idle tick wrote a row or published a frame"


def test_a_boundary_that_really_changes_is_still_written_and_published(
    svc, scene, studio, gate_repo, clock
):
    """The dedupe must not become a mute button: real movement still has to reach the ring."""
    asyncio.run(svc.reconciler.tick())
    before = svc.storage.select("intent_bindings")[0]
    published = len([r for r in svc.storage.select("events") if r["type"] == "intent.updated"])

    # Clear the gate on disk: the boundary's marker and token both change.
    record = Path(gate_repo) / "aidlc/spaces/default/intents" / GATE_DIR / "aidlc-state.md"
    record.write_text(record.read_text().replace("[?]", "[x]", 1))

    clock.advance(30)
    asyncio.run(svc.reconciler.tick())
    after = svc.storage.select("intent_bindings")[0]
    assert after["last_stable_boundary_json"] != before["last_stable_boundary_json"]
    assert len([r for r in svc.storage.select("events") if r["type"] == "intent.updated"]) > published


# --------------------------------------------------------------------------- #
# 9. the advisor pass: drafting ahead of the click (§1.13 step 6, A24)
# --------------------------------------------------------------------------- #


class StubAdvisor:
    """The two methods ``_advisor_pass`` calls, recording the order and able to explode on demand.

    A stub rather than the real broker for the ordering and failure tests: what they assert is what the
    *pass* does — which half runs first, and what the rest of the tick still does when a half raises —
    and neither fact is observable through a broker that also has to find a card and a spawn seam.

    ``expire`` is the one method that is *not* stubbed out when a test hands it in: retention retires
    drafts through the broker, so a test claiming "retention still ran" has to reach the real write to
    claim anything. Reimplementing that write here is how a stub starts proving only itself.
    """

    def __init__(
        self,
        *,
        raises: str = "",
        settled: int = 0,
        started: int = 0,
        on_settle: Callable[[], None] | None = None,
        expire: Callable[[], Any] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._raises = raises
        self._settled = settled
        self._started = started
        self._on_settle = on_settle
        self._expire = expire

    async def expire_stale(self) -> int:
        self.calls.append("expire_stale")
        return 0 if self._expire is None else int(await self._expire())

    async def settle_open(self, *, limit: int | None = None) -> int:
        self.calls.append("settle_open")
        if self._raises == "settle_open":
            raise RuntimeError("the host lost its subagent manager")
        if self._on_settle is not None:
            self._on_settle()
        return self._settled

    async def autodraft(self, *, limit: int | None = None) -> int:
        self.calls.append("autodraft")
        if self._raises == "autodraft":
            raise RuntimeError("this gateway grants no spawn seam")
        return self._started


@pytest.fixture
def advisor(svc, studio, fake_host):
    """A real ``AdvisorBroker`` attached to the container the way ``Services`` attaches it (§1.18).

    The real one, not a double: the claim these tests make is that a tick turns a granted repository's
    queued card into a spawn nobody clicked, and a stub for ``autodraft`` could only restate the call.
    ``subagents`` is wired because the settle half polls the host on the very next tick.
    """
    module = studio.advisor
    assert module is not None, getattr(studio, "advisor__error", "advisor.py did not import")
    fake_host.subagents = SimpleNamespace(get=lambda spawn_id: svc.ctx.spawn.results.get(spawn_id))
    broker = module.AdvisorBroker(
        svc.ctx,
        svc.storage,
        svc.host,
        svc.projection,
        svc.actions,
        svc.reader,
        svc.activity,
        svc.events,
        svc.clock,
        svc.ids,
        svc.settings,
        agent_lister=lambda: [
            SimpleNamespace(
                name=module.ADVISOR_AGENT_NAME,
                filename=f"{module.ADVISOR_AGENT_FILE_PREFIX}advisor.json",
            )
        ],
    )
    svc.advisor = broker
    return broker


def grant(svc, *repo_ids: str) -> None:
    """The standing grant, written through the real settings service so its validation is the one used."""
    asyncio.run(svc.settings.put({"advisor": {"auto_draft_repo_ids": list(repo_ids)}}))


def queued_ids(svc) -> list[str]:
    return [str(row["action_id"]) for row in svc.storage.select("actions", {"status": "Queued"})]


def test_a_tick_drafts_ahead_for_a_granted_repository(svc, scene, advisor, studio, clock, C):
    """The trigger is the tick, and a card this tick derived is one this tick may draft for.

    Server-side because the value is entirely in being early: a UI trigger can only start drafting once a
    human is already waiting on the card, which is the complaint FR-ADV-001 answers.
    """
    grant(svc, scene.repo_id)
    asyncio.run(svc.reconciler.tick())

    auto_kinds = studio.advisor.ADVISOR_AUTO_KIND_BY_TYPE
    row, = svc.storage.select("advisor_drafts")
    card = svc.storage.get("actions", row["action_id"])
    assert len(queued_ids(svc)) > 1, "one eligible card would prove nothing about the per-tick limit"
    assert card["status"] == "Queued" and card["repo_id"] == scene.repo_id
    assert row["kind"] == auto_kinds[str(card["type"])]
    assert row["request_json"]["auto"] is True, "the flag is what licenses the UI to pre-fill a form"
    assert len(svc.ctx.spawn.spawned) == C.ADVISOR_AUTO_DRAFT_PER_TICK
    # A distinct kind, not a param: the one question a user asks of a draft they did not ask for is who
    # started it, and Activity filters and translates on the kind.
    assert len(svc.storage.select("activity", {"kind": "advisor.auto_requested"})) == 1
    assert svc.storage.select("activity", {"kind": "advisor.requested"}) == []

    # The pass runs every RECONCILE_INTERVAL_SECS for the life of the installation, so what keeps one
    # grant from spawning an advisor every five seconds is that it walks *forward*: one more card per
    # tick, never a card it has already drafted for, and never a type with no automatic kind at all.
    for _ in range(3):
        clock.advance(C.RECONCILE_INTERVAL_SECS or 5)
        asyncio.run(svc.reconciler.tick())
    drafted = [str(d["action_id"]) for d in svc.storage.select("advisor_drafts")]
    eligible = {
        str(r["action_id"])
        for r in svc.storage.select("actions", {"status": "Queued"})
        if str(r["type"]) in auto_kinds
    }
    assert len(drafted) == len(set(drafted)), "a card drew a second automatic draft"
    assert set(drafted) == eligible
    assert eligible < set(queued_ids(svc)), "a card whose type has no automatic kind must stay undrafted"


def test_a_tick_drafts_nothing_without_the_grant(svc, scene, advisor):
    """The empty grant ships on every install, and it has to mean exactly today's behaviour: clicks only."""
    asyncio.run(svc.reconciler.tick())

    assert queued_ids(svc), "the scan must have derived a card, or this proves nothing"
    assert svc.storage.select("advisor_drafts") == []
    assert svc.ctx.spawn.spawned == []


@pytest.mark.parametrize("explodes", ["settle_open", "autodraft"])
def test_a_tick_completes_when_the_advisor_raises(svc, scene, clock, explodes, advisor):
    """Housekeeping may fail; the loop every action's delivery waits on may not.

    ``last_tick_ms`` is the proof rather than ``last_tick_at``: the timestamp is stamped before the passes
    run, while the duration is only written once the last one returned.
    """
    svc.advisor = StubAdvisor(raises=explodes, expire=advisor.expire_stale)
    svc.storage.insert(
        "advisor_drafts",
        {
            "draft_id": svc.ids.new("d"),
            "action_id": scene.make(status="Processing"),
            "kind": "gate_analysis",
            "status": "ready",
            "request_json": {},
            "created_at": clock.iso(),
            "updated_at": clock.iso(),
            "expires_at": "2026-09-04T09:00:00Z",
        },
    )
    asyncio.run(svc.reconciler.tick())
    first = svc.reconciler.status()["last_tick_at"]

    clock.advance(30)
    asyncio.run(svc.reconciler.tick())

    assert svc.reconciler.status()["last_tick_ms"] is not None
    assert svc.reconciler.status()["last_tick_at"] > first
    assert svc.storage.select("advisor_drafts")[0]["status"] == "expired", "retention ran behind it"
    assert svc.advisor.calls[-3:] == ["settle_open", "autodraft", "expire_stale"], (
        "both halves are still attempted, and retention still reaches the broker behind them"
    )


def test_a_container_without_an_advisor_ticks_cleanly(svc, scene):
    """Every partial shape is a real one: the advisor is the one collaborator a tick does not need."""
    assert not hasattr(svc, "advisor")
    assert asyncio.run(svc.reconciler._advisor_pass()) == (0, 0)

    # A broker from before this feature answers the same way, and must: the pass may not decide that half
    # of it is worth running against an object that cannot do the other half.
    svc.advisor = SimpleNamespace(settle_open=None)
    assert asyncio.run(svc.reconciler._advisor_pass()) == (0, 0)

    del svc.advisor
    asyncio.run(svc.reconciler.tick())
    assert svc.reconciler.status()["last_tick_ms"] is not None


def test_the_pass_settles_before_it_starts_anything_new(svc, scene):
    """Order is the contract. Settling frees the inflight slots the automatic cap counts, so the same tick
    can start the next draft; and ``poll`` is otherwise reachable only from the GET route, so a pre-draft —
    which exists because nobody has a browser open — would rot past the host's one-hour result TTL if a
    start could come first.
    """
    svc.advisor = StubAdvisor(settled=3, started=1)
    assert asyncio.run(svc.reconciler._advisor_pass()) == (3, 1)
    assert svc.advisor.calls == ["settle_open", "autodraft"]

    svc.advisor.calls.clear()
    asyncio.run(svc.reconciler.tick())
    # Retention's own call to the broker trails both halves: it is step 7, and expiring a draft the
    # settle half has not collected yet would retire an answer that had already arrived.
    assert svc.advisor.calls == ["settle_open", "autodraft", "expire_stale"]


def test_retention_still_runs_behind_the_pass_in_the_same_tick(svc, scene, clock, advisor):
    """A draft the pass settles now, already past its TTL, is expired by *this* tick and not the next.

    Which is the reason the advisor pass sits before retention rather than after it: the other order
    leaves a draft a user can still open sitting ``ready`` for another interval after it expired.
    """
    draft_id = svc.ids.new("d")
    svc.storage.insert(
        "advisor_drafts",
        {
            "draft_id": draft_id,
            "action_id": scene.make(status="Processing"),
            "kind": "gate_analysis",
            "status": "running",
            "request_json": {"auto": True},
            "created_at": "2026-09-04T08:00:00Z",
            "updated_at": "2026-09-04T08:00:00Z",
            "expires_at": "2026-09-04T09:00:00Z",
        },
    )

    def settled_by_the_pass() -> None:
        svc.storage.update("advisor_drafts", draft_id, {"status": "ready"})

    svc.advisor = StubAdvisor(settled=1, on_settle=settled_by_the_pass, expire=advisor.expire_stale)
    asyncio.run(svc.reconciler.tick())

    assert svc.storage.get("advisor_drafts", draft_id)["status"] == "expired"

"""Reconciliation: four proofs for ``NotDelivered``, one legal edge per doubt, a self-approved gate is an anomaly."""

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
SHARD = "fixture-host-0f1e2d3c4b5a.md"
T0 = "2026-09-04T10:00:00Z"
PAST_DEADLINE = "2026-09-04T09:59:00Z"


@pytest.fixture
def R(studio):
    module = getattr(studio, "reconciler", None)
    assert module is not None, getattr(studio, "reconciler__error", "reconciler.py did not import")
    return module


@pytest.fixture
def C(studio):
    return studio.constants


# --- the broker double (real Storage, real transition table) ---

#: Row columns a broker record carries through unchanged.
_REC_COLUMNS = ("type", "risk_class", "repo_id", "resolved_repo_identity", "intent_uuid", "intent_dir", "space",
                "stage", "unit", "payload_digest", "human_text", "wire_text", "status", "session_key", "slot_key",
                "delivery_id", "lease_generation", "created_at", "updated_at", "delivering_at", "delivered_at",
                "resolved_at", "deadline_at", "boot_id", "retry_fingerprint", "source", "waiting_since", "dedupe_key")
_TERMINAL_KINDS = {"StateChanged": "state_changed", "ResolvedNoTransition": "no_transition", "Failed": "failed",
                   "Cancelled": "cancelled"}


class FakeBroker:
    """The broker methods the reconciler calls, over the real store; ``transition`` validates and CASes."""

    def __init__(self, studio_ns: Any, storage: Any, clock: Any, ids: Any) -> None:
        self._m, self._st, self._clock, self._ids = studio_ns, storage, clock, ids
        self.failures: list[dict] = []
        self.retired: list[tuple[str, str, str, set[str]]] = []
        self.retire_reasons: list[str] = []  # `intent_missing` from the vanished-intent sweep, else the default
        self.transitions: list[tuple[str, str, str]] = []
        self.stamps: list[tuple[str, dict, dict]] = []

    def _rec(self, row: dict) -> SimpleNamespace:
        active = row.get("is_active_captured")
        return SimpleNamespace(
            **{name: row.get(name) for name in _REC_COLUMNS},
            action_id=str(row["action_id"]),
            intent_key=self._m.constants.intent_key_for(row.get("space"), row.get("intent_dir")),
            captured={"state_hash": row.get("captured_state_hash"), "boundary_token": row.get("boundary_token"),
                      "question_digest": row.get("question_digest"), "stage_attempt": row.get("stage_attempt"),
                      "evidence_digest": row.get("evidence_digest"),
                      "is_active": None if active is None else bool(active)},
            payload=row.get("payload_json") or {}, status_generation=int(row.get("status_generation") or 0),
            evidence=dict(row.get("evidence_json") or {}), resolution=row.get("resolution_json"),
            cursor_readback=row.get("cursor_readback_json"), presence_baseline=row.get("presence_baseline_json"))

    async def get(self, action_id: str) -> SimpleNamespace:
        row = await asyncio.to_thread(self._st.get, "actions", action_id)
        if row is None:
            raise self._m.errors.StudioError("action_not_found", "no such action")
        return self._rec(row)

    async def list_live(self, *, repo_id: str | None = None, space: str | None = None, intent_dir: str | None = None,
                        include_revision: bool = False) -> list[SimpleNamespace]:
        where: dict[str, Any] = {"status": list(self._m.constants.LIVE_ACTION_STATUS)}
        where.update({k: v for k, v in (("repo_id", repo_id), ("space", space), ("intent_dir", intent_dir)) if v})
        recs = [self._rec(r) for r in await asyncio.to_thread(self._st.select, "actions", where)]
        return recs if include_revision else [r for r in recs if r.type != "revision"]

    async def transition(self, action_id: str, to_status: str, *, expected_generation: int, reason: str,
                         evidence: dict) -> SimpleNamespace:
        row = await asyncio.to_thread(self._st.get, "actions", action_id)
        current = str(row["status"])
        if to_status not in self._m.constants.ALLOWED_TRANSITIONS.get(current, ()):
            raise self._m.errors.IllegalTransition(f"{current} -> {to_status}", details={"from": current, "to": to_status})
        merged = {**(row.get("evidence_json") or {}), **(evidence or {})}
        payload: dict[str, Any] = {"status": to_status, "evidence_json": merged}
        if to_status in _TERMINAL_KINDS:
            # The broker composes the resolution record from the reason and evidence it is handed (`_transition`).
            payload["resolved_at"] = self._clock.iso()
            payload["resolution_json"] = {"kind": _TERMINAL_KINDS[to_status], "resolved_at": self._clock.iso(),
                                          "evidence": dict(evidence) or None, "reason": reason}
        await asyncio.to_thread(self._st.cas_update, "actions", action_id, expected_generation, payload)
        await asyncio.to_thread(self._st.insert, "action_transitions", {
            "action_id": action_id, "from_status": current, "to_status": to_status,
            "generation": expected_generation + 1, "at": self._clock.iso(), "reason": reason, "evidence_json": merged})
        self.transitions.append((action_id, to_status, reason))
        return await self.get(action_id)

    async def patch_evidence(self, rec: Any, patch: dict, *, values: dict | None = None) -> SimpleNamespace:
        """As the broker's: writes nothing when nothing changed, and a lost CAS means the other writer is newer."""
        merged = {**rec.evidence, **patch}
        if merged != rec.evidence or values:
            try:
                await asyncio.to_thread(self._st.cas_update, "actions", rec.action_id, int(rec.status_generation),
                                        {"evidence_json": merged, **(values or {})})
            except self._m.errors.StaleGeneration:
                pass
        self.stamps.append((rec.action_id, dict(patch), dict(values or {})))
        return await self.get(rec.action_id)

    async def record_failure(self, rec: Any, *, fingerprint: str, fingerprint_class: str, summary: str,
                             evidence: dict) -> tuple[SimpleNamespace, bool]:
        self.failures.append({"action_id": rec.action_id, "fingerprint": fingerprint, "class": fingerprint_class,
                              "summary": summary})
        moved = await self.transition(
            rec.action_id, "Failed", expected_generation=int(rec.status_generation), reason=fingerprint,
            evidence=dict(evidence, fingerprint=fingerprint, fingerprint_class=fingerprint_class))
        return moved, fingerprint_class not in self._m.constants.TRANSIENT_FINGERPRINT_CLASSES

    async def upsert_derived(self, seeds: Sequence[Any]) -> list[SimpleNamespace]:
        live = set(self._m.constants.DEDUPE_LIVE_ACTION_STATUS)
        for seed in seeds:
            rows = await asyncio.to_thread(self._st.select, "actions", {"dedupe_key": seed.dedupe_key})
            if any(str(r["status"]) in live for r in rows):
                continue
            await asyncio.to_thread(self._st.insert, "actions", {
                "action_id": self._ids.new("a"), "type": seed.type, "risk_class": seed.risk_class,
                "repo_id": seed.repo_id, "intent_dir": seed.intent_dir, "space": seed.space, "stage": seed.stage,
                "status": "Queued", "created_at": self._clock.iso(), "updated_at": self._clock.iso(),
                "waiting_since": seed.waiting_since, "source": "studio", "dedupe_key": seed.dedupe_key,
                "evidence_json": {}})
        return await self.list_live(repo_id=seeds[0].repo_id if seeds else None, include_revision=True)

    async def retire_stale(self, repo_id: str, space: str, intent_dir: str, live_keys: set[str], *,
                           reason: str = "boundary_superseded") -> None:
        self.retired.append((repo_id, space, intent_dir, set(live_keys)))
        self.retire_reasons.append(reason)
        rows = await asyncio.to_thread(self._st.select, "actions",
                                       {"repo_id": repo_id, "space": space, "intent_dir": intent_dir})
        for row in rows:
            key, status = row.get("dedupe_key"), str(row["status"])
            if not key or key in live_keys or status not in ("Queued", "NotDelivered", "Failed"):
                continue
            values = ({"status": "Cancelled"} if status != "Failed"
                      else {"evidence_json": {**(row.get("evidence_json") or {}), "superseded": True}})
            await asyncio.to_thread(self._st.cas_update, "actions", str(row["action_id"]),
                                    int(row.get("status_generation") or 0), values)


@pytest.fixture
def svc(studio, fake_ctx, fake_host, clock, ids, fake_git, R):
    """Real modules except one double (``actions``); no ``scan_pool``, so scans use the default executor."""
    storage = studio.storage.Storage(Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME, clock=clock)
    storage.open()
    host = studio.sessions.HostBridge(clock, logging.getLogger("test.host"))
    host.attach(fake_host)
    reader = studio.aidlc_reader.AidlcReader(clock)
    consistency = studio.consistency.ConsistencyEngine()
    projection = studio.projection.Projection(reader, consistency, clock)
    activity = studio.activity.ActivityProjector(storage, clock)
    events = studio.events.EventLog(fake_ctx.events, clock)
    settings = studio.settings.SettingsService(storage, host, clock)
    settings.load()
    git = studio.git_observer.GitObserver(fake_git, clock)
    services = SimpleNamespace(
        ctx=fake_ctx, clock=clock, ids=ids, boot_id=BOOT, storage=storage, settings=settings,
        repos=studio.repo_registry.RepoRegistry(storage, git, clock, ids), reader=reader, consistency=consistency,
        projection=projection, scheduler=studio.leases.RepoScheduler(storage, host, settings, clock, activity),
        host=host, sessions=studio.sessions.SessionBinder(storage, host, projection, clock, activity),
        actions=FakeBroker(studio, storage, clock, ids), activity=activity, events=events, git=git, installer=None,
        scan_pool=None)
    services.reconciler = R.Reconciler(services)
    yield services
    storage.close()


# --- scenario helpers ---


def _record(root: Path, intent_dir: str = GATE_DIR, space: str = "default") -> Path:
    return root / "aidlc" / "spaces" / space / "intents" / intent_dir


def touch_human_turn(root: Path, *, mtime_ns: int | None = None) -> int:
    """Stamp ``.aidlc-human-turn`` as the adapter does on a real prompt: its mtime is the presence fact (C23)."""
    path = _record(root) / HUMAN_TURN_MARKER
    path.write_text("2026-09-04T10:00:00Z\n")
    stamp = mtime_ns if mtime_ns is not None else time.time_ns()
    os.utime(path, ns=(stamp, stamp))
    return stamp


def append_audit(root: Path, *blocks: str) -> Path:
    shard = _record(root) / "audit" / SHARD
    shard.write_text(shard.read_text() + "".join(blocks))
    return shard


@pytest.fixture
def scene(svc, gate_repo, fake_host):
    """One repo with its slot bound; ``make`` writes action rows with the columns ``submit`` commits."""
    root = Path(gate_repo)
    repo = svc.repos.add(str(root))
    slot_key = f"aidlc-studio-{repo.repo_id}-{GATE_DIR}"
    fake_host.add_slot(slot_key, project=str(root), agent="aidlc")
    # Bound: the scan reads the slot through the binding while the action path reads it off the row.
    asyncio.run(svc.sessions.bind(repo, "default", GATE_DIR, slot_key=slot_key, intent_uuid=None))

    def snapshot(**over: Any):
        return svc.projection.snapshot(svc.repos.get(repo.repo_id), "default", GATE_DIR, **over)

    def make(*, status: str = "Delivered", action_type: str = "gate", decision: str = "approve",
             risk_class: str = "human_lane", stage: str | None = GATE_STAGE, wire_text: str | None = "Approve",
             payload: dict | None = None, delivering_at: str = "2026-09-04T09:59:00Z",
             delivered_at: str | None = "2026-09-04T09:59:05Z", deadline_at: str | None = "2026-09-04T12:00:00Z",
             boot_id: str | None = BOOT, presence: bool = True, lease_generation: int | None = None,
             evidence_extra: dict | None = None, snap: Any = None) -> str:
        snap = snap if snap is not None else snapshot()
        extra = dict(evidence_extra or {})
        if status == "Delivered" and "queued_at" not in extra:
            extra.setdefault("turn_started_at", delivered_at)  # not queued behind a turn: it started on delivery
        captured = svc.projection.captured(snap, stage)
        evidence = svc.projection.evidence(snap).to_json()
        baseline = svc.reader.presence_baseline(root, "default", GATE_DIR, snap.audit, snap.state_sha256).to_json()
        action_id = svc.ids.new("a")
        svc.storage.insert("actions", {
            "action_id": action_id, "type": action_type, "risk_class": risk_class, "repo_id": repo.repo_id,
            "resolved_repo_identity": repo.resolved_identity, "intent_uuid": snap.row.uuid if snap.row else None,
            "intent_dir": GATE_DIR, "space": "default", "stage": stage,
            "captured_state_hash": captured["state_hash"], "boundary_token": captured["boundary_token"],
            "question_digest": captured["question_digest"], "stage_attempt": captured["stage_attempt"],
            "evidence_digest": captured["evidence_digest"], "is_active_captured": int(bool(captured["is_active"])),
            "payload_json": dict(payload or {"decision": decision}), "wire_text": wire_text,
            "human_text": "the human's own words", "status": status, "session_key": f"dashboard:{slot_key}",
            "slot_key": slot_key, "delivery_id": svc.ids.new("dl"), "lease_generation": lease_generation,
            "created_at": "2026-09-04T09:58:00Z", "updated_at": "2026-09-04T09:59:00Z",
            "delivering_at": delivering_at, "delivered_at": delivered_at, "deadline_at": deadline_at,
            "evidence_json": dict(evidence, **extra), "presence_baseline_json": baseline if presence else None,
            "boot_id": boot_id, "source": "studio", "waiting_since": "2026-09-04T09:00:00Z"})
        return action_id

    return SimpleNamespace(root=root, repo=repo, repo_id=repo.repo_id, slot_key=slot_key,
                           slot=fake_host.get_slot(slot_key), snapshot=snapshot, make=make)


def approve_on_disk(root: Path, *, human_turns: int = 1, ts: str = "2026-09-04T10:00:00Z") -> None:
    """The engine after a real gate approval; ``human_turns`` 0 and 2 are the §1.13 anomaly pair (review P23)."""
    append_audit(root, *[F.audit_block("HUMAN_TURN", ts) for _ in range(human_turns)],
                 F.audit_block("GATE_APPROVED", ts, Stage=GATE_STAGE, User_Input="Approve"),
                 F.audit_block("STAGE_COMPLETED", ts, Stage=GATE_STAGE, Details=f"Stage {GATE_STAGE} approved"))
    (_record(root) / "aidlc-state.md").write_text(F.state_text(marks={GATE_STAGE: "x"}, fields={
        "Current Stage": "user-stories", "Status": "Running", "Next Stage": "domain-model"}))
    touch_human_turn(root)


def reject_on_disk(root: Path, *, ts: str = "2026-09-04T10:00:00Z") -> None:
    append_audit(root, F.audit_block("HUMAN_TURN", ts),
                 F.audit_block("GATE_REJECTED", ts, Stage=GATE_STAGE, Feedback="Spell out the criteria."),
                 F.audit_block("STAGE_REVISING", ts, Stage=GATE_STAGE, Revision_count="1"))
    (_record(root) / "aidlc-state.md").write_text(F.state_text(marks={GATE_STAGE: "R"}, fields={
        "Current Stage": GATE_STAGE, "Status": "Running", "Revision Count": "1"}))
    touch_human_turn(root)


def finish_turn(fake_host, slot_key: str, *, wire_text: str = "Approve", user_ts: str = "2026-09-04T09:59:30Z",
                reply_ts: str = "2026-09-04T10:00:10Z") -> None:
    """A finished turn: the user row is the delivery evidence; the reply tells "finished" from "never started"."""
    slot = fake_host.get_slot(slot_key)
    slot.messages += [{"role": "user", "content": wire_text, "cls": "msg msg-u", "ts": user_ts, "meta": {}},
                      {"role": "assistant", "content": "done", "cls": "msg msg-a", "ts": reply_ts, "meta": {}}]
    slot.running = False


def tick(svc) -> None:
    asyncio.run(svc.reconciler.tick())


def status_of(svc, action_id: str) -> str:
    return str(svc.storage.get("actions", action_id)["status"])


def evidence_of(svc, action_id: str) -> dict:
    return dict(svc.storage.get("actions", action_id).get("evidence_json") or {})


def finding_codes(svc, action_id: str) -> set[str]:
    return {str(f.get("code")) for f in evidence_of(svc, action_id).get("findings", []) if isinstance(f, dict)}


def ended_turn(svc, scene, fake_host, wire: str, *blocks: str, after: str | None = None, **make: Any) -> str:
    """Dispatch a ``Delivered`` action, leave ``after`` and ``blocks`` behind a human turn, end it, tick once."""
    make.setdefault("action_type", "question")
    action_id = scene.make(wire_text=wire, **make)
    if after is not None:
        (_record(scene.root) / QUESTIONS_REL).write_text(after)
    touch_human_turn(scene.root)
    append_audit(scene.root, F.audit_block("HUMAN_TURN", T0), *blocks)
    finish_turn(fake_host, scene.slot_key, wire_text=wire)
    tick(svc)
    return action_id


def gate_card_after_a_tick(svc, scene):
    tick(svc)
    live = asyncio.run(svc.actions.list_live(repo_id=scene.repo_id, include_revision=True))
    return next(a for a in live if a.type == "gate")


def insert_card(svc, scene, *, type: str, status: str, dedupe_key: str, risk_class: str = "human_lane",
                **extra: Any) -> str:
    """A raw card row for the retirement sweeps, outside the scan's own derivation."""
    action_id = svc.ids.new("a")
    svc.storage.insert("actions", {
        "action_id": action_id, "type": type, "risk_class": risk_class, "repo_id": scene.repo_id,
        "intent_dir": GATE_DIR, "space": "default", "status": status, "created_at": "2026-09-04T09:00:00Z",
        "updated_at": "2026-09-04T09:00:00Z", "waiting_since": "2026-09-04T09:00:00Z", "source": "studio",
        "dedupe_key": dedupe_key, "evidence_json": {}, **extra})
    return action_id


def test_unchanged_reconciliation_evidence_keeps_generation_stable(svc, scene):
    """A quiet recovery card must not go stale on every scan: an unchanged second pass writes nothing."""
    action_id = scene.make(status="ReconciliationRequired")
    touch_human_turn(scene.root)  # the disk moved, so absence is unprovable and the row stays the human's
    asyncio.run(svc.reconciler.reconcile_now(action_id))
    before = svc.storage.get("actions", action_id)
    asyncio.run(svc.reconciler.reconcile_now(action_id))
    after = svc.storage.get("actions", action_id)
    assert after["status"] == "ReconciliationRequired"
    assert after["status_generation"] == before["status_generation"]
    assert after["updated_at"] == before["updated_at"]
    assert after["evidence_json"] == before["evidence_json"]


def test_changed_reconciliation_evidence_still_advances_generation(svc, scene, fake_host):
    action_id = scene.make(status="ReconciliationRequired")
    touch_human_turn(scene.root)
    asyncio.run(svc.reconciler.reconcile_now(action_id))
    before = svc.storage.get("actions", action_id)
    fake_host.append_user_row(scene.slot_key, "Approve", "2026-09-04T09:59:30Z")
    asyncio.run(svc.reconciler.reconcile_now(action_id))
    after = svc.storage.get("actions", action_id)
    assert after["status_generation"] == before["status_generation"] + 1
    assert after["evidence_json"]["delivery_confirmed"] is True


# --- 1. startup ordering ---


def test_startup_reevaluates_every_inflight_action_before_scanning(svc, scene):
    """A card derived before a delivered decision is re-read duplicates a closed boundary (§1.13)."""
    order: list[str] = []
    original_reconcile, original_scan = svc.reconciler.reconcile_action, svc.reconciler.scan_repo

    async def note_reconcile(rec, snap=None):
        order.append(f"reconcile:{rec.action_id}")
        return await original_reconcile(rec, snap)

    async def note_scan(repo):
        order.append("scan")
        return await original_scan(repo)

    svc.reconciler.reconcile_action = note_reconcile
    svc.reconciler.scan_repo = note_scan
    a = scene.make(status="Delivering")
    b = scene.make(status="Delivered", evidence_extra={"queued_at": "2026-09-04T09:59:05Z"})
    c = scene.make(status="Delivered")
    report = asyncio.run(svc.reconciler.startup())
    assert order.index(f"reconcile:{a}") < order.index("scan")
    assert {f"reconcile:{a}", f"reconcile:{b}", f"reconcile:{c}"} <= set(order)
    assert report.actions_reevaluated == 3
    assert report.integrity == ()


def test_startup_stamps_boot_id_mismatch_and_never_reaches_not_delivered(svc, scene, clock):
    """After a restart "no user row" looks like a lost transcript: stamp the boot first, prove nothing (R02)."""
    action_id = scene.make(status="Delivering", boot_id=OTHER_BOOT, deadline_at=PAST_DEADLINE)
    svc.host.attach(None)
    svc.host._state = None  # the restarted process has an empty slot table
    report = asyncio.run(svc.reconciler.startup())
    assert report.boot_id_mismatches == 1
    assert evidence_of(svc, action_id)["boot_id_unchanged"] is False
    for _ in range(4):
        clock.advance(10)
        tick(svc)
        assert status_of(svc, action_id) != "NotDelivered"
    assert status_of(svc, action_id) == "ReconciliationRequired"


def test_startup_report_publishes_health_updated(svc, scene):
    """Published, not only returned: `/health` and the UI read it from the event ring."""
    asyncio.run(svc.reconciler.startup())
    rows = [record for record in asyncio.run(svc.events.since(0)) if record.type == "health.updated"]
    assert rows and "startup" in rows[0].payload
    assert "health.startup" in {row["kind"] for row in svc.storage.select("activity")}


# --- 2. the ack deadline path ---


def test_ack_deadline_with_user_row_found_lands_on_reconciliation_required(svc, scene, fake_host):
    """Sent, never reported: one tick, one legal edge, ``delivery_confirmed`` from the row, no NotDelivered."""
    action_id = scene.make(status="Delivering", deadline_at=PAST_DEADLINE)
    finish_turn(fake_host, scene.slot_key)
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"
    ev = evidence_of(svc, action_id)
    assert ev["transcript_row"]["slot_key"] == scene.slot_key
    assert ev["transcript_row_absent"] is False and ev["delivery_confirmed"] is True
    assert svc.actions.transitions == [(action_id, "ReconciliationRequired", "ack_deadline")]
    assert "NotDelivered" not in [t for _, t, _ in svc.actions.transitions]


def test_ack_deadline_matches_by_delivery_meta_before_content(svc, scene, fake_host):
    """``meta.studio_delivery_id`` is the primary key, so an edited or normalised message still matches (R08)."""
    action_id = scene.make(status="Delivering", deadline_at=PAST_DEADLINE)
    delivery_id = svc.storage.get("actions", action_id)["delivery_id"]
    fake_host.get_slot(scene.slot_key).messages.append({
        "role": "user", "content": "something else entirely", "cls": "msg msg-u", "ts": "2026-09-04T09:59:30Z",
        "meta": {"studio_delivery_id": delivery_id}})
    tick(svc)
    assert evidence_of(svc, action_id)["transcript_row"]["ts"] == "2026-09-04T09:59:30Z"


def test_ack_deadline_with_no_row_and_all_four_proofs_reaches_not_delivered(svc, scene, clock):
    """Row absent, slot idle, baseline identical, same boot, and only from ``ReconciliationRequired`` (C24)."""
    action_id = scene.make(status="Delivering", deadline_at=PAST_DEADLINE)
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"
    ev = evidence_of(svc, action_id)
    assert ev["transcript_row_absent"] is True and ev["slot_ran_since"] is False
    assert ev["disk_baseline_unchanged"] is True and ev["boot_id_unchanged"] is True
    assert ev["delivery_confirmed"] is False
    clock.advance(5)
    tick(svc)
    assert status_of(svc, action_id) == "NotDelivered"


@pytest.mark.parametrize("break_proof", ["slot_ran", "disk_moved"])
def test_not_delivered_refused_when_any_proof_is_missing(svc, scene, clock, fake_host, break_proof):
    action_id = scene.make(status="Delivering", deadline_at=PAST_DEADLINE)
    if break_proof == "slot_ran":
        fake_host.get_slot(scene.slot_key).messages.append(
            {"role": "assistant", "content": "hi", "cls": "a", "ts": "2026-09-04T09:59:40Z", "meta": {}})
    else:
        touch_human_turn(scene.root)
    for _ in range(3):
        tick(svc)
        clock.advance(5)
    assert status_of(svc, action_id) == "ReconciliationRequired"


def test_a_lost_ack_reaches_a_human_in_one_tick_with_no_evidence_at_all(svc, scene):
    """No dead ends and no waiting room (R07): the first tick lands where the human's decisions are offered."""
    action_id = scene.make(status="Delivering", deadline_at=PAST_DEADLINE, presence=False)
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert svc.actions.transitions == [(action_id, "ReconciliationRequired", "ack_deadline")]


# --- 3. deadlines, queued deliveries and replaced sessions ---


def test_queued_delivery_waits_in_delivered_until_the_turn_starts(svc, scene, fake_host, clock):
    """``{ok:true, queued:true}`` is delivered but not processing (P22): the deadline starts when the turn does."""
    action_id = scene.make(status="Delivered", deadline_at=PAST_DEADLINE,
                           evidence_extra={"queued_at": "2026-09-04T09:59:05Z"})
    tick(svc)
    assert status_of(svc, action_id) == "Delivered"
    assert "turn_started_at" not in evidence_of(svc, action_id)
    fake_host.set_running(scene.slot_key, True)
    clock.advance(5)
    tick(svc)
    row = svc.storage.get("actions", action_id)
    assert row["status"] == "Delivered", "the turn starting is evidence on the row, not a status"
    assert row["evidence_json"]["turn_started_at"] == clock.iso()
    assert row["deadline_at"] > "2026-09-04T10:00:00Z"
    assert svc.actions.transitions == []


def test_queued_delivery_gives_up_after_the_wait_factor(svc, scene, clock, C):
    action_id = scene.make(status="Delivered", deadline_at=PAST_DEADLINE,
                           evidence_extra={"queued_at": "2026-09-04T09:59:05Z"})
    clock.advance(C.DELIVERED_QUEUED_WAIT_FACTOR * svc.host.turn_timeout_secs() + 60)
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"


@pytest.mark.parametrize("deadline,disrupt", [
    # The slot now runs its turns in another session, so the audit read could be another conversation's.
    ("2026-09-04T12:00:00Z", lambda scene, host, clock: setattr(
        host.get_slot(scene.slot_key), "linked_session_key", "channel:other")),
    ("2026-09-04T12:00:00Z", lambda scene, host, clock: host._slots.pop(scene.slot_key)),
    ("2026-09-04T10:00:00Z", lambda scene, host, clock: clock.advance(120)),
], ids=["session_replaced", "slot_gone", "turn_past_its_deadline"])
def test_a_delivered_turn_studio_can_no_longer_follow_is_reconciliation_required(
    svc, scene, fake_host, clock, deadline, disrupt
):
    action_id = scene.make(status="Delivered", deadline_at=deadline)
    disrupt(scene, fake_host, clock)
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"


def test_detached_host_does_not_mistake_a_missing_slot_for_a_dead_session(svc, scene):
    """Disk-only is a reduced mode, not a terminated session (C08)."""
    action_id = scene.make(status="Delivered")
    svc.host._state = None
    tick(svc)
    assert status_of(svc, action_id) == "Delivered"


# --- 4. resolution contracts ---

PLAN = "# Code Generation — Questions\n\n---\n\n## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n\n"
ASSUMPTION_QUESTIONS = (
    "# Intent Capture — Questions\n\n---\n\n"
    "## Assumption Confirmation\n\n- The demo covers the extra features [assumption]\n\n"
    "A. Accept assumptions\nB. Convert to follow-up questions\n\n[Answer]:\n"
)


def _questions_file(scene, text: str) -> None:
    (_record(scene.root) / QUESTIONS_REL).write_text(text)


def test_gate_approve_resolves_state_changed_from_same_second_rows(svc, scene, fake_host):
    """``gate_approved_audit`` plus a `[x]` row is ``StateChanged``, same-second ordering included (P23)."""
    action_id = scene.make(status="Delivered")
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key)
    tick(svc)
    row = svc.storage.get("actions", action_id)
    assert row["status"] == "StateChanged"
    assert row["resolution_json"]["kind"] == "state_changed"
    assert row["resolution_json"]["evidence"]["presence"]["ok"] is True


def test_gate_request_changes_needs_rejected_revising_and_the_r_row(svc, scene, fake_host):
    wire = "Request Changes: Spell out the criteria."
    action_id = scene.make(status="Delivered", decision="request_changes", wire_text=wire)
    reject_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key, wire_text=wire)
    tick(svc)
    assert status_of(svc, action_id) == "StateChanged"


def test_gate_approve_without_the_audit_row_stays_processing(svc, scene, fake_host):
    """A finished turn is not a resolution: only the engine's own ``GATE_APPROVED`` closes a gate."""
    action_id = scene.make(status="Delivered")
    finish_turn(fake_host, scene.slot_key)
    touch_human_turn(scene.root)
    tick(svc)
    assert status_of(svc, action_id) == "Delivered"


def test_question_answers_resolve_no_transition(svc, scene, fake_host):
    """Answering moves the conversation, not the lifecycle: ``ResolvedNoTransition``."""
    snap = scene.snapshot()
    pending = [q.index for q in snap.questions.questions if not q.answered]
    action_id = ended_turn(
        svc, scene, fake_host, "Yes", F.audit_block("QUESTION_ANSWERED", T0, Stage=GATE_STAGE, Details="A"),
        after=F.real_questions(), decision="answers", snap=snap,
        payload={"decision": "answers", "answers": [{"index": pending[0], "option_letters": ["A"]}]})
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_confirm_summary_resolves_on_the_audit_row(svc, scene, fake_host):
    action_id = ended_turn(
        svc, scene, fake_host, "Looks correct",
        F.audit_block("SUMMARY_CONFIRMATION_RECORDED", T0, Stage=GATE_STAGE,
                      Checkpoint="Consolidated Summary Confirmation"),
        decision="confirm_summary", payload={"decision": "confirm_summary", "choice": "looks_correct"})
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_approve_plan_needs_the_checkpoint_answer_in_the_file(svc, scene, fake_host, C):
    """``plan-approval-guard`` is a hook, not an audit event: the tag holding ``Approve Plan`` is the trace (C34)."""
    _questions_file(scene, PLAN)
    action_id = ended_turn(svc, scene, fake_host, C.WIRE_APPROVE_PLAN, decision="approve_plan",
                           after=PLAN.replace("[Answer]:", f"[Answer]: {C.WIRE_APPROVE_PLAN}"))
    assert status_of(svc, action_id) == "ResolvedNoTransition"


@pytest.mark.parametrize("recorded,status", [
    ("A. Accept assumptions", "ResolvedNoTransition"),
    ("B. Convert to follow-up questions", "Delivered"),  # the engine recorded the option the human did not pick
])
def test_accept_assumptions_resolves_only_on_the_chosen_option(svc, scene, fake_host, C, recorded, status):
    _questions_file(scene, ASSUMPTION_QUESTIONS)
    action_id = ended_turn(svc, scene, fake_host, C.WIRE_ACCEPT_ASSUMPTIONS, decision="accept_assumptions",
                           after=ASSUMPTION_QUESTIONS.replace("[Answer]:", f"[Answer]: {recorded}"))
    assert status_of(svc, action_id) == status


def test_convert_assumptions_resolves_on_its_question_answered_row(svc, scene, fake_host, C):
    """The standard answer receipt naming the choice is proof on its own."""
    _questions_file(scene, ASSUMPTION_QUESTIONS)
    action_id = ended_turn(
        svc, scene, fake_host, C.WIRE_CONVERT_ASSUMPTIONS,
        F.audit_block("QUESTION_ANSWERED", T0, Stage=GATE_STAGE, Details=C.WIRE_CONVERT_ASSUMPTIONS),
        decision="convert_assumptions")
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_request_plan_changes_resolves_when_the_tag_is_cleared_for_revision(svc, scene, fake_host, C):
    """Holding ``Request Changes…`` or cleared for the revision both count: the engine leaves either (§7)."""
    _questions_file(scene, PLAN.replace("[Answer]:", "[Answer]: Approve Plan"))
    wire = C.WIRE_REQUEST_CHANGES_PREFIX + "Split the plan."
    action_id = ended_turn(svc, scene, fake_host, wire, decision="request_plan_changes", after=PLAN,
                           payload={"decision": "request_plan_changes", "feedback": "Split the plan."})
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def test_resolution_events_mirror_the_bundled_engines_gate_resolution_set(C):
    """Read out of the bundled engine; ``AUTONOMY_MODE_SET`` is the documented non-mirror."""
    lib = (APP_ROOT / "payload" / "aidlc-kiro" / ".kiro" / "tools" / "aidlc-lib.ts").read_text("utf-8")
    start = lib.index("GATE_RESOLUTION_EVENTS = new Set([")
    engine = set(re.findall(r'"([A-Z_]+)"', lib[start : lib.index("]);", start)]))
    assert engine, "the engine's own set could not be read; the mirror below would be vacuous"
    assert set(C.RESOLUTION_EVENTS) == engine - {"AUTONOMY_MODE_SET"}


def test_a_plan_approval_row_counts_as_a_gate_resolution(svc, scene, clock):
    """``resolution_event_seen`` tells the human reconciling a timed-out turn that the engine did record one."""
    action_id = scene.make(status="Delivered", deadline_at="2026-09-04T10:00:00Z")
    append_audit(scene.root, F.audit_block("HUMAN_TURN", T0),
                 F.audit_block("PLAN_APPROVAL_RECORDED", T0, Stage=GATE_STAGE, Checkpoint="Plan Approval"))
    touch_human_turn(scene.root)
    clock.advance(120)
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert evidence_of(svc, action_id)["disk_movement"]["resolution_event_seen"] is True


def test_a_plan_approval_row_is_movement_for_a_run_action(svc, scene, fake_host, C):
    """With ``HUMAN_TURN`` alone the same run is ``ResolvedNoTransition``; the approved plan is movement."""
    action_id = ended_turn(
        svc, scene, fake_host, C.WIRE_RUN,
        F.audit_block("PLAN_APPROVAL_RECORDED", "2026-09-04T10:00:01Z", Stage=GATE_STAGE, Checkpoint="Plan Approval"),
        action_type="run", decision="run")
    assert status_of(svc, action_id) == "StateChanged"
    assert svc.storage.get("actions", action_id)["resolution_json"]["reason"] == "workflow_moved"


def test_provide_input_scope_resolves_only_when_the_state_records_the_scope(svc, scene, fake_host, C):
    """The turn ending proves nothing (a follow-up question instead of the scope); the field does."""
    action_id = ended_turn(svc, scene, fake_host, C.WIRE_SCOPE_PREFIX + "bugfix", action_type="missing_input",
                           decision="provide_input", stage=None,
                           payload={"decision": "provide_input", "kind": "scope", "scope": "bugfix"})
    assert status_of(svc, action_id) == "Delivered"
    (_record(scene.root) / "aidlc-state.md").write_text(
        F.state_text(marks={GATE_STAGE: "?"}, fields={"Scope": "bugfix", "Current Stage": GATE_STAGE}))
    tick(svc)
    assert status_of(svc, action_id) == "StateChanged"


@pytest.mark.parametrize("kind,wire", [("run", "WIRE_RUN"), ("resume", "WIRE_RESUME")])
def test_run_and_resume_resolve_state_changed_when_the_workflow_moved(svc, scene, fake_host, C, kind, wire):
    action_id = scene.make(status="Delivered", action_type=kind, decision=kind, wire_text=getattr(C, wire))
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key, wire_text=getattr(C, wire))
    tick(svc)
    assert status_of(svc, action_id) == "StateChanged"


@pytest.mark.parametrize("kind,wire", [("run", "WIRE_RUN"), ("prepare_commit", "WIRE_PREPARE_COMMIT")])
def test_a_turn_that_ended_without_movement_resolves_no_transition(svc, scene, fake_host, C, kind, wire):
    """The engine asked a question or parked (run), or the turn alone is the outcome (prepare_commit)."""
    action_id = ended_turn(svc, scene, fake_host, getattr(C, wire), action_type=kind, decision=kind)
    assert status_of(svc, action_id) == "ResolvedNoTransition"


def _force_stop(scene) -> str:
    return scene.make(status="Delivered", action_type="force_stop", decision="force_stop", risk_class="host_control",
                      stage=None, wire_text=None, presence=False)


def test_force_stop_resolves_when_the_slot_goes_idle_and_records_the_interruption(svc, scene):
    """Host control needs no ``HUMAN_TURN`` (§1.12); ``interrupted_at`` is cleared only by a human (C28/P17)."""
    action_id = _force_stop(scene)
    tick(svc)
    assert status_of(svc, action_id) == "ResolvedNoTransition"
    assert asyncio.run(svc.sessions.get(scene.repo_id, "default", GATE_DIR)).interrupted_at is not None
    assert "intent.force_stop" in {row["kind"] for row in svc.storage.select("activity")}


def test_force_stop_stays_pending_while_the_stop_is_in_progress(svc, scene, fake_host):
    action_id = _force_stop(scene)
    fake_host.get_slot(scene.slot_key)._stop_state = "killing"
    tick(svc)
    assert status_of(svc, action_id) == "Delivered"


# --- 5. the presence invariant, per dispatch class ---


@pytest.mark.parametrize("human_turns", [0, 2])
def test_gate_approved_without_exactly_one_human_turn_is_an_anomaly(svc, scene, fake_host, human_turns):
    """Zero turns is an approval nobody gave; two cannot be attributed to this decision (FR-SES-010/P04)."""
    action_id = scene.make(status="Delivered")
    approve_on_disk(scene.root, human_turns=human_turns)
    finish_turn(fake_host, scene.slot_key)
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert "human_presence_anomaly" in finding_codes(svc, action_id)
    assert svc.actions.transitions == [(action_id, "ReconciliationRequired", "human_presence_anomaly")]


def test_presence_requires_the_marker_to_advance_too(svc, scene, fake_host):
    """A hook an agent can trigger writes the row; only a real prompt moves the marker, so both are needed (C23)."""
    marker = _record(scene.root) / HUMAN_TURN_MARKER
    marker.write_text("2026-09-04T09:00:00Z\n")
    os.utime(marker, ns=(1_000_000_000, 1_000_000_000))
    action_id = scene.make(status="Delivered")
    append_audit(scene.root, F.audit_block("HUMAN_TURN", T0),
                 F.audit_block("GATE_APPROVED", T0, Stage=GATE_STAGE, User_Input="Approve"))
    (_record(scene.root) / "aidlc-state.md").write_text(
        F.state_text(marks={GATE_STAGE: "x"}, fields={"Current Stage": "user-stories"}))
    os.utime(marker, ns=(1_000_000_000, 1_000_000_000))
    finish_turn(fake_host, scene.slot_key)
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert "human_presence_anomaly" in finding_codes(svc, action_id)


def test_cursor_moved_between_dispatch_and_resolution_is_an_anomaly(svc, scene, fake_host):
    """The conductor resolves the record from the shared cursor, so the rows may be another intent's (R01/C22)."""
    action_id = scene.make(status="Delivered")
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key)
    (scene.root / "aidlc" / "spaces" / "default" / "intents" / "active-intent").write_text("someone-else\n")
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert "cursor_moved" in finding_codes(svc, action_id)


def test_row_found_in_another_slot_is_wrong_slot_and_waits_for_a_human(svc, scene, fake_host, clock):
    """Delivered to another session: neither ``NotDelivered`` nor a disk resolution is safe, a human decides (R08)."""
    action_id = scene.make(status="Delivering", deadline_at=PAST_DEADLINE)
    fake_host.add_slot("hand-made", project=str(scene.root), agent="aidlc")
    fake_host.get_slot("hand-made").messages.append(
        {"role": "user", "content": "Approve", "cls": "u", "ts": "2026-09-04T09:59:30Z", "meta": {}})
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert evidence_of(svc, action_id)["wrong_slot"] == "hand-made"
    clock.advance(5)
    tick(svc)
    assert status_of(svc, action_id) == "ReconciliationRequired"
    assert "wrong_slot" in finding_codes(svc, action_id)


# --- 6. stable boundary, retirement, release ---


def test_stable_boundary_is_recorded_and_names_every_unmet_condition(svc, scene):
    """PRD §12.2's five conditions are named when unmet; an in-flight action alone makes the boundary unstable."""
    tick(svc)
    boundary = asyncio.run(svc.sessions.get(scene.repo_id, "default", GATE_DIR)).last_stable_boundary
    assert boundary is not None and boundary.marker == "[?]" and boundary.stable is True
    scene.make(status="Delivered")
    tick(svc)
    boundary = asyncio.run(svc.sessions.get(scene.repo_id, "default", GATE_DIR)).last_stable_boundary
    assert boundary.stable is False and "action_in_flight" in boundary.reasons


def test_busy_slot_makes_the_boundary_unstable(svc, scene, fake_host):
    fake_host.set_running(scene.slot_key, True)
    tick(svc)
    binding = asyncio.run(svc.sessions.get(scene.repo_id, "default", GATE_DIR))
    assert "session_busy" in binding.last_stable_boundary.reasons


def test_stale_card_retires_while_the_live_boundary_keeps_its_card(svc, scene):
    """Retirement is by dedupe key, or a ``Queued`` gate card would be dismissed with its `[?]` still on disk."""
    gate = gate_card_after_a_tick(svc, scene)
    stale_id = insert_card(svc, scene, type="gate", status="Queued", dedupe_key="gate:vanished:boundary",
                           stage="user-stories")
    tick(svc)
    assert status_of(svc, stale_id) == "Cancelled"
    assert status_of(svc, gate.action_id) == "Queued"


def test_failed_cards_are_marked_superseded_and_never_cancelled(svc, scene):
    """``Failed`` is terminal (P07): cancelling it would erase the fingerprint and breaker context."""
    failed_id = insert_card(svc, scene, type="run", status="Failed", dedupe_key="run:vanished:boundary")
    tick(svc)
    assert status_of(svc, failed_id) == "Failed"
    assert evidence_of(svc, failed_id)["superseded"] is True


def _hide_intent(root: Path, intent_dir: str = GATE_DIR) -> Path:
    """Move the intent directory out of the space: a branch switch or a hand deletion, as the scan sees it."""
    hidden = root / f".hidden-{intent_dir}"
    _record(root, intent_dir).rename(hidden)
    return hidden


def test_cards_of_an_intent_that_left_the_disk_are_retired_after_the_grace(svc, scene, clock, C):
    """§1.13 step 1b: a vanished intent's cards retire only after ``INTENT_MISSING_GRACE_SECS`` of absence."""
    gate = gate_card_after_a_tick(svc, scene)
    assert svc.reconciler.intents_on_disk() == 1
    failed_id = insert_card(svc, scene, type="failure", status="Failed", dedupe_key="failure:gone:fp",
                            risk_class="studio_only")
    _hide_intent(scene.root)
    tick(svc)
    # Inside the grace: nothing moves, but the count already says what the disk says.
    assert status_of(svc, gate.action_id) == "Queued"
    assert "intent_missing" not in svc.actions.retire_reasons
    assert svc.reconciler.intents_on_disk() == 0
    clock.advance(C.INTENT_MISSING_GRACE_SECS + 1)
    tick(svc)
    assert status_of(svc, gate.action_id) == "Cancelled"
    assert svc.actions.retire_reasons[-1] == "intent_missing"
    # ``Failed`` is terminal (P07): marked, never moved, as in the per-intent retirement.
    assert status_of(svc, failed_id) == "Failed"
    assert evidence_of(svc, failed_id)["superseded"] is True
    tick(svc)  # nothing left to remember: no live card for the vanished intent
    assert svc.actions.retire_reasons.count("intent_missing") == 1


def test_an_intent_that_comes_back_inside_the_grace_keeps_every_card(svc, scene, clock, C):
    """A checkout that briefly hides the directory must not cost the human their gate card."""
    gate = gate_card_after_a_tick(svc, scene)
    hidden = _hide_intent(scene.root)
    tick(svc)
    clock.advance(C.INTENT_MISSING_GRACE_SECS // 2)
    hidden.rename(_record(scene.root))
    tick(svc)
    # The timer is dropped the moment the directory is seen again, so a later absence starts afresh.
    clock.advance(C.INTENT_MISSING_GRACE_SECS)
    tick(svc)
    assert status_of(svc, gate.action_id) == "Queued"
    assert "intent_missing" not in svc.actions.retire_reasons
    assert svc.reconciler.intents_on_disk() == 1


def test_an_unavailable_repository_never_sweeps_its_cards(svc, scene, clock, C):
    """An unmounted repository says nothing about its intents, and its timers are forgotten while it is away."""
    gate = gate_card_after_a_tick(svc, scene)
    parked = scene.root.parent / f"{scene.root.name}-parked"
    scene.root.rename(parked)
    try:
        tick(svc)
        clock.advance(C.INTENT_MISSING_GRACE_SECS * 2)
        tick(svc)
        assert svc.repos.get(scene.repo_id).availability != "available"
        assert status_of(svc, gate.action_id) == "Queued"
        assert "intent_missing" not in svc.actions.retire_reasons
    finally:
        parked.rename(scene.root)
    tick(svc)
    assert status_of(svc, gate.action_id) == "Queued"
    assert svc.reconciler.intents_on_disk() == 1


def test_a_truncated_listing_never_sweeps_cards(svc, scene, clock, C, monkeypatch):
    """A listing cut at ``MAX_INTENTS_PER_REPO`` is a lower bound, never proof that an intent is gone."""
    gate = gate_card_after_a_tick(svc, scene)
    # An earlier-sorting second intent and a cap of one push the gate's intent off the listing, not the disk.
    other = _record(scene.root, "260901-earlier")
    other.mkdir(parents=True)
    (other / "aidlc-state.md").write_text((_record(scene.root) / "aidlc-state.md").read_text())
    monkeypatch.setattr(C, "MAX_INTENTS_PER_REPO", 1)
    tick(svc)
    clock.advance(C.INTENT_MISSING_GRACE_SECS * 2)
    tick(svc)
    assert status_of(svc, gate.action_id) == "Queued"
    assert "intent_missing" not in svc.actions.retire_reasons


def test_resolution_releases_the_execution_lease(svc, scene, fake_host):
    """A resolved action gives its repository back: the in-flight row was the lease."""
    action_id = scene.make(status="Delivered", lease_generation=7)
    lease = asyncio.run(svc.scheduler.get(scene.repo.resolved_identity))
    assert (lease.kind, lease.action_id, lease.generation) == ("execution", action_id, 7)
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key)
    tick(svc)
    assert status_of(svc, action_id) == "StateChanged"
    assert asyncio.run(svc.scheduler.get(scene.repo.resolved_identity)) is None


# --- 7. failure classification ---


@pytest.mark.parametrize("message,expected", [
    ("ACP stream closed unexpectedly", "transport"), ("connection reset by peer", "transport"),
    ("ECONNRESET", "transport"), ("Rate limit exceeded, retry later", "rate_limit"),
    ("HTTP 429 Too Many Requests", "rate_limit"), ("request was throttled", "rate_limit"),
    ("turn exceeded the 7200s ceiling", "acp_timeout"), ("TimeoutError: no response", "acp_timeout"),
    ("Refusing to record this answer: a real human has not acted", "guard"), ("Unknown intent: nope", "validation"),
    ("Ambiguous intent", "validation"), ("Invalid scope: banana", "validation"),
    ("Cannot use --resume here", "validation"), ("EACCES: permission denied, open '/x'", "permission"),
    ("bun: command not found", "dependency"), ("Cannot find module 'aidlc-state.ts'", "dependency"),
    ("something else entirely", "config"),
])
def test_failure_classification_table(svc, message, expected):
    fingerprint, cls, summary = svc.reconciler.classify_failure(host_error=message, audit_errors=[])
    assert cls == expected
    assert fingerprint.startswith(f"{expected}.")
    assert summary == f"template.failure.{expected}"


def test_fingerprints_are_stable_across_volatile_detail(svc):
    """The same failure must fingerprint the same twice, or the breaker never reaches its threshold."""
    a, _, _ = svc.reconciler.classify_failure(
        host_error="EACCES: permission denied, open '/Users/a/repo/x.md' (pid 1234)", audit_errors=[])
    b, _, _ = svc.reconciler.classify_failure(
        host_error="EACCES: permission denied, open '/Users/b/other/y.md' (pid 99)", audit_errors=[])
    assert a == b


def test_error_burst_fails_the_action_and_a_single_row_does_not(svc, scene, fake_host, clock):
    """Three ``ERROR_LOGGED`` rows are a failure; one is AI-DLC correcting itself (FORMAT-NOTES §6)."""
    action_id = scene.make(status="Delivered")
    append_audit(scene.root, F.audit_block("ERROR_LOGGED", "2026-09-04T09:59:30Z", Tool="aidlc-orchestrate.ts",
                                           Error="ACP stream closed"))
    finish_turn(fake_host, scene.slot_key)
    tick(svc)
    assert status_of(svc, action_id) == "Delivered"
    # Explicit error text: the fixture's default ("ACP transport closed") is not a pinned transport string.
    append_audit(scene.root, F.error_burst_audit(ts="2026-09-04T09:59:40Z", count=3, error="ACP stream closed"))
    clock.advance(5)
    tick(svc)
    assert status_of(svc, action_id) == "Failed"
    assert svc.actions.failures[-1]["class"] == "transport"


@pytest.mark.parametrize("initial_status", ["Delivered", "ReconciliationRequired"])
def test_running_question_can_recover_from_tool_errors_before_it_finishes(svc, scene, fake_host, initial_status):
    action_id = scene.make(status=initial_status, action_type="question", decision="answers", wire_text="Yes",
                           evidence_extra={"delivery_confirmed": True},
                           payload={"decision": "answers", "answers": [{"index": 1, "option_letters": ["A"]}]})
    append_audit(scene.root, F.audit_block("HUMAN_TURN", T0),
                 F.error_burst_audit(ts="2026-09-04T10:00:01Z", count=3, error="Missing --details <text>"))
    touch_human_turn(scene.root)
    scene.slot.running = True
    asyncio.run(svc.reconciler.reconcile_now(action_id))
    assert status_of(svc, action_id) == initial_status
    assert svc.actions.failures == []
    append_audit(scene.root, F.audit_block("QUESTION_ANSWERED", "2026-09-04T10:00:09Z", Stage=GATE_STAGE, Details="Yes"))
    finish_turn(fake_host, scene.slot_key, wire_text="Yes")
    asyncio.run(svc.reconciler.reconcile_now(action_id))
    assert status_of(svc, action_id) == "ResolvedNoTransition"
    assert svc.actions.failures == []


# --- 8. scan pool, budget and retention ---


def test_scan_fanout_bounded(svc, tmp_path, monkeypatch, R, C):
    """At most ``SCAN_CONCURRENCY`` repository walks in flight, counted inside the bounded part (R11/C32)."""
    concurrent = peak = 0
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
    assert 1 <= peak <= C.SCAN_CONCURRENCY


def test_tick_honours_the_scan_budget_and_rotates(svc, tmp_path, monkeypatch, R):
    """A spent budget stops new scans and the remainder leads the next tick, or a registry's tail never runs."""
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
    tick(svc)
    first = list(seen[-1])
    seen.append([])
    tick(svc)
    second = list(seen[-1])
    assert 0 < len(first) < 6
    assert second and second[0] != first[0]


def test_tick_never_blocks_the_loop(svc, scene):
    """Disk work goes to a worker thread: a heartbeat beside the tick keeps its turns during slow snapshots (§0.6)."""
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
    action_id = scene.make(status="Delivered")
    asyncio.run(svc.activity.record(kind="action.submitted", repo_id=scene.repo_id, action_id=action_id, params={},
                                    human_text="the human's own words"))
    assert svc.storage.get("actions", action_id)["human_text"] == "the human's own words"
    clock.advance((svc.settings.human_text_retention_days() + 1) * 86400)
    tick(svc)
    assert svc.storage.get("actions", action_id)["human_text"] is None
    assert all(row.get("human_text") is None for row in svc.storage.select("activity", {"kind": "action.submitted"}))


def insert_draft(svc, scene, *, status: str = "ready", request: dict | None = None, at: str | None = None) -> str:
    draft_id = svc.ids.new("d")
    svc.storage.insert("advisor_drafts", {
        "draft_id": draft_id, "action_id": scene.make(status="Delivered"), "kind": "gate_analysis", "status": status,
        "request_json": request or {}, "created_at": at or svc.clock.iso(), "updated_at": at or svc.clock.iso(),
        "expires_at": "2026-09-04T09:00:00Z"})
    return draft_id


def test_expired_advisor_drafts_are_marked_not_deleted(svc, scene, advisor):
    """Retention retires the row through the real broker, so the tab watching the card hears about it."""
    insert_draft(svc, scene)
    tick(svc)
    rows = svc.storage.select("advisor_drafts")
    assert rows and rows[0]["status"] == "expired"


def test_reconcile_now_answers_for_one_action(svc, scene, fake_host):
    action_id = scene.make(status="Delivered")
    approve_on_disk(scene.root)
    finish_turn(fake_host, scene.slot_key)
    assert asyncio.run(svc.reconciler.reconcile_now(action_id)).status == "StateChanged"


def test_reconciler_never_writes_aidlc_state(svc, scene):
    """Reconciliation reads AI-DLC and writes only Studio's rows."""
    watched = [_record(scene.root) / "aidlc-state.md", _record(scene.root) / "audit" / SHARD,
               _record(scene.root) / QUESTIONS_REL, scene.root / "aidlc" / "spaces" / "default" / "intents" /
               "active-intent", scene.root / "aidlc" / "active-space"]
    before = {p: p.stat().st_mtime_ns for p in watched if p.exists()}
    scene.make(status="Delivered")
    scene.make(status="Delivering", deadline_at=PAST_DEADLINE)
    asyncio.run(svc.reconciler.startup())
    tick(svc)
    assert {p: p.stat().st_mtime_ns for p in watched if p.exists()} == before


def test_an_idle_tick_writes_nothing_and_publishes_nothing(svc, scene, studio, clock):
    """Later ticks over an unchanged repo write nothing: no binding CAS, no ``intent.updated`` frame."""
    tick(svc)

    def state() -> tuple[Any, ...]:
        binding = svc.storage.select("intent_bindings")[0]
        events = [r for r in asyncio.run(svc.events.since(0)) if r.type == "intent.updated"]
        return binding["binding_generation"], binding["last_stable_boundary_json"], len(events)

    first = state()
    assert first[2] >= 1, "the first tick should publish the intent it just learned about"
    for _ in range(3):
        clock.advance(studio.constants.RECONCILE_INTERVAL_SECS or 5)
        tick(svc)
    assert state() == first, "an idle tick wrote a row or published a frame"


def test_a_boundary_that_really_changes_is_still_written_and_published(svc, scene, gate_repo, clock):
    """The dedupe must not become a mute button: real movement still reaches the ring."""
    tick(svc)
    before = svc.storage.select("intent_bindings")[0]
    published = len([r for r in asyncio.run(svc.events.since(0)) if r.type == "intent.updated"])
    record = Path(gate_repo) / "aidlc/spaces/default/intents" / GATE_DIR / "aidlc-state.md"
    record.write_text(record.read_text().replace("[?]", "[x]", 1))  # the boundary's marker and token both change
    clock.advance(30)
    tick(svc)
    after = svc.storage.select("intent_bindings")[0]
    assert after["last_stable_boundary_json"] != before["last_stable_boundary_json"]
    assert len([r for r in asyncio.run(svc.events.since(0)) if r.type == "intent.updated"]) > published


# --- 9. the advisor pass: drafting ahead of the click (§1.13 step 6, A24) ---


class StubAdvisor:
    """The two methods ``_advisor_pass`` calls, recording order and able to explode; ``expire`` may be real."""

    def __init__(self, *, raises: str = "", settled: int = 0, started: int = 0,
                 on_settle: Callable[[], None] | None = None, expire: Callable[[], Any] | None = None) -> None:
        self.calls: list[str] = []
        self._raises, self._settled, self._started = raises, settled, started
        self._on_settle, self._expire = on_settle, expire

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
    """The real ``AdvisorBroker`` attached as ``Services`` attaches it (§1.18), with ``subagents`` wired."""
    module = studio.advisor
    assert module is not None, getattr(studio, "advisor__error", "advisor.py did not import")
    fake_host.subagents = SimpleNamespace(get=lambda spawn_id: svc.ctx.spawn.results.get(spawn_id))
    svc.advisor = module.AdvisorBroker(
        svc.ctx, svc.storage, svc.host, svc.projection, svc.actions, svc.reader, svc.activity, svc.events, svc.clock,
        svc.ids, svc.settings, agent_lister=lambda: [SimpleNamespace(
            name=module.ADVISOR_AGENT_NAME, filename=f"{module.ADVISOR_AGENT_FILE_PREFIX}advisor.json")])
    return svc.advisor


def grant(svc, *repo_ids: str) -> None:
    """The standing grant, written through the real settings service so its validation is the one used."""
    asyncio.run(svc.settings.put({"advisor": {"auto_draft_repo_ids": list(repo_ids)}}))


def queued_ids(svc) -> list[str]:
    return [str(row["action_id"]) for row in svc.storage.select("actions", {"status": "Queued"})]


def test_a_tick_drafts_ahead_for_a_granted_repository(svc, scene, advisor, studio, clock, C):
    """The tick is the trigger, so the draft is early: a UI trigger starts only once a human waits (FR-ADV-001)."""
    grant(svc, scene.repo_id)
    tick(svc)
    auto_kinds = studio.advisor.ADVISOR_AUTO_KIND_BY_TYPE
    row, = svc.storage.select("advisor_drafts")
    card = svc.storage.get("actions", row["action_id"])
    assert len(queued_ids(svc)) > 1, "one eligible card would prove nothing about the per-tick limit"
    assert card["status"] == "Queued" and card["repo_id"] == scene.repo_id
    assert row["kind"] == auto_kinds[str(card["type"])]
    assert row["request_json"]["auto"] is True, "the flag is what licenses the UI to pre-fill a form"
    assert len(svc.ctx.spawn.spawned) == C.ADVISOR_AUTO_DRAFT_PER_TICK
    # A distinct kind, not a param: Activity filters and translates on who started a draft.
    assert len(svc.storage.select("activity", {"kind": "advisor.auto_requested"})) == 1
    assert svc.storage.select("activity", {"kind": "advisor.requested"}) == []
    # The pass walks forward: one more card per tick, never a card twice, never a type with no automatic kind.
    for _ in range(3):
        clock.advance(C.RECONCILE_INTERVAL_SECS or 5)
        tick(svc)
    drafted = [str(d["action_id"]) for d in svc.storage.select("advisor_drafts")]
    eligible = {str(r["action_id"]) for r in svc.storage.select("actions", {"status": "Queued"})
                if str(r["type"]) in auto_kinds}
    assert len(drafted) == len(set(drafted)), "a card drew a second automatic draft"
    assert set(drafted) == eligible
    assert eligible < set(queued_ids(svc)), "a card whose type has no automatic kind must stay undrafted"


def test_a_tick_drafts_nothing_without_the_grant(svc, scene, advisor):
    """The empty grant ships on every install and must mean exactly today's behaviour: clicks only."""
    tick(svc)
    assert queued_ids(svc), "the scan must have derived a card, or this proves nothing"
    assert svc.storage.select("advisor_drafts") == []
    assert svc.ctx.spawn.spawned == []


@pytest.mark.parametrize("explodes", ["settle_open", "autodraft"])
def test_a_tick_completes_when_the_advisor_raises(svc, scene, clock, explodes, advisor):
    """Housekeeping may fail, the delivery loop may not; ``last_tick_ms`` is written only once the last pass returned."""
    svc.advisor = StubAdvisor(raises=explodes, expire=advisor.expire_stale)
    insert_draft(svc, scene)
    tick(svc)
    first = svc.reconciler.status()["last_tick_at"]
    clock.advance(30)
    tick(svc)
    assert svc.reconciler.status()["last_tick_ms"] is not None
    assert svc.reconciler.status()["last_tick_at"] > first
    assert svc.storage.select("advisor_drafts")[0]["status"] == "expired", "retention ran behind it"
    assert svc.advisor.calls[-3:] == ["settle_open", "autodraft", "expire_stale"], (
        "both halves are still attempted, and retention still reaches the broker behind them")


def test_a_container_without_an_advisor_ticks_cleanly(svc, scene):
    """The advisor is the one collaborator a tick does not need, in every partial shape."""
    assert not hasattr(svc, "advisor")
    assert asyncio.run(svc.reconciler._advisor_pass()) == (0, 0)
    # A broker from before this feature answers the same way: no half of the pass runs against it.
    svc.advisor = SimpleNamespace(settle_open=None)
    assert asyncio.run(svc.reconciler._advisor_pass()) == (0, 0)
    del svc.advisor
    tick(svc)
    assert svc.reconciler.status()["last_tick_ms"] is not None


def test_the_pass_settles_before_it_starts_anything_new(svc, scene):
    """Settling frees the inflight slots the cap counts before anything new starts; retention trails both."""
    svc.advisor = StubAdvisor(settled=3, started=1)
    assert asyncio.run(svc.reconciler._advisor_pass()) == (3, 1)
    assert svc.advisor.calls == ["settle_open", "autodraft"]
    svc.advisor.calls.clear()
    tick(svc)
    assert svc.advisor.calls == ["settle_open", "autodraft", "expire_stale"]


def test_retention_still_runs_behind_the_pass_in_the_same_tick(svc, scene, advisor):
    """A draft the pass settles, already past its TTL, is expired by this tick and not left ``ready`` a cycle."""
    draft_id = insert_draft(svc, scene, status="running", request={"auto": True}, at="2026-09-04T08:00:00Z")

    def settled_by_the_pass() -> None:
        svc.storage.update("advisor_drafts", draft_id, {"status": "ready"})

    svc.advisor = StubAdvisor(settled=1, on_settle=settled_by_the_pass, expire=advisor.expire_stale)
    tick(svc)
    assert svc.storage.get("advisor_drafts", draft_id)["status"] == "expired"

"""Sessions: the bridge degrades rather than raises; "unknown" is never "idle"; a slot is refused, never guessed."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from _helpers_a import refused
from conftest import APP_NAME

APP_ROOT = Path(__file__).resolve().parent.parent
TS = "2026-09-04T10:00:00Z"
#: The host's transcript spelling (offset-aware, microseconds) sorts BEFORE the same second's `…Z` as a string.
HOST_TS = "2026-09-04T10:00:00.500000+00:00"
IDENTITY = "1:100"
SLOT = f"{APP_NAME}-r_1-260904-gate-demo"
INTENT = "260904-gate-demo"
BINDING = f"r_1:default:{INTENT}"


@pytest.fixture
def S(studio):
    assert studio.sessions is not None, getattr(studio, "sessions__error", "sessions.py did not import")
    return studio.sessions


@pytest.fixture
def C(studio):
    return studio.constants


@pytest.fixture
def errors(studio):
    return studio.errors


@pytest.fixture
def store(studio, fake_ctx, clock, ids):
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME, clock=clock)
    st.open()
    yield st
    st.close()


@pytest.fixture
def bridge(S, clock):
    return S.HostBridge(clock, logging.getLogger("test.sessions"))


def run(coro):
    return asyncio.run(coro)


# --- host stand-ins ---

#: Every key `_ChatSlot.to_dict()` emits on 0.5.0-insider.9, so a dropped key `SlotView` maps fails here.
FULL_KEYS = (
    "key title agent effective_agent model reasoning_effort mode surface workspace project artifact "
    "messages running orchestrating queue_depth stopping pending_approval pending_approval_info "
    "last_activity_ts waiting_for_input needs_input interrupted stop_state wait_state created last_ts "
    "last_turn_ts last_message source_links source_links_total todo has_options options prompt_preview "
    "trust trust_reads trusted_patterns_count slack_linked slack_channel slack_thread_ts folder_id "
    "pinned tags color_index color_hex color_theme theme_consent theme_consent_sha memory_mode "
    "forked_from linked_session_key app origin"
).split()


class Slot:
    """A duck-typed ``_ChatSlot``: the real ``to_dict()`` keys plus the private busy attributes."""

    def __init__(self, key: str = SLOT, *, project: str = "/FIXTURE/repo/r_1", agent: str = "aidlc",
                 app: str = "", running: bool = False, **over: Any) -> None:
        self.key, self.project, self.agent, self._app, self.running, self.title = key, project, agent, app, running, key
        self.messages: list[dict] = []
        self._queue: list[dict] = []
        self._last_enqueue_ts = ""
        self._stop_state = "idle"
        self._stopping = False
        self._in_stage_execution = False
        self._approval_futures: dict[str, Any] = {}
        self._question_pending: dict[str, dict] = {}
        self._subagent_deliveries_inflight = 0
        self.linked_session_key = ""
        self.over = dict(over)

    @property
    def queue_depth(self) -> int:
        return len(getattr(self, "_queue", ()))

    def _approval_info(self) -> dict | None:
        """The 0.8 host's own reading (``slot_projection.to_dict``): the newest unresolved permission row."""
        for message in reversed(self.messages):
            if message.get("role") != "permission":
                continue
            meta = json.loads(message.get("cls") or "{}")
            if meta.get("resolved"):
                continue
            return {"tool": message.get("content") or "", "tool_input": meta.get("tool_input", ""),
                    "tool_kind": "", "request_id": meta.get("approval_id", meta.get("request_id", ""))}
        return None

    def to_dict(self) -> dict:
        slot_pending = any(
            not getattr(f, "done", lambda: False)() for f in getattr(self, "_approval_futures", {}).values())
        payload: dict[str, Any] = {name: "" for name in FULL_KEYS}
        payload.update({
            "key": self.key, "title": self.title, "agent": self.agent, "project": self.project, "app": self._app,
            "messages": len(self.messages), "running": self.running,
            "orchestrating": getattr(self, "_in_stage_execution", False), "queue_depth": self.queue_depth,
            "stopping": getattr(self, "_stopping", False), "pending_approval": slot_pending,
            "pending_approval_info": self._approval_info() if slot_pending else None, "waiting_for_input": False,
            "needs_input": bool(getattr(self, "_question_pending", None)), "interrupted": False,
            "stop_state": getattr(self, "_stop_state", "idle"), "wait_state": None,
            "last_ts": self.messages[-1].get("ts", "") if self.messages else "", "last_turn_ts": "",
            "has_options": False, "options": [], "slack_linked": False, "todo": None,
            "linked_session_key": self.linked_session_key})
        payload.update(self.over)
        return payload

    def append(self, role: str, content: str, *, ts: str = HOST_TS, meta: dict | None = None,
               cls: str = "msg msg-u") -> dict:
        row = {"role": role, "content": content, "cls": cls, "ts": ts}
        if meta is not None:
            row["meta"] = meta
        self.messages.append(row)
        return row

    def enqueue(self, content: str, *, meta: dict | None = None, qid: str = "q1") -> None:
        item: dict[str, Any] = {"id": qid, "content": content, "kind": ""}
        if meta is not None:
            item["meta"] = meta
        self._queue.append(item)
        self._last_enqueue_ts = HOST_TS


class PartialSlot:
    """A host whose serialisation carries almost nothing: every mapped field must still resolve."""

    key = "slot-partial"
    messages: list[dict] = []

    def to_dict(self) -> dict:
        return {"key": self.key, "running": True, "last_ts": HOST_TS}


class Slack:
    def __init__(self, *, fail: bool = False) -> None:
        self.posts: list[dict] = []
        self.fail = fail

    async def open_dm(self, user_id: str) -> str:
        if self.fail:
            raise RuntimeError("slack transport down")
        return f"D{user_id}"

    async def post_blocks(self, channel: str, blocks: list, text: str = "", thread_ts: str | None = None) -> str:
        self.posts.append({"channel": channel, "blocks": blocks, "text": text})
        return f"ts-{len(self.posts)}"


class Subagents:
    def __init__(self, *, running: int = 0, queued: int = 0, info: Any = None) -> None:
        self._running, self._queued, self._info = running, queued, info

    def running_agents_for(self, parent_key: str) -> list[dict]:
        return [{"id": f"s{i}"} for i in range(self._running)]

    def _queued_depth(self, parent_session_key: str) -> int:
        return self._queued

    def queued_count_for(self, parent_session_key: str) -> int:
        return self._queued

    def get(self, agent_id: str) -> Any:
        return self._info


def host(slot: Slot | PartialSlot | None = None, *, owner: str = "owner-1", slack: Any = ...,
         subagents: Any = ..., busy: set[str] | None = None) -> SimpleNamespace:
    """A duck-typed ``DashboardState`` carrying every symbol the bridge feature-detects."""
    slots: dict[str, Any] = {slot.key: slot} if slot is not None else {}
    state = SimpleNamespace(_slots=slots, _pending_questions={}, notifications=[], get_slot=slots.get,
                            sessions=SimpleNamespace(is_busy=lambda key: key in (busy or set())), owner_id=owner,
                            slack_client=Slack() if slack is ... else slack,
                            subagents=Subagents() if subagents is ... else subagents)

    def notify(kind, title, body, *, meta=None, url=None, actions=None):
        state.notifications.append({"kind": kind, "title": title, "body": body, "meta": meta, "url": url,
                                    "actions": actions})

    state.notify = notify
    return state


class Activity:
    """``ActivityProjector.record`` stand-in: keeps the kwargs so a test can read the audit trail."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def record(self, **kwargs: Any) -> int:
        self.rows.append(kwargs)
        return len(self.rows)

    def kinds(self) -> list[str]:
        return [r["kind"] for r in self.rows]

    def of(self, kind: str) -> list[dict]:
        return [r for r in self.rows if r["kind"] == kind]


# --- HostBridge: attachment and capabilities ---


def test_an_unattached_bridge_answers_nothing_and_says_why(S, bridge):
    assert bridge.attached() is False
    assert bridge.slot(SLOT) is None
    assert bridge.slots_for_project("/FIXTURE/repo/r_1") == [] and bridge.slots_by_prefix("aidlc-studio-") == []
    assert bridge.recent_rows(SLOT, since_ts=TS) == []
    assert bridge.find_delivery_row(SLOT, since_ts=TS, delivery_id="dl_1", wire_text="Approve") is None
    assert bridge.pending_question_cards(SLOT) == []
    assert bridge.is_busy("dashboard:x") is None
    assert bridge.subagent("spawn-1") is None
    assert bridge.notify("t", "b", url="/apps/aidlc-studio?view=actions") is False
    assert run(bridge.slack_dm([], "text")) is None
    caps = bridge.capabilities()
    assert set(caps) == set(S.CAPABILITIES)
    assert all(cap.available is False for cap in caps.values())
    assert {cap.reason for cap in caps.values()} == {S.REASON_NOT_ATTACHED}


def test_attach_is_idempotent_and_re_probes_only_when_stale(S, bridge, clock):
    state = host(Slot())
    bridge.attach(state)
    first = bridge.capabilities()["slots"].checked_at
    clock.advance(1)
    bridge.attach(state)
    assert bridge.capabilities()["slots"].checked_at == first, "a per-request attach must not re-probe"
    clock.advance(S.HOST_PROBE_TTL_SECS)
    bridge.attach(state)
    assert bridge.capabilities()["slots"].checked_at != first, "a stale probe must be refreshed"


def test_attach_ignores_none_so_a_handler_cannot_detach_the_host(bridge):
    bridge.attach(host(Slot()))
    bridge.attach(None)
    assert bridge.attached() is True
    assert bridge.slot(SLOT) is not None


def test_a_new_host_object_is_re_probed(bridge):
    bridge.attach(host(Slot(), slack=None))
    assert bridge.capabilities()["slack"].available is False
    bridge.attach(host(Slot()))
    assert bridge.capabilities()["slack"].available is True


# --- HostBridge: SlotView mapping and busy predicates ---


def test_slot_view_maps_every_key_the_contract_lists(bridge):
    slot = Slot(project="/FIXTURE/repo/r_1", app="aidlc-studio", running=True)
    slot.append("user", "Approve")
    slot.enqueue("second")
    slot._in_stage_execution = True
    slot._stop_state = "soft_pending"
    slot._stopping = True
    slot._approval_futures = {"req-1": SimpleNamespace(done=lambda: False)}
    slot._subagent_deliveries_inflight = 2
    slot.over = {"has_options": True, "options": ["Approve", "Request Changes"], "slack_linked": True,
                 "interrupted": True, "needs_input": True, "waiting_for_input": True, "last_turn_ts": HOST_TS,
                 "wait_state": {"wait_id": "w1", "seconds": 30}, "title": "repo / gate demo"}
    bridge.attach(host(slot, subagents=Subagents(running=1, queued=2)))
    view = bridge.slot(SLOT)
    assert view is not None
    assert (view.key, view.project, view.agent, view.app, view.title) == (
        SLOT, "/FIXTURE/repo/r_1", "aidlc", "aidlc-studio", "repo / gate demo")
    flags = ("running", "pending_approval", "needs_input", "waiting_for_input", "interrupted", "has_options",
             "slack_linked", "stopping", "in_stage_execution")
    assert [flag for flag in flags if getattr(view, flag) is not True] == []
    assert (view.queue_depth, view.messages, view.approvals_pending) == (1, 1, 1)
    assert (view.subagents_running, view.subagents_queued, view.deliveries_inflight) == (1, 2, 2)
    assert (view.stop_state, view.last_ts, view.last_turn_ts) == ("soft_pending", HOST_TS, HOST_TS)
    assert (view.linked_session_key, view.session_key) == ("", f"dashboard:{SLOT}")
    assert view.options == ("Approve", "Request Changes")
    assert view.wait_state == {"wait_id": "w1", "seconds": 30}
    # The wire shape is pinned to the §3.1 interface: no extra keys, no missing ones.
    assert set(view.to_json()) == {
        "key", "running", "project", "agent", "app", "queue_depth", "pending_approval", "needs_input",
        "waiting_for_input", "stop_state", "interrupted", "last_ts", "last_turn_ts", "linked_session_key",
        "session_key", "messages", "has_options", "options", "slack_linked", "title", "stopping", "wait_state",
        "in_stage_execution", "approvals_pending", "subagents_running", "subagents_queued", "deliveries_inflight"}


def test_slot_view_defaults_every_key_the_host_does_not_send(bridge):
    bridge.attach(host(PartialSlot()))
    view = bridge.slot("slot-partial")
    assert view is not None
    assert (view.project, view.agent, view.app, view.title) == ("", "", "", "slot-partial")
    assert (view.queue_depth, view.messages) == (0, 0)
    assert (view.pending_approval, view.needs_input, view.waiting_for_input) == (False, False, False)
    assert view.stop_state == "idle"
    assert view.options == () and view.wait_state is None
    assert view.last_turn_ts == HOST_TS, "a host without last_turn_ts falls back to the newest row"
    assert (view.in_stage_execution, view.approvals_pending, view.deliveries_inflight) == (False, 0, 0)


def test_a_linked_session_key_wins_over_the_slot_name(bridge):
    slot = Slot()
    slot.linked_session_key = "slack:1725444000.1234"
    bridge.attach(host(slot))
    assert bridge.slot(SLOT).session_key == "slack:1725444000.1234"


def test_an_unreadable_serialisation_marks_the_slots_capability(bridge):
    slot = Slot()
    slot.to_dict = lambda: (_ for _ in ()).throw(AttributeError("display_title"))
    bridge.attach(host(slot))
    assert bridge.slot(SLOT) is None
    cap = bridge.capabilities()["slots"]
    assert cap.available is False and cap.reason == "unusable:slot.to_dict"


def busy_view(S, **over: Any):
    slot = Slot(running=over.pop("running", False))
    slot._in_stage_execution = over.pop("in_stage_execution", False)
    slot._stop_state = over.pop("stop_state", "idle")
    slot._stopping = over.pop("stopping", False)
    for _ in range(over.pop("queue_depth", 0)):
        slot.enqueue("queued text")
    for i in range(over.pop("approvals_pending", 0)):
        slot._approval_futures[f"req-{i}"] = SimpleNamespace(done=lambda: False)
    slot._subagent_deliveries_inflight = over.pop("deliveries_inflight", 0)
    running, queued = over.pop("subagents_running", 0), over.pop("subagents_queued", 0)
    assert not over, f"unused: {over}"
    bridge = S.HostBridge(SimpleNamespace(iso=lambda: TS, now=lambda: 0.0, monotonic=lambda: 0.0),
                          logging.getLogger("test.sessions"))
    bridge.attach(host(slot, subagents=Subagents(running=running, queued=queued)))
    return bridge.slot(SLOT)


def test_busy_reasons_returns_the_full_ordered_set(S):
    view = busy_view(S, running=True, in_stage_execution=True, stop_state="killing", queue_depth=1,
                     approvals_pending=1, subagents_running=1, deliveries_inflight=1)
    assert S.busy_reasons(view) == S.BUSY_REASONS
    assert view.busy_reasons == S.BUSY_REASONS, "the projection reads the predicate off the view"


def test_an_idle_slot_is_dispatchable(S):
    assert S.busy_reasons(busy_view(S)) == ()


@pytest.mark.parametrize("kwargs, reason", [
    ({"running": True}, "running"), ({"in_stage_execution": True}, "stage_execution"),
    ({"stop_state": "soft_pending"}, "stopping"), ({"stopping": True}, "stopping"), ({"queue_depth": 1}, "queued"),
    ({"approvals_pending": 1}, "approval_pending"), ({"subagents_running": 1}, "subagents"),
    ({"subagents_queued": 1}, "subagents"), ({"deliveries_inflight": 1}, "deliveries_inflight"),
])
def test_each_predicate_alone_blocks_a_dispatch(S, kwargs, reason):
    assert S.busy_reasons(busy_view(S, **kwargs)) == (reason,)


def test_busy_reasons_refuses_to_default_a_field_it_cannot_read(S):
    """A shape it cannot read must raise, because the scheduler reads a raise as "busy"."""
    with pytest.raises(AttributeError):
        S.busy_reasons(SimpleNamespace(running=False))


@pytest.mark.parametrize("serialised,expected", [(None, False), (True, True)])
def test_a_renamed_stage_execution_attribute_falls_back_to_the_serialised_flag(bridge, serialised, expected):
    slot = Slot()
    del slot._in_stage_execution
    slot.over = {"orchestrating": serialised}  # None: a host that stopped serialising it too
    bridge.attach(host(slot))
    view = bridge.slot(SLOT)
    assert view is not None and view.in_stage_execution is expected
    assert bridge.capabilities()["slots"].available is True


def test_approvals_fall_back_to_the_serialised_boolean(bridge):
    slot = Slot()
    del slot._approval_futures
    slot.over = {"pending_approval": True}
    bridge.attach(host(slot))
    view = bridge.slot(SLOT)
    assert view.approvals_pending == 1 and "approval_pending" in view.busy_reasons


def test_an_unreadable_approval_future_counts_as_pending(bridge):
    slot = Slot()
    slot._approval_futures = {"req-1": object()}
    bridge.attach(host(slot))
    assert bridge.slot(SLOT).approvals_pending == 1


def test_a_missing_delivery_counter_reads_zero(bridge):
    slot = Slot()
    del slot._subagent_deliveries_inflight
    bridge.attach(host(slot))
    assert bridge.slot(SLOT).deliveries_inflight == 0


def test_a_host_without_a_subagent_manager_marks_the_capability(bridge):
    bridge.attach(host(Slot(), subagents=None))
    view = bridge.slot(SLOT)
    assert (view.subagents_running, view.subagents_queued) == (0, 0)
    cap = bridge.capabilities()["subagents"]
    assert cap.available is False and cap.reason == "missing:state.subagents"


def test_the_public_queue_depth_twin_is_used_when_the_private_one_is_gone(bridge):
    class Legacy(Subagents):
        _queued_depth = None  # a host that kept only `queued_count_for`

    bridge.attach(host(Slot(), subagents=Legacy(queued=3)))
    assert bridge.slot(SLOT).subagents_queued == 3


# --- HostBridge: transcript evidence ---


def test_recent_rows_filters_roles_newest_first_and_redacts(bridge):
    slot = Slot()
    slot.append("user", "Approve")
    slot.append("assistant", "working on it")
    slot.append("queued", "/aidlc")
    slot.append("user", "token=0123456789abcdef")
    bridge.attach(host(slot))
    rows = bridge.recent_rows(SLOT, since_ts=TS)
    assert [r.role for r in rows] == ["user", "queued", "user"], "assistant rows are not evidence"
    assert "0123456789abcdef" not in rows[0].content
    assert rows[0].content.endswith("<redacted>")
    assert bridge.recent_rows(SLOT, since_ts=TS, roles=("assistant",))[0].content == "working on it"
    assert len(bridge.recent_rows(SLOT, since_ts=TS, limit=1)) == 1


@pytest.mark.parametrize("rows", [
    # Dropping a row stamped in the dispatch second would fake one of NotDelivered's four proofs.
    pytest.param([("Approve", HOST_TS)], id="kept_in_the_dispatch_second"),
    pytest.param([("older", "2026-09-04T09:59:59.999+00:00"), ("Approve", HOST_TS)], id="drops_rows_before_dispatch"),
    pytest.param([("Approve", "not-a-timestamp")], id="keeps_an_unreadable_stamp"),
])
def test_recent_rows_compares_instants_not_strings(bridge, rows):
    slot = Slot()
    for content, ts in rows:
        slot.append("user", content, ts=ts)
    bridge.attach(host(slot))
    assert HOST_TS < TS, "the string order really is the wrong way round"
    assert [r.content for r in bridge.recent_rows(SLOT, since_ts=TS)] == ["Approve"]


def test_recent_rows_on_an_absent_slot_is_empty_evidence_not_proof(S, bridge):
    bridge.attach(host(Slot()))
    assert bridge.recent_rows(f"{APP_NAME}-r_1-gone", since_ts=TS) == []
    cap = bridge.capabilities()["slot_messages"]
    assert cap.available is False and cap.reason == S.REASON_SLOT_ABSENT


def test_a_renamed_transcript_attribute_marks_slot_messages(bridge):
    slot = Slot()
    del slot.messages
    bridge.attach(host(slot))
    assert bridge.recent_rows(SLOT, since_ts=TS) == []
    cap = bridge.capabilities()["slot_messages"]
    assert cap.available is False and cap.reason == "missing:slot.messages"


def find_row(bridge, wire_text: str = "Approve"):
    return bridge.find_delivery_row(SLOT, since_ts=TS, delivery_id="dl_1", wire_text=wire_text)


def test_find_delivery_row_prefers_the_delivery_id_over_the_text(bridge):
    slot = Slot()
    slot.append("user", "Approve")  # a human typed it by hand
    slot.append("user", "Approve", meta={"studio_delivery_id": "dl_1", "studio_action_id": "a_1"})
    bridge.attach(host(slot))
    row = find_row(bridge)
    assert row is not None and row.meta["studio_delivery_id"] == "dl_1"


def test_find_delivery_row_falls_back_to_the_exact_wire_text(bridge):
    slot = Slot()
    slot.append("user", "Approve")
    bridge.attach(host(slot))
    assert find_row(bridge) is not None
    assert find_row(bridge, "Approve ") is None


def test_find_delivery_row_sees_a_message_still_in_the_queue(bridge):
    """The queue path drops the browser's meta, so a queued send has no transcript row yet."""
    slot = Slot(running=True)
    slot.enqueue("Approve")
    bridge.attach(host(slot))
    row = find_row(bridge)
    assert row is not None and row.role == "queued" and row.meta["queue_id"] == "q1"


def test_find_delivery_row_ignores_a_row_from_before_the_dispatch(bridge):
    slot = Slot()
    slot.append("user", "Approve", ts="2026-09-04T09:00:00+00:00")
    bridge.attach(host(slot))
    assert find_row(bridge) is None


# --- HostBridge: repo matching ---


def test_slot_matches_repo_requires_project_agent_and_app(bridge, tmp_path):
    repo, other = tmp_path / "repo", tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    canonical = os.path.realpath(str(repo))

    def view_for(**over):
        slot = Slot(project=over.pop("project", canonical), agent=over.pop("agent", "aidlc"), app=over.pop("app", ""))
        bridge.attach(host(slot))
        return bridge.slot(slot.key)

    assert bridge.slot_matches_repo(view_for(), canonical) is True
    assert bridge.slot_matches_repo(view_for(app=APP_NAME), canonical) is True
    assert bridge.slot_matches_repo(view_for(app="other-app"), canonical) is False
    assert bridge.slot_matches_repo(view_for(agent="kiro"), canonical) is False
    assert bridge.slot_matches_repo(view_for(project=str(other)), canonical) is False
    assert bridge.slot_matches_repo(view_for(project=""), canonical) is False
    assert bridge.slot_matches_repo(None, canonical) is False


def test_slot_matches_repo_accepts_a_case_aliased_path(bridge, tmp_path):
    """One directory, two spellings: `realpath` keeps the caller's case (A13), inodes do not lie."""
    repo = tmp_path / "Repo"
    repo.mkdir()
    alias = str(tmp_path / "repo")
    if not os.path.isdir(alias):
        pytest.skip("case-sensitive filesystem")
    slot = Slot(project=alias)
    bridge.attach(host(slot))
    assert bridge.slot_matches_repo(bridge.slot(slot.key), os.path.realpath(str(repo))) is True


def test_slots_for_project_and_by_prefix(bridge, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    canonical = os.path.realpath(str(repo))
    state = host()
    for slot in (Slot(SLOT, project=canonical), Slot("chat-7", project=canonical),
                 Slot(f"{APP_NAME}-r_1-other-intent", project=str(tmp_path))):
        state._slots[slot.key] = slot
    bridge.attach(state)
    assert sorted(v.key for v in bridge.slots_for_project(canonical)) == sorted([SLOT, "chat-7"])
    assert sorted(v.key for v in bridge.slots_by_prefix(f"{APP_NAME}-r_1-")) == [SLOT, f"{APP_NAME}-r_1-other-intent"]


def test_a_host_without_a_slot_table_degrades_the_slots_capability(bridge):
    state = host(Slot())
    del state._slots
    del state.get_slot
    bridge.attach(state)
    assert bridge.slots_for_project("/x") == []
    assert bridge.slot(SLOT) is None
    for name in ("state", "slots"):
        cap = bridge.capabilities()[name]
        assert cap.available is False
        assert cap.reason.startswith("missing:state."), "the reason must name the symbol that moved"


# --- HostBridge: session, owner, process facts ---


def test_is_busy_reports_the_host_answer_and_none_when_it_cannot(bridge):
    bridge.attach(host(Slot(), busy={"dashboard:" + SLOT}))
    assert bridge.is_busy("dashboard:" + SLOT) is True
    assert bridge.is_busy("dashboard:other") is False
    broken = host(Slot())
    del broken.sessions
    bridge.attach(broken)
    assert bridge.is_busy("dashboard:x") is None
    cap = bridge.capabilities()["sessions_busy"]
    assert cap.available is False and cap.reason == "missing:state.sessions"


def test_a_raising_is_busy_answers_unknown(bridge):
    state = host(Slot())
    state.sessions = SimpleNamespace(is_busy=lambda key: (_ for _ in ()).throw(KeyError(key)))
    bridge.attach(state)
    assert bridge.is_busy("dashboard:x") is None
    assert bridge.capabilities()["sessions_busy"].reason == "unusable:state.sessions.is_busy"


def test_is_owner_request_reproduces_the_host_rule(bridge):
    bridge.attach(host(Slot()))
    assert bridge.is_owner_request({"user": "owner-1", "app": ""}) is True
    assert bridge.is_owner_request({"user": "someone-else", "app": ""}) is False
    assert bridge.is_owner_request({"user": "owner-1", "app": "aidlc-studio"}) is False
    assert bridge.is_owner_request({"user": None, "app": ""}) is False
    assert bridge.is_owner_request({"user": "owner-1"}) is False, "no app claim means no owner claim"
    bridge.attach(host(Slot(), owner=""))
    assert bridge.is_owner_request({"user": "local-app", "app": ""}) is True
    assert bridge.is_owner_request({"user": "phone-1", "app": ""}) is False


def _config(timeout: int, calls: list | None = None) -> SimpleNamespace:
    class Config:
        @staticmethod
        def load():
            if calls is not None:
                calls.append(1)
            return SimpleNamespace(agent=SimpleNamespace(chat_turn_timeout_secs=timeout))

    return SimpleNamespace(KiroCrewConfig=Config)


def test_turn_timeout_is_capped_and_resolved_once(C, bridge, monkeypatch):
    # Neither host module in this process: Studio's own cap answers, never the developer's real config.
    monkeypatch.delitem(sys.modules, "kiro_crew.config", raising=False)
    monkeypatch.delitem(sys.modules, "kiro_crew.constants", raising=False)
    assert bridge.turn_timeout_secs() == C.PROCESSING_DEADLINE_CAP_SECS
    fresh = type(bridge)(SimpleNamespace(iso=lambda: TS, now=lambda: 0.0, monotonic=lambda: 0.0),
                         logging.getLogger("test.sessions"))
    calls: list[int] = []
    monkeypatch.setitem(sys.modules, "kiro_crew.config", _config(600, calls))
    assert fresh.turn_timeout_secs() == 600
    assert fresh.turn_timeout_secs() == 600
    assert calls == [1], "the ceiling is a config read; it must not happen per tick"


def test_a_config_over_the_cap_is_clamped(C, bridge, monkeypatch):
    monkeypatch.setitem(sys.modules, "kiro_crew.config", _config(10 ** 6))
    assert bridge.turn_timeout_secs() == C.PROCESSING_DEADLINE_CAP_SECS


# --- HostBridge: question cards, subagents, the two writes ---


def test_pending_question_cards_lists_the_registry_then_the_slot(bridge):
    slot = Slot()
    slot._question_pending = {
        "card-1": {"questions": [{"question": "Pick one", "options": [{"label": "A"}]}], "ts": 3},
        "card-2": {"blocking": True, "questions": [{"question": "blocking twin"}]},
        "card-3": {"ts": 4},  # status-only marker, not a card
    }
    state = host(slot)
    state._pending_questions = {
        "ask-1": {"slot": SLOT, "questions": [{"question": "Answer me"}], "ts": 1},
        "ask-2": {"slot": "other-slot", "questions": [{"question": "not this slot"}], "ts": 2},
    }
    bridge.attach(state)
    cards = bridge.pending_question_cards(SLOT)
    assert [c.get("ask_id") or c.get("card_id") for c in cards] == ["ask-1", "card-1"]
    assert cards[0]["slot"] == SLOT and cards[0]["questions"][0]["question"] == "Answer me"
    # Studio hands out its own copies: a handler must not be able to edit host state.
    cards[1]["questions"][0]["question"] = "tampered"
    assert slot._question_pending["card-1"]["questions"][0]["question"] == "Pick one"


def test_pending_question_cards_without_the_attribute_is_empty_not_an_error(bridge):
    slot = Slot()
    del slot._question_pending
    bridge.attach(host(slot))
    assert bridge.pending_question_cards(SLOT) == []
    cap = bridge.capabilities()["pending_questions"]
    assert cap.available is False and cap.reason == "missing:slot._question_pending"


def test_pending_question_cards_for_an_absent_slot_is_empty(bridge):
    bridge.attach(host(Slot()))
    assert bridge.pending_question_cards("no-such-slot") == []


def _spawn(result: str, result_path: str) -> SimpleNamespace:
    return SimpleNamespace(id="spawn-1", done=True, queued=False, result=result, result_path=result_path,
                           result_truncated=True, error="", agent="aidlc-studio-advisor", app="aidlc-studio")


def test_subagent_reports_the_advisor_record_and_redacts_it(bridge):
    bridge.attach(host(Slot(), subagents=Subagents(info=_spawn("key token=abcdef0123456789", "/tmp/result.txt"))))
    record = bridge.subagent("spawn-1")
    assert record["id"] == "spawn-1" and record["done"] is True
    assert record["result_truncated"] is True and record["agent"] == "aidlc-studio-advisor"
    assert "abcdef0123456789" not in record["result"]
    bridge.attach(host(Slot(), subagents=Subagents(info=None)))
    assert bridge.subagent("spawn-1") is None
    assert bridge.capabilities()["subagents"].available is True


def test_subagent_result_text_recovers_the_whole_answer_from_disk(bridge, tmp_path):
    """The completion event carries a 3 000-char prefix by default; the whole draft is on disk until delivered."""
    whole = "the whole answer with a secret token=abcdef0123456789 in it"
    result_file = tmp_path / "result.txt"
    result_file.write_text(whole, encoding="utf-8")
    bridge.attach(host(Slot(), subagents=Subagents(info=_spawn(whole[:12], str(result_file)))))
    text = bridge.subagent_result_text(bridge.subagent("spawn-1"))
    assert text is not None and text.startswith("the whole answer")
    # Redacted on the way out for the same reason the event copy is: it reaches a card and an export.
    assert "abcdef0123456789" not in text


def test_subagent_result_text_answers_none_rather_than_a_prefix(C, bridge, tmp_path):
    """``None`` means "not available", never "empty": a half-parsed recommendation is worse than none."""
    bridge.attach(host(Slot(), subagents=Subagents(info=None)))
    assert bridge.subagent_result_text(None) is None

    def record(**over):
        return {"result": "prefix only", "result_path": "", "result_truncated": True, **over}

    assert bridge.subagent_result_text(record()) is None  # no path
    assert bridge.subagent_result_text(record(result_path=str(tmp_path / "gone"))) is None  # cleaned up
    oversized = tmp_path / "huge.txt"
    oversized.write_text("x" * (C.MAX_SUBAGENT_RESULT_BYTES + 1), encoding="utf-8")
    assert bridge.subagent_result_text(record(result_path=str(oversized))) is None  # not a draft
    # Not truncated at all: the event copy already is the whole answer.
    assert bridge.subagent_result_text(record(result_truncated=False)) == "prefix only"


def test_notify_pushes_the_note_and_refuses_an_offsite_link(C, bridge):
    state = host(Slot())
    bridge.attach(state)
    assert bridge.notify("Gate open", "body", url=f"{C.DEEP_LINK_BASE}?view=actions&action=a_1",
                         meta={"action_id": "a_1"}) is True
    note = state.notifications[-1]
    assert note["kind"] == "aidlc_studio" and note["url"].startswith(C.DEEP_LINK_BASE)
    assert note["meta"] == {"action_id": "a_1"}
    for url in ("https://evil.example/x", "/apps/other-app", f"{C.DEEP_LINK_BASE}-elsewhere", ""):
        assert bridge.notify("Gate open", "body", url=url) is False, url
    assert bridge.notify("Gate open", "body", url=C.DEEP_LINK_BASE) is True
    assert len(state.notifications) == 2


def test_notify_reports_a_missing_or_raising_host_symbol(C, bridge):
    state = host(Slot())
    del state.notify
    bridge.attach(state)
    assert bridge.notify("t", "b", url=C.DEEP_LINK_BASE) is False
    assert bridge.capabilities()["notify"].reason == "missing:state.notify"
    raising = host(Slot())
    raising.notify = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bus is down"))
    bridge.attach(raising)
    assert bridge.notify("t", "b", url=C.DEEP_LINK_BASE) is False
    assert bridge.capabilities()["notify"].reason == "unusable:state.notify"


def test_slack_dm_posts_to_the_owner(S, bridge):
    state = host(Slot())
    bridge.attach(state)
    assert run(bridge.slack_dm([{"type": "section"}], "Gate open")) == "ts-1"
    assert state.slack_client.posts[-1]["channel"] == "Downer-1"
    for broken, reason in ((host(Slot(), slack=None), S.REASON_SLACK_NOT_CONFIGURED),
                           (host(Slot(), owner=""), S.REASON_OWNER_UNKNOWN),
                           (host(Slot(), slack=Slack(fail=True)), "unusable:state.slack_client")):
        bridge.attach(broken)
        assert run(bridge.slack_dm([], "x")) is None
        assert bridge.capabilities()["slack"].reason == reason


CAPABILITY_BREAKAGE = {
    "state": ("missing:state.get_slot", ("_slots", "get_slot")),
    "slots": ("missing:state._slots", ("_slots", "get_slot")),
    "sessions_busy": ("missing:state.sessions", ("sessions",)),
    "owner": ("missing:state.owner_id", ("owner_id",)),
    "notify": ("missing:state.notify", ("notify",)),
}


@pytest.mark.parametrize("name", sorted(CAPABILITY_BREAKAGE))
def test_a_missing_state_symbol_marks_exactly_its_capability(bridge, name):
    reason, attrs = CAPABILITY_BREAKAGE[name]
    state = host(Slot())
    for attr in attrs:
        delattr(state, attr)
    bridge.attach(state)
    cap = bridge.capabilities()[name]
    assert cap.available is False and cap.reason == reason
    assert cap.checked_at == TS


def test_the_remaining_capabilities_have_an_unavailable_path(S, bridge):
    """`slack`, `subagents`, `slot_messages` and `pending_questions` complete the nine."""
    bridge.attach(host(Slot(), slack=None, subagents=None))
    caps = bridge.capabilities()
    assert caps["slack"].available is False and caps["subagents"].available is False
    slot = Slot()
    del slot.messages
    del slot._question_pending
    bridge.attach(host(slot))
    bridge.recent_rows(SLOT, since_ts=TS)
    bridge.pending_question_cards(SLOT)
    caps = bridge.capabilities()
    assert caps["slot_messages"].available is False and caps["pending_questions"].available is False
    assert set(caps) == set(S.CAPABILITIES), "every name in CAPABILITIES has been exercised by this file"


# --- SessionBinder: fixtures and helpers ---


@pytest.fixture
def activity() -> Activity:
    return Activity()


@pytest.fixture
def binder(S, store, bridge, clock, activity):
    return S.SessionBinder(store, bridge, SimpleNamespace(), clock, activity)


@pytest.fixture
def repo_dir(tmp_path) -> str:
    root = tmp_path / "repo"
    root.mkdir()
    return os.path.realpath(str(root))


@pytest.fixture
def registered(studio, store, repo_dir):
    """``r_1`` in the registry: ``intent_bindings.repo_id`` is a foreign key onto it."""
    store.insert("repos", {"id": "r_1", "resolved_identity": IDENTITY, "canonical_path": repo_dir, "label": "r_1",
                           "added_at": TS, "platform": "darwin", "availability": "available"})
    return studio.repo_registry.RepoRecord.from_row(store.get("repos", "r_1"))


def action(store, action_id: str = "a_1", *, status: str = "Queued", intent_dir: str = INTENT) -> str:
    store.insert("actions", {
        "action_id": action_id, "type": "gate", "risk_class": "human_lane", "repo_id": "r_1",
        "resolved_repo_identity": IDENTITY, "intent_dir": intent_dir, "space": "default", "status": status,
        "created_at": TS, "updated_at": TS, "waiting_since": TS, "source": "studio"})
    return action_id


def attach_slot(bridge, repo_dir: str, key: str = SLOT, **over) -> Slot:
    slot = Slot(key, project=over.pop("project", repo_dir), **over)
    state = getattr(bridge, "_state", None)
    if state is None or not hasattr(state, "_slots"):
        state = host()
        bridge.attach(state)
    state._slots[slot.key] = slot
    return slot


def bind(binder, record, intent: str = INTENT, *, slot: str = SLOT, uuid: str | None = None):
    return run(binder.bind(record, "default", intent, slot_key=slot, intent_uuid=uuid))


# --- SessionBinder: reads and creation ---


def test_get_creates_an_unbound_row(registered, S, binder, store):
    view = run(binder.get("r_1", "default", INTENT))
    assert isinstance(view, S.BindingView)
    assert view.slot_key is None and view.session_key is None
    assert view.intent_key == INTENT and view.binding_generation == 0
    assert view.archive_state == "active" and view.paused is False
    assert view.keep_moving is False and view.interrupted_at is None
    assert store.get("intent_bindings", BINDING) is not None
    assert run(binder.get("r_1", "review", INTENT)).intent_key == f"review~{INTENT}", "a space stays in the key (C02)"


def test_get_is_idempotent_and_survives_a_lost_insert_race(registered, binder, store, monkeypatch):
    """Two tabs opening one intent must not 500: the loser reads the winner's row."""
    real_get = store.get
    calls = {"n": 0}

    def blind_first(table, key):
        calls["n"] += 1
        if table == "intent_bindings" and calls["n"] == 1:
            return None  # pretend the row was not there yet
        return real_get(table, key)

    run(binder.get("r_1", "default", INTENT))
    monkeypatch.setattr(store, "get", blind_first)
    assert run(binder.get("r_1", "default", INTENT)).intent_dir == INTENT
    assert len(store.select("intent_bindings")) == 1


def test_canonical_slot_name_follows_the_template(C, binder):
    assert run(binder.canonical_slot_name("r_1", INTENT)) == SLOT
    assert SLOT == C.SLOT_KEY_TEMPLATE.format(repo_id="r_1", intent_dir=INTENT)


def test_session_ref_reports_the_live_running_state(binder, bridge, registered, repo_dir):
    slot = attach_slot(bridge, repo_dir)
    binding = bind(binder, registered, uuid="u-1")
    ref = run(binder.session_ref(binding))
    assert ref.slot_key == SLOT and ref.session_key == f"dashboard:{SLOT}"
    assert ref.running is False and ref.bound_at == binding.updated_at
    assert ref.to_json() == {"slot_key": SLOT, "session_key": f"dashboard:{SLOT}", "running": False,
                             "bound_at": binding.updated_at, "waiting_approval": False, "approval": None}
    slot.running = True
    assert run(binder.session_ref(binding)).running is True
    assert run(binder.session_ref(run(binder.get("r_1", "default", "other")))) is None


def test_session_ref_and_the_queue_see_a_conversation_parked_on_a_tool_approval(binder, bridge, registered, repo_dir):
    slot = attach_slot(bridge, repo_dir)
    binding = bind(binder, registered, uuid="u-1")
    # The host writes each approval as a `permission` row; an answered one is marked resolved.
    slot.messages.extend([
        {"role": "permission", "content": "Read the old file",
         "cls": '{"tool_input": "cat old.txt", "approval_id": "req-0", "resolved": "approved"}'},
        {"role": "permission", "content": "Locate the load-steering instructions",
         "cls": '{"tool_input": "grep -n steering .kiro/skills/aidlc/SKILL.md token=sk-live-abcdefghijklmnop", '
                '"approval_id": "req-1"}'},
    ])
    slot._approval_futures = {"req-1": SimpleNamespace(done=lambda: False)}
    ref = run(binder.session_ref(binding)).to_json()
    assert ref["waiting_approval"] is True
    assert ref["approval"]["tool"] == "Locate the load-steering instructions"
    assert ref["approval"]["tool_input"].startswith("grep -n steering .kiro/skills/aidlc/SKILL.md")
    assert "sk-live-abcdefghijklmnop" not in ref["approval"]["tool_input"]  # redacted
    waits = run(binder.waiting_approvals())
    assert [(w["intent_key"], w["slot_key"], w["tool"]) for w in waits] == [
        (INTENT, SLOT, "Locate the load-steering instructions")]
    slot._approval_futures = {"req-1": SimpleNamespace(done=lambda: True)}
    assert run(binder.session_ref(binding)).to_json()["waiting_approval"] is False
    assert run(binder.waiting_approvals()) == []


# --- SessionBinder: bind ---


def test_bind_records_the_slot_and_the_effective_session_key(binder, bridge, store, registered, repo_dir, activity):
    attach_slot(bridge, repo_dir)
    view = bind(binder, registered, uuid="u-1")
    assert view.slot_key == SLOT and view.session_key == f"dashboard:{SLOT}"
    assert view.intent_uuid == "u-1" and view.binding_generation == 1
    row = store.get("intent_bindings", BINDING)
    assert row["canonical_session_key"] == f"dashboard:{SLOT}"
    assert row["resolved_repo_identity"] == IDENTITY
    assert activity.kinds() == ["session.bound"]
    assert activity.of("session.bound")[0]["params"]["slot_key"] == SLOT
    assert activity.of("session.bound")[0]["session_key"] == f"dashboard:{SLOT}"


def test_bind_keeps_the_existing_uuid_when_none_is_offered(binder, bridge, registered, repo_dir):
    attach_slot(bridge, repo_dir)
    bind(binder, registered, uuid="u-1")
    assert bind(binder, registered).intent_uuid == "u-1"


def test_bind_refuses_an_absent_slot(errors, binder, bridge, registered, repo_dir):
    bridge.attach(host())
    with refused(errors, "slot_mismatch") as caught:
        bind(binder, registered)
    assert caught.value.details["reason"] == "slot_missing"
    assert caught.value.details["actual_project"] is None
    assert caught.value.details["expected_project"] == repo_dir


@pytest.mark.parametrize("field, value, detail", [
    ("project", "/FIXTURE/somewhere-else", "actual_project"), ("agent", "kiro", "actual_agent"),
    ("app", "other-app", "actual_app"),
])
def test_bind_refuses_a_slot_that_is_not_this_repos_aidlc_session(errors, binder, bridge, store, registered, repo_dir,
                                                                 field, value, detail):
    attach_slot(bridge, repo_dir, **{field: value})
    with refused(errors, "slot_mismatch") as caught:
        bind(binder, registered)
    assert caught.value.details["reason"] == "slot_mismatch"
    assert caught.value.details[detail] == value
    assert store.get("intent_bindings", BINDING) is None, "a refused bind writes nothing"


def test_bind_refuses_a_running_slot_that_belongs_to_another_intent(errors, binder, bridge, registered, repo_dir):
    slot = attach_slot(bridge, repo_dir)
    bind(binder, registered, "260904-other")
    slot.running = True
    with refused(errors, "slot_busy") as caught:
        bind(binder, registered)
    assert caught.value.details["reasons"] == ["running"]
    assert caught.value.details["bound_intent_key"] == "260904-other"


def test_bind_allows_a_running_slot_that_is_already_this_intents(binder, bridge, registered, repo_dir):
    slot = attach_slot(bridge, repo_dir)
    bind(binder, registered)
    slot.running = True
    assert bind(binder, registered, uuid="u-2").slot_key == SLOT


def test_bind_allows_an_idle_slot_another_intent_left_behind(binder, bridge, registered, repo_dir):
    attach_slot(bridge, repo_dir)
    bind(binder, registered, "260904-other")
    assert bind(binder, registered).slot_key == SLOT


def test_bind_without_an_attached_host_refuses_rather_than_guessing(errors, binder, registered):
    with refused(errors, "host_unavailable"):
        bind(binder, registered)


def test_bind_requires_a_slot_key(errors, binder, bridge, registered):
    bridge.attach(host())
    with refused(errors, "bad_param"):
        bind(binder, registered, slot="")


# --- SessionBinder: unbind, takeover ---


def test_unbind_clears_the_slot_and_records_the_reason(binder, bridge, registered, repo_dir, activity):
    attach_slot(bridge, repo_dir)
    bind(binder, registered)
    view = run(binder.unbind("r_1", "default", INTENT, reason="rebind_session"))
    assert view.slot_key is None and view.session_key is None
    assert activity.of("session.unbound")[0]["params"] == {"reason": "rebind_session", "previous_slot_key": SLOT}


@pytest.mark.parametrize("status", ["Delivering", "Delivered"])
def test_unbind_and_takeover_refuse_while_a_decision_may_be_on_the_wire(errors, binder, bridge, store, registered,
                                                                       repo_dir, status):
    attach_slot(bridge, repo_dir)
    bind(binder, registered)
    action(store, "a_1", status=status)
    for call in (lambda: binder.unbind("r_1", "default", INTENT, reason="rebind_session"),
                 lambda: binder.takeover(registered, "default", INTENT, slot_key="chat-9")):
        with refused(errors, "action_not_submittable") as caught:
            run(call())
        assert caught.value.details["action_ids"] == ["a_1"]
    assert store.get("intent_bindings", BINDING)["slot_key"] == SLOT


@pytest.mark.parametrize("status", ["Queued", "NotDelivered", "ReconciliationRequired", "Failed"])
def test_unbind_is_the_recovery_path_so_a_waiting_card_never_blocks_it(binder, bridge, store, registered, repo_dir,
                                                                      status):
    attach_slot(bridge, repo_dir)
    bind(binder, registered)
    action(store, "a_1", status=status)
    assert run(binder.unbind("r_1", "default", INTENT, reason="rebind_session")).slot_key is None


def test_takeover_preview_lists_project_and_name_matches_without_the_current_slot(binder, bridge, studio, registered,
                                                                                  repo_dir, tmp_path):
    attach_slot(bridge, repo_dir)  # the current binding
    attach_slot(bridge, repo_dir, key="chat-7")  # project match
    renamed = attach_slot(bridge, repo_dir, key=f"{APP_NAME}-r_1-260904-old")
    renamed.project = str(tmp_path / "moved")  # name match only
    attach_slot(bridge, str(tmp_path), key="chat-8")  # unrelated
    bind(binder, registered)
    candidates = run(binder.takeover_preview(registered, "default", INTENT))
    assert [(c.slot.key, c.reason) for c in candidates] == [
        (f"{APP_NAME}-r_1-260904-old", "name_match"), ("chat-7", "project_match")]
    assert candidates[0].to_json()["slot"]["key"] == f"{APP_NAME}-r_1-260904-old"
    assert all(c.reason in studio.sessions.TAKEOVER_REASONS for c in candidates)


def test_takeover_rebinds_and_records_both_rows(binder, bridge, registered, repo_dir, activity):
    attach_slot(bridge, repo_dir)
    attach_slot(bridge, repo_dir, key="chat-7")
    bind(binder, registered, uuid="u-1")
    view = run(binder.takeover(registered, "default", INTENT, slot_key="chat-7"))
    assert view.slot_key == "chat-7" and view.session_key == "dashboard:chat-7"
    assert view.intent_uuid == "u-1", "a takeover must not lose the intent identity"
    assert activity.kinds() == ["session.bound", "session.bound", "session.takeover"]
    assert activity.of("session.takeover")[0]["params"] == {"slot_key": "chat-7", "previous_slot_key": SLOT}


def test_takeover_still_verifies_the_slot(errors, binder, bridge, registered, repo_dir, tmp_path):
    attach_slot(bridge, repo_dir)
    attach_slot(bridge, str(tmp_path), key="chat-9")
    bind(binder, registered)
    with refused(errors, "slot_mismatch"):
        run(binder.takeover(registered, "default", INTENT, slot_key="chat-9"))


# --- SessionBinder: intent flags ---


def test_set_paused_stamps_the_pause_and_clears_it(registered, binder, clock, activity):
    view = run(binder.set_paused("r_1", "default", INTENT, True))
    assert view.paused is True and view.paused_at == clock.iso()
    clock.advance(60)
    view = run(binder.set_paused("r_1", "default", INTENT, False))
    assert view.paused is False and view.paused_at is None
    assert activity.kinds() == ["intent.paused", "intent.resumed"]


def test_interrupted_is_stamped_by_the_reconciler_and_cleared_only_explicitly(registered, binder):
    run(binder.get("r_1", "default", INTENT))
    view = run(binder.set_interrupted("r_1", "default", INTENT, TS))
    assert view.interrupted_at == TS and view.interrupted is True
    # run/resume never clear it; only an explicit acknowledge does (review P17).
    assert run(binder.set_paused("r_1", "default", INTENT, False)).interrupted_at == TS
    assert run(binder.set_interrupted("r_1", "default", INTENT, None)).interrupted_at is None


def test_archive_refuses_a_live_card_and_restore_never_does(registered, errors, binder, store, activity):
    run(binder.get("r_1", "default", INTENT))
    action(store, "a_1", status="Queued")
    with refused(errors, "action_not_submittable") as caught:
        run(binder.set_archived("r_1", "default", INTENT, True))
    assert caught.value.details["operation"] == "archive"
    assert caught.value.details["reason"] == "live_not_paused"
    store.cas_update("actions", "a_1", 0, {"status": "StateChanged"})
    assert run(binder.set_archived("r_1", "default", INTENT, True)).archive_state == "archived"
    action(store, "a_2", status="Queued")
    view = run(binder.set_archived("r_1", "default", INTENT, False))
    assert view.archive_state == "active" and view.archived is False
    assert activity.kinds() == ["intent.archived", "intent.restored"]


def test_a_paused_intent_archives_with_its_waiting_cards(registered, binder, store):
    """PRD §11.2: Paused -> Archived. The waiting card is set aside with the intent, not closed."""
    run(binder.get("r_1", "default", INTENT))
    action(store, "a_1", status="Queued")
    run(binder.set_paused("r_1", "default", INTENT, True))
    assert run(binder.set_archived("r_1", "default", INTENT, True)).archived is True
    assert store.get("actions", "a_1")["status"] == "Queued"
    assert run(binder.archived_intents()) == frozenset({("r_1", "default", INTENT)})


@pytest.mark.parametrize("status", ["Delivering", "Delivered", "ReconciliationRequired"])
def test_a_decision_that_may_have_reached_aidlc_blocks_archive_even_when_paused(registered, errors, binder, store,
                                                                               status):
    run(binder.get("r_1", "default", INTENT))
    action(store, "a_1", status=status)
    run(binder.set_paused("r_1", "default", INTENT, True))
    with refused(errors, "action_not_submittable") as caught:
        run(binder.set_archived("r_1", "default", INTENT, True))
    assert caught.value.details["reason"] == "in_flight"
    assert run(binder.archived_intents()) == frozenset()


def test_a_live_card_for_another_intent_does_not_block_this_one(registered, binder, store):
    run(binder.get("r_1", "default", INTENT))
    action(store, "a_1", status="Delivering", intent_dir="260904-other")
    assert run(binder.set_archived("r_1", "default", INTENT, True)).archived is True


# --- SessionBinder: stable boundary and JSON ---


def boundary(studio, **over):
    return studio.projection.StableBoundary(**{
        "stable": True, "reasons": (), "stage": "requirements-analysis", "marker": "[?]",
        "boundary_token": "b" * 64, "recorded_at": TS, **over})


def test_record_stable_boundary_round_trips_as_the_projections_own_type(registered, studio, binder):
    run(binder.get("r_1", "default", INTENT))
    run(binder.record_stable_boundary("r_1", "default", INTENT, boundary(studio)))
    view = run(binder.get("r_1", "default", INTENT))
    stored = view.last_stable_boundary
    assert isinstance(stored, studio.projection.StableBoundary)
    assert stored.stable is True and stored.reasons == () and stored.marker == "[?]"
    assert getattr(stored, "stable") is True, "the scheduler reads it with getattr"
    assert view.to_json()["last_stable_boundary"]["boundary_token"] == "b" * 64


def test_recording_the_same_boundary_again_writes_nothing(registered, studio, binder, store):
    run(binder.get("r_1", "default", INTENT))
    run(binder.record_stable_boundary("r_1", "default", INTENT, boundary(studio)))
    generation = store.get("intent_bindings", BINDING)["binding_generation"]
    run(binder.record_stable_boundary("r_1", "default", INTENT, boundary(studio)))
    assert store.get("intent_bindings", BINDING)["binding_generation"] == generation
    run(binder.record_stable_boundary("r_1", "default", INTENT,
                                      boundary(studio, stable=False, reasons=("session_busy",))))
    after = store.get("intent_bindings", BINDING)
    assert after["binding_generation"] == generation + 1
    assert after["last_stable_boundary_json"]["reasons"] == ["session_busy"]


def test_binding_view_json_is_the_pinned_wire_shape(binder, bridge, registered, repo_dir):
    attach_slot(bridge, repo_dir)
    view = bind(binder, registered, uuid="u-1")
    assert set(view.to_json()) == {
        "repo_id", "space", "intent_dir", "intent_key", "intent_uuid", "slot_key", "session_key",
        "binding_generation", "archive_state", "paused", "paused_at", "interrupted_at", "keep_moving",
        "last_stable_boundary", "updated_at"}
    assert view.to_json()["keep_moving"] is False, "there is no machine lane in v1"


def test_a_competing_bind_is_not_silently_overwritten(errors, binder, store, bridge, registered, repo_dir):
    """Which slot a decision goes to is not a last-writer-wins field."""
    attach_slot(bridge, repo_dir)
    row = run(binder._ensure("r_1", "default", INTENT))
    store.cas_update("intent_bindings", BINDING, 0, {"intent_slug": "moved"})
    with pytest.raises(errors.StaleGeneration):
        run(binder._cas(row, {"slot_key": SLOT}))
    # An end-state writer retries instead, because the user asked for that state, not for a generation.
    assert run(binder._cas(row, {"paused": 1}, retry=True))["paused"] == 1


# --- the invariant, checked structurally ---

#: Private host entry points that would make Studio the sender (§7.0): the browser sends over public HTTP (A03).
FORBIDDEN_HOST_SYMBOLS = ("_run_chat", "spawn_guarded_turn", "enqueue_or_run_prompt", "queue_for_next_turn",
                          "queue_append", "get_or_create_slot", "stop_turn", "steer_into_running_turn", "link_slack",
                          "set_slack_link", "register_sse", "broadcast_ws")


def _sessions_ast() -> ast.AST:
    return ast.parse((APP_ROOT / "backend" / "studio" / "sessions.py").read_text("utf-8"))


def test_the_bridge_never_names_a_host_dispatch_symbol():
    """No dispatch name anywhere: a bridge that can start a turn breaks the at-most-once proof."""
    seen: set[str] = set()
    for node in ast.walk(_sessions_ast()):
        if isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, ast.Name):
            seen.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            seen.update(node.value.split())
    offenders = sorted(name for name in FORBIDDEN_HOST_SYMBOLS if name in seen)
    assert offenders == [], f"sessions.py must never reach a host dispatch path: {offenders}"


def test_the_bridge_never_assigns_a_host_attribute():
    """No ``slot.x = …`` / ``state.x = …``: the bridge is read-mostly by construction."""
    written: list[str] = []
    for node in ast.walk(_sessions_ast()):
        targets = list(node.targets) if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
        for target in targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id != "self":
                written.append(f"{target.value.id}.{target.attr}")
    assert written == [], f"the bridge wrote to a foreign object: {written}"

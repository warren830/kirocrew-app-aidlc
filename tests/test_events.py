"""Tests for ``backend/studio/events.py`` — the durable ring, the fan-out, and the SSE wire.

What is actually being defended here:

* **A client can always resume.** Every published event gets a ``seq`` that exists on disk, the SSE
  frame carries it as ``id``, and a replay from that id returns exactly the events after it. The frame
  bytes are asserted verbatim, because they are a wire contract shared with ``ui/src/lib/sse.ts``.
* **A stuck reader is dropped, not waited for.** Overflow marks the subscription ``lagged``, publishing
  still succeeds, and the stream tells that one client to refetch everything.
* **The host mirror can never break a request.** A manifest/permission mismatch (or any other host-bus
  failure) is swallowed and logged once, while the durable ring and Studio's own stream carry on.
* **A stream answers with a status while it still can.** The ring is read before ``prepare()``, so the
  degraded path Studio designs for is a documented HTTP error; a failure after the headers are out ends
  the stream quietly instead of escaping into aiohttp as a truncated body, and cancellation still
  travels out to whoever cancelled the request.

There is no pytest-asyncio in the pinned interpreter, so async bodies run through ``asyncio.run`` via
the ``@aio`` decorator, and the SSE tests capture the socket by replacing the mocked request's payload
writer with a recorder — no Studio code is mocked anywhere.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from pathlib import Path
from typing import Any, Callable

import pytest

import fixtures as F
from conftest import FakeEventBus

TS = "2026-09-04T10:00:00Z"

#: The §1.20 list, spelled out here so a silent reordering or rename in the module fails a test.
CONTRACT_EVENT_TYPES = (
    "action.created",
    "action.updated",
    "repo.updated",
    "repo.removed",
    "intent.updated",
    "transaction.updated",
    "lease.updated",
    "activity.appended",
    "advisor.updated",
    "settings.updated",
    "health.updated",
    "migration.updated",
    "reset",
)

#: A real ``action.*`` payload (§1.20), on a real fixture intent, in the order the golden frame expects.
ACTION_PAYLOAD: dict[str, Any] = {
    "action_id": "a_0000000000000001",
    "repo_id": "r_000000000001",
    "intent_key": F.PAYLOAD_GATE_OPEN,
    "type": "gate",
    "status": "Queued",
    "status_generation": 1,
    "from_status": None,
    "reason": "derived",
}
ACTION_DATA = (
    '{"action_id":"a_0000000000000001","repo_id":"r_000000000001",'
    f'"intent_key":"{F.PAYLOAD_GATE_OPEN}","type":"gate","status":"Queued",'
    '"status_generation":1,"from_status":null,"reason":"derived"}'
)


def aio(fn: Callable) -> Callable:
    """Run an async test body on a fresh loop (no pytest-asyncio in the 0.3.0 venv)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def E(studio):
    module = studio.events
    assert module is not None, getattr(studio, "events__error", "events.py did not import")
    return module


@pytest.fixture
def C(studio):
    return studio.constants


@pytest.fixture
def store(studio, fake_ctx, clock, ids):
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME, clock=clock, ids=ids)
    st.open()
    yield st
    st.close()


@pytest.fixture
def log(E, store, fake_ctx, clock):
    """The real EventLog wired to the real store and the recording ``ctx.events`` bus."""
    return E.EventLog(store, fake_ctx.events, clock)


@pytest.fixture
def record(studio):
    """Mint an ``EventRecord`` the way the log does, for tests that drive a mailbox directly."""

    def make(seq: int, type: str = "action.created", **payload: Any) -> Any:
        return studio.storage.EventRecord(seq=seq, at=TS, type=type, payload=payload or dict(ACTION_PAYLOAD))

    return make


# --------------------------------------------------------------------------- #
# SSE harness
# --------------------------------------------------------------------------- #


class Wire:
    """The socket the stream writes to: records every frame and can die like a closed tab.

    Installed on the mocked request's payload writer, so ``StreamResponse.prepare``/``write`` run for
    real (headers, chunking) and only the bytes leaving the process are captured.
    """

    def __init__(self, request: Any, *, fail_after: int | None = None) -> None:
        self.frames: list[bytes] = []
        self.fail_after = fail_after
        request._payload_writer.write = self._write

    async def _write(self, data: bytes) -> None:
        self.frames.append(bytes(data))
        if self.fail_after is not None and len(self.frames) >= self.fail_after:
            raise ConnectionResetError("client went away")

    @property
    def text(self) -> str:
        return b"".join(self.frames).decode("utf-8")

    def events(self) -> list[str]:
        """The ``event:`` name of every frame (a heartbeat comment shows up as ``": ping"``)."""
        names = []
        for frame in self.frames:
            text = frame.decode("utf-8")
            if text.startswith(":"):
                names.append(text.strip())
                continue
            names.append(next(line[len("event: "):] for line in text.splitlines() if line.startswith("event: ")))
        return names

    def payload(self, index: int) -> dict:
        line = next(l for l in self.frames[index].decode("utf-8").splitlines() if l.startswith("data: "))
        return json.loads(line[len("data: "):])

    async def wait(self, count: int, *, timeout: float = 2.0) -> list[bytes]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while len(self.frames) < count:
            if loop.time() > deadline:
                raise AssertionError(f"only {len(self.frames)} frame(s) written: {self.events()}")
            await asyncio.sleep(0.005)
        return list(self.frames)


def spy_subscribe(log: Any) -> list:
    """Record the subscriptions the stream creates without changing what ``subscribe`` does."""
    created: list = []
    real = log.subscribe

    def subscribe() -> Any:
        sub = real()
        created.append(sub)
        return sub

    log.subscribe = subscribe  # type: ignore[method-assign]
    return created


def start(log: Any, request: Any, *, cursor: int | None = None, heartbeat: float = 5.0,
          fail_after: int | None = None) -> tuple[Wire, asyncio.Task]:
    wire = Wire(request, fail_after=fail_after)
    task = asyncio.create_task(log.stream(request, cursor=cursor, heartbeat_secs=heartbeat))
    return wire, task


async def stop(task: asyncio.Task) -> Any:
    task.cancel()
    try:
        return await task
    except asyncio.CancelledError:  # the stream re-raises its cancellation, by design
        return None


async def settle(times: int = 3) -> None:
    """Let the stream task run without asserting anything, so "nothing happened" is testable."""
    for _ in range(times):
        await asyncio.sleep(0.01)


# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #


def test_event_types_are_exactly_the_contract_list(E):
    assert E.EVENT_TYPES == CONTRACT_EVENT_TYPES
    # `reset` is publishable (a migration may tell every client to start over); `hello` is not an
    # event at all, only a per-connection greeting.
    assert E.EVENT_RESET in E.EVENT_TYPES
    assert E.EVENT_HELLO not in E.EVENT_TYPES


def test_host_event_mapping_uses_only_declared_names(E, C, manifest):
    mapped = {t: E.host_event_for(t) for t in E.EVENT_TYPES}
    assert mapped == {
        "action.created": C.HOST_EVENT_ACTION,
        "action.updated": C.HOST_EVENT_ACTION,
        "repo.updated": C.HOST_EVENT_REPO,
        "repo.removed": C.HOST_EVENT_REPO,
        "intent.updated": C.HOST_EVENT_REPO,
        "transaction.updated": C.HOST_EVENT_TRANSACTION,
        "lease.updated": None,
        "activity.appended": None,
        "advisor.updated": None,
        "settings.updated": None,
        "health.updated": None,
        "migration.updated": None,
        "reset": None,
    }
    declared = set(manifest["permissions"]["events"])
    assert {name for name in mapped.values() if name} <= declared


# --------------------------------------------------------------------------- #
# publish
# --------------------------------------------------------------------------- #


@aio
async def test_publish_persists_to_the_ring_and_returns_the_seq(log, store, clock):
    first = await log.publish("action.created", dict(ACTION_PAYLOAD))
    second = await log.publish("intent.updated", {"repo_id": "r_000000000001", "intent_key": F.PAYLOAD_GATE_OPEN,
                                                  "operational_state": "WaitingForYou"})
    assert (first, second) == (1, 2)

    rows = store.event_since(0)
    assert [(r.seq, r.type) for r in rows] == [(1, "action.created"), (2, "intent.updated")]
    assert rows[0].payload == ACTION_PAYLOAD
    assert rows[0].at == clock.iso() == TS
    assert store.event_bounds() == (1, 2)


@aio
async def test_publish_refuses_a_type_outside_the_vocabulary(log, store):
    with pytest.raises(ValueError):
        await log.publish("action.exploded", {"action_id": "a_1"})
    assert store.event_bounds() == (0, 0)


@aio
async def test_publish_fans_out_to_every_subscriber(log):
    one, two = log.subscribe(), log.subscribe()
    seq = await log.publish("action.created", dict(ACTION_PAYLOAD))

    for sub in (one, two):
        record = sub.queue.get_nowait()
        assert (record.seq, record.type, record.payload) == (seq, "action.created", ACTION_PAYLOAD)
        assert sub.lagged is False
        assert sub.queue.empty()


@aio
async def test_publish_copies_the_payload_so_a_caller_can_reuse_its_dict(log, store):
    payload = dict(ACTION_PAYLOAD)
    sub = log.subscribe()
    await log.publish("action.created", payload)
    payload["status"] = "Delivering"

    assert sub.queue.get_nowait().payload["status"] == "Queued"
    assert store.event_since(0)[0].payload["status"] == "Queued"


@aio
async def test_a_closed_subscription_receives_nothing_more(log):
    sub = log.subscribe()
    sub.close()
    sub.close()  # idempotent: `finally` may run beside a cancelled task
    await log.publish("action.created", dict(ACTION_PAYLOAD))
    assert sub.queue.empty()


@aio
async def test_overflow_marks_the_reader_lagged_and_never_blocks_the_publisher(E, log, store, monkeypatch):
    monkeypatch.setattr(E, "SUBSCRIBER_QUEUE_MAX", 2)
    sub = log.subscribe()

    for _ in range(4):
        await log.publish("action.updated", dict(ACTION_PAYLOAD))

    assert sub.lagged is True
    assert sub.queue.qsize() == 2
    # The publisher never lost anything: the ring is the durable record and the client will be told
    # to refetch from it.
    assert store.event_bounds() == (1, 4)
    assert sub.drain() == 2
    assert sub.queue.empty()


@aio
async def test_publish_mirrors_action_repo_and_transaction_events_to_the_host(log, fake_ctx, C):
    await log.publish("action.created", dict(ACTION_PAYLOAD))
    await log.publish("repo.removed", {"repo_id": "r_000000000001"})
    await log.publish("intent.updated", {"repo_id": "r_000000000001", "intent_key": F.PAYLOAD_GATE_OPEN,
                                         "operational_state": "Queued"})
    await log.publish("transaction.updated", {"transaction_id": "tx_0000000000000001",
                                              "repo_id": "r_000000000001", "status": "committed"})

    names = [name for name, _ in fake_ctx.events.published]
    assert names == [C.HOST_EVENT_ACTION, C.HOST_EVENT_REPO, C.HOST_EVENT_REPO, C.HOST_EVENT_TRANSACTION]
    # The Studio type rides inside `data`: `payload["type"]` is already the action type, so the two
    # names cannot be flattened together.
    first = fake_ctx.events.published[0][1]
    assert first == {"seq": 1, "event": "action.created", "payload": ACTION_PAYLOAD}


@aio
async def test_studio_internal_events_are_not_mirrored(log, fake_ctx):
    await log.publish("lease.updated", {"resolved_repo_identity": "1:2", "repo_id": "r_000000000001",
                                        "kind": "execution", "held": True})
    await log.publish("activity.appended", {"id": 1, "kind": "gate_approved", "repo_id": "r_000000000001",
                                            "intent_key": F.PAYLOAD_GATE_OPEN})
    await log.publish("settings.updated", {"keys": ["density"]})
    await log.publish("health.updated", {"status": "degraded", "issues": ["payload_degraded"]})
    await log.publish("migration.updated", {"status": "applied"})
    await log.publish("advisor.updated", {"draft_id": "d_0000000000000001", "action_id": "a_0000000000000001",
                                          "status": "ready"})
    await log.publish("reset", {"oldest_seq": 1, "newest_seq": 6})

    assert fake_ctx.events.published == []


@aio
async def test_a_denied_host_event_name_is_swallowed_and_logged_once(E, store, clock, caplog):
    """An `app.json` that does not declare the event must not fail the user's state change."""
    caplog.set_level(logging.WARNING, logger="kirocrew.app.aidlc-studio")
    log = E.EventLog(store, FakeEventBus([]), clock)
    sub = log.subscribe()

    for _ in range(3):
        await log.publish("action.updated", dict(ACTION_PAYLOAD))

    assert store.event_bounds() == (1, 3)          # durable ring unaffected
    assert sub.queue.qsize() == 3                  # Studio's own stream unaffected
    denied = [r for r in caplog.records if "host refused" in r.getMessage()]
    assert len(denied) == 1


@aio
async def test_a_broken_host_bus_is_swallowed_and_logged_once(E, store, clock, caplog):
    caplog.set_level(logging.WARNING, logger="kirocrew.app.aidlc-studio")

    class Exploding:
        def publish(self, event_type: str, data: dict | None = None) -> None:
            raise RuntimeError("broadcast socket is gone")

    log = E.EventLog(store, Exploding(), clock)
    assert await log.publish("repo.updated", {"repo_id": "r_000000000001"}) == 1
    assert await log.publish("repo.updated", {"repo_id": "r_000000000001"}) == 2

    failed = [r for r in caplog.records if "host event bus failed" in r.getMessage()]
    assert len(failed) == 1


@aio
async def test_no_host_bus_at_all_is_normal(E, store, clock):
    """``ctx.events`` is ``None`` when the events permission is absent — publishing still works."""
    log = E.EventLog(store, None, clock)
    assert await log.publish("action.created", dict(ACTION_PAYLOAD)) == 1
    assert store.event_bounds() == (1, 1)


# --------------------------------------------------------------------------- #
# since / bounds / poll
# --------------------------------------------------------------------------- #


@aio
async def test_since_returns_only_events_after_the_cursor(log):
    for _ in range(5):
        await log.publish("action.updated", dict(ACTION_PAYLOAD))

    assert [r.seq for r in await log.since(2)] == [3, 4, 5]
    assert [r.seq for r in await log.since(0, 2)] == [1, 2]
    assert await log.since(5) == []
    assert await log.bounds() == (1, 5)


@aio
async def test_poll_cold_start_hands_back_only_the_cursor(log):
    await log.publish("action.created", dict(ACTION_PAYLOAD))
    body = await log.poll()
    assert body == {"events": [], "cursor": 1, "oldest_seq": 1, "reset": False}


@aio
async def test_poll_returns_the_events_after_the_cursor(log):
    await log.publish("action.created", dict(ACTION_PAYLOAD))
    await log.publish("repo.updated", {"repo_id": "r_000000000001"})

    body = await log.poll(1)
    assert set(body) == {"events", "cursor", "oldest_seq", "reset"}
    assert body["reset"] is False
    assert body["cursor"] == 2
    assert body["oldest_seq"] == 1
    assert body["events"] == [{"seq": 2, "at": TS, "type": "repo.updated",
                               "payload": {"repo_id": "r_000000000001"}}]
    # Caught up: same cursor, no events, still not a reset.
    assert await log.poll(2) == {"events": [], "cursor": 2, "oldest_seq": 1, "reset": False}


@aio
async def test_poll_resets_a_cursor_that_fell_out_of_the_ring(C, log, monkeypatch):
    monkeypatch.setattr(C, "EVENTS_RING_SIZE", 3)
    for _ in range(5):
        await log.publish("action.updated", dict(ACTION_PAYLOAD))
    assert await log.bounds() == (3, 5)

    assert await log.poll(1) == {"events": [], "cursor": 5, "oldest_seq": 3, "reset": True}
    # The oldest surviving row is still replayable from the frame before it.
    assert [e["seq"] for e in (await log.poll(2))["events"]] == [3, 4, 5]


@aio
async def test_poll_resets_a_cursor_ahead_of_the_log(log):
    """A rebuilt storage file restarts ``seq`` at 1; resuming would drop every new event."""
    await log.publish("action.created", dict(ACTION_PAYLOAD))
    assert await log.poll(99) == {"events": [], "cursor": 1, "oldest_seq": 1, "reset": True}


@aio
async def test_poll_limit_is_clamped_to_the_ring(C, log):
    for _ in range(3):
        await log.publish("action.updated", dict(ACTION_PAYLOAD))

    assert [e["seq"] for e in (await log.poll(0, 2))["events"]] == [1, 2]
    assert [e["seq"] for e in (await log.poll(0, 0))["events"]] == [1]                     # floor of 1
    assert len((await log.poll(0, C.EVENTS_RING_SIZE * 10))["events"]) == 3                # ceiling


# --------------------------------------------------------------------------- #
# the SSE stream (§2.12)
# --------------------------------------------------------------------------- #


@aio
async def test_stream_sets_the_sse_headers_before_the_first_frame(E, log, request_factory):
    request = request_factory.owner("GET", "/events")
    wire, task = start(log, request, fail_after=1)          # die on the greeting
    response = await task

    assert wire.frames == [
        b'id: 0\nevent: hello\ndata: {"newest_seq":0,"server_time":"2026-09-04T10:00:00Z",'
        b'"app_version":"1.0.0"}\n\n'
    ]
    for key, value in E.SSE_HEADERS.items():
        assert response.headers[key] == value


@aio
async def test_stream_greets_then_writes_live_frames_verbatim(log, request_factory):
    request = request_factory.owner("GET", "/events")
    wire, task = start(log, request)
    await wire.wait(1)

    await log.publish("action.created", dict(ACTION_PAYLOAD))
    frames = await wire.wait(2)
    await stop(task)

    assert frames[1] == f"id: 1\nevent: action.created\ndata: {ACTION_DATA}\n\n".encode("utf-8")


@aio
async def test_stream_replays_from_the_cursor_then_goes_live(log, request_factory):
    for _ in range(3):
        await log.publish("action.updated", dict(ACTION_PAYLOAD))

    request = request_factory.owner("GET", "/events", query={"cursor": "1"})
    wire, task = start(log, request, cursor=1)
    await wire.wait(2)                                    # seq 2 and 3, no greeting

    await log.publish("repo.updated", {"repo_id": "r_000000000001"})
    frames = await wire.wait(3)
    await stop(task)

    assert wire.events() == ["action.updated", "action.updated", "repo.updated"]
    assert [f.split(b"\n", 1)[0] for f in frames] == [b"id: 2", b"id: 3", b"id: 4"]


@aio
async def test_stream_replays_more_than_one_page(E, log, request_factory, monkeypatch):
    """A catch-up longer than one query is written page by page, in order, with nothing missing."""
    monkeypatch.setattr(E, "REPLAY_PAGE", 2)
    for _ in range(5):
        await log.publish("action.updated", dict(ACTION_PAYLOAD))

    request = request_factory.owner("GET", "/events", query={"cursor": "0"})
    wire, task = start(log, request, cursor=0)
    frames = await wire.wait(5)
    await settle()
    await stop(task)

    assert [f.split(b"\n", 1)[0] for f in frames] == [b"id: 1", b"id: 2", b"id: 3", b"id: 4", b"id: 5"]
    assert len(wire.frames) == 5


@aio
async def test_stream_with_a_current_cursor_replays_nothing_and_waits(log, request_factory):
    await log.publish("action.created", dict(ACTION_PAYLOAD))

    request = request_factory.owner("GET", "/events", query={"cursor": "1"})
    wire, task = start(log, request, cursor=1)
    await settle()
    assert wire.frames == []                               # no greeting, no reset, no replay

    await log.publish("repo.updated", {"repo_id": "r_000000000001"})
    frames = await wire.wait(1)
    await stop(task)
    assert frames[0].startswith(b"id: 2\nevent: repo.updated")


@aio
async def test_stream_honours_the_last_event_id_header_when_no_cursor_is_given(log, request_factory):
    for _ in range(3):
        await log.publish("action.updated", dict(ACTION_PAYLOAD))

    request = request_factory.owner("GET", "/events", headers={"Last-Event-ID": "2"})
    wire, task = start(log, request, cursor=None)
    frames = await wire.wait(1)
    await stop(task)

    assert frames[0].startswith(b"id: 3\nevent: action.updated")


@aio
async def test_stream_ignores_a_malformed_last_event_id(log, request_factory):
    await log.publish("action.created", dict(ACTION_PAYLOAD))
    request = request_factory.owner("GET", "/events", headers={"Last-Event-ID": "not-a-number"})
    wire, task = start(log, request, cursor=None)
    frames = await wire.wait(1)
    await stop(task)

    assert wire.events() == ["hello"]
    assert frames[0].startswith(b"id: 1\nevent: hello")


@aio
async def test_stream_resets_a_cursor_that_fell_out_of_the_ring(C, log, request_factory, monkeypatch):
    monkeypatch.setattr(C, "EVENTS_RING_SIZE", 3)
    for _ in range(5):
        await log.publish("action.updated", dict(ACTION_PAYLOAD))

    request = request_factory.owner("GET", "/events", query={"cursor": "1"})
    wire, task = start(log, request, cursor=1)
    await wire.wait(1)

    await log.publish("repo.updated", {"repo_id": "r_000000000001"})
    frames = await wire.wait(2)
    await stop(task)

    assert frames[0] == b'id: 5\nevent: reset\ndata: {"oldest_seq":3,"newest_seq":5}\n\n'
    # The reset carries the newest id so the browser's own Last-Event-ID moves on instead of asking
    # for the same dead cursor forever, and the next live event follows normally.
    assert frames[1].startswith(b"id: 6\nevent: repo.updated")


@aio
async def test_stream_resets_a_lagged_reader_and_drops_the_backlog(E, log, request_factory, record,
                                                                  monkeypatch):
    monkeypatch.setattr(E, "SUBSCRIBER_QUEUE_MAX", 1)
    await log.publish("action.created", dict(ACTION_PAYLOAD))

    request = request_factory.owner("GET", "/events")
    subs = spy_subscribe(log)
    wire, task = start(log, request)
    await wire.wait(1)                                    # hello

    sub = subs[0]
    assert sub.offer(record(2)) is True                    # fits
    assert sub.offer(record(3)) is False                   # overflow → lagged
    sub.offer(record(4))
    assert sub.lagged is True

    frames = await wire.wait(2)
    await settle()
    await stop(task)

    assert wire.events() == ["hello", "reset"]             # the buffered frame is never written
    assert frames[1] == b'id: 1\nevent: reset\ndata: {"oldest_seq":1,"newest_seq":1}\n\n'
    assert sub.lagged is False and sub.queue.empty()


@aio
async def test_stream_never_repeats_an_event_the_replay_already_sent(log, request_factory, record):
    """The subscription is opened before the ring is read, so the overlap is a duplicate, not a gap."""
    await log.publish("action.created", dict(ACTION_PAYLOAD))

    request = request_factory.owner("GET", "/events", query={"cursor": "0"})
    subs = spy_subscribe(log)
    wire, task = start(log, request, cursor=0)
    await wire.wait(1)                                    # replayed seq 1

    subs[0].offer(record(1))                              # the same event, still in the mailbox
    await settle()
    assert len(wire.frames) == 1

    subs[0].offer(record(2))
    frames = await wire.wait(2)
    await stop(task)
    assert frames[1].startswith(b"id: 2\n")


@aio
async def test_stream_heartbeats_while_idle(log, request_factory):
    request = request_factory.owner("GET", "/events")
    wire, task = start(log, request, heartbeat=0.01)
    frames = await wire.wait(3)
    await stop(task)

    assert frames[0].startswith(b"id: 0\nevent: hello")
    assert frames[1:3] == [b": ping\n\n", b": ping\n\n"]


@aio
async def test_stream_keeps_streaming_after_a_heartbeat(log, request_factory):
    request = request_factory.owner("GET", "/events")
    wire, task = start(log, request, heartbeat=0.01)
    await wire.wait(2)                                     # hello + at least one ping

    await log.publish("action.created", dict(ACTION_PAYLOAD))
    await wire.wait(3)
    await stop(task)

    assert wire.events()[-1] == "action.created"
    assert wire.payload(len(wire.frames) - 1) == ACTION_PAYLOAD


@aio
async def test_stream_closes_its_subscription_when_the_client_disappears(log, request_factory):
    request = request_factory.owner("GET", "/events")
    subs = spy_subscribe(log)
    wire, task = start(log, request, fail_after=2)
    await wire.wait(1)

    await log.publish("action.created", dict(ACTION_PAYLOAD))   # write #2 → ConnectionResetError
    response = await task

    assert response.prepared
    sub = subs[0]
    sub.drain()
    await log.publish("action.updated", dict(ACTION_PAYLOAD))
    assert sub.queue.empty()                                   # unsubscribed, not leaked


@aio
async def test_stream_cancellation_closes_the_subscription(log, request_factory):
    request = request_factory.owner("GET", "/events")
    subs = spy_subscribe(log)
    wire, task = start(log, request)
    await wire.wait(1)

    await stop(task)                                           # what on_shutdown does
    await log.publish("action.created", dict(ACTION_PAYLOAD))
    assert subs[0].queue.empty()


# --------------------------------------------------------------------------- #
# a stream that breaks while it is being served
# --------------------------------------------------------------------------- #


class UnreadableRing:
    """A storage whose ring queries fail the way a closed database does: ``storage_unavailable``.

    ``bounds_ok`` aims the same degraded state at either half of the handler: the stream reads the
    bounds before it prepares the response and pages the replay after, so the flag decides whether the
    failure lands where an HTTP status is still available or where it no longer is.
    """

    def __init__(self, errors: Any, *, bounds_ok: bool = False) -> None:
        self._errors = errors
        self._bounds_ok = bool(bounds_ok)

    def _closed(self) -> Exception:
        return self._errors.StudioError("storage_unavailable", "the Studio database is not open")

    def event_bounds(self) -> tuple[int, int]:
        if self._bounds_ok:
            return (0, 0)
        raise self._closed()

    def event_since(self, cursor: int, limit: int = 500) -> list:
        raise self._closed()

    def event_append(self, type: str, payload: Any) -> int:
        raise self._closed()


@aio
async def test_a_ring_that_cannot_be_read_at_connect_time_answers_an_http_error_not_a_stream(
    E, studio, clock, request_factory
):
    """Adversary: the database is closed when a reader connects, which is a path Studio designs for.

    The opening ring query is what raises ``storage_unavailable``. If it runs after ``prepare()`` the
    client already holds ``200 text/event-stream`` and gets a chunked body that simply stops, which it
    cannot tell from a healthy stream that ended, and the handler's 503 envelope can no longer be sent.
    """
    log = E.EventLog(UnreadableRing(studio.errors), None, clock)
    request = request_factory.owner("GET", "/events")
    wire = Wire(request)
    subs = spy_subscribe(log)

    with pytest.raises(studio.errors.StudioError) as caught:
        await log.stream(request, cursor=None, heartbeat_secs=5.0)

    assert (caught.value.code, caught.value.status) == ("storage_unavailable", 503)
    # No status line and no frame ever left the process, so the route wrapper's error envelope is
    # still the response this request gets.
    assert request._payload_writer.write_headers.called is False
    assert wire.frames == []
    # And the mailbox is not leaked: nothing prepared, so the streaming `finally` never ran.
    log._fan_out(studio.storage.EventRecord(seq=1, at=TS, type="reset", payload={}))
    assert subs[0].queue.empty()


@aio
async def test_a_ring_that_fails_after_the_headers_are_out_ends_the_stream_instead_of_escaping(
    E, studio, clock, request_factory, caplog
):
    """Adversary: storage dies between ``prepare()`` and the first replayed frame.

    There is no status left to send at that point, so the exception must not escape into aiohttp: it
    would truncate the body with nothing the client could act on. The stream ends, the browser's
    ``EventSource`` reconnects on its own, and the failure is in the log with its traceback.
    """
    caplog.set_level(logging.ERROR, logger="kirocrew.app.aidlc-studio")
    log = E.EventLog(UnreadableRing(studio.errors, bounds_ok=True), None, clock)
    request = request_factory.owner("GET", "/events", query={"cursor": "0"})
    wire = Wire(request)
    subs = spy_subscribe(log)

    response = await log.stream(request, cursor=0, heartbeat_secs=5.0)

    assert response.prepared is True                # the headers were already on the wire
    assert wire.frames == []                        # nothing half-written after them
    ended = [r for r in caplog.records if "SSE stream ended early" in r.getMessage()]
    assert len(ended) == 1
    assert ended[0].exc_info is not None and ended[0].exc_info[1].code == "storage_unavailable"
    log._fan_out(studio.storage.EventRecord(seq=1, at=TS, type="reset", payload={}))
    assert subs[0].queue.empty()                    # unsubscribed on the way out


@aio
async def test_cancellation_still_reaches_whoever_cancelled_the_stream(log, request_factory):
    """Adversary: shutdown cancels the request task and the stream reports that it finished normally.

    A swallowed ``CancelledError`` makes ``await task`` hand back a value, so ``on_shutdown`` (or any
    ``wait_for`` around the request) is told the connection closed by itself while the task was in fact
    killed, and the shutdown moves on with a request it never actually saw end.
    """
    request = request_factory.owner("GET", "/events")
    subs = spy_subscribe(log)
    wire, task = start(log, request)
    await wire.wait(1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # §2.12's actual promise for this arm is unchanged: the subscription is dropped, not leaked.
    await log.publish("action.created", dict(ACTION_PAYLOAD))
    assert subs[0].queue.empty()

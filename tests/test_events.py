"""Tests for ``backend/studio/events.py`` — the in-memory ring, the SSE wire and the host mirror.

What is actually being defended here:

* **A client can always resume, or is told it cannot.** Every published event gets a ``seq``, the SSE
  frame carries it as ``id``, and a replay from that id returns exactly the events after it. A cursor the
  ring cannot honour — trimmed, ahead of the ring, or handed out by an earlier process — resets. The frame
  bytes are asserted verbatim, because they are a wire contract shared with ``ui/src/lib/sse.ts``.
* **A slow reader is reset, not waited for.** The publisher never blocks on a socket; a reader that falls
  off the ring while it drains is told to refetch everything.
* **The host mirror can never break a request.** A manifest/permission mismatch (or any other host-bus
  failure) is swallowed and logged once, while Studio's own stream carries on.

There is no pytest-asyncio in the pinned interpreter, so async bodies run through ``asyncio.run`` via the
``@aio`` decorator, and the SSE tests capture the socket by replacing the mocked request's payload writer
with a recorder — no Studio code is mocked anywhere.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
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
    """Run an async test body on a fresh loop (no pytest-asyncio in the pinned venv)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


@pytest.fixture
def E(studio):
    module = studio.events
    assert module is not None, getattr(studio, "events__error", "events.py did not import")
    return module


@pytest.fixture
def C(studio):
    return studio.constants


@pytest.fixture
def log(E, fake_ctx, clock):
    """The real EventLog wired to the recording ``ctx.events`` bus."""
    return E.EventLog(fake_ctx.events, clock)


async def publish(log: Any, count: int, type: str = "action.updated") -> list[int]:
    return [await log.publish(type, dict(ACTION_PAYLOAD)) for _ in range(count)]


# --------------------------------------------------------------------------- #
# SSE harness
# --------------------------------------------------------------------------- #


class Wire:
    """The socket the stream writes to: records every frame, can die like a closed tab or stall like a
    slow one. Installed on the mocked request's payload writer, so ``prepare``/``write`` run for real."""

    def __init__(self, request: Any, *, fail_after: int | None = None, error: type = ConnectionResetError) -> None:
        self.frames: list[bytes] = []
        self.fail_after = fail_after
        self.error = error
        self.open = asyncio.Event()
        self.open.set()
        request._payload_writer.write = self._write

    async def _write(self, data: bytes) -> None:
        await self.open.wait()
        self.frames.append(bytes(data))
        if self.fail_after is not None and len(self.frames) >= self.fail_after:
            raise self.error("client went away")

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

    def ids(self) -> list[int]:
        return [int(f.split(b"\n", 1)[0][len(b"id: "):]) for f in self.frames if f.startswith(b"id: ")]

    def payload(self, index: int) -> dict:
        line = next(l for l in self.frames[index].decode("utf-8").splitlines() if l.startswith("data: "))
        return json.loads(line[len("data: "):])

    async def wait(self, count: int = 0, *, event: str | None = None, timeout: float = 2.0) -> list[bytes]:
        """Until ``count`` frames are written, or until a frame of type ``event`` is."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while len(self.frames) < count or (event is not None and event not in self.events()):
            if loop.time() > deadline:
                raise AssertionError(f"only {len(self.frames)} frame(s) written: {self.events()}")
            await asyncio.sleep(0.005)
        return list(self.frames)


def start(log: Any, request: Any, *, cursor: int | None = None, heartbeat: float = 5.0, **wire: Any):
    wire_ = Wire(request, **wire)
    task = asyncio.create_task(log.stream(request, cursor=cursor, heartbeat_secs=heartbeat))
    return wire_, task


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
    # `reset` is publishable (a producer may tell every client to start over); `hello` is only a greeting.
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
        "reset": None,
    }
    declared = set(manifest["permissions"]["events"])
    assert {name for name in mapped.values() if name} <= declared


# --------------------------------------------------------------------------- #
# publish
# --------------------------------------------------------------------------- #


@aio
async def test_seq_starts_at_the_process_clock_and_counts_by_one(E, log, clock):
    base = int(clock.monotonic() * 1_000_000)
    assert log.bounds() == (base + 1, base)                # empty: "oldest - 1" is the newest cursor
    first = await log.publish("action.created", dict(ACTION_PAYLOAD))
    second = await log.publish("intent.updated", {"repo_id": "r_000000000001", "intent_key": F.PAYLOAD_GATE_OPEN,
                                                  "operational_state": "WaitingForYou"})
    assert (first, second) == (base + 1, base + 2)
    records = await log.since(0)
    assert [(r.seq, r.type) for r in records] == [(base + 1, "action.created"), (base + 2, "intent.updated")]
    assert records[0].payload == ACTION_PAYLOAD and records[0].at == clock.iso() == TS
    assert records[0].to_json() == {"seq": base + 1, "at": TS, "type": "action.created", "payload": ACTION_PAYLOAD}
    assert log.bounds() == (base + 1, base + 2)


@aio
async def test_publish_refuses_a_type_outside_the_vocabulary(log):
    before = log.bounds()
    with pytest.raises(ValueError):
        await log.publish("action.exploded", {"action_id": "a_1"})
    assert log.bounds() == before


@aio
async def test_publish_copies_the_payload_so_a_caller_can_reuse_its_dict(log):
    payload = dict(ACTION_PAYLOAD)
    await log.publish("action.created", payload)
    payload["status"] = "Delivering"
    assert (await log.since(0))[0].payload["status"] == "Queued"


@aio
async def test_the_ring_keeps_only_the_newest_events(E, fake_ctx, clock):
    log = E.EventLog(fake_ctx.events, clock, ring_size=3)
    seqs = await publish(log, 5)
    assert [r.seq for r in await log.since(0)] == seqs[2:]
    assert log.bounds() == (seqs[2], seqs[4])


@aio
async def test_publish_mirrors_action_repo_and_transaction_events_to_the_host(log, fake_ctx, C):
    seq = await log.publish("action.created", dict(ACTION_PAYLOAD))
    await log.publish("repo.removed", {"repo_id": "r_000000000001"})
    await log.publish("intent.updated", {"repo_id": "r_000000000001", "intent_key": F.PAYLOAD_GATE_OPEN,
                                         "operational_state": "Queued"})
    await log.publish("transaction.updated", {"transaction_id": "tx_0000000000000001",
                                              "repo_id": "r_000000000001", "status": "committed"})

    names = [name for name, _ in fake_ctx.events.published]
    assert names == [C.HOST_EVENT_ACTION, C.HOST_EVENT_REPO, C.HOST_EVENT_REPO, C.HOST_EVENT_TRANSACTION]
    # The Studio type rides inside `data`: `payload["type"]` is already the action type.
    assert fake_ctx.events.published[0][1] == {"seq": seq, "event": "action.created", "payload": ACTION_PAYLOAD}


@aio
async def test_studio_internal_events_are_not_mirrored(log, fake_ctx):
    await log.publish("lease.updated", {"resolved_repo_identity": "1:2", "repo_id": "r_000000000001",
                                        "kind": "execution", "held": True})
    await log.publish("activity.appended", {"id": 1, "kind": "gate_approved", "repo_id": "r_000000000001",
                                            "intent_key": F.PAYLOAD_GATE_OPEN})
    await log.publish("settings.updated", {"keys": ["density"]})
    await log.publish("health.updated", {"status": "degraded", "issues": ["payload_degraded"]})
    await log.publish("advisor.updated", {"draft_id": "d_0000000000000001", "action_id": "a_0000000000000001",
                                          "status": "ready"})
    await log.publish("reset", {"oldest_seq": 1, "newest_seq": 6})
    assert fake_ctx.events.published == []


@aio
async def test_a_denied_host_event_name_is_swallowed_and_logged_once(E, clock, caplog):
    """An `app.json` that does not declare the event must not fail the user's state change."""
    caplog.set_level(logging.WARNING, logger="kirocrew.app.aidlc-studio")
    log = E.EventLog(FakeEventBus([]), clock)
    seqs = await publish(log, 3)
    assert [r.seq for r in await log.since(0)] == seqs          # Studio's own ring unaffected
    assert len([r for r in caplog.records if "host event bus refused" in r.getMessage()]) == 1


@aio
async def test_a_broken_host_bus_is_swallowed_and_logged_once(E, clock, caplog):
    caplog.set_level(logging.WARNING, logger="kirocrew.app.aidlc-studio")

    class Exploding:
        def publish(self, event_type: str, data: dict | None = None) -> None:
            raise RuntimeError("broadcast socket is gone")

    log = E.EventLog(Exploding(), clock)
    assert len(await publish(log, 2, "repo.updated")) == 2
    assert len([r for r in caplog.records if "host event bus failed" in r.getMessage()]) == 1


@aio
async def test_no_host_bus_at_all_is_normal(E, clock):
    """``ctx.events`` is ``None`` when the events permission is absent — publishing still works."""
    log = E.EventLog(None, clock)
    assert await log.publish("action.created", dict(ACTION_PAYLOAD)) == log.bounds()[1]


# --------------------------------------------------------------------------- #
# since / poll
# --------------------------------------------------------------------------- #


@aio
async def test_since_returns_only_events_after_the_cursor(log):
    seqs = await publish(log, 5)
    assert [r.seq for r in await log.since(seqs[1])] == seqs[2:]
    assert [r.seq for r in await log.since(0, 2)] == seqs[:2]
    assert await log.since(seqs[-1]) == []


@aio
async def test_poll_cold_start_hands_back_only_the_cursor(log):
    seq = await log.publish("action.created", dict(ACTION_PAYLOAD))
    assert await log.poll() == {"events": [], "cursor": seq, "oldest_seq": seq, "reset": False}


@aio
async def test_poll_returns_the_events_after_the_cursor(log):
    first = await log.publish("action.created", dict(ACTION_PAYLOAD))
    second = await log.publish("repo.updated", {"repo_id": "r_000000000001"})

    body = await log.poll(first)
    assert body == {
        "events": [{"seq": second, "at": TS, "type": "repo.updated", "payload": {"repo_id": "r_000000000001"}}],
        "cursor": second,
        "oldest_seq": first,
        "reset": False,
    }
    # Caught up: same cursor, no events, still not a reset.
    assert await log.poll(second) == {"events": [], "cursor": second, "oldest_seq": first, "reset": False}


@aio
async def test_cursor_zero_means_from_the_start_of_this_process(E, fake_ctx, clock):
    log = E.EventLog(fake_ctx.events, clock, ring_size=3)
    assert await log.poll(0) == {"events": [], "cursor": 0, "oldest_seq": log.bounds()[0], "reset": False}
    seqs = await publish(log, 3)
    assert [e["seq"] for e in (await log.poll(0))["events"]] == seqs
    # Once the ring has trimmed this process's first event, "everything" can no longer be replayed.
    await publish(log, 1)
    assert (await log.poll(0))["reset"] is True


@aio
async def test_poll_resets_a_cursor_that_fell_out_of_the_ring(E, fake_ctx, clock):
    log = E.EventLog(fake_ctx.events, clock, ring_size=3)
    seqs = await publish(log, 5)
    assert await log.poll(seqs[0]) == {"events": [], "cursor": seqs[4], "oldest_seq": seqs[2], "reset": True}
    # The oldest surviving event is still replayable from the frame before it.
    assert [e["seq"] for e in (await log.poll(seqs[1]))["events"]] == seqs[2:]


@aio
async def test_poll_resets_a_cursor_ahead_of_the_ring(log):
    seq = await log.publish("action.created", dict(ACTION_PAYLOAD))
    assert await log.poll(seq + 99) == {"events": [], "cursor": seq, "oldest_seq": seq, "reset": True}


@aio
async def test_a_cursor_from_an_earlier_process_resets_even_after_this_one_caught_up(E, fake_ctx, clock):
    """The restart case a per-process counter starting at 1 would get silently wrong.

    The old process handed out three ids; the new one publishes more than three before the browser
    reconnects. Resuming would replay the new process's events after "3" and drop its first three.
    """
    before = E.EventLog(fake_ctx.events, clock)
    cursor = (await publish(before, 3))[-1]
    clock.advance(5)                                        # the gateway restarts
    after = E.EventLog(fake_ctx.events, clock)
    await publish(after, 10)
    assert (await after.poll(cursor))["reset"] is True


@aio
async def test_poll_limit_is_clamped_to_the_ring(C, log):
    seqs = await publish(log, 3)
    assert [e["seq"] for e in (await log.poll(0, 2))["events"]] == seqs[:2]
    assert [e["seq"] for e in (await log.poll(0, 0))["events"]] == seqs[:1]              # floor of 1
    assert len((await log.poll(0, C.EVENTS_RING_SIZE * 10))["events"]) == 3              # ceiling


# --------------------------------------------------------------------------- #
# the SSE stream (§2.12)
# --------------------------------------------------------------------------- #


@aio
async def test_stream_sets_the_sse_headers_before_the_first_frame(C, E, log, request_factory):
    request = request_factory.owner("GET", "/events")
    base = log.bounds()[1]
    wire, task = start(log, request, fail_after=1)          # die on the greeting
    response = await task

    assert wire.frames == [
        f'id: {base}\nevent: hello\ndata: {{"newest_seq":{base},"server_time":"2026-09-04T10:00:00Z",'
        f'"app_version":"{C.APP_VERSION}"}}\n\n'.encode("utf-8")
    ]
    for key, value in E.SSE_HEADERS.items():
        assert response.headers[key] == value
    assert log._readers == set()                            # the dead reader is not left registered


@aio
async def test_stream_greets_then_writes_live_frames_verbatim(log, request_factory):
    wire, task = start(log, request_factory.owner("GET", "/events"))
    await wire.wait(1)
    seq = await log.publish("action.created", dict(ACTION_PAYLOAD))
    frames = await wire.wait(2)
    await stop(task)
    assert frames[1] == f"id: {seq}\nevent: action.created\ndata: {ACTION_DATA}\n\n".encode("utf-8")


@aio
async def test_stream_replays_from_the_cursor_then_goes_live(log, request_factory):
    seqs = await publish(log, 3)
    wire, task = start(log, request_factory.owner("GET", "/events"), cursor=seqs[0])
    await wire.wait(2)                                      # the two after the cursor, no greeting
    live = await log.publish("repo.updated", {"repo_id": "r_000000000001"})
    await wire.wait(3)
    await stop(task)
    assert wire.events() == ["action.updated", "action.updated", "repo.updated"]
    assert wire.ids() == [seqs[1], seqs[2], live]


@aio
async def test_stream_with_a_current_cursor_replays_nothing_and_waits(log, request_factory):
    seq = await log.publish("action.created", dict(ACTION_PAYLOAD))
    wire, task = start(log, request_factory.owner("GET", "/events"), cursor=seq)
    await settle()
    assert wire.frames == []                               # no greeting, no reset, no replay
    live = await log.publish("repo.updated", {"repo_id": "r_000000000001"})
    frames = await wire.wait(1)
    await stop(task)
    assert frames[0].startswith(f"id: {live}\nevent: repo.updated".encode())


@aio
async def test_stream_honours_the_last_event_id_header_when_no_cursor_is_given(log, request_factory):
    seqs = await publish(log, 3)
    request = request_factory.owner("GET", "/events", headers={"Last-Event-ID": str(seqs[1])})
    wire, task = start(log, request, cursor=None)
    frames = await wire.wait(1)
    await stop(task)
    assert frames[0].startswith(f"id: {seqs[2]}\nevent: action.updated".encode())


@aio
async def test_stream_ignores_a_malformed_last_event_id(log, request_factory):
    seq = await log.publish("action.created", dict(ACTION_PAYLOAD))
    request = request_factory.owner("GET", "/events", headers={"Last-Event-ID": "not-a-number"})
    wire, task = start(log, request, cursor=None)
    frames = await wire.wait(1)
    await stop(task)
    assert wire.events() == ["hello"]
    assert frames[0].startswith(f"id: {seq}\nevent: hello".encode())


@aio
async def test_stream_resets_a_cursor_that_fell_out_of_the_ring(E, fake_ctx, clock, request_factory):
    log = E.EventLog(fake_ctx.events, clock, ring_size=3)
    seqs = await publish(log, 5)
    wire, task = start(log, request_factory.owner("GET", "/events"), cursor=seqs[0])
    await wire.wait(1)
    live = await log.publish("repo.updated", {"repo_id": "r_000000000001"})
    frames = await wire.wait(2)
    await stop(task)

    assert frames[0] == f'id: {seqs[4]}\nevent: reset\ndata: {{"oldest_seq":{seqs[2]},"newest_seq":{seqs[4]}}}\n\n'.encode()
    # The reset carries the newest id so the browser's own Last-Event-ID moves on, and the next live event
    # follows normally.
    assert frames[1].startswith(f"id: {live}\nevent: repo.updated".encode())


@aio
async def test_stream_resets_a_cursor_from_an_earlier_process(E, fake_ctx, clock, request_factory):
    before = E.EventLog(fake_ctx.events, clock)
    cursor = (await publish(before, 3))[-1]
    clock.advance(5)
    after = E.EventLog(fake_ctx.events, clock)
    await publish(after, 10)
    wire, task = start(after, request_factory.owner("GET", "/events"), cursor=cursor)
    await wire.wait(1)
    await stop(task)
    assert wire.events() == ["reset"]


@aio
async def test_a_reader_that_falls_off_the_ring_is_reset_and_never_blocks_the_publisher(
    E, fake_ctx, clock, request_factory
):
    log = E.EventLog(fake_ctx.events, clock, ring_size=3)
    wire, task = start(log, request_factory.owner("GET", "/events"))
    await wire.wait(1)                                      # hello
    wire.open.clear()                                       # the socket stops draining
    first = await log.publish("action.created", dict(ACTION_PAYLOAD))
    await settle()                                          # the reader is now stuck writing `first`
    seqs = await publish(log, 5)                            # the publisher does not wait for it
    wire.open.set()
    frames = await wire.wait(3)
    await settle()
    await stop(task)

    assert wire.events() == ["hello", "action.created", "reset"]
    assert wire.ids()[1] == first
    assert frames[2] == f'id: {seqs[-1]}\nevent: reset\ndata: {{"oldest_seq":{seqs[2]},"newest_seq":{seqs[-1]}}}\n\n'.encode()


@aio
async def test_stream_heartbeats_while_idle(log, request_factory):
    wire, task = start(log, request_factory.owner("GET", "/events"), heartbeat=0.01)
    frames = await wire.wait(3)
    await stop(task)
    assert wire.events()[0] == "hello"
    assert frames[1:3] == [b": ping\n\n", b": ping\n\n"]


@aio
async def test_stream_keeps_streaming_after_a_heartbeat(log, request_factory):
    wire, task = start(log, request_factory.owner("GET", "/events"), heartbeat=0.01)
    await wire.wait(2)                                      # hello + at least one ping
    await log.publish("action.created", dict(ACTION_PAYLOAD))
    await wire.wait(event="action.created")
    await stop(task)
    created = wire.events().index("action.created")
    assert wire.payload(created) == ACTION_PAYLOAD


@aio
async def test_stream_drops_its_reader_when_the_client_disappears(log, request_factory):
    wire, task = start(log, request_factory.owner("GET", "/events"), fail_after=2)
    await wire.wait(1)
    await log.publish("action.created", dict(ACTION_PAYLOAD))   # write #2 → ConnectionResetError
    response = await task
    assert response.prepared
    assert log._readers == set()


@aio
async def test_an_unexpected_error_after_the_headers_ends_the_stream_quietly(log, request_factory, caplog):
    """No status is left to send once the headers are out, so the stream stops and the failure is logged;
    an exception escaping into aiohttp would truncate the body with nothing the client could act on."""
    caplog.set_level(logging.ERROR, logger="kirocrew.app.aidlc-studio")
    wire, task = start(log, request_factory.owner("GET", "/events"), fail_after=1, error=RuntimeError)
    response = await task
    assert response.prepared is True and wire.events() == ["hello"]
    ended = [r for r in caplog.records if "SSE stream ended early" in r.getMessage()]
    assert len(ended) == 1 and ended[0].exc_info is not None
    assert log._readers == set()


@aio
async def test_cancellation_still_reaches_whoever_cancelled_the_stream(log, request_factory):
    """A swallowed ``CancelledError`` would tell ``on_shutdown`` the request ended by itself."""
    wire, task = start(log, request_factory.owner("GET", "/events"))
    await wire.wait(1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert log._readers == set()

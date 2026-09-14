"""Studio's event log: one durable ring, N in-process SSE readers, one best-effort host mirror.

Every module that changes something a browser can see calls :meth:`EventLog.publish`. It has three
fan-out targets and the *order* is the design:

1. **The SQLite ring, first.** ``seq`` is the SSE ``id`` a client resumes from, so the row must be on
   disk before anyone learns the number exists: a client that received ``id: 41`` and reconnects asking
   for everything after 41 has to find 41 in the ring, otherwise the replay window silently starts at
   42 and one update is lost forever. Publishing therefore fails loudly (the storage error propagates)
   rather than notifying live readers about an event nobody can replay.
2. **The subscriber queues, never waiting.** A browser that stopped reading must not be able to stall
   the reconciler tick that published the event. Each subscriber has a bounded mailbox; when it is full
   the subscription is flagged ``lagged`` and the frames are dropped, because a queue that grows
   without limit turns one stuck tab into the gateway's memory leak. The stream then tells that client
   to refetch everything (``event: reset``) instead of feeding it a partial history.
3. **``ctx.events``, best effort and last.** The host does not forward ``app_event`` to app frontends
   today (architecture A07), so this mirror exists only so a future host works. A permission mismatch
   between this code and ``app.json`` — or any other failure inside the host bus — must therefore
   never turn a successful state change into a failed request: it is swallowed and logged once.

Payloads are **references, not content** (see the table in ``docs/design/contracts.md`` §1.20): ids,
enum values and counts. The client refetches the resource. That keeps every frame small enough to be
irrelevant to the ring's disk budget, and it keeps human text, credentials and repository paths out of
a channel that is broadcast to every connected dashboard client.

Threading: this module is async and lives on the gateway loop (§0.6). The only blocking work is SQLite,
which goes through ``asyncio.to_thread``; nothing here holds a lock across an ``await``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from aiohttp import web

from . import constants as C
from .storage import EventRecord

if TYPE_CHECKING:  # annotations only — importing these at runtime would invert the module layering
    from .constants import Clock
    from .storage import Storage

logger = logging.getLogger("kirocrew.app.aidlc-studio")

# --------------------------------------------------------------------------- #
# the vocabulary (§1.20)
# --------------------------------------------------------------------------- #

#: Every type Studio may publish. Closed on purpose: an unlisted type would be persisted, replayed and
#: silently ignored by the UI's reducer, which is the hardest kind of bug to see. ``publish`` refuses
#: one instead. ``"hello"`` is deliberately absent — it is a per-connection greeting, not an event.
EVENT_TYPES = (
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

#: Stream-only frame names. ``reset`` is also a publishable type (a migration may tell every client to
#: start over), so the constant is shared rather than spelled twice.
EVENT_HELLO = "hello"
EVENT_RESET = "reset"

#: Mapping from Studio's event names to the three host event names declared in ``app.json``
#: (``permissions.events``). Prefix-based so a new ``action.*`` or ``repo.*`` type is mirrored without
#: touching the host manifest, which cannot be changed without a reinstall.
HOST_EVENT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("action.", C.HOST_EVENT_ACTION),
    ("repo.", C.HOST_EVENT_REPO),
    ("intent.", C.HOST_EVENT_REPO),
    ("transaction.", C.HOST_EVENT_TRANSACTION),
)

#: Set before ``prepare()``: the host's ``no_cache_middleware`` only ``setdefault``s security headers,
#: so whatever is written here wins, and after ``prepare()`` the headers are already on the wire
#: (research 01 §1.4). ``X-Accel-Buffering`` is for a reverse proxy in front of the gateway.
SSE_HEADERS: dict[str, str] = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

#: An SSE comment. Its only job is to make a dead connection fail a write, so the handler notices the
#: client is gone instead of holding a subscription open until the gateway restarts.
SSE_PING = b": ping\n\n"

#: Per-connection mailbox depth. 1 000 references (~100 kB) is far more than a browser can fall behind
#: by while remaining useful: past that, replaying the backlog is slower and less correct than telling
#: the client to refetch. Module-level so a test can lower it instead of publishing a thousand events.
SUBSCRIBER_QUEUE_MAX = 1000

#: Replay page size. Matches ``Storage.event_since``'s default so a full-ring catch-up is a handful of
#: bounded queries that are written to the socket as they are read, rather than one 10 000-row list
#: held in memory while the client's TCP window drains.
REPLAY_PAGE = 500

#: The header an ``EventSource`` sends by itself on reconnect. Honouring it means a browser resumes
#: correctly even when the reconnect URL lost its ``?cursor=`` (§2.12).
LAST_EVENT_ID_HEADER = "Last-Event-ID"


class EventBus(Protocol):
    """The slice of the host's ``ctx.events`` this module uses.

    Structural, so nothing here imports ``kiro_crew`` — the app must stay importable (and testable)
    without the gateway, and the host's own bus is sync while everything around it is async.
    """

    def publish(self, event_type: str, data: dict[str, Any] | None = ...) -> None: ...


def host_event_for(event_type: str) -> str | None:
    """The host event name a Studio event mirrors to, or ``None`` when it is Studio-internal.

    Only the three names declared in ``app.json`` exist; anything else (``lease.updated``,
    ``activity.appended``, ``settings.updated``, …) is not mirrored, because publishing an undeclared
    name raises ``PermissionError`` inside the host and would produce nothing but a log line.
    """
    for prefix, host_name in HOST_EVENT_PREFIXES:
        if event_type.startswith(prefix):
            return host_name
    return None


def _data(payload: Mapping[str, Any]) -> str:
    """One JSON line for an SSE ``data:`` field.

    ``json.dumps`` always escapes ``\\n`` and ``\\r`` inside strings, so the single-line guarantee the
    frame format depends on holds for any payload. The separators are pinned because the frame bytes
    are asserted verbatim by the golden test — a formatting change is a wire change.
    """
    return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))


def _frame(seq: int, event_type: str, payload: Mapping[str, Any]) -> bytes:
    """The §2.12 frame: ``id``, ``event``, one ``data`` line, blank line."""
    return f"id: {seq}\nevent: {event_type}\ndata: {_data(payload)}\n\n".encode("utf-8")


def _lost_history(cursor: int, oldest_seq: int, newest_seq: int) -> bool:
    """True when ``cursor`` cannot be replayed from the ring, so the client must refetch.

    Two ways to lose: the cursor fell off the old end (the ring trimmed those rows), or it is *ahead*
    of the newest row — which happens when the storage file was rebuilt and ``seq`` restarted, and
    which must reset rather than resume, otherwise every new event would look like one already seen.
    """
    return cursor < oldest_seq - 1 or cursor > newest_seq


def _parse_cursor(raw: str | None) -> int | None:
    """A ``Last-Event-ID`` value, or ``None`` when absent or not a number.

    A malformed header is treated as no cursor (a fresh ``hello``) rather than as an error: the browser
    generated it from a frame this server sent, so the only realistic cause is a client from another
    version, and refusing the connection would leave that user with no live updates at all.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# subscriptions
# --------------------------------------------------------------------------- #


class Subscription:
    """One live reader's mailbox.

    Not a frozen dataclass (the rest of this codebase's public records are): ``lagged`` is mutated by
    the publisher and cleared by the reader, which is the whole point of the object — it is the one
    bit of shared state between a fast writer and a slow socket.
    """

    __slots__ = ("queue", "lagged", "_log")

    def __init__(self, log: "EventLog", maxsize: int | None = None) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_MAX if maxsize is None else int(maxsize)
        )
        self.lagged = False
        self._log = log

    def offer(self, record: EventRecord) -> bool:
        """Enqueue without blocking; ``False`` (and ``lagged``) when the reader has fallen behind."""
        try:
            self.queue.put_nowait(record)
        except asyncio.QueueFull:
            self.lagged = True
            return False
        return True

    def drain(self) -> int:
        """Discard everything buffered and return how much was dropped.

        Called when a ``reset`` is about to be sent: the frames in the mailbox describe changes the
        client is about to refetch wholesale, and sending them after the reset would make the UI
        re-apply a stale delta on top of fresh state.
        """
        dropped = 0
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return dropped
            dropped += 1

    def close(self) -> None:
        """Unsubscribe. Idempotent, so it is safe in a ``finally`` next to a cancelled task."""
        self._log._unsubscribe(self)


# --------------------------------------------------------------------------- #
# EventLog
# --------------------------------------------------------------------------- #


class EventLog:
    """Publishes Studio events and serves the SSE stream (§1.20, §2.12)."""

    def __init__(self, storage: "Storage", ctx_events: EventBus | None, clock: "Clock") -> None:
        self._storage = storage
        self._ctx_events = ctx_events
        self._clock = clock
        self._subs: set[Subscription] = set()
        #: One log line per process for each mirror failure mode. A denied event name repeats on every
        #: single publish; logging each one would bury the rest of the gateway's log in a defect that
        #: is fixed in ``app.json``, not at runtime.
        self._host_denied_logged = False
        self._host_failed_logged = False

    # ---- publishing ----

    async def publish(self, type: str, payload: dict) -> int:
        """Persist one event, wake live readers, mirror it to the host. Returns the ring ``seq``.

        The payload is copied shallowly so a caller may keep mutating its own dict; nested values are
        shared, which is safe because every payload in §1.20 is flat. The record handed to live readers
        is timestamped from the same injected clock ``Storage`` used for the row, so the ``at`` a
        subscriber sees and the ``at`` a replay reads back are the same string.
        """
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown Studio event type {type!r}; add it to events.EVENT_TYPES")
        body = dict(payload)
        seq = await asyncio.to_thread(self._storage.event_append, type, body)
        record = EventRecord(seq=seq, at=self._clock.iso(), type=type, payload=body)
        self._fan_out(record)
        self._mirror(record)
        return seq

    def _fan_out(self, record: EventRecord) -> None:
        """Offer the record to every subscriber. Iterates a copy: a reader may close mid-loop."""
        for sub in tuple(self._subs):
            already_lagged = sub.lagged
            if not sub.offer(record) and not already_lagged:
                # Only the transition is logged: a stuck reader overflows on every publish, and one
                # line per dropped event would be thousands of lines about a single closed tab.
                logger.info(
                    "aidlc-studio: SSE subscriber fell behind at seq %s; it will be told to resync",
                    record.seq,
                )

    def _mirror(self, record: EventRecord) -> None:
        """Best-effort copy onto the host's WebSocket bus.

        The Studio event name travels *inside* ``data`` rather than as the host event name: only three
        host names are declared, and the payloads themselves already use ``type`` for the action type,
        so flattening would collide.
        """
        bus = self._ctx_events
        if bus is None:
            return
        host_name = host_event_for(record.type)
        if host_name is None:
            return
        try:
            bus.publish(host_name, {"seq": record.seq, "event": record.type, "payload": dict(record.payload)})
        except PermissionError as exc:
            if not self._host_denied_logged:
                self._host_denied_logged = True
                logger.warning(
                    "aidlc-studio: host refused event %r (%s); Studio's own SSE stream is unaffected",
                    host_name,
                    exc,
                )
        except Exception as exc:  # a host-side defect must not fail the user's state change
            if not self._host_failed_logged:
                self._host_failed_logged = True
                logger.warning("aidlc-studio: host event bus failed for %r: %s", host_name, exc)

    # ---- reading ----

    async def since(self, cursor: int, limit: int = REPLAY_PAGE) -> list[EventRecord]:
        """Events after ``cursor``, oldest first (bounded by the ring and by ``limit``)."""
        return await asyncio.to_thread(self._storage.event_since, int(cursor), int(limit))

    async def bounds(self) -> tuple[int, int]:
        """``(oldest_seq, newest_seq)`` currently in the ring; ``(0, 0)`` when it is empty."""
        return await asyncio.to_thread(self._storage.event_bounds)

    async def poll(self, cursor: int | None = None, limit: int = REPLAY_PAGE) -> dict:
        """The ``GET /events/poll`` body (§2.5) — the fallback for a client without ``EventSource``.

        A missing cursor is a cold start: the client is about to fetch every resource anyway, so it
        gets the current cursor and no backlog. ``reset`` is true only when a cursor it *did* send
        cannot be honoured, which is the one case where the client has to discard local state.
        """
        oldest, newest = await self.bounds()
        page = max(1, min(int(limit), int(C.EVENTS_RING_SIZE)))
        if cursor is None:
            return {"events": [], "cursor": newest, "oldest_seq": oldest, "reset": False}
        start = int(cursor)
        if _lost_history(start, oldest, newest):
            return {"events": [], "cursor": newest, "oldest_seq": oldest, "reset": True}
        records = await self.since(start, page)
        return {
            "events": [r.to_json() for r in records],
            "cursor": records[-1].seq if records else start,
            "oldest_seq": oldest,
            "reset": False,
        }

    # ---- subscriptions ----

    def subscribe(self) -> Subscription:
        """Register a mailbox for live events. The caller must ``close()`` it (a ``finally``)."""
        sub = Subscription(self)
        self._subs.add(sub)
        return sub

    def _unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    # ---- the SSE stream (§2.12) ----

    async def stream(
        self,
        request: web.Request,
        *,
        cursor: int | None,
        heartbeat_secs: float | None = None,
    ) -> web.StreamResponse:
        """Serve ``GET /events`` until the client goes away, then return the finished response.

        Ordering matters twice here:

        * **Subscribe before reading the ring.** An event published between the replay query and the
          subscription would otherwise reach neither — the client would sit on a live connection
          missing one update with no way to notice. Subscribing first makes the overlap a *duplicate*
          instead of a gap, and ``last_seq`` drops the duplicate.
        * **``prepare()`` before any frame**, with the headers set before that: after ``prepare`` the
          headers are already on the wire and the host's security middleware can only ``setdefault``
          onto them (research 01 §1.4). And because ``prepare`` also spends the status line, the ring
          is read *before* it, so a storage failure is still answerable as an HTTP error.

        The greeting and the ``reset`` frame carry an ``id`` even though they are not ring rows. That
        is deliberate: ``EventSource`` remembers the last id it saw and replays it on reconnect, so
        without one a browser that reset would send the same dead cursor forever and reset in a loop.
        """
        beat = float(C.SSE_HEARTBEAT_SECS if heartbeat_secs is None else heartbeat_secs)
        if cursor is None:
            cursor = _parse_cursor(request.headers.get(LAST_EVENT_ID_HEADER))
        response = web.StreamResponse(status=200, headers=dict(SSE_HEADERS))
        sub = self.subscribe()
        try:
            # The ring is read while this is still an ordinary response. Studio's own designed degraded
            # path raises `storage_unavailable` out of exactly this query, and once `prepare()` has run
            # the 200 and the SSE headers are on the wire: the client would get a chunked body that
            # just stops, which it cannot tell from a healthy stream that ended, and the handler's
            # error envelope could no longer be sent. Reading first keeps the documented 503 sendable.
            oldest, newest = await self.bounds()
        except BaseException:
            # Nothing was prepared, so the streaming `finally` below never runs. The mailbox has to be
            # dropped here or the publisher keeps filling a subscription nobody will ever read.
            sub.close()
            raise
        try:
            await response.prepare(request)
            last_seq = await self._open(response, cursor, oldest, newest)
            while True:
                if sub.lagged:
                    last_seq = await self._resync(response, sub)
                    continue
                try:
                    record = await asyncio.wait_for(sub.queue.get(), beat)
                except asyncio.TimeoutError:
                    await response.write(SSE_PING)
                    continue
                if sub.lagged or record.seq <= last_seq:
                    # Either the mailbox overflowed while this record waited in it (the resync at the
                    # top of the next turn supersedes it), or the opening replay already sent it.
                    continue
                await response.write(_frame(record.seq, record.type, record.payload))
                last_seq = record.seq
        except asyncio.CancelledError:
            # The gateway is shutting this request task down. Cancellation has to keep travelling:
            # swallowing it makes the task look like it completed normally, so whoever cancelled it
            # (`on_shutdown`, a `wait_for`) is told the request finished when it was really killed.
            # The `finally` still drops the subscription, which is what §2.12 asks of this arm.
            raise
        except (ConnectionResetError, ConnectionAbortedError):
            # The reader is gone (a closed tab). Not an error, and there is nobody left to tell.
            pass
        except Exception:
            # Anything else, once the headers are out. There is no status left to send, so the only
            # honest ending is to stop writing: the frames already sent stay valid, the browser's
            # EventSource sees the stream end and reconnects with its own `Last-Event-ID`, and that
            # request gets a real HTTP status if the failure is still there. Letting it escape into
            # aiohttp instead would truncate the body with no status the client could act on.
            logger.exception("aidlc-studio: SSE stream ended early after an unexpected error")
        finally:
            sub.close()
        return response

    async def _open(
        self, response: web.StreamResponse, cursor: int | None, oldest: int, newest: int
    ) -> int:
        """Write the greeting or the replay; return the highest ``seq`` the client now has.

        The bounds are passed in rather than read here because the caller has to read them before
        ``prepare()``: a ring that cannot be read must still be able to become an HTTP error. They may
        therefore be a moment stale, which costs nothing — the subscription is already open, so an
        event published in that moment arrives live instead of being replayed.
        """
        if cursor is None:
            await response.write(
                _frame(
                    newest,
                    EVENT_HELLO,
                    {
                        "newest_seq": newest,
                        "server_time": self._clock.iso(),
                        "app_version": C.APP_VERSION,
                    },
                )
            )
            return newest
        if _lost_history(cursor, oldest, newest):
            await response.write(_frame(newest, EVENT_RESET, {"oldest_seq": oldest, "newest_seq": newest}))
            return newest
        last = cursor
        while True:
            page = await self.since(last, REPLAY_PAGE)
            for record in page:
                await response.write(_frame(record.seq, record.type, record.payload))
                last = record.seq
            if len(page) < REPLAY_PAGE:
                return last

    async def _resync(self, response: web.StreamResponse, sub: Subscription) -> int:
        """Tell a lagged client to refetch everything, and forget what it missed.

        The mailbox is emptied *before* the bounds are read, so ``newest_seq`` can only be at or ahead
        of the last dropped event — the opposite order would let an event published in between be both
        dropped and outside the window the client is told to resume from.
        """
        dropped = sub.drain()
        sub.lagged = False
        oldest, newest = await self.bounds()
        logger.info(
            "aidlc-studio: SSE reader resynced after dropping %s buffered event(s); newest seq %s",
            dropped,
            newest,
        )
        await response.write(_frame(newest, EVENT_RESET, {"oldest_seq": oldest, "newest_seq": newest}))
        return newest

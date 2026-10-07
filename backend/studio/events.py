"""Studio's live updates: one in-memory ring per process, SSE readers over it, a best-effort host mirror.

Payloads are references (ids, enums, counts), never content (contracts §1.20): the client refetches the
resource, and nothing broadcast to every dashboard tab carries human text, credentials or paths.

``seq`` starts at this process's monotonic clock in microseconds and counts up by one per event, so every
cursor an earlier process handed out lies below this ring and resets instead of resuming into another
process's numbering. ``0`` is the conventional "from the beginning" cursor and means this process's first
event. A reader is woken rather than fed through a mailbox: one that falls behind the ring gets ``reset``
instead of a partial history, and a stuck tab costs no memory.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from aiohttp import web

from . import constants as C

logger = logging.getLogger("kirocrew.app.aidlc-studio")

#: Closed on purpose: an unlisted type would be silently ignored by the UI's reducer, so ``publish``
#: refuses it. ``hello`` is a per-connection greeting, not an event.
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
    "reset",
)
EVENT_HELLO = "hello"
EVENT_RESET = "reset"

#: Studio event prefix -> one of the three host names ``app.json`` declares; publishing an undeclared
#: name raises inside the host, so everything else stays Studio-internal.
HOST_EVENT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("action.", C.HOST_EVENT_ACTION),
    ("repo.", C.HOST_EVENT_REPO),
    ("intent.", C.HOST_EVENT_REPO),
    ("transaction.", C.HOST_EVENT_TRANSACTION),
)

#: Set before ``prepare()``: the host's middleware only ``setdefault``s headers (research 01 §1.4).
SSE_HEADERS: dict[str, str] = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
#: A comment whose only job is to make a dead connection fail a write.
SSE_PING = b": ping\n\n"
LAST_EVENT_ID_HEADER = "Last-Event-ID"


class EventBus(Protocol):
    """The slice of the host's ``ctx.events`` used here (structural: no ``kiro_crew`` import)."""

    def publish(self, event_type: str, data: dict | None = ...) -> None: ...


@dataclass(frozen=True, slots=True)
class EventRecord:
    """One published event; ``seq`` is the SSE ``id`` a client resumes from."""

    seq: int
    at: str
    type: str
    payload: dict = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"seq": self.seq, "at": self.at, "type": self.type, "payload": self.payload}


def host_event_for(event_type: str) -> str | None:
    """The declared host event a Studio event mirrors to, or ``None`` when it is Studio-internal."""
    return next((host for prefix, host in HOST_EVENT_PREFIXES if event_type.startswith(prefix)), None)


def _frame(seq: int, event_type: str, payload: Mapping[str, Any]) -> bytes:
    """The §2.12 frame. ``json.dumps`` escapes newlines, so ``data`` is one line; the separators are pinned
    because the frame bytes are a wire contract with ``ui/src/lib/sse.ts``."""
    data = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    return f"id: {seq}\nevent: {event_type}\ndata: {data}\n\n".encode("utf-8")


def _parse_cursor(raw: str | None) -> int | None:
    """A ``Last-Event-ID``, or ``None``: a malformed one is a cold start rather than a refused stream."""
    try:
        return int(raw.strip()) if raw and raw.strip() else None
    except ValueError:
        return None


class EventLog:
    """Publishes Studio events and serves ``GET /events`` (§2.12) and ``GET /events/poll`` (§2.5)."""

    def __init__(self, storage: Any, ctx_events: EventBus | None, clock: Any, *, ring_size: int | None = None) -> None:
        # ``storage`` is unused since the ring moved into memory; the parameter keeps every constructor valid.
        self._ctx_events = ctx_events
        self._clock = clock
        self._base = self._seq = int(clock.monotonic() * 1_000_000)
        self._ring: collections.deque[EventRecord] = collections.deque(
            maxlen=int(C.EVENTS_RING_SIZE if ring_size is None else ring_size)
        )
        self._readers: set[asyncio.Event] = set()
        #: A denied name repeats on every publish; one line per failure mode keeps the gateway log readable.
        self._host_logged: set[str] = set()

    async def publish(self, type: str, payload: dict) -> int:
        """Append to the ring, wake the readers, mirror to the host; returns the new ``seq``.

        The payload is copied shallowly so a caller may keep mutating its dict (every §1.20 payload is flat).
        """
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown Studio event type {type!r}; add it to events.EVENT_TYPES")
        self._seq += 1
        record = EventRecord(seq=self._seq, at=self._clock.iso(), type=type, payload=dict(payload))
        self._ring.append(record)
        for wake in tuple(self._readers):
            wake.set()
        self._mirror(record)
        return record.seq

    def _mirror(self, record: EventRecord) -> None:
        """Best-effort copy onto the host bus, Studio's type inside ``data`` (payloads already use ``type``).

        The host forwards nothing to app frontends today (A07), so a denied name or a broken bus must never
        fail the state change that published.
        """
        host_name = host_event_for(record.type) if self._ctx_events is not None else None
        if host_name is None:
            return
        try:
            self._ctx_events.publish(host_name, {"seq": record.seq, "event": record.type, "payload": dict(record.payload)})
        except Exception as exc:  # noqa: BLE001 - a host-side defect must not fail the user's change
            mode = "denied" if isinstance(exc, PermissionError) else "failed"
            if mode not in self._host_logged:
                self._host_logged.add(mode)
                if mode == "denied":
                    logger.warning("aidlc-studio: host refused event %r (%s); Studio's own SSE stream is unaffected",
                                   host_name, exc)
                else:
                    logger.warning("aidlc-studio: host event bus failed for %r: %s", host_name, exc)

    # ---- reading ----

    def bounds(self) -> tuple[int, int]:
        """``(oldest_seq, newest_seq)``. An empty ring reports ``(newest + 1, newest)``, so ``oldest - 1`` is
        always the newest cursor that has missed nothing."""
        return (self._ring[0].seq if self._ring else self._seq + 1, self._seq)

    def _resumable(self, cursor: int) -> bool:
        """Every event after ``cursor`` is still in the ring. Below it the client lost events (trimmed, or an
        earlier process); above it the cursor was never issued here."""
        oldest, newest = self.bounds()
        return oldest - 1 <= (self._base if cursor == 0 else cursor) <= newest

    def _after(self, cursor: int, limit: int | None = None) -> list[EventRecord]:
        records = [record for record in self._ring if record.seq > cursor]
        return records if limit is None else records[: max(1, int(limit))]

    async def since(self, cursor: int, limit: int = 500) -> list[EventRecord]:
        """Events after ``cursor`` still in the ring, oldest first."""
        return self._after(int(cursor), limit)

    async def poll(self, cursor: int | None = None, limit: int = 500) -> dict:
        """The ``GET /events/poll`` body. No cursor is a cold start (the client fetches everything anyway);
        ``reset`` is true only when a cursor it did send cannot be resumed."""
        oldest, newest = self.bounds()
        if cursor is None or not self._resumable(int(cursor)):
            return {"events": [], "cursor": newest, "oldest_seq": oldest, "reset": cursor is not None}
        records = self._after(int(cursor), min(int(limit), int(C.EVENTS_RING_SIZE)))
        return {
            "events": [record.to_json() for record in records],
            "cursor": records[-1].seq if records else int(cursor),
            "oldest_seq": oldest,
            "reset": False,
        }

    async def stream(
        self, request: web.Request, *, cursor: int | None, heartbeat_secs: float | None = None
    ) -> web.StreamResponse:
        """Serve ``GET /events`` until the client goes away.

        ``hello`` and ``reset`` carry an ``id`` although they are not ring rows: ``EventSource`` replays the
        last id it saw, so without one a browser that reset would resend the same dead cursor forever.
        """
        beat = float(C.SSE_HEARTBEAT_SECS if heartbeat_secs is None else heartbeat_secs)
        if cursor is None:
            cursor = _parse_cursor(request.headers.get(LAST_EVENT_ID_HEADER))
        response = web.StreamResponse(status=200, headers=dict(SSE_HEADERS))
        wake = asyncio.Event()
        self._readers.add(wake)
        try:
            await response.prepare(request)
            last = self._seq if cursor is None else cursor
            if cursor is None:
                hello = {"newest_seq": last, "server_time": self._clock.iso(), "app_version": C.APP_VERSION}
                await response.write(_frame(last, EVENT_HELLO, hello))
            while True:
                wake.clear()  # before reading the ring: a publish from here on wakes the wait below
                if not self._resumable(last):
                    oldest, last = self.bounds()
                    await response.write(_frame(last, EVENT_RESET, {"oldest_seq": oldest, "newest_seq": last}))
                for record in self._after(last):
                    await response.write(_frame(record.seq, record.type, record.payload))
                    last = record.seq
                try:
                    await asyncio.wait_for(wake.wait(), beat)
                except asyncio.TimeoutError:
                    await response.write(SSE_PING)
        except asyncio.CancelledError:
            # Shutdown cancelled the request task; swallowing it would tell the canceller the task finished.
            raise
        except (ConnectionResetError, ConnectionAbortedError):
            pass  # the reader is gone (a closed tab)
        except Exception:
            # The headers are out, so there is no status left to send: stop writing, and the browser
            # reconnects with its own Last-Event-ID instead of reading a body truncated mid-frame.
            logger.exception("aidlc-studio: SSE stream ended early after an unexpected error")
        finally:
            self._readers.discard(wake)
        return response

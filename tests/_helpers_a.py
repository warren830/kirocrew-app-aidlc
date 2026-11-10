"""Helpers shared by the decision-lane tests (actions, reconciler, sessions, leases, notifications)."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import os
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import pytest


def aio(fn: Callable) -> Callable:
    """Run an async test body on a fresh loop (no pytest-asyncio in the pinned venv)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


@contextlib.contextmanager
def refused(errors: Any, code: str, exc_type: type | None = None) -> Iterator[Any]:
    """``pytest.raises(StudioError)`` whose ``code`` must equal ``code``; yields the ``ExceptionInfo``."""
    with pytest.raises(exc_type or errors.StudioError) as exc:
        yield exc
    assert exc.value.code == code, (exc.value.code, getattr(exc.value, "details", None))


def stamp_human_turn(record: Path, ts: str, ns: int | None = 2_000_000_000) -> Path:
    """Write ``.aidlc-human-turn`` the way the adapter does on a real prompt; ``ns`` pins its mtime."""
    marker = record / ".aidlc-human-turn"
    marker.write_text(ts + "\n")
    if ns is not None:
        os.utime(marker, ns=(ns, ns))
    return marker


def fail_bounded_read(monkeypatch: pytest.MonkeyPatch, reader: Any, path: Path) -> None:
    """Make ``security.bounded_read`` answer ``None`` (unreadable) for ``path`` only."""
    security = __import__(reader.__module__.rsplit(".", 1)[0] + ".security", fromlist=["bounded_read"])
    read = security.bounded_read
    monkeypatch.setattr(security, "bounded_read", lambda p, cap: None if p == path else read(p, cap))


def truncate_audit(monkeypatch: pytest.MonkeyPatch, reader: Any, tail_bytes: int = 1500) -> None:
    """Every audit read keeps only a ``tail_bytes`` tail, so the reader sees a partial audit."""
    read = reader.read_audit
    monkeypatch.setattr(reader, "read_audit", lambda *a, **kw: read(*a, tail_bytes=tail_bytes))


def dispatches(storage: Any, action_id: str) -> int:
    """How many times an action was ever dispatched: its edges into ``Delivering``."""
    return [row["to_status"] for row in storage.select("action_transitions", {"action_id": action_id})].count(
        "Delivering")


def illegal_edges(storage: Any, action_id: str, allowed: Mapping[str, Sequence[str]]) -> list[tuple[str, str]]:
    """The action's recorded transitions that ``allowed`` (``ALLOWED_TRANSITIONS``) does not declare."""
    edges = ((row["from_status"], row["to_status"])
             for row in storage.select("action_transitions", {"action_id": action_id}))
    return [edge for edge in edges if edge[1] not in allowed[edge[0]]]


def unreadable_slots(monkeypatch: pytest.MonkeyPatch, host: Any) -> None:
    """A slot table Studio cannot read (§7.0 drift): every ``get_slot`` raises."""

    def unreadable(name: str) -> Any:
        raise TypeError("get_slot moved")

    monkeypatch.setattr(host, "get_slot", unreadable)

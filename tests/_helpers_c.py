"""Helpers shared by the installer, workspace and route test files (group C)."""

from __future__ import annotations

import inspect
import threading
from typing import Any, Callable

import pytest

from conftest import fork_process


def die_in_child(target: Callable[[], Any], *, exit_code: int = 77, timeout: float = 15) -> None:
    """Run ``target`` in a forked child that must die with ``exit_code``: real process death, not a fake one."""
    process = fork_process(target)
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("isolated child process did not exit")
    assert process.exitcode == exit_code


def after(monkeypatch: pytest.MonkeyPatch, owner: Any, name: str, effect: Callable[[], Any]) -> None:
    """Patch ``owner.name`` so ``effect()`` runs right after the real call returns (sync or async)."""
    original = getattr(owner, name)
    if inspect.iscoroutinefunction(original):
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = await original(*args, **kwargs)
            effect()
            return result
    else:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            effect()
            return result
    monkeypatch.setattr(owner, name, wrapped)


class ThreadRecordingStore:
    """Delegates to the real ``Storage`` and records each method called and the thread it arrived on."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.threads: list[str] = []
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if not callable(target):
            return target

        def recorded(*args: Any, **kwargs: Any) -> Any:
            self.threads.append(threading.current_thread().name)
            self.calls.append(name)
            return target(*args, **kwargs)

        return recorded


def capture_run(monkeypatch: pytest.MonkeyPatch, installer: Any) -> tuple[Callable[..., Any], list[tuple]]:
    """Replace ``installer.run`` with a recorder; returns the real ``run`` and the recorded calls to replay."""
    original = installer.run
    calls: list[tuple] = []

    async def record_run(repo: Any, kind: str, *, plan_digest: str, transaction_id: str | None = None) -> None:
        calls.append((repo, kind, plan_digest, transaction_id))

    monkeypatch.setattr(installer, "run", record_run)
    return original, calls

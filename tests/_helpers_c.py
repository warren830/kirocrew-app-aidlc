"""Helpers shared by the installer, workspace and route test files (group C)."""

from __future__ import annotations

import inspect
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

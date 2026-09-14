"""Explicit, one-level directory browsing for the gateway's owner."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .errors import StudioError

MAX_DIRECTORY_RESULTS = 200
MAX_DIRECTORY_ENTRIES = 2000


def browse_directory(path: Any = None) -> dict[str, Any]:
    if path is None:
        root = Path.home()
    else:
        if (not isinstance(path, str) or not path or len(path) > 4096
                or "\x00" in path or not (path.startswith("/") or path.startswith("~"))):
            raise StudioError("bad_path", "choose an absolute directory path")
        root = Path(path)
    directories = []
    truncated = False
    try:
        root = root.expanduser().resolve(strict=True)
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DIRECTORY_ENTRIES:
                    truncated = True
                    break
                try:
                    is_directory = entry.is_dir()
                except OSError:
                    continue
                if not is_directory:
                    continue
                if len(directories) >= MAX_DIRECTORY_RESULTS:
                    truncated = True
                    break
                directories.append({"name": entry.name, "path": str(root / entry.name)})
    except (OSError, RuntimeError) as exc:
        raise StudioError("bad_path", "this directory cannot be opened",
                          details={"path": str(root)}) from exc
    return {
        "path": str(root),
        "parent": str(root.parent) if root.parent != root else None,
        "directories": sorted(directories, key=lambda d: (d["name"].casefold(), d["name"])),
        "truncated": truncated,
    }

#!/usr/bin/env python3
"""Count code, comment/docstring and blank lines, so every slimming step can be measured.

    scripts/loc.py              # summary for backend/, tests/, ui/src
    scripts/loc.py --modules    # plus the backend modules, largest first

Python docstrings count as comments. TypeScript lines are split into code, `//`/`/* */` comments and blank.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _python(path: Path) -> dict[str, int]:
    src = path.read_text(encoding="utf-8")
    lines = src.split("\n")
    kind = ["blank" if not line.strip() else "comment" if line.strip().startswith("#") else "code" for line in lines]
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for i in range(node.lineno - 1, node.end_lineno or node.lineno):
                    if kind[i] == "code":
                        kind[i] = "comment"
    return {k: kind.count(k) for k in ("code", "comment", "blank")}


def _typescript(path: Path) -> dict[str, int]:
    counts = {"code": 0, "comment": 0, "blank": 0}
    in_block = False
    for line in path.read_text(encoding="utf-8").split("\n"):
        s = line.strip()
        if in_block:
            counts["comment"] += 1
            in_block = "*/" not in s
        elif not s:
            counts["blank"] += 1
        elif s.startswith("//"):
            counts["comment"] += 1
        elif s.startswith("/*"):
            counts["comment"] += 1
            in_block = "*/" not in s
        else:
            counts["code"] += 1
    return counts


def count(base: Path, suffixes: tuple[str, ...], skip: tuple[str, ...] = ()) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file() or path.suffix not in suffixes or any(s in rel for s in skip):
            continue
        out[rel] = _python(path) if path.suffix == ".py" else _typescript(path)
    return out


def total(rows: dict[str, dict[str, int]]) -> dict[str, int]:
    t = {"code": 0, "comment": 0, "blank": 0}
    for row in rows.values():
        for k in t:
            t[k] += row[k]
    return t


def main() -> None:
    groups = {
        "backend": count(ROOT / "backend", (".py",), ("__pycache__",)),
        "tests": count(ROOT / "tests", (".py",), ("__pycache__", "tests/fixtures/")),
        "ui/src": count(ROOT / "ui" / "src", (".ts", ".tsx"), (".test.", "/test/", ".generated.")),
        "ui tests": count(ROOT / "ui" / "src", (".ts", ".tsx"), (".generated.",)),
    }
    ui_all = groups.pop("ui tests")
    groups["ui tests"] = {k: v for k, v in ui_all.items() if ".test." in k or "/test/" in k}
    print(f"{'':10} {'code':>7} {'comment':>8} {'blank':>7} {'total':>7}")
    for name, rows in groups.items():
        t = total(rows)
        print(f"{name:10} {t['code']:7d} {t['comment']:8d} {t['blank']:7d} {sum(t.values()):7d}")
    if "--modules" in sys.argv:
        print()
        for rel, row in sorted(groups["backend"].items(), key=lambda kv: -kv[1]["code"]):
            print(f"{row['code']:6d} {row['comment']:6d} {row['blank']:6d}  {rel}")


if __name__ == "__main__":
    main()

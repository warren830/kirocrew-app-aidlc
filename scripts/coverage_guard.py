#!/usr/bin/env python3
"""Fail when a test change stops executing backend code that a baseline run executed.

    python -m pytest tests -n 8 --cov=backend --cov-branch --cov-report=json:/tmp/cov-new.json --cov-report=
    scripts/coverage_guard.py BASELINE.json /tmp/cov-new.json

Both reports must come from the same backend code: lines are compared by number. Every line and every branch
arc the baseline executed must still be executed; anything newly covered is fine. Prints the regressions per
file and exits 1 when there are any.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(path: str) -> dict[str, tuple[set[int], set[tuple[int, int]]]]:
    files = json.loads(Path(path).read_text("utf-8"))["files"]
    return {
        name: (set(data.get("executed_lines", ())), {tuple(arc) for arc in data.get("executed_branches", ())})
        for name, data in files.items()
    }


def main(baseline: str, current: str) -> int:
    before, after = _load(baseline), _load(current)
    lost_total = 0
    for name in sorted(before):
        lines, arcs = before[name]
        now_lines, now_arcs = after.get(name, (set(), set()))
        lost_lines, lost_arcs = sorted(lines - now_lines), sorted(arcs - now_arcs)
        if lost_lines or lost_arcs:
            lost_total += len(lost_lines) + len(lost_arcs)
            print(f"{name}: {len(lost_lines)} lines, {len(lost_arcs)} branches no longer executed")
            if lost_lines:
                print(f"  lines: {lost_lines[:40]}{' …' if len(lost_lines) > 40 else ''}")
            if lost_arcs:
                print(f"  branches: {lost_arcs[:20]}{' …' if len(lost_arcs) > 20 else ''}")
    if lost_total:
        print(f"coverage_guard: {lost_total} regressions")
        return 1
    print("coverage_guard: every baseline line and branch is still executed")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))

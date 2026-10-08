"""Diff two harness outputs view by view, ignoring values that differ between any two runs."""

import json
import sys

VOLATILE = {"scanned_at", "last_seen", "checked_at", "generated_at", "took_ms", "now", "mtime", "mtime_ns",
            "free_space_bytes", "last_tick_at", "last_tick_ms", "boot_id", "started_at", "migrated_at",
            "captured_at", "acquired_at", "heartbeat_at", "updated_at"}


def diff(a, b, path, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in VOLATILE:
                continue
            if k not in a:
                out.append(f"{path}.{k}: only in v2 = {json.dumps(b[k])[:120]}")
            elif k not in b:
                out.append(f"{path}.{k}: only in v1 = {json.dumps(a[k])[:120]}")
            else:
                diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: list length v1={len(a)} v2={len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(f"{path}: v1={json.dumps(a)[:100]} v2={json.dumps(b)[:100]}")


v1, v2 = (json.load(open(p)) for p in sys.argv[1:3])
for name in sorted(set(v1) | set(v2)):
    if name not in v1 or name not in v2:
        print(f"== {name}: only in {'v2' if name not in v1 else 'v1'}")
        continue
    out = []
    diff(v1[name], v2[name], "", out)
    print(f"== {name}: {'identical' if not out else f'{len(out)} differences'}")
    for line in out[:25]:
        print("   ", line)
    if len(out) > 25:
        print(f"    … {len(out) - 25} more")

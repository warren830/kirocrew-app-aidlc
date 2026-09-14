#!/usr/bin/env python3
"""Build ``payload/manifest.json`` for the bundled AI-DLC Kiro harness payload.

The manifest is the installer's single source of truth (PRD FR-INST-003/-008,
§12.4): every file the bundled AI-DLC distribution ships is listed with its
SHA-256 and an *ownership kind* that decides how the Studio installer treats it.

Ownership kinds
---------------
framework
    Receipt-owned framework file. Installed verbatim, verified by digest, retired
    on upgrade only when live bytes still match the old receipt (FR-INST-010).
framework-mutable
    Receipt-owned, but AI-DLC itself rewrites it at runtime (``aidlc-utility.ts
    space <name>`` re-points ``resources`` in every ``.kiro/agents/*.json``).
    Drift in these files is reported as *engine-modified*, not as a user conflict.
merge
    Project-owned merge target (``.kiro/settings/cli.json``, ``AGENTS.md``,
    ``.gitignore``, ``.kiro/tools/data/scope-grid.json``, ``harness.json``).
    Studio only ever merges the managed fragment and receipts the fragment's
    canonical digest, never the whole file (FR-INST-006). A file belongs here
    when AI-DLC — or the user through AI-DLC — adds content to the *installed
    copy* that Studio must keep: ``framework`` would call that drift and block
    the next install, and ``framework-mutable`` would overwrite it.
shell
    Workspace shell under ``aidlc/`` (memory seed, ``active-space``). Created only
    when absent, never owned, never retired, never overwritten (FR-INST-005).

Usage::

    python3 scripts/build_payload_manifest.py --source-commit <sha> --version 2.3.0

Run from the repo root. Deterministic: sorted paths, stable JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_DIR = ROOT / "payload" / "aidlc-kiro"
MANIFEST = ROOT / "payload" / "manifest.json"

# Order matters: first match wins. Derived from the upstream harness manifest
# (``harness/kiro/manifest.ts``): ``coreDirs`` + ``harnessFiles`` are framework,
# ``settings/*``+``AGENTS.md``+``.gitignore`` are project-owned merge targets, and
# ``aidlc/`` is the workspace shell the engine (not Studio) owns.
OWNERSHIP_RULES: list[tuple[str, str]] = [
    ("aidlc/", "shell"),
    (".kiro/settings/", "merge"),
    ("AGENTS.md", "merge"),
    (".gitignore", "merge"),
    # Exact path, and it MUST stay ahead of the ``.kiro/`` catch-all below, which
    # would otherwise claim it as framework. AI-DLC's own composer agent appends a
    # composed-scope key to the copy of this file inside the user's repository
    # (``/aidlc compose`` writes the new scope's column next to the shipped ones),
    # so the live file legitimately differs from the payload in a repository nobody
    # has hand-edited. As framework that drift resolves to ``owned_modified``, which
    # is blocking with no force path — a composed repository could never be
    # installed into or upgraded again. As a merge target Studio owns only the
    # scopes it ships and the composed one survives (FR-INST-006).
    (".kiro/tools/data/scope-grid.json", "merge"),
    # Same shape, a different writer: ``aidlc-utility.ts select-plugins`` (the
    # ``/aidlc plugin select`` verb, which works in a stock install with no plugins
    # at all) appends a ``plugins`` array to the copy of this file in the user's
    # repository, and ``documentExtractors`` / ``runnerFrontmatterAdditions`` are
    # documented operator config a human edits there. Studio owns the harness
    # IDENTITY keys it ships and nothing else. Not ``framework-mutable``: that would
    # let an upgrade overwrite the file and silently re-enable plugins the user
    # disabled.
    (".kiro/tools/data/harness.json", "merge"),
    (".kiro/agents/", "framework-mutable"),
    (".kiro/", "framework"),
]

#: Managed fragments inside project-owned JSON files. Studio writes only these keys,
#: receipts each key's canonical value digest, and preserves every other key
#: (including secrets, and including a composed scope) byte-for-byte (FR-INST-006).
#: ``None`` means "derive the key list from the payload file itself" — see
#: ``derive_managed_keys``, which is what keeps a payload that starts shipping one
#: more server or one more scope from needing an edit here.
JSON_MERGE_TARGETS: dict[str, list[str] | None] = {
    ".kiro/settings/cli.json": ["chat.defaultAgent", "chat.modelDefaults"],
    # AI-DLC ships an empty/registry-shaped mcp.json; only the servers it declares
    # are managed, so the key list is computed from the payload file itself below.
    ".kiro/settings/mcp.json": None,
    ".kiro/tools/data/scope-grid.json": None,
    ".kiro/tools/data/harness.json": None,
}


def derive_managed_keys(rel: str, data: object) -> list[str]:
    """The managed key list read out of the payload file, for a ``None`` entry above.

    Sorted, because the list is part of the receipt's ``fragment_key`` and of the
    canonical fragment digest: deriving it from a dict's iteration order would make
    the manifest depend on how upstream happened to order the file that day.
    """
    if rel == ".kiro/settings/mcp.json":
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            return []
        return [f"mcpServers.{name}" for name in sorted(servers)]
    if rel == ".kiro/tools/data/scope-grid.json":
        # The grid is a flat object keyed by scope name (``feature``, ``bugfix``,
        # ``classic``, …), one entry per scope AI-DLC ships. Each shipped name is one
        # managed key, spelled exactly as the file spells it; every other top-level
        # key — i.e. a scope the composer added in the user's repository — is left
        # alone by the installer because it is not on this list.
        return sorted(data) if isinstance(data, dict) else []
    if rel == ".kiro/tools/data/harness.json":
        # The identity keys AI-DLC ships (``name``, ``harnessDir``, ``rulesSubdir``)
        # and only those: ``plugins`` and the operator-config keys are absent from the
        # payload, so they never become managed. Derived rather than written out here
        # for the same reason as mcp.json — but a payload that starts shipping
        # ``documentExtractors`` would hand Studio a key humans edit, so a bump that
        # changes this list is one to read before it ships.
        return sorted(data) if isinstance(data, dict) else []
    raise SystemExit(f"no managed-key derivation for {rel}")


def classify(rel: str) -> str:
    for prefix, kind in OWNERSHIP_RULES:
        if rel == prefix or rel.startswith(prefix):
            return kind
    raise SystemExit(f"unclassified payload path: {rel}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="AI-DLC framework version, e.g. 2.3.0")
    ap.add_argument("--source-commit", required=True, help="upstream commit the payload was extracted from")
    ap.add_argument("--source-ref", default="", help="upstream tag/ref, e.g. v2.3.0")
    ap.add_argument(
        "--state-versions",
        default="7",
        help="comma-separated AI-DLC State Version values this payload writes/accepts",
    )
    args = ap.parse_args(argv)

    if not PAYLOAD_DIR.is_dir():
        print(f"payload dir missing: {PAYLOAD_DIR}", file=sys.stderr)
        return 2

    files = []
    digest_all = hashlib.sha256()
    for path in sorted(p for p in PAYLOAD_DIR.rglob("*") if p.is_file()):
        rel = path.relative_to(PAYLOAD_DIR).as_posix()
        digest = sha256_file(path)
        kind = classify(rel)
        files.append(
            {
                "path": rel,
                "sha256": digest,
                "size": path.stat().st_size,
                "ownership": kind,
            }
        )
        digest_all.update(f"{rel}\0{digest}\n".encode("utf-8"))

    graph_path = PAYLOAD_DIR / ".kiro" / "tools" / "data" / "stage-graph.json"
    graph = json.loads(graph_path.read_text("utf-8")) if graph_path.is_file() else []
    stage_count = len(graph) if isinstance(graph, list) else None

    # Managed JSON fragment keys: explicit for cli.json, derived from the payload file
    # for mcp.json and scope-grid.json so a payload that starts shipping one more
    # server or one more scope is receipted without editing this script.
    merge_targets: dict[str, dict] = {}
    for rel, keys in JSON_MERGE_TARGETS.items():
        path = PAYLOAD_DIR / rel
        if not path.is_file():
            continue
        if keys is None:
            try:
                data = json.loads(path.read_text("utf-8"))
            except ValueError:
                data = {}
            keys = derive_managed_keys(rel, data)
            if not keys:
                # The installer degrades a json-managed-keys target with an empty key
                # list (``merge_keys_missing``), which would take the whole app down
                # rather than one file. A payload that reshaped one of these files is a
                # bump to look at by hand, so stop here instead of shipping it.
                print(f"no managed keys derived for {rel}", file=sys.stderr)
                return 2
        merge_targets[rel] = {"strategy": "json-managed-keys", "managedKeys": keys}
        if rel == ".kiro/settings/cli.json":
            # Model IDs contain dots: keep them as literal map keys, never dotted managed paths.
            # This exception applies only to model defaults, not to MCP servers or their env/secrets.
            def unique(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj:
                        raise ValueError("duplicate JSON key")
                    obj[key] = value
                return obj

            try:
                data = json.loads(path.read_text("utf-8"), object_pairs_hook=unique)
                models = data.get("chat.modelDefaults") if isinstance(data, dict) else None
                nested = data.get("chat") if isinstance(data, dict) else None
                if (
                    not isinstance(models, dict)
                    or any(not key or not isinstance(value, dict) for key, value in models.items())
                    or (isinstance(nested, dict) and "modelDefaults" in nested)
                ):
                    raise ValueError("expected unambiguous per-model objects")
            except (ValueError, UnicodeDecodeError):
                print(f"invalid model defaults in {rel}", file=sys.stderr)
                return 2
            merge_targets[rel]["managedMapEntries"] = {"chat.modelDefaults": sorted(models)}
    merge_targets["AGENTS.md"] = {
        "strategy": "append-fenced-block",
        "markerStart": "<!-- aidlc-studio:managed:start -->",
        "markerEnd": "<!-- aidlc-studio:managed:end -->",
    }
    merge_targets[".gitignore"] = {"strategy": "append-missing-lines"}

    manifest = {
        "schema": "aidlc-studio-payload/1",
        "harness": "kiro",
        "engineVersion": args.version,
        "source": {
            "repository": "https://github.com/awslabs/aidlc-workflows",
            "ref": args.source_ref,
            "commit": args.source_commit,
            "distPath": "dist/kiro",
            "license": "MIT-0",
        },
        "compatibleStateVersions": [int(v) for v in args.state_versions.split(",") if v.strip()],
        "stageCount": stage_count,
        "payloadDigest": digest_all.hexdigest(),
        "fileCount": len(files),
        "mergeTargets": merge_targets,
        "files": files,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    kinds: dict[str, int] = {}
    for f in files:
        kinds[f["ownership"]] = kinds.get(f["ownership"], 0) + 1
    print(f"wrote {MANIFEST} ({len(files)} files, stages={stage_count}) {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

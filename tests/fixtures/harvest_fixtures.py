#!/usr/bin/env python3
"""Harvest scrubbed AI-DLC test fixtures from the local installs.

Re-runnable and idempotent. Usage:

    AIDLC_FIXTURE_WS=<your workspace dir> python3 tests/fixtures/harvest_fixtures.py

where <workspace dir> is the directory that contains the three sources (no machine path is hard-coded here on purpose):
  * <ws>/DevDelta                                  (AI-DLC 2.6.2 on Kiro, State Version 8, 33 stages)
  * <ws>/aidlc-workflows @ tag v2.3.0, dist/kiro   (AI-DLC 2.3.0, State Version 7, 32 stages;
    this is exactly what <project>/payload/aidlc-kiro held until it was re-pinned to 2.6.2 on 2026-09-04)
  * <ws>/devlake                                   (AI-DLC 2.1.1 on Kiro, State Version 7, 32 stages)
Writes ONLY under <project>/tests/fixtures/{aidlc-2.6.2-devdelta,aidlc-2.3.0-payload,aidlc-older}.

Placeholder vocabulary (shared with the READMEs and FORMAT-NOTES): /FIXTURE/repo = the source repository root,
/FIXTURE/ws = <ws>, /FIXTURE/home = the account home directory.

Scrubbing rules (applied to every copied text file, in this order):
  1. repo_prefix     <source repo abs path>            -> /FIXTURE/repo
  2. ws_prefix       <ws>/  and  ~/<basename(ws)>/     -> /FIXTURE/ws/     (sibling checkouts in the same workspace)
  2b. home_prefix    /Users/<account>/  and  <home>/   -> /FIXTURE/home/   (any other absolute home path)
  3. email           <anything>@<host>.<tld>           -> REDACTED
  4. aws_key         AKIA…/ASIA… (16 uppercase alnum)  -> REDACTED
  5. slack_token     xox[baprs]-…                      -> REDACTED
  6. github_token    gh[pousr]_…                       -> REDACTED
  7. openai_key      sk-… (20+)                        -> REDACTED
  8. private_key     -----BEGIN … PRIVATE KEY----- …   -> REDACTED
  9. keyword_secret  (token|secret|password|passwd|api[_-]?key|bearer)<sep><16+ alnum> -> keyword kept, value REDACTED
Everything else is byte-faithful (UTF-8 decode -> regex -> UTF-8 encode; unchanged content re-encodes to identical bytes).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent  # <project>/tests/fixtures
PROJECT = HERE.parent.parent
_ws_env = os.environ.get("AIDLC_FIXTURE_WS", "").strip()
if not _ws_env:
    sys.exit("set AIDLC_FIXTURE_WS to the workspace directory that contains DevDelta/, devlake/ and aidlc-workflows/")
WS_ROOT = Path(_ws_env).resolve()
WS_PLACEHOLDER = "/FIXTURE/ws"
SRC_DEVDELTA = WS_ROOT / "DevDelta"
SRC_DEVLAKE = WS_ROOT / "devlake"
# The 2.3.0 engine data comes from the local aidlc-workflows checkout at tag v2.3.0. It used to be what
# payload/aidlc-kiro held; on 2026-09-04 the payload was re-pinned to 2.6.2 (see payload/manifest.json),
# so the payload directory can no longer serve as the 2.3.0 source.
GIT_AIDLC = WS_ROOT / "aidlc-workflows"
GIT_TAG = "v2.3.0"
GIT_DIST = "dist/kiro"
STAGING_230 = HERE / ".staging-aidlc-2.3.0"
PAYLOAD_DIR = PROJECT / "payload" / "aidlc-kiro"  # read only to report its current version
OUT_A = HERE / "aidlc-2.6.2-devdelta"
OUT_B = HERE / "aidlc-2.3.0-payload"
OUT_C = HERE / "aidlc-older"

AUDIT_HEADER = "# AI-DLC Audit Log\n"
AUDIT_TAIL_BLOCKS = 120
SAMPLE_MAX_BYTES = 20 * 1024
FRONTMATTER_MIN_LINES = 60

for p in (SRC_DEVDELTA, SRC_DEVLAKE, GIT_AIDLC):
    if not p.is_dir():
        sys.exit(f"missing source: {p}")
for out in (OUT_A, OUT_B, OUT_C, STAGING_230):
    if not str(out).startswith(str(HERE)):
        sys.exit("refusing to write outside tests/fixtures")


def extract_230() -> Path:
    """git-archive dist/kiro at v2.3.0 into a staging dir inside tests/fixtures (removed by the caller)."""
    if STAGING_230.exists():
        shutil.rmtree(STAGING_230)
    STAGING_230.mkdir(parents=True)
    archive = subprocess.run(["git", "-C", str(GIT_AIDLC), "archive", GIT_TAG, GIT_DIST], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(STAGING_230)], input=archive, check=True)
    src = STAGING_230 / GIT_DIST
    version = (src / ".kiro/tools/aidlc-version.ts").read_text(encoding="utf-8")
    if 'AIDLC_VERSION = "2.3.0"' not in version:
        sys.exit(f"unexpected version in {GIT_TAG}: {version!r}")
    n = len(list((src / ".kiro/aidlc-common/stages").glob("*/*.md")))
    if n != 32:
        sys.exit(f"expected 32 stage files at {GIT_TAG}, found {n}")
    return src


def payload_version_now() -> str:
    try:
        m = re.search(r'AIDLC_VERSION = "([^"]+)"', (PAYLOAD_DIR / ".kiro/tools/aidlc-version.ts").read_text(encoding="utf-8"))
        return m.group(1) if m else "unknown"
    except OSError:
        return "absent"


# ----------------------------------------------------------------------------- scrubbing
class Scrubber:
    def __init__(self, repo_prefixes: list[str]):
        self.rules: list[tuple[str, re.Pattern[str], str]] = []
        for p in repo_prefixes:
            self.rules.append(("repo_prefix", re.compile(re.escape(p)), "/FIXTURE/repo"))
        ws_forms = [re.escape(str(WS_ROOT) + "/"), re.escape("~/" + WS_ROOT.name + "/")]
        self.rules += [
            ("ws_prefix", re.compile("|".join(ws_forms)), WS_PLACEHOLDER + "/"),
            ("home_prefix", re.compile(r"/Users/[^/\s]+/|" + re.escape(str(Path.home()) + "/")), "/FIXTURE/home/"),
            ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "REDACTED"),
            ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "REDACTED"),
            ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "REDACTED"),
            ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "REDACTED"),
            ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "REDACTED"),
            (
                "private_key",
                re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
                "REDACTED",
            ),
            (
                "keyword_secret",
                re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b([:=]?[ \t]+)([A-Za-z0-9+/_=-]{16,})"),
                r"\1\2REDACTED",
            ),
        ]
        self.counts: dict[str, dict[str, int]] = {}

    def scrub(self, text: str, label: str) -> str:
        for name, rx, repl in self.rules:
            text, n = rx.subn(repl, text)
            if n:
                self.counts.setdefault(label, {})
                self.counts[label][name] = self.counts[label].get(name, 0) + n
        return text


@dataclass
class Prov:
    dest: str
    source: str
    transform: str
    src_size: int
    dst_size: int
    src_sha256: str
    dst_sha256: str


@dataclass
class Harvest:
    out: Path
    scrubber: Scrubber
    prov: list[Prov] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def rel(self, dst: Path) -> str:
        return dst.relative_to(self.out).as_posix()

    def write_text(self, dst: Path, text: str, source: str, transform: str, src_bytes: bytes | None = None) -> None:
        label = self.rel(dst)
        text = self.scrubber.scrub(text, label)
        data = text.encode("utf-8")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        self.prov.append(
            Prov(
                dest=label,
                source=source,
                transform=transform,
                src_size=len(src_bytes) if src_bytes is not None else -1,
                dst_size=len(data),
                src_sha256=hashlib.sha256(src_bytes).hexdigest() if src_bytes is not None else "-",
                dst_sha256=hashlib.sha256(data).hexdigest(),
            )
        )

    def copy(self, src: Path, dst: Path, transform=None, transform_name: str = "scrub-only") -> bool:
        if not src.is_file() or src.is_symlink():
            return False
        raw = src.read_bytes()
        text = raw.decode("utf-8")
        if transform is not None:
            text = transform(text)
        self.write_text(dst, text, source=str(src), transform=transform_name, src_bytes=raw)
        return True


# ----------------------------------------------------------------------------- transforms
def split_audit(text: str) -> tuple[list[str], str]:
    """Return (blocks, trailing). Each block is the text between separators, starting with '\\n## '."""
    if not text.startswith(AUDIT_HEADER):
        raise ValueError("audit shard does not start with the standard header")
    body = text[len(AUDIT_HEADER):]
    parts = body.split("\n---\n")
    trailing = parts[-1]
    blocks = parts[:-1]
    # round-trip guarantee: header + join + '\n---\n' + trailing == text
    assert AUDIT_HEADER + "\n---\n".join(blocks) + "\n---\n" + trailing == text
    return blocks, trailing


def join_audit(blocks: list[str]) -> str:
    return AUDIT_HEADER + "\n---\n".join(blocks) + "\n---\n"


def audit_window(text: str, start: int, end: int) -> str:
    blocks, trailing = split_audit(text)
    if trailing.strip():
        raise ValueError("shard ends with a partial block; refusing to tail it")
    return join_audit(blocks[start:end])


def audit_tail(text: str, n: int = AUDIT_TAIL_BLOCKS) -> tuple[str, int, int]:
    blocks, trailing = split_audit(text)
    if trailing.strip():
        raise ValueError("shard ends with a partial block; refusing to tail it")
    tail = blocks[-n:]
    return join_audit(tail), len(blocks), len(tail)


def event_of(block: str) -> str | None:
    m = re.search(r"^\*\*Event\*\*: (\S+)", block, re.M)
    return m.group(1) if m else None


def frontmatter_head(text: str) -> str:
    lines = text.split("\n")
    end = None
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
    keep = FRONTMATTER_MIN_LINES
    if end is not None:
        keep = max(keep, end + 1)
    keep = min(keep, len(lines))
    return "\n".join(lines[:keep]) + "\n"


def artifacts_index(record_src: Path, copied: set[str]) -> str:
    rows = []
    for p in sorted(record_src.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(record_src).as_posix()
            rows.append((p.stat().st_size, rel, "yes" if rel in copied else "no"))
    out = "# size_bytes\trelpath\tcopied_into_fixture\n"
    out += "# every regular file under this intent record dir at harvest time (dotfiles included); contents were copied only where the third column says yes\n"
    out += "".join(f"{s}\t{r}\t{c}\n" for s, r, c in rows)
    return out


def reset(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)


def readme(h: Harvest, title: str, intro: list[str], extra_sections: list[str]) -> None:
    lines = [f"# {title}", ""]
    lines += intro
    lines += ["", "## Scrubbing rules (applied in this order to every copied text file)", ""]
    lines += [
        "1. `repo_prefix` — the source repository's absolute path -> `/FIXTURE/repo`.",
        "2. `ws_prefix` — the workspace directory (`$AIDLC_FIXTURE_WS/` and its `~/<name>/` spelling) -> `/FIXTURE/ws/` (sibling checkouts keep their basename).",
        "2b. `home_prefix` — any remaining `/Users/<account>/` (or the real home dir) -> `/FIXTURE/home/`.",
        "3. `email` — `<local>@<host>.<tld>` -> `REDACTED`.",
        "4. `aws_key` — `AKIA…`/`ASIA…` access-key ids -> `REDACTED`.",
        "5. `slack_token`, `github_token`, `openai_key`, `private_key` — well-known credential shapes -> `REDACTED`.",
        "6. `keyword_secret` — a secret-ish keyword (`token`, `secret`, `password`, `passwd`, `api_key`/`api-key`/`apikey`, `bearer`) followed by a separator and a 16+ character alphanumeric value: the keyword is kept, the value becomes `REDACTED`. SHA-256 digests in `**Questions SHA-256**`, `**Artifact Fingerprint**` and `state_sha256` are untouched (no keyword precedes them).",
        "",
        "Everything else is byte-faithful: files are decoded as UTF-8, regex-substituted, re-encoded as UTF-8. Unchanged files re-encode to identical bytes (verified by the sha256 columns below).",
        "",
        "### Replacements actually made",
        "",
    ]
    if h.scrubber.counts:
        lines.append("| file | rule | count |")
        lines.append("|---|---|---|")
        for label in sorted(h.scrubber.counts):
            for rule, n in sorted(h.scrubber.counts[label].items()):
                lines.append(f"| `{label}` | {rule} | {n} |")
    else:
        lines.append("(none)")
    lines += extra_sections
    lines += ["", "## Provenance (dest <- source, transform, sizes, sha256)", ""]
    lines.append("| dest | source | transform | src bytes | dst bytes | src sha256 | dst sha256 |")
    lines.append("|---|---|---|---|---|---|---|")
    for p in h.prov:
        lines.append(f"| `{p.dest}` | `{p.source}` | {p.transform} | {p.src_size} | {p.dst_size} | `{p.src_sha256}` | `{p.dst_sha256}` |")
    if h.notes:
        lines += ["", "## Harvest notes", ""]
        lines += [f"- {n}" for n in h.notes]
    lines += ["", "Generated by `tests/fixtures/harvest_fixtures.py` (run with `AIDLC_FIXTURE_WS=<workspace dir>`); re-run it to refresh (it deletes and rebuilds this directory). In this file `/FIXTURE/ws` stands for that workspace directory and `/FIXTURE/home` for the account home.", ""]
    text = "\n".join(lines)
    # The README itself must not carry machine-specific paths either: apply the same placeholder vocabulary.
    text = text.replace(str(WS_ROOT) + "/", WS_PLACEHOLDER + "/").replace(str(WS_ROOT), WS_PLACEHOLDER)
    text = re.sub(r"/Users/[^/\s`]+/", "/FIXTURE/home/", text.replace(str(Path.home()) + "/", "/FIXTURE/home/"))
    (h.out / "README.md").write_text(text, encoding="utf-8")


# ----------------------------------------------------------------------------- shared: engine data + stage frontmatter
def harvest_engine_files(h: Harvest, root: Path, *, settings: bool) -> None:
    for name in ("stage-graph.json", "scope-grid.json", "harness.json"):
        h.copy(root / ".kiro/tools/data" / name, h.out / ".kiro/tools/data" / name)
    h.copy(root / ".kiro/tools/aidlc-version.ts", h.out / ".kiro/tools/aidlc-version.ts")
    for f in sorted((root / ".kiro/scopes").glob("*.md")):
        h.copy(f, h.out / ".kiro/scopes" / f.name)
    if settings:
        h.copy(root / ".kiro/settings/cli.json", h.out / ".kiro/settings/cli.json")
    n = 0
    for f in sorted((root / ".kiro/aidlc-common/stages").glob("*/*.md")):
        phase = f.parent.name
        h.copy(f, h.out / "stage-frontmatter" / phase / f.name, transform=frontmatter_head,
               transform_name=f"first max({FRONTMATTER_MIN_LINES}, frontmatter-end) lines")
        n += 1
    h.notes.append(f"stage-frontmatter/: {n} stage files; each copy is the first {FRONTMATTER_MIN_LINES} lines OR through the closing `---` of the YAML frontmatter, whichever is longer (some stage files close their frontmatter after line 60 — e.g. 2.6.2 `construction/nfr-design.md` at line 62 — so a hard 60-line cut would truncate them).")


# ----------------------------------------------------------------------------- (a) DevDelta 2.6.2
DEVDELTA_SAMPLES = {
    "260814-review-major-remediation": [
        "inception/reverse-engineering/memory.md",
    ],
    "260828-kiro-impact-poc": [
        "ideation/intent-capture/intent-statement.md",
        "ideation/intent-capture/learnings-selections.json",
        "inception/requirements-analysis/requirements.md",
        "inception/reverse-engineering/memory.md",
        "construction/kiro-impact-poc/code-generation/code-generation-plan.md",
        "construction/build-and-test/build-and-test-summary.md",
        "construction/code-generation/memory.md",
    ],
}
RECORD_DOTFILES = (".aidlc-active-directive.json", ".aidlc-recovery.md", ".aidlc-goal-stop", ".aidlc-human-turn", ".aidlc-engine-touch")


def harvest_record(h: Harvest, rec_src: Path, rec_dst: Path, samples: list[str], tail_blocks: int = AUDIT_TAIL_BLOCKS,
                   audit_selector=None, extra_files: list[str] | None = None) -> None:
    copied: set[str] = set()

    def cp(rel: str, transform=None, transform_name="scrub-only") -> bool:
        ok = h.copy(rec_src / rel, rec_dst / rel, transform=transform, transform_name=transform_name)
        if ok:
            copied.add(rel)
        return ok

    if not cp("aidlc-state.md"):
        raise SystemExit(f"no aidlc-state.md in {rec_src}")
    for name in RECORD_DOTFILES:
        if name == ".aidlc-active-directive.json" and (rec_src / name).is_file():
            # Scrubbing changes the state bytes (Project Root), so a directive whose digest matched the SOURCE
            # state would mismatch the fixture state for a reason that has nothing to do with the engine.
            # Preserve the semantic: recompute over the scrubbed bytes when (and only when) the source matched.
            src_dir = json.loads((rec_src / name).read_text(encoding="utf-8"))
            src_digest = hashlib.sha256((rec_src / "aidlc-state.md").read_bytes()).hexdigest()
            if src_dir.get("state_sha256") == src_digest:
                new_digest = hashlib.sha256((rec_dst / "aidlc-state.md").read_bytes()).hexdigest()
                cp(name, transform=lambda t, a=src_digest, b=new_digest: t.replace(a, b),
                   transform_name="state_sha256 recomputed over the SCRUBBED state bytes (source digest matched source bytes)")
                h.notes.append(f"`{h.rel(rec_dst / name)}`: source digest `{src_digest[:12]}…` matched the source state; rewritten to `{new_digest[:12]}…` so the fixture still represents a MATCHING (live) directive.")
            else:
                cp(name)
                h.notes.append(f"`{h.rel(rec_dst / name)}`: source digest `{src_dir.get('state_sha256', '')[:12]}…` did NOT match the source state (`{src_digest[:12]}…`) — a real stale marker; copied unchanged.")
            continue
        cp(name)
    for rel in (extra_files or []):
        cp(rel)
    for shard in sorted((rec_src / "audit").glob("*.md")):
        rel = f"audit/{shard.name}"
        text = shard.read_text(encoding="utf-8")
        blocks, _ = split_audit(text)
        if audit_selector is not None:
            start, end, why = audit_selector(shard.name, blocks)
        else:
            start, end, why = max(0, len(blocks) - tail_blocks), len(blocks), f"last {tail_blocks} blocks"
        if start == 0 and end == len(blocks):
            cp(rel, transform_name=f"whole shard ({len(blocks)} blocks)")
        else:
            cp(rel, transform=lambda t, s=start, e=end: audit_window(t, s, e),
               transform_name=f"blocks [{start}, {end}) of {len(blocks)} ({why})")
        evs: dict[str, int] = {}
        for b in blocks[start:end]:
            ev = event_of(b) or "<none>"
            evs[ev] = evs.get(ev, 0) + 1
        h.notes.append(f"`{h.rel(rec_dst / rel)}`: kept blocks [{start}, {end}) of {len(blocks)}; events in the kept window: "
                       + ", ".join(f"{k}×{v}" for k, v in sorted(evs.items(), key=lambda kv: -kv[1])))
    for q in sorted(rec_src.rglob("*-questions.md")):
        cp(q.relative_to(rec_src).as_posix())
    for rel in samples:
        src = rec_src / rel
        if not src.is_file():
            raise SystemExit(f"sample artifact missing: {src}")
        if src.stat().st_size > SAMPLE_MAX_BYTES:
            raise SystemExit(f"sample artifact over {SAMPLE_MAX_BYTES} bytes: {src}")
        cp(rel, transform_name="sample artifact (scrub-only)")
    idx = artifacts_index(rec_src, copied)
    h.write_text(rec_dst / "artifacts-index.txt", idx, source=f"{rec_src} (directory listing)", transform="generated index")


def harvest_devdelta() -> None:
    reset(OUT_A)
    h = Harvest(OUT_A, Scrubber([str(SRC_DEVDELTA)]))
    ws = SRC_DEVDELTA / "aidlc"
    h.copy(ws / "active-space", OUT_A / "aidlc/active-space")
    h.copy(ws / ".aidlc-clone-id", OUT_A / "aidlc/.aidlc-clone-id")
    h.copy(ws / ".aidlc-turn-counter", OUT_A / "aidlc/.aidlc-turn-counter")
    intents = ws / "spaces/default/intents"
    h.copy(intents / "intents.json", OUT_A / "aidlc/spaces/default/intents/intents.json")
    h.copy(intents / "active-intent", OUT_A / "aidlc/spaces/default/intents/active-intent")
    event_samples: dict[str, tuple[str, str, int]] = {}
    for rec in sorted(p for p in intents.iterdir() if p.is_dir() and (p / "aidlc-state.md").is_file()):
        harvest_record(h, rec, OUT_A / "aidlc/spaces/default/intents" / rec.name, DEVDELTA_SAMPLES.get(rec.name, []))
        for shard in sorted((rec / "audit").glob("*.md")):
            blocks, _ = split_audit(shard.read_text(encoding="utf-8"))
            for i, b in enumerate(blocks):
                ev = event_of(b)
                if ev and ev not in event_samples:
                    event_samples[ev] = (f"{rec.name}/audit/{shard.name}", b, i)
    # codekb index (space-level reverse-engineering output; contents not copied)
    codekb = ws / "spaces/default/codekb"
    rows = [(p.stat().st_size, p.relative_to(codekb).as_posix()) for p in sorted(codekb.rglob("*")) if p.is_file()]
    h.write_text(OUT_A / "aidlc/spaces/default/codekb-index.txt",
                 "# size_bytes\trelpath (aidlc/spaces/default/codekb/**; reverse-engineering artifacts live here, not in the intent record)\n"
                 + "".join(f"{s}\t{r}\n" for s, r in rows),
                 source=f"{codekb} (directory listing)", transform="generated index")
    # space-level hooks-health names (pre-birth hook fires)
    hh = intents / ".aidlc-hooks-health"
    if hh.is_dir():
        h.notes.append("space-level `aidlc/spaces/default/intents/.aidlc-hooks-health/` exists in the source with: "
                       + ", ".join(sorted(p.name for p in hh.iterdir())) + " (not copied; heartbeat files only).")
    harvest_engine_files(h, SRC_DEVDELTA, settings=True)
    # excerpt: first real block of every distinct event type (outside the aidlc/ tree so a fixture-rooted reader ignores it)
    ex = ["# First real audit block per distinct event type (DevDelta 2.6.2, both intents; scrubbed)",
          "# NOT an audit shard. Kept outside aidlc/ so readers that glob <record>/audit/*.md never see it.",
          "# Format of each entry: a comment line `# <event> <- <record>/audit/<shard> block #<index>` followed by the block bytes exactly as on disk, then the `---` separator.",
          ""]
    for ev in sorted(event_samples):
        src, block, idx = event_samples[ev]
        ex.append(f"# {ev} <- {src} block #{idx}")
        ex.append(block.lstrip("\n").rstrip("\n"))
        ex.append("")
        ex.append("---")
        ex.append("")
    h.write_text(OUT_A / "_excerpts/audit-event-samples.md", "\n".join(ex), source="DevDelta audit shards (first block per event type)", transform="generated excerpt")
    h.notes.append(f"_excerpts/audit-event-samples.md carries {len(event_samples)} distinct event types.")
    readme(
        h,
        "Fixture: aidlc-2.6.2-devdelta",
        [
            "Scrubbed copy of the live AI-DLC **2.6.2** Kiro install at `/FIXTURE/ws/DevDelta` (33-stage graph, **State Version 8**), harvested 2026-09-04.",
            "",
            "Layout mirrors the repo root: `aidlc/**` (workspace + two intent records), `.kiro/**` (engine data actually read by Studio), `stage-frontmatter/<phase>/<slug>.md` (frontmatter of every `.kiro/aidlc-common/stages/**/*.md`), `_excerpts/` (test helpers, not part of the on-disk model).",
            "",
            "What was copied per intent record: `aidlc-state.md`, `.aidlc-active-directive.json`, `.aidlc-recovery.md`, `.aidlc-goal-stop` (only where present), `.aidlc-human-turn` / `.aidlc-engine-touch` (21-byte ISO timestamps, extra), the LAST 120 blocks of every `audit/*.md` shard (or the whole shard when shorter; exact block format preserved, header line kept), every `*-questions.md`, ONE small (<=20 KB) markdown artifact per stage kind (plus the 57-byte `learnings-selections.json`), and `artifacts-index.txt` listing EVERY regular file of the record with its size and whether it was copied.",
            "",
            "Deliberately NOT copied: artifact bodies beyond the samples, `runtime-graph.json`, `.aidlc-hooks-health/**`, `.aidlc-sensors/**`, `.aidlc-steering-token-key` (machine-local HMAC secret), `aidlc/.aidlc-sessions/**` (transcript paths), `aidlc/spaces/default/codekb/**` (indexed only), `.kiro/settings/mcp.json` and `lsp.json`.",
        ],
        [],
    )


# ----------------------------------------------------------------------------- (b) payload 2.3.0 + synthetic v7 workspace
EVENT_HEADINGS_230 = {
    "STAGE_STARTED": "Stage Start", "STAGE_AWAITING_APPROVAL": "Stage Awaiting Approval", "STAGE_REVISING": "Stage Revising",
    "STAGE_COMPLETED": "Stage Completion", "STAGE_JUMPED": "Stage Jump", "STAGE_SKIPPED": "Stage Skip",
    "PHASE_STARTED": "Phase Start", "PHASE_COMPLETED": "Phase Completion", "PHASE_VERIFIED": "Phase Verification", "PHASE_SKIPPED": "Phase Skip",
    "WORKFLOW_STARTED": "Workflow Start", "WORKFLOW_COMPLETED": "Workflow Completion", "WORKFLOW_PARKED": "Workflow Parked", "WORKFLOW_UNPARKED": "Workflow Unparked",
    "SESSION_STARTED": "Session Start", "SESSION_RESUMED": "Session Resume", "SESSION_COMPACTED": "Session Compacted", "SESSION_ENDED": "Session End",
    "HUMAN_TURN": "Human Turn", "WORKSPACE_SCAFFOLDED": "Workspace Scaffolded", "WORKSPACE_SCANNED": "Workspace Scanned", "WORKSPACE_INITIALISED": "Workspace Initialised",
    "DECISION_RECORDED": "Decision Recorded", "GATE_APPROVED": "Gate Approved", "GATE_REJECTED": "Gate Rejected", "QUESTION_ANSWERED": "Question Answered",
    "ARTIFACT_CREATED": "Artifact Created", "ARTIFACT_UPDATED": "Artifact Updated", "ARTIFACT_REUSED": "Artifact Reused", "SUBAGENT_COMPLETED": "Subagent Completed",
    "HEALTH_CHECKED": "Health Check", "SCOPE_DETECTED": "Scope Detection", "SCOPE_CHANGED": "Scope Change", "DEPTH_CHANGED": "Depth Change",
    "TEST_STRATEGY_CHANGED": "Test Strategy Change", "RECOMPOSED": "Plan Recomposed", "ERROR_LOGGED": "Error Logged", "RECOVERY_COMPLETED": "Recovery Completed",
    "SENSOR_FIRED": "Sensor Fired", "SENSOR_PASSED": "Sensor Passed", "SENSOR_FAILED": "Sensor Failed", "GUARDRAIL_LOADED": "Guardrail Loaded", "MEMORY_EMPTY": "Memory Empty",
}


class AuditWriter:
    """Byte-exact reproduction of aidlc-audit.ts appendAuditEntryUnlocked (2.3.0)."""

    def __init__(self) -> None:
        self.text = AUDIT_HEADER
        self.count = 0

    def add(self, ts: str, event: str, fields: dict[str, str] | None = None) -> None:
        if event not in EVENT_HEADINGS_230:
            raise ValueError(f"not a 2.3.0 event: {event}")
        block = f"\n## {EVENT_HEADINGS_230[event]}\n"
        block += f"**Timestamp**: {ts}\n"
        block += f"**Event**: {event}\n"
        for k, v in (fields or {}).items():
            block += f"**{k}**: {v.replace(chr(13)+chr(10), chr(92)+'n').replace(chr(10), chr(92)+'n')}\n"
        block += "\n---\n"
        self.text += block
        self.count += 1


def uuid7(ms: int, tail: str) -> str:
    """Deterministic UUIDv7 shape: 48-bit ms + version nibble 7 + variant nibble 8 + 18 hex of tail."""
    hx = f"{ms:012x}"
    assert len(tail) == 18 and re.fullmatch(r"[0-9a-f]{18}", tail)
    u = f"{hx[:8]}-{hx[8:12]}-7{tail[:3]}-8{tail[3:6]}-{tail[6:18]}"
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-8[0-9a-f]{3}-[0-9a-f]{12}", u)
    return u


FIX_ROOT = "/FIXTURE/repo"
CLONE_ID = "0f1e2d3c4b5a"
SHARD = f"fixture-host-{CLONE_ID}.md"
INTENT_A = "260820-health-probe"       # complete, poc
INTENT_B = "260901-ledger-export"      # in-flight, feature, gate open at requirements-analysis
REC = "aidlc/spaces/default/intents"


def render_state_v7(*, project: str, project_type: str, scope: str, start: str, agent: str, execute: str, skip: str,
                    depth: str, test_strategy: str, languages: str, frameworks: str, build: str, total: int, completed: int,
                    in_progress: str, revision: int, phases: dict[str, str], rows: dict[str, list[tuple[str, str, str]]],
                    lifecycle: str, current: str, nxt: str, status: str, updated: str, last_completed: str, next_action: str) -> str:
    def phase_block(title: str, items: list[tuple[str, str, str]], per_unit: bool = False) -> str:
        out = f"### {title} PHASE\n"
        if per_unit:
            out += "Per unit: [TBD]\n"
        for mark, slug, suffix in items:
            out += f"- [{mark}] {slug} — {suffix}\n"
        return out

    stage_progress = "\n".join([
        phase_block("INITIALIZATION", rows["initialization"]),
        phase_block("IDEATION", rows["ideation"]),
        phase_block("INCEPTION", rows["inception"]),
        phase_block("CONSTRUCTION", rows["construction"], per_unit=True),
        phase_block("OPERATION", rows["operation"]),
    ])
    return f"""# AI-DLC State Tracking

## Project Information
- **Project**: {project}
- **Project Type**: {project_type}
- **Scope**: {scope}
- **Start Date**: {start}
- **State Version**: 7
- **Active Agent**: {agent}
- **Worktree Path**:
- **Bolt Refs**:
- **Practices Affirmed Timestamp**:

## Scope Configuration
- **Stages to Execute**: {execute}
- **Stages to Skip**: {skip}
- **Depth**: {depth}
- **Test Strategy**: {test_strategy}

## Workspace State
- **Project Root**: {FIX_ROOT}
- **Languages**: {languages}
- **Frameworks**: {frameworks}
- **Build System**: {build}

## Execution Plan Summary
- **Total Stages**: {total}
- **Completed**: {completed}
- **In Progress**: {in_progress}

## Runtime State
- **Revision Count**: {revision}

## Phase Progress
<!-- Status values: Pending, Active, Verified, Skipped -->

- **Initialization**: {phases['initialization']}
- **Ideation**: {phases['ideation']}
- **Inception**: {phases['inception']}
- **Construction**: {phases['construction']}
- **Operation**: {phases['operation']}

## Stage Progress
<!-- Checkbox states: [ ] not started, [-] in progress, [?] awaiting approval (gate open), [R] revising (user rejected gate), [x] completed, [S] skipped via --stage/--phase jump -->

{stage_progress}
## Current Status
- **Lifecycle Phase**: {lifecycle}
- **Current Stage**: {current}
- **Next Stage**: {nxt}
- **Status**: {status}
- **Last Updated**: {updated}

## Session Resume Point
- **Last Completed Stage**: {last_completed}
- **Next Action**: {next_action}
- **Pending Artifacts**: none
"""


def build_synthetic_v7(h: Harvest, src_230: Path) -> None:
    graph = json.loads((src_230 / ".kiro/tools/data/stage-graph.json").read_text(encoding="utf-8"))
    grid = json.loads((src_230 / ".kiro/tools/data/scope-grid.json").read_text(encoding="utf-8"))
    assert len(graph) == 32 and "application-design" in {s["slug"] for s in graph}
    by_slug = {s["slug"]: s for s in graph}
    phases = ("initialization", "ideation", "inception", "construction", "operation")

    def exec_skip(scope: str) -> tuple[str, str, int]:
        st = grid[scope]["stages"]
        ex = [s["number"] for s in graph if st.get(s["slug"]) == "EXECUTE"]
        sk = [f"{s['number']} ({s['slug']})" for s in graph if st.get(s["slug"]) != "EXECUTE"]
        return ", ".join(ex), (", ".join(sk) if sk else "none"), len(ex)

    def rows_for(scope: str, marks: dict[str, str]) -> dict[str, list[tuple[str, str, str]]]:
        st = grid[scope]["stages"]
        out: dict[str, list[tuple[str, str, str]]] = {p: [] for p in phases}
        for s in graph:
            suffix = "EXECUTE" if st.get(s["slug"]) == "EXECUTE" else "SKIP"
            out[s["phase"]].append((marks.get(s["slug"], " "), s["slug"], suffix))
        return out

    def stage_name(slug: str) -> str:
        return by_slug[slug]["name"]

    def lead(slug: str) -> str:
        return by_slug[slug]["lead_agent"]

    # ---- workspace-level files
    h.write_text(h.out / "aidlc/active-space", "default\n", source="synthetic", transform="authored")
    h.write_text(h.out / "aidlc/.aidlc-clone-id", f"{CLONE_ID}\n", source="synthetic", transform="authored")
    h.write_text(h.out / "aidlc/.aidlc-turn-counter", "41\n", source="synthetic", transform="authored")

    uuid_a = uuid7(1787299331000, "3c9d2a1f0b7e4d5a6c")  # 2026-08-20T08:02:11Z
    uuid_b = uuid7(1788339614000, "9a4e1c7b2d6f083e1b")  # 2026-09-01T09:00:14Z
    registry = [
        {"uuid": uuid_a, "slug": "health-probe", "dirName": INTENT_A, "scope": "poc", "status": "complete"},
        {"uuid": uuid_b, "slug": "ledger-export", "dirName": INTENT_B, "scope": "feature", "status": "in-flight"},
    ]
    h.write_text(h.out / REC / "intents.json", json.dumps(registry, indent=2) + "\n", source="synthetic", transform="authored (JSON.stringify(list, null, 2)+\"\\n\")")
    h.write_text(h.out / REC / "active-intent", f"{INTENT_B}\n", source="synthetic", transform="authored")

    # =============================================================== intent A: complete poc workflow
    ex_a, sk_a, total_a = exec_skip("poc")
    marks_a = {s: "x" for s in ("workspace-scaffold", "workspace-detection", "state-init", "intent-capture", "reverse-engineering",
                                 "requirements-analysis", "code-generation", "build-and-test")}
    state_a = render_state_v7(
        project="Prove that the billing service can expose a /health probe that reports database and queue connectivity",
        project_type="Brownfield", scope="poc", start="2026-08-20T08:02:11Z", agent=lead("build-and-test"),
        execute=ex_a, skip=sk_a, depth="Minimal", test_strategy="Minimal",
        languages="TypeScript", frameworks="Express", build="npm (package.json)",
        total=total_a, completed=8, in_progress="none", revision=0,
        phases={"initialization": "Verified", "ideation": "Verified", "inception": "Verified", "construction": "Verified", "operation": "Skipped"},
        rows=rows_for("poc", marks_a), lifecycle="CONSTRUCTION", current="build-and-test", nxt="none", status="Completed",
        updated="2026-08-21T16:40:09Z", last_completed="build-and-test", next_action="Workflow complete",
    )
    h.write_text(h.out / REC / INTENT_A / "aidlc-state.md", state_a, source="synthetic", transform="authored (2.3.0 handleIntentBirthStateBuild template + engine transitions)")

    req_a = "/aidlc Prove that the billing service can expose a /health probe that reports database and queue connectivity"
    rec_a = f"{FIX_ROOT}/{REC}/{INTENT_A}"
    w = AuditWriter()
    w.add("2026-08-20T08:01:50Z", "SESSION_STARTED", {"Source": "startup"})
    w.add("2026-08-20T08:02:09Z", "HUMAN_TURN")
    w.add("2026-08-20T08:02:11Z", "WORKFLOW_STARTED", {"Scope": "poc", "Request": req_a})
    w.add("2026-08-20T08:02:11Z", "PHASE_STARTED", {"Phase": "initialization", "Stage count": "3", "Scope": "poc"})
    w.add("2026-08-20T08:02:11Z", "PHASE_SKIPPED", {"Phase": "operation", "Scope": "poc", "Reason": "scope poc excludes operation"})
    w.add("2026-08-20T08:02:11Z", "STAGE_STARTED", {"Stage": "workspace-scaffold", "Agent": "orchestrator"})
    w.add("2026-08-20T08:02:11Z", "WORKSPACE_SCAFFOLDED", {"Request": req_a, "Details": "Per-intent artifact dirs + space-level knowledge/ ensured"})
    w.add("2026-08-20T08:02:11Z", "STAGE_COMPLETED", {"Stage": "workspace-scaffold", "Details": "Per-intent artifact dirs + space-level knowledge/ ensured"})
    w.add("2026-08-20T08:02:11Z", "STAGE_STARTED", {"Stage": "workspace-detection", "Agent": "orchestrator"})
    w.add("2026-08-20T08:02:12Z", "WORKSPACE_SCANNED", {"Project Type": "Brownfield", "Languages": "TypeScript", "Frameworks": "Express", "Build System": "npm (package.json)", "Details": "Deterministic rule-based scan"})
    w.add("2026-08-20T08:02:12Z", "STAGE_COMPLETED", {"Stage": "workspace-detection", "Details": "Classified Brownfield; languages=TypeScript; frameworks=Express"})
    w.add("2026-08-20T08:02:12Z", "STAGE_STARTED", {"Stage": "state-init", "Agent": "orchestrator"})
    w.add("2026-08-20T08:02:12Z", "WORKSPACE_INITIALISED", {"Request": req_a, "Project Type": "Brownfield", "Scope": "poc", "Languages": "TypeScript", "Frameworks": "Express", "Build System": "npm (package.json)", "Details": f"{total_a} stages in scope, routing to intent-capture"})
    w.add("2026-08-20T08:02:12Z", "STAGE_COMPLETED", {"Stage": "state-init", "Details": f"State initialized: poc scope, {total_a} stages, routing to intent-capture"})
    w.add("2026-08-20T08:02:12Z", "PHASE_COMPLETED", {"From phase": "initialization", "To phase": "ideation", "Stages completed": "3"})
    w.add("2026-08-20T08:02:12Z", "PHASE_VERIFIED", {"Phase boundary": "initialization → ideation"})
    w.add("2026-08-20T08:02:12Z", "PHASE_STARTED", {"Phase": "ideation", "Scope": "poc"})
    w.add("2026-08-20T08:02:12Z", "STAGE_STARTED", {"Stage": "intent-capture", "Agent": lead("intent-capture")})
    w.add("2026-08-20T08:04:31Z", "DECISION_RECORDED", {"Stage": "intent-capture", "Decision": "How would you like to answer the Intent Capture questions?", "Options": "Guide me,I'll edit the file,Chat"})
    w.add("2026-08-20T08:05:02Z", "HUMAN_TURN")
    w.add("2026-08-20T08:05:04Z", "QUESTION_ANSWERED", {"Stage": "intent-capture", "Details": "Guide me"})
    w.add("2026-08-20T08:05:40Z", "ERROR_LOGGED", {"Tool": "aidlc-state", "Command": "aidlc-state set-status intent-capture", "Error": "Unknown subcommand: set-status. Valid: get, set, set-skeleton-stance, checkbox, count, advance, finalize, complete-workflow, gate-start, approve, reject, revise, skip, resume, acknowledge-compaction, reuse-artifact, lookup, practices-event, practices-promote, fork, merge"})
    w.add("2026-08-20T08:09:17Z", "HUMAN_TURN")
    w.add("2026-08-20T08:09:20Z", "QUESTION_ANSWERED", {"Stage": "intent-capture", "Details": "Q1=A, Q2=C, Q3=A, B"})
    w.add("2026-08-20T08:11:48Z", "ARTIFACT_CREATED", {"Tool": "Write", "File": f"{rec_a}/ideation/intent-capture/intent-statement.md", "Context": "ideation > intent-capture > intent-statement.md"})
    w.add("2026-08-20T08:11:48Z", "SENSOR_FIRED", {"Fire id": "5e2b90a1", "Sensor ID": "required-sections", "Stage slug": "intent-capture", "Output path": f"{REC}/{INTENT_A}/ideation/intent-capture/intent-statement.md"})
    w.add("2026-08-20T08:11:49Z", "SENSOR_PASSED", {"Fire id": "5e2b90a1", "Sensor ID": "required-sections", "Stage slug": "intent-capture", "Output path": f"{REC}/{INTENT_A}/ideation/intent-capture/intent-statement.md", "Duration ms": "14"})
    w.add("2026-08-20T08:12:03Z", "STAGE_AWAITING_APPROVAL", {"Stage": "intent-capture"})
    w.add("2026-08-20T08:14:55Z", "HUMAN_TURN")
    w.add("2026-08-20T08:14:58Z", "GATE_APPROVED", {"Stage": "intent-capture", "User Input": "批准"})
    w.add("2026-08-20T08:14:58Z", "STAGE_COMPLETED", {"Stage": "intent-capture", "Details": f"Stage {stage_name('intent-capture')} approved by gate"})
    w.add("2026-08-20T08:14:58Z", "PHASE_COMPLETED", {"From phase": "ideation", "To phase": "inception", "Stages completed": "1"})
    w.add("2026-08-20T08:14:58Z", "PHASE_VERIFIED", {"Phase boundary": "ideation → inception"})
    w.add("2026-08-20T08:14:58Z", "PHASE_STARTED", {"Phase": "inception", "Scope": "poc"})
    w.add("2026-08-20T08:14:58Z", "STAGE_STARTED", {"Stage": "reverse-engineering", "Agent": lead("reverse-engineering")})
    w.add("2026-08-20T08:41:20Z", "SUBAGENT_COMPLETED", {"Agent Type": "", "Agent ID": "a3f9c1d27e5b0a814", "Message": "Reverse engineering of the billing service is complete; nine codekb artifacts were written under aidlc/spaces/default/codekb/repo."})
    w.add("2026-08-20T08:41:33Z", "STAGE_AWAITING_APPROVAL", {"Stage": "reverse-engineering"})
    w.add("2026-08-20T08:47:10Z", "HUMAN_TURN")
    w.add("2026-08-20T08:47:12Z", "GATE_APPROVED", {"Stage": "reverse-engineering", "User Input": "Approve"})
    w.add("2026-08-20T08:47:12Z", "STAGE_COMPLETED", {"Stage": "reverse-engineering", "Details": f"Stage {stage_name('reverse-engineering')} approved by gate"})
    w.add("2026-08-20T08:47:12Z", "STAGE_STARTED", {"Stage": "requirements-analysis", "Agent": lead("requirements-analysis")})
    w.add("2026-08-20T08:52:40Z", "DECISION_RECORDED", {"Stage": "requirements-analysis", "Decision": "How would you like to answer the Requirements Analysis questions?", "Options": "Guide me,I'll edit the file,Chat"})
    w.add("2026-08-20T08:53:01Z", "HUMAN_TURN")
    w.add("2026-08-20T08:53:03Z", "QUESTION_ANSWERED", {"Stage": "requirements-analysis", "Details": "Chat"})
    w.add("2026-08-20T09:02:44Z", "HUMAN_TURN")
    w.add("2026-08-20T09:02:47Z", "QUESTION_ANSWERED", {"Stage": "requirements-analysis", "Details": "Q1=A (HTTP 200/503 only), Q2=B (no auth on the probe), Q3=A"})
    w.add("2026-08-20T09:10:12Z", "ARTIFACT_CREATED", {"Tool": "Write", "File": f"{rec_a}/inception/requirements-analysis/requirements.md", "Context": "inception > requirements-analysis > requirements.md"})
    w.add("2026-08-20T09:10:20Z", "STAGE_AWAITING_APPROVAL", {"Stage": "requirements-analysis"})
    w.add("2026-08-20T09:15:37Z", "HUMAN_TURN")
    w.add("2026-08-20T09:15:40Z", "GATE_APPROVED", {"Stage": "requirements-analysis", "User Input": "Approve"})
    w.add("2026-08-20T09:15:40Z", "STAGE_COMPLETED", {"Stage": "requirements-analysis", "Details": f"Stage {stage_name('requirements-analysis')} approved by gate"})
    w.add("2026-08-20T09:15:40Z", "PHASE_COMPLETED", {"From phase": "inception", "To phase": "construction", "Stages completed": "2"})
    w.add("2026-08-20T09:15:40Z", "PHASE_VERIFIED", {"Phase boundary": "inception → construction"})
    w.add("2026-08-20T09:15:40Z", "PHASE_STARTED", {"Phase": "construction", "Scope": "poc"})
    w.add("2026-08-20T09:15:40Z", "STAGE_STARTED", {"Stage": "code-generation", "Agent": lead("code-generation")})
    w.add("2026-08-20T09:31:02Z", "ARTIFACT_CREATED", {"Tool": "Write", "File": f"{rec_a}/construction/health-probe/code-generation/code-generation-plan.md", "Context": "construction > health-probe > code-generation > code-generation-plan.md"})
    w.add("2026-08-20T09:31:15Z", "DECISION_RECORDED", {"Stage": "code-generation", "Decision": "Plan Approval", "Options": "Approve Plan,Request Changes"})
    w.add("2026-08-20T09:36:48Z", "HUMAN_TURN")
    w.add("2026-08-20T09:36:50Z", "QUESTION_ANSWERED", {"Stage": "code-generation", "Details": "Approve Plan"})
    w.add("2026-08-21T15:58:21Z", "SESSION_STARTED", {"Source": "startup"})
    w.add("2026-08-21T15:58:40Z", "HUMAN_TURN")
    w.add("2026-08-21T16:20:05Z", "SUBAGENT_COMPLETED", {"Agent Type": "", "Agent ID": "ab61e0f4c7d92e330", "Message": "Implemented GET /health with database and queue checks plus 6 unit tests; code-summary.md written."})
    w.add("2026-08-21T16:20:19Z", "STAGE_AWAITING_APPROVAL", {"Stage": "code-generation"})
    w.add("2026-08-21T16:24:52Z", "HUMAN_TURN")
    w.add("2026-08-21T16:24:55Z", "GATE_APPROVED", {"Stage": "code-generation", "User Input": "Approve"})
    w.add("2026-08-21T16:24:55Z", "STAGE_COMPLETED", {"Stage": "code-generation", "Details": f"Stage {stage_name('code-generation')} approved by gate"})
    w.add("2026-08-21T16:24:55Z", "STAGE_STARTED", {"Stage": "build-and-test", "Agent": lead("build-and-test")})
    w.add("2026-08-21T16:38:30Z", "ARTIFACT_CREATED", {"Tool": "Write", "File": f"{rec_a}/construction/build-and-test/build-test-results.md", "Context": "construction > build-and-test > build-test-results.md"})
    w.add("2026-08-21T16:38:44Z", "STAGE_AWAITING_APPROVAL", {"Stage": "build-and-test"})
    w.add("2026-08-21T16:40:06Z", "HUMAN_TURN")
    w.add("2026-08-21T16:40:09Z", "GATE_APPROVED", {"Stage": "build-and-test", "User Input": "Approve"})
    w.add("2026-08-21T16:40:09Z", "STAGE_COMPLETED", {"Stage": "build-and-test", "Details": f"Stage {stage_name('build-and-test')} approved by gate"})
    w.add("2026-08-21T16:40:09Z", "PHASE_VERIFIED", {"Phase boundary": "construction → end"})
    w.add("2026-08-21T16:40:09Z", "WORKFLOW_COMPLETED", {"Scope": "poc", "Details": f"Scope: poc, {total_a} stages completed"})
    h.write_text(h.out / REC / INTENT_A / "audit" / SHARD, w.text, source="synthetic", transform=f"authored ({w.count} blocks, 2.3.0 appendAuditEntryUnlocked format)")

    h.write_text(h.out / REC / INTENT_A / "construction/health-probe/code-generation/code-generation-questions.md",
                 """# Code Generation — Plan Approval

Stage: `code-generation` · Unit: `health-probe` · Depth: Minimal · Scope: `poc`

---

## Plan Approval

Covers `code-generation-plan.md`.

Plan summary: add `GET /health` to the existing Express router, returning `200 {"db":"ok","queue":"ok"}` or `503` with the failing dependency named; reuse the existing `pg` pool and the AMQP channel already held by the worker module. No new dependencies, no schema changes, no deployment.

Tests: 6 unit tests with the existing Jest runner (happy path, db down, queue down, both down, timeout budget 800 ms, response shape).

- Approve Plan
- Request Changes

[Answer]: Approve Plan
""", source="synthetic", transform="authored (per-unit questions file, Plan Approval answered)")
    h.write_text(h.out / REC / INTENT_A / "construction/build-and-test/build-test-results.md",
                 """# Build and Test Results

## Build

- `npm ci` — ok (42 s)
- `npm run build` — ok, 0 type errors

## Unit tests

| Suite | Tests | Passed | Failed |
|---|---|---|---|
| health.route.test.ts | 6 | 6 | 0 |
| existing suites | 118 | 118 | 0 |

## Notes

- The probe answers in 11 ms with both dependencies healthy and in 812 ms when the queue is unreachable (timeout budget 800 ms plus overhead).
- No production behaviour changed; all pre-existing tests still pass.
""", source="synthetic", transform="authored (small artifact)")

    # =============================================================== intent B: in-flight feature workflow, gate open at requirements-analysis
    ex_b, sk_b, total_b = exec_skip("feature")
    done_b = ["workspace-scaffold", "workspace-detection", "state-init", "intent-capture", "market-research", "feasibility",
              "scope-definition", "team-formation", "rough-mockups", "approval-handoff", "reverse-engineering", "practices-discovery"]
    marks_b = {s: "x" for s in done_b}
    marks_b["requirements-analysis"] = "?"
    project_b = "Add a monthly ledger export (CSV and PDF) to the billing service without changing the existing reports"
    state_b = render_state_v7(
        project=project_b, project_type="Brownfield", scope="feature", start="2026-09-01T09:00:14Z", agent=lead("requirements-analysis"),
        execute=ex_b, skip=sk_b, depth="Standard", test_strategy="Standard",
        languages="TypeScript", frameworks="Express", build="npm (package.json)",
        total=total_b, completed=len(done_b), in_progress="requirements-analysis", revision=1,
        phases={"initialization": "Verified", "ideation": "Verified", "inception": "Active", "construction": "Pending", "operation": "Pending"},
        rows=rows_for("feature", marks_b), lifecycle="INCEPTION", current="requirements-analysis", nxt="user-stories", status="Running",
        updated="2026-09-03T14:22:41Z", last_completed="practices-discovery", next_action="Await gate decision for requirements-analysis",
    )
    state_b_bytes = state_b.encode("utf-8")
    h.write_text(h.out / REC / INTENT_B / "aidlc-state.md", state_b, source="synthetic", transform="authored (2.3.0 template + engine transitions; Revision Count 1 after one GATE_REJECTED)")
    directive = {"version": 1, "stage": "requirements-analysis", "state_sha256": hashlib.sha256(state_b_bytes).hexdigest()}
    h.write_text(h.out / REC / INTENT_B / ".aidlc-active-directive.json", json.dumps(directive, indent=2) + "\n", source="synthetic",
                 transform="authored; state_sha256 = sha256(aidlc-state.md bytes) computed at build time")
    assert hashlib.sha256((h.out / REC / INTENT_B / "aidlc-state.md").read_bytes()).hexdigest() == directive["state_sha256"]

    req_b = f"/aidlc {project_b}"
    rec_b = f"{FIX_ROOT}/{REC}/{INTENT_B}"
    w = AuditWriter()
    w.add("2026-09-01T09:00:00Z", "SESSION_STARTED", {"Source": "startup"})
    w.add("2026-09-01T09:00:12Z", "HUMAN_TURN")
    w.add("2026-09-01T09:00:14Z", "WORKFLOW_STARTED", {"Scope": "feature", "Request": req_b})
    w.add("2026-09-01T09:00:14Z", "PHASE_STARTED", {"Phase": "initialization", "Stage count": "3", "Scope": "feature"})
    w.add("2026-09-01T09:00:14Z", "STAGE_STARTED", {"Stage": "workspace-scaffold", "Agent": "orchestrator"})
    w.add("2026-09-01T09:00:14Z", "WORKSPACE_SCAFFOLDED", {"Request": req_b, "Details": "Per-intent artifact dirs + space-level knowledge/ ensured"})
    w.add("2026-09-01T09:00:14Z", "STAGE_COMPLETED", {"Stage": "workspace-scaffold", "Details": "Per-intent artifact dirs + space-level knowledge/ ensured"})
    w.add("2026-09-01T09:00:14Z", "STAGE_STARTED", {"Stage": "workspace-detection", "Agent": "orchestrator"})
    w.add("2026-09-01T09:00:15Z", "WORKSPACE_SCANNED", {"Project Type": "Brownfield", "Languages": "TypeScript", "Frameworks": "Express", "Build System": "npm (package.json)", "Details": "Deterministic rule-based scan"})
    w.add("2026-09-01T09:00:15Z", "STAGE_COMPLETED", {"Stage": "workspace-detection", "Details": "Classified Brownfield; languages=TypeScript; frameworks=Express"})
    w.add("2026-09-01T09:00:15Z", "STAGE_STARTED", {"Stage": "state-init", "Agent": "orchestrator"})
    w.add("2026-09-01T09:00:15Z", "WORKSPACE_INITIALISED", {"Request": req_b, "Project Type": "Brownfield", "Scope": "feature", "Languages": "TypeScript", "Frameworks": "Express", "Build System": "npm (package.json)", "Details": f"{total_b} stages in scope, routing to intent-capture"})
    w.add("2026-09-01T09:00:15Z", "STAGE_COMPLETED", {"Stage": "state-init", "Details": f"State initialized: feature scope, {total_b} stages, routing to intent-capture"})
    w.add("2026-09-01T09:00:15Z", "PHASE_COMPLETED", {"From phase": "initialization", "To phase": "ideation", "Stages completed": "3"})
    w.add("2026-09-01T09:00:15Z", "PHASE_VERIFIED", {"Phase boundary": "initialization → ideation"})
    w.add("2026-09-01T09:00:15Z", "PHASE_STARTED", {"Phase": "ideation", "Scope": "feature"})
    w.add("2026-09-01T09:00:15Z", "STAGE_STARTED", {"Stage": "intent-capture", "Agent": lead("intent-capture")})
    w.add("2026-09-01T09:03:40Z", "DECISION_RECORDED", {"Stage": "intent-capture", "Decision": "How would you like to answer the Intent Capture questions?", "Options": "Guide me,I'll edit the file,Chat"})
    w.add("2026-09-01T09:04:02Z", "HUMAN_TURN")
    w.add("2026-09-01T09:04:05Z", "QUESTION_ANSWERED", {"Stage": "intent-capture", "Details": "Chat"})
    w.add("2026-09-01T09:07:30Z", "HUMAN_TURN")
    w.add("2026-09-01T09:07:33Z", "QUESTION_ANSWERED", {"Stage": "intent-capture", "Details": "Q1=A, Q2=B, Q3=A, B"})
    w.add("2026-09-01T09:08:01Z", "DECISION_RECORDED", {"Stage": "intent-capture", "Decision": "Does this all look correct before I generate the artifact?", "Options": "Looks correct,Request changes"})
    w.add("2026-09-01T09:08:40Z", "HUMAN_TURN")
    w.add("2026-09-01T09:08:42Z", "QUESTION_ANSWERED", {"Stage": "intent-capture", "Details": "Looks correct"})
    w.add("2026-09-01T09:09:52Z", "ARTIFACT_CREATED", {"Tool": "Write", "File": f"{rec_b}/ideation/intent-capture/intent-capture-questions.md", "Context": "ideation > intent-capture > intent-capture-questions.md"})
    w.add("2026-09-01T09:12:20Z", "ARTIFACT_CREATED", {"Tool": "Write", "File": f"{rec_b}/ideation/intent-capture/intent-statement.md", "Context": "ideation > intent-capture > intent-statement.md"})
    w.add("2026-09-01T09:12:21Z", "SENSOR_FIRED", {"Fire id": "7c1a9e02", "Sensor ID": "required-sections", "Stage slug": "intent-capture", "Output path": f"{REC}/{INTENT_B}/ideation/intent-capture/intent-statement.md"})
    w.add("2026-09-01T09:12:21Z", "SENSOR_PASSED", {"Fire id": "7c1a9e02", "Sensor ID": "required-sections", "Stage slug": "intent-capture", "Output path": f"{REC}/{INTENT_B}/ideation/intent-capture/intent-statement.md", "Duration ms": "12"})
    w.add("2026-09-01T09:12:23Z", "SUBAGENT_COMPLETED", {"Agent Type": "", "Agent ID": "a7d0c4e19f3b2a615", "Message": "Drafted the intent statement and stakeholder map for the ledger export; waiting for the gate decision."})
    w.add("2026-09-01T09:12:30Z", "STAGE_AWAITING_APPROVAL", {"Stage": "intent-capture"})
    w.add("2026-09-01T09:15:02Z", "HUMAN_TURN")
    w.add("2026-09-01T09:15:05Z", "GATE_APPROVED", {"Stage": "intent-capture", "User Input": "Approve"})
    w.add("2026-09-01T09:15:05Z", "STAGE_COMPLETED", {"Stage": "intent-capture", "Details": f"Stage {stage_name('intent-capture')} approved by gate"})
    ts = [
        ("market-research", "2026-09-01T09:15:05Z", "2026-09-01T09:31:44Z", "2026-09-01T09:35:10Z", "Approve"),
        ("feasibility", "2026-09-01T09:35:13Z", "2026-09-01T09:52:08Z", "2026-09-01T09:58:41Z", "approve"),
        ("scope-definition", "2026-09-01T09:58:44Z", "2026-09-01T10:20:19Z", "2026-09-01T10:27:03Z", "Approve — scope looks right, keep the PDF renderer optional"),
        ("team-formation", "2026-09-01T10:27:06Z", "2026-09-01T10:39:57Z", "2026-09-01T10:41:22Z", "Approve"),
        ("rough-mockups", "2026-09-01T10:41:25Z", "2026-09-01T11:02:36Z", "2026-09-01T11:09:50Z", "Approve"),
        ("approval-handoff", "2026-09-01T11:09:53Z", "2026-09-01T11:18:14Z", "2026-09-01T11:20:47Z", "Approve"),
    ]
    for slug, started, awaiting, approved, user_input in ts:
        w.add(started, "STAGE_STARTED", {"Stage": slug, "Agent": lead(slug)})
        w.add(awaiting, "STAGE_AWAITING_APPROVAL", {"Stage": slug})
        human = approved[:-3] + f"{int(approved[-3:-1]) - 3:02d}Z"
        w.add(human, "HUMAN_TURN")
        w.add(approved, "GATE_APPROVED", {"Stage": slug, "User Input": user_input})
        w.add(approved, "STAGE_COMPLETED", {"Stage": slug, "Details": f"Stage {stage_name(slug)} approved by gate"})
    w.add("2026-09-01T11:20:47Z", "PHASE_COMPLETED", {"From phase": "ideation", "To phase": "inception", "Stages completed": "7"})
    w.add("2026-09-01T11:20:47Z", "PHASE_VERIFIED", {"Phase boundary": "ideation → inception"})
    w.add("2026-09-01T11:20:47Z", "PHASE_STARTED", {"Phase": "inception", "Scope": "feature"})
    w.add("2026-09-01T11:20:47Z", "STAGE_STARTED", {"Stage": "reverse-engineering", "Agent": lead("reverse-engineering")})
    # session 2
    w.add("2026-09-02T08:30:00Z", "SESSION_STARTED", {"Source": "startup"})
    w.add("2026-09-02T08:30:20Z", "HUMAN_TURN")
    w.add("2026-09-02T08:31:00Z", "ERROR_LOGGED", {"Tool": "aidlc-state", "Command": "aidlc-state set-status reverse-engineering", "Error": "Unknown subcommand: set-status. Valid: get, set, set-skeleton-stance, checkbox, count, advance, finalize, complete-workflow, gate-start, approve, reject, revise, skip, resume, acknowledge-compaction, reuse-artifact, lookup, practices-event, practices-promote, fork, merge"})
    w.add("2026-09-02T09:40:12Z", "SUBAGENT_COMPLETED", {"Agent Type": "", "Agent ID": "a2c8e5b09d4f17a63", "Message": "Reverse engineering of the billing service is complete; nine codekb artifacts written."})
    w.add("2026-09-02T09:40:30Z", "STAGE_AWAITING_APPROVAL", {"Stage": "reverse-engineering"})
    w.add("2026-09-02T09:52:07Z", "HUMAN_TURN")
    w.add("2026-09-02T09:52:10Z", "GATE_APPROVED", {"Stage": "reverse-engineering", "User Input": "Approve"})
    w.add("2026-09-02T09:52:10Z", "STAGE_COMPLETED", {"Stage": "reverse-engineering", "Details": f"Stage {stage_name('reverse-engineering')} approved by gate"})
    w.add("2026-09-02T09:52:10Z", "STAGE_STARTED", {"Stage": "practices-discovery", "Agent": lead("practices-discovery")})
    w.add("2026-09-02T10:05:37Z", "STAGE_AWAITING_APPROVAL", {"Stage": "practices-discovery"})
    w.add("2026-09-02T10:08:44Z", "HUMAN_TURN")
    w.add("2026-09-02T10:08:47Z", "GATE_APPROVED", {"Stage": "practices-discovery", "User Input": "Approve"})
    w.add("2026-09-02T10:08:47Z", "STAGE_COMPLETED", {"Stage": "practices-discovery", "Details": f"Stage {stage_name('practices-discovery')} approved by gate"})
    w.add("2026-09-02T10:08:47Z", "STAGE_STARTED", {"Stage": "requirements-analysis", "Agent": lead("requirements-analysis")})
    w.add("2026-09-02T10:12:03Z", "DECISION_RECORDED", {"Stage": "requirements-analysis", "Decision": "How would you like to answer the Requirements Analysis questions?", "Options": "Guide me,I'll edit the file,Chat"})
    w.add("2026-09-02T10:12:30Z", "ERROR_LOGGED", {"Tool": "aidlc-log", "Command": "aidlc-log answer --stage requirements-analysis --details I'll edit the file", "Error": "Refusing to record this answer: a real human has not acted at this checkpoint this turn. Type your answer in the session (which records a human turn) before logging it."})
    w.add("2026-09-02T10:13:10Z", "HUMAN_TURN")
    w.add("2026-09-02T10:13:12Z", "QUESTION_ANSWERED", {"Stage": "requirements-analysis", "Details": "I'll edit the file"})
    w.add("2026-09-02T10:13:30Z", "ARTIFACT_CREATED", {"Tool": "Write", "File": f"{rec_b}/inception/requirements-analysis/requirements-analysis-questions.md", "Context": "inception > requirements-analysis > requirements-analysis-questions.md"})
    w.add("2026-09-02T10:31:45Z", "HUMAN_TURN")
    w.add("2026-09-02T10:31:48Z", "QUESTION_ANSWERED", {"Stage": "requirements-analysis", "Details": "Q1=B, Q2=A, Q3=C (answers written into the questions file by the user)"})
    w.add("2026-09-02T10:32:01Z", "DECISION_RECORDED", {"Stage": "requirements-analysis", "Decision": "Does this all look correct before I generate the requirements artifact?", "Options": "Looks correct,Request changes"})
    w.add("2026-09-02T10:33:20Z", "HUMAN_TURN")
    w.add("2026-09-02T10:33:22Z", "QUESTION_ANSWERED", {"Stage": "requirements-analysis", "Details": "Looks correct"})
    w.add("2026-09-02T10:41:09Z", "ARTIFACT_CREATED", {"Tool": "Write", "File": f"{rec_b}/inception/requirements-analysis/requirements.md", "Context": "inception > requirements-analysis > requirements.md"})
    w.add("2026-09-02T10:41:10Z", "SENSOR_FIRED", {"Fire id": "b04d7e61", "Sensor ID": "required-sections", "Stage slug": "requirements-analysis", "Output path": f"{REC}/{INTENT_B}/inception/requirements-analysis/requirements.md"})
    w.add("2026-09-02T10:41:10Z", "SENSOR_PASSED", {"Fire id": "b04d7e61", "Sensor ID": "required-sections", "Stage slug": "requirements-analysis", "Output path": f"{REC}/{INTENT_B}/inception/requirements-analysis/requirements.md", "Duration ms": "19"})
    w.add("2026-09-02T10:41:10Z", "SENSOR_FIRED", {"Fire id": "c93a1f58", "Sensor ID": "upstream-coverage", "Stage slug": "requirements-analysis", "Output path": f"{REC}/{INTENT_B}/inception/requirements-analysis/requirements.md"})
    w.add("2026-09-02T10:41:11Z", "SENSOR_FAILED", {"Fire id": "c93a1f58", "Sensor ID": "upstream-coverage", "Stage slug": "requirements-analysis", "Output path": f"{REC}/{INTENT_B}/inception/requirements-analysis/requirements.md", "Detail path": f"{REC}/{INTENT_B}/.aidlc-sensors/requirements-analysis/upstream-coverage-c93a1f58.md", "Findings count": "1"})
    w.add("2026-09-02T10:41:30Z", "STAGE_AWAITING_APPROVAL", {"Stage": "requirements-analysis"})
    w.add("2026-09-02T10:55:12Z", "HUMAN_TURN")
    feedback = "FR-4 must state the retention period for exported files, and the PDF export must sit behind the existing billing.export permission."
    w.add("2026-09-02T10:55:15Z", "GATE_REJECTED", {"Stage": "requirements-analysis", "Feedback": feedback})
    w.add("2026-09-02T10:55:15Z", "STAGE_REVISING", {"Stage": "requirements-analysis", "Revision count": "1", "Feedback": feedback})
    # session 3
    w.add("2026-09-03T14:02:00Z", "SESSION_STARTED", {"Source": "startup"})
    w.add("2026-09-03T14:02:15Z", "HUMAN_TURN")
    w.add("2026-09-03T14:20:33Z", "ARTIFACT_UPDATED", {"Tool": "Edit", "File": f"{rec_b}/inception/requirements-analysis/requirements.md", "Context": "inception > requirements-analysis > requirements.md"})
    w.add("2026-09-03T14:20:34Z", "SENSOR_FIRED", {"Fire id": "d1e6f207", "Sensor ID": "required-sections", "Stage slug": "requirements-analysis", "Output path": f"{REC}/{INTENT_B}/inception/requirements-analysis/requirements.md"})
    w.add("2026-09-03T14:20:34Z", "SENSOR_PASSED", {"Fire id": "d1e6f207", "Sensor ID": "required-sections", "Stage slug": "requirements-analysis", "Output path": f"{REC}/{INTENT_B}/inception/requirements-analysis/requirements.md", "Duration ms": "17"})
    w.add("2026-09-03T14:22:41Z", "STAGE_AWAITING_APPROVAL", {"Stage": "requirements-analysis", "Details": "Re-entering gate after revision"})
    w.add("2026-09-03T14:23:05Z", "DECISION_RECORDED", {"Stage": "requirements-analysis", "Decision": "Q4. Should the export job run on the existing nightly scheduler or on demand only?", "Options": "A,B,C,X"})
    h.write_text(h.out / REC / INTENT_B / "audit" / SHARD, w.text, source="synthetic", transform=f"authored ({w.count} blocks, 2.3.0 appendAuditEntryUnlocked format)")

    h.write_text(h.out / REC / INTENT_B / "ideation/intent-capture/intent-capture-questions.md",
                 """# Intent Capture — Clarifying Questions

## Sources

- [desc] Initial description: "Add a monthly ledger export (CSV and PDF) to the billing service without changing the existing reports"
- [scope] Workflow-selected scope: `feature`.

## Q1. Which business problem does the ledger export solve first?

A. Finance closes the month from a spreadsheet and re-keys totals from the web report.
B. Auditors need an immutable file per month.
C. Customers ask for a downloadable statement.
D. Not yet defined.
X. Other (please specify)

[Answer]: A

## Q2. Who is the primary user, and what do they need most?

A. Individual account owners downloading their own statement.
B. The finance team exporting all accounts for a period.
C. External auditors with read-only access.
D. Not yet identified.
X. Other (please specify)

[Answer]: B

## Q3. Which conditions must all hold for this to count as done? (multi-select)

A. CSV and PDF exports agree with the on-screen report to the cent for the same period.
B. Existing reports, endpoints and tests are unchanged.
C. Exports are retained for a fixed period and are downloadable again without recomputation.
D. Not yet defined.
X. Other (please specify)

[Answer]: A, B

## Consolidated Summary Confirmation

- The export exists so finance can close the month without re-keying totals.
- Primary user is the finance team exporting all accounts for a period; account owners are secondary.
- Done means CSV/PDF match the on-screen report to the cent and nothing existing changes.
- Retention is out of scope for this intent unless requirements-analysis raises it.

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
""", source="synthetic", transform="authored (fully answered questions file incl. Consolidated Summary Confirmation)")
    h.write_text(h.out / REC / INTENT_B / "ideation/intent-capture/intent-statement.md",
                 """# Intent Statement

## Problem

Finance closes each month from a spreadsheet, re-keying totals from the billing web report. The re-keying takes about two hours per close and has produced three reconciliation errors this year.

## Intended outcome

A monthly ledger export, available as CSV and PDF, whose totals agree with the on-screen report to the cent for the same period.

## Boundaries

- Existing reports, endpoints and tests are unchanged.
- No new external dependencies for the CSV path; the PDF renderer may be optional.
- Retention and re-download are deferred to requirements-analysis.

## Success signals

- Month-end close no longer needs the spreadsheet.
- Zero reconciliation differences between export and report for two consecutive months.
""", source="synthetic", transform="authored (small artifact)")
    h.write_text(h.out / REC / INTENT_B / "inception/requirements-analysis/requirements-analysis-questions.md",
                 """# Requirements Analysis — Clarifying Questions

Stage: `requirements-analysis` · Depth: Standard · Scope: `feature`

Upstream: `ideation/intent-capture/intent-statement.md`, `ideation/scope-definition/scope-document.md`, `codekb/repo/architecture.md`.

---

## Q1. How is the export period defined?

Background: the web report uses a calendar-month picker resolved in the browser's timezone; invoices are stored with UTC timestamps.

- A. Calendar month in the browser's timezone, matching the report exactly.
- B. Calendar month in UTC, with the effective `[from, to]` echoed in the file header.
- C. Arbitrary `[from, to]` range chosen by the user; month is only a preset.
- X. Other (please specify)

[Answer]: B

## Q2. Where does the export run?

- A. Synchronously in the request for CSV; PDF rendered by the existing worker with a download link on completion.
- B. Both formats rendered by the worker; the UI polls for completion.
- C. Both formats synchronously in the request.
- X. Other (please specify)

[Answer]: A

## Q3. How are exported files retained?

- A. Not retained; every download recomputes the export.
- B. Retained 30 days in the existing object store bucket.
- C. Retained 7 years to satisfy the audit requirement raised in feasibility.
- X. Other (please specify)

[Answer]: C

---

## Consolidated Summary Confirmation

- Q1: UTC calendar month; the effective range is echoed in the file header.
- Q2: CSV synchronous, PDF via the existing worker with a completion link.
- Q3: exports retained 7 years in the existing object store.

Does this all look correct before I generate the requirements artifact?

- Looks correct
- Request changes

[Answer]: Looks correct


## Post-approval Amendment

## Q4. Should the export job run on the existing nightly scheduler or on demand only?

- A. On demand only (user-triggered).
- B. Nightly for the previous month plus on demand.
- C. Nightly only; on-demand downloads serve the stored file.
- X. Other (please specify)

[Answer]:
""", source="synthetic", transform="authored (answered Q1-Q3 + summary confirmation; Post-approval Amendment Q4 has a BLANK [Answer]: tag)")
    h.write_text(h.out / REC / INTENT_B / "inception/requirements-analysis/requirements.md",
                 """# Requirements — Monthly Ledger Export

## Functional requirements

| ID | Requirement | Source |
|---|---|---|
| FR-1 | The user can export the ledger for a UTC calendar month as CSV. | Q1, Q2 |
| FR-2 | The user can request the same period as PDF; the PDF is rendered by the existing worker and a download link appears on completion. | Q2 |
| FR-3 | CSV and PDF totals equal the on-screen report totals for the same period to the cent. | intent-statement |
| FR-4 | Exported files are retained for 7 years in the existing object store bucket and can be downloaded again without recomputation. | Q3, gate feedback (revision 1) |
| FR-5 | The PDF export is available only to principals holding the existing `billing.export` permission. | gate feedback (revision 1) |

## Non-functional requirements

- NFR-1 CSV for a month with up to 50 000 lines completes within 5 s at p95.
- NFR-2 No change to any existing endpoint contract or test.

## Out of scope

- Custom date ranges (kept as a possible follow-up; see Q4 amendment).
- New external dependencies for the CSV path.
""", source="synthetic", transform="authored (small artifact, revised after GATE_REJECTED)")
    h.write_text(h.out / REC / INTENT_B / "inception/requirements-analysis/memory.md",
                 """# Stage Memory — requirements-analysis

## Interpretations

- 2026-09-02T10:35:00Z — "Month" read as a UTC calendar month because invoices are stored in UTC; the report's browser-timezone month is a display concern.

## Deviations

- 2026-09-03T14:20:00Z — Retention raised from "deferred" to a hard 7-year requirement after the gate was rejected; the intent statement had deferred it.

## Tradeoffs

- 2026-09-02T10:36:00Z — PDF through the worker adds latency but avoids a second renderer in the request path.

## Open questions

- 2026-09-03T14:23:00Z — Scheduler vs on-demand (Q4 amendment) still unanswered.
""", source="synthetic", transform="authored (stage diary)")


def harvest_payload() -> None:
    reset(OUT_B)
    src_230 = extract_230()
    try:
        h = Harvest(OUT_B, Scrubber([str(src_230)]))
        harvest_engine_files(h, src_230, settings=True)
        build_synthetic_v7(h, src_230)
    finally:
        shutil.rmtree(STAGING_230, ignore_errors=True)
    git_label = f"git:{GIT_AIDLC}@{GIT_TAG}:{GIT_DIST}"
    for p in h.prov:
        if p.source.startswith(str(src_230)):
            p.source = git_label + p.source[len(str(src_230)):]
    h.notes.append(f"Engine data source: `{git_label}` (258 files at the tag), extracted with `git archive` into a temporary staging dir that is deleted after the run. At harvest time `payload/aidlc-kiro` held AI-DLC **{payload_version_now()}** (re-pinned on 2026-09-04, see `payload/manifest.json`), so the payload directory is NOT the source of this fixture any more; the fixture keeps its original name because tests reference it.")
    h.notes.append("2.3.0 has NO writer for `.aidlc-active-directive.json` (grep over aidlc-lib.ts / aidlc-orchestrate.ts / aidlc-directive.ts at v2.3.0 finds nothing); the marker in 260901-ledger-export is included only to exercise the digest check and would in reality come from a 2.5+/2.6.x engine.")
    h.notes.append("2.3.0 shipped `aidlc/` shell contains only `active-space` and `spaces/default/memory/**`; there is no example intent in the distribution, so both intents here are authored.")
    readme(
        h,
        "Fixture: aidlc-2.3.0-payload",
        [
            f"Engine data copied verbatim from AI-DLC **2.3.0** `dist/kiro` (32-stage graph, **State Version 7**) at `{git_label}` — the exact tree that `payload/aidlc-kiro` contained when this fixture was specified — plus an AUTHORED, format-exact v7 workspace under `aidlc/`.",
            "",
            "Authored workspace (`aidlc/`): `active-space`, `.aidlc-clone-id`, `.aidlc-turn-counter`, `spaces/default/intents/{intents.json,active-intent}` and two intent records:",
            "",
            f"- `{INTENT_A}` — scope `poc`, **complete** (`Status: Completed`, 8/8 EXECUTE rows `[x]`, `Operation: Skipped`); audit shard ends with `PHASE_VERIFIED construction → end` + `WORKFLOW_COMPLETED`; includes a per-unit `construction/health-probe/code-generation/code-generation-questions.md` with `## Plan Approval` answered `Approve Plan` and one small artifact.",
            f"- `{INTENT_B}` — scope `feature`, **in-flight**, gate OPEN: `[?] requirements-analysis`, `Revision Count: 1`, `Status: Running`. Audit contains HUMAN_TURN, STAGE_STARTED, STAGE_AWAITING_APPROVAL (plain, and `Details: Re-entering gate after revision`), GATE_APPROVED (varied `User Input`), GATE_REJECTED (`Feedback`), STAGE_REVISING (`Revision count`, `Feedback`), DECISION_RECORDED, QUESTION_ANSWERED, ERROR_LOGGED (two real refusal texts), SENSOR_FIRED/PASSED/FAILED, ARTIFACT_CREATED/UPDATED, SUBAGENT_COMPLETED, SESSION_STARTED, PHASE_*. `.aidlc-active-directive.json` has `stage: requirements-analysis` and `state_sha256` equal to sha256 of the `aidlc-state.md` bytes (asserted at build). `intent-capture-questions.md` is fully answered; `requirements-analysis-questions.md` is answered through its Consolidated Summary Confirmation and then carries a `## Post-approval Amendment` `Q4` with a BLANK `[Answer]:` (a pending question while the gate is open; the last audit block is the matching `DECISION_RECORDED` with no `QUESTION_ANSWERED`).",
            "",
            "Both audit shards are named `fixture-host-0f1e2d3c4b5a.md` (= `<hostname>-<cloneId>.md`). No `.aidlc-recovery.md`, `.aidlc-human-turn`, `.aidlc-engine-touch` or `runtime-graph.json` were authored (2.3.0 on Kiro CLI writes none of the first three; the last is derived).",
        ],
        [],
    )


# ----------------------------------------------------------------------------- (c) devlake 2.1.1
def harvest_devlake() -> None:
    reset(OUT_C)
    h = Harvest(OUT_C, Scrubber([str(SRC_DEVLAKE)]))
    intents = SRC_DEVLAKE / "aidlc/spaces/default/intents"
    if not (SRC_DEVLAKE / "aidlc/active-space").exists():
        h.notes.append("SOURCE HAS NO `aidlc/active-space` (2.1.1 never wrote it) — deliberately not created here; readers must default to `default`.")
    h.copy(SRC_DEVLAKE / "aidlc/.aidlc-clone-id", OUT_C / "aidlc/.aidlc-clone-id")
    h.copy(intents / "intents.json", OUT_C / "aidlc/spaces/default/intents/intents.json")
    h.copy(intents / "active-intent", OUT_C / "aidlc/spaces/default/intents/active-intent")
    rec = intents / "260707-trello-plugin"

    def whole_shard(name: str, blocks: list[str]):
        # The only shard of this fixture; keep it whole so the non-taxonomy events (LOOP_RUN_STARTED,
        # RULE_LEARNED) and the gate/question/error rows from July stay in (they sit >120 blocks from the end).
        return 0, len(blocks), "whole shard"

    harvest_record(h, rec, OUT_C / "aidlc/spaces/default/intents/260707-trello-plugin", samples=[], audit_selector=whole_shard,
                   extra_files=[".aidlc-learnings-selections-intent-capture.json"])
    for name in ("stage-graph.json", "scope-grid.json", "harness.json"):
        h.copy(SRC_DEVLAKE / ".kiro/tools/data" / name, OUT_C / ".kiro/tools/data" / name)
    h.copy(SRC_DEVLAKE / ".kiro/tools/aidlc-version.ts", OUT_C / ".kiro/tools/aidlc-version.ts")
    readme(
        h,
        "Fixture: aidlc-older (AI-DLC 2.1.1, State Version 7)",
        [
            "Scrubbed copy of the single intent in the AI-DLC **2.1.1** Kiro install at `/FIXTURE/ws/devlake` (32-stage graph, **State Version 7**), harvested 2026-09-04, to exercise version tolerance.",
            "",
            "Copied: `intents.json`, `active-intent`, `aidlc/.aidlc-clone-id`, `260707-trello-plugin/aidlc-state.md` (feasibility `[-]` in progress, `Stages to Skip: 2.1 (reverse-engineering — greenfield)`, `Total Stages: 31`, TWO blank lines after `## Runtime State`, `Initialization: Active` while `Lifecycle Phase: IDEATION`), `.aidlc-recovery.md`, the WHOLE single audit shard (346 blocks; includes the non-taxonomy events `LOOP_RUN_STARTED` and `RULE_LEARNED` plus gate/question/error rows), every `*-questions.md` (`feasibility-questions.md` has SIX blank `[Answer]:` tags — the pure pending-question case; `intent-capture-questions.md` and `market-research-questions.md` are answered), the 2.1.x-style `.aidlc-learnings-selections-intent-capture.json`, `artifacts-index.txt`, and the 2.1.1 `stage-graph.json`/`scope-grid.json`/`harness.json`/`aidlc-version.ts`.",
            "",
            "Not present in the source and therefore absent here: `aidlc/active-space`, `.aidlc-active-directive.json`, `.aidlc-human-turn`, `.aidlc-engine-touch`, `.aidlc-steering-token-key`, `traceability.json`.",
        ],
        [],
    )


if __name__ == "__main__":
    harvest_devdelta()
    harvest_payload()
    harvest_devlake()
    for out in (OUT_A, OUT_B, OUT_C):
        files = sorted(p for p in out.rglob("*") if p.is_file())
        total = sum(p.stat().st_size for p in files)
        print(f"{out.relative_to(PROJECT)}: {len(files)} files, {total} bytes")

"""Studio binds the active directive to the state with the engine's own digest, not the file's bytes.

The 2.10 engine stamps ``stateDigest`` — a sha256 over the routing projection of ``aidlc-state.md`` — into
``active-directive.json``. Comparing it with the raw file hash made every directive read stale after the
next cache write, and in unit-major Construction Studio then read the first block stage while the unit
waited at Code Generation's Plan Approval (measured on a Standard run). The parity half runs the bundled
engine's own function under real Bun.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import fixtures as F
import pytest

PAYLOAD = Path(__file__).resolve().parents[1] / "payload" / "aidlc-kiro"

CASES = {
    "fixture": F.state_text(marks={"functional-design": "-"}, fields={"Current Stage": "functional-design"}),
    "cache-fields": "\n".join([
        "## Runtime State", "- **Last Updated**: 2026-10-30T23:57:58Z", "- **Active Unit**: due-dates",
        "- **Unit State**: in-progress", "", "- **Construction Iteration**: unit-major",
    ]),
    "unit-progress-table": "## Unit Progress\n| Unit | Owner |\n|---|---|\n| a | me |\nnot a row\n## Next\n- **Status**: Running",
    "renamed-guard-field": "- **Change Control**: relaxed (from scope classic)\n- **Current Stage**: x",
    "line-terminators": "- **Current Stage**: a\r\n- **Last Updated**: b\r- **Status**: c - **Depth**: d end",
    "whitespace-lines": "## A\n   \n\t\n- **Status**:  spaced  \n",
}


@pytest.fixture
def R(studio):
    return studio.aidlc_reader


@pytest.fixture(scope="module")
def engine_digests(tmp_path_factory):
    bun = shutil.which("bun")
    if bun is None:
        pytest.skip("real Bun is required to run the bundled engine")
    folder = tmp_path_factory.mktemp("digests")
    for name, text in CASES.items():
        (folder / f"{name}.md").write_bytes(text.encode("utf-8"))
    script = (
        'import { stateDigest } from "./.kiro/tools/aidlc-lib.ts";'
        'import { readFileSync, readdirSync } from "fs";'
        "const dir = process.argv[1]; const out = {};"
        'for (const f of readdirSync(dir)) out[f.replace(/\\.md$/, "")] = stateDigest(readFileSync(dir + "/" + f, "utf-8"));'
        "console.log(JSON.stringify(out));"
    )
    done = subprocess.run([bun, "-e", script, str(folder)], cwd=PAYLOAD, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("name", sorted(CASES))
def test_studio_digest_is_the_engines(R, engine_digests, name):
    assert R.engine_state_digest(CASES[name]) == engine_digests[name]


def test_a_cache_write_keeps_the_directive_and_a_routing_write_does_not(R):
    state = CASES["fixture"]
    digest = R.engine_state_digest(state)
    marker = json.dumps({"version": 2, "kind": "run-stage", "stage": "nfr-requirements", "state_sha256": digest}).encode()
    cached = F.insert_runtime_field(state, "Active Unit", "due-dates")
    routed = state.replace("- **Status**:", "- **Status**: Parked", 1)
    for text, current in ((cached, True), (routed, False)):
        raw = F.sha256(text)
        assert raw != digest
        assert R.parse_directive(marker, raw, R.engine_state_digest(text)).matches_state is current
    # A marker that bound to the file's bytes still matches them.
    legacy = json.dumps({"version": 1, "stage": "x", "state_sha256": F.sha256(state)}).encode()
    assert R.parse_directive(legacy, F.sha256(state), digest).matches_state is True

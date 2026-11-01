"""The bundled Kiro adapter reads the person's own words out of KiroCrew's prompt envelope.

KiroCrew hands kiro-cli every dashboard turn wrapped in its context blocks, ending with
``[CURRENT USER REQUEST -- respond to this]`` and the typed text, and kiro-cli passes that whole prompt
to the userPromptSubmit hook. AI-DLC's exact-text checks (a protected checkpoint's Approve, Approve Plan,
``/aidlc`` terminal verbs) then compared the envelope and refused real answers — measured on a Standard
run, where the Construction verification command could not be approved at all. Real Bun, the real
bundled adapter, a throwaway copy of the payload; never a native workflow.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAYLOAD = Path(__file__).resolve().parents[1] / "payload" / "aidlc-kiro"
ENVELOPE = (
    "[RUNTIME] KiroCrew dashboard\nThis is the interface carrying the current user message.\n\n"
    "[PROJECT] Active project directory: /tmp/project\n\n[REPLY FORMAT RULES]\n\n\n"
    "(If presenting choices, end with [OPTIONS: choice1 | choice2] as the very last line.)"
    "[CURRENT USER REQUEST -- respond to this]\n"
)


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    if shutil.which("bun") is None:
        pytest.skip("real Bun is required to run the bundled adapter")
    root = tmp_path_factory.mktemp("envelope") / "project"
    shutil.copytree(PAYLOAD, root)
    return root


def intercept(project: Path, prompt: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AIDLC_", "CLAUDE_"))}
    done = subprocess.run(
        [shutil.which("bun"), ".kiro/tools/aidlc.ts", "engine", "adapter", "kiro", "verb-intercept"],
        cwd=project, env=env, capture_output=True, text=True, timeout=60,
        input=json.dumps({"hook_event_name": "userPromptSubmit", "cwd": str(project), "prompt": prompt}),
    )
    assert done.returncode == 0, done.stderr
    # 2.11 fences command output between per-call random markers; compare what is fenced, not the nonce.
    return re.sub(r"\b[0-9A-F]{16}\b", "<marker>", done.stdout)


@pytest.mark.parametrize("header", ["--", "—"])
def test_a_command_inside_the_envelope_dispatches_like_the_typed_one(project, header):
    typed = intercept(project, "/aidlc --version")
    assert "has ALREADY been run by the harness" in typed
    wrapped = intercept(project, ENVELOPE.replace("--", header) + "/aidlc --version")
    assert wrapped == typed


def test_context_free_turns_drop_the_trailing_reply_rules(project):
    typed = intercept(project, "/aidlc --version")
    trailing = "/aidlc --version\n\n(If presenting choices, end with [OPTIONS: choice1 | choice2] as the very last line.)"
    assert intercept(project, trailing) == typed


def test_a_prompt_without_the_envelope_is_left_alone(project):
    # Plain text that only mentions the header further in is not KiroCrew's envelope boundary twice over.
    assert intercept(project, "please run /aidlc --version") == ""

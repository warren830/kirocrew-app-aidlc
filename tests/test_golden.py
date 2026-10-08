"""Golden read-model outputs, recorded from v1 (1.1.6), so each v2 slimming step can prove what it changed.

Every scenario builds a repository on disk, registers it, runs one reconciler tick, then reads every view
the UI renders: the repo, its intents, each intent's detail, map and artifacts, the action queue and each
card's detail, an install preview and a plan preview. The answers are compared with the recorded JSON
under ``tests/golden/<scenario>/``.

    GOLDEN_UPDATE=1 pytest tests/test_golden.py -q -o addopts=   # re-record after an intentional change

A difference is either a regression or an intended change; intended ones are re-recorded in the same
commit and listed in ``docs/plan.md`` under the golden change log.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

import pytest

import conftest as CT
import fixtures as F
from test_handlers_auth import APP_ROOT, call, common, routes, sv  # noqa: F401 - fixtures

GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"

STAGE = "requirements-analysis"
QUESTIONS_REL = f"inception/{STAGE}/{STAGE}-questions.md"
_FIXTURE_ONLY = shutil.ignore_patterns("_excerpts", "stage-frontmatter", "README.md")


def _state(marks: dict[str, str], **fields: str) -> str:
    values = {
        "Current Stage": STAGE,
        "Status": "Running",
        "Next Stage": "user-stories",
        "Lifecycle Phase": "INCEPTION",
        "Completed": "7",
    }
    values.update(fields)
    return F.state_text(marks=marks, fields=values)


def _copy(tree: Path) -> Callable[[Any], Path]:
    def build(builder: Any) -> Path:
        shutil.copytree(tree, builder.root, ignore=_FIXTURE_ONLY, dirs_exist_ok=True)
        return builder.with_git().build()

    return build


def _gate(builder: Any) -> Path:
    """An open gate with a reviewed artifact, plus a second intent waiting on its questions."""
    return (
        builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            "260904-gate-demo",
            state=_state({STAGE: "?"}),
            audit=F.gate_open_audit(STAGE),
            directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
            artifacts={
                f"inception/{STAGE}/requirements.md": (
                    "# Requirements\n\nOne requirement.\n\n"
                    "## Review\n\n**Reviewer**: reviewer-agent\n**Iteration**: 1\n"
                    "**Verdict**: READY\n\n### Advisory\n\n- Consider naming the store\n"
                ),
            },
        )
        .with_intent(
            "260905-question-demo",
            state=_state({STAGE: "-"}),
            audit=F.audit_text(F.audit_block("STAGE_STARTED", "2026-09-05T08:00:00Z", Stage=STAGE)),
            questions={QUESTIONS_REL: F.blank_answers(F.real_questions())},
            active=False,
        )
        .with_git()
        .build()
    )


def _trouble(builder: Any) -> Path:
    """A rejected gate under revision and an intent whose audit shows an error burst."""
    return (
        builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            "260906-revising",
            state=_state({STAGE: "R"}, **{"Revision Count": "1"}),
            audit=F.gate_rejected_audit(STAGE),
        )
        .with_intent(
            "260907-error-burst",
            state=_state({STAGE: "-"}),
            audit=F.error_burst_audit(count=3),
            active=False,
        )
        .with_git()
        .build()
    )


def _bare(builder: Any) -> Path:
    """A git repository with no AI-DLC at all: what a first install previews."""
    (builder.root / "README.md").write_text("# demo\n")
    return builder.with_git().build()


SCENARIOS: dict[str, Callable[[Any], Path]] = {
    "bare-repo": _bare,
    "devdelta-2.6.2": _copy(F.DEVDELTA),
    "payload-2.3.0": _copy(F.PAYLOAD_230),
    "older-2.1.1": _copy(F.OLDER),
    "gate-and-questions": _gate,
    "revision-and-errors": _trouble,
}


# --------------------------------------------------------------------------- #
# normalisation: only what differs between two runs of the same code
# --------------------------------------------------------------------------- #

#: File modification times (and what is derived from them) are the only inputs that change between runs.
_VOLATILE_KEYS = {"mtime", "mtime_ns", "scanned_at", "last_seen", "added_at", "checked_at", "generated_at",
                  "waiting_since", "free_space_bytes", "st_ino", "st_dev",
                  # Derived from fixture file mtimes, which are the checkout time of this worktree.
                  "engine_touch_at", "human_turn_at"}


def _normalise(value: Any, roots: list[str]) -> Any:
    if isinstance(value, dict):
        return {k: ("<volatile>" if k in _VOLATILE_KEYS else _normalise(v, roots)) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalise(v, roots) for v in value]
    if isinstance(value, str):
        for i, root in enumerate(roots):
            value = value.replace(root, f"<ROOT{i}>")
        return re.sub(r"\b\d+:\d+\b(?=$)", "<dev:ino>", value) if re.fullmatch(r"\d+:\d+", value) else value
    return value


def _read(sv: Any, routes: Any, host: Any, method: str, path: str, body: Any = None) -> dict:
    request = CT.owner_request(method, path, host=host, body=body)
    status, payload = call(sv, routes, method, path, request)
    return {"status": status, "body": payload}


def _views(sv: Any, routes: Any, host: Any, repo: Any) -> dict[str, Any]:
    rid = repo.repo_id
    out: dict[str, Any] = {
        "repo": _read(sv, routes, host, "GET", f"/repos/{rid}"),
        "intents": _read(sv, routes, host, "GET", f"/repos/{rid}/intents"),
        "actions": _read(sv, routes, host, "GET", "/actions"),
        **{f"{kind}_preview": _read(sv, routes, host, "POST", f"/repos/{rid}/transactions/preview", {"kind": kind})
           for kind in ("install", "upgrade", "uninstall")},
        "plan_preview": _read(sv, routes, host, "POST", f"/repos/{rid}/intents/plan/preview",
                              {"scope": "poc", "depth": "Standard"}),
    }
    for summary in (out["intents"]["body"] or {}).get("intents", []):
        key = summary["intent_key"]
        base = f"/repos/{rid}/intents/{key}"
        out[f"intent:{key}"] = _read(sv, routes, host, "GET", base)
        out[f"map:{key}"] = _read(sv, routes, host, "GET", f"{base}/map")
        out[f"artifacts:{key}"] = _read(sv, routes, host, "GET", f"{base}/artifacts")
    for card in (out["actions"]["body"] or {}).get("actions", []):
        aid = card["action_id"]
        out[f"action:{aid}"] = _read(sv, routes, host, "GET", f"/actions/{aid}")
    return out


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_read_model_matches_the_recorded_v1_outputs(scenario, sv, routes, fake_host, repo_builder,
                                                    kirocrew_home):
    path = SCENARIOS[scenario](repo_builder)
    repo = sv.repos.add(str(path), scenario)
    asyncio.run(sv.reconciler.tick())
    roots = [str(path.resolve()), str(path), str(kirocrew_home.resolve()), str(kirocrew_home)]
    views = {name: _normalise(view, roots) for name, view in _views(sv, routes, fake_host, repo).items()}

    folder = GOLDEN / scenario
    if UPDATE:
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        for name, view in views.items():
            (folder / f"{_file(name)}.json").write_text(json.dumps(view, indent=1, ensure_ascii=False) + "\n")
        return
    assert folder.is_dir(), f"no golden outputs for {scenario}; record them with GOLDEN_UPDATE=1"
    recorded = {p.stem: json.loads(p.read_text()) for p in folder.glob("*.json")}
    current = {_file(name): view for name, view in views.items()}
    assert sorted(current) == sorted(recorded), "the set of views changed"
    # Every view is compared before failing: stopping at the first difference hides the rest, and a
    # slimming step wants the whole blast radius in one run.
    differing = [name for name in sorted(current) if current[name] != recorded[name]]
    assert differing == [], f"{scenario}: {len(differing)} view(s) differ from the recorded v1 output: {differing}"


def _file(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)

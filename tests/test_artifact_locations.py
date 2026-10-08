"""Regression coverage for the declared output locations in open-defects.md §1."""

from __future__ import annotations

import json

import pytest

import conftest as CT
import fixtures as F
from test_handlers_auth import call, common, routes, sv  # noqa: F401
from test_projection import proj_mod, projection, repo_record  # noqa: F401

INTENT = "260910-output-locations"
BUILD_REL = f"aidlc/spaces/default/intents/{INTENT}/construction/build-and-test"
BUILD_EXTRA_INSTRUCTIONS = {"integration-test-instructions", "performance-test-instructions",
                            "security-test-instructions"}


def workspace(builder, stage, *, space="default", unit=None, test_strategy=None):
    phase = "CONSTRUCTION" if unit or stage == "build-and-test" else "INCEPTION"
    fields = {"Current Stage": stage, "Status": "Running", "Lifecycle Phase": phase,
              **({} if test_strategy is None else {"Test Strategy": test_strategy})}
    root = builder.with_engine().with_workspace(active_space=space).with_intent(
        INTENT, space=space, state=F.state_text(marks={stage: "?"}, fields=fields), audit=F.gate_open_audit(stage),
        directive={"version": 2, "kind": "run-stage", "stage": stage, "unit": unit, "state_sha256": "AUTO"},
    ).with_git().build()
    graph = json.loads((root / ".kiro/tools/data/stage-graph.json").read_text())
    return root, next(n for n in graph if n["slug"] == stage)


def write_outputs(root, rel, node):
    directory = root / rel
    directory.mkdir(parents=True, exist_ok=True)
    for name in node["produces"]:
        (directory / f"{name}.md").write_text(f"# {name}\n\nVerified output.\n")
    return directory


def artifact_findings(projection, snap):
    facts = CT.studio_module("consistency").RepoFacts(now=snap.taken_at, repo=None, install=None)
    return [f for f in projection._consistency.evaluate_intent(snap, facts) if f.code == "missing_required_artifact"]


@pytest.fixture
def findings(projection, repo_record):
    """``findings(root, space="default")``: the missing-artifact findings of a fresh snapshot."""
    def of(root, space="default"):
        return artifact_findings(projection, projection.snapshot(repo_record(root), space, INTENT))
    return of


def build_and_test(repo_builder, strategy):
    """Build-and-test with every output written, its results under the engine's ``test-results.md`` alias."""
    root, node = workspace(repo_builder, "build-and-test", test_strategy=strategy)
    directory = write_outputs(root, BUILD_REL, node)
    (directory / "build-test-results.md").rename(directory / "test-results.md")
    return root, directory


@pytest.mark.parametrize("strategy, missing", [
    ("Minimal", set()),
    ("Standard", {"integration-test-instructions"}),
    ("Comprehensive", BUILD_EXTRA_INSTRUCTIONS),
    ("", BUILD_EXTRA_INSTRUCTIONS),
    ("Custom", BUILD_EXTRA_INSTRUCTIONS),
])
def test_build_artifact_requirements_follow_the_test_strategy(repo_builder, findings, strategy, missing):
    """Build-and-Test Steps 3–7 explicitly require no extra instructions for Minimal."""
    root, directory = build_and_test(repo_builder, strategy)
    for name in BUILD_EXTRA_INSTRUCTIONS:
        (directory / f"{name}.md").unlink()
    found = findings(root)
    assert {f.params["artifact"] for f in found} == missing
    assert all(f.severity == "blocking" for f in found)


@pytest.mark.parametrize("strategy", ["Minimal", "Standard"])
@pytest.mark.parametrize("logical_name, filename", [
    ("build-instructions", "build-instructions.md"),
    ("build-and-test-summary", "build-and-test-summary.md"),
    ("build-test-results", "test-results.md"),
    ("cross-unit-traceability", "cross-unit-traceability.md"),
])
def test_lighter_test_strategies_keep_core_build_evidence_required(repo_builder, findings, strategy, logical_name,
                                                                   filename):
    root, directory = build_and_test(repo_builder, strategy)
    (directory / filename).unlink()
    assert [(f.params["artifact"], f.severity) for f in findings(root)] == [(logical_name, "blocking")]


@pytest.mark.parametrize("stage, logical_name", [("build-and-test", "build-test-results"),
                                                 ("performance-validation", "load-test-results")])
@pytest.mark.parametrize("canonical_present", [True, False])
def test_engine_test_result_filename_aliases_are_checked_exactly(repo_builder, findings, stage, logical_name,
                                                                 canonical_present):
    root, node = workspace(repo_builder, stage)
    directory = write_outputs(root, f"aidlc/spaces/default/intents/{INTENT}/{node['phase']}/{stage}", node)
    if canonical_present:
        (directory / f"{logical_name}.md").rename(directory / "test-results.md")
    assert [f.params["artifact"] for f in findings(root)] == ([] if canonical_present else [logical_name])


@pytest.mark.parametrize("space", ["default", "release"])
def test_codekb_outputs_are_evidence_and_missing_files_are_blocking(repo_builder, projection, repo_record, findings,
                                                                    space):
    root, node = workspace(repo_builder, "reverse-engineering", space=space)
    stage_rel = f"aidlc/spaces/{space}/intents/{INTENT}/inception/reverse-engineering"
    (root / stage_rel).mkdir(parents=True)
    for name in ("developer-scan.md", "memory.md"):
        (root / stage_rel / name).write_text("# Stage notes\n")
    rel = f"aidlc/spaces/{space}/codekb/{root.name}"
    directory = write_outputs(root, rel, node)
    snap = projection.snapshot(repo_record(root), space, INTENT)
    assert not artifact_findings(projection, snap)
    assert {f"{name}.md" for name in node["produces"]} <= {m.name for m in snap.stage_artifacts}
    before = projection.captured(snap, "reverse-engineering")["evidence_digest"]
    (directory / "architecture.md").write_text("# Architecture\n\nChanged design.\n")
    snap = projection.snapshot(repo_record(root), space, INTENT)
    assert projection.captured(snap, "reverse-engineering")["evidence_digest"] != before
    (directory / "architecture.md").unlink()
    (root / stage_rel / "architecture.md").write_text("# Stale copy in the wrong directory\n")
    found = findings(root, space)
    assert [(f.params["artifact"], f.severity) for f in found] == [("architecture", "blocking")]
    assert found[0].params["stage_dir"] == rel


def test_codekb_does_not_use_another_space_repo_or_symlink(repo_builder, findings):
    root, node = workspace(repo_builder, "reverse-engineering", space="release")
    wrong = write_outputs(root, f"aidlc/spaces/default/codekb/{root.name}", node)
    write_outputs(root, "aidlc/spaces/release/codekb/another-repo", node)
    expected = root / f"aidlc/spaces/release/codekb/{root.name}"
    expected.mkdir()
    (expected / "architecture.md").symlink_to(wrong / "architecture.md")
    assert len(findings(root, "release")) == 9


@pytest.mark.parametrize("stage", ["functional-design", "nfr-design", "code-generation"])
def test_current_unit_owns_its_artifacts(repo_builder, projection, repo_record, findings, stage):
    root, node = workspace(repo_builder, stage, unit="billing")
    rel = f"aidlc/spaces/default/intents/{INTENT}/construction/billing/{stage}"
    directory = write_outputs(root, rel, node)
    snap = projection.snapshot(repo_record(root), "default", INTENT)
    assert snap.stage_dir_rel == rel and not artifact_findings(projection, snap)
    assert all(m.relpath.startswith(f"{rel}/") for m in snap.stage_artifacts)
    # a neighbouring unit's copies cannot fill this unit's missing mandatory outputs
    write_outputs(root, rel.replace("/billing/", "/identity/"), node)
    for path in directory.iterdir():
        path.unlink()
    required = set(node["produces"]) - set(node.get("optional_produces", [])) - set(node.get("produces_kinds", {}))
    found = findings(root)
    assert {f.params["artifact"] for f in found} == required
    assert all(f.params["stage_dir"] == rel for f in found)


def test_practices_promotion_keeps_the_stage_drafts_required(repo_builder, findings):
    root, node = workspace(repo_builder, "practices-discovery")
    directory = write_outputs(root, f"aidlc/spaces/default/intents/{INTENT}/inception/practices-discovery", node)
    for name in ("team.md", "project.md"):
        (root / "aidlc/spaces/default/memory" / name).write_text("# Previously affirmed practices\n")
    assert not findings(root)
    (directory / "team-practices.md").unlink()
    assert [f.params["artifact"] for f in findings(root)] == ["team-practices"]


def test_unrecognized_external_output_location_does_not_invent_missing_files(repo_builder, findings):
    root, _ = workspace(repo_builder, "reverse-engineering")
    path = root / ".kiro/tools/data/stage-graph.json"
    graph = json.loads(path.read_text())
    next(n for n in graph if n["slug"] == "reverse-engineering")["outputs"] = "external build service"
    path.write_text(json.dumps(graph))
    assert not findings(root)


def test_complete_brownfield_can_submit_gate_through_api(sv, routes, fake_host, repo_builder):
    root, node = workspace(repo_builder, "reverse-engineering")
    write_outputs(root, f"aidlc/spaces/default/codekb/{root.name}", node)

    def request(method, path, body=None, expect=200):
        status, answer = call(sv, routes, method, path, CT.owner_request(method, path, host=fake_host, body=body))
        assert status == expect, answer
        return answer

    repo_id = request("POST", "/repos", {"path": str(root)}, expect=201)["repo"]["repo_id"]
    slot = f"aidlc-studio-{repo_id}-{INTENT}"
    fake_host.add_slot(slot, project=str(root), agent="aidlc", app="")
    sv.host.attach(fake_host)
    request("POST", f"/repos/{repo_id}/intents/{INTENT}/session", {"slot_key": slot})
    request("POST", f"/repos/{repo_id}/rescan", {})
    body = request("GET", "/actions")
    gates = [c for c in body["actions"] if c["type"] == "gate"]
    assert len(gates) == 1, body
    card = gates[0]
    for artifact in card["evidence"]["artifacts"]:
        request("GET", f"/repos/{repo_id}/intents/{INTENT}/artifacts/{artifact['artifact_id']}")
    request("POST", f"/actions/{card['action_id']}/submit",
            {"captured": card["captured"], "payload": {"decision": "approve"}, "client_wire_text": "Approve"})
    assert sv.storage.get("actions", card["action_id"])["status"] == "Delivering"

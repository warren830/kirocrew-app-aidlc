"""Run the bundled sensor against temporary artifacts; never drive a native workflow."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


SENSOR = (
    Path(__file__).resolve().parents[1]
    / "payload/aidlc-kiro/.kiro/tools/aidlc-sensor-traceability.ts"
)
REQUIREMENTS = ["FR1", "FR1.1", "FR2"]


@pytest.fixture
def workspace(tmp_path):
    record = tmp_path / "aidlc/spaces/default/intents/fixture"

    def write(rel, text):
        path = record / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    write("aidlc-state.md", "# AI-DLC State\n\n- **State Version**: 8\n")
    write("inception/requirements-analysis/requirements.md", "\n".join(REQUIREMENTS + ["NFR1"]))
    write("inception/units-generation/unit-of-work-dependency.md", """# Dependencies
```yaml
units:
  - name: alpha
    kind: service
    depends_on: []
  - name: beta
    kind: service
    depends_on: []
```
""")
    write("inception/units-generation/unit-of-work.md", """| Unit ID | Name |
|---|---|
| U1 | alpha |
| U2 | beta |
""")
    write("construction/alpha/functional-design/rules.md", "# Rules\n\nBR1.1: Validate input.\n")
    return SimpleNamespace(root=tmp_path, record=record, write=write)


def write_map(workspace, rows):
    return workspace.write(
        "inception/units-generation/unit-of-work-story-map.md",
        "| Upstream ID | Unit |\n|---|---|\n"
        + "".join(f"| {identifier} | {unit} |\n" for identifier, unit in rows),
    )


def add_stories(workspace):
    workspace.write(
        "inception/user-stories/stories.md",
        "# Stories\n\nUS1.1: Alpha\nAC1.1.1: Valid input.\nAC1.1.2: Invalid input.\n"
        "\nUS2.1: Beta\nAC2.1.1: Other unit.\n",
    )


def run_sensor(workspace, stage, coverage):
    bun = shutil.which("bun")
    if bun is None:
        pytest.skip("real Bun is required for the bundled traceability sensor")
    stage_dir = (
        "inception/units-generation" if stage == "units-generation"
        else f"construction/alpha/{stage}"
    )
    output = workspace.write(f"{stage_dir}/traceability.json", json.dumps({
        "stage": stage,
        "upstream_ids": list(coverage),
        "coverage": [
            {"id": identifier, "status": "OK", "target": target}
            for identifier, target in coverage.items()
        ],
    }))
    before = {p.relative_to(workspace.root): p.read_bytes()
              for p in workspace.root.rglob("*") if p.is_file()}
    # Do not inherit a caller's active project, session selection, or guard bypasses.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("AIDLC_", "CLAUDE_"))}
    env["AIDLC_PROJECT_DIR"] = str(workspace.root)
    completed = subprocess.run(
        [bun, str(SENSOR), "--stage", stage, "--output-path", str(output)],
        cwd=workspace.root, env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=20, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    after = {p.relative_to(workspace.root): p.read_bytes()
             for p in workspace.root.rglob("*") if p.is_file()}
    assert after == before, "a standalone sensor must leave the fixture unchanged"
    return json.loads(completed.stdout)


def assert_pass(result):
    assert result == {
        "pass": True, "gaps": [], "orphans": [], "missing_from_table": [],
        "missing_from_upstream_ids": [], "invalid_entries": [], "invalid_targets": [],
        "findings_count": 0,
    }


def test_units_generation_maps_requirement_groups_and_details(workspace):
    write_map(workspace, [("FR1", "U1"), ("FR1.1", "alpha"), ("FR2", "U2")])
    assert_pass(run_sensor(workspace, "units-generation", {
        "FR1": "alpha", "FR1.1": "U1", "FR2": "beta",
    }))


@pytest.mark.parametrize("failure", ["missing-row", "wrong-unit", "unknown-unit", "missing-map"])
def test_units_generation_rejects_missing_or_wrong_requirement_assignment(workspace, failure):
    rows = [("FR1", "U1"), ("FR1.1", "U1"), ("FR2", "U2")]
    if failure == "missing-row":
        rows = rows[1:]
    elif failure == "wrong-unit":
        rows[0] = ("FR1", "U2")
    elif failure == "unknown-unit":
        rows[0] = ("FR1", "U99")
    if failure != "missing-map":
        write_map(workspace, rows)
    result = run_sensor(workspace, "units-generation", {
        "FR1": "U1", "FR1.1": "U1", "FR2": "U2",
    })
    assert not result["pass"], result
    assert any(item.startswith("FR1:") for item in result["invalid_targets"]), result
    if failure == "wrong-unit":
        assert result["gaps"] == [], result
    else:
        assert "FR1" in result["gaps"], result


@pytest.mark.parametrize("target", ["U2", "U99"])
def test_units_generation_rejects_wrong_coverage_target(workspace, target):
    write_map(workspace, [(identifier, "U1") for identifier in REQUIREMENTS])
    result = run_sensor(workspace, "units-generation", {
        identifier: target if identifier == "FR1" else "U1" for identifier in REQUIREMENTS
    })
    assert not result["pass"] and result["gaps"] == [], result
    assert len(result["invalid_targets"]) == 1, result
    assert result["invalid_targets"][0].startswith("FR1:"), result


@pytest.mark.parametrize("identifier", ["FR99", "US9.9", "NFR1", "BR1.1", "CUSTOM-1"])
def test_units_generation_does_not_accept_ids_outside_selected_source(workspace, identifier):
    write_map(workspace, [(item, "U1") for item in [*REQUIREMENTS, identifier]])
    result = run_sensor(workspace, "units-generation", {
        item: "U1" for item in [*REQUIREMENTS, identifier]
    })
    assert not result["pass"], result
    assert any(item.startswith(f"{identifier}:") for item in result["invalid_targets"]), result


def test_functional_design_scopes_requirement_fallback_to_its_unit(workspace):
    # A stale FR-shaped row cannot become a source; beta's FR2 is not required by alpha.
    write_map(workspace, [("FR1, FR1.1", "U1"), ("FR2", "U2"), ("FR99", "U1")])
    assert_pass(run_sensor(workspace, "functional-design", {
        "FR1": "BR1.1", "FR1.1": "BR1.1",
    }))


@pytest.mark.parametrize("rows", [
    [("FR1", "U2"), ("FR1.1", "U2"), ("FR2", "U2")],
    [("FR99", "U1"), ("FR2", "U2")],
    [("FR1", "U99"), ("FR2", "U2")],
    [],
], ids=["other-unit", "unknown-source", "unknown-unit", "empty-map"])
def test_functional_design_rejects_map_without_requirements_for_unit(workspace, rows):
    write_map(workspace, rows)
    result = run_sensor(workspace, "functional-design", {
        identifier: "BR1.1" for identifier in REQUIREMENTS
    })
    assert not result["pass"], result
    assert 'map to unit "alpha"' in result["reason"], result


def test_functional_design_requires_all_mapped_requirements(workspace):
    write_map(workspace, [("FR1, FR1.1", "U1"), ("FR2", "U2")])
    result = run_sensor(workspace, "functional-design", {"FR1": "BR1.1"})
    assert not result["pass"], result
    assert result["missing_from_upstream_ids"] == ["FR1.1"], result


@pytest.mark.parametrize("identifier", ["FR2", "FR99", "CUSTOM-1"])
def test_functional_design_rejects_coverage_outside_units_source(workspace, identifier):
    write_map(workspace, [("FR1, FR1.1", "U1"), ("FR2", "U2")])
    result = run_sensor(workspace, "functional-design", {
        "FR1": "BR1.1", "FR1.1": "BR1.1", identifier: "BR1.1",
    })
    assert not result["pass"], result
    assert any(identifier in item for item in result["invalid_entries"]), result


@pytest.mark.parametrize("has_stories", [False, True])
def test_functional_design_preserves_requirements_fallback_without_map(workspace, has_stories):
    if has_stories:
        add_stories(workspace)
    assert_pass(run_sensor(workspace, "functional-design", {
        identifier: "BR1.1" for identifier in REQUIREMENTS
    }))


def test_existing_stories_take_precedence_over_requirements(workspace):
    add_stories(workspace)
    write_map(workspace, [("US1.1", "U1"), ("US2.1", "U2"), ("FR1", "U1")])
    assert_pass(run_sensor(workspace, "units-generation", {"US1.1": "U1", "US2.1": "beta"}))
    assert_pass(run_sensor(workspace, "functional-design", {
        "AC1.1.1": "BR1.1", "AC1.1.2": "BR1.1",
    }))


def test_existing_story_assignment_and_acceptance_coverage_failures_remain_visible(workspace):
    add_stories(workspace)
    write_map(workspace, [("US1.1", "U2"), ("US2.1", "U2")])
    result = run_sensor(workspace, "units-generation", {"US1.1": "U1", "US2.1": "U2"})
    assert not result["pass"] and result["gaps"] == [], result
    assert len(result["invalid_targets"]) == 1, result

    write_map(workspace, [("US1.1", "U1"), ("US2.1", "U2")])
    result = run_sensor(workspace, "functional-design", {"AC1.1.1": "BR1.1"})
    assert not result["pass"], result
    assert result["missing_from_upstream_ids"] == ["AC1.1.2"], result


def test_existing_empty_stories_do_not_silently_fall_back_to_requirements(workspace):
    workspace.write("inception/user-stories/stories.md", "# Stories pending\n")
    write_map(workspace, [(identifier, "U1") for identifier in REQUIREMENTS])
    for stage, target in [("units-generation", "U1"), ("functional-design", "BR1.1")]:
        result = run_sensor(workspace, stage, {identifier: target for identifier in REQUIREMENTS})
        assert not result["pass"], result
        assert "stories.md contains no traceable IDs" in result["reason"], result


@pytest.mark.parametrize("stage", ["units-generation", "functional-design"])
def test_a_requirement_map_cannot_replace_a_missing_source(workspace, stage):
    (workspace.record / "inception/requirements-analysis/requirements.md").unlink()
    write_map(workspace, [(identifier, "U1") for identifier in REQUIREMENTS])
    target = "U1" if stage == "units-generation" else "BR1.1"
    result = run_sensor(workspace, stage, {identifier: target for identifier in REQUIREMENTS})
    assert not result["pass"], result
    assert "required upstream artifact is missing:" in result["reason"], result


def test_code_generation_keeps_its_existing_story_and_rule_sources(workspace):
    add_stories(workspace)
    write_map(workspace, [("US1.1", "U1"), ("US2.1", "U2"), ("FR1", "U1")])
    workspace.write("construction/alpha/code-generation/implementation.py", "pass\n")
    target = "aidlc/spaces/default/intents/fixture/construction/alpha/code-generation/implementation.py"
    assert_pass(run_sensor(workspace, "code-generation", {
        "AC1.1.1": target, "AC1.1.2": target, "BR1.1": target,
    }))

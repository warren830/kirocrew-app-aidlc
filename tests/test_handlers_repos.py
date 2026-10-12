"""Repository and installer routes (§2.2).

Three behaviours matter more than the shapes: a 202 is not an install (``POST …/transactions`` answers with the id of
a row that already exists and writes afterwards); nothing is written without the digest the user was shown (a stale
``plan_digest`` is ``install_conflict{preview_changed}``); deleting a registration never touches the repository, and
is refused while a lease is live.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import conftest as CT
from test_handlers_auth import APP_ROOT, call, common, routes, sv  # noqa: F401 - fixtures

MANIFEST = json.loads((APP_ROOT / "payload" / "manifest.json").read_text("utf-8"))
INTENT = "260904-gate-demo"
REPO_KEYS = {"repo_id", "label", "canonical_path", "resolved_identity", "git_common_dir_identity", "platform",
             "added_at", "last_seen", "availability", "availability_detail", "archived", "legacy_console_id",
             "install_status", "installed_engine_version", "engine_dir", "receipt_version", "install", "counts",
             "leases", "git", "findings", "scanned_at"}
INSTALL_KEYS = {"status", "engine_dir", "engine_version", "own_engine_version", "engine_state_version", "stage_count",
                "harness_dirs", "receipt", "bundled_engine_version", "upgrade_available", "newer_installed",
                "state_version_blocked", "drift_count", "rollback_target"}
PREFLIGHT_KEYS = {"path_input", "canonical_path", "identity", "is_directory", "sensitive", "duplicate_of", "platform",
                  "git", "bun", "harness_dirs", "aidlc", "symlinks_at_managed_paths", "free_space_bytes", "writable",
                  "existing_receipt", "warnings", "can_register", "can_install"}


@pytest.fixture
def api(sv, routes, fake_host):
    """``api(method, path, body=None, **query) -> (status, body)``: one owner request through the route table."""
    def send(method: str, path: str, body: Any = None, **query: str) -> tuple[int, Any]:
        return call(sv, routes, method, path, CT.owner_request(method, path, host=fake_host, body=body, query=query))
    return send


@pytest.fixture
def demo(sv, gate_repo):
    """The real harvested repository, registered."""
    return sv.repos.add(str(gate_repo), "demo")


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    """A directory with no AI-DLC in it: not the only thing ``install`` may be offered for (``foreign``)."""
    root = tmp_path / "bare-repo"
    root.mkdir()
    (root / "README.md").write_text("# nothing here yet\n")
    return root


@pytest.fixture
def bare_repo(sv, bare):
    return sv.repos.add(str(bare), "bare")


@pytest.fixture
def foreign(tmp_path: Path):
    """``foreign(version)``: AI-DLC under a harness Studio does not manage — read and dispatched, never Studio's."""
    def build(version: str = MANIFEST["engineVersion"]) -> Path:
        tools = tmp_path / f"foreign-{version}" / ".claude" / "tools"
        tools.mkdir(parents=True)
        (tools / "aidlc-version.ts").write_text(f'export const AIDLC_VERSION = "{version}";\n')
        return tmp_path / f"foreign-{version}"

    return build


@pytest.fixture
def foreign_repo(sv, foreign):
    return sv.repos.add(str(foreign()), "foreign")


def refusal(answer: tuple[int, Any]) -> tuple[int, str]:
    return answer[0], answer[1]["code"]


def preview(api: Any, repo: Any, kind: str) -> tuple[int, Any]:
    return api("POST", f"/repos/{repo.repo_id}/transactions/preview", {"kind": kind})


def begin(api: Any, repo: Any, body: dict) -> tuple[int, Any]:
    return api("POST", f"/repos/{repo.repo_id}/transactions", body)


# --- list and detail ---
def test_repos_is_empty_before_anything_is_registered(api):
    assert api("GET", "/repos") == (200, {"repos": [], "totals": {"repos": 0, "unavailable": 0, "open_actions": 0}})


def test_a_registered_repo_carries_the_full_record(api, demo, gate_repo):
    body = api("GET", "/repos")[1]
    record = body["repos"][0]
    assert set(record) == REPO_KEYS and set(record["install"]) == INSTALL_KEYS
    assert (record["label"], record["canonical_path"], record["availability"]) == ("demo", str(gate_repo), "available")
    install = record["install"]
    # every payload-derived number comes from the manifest (C21)
    assert install["bundled_engine_version"] == install["engine_version"] == MANIFEST["engineVersion"]
    assert install["own_engine_version"] == MANIFEST["engineVersion"]
    assert install["upgrade_available"] is False and install["newer_installed"] is False
    assert install["state_version_blocked"] is False
    assert set(record["counts"]) == {"intents", "in_flight", "open_actions", "blocking_findings"}
    assert record["counts"]["intents"] == 1 and record["leases"] == {"execution": None, "admin": None}
    assert body["totals"] == {"repos": 1, "unavailable": 0, "open_actions": 0}


def test_the_list_does_not_observe_git_and_the_detail_does(api, demo):
    """A 64-repository list must not be 64 subprocesses; the page a user opens on purpose may be."""
    assert api("GET", "/repos")[1]["repos"][0]["git"] is None
    detail = api("GET", f"/repos/{demo.repo_id}")[1]
    assert detail["repo"]["git"]["available"] in (True, False)
    assert set(detail) == {"repo", "intents", "transactions"}
    assert detail["intents"][0]["intent_key"] == INTENT and detail["transactions"] == []


def test_archived_repositories_are_hidden_unless_asked_for(sv, api, demo):
    sv.storage.update("repos", demo.repo_id, {"archived": 1})
    assert api("GET", "/repos")[1]["repos"] == []
    assert api("GET", "/repos", include_archived="1")[1]["repos"][0]["archived"] is True


def test_an_unknown_repo_is_a_404(api):
    assert refusal(api("GET", "/repos/r_000000000009")) == (404, "repo_not_found")


# --- preflight and registration ---
def test_preflight_reports_the_candidate_without_registering_it(sv, api, gate_repo):
    status, body = api("POST", "/repos/preflight", {"path": str(gate_repo)})
    assert status == 200 and set(body["preflight"]) == PREFLIGHT_KEYS
    assert body["preflight"]["can_register"] is True and body["preflight"]["aidlc"]["intents"] == 1
    assert sv.repos.list() == []


def test_preflight_refuses_a_relative_path(api):
    assert refusal(api("POST", "/repos/preflight", {"path": "repo"})) == (400, "bad_path")


def test_preflight_needs_a_path(api):
    status, body = api("POST", "/repos/preflight", {})
    assert (status, body["code"], body["details"]["missing"]) == (400, "bad_body", ["path"])


def test_adding_a_repo_returns_the_record_and_the_preflight(sv, api, gate_repo):
    status, body = api("POST", "/repos", {"path": str(gate_repo), "label": "demo"})
    assert status == 201 and body["ok"] is True
    assert set(body["repo"]) == REPO_KEYS and body["repo"]["label"] == "demo"
    assert set(body["preflight"]) == PREFLIGHT_KEYS
    assert [record.repo_id for record in sv.repos.list()] == [body["repo"]["repo_id"]]
    assert any(record.type == "repo.updated" for record in asyncio.run(sv.events.since(0)))


def test_registering_the_same_directory_twice_names_the_row_that_owns_it(api, demo, gate_repo):
    status, body = api("POST", "/repos", {"path": str(gate_repo)})
    assert (status, body["code"], body["details"]["repo_id"]) == (409, "duplicate_identity", demo.repo_id)


def test_registering_a_protected_directory_is_refused(api):
    ssh = Path.home() / ".ssh"
    if not ssh.is_dir():
        pytest.skip("no protected directory available to point at on this machine")
    assert refusal(api("POST", "/repos", {"path": str(ssh)})) == (403, "sensitive_path")


def test_removing_a_registration_never_touches_the_repository(sv, api, demo, gate_repo):
    assert api("DELETE", f"/repos/{demo.repo_id}") == (200, {"ok": True, "removed": demo.repo_id})
    assert sv.repos.list() == []
    # FR-REP-007: the files are the user's, and unregistering is a Studio-only act
    assert (gate_repo / "aidlc" / "spaces" / "default" / "intents" / INTENT / "aidlc-state.md").is_file()
    assert (gate_repo / ".kiro").is_dir()


def test_removing_a_registration_is_refused_while_a_lease_is_live(sv, api, demo):
    asyncio.run(sv.scheduler.acquire_admin(demo, operation_type="install", transaction_id="tx_1"))
    status, body = api("DELETE", f"/repos/{demo.repo_id}")
    assert (status, body["code"], body["details"]["kind"]) == (409, "repo_busy", "admin")
    assert sv.repos.list() != []


def test_rebinding_an_available_repository_is_refused(api, demo, tmp_path):
    (tmp_path / "moved").mkdir()
    answer = api("POST", f"/repos/{demo.repo_id}/rebind", {"path": str(tmp_path / "moved")})
    assert refusal(answer) == (409, "rebind_not_allowed")


def test_rescan_reports_availability_install_and_the_intents(api, demo):
    status, body = api("POST", f"/repos/{demo.repo_id}/rescan", {})
    assert status == 200 and body["ok"] is True
    scan = body["scan"]
    assert set(scan) == {"repo_id", "availability", "install", "intents", "findings", "took_ms", "truncated",
                         "scanned_at"}
    assert scan["availability"] == "available" and [i["intent_key"] for i in scan["intents"]] == [INTENT]


# --- doctor ---
def test_doctor_requires_an_explicit_confirmation_because_it_appends_audit_rows(api, demo):
    status, body = api("POST", f"/repos/{demo.repo_id}/doctor", {})
    assert (status, body["code"], body["details"]["reason"]) == (400, "invalid_decision", "confirm_required")


def test_doctor_runs_the_engine_verb_under_an_admin_lease_and_releases_it(sv, api, demo):
    status, body = api("POST", f"/repos/{demo.repo_id}/doctor", {"confirm": True})
    assert status == 200 and body["ok"] is True
    assert body["result"]["verb_key"] == "utility.doctor" and body["result"]["ok"] is True
    assert sv.scheduler.live() == []


# --- installer ---
def test_install_preview_offers_a_plan_and_its_digest_for_a_bare_directory(api, bare_repo):
    status, body = preview(api, bare_repo, "install")
    assert status == 200
    plan = body["plan"]
    assert (plan["kind"], plan["engine_to"], plan["engine_from"]) == ("install", MANIFEST["engineVersion"], None)
    assert len(plan["entries"]) > 0
    assert body["plan_digest"] and len(body["plan_digest"]) == 64


@pytest.mark.parametrize("target, kind, code", [("demo", "install", "already_installed"),
                                                ("demo", "upgrade", "same_version_installed"),
                                                ("bare_repo", "upgrade", "not_installed"),
                                                ("foreign_repo", "upgrade", "not_installed")])
def test_a_preview_the_installed_engine_rules_out_is_refused(api, request, target, kind, code):
    """A foreign ``.claude`` has nothing to upgrade: no receipt, no ``.kiro`` tree, no file of Studio's."""
    assert refusal(preview(api, request.getfixturevalue(target), kind)) == (409, code)


def test_install_preview_is_offered_when_only_a_harness_studio_does_not_manage_is_present(api, foreign_repo):
    """A ``.claude`` repo at the payload's own version may still get Studio's ``.kiro`` (FR-INST-003)."""
    status, body = preview(api, foreign_repo, "install")
    assert status == 200 and (body["plan"]["engine_from"], body["plan"]["kind"]) == (None, "install")
    assert all(not e["path"].startswith(".claude/") for e in body["plan"]["entries"])


def test_the_install_block_reports_studios_own_harness_version_and_no_upgrade_for_a_stranger(sv, api, foreign):
    """``upgrade_available`` is about Studio's own harness, so an older ``.claude`` does not raise it (FR-INST-014)."""
    sv.repos.add(str(foreign("2.6.2")), "foreign")
    install = api("GET", "/repos")[1]["repos"][0]["install"]
    assert install["status"] == "installed" and install["engine_dir"] == ".claude"
    assert install["engine_version"] == "2.6.2" and install["engine_version"] != MANIFEST["engineVersion"]
    assert install["own_engine_version"] is None
    assert install["upgrade_available"] is False and install["newer_installed"] is False
    assert [h["dir"] for h in install["harness_dirs"]] == [".claude"]


def test_install_answers_202_with_a_transaction_that_already_exists(sv, api, bare_repo, monkeypatch):
    plan = sv.installer.preview(bare_repo, "install")
    ran: list[tuple[str, str]] = []

    async def record_run(repo_arg, kind, *, plan_digest, transaction_id=None):
        ran.append((kind, transaction_id))
        return sv.installer.transaction(transaction_id)

    # the transaction is test_installer.py's subject; this route owns the 202, the pollable id and the hand-off
    monkeypatch.setattr(sv.installer, "run", record_run)
    status, body = begin(api, bare_repo, {"kind": "install", "plan_digest": plan.digest()})
    assert status == 202 and body["ok"] is True and body["status"] == "staged"
    assert body["transaction_id"].startswith("tx_")
    assert sv.installer.transaction(body["transaction_id"]).kind == "install"
    status, seen = api("GET", f"/repos/{bare_repo.repo_id}/transactions/{body['transaction_id']}")
    assert status == 200 and seen["transaction"]["transaction_id"] == body["transaction_id"]


def test_install_refuses_a_digest_the_repository_has_moved_past(api, bare_repo):
    status, body = begin(api, bare_repo, {"kind": "install", "plan_digest": "0" * 64})
    assert (status, body["code"], body["details"]["reason"]) == (409, "install_conflict", "preview_changed")


def test_a_transaction_needs_its_kind_and_a_plan_digest(api, bare_repo):
    status, body = begin(api, bare_repo, {"kind": "install"})
    assert (status, body["code"], body["details"]["missing"]) == (400, "bad_body", ["plan_digest"])
    status, body = begin(api, bare_repo, {})
    assert status == 400 and body["details"]["missing"] == ["kind", "plan_digest"]
    status, body = preview(api, bare_repo, "reinstall")
    assert (status, body["code"]) == (400, "bad_body")
    assert body["details"] == {"key": "kind", "choices": ["install", "upgrade", "recovery", "uninstall", "rollback"]}


def test_every_install_path_refuses_while_the_payload_is_degraded(sv, api, bare_repo):
    sv.payload_error = "payload_degraded"
    for kind in ("install", "upgrade", "recovery"):
        for answer in (preview(api, bare_repo, kind), begin(api, bare_repo, {"kind": kind, "plan_digest": "0" * 64})):
            assert refusal(answer) == (409, "payload_degraded"), kind


def test_recovery_preview_is_offered_for_an_installed_repository(sv, api, demo):
    """``install_recovery_required`` is not an error here — recovery is exactly what it needs."""
    sv.storage.update("repos", demo.repo_id, {"install_status": "recovery_required"})
    status, body = preview(api, demo, "recovery")
    assert status == 200 and body["plan"]["kind"] == "recovery"


def test_recovery_preview_is_offered_when_the_failed_install_left_no_readable_engine(sv, api, bare_repo):
    """Recover is the one action left after a crash between ``backup`` and ``write``: its preview must not refuse."""
    sv.storage.update("repos", bare_repo.repo_id, {"install_status": "recovery_required"})
    status, body = preview(api, bare_repo, "recovery")
    assert status == 200 and body["plan"]["kind"] == "recovery", body
    assert body["plan"]["engine_from"] is None, "nothing readable is installed, and recovery is still offered"
    # the waiver is scoped to recovery: upgrade on the same repository still refuses
    assert refusal(preview(api, bare_repo, "upgrade")) == (409, "not_installed")


# --- transactions, git ---
def test_an_unknown_transaction_is_a_404(api, demo):
    answer = api("GET", f"/repos/{demo.repo_id}/transactions/tx_0000000000000009")
    assert refusal(answer) == (404, "transaction_not_found")


def test_repo_git_reports_the_observation_and_the_owned_dirty_split(api, demo):
    """FR-GIT-004: owned files are named, the user's own work only counted (``fake_git`` answers its defaults)."""
    status, body = api("GET", f"/repos/{demo.repo_id}/git")
    assert status == 200 and set(body) == {"git", "owned_dirty", "unrelated_dirty"}
    assert body["git"]["branch"] == "main"
    assert body["owned_dirty"] == []  # no receipt yet, so Studio owns nothing: an unrelated dirty file is never named
    assert isinstance(body["unrelated_dirty"], int)


def test_repo_git_still_answers_once_a_receipt_exists(sv, api, demo):
    """The receipt branch of the same handler (it once read a field that does not exist and answered 500)."""
    sv.storage.insert("install_receipts", {
        "receipt_id": "rc_1", "repo_id": demo.repo_id, "resolved_repo_identity": demo.resolved_identity,
        "studio_version": "1.0.0", "engine_version": "2.7.1", "payload_digest": "d" * 64,
        "committed_at": "2026-09-09T00:00:00Z", "prior_receipt_id": None, "transaction_id": "tx_1",
        "files_json": [{"path": ".kiro/tools/aidlc-utility.ts", "ownership": "framework", "kind": "file",
                        "sha256": "a" * 64, "canonical_digest": None, "fragment_key": None, "adopted": False}],
        "status": "current"})
    status, body = api("GET", f"/repos/{demo.repo_id}/git")
    assert status == 200, body
    assert set(body) == {"git", "owned_dirty", "unrelated_dirty"} and isinstance(body["owned_dirty"], list)


def test_a_sent_decision_counts_as_in_flight_not_as_waiting_for_you(api, sv, demo):
    """The Action Center hides a sent decision, so the repo's "waiting for you" must not count it either."""
    from test_handlers_misc import _seed_gate_card

    card = _seed_gate_card(sv, demo)
    record = api("GET", "/repos")[1]["repos"][0]
    assert (record["counts"]["open_actions"], record["counts"]["in_flight"]) == (1, 0)
    sv.storage.cas_update("actions", card.action_id, card.status_generation, {"status": "Delivered"})
    body = api("GET", "/repos")[1]
    assert (body["repos"][0]["counts"]["open_actions"], body["repos"][0]["counts"]["in_flight"]) == (0, 1)
    assert body["totals"]["open_actions"] == 0

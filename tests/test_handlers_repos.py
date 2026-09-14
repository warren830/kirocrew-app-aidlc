"""Repository and installer routes (§2.2).

The shapes matter, but three behaviours matter more and each has a test that would fail loudly if it
regressed:

* **A 202 is not an install.** ``POST …/install`` answers with a transaction id and the row it points at
  already exists; the writing happens afterwards. A test that accepted a 200 would be endorsing a
  request that blocks a user's browser for the length of a whole-payload transaction.
* **Nothing is written without the digest the user was shown.** A stale ``plan_digest`` is
  ``install_conflict{preview_changed}``, not a "close enough" install.
* **Deleting a registration never touches the repository**, and is refused while a lease is live.
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

_REPO_KEYS = {
    "repo_id", "label", "canonical_path", "resolved_identity", "git_common_dir_identity", "platform",
    "added_at", "last_seen", "availability", "availability_detail", "archived", "legacy_console_id",
    "install_status", "installed_engine_version", "engine_dir", "receipt_version",
    "install", "counts", "leases", "git", "findings", "scanned_at",
}
_INSTALL_KEYS = {
    "status", "engine_dir", "engine_version", "own_engine_version", "engine_state_version",
    "stage_count", "harness_dirs", "receipt", "bundled_engine_version", "upgrade_available",
    "newer_installed", "state_version_blocked", "drift_count", "rollback_target",
}


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    """A directory with no AI-DLC in it: not the only thing ``install`` may be offered for (``foreign``)."""
    root = tmp_path / "bare-repo"
    root.mkdir()
    (root / "README.md").write_text("# nothing here yet\n")
    return root


@pytest.fixture
def foreign(tmp_path: Path):
    """Factory: ``foreign(version)`` → a repository whose AI-DLC lives under a harness Studio does not manage.

    A hand-installed ``.claude`` tree is a real and supported shape — Studio reads and dispatches it —
    but it is nobody's *install* of Studio's, so the install lane must ignore its version.
    """

    def build(version: str = MANIFEST["engineVersion"]) -> Path:
        root = tmp_path / f"foreign-{version}"
        tools = root / ".claude" / "tools"
        tools.mkdir(parents=True)
        (tools / "aidlc-version.ts").write_text(f'export const AIDLC_VERSION = "{version}";\n')
        return root

    return build


# --------------------------------------------------------------------------- #
# list and detail
# --------------------------------------------------------------------------- #


def test_repos_is_empty_before_anything_is_registered(sv, routes, fake_host):
    status, body = call(sv, routes, "GET", "/repos", CT.owner_request("GET", "/repos", host=fake_host))
    assert status == 200
    assert body == {"repos": [], "totals": {"repos": 0, "unavailable": 0, "open_actions": 0}}


def test_a_registered_repo_carries_the_full_record(sv, routes, fake_host, gate_repo):
    sv.repos.add(str(gate_repo), "demo")
    _status, body = call(sv, routes, "GET", "/repos",
                         CT.owner_request("GET", "/repos", host=fake_host))
    record = body["repos"][0]
    assert set(record) == _REPO_KEYS
    assert record["label"] == "demo"
    assert record["canonical_path"] == str(gate_repo)
    assert record["availability"] == "available"
    assert set(record["install"]) == _INSTALL_KEYS
    # Every payload-derived number comes from the manifest (C21).
    assert record["install"]["bundled_engine_version"] == MANIFEST["engineVersion"]
    assert record["install"]["engine_version"] == MANIFEST["engineVersion"]
    assert record["install"]["own_engine_version"] == MANIFEST["engineVersion"]
    assert record["install"]["upgrade_available"] is False
    assert record["install"]["newer_installed"] is False
    assert record["install"]["state_version_blocked"] is False
    assert set(record["counts"]) == {"intents", "in_flight", "open_actions", "blocking_findings"}
    assert record["counts"]["intents"] == 1
    assert record["leases"] == {"execution": None, "admin": None}
    assert body["totals"] == {"repos": 1, "unavailable": 0, "open_actions": 0}


def test_the_list_does_not_observe_git_and_the_detail_does(sv, routes, fake_host, gate_repo):
    """A 64-repository list must not be 64 subprocesses; the page a user opens on purpose may be."""
    repo = sv.repos.add(str(gate_repo), "demo")
    _status, listing = call(sv, routes, "GET", "/repos",
                            CT.owner_request("GET", "/repos", host=fake_host))
    assert listing["repos"][0]["git"] is None

    path = f"/repos/{repo.repo_id}"
    _status, detail = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert detail["repo"]["git"]["available"] in (True, False)
    assert set(detail) == {"repo", "intents", "transactions"}
    assert detail["intents"][0]["intent_key"] == INTENT
    assert detail["transactions"] == []


def test_archived_repositories_are_hidden_unless_asked_for(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    sv.storage.update("repos", repo.repo_id, {"archived": 1})
    _status, body = call(sv, routes, "GET", "/repos",
                         CT.owner_request("GET", "/repos", host=fake_host))
    assert body["repos"] == []

    request = CT.owner_request("GET", "/repos", host=fake_host, query={"include_archived": "1"})
    _status, body = call(sv, routes, "GET", "/repos", request)
    assert body["repos"][0]["archived"] is True


def test_an_unknown_repo_is_a_404(sv, routes, fake_host):
    status, body = call(sv, routes, "GET", "/repos/r_000000000009",
                        CT.owner_request("GET", "/repos/r_000000000009", host=fake_host))
    assert status == 404 and body["code"] == "repo_not_found"


# --------------------------------------------------------------------------- #
# preflight and registration
# --------------------------------------------------------------------------- #

_PREFLIGHT_KEYS = {
    "path_input", "canonical_path", "identity", "is_directory", "sensitive", "duplicate_of",
    "platform", "git", "bun", "harness_dirs", "aidlc", "symlinks_at_managed_paths",
    "free_space_bytes", "writable", "existing_receipt", "warnings", "can_register", "can_install",
}


def test_preflight_reports_the_candidate_without_registering_it(sv, routes, fake_host, gate_repo):
    request = CT.owner_request("POST", "/repos/preflight", host=fake_host,
                               body={"path": str(gate_repo)})
    status, body = call(sv, routes, "POST", "/repos/preflight", request)
    assert status == 200
    assert set(body["preflight"]) == _PREFLIGHT_KEYS
    assert body["preflight"]["can_register"] is True
    assert body["preflight"]["aidlc"]["intents"] == 1
    assert sv.repos.list() == []


def test_preflight_refuses_a_relative_path(sv, routes, fake_host):
    request = CT.owner_request("POST", "/repos/preflight", host=fake_host, body={"path": "repo"})
    status, body = call(sv, routes, "POST", "/repos/preflight", request)
    assert status == 400 and body["code"] == "bad_path"


def test_preflight_needs_a_path(sv, routes, fake_host):
    request = CT.owner_request("POST", "/repos/preflight", host=fake_host, body={})
    status, body = call(sv, routes, "POST", "/repos/preflight", request)
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["missing"] == ["path"]


def test_adding_a_repo_returns_the_record_and_the_preflight(sv, routes, fake_host, gate_repo):
    request = CT.owner_request("POST", "/repos", host=fake_host,
                               body={"path": str(gate_repo), "label": "demo"})
    status, body = call(sv, routes, "POST", "/repos", request)
    assert status == 201
    assert body["ok"] is True
    assert set(body["repo"]) == _REPO_KEYS
    assert body["repo"]["label"] == "demo"
    assert set(body["preflight"]) == _PREFLIGHT_KEYS
    assert [record.repo_id for record in sv.repos.list()] == [body["repo"]["repo_id"]]
    assert any(kind == "repo.updated" for kind, _ in _events(sv))


def test_registering_the_same_directory_twice_names_the_row_that_owns_it(sv, routes, fake_host,
                                                                        gate_repo):
    first = sv.repos.add(str(gate_repo), "demo")
    request = CT.owner_request("POST", "/repos", host=fake_host, body={"path": str(gate_repo)})
    status, body = call(sv, routes, "POST", "/repos", request)
    assert status == 409 and body["code"] == "duplicate_identity"
    assert body["details"]["repo_id"] == first.repo_id


def test_registering_a_protected_directory_is_refused(sv, routes, fake_host):
    ssh = Path.home() / ".ssh"
    if not ssh.is_dir():
        pytest.skip("no protected directory available to point at on this machine")
    request = CT.owner_request("POST", "/repos", host=fake_host, body={"path": str(ssh)})
    status, body = call(sv, routes, "POST", "/repos", request)
    assert status == 403 and body["code"] == "sensitive_path"


def test_removing_a_registration_never_touches_the_repository(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    path = f"/repos/{repo.repo_id}"
    status, body = call(sv, routes, "DELETE", path, CT.owner_request("DELETE", path, host=fake_host))
    assert status == 200 and body == {"ok": True, "removed": repo.repo_id}
    assert sv.repos.list() == []
    # FR-REP-007: the files are the user's, and unregistering is a Studio-only act.
    assert (gate_repo / "aidlc" / "spaces" / "default" / "intents" / INTENT / "aidlc-state.md").is_file()
    assert (gate_repo / ".kiro").is_dir()


def test_removing_a_registration_is_refused_while_a_lease_is_live(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    asyncio.run(
        sv.scheduler.acquire_admin(repo, operation_type="install", transaction_id="tx_1")
    )
    path = f"/repos/{repo.repo_id}"
    status, body = call(sv, routes, "DELETE", path, CT.owner_request("DELETE", path, host=fake_host))
    assert status == 409 and body["code"] == "repo_busy"
    assert body["details"]["kind"] == "admin"
    assert sv.repos.list() != []


def test_rebinding_an_available_repository_is_refused(sv, routes, fake_host, gate_repo, tmp_path):
    repo = sv.repos.add(str(gate_repo), "demo")
    other = tmp_path / "moved"
    other.mkdir()
    path = f"/repos/{repo.repo_id}/rebind"
    request = CT.owner_request("POST", path, host=fake_host, body={"path": str(other)})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 409 and body["code"] == "rebind_not_allowed"


def test_rescan_reports_availability_install_and_the_intents(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    path = f"/repos/{repo.repo_id}/rescan"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 200 and body["ok"] is True
    scan = body["scan"]
    assert set(scan) == {"repo_id", "availability", "install", "intents", "findings", "took_ms",
                         "truncated", "scanned_at"}
    assert scan["availability"] == "available"
    assert [intent["intent_key"] for intent in scan["intents"]] == [INTENT]


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def test_doctor_requires_an_explicit_confirmation_because_it_appends_audit_rows(sv, routes, fake_host,
                                                                               gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    path = f"/repos/{repo.repo_id}/doctor"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 400 and body["code"] == "invalid_decision"
    assert body["details"]["reason"] == "confirm_required"


def test_doctor_runs_the_engine_verb_under_an_admin_lease_and_releases_it(sv, routes, fake_host,
                                                                         gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    path = f"/repos/{repo.repo_id}/doctor"
    request = CT.owner_request("POST", path, host=fake_host, body={"confirm": True})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 200 and body["ok"] is True
    assert body["result"]["verb_key"] == "utility.doctor"
    assert body["result"]["ok"] is True
    assert sv.storage.lease_list() == []


# --------------------------------------------------------------------------- #
# installer
# --------------------------------------------------------------------------- #


def test_install_preview_offers_a_plan_and_its_digest_for_a_bare_directory(sv, routes, fake_host, bare):
    repo = sv.repos.add(str(bare), "bare")
    path = f"/repos/{repo.repo_id}/install/preview"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 200
    assert body["plan"]["kind"] == "install"
    assert body["plan"]["engine_to"] == MANIFEST["engineVersion"]
    assert body["plan"]["engine_from"] is None
    assert len(body["plan"]["entries"]) > 0
    assert body["plan_digest"] and len(body["plan_digest"]) == 64


def test_install_preview_refuses_a_repository_that_already_has_the_engine(sv, routes, fake_host,
                                                                         gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    path = f"/repos/{repo.repo_id}/install/preview"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 409 and body["code"] == "already_installed"


def test_upgrade_preview_refuses_when_the_bundled_engine_is_already_installed(sv, routes, fake_host,
                                                                             gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    path = f"/repos/{repo.repo_id}/upgrade/preview"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 409 and body["code"] == "same_version_installed"


def test_upgrade_preview_refuses_a_repository_with_nothing_installed(sv, routes, fake_host, bare):
    repo = sv.repos.add(str(bare), "bare")
    path = f"/repos/{repo.repo_id}/upgrade/preview"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 409 and body["code"] == "not_installed"


def test_install_preview_is_offered_when_only_a_harness_studio_does_not_manage_is_present(
    sv, routes, fake_host, foreign
):
    """A ``.claude`` repository at the payload's own version is still a repository Studio may install into.

    Both refusals used to fire at once here — ``already_installed`` on the install lane because
    *something* was installed, ``same_version_installed`` on the upgrade lane because the stranger was
    the payload's version — which left no route at all for adding Studio's ``.kiro`` (FR-INST-003).
    """
    repo = sv.repos.add(str(foreign()), "foreign")
    path = f"/repos/{repo.repo_id}/install/preview"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 200
    assert body["plan"]["engine_from"] is None
    assert body["plan"]["kind"] == "install"
    assert all(not e["path"].startswith(".claude/") for e in body["plan"]["entries"])


def test_upgrade_preview_refuses_a_repository_where_only_a_foreign_harness_is_installed(
    sv, routes, fake_host, foreign
):
    """There is nothing to upgrade: no receipt, no ``.kiro`` tree, and none of those files are Studio's."""
    repo = sv.repos.add(str(foreign()), "foreign")
    path = f"/repos/{repo.repo_id}/upgrade/preview"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 409 and body["code"] == "not_installed"


def test_the_install_block_reports_studios_own_harness_version_and_no_upgrade_for_a_stranger(
    sv, routes, fake_host, foreign
):
    """``upgrade_available`` is about Studio's harness, so an older ``.claude`` does not raise it.

    Keying it on ``engine_version`` offered "Upgrade" for a tree Studio has no receipt for and whose
    files no plan would touch; the honest affordance for this repository is Install (FR-INST-014).
    ``status`` and ``engine_dir`` still describe the harness Studio reads, and ``harness_dirs`` still
    names it, so the detail view can say which stranger it found.
    """
    sv.repos.add(str(foreign("2.6.2")), "foreign")
    _status, body = call(sv, routes, "GET", "/repos",
                         CT.owner_request("GET", "/repos", host=fake_host))
    install = body["repos"][0]["install"]
    assert install["status"] == "installed" and install["engine_dir"] == ".claude"
    assert install["engine_version"] == "2.6.2" and install["engine_version"] != MANIFEST["engineVersion"]
    assert install["own_engine_version"] is None
    assert install["upgrade_available"] is False and install["newer_installed"] is False
    assert [h["dir"] for h in install["harness_dirs"]] == [".claude"]


def test_install_answers_202_with_a_transaction_that_already_exists(sv, routes, fake_host, bare,
                                                                   monkeypatch):
    repo = sv.repos.add(str(bare), "bare")
    plan = sv.installer.preview(repo, "install")
    ran: list[tuple[str, str]] = []

    async def record_run(repo_arg, kind, *, plan_digest, transaction_id=None):
        ran.append((kind, transaction_id))
        return sv.installer.transaction(transaction_id)

    # The transaction itself is `test_installer.py`'s subject; what this test owns is that the route
    # answers 202 with a pollable id and hands the work to a background task.
    monkeypatch.setattr(sv.installer, "run", record_run)

    path = f"/repos/{repo.repo_id}/install"
    request = CT.owner_request("POST", path, host=fake_host, body={"plan_digest": plan.digest()})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 202
    assert body["ok"] is True
    assert body["transaction_id"].startswith("tx_")
    assert body["status"] == "staged"
    assert sv.installer.transaction(body["transaction_id"]).kind == "install"

    detail = f"/repos/{repo.repo_id}/transactions/{body['transaction_id']}"
    status, seen = call(sv, routes, "GET", detail,
                        CT.owner_request("GET", detail, host=fake_host))
    assert status == 200 and seen["transaction"]["transaction_id"] == body["transaction_id"]


def test_install_refuses_a_digest_the_repository_has_moved_past(sv, routes, fake_host, bare):
    repo = sv.repos.add(str(bare), "bare")
    path = f"/repos/{repo.repo_id}/install"
    request = CT.owner_request("POST", path, host=fake_host, body={"plan_digest": "0" * 64})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 409 and body["code"] == "install_conflict"
    assert body["details"]["reason"] == "preview_changed"


def test_install_needs_a_plan_digest(sv, routes, fake_host, bare):
    repo = sv.repos.add(str(bare), "bare")
    path = f"/repos/{repo.repo_id}/install"
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["missing"] == ["plan_digest"]


def test_every_install_path_refuses_while_the_payload_is_degraded(sv, routes, fake_host, bare):
    repo = sv.repos.add(str(bare), "bare")
    sv.payload_error = "payload_degraded"
    for suffix in ("install/preview", "install", "upgrade/preview", "upgrade",
                   "install/recovery/preview", "install/recovery"):
        path = f"/repos/{repo.repo_id}/{suffix}"
        request = CT.owner_request("POST", path, host=fake_host, body={"plan_digest": "0" * 64})
        status, body = call(sv, routes, "POST", path, request)
        assert status == 409 and body["code"] == "payload_degraded", suffix


def test_recovery_preview_is_offered_for_an_installed_repository(sv, routes, fake_host, gate_repo):
    """``install_recovery_required`` is not an error here — recovery is exactly what it needs."""
    repo = sv.repos.add(str(gate_repo), "demo")
    sv.storage.update("repos", repo.repo_id, {"install_status": "recovery_required"})
    path = f"/repos/{repo.repo_id}/install/recovery/preview"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 200 and body["plan"]["kind"] == "recovery"


def test_recovery_preview_is_offered_when_the_failed_install_left_no_readable_engine(
    sv, routes, fake_host, bare
):
    """`recovery_required` was a dead end whenever the dead transaction left no parseable version.

    Recover is the ONLY action such a repository is offered, and this preview refused it `not_installed`
    because `engine_from` reads a version out of a file the failed install may never have written (crash
    between `backup` and `write`, or a rollback that could not finish). The transaction preflight
    (`Installer._plan_for_run`) applies no such check, so the refusal was the preview's alone.
    """
    repo = sv.repos.add(str(bare), "bare")
    sv.storage.update("repos", repo.repo_id, {"install_status": "recovery_required"})
    path = f"/repos/{repo.repo_id}/install/recovery/preview"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 200 and body["plan"]["kind"] == "recovery", body
    assert body["plan"]["engine_from"] is None, "nothing readable is installed, and recovery is still offered"

    # The waiver is scoped to recovery: upgrade on the same repository still refuses.
    path = f"/repos/{repo.repo_id}/upgrade/preview"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host,
                                                                  body={}))
    assert status == 409 and body["code"] == "not_installed"


# --------------------------------------------------------------------------- #
# transactions, receipts, git
# --------------------------------------------------------------------------- #


def test_an_unknown_transaction_is_a_404(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    path = f"/repos/{repo.repo_id}/transactions/tx_0000000000000009"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 404 and body["code"] == "transaction_not_found"


def test_receipts_is_empty_before_any_install(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    path = f"/repos/{repo.repo_id}/receipts"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200 and body == {"receipts": []}


def test_repo_git_reports_the_observation_and_the_owned_dirty_split(sv, routes, fake_host, gate_repo):
    """The split is FR-GIT-004: owned files are named, the user's own work is only counted.

    ``fake_git`` answers with its built-in defaults here, because Studio strips every child's
    environment (only ``PATH``/``HOME``/``LANG``/``LC_ALL``/``TMPDIR`` survive) and the harness passes its
    scripted state through an env var — which is itself the env-scrubbing rule working.
    """
    repo = sv.repos.add(str(gate_repo), "demo")
    path = f"/repos/{repo.repo_id}/git"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200
    assert set(body) == {"git", "owned_dirty", "unrelated_dirty"}
    assert body["git"]["branch"] == "main"
    # No receipt yet, so Studio owns nothing here: an unrelated dirty file must never be named.
    assert body["owned_dirty"] == []
    assert isinstance(body["unrelated_dirty"], int)


def test_repo_git_still_answers_once_a_receipt_exists(sv, routes, fake_host, gate_repo):
    """The receipt branch of the same handler, which nothing exercised.

    `owned` was read as `ReceiptFile.relpath`, a field that does not exist, so this endpoint raised
    `AttributeError` -> 500 for every repository that HAD an install receipt, and passed only while
    there was none. The panel therefore broke at exactly the moment an install succeeded.
    """
    repo = sv.repos.add(str(gate_repo), "demo")
    sv.storage.insert(
        "install_receipts",
        {
            "receipt_id": "rc_1",
            "repo_id": repo.repo_id,
            "resolved_repo_identity": repo.resolved_identity,
            "studio_version": "1.0.0",
            "engine_version": "2.7.1",
            "payload_digest": "d" * 64,
            "committed_at": "2026-09-09T00:00:00Z",
            "prior_receipt_id": None,
            "transaction_id": "tx_1",
            "files_json": [
                {
                    "path": ".kiro/tools/aidlc-utility.ts",
                    "ownership": "framework",
                    "kind": "file",
                    "sha256": "a" * 64,
                    "canonical_digest": None,
                    "fragment_key": None,
                    "adopted": False,
                }
            ],
            "status": "current",
        },
    )
    path = f"/repos/{repo.repo_id}/git"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200, body
    assert set(body) == {"git", "owned_dirty", "unrelated_dirty"}
    assert isinstance(body["owned_dirty"], list)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _events(sv: Any) -> list[tuple[str, dict]]:
    return [(record.type, record.payload) for record in sv.storage.event_since(0)]

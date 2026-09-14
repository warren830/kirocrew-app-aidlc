"""Health, payload, leases, diagnostics, settings, calibration, migration, events, activity, advisor
and the Slack correlation hint (§2.1, §2.5–§2.9).

Two properties are worth more than the shapes here.

* **``/payload`` is compared to ``payload/manifest.json``, never to literals** (review P03/R03). A test
  that asserted ``"2.6.2"`` would have to be edited when the payload is bumped, and then it would be
  the test — not the product — that decided which engine version Studio claims to ship.
* **``/diagnostics`` is redactable by construction.** The bundle is what a user pastes into an issue, so
  the assertions are about what is *absent*: verbatim decision text unless two switches are on, and no
  absolute repository path.
"""

from __future__ import annotations

import asyncio
import functools
import json
from typing import Any

import pytest

import conftest as CT
from test_handlers_auth import APP_ROOT, BOOT, call, common, routes, sv  # noqa: F401 - fixtures

MANIFEST = json.loads((APP_ROOT / "payload" / "manifest.json").read_text("utf-8"))


def aio(fn):
    """Run an async test body on a fresh loop (no pytest-asyncio in the pinned interpreter)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


# --------------------------------------------------------------------------- #
# common.py units (§1.24)
# --------------------------------------------------------------------------- #


def test_an_intent_key_splits_unambiguously_into_space_and_dir(common):
    """``~`` is outside the dir grammar, so exactly one reading of the path exists (C02)."""
    assert common.parse_intent_key("260904-demo") == ("default", "260904-demo")
    assert common.parse_intent_key("team-a~260904-demo") == ("team-a", "260904-demo")
    for bad in ("", "has space", "a~b~c", "../escape", "team-a~"):
        with pytest.raises(Exception) as raised:
            common.parse_intent_key(bad)
        assert raised.value.code == "bad_param"


def test_pagination_is_descending_and_its_cursor_round_trips(common):
    rows = [{"id": n} for n in (9, 7, 5, 3, 1)]
    page, cursor = common.paginate(rows, None, 2, lambda row: row["id"])
    assert [row["id"] for row in page] == [9, 7]
    assert common.decode_cursor(cursor) == 7

    page, cursor = common.paginate(rows, cursor, 2, lambda row: row["id"])
    assert [row["id"] for row in page] == [5, 3]

    page, cursor = common.paginate(rows, cursor, 2, lambda row: row["id"])
    assert [row["id"] for row in page] == [1]
    assert cursor is None                      # no next page, so no cursor to offer

    with pytest.raises(Exception) as raised:
        common.paginate(rows, "not-base64!!", 2, lambda row: row["id"])
    assert raised.value.code == "bad_param"


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #

_HEALTH_KEYS = {
    "app", "version", "bundled_engine_version", "min_kirocrew_version", "boot_id", "host_version",
    "started_at", "status", "issues", "storage", "payload", "host", "tools", "reconciler", "counts",
    "machine_lane",
}


def test_health_reports_the_whole_envelope(sv, routes, fake_host, studio):
    status, body = call(sv, routes, "GET", "/health",
                        CT.owner_request("GET", "/health", host=fake_host))
    assert status == 200
    assert set(body) == _HEALTH_KEYS
    assert body["app"] == "aidlc-studio"
    assert body["version"] == studio.constants.APP_VERSION
    assert body["min_kirocrew_version"] == studio.constants.MIN_KIROCREW_VERSION
    # Read from the manifest, not from a literal (C21).
    assert body["bundled_engine_version"] == MANIFEST["engineVersion"]
    assert body["boot_id"] == BOOT
    # Read from the module rather than pinned to a number: the ladder is allowed to grow, and a test that
    # had to be edited on every migration would be pinning the edit, not the behaviour.
    assert body["storage"]["schema_version"] == studio.storage.SCHEMA_VERSION
    assert body["storage"]["integrity"] == "ok"
    assert body["storage"]["wal"] is True
    # A path hash, never the path: /health is the most-scraped endpoint in the app.
    assert "/" not in body["storage"]["path_hash"]
    assert body["counts"] == {"repos": 0, "intents": 0, "live_actions": 0,
                              "execution_leases": 0, "admin_leases": 0}
    assert body["tools"]["bun"]["found"] is True
    assert body["tools"]["git"]["found"] is True
    assert body["host"]["attached"] is True
    assert body["machine_lane"] == {"available": False, "reason": "machine_lane_unavailable"}


def test_health_is_degraded_while_the_host_is_not_attached(sv, routes, fake_host, studio):
    sv.host._state = None  # what the gateway looks like between on_startup and the first request
    sv.host._reset("host_not_attached")
    status, body = call(sv, routes, "GET", "/health",
                        CT.owner_request("GET", "/health", host=None))
    assert status == 200 and body["status"] == "degraded"
    assert "host_unattached" in body["issues"]
    assert body["host"]["attached"] is False


def test_health_reports_error_when_the_store_fails_its_integrity_check(sv, routes, fake_host,
                                                                      monkeypatch):
    monkeypatch.setattr(sv.storage, "integrity_check", lambda: ["*** in database main"])
    _status, body = call(sv, routes, "GET", "/health",
                         CT.owner_request("GET", "/health", host=fake_host))
    assert body["status"] == "error"
    assert body["storage"]["integrity"] == "error"
    assert "storage_integrity" in body["issues"]


# --------------------------------------------------------------------------- #
# lifecycle (§1.23)
# --------------------------------------------------------------------------- #


@aio
async def test_start_reconciles_then_hands_over_to_the_background_and_stop_cleans_up(
    studio, fake_ctx, fake_host, clock, ids, fake_bun, fake_git
):
    """``on_startup`` must return inside the host's budget with the loops running (§1.23).

    Built here rather than on the shared fixture because ``start`` opens the store itself: the point of
    the test is that the documented order works end to end, and a store somebody else opened would skip
    the first step of it.
    """
    services = studio.services.Services.build(
        fake_ctx, clock=clock, ids=ids, bun_path=fake_bun, git_path=fake_git,
        app_root=APP_ROOT, boot_id=BOOT, bun_version=lambda path: "1.1.42",
    )
    try:
        await services.start()
        assert services.started_at == clock.iso()
        assert services.payload_status is not None and services.payload_status.ok is True
        assert len(services.background) == 1
        health = await asyncio.to_thread(services.health)
        assert health["reconciler"]["running"] is True
        assert health["started_at"] == services.started_at
        assert "payload_degraded" not in health["issues"]
    finally:
        await services.stop()
    assert services.background == []
    # Stopping twice must not raise: disable, uninstall and trust withdrawal can all arrive together.
    await services.stop()


@aio
async def test_start_degrades_instead_of_failing_when_the_payload_manifest_is_unreadable(
    studio, fake_ctx, fake_host, clock, ids, fake_bun, fake_git, tmp_path
):
    """A broken payload must cost the install paths, not the whole app (``/health`` above all)."""
    services = studio.services.Services.build(
        fake_ctx, clock=clock, ids=ids, bun_path=fake_bun, git_path=fake_git,
        app_root=tmp_path / "no-payload-here", boot_id=BOOT, bun_version=lambda path: None,
    )
    try:
        await services.start()
        health = await asyncio.to_thread(services.health)
        assert health["status"] == "degraded"
        assert "payload_manifest_unreadable" in health["issues"]
        assert health["payload"]["ok"] is False
    finally:
        await services.stop()


# --------------------------------------------------------------------------- #
# payload
# --------------------------------------------------------------------------- #


def test_payload_is_the_manifest_plus_its_verification_status(sv, routes, fake_host):
    status, body = call(sv, routes, "GET", "/payload",
                        CT.owner_request("GET", "/payload", host=fake_host))
    assert status == 200
    assert body["schema"] == MANIFEST["schema"]
    assert body["harness"] == MANIFEST["harness"]
    assert body["engine_version"] == MANIFEST["engineVersion"]
    assert body["source"] == MANIFEST["source"]
    assert body["compatible_state_versions"] == sorted(set(MANIFEST["compatibleStateVersions"]))
    assert body["stage_count"] == MANIFEST["stageCount"]
    assert body["payload_digest"] == MANIFEST["payloadDigest"]
    assert body["file_count"] == MANIFEST["fileCount"]
    assert body["merge_targets"] == MANIFEST["mergeTargets"]
    assert sum(body["ownership_counts"].values()) == len(MANIFEST["files"])
    assert body["status"]["ok"] is True
    assert "files" not in body


def test_payload_lists_the_files_only_when_asked(sv, routes, fake_host):
    request = CT.owner_request("GET", "/payload", host=fake_host, query={"files": "1"})
    _status, body = call(sv, routes, "GET", "/payload", request)
    assert len(body["files"]) == len(MANIFEST["files"])
    assert set(body["files"][0]) == {"path", "sha256", "size", "ownership"}


def test_payload_refuses_a_bad_boolean_rather_than_guessing(sv, routes, fake_host):
    request = CT.owner_request("GET", "/payload", host=fake_host, query={"files": "yes"})
    status, body = call(sv, routes, "GET", "/payload", request)
    assert status == 400 and body["code"] == "bad_param"


# --------------------------------------------------------------------------- #
# leases
# --------------------------------------------------------------------------- #


def test_leases_is_empty_and_reports_the_cap(sv, routes, fake_host):
    _status, body = call(sv, routes, "GET", "/leases",
                         CT.owner_request("GET", "/leases", host=fake_host))
    assert body["leases"] == []
    assert body["live_execution"] == 0
    assert body["global_concurrency_cap"] == sv.settings.global_concurrency_cap()


def test_leases_shows_a_live_execution_lease_with_its_intent_key(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    card = _seed_gate_card(sv, repo)
    grant = asyncio.run(
        sv.scheduler.acquire_execution(
            repo, intent_uuid=None, session_key="dashboard:slot", action_id=card.action_id
        )
    )
    _status, body = call(sv, routes, "GET", "/leases",
                         CT.owner_request("GET", "/leases", host=fake_host))
    assert body["live_execution"] == 1
    view = body["leases"][0]
    assert view["kind"] == "execution"
    assert view["repo_id"] == repo.repo_id
    assert view["generation"] == grant.generation
    assert view["intent_key"] == "260904-gate-demo"
    assert view["orphaned"] is False


# --------------------------------------------------------------------------- #
# diagnostics
# --------------------------------------------------------------------------- #

_DIAGNOSTICS_KEYS = {
    "generated_at", "health", "settings", "repos", "live_actions", "leases", "recent_transactions",
    "recent_activity", "breakers", "redacted",
}


def test_diagnostics_carries_every_section_and_says_it_is_redacted(sv, routes, fake_host, gate_repo):
    sv.repos.add(str(gate_repo), "demo")
    status, body = call(sv, routes, "GET", "/diagnostics",
                        CT.owner_request("GET", "/diagnostics", host=fake_host))
    assert status == 200
    assert set(body) == _DIAGNOSTICS_KEYS
    assert body["redacted"] is True
    assert body["health"]["app"] == "aidlc-studio"
    assert body["repos"][0]["label"] == "demo"


def test_diagnostics_keeps_the_registered_root_and_scrubs_every_other_path(sv, routes, fake_host,
                                                                          gate_repo):
    """A bundle may name the repository it is about, and nothing else on the user's disk (§10)."""
    repo = sv.repos.add(str(gate_repo), "demo")
    asyncio.run(
        sv.activity.record(
            kind="repo.added", severity="info", repo_id=repo.repo_id,
            params={"where": str(gate_repo) + "/aidlc", "elsewhere": "/Users/someone/secrets/keys.txt"},
        )
    )
    _status, body = call(sv, routes, "GET", "/diagnostics",
                         CT.owner_request("GET", "/diagnostics", host=fake_host))
    params = body["recent_activity"][0]["params"]
    assert params["where"] == str(gate_repo) + "/aidlc"
    assert params["elsewhere"] == "<path>"
    assert body["recent_activity"][0]["human_text"] is None


def test_diagnostics_export_sets_a_download_header(sv, routes, fake_host):
    request = CT.owner_request("GET", "/diagnostics", host=fake_host, query={"export": "1"})
    request.app["ctx"] = sv.ctx
    entry = next(e for e in routes if e.path == "/diagnostics")
    response = asyncio.run(entry.handler(request, sv.ctx))
    assert response.status == 200
    assert response.headers["Content-Disposition"].startswith(
        'attachment; filename="aidlc-studio-diagnostics-'
    )


def test_diagnostics_withholds_human_text_unless_the_setting_and_the_flag_agree(sv, routes, fake_host,
                                                                               gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    asyncio.run(
        sv.activity.record(
            kind="action.submitted", severity="info", repo_id=repo.repo_id,
            action_id="a_0000000000000001", params={}, human_text="Request Changes: do it again",
        )
    )
    request = CT.owner_request("GET", "/diagnostics", host=fake_host,
                               query={"include_human_text": "1"})
    _status, body = call(sv, routes, "GET", "/diagnostics", request)
    assert body["recent_activity"][0]["human_text"] is None    # the setting is off by default

    asyncio.run(sv.settings.put({"diagnostics": {"export_include_human_text": True}}))
    request = CT.owner_request("GET", "/diagnostics", host=fake_host,
                               query={"include_human_text": "1"})
    _status, body = call(sv, routes, "GET", "/diagnostics", request)
    assert body["recent_activity"][0]["human_text"] == "Request Changes: do it again"


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #


def test_settings_returns_values_capabilities_and_versions(sv, routes, fake_host):
    status, body = call(sv, routes, "GET", "/settings",
                        CT.owner_request("GET", "/settings", host=fake_host))
    assert status == 200
    assert set(body) == {"settings", "capabilities", "versions", "updated_at"}
    assert body["capabilities"]["night_window"] == {"available": False,
                                                   "reason": "machine_lane_unavailable"}
    assert body["capabilities"]["slack_quick_actions"]["available"] is False
    assert body["versions"]["studio"] == sv.settings.versions()["studio"]


def test_put_settings_stores_a_partial_patch_and_publishes_the_change(sv, routes, fake_host):
    request = CT.owner_request("PUT", "/settings", host=fake_host,
                               body={"locale": "zh-CN"})
    status, body = call(sv, routes, "PUT", "/settings", request)
    assert status == 200
    assert body["settings"]["locale"] == "zh-CN"
    assert body["updated_at"]
    # Published on Studio's own ring; `settings.updated` maps to no host event name, so the app event
    # bus is deliberately silent for it (the manifest declares three host events, none of them this).
    assert "settings.updated" in [record.type for record in sv.storage.event_since(0)]


def test_put_settings_refuses_an_unknown_value_without_storing_anything(sv, routes, fake_host):
    request = CT.owner_request("PUT", "/settings", host=fake_host, body={"locale": "kl-KL"})
    status, body = call(sv, routes, "PUT", "/settings", request)
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["key"] == "locale"
    assert sv.settings.values()["locale"] != "kl-KL"


def test_put_settings_refuses_a_capability_that_does_not_exist_in_this_version(sv, routes, fake_host):
    request = CT.owner_request("PUT", "/settings", host=fake_host,
                               body={"night_window": {"enabled": True}})
    status, body = call(sv, routes, "PUT", "/settings", request)
    assert status == 409 and body["code"] == "machine_lane_unavailable"


# --------------------------------------------------------------------------- #
# calibration
# --------------------------------------------------------------------------- #


def test_calibration_reports_cohorts_and_the_minimum_sample_count(sv, routes, fake_host, studio):
    status, body = call(sv, routes, "GET", "/calibration",
                        CT.owner_request("GET", "/calibration", host=fake_host))
    assert status == 200
    assert body == {"cohorts": [], "total": 0,
                    "min_samples": studio.constants.CALIBRATION_MIN_SAMPLES}


def test_clearing_calibration_needs_an_explicit_confirmation(sv, routes, fake_host):
    request = CT.owner_request("POST", "/calibration/clear", host=fake_host, body={})
    status, body = call(sv, routes, "POST", "/calibration/clear", request)
    assert status == 400 and body["code"] == "invalid_decision"

    request = CT.owner_request("POST", "/calibration/clear", host=fake_host, body={"confirm": True})
    status, body = call(sv, routes, "POST", "/calibration/clear", request)
    assert status == 200 and body == {"ok": True, "removed": 0}


# --------------------------------------------------------------------------- #
# migration
# --------------------------------------------------------------------------- #


def test_migration_status_reports_no_prototype_to_migrate(sv, routes, fake_host):
    status, body = call(sv, routes, "GET", "/migration/status",
                        CT.owner_request("GET", "/migration/status", host=fake_host))
    assert status == 200
    assert body["applied"] is False
    assert body["result"] is None
    assert body["preview_available"] is False
    assert body["console"] == {"installed": False, "enabled": False}


def test_migration_preview_reports_why_it_is_not_applicable(sv, routes, fake_host):
    request = CT.owner_request("POST", "/migration/preview", host=fake_host, body={})
    status, body = call(sv, routes, "POST", "/migration/preview", request)
    assert status == 200
    assert body["preview"]["applicable"] is False
    assert body["preview"]["reason"]


def test_migration_apply_refuses_without_a_confirmation_and_a_digest(sv, routes, fake_host):
    request = CT.owner_request("POST", "/migration/apply", host=fake_host, body={"confirm": True})
    status, body = call(sv, routes, "POST", "/migration/apply", request)
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["missing"] == ["source_sha256"]

    request = CT.owner_request("POST", "/migration/apply", host=fake_host,
                               body={"source_sha256": "0" * 64})
    status, body = call(sv, routes, "POST", "/migration/apply", request)
    assert status == 400 and body["code"] == "invalid_decision"

    request = CT.owner_request("POST", "/migration/apply", host=fake_host,
                               body={"confirm": True, "source_sha256": "0" * 64})
    status, body = call(sv, routes, "POST", "/migration/apply", request)
    assert status == 409 and body["code"] == "migration_not_applicable"


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #


def test_events_poll_without_a_cursor_is_a_cold_start(sv, routes, fake_host):
    asyncio.run(sv.events.publish("repo.updated", {"repo_id": "r_000000000001"}))
    status, body = call(sv, routes, "GET", "/events/poll",
                        CT.owner_request("GET", "/events/poll", host=fake_host))
    assert status == 200
    assert body == {"events": [], "cursor": 1, "oldest_seq": 1, "reset": False}


def test_events_poll_replays_from_a_cursor(sv, routes, fake_host):
    asyncio.run(sv.events.publish("repo.updated", {"repo_id": "r_1"}))
    asyncio.run(sv.events.publish("action.created", {"action_id": "a_1"}))
    request = CT.owner_request("GET", "/events/poll", host=fake_host, query={"cursor": "0"})
    _status, body = call(sv, routes, "GET", "/events/poll", request)
    assert [event["type"] for event in body["events"]] == ["repo.updated", "action.created"]
    assert body["cursor"] == 2 and body["reset"] is False


def test_events_poll_resets_a_cursor_that_fell_out_of_the_ring(sv, routes, fake_host, monkeypatch):
    asyncio.run(sv.events.publish("repo.updated", {"repo_id": "r_1"}))
    request = CT.owner_request("GET", "/events/poll", host=fake_host, query={"cursor": "999"})
    _status, body = call(sv, routes, "GET", "/events/poll", request)
    # A cursor ahead of the newest row means the storage file was rebuilt and `seq` restarted; resuming
    # would make every new event look like one already seen.
    assert body["reset"] is True and body["events"] == []

    monkeypatch.setattr(sv.storage, "event_bounds", lambda: (50, 60))
    request = CT.owner_request("GET", "/events/poll", host=fake_host, query={"cursor": "1"})
    _status, body = call(sv, routes, "GET", "/events/poll", request)
    assert body["reset"] is True and body["events"] == []


def test_events_poll_refuses_a_negative_cursor(sv, routes, fake_host):
    request = CT.owner_request("GET", "/events/poll", host=fake_host, query={"cursor": "-1"})
    status, body = call(sv, routes, "GET", "/events/poll", request)
    assert status == 400 and body["code"] == "bad_param"


class Wire:
    """The socket the SSE route writes to. Records the bytes that leave the process, nothing else.

    ``fail_after`` makes the reader die the way a closed browser tab does, which is the only way to make
    the stream return its response so the headers can be asserted.
    """

    def __init__(self, request: Any, *, fail_after: int | None = None) -> None:
        self.frames: list[bytes] = []
        self.fail_after = fail_after
        request._payload_writer.write = self._write

    async def _write(self, data: bytes) -> None:
        self.frames.append(bytes(data))
        if self.fail_after is not None and len(self.frames) >= self.fail_after:
            raise ConnectionResetError("client went away")

    @property
    def text(self) -> str:
        return b"".join(self.frames).decode("utf-8")

    async def wait(self, count: int, *, timeout: float = 2.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while len(self.frames) < count:
            if loop.time() > deadline:
                raise AssertionError(f"only {len(self.frames)} frame(s): {self.text!r}")
            await asyncio.sleep(0.005)


@aio
async def test_the_sse_route_greets_then_streams_frames_with_ids(sv, routes, fake_host):
    entry = next(e for e in routes if (e.method, e.path) == ("GET", "/events"))
    request = CT.owner_request("GET", "/events", host=fake_host)
    wire = Wire(request)
    task = asyncio.create_task(entry.handler(request, sv.ctx))
    try:
        await wire.wait(1)
        greeting = wire.frames[0].decode("utf-8")
        assert "event: hello" in greeting
        assert greeting.startswith("id: ")
        payload = json.loads(
            next(line[len("data: "):] for line in greeting.splitlines() if line.startswith("data: "))
        )
        assert set(payload) == {"newest_seq", "server_time", "app_version"}

        await sv.events.publish("repo.updated", {"repo_id": "r_000000000001"})
        await wire.wait(2)
        frame = wire.frames[1].decode("utf-8")
        assert frame.splitlines()[0] == "id: 1"
        assert frame.splitlines()[1] == "event: repo.updated"
        assert frame.endswith("\n\n")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@aio
async def test_the_sse_route_sets_the_stream_headers(sv, routes, fake_host):
    """Headers must be on the response before ``prepare``; the host middleware only ``setdefault``s."""
    entry = next(e for e in routes if (e.method, e.path) == ("GET", "/events"))
    request = CT.owner_request("GET", "/events", host=fake_host)
    wire = Wire(request, fail_after=1)          # dies like a closed tab, so the stream returns
    response = await entry.handler(request, sv.ctx)
    assert len(wire.frames) == 1
    assert response.headers["Content-Type"].startswith("text/event-stream")
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.headers["Connection"] == "keep-alive"
    assert response.headers["X-Accel-Buffering"] == "no"


@aio
async def test_the_sse_route_refuses_an_anonymous_reader_before_it_prepares(sv, routes, fake_host):
    entry = next(e for e in routes if (e.method, e.path) == ("GET", "/events"))
    request = CT.anon_request("GET", "/events", host=fake_host)
    wire = Wire(request)
    response = await entry.handler(request, sv.ctx)
    assert response.status == 401
    assert wire.frames == []


# --------------------------------------------------------------------------- #
# activity
# --------------------------------------------------------------------------- #

_ENTRY_KEYS = {"at", "source", "kind", "message_key", "params", "severity", "refs", "raw", "id"}


def test_activity_returns_studio_rows_as_timeline_entries(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    asyncio.run(
        sv.activity.record(
            kind="repo.added", severity="info", repo_id=repo.repo_id, params={"label": "demo"}
        )
    )
    status, body = call(sv, routes, "GET", "/activity",
                        CT.owner_request("GET", "/activity", host=fake_host))
    assert status == 200
    assert body["next_cursor"] is None
    item = body["items"][0]
    assert set(item) == _ENTRY_KEYS
    assert item["source"] == "studio"
    assert item["kind"] == "repo.added"
    assert item["message_key"] == "activity.repo.added"
    assert item["raw"] is None


def test_activity_merges_the_audit_trail_for_one_intent(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    request = CT.owner_request(
        "GET", "/activity", host=fake_host,
        query={"repo": repo.repo_id, "intent": "260904-gate-demo"},
    )
    status, body = call(sv, routes, "GET", "/activity", request)
    assert status == 200
    sources = {item["source"] for item in body["items"]}
    assert "aidlc" in sources
    assert all(set(item) == _ENTRY_KEYS for item in body["items"])
    assert body["next_cursor"] is None


def test_activity_refuses_an_unknown_severity_filter(sv, routes, fake_host):
    request = CT.owner_request("GET", "/activity", host=fake_host, query={"severity": "urgent"})
    status, body = call(sv, routes, "GET", "/activity", request)
    assert status == 400 and body["code"] == "bad_param"


def test_activity_refuses_a_malformed_cursor(sv, routes, fake_host):
    request = CT.owner_request("GET", "/activity", host=fake_host, query={"cursor": "!!!"})
    status, body = call(sv, routes, "GET", "/activity", request)
    assert status == 400 and body["code"] == "bad_param"


# --------------------------------------------------------------------------- #
# advisor
# --------------------------------------------------------------------------- #


def test_requesting_a_draft_for_an_unknown_action_is_a_404(sv, routes, fake_host):
    request = CT.owner_request("POST", "/advisor/draft", host=fake_host,
                               body={"action_id": "a_0000000000000009", "kind": "gate_analysis"})
    status, body = call(sv, routes, "POST", "/advisor/draft", request)
    assert status in (404, 503)
    assert body["code"] in ("action_not_found", "advisor_unavailable")


def test_requesting_a_draft_with_an_unknown_kind_is_refused(sv, routes, fake_host):
    request = CT.owner_request("POST", "/advisor/draft", host=fake_host,
                               body={"action_id": "a_1", "kind": "please_decide_for_me"})
    status, body = call(sv, routes, "POST", "/advisor/draft", request)
    assert status == 400 and body["code"] == "invalid_decision"


def test_a_draft_request_needs_an_action_and_a_kind(sv, routes, fake_host):
    request = CT.owner_request("POST", "/advisor/draft", host=fake_host, body={"kind": "diagnose"})
    status, body = call(sv, routes, "POST", "/advisor/draft", request)
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["missing"] == ["action_id"]


def test_an_unknown_draft_is_a_404(sv, routes, fake_host):
    path = "/advisor/drafts/d_0000000000000001"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 404 and body["code"] == "draft_not_found"


# --------------------------------------------------------------------------- #
# slack correlation
# --------------------------------------------------------------------------- #


def test_the_slack_callback_is_a_404_for_an_unknown_action(sv, routes, fake_host):
    request = CT.owner_request("POST", "/slack/actions/callback", host=fake_host,
                               body={"action_id": "a_0000000000000009"})
    status, body = call(sv, routes, "POST", "/slack/actions/callback", request)
    assert status == 404 and body["code"] == "action_not_found"


def test_the_slack_callback_records_the_thread_on_the_action_and_authorises_nothing(
    sv, routes, fake_host, gate_repo
):
    repo = sv.repos.add(str(gate_repo), "demo")
    rec = _seed_gate_card(sv, repo)
    request = CT.owner_request(
        "POST", "/slack/actions/callback", host=fake_host,
        body={
            "action_id": rec.action_id, "slot_key": "aidlc-studio-x", "channel": "C1",
            "thread_ts": "1700000000.1", "message_ts": "1700000000.2", "selection": None,
        },
    )
    status, body = call(sv, routes, "POST", "/slack/actions/callback", request)
    assert status == 200 and body == {"ok": True, "recorded": True}

    after = asyncio.run(sv.actions.get(rec.action_id))
    assert after.evidence["slack"]["thread_ts"] == "1700000000.1"
    # Nothing was authorised: the status did not move and no transition was recorded. (The generation
    # does advance — `actions` is a CAS table, and even an evidence-only write goes through it.)
    assert after.status == rec.status
    assert sv.storage.select("action_transitions", {"action_id": rec.action_id}) == []


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _published(sv: Any) -> list[tuple[str, dict]]:
    return list(sv.ctx.events.published)


def _seed_gate_card(sv: Any, repo: Any, intent_dir: str = "260904-gate-demo") -> Any:
    """One derived ``gate`` card, produced the way the reconciler produces it."""
    snap = sv.projection.snapshot(repo, "default", intent_dir)
    seeds = sv.projection.derive_cards(snap, findings=[], binding=None, install=None, breakers=[],
                                       live_actions=[], slot=None, host_card=None)
    gate = [seed for seed in seeds if seed.type == "gate"]
    assert gate, "the fixture repository has no open gate"
    records = asyncio.run(sv.actions.upsert_derived(gate))
    return next(rec for rec in records if rec.type == "gate")

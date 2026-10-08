"""Health, diagnostics, settings, events, activity and advisor routes (§2.1, §2.5–§2.8).

Payload numbers are compared to ``payload/manifest.json``, never to literals (review P03/R03), and ``/diagnostics``
is redactable by construction: the assertions are about what is absent.
"""

from __future__ import annotations

import asyncio
import functools
import json
from typing import Any

import pytest

import conftest as CT
from test_handlers_auth import APP_ROOT, BOOT, call, common, ocall, routes, sv  # noqa: F401 - fixtures

MANIFEST = json.loads((APP_ROOT / "payload" / "manifest.json").read_text("utf-8"))


def aio(fn):
    """Run an async test body on a fresh loop (no pytest-asyncio in the pinned interpreter)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def _seed_gate_card(sv: Any, repo: Any, intent_dir: str = "260904-gate-demo") -> Any:
    """One derived ``gate`` card, produced the way the reconciler produces it."""
    snap = sv.projection.snapshot(repo, "default", intent_dir)
    seeds = sv.projection.derive_cards(snap, findings=[], binding=None, install=None, breakers=[],
                                       live_actions=[], slot=None, host_card=None)
    gate = [seed for seed in seeds if seed.type == "gate"]
    assert gate, "the fixture repository has no open gate"
    records = asyncio.run(sv.actions.upsert_derived(gate))
    return next(rec for rec in records if rec.type == "gate")


def gate_lease(sv, gate_repo):
    """A registered gate repository whose card holds an execution lease: ``(repo, grant)``."""
    repo = sv.repos.add(str(gate_repo), "demo")
    card = _seed_gate_card(sv, repo)
    return repo, asyncio.run(sv.scheduler.acquire_execution(repo, intent_uuid=None, session_key="dashboard:slot",
                                                            action_id=card.action_id))


# ---- common.py units (§1.24) ---- #


def test_an_intent_key_splits_unambiguously_into_space_and_dir(common):
    """``~`` is outside the dir grammar, so exactly one reading of the path exists (C02)."""
    assert common.parse_intent_key("260904-demo") == ("default", "260904-demo")
    assert common.parse_intent_key("team-a~260904-demo") == ("team-a", "260904-demo")
    for bad in ("", "has space", "a~b~c", "../escape", "team-a~"):
        with pytest.raises(Exception) as raised:
            common.parse_intent_key(bad)
        assert raised.value.code == "bad_param"


# ---- health ---- #

_HEALTH_KEYS = {
    "app", "version", "bundled_engine_version", "min_kirocrew_version", "boot_id", "host_version",
    "started_at", "status", "issues", "storage", "payload", "host", "tools", "reconciler", "counts",
}


def test_health_reports_the_whole_envelope(ocall, studio):
    status, body = ocall("GET", "/health")
    assert status == 200
    assert set(body) == _HEALTH_KEYS
    assert body["app"] == "aidlc-studio"
    assert body["version"] == studio.constants.APP_VERSION
    assert body["min_kirocrew_version"] == studio.constants.MIN_KIROCREW_VERSION
    assert body["bundled_engine_version"] == MANIFEST["engineVersion"]  # from the manifest, not a literal (C21)
    assert body["boot_id"] == BOOT
    assert body["storage"]["schema_version"] == studio.storage.SCHEMA_VERSION  # the ladder may grow
    assert body["storage"]["integrity"] == "ok"
    assert body["storage"]["wal"] is True
    assert "/" not in body["storage"]["path_hash"]  # a path hash, never the path: /health is the most scraped
    assert body["counts"] == {"repos": 0, "intents": 0, "live_actions": 0, "execution_leases": 0, "admin_leases": 0}
    assert body["tools"]["bun"]["found"] is True
    assert body["tools"]["git"]["found"] is True
    assert body["host"]["attached"] is True


def test_health_is_degraded_while_the_host_is_not_attached(sv, routes, studio):
    sv.host._state = None  # what the gateway looks like between on_startup and the first request
    sv.host._reset("host_not_attached")
    status, body = call(sv, routes, "GET", "/health", CT.owner_request("GET", "/health", host=None))
    assert status == 200 and body["status"] == "degraded"
    assert "host_unattached" in body["issues"]
    assert body["host"]["attached"] is False


def test_health_reports_error_when_the_store_fails_its_integrity_check(sv, ocall, monkeypatch):
    monkeypatch.setattr(sv.storage, "integrity_check", lambda: ["*** in database main"])
    _status, body = ocall("GET", "/health")
    assert body["status"] == "error"
    assert body["storage"]["integrity"] == "error"
    assert "storage_integrity" in body["issues"]


def test_health_counts_the_leases_the_status_strip_shows(sv, ocall, gate_repo):
    """The strip's "turns running" and "leases held" are ``counts.execution_leases`` and ``admin_leases``."""
    gate_lease(sv, gate_repo)
    _status, body = ocall("GET", "/health")
    assert (body["counts"]["execution_leases"], body["counts"]["admin_leases"]) == (1, 0)


def test_health_reports_no_counts_when_the_store_cannot_be_read(sv, ocall, studio, monkeypatch):
    """Absent rather than zero: "0 turns running" would be a claim with nothing behind it."""
    def unreadable(*_a, **_k):
        raise studio.errors.StudioError("storage_error", "database is locked")

    monkeypatch.setattr(sv.storage, "pragmas", unreadable)
    _status, body = ocall("GET", "/health")
    assert body["status"] == "error" and "storage:storage_error" in body["issues"]
    assert set(body["counts"].values()) == {None}


# ---- lifecycle (§1.23) ---- #


def build(studio, fake_ctx, clock, ids, fake_bun, fake_git, app_root, bun_version):
    """A fresh ``Services`` (``start`` opens the store itself, so the shared ``sv`` would skip that step)."""
    return studio.services.Services.build(fake_ctx, clock=clock, ids=ids, bun_path=fake_bun, git_path=fake_git,
                                          app_root=app_root, boot_id=BOOT, bun_version=bun_version)


@aio
async def test_start_reconciles_then_hands_over_to_the_background_and_stop_cleans_up(
        studio, fake_ctx, fake_host, clock, ids, fake_bun, fake_git):
    """``on_startup`` must return inside the host's budget with the loops running (§1.23)."""
    services = build(studio, fake_ctx, clock, ids, fake_bun, fake_git, APP_ROOT, lambda path: "1.1.42")
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
    await services.stop()  # twice must not raise: disable, uninstall and trust withdrawal can arrive together


@aio
async def test_start_degrades_instead_of_failing_when_the_payload_manifest_is_unreadable(
        studio, fake_ctx, fake_host, clock, ids, fake_bun, fake_git, tmp_path):
    """A broken payload must cost the install paths, not the whole app (``/health`` above all)."""
    services = build(studio, fake_ctx, clock, ids, fake_bun, fake_git, tmp_path / "no-payload-here", lambda path: None)
    try:
        await services.start()
        health = await asyncio.to_thread(services.health)
        assert health["status"] == "degraded"
        assert "payload_manifest_unreadable" in health["issues"]
        assert health["payload"]["ok"] is False
    finally:
        await services.stop()


# ---- diagnostics ---- #

_DIAGNOSTICS_KEYS = {
    "generated_at", "health", "settings", "repos", "live_actions", "leases", "recent_transactions",
    "recent_activity", "breakers", "redacted",
}


def test_diagnostics_carries_every_section_and_says_it_is_redacted(sv, ocall, gate_repo):
    sv.repos.add(str(gate_repo), "demo")
    status, body = ocall("GET", "/diagnostics")
    assert status == 200
    assert set(body) == _DIAGNOSTICS_KEYS
    assert body["redacted"] is True
    assert body["health"]["app"] == "aidlc-studio"
    assert body["repos"][0]["label"] == "demo"


def test_diagnostics_names_the_intent_an_execution_lease_is_working_on(sv, ocall, gate_repo):
    repo, grant = gate_lease(sv, gate_repo)
    _status, body = ocall("GET", "/diagnostics")
    [view] = body["leases"]
    assert (view["kind"], view["repo_id"], view["generation"]) == ("execution", repo.repo_id, grant.generation)
    assert view["intent_key"] == "260904-gate-demo"
    assert view["orphaned"] is False


def test_diagnostics_keeps_the_registered_root_and_scrubs_every_other_path(sv, ocall, gate_repo):
    """A bundle may name the repository it is about, and nothing else on the user's disk (§10)."""
    repo = sv.repos.add(str(gate_repo), "demo")
    asyncio.run(sv.activity.record(kind="repo.added", severity="info", repo_id=repo.repo_id, params={
        "where": str(gate_repo) + "/aidlc", "elsewhere": "/Users/someone/secrets/keys.txt"}))
    _status, body = ocall("GET", "/diagnostics")
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
    assert response.headers["Content-Disposition"].startswith('attachment; filename="aidlc-studio-diagnostics-')


def test_diagnostics_withholds_human_text_unless_the_setting_and_the_flag_agree(sv, ocall, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    asyncio.run(sv.activity.record(kind="action.submitted", severity="info", repo_id=repo.repo_id, params={},
                                   action_id="a_0000000000000001", human_text="Request Changes: do it again"))
    _status, body = ocall("GET", "/diagnostics", query={"include_human_text": "1"})
    assert body["recent_activity"][0]["human_text"] is None  # the setting is off by default
    asyncio.run(sv.settings.put({"diagnostics": {"export_include_human_text": True}}))
    _status, body = ocall("GET", "/diagnostics", query={"include_human_text": "1"})
    assert body["recent_activity"][0]["human_text"] == "Request Changes: do it again"


# ---- settings ---- #


def test_settings_returns_values_capabilities_and_versions(sv, ocall):
    status, body = ocall("GET", "/settings")
    assert status == 200
    assert set(body) == {"settings", "capabilities", "versions", "updated_at"}
    assert "night_window" not in body["settings"] and "night_window" not in body["capabilities"]
    assert body["capabilities"]["slack_quick_actions"]["available"] is False
    assert body["versions"]["studio"] == sv.settings.versions()["studio"]


def test_put_settings_stores_a_partial_patch_and_publishes_the_change(sv, ocall):
    status, body = ocall("PUT", "/settings", body={"locale": "zh-CN"})
    assert status == 200
    assert body["settings"]["locale"] == "zh-CN"
    assert body["updated_at"]
    # On Studio's own ring; `settings.updated` maps to no host event name, so the host bus stays silent.
    assert "settings.updated" in [record.type for record in asyncio.run(sv.events.since(0))]


def test_put_settings_refuses_an_unknown_value_without_storing_anything(sv, ocall):
    status, body = ocall("PUT", "/settings", body={"locale": "kl-KL"})
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["key"] == "locale"
    assert sv.settings.values()["locale"] != "kl-KL"


def test_put_settings_refuses_the_retired_night_window_as_an_unknown_key(ocall):
    status, body = ocall("PUT", "/settings", body={"night_window": {"enabled": True}})
    assert status == 400 and body["code"] == "bad_body"
    assert (body["details"]["key"], body["details"]["reason"]) == ("night_window", "unknown_key")


def test_activity_older_than_the_diagnostic_retention_is_deleted(sv, clock):
    """``diagnostics.retention_days`` is how long Studio keeps its own Activity rows (the retention pass)."""
    asyncio.run(sv.settings.put({"diagnostics": {"retention_days": 7}}))
    asyncio.run(sv.activity.record(kind="health.startup"))
    clock.advance(6 * 86400)
    asyncio.run(sv.activity.record(kind="settings.updated"))
    asyncio.run(sv.reconciler.tick())
    assert sorted(row["kind"] for row in sv.storage.select("activity")) == ["health.startup", "settings.updated"]
    clock.advance(2 * 86400)  # the first row is now eight days old, the second two
    asyncio.run(sv.reconciler.tick())
    assert [row["kind"] for row in sv.storage.select("activity")] == ["settings.updated"]


# ---- events ---- #


def test_events_poll_without_a_cursor_is_a_cold_start(sv, ocall):
    seq = asyncio.run(sv.events.publish("repo.updated", {"repo_id": "r_000000000001"}))
    status, body = ocall("GET", "/events/poll")
    assert status == 200
    assert body == {"events": [], "cursor": seq, "oldest_seq": seq, "reset": False}


def test_events_poll_replays_from_a_cursor(sv, ocall):
    asyncio.run(sv.events.publish("repo.updated", {"repo_id": "r_1"}))
    last = asyncio.run(sv.events.publish("action.created", {"action_id": "a_1"}))
    _status, body = ocall("GET", "/events/poll", query={"cursor": "0"})
    assert [event["type"] for event in body["events"]] == ["repo.updated", "action.created"]
    assert body["cursor"] == last and body["reset"] is False


def test_events_poll_resets_a_cursor_it_cannot_resume(sv, ocall):
    """Ahead of the ring (never issued) or below it (an earlier process): reset, never resume."""
    seq = asyncio.run(sv.events.publish("repo.updated", {"repo_id": "r_1"}))
    for cursor in (seq + 999, seq - 999):
        _status, body = ocall("GET", "/events/poll", query={"cursor": str(cursor)})
        assert body["reset"] is True and body["events"] == []


def test_events_poll_refuses_a_negative_cursor(ocall):
    status, body = ocall("GET", "/events/poll", query={"cursor": "-1"})
    assert status == 400 and body["code"] == "bad_param"


class Wire:
    """The socket the SSE route writes to; ``fail_after`` dies like a closed tab, so the stream returns."""

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


def sse_route(routes):
    return next(e for e in routes if (e.method, e.path) == ("GET", "/events"))


@aio
async def test_the_sse_route_greets_then_streams_frames_with_ids(sv, routes, fake_host):
    request = CT.owner_request("GET", "/events", host=fake_host)
    wire = Wire(request)
    task = asyncio.create_task(sse_route(routes).handler(request, sv.ctx))
    try:
        await wire.wait(1)
        greeting = wire.frames[0].decode("utf-8")
        assert "event: hello" in greeting
        assert greeting.startswith("id: ")
        payload = json.loads(next(line[len("data: "):] for line in greeting.splitlines() if line.startswith("data: ")))
        assert set(payload) == {"newest_seq", "server_time", "app_version"}
        seq = await sv.events.publish("repo.updated", {"repo_id": "r_000000000001"})
        await wire.wait(2)
        frame = wire.frames[1].decode("utf-8")
        assert frame.splitlines()[0] == f"id: {seq}"
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
    request = CT.owner_request("GET", "/events", host=fake_host)
    wire = Wire(request, fail_after=1)  # dies like a closed tab, so the stream returns
    response = await sse_route(routes).handler(request, sv.ctx)
    assert len(wire.frames) == 1
    assert response.headers["Content-Type"].startswith("text/event-stream")
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.headers["Connection"] == "keep-alive"
    assert response.headers["X-Accel-Buffering"] == "no"


@aio
async def test_the_sse_route_refuses_an_anonymous_reader_before_it_prepares(sv, routes, fake_host):
    request = CT.anon_request("GET", "/events", host=fake_host)
    wire = Wire(request)
    response = await sse_route(routes).handler(request, sv.ctx)
    assert response.status == 401
    assert wire.frames == []


# ---- activity ---- #

_ENTRY_KEYS = {"at", "source", "kind", "message_key", "params", "severity", "refs", "raw", "id"}


def test_activity_returns_studio_rows_as_timeline_entries(sv, ocall, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    asyncio.run(sv.activity.record(kind="repo.added", severity="info", repo_id=repo.repo_id, params={"label": "demo"}))
    status, body = ocall("GET", "/activity")
    assert status == 200
    assert body["next_cursor"] is None
    item = body["items"][0]
    assert set(item) == _ENTRY_KEYS
    assert item["source"] == "studio"
    assert item["kind"] == "repo.added"
    assert item["message_key"] == "activity.repo.added"
    assert item["raw"] is None


def test_activity_merges_the_audit_trail_for_one_intent(sv, ocall, gate_repo):
    repo = sv.repos.add(str(gate_repo), "demo")
    status, body = ocall("GET", "/activity", query={"repo": repo.repo_id, "intent": "260904-gate-demo"})
    assert status == 200
    assert "aidlc" in {item["source"] for item in body["items"]}
    assert all(set(item) == _ENTRY_KEYS for item in body["items"])
    assert body["next_cursor"] is None


@pytest.mark.parametrize("query", [{"severity": "urgent"}, {"cursor": "!!!"}])
def test_activity_refuses_an_unknown_severity_filter_or_a_malformed_cursor(ocall, query):
    status, body = ocall("GET", "/activity", query=query)
    assert status == 400 and body["code"] == "bad_param"


# ---- advisor ---- #


def test_requesting_a_draft_for_an_unknown_action_is_a_404(ocall):
    status, body = ocall("POST", "/advisor/draft", body={"action_id": "a_0000000000000009", "kind": "gate_analysis"})
    assert status in (404, 503)
    assert body["code"] in ("action_not_found", "advisor_unavailable")


def test_requesting_a_draft_with_an_unknown_kind_is_refused(ocall):
    status, body = ocall("POST", "/advisor/draft", body={"action_id": "a_1", "kind": "please_decide_for_me"})
    assert status == 400 and body["code"] == "invalid_decision"


def test_a_draft_request_needs_an_action_and_a_kind(ocall):
    status, body = ocall("POST", "/advisor/draft", body={"kind": "diagnose"})
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["missing"] == ["action_id"]


def test_an_unknown_draft_is_a_404(ocall):
    status, body = ocall("GET", "/advisor/drafts/d_0000000000000001")
    assert status == 404 and body["code"] == "draft_not_found"

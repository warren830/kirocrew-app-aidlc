"""One exception type, one code table, one JSON shape.

Handlers never choose an HTTP status: they raise ``StudioError(code, …)`` and the code table decides.
That keeps the API's machine-readable contract (PRD §15) in a single auditable place and makes it
impossible for two routes to report the same failure with different statuses.

The message is advisory English for logs and for a developer reading a response body. The UI renders
its own localized text keyed by ``code`` (`ui/src/i18n/*.json` has one entry per code), so adding a
code here means adding a catalog entry there — a test asserts the two sets match.
"""

from __future__ import annotations

from typing import Any

#: ``code -> HTTP status``. Data on purpose: the handler layer looks statuses up, tests assert every
#: raised code is present, and the UI's catalog is checked against the keys.
ERROR_CODES: dict[str, int] = {
    # ---- 400: the request itself is malformed ----
    "bad_body": 400,
    "bad_path": 400,
    "bad_param": 400,
    "invalid_decision": 400,
    "feedback_required": 400,
    "answers_incomplete": 400,
    "unknown_stage": 400,
    "unsupported_locale": 400,
    "invalid_settings": 400,
    # ---- 401 / 403: who is asking ----
    "unauthorized": 401,
    "owner_required": 403,
    "app_token_forbidden": 403,
    "sensitive_path": 403,
    "internal_secret_invalid": 403,
    # ---- 404: no such thing ----
    "repo_not_found": 404,
    "intent_not_found": 404,
    "action_not_found": 404,
    "artifact_not_found": 404,
    "draft_not_found": 404,
    "transaction_not_found": 404,
    "receipt_not_found": 404,
    "stage_not_found": 404,
    "route_not_found": 404,
    # ---- 409: the world is not in a state where this can proceed ----
    "duplicate_identity": 409,
    "repo_unavailable": 409,
    "identity_unprovable": 409,
    "action_stale": 409,
    "action_not_submittable": 409,
    "run_not_applicable": 409,
    "repo_busy": 409,
    "illegal_transition": 409,
    "stale_generation": 409,
    # the execution lease vanished or was regenerated between acquire and the Delivering CAS
    # (`Storage.deliver_under_lease`, review P15) — the caller must not send anything
    "lease_lost": 409,
    "state_inconsistent": 409,
    "machine_lane_unavailable": 409,
    # a multi-question grouped submit while `Settings.capabilities.grouped_answers` is off, i.e. while
    # `constants.GROUPED_ANSWERS_VERIFIED` is still False (review P18). Refusing is the safe direction:
    # one prompt carrying several answers is only known to work once the S1/S2 spike says so.
    "grouped_answers_unavailable": 409,
    "install_conflict": 409,
    "install_recovery_required": 409,
    "not_installed": 409,
    "already_installed": 409,
    "newer_installed": 409,
    "same_version_installed": 409,
    "state_version_migration_unconfirmed": 409,
    "payload_degraded": 409,
    "migration_not_applicable": 409,
    "migration_already_applied": 409,
    "session_unbound": 409,
    "slot_mismatch": 409,
    "slot_busy": 409,
    "session_busy": 409,
    "unstable_read": 409,
    "breaker_open": 409,
    "intent_paused": 409,
    "intent_archived": 409,
    "cancel_not_safe": 409,
    "delivery_ack_invalid": 409,
    "not_delivered_unproven": 409,
    "cursor_mismatch": 409,
    "plan_invalid": 409,
    "recompose_not_allowed": 409,
    "legacy_layout": 409,
    "rebind_not_allowed": 409,
    "retry_not_allowed": 409,
    "takeover_not_safe": 409,
    # ---- 413 / 429 ----
    "too_large": 413,
    "too_many_repos": 429,
    "rate_limited": 429,
    # ---- 5xx ----
    "storage_error": 500,
    "internal_error": 500,
    "host_unavailable": 503,
    "host_submission_unavailable": 503,
    "bun_missing": 503,
    "git_missing": 503,
    "storage_unavailable": 503,
    "advisor_unavailable": 503,
    "slack_unavailable": 503,
    "engine_unavailable": 503,
}


class StudioError(Exception):
    """A failure with a stable machine-readable code and a known HTTP status.

    ``details`` carries whatever the UI needs to act on the failure without parsing prose: the
    refreshed card for ``action_stale``, the lease owner and a retry hint for ``repo_busy``, the
    conflicting file list for ``install_conflict``. It must be JSON-serialisable; callers put no
    credentials or absolute paths outside the registered repo in it (see ``security.redact_json``).
    """

    __slots__ = ("code", "status", "message", "details")

    def __init__(self, code: str, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        try:
            status = ERROR_CODES[code]
        except KeyError:  # pragma: no cover - guarded by test_errors.py
            raise KeyError(
                f"unknown Studio error code {code!r}; add it to ERROR_CODES with its HTTP status"
            ) from None
        self.code = code
        self.status = status
        self.message = message or code.replace("_", " ")
        self.details: dict[str, Any] = dict(details or {})
        super().__init__(f"{code}: {self.message}")

    def to_json(self) -> dict[str, Any]:
        return {"error": self.message, "code": self.code, "details": self.details}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"StudioError(code={self.code!r}, status={self.status}, message={self.message!r})"


class StaleGeneration(StudioError):
    """A compare-and-set lost: the record changed under the caller.

    Raised by ``Storage.cas_update`` and by lease generation checks. Never retried automatically for
    a human action — a lost CAS means someone or something else already moved that action, and
    replaying it is exactly the duplicate the design forbids.
    """

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__("stale_generation", message or "record changed under this operation", details=details)


class IllegalTransition(StudioError):
    """A state change that the canonical transition table does not permit."""

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__("illegal_transition", message or "transition not permitted", details=details)


class LeaseHeld(StudioError):
    """Another operation owns the repository's lease.

    ``details`` = ``{"kind": "execution"|"admin", "owner": {...}, "retry_after_secs": int}``. A live
    owner is never preempted; the caller queues or asks the user to wait.
    """

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__("repo_busy", message or "repository is busy", details=details)


class Unstable(StudioError):
    """A file changed while it was being read as decision evidence.

    Surfaced as ``Refreshing`` in the UI: the snapshot cannot authorise a decision (FR-ACT-009), and
    the caller must re-read rather than proceed with either version.
    """

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__("unstable_read", message or "file changed while being read", details=details)


class ActionStale(StudioError):
    """The captured evidence no longer matches the live state; the decision must be re-confirmed."""

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__("action_stale", message or "the decision context changed", details=details)


def status_for(code: str) -> int:
    """HTTP status for a code, or 500 for an unknown one (never raises in a response path)."""
    return ERROR_CODES.get(code, 500)

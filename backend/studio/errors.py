"""One exception type, one code table, one JSON shape.

Handlers never choose an HTTP status: they raise ``StudioError(code, …)`` and the table decides, so two routes
cannot report the same failure with different statuses (PRD §15). The message is advisory English for logs;
the UI renders its own text keyed by ``code`` (``ui/src/i18n``), and a test keeps the two sets in step.
"""

from __future__ import annotations

from typing import Any

#: ``code -> HTTP status``, one row per status: every code a module raises, plus ``route_not_found``, which
#: the UI also uses for a bare host 404. ``lease_lost``: the execution lease vanished between acquire and the
#: Delivering CAS (review P15); ``grouped_answers_unavailable``: a grouped submit while
#: ``GROUPED_ANSWERS_VERIFIED`` is off (P18).
_CODES_BY_STATUS: dict[int, str] = {
    400: (
        "bad_body bad_path bad_param invalid_decision feedback_required answers_incomplete unknown_stage "
        "unsupported_locale"
    ),
    401: "unauthorized",
    403: "owner_required app_token_forbidden sensitive_path",
    404: (
        "repo_not_found intent_not_found action_not_found artifact_not_found draft_not_found "
        "transaction_not_found receipt_not_found route_not_found"
    ),
    409: (
        "duplicate_identity repo_unavailable identity_unprovable action_stale action_not_submittable "
        "run_not_applicable repo_busy illegal_transition stale_generation lease_lost state_inconsistent "
        "machine_lane_unavailable grouped_answers_unavailable install_conflict install_recovery_required "
        "not_installed already_installed newer_installed same_version_installed "
        "state_version_migration_unconfirmed payload_degraded session_unbound slot_mismatch slot_busy "
        "session_busy unstable_read breaker_open intent_paused intent_archived cancel_not_safe "
        "delivery_ack_invalid not_delivered_unproven cursor_mismatch plan_invalid recompose_not_allowed "
        "legacy_layout rebind_not_allowed retry_not_allowed"
    ),
    413: "too_large",
    429: "too_many_repos rate_limited",
    500: "storage_error internal_error",
    503: "host_unavailable bun_missing git_missing storage_unavailable advisor_unavailable engine_unavailable",
}
ERROR_CODES: dict[str, int] = {code: status for status, codes in _CODES_BY_STATUS.items() for code in codes.split()}


class StudioError(Exception):
    """A failure with a stable machine-readable code and a known HTTP status.

    ``details`` carries what the UI needs to act without parsing prose (the refreshed card for
    ``action_stale``, the lease owner for ``repo_busy``, the conflicting files for ``install_conflict``). It
    must be JSON-serialisable and carry no credentials or paths outside the registered repository.
    """

    __slots__ = ("code", "status", "message", "details")

    def __init__(self, code: str, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        try:
            status = ERROR_CODES[code]
        except KeyError:  # pragma: no cover - guarded by tests
            raise KeyError(f"unknown Studio error code {code!r}; add it to ERROR_CODES with its HTTP status") from None
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
    """A compare-and-set lost: someone else already moved the record, so a human action is never retried."""

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__("stale_generation", message or "record changed under this operation", details=details)


class IllegalTransition(StudioError):
    """A state change that the canonical transition table does not permit."""

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__("illegal_transition", message or "transition not permitted", details=details)


class LeaseHeld(StudioError):
    """Another operation owns the repository; a live owner is never preempted.

    ``details`` = ``{"kind": "execution"|"admin", "owner": {...}, "retry_after_secs": int}``.
    """

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__("repo_busy", message or "repository is busy", details=details)

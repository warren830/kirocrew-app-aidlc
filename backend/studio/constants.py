"""Every limit, deadline, grammar, enum and protocol constant AI-DLC Studio depends on, in one module so the whole
envelope can be audited in one read and no other module invents a cap or a wire string. No I/O, no Studio imports.

* **Grammar** parses AI-DLC's own files and tolerates version drift (State Version 7 and 8, 32- and 33-stage graphs,
  three em-dash spellings): the engine rewrites its templates between releases, and a strict parser would
  confidently misread a newer file. Every pattern was checked against the fixtures under ``tests/fixtures/``.
* **Wire text** is the exact bytes a human decision sends into the conversation. The engine supplies no response
  token, so these are protocol constants and casing is load-bearing (a gate says ``Request Changes``, the summary
  checkpoint ``Request changes``); ``docs/design/wire-text.json`` pins them for both languages.
* **Env scrubbing** guards every subprocess: AI-DLC reads bypass variables from the environment, so Studio proves
  they are absent rather than merely not setting them.
"""

from __future__ import annotations

import calendar
import re
import time
from typing import Any, Mapping, Protocol

# ---- identity ------------------------------------------------------------- #

#: The name ``app.json`` ships with; a side-by-side development install runs under another (:data:`APP_NAME`).
DEFAULT_APP_NAME = "aidlc-studio"


def _installed_name(module_name: str) -> str:
    """The app name the host loaded this module under: KiroCrew 0.8 imports an app's backend as
    ``_kirocrew_app_<name>.backend…``. Anything else (a script, a REPL) gets the shipped name."""
    head, prefix = module_name.split(".", 1)[0], "_kirocrew_app_"
    return head[len(prefix):] if head.startswith(prefix) and len(head) > len(prefix) else DEFAULT_APP_NAME


APP_NAME = _installed_name(__name__)
#: Must equal ``app.json`` ``version`` (pinned by tests/test_manifest.py).
APP_VERSION = "2.0.0-beta.1"
#: Must equal ``app.json`` ``minKiroCrewVersion``: the 0.8 loader registers the app's parent packages, which is
#: what lets ``backend/routes.py`` import ``studio`` relatively.
MIN_KIROCREW_VERSION = "0.8.0"
#: Must equal ``payload/manifest.json`` ``engineVersion``.
BUNDLED_ENGINE_VERSION = "2.11.0"
#: State-file versions the parsers accept: wider than what the bundled engine writes, because a registered repo may
#: run an older AI-DLC and refusing to read it would hide work the user can still see on disk.
SUPPORTED_STATE_VERSIONS = (7, 8)
#: Bytes of entropy in ``Services.boot_id``, stamped on every ``Delivering`` row: the only way to tell "this process
#: sent it" from "the process before the restart did", which keeps ``NotDelivered`` unreachable after a crash (C24).
BOOT_ID_BYTES = 8

# ---- bounded reads (bytes) ------------------------------------------------ #

MAX_STATE_BYTES = 512 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_RENDER_BYTES = 1024 * 1024
#: The engine's own sources, read for ``CURRENT_STATE_VERSION``: code, not artifacts, and 2.10.0's ``aidlc-lib.ts`` is
#: 1.2 MiB, which the render cap read as absent.
MAX_ENGINE_SOURCE_BYTES = 8 * 1024 * 1024
MAX_AUDIT_TAIL_BYTES = 256 * 1024
#: The same aggregate as the former 64 × 256 KiB shard windows.
MAX_AUDIT_HISTORY_BYTES = 16 * 1024 * 1024
MAX_QUESTIONS_BYTES = 256 * 1024
MAX_TEXT_PREVIEW_BYTES = 64 * 1024
MAX_ARTIFACTS_PER_INTENT = 2000
MAX_INTENTS_PER_REPO = 256
MAX_REPOS = 64
MAX_AUDIT_SHARDS = 64
MAX_SUBPROCESS_STDOUT = 256 * 1024
MAX_SUBPROCESS_STDERR = 64 * 1024
MAX_FEEDBACK_CHARS = 8000
MAX_ANSWER_CHARS = 8000
MAX_LABEL_CHARS = 120
MAX_EVIDENCE_PACKAGE_CHARS = 60_000
#: How much of a subagent's ``result.txt`` Studio reads back when the host's completion event kept only
#: ``agent.completion_keep_chars`` (3 000 by default; a real Advisor draft is ~10 KB): far above a draft, far below a
#: memory incident. Bytes, because the recovery read is byte-capped (``security.bounded_read``).
MAX_SUBAGENT_RESULT_BYTES = 200_000

# ---- deadlines (seconds) -------------------------------------------------- #

REPO_SCAN_DEADLINE_SECS = 10.0
SUBPROCESS_TIMEOUT_SECS = 60.0
ENGINE_READ_TIMEOUT_SECS = 20.0
GIT_TIMEOUT_SECS = 15.0
RECONCILE_INTERVAL_SECS = 5.0
#: Concurrent repository scans, and the size of their private pool (``Services.scan_pool``): a slow filesystem
#: cannot starve the default executor every other ``to_thread`` shares, and a third thread could only wait.
SCAN_CONCURRENCY = 2
#: Wall budget one tick may spend scanning; repositories with no open action are round-robined across ticks, so a
#: 64-repo install cannot make one tick take minutes (review R11).
SCAN_TICK_BUDGET_SECS = 4.0
#: How long the UI has to report its host submission before the reconciler looks for evidence instead.
DELIVERY_ACK_DEADLINE_SECS = 90
#: Upper bound for "the turn should have finished by now": ``min(agent.chat_turn_timeout_secs, this)``.
PROCESSING_DEADLINE_CAP_SECS = 7200
#: A message the host accepted behind a running turn has not started its own yet, so it may wait this multiple of
#: the turn timeout in ``Delivered`` before it counts as uncertain.
DELIVERED_QUEUED_WAIT_FACTOR = 2
SSE_HEARTBEAT_SECS = 15.0
STABLE_READ_RETRY_SECS = 0.05
#: An unreadable state file is often a write in flight, so it blocks only once it has stayed unreadable this long.
STATE_UNREADABLE_GRACE_SECS = 60
#: How long an intent directory must stay missing before its cards are retired: one tick's absence is a branch
#: switch or a rename in progress, and cancelling then would dismiss a gate the human is about to answer.
INTENT_MISSING_GRACE_SECS = 60
#: ``on_startup`` must return well inside the host's 30 s budget, or the gateway detaches it and refuses later
#: lifecycle operations for this app.
HOST_STARTUP_HOOK_BUDGET_SECS = 10.0
ADVISOR_TIMEOUT_SECS = 300.0
#: The ``name`` inside ``agents/advisor.json``. ``SpawnSDK.run`` matches on it (`kc:apps/spawn_sdk.py:173-181`), so the
#: JSON, ``spawn.run`` and the uniqueness check must share one spelling or a rename silently breaks the advisor (C25).
ADVISOR_AGENT_NAME = f"{APP_NAME}-advisor"

#: Automatic advisor drafts queued or running at once across the installation (FR-ADV-010): after a night of engine
#: work a repository can hold twenty open cards, and the first tick after the opt-in would otherwise spawn twenty
#: models. A draft a human clicked for is never counted or blocked.
ADVISOR_AUTO_DRAFT_MAX_INFLIGHT = 2
#: Automatic drafts one tick may start: one, so the ramp is visible in Activity and a misconfiguration costs one spawn.
ADVISOR_AUTO_DRAFT_PER_TICK = 1
#: Open drafts one tick settles. The host prunes a finished subagent's answer after
#: ``agent.subagent_result_ttl_secs`` (an hour by default) and no browser polls a pre-draft, so the tick must; a poll
#: is one in-memory read, and a human may have clicked far more drafts than the auto cap allows.
ADVISOR_SETTLE_PER_TICK = 8

#: Every advisor draft kind (§1.18); here so scripts/gen_ui_sources.py can generate the UI's ``AdvisorKind``.
ADVISOR_KINDS: tuple[str, ...] = (
    "gate_analysis",
    "question_draft",
    "question_explain",
    "request_changes_draft",
    "diagnose",
    "plan_draft",
)
#: Kinds with no card: a plan draft is asked for in the new-intent wizard, before any intent exists (§1.18a).
ADVISOR_CARDLESS_KINDS: tuple[str, ...] = ("plan_draft",)
#: ``advisor_drafts.action_id`` is NOT NULL, so a card-less draft stores ``plan:<repo_id>``, never a card's ``a_<hex>``.
ADVISOR_PLAN_ACTION_PREFIX = "plan:"
#: The objective and the context a plan draft quotes, each: a page of prose; longer is a document to attach later.
MAX_PLAN_OBJECTIVE_CHARS = 4000
#: Rows of ``intents.json`` a plan package lists as "what this repository already has".
MAX_PLAN_EXISTING_INTENTS = 10
#: Top-level names in ``WorkspaceSignals``: enough to see a project's shape, too few to be a listing.
MAX_WORKSPACE_ENTRIES = 60
#: Characters of the README the plan package quotes (FR-ADV-003: bounded, one file, names otherwise).
MAX_README_CHARS = 3000

# ---- retention and ring buffers ------------------------------------------- #

EVENTS_RING_SIZE = 2_000
ACTIVITY_DEFAULT_LIMIT = 100
ACTIVITY_MAX_LIMIT = 500
HUMAN_TEXT_RETENTION_DAYS_DEFAULT = 30
HUMAN_TEXT_RETENTION_DAYS_MAX = 30
ADVISOR_DRAFT_TTL_SECS = 24 * 3600

# ---- circuit breaker ------------------------------------------------------ #

BREAKER_TRANSIENT_THRESHOLD = 3
#: Failure classes that open the breaker only after ``BREAKER_TRANSIENT_THRESHOLD`` repeats; any other opens it at once.
TRANSIENT_FINGERPRINT_CLASSES = ("transport", "rate_limit", "acp_timeout")

# ---- canonical enums (PRD §11 — verbatim wire strings, never localized) ---- #

#: ``Delivered`` covers the running turn too (``evidence.turn_started_at``; the UI says "processing"), and
#: ``ReconciliationRequired`` is every delivery Studio cannot account for, from the first moment of doubt.
ACTION_STATUS = (
    "Queued",
    "Delivering",
    "Delivered",
    "StateChanged",
    "ResolvedNoTransition",
    "NotDelivered",
    "ReconciliationRequired",
    "Failed",
    "Cancelled",
)
#: Statuses that keep a card in the Action Center queue.
LIVE_ACTION_STATUS = (
    "Queued",
    "Delivering",
    "Delivered",
    "ReconciliationRequired",
    "NotDelivered",
    "Failed",
)
TERMINAL_ACTION_STATUS = ("StateChanged", "ResolvedNoTransition", "Cancelled")
#: Live statuses that wait on a person. A sent decision (``Delivering``/``Delivered``) waits on AI-DLC: the Action
#: Center hides it (``ui/src/lib/actionQueue.ts``), so no "waiting for you" count may include it either.
AWAITING_PERSON_STATUS = ("Queued", "ReconciliationRequired", "NotDelivered", "Failed")

INTENT_STATE = (
    "Idle",
    "Queued",
    "Running",
    "WaitingForYou",
    "Paused",
    "Parked",
    "Interrupted",
    "ReconciliationRequired",
    "RetryEligible",
    "CircuitOpen",
    "Failed",
    "Completed",
    "Archived",
)
ACTION_TYPE = (
    "gate",
    "revision",
    "question",
    "missing_input",
    "recovery",
    "delivery_uncertain",
    "failure",
    "circuit_breaker",
    "install_conflict",
    "budget_stop",
    "run",
    "resume",
    "force_stop",
    "prepare_commit",
)
#: Every consequence variant each card type may emit, so the i18n gate proves no card reaches a person without a "what
#: happens if you act" sentence. Declared, not read from ``projection.SEED_CONSEQUENCE``: two sites override the seed at
#: runtime (``gate`` on the last stage, ``run``); ``tests/test_constants.py`` checks seeds and overrides against it.
CARD_CONSEQUENCE_VARIANTS: dict[str, tuple[str, ...]] = {
    "gate": ("approve_unlocks", "final_stage"),
    "question": ("advances_turn", "plan_session_stale"),
    "recovery": ("blocked_until_agree",),
    "delivery_uncertain": ("no_replay",),
    "failure": ("no_dispatch",),
    "install_conflict": ("nothing_written",),
    "run": ("one_turn",),
}
SEVERITY = ("critical", "blocking", "attention", "info")
FINDING_SEVERITY = ("blocking", "warn", "info")
SOURCE = ("studio", "aidlc", "kirocrew", "git", "slack")
#: ``host_control`` is ``force_stop`` alone (§0.5, review P16): it reaches the host through the chat stop endpoint,
#: so it is neither ``human_lane`` (no prompt, no lease, no cursor switch) nor ``studio_only`` (it changes the host).
RISK_CLASS = ("read", "studio_only", "human_lane", "admin", "host_control")
LEASE_KIND = ("execution", "admin")
INSTALL_STATUS = ("not_installed", "installed", "drift", "recovery_required")
AVAILABILITY = ("available", "moved", "permission_denied", "unavailable", "identity_unprovable")
OWNERSHIP = ("framework", "framework-mutable", "merge", "shell")
STAGE_STATE = (
    "not_started",
    "in_progress",
    "awaiting_approval",
    "revising",
    "completed",
    "skipped",
    "unknown",
)
#: Every decision a card can offer, in three lanes that reach AI-DLC differently: HUMAN_LANE decisions become a
#: prompt in the intent's own conversation, HOST_CONTROL uses a host endpoint (force stop), and STUDIO_ONLY ones
#: change only Studio's records.
DECISION_KIND = (
    "approve",
    "request_changes",
    "accept_as_is",
    "approve_plan",
    "request_plan_changes",
    "confirm_summary",
    "accept_assumptions",
    "convert_assumptions",
    "answers",
    "provide_input",
    "run",
    "resume",
    "prepare_commit",
    "force_stop",
    "rebind_session",
    "mark_not_delivered",
    "acknowledge",
    "reconcile",
    "resubmit",
    "retry_now",
    "keep_paused",
    "run_now",
    "pick_intent",
)
HUMAN_LANE_DECISIONS = frozenset(
    {
        "approve",
        "request_changes",
        "accept_as_is",
        "answers",
        "confirm_summary",
        "accept_assumptions",
        "convert_assumptions",
        "approve_plan",
        "request_plan_changes",
        "provide_input",
        "run",
        "resume",
        "prepare_commit",
    }
)
HOST_CONTROL_DECISIONS = frozenset({"force_stop"})
STUDIO_ONLY_DECISIONS = frozenset(
    {
        "rebind_session",
        "mark_not_delivered",
        "acknowledge",
        "reconcile",
        "resubmit",
        "retry_now",
        "keep_paused",
        "run_now",
        "pick_intent",
    }
)

#: Action type → every decision it may carry (§1.12): ``projection`` offers from this table and the broker validates
#: against it, so a card cannot offer a decision answered ``invalid_decision``. A row is what is *legal*; what is
#: *offered* can be narrower (``accept_as_is`` from ``ACCEPT_AS_IS_MIN_ATTEMPT``, question decisions per checkpoint).
DECISIONS: dict[str, tuple[str, ...]] = {
    "gate": ("approve", "request_changes", "accept_as_is"),
    "question": (
        "answers", "confirm_summary", "accept_assumptions", "convert_assumptions", "approve_plan",
        "request_plan_changes", "resume",
    ),
    "missing_input": ("provide_input", "pick_intent"),
    "recovery": ("rebind_session", "mark_not_delivered", "acknowledge"),
    "delivery_uncertain": ("reconcile", "mark_not_delivered", "resubmit"),
    "failure": ("retry_now", "keep_paused"),
    "circuit_breaker": ("retry_now", "keep_paused"),
    "install_conflict": (),  # navigation only (Repos page)
    "budget_stop": ("run_now", "keep_paused"),
    "revision": (),  # informational (C18)
    "run": ("run",),
    "resume": ("resume",),
    "force_stop": ("force_stop",),
    "prepare_commit": ("prepare_commit",),
}

#: Legal action-state transitions (PRD §11.1, with ``Processing`` folded into ``Delivered`` and
#: ``DeliveryUncertain`` into ``ReconciliationRequired``). Data, so the state machine and its test read one table.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "Queued": ("Delivering", "Cancelled"),
    "Delivering": ("Delivered", "NotDelivered", "ReconciliationRequired"),
    "Delivered": ("StateChanged", "ResolvedNoTransition", "ReconciliationRequired", "Failed"),
    "ReconciliationRequired": ("StateChanged", "ResolvedNoTransition", "NotDelivered", "Failed"),
    "NotDelivered": ("Queued", "Cancelled"),
    "StateChanged": (),
    "ResolvedNoTransition": (),
    "Failed": (),
    "Cancelled": (),
}


# ---- AI-DLC grammar ------------------------------------------------------- #

#: ``- [x] build-and-test — EXECUTE``. Current writers use an em dash; en dash and ``--`` are older files, and a bare
#: stage row without the mode suffix still parses.
CHECKBOX_RE = re.compile(
    r"^- \[([ xXsSrR?\-])\] (\S+)(?:\s*(?:—|–|--)\s*(.*))?$", re.MULTILINE
)
#: ``- **Current Stage**: build-and-test``. The value may be empty: 2.6.2 writes ``- **Review Override**: `` with a
#: trailing space and nothing after it.
FIELD_RE = re.compile(r"^- \*\*(.+?)\*\*:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
SUBSECTION_RE = re.compile(r"^###\s+(.+?)\s*$")
PHASE_HEADER_RE = re.compile(r"^([A-Z][A-Z ]*?)\s+PHASE\s*$")
#: Audit blocks use ``**Key**: value`` lines; a few writers prefix them with ``- ``.
AUDIT_FIELD_RE = re.compile(r"^(?:- )?\*\*([A-Za-z][A-Za-z0-9 ._()/-]*)\*\*:[ \t]?(.*)$")
#: Audit shards are a sequence of blocks separated by a ``---`` fence on its own line.
AUDIT_BLOCK_SEPARATOR = "\n---\n"
ANSWER_TAG_RE = re.compile(r"^\[Answer\]:[ \t]*(.*)$", re.MULTILINE)
#: An unanswered tag: empty, or only the underscore placeholder the templates write.
BLANK_ANSWER_RE = re.compile(r"^\[Answer\]:[ \t]*_*[ \t]*$", re.MULTILINE)
#: A blank tag that still owes an answer, bare or as a list item. The engine's Stop hook (``hasPendingQuestion``)
#: matches the tag anywhere on a line; quoted and indented copies stay excluded here on purpose: they are examples.
PENDING_ANSWER_TAG_RE = re.compile(r"^(?:[-*+][ \t]+)?\[Answer\]:[ \t]*_*[ \t]*$")
#: ``## Q1. …`` in every file before 2.10; a 2.10 conductor also writes ``## Question 1: …`` (the protocol fixes the
#: answer tags and options, not the heading), and either may separate the title with a dash.
QUESTION_HEADING_RE = re.compile(r"^## Q(?:uestion)?[ \t]*(\d+)(?:[.:]|[ \t]+[—–-])?\s*(.*)$", re.MULTILINE)
#: ``- A. Some option`` / ``A. Some option`` / ``- X. Other (please specify)``.
OPTION_LINE_RE = re.compile(r"^(?:- )?([A-Z])\.[ \t]+(.*)$", re.MULTILINE)
OTHER_OPTION_LETTER = "X"
#: Bullet lines immediately after an ``[Answer]:`` tag continue that answer (real files do this).
ANSWER_CONTINUATION_RE = re.compile(r"^- (.*)$")
#: Checkpoint headings: the reader maps a heading to a checkpoint kind and the pattern below matches it.
SUMMARY_CONFIRMATION_HEADING = "Consolidated Summary Confirmation"
PLAN_APPROVAL_HEADING = "Plan Approval"
#: The engine's audit name for the same checkpoint differs from the Markdown heading.
PLAN_APPROVAL_AUDIT_CHECKPOINT = "Code Generation Plan Approval"
#: Every ``Checkpoint`` an audit row names Plan Approval by: 2.10's, and 2.11's engine-held question.
PLAN_APPROVAL_AUDIT_CHECKPOINTS = (PLAN_APPROVAL_AUDIT_CHECKPOINT, "plan-approval")
#: 2.11 asks Plan Approval from the engine and writes its choices lettered (``- A. Approve Plan``); the answer it
#: records is the lettered choice (``[Answer]: A. Approve Plan``). 2.10 never lettered them.
PLAN_APPROVAL_OPTION_RE = re.compile(
    r"^- ([A-C])\.[ \t]+(Approve Plan|Approve all|Request Changes|I'll edit the files)\s*$", re.MULTILINE
)
PLAN_ANSWER_LETTER_RE = re.compile(r"^[A-Z]\.[ \t]+")
#: The ``Details`` of the plan-approval ``QUESTION_ANSWERED`` row 2.10 writes for a Request Changes choice.
PLAN_REQUEST_CHANGES_CHOICE = "Request Changes"
#: What a code stage lists as written (``writes[].path``) in its record; a gate on it shows those files' change.
SOURCE_MANIFEST_NAME = "source-manifest.json"
#: 2.11 writes that manifest only for a Unit. Zero-Unit code generation binds the whole workspace instead, and its
#: ``traceability.json`` names the files: every ``OK`` coverage target is "one existing workspace-relative
#: implementation or test file".
CODE_STAGE = "code-generation"
TRACEABILITY_NAME = "traceability.json"
MAX_SOURCE_CHANGE_FILES = 50
#: Source manifests read to name which intent wrote a dirty file on the repository's Git panel.
MAX_SOURCE_MANIFESTS = 200
MAX_SOURCE_CHANGE_DIFF_BYTES = 64 * 1024
#: The whole ``…/changes`` response: past it, the remaining files list their counts without a diff.
MAX_SOURCE_CHANGES_DIFF_BYTES = 512 * 1024
#: The replies a Plan Approval card sends; AI-DLC records them only in the session that asked.
PLAN_REPLY_DECISIONS = ("approve_plan", "request_plan_changes")
POST_APPROVAL_AMENDMENT_HEADING = "Post-approval Amendment"
#: 2.10's intent-capture Step 5: kept assumptions are accepted or turned into follow-up questions. The engine fills
#: the tag with the chosen option verbatim (``A. Accept assumptions``), so the option texts are its wire text.
ASSUMPTION_CONFIRMATION_HEADING = "Assumption Confirmation"
#: ``Checkpoint.kind`` values a question card can be waiting on, in the order the engine asks them.
QUESTION_CHECKPOINT_KINDS = ("summary_confirmation", "assumption_confirmation", "plan_approval")
#: Blocks inside a questions file that are checkpoints rather than questions; each has its own wire text.
CHECKPOINT_HEADING_RE = re.compile(
    "^## ("
    + "|".join(
        re.escape(h)
        for h in (
            PLAN_APPROVAL_HEADING, SUMMARY_CONFIRMATION_HEADING, ASSUMPTION_CONFIRMATION_HEADING,
            POST_APPROVAL_AMENDMENT_HEADING,
        )
    )
    + r")\s*$",
    re.MULTILINE,
)
CHECKPOINT_OPTION_RE = re.compile(
    r"^- (Approve Plan|Request Changes|Looks correct|Request changes)\s*$", re.MULTILINE
)
#: The Assumption Confirmation's two options, written as lettered lines (``A. Accept assumptions``).
ASSUMPTION_OPTION_RE = re.compile(
    r"^(?:- )?(A\. Accept assumptions|B\. Convert to follow-up questions)\s*$", re.MULTILINE
)

INTENT_DIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SPACE_RE = INTENT_DIR_RE
UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
STAGE_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")
#: A full commit id as `git status --porcelain=v2` prints it (SHA-1, or SHA-256 in a sha256 repository).
COMMIT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ENGINE_VERSION_RE = re.compile(r'AIDLC_VERSION\s*=\s*"([0-9][0-9.]*?)"')
ENGINE_STATE_VERSION_RE = re.compile(r'CURRENT_STATE_VERSION\s*=\s*"(\d+)"')
STATE_VERSION_FIELD = "State Version"
#: ``- **State Version**: 8`` in a state file *or* an engine's state template, the only place a pre-2.5 build records
#: the version it writes. The bullet is optional because a template carries the line without one.
TEMPLATE_STATE_VERSION_RE = re.compile(
    rf"^-? ?\*\*{re.escape(STATE_VERSION_FIELD)}\*\*:[ \t]*(\d+)", re.MULTILINE
)

#: Checkbox mark (lowercased) → stage state.
CHECKBOX_MAP = {
    " ": "not_started",
    "-": "in_progress",
    "?": "awaiting_approval",
    "r": "revising",
    "x": "completed",
    "s": "skipped",
}
#: Stage states that count as finished for progress arithmetic.
DONE_STAGE_STATES = ("completed", "skipped")

PHASES = ("initialization", "ideation", "inception", "construction", "operation")
#: Stages the engine may run once per Construction unit; the map expands these into sub-lanes.
PER_UNIT_STAGES = (
    "functional-design",
    "nfr-requirements",
    "nfr-design",
    "infrastructure-design",
    "code-generation",
)
#: Directories an AI-DLC harness may install into. ``<dir>/tools/data/harness.json`` is the marker.
HARNESS_DIRS = (".kiro", ".claude", ".codex", ".cursor", ".aidlc")
#: The harness Studio installs and drives.
STUDIO_HARNESS_DIR = ".kiro"
AIDLC_AGENT_NAME = "aidlc"
LEGACY_STATE_REL = "aidlc-docs/aidlc-state.md"

# ---- AI-DLC audit event classification ------------------------------------ #

GATE_EVENTS = ("STAGE_AWAITING_APPROVAL", "GATE_APPROVED", "GATE_REJECTED", "STAGE_REVISING")
#: ``PLAN_APPROVAL_RECORDED`` is 2.7.1's row for an approved Code Generation plan (2.6.2 wrote ``QUESTION_ANSWERED``).
QUESTION_EVENTS = (
    "DECISION_RECORDED",
    "QUESTION_ANSWERED",
    "SUMMARY_CONFIRMATION_RECORDED",
    "PLAN_APPROVAL_RECORDED",
)
REVIEW_EVENTS = ("REVIEW_REQUESTED", "REVIEW_COMPLETED")
BOUNDARY_EVENTS = GATE_EVENTS + QUESTION_EVENTS
#: The engine's own ``GATE_RESOLUTION_EVENTS`` (``aidlc-lib.ts``) member for member, so "did the boundary move" has
#: the engine's answer; tests/test_reconciler.py compares them. Not mirrored: the carve-out for ``AUTONOMY_MODE_SET``
#: with ``Mode: autonomous`` — Studio never dispatches unattended, so a handover is no resolution it may claim.
RESOLUTION_EVENTS = (
    "GATE_APPROVED",
    "GATE_REJECTED",
    "QUESTION_ANSWERED",
    "SUMMARY_CONFIRMATION_RECORDED",
    "PLAN_APPROVAL_RECORDED",
    # 2.10.0 resolves the Construction checkpoint-policy and verification-command gates with their own rows.
    "CONSTRUCTION_POLICY_RECORDED",
    "VERIFICATION_COMMAND_RECORDED",
)
#: Events that prove the workflow moved. Audit growth alone is not movement: a session start or a failed command
#: appends rows too (``NOISE_EVENTS``).
MOVEMENT_EVENTS = BOUNDARY_EVENTS + (
    "STAGE_STARTED",
    "STAGE_COMPLETED",
    "STAGE_SKIPPED",
    "STAGE_JUMPED",
    "PHASE_STARTED",
    "PHASE_COMPLETED",
    "PHASE_VERIFIED",
    "WORKFLOW_COMPLETED",
    "WORKFLOW_PARKED",
    "WORKFLOW_UNPARKED",
    "RECOMPOSED",
    "SCOPE_CHANGED",
    "DEPTH_CHANGED",
    "TEST_STRATEGY_CHANGED",
)
NOISE_EVENTS = (
    "HUMAN_TURN",
    "SESSION_STARTED",
    "SESSION_ENDED",
    "SESSION_RESUMED",
    "SESSION_COMPACTED",
    "HEALTH_CHECKED",
    "GUARDRAIL_LOADED",
    "SENSOR_FIRED",
    "SENSOR_PASSED",
    "SENSOR_FAILED",
    "SUBAGENT_COMPLETED",
    "ARTIFACT_CREATED",
    "ARTIFACT_UPDATED",
)
HUMAN_PRESENCE_EVENT = "HUMAN_TURN"
ERROR_EVENT = "ERROR_LOGGED"


# ---- engine invocation allowlist ------------------------------------------ #

#: Read-only verbs the backend may run unattended. Anything that can transition state, append a protected audit event
#: or repair the workflow stays absent (``next``, ``report``, ``park``, the ``aidlc-state.ts`` mutators, ``aidlc-audit.ts
#: append*``, ``aidlc-jump.ts execute``); ``doctor`` appends audit rows, so it is admin-only. An older engine may lack a
#: verb, and ``EngineRunner`` reports ``engine_verb_unsupported`` rather than guess. The engine refuses a present but
#: blank value flag (``--scope``, ``--label``, ``--depth``, …), so a flag without a value stays out of the argv.
ENGINE_READ_VERBS: dict[str, tuple[str, ...]] = {
    "aidlc-utility.ts": ("version", "intent", "space"),
    "aidlc-state.ts": ("lookup",),
}
#: Verbs Studio may run under a RepoAdminLease on explicit user action: each mutates something the engine owns, by
#: the engine's own supported way.
ENGINE_ADMIN_VERBS: dict[str, tuple[str, ...]] = {
    "aidlc-runtime.ts": ("compile",),  # derived runtime graph; never a workflow transition
    "aidlc-utility.ts": (
        "intent",          # switch the active-intent cursor (dirName argument)
        "space",           # switch the active space (also rewrites .kiro/agents/*.json resources)
        "space-create",
        "intent-create",   # 2.6+; `intent-birth` in older engines (renamed, old name now dies)
        "recompose",
        "scope-change",
        "config-change",
        "doctor",          # audit-appending; only ever on an explicit user request
    ),
}
#: Spellings per engine generation, newest first; ``EngineRunner`` picks one from the installed ``AIDLC_VERSION``.
ENGINE_VERB_ALIASES: dict[str, tuple[str, ...]] = {
    "intent-create": ("intent-create", "intent-birth"),
}
#: ``aidlc-utility.ts upgrade`` only prints "unavailable" and exits 1, so Studio's transactional installer owns
#: upgrades (FR-INST-015). Listed so nobody re-adds it.
ENGINE_UNAVAILABLE_VERBS = ("upgrade",)
#: Tools and verbs no argv Studio builds may contain (``security.assert_engine_argv_allowed``): the floor under the
#: allowlists, so a new engine-owned tool added to a verb table by mistake is still refused.
ENGINE_FORBIDDEN_TOOLS = ("aidlc-orchestrate.ts", "aidlc-audit.ts", "aidlc-jump.ts", "aidlc-log.ts")
ENGINE_FORBIDDEN_VERBS = (
    "next",
    "report",
    "park",
    "unpark",
    "approve",
    "reject",
    "revise",
    "advance",
    "skip",
    "set",
    "checkbox",
    "gate-start",
    "finalize",
    "complete-workflow",
    "append",
    "append-raw",
    "execute",
    "fork",
    "merge",
)

# ---- git ------------------------------------------------------------------ #

#: A static test greps the backend for these, and ``assert_git_argv_readonly`` refuses them at runtime.
GIT_WRITE_VERBS = frozenset(
    "add commit push pull merge rebase checkout switch reset restore stash tag branch cherry-pick "
    "revert am apply clean rm mv fetch remote submodule worktree gc prune reflog update-ref "
    "symbolic-ref filter-branch notes replace bisect init clone".split()
)
#: The only verbs Studio may run; an unlisted verb is refused even if it looks harmless.
GIT_READ_VERBS = frozenset(
    "rev-parse status log diff show ls-files name-rev describe cat-file rev-list for-each-ref "
    "merge-base".split()
)
#: Global options that can point git at another repository, run an arbitrary binary or write a file, refused wherever
#: they appear: ``cwd``, never ``-C``, is how Studio selects a repository.
GIT_FORBIDDEN_TOKENS = frozenset(
    {"-c", "-C", "--git-dir", "--work-tree", "--exec-path", "--output", "--namespace",
     "--config-env", "--super-prefix"}
)
GIT_FORBIDDEN_PREFIXES = (
    "--output=", "--exec-path=", "--git-dir=", "--work-tree=", "--namespace=", "--config-env=",
)

# ---- subprocess environment ----------------------------------------------- #

ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
#: AI-DLC and the harnesses read their configuration from the environment; inheriting the gateway's would silently
#: point the engine at another project or framework tree.
ENV_FORBIDDEN_PREFIXES = ("AIDLC_", "CLAUDE_", "AWS_AIDLC_", "KIRO", "KIROCREW_")
#: Bypass switches asserted absent, so one exported in the gateway's shell cannot disable AI-DLC's anti-forgery guard
#: through a process Studio starts.
ENV_MUST_BE_ABSENT = (
    "AIDLC_SKIP_HUMAN_PRESENCE_GUARD",
    "AIDLC_SKIP_ARTIFACT_GUARD",
    "CLAUDE_PROJECT_DIR",
    "AIDLC_HARNESS_DIR",
    "AIDLC_RULES_SUBDIR",
    "AIDLC_STAGE_GRAPH",
    "AIDLC_SCOPE_GRID",
)

# ---- wire text (FR-GATE-002) ---------------------------------------------- #
# The ONLY source of what a decision sends; docs/design/wire-text.json pins every ``WIRE_*`` constant.

WIRE_APPROVE = "Approve"                                   # stage gate
WIRE_REQUEST_CHANGES_PREFIX = "Request Changes: "          # stage gate AND plan approval (capital C)
WIRE_ACCEPT_AS_IS = "Accept as-is"                         # revision-loop escape hatch only
WIRE_APPROVE_PLAN = "Approve Plan"                         # code-generation plan-approval checkpoint
WIRE_LOOKS_CORRECT = "Looks correct"                       # consolidated summary confirmation
WIRE_SUMMARY_REQUEST_CHANGES_PREFIX = "Request changes: "  # summary confirmation (lower-case c — not a typo)
WIRE_ANSWER_LINE = "Q{index}: {answer}"                    # one line per question in a grouped submit
WIRE_ANSWER_JOINER = "\n"
WIRE_GROUPED_ANSWER_SUFFIX = (
    "\n\nStudio grouped-answer delivery: this is one human reply. Apply each Q<n> answer to its "
    "matching [Answer] tag. Record all Q<n> answers together in ONE aidlc-log.ts answer --details "
    "call, preserving their text; do not record separate answer receipts for each question. "
    "Then present the next human checkpoint and wait."
)
WIRE_MULTI_SELECT_JOINER = ", "                            # multi-select labels inside one answer
# Several audit-log questions asked in one turn (A37): one reply, one receipt.
WIRE_AUDIT_GROUPED_ANSWER_SUFFIX = (
    "\n\nStudio grouped-answer delivery: this is one human reply to the questions you asked together, "
    "in the order you asked them. Record all Q<n> answers together in ONE aidlc-log.ts answer --details "
    "call, preserving their text. Then continue the stage."
)
AUDIT_NONE_LABEL = "None of these"       # Studio's own choice on a multi-select audit question (A37)
AUDIT_OTHER_LABEL = "Other"              # free-text escape on an audit question asked with others (A37)
LEARNINGS_ADD_PROMPT = "Learnings: anything to add?"   # the engine's mandated single-choice prompt
WIRE_RUN = "/aidlc"
WIRE_RESUME = "/aidlc --resume"
WIRE_SCOPE_PREFIX = "/aidlc --scope "
WIRE_PREPARE_COMMIT = "Please prepare a commit for the current AI-DLC changes. Do not push."
WIRE_ACCEPT_ASSUMPTIONS = "A. Accept assumptions"
WIRE_CONVERT_ASSUMPTIONS = "B. Convert to follow-up questions"
# There is deliberately NO wire text for picking the active intent: ``/aidlc intent <name>`` as a prompt would mint a
# human turn for a navigation step, so the cursor switch is an admin-lane engine verb.

#: ``accept_as_is`` is a conductor option only after three rejections, so Studio offers it from the fourth attempt.
ACCEPT_AS_IS_MIN_ATTEMPT = 4

# ---- host integration ----------------------------------------------------- #

#: Must equal ``app.json`` ``permissions.events``.
HOST_EVENT_ACTION = f"{APP_NAME}:action"
HOST_EVENT_REPO = f"{APP_NAME}:repo"
HOST_EVENT_TRANSACTION = f"{APP_NAME}:transaction"

#: Host chat endpoints the UI calls with the owner's own cookie (``permissions.api``); the backend never calls them,
#: it only records what the UI reports back.
HOST_CHAT_SEND_PATH = "/api/chat?ws=1"
HOST_CHAT_SLOTS_PATH = "/api/chat/slots"

#: Slot name Studio asks the UI to create for an intent's canonical session.
SLOT_KEY_TEMPLATE = APP_NAME + "-{repo_id}-{intent_dir}"

DEEP_LINK_BASE = f"/apps/{APP_NAME}"
#: File-backed grouped answers were round-tripped on KiroCrew 0.6.0-insider.6 / AI-DLC 2.7.1 (2026-09-11: four Answer
#: tags, one audit receipt, one host message; docs/verification/2026-09-11-grouped-answers/README.md). A fact, not a
#: preference: native blocking widgets still use the canonical conversation.
GROUPED_ANSWERS_VERIFIED = True

#: Studio-owned storage names inside ``ctx.data_dir``.
DB_FILENAME = "studio.sqlite3"
STAGING_DIRNAME = "staging"
BACKUP_DIRNAME = "backups"
FAILED_DIRNAME = "failed"

# ---- SQLite (storage.py) -------------------------------------------------- #

#: ``PRAGMA busy_timeout`` and the connect timeout. Studio has one connection, so this matters only when another
#: process (a gateway still shutting down, an operator's ``sqlite3``) holds the write lock: waiting five seconds is
#: right, failing the user's decision at once is not.
SQLITE_BUSY_TIMEOUT_MS = 5000

#: Statuses under which ``actions.dedupe_key`` is unique (the ``actions_live_dedupe`` index ``storage.SCHEMA_V3``
#: builds, and the set ``upsert_derived`` treats as live): ``LIVE_ACTION_STATUS`` minus ``Failed``, because a failed
#: card stays visible in the queue but must not block a retry of the same boundary (review P06/R12).
DEDUPE_LIVE_ACTION_STATUS = (
    "Queued",
    "Delivering",
    "Delivered",
    "ReconciliationRequired",
    "NotDelivered",
)

# ---- injectable protocols (§0.2): structural, so a test passes a fake ----- #


class Clock(Protocol):
    """Time as a dependency: every deadline and retention cut-off compares against it, so a test moves time by hand."""

    def now(self) -> float: ...

    def iso(self) -> str: ...

    def monotonic(self) -> float: ...


class IdFactory(Protocol):
    """Mints the prefixed ids of §0.1 (``new("a") -> "a_<16 hex>"``); a deterministic one makes ids assertable."""

    def new(self, prefix: str) -> str: ...


#: The space AI-DLC creates and falls back to when ``aidlc/active-space`` is missing (04 §2).
DEFAULT_SPACE = "default"
#: Separates the halves of an ``intent_key``; outside ``INTENT_DIR_RE``/``SPACE_RE``, so one path parameter can carry
#: both unambiguously (C02).
INTENT_KEY_SEPARATOR = "~"


def intent_key_for(space: str, intent_dir: str) -> str:
    """``intent_dir`` in the default space, ``space~intent_dir`` elsewhere (§0.1, C02). Shared because the projection
    and ``Storage`` both mint it, and two spellings would stop a non-default space's activity matching its cards."""
    return intent_dir if space == DEFAULT_SPACE else f"{space}{INTENT_KEY_SEPARATOR}{intent_dir}"


def attr(obj: object, name: str, default: object = None) -> Any:
    """Read ``name`` off a dataclass, a mapping, a ``SimpleNamespace`` or a stub.

    Modules handed another module's records read them structurally, so they need no import edge to each other and a
    test can pass a three-field stub; the same records arrive as SQLite rows on one path and dataclasses on another. A
    missing field is ``default``, so one field a newer host lacks degrades one value instead of a whole scan.
    """
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


#: Wire artifact names whose file stem differs (mirrors ``aidlc-artifact-vocabulary.ts``).
ARTIFACT_FILENAME_ALIASES = {
    "build-test-results": "test-results.md",
    "load-test-results": "test-results.md",
}


def artifact_output_location(node: object) -> str | None:
    """The output layouts Studio understands from the engine's own stage declaration; ``None`` asserts nothing.

    Initialization outputs describe workspace structure or state, not files in a stage record. Practices drafts stay
    in their stage directory even after their contents are promoted to shared memory.
    """
    outputs = attr(node, "outputs")
    if not isinstance(outputs, str):
        return None
    if outputs.startswith("aidlc/spaces/<active-space>/codekb/<repo>/"):
        return "codekb"
    if "this stage's record dir" in outputs or "this stage's per-unit record dir" in outputs:
        return "stage"
    if attr(node, "slug") == "practices-discovery" and outputs.startswith(
        "team-practices.md, discovered-rules.md, evidence.md, practices-discovery-timestamp.md"
    ):
        return "stage"
    return None


# ---- timestamp conversion (§0.2): one implementation for every module ----- #

#: The §0.2 stamp for Studio's own rows; AI-DLC's audit files use it too, so the two sort together.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def iso_from_epoch(epoch: float) -> str:
    """Format a POSIX timestamp as the §0.2 wire stamp (UTC, second precision, ``Z``)."""
    return time.strftime(TIMESTAMP_FORMAT, time.gmtime(epoch))


def epoch_from_iso(value: object) -> float | None:
    """Parse a §0.2 wire stamp back to POSIX seconds; ``None`` for anything unparseable.

    ``calendar.timegm``, not ``time.mktime(...) - time.timezone``: ``mktime`` reads the struct as local time and
    guesses DST, which put a stage's elapsed time an hour off for half the year. Unparseable is ``None`` because every
    caller derives a display value or an age from a file AI-DLC wrote, and one bad stamp must not cost the whole read.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return float(calendar.timegm(time.strptime(value, TIMESTAMP_FORMAT)))
    except (ValueError, TypeError):
        return None

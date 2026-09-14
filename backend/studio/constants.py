"""Every limit, deadline, grammar, enum and protocol constant AI-DLC Studio depends on.

One module so a reviewer can audit the whole envelope in one read, and so no other module invents a
cap or a wire string of its own. Nothing here does I/O or imports another Studio module.

Three groups deserve care:

* **Grammar** — the regexes that parse AI-DLC's own files. They are deliberately tolerant of version
  drift (State Version 7 and 8, 32- and 33-stage graphs, three em-dash spellings) because the engine
  rewrites its own templates between releases and a strict parser would confidently misread a newer
  file. Every pattern here was checked against the real fixtures under ``tests/fixtures/``.
* **Wire text** — the exact bytes a human decision sends into the AI-DLC conversation. The engine
  supplies no response token, so these are protocol constants; casing is load-bearing (a gate says
  ``Request Changes``, the summary checkpoint says ``Request changes``). ``ui/src/lib/wire.ts``
  mirrors them and a test pins the two copies equal.
* **Env scrubbing** — the guard rails around every subprocess Studio starts. AI-DLC reads bypass
  variables from the environment, so Studio must prove they are absent rather than merely not set
  them.
"""

from __future__ import annotations

import calendar
import re
import time
from typing import Any, Mapping, Protocol

# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #

APP_NAME = "aidlc-studio"
#: Must equal ``app.json`` ``version`` (pinned by tests/test_manifest.py).
APP_VERSION = "1.0.0"
#: Must equal ``app.json`` ``minKiroCrewVersion``. 0.3.0 is enough because Studio registers its own
#: module namespace (see ``backend/routes.py``) instead of relying on the 0.5.0 loader, and every
#: other host primitive it uses exists in 0.3.0.
MIN_KIROCREW_VERSION = "0.3.0"
#: Must equal ``payload/manifest.json`` ``engineVersion``.
BUNDLED_ENGINE_VERSION = "2.7.1"
#: What the bundled engine writes. Code and tests read these from ``payload/manifest.json`` rather than
#: comparing against the literals below; the literals exist so a mismatch is a visible diff here too.
BUNDLED_STATE_VERSION = 8
BUNDLED_STAGE_COUNT = 33

#: State-file versions the PARSERS accept. Wider than what the bundled engine writes: a registered
#: repo may run an older AI-DLC, and refusing to read it would hide work the user can still see on disk.
SUPPORTED_STATE_VERSIONS = (7, 8)
#: Bytes of entropy in ``Services.boot_id``, which is stamped on every ``Delivering`` row. It is the
#: only way to tell "this process sent it" from "the process before the restart did", and that
#: distinction is what makes ``NotDelivered`` unreachable after a crash (C24/review R02).
BOOT_ID_BYTES = 8

#: Stage-graph sizes seen in the wild (32 = ``application-design``, 33 = ``domain-design`` +
#: ``contract-design``). Used for sanity reporting only; the graph on disk is always the authority.
SUPPORTED_STAGE_COUNTS = (32, 33)

# --------------------------------------------------------------------------- #
# bounded reads (bytes)
# --------------------------------------------------------------------------- #

MAX_STATE_BYTES = 512 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_RENDER_BYTES = 1024 * 1024
MAX_AUDIT_TAIL_BYTES = 256 * 1024
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
#: How much of a subagent's ``result.txt`` Studio will read back when the host's completion event
#: dropped content. The host caps its own event copy at ``agent.completion_keep_chars`` (3 000 by
#: default), which a real Advisor draft exceeds every time, and keeps the untruncated text on disk;
#: without this recovery the Advisor fails on every host that has not been reconfigured. The cap is
#: far above a real draft (~10 KB observed) and far below a memory incident. Measured in bytes because
#: the recovery read is byte-capped (``security.bounded_read``), the way the transport measures it.
MAX_SUBAGENT_RESULT_BYTES = 200_000

# --------------------------------------------------------------------------- #
# deadlines (seconds)
# --------------------------------------------------------------------------- #

REPO_SCAN_DEADLINE_SECS = 10.0
SUBPROCESS_TIMEOUT_SECS = 60.0
ENGINE_READ_TIMEOUT_SECS = 20.0
GIT_TIMEOUT_SECS = 15.0
RECONCILE_INTERVAL_SECS = 5.0
#: Repository scans run on a small private pool with this many concurrent slots, so a slow filesystem
#: cannot starve the default executor that every other ``to_thread`` call shares.
SCAN_CONCURRENCY = 2
#: Size of that pool (``Services.scan_pool``). Two threads and two semaphore slots on purpose: a third
#: thread could only wait on the semaphore.
SCAN_POOL_WORKERS = 2
#: Wall budget one tick may spend scanning. Repositories with no open action are round-robined across
#: ticks so a 64-repo install cannot make the heartbeat loop late (review R11).
SCAN_TICK_BUDGET_SECS = 4.0
LEASE_HEARTBEAT_SECS = 15.0
#: A lease whose heartbeat is older than this is *stale*, which permits a reclaim ATTEMPT. Elapsed
#: time alone never authorises reclaim: the owner must also be provably not executing (FR-SES-007).
LEASE_STALE_SECS = 120.0
#: How long the UI has to report the outcome of its host submission before the action is treated as
#: uncertain and the reconciler starts looking for evidence instead.
DELIVERY_ACK_DEADLINE_SECS = 90
#: Upper bound for "the turn should have finished by now"; the effective value is
#: ``min(agent.chat_turn_timeout_secs, this)``.
PROCESSING_DEADLINE_CAP_SECS = 7200
#: A message the host accepted while a turn was already running has not started its own turn yet, so it
#: may wait this multiple of the turn timeout in ``Delivered`` before it counts as uncertain.
DELIVERED_QUEUED_WAIT_FACTOR = 2
SSE_HEARTBEAT_SECS = 15.0
STABLE_READ_RETRY_SECS = 0.05
#: An unreadable state file is transient often enough (a write in flight) that it only becomes a blocking
#: finding once it has stayed unreadable this long.
STATE_UNREADABLE_GRACE_SECS = 60
#: How long an intent directory must stay missing from the scan before the cards derived for it are
#: retired. A directory that vanishes for one tick is a branch switch, a rename in progress or an
#: unreadable listing; cancelling its Queued cards on that evidence would dismiss a gate the human is
#: about to answer. After the grace, a card for a boundary that is no longer on disk is a card for
#: nothing (FR-ACT-007), and its Activity resolution says ``intent_missing``.
INTENT_MISSING_GRACE_SECS = 60
#: ``on_startup`` must return well inside the host's 30 s budget or the gateway detaches the task and
#: refuses later lifecycle operations for this app.
HOST_STARTUP_HOOK_BUDGET_SECS = 10.0
ADVISOR_TIMEOUT_SECS = 300.0
#: The ``name`` declared inside ``agents/advisor.json``. ``SpawnSDK.run`` matches on the declared name
#: among the files this app owns (`kc:apps/spawn_sdk.py:173-181`), so the string in the JSON, the string
#: passed to ``spawn.run`` and the string the uniqueness check looks for must be one constant: two
#: spellings would make the advisor start refusing after a rename with nothing to point at (C25).
ADVISOR_AGENT_NAME = "aidlc-studio-advisor"

#: How many *automatic* advisor drafts may be queued or running at once across the whole installation
#: (FR-ADV-010). The pre-draft opt-in turns one open card into one subagent, and a repository can easily
#: hold twenty open cards after a night of engine work; without a ceiling the first tick after the opt-in
#: would spawn twenty models at once. Two is the same number the execution lane uses for concurrency:
#: enough that a human working through a queue usually finds the next card already drafted, small enough
#: that a mistake costs two spawns rather than a bill. A draft a human clicked for is never counted or
#: blocked by this — an explicit request must always be honoured.
ADVISOR_AUTO_DRAFT_MAX_INFLIGHT = 2
#: How many automatic drafts one reconciler tick may start. One, so the ramp is visible in Activity and a
#: misconfiguration is noticed after one spawn instead of after the cap. At a five-second tick the cap
#: still fills in ten seconds.
ADVISOR_AUTO_DRAFT_PER_TICK = 1
#: How many open drafts one tick settles. Settling on the tick is what makes a pre-draft worth anything:
#: the host keeps a finished subagent's whole answer for ``agent.subagent_result_ttl_secs`` (an hour by
#: default) and then prunes it, and a pre-draft exists precisely because no browser is open to poll it.
#: The bound is here because a human may click far more drafts than the auto cap allows; polling a
#: still-running subagent is one in-memory read, so eight per tick is generous.
ADVISOR_SETTLE_PER_TICK = 8

#: Every advisor draft kind (§1.18). Lives here, not in advisor.py, so scripts/gen_ui_sources.py can
#: generate the UI's `AdvisorKind` union and the i18n parity test can demand a label for each.
ADVISOR_KINDS: tuple[str, ...] = (
    "gate_analysis",
    "question_draft",
    "question_explain",
    "request_changes_draft",
    "diagnose",
    "plan_draft",
)
#: Kinds that have no card: a plan draft is asked for in the new-intent wizard before any intent exists.
#: Never a key or value of ADVISOR_KINDS_BY_TYPE / ADVISOR_AUTO_KIND_BY_TYPE (§1.18a).
ADVISOR_CARDLESS_KINDS: tuple[str, ...] = ("plan_draft",)
#: `advisor_drafts.action_id` is NOT NULL (§1.4) and no column may be added for advisor features (§1.4 note);
#: a card-less draft stores `f"{ADVISOR_PLAN_ACTION_PREFIX}{repo_id}"` there. It can never equal a card id
#: (those are `a_<hex>`), so `_auto_redundant`, `list_for_action` and `ActionCard.advisor` never see it.
ADVISOR_PLAN_ACTION_PREFIX = "plan:"
#: The objective and the context a plan draft quotes, each, in characters (§1.18a). Four thousand is
#: room for a page of prose; anything longer is a document the human should attach as context later.
MAX_PLAN_OBJECTIVE_CHARS = 4000
#: How many rows of ``intents.json`` a plan package lists as "what this repository already has".
MAX_PLAN_EXISTING_INTENTS = 10
#: Top-level names in ``WorkspaceSignals`` — enough to see a project's shape, too few to be a listing.
MAX_WORKSPACE_ENTRIES = 60
#: Characters of the README the plan package quotes (FR-ADV-003: bounded, one file, names otherwise).
MAX_README_CHARS = 3000

# --------------------------------------------------------------------------- #
# retention and ring buffers
# --------------------------------------------------------------------------- #

EVENTS_RING_SIZE = 10_000
ACTIVITY_DEFAULT_LIMIT = 100
ACTIVITY_MAX_LIMIT = 500
HUMAN_TEXT_RETENTION_DAYS_DEFAULT = 30
HUMAN_TEXT_RETENTION_DAYS_MAX = 30
ADVISOR_DRAFT_TTL_SECS = 24 * 3600
#: Below this many comparable local samples, an estimate is presented as a rule-based band and
#: labelled as such — never as calibrated history (FR-EST/§17).
CALIBRATION_MIN_SAMPLES = 10

# --------------------------------------------------------------------------- #
# circuit breaker
# --------------------------------------------------------------------------- #

BREAKER_TRANSIENT_THRESHOLD = 3
TRANSIENT_FINGERPRINT_CLASSES = ("transport", "rate_limit", "acp_timeout")
DETERMINISTIC_FINGERPRINT_CLASSES = (
    "validation",
    "permission",
    "dependency",
    "guard",
    "human_marker_movement",
    "config",
)

# --------------------------------------------------------------------------- #
# canonical enums (PRD §11 — verbatim wire strings, never localized)
# --------------------------------------------------------------------------- #

ACTION_STATUS = (
    "Draft",
    "Queued",
    "Delivering",
    "Delivered",
    "Processing",
    "StateChanged",
    "ResolvedNoTransition",
    "NotDelivered",
    "DeliveryUncertain",
    "ReconciliationRequired",
    "Failed",
    "Cancelled",
)
#: Statuses that keep a card in the Action Center queue.
LIVE_ACTION_STATUS = (
    "Queued",
    "Delivering",
    "Delivered",
    "Processing",
    "DeliveryUncertain",
    "ReconciliationRequired",
    "NotDelivered",
    "Failed",
)
TERMINAL_ACTION_STATUS = ("StateChanged", "ResolvedNoTransition", "Cancelled")

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
#: Every consequence variant each card type may emit, so the i18n gate can prove that no card can
#: reach a person without a real "what happens if you act" sentence.
#:
#: Why declared here rather than derived from ``projection.SEED_CONSEQUENCE``: the seed table holds only
#: the DEFAULT variant, and two sites override it at runtime (``gate`` becomes ``final_stage`` on the
#: last stage, ``run`` becomes ``one_turn``). A gate that read the seeds alone would pass while the
#: override rendered a missing key -- which is exactly the defect this table exists to prevent.
#: ``tests/test_constants.py`` checks the seeds and the overrides against it.
CARD_CONSEQUENCE_VARIANTS: dict[str, tuple[str, ...]] = {
    "gate": ("approve_unlocks", "final_stage"),
    "question": ("advances_turn",),
    "recovery": ("blocked_until_agree",),
    "delivery_uncertain": ("no_replay",),
    "failure": ("no_dispatch",),
    "install_conflict": ("nothing_written",),
    "run": ("one_turn",),
}
SEVERITY = ("critical", "blocking", "attention", "info")
FINDING_SEVERITY = ("blocking", "warn", "info")
SOURCE = ("studio", "aidlc", "kirocrew", "git", "slack")
#: ``host_control`` is ``force_stop`` alone (§0.5, review P16): it reaches the host through the chat
#: stop endpoint rather than through a prompt, so it is neither ``human_lane`` (no prompt, no execution
#: lease, no cursor switch) nor ``studio_only`` (it does change the host's state).
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
#: Every decision a card can offer. Three lanes, distinguished because they reach AI-DLC differently:
#: HUMAN_LANE decisions become a prompt in the intent's own conversation, HOST_CONTROL uses a host
#: control endpoint (force stop), and STUDIO_ONLY decisions change only Studio's own records.
DECISION_KIND = (
    "approve",
    "request_changes",
    "accept_as_is",
    "approve_plan",
    "request_plan_changes",
    "confirm_summary",
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

#: Action type → every decision that type may carry (§1.12). One table because two modules read it from
#: opposite ends: ``projection`` offers a subset of a row on a derived card, and the action broker
#: validates a submitted decision against the same row. Two tables would drift, and the drift would
#: show up as a card offering a decision the broker answers ``invalid_decision`` to.
#: A row lists what is *legal*; whether a decision is *offered* can be narrower — ``accept_as_is``
#: only from ``ACCEPT_AS_IS_MIN_ATTEMPT`` on (review P24), question decisions per pending checkpoint.
DECISIONS: dict[str, tuple[str, ...]] = {
    "gate": ("approve", "request_changes", "accept_as_is"),
    "question": ("answers", "confirm_summary", "approve_plan", "request_plan_changes"),
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

#: Legal action-state transitions (PRD §11.1). Data, not code, so the state machine and its test read
#: the same table.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "Draft": ("Queued", "Cancelled"),
    "Queued": ("Delivering", "Cancelled"),
    "Delivering": ("Delivered", "NotDelivered", "DeliveryUncertain"),
    "Delivered": ("Processing", "DeliveryUncertain"),
    "Processing": ("StateChanged", "ResolvedNoTransition", "DeliveryUncertain", "Failed"),
    "DeliveryUncertain": ("ReconciliationRequired",),
    "ReconciliationRequired": ("StateChanged", "ResolvedNoTransition", "NotDelivered", "Failed"),
    "NotDelivered": ("Queued", "Cancelled"),
    "StateChanged": (),
    "ResolvedNoTransition": (),
    "Failed": (),
    "Cancelled": (),
}

#: Legal intent operational transitions (PRD §11.2). Studio's projection is derived from disk, so this
#: table is used to VALIDATE a derived change, never to drive one.
INTENT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "Idle": ("Queued", "Archived"),
    "Queued": ("Running", "Paused"),
    "Running": (
        "WaitingForYou",
        "Paused",
        "Parked",
        "Interrupted",
        "RetryEligible",
        "Failed",
        "Completed",
    ),
    "WaitingForYou": ("Queued", "Paused", "Parked"),
    "Paused": ("Queued", "Parked", "Archived"),
    "Parked": ("Queued", "Archived"),
    "RetryEligible": ("Queued", "CircuitOpen", "Paused"),
    "Interrupted": ("ReconciliationRequired",),
    "ReconciliationRequired": ("Queued", "Paused", "Failed"),
    "CircuitOpen": ("RetryEligible", "Paused"),
    "Completed": ("Archived",),
    "Failed": ("Archived",),
    "Archived": ("Completed", "Paused", "Parked", "Failed"),
}

# --------------------------------------------------------------------------- #
# AI-DLC grammar
# --------------------------------------------------------------------------- #

#: ``- [x] build-and-test — EXECUTE``. The separator is an em dash in current writers; en dash and
#: ``--`` are accepted because older files used them, and the mode suffix is optional so a bare stage
#: row still parses.
CHECKBOX_RE = re.compile(
    r"^- \[([ xXsSrR?\-])\] (\S+)(?:\s*(?:—|–|--)\s*(.*))?$", re.MULTILINE
)
#: ``- **Current Stage**: build-and-test``. Trailing whitespace is common (2.6.2 writes
#: ``- **Review Override**: `` with a trailing space and no value), so the value group may be empty.
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
QUESTION_HEADING_RE = re.compile(r"^## Q(\d+)[.:]?\s*(.*)$", re.MULTILINE)
#: ``- A. Some option`` / ``A. Some option`` / ``- X. Other (please specify)``.
OPTION_LINE_RE = re.compile(r"^(?:- )?([A-Z])\.[ \t]+(.*)$", re.MULTILINE)
OTHER_OPTION_LETTER = "X"
QUESTION_MODE_RE = re.compile(r"^\*\*Mode:\*\*\s*(.+?)\s*$", re.MULTILINE)
#: Bullet lines immediately after an ``[Answer]:`` tag continue that answer (real files do this).
ANSWER_CONTINUATION_RE = re.compile(r"^- (.*)$")
#: The three checkpoint headings (§1.1). Named because the reader maps a heading to a checkpoint kind and
#: the pattern below matches it: one literal, two readers.
SUMMARY_CONFIRMATION_HEADING = "Consolidated Summary Confirmation"
PLAN_APPROVAL_HEADING = "Plan Approval"
# The Markdown heading and the engine audit checkpoint have distinct names.
PLAN_APPROVAL_AUDIT_CHECKPOINT = "Code Generation Plan Approval"
POST_APPROVAL_AMENDMENT_HEADING = "Post-approval Amendment"
#: Blocks inside a questions file that are checkpoints rather than questions; each has its own wire text.
CHECKPOINT_HEADING_RE = re.compile(
    "^## ("
    + "|".join(
        re.escape(h)
        for h in (PLAN_APPROVAL_HEADING, SUMMARY_CONFIRMATION_HEADING, POST_APPROVAL_AMENDMENT_HEADING)
    )
    + r")\s*$",
    re.MULTILINE,
)
CHECKPOINT_OPTION_RE = re.compile(
    r"^- (Approve Plan|Request Changes|Looks correct|Request changes)\s*$", re.MULTILINE
)

INTENT_DIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SPACE_RE = INTENT_DIR_RE
UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
STAGE_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")
ENGINE_VERSION_RE = re.compile(r'AIDLC_VERSION\s*=\s*"([0-9][0-9.]*?)"')
ENGINE_STATE_VERSION_RE = re.compile(r'CURRENT_STATE_VERSION\s*=\s*"(\d+)"')
STATE_VERSION_FIELD = "State Version"
#: ``- **State Version**: 8`` in a state file *or* in an engine's state template — the only place a
#: pre-2.5 build records the version it writes. Broader than §1.1's spelling on purpose: the bullet is
#: optional because a template carries the line without it, and the field name is interpolated rather
#: than repeated so it cannot drift from ``STATE_VERSION_FIELD``. Two modules read it (the reader for a
#: record, the registry for a template), which is why it lives here.
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

# --------------------------------------------------------------------------- #
# AI-DLC audit event classification
# --------------------------------------------------------------------------- #

GATE_EVENTS = ("STAGE_AWAITING_APPROVAL", "GATE_APPROVED", "GATE_REJECTED", "STAGE_REVISING")
#: ``PLAN_APPROVAL_RECORDED`` is 2.7.1's row for an approved Code Generation plan, where 2.6.2 wrote a
#: plain ``QUESTION_ANSWERED`` (``aidlc-log.ts``). It is a question-file checkpoint answer like the
#: other three, so it belongs here — and through ``BOUNDARY_EVENTS``/``MOVEMENT_EVENTS`` it becomes a
#: boundary move and an ``audit.`` key without a second list to remember.
QUESTION_EVENTS = (
    "DECISION_RECORDED",
    "QUESTION_ANSWERED",
    "SUMMARY_CONFIRMATION_RECORDED",
    "PLAN_APPROVAL_RECORDED",
)
REVIEW_EVENTS = ("REVIEW_REQUESTED", "REVIEW_COMPLETED")
BOUNDARY_EVENTS = GATE_EVENTS + QUESTION_EVENTS
#: What ``humanActedSinceGate`` in the engine treats as a gate resolution; the reconciler uses the same
#: set so its "did the boundary move" question has the engine's own answer. Mirrors
#: ``GATE_RESOLUTION_EVENTS`` in ``aidlc-lib.ts`` member for member (tests/test_reconciler.py reads the
#: bundled engine and compares). The one thing it does NOT mirror is the engine's extra carve-out for
#: ``AUTONOMY_MODE_SET`` with ``Mode: autonomous`` — Studio never dispatches unattended, so a row that
#: says "the human handed the wheel over" is not a resolution Studio may claim credit for.
RESOLUTION_EVENTS = (
    "GATE_APPROVED",
    "GATE_REJECTED",
    "QUESTION_ANSWERED",
    "SUMMARY_CONFIRMATION_RECORDED",
    "PLAN_APPROVAL_RECORDED",
)
#: Events that prove the workflow actually moved. Deliberately excludes the noise below: audit growth
#: alone is NOT movement (a session start or a failed command appends rows too).
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

#: Runtime files any Kiro session over the repo touches. Excluded from every "nothing changed" claim
#: (FR-ADV-007) because their movement says nothing about the workflow.
VOLATILE_RUNTIME_PATHS = (
    "aidlc/.aidlc-turn-counter",
    "aidlc/.aidlc-readonly-latch",
    "aidlc/.aidlc-human-turn",
    "aidlc/.aidlc-sessions",
    "aidlc/.aidlc-clone-id",
    # 2.7.1's per-turn unit/claim bookkeeping. Listed in the payload's own ``.gitignore``, which is the
    # engine saying the same thing: their movement is not the workflow's.
    "aidlc/.aidlc-unit-scope.json",
    "aidlc/.aidlc-unit-parked",
    "aidlc/.aidlc-unit-participant",
    "aidlc/.aidlc-claim-generations.json",
    "aidlc/.aidlc-claim-registry.json",
    "aidlc/.aidlc-unit-releases",
    "aidlc/.aidlc-unit-merges",
    ".aidlc-hooks-health",
    ".aidlc-stop-hook",
    ".aidlc-engine-touch",
    ".aidlc-sensors",
    "runtime-graph.json",
)

# --------------------------------------------------------------------------- #
# engine invocation allowlist
# --------------------------------------------------------------------------- #

#: Read-only engine verbs Studio's backend may run unattended. Anything that can transition state,
#: append a protected audit event, or repair the workflow is absent on purpose and stays absent:
#: ``next``, ``report``, ``park``, every ``aidlc-state.ts`` mutator, ``aidlc-audit.ts append*``,
#: ``aidlc-jump.ts execute``. ``doctor`` is NOT here — it appends audit rows, so it is an explicit
#: user action routed through the admin allowlist below.
#: Verb names verified against the bundled 2.7.1 ``aidlc-utility.ts`` dispatch (every verb below is still
#: dispatched, with the same flags). Older engines differ:
#: ``intent-create`` was called ``intent-birth`` before 2.6, and ``config-get``/``config-list``/
#: ``stage-table``/``codekb-scope-diff`` did not exist. ``EngineRunner`` probes the installed version and
#: reports ``engine_verb_unsupported`` rather than guessing.
#: One flag rule the engine enforces before it dispatches ``intent-create`` (unchanged since 2.6.2):
#: ``--scope``, ``--arguments``, ``--label``, ``--depth``, ``--test-strategy``, ``--review``, ``--repos``
#: and ``--project-dir`` are refused with "requires a nonblank value" when present but blank, so an
#: optional flag Studio has no value for must be left OUT of the argv, never passed empty.
ENGINE_READ_VERBS: dict[str, tuple[str, ...]] = {
    "aidlc-utility.ts": (
        "version",
        "status",
        "intent",          # bare = list; with --json = machine-readable cursor + registry
        "space",
        "detect",
        "detect-scope",
        "resolve-env-scope",
        "scope-table",
        "stage-table",
        "codekb-path",
        "codekb-scope-diff",
        "config-get",
        "config-list",
        "plugin-list",
    ),
    "aidlc-state.ts": ("get", "lookup", "resume", "count"),
    "aidlc-runtime.ts": ("summary", "read"),
}
#: Engine verbs Studio may run while holding a RepoAdminLease, on explicit user action. Each mutates
#: something the engine owns, and each is the engine's own supported way to do it.
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
#: Verb spelling per engine generation, so one code path serves both. ``EngineRunner`` resolves the
#: alias from the installed ``AIDLC_VERSION`` before building an argv.
ENGINE_VERB_ALIASES: dict[str, tuple[str, ...]] = {
    # canonical name -> spellings to try, newest first
    "intent-create": ("intent-create", "intent-birth"),
}
#: ``aidlc-utility.ts upgrade`` exists in 2.7.1 but only prints "unavailable" and exits 1, so Studio's
#: own transactional installer owns upgrades (PRD FR-INST-015). Listed here so nobody re-adds it.
ENGINE_UNAVAILABLE_VERBS = ("upgrade",)
#: Tools and verbs that must never appear in an argv Studio builds. Enforced in
#: ``security.assert_engine_argv_allowed``, which ``EngineRunner`` calls for every invocation
#: (engine.py:632). Deliberately narrower than ``engine.ENGINE_DENY``: that list names every
#: engine-owned tool and per-verb transition and is the one to extend when the engine grows one,
#: while these two are the floor the argv builder itself trips on.
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

# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #

#: Deny list first: a static test greps the backend for these, and ``assert_git_argv_readonly``
#: refuses them at runtime even if a future caller forgets.
GIT_WRITE_VERBS = frozenset(
    "add commit push pull merge rebase checkout switch reset restore stash tag branch cherry-pick "
    "revert am apply clean rm mv fetch remote submodule worktree gc prune reflog update-ref "
    "symbolic-ref filter-branch notes replace bisect init clone".split()
)
#: The only verbs Studio is allowed to run. An unlisted verb is refused even if it looks harmless.
GIT_READ_VERBS = frozenset(
    "rev-parse status log diff show ls-files name-rev describe cat-file rev-list for-each-ref "
    "merge-base".split()
)
#: Global options that can redirect git at another repository, run an arbitrary binary, or write a file.
#: Refused wherever they appear, so ``-C`` is never how Studio selects a repository (``cwd`` is).
GIT_FORBIDDEN_TOKENS = frozenset(
    {"-c", "-C", "--git-dir", "--work-tree", "--exec-path", "--output", "--namespace",
     "--config-env", "--super-prefix"}
)
GIT_FORBIDDEN_PREFIXES = (
    "--output=", "--exec-path=", "--git-dir=", "--work-tree=", "--namespace=", "--config-env=",
)

# --------------------------------------------------------------------------- #
# subprocess environment
# --------------------------------------------------------------------------- #

ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
#: No variable whose name starts with one of these may reach a child: AI-DLC and the harnesses read
#: their own configuration from the environment, and inheriting the gateway's would silently redirect
#: the engine at another project or another framework tree.
ENV_FORBIDDEN_PREFIXES = ("AIDLC_", "CLAUDE_", "AWS_AIDLC_", "KIRO", "KIROCREW_")
#: Bypass switches. Studio asserts they are absent rather than merely not setting them, so an operator
#: who exported one in the gateway's shell cannot silently disable AI-DLC's anti-forgery guard through
#: a Studio-started process.
ENV_MUST_BE_ABSENT = (
    "AIDLC_SKIP_HUMAN_PRESENCE_GUARD",
    "AIDLC_SKIP_ARTIFACT_GUARD",
    "CLAUDE_PROJECT_DIR",
    "AIDLC_HARNESS_DIR",
    "AIDLC_RULES_SUBDIR",
    "AIDLC_STAGE_GRAPH",
    "AIDLC_SCOPE_GRID",
)

# --------------------------------------------------------------------------- #
# wire text (FR-GATE-002) — the ONLY source of what a decision sends
# --------------------------------------------------------------------------- #

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
WIRE_RUN = "/aidlc"
WIRE_RESUME = "/aidlc --resume"
WIRE_SCOPE_PREFIX = "/aidlc --scope "
WIRE_PREPARE_COMMIT = "Please prepare a commit for the current AI-DLC changes. Do not push."

#: There is deliberately NO wire text for picking the active intent. Sending `/aidlc intent <name>` as a
#: prompt would mint a human turn for a navigation step; the cursor switch is an admin-lane engine verb.

#: The pinned source both this module and ``ui/src/lib/wire.ts`` are tested against. Keeping the file
#: outside the code means a casing change cannot pass by editing only one language's copy.
WIRE_TEXT_SOURCE = "docs/design/wire-text.json"
WIRE_TEXT = {
    "WIRE_APPROVE": WIRE_APPROVE,
    "WIRE_REQUEST_CHANGES_PREFIX": WIRE_REQUEST_CHANGES_PREFIX,
    "WIRE_ACCEPT_AS_IS": WIRE_ACCEPT_AS_IS,
    "WIRE_APPROVE_PLAN": WIRE_APPROVE_PLAN,
    "WIRE_LOOKS_CORRECT": WIRE_LOOKS_CORRECT,
    "WIRE_SUMMARY_REQUEST_CHANGES_PREFIX": WIRE_SUMMARY_REQUEST_CHANGES_PREFIX,
    "WIRE_ANSWER_LINE": WIRE_ANSWER_LINE,
    "WIRE_ANSWER_JOINER": WIRE_ANSWER_JOINER,
    "WIRE_GROUPED_ANSWER_SUFFIX": WIRE_GROUPED_ANSWER_SUFFIX,
    "WIRE_MULTI_SELECT_JOINER": WIRE_MULTI_SELECT_JOINER,
    "WIRE_RUN": WIRE_RUN,
    "WIRE_RESUME": WIRE_RESUME,
    "WIRE_SCOPE_PREFIX": WIRE_SCOPE_PREFIX,
    "WIRE_PREPARE_COMMIT": WIRE_PREPARE_COMMIT,
}

#: ``accept_as_is`` is only one of the conductor's options once three rejections have happened, so Studio
#: offers it from the fourth stage attempt and refuses it before that.
ACCEPT_AS_IS_MIN_ATTEMPT = 4

# --------------------------------------------------------------------------- #
# host integration
# --------------------------------------------------------------------------- #

#: Must equal ``app.json`` ``permissions.events``.
HOST_EVENT_ACTION = "aidlc-studio:action"
HOST_EVENT_REPO = "aidlc-studio:repo"
HOST_EVENT_TRANSACTION = "aidlc-studio:transaction"
HOST_EVENTS = (HOST_EVENT_ACTION, HOST_EVENT_REPO, HOST_EVENT_TRANSACTION)

#: Host chat endpoints the UI calls with the owner's own cookie (declared in ``permissions.api``).
#: The backend never calls them; it only records what the UI reports back.
HOST_CHAT_SEND_PATH = "/api/chat?ws=1"
HOST_CHAT_SLOTS_PATH = "/api/chat/slots"

#: Slot name Studio asks the UI to create for an intent's canonical session.
SLOT_KEY_TEMPLATE = "aidlc-studio-{repo_id}-{intent_dir}"

DEEP_LINK_BASE = "/apps/aidlc-studio"
#: Machine-lane features stay off until a host-authenticated non-human submission path is proven
#: (PRD P-08 / S12). The string is a code the UI translates, not prose.
MACHINE_LANE_REASON = "s12_unproven"

#: File-backed groups were round-tripped on KiroCrew 0.6.0-insider.6 / AI-DLC 2.7.1 on 2026-09-11:
#: four matching Answer tags, one complete audit receipt, one host message, and an unanswered summary
#: checkpoint. See docs/verification/2026-09-11-grouped-answers/README.md. Native blocking widgets use
#: the canonical conversation; submit rechecks that routing under the lease. This is not a preference.
GROUPED_ANSWERS_VERIFIED = True

#: Studio-owned storage file names inside ``ctx.data_dir``.
DB_FILENAME = "studio.sqlite3"
STAGING_DIRNAME = "staging"
BACKUP_DIRNAME = "backups"
FAILED_DIRNAME = "failed"
MIGRATION_BACKUP_DIRNAME = "migration-backup"

#: The prototype this app replaces. Its registry is migrated once, then its routes are retired.
LEGACY_APP_NAME = "aidlc-console"
LEGACY_STORAGE_KEY = "repos"

# --------------------------------------------------------------------------- #
# SQLite (storage.py)
# --------------------------------------------------------------------------- #

#: ``PRAGMA busy_timeout`` and the driver's connect timeout. Studio uses one connection, so this only
#: matters when a second process (a stale gateway shutting down, an operator with ``sqlite3``) holds
#: the write lock: waiting five seconds is right, failing the user's decision immediately is not.
SQLITE_BUSY_TIMEOUT_MS = 5000
#: ``VACUUM`` rewrites the whole file, so it runs on a free-list threshold rather than on a schedule:
#: both conditions must hold (enough absolute waste, and enough of the file) before it is worth the
#: pause. An unconditional vacuum at startup would make the gateway's budget depend on history size.
VACUUM_MIN_FREELIST_PAGES = 2000
VACUUM_FREELIST_RATIO = 0.25

#: Statuses under which ``actions.dedupe_key`` must be unique — the ``actions_live_dedupe`` partial
#: index in ``storage.SCHEMA_V1`` and the set ``Projection.upsert_derived`` treats as live. It is
#: ``LIVE_ACTION_STATUS`` minus ``Failed``: a failed card stays *visible* in the queue but must not
#: block a retry of the same boundary from being inserted (review P06/R12).
DEDUPE_LIVE_ACTION_STATUS = (
    "Queued",
    "Delivering",
    "Delivered",
    "Processing",
    "DeliveryUncertain",
    "ReconciliationRequired",
    "NotDelivered",
)

# --------------------------------------------------------------------------- #
# injectable protocols (§0.2) — structural, so tests can pass a fake without importing anything
# --------------------------------------------------------------------------- #


class Clock(Protocol):
    """Time as a dependency.

    Every deadline, retention cut-off and lease heartbeat in Studio is compared against this, so a
    test can move time by hand instead of sleeping — and so no module reaches for ``time.time()``
    directly and becomes untestable.
    """

    def now(self) -> float: ...

    def iso(self) -> str: ...

    def monotonic(self) -> float: ...


class IdFactory(Protocol):
    """Mints the prefixed identifiers of §0.1 (``new("a") -> "a_<16 hex>"``).

    Injectable because a deterministic factory turns "which action did this?" assertions into exact
    string comparisons; production uses ``secrets.token_hex`` so an id is never guessable from another.
    """

    def new(self, prefix: str) -> str: ...


#: The space AI-DLC creates and falls back to when ``aidlc/active-space`` is missing (04 §2). Named here
#: because four modules compare against it and one on-disk fact must have one spelling.
DEFAULT_SPACE = "default"
#: Separates the two halves of an ``intent_key``. Outside ``INTENT_DIR_RE``/``SPACE_RE`` by construction,
#: which is what lets one path parameter carry both halves unambiguously (C02).
INTENT_KEY_SEPARATOR = "~"


def intent_key_for(space: str, intent_dir: str) -> str:
    """``intent_dir`` in the default space, ``space~intent_dir`` elsewhere (§0.1, C02).

    Here rather than in one of its callers because two modules on different layers mint it — the
    projection for every card and summary, ``Storage`` when it reads an activity row back — and a repo
    with a non-default space would silently stop correlating its activity with its cards if the two
    spellings ever diverged. ``projection.intent_key_for`` re-exports this as the §1.8 public name.
    """
    return intent_dir if space == DEFAULT_SPACE else f"{space}{INTENT_KEY_SEPARATOR}{intent_dir}"


def attr(obj: object, name: str, default: object = None) -> Any:
    """Read ``name`` off a dataclass, a mapping, a ``SimpleNamespace`` or a stub.

    The runtime counterpart of the protocols above, and for the same reason: several modules are
    handed objects another module owns (``RepoRecord``, ``BindingView``, ``SlotView``, ``ActionRecord``,
    ``Receipt``, ``InstallHealth``). Reading them structurally means those modules need no import edge
    to each other — they can be written in parallel, and a test can pass a three-field stub instead of
    constructing a peer module's dataclass. Tolerating a mapping matters too: the same records arrive
    as SQLite rows on one path and as dataclasses on another.

    A missing field yields ``default`` rather than raising, so one field a newer host (or an older
    reader) does not provide degrades one value instead of failing a whole repository scan.
    """
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


# Wire artifact names whose physical filename stem differs. These exceptions
# mirror aidlc-artifact-vocabulary.ts; extension-only names keep legacy handling.
ARTIFACT_FILENAME_ALIASES = {
    "build-test-results": "test-results.md",
    "load-test-results": "test-results.md",
}


def artifact_output_location(node: object) -> str | None:
    """The output layouts Studio understands from the engine's own stage declaration.

    An unfamiliar location makes no assertion about missing files. In particular, initialization
    outputs describe workspace structure or state, not files in a stage record. Practices drafts
    remain in their stage directory even after their contents are promoted to shared memory.
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


# --------------------------------------------------------------------------- #
# timestamp conversion (§0.2) — the one implementation every module shares
# --------------------------------------------------------------------------- #

#: The single timestamp spelling of §0.2, used for Studio's own rows and by AI-DLC's audit files, so the
#: two sort lexicographically together.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def iso_from_epoch(epoch: float) -> str:
    """Format a POSIX timestamp as the §0.2 wire stamp (UTC, second precision, ``Z``)."""
    return time.strftime(TIMESTAMP_FORMAT, time.gmtime(epoch))


def epoch_from_iso(value: object) -> float | None:
    """Parse a §0.2 wire stamp back to POSIX seconds; ``None`` for anything unparseable.

    Lives here, next to ``Clock``, because four modules need it and the obvious local spelling
    (``time.mktime(...) - time.timezone``) is wrong: ``mktime`` reads the struct as *local* time and
    guesses DST, so subtracting the standard-time offset lands an hour off for half the year in any
    zone that observes DST — which turned a stage's elapsed time into "1h 2m" two minutes after it
    started. ``calendar.timegm`` has no local-time step at all.

    Unparseable input is ``None`` rather than an exception: every caller is deriving a display value or
    an age from a file AI-DLC wrote, and one malformed timestamp must not cost the user the whole read.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return float(calendar.timegm(time.strptime(value, TIMESTAMP_FORMAT)))
    except (ValueError, TypeError):
        return None

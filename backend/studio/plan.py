"""The plan a user is about to commit to, and the only three ways Studio may change one (§1.15).

Studio never composes an AI-DLC workflow itself. The engine owns the stage graph, the scope grid and
the state file; this module *reads* them, shows the user what the composition means, and — for the
three changes AI-DLC exposes as its own verbs (``intent-create``, ``recompose``,
``scope-change``/``config-change``) — runs that verb under an admin lease and then checks on disk that
it did what it said. Nothing here writes an AI-DLC file, and nothing here sends a prompt: a created
intent is inert until a human presses Run (FR-RUN-001).

The three judgements worth reading carefully:

* **Locks.** A locked stage is one the user must not be offered a toggle for, because changing it would
  either contradict the graph (``execution: ALWAYS``), break a stage that needs it
  (``required_by:<slug>``), or rewrite history (``completed``/``current``/``at_gate``/
  ``behind_cursor``). Offering a toggle that the engine would ignore is worse than not offering it: the
  user would believe a plan that is not the plan.
* **Dependencies.** The rule is the engine's own (``aidlc-graph.ts::validateGrid`` + the ``recompose``
  guard, 2.6.2): a stage is starved when a ``consumes`` entry with ``required: true`` has no producer on
  the selected path, and only starvation that this request *introduces* is an error. The diff matters
  because the stock grids ship starved by design — ``poc`` runs ``code-generation`` while skipping
  ``units-generation``, whose ``unit-of-work`` it consumes — so validating the composition itself would
  mark every proof-of-concept plan invalid and block the wizard on a rule the engine does not hold.
  ``requires_stage`` is ordering metadata, not a constraint: locking on it would freeze the entire
  ``feature`` plan (its graph is a chain) and forbid recomposes the engine performs happily.
* **Recompose scope.** Only pending stages ahead of the cursor may be skipped or added. A completed
  stage's artifacts already exist and a stage at a gate has a human waiting on it; ``recompose`` would
  rewrite the row underneath both.

The engine is the only mutation path, and every mutating call here holds ``RepoAdminLease`` for the
repository identity, releases it in a ``finally``, and verifies the result by re-reading disk rather
than by trusting an exit code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from . import constants as C
from .aidlc_reader import match_intent_row
from .errors import StudioError
from .estimates import Estimate, EstimateService

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .activity import ActivityProjector
    from .aidlc_reader import AidlcReader, ScopeMeta, StageNode, StageRow
    from .consistency import Finding
    from .engine import EngineResult, EngineRunner
    from .events import EventLog
    from .leases import RepoScheduler
    from .projection import IntentSnapshot, IntentSummary, Projection
    from .repo_registry import RepoRecord, RepoRegistry
    from .storage import Storage


# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #

DEPTHS = ("Minimal", "Standard", "Comprehensive")
TEST_STRATEGIES = DEPTHS
REVIEW_CAPS = ("none", "advisory", "adversarial")
#: ``none < advisory < adversarial``. The effective review class of a stage is the *minimum* rank of the
#: stage's own class, the scope's cap and the request's cap: a cap may only ever reduce review work.
REVIEW_RANK = {name: rank for rank, name in enumerate(REVIEW_CAPS)}

#: ``PlanStage.lock_reason`` values (§1.15). ``required_by:<slug>`` is minted by ``lock_required_by``.
LOCK_ALWAYS = "always"
LOCK_COMPLETED = "completed"
LOCK_CURRENT = "current"
LOCK_AT_GATE = "at_gate"
LOCK_BEHIND_CURSOR = "behind_cursor"
LOCK_NOT_IN_GRAPH = "not_in_graph"
_REQUIRED_BY_PREFIX = "required_by:"


def lock_required_by(slug: str) -> str:
    return f"{_REQUIRED_BY_PREFIX}{slug}"


#: Which lock wins when several apply. A stage can be simultaneously ALWAYS, needed by a neighbour and
#: already completed; the UI shows one reason, so the order is pinned here rather than left to whichever
#: check ran last. Row-derived reasons come first: "completed" tells the user something about their
#: workflow, while "always" only restates the graph, and for a plan with no intent yet (the wizard) the
#: row reasons cannot apply at all.
_LOCK_PRECEDENCE = (LOCK_NOT_IN_GRAPH, LOCK_COMPLETED, LOCK_CURRENT, LOCK_AT_GATE, LOCK_BEHIND_CURSOR,
                    LOCK_ALWAYS, _REQUIRED_BY_PREFIX)

#: Stage states that satisfy a dependency even when the plan no longer selects the stage: its artifacts
#: are already on disk. ``skipped`` deliberately does NOT — a skipped stage produced nothing. The engine
#: makes the same exception (``recompose`` marks completed stages EXECUTE in both dependency walks).
_DEPENDENCY_SATISFIED_STATES = ("completed",)
#: Marks the engine refuses to re-shape, verbatim from the 2.6.2 ``recompose`` guard: "Only a PENDING
#: stage's plan can be re-shaped; completed/in-progress/skipped stages are frozen."
_FROZEN_STATES = ("completed", "skipped", "in_progress", "awaiting_approval", "revising")

#: ``PlanIssue.code`` values (§1.15) plus two codes for engine refusals the pinned list cannot express:
#: ``recompose_not_allowed`` (intent not Running / autonomy autonomous) and ``skeleton_anchor`` (the flip
#: would move the first EXECUTE stage of Construction, which the engine rejects because the
#: walking-skeleton gate is anchored to it). Without them ``RecomposeProposal.allowed`` would be ``False``
#: with an empty ``refusals`` list, and the UI would show a refusal it cannot explain.
PLAN_ISSUE_CODES = (
    "dependency_missing",
    "required_stage_disabled",
    "unknown_stage",
    "behind_cursor",
    "frozen_stage",
    "scope_unknown",
    "depth_invalid",
    "recompose_not_allowed",
    "skeleton_anchor",
)

#: The phase whose first selected stage the walking-skeleton gate is derived from.
_ANCHOR_PHASE = "construction"

#: ``PlanDiffEntry.reason`` values. One reason exists because one thing can move a stage: the request.
DIFF_REASON_OVERRIDE = "override"

#: A label becomes the new record's directory name, so it must be short and kebab-cased. "Two or three
#: words" is the product rule (§1.15); the engine's own grammar (``INTENT_DIR_RE``) is the outer bound
#: and is enforced by ``EngineRunner``.
LABEL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){0,2}$")
MAX_LABEL_WORDS = 3

#: ``Status`` a live intent must have for ``recompose`` to be legal, and the autonomy mode that forbids
#: it. ``autonomous`` construction means the engine is allowed to advance without a gate, so the plan may
#: move between the preview and the write (``aidlc-bolt set-autonomy --mode autonomous|gated``).
RECOMPOSE_REQUIRED_STATUS = "Running"
AUTONOMOUS_MODE = "autonomous"

#: Admin lease operation types this module takes (mirrors ``leases.ADMIN_OPERATION_TYPES``).
OP_INTENT_CREATE = "intent_create"
OP_RECOMPOSE = "recompose"
OP_SCOPE_CHANGE = "scope_change"
OP_CONFIG_CHANGE = "config_change"

#: What ``intent-create`` writes into ``--arguments``: the objective, then the optional context, split by
#: a blank line so the conductor reads two paragraphs rather than one run-on sentence.
_ARGUMENTS_JOINER = "\n\n"

#: The markers a user wraps a pasted document in. The engine treats everything OUTSIDE them as the
#: authoritative directions and refuses a paste it cannot read that way.
_DOCUMENT_OPEN = "<document>"
_DOCUMENT_CLOSE = "</document>"


def _document_marker_reason(text: str) -> tuple[str | None, int]:
    """Why the engine would refuse this ``--arguments`` paste, and where the offending marker is.

    A mirror of ``aidlc-lib.ts::authoritativeProjectDescription`` (:16301-16368) plus ``intent-create``'s
    own directions-free guard (aidlc-utility.ts:5468-5487), in the same order, so the two agree on what
    "well-formed" means. Returns ``(None, 0)`` when the paste is fine.

    It exists because the engine's version of this check runs AFTER Studio has taken the admin lease and
    started the verb: the user would get ``state_inconsistent`` and an ``ERROR_LOGGED`` audit row for
    what is a typo in a textarea. The offset is returned so the caller can say WHICH field to fix.
    """
    start = text.find(_DOCUMENT_OPEN)
    stray = text.find(_DOCUMENT_CLOSE)
    if start < 0:
        if stray >= 0:
            return f"{_DOCUMENT_CLOSE} without a matching {_DOCUMENT_OPEN}", stray
        return None, 0
    if 0 <= stray < start:
        return f"{_DOCUMENT_CLOSE} before the next {_DOCUMENT_OPEN}", stray
    end = text.find(_DOCUMENT_CLOSE, start + len(_DOCUMENT_OPEN))
    if end < 0:
        return f"{_DOCUMENT_OPEN} without a matching {_DOCUMENT_CLOSE}", start
    nested = text.find(_DOCUMENT_OPEN, start + len(_DOCUMENT_OPEN))
    if 0 <= nested < end:
        return f"nested {_DOCUMENT_OPEN} blocks", nested
    tail_at = end + len(_DOCUMENT_CLOSE)
    trailing = text[tail_at:]
    for marker in (_DOCUMENT_OPEN, _DOCUMENT_CLOSE):
        if marker in trailing:
            return f"repeated or additional {_DOCUMENT_OPEN} markers", tail_at + trailing.find(marker)
    if trailing.strip():
        return f"content after the closing {_DOCUMENT_CLOSE}", tail_at
    if not text[:start].strip():
        # The engine mints nothing from a paste with no instructions: it cannot tell what to DO with
        # the document. Reported against the field the directions would have to go in.
        return f"a pasted {_DOCUMENT_OPEN} block and no directions outside it", start
    return None, 0


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """What the user asked for. ``repo_id`` is the path parameter, not part of the JSON body (§2.3)."""

    repo_id: str
    space: str
    scope: str
    depth: str
    test_strategy: str | None
    review_cap: str | None
    project_type: str | None
    overrides: Mapping[str, bool]
    objective: str | None
    label: str | None
    context: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "space": self.space,
            "scope": self.scope,
            "depth": self.depth,
            "test_strategy": self.test_strategy,
            "review_cap": self.review_cap,
            "project_type": self.project_type,
            "overrides": {k: bool(v) for k, v in self.overrides.items()},
            "objective": self.objective,
            "label": self.label,
            "context": self.context,
        }

    @classmethod
    def from_json(cls, body: Mapping[str, Any] | None, *, repo_id: str) -> "PlanRequest":
        """Build a request from a route body, refusing shapes rather than coercing them.

        Every refusal is ``bad_body`` with the offending key in ``details``: a plan is the input to an
        engine invocation, and silently repairing "depth": 3 into "Standard" would run a workflow the
        user did not describe.
        """
        data = dict(body or {})
        overrides_raw = data.get("overrides")
        if overrides_raw is None:
            overrides_raw = {}
        if not isinstance(overrides_raw, Mapping):
            raise StudioError("bad_body", "overrides must be an object", details={"key": "overrides"})
        overrides: dict[str, bool] = {}
        for slug, value in overrides_raw.items():
            if not isinstance(slug, str) or not C.STAGE_SLUG_RE.match(slug):
                raise StudioError("bad_body", "override key is not a stage slug",
                                  details={"key": "overrides", "value": str(slug)})
            if not isinstance(value, bool):
                raise StudioError("bad_body", "override value must be a boolean",
                                  details={"key": f"overrides.{slug}"})
            overrides[slug] = value
        return cls(
            repo_id=repo_id,
            space=_opt_str(data.get("space")) or C.DEFAULT_SPACE,
            scope=_opt_str(data.get("scope")) or "",
            depth=_opt_str(data.get("depth")) or "",
            test_strategy=_opt_str(data.get("test_strategy")),
            review_cap=_opt_str(data.get("review_cap")),
            project_type=_opt_str(data.get("project_type")),
            overrides=overrides,
            objective=_opt_str(data.get("objective")),
            label=_opt_str(data.get("label")),
            context=_opt_str(data.get("context")),
        )


@dataclass(frozen=True, slots=True)
class PlanStage:
    """One row of the plan matrix.

    ``in_grid`` means "selected by the composition as it stands today" — the scope grid for a plan that
    has no intent yet, the state file's own ``EXECUTE`` selection for a live one. That is what makes
    ``in_grid == False and enabled == True`` mean "this request added it" in both modes, which is the
    only signal dependency validation and ``coverage_lost`` need.

    ``review_class`` is the EFFECTIVE class (stage class, capped by scope and request), because that is
    the number both ``ExactCounts.review_intensity`` and the estimate's review uplift must agree on.

    ``mode`` and ``summary_confirmation`` are carried for the estimator (the rule bands key off the
    engine's execution mode and the summary checkpoint costs a turn) and are deliberately NOT
    serialised: §3.1's ``PlanStage`` is the wire contract and must stay byte-comparable with it.
    """

    slug: str
    number: str
    name: str
    phase: str
    execution: str
    in_grid: bool
    enabled: bool
    locked: bool
    lock_reason: str | None
    gate: bool
    review_class: str | None
    reviewer: str | None
    per_unit: bool
    produces: tuple[str, ...]
    consumes: tuple[str, ...]
    depends_on: tuple[str, ...]
    conditional_on: str | None
    state: str | None
    mode: str = ""
    summary_confirmation: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "number": self.number,
            "name": self.name,
            "phase": self.phase,
            "execution": self.execution,
            "in_grid": self.in_grid,
            "enabled": self.enabled,
            "locked": self.locked,
            "lock_reason": self.lock_reason,
            "gate": self.gate,
            "review_class": self.review_class,
            "reviewer": self.reviewer,
            "per_unit": self.per_unit,
            "produces": list(self.produces),
            "consumes": list(self.consumes),
            "depends_on": list(self.depends_on),
            "conditional_on": self.conditional_on,
            "state": self.state,
        }


@dataclass(frozen=True, slots=True)
class ExactCounts:
    """Counts, not predictions. Kept apart from ``Estimate`` so the UI can never label one as the other."""

    stages: int
    gates: int
    artifacts: int
    review_intensity: dict[str, int]
    assumes_units: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "stages": self.stages,
            "gates": self.gates,
            "artifacts": self.artifacts,
            "review_intensity": dict(self.review_intensity),
            "assumes_units": self.assumes_units,
        }


@dataclass(frozen=True, slots=True)
class PlanIssue:
    """Why a plan cannot be committed as asked. A code and params, never English prose (C11)."""

    code: str
    slugs: tuple[str, ...]
    message_key: str
    params: dict[str, Any]

    @classmethod
    def of(cls, code: str, *slugs: str, **params: Any) -> "PlanIssue":
        if code not in PLAN_ISSUE_CODES:  # pragma: no cover - guarded by test_plan.py
            raise StudioError("internal_error", f"unknown plan issue code {code!r}", details={"code": code})
        return cls(code=code, slugs=tuple(slugs), message_key=f"plan.issue.{code}", params=dict(params))

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "slugs": list(self.slugs),
            "message_key": self.message_key,
            "params": dict(self.params),
        }


@dataclass(frozen=True, slots=True)
class PlanDiffEntry:
    """One stage whose selection this request changes, relative to the composition on disk."""

    slug: str
    from_enabled: bool
    to_enabled: bool
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "from_enabled": self.from_enabled,
            "to_enabled": self.to_enabled,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class EffectivePlan:
    """Everything the plan screen shows, computed from one read of the installed engine."""

    request: PlanRequest
    scope_meta: "ScopeMeta | None"
    stages: tuple[PlanStage, ...]
    exact: ExactCounts
    estimate: Estimate
    diff: tuple[PlanDiffEntry, ...]
    issues: tuple[PlanIssue, ...]
    valid: bool
    products: tuple[str, ...]
    graph_stage_count: int
    engine_version: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "request": self.request.to_json(),
            "scope_meta": None if self.scope_meta is None else self.scope_meta.to_json(),
            "stages": [s.to_json() for s in self.stages],
            "exact": self.exact.to_json(),
            "estimate": self.estimate.to_json(),
            "diff": [d.to_json() for d in self.diff],
            "issues": [i.to_json() for i in self.issues],
            "valid": self.valid,
            "products": list(self.products),
            "graph_stage_count": self.graph_stage_count,
            "engine_version": self.engine_version,
        }

    def stage(self, slug: str) -> PlanStage | None:
        for entry in self.stages:
            if entry.slug == slug:
                return entry
        return None

    def digest(self) -> str:
        """What ``POST /repos/{id}/intents`` confirms (``confirm_plan_digest``, §2.3).

        Over the request and the resulting selection, not over the estimate: a band that shifted because
        a calibration sample landed between preview and confirm is not a different plan, while a stage
        that changed state is.
        """
        payload = {
            "request": self.request.to_json(),
            "stages": [[s.slug, s.enabled] for s in self.stages],
            "graph_stage_count": self.graph_stage_count,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class RecomposeProposal:
    """A recompose the user can confirm, with the argv it will produce and every reason it may not run."""

    intent_key: str
    current_stage: str | None
    skip: tuple[str, ...]
    add: tuple[str, ...]
    plan: EffectivePlan
    argv_preview: tuple[str, ...]
    allowed: bool
    refusals: tuple[PlanIssue, ...]
    state_sha256: str | None = None

    def digest(self) -> str:
        """What the confirmation is bound to.

        The intent, the cursor, the two stage lists and the state bytes the preview was computed from —
        so a recompose confirmed against one plan cannot be applied to a workflow that has since moved a
        stage. Not part of ``to_json``: the route returns it beside the proposal (§2.3).
        """
        payload = {
            "intent_key": self.intent_key,
            "current_stage": self.current_stage,
            "skip": list(self.skip),
            "add": list(self.add),
            "state_sha256": self.state_sha256,
            "stages": [[s.slug, s.enabled] for s in self.plan.stages],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def to_json(self) -> dict[str, Any]:
        return {
            "intent_key": self.intent_key,
            "current_stage": self.current_stage,
            "skip": list(self.skip),
            "add": list(self.add),
            "plan": self.plan.to_json(),
            "argv_preview": list(self.argv_preview),
            "allowed": self.allowed,
            "refusals": [r.to_json() for r in self.refusals],
        }


@dataclass(frozen=True, slots=True)
class RecomposeResult:
    """The outcome of an applied recompose. §1.15 names the type; §2.3 pins what the route returns."""

    repo_id: str
    intent_key: str
    transaction_id: str
    skip: tuple[str, ...]
    add: tuple[str, ...]
    engine: "EngineResult"
    verified: dict[str, Any]
    summary: "IntentSummary | None"

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "intent_key": self.intent_key,
            "transaction_id": self.transaction_id,
            "skip": list(self.skip),
            "add": list(self.add),
            "result": self.engine.to_json(),
            "verified": dict(self.verified),
            "intent": None if self.summary is None else self.summary.to_json(),
        }


@dataclass(frozen=True, slots=True)
class IntentCreateResult:
    """A created — and deliberately unstarted — intent, with the disk evidence that it exists."""

    repo_id: str
    space: str
    intent_dir: str
    intent_key: str
    uuid: str | None
    slug: str | None
    transaction_id: str
    engine: "EngineResult"
    verified: dict[str, Any]
    summary: "IntentSummary | None"

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "space": self.space,
            "intent_dir": self.intent_dir,
            "intent_key": self.intent_key,
            "uuid": self.uuid,
            "slug": self.slug,
            "transaction_id": self.transaction_id,
            "engine": self.engine.to_json(),
            "verified": dict(self.verified),
            "summary": None if self.summary is None else self.summary.to_json(),
        }


# --------------------------------------------------------------------------- #
# service
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PlanCatalog:
    """The installed vocabulary a plan is composed from: scopes, stages, and the grid between them (§1.15).

    One coherent read for callers that need the *whole* menu rather than one request's plan — the
    Advisor's plan draft (§1.18a) renders it as the wizard's questionnaire. Grid values are upper-cased
    once here so ``grid_row`` can compare against ``"EXECUTE"`` the way ``_build_stages`` does.
    """

    scopes: tuple["ScopeMeta", ...]
    #: Graph order.
    stages: tuple["StageNode", ...]
    #: ``scope -> slug -> "EXECUTE" | "SKIP"``.
    grid: dict[str, dict[str, str]]
    engine_version: str | None

    def grid_row(self, scope: str) -> dict[str, bool]:
        """``{slug: selected}`` for a scope — the same baseline ``effective_plan`` uses without a snap.

        A scope with a grid row reads the row; a scope the grid does not know falls back to the stages
        that list it in their own ``scopes`` (the rule ``_build_stages`` applies); a scope unknown to
        both is ``{}``, so a caller can tell "nothing selected" from "no such scope".
        """
        row = self.grid.get(scope)
        if row:
            return {slug: value == "EXECUTE" for slug, value in row.items()}
        if any(scope in node.scopes for node in self.stages):
            return {node.slug: scope in node.scopes for node in self.stages}
        return {}


class PlanService:
    """Reads the installed plan; changes it only through the engine's own verbs, under an admin lease."""

    def __init__(
        self,
        reader: "AidlcReader",
        engine: "EngineRunner",
        scheduler: "RepoScheduler",
        registry: "RepoRegistry",
        projection: "Projection",
        storage: "Storage",
        estimates: EstimateService,
        activity: "ActivityProjector",
        events: "EventLog",
        clock: "C.Clock",
        ids: Any,
    ) -> None:
        """``registry`` is held, not called.

        §1.15 pins the constructor, and holding the reference keeps one object graph — but a plan is
        derived from the engine's files, and every repository fact this module needs already arrives on
        the ``RepoRecord`` the caller passes. Reaching back into the registry would mean a second,
        possibly newer, view of the same repository inside one plan.
        """
        self._reader = reader
        self._engine = engine
        self._scheduler = scheduler
        self._registry = registry
        self._projection = projection
        self._storage = storage
        self._estimates = estimates
        self._activity = activity
        self._events = events
        self._clock = clock
        self._ids = ids

    # ---- read model (sync; call through to_thread) --------------------------

    def catalog(self, repo: "RepoRecord", engine_dir: str) -> PlanCatalog:
        """Scopes, graph and grid, read exactly as ``effective_plan`` reads them without a snap (§1.15).

        Sync; callers run it off-thread. An empty graph is ``not_installed`` rather than an empty
        catalogue: a questionnaire with no stages would let a caller propose a plan for a repository
        that cannot run one.
        """
        root = Path(repo.canonical_path)
        graph = self._reader.read_stage_graph(root, engine_dir)
        if not graph:
            raise StudioError(
                "not_installed",
                "no stage graph is installed in this repository",
                details={"repo_id": repo.repo_id},
            )
        grid = self._reader.read_scope_grid(root, engine_dir)
        scopes = self._reader.read_scopes(root, engine_dir)
        return PlanCatalog(
            scopes=tuple(scopes),
            stages=tuple(graph),
            grid={
                str(scope): {str(slug): str(value).upper() for slug, value in row.items()}
                for scope, row in grid.items()
            },
            engine_version=repo.installed_engine_version,
        )

    def effective_plan(
        self,
        repo: "RepoRecord",
        engine_dir: str,
        req: PlanRequest,
        *,
        snap: "IntentSnapshot | None" = None,
    ) -> EffectivePlan:
        """The plan a request describes, against the engine installed in this repository.

        With ``snap`` the baseline is the intent's own state file (that is the plan that will actually
        run, including any earlier recompose); without it the baseline is the scope grid. Reading the
        graph from the snapshot when one is given keeps the whole answer inside one coherent read —
        mixing a fresh graph with an older state would produce lock reasons for stages that no longer
        exist.
        """
        root = Path(repo.canonical_path)
        have_snap = snap is not None
        read = self._reader
        graph = list(snap.graph) if have_snap and snap.graph else read.read_stage_graph(root, engine_dir)
        grid = dict(snap.grid) if have_snap and snap.grid else read.read_scope_grid(root, engine_dir)
        scopes = list(snap.scopes) if have_snap and snap.scopes else read.read_scopes(root, engine_dir)

        issues: list[PlanIssue] = []
        scope = req.scope
        scope_meta = next((s for s in scopes if s.name == scope), None)
        grid_row = {slug: str(value).upper() for slug, value in (grid.get(scope) or {}).items()}
        if not grid_row and scope_meta is None:
            issues.append(PlanIssue.of("scope_unknown", scope=scope,
                                       known=[s.name for s in scopes] or sorted(grid)))

        depth = self._resolve_depth(req, scope_meta, issues)
        request_cap = self._resolve_cap(req.review_cap, "review_cap", issues)
        scope_cap = self._resolve_cap(scope_meta.review_cap if scope_meta else None, None, issues)

        rows = self._state_rows(snap)
        current_index = self._current_index(snap, graph)

        stages = self._build_stages(
            graph=graph,
            grid_row=grid_row,
            rows=rows,
            snap=snap,
            req=req,
            scope=scope,
            caps=(scope_cap, request_cap),
            current_index=current_index,
            issues=issues,
        )
        issues.extend(self.validate(stages, graph, project_type=req.project_type))

        units = self._units(repo, snap)
        exact = self._exact_counts(stages, units)
        estimate = self._estimates.estimate(stages, depth=depth, units=units, scope=scope)
        engine_version = None
        if snap is not None and snap.engine is not None:
            engine_version = snap.engine.engine_version
        engine_version = engine_version or repo.installed_engine_version

        return EffectivePlan(
            request=req,
            scope_meta=scope_meta,
            stages=stages,
            exact=exact,
            estimate=estimate,
            diff=tuple(
                PlanDiffEntry(
                    slug=s.slug, from_enabled=s.in_grid, to_enabled=s.enabled, reason=DIFF_REASON_OVERRIDE
                )
                for s in stages
                if s.in_grid != s.enabled
            ),
            issues=tuple(issues),
            valid=not issues,
            products=tuple(sorted({a for s in stages if s.enabled for a in s.produces})),
            graph_stage_count=len(graph),
            engine_version=engine_version,
        )

    def validate(
        self,
        stages: Sequence[PlanStage],
        graph: Sequence["StageNode"],
        *,
        project_type: str | None = None,
    ) -> list[PlanIssue]:
        """The starvation this request would INTRODUCE (the engine's strict-diff rule).

        A required ``consumes`` entry with no producer on the selected path is starvation; a plan that was
        already starved before the request stays that way without being an error, because that is the
        scope author's decision, not the user's. ``project_type`` is keyword-only with a default so
        §1.15's two-argument call still holds: when it is unknown the conditional consumes are checked
        (the engine skips them only when it knows the type and it differs).

        Pure and public because the rule is applied twice — inside ``effective_plan``, and by
        ``recompose_preview`` when it decides whether a flip is legal.
        """
        satisfied = {s.slug for s in stages if s.state in _DEPENDENCY_SATISFIED_STATES}
        before = _starvations(graph, {s.slug for s in stages if s.in_grid} | satisfied, project_type)
        after = _starvations(graph, {s.slug for s in stages if s.enabled} | satisfied, project_type)
        producers = _producers_of(graph)
        issues: list[PlanIssue] = []
        for consumer, artifact in sorted(after - before):
            issues.append(
                PlanIssue.of(
                    "dependency_missing",
                    consumer,
                    *producers.get(artifact, ()),
                    stage=consumer,
                    artifact=artifact,
                    producers=list(producers.get(artifact, ())),
                )
            )
        return issues

    def recompose_preview(
        self,
        repo: "RepoRecord",
        snap: "IntentSnapshot",
        *,
        skip: Sequence[str],
        add: Sequence[str],
    ) -> RecomposeProposal:
        """What ``recompose --skip … --add …`` would do, and every reason it must not run.

        The refusals are the engine's, in the order it applies them: the per-flip guards (compiled,
        pending, ahead of the cursor), then anything wrong with the resulting plan, then the two
        workflow-level guards (Status must be Running, autonomy must not be autonomous; the
        walking-skeleton anchor must not move). Previewing a flip as allowed that the engine would die
        on turns a confirmation dialog into a failed subprocess, so every guard is mirrored here.
        """
        engine_dir = self._engine_dir(repo, snap)
        wanted_skip = _slug_list(skip)
        wanted_add = _slug_list(add)
        req = self._request_from_state(repo, snap, skip=wanted_skip, add=wanted_add)
        plan = self.effective_plan(repo, engine_dir, req, snap=snap)

        refusals: list[PlanIssue] = []
        for slug in (*wanted_skip, *wanted_add):
            refusals.extend(self._recompose_refusals(plan, slug))
        # Every plan issue is a refusal here: a recompose is applied as a whole, so a stage whose flip
        # was ignored (locked) or whose flip starves a consumer must stop the apply, not be silently
        # dropped from the argv.
        refusals.extend(plan.issues)
        lane = self._recompose_lane_refusal(snap)
        if lane is not None:
            refusals.append(lane)
        anchor = self._anchor_refusal(plan)
        if anchor is not None:
            refusals.append(anchor)
        refusals = _dedupe_issues(refusals)

        applied_skip = tuple(s for s in wanted_skip if s not in wanted_add)
        applied_add = tuple(wanted_add)
        return RecomposeProposal(
            intent_key=snap.intent_key,
            current_stage=snap.state.current_stage if snap.state is not None else None,
            skip=applied_skip,
            add=applied_add,
            plan=plan,
            argv_preview=_recompose_argv(applied_skip, applied_add),
            allowed=bool(plan.valid and not refusals and (applied_skip or applied_add)),
            refusals=tuple(refusals),
            state_sha256=snap.state_sha256,
        )

    # ---- mutations (async; engine verbs under an admin lease) ---------------

    async def create_intent(
        self,
        repo: "RepoRecord",
        req: PlanRequest,
        *,
        blocking_findings: Sequence["Finding"] = (),
        confirm_plan_digest: str | None = None,
    ) -> IntentCreateResult:
        """Create an intent with ``utility.intent_create`` and prove on disk that it exists.

        Never sends a prompt and never runs ``next``: the new intent is inert (``Idle``) until a human
        presses Run, which is what makes "Studio started a workflow the user did not ask for"
        impossible (FR-RUN-001, PRD §6).

        ``blocking_findings`` is keyword-only with an empty default so §1.15's two-argument call still
        type-checks: the caller (the route) already has ``RepoFacts`` and the evaluated findings, and
        re-deriving them here would need a second, possibly disagreeing, read of the repository.
        ``confirm_plan_digest`` is the §2.3 route field: when present it must match the plan computed
        here, so an intent is never created from a composition the user did not see (a scope file or a
        graph can change between the preview and the confirm).
        """
        self._require_ready(repo)
        blocking = [f for f in blocking_findings if getattr(f, "severity", "") == "blocking"]
        if blocking:
            raise StudioError(
                "state_inconsistent",
                "the repository has blocking findings; resolve them before creating an intent",
                details={"findings": [f.to_json() for f in blocking]},
            )
        engine_dir = self._engine_dir(repo, None)
        space = req.space or C.DEFAULT_SPACE
        root = Path(repo.canonical_path)

        active_space = await asyncio.to_thread(self._reader.active_space, root)
        if space != active_space:
            # `intent-create` has no --space flag: it always writes into the active space. Creating in
            # another space would need a space switch, which rewrites the harness agents — a separate,
            # explicit operation, not a side effect of the wizard.
            raise StudioError(
                "bad_param",
                "an intent can only be created in the active space",
                details={"space": space, "active_space": active_space},
            )

        plan = await asyncio.to_thread(self.effective_plan, repo, engine_dir, req)
        if not plan.valid:
            raise StudioError("plan_invalid", "the plan cannot be committed as described",
                              details={"issues": [i.to_json() for i in plan.issues]})
        if confirm_plan_digest is not None and confirm_plan_digest != plan.digest():
            raise StudioError(
                "plan_invalid",
                "the plan changed since it was previewed",
                details={"reason": "plan_digest_mismatch", "expected": plan.digest()},
            )
        label = self._require_label(req)
        arguments = self._require_arguments(req)

        transaction_id = self._ids.new("tx")
        grant = await self._scheduler.acquire_admin(
            repo, operation_type=OP_INTENT_CREATE, transaction_id=transaction_id
        )
        try:
            # Listed under the lease, so "exactly one new record" cannot be spoiled by a concurrent
            # create: with the lease held, this verb is the only thing that can add one.
            before = set(await asyncio.to_thread(self._reader.list_intent_dirs, root, space))
            result = await self._engine.run(
                "utility.intent_create",
                root,
                engine_dir,
                scope=req.scope,
                arguments=arguments,
                label=label,
                depth=req.depth or "",
                test_strategy=req.test_strategy or "",
                review=req.review_cap or "",
                lease_generation=grant.generation,
                repo_id=repo.repo_id,
            )
            self._require_engine_ok(result)
            intent_dir, verified = await asyncio.to_thread(self._verify_created, root, space, before)
            try:
                runtime = await self._engine.run(
                    "runtime.compile", root, engine_dir,
                    lease_generation=grant.generation, repo_id=repo.repo_id,
                )
                self._require_engine_ok(runtime)
                runtime_path = f"{self._reader.record_rel(space, intent_dir)}/runtime-graph.json"
                runtime_snap = await asyncio.to_thread(self._reader.read_artifact, root, runtime_path)
                try:
                    graph = json.loads(runtime_snap.bytes) if runtime_snap is not None else None
                except (ValueError, UnicodeDecodeError):
                    graph = None
                if not isinstance(graph, dict) or not isinstance(graph.get("stages"), list):
                    raise StudioError(
                        "state_inconsistent", "runtime compilation did not produce a readable graph",
                        details={"relpath": runtime_path},
                    )
                verified["runtime_graph_present"] = True
            except StudioError as exc:
                # Creation has already succeeded. Preserve that fact in the error so callers do not
                # mistake a failed derived-file compile for an intent that never existed.
                raise StudioError(
                    exc.code, f"Intent {intent_dir} was created, but its runtime graph is not ready: {exc.message}",
                    details={**exc.details, "intent_created": True, "intent_dir": intent_dir,
                             "intent_key": C.intent_key_for(space, intent_dir), "space": space},
                ) from exc
        finally:
            await self._scheduler.release(grant)

        row = await asyncio.to_thread(self._registry_row, root, space, intent_dir)
        summary = await self._summary(repo, space, intent_dir)
        intent_key = C.intent_key_for(space, intent_dir)
        await self._activity.record(
            kind="intent.created",
            repo_id=repo.repo_id,
            space=space,
            intent_dir=intent_dir,
            params={
                "scope": req.scope,
                "depth": req.depth,
                "test_strategy": req.test_strategy,
                "review_cap": req.review_cap,
                "label": label,
                "transaction_id": transaction_id,
                "state_version": verified.get("state_version"),
            },
        )
        await self._events.publish(
            "intent.updated",
            {
                "repo_id": repo.repo_id,
                "intent_key": intent_key,
                "operational_state": summary.operational_state if summary is not None else "Idle",
            },
        )
        return IntentCreateResult(
            repo_id=repo.repo_id,
            space=space,
            intent_dir=intent_dir,
            intent_key=intent_key,
            uuid=row.uuid if row is not None else None,
            slug=row.slug if row is not None else None,
            transaction_id=transaction_id,
            engine=result,
            verified=verified,
            summary=summary,
        )

    async def recompose(
        self,
        repo: "RepoRecord",
        snap: "IntentSnapshot",
        *,
        skip: Sequence[str],
        add: Sequence[str],
        proposal_digest: str,
    ) -> RecomposeResult:
        """Apply a previewed recompose.

        The proposal is rebuilt from ``snap`` and its digest compared with the one the user confirmed: a
        confirmation is a statement about one plan, and applying it to a workflow that has moved a stage
        in the meantime is how a user ends up skipping a stage they were looking at. The caller must pass
        a FRESH snapshot — the digest can only detect a change it can see.
        """
        self._require_ready(repo)
        engine_dir = self._engine_dir(repo, snap)
        proposal = await asyncio.to_thread(
            self.recompose_preview, repo, snap, skip=list(skip), add=list(add)
        )
        if proposal.digest() != proposal_digest:
            raise StudioError(
                "plan_invalid",
                "the plan changed since this proposal was previewed",
                details={"reason": "proposal_digest_mismatch", "expected": proposal.digest()},
            )
        if not proposal.allowed:
            raise StudioError(
                "recompose_not_allowed",
                "this recompose cannot be applied",
                details={"refusals": [r.to_json() for r in proposal.refusals]},
            )

        transaction_id = self._ids.new("tx")
        root = Path(repo.canonical_path)
        grant = await self._scheduler.acquire_admin(
            repo, operation_type=OP_RECOMPOSE, transaction_id=transaction_id
        )
        try:
            # The record is named explicitly. Everything else in this method — the lease, the
            # proposal, the digest the user confirmed, the on-disk verification, the event — is about
            # THIS intent, and an omitted selector lets the engine resolve its own (the calling
            # session's binding before the cursor, on 2.7.1). Both flags travel together: `--intent`
            # alone would still take the space from that binding.
            result = await self._engine.run(
                "utility.recompose",
                root,
                engine_dir,
                skip=",".join(proposal.skip),
                add=",".join(proposal.add),
                dir_name=snap.intent_dir,
                slug=snap.space,
                lease_generation=grant.generation,
                repo_id=repo.repo_id,
            )
            self._require_engine_ok(result)
            verified = await asyncio.to_thread(
                self._verify_recomposed, root, snap.space, snap.intent_dir, proposal
            )
        finally:
            await self._scheduler.release(grant)

        summary = await self._summary(repo, snap.space, snap.intent_dir)
        await self._events.publish(
            "intent.updated",
            {
                "repo_id": repo.repo_id,
                "intent_key": snap.intent_key,
                "operational_state": summary.operational_state if summary is not None else "Idle",
            },
        )
        return RecomposeResult(
            repo_id=repo.repo_id,
            intent_key=snap.intent_key,
            transaction_id=transaction_id,
            skip=proposal.skip,
            add=proposal.add,
            engine=result,
            verified=verified,
            summary=summary,
        )

    async def scope_change(
        self,
        repo: "RepoRecord",
        snap: "IntentSnapshot",
        *,
        scope: str,
        depth: str | None = None,
        test_strategy: str | None = None,
    ) -> "EngineResult":
        """Re-scope a live intent through ``utility.scope_change``."""
        if not C.STAGE_SLUG_RE.match(scope or ""):
            raise StudioError("bad_param", "scope is not a scope id", details={"scope": scope})
        return await self._config_verb(
            repo,
            snap,
            verb_key="utility.scope_change",
            operation_type=OP_SCOPE_CHANGE,
            kwargs={
                "scope": scope,
                "depth": self._checked_setting(depth, DEPTHS, "depth"),
                "test_strategy": self._checked_setting(test_strategy, TEST_STRATEGIES, "test_strategy"),
            },
        )

    async def config_change(
        self,
        repo: "RepoRecord",
        snap: "IntentSnapshot",
        *,
        depth: str | None = None,
        test_strategy: str | None = None,
    ) -> "EngineResult":
        """Change depth / test strategy through ``utility.config_change``."""
        if not depth and not test_strategy:
            raise StudioError("bad_param", "nothing to change", details={"fields": ["depth", "test_strategy"]})
        return await self._config_verb(
            repo,
            snap,
            verb_key="utility.config_change",
            operation_type=OP_CONFIG_CHANGE,
            kwargs={
                "depth": self._checked_setting(depth, DEPTHS, "depth"),
                "test_strategy": self._checked_setting(test_strategy, TEST_STRATEGIES, "test_strategy"),
            },
        )

    def settings_preview(
        self, repo: "RepoRecord", snap: "IntentSnapshot", changes: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Preview the existing-intent verbs, including the engine's scope row rebuild.

        Scope change is different from recompose: it resets the selection to the new grid while
        retaining completed checkboxes. In 2.7.1 its row serializer cannot retain gate/revision marks,
        so those changes are refused before the engine touches the record.
        """
        self._require_ready(repo)
        unknown = set(changes) - {"scope", "depth", "test_strategy"}
        if unknown:
            raise StudioError("bad_body", "unknown settings", details={"keys": sorted(unknown)})
        state = snap.state
        if state is None or state.header_only or not snap.state_sha256 or not snap.stable:
            raise StudioError("state_inconsistent", "a stable initialized intent is required")
        current = self._request_from_state(repo, snap, skip=(), add=())
        before = {"scope": current.scope, "depth": current.depth,
                  "test_strategy": current.test_strategy or current.depth}
        after = dict(before)
        for key, value in changes.items():
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise StudioError("bad_body", "setting must be a nonblank string", details={"key": key})
            if key != "scope":
                self._checked_setting(value, DEPTHS, key)
            after[key] = value
        if after["scope"] not in snap.grid:
            raise StudioError("plan_invalid", "unknown scope", details={"scope": after["scope"]})
        if not before["scope"] or not before["depth"]:
            raise StudioError("state_inconsistent", "the current scope or depth is missing")
        if after["depth"] not in DEPTHS or after["test_strategy"] not in DEPTHS:
            raise StudioError("state_inconsistent", "the recorded depth or test strategy is invalid")
        changing_scope = after["scope"] != before["scope"]
        # Always pass all three resolved settings: changing scope alone must not silently reset the
        # user's chosen depth/test strategy to the new scope's defaults.
        rows = {row.slug: row for row in state.stages}
        graph_slugs = {node.slug for node in snap.graph}
        grid = snap.grid[after["scope"]]
        stages = []
        refusals = []
        if changing_scope and (state.autonomy_mode or "").lower() == AUTONOMOUS_MODE:
            refusals.append("autonomous")
        if changing_scope and (set(rows) - graph_slugs or len(rows) != len(state.stages)):
            refusals.append("unknown_stages")
        for node in snap.graph:
            row = rows.get(node.slug)
            selected = row is not None and _row_selected(row)
            target = str(grid.get(node.slug, "SKIP")).upper() == "EXECUTE" if changing_scope else selected
            if changing_scope and node.slug == "reverse-engineering":
                if (current.project_type or "Greenfield").lower() == "greenfield":
                    target = False
            if row is not None and row.state == "skipped":
                target = False
            stages.append({"slug": node.slug, "state": row.state if row else "not_started",
                           "before": selected, "after": target})
            if changing_scope and row is not None:
                if row.state in ("awaiting_approval", "revising", "unknown"):
                    refusals.append("open_boundary")
                if row.state == "in_progress" and not target:
                    refusals.append("current_stage_removed")
                if node.slug == state.current_stage and selected and not target:
                    refusals.append("current_stage_removed")
        if before == after:
            refusals.append("no_changes")
        verb = "utility.scope_change" if changing_scope else "utility.config_change"
        kwargs = {**after, "dir_name": snap.intent_dir, "slug": snap.space}
        if not changing_scope:
            kwargs.pop("scope")
        argv = self._engine.build_argv(
            self._engine.verb_for(verb), Path(repo.canonical_path), self._engine_dir(repo, snap), **kwargs
        )
        proposal = {
            "repo_id": repo.repo_id, "intent_key": snap.intent_key,
            "before": before, "after": after, "stages": stages,
            "scopes": sorted(snap.grid), "allowed": not refusals,
            "refusals": list(dict.fromkeys(refusals)), "argv_preview": argv,
            "verb_key": verb,
            "selection": {"space": snap.space, "intent_dir": snap.intent_dir},
        }
        payload = {
            "proposal": proposal, "state_sha256": snap.state_sha256,
            "identity": repo.resolved_identity, "engine_dir": self._engine_dir(repo, snap),
            "graph": [node.to_json() for node in snap.graph], "grid": snap.grid,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return {"proposal": proposal, "proposal_digest": digest}

    async def change_settings(
        self, repo: "RepoRecord", snap: "IntentSnapshot", changes: Mapping[str, Any],
        *, proposal_digest: str,
    ) -> dict[str, Any]:
        """Re-read and compare under the lease, then verify settings and every existing mark on disk."""
        self._require_ready(repo)
        grant = await self._scheduler.acquire_admin(
            repo, operation_type=OP_SCOPE_CHANGE if "scope" in changes else OP_CONFIG_CHANGE,
            transaction_id=None,
        )
        try:
            # The handler's earlier snapshot is only an address. It must not authorize a write after
            # another holder advanced the intent while this request was acquiring its lease.
            await asyncio.to_thread(self._registry.require_current_identity, repo)
            self._scheduler.require_host_idle(repo)
            fresh = await asyncio.to_thread(self._projection.snapshot, repo, snap.space, snap.intent_dir)
            answer = await asyncio.to_thread(self.settings_preview, repo, fresh, changes)
            if answer["proposal_digest"] != proposal_digest:
                raise StudioError("plan_invalid", "the settings proposal is stale",
                                  details={"reason": "proposal_digest_mismatch"})
            proposal = answer["proposal"]
            if not proposal["allowed"]:
                raise StudioError("plan_invalid", "this change cannot be applied",
                                  details={"refusals": proposal["refusals"]})
            # 2.7.1 applies --intent/--space to the state write, but appendAuditEvent still resolves
            # the calling session/cursor. Pin and verify both before writing, so an inactive intent's
            # settings event cannot land in somebody else's audit. Activation is disclosed in preview.
            await asyncio.to_thread(self._registry.require_current_identity, repo)
            self._scheduler.require_host_idle(repo)
            cursor = await asyncio.to_thread(
                self._engine.switch_cursor_sync, Path(repo.canonical_path), self._engine_dir(repo, fresh),
                fresh.space, fresh.intent_dir, C.attr(fresh.row, "uuid"),
                lease_generation=grant.generation, repo_id=repo.repo_id,
            )
            await self._engine.drain_activity()
            await self._events.publish("repo.updated", {"repo_id": repo.repo_id})
            if not cursor.ok or any(not item.ok for item in cursor.results):
                raise StudioError("state_inconsistent", "the selected intent could not be activated",
                                  details={"mismatch": list(cursor.mismatch)})
            if cursor.state_sha256 != fresh.state_sha256:
                raise StudioError("plan_invalid", "the state changed while activating the intent",
                                  details={"reason": "proposal_digest_mismatch"})
            kwargs = dict(proposal["after"])
            if proposal["verb_key"] == "utility.config_change":
                kwargs.pop("scope")
            await asyncio.to_thread(self._registry.require_current_identity, repo)
            self._scheduler.require_host_idle(repo)
            result = await self._engine.run(
                proposal["verb_key"], Path(repo.canonical_path), self._engine_dir(repo, fresh),
                dir_name=fresh.intent_dir, slug=fresh.space, lease_generation=grant.generation,
                repo_id=repo.repo_id, **kwargs,
            )
            self._require_engine_ok(result)
            observed = await asyncio.to_thread(
                self._reader.read_state, Path(repo.canonical_path), fresh.space, fresh.intent_dir
            )
            if observed is None:
                raise StudioError("state_inconsistent", "the changed state cannot be read")
            state = observed[1]
            actual = {"scope": state.project.get("Scope"), "depth": state.scope_config.get("Depth"),
                      "test_strategy": state.scope_config.get("Test Strategy")}
            preserved = all(
                state.row(row.slug) is not None and state.row(row.slug).state == row.state
                for row in fresh.state.stages
            )
            selections_match = all(
                (state.row(row["slug"]) is not None
                 and _row_selected(state.row(row["slug"])) == row["after"])
                or (proposal["verb_key"] == "utility.config_change"
                    and fresh.state.row(row["slug"]) is None and state.row(row["slug"]) is None)
                for row in proposal["stages"]
            )
            if actual != proposal["after"] or not preserved or not selections_match:
                raise StudioError("state_inconsistent", "the engine result did not match the preview",
                                  details={"settings": actual, "stages_preserved": preserved,
                                           "selections_match": selections_match})
        finally:
            await self._scheduler.release(grant)
        summary = await self._summary(repo, snap.space, snap.intent_dir)
        await self._events.publish("intent.updated", {
            "repo_id": repo.repo_id, "intent_key": snap.intent_key,
            "operational_state": summary.operational_state if summary is not None else "Idle",
        })
        return {"ok": True, "result": result.to_json(),
                "verified": {"settings": actual, "stages_preserved": preserved}}

    # ---- stage construction ------------------------------------------------

    def _build_stages(
        self,
        *,
        graph: Sequence["StageNode"],
        grid_row: Mapping[str, str],
        rows: Mapping[str, "StageRow"],
        snap: "IntentSnapshot | None",
        req: PlanRequest,
        scope: str,
        caps: tuple[str | None, str | None],
        current_index: int | None,
        issues: list[PlanIssue],
    ) -> tuple[PlanStage, ...]:
        live = snap is not None
        base: dict[str, bool] = {}
        locks: dict[str, str | None] = {}
        drafts: list[dict[str, Any]] = []

        for index, node in enumerate(graph):
            row = rows.get(node.slug)
            if live:
                # The state file is the plan that will run; the grid only says what the scope composes.
                selected = row is not None and _row_selected(row)
            elif grid_row:
                selected = grid_row.get(node.slug) == "EXECUTE"
            else:
                selected = scope in node.scopes
            base[node.slug] = selected
            locks[node.slug] = self._base_lock(node, row, index, current_index, selected)
            drafts.append({"node": node, "row": row})

        graph_slugs = {node.slug for node in graph}
        for slug, row in rows.items():
            if slug in graph_slugs:
                continue
            # A row the installed graph does not know (a state written by another engine generation).
            # It still runs, so it is shown — locked, because nothing here can reason about it.
            base[slug] = _row_selected(row)
            locks[slug] = LOCK_NOT_IN_GRAPH
            drafts.append({"node": None, "row": row, "slug": slug})

        # The dependency lock has to be part of the BASE locks, not only of the rendered result: it is
        # what refuses "turn off a stage another enabled stage needs". Computed after the overrides it
        # could only ever explain a plan that had already lost its input.
        satisfied = {slug for slug, row in rows.items() if row.state in _DEPENDENCY_SATISFIED_STATES}
        base_required_by = _required_by(graph, base, satisfied, req.project_type)
        for slug, needed_by in base_required_by.items():
            locks[slug] = _best_lock([locks.get(slug), lock_required_by(needed_by)])

        enabled = dict(base)
        for slug, wanted in req.overrides.items():
            if slug not in base:
                issues.append(PlanIssue.of("unknown_stage", slug, stage=slug))
                continue
            if bool(wanted) == base[slug]:
                continue
            reason = locks.get(slug)
            if reason is not None:
                issues.append(self._override_refused(slug, reason))
                continue
            enabled[slug] = bool(wanted)

        # An optional producer must remain removable when that only restores a gap the preset
        # already permits. Match validate_dependencies' strict-diff rule; newly added consumers
        # still lock the producers they actually require.
        baseline_gaps = _starvations(
            graph, {slug for slug, selected in base.items() if selected} | satisfied, req.project_type
        )
        required_by = _required_by(
            graph, enabled, satisfied, req.project_type, allowed_gaps=baseline_gaps
        )

        stages: list[PlanStage] = []
        for draft in drafts:
            node = draft["node"]
            row = draft["row"]
            slug = node.slug if node is not None else str(draft["slug"])
            is_enabled = bool(enabled.get(slug))
            # Only the baseline can make an ALWAYS stage mandatory. An optional stage added in
            # this preview must remain removable until the user commits the plan.
            reasons = [locks.get(slug)]
            if slug in required_by and is_enabled:
                reasons.append(lock_required_by(required_by[slug]))
            reason = _best_lock(reasons)
            phase = (node.phase if node is not None else (row.phase if row is not None else "")) or ""
            stages.append(
                PlanStage(
                    slug=slug,
                    number=node.number if node is not None else "",
                    name=node.name if node is not None else slug,
                    phase=phase,
                    execution=node.execution if node is not None else "",
                    in_grid=bool(base.get(slug)),
                    enabled=is_enabled,
                    locked=reason is not None,
                    lock_reason=reason,
                    gate=phase != "initialization",
                    review_class=_effective_review_class(node, caps),
                    reviewer=node.reviewer if node is not None else None,
                    per_unit=bool(node.per_unit) if node is not None else False,
                    produces=tuple(node.produces) if node is not None else (),
                    consumes=tuple(c.artifact for c in node.consumes) if node is not None else (),
                    depends_on=tuple(node.requires_stage) if node is not None else (),
                    conditional_on=(
                        node.condition if node is not None and node.execution == "CONDITIONAL" else None
                    ),
                    state=row.state if row is not None else None,
                    mode=node.mode if node is not None else "",
                    summary_confirmation=node.summary_confirmation if node is not None else None,
                )
            )
        return tuple(stages)

    @staticmethod
    def _base_lock(
        node: "StageNode",
        row: "StageRow | None",
        index: int,
        current_index: int | None,
        selected: bool,
    ) -> str | None:
        """The lock a stage has before overrides are applied.

        ``always`` is applied only to a stage that is currently selected: the stock ``poc``/``bugfix``
        grids SKIP four ``execution: ALWAYS`` stages, and locking those would present rows the user can
        neither enable nor understand — and would make ``recompose --add units-generation`` impossible.
        """
        if row is not None:
            if row.state == "completed" or row.state == "skipped":
                return LOCK_COMPLETED
            if row.state == "in_progress":
                return LOCK_CURRENT
            if row.state in ("awaiting_approval", "revising"):
                return LOCK_AT_GATE
            # Graph POSITION, not stage number: that is what the engine compares
            # (`idx <= currentIdx`), and numbers moved between graph generations.
            if current_index is not None and index <= current_index:
                return LOCK_BEHIND_CURSOR
        if node.execution == "ALWAYS" and selected:
            return LOCK_ALWAYS
        return None

    @staticmethod
    def _override_refused(slug: str, reason: str) -> PlanIssue:
        """Map a lock to the issue code that explains why the toggle was ignored."""
        if reason == LOCK_BEHIND_CURSOR:
            return PlanIssue.of("behind_cursor", slug, stage=slug)
        if reason == LOCK_ALWAYS or reason.startswith(_REQUIRED_BY_PREFIX):
            return PlanIssue.of("required_stage_disabled", slug, stage=slug, lock_reason=reason)
        return PlanIssue.of("frozen_stage", slug, stage=slug, lock_reason=reason)

    def _exact_counts(self, stages: Sequence[PlanStage], units: int | None) -> ExactCounts:
        unit_count = units if units is not None else 1
        enabled = [s for s in stages if s.enabled]
        intensity = {name: 0 for name in REVIEW_CAPS}
        artifacts = 0
        for stage in enabled:
            intensity[stage.review_class or "none"] += 1
            artifacts += len(stage.produces) * (unit_count if stage.per_unit else 1)
        return ExactCounts(
            stages=len(enabled),
            gates=sum(1 for s in enabled if s.gate),
            artifacts=artifacts,
            review_intensity=intensity,
            assumes_units=None if units is not None else 1,
        )

    # ---- request / state plumbing ------------------------------------------

    def _request_from_state(
        self,
        repo: "RepoRecord",
        snap: "IntentSnapshot",
        *,
        skip: Sequence[str],
        add: Sequence[str],
    ) -> PlanRequest:
        """Rebuild the request a live intent already represents, then apply ``skip``/``add`` on top."""
        state = snap.state
        config = dict(state.scope_config) if state is not None else {}
        project = dict(state.project) if state is not None else {}
        overrides: dict[str, bool] = {slug: False for slug in skip}
        overrides.update({slug: True for slug in add})
        return PlanRequest(
            repo_id=repo.repo_id,
            space=snap.space,
            scope=(project.get("Scope") or "").strip(),
            depth=(config.get("Depth") or "").strip(),
            test_strategy=(config.get("Test Strategy") or "").strip() or None,
            review_cap=(config.get("Review Override") or "").strip() or None,
            project_type=(project.get("Project Type") or "").strip() or None,
            overrides=overrides,
            objective=None,
            label=None,
            context=None,
        )

    @staticmethod
    def _state_rows(snap: "IntentSnapshot | None") -> dict[str, "StageRow"]:
        if snap is None or snap.state is None:
            return {}
        return {row.slug: row for row in snap.state.stages}

    @staticmethod
    def _current_index(snap: "IntentSnapshot | None", graph: Sequence["StageNode"]) -> int | None:
        """The cursor as a position in the installed graph, or ``None`` when it cannot be located.

        ``None`` disables the ``behind_cursor`` lock rather than guessing: a state file written by another
        engine generation can name a stage this graph does not have, and a guessed position would freeze
        the wrong half of the plan.
        """
        if snap is None or snap.state is None or not snap.state.current_stage:
            return None
        for index, node in enumerate(graph):
            if node.slug == snap.state.current_stage:
                return index
        return None

    def _units(self, repo: "RepoRecord", snap: "IntentSnapshot | None") -> int | None:
        """How many units of work the per-unit stages will fan out over, or ``None`` when unknown."""
        if snap is None:
            return None
        try:
            units = self._reader.list_units(Path(repo.canonical_path), snap.space, snap.intent_dir)
        except StudioError:
            return None
        return len(units) or None

    def _resolve_depth(
        self, req: PlanRequest, scope_meta: "ScopeMeta | None", issues: list[PlanIssue]
    ) -> str:
        """The depth the estimate uses. An empty request depth means "the scope's own"."""
        if req.depth and req.depth in DEPTHS:
            return req.depth
        if req.depth:
            issues.append(PlanIssue.of("depth_invalid", depth=req.depth, allowed=list(DEPTHS)))
        if scope_meta is not None and scope_meta.depth in DEPTHS:
            return str(scope_meta.depth)
        return "Standard"

    @staticmethod
    def _resolve_cap(value: str | None, key: str | None, issues: list[PlanIssue]) -> str | None:
        """A review cap, or ``None``.

        An unknown value is dropped rather than trusted (dropping a cap can only ever mean *more*
        review, never less) and reported under ``depth_invalid`` — §1.15's code for "a configuration
        value is not one of the engine's settings"; ``params.field`` says which one. A cap read off a
        state file (``key is None``) is dropped silently: the user did not type it, and it must not make
        their live plan unrecomposable.
        """
        if not value:
            return None
        if value in REVIEW_CAPS:
            return value
        if key is not None:
            issues.append(PlanIssue.of("depth_invalid", field=key, value=value, allowed=list(REVIEW_CAPS)))
        return None

    @staticmethod
    def _checked_setting(value: str | None, allowed: Sequence[str], field: str) -> str:
        """A depth-shaped engine setting, or ``""`` for "leave it alone".

        Refused here rather than reported as a plan issue: these two methods write straight through to
        the engine, so an unknown value would become an argv token the engine has to reject instead of a
        400 the caller can fix.
        """
        if not value:
            return ""
        if value not in allowed:
            raise StudioError("bad_param", f"{field} must be one of the engine's settings",
                              details={"field": field, "value": value, "allowed": list(allowed)})
        return value

    # ---- recompose refusals ------------------------------------------------

    @staticmethod
    def _recompose_refusals(plan: EffectivePlan, slug: str) -> list[PlanIssue]:
        """The engine's three per-flip guards: compiled, pending, ahead of the cursor.

        Deliberately not "is this stage locked": a lock that a flip actually contradicts already produced
        an issue on the plan (the override was refused), while an ``--add`` of a stage the plan already
        runs is a no-op the engine accepts. Refusing on the lock alone would reject that no-op with a
        reason that is not true of it.
        """
        stage = plan.stage(slug)
        if stage is None or stage.lock_reason == LOCK_NOT_IN_GRAPH:
            return [PlanIssue.of("unknown_stage", slug, stage=slug)]
        if stage.state in _FROZEN_STATES:
            return [PlanIssue.of("frozen_stage", slug, stage=slug, state=stage.state)]
        if stage.lock_reason == LOCK_BEHIND_CURSOR:
            return [PlanIssue.of("behind_cursor", slug, stage=slug)]
        return []

    def _anchor_refusal(self, plan: EffectivePlan) -> PlanIssue | None:
        """Refuse a flip that moves the walking-skeleton anchor, as the engine does.

        The skeleton gate is derived from the first selected Construction stage, so moving it would
        silently relocate Bolt 1 and desynchronise the skeleton-stance round trip. The engine rejects
        this; previewing it as allowed would turn a confirmation into a failed subprocess.
        """
        before = _anchor_stage_from(plan.stages, use_base=True)
        after = _anchor_stage_from(plan.stages, use_base=False)
        if before == after:
            return None
        moved = after or before
        return PlanIssue.of(
            "skeleton_anchor",
            *(s for s in (moved,) if s),
            anchor_before=before,
            anchor_after=after,
        )

    @staticmethod
    def _recompose_lane_refusal(snap: "IntentSnapshot") -> PlanIssue | None:
        """Refuse a recompose the engine would not honour: not Running, or autonomous construction."""
        state = snap.state
        status = (state.status if state is not None else None) or ""
        autonomy = ((state.autonomy_mode if state is not None else None) or "").strip().lower()
        if status != RECOMPOSE_REQUIRED_STATUS or autonomy == AUTONOMOUS_MODE:
            return PlanIssue.of(
                "recompose_not_allowed",
                status=status,
                required_status=RECOMPOSE_REQUIRED_STATUS,
                autonomy_mode=autonomy or None,
            )
        return None

    # ---- engine plumbing ---------------------------------------------------

    async def _config_verb(
        self,
        repo: "RepoRecord",
        snap: "IntentSnapshot",
        *,
        verb_key: str,
        operation_type: str,
        kwargs: Mapping[str, str],
    ) -> "EngineResult":
        self._require_ready(repo)
        engine_dir = self._engine_dir(repo, snap)
        transaction_id = self._ids.new("tx")
        grant = await self._scheduler.acquire_admin(
            repo, operation_type=operation_type, transaction_id=transaction_id
        )
        try:
            result = await self._engine.run(
                verb_key,
                Path(repo.canonical_path),
                engine_dir,
                # Same reason as `recompose`: the snapshot names the record, so the engine is never
                # left to resolve one of its own from a session binding or a moved cursor.
                dir_name=snap.intent_dir,
                slug=snap.space,
                lease_generation=grant.generation,
                repo_id=repo.repo_id,
                **{k: v for k, v in kwargs.items()},
            )
            self._require_engine_ok(result)
        finally:
            await self._scheduler.release(grant)
        summary = await self._summary(repo, snap.space, snap.intent_dir)
        await self._events.publish(
            "intent.updated",
            {
                "repo_id": repo.repo_id,
                "intent_key": snap.intent_key,
                "operational_state": summary.operational_state if summary is not None else "Idle",
            },
        )
        return result

    @staticmethod
    def _require_engine_ok(result: "EngineResult") -> None:
        """A refused or timed-out verb is never treated as applied."""
        if result.ok:
            return
        if result.timed_out:
            raise StudioError(
                "engine_unavailable",
                "the AI-DLC engine did not finish in time",
                details={"verb_key": result.verb_key, "duration_ms": result.duration_ms},
            )
        raise StudioError(
            "state_inconsistent",
            "the AI-DLC engine refused the operation",
            details={
                "verb_key": result.verb_key,
                "exit_code": result.exit_code,
                "stderr": result.stderr[-2000:],
            },
        )

    @staticmethod
    def _require_ready(repo: "RepoRecord") -> None:
        """Refuse before taking a lease when the repository cannot host an engine verb at all."""
        if repo.availability == "identity_unprovable":
            raise StudioError("identity_unprovable", "this repository's identity cannot be proved",
                              details={"repo_id": repo.repo_id})
        if repo.availability != "available":
            raise StudioError("repo_unavailable", "this repository is not available",
                              details={"repo_id": repo.repo_id, "availability": repo.availability})
        if repo.install_status == "recovery_required":
            raise StudioError("install_recovery_required", "the installation needs recovery first",
                              details={"repo_id": repo.repo_id})
        if repo.install_status == "not_installed" or not repo.engine_dir:
            raise StudioError("not_installed", "AI-DLC is not installed in this repository",
                              details={"repo_id": repo.repo_id})

    @staticmethod
    def _engine_dir(repo: "RepoRecord", snap: "IntentSnapshot | None") -> str:
        if snap is not None and snap.engine is not None and snap.engine.dir:
            return snap.engine.dir
        return repo.engine_dir or C.STUDIO_HARNESS_DIR

    def _require_label(self, req: PlanRequest) -> str:
        """The label the engine slugifies into the record directory.

        Derived from the objective when the request omits it, but only when the derivation is non-empty:
        an objective written in Chinese (the fixtures have them) slugifies to nothing, and a silent
        fallback label would put an unrelated name on the user's intent directory forever.
        """
        label = (req.label or "").strip().lower() or _slugify(req.objective or "")
        if not label:
            raise StudioError("bad_body", "a label is required", details={"key": "label"})
        if len(label) > C.MAX_LABEL_CHARS or not LABEL_RE.match(label):
            raise StudioError(
                "bad_body",
                f"a label is at most {MAX_LABEL_WORDS} lowercase kebab-case words",
                details={"key": "label", "value": label},
            )
        return label

    @staticmethod
    def _require_arguments(req: PlanRequest) -> str:
        """Build the ``--arguments`` token, refusing before the lease anything the engine would die() on.

        The engine is still the authority on ``<document>`` pastes; this only moves its verdict earlier,
        to where the answer can be a 400 naming the field instead of a refused transaction with an
        ``ERROR_LOGGED`` row behind it (see ``_document_marker_reason``).
        """
        objective = (req.objective or "").strip()
        if not objective:
            raise StudioError("bad_body", "an objective is required", details={"key": "objective"})
        context = (req.context or "").strip()
        arguments = objective + (_ARGUMENTS_JOINER + context if context else "")
        reason, offset = _document_marker_reason(arguments)
        if reason is not None:
            key = "context" if context and offset >= len(objective) + len(_ARGUMENTS_JOINER) else "objective"
            raise StudioError("bad_body", f"the {key} has {reason}", details={"key": key, "reason": reason})
        return arguments

    # ---- verification ------------------------------------------------------

    def _verify_created(
        self, root: Path, space: str, before: set[str]
    ) -> tuple[str, dict[str, Any]]:
        """Prove on disk that exactly one new intent exists, is registered, and is the cursor."""
        after = set(self._reader.list_intent_dirs(root, space))
        fresh = sorted(after - before)
        if len(fresh) != 1:
            raise StudioError(
                "state_inconsistent",
                "intent-create did not produce exactly one new record",
                details={"created": fresh},
            )
        intent_dir = fresh[0]
        row = self._registry_row(root, space, intent_dir)
        cursor, _dangling = self._reader.active_intent(root, space)
        read = self._reader.read_state(root, space, intent_dir)
        verified = {
            "state_present": read is not None,
            "registry_row": row is not None,
            "cursor_set": cursor == intent_dir,
            "state_version": read[1].state_version if read is not None else None,
        }
        missing = [key for key in ("state_present", "registry_row", "cursor_set") if not verified[key]]
        if missing:
            raise StudioError(
                "state_inconsistent",
                "the new intent is not fully on disk",
                details={"intent_dir": intent_dir, "verified": verified, "missing": missing},
            )
        return intent_dir, verified

    def _verify_recomposed(
        self, root: Path, space: str, intent_dir: str, proposal: RecomposeProposal
    ) -> dict[str, Any]:
        """Re-read the state file and report which requested rows actually moved."""
        read = self._reader.read_state(root, space, intent_dir)
        if read is None:
            raise StudioError("state_inconsistent", "the state file is unreadable after recompose",
                              details={"intent_dir": intent_dir})
        _snapshot, state = read
        rows = {row.slug: row for row in state.stages}
        return {
            "state_present": True,
            "state_version": state.state_version,
            "skipped": {slug: not _row_selected(rows[slug]) for slug in proposal.skip if slug in rows},
            "added": {slug: _row_selected(rows[slug]) for slug in proposal.add if slug in rows},
            "missing_rows": [slug for slug in (*proposal.skip, *proposal.add) if slug not in rows],
        }

    def _registry_row(self, root: Path, space: str, intent_dir: str) -> Any:
        return match_intent_row(self._reader.registry(root, space), intent_dir)

    async def _summary(
        self, repo: "RepoRecord", space: str, intent_dir: str
    ) -> "IntentSummary | None":
        """A best-effort summary of the affected intent; never the reason a mutation reports failure.

        The engine work is already committed by the time this runs, so a read that trips over an
        unreadable file must not turn a successful operation into a 500.
        """
        try:
            snap = await asyncio.to_thread(self._projection.snapshot, repo, space, intent_dir)
            return self._projection.summarize(snap)
        except StudioError:
            return None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _row_selected(row: "StageRow") -> bool:
    """Whether the state file has this stage in the plan.

    ``[S]`` is a jump-skip and ``SKIP: …`` is a recompose/scope skip; both mean "not part of the run".
    A row with no suffix at all (older writers) counts as selected — the engine's default is to execute.
    """
    if row.state == "skipped":
        return False
    suffix = (row.suffix or "").strip().upper()
    return not suffix.startswith("SKIP")


def _effective_review_class(node: "StageNode | None", caps: tuple[str | None, str | None]) -> str:
    """``min(stage class, scope cap, request cap)`` by rank (§1.15).

    A stage with a reviewer but no declared class (every 32-stage graph) counts as ``advisory``: the
    reviewer runs, so the review work is real even though the older graph does not name its intensity.
    """
    if node is None:
        return "none"
    base = node.review_class if node.review_class in REVIEW_CAPS else ("advisory" if node.reviewer else "none")
    rank = REVIEW_RANK[base]
    for cap in caps:
        if cap in REVIEW_RANK:
            rank = min(rank, REVIEW_RANK[cap])
    return REVIEW_CAPS[rank]


def _best_lock(reasons: Sequence[str | None]) -> str | None:
    present = [r for r in reasons if r]
    if not present:
        return None
    for candidate in _LOCK_PRECEDENCE:
        for reason in present:
            if reason == candidate or (candidate == _REQUIRED_BY_PREFIX and reason.startswith(candidate)):
                return reason
    return present[0]


def _required_by(
    graph: Sequence["StageNode"],
    enabled: Mapping[str, bool],
    satisfied: set[str],
    project_type: str | None,
    *,
    allowed_gaps: set[tuple[str, str]] | None = None,
) -> dict[str, str]:
    """``{stage: the stage that would starve without it}``.

    A stage is dependency-locked when it is the ONLY producer on the selected path of an artifact another
    selected stage requires — exactly the condition the engine's strict validator would turn into an
    error if the stage were dropped. Naming one consumer is enough: the lock reason is a single string,
    and listing every consumer would lengthen the label without changing what the user may do.
    """
    on_path = {slug for slug, is_on in enabled.items() if is_on} | satisfied
    producers = _producers_of(graph)
    out: dict[str, str] = {}
    for node in graph:
        if not enabled.get(node.slug):
            continue
        for consume in node.consumes:
            if not consume.required or _consume_skipped(consume, project_type):
                continue
            if allowed_gaps and (node.slug, consume.artifact) in allowed_gaps:
                continue
            available = [p for p in producers.get(consume.artifact, ()) if p in on_path]
            sole = available[0] if len(available) == 1 else None
            # A completed producer keeps its artifacts on disk, so dropping it from the plan starves
            # nobody; only a producer that still has to run is load-bearing.
            if sole is not None and sole not in satisfied and sole not in out:
                out[sole] = node.slug
    return out


def _producers_of(graph: Sequence["StageNode"]) -> dict[str, tuple[str, ...]]:
    """``{artifact: producing stages}``, declared and optional outputs alike (``graph.producersOf``)."""
    out: dict[str, list[str]] = {}
    for node in graph:
        for artifact in (*node.produces, *node.optional_produces):
            out.setdefault(artifact, []).append(node.slug)
    return {artifact: tuple(slugs) for artifact, slugs in out.items()}


def _consume_skipped(consume: Any, project_type: str | None) -> bool:
    """Whether the engine would skip this consume for the project type (``validateGrid``)."""
    conditional = getattr(consume, "conditional_on", None)
    if not conditional or not project_type:
        return False
    return str(conditional).strip().lower() != project_type.strip().lower()


def _starvations(
    graph: Sequence["StageNode"], on_path: set[str], project_type: str | None
) -> set[tuple[str, str]]:
    """``{(consumer, artifact)}`` for every required input no on-path stage produces."""
    producers = _producers_of(graph)
    out: set[tuple[str, str]] = set()
    for node in graph:
        if node.slug not in on_path:
            continue
        for consume in node.consumes:
            if not consume.required or _consume_skipped(consume, project_type):
                continue
            if not any(p in on_path for p in producers.get(consume.artifact, ())):
                out.add((node.slug, consume.artifact))
    return out


def _anchor_stage_from(stages: Sequence[PlanStage], *, use_base: bool) -> str | None:
    """The walking-skeleton anchor: the first selected Construction stage in graph order.

    ``use_base`` picks the composition on disk (``in_grid``) rather than the proposed one, so the two
    calls answer "where is the anchor now" and "where would it be".
    """
    for stage in stages:
        if stage.phase != _ANCHOR_PHASE:
            continue
        if stage.in_grid if use_base else stage.enabled:
            return stage.slug
    return None


def _dedupe_issues(issues: Sequence[PlanIssue]) -> list[PlanIssue]:
    """One entry per (code, slugs): the per-slug pass and the plan's own issues can name the same row."""
    seen: set[tuple[str, tuple[str, ...]]] = set()
    out: list[PlanIssue] = []
    for issue in issues:
        key = (issue.code, issue.slugs)
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


def _slug_list(values: Sequence[str]) -> tuple[str, ...]:
    """Deduplicated, order-preserving stage slugs. A malformed slug is refused, not silently dropped."""
    out: list[str] = []
    for value in values or ():
        slug = str(value).strip()
        if not slug:
            continue
        if not C.STAGE_SLUG_RE.match(slug):
            raise StudioError("bad_param", "not a stage slug", details={"value": slug})
        if slug not in out:
            out.append(slug)
    return tuple(out)


def _recompose_argv(skip: Sequence[str], add: Sequence[str]) -> tuple[str, ...]:
    """The verb and flags a confirmation will run.

    The interpreter path and the repository path are omitted on purpose: they are absolute host paths,
    and this tuple is rendered in a browser.
    """
    argv: list[str] = ["aidlc-utility.ts", "recompose"]
    if skip:
        argv += ["--skip", ",".join(skip)]
    if add:
        argv += ["--add", ",".join(add)]
    return tuple(argv)


def _slugify(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return "-".join(words[:MAX_LABEL_WORDS])


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StudioError("bad_body", "expected a string", details={"value": str(value)})
    stripped = value.strip()
    return stripped or None

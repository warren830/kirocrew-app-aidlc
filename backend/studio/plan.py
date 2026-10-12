"""The plan a user is about to commit to, and the engine verbs that may change one (§1.15).

Studio reads the engine's graph, grid and state and changes them only through the engine's own verbs under an
admin lease, verifying on disk. No toggle is offered for a stage the engine would not move; only starvation a
request *introduces* is an error (stock grids ship starved); recompose flips pending stages ahead of the cursor.
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
from .aidlc_reader import REVIEW_CAPS, effective_review_class, match_intent_row
from .errors import StudioError
from .estimates import Estimate, EstimateService
from .wire import WireRecord

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

DEPTHS = ("Minimal", "Standard", "Comprehensive")
TEST_STRATEGIES = DEPTHS

LOCK_ALWAYS, LOCK_COMPLETED, LOCK_CURRENT, LOCK_AT_GATE = "always", "completed", "current", "at_gate"
LOCK_BEHIND_CURSOR, LOCK_NOT_IN_GRAPH = "behind_cursor", "not_in_graph"
_REQUIRED_BY_PREFIX = "required_by:"


def lock_required_by(slug: str) -> str:
    return f"{_REQUIRED_BY_PREFIX}{slug}"


#: Which lock the UI shows when several apply: history first, since "always" only restates the graph.
_LOCK_PRECEDENCE = (LOCK_NOT_IN_GRAPH, LOCK_COMPLETED, LOCK_CURRENT, LOCK_AT_GATE, LOCK_BEHIND_CURSOR,
                    LOCK_ALWAYS, _REQUIRED_BY_PREFIX)
#: A completed stage's artifacts are on disk, so it satisfies a dependency unselected; a skipped one does not.
_DEPENDENCY_SATISFIED_STATES = ("completed",)
#: The marks the engine's ``recompose`` guard refuses to re-shape.
_FROZEN_STATES = ("completed", "skipped", "in_progress", "awaiting_approval", "revising")
#: ``recompose_not_allowed`` and ``skeleton_anchor`` name engine refusals the §1.15 list cannot express.
PLAN_ISSUE_CODES = ("dependency_missing", "required_stage_disabled", "unknown_stage", "behind_cursor",
                    "frozen_stage", "scope_unknown", "depth_invalid", "recompose_not_allowed", "skeleton_anchor")
#: The phase whose first selected stage anchors the walking-skeleton gate.
_ANCHOR_PHASE = "construction"
DIFF_REASON_OVERRIDE = "override"
#: A label becomes the record's directory name: two or three kebab-case words (§1.15).
LABEL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){0,2}$")
MAX_LABEL_WORDS = 3
#: ``recompose`` needs a Running intent that is not in autonomous construction (the plan could move under it).
RECOMPOSE_REQUIRED_STATUS = "Running"
AUTONOMOUS_MODE = "autonomous"
OP_INTENT_CREATE, OP_RECOMPOSE = "intent_create", "recompose"
OP_SCOPE_CHANGE, OP_CONFIG_CHANGE = "scope_change", "config_change"
_ARGUMENTS_JOINER = "\n\n"
_DOCUMENT_OPEN, _DOCUMENT_CLOSE = "<document>", "</document>"


def _document_marker_reason(text: str) -> tuple[str | None, int]:
    """Why the engine would refuse this ``--arguments`` paste, and where (``(None, 0)`` if fine). Mirrors the
    engine's own checks in order, so a typo is a 400 naming the field instead of a refused transaction."""
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
        return f"a pasted {_DOCUMENT_OPEN} block and no directions outside it", start
    return None, 0


def _sha256_json(payload: Any, **dumps: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, **dumps).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanRequest(WireRecord):
    """What the user asked for. ``repo_id`` is the path parameter, not part of the JSON body (§2.3)."""

    _wire_keys = ("space", "scope", "depth", "test_strategy", "review_cap", "project_type", "overrides", "objective",
                  "label", "context")
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

    @classmethod
    def from_json(cls, body: Mapping[str, Any] | None, *, repo_id: str) -> "PlanRequest":
        """Refuse shapes as ``bad_body``, never coerce: a repaired value runs a workflow nobody asked for."""
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
            repo_id=repo_id, space=_opt_str(data.get("space")) or C.DEFAULT_SPACE,
            scope=_opt_str(data.get("scope")) or "", depth=_opt_str(data.get("depth")) or "",
            test_strategy=_opt_str(data.get("test_strategy")), review_cap=_opt_str(data.get("review_cap")),
            project_type=_opt_str(data.get("project_type")), overrides=overrides,
            objective=_opt_str(data.get("objective")), label=_opt_str(data.get("label")),
            context=_opt_str(data.get("context")),
        )


@dataclass(frozen=True, slots=True)
class PlanStage(WireRecord):
    """One row of the plan matrix (§3.1). ``in_grid`` is the composition as it stands (grid, or state file), so
    ``in_grid != enabled`` means "this request moved it"; ``mode``/``summary_confirmation`` feed the estimator."""

    _wire_keys = ("slug", "number", "name", "phase", "execution", "in_grid", "enabled", "locked", "lock_reason",
                  "gate", "review_class", "reviewer", "per_unit", "produces", "consumes", "depends_on",
                  "conditional_on", "state")
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


@dataclass(frozen=True, slots=True)
class ExactCounts(WireRecord):
    """Counts, not predictions: kept apart from ``Estimate`` so the UI can never label one as the other."""

    stages: int
    gates: int
    artifacts: int
    review_intensity: dict[str, int]
    assumes_units: int | None


@dataclass(frozen=True, slots=True)
class PlanIssue(WireRecord):
    """Why a plan cannot be committed as asked: a code and params, never English prose (C11)."""

    code: str
    slugs: tuple[str, ...]
    message_key: str
    params: dict[str, Any]

    @classmethod
    def of(cls, code: str, *slugs: str, **params: Any) -> "PlanIssue":
        if code not in PLAN_ISSUE_CODES:  # pragma: no cover - guarded by test_plan.py
            raise StudioError("internal_error", f"unknown plan issue code {code!r}", details={"code": code})
        return cls(code=code, slugs=tuple(slugs), message_key=f"plan.issue.{code}", params=dict(params))


@dataclass(frozen=True, slots=True)
class PlanDiffEntry(WireRecord):
    slug: str
    from_enabled: bool
    to_enabled: bool
    reason: str


@dataclass(frozen=True, slots=True)
class EffectivePlan(WireRecord):
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

    def stage(self, slug: str) -> PlanStage | None:
        return next((entry for entry in self.stages if entry.slug == slug), None)

    def digest(self) -> str:
        """What ``confirm_plan_digest`` binds (§2.3): the request and the selection, never the estimate."""
        return _sha256_json({
            "request": self.request.to_json(), "stages": [[s.slug, s.enabled] for s in self.stages],
            "graph_stage_count": self.graph_stage_count,
        }, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class RecomposeProposal(WireRecord):
    """A recompose the user can confirm, with the argv it will produce and every reason it may not run."""

    _wire_keys = ("intent_key", "current_stage", "skip", "add", "plan", "argv_preview", "allowed", "refusals")
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
        """Binds the confirmation to the cursor, both lists and the state bytes the preview was computed from."""
        return _sha256_json({
            "intent_key": self.intent_key, "current_stage": self.current_stage, "skip": list(self.skip),
            "add": list(self.add), "state_sha256": self.state_sha256,
            "stages": [[s.slug, s.enabled] for s in self.plan.stages],
        }, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class RecomposeResult:
    """An applied recompose (§1.15); the route picks what it returns (§2.3)."""

    repo_id: str
    intent_key: str
    transaction_id: str
    skip: tuple[str, ...]
    add: tuple[str, ...]
    engine: "EngineResult"
    verified: dict[str, Any]
    summary: "IntentSummary | None"


@dataclass(frozen=True, slots=True)
class IntentCreateResult(WireRecord):
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


@dataclass(frozen=True, slots=True)
class PlanCatalog:
    """Scopes, stages (graph order) and the upper-cased grid between them, for the Advisor's plan draft."""

    scopes: tuple["ScopeMeta", ...]
    stages: tuple["StageNode", ...]
    grid: dict[str, dict[str, str]]
    engine_version: str | None

    def grid_row(self, scope: str) -> dict[str, bool]:
        """``{slug: selected}``: the grid row, else the stages listing the scope, else ``{}`` (no such scope)."""
        row = self.grid.get(scope)
        if row:
            return {slug: value == "EXECUTE" for slug, value in row.items()}
        if any(scope in node.scopes for node in self.stages):
            return {node.slug: scope in node.scopes for node in self.stages}
        return {}


class PlanService:
    """Reads the installed plan; changes it only through the engine's own verbs, under an admin lease."""

    def __init__(
        self, reader: "AidlcReader", engine: "EngineRunner", scheduler: "RepoScheduler",
        registry: "RepoRegistry", projection: "Projection", storage: "Storage", estimates: EstimateService,
        activity: "ActivityProjector", events: "EventLog", clock: "C.Clock", ids: Any,
    ) -> None:
        self._reader = reader
        self.engine = engine
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
        """Scopes, graph and grid, read as ``effective_plan`` reads them; no graph is ``not_installed``."""
        root = Path(repo.canonical_path)
        graph = self._reader.read_stage_graph(root, engine_dir)
        if not graph:
            raise StudioError("not_installed", "no stage graph is installed in this repository",
                              details={"repo_id": repo.repo_id})
        grid = self._reader.read_scope_grid(root, engine_dir)
        scopes = self._reader.read_scopes(root, engine_dir)
        return PlanCatalog(
            scopes=tuple(scopes), stages=tuple(graph),
            grid={str(scope): {str(slug): str(value).upper() for slug, value in row.items()}
                  for scope, row in grid.items()},
            engine_version=repo.installed_engine_version,
        )

    def effective_plan(
        self, repo: "RepoRecord", engine_dir: str, req: PlanRequest, *, snap: "IntentSnapshot | None" = None
    ) -> EffectivePlan:
        """The plan a request describes. With ``snap`` the baseline is the intent's state file and the graph
        comes from the same snapshot (one coherent read); without it, the scope grid."""
        root = Path(repo.canonical_path)
        read = self._reader
        graph = list(snap.graph) if snap is not None and snap.graph else read.read_stage_graph(root, engine_dir)
        grid = dict(snap.grid) if snap is not None and snap.grid else read.read_scope_grid(root, engine_dir)
        scopes = list(snap.scopes) if snap is not None and snap.scopes else read.read_scopes(root, engine_dir)

        issues: list[PlanIssue] = []
        scope = req.scope
        scope_meta = next((s for s in scopes if s.name == scope), None)
        grid_row = {slug: str(value).upper() for slug, value in (grid.get(scope) or {}).items()}
        if not grid_row and scope_meta is None:
            known = [s.name for s in scopes] or sorted(grid)
            issues.append(PlanIssue.of("scope_unknown", scope=scope, known=known))
        depth = self._resolve_depth(req, scope_meta, issues)
        request_cap = self._resolve_cap(req.review_cap, "review_cap", issues)
        scope_cap = self._resolve_cap(scope_meta.review_cap if scope_meta else None, None, issues)
        stages = self._build_stages(
            graph=graph, grid_row=grid_row, rows=self._state_rows(snap), snap=snap, req=req, scope=scope,
            caps=(scope_cap, request_cap), current_index=self._current_index(snap, graph), issues=issues,
        )
        issues.extend(self.validate(stages, graph, project_type=req.project_type))
        units = self._units(repo, snap)
        engine_version = snap.engine.engine_version if snap is not None and snap.engine is not None else None
        return EffectivePlan(
            request=req, scope_meta=scope_meta, stages=stages, exact=self._exact_counts(stages, units),
            estimate=self._estimates.estimate(stages, depth=depth, units=units, scope=scope),
            diff=tuple(PlanDiffEntry(s.slug, s.in_grid, s.enabled, DIFF_REASON_OVERRIDE)
                       for s in stages if s.in_grid != s.enabled),
            issues=tuple(issues), valid=not issues,
            products=tuple(sorted({a for s in stages if s.enabled for a in s.produces})),
            graph_stage_count=len(graph), engine_version=engine_version or repo.installed_engine_version,
        )

    def validate(
        self, stages: Sequence[PlanStage], graph: Sequence["StageNode"], *, project_type: str | None = None
    ) -> list[PlanIssue]:
        """The starvation this request would INTRODUCE (the engine's strict-diff rule); with ``project_type``
        unknown the conditional consumes are checked, as the engine skips one only for a known, different type."""
        satisfied = {s.slug for s in stages if s.state in _DEPENDENCY_SATISFIED_STATES}
        before = _starvations(graph, {s.slug for s in stages if s.in_grid} | satisfied, project_type)
        after = _starvations(graph, {s.slug for s in stages if s.enabled} | satisfied, project_type)
        producers = _producers_of(graph)
        return [
            PlanIssue.of("dependency_missing", consumer, *producers.get(artifact, ()), stage=consumer,
                         artifact=artifact, producers=list(producers.get(artifact, ())))
            for consumer, artifact in sorted(after - before)
        ]

    def recompose_preview(
        self, repo: "RepoRecord", snap: "IntentSnapshot", *, skip: Sequence[str], add: Sequence[str]
    ) -> RecomposeProposal:
        """What ``recompose --skip … --add …`` would do, and every engine guard that refuses it, in order."""
        wanted_skip = _slug_list(skip)
        wanted_add = _slug_list(add)
        req = self._request_from_state(repo, snap, skip=wanted_skip, add=wanted_add)
        plan = self.effective_plan(repo, self.engine_dir(repo, snap), req, snap=snap)
        refusals: list[PlanIssue] = []
        for slug in (*wanted_skip, *wanted_add):
            refusals.extend(self._recompose_refusals(plan, slug))
        # A recompose applies as a whole, so every plan issue (an ignored flip, a starved consumer) refuses it.
        refusals.extend(plan.issues)
        guards = (self._recompose_lane_refusal(snap), self._anchor_refusal(plan))
        refusals.extend(r for r in guards if r is not None)
        refusals = _dedupe_issues(refusals)
        applied_skip = tuple(s for s in wanted_skip if s not in wanted_add)
        applied_add = tuple(wanted_add)
        return RecomposeProposal(
            intent_key=snap.intent_key,
            current_stage=snap.state.current_stage if snap.state is not None else None,
            skip=applied_skip, add=applied_add, plan=plan,
            argv_preview=_recompose_argv(applied_skip, applied_add),
            allowed=bool(plan.valid and not refusals and (applied_skip or applied_add)),
            refusals=tuple(refusals), state_sha256=snap.state_sha256,
        )

    # ---- mutations (async; engine verbs under an admin lease) ---------------

    async def create_intent(
        self, repo: "RepoRecord", req: PlanRequest, *, blocking_findings: Sequence["Finding"] = (),
        confirm_plan_digest: str | None = None,
    ) -> IntentCreateResult:
        """Create an intent, apply its confirmed overrides, compile its runtime, and verify all on disk. Never
        sends a prompt: the new intent is ``Idle`` until a human presses Run (FR-RUN-001)."""
        self.require_ready(repo)
        blocking = [f for f in blocking_findings if getattr(f, "severity", "") == "blocking"]
        if blocking:
            raise StudioError("state_inconsistent",
                              "the repository has blocking findings; resolve them before creating an intent",
                              details={"findings": [f.to_json() for f in blocking]})
        engine_dir = self.engine_dir(repo, None)
        space = req.space or C.DEFAULT_SPACE
        root = Path(repo.canonical_path)
        active_space = await asyncio.to_thread(self._reader.active_space, root)
        if space != active_space:
            # `intent-create` always writes into the active space; switching spaces is its own operation.
            raise StudioError("bad_param", "an intent can only be created in the active space",
                              details={"space": space, "active_space": active_space})
        plan = await asyncio.to_thread(self.effective_plan, repo, engine_dir, req)
        if not plan.valid:
            raise StudioError("plan_invalid", "the plan cannot be committed as described",
                              details={"issues": [i.to_json() for i in plan.issues]})
        if confirm_plan_digest is not None and confirm_plan_digest != plan.digest():
            raise StudioError("plan_invalid", "the plan changed since it was previewed",
                              details={"reason": "plan_digest_mismatch", "expected": plan.digest()})
        label = self._require_label(req)
        arguments = self._require_arguments(req)

        transaction_id = self._ids.new("tx")
        grant = await self._scheduler.acquire_admin(
            repo, operation_type=OP_INTENT_CREATE, transaction_id=transaction_id
        )
        lease = {"lease_generation": grant.generation, "repo_id": repo.repo_id}
        try:
            # Listed under the lease, so nothing but this verb can add the one new record.
            before = set(await asyncio.to_thread(self._reader.list_intent_dirs, root, space))
            result = await self.engine.run(
                "utility.intent_create", root, engine_dir, scope=req.scope, arguments=arguments, label=label,
                depth=req.depth or "", test_strategy=req.test_strategy or "", review=req.review_cap or "",
                **lease,
            )
            self.require_engine_ok(result)
            intent_dir, verified = await asyncio.to_thread(self._verify_created, root, space, before)
            phase = "plan_composition"
            try:
                if req.overrides:
                    # Creation takes scope settings, not per-stage switches: apply the confirmed delta, re-check.
                    def diff() -> Any:
                        return asyncio.to_thread(
                            self._created_override_diff, root, space, intent_dir, req.overrides
                        )

                    changes = await diff()
                    if changes:
                        composed = await self.engine.run(
                            "utility.recompose", root, engine_dir,
                            skip=",".join(change.slug for change in changes if not change.to_enabled),
                            add=",".join(change.slug for change in changes if change.to_enabled),
                            dir_name=intent_dir, slug=space, **lease,
                        )
                        self.require_engine_ok(composed)
                        remaining = await diff()
                        if remaining:
                            raise StudioError(
                                "state_inconsistent", "created plan does not match confirmed overrides",
                                details={"reason": "plan_overrides_not_applied",
                                         "diff": [change.to_json() for change in remaining]},
                            )
                    verified["plan_overrides"] = dict(req.overrides)
                phase = "runtime_compile"
                self.require_engine_ok(await self.engine.run("runtime.compile", root, engine_dir, **lease))
                runtime_path = f"{self._reader.record_rel(space, intent_dir)}/runtime-graph.json"
                runtime_snap = await asyncio.to_thread(self._reader.read_artifact, root, runtime_path)
                try:
                    graph = json.loads(runtime_snap.bytes) if runtime_snap is not None else None
                except (ValueError, UnicodeDecodeError):
                    graph = None
                if not isinstance(graph, dict) or not isinstance(graph.get("stages"), list):
                    raise StudioError("state_inconsistent",
                                      "runtime compilation did not produce a readable graph",
                                      details={"relpath": runtime_path})
                verified["runtime_graph_present"] = True
            except StudioError as exc:
                # The intent exists: say so, and which phase failed (compile-only repair is safe only after
                # composition was verified).
                raise StudioError(
                    exc.code,
                    f"Intent {intent_dir} was created, but its confirmed plan or runtime graph is not ready: "
                    f"{exc.message}",
                    details={**exc.details, "intent_created": True, "intent_dir": intent_dir,
                             "intent_key": C.intent_key_for(space, intent_dir), "space": space,
                             "creation_failed_phase": phase},
                ) from exc
        finally:
            await self._scheduler.release(grant)

        row = await asyncio.to_thread(self._registry_row, root, space, intent_dir)
        summary = await self._summary(repo, space, intent_dir)
        intent_key = C.intent_key_for(space, intent_dir)
        await self._activity.record(
            kind="intent.created", repo_id=repo.repo_id, space=space, intent_dir=intent_dir,
            params={"scope": req.scope, "depth": req.depth, "test_strategy": req.test_strategy,
                    "review_cap": req.review_cap, "label": label, "transaction_id": transaction_id,
                    "state_version": verified.get("state_version")},
        )
        await self._announce(repo, intent_key, summary)
        return IntentCreateResult(
            repo_id=repo.repo_id, space=space, intent_dir=intent_dir, intent_key=intent_key,
            uuid=row.uuid if row is not None else None, slug=row.slug if row is not None else None,
            transaction_id=transaction_id, engine=result, verified=verified, summary=summary,
        )

    async def recompose(
        self, repo: "RepoRecord", snap: "IntentSnapshot", *, skip: Sequence[str], add: Sequence[str],
        proposal_digest: str,
    ) -> RecomposeResult:
        """Apply a previewed recompose, rebuilt from a FRESH ``snap`` and bound to the confirmed digest."""
        self.require_ready(repo)
        engine_dir = self.engine_dir(repo, snap)
        proposal = await asyncio.to_thread(self.recompose_preview, repo, snap, skip=list(skip), add=list(add))
        if proposal.digest() != proposal_digest:
            raise StudioError("plan_invalid", "the plan changed since this proposal was previewed",
                              details={"reason": "proposal_digest_mismatch", "expected": proposal.digest()})
        if not proposal.allowed:
            raise StudioError("recompose_not_allowed", "this recompose cannot be applied",
                              details={"refusals": [r.to_json() for r in proposal.refusals]})
        transaction_id = self._ids.new("tx")
        root = Path(repo.canonical_path)
        grant = await self._scheduler.acquire_admin(
            repo, operation_type=OP_RECOMPOSE, transaction_id=transaction_id
        )
        try:
            # The record is named explicitly (both flags): an omitted selector lets the engine resolve the
            # calling session's binding, which is how a confirmed change lands on a record nobody was shown.
            result = await self.engine.run(
                "utility.recompose", root, engine_dir, skip=",".join(proposal.skip), add=",".join(proposal.add),
                dir_name=snap.intent_dir, slug=snap.space, lease_generation=grant.generation,
                repo_id=repo.repo_id,
            )
            self.require_engine_ok(result)
            verified = await asyncio.to_thread(
                self._verify_recomposed, root, snap.space, snap.intent_dir, proposal
            )
        finally:
            await self._scheduler.release(grant)
        summary = await self._summary(repo, snap.space, snap.intent_dir)
        await self._announce(repo, snap.intent_key, summary)
        return RecomposeResult(
            repo_id=repo.repo_id, intent_key=snap.intent_key, transaction_id=transaction_id, skip=proposal.skip,
            add=proposal.add, engine=result, verified=verified, summary=summary,
        )

    def settings_preview(
        self, repo: "RepoRecord", snap: "IntentSnapshot", changes: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Preview ``scope-change``/``config-change``, including the engine's row rebuild: 2.7.1 cannot keep
        gate/revision marks across a scope change, so those are refused before the engine touches the record."""
        self.require_ready(repo)
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
            if key != "scope" and value not in DEPTHS:
                raise StudioError("bad_param", f"{key} must be one of the engine's settings",
                                  details={"field": key, "value": value, "allowed": list(DEPTHS)})
            after[key] = value
        if after["scope"] not in snap.grid:
            raise StudioError("plan_invalid", "unknown scope", details={"scope": after["scope"]})
        if not before["scope"] or not before["depth"]:
            raise StudioError("state_inconsistent", "the current scope or depth is missing")
        if after["depth"] not in DEPTHS or after["test_strategy"] not in DEPTHS:
            raise StudioError("state_inconsistent", "the recorded depth or test strategy is invalid")
        changing_scope = after["scope"] != before["scope"]
        # All three resolved settings always travel: a scope change alone must not reset depth/test strategy.
        rows = {row.slug: row for row in state.stages}
        grid = snap.grid[after["scope"]]
        stages = []
        refusals = []
        if changing_scope and (state.autonomy_mode or "").lower() == AUTONOMOUS_MODE:
            refusals.append("autonomous")
        if changing_scope and (set(rows) - {node.slug for node in snap.graph} or len(rows) != len(state.stages)):
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
        argv = self.engine.build_argv(
            self.engine.verb_for(verb), Path(repo.canonical_path), self.engine_dir(repo, snap), **kwargs
        )
        proposal = {
            "repo_id": repo.repo_id, "intent_key": snap.intent_key,
            "before": before, "after": after, "stages": stages,
            "scopes": sorted(snap.grid), "allowed": not refusals,
            "refusals": list(dict.fromkeys(refusals)), "argv_preview": argv,
            "verb_key": verb,
            "selection": {"space": snap.space, "intent_dir": snap.intent_dir},
        }
        digest = _sha256_json({
            "proposal": proposal, "state_sha256": snap.state_sha256,
            "identity": repo.resolved_identity, "engine_dir": self.engine_dir(repo, snap),
            "graph": [node.to_json() for node in snap.graph], "grid": snap.grid,
        })
        return {"proposal": proposal, "proposal_digest": digest}

    async def change_settings(
        self, repo: "RepoRecord", snap: "IntentSnapshot", changes: Mapping[str, Any], *, proposal_digest: str,
    ) -> dict[str, Any]:
        """Re-read and compare under the lease, then verify settings and every existing mark on disk."""
        self.require_ready(repo)
        grant = await self._scheduler.acquire_admin(
            repo, operation_type=OP_SCOPE_CHANGE if "scope" in changes else OP_CONFIG_CHANGE,
            transaction_id=None,
        )
        try:
            # The handler's snapshot is only an address: re-check identity and idleness before every step that
            # could act on a record another holder advanced while this request waited.
            await self._require_current(repo)
            fresh = await asyncio.to_thread(self._projection.snapshot, repo, snap.space, snap.intent_dir)
            answer = await asyncio.to_thread(self.settings_preview, repo, fresh, changes)
            if answer["proposal_digest"] != proposal_digest:
                raise StudioError("plan_invalid", "the settings proposal is stale",
                                  details={"reason": "proposal_digest_mismatch"})
            proposal = answer["proposal"]
            if not proposal["allowed"]:
                raise StudioError("plan_invalid", "this change cannot be applied",
                                  details={"refusals": proposal["refusals"]})
            # 2.7.1's appendAuditEvent still resolves the session/cursor: pin the cursor first, so the settings
            # event cannot land in another intent's audit.
            await self._require_current(repo)
            cursor = await asyncio.to_thread(
                self.engine.switch_cursor_sync, Path(repo.canonical_path), self.engine_dir(repo, fresh),
                fresh.space, fresh.intent_dir, C.attr(fresh.row, "uuid"),
                lease_generation=grant.generation, repo_id=repo.repo_id,
            )
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
            await self._require_current(repo)
            result = await self.engine.run(
                proposal["verb_key"], Path(repo.canonical_path), self.engine_dir(repo, fresh),
                dir_name=fresh.intent_dir, slug=fresh.space, lease_generation=grant.generation,
                repo_id=repo.repo_id, **kwargs,
            )
            self.require_engine_ok(result)
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
                (state.row(row["slug"]) is not None and _row_selected(state.row(row["slug"])) == row["after"])
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
        await self._announce(repo, snap.intent_key, await self._summary(repo, snap.space, snap.intent_dir))
        return {"ok": True, "result": result.to_json(),
                "verified": {"settings": actual, "stages_preserved": preserved}}

    # ---- stage construction ------------------------------------------------

    def _build_stages(
        self, *, graph: Sequence["StageNode"], grid_row: Mapping[str, str], rows: Mapping[str, "StageRow"],
        snap: "IntentSnapshot | None", req: PlanRequest, scope: str, caps: tuple[str | None, str | None],
        current_index: int | None, issues: list[PlanIssue],
    ) -> tuple[PlanStage, ...]:
        base: dict[str, bool] = {}
        locks: dict[str, str | None] = {}
        entries: list[tuple["StageNode | None", "StageRow | None", str]] = []
        for index, node in enumerate(graph):
            row = rows.get(node.slug)
            if snap is not None:
                # The state file is the plan that will run; the grid only says what the scope composes.
                selected = row is not None and _row_selected(row)
            elif grid_row:
                selected = grid_row.get(node.slug) == "EXECUTE"
            else:
                selected = scope in node.scopes
            base[node.slug] = selected
            locks[node.slug] = self._base_lock(node, row, index, current_index, selected)
            entries.append((node, row, node.slug))
        graph_slugs = {node.slug for node in graph}
        for slug, row in rows.items():
            if slug not in graph_slugs:
                # A row from another engine generation still runs, so it is shown — locked, being unknowable.
                base[slug] = _row_selected(row)
                locks[slug] = LOCK_NOT_IN_GRAPH
                entries.append((None, row, slug))

        satisfied = {slug for slug, row in rows.items() if row.state in _DEPENDENCY_SATISFIED_STATES}
        base_required_by = _required_by(graph, base, satisfied, req.project_type)
        enabled = dict(base)
        for slug, wanted in req.overrides.items():
            if slug not in base:
                issues.append(PlanIssue.of("unknown_stage", slug, stage=slug))
            elif bool(wanted) != base[slug]:
                if locks.get(slug) is not None:
                    issues.append(self._override_refused(slug, locks[slug]))
                else:
                    enabled[slug] = bool(wanted)

        # An optional producer stays removable when that only restores a gap the preset already permits (the
        # strict-diff rule). A producer is refused removal only while a selected consumer still needs it, and
        # restoring one can restore its own dependencies, so repeat until nothing more is refused.
        baseline_gaps = _starvations(
            graph, {slug for slug, selected in base.items() if selected} | satisfied, req.project_type
        )
        restored = True
        while restored:
            restored = False
            for slug in base_required_by:
                if enabled[slug]:
                    continue
                needed_by = _required_by(graph, {**enabled, slug: True}, satisfied, req.project_type,
                                         allowed_gaps=baseline_gaps).get(slug)
                if needed_by is not None:
                    enabled[slug] = True
                    issues.append(self._override_refused(slug, lock_required_by(needed_by)))
                    restored = True
        required_by = _required_by(graph, enabled, satisfied, req.project_type, allowed_gaps=baseline_gaps)

        stages: list[PlanStage] = []
        for node, row, slug in entries:
            is_enabled = bool(enabled.get(slug))
            # Only the baseline makes an ALWAYS stage mandatory; a stage added in this preview stays removable.
            reasons = [locks.get(slug)]
            if slug in required_by and is_enabled:
                reasons.append(lock_required_by(required_by[slug]))
            reason = _best_lock(reasons)
            phase = (node.phase if node is not None else (row.phase if row is not None else "")) or ""
            known = node is not None
            stages.append(PlanStage(
                slug=slug, number=node.number if known else "", name=node.name if known else slug, phase=phase,
                execution=node.execution if known else "", in_grid=bool(base.get(slug)), enabled=is_enabled,
                locked=reason is not None, lock_reason=reason, gate=phase != "initialization",
                review_class=effective_review_class(node, caps), reviewer=node.reviewer if known else None,
                per_unit=bool(node.per_unit) if known else False,
                produces=tuple(node.produces) if known else (),
                consumes=tuple(c.artifact for c in node.consumes) if known else (),
                depends_on=tuple(node.requires_stage) if known else (),
                conditional_on=node.condition if known and node.execution == "CONDITIONAL" else None,
                state=row.state if row is not None else None, mode=node.mode if known else "",
                summary_confirmation=node.summary_confirmation if known else None,
            ))
        return tuple(stages)

    @staticmethod
    def _base_lock(
        node: "StageNode", row: "StageRow | None", index: int, current_index: int | None, selected: bool
    ) -> str | None:
        """The lock before overrides. ``always`` binds only a selected stage: stock poc/bugfix grids SKIP four
        ALWAYS stages, and locking those would forbid ``recompose --add units-generation``."""
        if row is not None:
            if row.state in ("completed", "skipped"):
                return LOCK_COMPLETED
            if row.state == "in_progress":
                return LOCK_CURRENT
            if row.state in ("awaiting_approval", "revising"):
                return LOCK_AT_GATE
            # Graph POSITION, not stage number: that is what the engine compares (`idx <= currentIdx`).
            if current_index is not None and index <= current_index:
                return LOCK_BEHIND_CURSOR
        if node.execution == "ALWAYS" and selected:
            return LOCK_ALWAYS
        return None

    @staticmethod
    def _override_refused(slug: str, reason: str) -> PlanIssue:
        if reason == LOCK_BEHIND_CURSOR:
            return PlanIssue.of("behind_cursor", slug, stage=slug)
        if reason == LOCK_ALWAYS or reason.startswith(_REQUIRED_BY_PREFIX):
            return PlanIssue.of("required_stage_disabled", slug, stage=slug, lock_reason=reason)
        return PlanIssue.of("frozen_stage", slug, stage=slug, lock_reason=reason)

    @staticmethod
    def _exact_counts(stages: Sequence[PlanStage], units: int | None) -> ExactCounts:
        unit_count = units if units is not None else 1
        enabled = [s for s in stages if s.enabled]
        intensity = {name: 0 for name in REVIEW_CAPS}
        for stage in enabled:
            intensity[stage.review_class or "none"] += 1
        return ExactCounts(
            stages=len(enabled), gates=sum(1 for s in enabled if s.gate),
            artifacts=sum(len(s.produces) * (unit_count if s.per_unit else 1) for s in enabled),
            review_intensity=intensity, assumes_units=None if units is not None else 1,
        )

    # ---- request / state plumbing ------------------------------------------

    def _request_from_state(
        self, repo: "RepoRecord", snap: "IntentSnapshot", *, skip: Sequence[str], add: Sequence[str]
    ) -> PlanRequest:
        """The request a live intent already represents, with ``skip``/``add`` applied on top."""
        state = snap.state
        config = dict(state.scope_config) if state is not None else {}
        project = dict(state.project) if state is not None else {}
        overrides: dict[str, bool] = {slug: False for slug in skip}
        overrides.update({slug: True for slug in add})
        return PlanRequest(
            repo_id=repo.repo_id, space=snap.space, scope=(project.get("Scope") or "").strip(),
            depth=(config.get("Depth") or "").strip(),
            test_strategy=(config.get("Test Strategy") or "").strip() or None,
            review_cap=(config.get("Review Override") or "").strip() or None,
            project_type=(project.get("Project Type") or "").strip() or None,
            overrides=overrides, objective=None, label=None, context=None,
        )

    @staticmethod
    def _state_rows(snap: "IntentSnapshot | None") -> dict[str, "StageRow"]:
        if snap is None or snap.state is None:
            return {}
        return {row.slug: row for row in snap.state.stages}

    @staticmethod
    def _current_index(snap: "IntentSnapshot | None", graph: Sequence["StageNode"]) -> int | None:
        """The cursor's graph position; ``None`` (no ``behind_cursor`` lock) when this graph lacks the stage."""
        if snap is None or snap.state is None or not snap.state.current_stage:
            return None
        return next((i for i, node in enumerate(graph) if node.slug == snap.state.current_stage), None)

    def _units(self, repo: "RepoRecord", snap: "IntentSnapshot | None") -> int | None:
        """How many units of work the per-unit stages fan out over, or ``None`` when unknown."""
        if snap is None:
            return None
        try:
            units = self._reader.list_units(Path(repo.canonical_path), snap.space, snap.intent_dir)
        except StudioError:
            return None
        return len(units) or None

    @staticmethod
    def _resolve_depth(req: PlanRequest, scope_meta: "ScopeMeta | None", issues: list[PlanIssue]) -> str:
        """The depth the estimate uses; an empty request depth means "the scope's own"."""
        if req.depth and req.depth in DEPTHS:
            return req.depth
        if req.depth:
            issues.append(PlanIssue.of("depth_invalid", depth=req.depth, allowed=list(DEPTHS)))
        if scope_meta is not None and scope_meta.depth in DEPTHS:
            return str(scope_meta.depth)
        return "Standard"

    @staticmethod
    def _resolve_cap(value: str | None, key: str | None, issues: list[PlanIssue]) -> str | None:
        """A review cap or ``None``. An unknown one is dropped (only ever *more* review) and reported as
        ``depth_invalid``, silently when it came off a state file (``key is None``): the user never typed it."""
        if not value:
            return None
        if value in REVIEW_CAPS:
            return value
        if key is not None:
            issues.append(PlanIssue.of("depth_invalid", field=key, value=value, allowed=list(REVIEW_CAPS)))
        return None

    # ---- recompose refusals ------------------------------------------------

    @staticmethod
    def _recompose_refusals(plan: EffectivePlan, slug: str) -> list[PlanIssue]:
        """The engine's per-flip guards: compiled, pending, ahead of the cursor. Not "is it locked": a
        contradicted lock already made a plan issue, and ``--add`` of a running stage is an accepted no-op."""
        stage = plan.stage(slug)
        if stage is None or stage.lock_reason == LOCK_NOT_IN_GRAPH:
            return [PlanIssue.of("unknown_stage", slug, stage=slug)]
        if stage.state in _FROZEN_STATES:
            return [PlanIssue.of("frozen_stage", slug, stage=slug, state=stage.state)]
        if stage.lock_reason == LOCK_BEHIND_CURSOR:
            return [PlanIssue.of("behind_cursor", slug, stage=slug)]
        return []

    @staticmethod
    def _anchor_refusal(plan: EffectivePlan) -> PlanIssue | None:
        """Moving the first selected Construction stage would relocate the walking-skeleton gate; the engine
        refuses that, so the preview does too."""
        before = _anchor_stage_from(plan.stages, use_base=True)
        after = _anchor_stage_from(plan.stages, use_base=False)
        if before == after:
            return None
        moved = after or before
        return PlanIssue.of("skeleton_anchor", *(s for s in (moved,) if s), anchor_before=before,
                            anchor_after=after)

    @staticmethod
    def _recompose_lane_refusal(snap: "IntentSnapshot") -> PlanIssue | None:
        state = snap.state
        status = (state.status if state is not None else None) or ""
        autonomy = ((state.autonomy_mode if state is not None else None) or "").strip().lower()
        if status != RECOMPOSE_REQUIRED_STATUS or autonomy == AUTONOMOUS_MODE:
            return PlanIssue.of("recompose_not_allowed", status=status,
                                required_status=RECOMPOSE_REQUIRED_STATUS, autonomy_mode=autonomy or None)
        return None

    # ---- engine plumbing ---------------------------------------------------

    @staticmethod
    def require_engine_ok(result: "EngineResult") -> None:
        """A refused or timed-out verb is never treated as applied."""
        if result.ok:
            return
        if result.timed_out:
            raise StudioError("engine_unavailable", "the AI-DLC engine did not finish in time",
                              details={"verb_key": result.verb_key, "duration_ms": result.duration_ms})
        raise StudioError("state_inconsistent", "the AI-DLC engine refused the operation",
                          details={"verb_key": result.verb_key, "exit_code": result.exit_code,
                                   "stderr": result.stderr[-2000:]})

    @staticmethod
    def require_ready(repo: "RepoRecord") -> None:
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

    async def _require_current(self, repo: "RepoRecord") -> None:
        await asyncio.to_thread(self._registry.require_current_identity, repo)
        self._scheduler.require_host_idle(repo)

    @staticmethod
    def engine_dir(repo: "RepoRecord", snap: "IntentSnapshot | None") -> str:
        if snap is not None and snap.engine is not None and snap.engine.dir:
            return snap.engine.dir
        return repo.engine_dir or C.STUDIO_HARNESS_DIR

    @staticmethod
    def _require_label(req: PlanRequest) -> str:
        """The record's directory label, derived from the objective only when that yields something (a Chinese
        objective slugifies to nothing, and a silent fallback would misname the intent forever)."""
        label = (req.label or "").strip().lower() or _slugify(req.objective or "")
        if not label:
            raise StudioError("bad_body", "a label is required", details={"key": "label"})
        if len(label) > C.MAX_LABEL_CHARS or not LABEL_RE.match(label):
            raise StudioError("bad_body", f"a label is at most {MAX_LABEL_WORDS} lowercase kebab-case words",
                              details={"key": "label", "value": label})
        return label

    @staticmethod
    def _require_arguments(req: PlanRequest) -> str:
        """The ``--arguments`` token, refusing before the lease anything the engine would die() on."""
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

    def _created_override_diff(
        self, root: Path, space: str, intent_dir: str, overrides: Mapping[str, bool]
    ) -> tuple[PlanDiffEntry, ...]:
        """The confirmed switches the engine's actual stage suffixes do not match yet."""
        read = self._reader.read_state(root, space, intent_dir)
        if read is None or read[0].unstable:
            raise StudioError("state_inconsistent", "created state is not stably readable",
                              details={"intent_dir": intent_dir})
        rows = {row.slug: row for row in read[1].stages}
        missing = sorted(set(overrides) - set(rows))
        if missing:
            raise StudioError("state_inconsistent", "created state is missing confirmed stages",
                              details={"intent_dir": intent_dir, "missing_rows": missing})
        return tuple(
            PlanDiffEntry(slug, _row_selected(rows[slug]), wanted, DIFF_REASON_OVERRIDE)
            for slug, wanted in overrides.items()
            if _row_selected(rows[slug]) != wanted
        )

    def _verify_created(self, root: Path, space: str, before: set[str]) -> tuple[str, dict[str, Any]]:
        """Prove on disk that exactly one new intent exists, is registered, and is the cursor."""
        fresh = sorted(set(self._reader.list_intent_dirs(root, space)) - before)
        if len(fresh) != 1:
            raise StudioError("state_inconsistent", "intent-create did not produce exactly one new record",
                              details={"created": fresh})
        intent_dir = fresh[0]
        row = self._registry_row(root, space, intent_dir)
        cursor, _dangling = self._reader.active_intent(root, space)
        read = self._reader.read_state(root, space, intent_dir)
        verified = {
            "state_present": read is not None, "registry_row": row is not None,
            "cursor_set": cursor == intent_dir,
            "state_version": read[1].state_version if read is not None else None,
        }
        missing = [key for key in ("state_present", "registry_row", "cursor_set") if not verified[key]]
        if missing:
            raise StudioError("state_inconsistent", "the new intent is not fully on disk",
                              details={"intent_dir": intent_dir, "verified": verified, "missing": missing})
        return intent_dir, verified

    def _verify_recomposed(
        self, root: Path, space: str, intent_dir: str, proposal: RecomposeProposal
    ) -> dict[str, Any]:
        """Re-read the state file and report which requested rows actually moved."""
        read = self._reader.read_state(root, space, intent_dir)
        if read is None:
            raise StudioError("state_inconsistent", "the state file is unreadable after recompose",
                              details={"intent_dir": intent_dir})
        state = read[1]
        rows = {row.slug: row for row in state.stages}
        return {
            "state_present": True, "state_version": state.state_version,
            "skipped": {slug: not _row_selected(rows[slug]) for slug in proposal.skip if slug in rows},
            "added": {slug: _row_selected(rows[slug]) for slug in proposal.add if slug in rows},
            "missing_rows": [slug for slug in (*proposal.skip, *proposal.add) if slug not in rows],
        }

    def _registry_row(self, root: Path, space: str, intent_dir: str) -> Any:
        return match_intent_row(self._reader.registry(root, space), intent_dir)

    async def _summary(self, repo: "RepoRecord", space: str, intent_dir: str) -> "IntentSummary | None":
        """Best effort: the engine work is committed, so an unreadable file must not turn success into a 500."""
        try:
            snap = await asyncio.to_thread(self._projection.snapshot, repo, space, intent_dir)
            return self._projection.summarize(snap)
        except StudioError:
            return None

    async def _announce(self, repo: "RepoRecord", intent_key: str, summary: "IntentSummary | None") -> None:
        await self._events.publish("intent.updated", {
            "repo_id": repo.repo_id, "intent_key": intent_key,
            "operational_state": summary.operational_state if summary is not None else "Idle",
        })


def _row_selected(row: "StageRow") -> bool:
    """``[S]`` (jump-skip) and ``SKIP: …`` (recompose/scope) are out of the run; no suffix means selected."""
    if row.state == "skipped":
        return False
    return not (row.suffix or "").strip().upper().startswith("SKIP")


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
    graph: Sequence["StageNode"], enabled: Mapping[str, bool], satisfied: set[str], project_type: str | None, *,
    allowed_gaps: set[tuple[str, str]] | None = None,
) -> dict[str, str]:
    """``{stage: a selected stage that would starve without it}`` — the sole on-path producer of a required
    input, which is exactly what the engine's strict validator would refuse to drop."""
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
            # A completed producer keeps its artifacts, so dropping it starves nobody.
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
    """Whether the engine skips this consume for the project type (``validateGrid``)."""
    conditional = getattr(consume, "conditional_on", None)
    if not conditional or not project_type:
        return False
    return str(conditional).strip().lower() != project_type.strip().lower()


def _starvations(
    graph: Sequence["StageNode"], on_path: set[str], project_type: str | None
) -> set[tuple[str, str]]:
    """``{(consumer, artifact)}`` for every required input no on-path stage produces."""
    producers = _producers_of(graph)
    return {
        (node.slug, consume.artifact)
        for node in graph
        if node.slug in on_path
        for consume in node.consumes
        if consume.required and not _consume_skipped(consume, project_type)
        and not any(p in on_path for p in producers.get(consume.artifact, ()))
    }


def _anchor_stage_from(stages: Sequence[PlanStage], *, use_base: bool) -> str | None:
    """The walking-skeleton anchor on disk (``use_base``) or as proposed: the first selected Construction."""
    return next((s.slug for s in stages if s.phase == _ANCHOR_PHASE and (s.in_grid if use_base else s.enabled)),
                None)


def _dedupe_issues(issues: Sequence[PlanIssue]) -> list[PlanIssue]:
    """One entry per (code, slugs): the per-slug pass and the plan's own issues can name the same row."""
    seen: set[tuple[str, tuple[str, ...]]] = set()
    out: list[PlanIssue] = []
    for issue in issues:
        if (issue.code, issue.slugs) not in seen:
            seen.add((issue.code, issue.slugs))
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
    """The verb and flags a confirmation will run, minus host paths (this tuple is rendered in a browser)."""
    argv: list[str] = ["aidlc-utility.ts", "recompose"]
    if skip:
        argv += ["--skip", ",".join(skip)]
    if add:
        argv += ["--add", ",".join(add)]
    return tuple(argv)


def _slugify(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", (text or "").lower())[:MAX_LABEL_WORDS])


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StudioError("bad_body", "expected a string", details={"value": str(value)})
    return value.strip() or None

import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve } from "node:path";
import {
  asksForHelp,
  errorMessage,
  getField,
  parseCheckboxes,
  parseStateStageSuffixes,
  printUsageAndExit,
  readStateFile,
  recordDir,
  resolveBoltDag,
  resolveProjectDir,
  usesStageLevelPerUnitArtifacts,
} from "./aidlc-lib.ts";
import { aidlcDispatcherInvocation, sensorFireUsage } from "./aidlc-runtime-paths.ts";

const VALID_STATUSES = new Set(["OK", "GAP", "ORPHAN", "Deferred", "N/A"]);

interface CoverageEntry {
  id: string;
  status: string;
  target?: string;
}

interface TraceabilityData {
  stage?: string;
  unit?: string;
  upstream_ids: string[];
  coverage: CoverageEntry[];
  reverse: CoverageEntry[];
}

interface Result {
  pass: boolean;
  gaps: string[];
  orphans: string[];
  missing_from_table: string[];
  missing_from_upstream_ids: string[];
  invalid_entries: string[];
  invalid_targets: string[];
  findings_count: number;
  reason?: string;
}

interface Flags {
  outputPath?: string;
  stage?: string;
  writeUpstream?: boolean;
}

interface UnitContext {
  unitName: string;
  units: string[];
  unitIds: Map<string, string>;
}

type RequirementKind = "functional" | "non-functional";

interface RecordedRequirement {
  id: string;
  kind: RequirementKind;
  of?: string;
}

interface UpstreamResolution {
  ids: Set<string>;
  reasons: string[];
  extraGaps: string[];
  unitContext?: UnitContext;
  storyAssignments?: Map<string, Set<string>>;
  recorded: Map<string, RecordedRequirement> | null;
  // The requirement kinds requirements.md gave no ID of, for want of a record:
  // the table lists them as written.
  unrecorded: RequirementKind[];
  requirementsFile: string;
  /** The whole selected source when `ids` is one unit's share of it. */
  sourceIds?: Set<string>;
  // IDs a stage may declare and cover without being required to (2.10.0: NFR rows in a
  // stories-less units-generation map). Still bounded by what the upstream artifact names.
  optionalIds?: Set<string>;
}

interface StoryAssignments {
  assignments: Map<string, Set<string>>;
  reason?: string;
  /** The map was read but maps no source ID to a declared unit. */
  empty: boolean;
}

const ID_PATTERNS = {
  FR: /\bFR\d+(?:\.\d+)?\b/g,
  NFR: /\bNFR\d+\b(?!\.\d)/g,
  NFR_DETAIL: /\bNFR\d+\.\d+\b/g,
  US: /\bUS\d+\.\d+\b/g,
  AC: /\bAC\d+\.\d+\.\d+\b/g,
  BR: /\bBR\d+\.\d+\b/g,
};

function parseFlags(argv: string[]): Flags {
  const out: Flags = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--stage") out.stage = argv[++i];
    else if (arg === "--output-path") out.outputPath = argv[++i];
    else if (arg === "--write-upstream") out.writeUpstream = true;
  }
  return out;
}

function fail(message: string): never {
  process.stderr.write(`aidlc-sensor-traceability: ${message}\n`);
  process.exit(1);
}

function emit(result: Result): void {
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

function failedResult(reason: string): Result {
  return {
    pass: false,
    gaps: [],
    orphans: [],
    missing_from_table: [],
    missing_from_upstream_ids: [],
    invalid_entries: [],
    invalid_targets: [],
    findings_count: 1,
    reason,
  };
}

function normalizePath(path: string): string {
  return path.replace(/\\/g, "/");
}

function parseJson(content: string): unknown {
  try {
    return JSON.parse(content) as unknown;
  } catch {
    return undefined;
  }
}

function validateEntry(value: unknown, field: string, index: number): string | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return `${field}[${index}] must be an object`;
  }
  const entry = value as Record<string, unknown>;
  if (typeof entry.id !== "string" || entry.id.trim() === "") {
    return `${field}[${index}].id must be a non-empty string`;
  }
  if (typeof entry.status !== "string" || entry.status.trim() === "") {
    return `${field}[${index}].status must be a non-empty string`;
  }
  if ("target" in entry && entry.target !== undefined && typeof entry.target !== "string") {
    return `${field}[${index}].target must be a string when present`;
  }
  return null;
}

function validateTraceability(value: unknown): { ok: true; data: TraceabilityData } | { ok: false; reason: string } {
  if (value === undefined) return { ok: false, reason: "invalid JSON in traceability file" };
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return { ok: false, reason: "traceability.json must contain a JSON object" };
  }
  const obj = value as Record<string, unknown>;
  if (!Array.isArray(obj.upstream_ids) || !obj.upstream_ids.every((id) => typeof id === "string" && id.trim() !== "")) {
    return { ok: false, reason: "upstream_ids must be an array of non-empty strings" };
  }
  if (!Array.isArray(obj.coverage)) {
    return { ok: false, reason: "coverage must be an array" };
  }
  if ("reverse" in obj && !Array.isArray(obj.reverse)) {
    return { ok: false, reason: "reverse must be an array when present" };
  }
  const reverse = (obj.reverse ?? []) as unknown[];
  for (const [field, entries] of [["coverage", obj.coverage], ["reverse", reverse]] as const) {
    for (let i = 0; i < entries.length; i++) {
      const error = validateEntry(entries[i], field, i);
      if (error) return { ok: false, reason: error };
    }
  }
  if ("stage" in obj && obj.stage !== undefined && typeof obj.stage !== "string") {
    return { ok: false, reason: "stage must be a string when present" };
  }
  if ("unit" in obj && obj.unit !== undefined && typeof obj.unit !== "string") {
    return { ok: false, reason: "unit must be a string when present" };
  }
  return {
    ok: true,
    data: {
      stage: obj.stage as string | undefined,
      unit: obj.unit as string | undefined,
      upstream_ids: obj.upstream_ids as string[],
      coverage: obj.coverage as CoverageEntry[],
      reverse: reverse as CoverageEntry[],
    },
  };
}

function readText(path: string): { content: string | null; reason?: string } {
  try {
    if (!existsSync(path)) return { content: null, reason: `required upstream artifact is missing: ${path}` };
    if (!statSync(path).isFile()) return { content: null, reason: `required upstream artifact is not a file: ${path}` };
    return { content: readFileSync(path, "utf-8") };
  } catch (error) {
    return { content: null, reason: `cannot read upstream artifact ${path}: ${errorMessage(error)}` };
  }
}

function extractIds(content: string, patterns: RegExp[]): Set<string> {
  const ids = new Set<string>();
  for (const pattern of patterns) {
    const regex = new RegExp(pattern.source, "g");
    for (const match of content.matchAll(regex)) ids.add(match[0]);
  }
  return ids;
}

function tracedParent(id: string): string | undefined {
  return /^((?:FR|NFR)\d+)\.\d+$/.exec(id)?.[1];
}

// A recorded ID's requirement is its `of`; an ID the record does not hold (an
// NFRx.y from NFR Requirements) is read by its form.
function requirementOf(id: string, recorded: Map<string, RecordedRequirement> | null): string | undefined {
  return recorded?.has(id) ? recorded.get(id)?.of : tracedParent(id);
}

// A requirement whose sub-requirements are traced too (FR1 beside FR1.1, or at
// Code Generation NFR1 beside the Unit's NFR1.1) is traced through them, so it
// needs no row of its own; a sub-requirement left out is still reported.
function withoutTracedParents(ids: Set<string>, recorded: Map<string, RecordedRequirement> | null = null): Set<string> {
  const parents = new Set<string>();
  for (const id of ids) {
    const parent = requirementOf(id, recorded);
    if (parent) parents.add(parent);
  }
  return new Set([...ids].filter((id) => !parents.has(id)));
}

// The FR and NFR IDs Requirements Analysis recorded in its own traceability.json,
// spelled as requirements.md spells them, or null when it recorded none (work
// begun before it did) or the record's shape is not exact. Every reader then
// takes a table's requirements as the table lists them, and the ones the FR and
// NFR patterns find in requirements.md, as every check read them before the
// record.
function recordedRequirements(docsDir: string): Map<string, RecordedRequirement> | null {
  const read = readText(join(docsDir, "inception", "requirements-analysis", "traceability.json"));
  const value = read.content === null ? undefined : parseJson(read.content);
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const ids = (value as { ids?: unknown }).ids;
  if (!Array.isArray(ids)) return null;
  const recorded = new Map<string, RecordedRequirement>();
  for (const entry of ids) {
    if (entry === null || typeof entry !== "object" || Array.isArray(entry)) return null;
    const { id, kind, of } = entry as Record<string, unknown>;
    if (typeof id !== "string" || id.trim() === "" || recorded.has(id.trim())) return null;
    if (kind !== "functional" && kind !== "non-functional") return null;
    if (of !== undefined && (typeof of !== "string" || of.trim() === "")) return null;
    recorded.set(id.trim(), { id: id.trim(), kind, ...(typeof of === "string" ? { of: of.trim() } : {}) });
  }
  for (const entry of recorded.values()) {
    if (entry.of !== undefined && (entry.of === entry.id || !recorded.has(entry.of))) return null;
  }
  return recorded;
}

const KIND_PATTERN: Record<RequirementKind, RegExp> = { functional: ID_PATTERNS.FR, "non-functional": ID_PATTERNS.NFR };

// The requirements of these kinds a stage traces: the recorded ones, or without
// a record the ones the table lists, as written, and the ones the pattern finds
// in requirements.md. Its reasons are not said: a team's own form (FR-1) finds
// none and is the table's to list.
function addRequirements(result: UpstreamResolution, kinds: RequirementKind[], declared: readonly string[]): Set<string> {
  if (result.recorded === null) {
    const ids = new Set(declared);
    for (const kind of kinds) {
      const written = idsFromFile(result.requirementsFile, [KIND_PATTERN[kind]], "requirements.md").ids;
      if (written.size === 0 && !result.unrecorded.includes(kind)) result.unrecorded.push(kind);
      for (const id of written) ids.add(id);
    }
    return addSource(result, { ids });
  }
  const ids = [...result.recorded.values()].filter((entry) => kinds.includes(entry.kind)).map((entry) => entry.id);
  return addSource(result, { ids: new Set(ids) });
}

// The requirements of one kind a story map may assign to Units: the recorded
// ones, or without a record the ones the pattern finds in requirements.md, with
// its reason. A table's own IDs are never among them, so neither the table nor
// the map can add a requirement that Requirements Analysis did not write.
function requirementSource(result: UpstreamResolution, kind: RequirementKind): { ids: Set<string>; reason?: string } {
  if (result.recorded === null) return idsFromFile(result.requirementsFile, [KIND_PATTERN[kind]], "requirements.md");
  return { ids: new Set([...result.recorded.values()].filter((entry) => entry.kind === kind).map((entry) => entry.id)) };
}

// A criterion `AC{g}.{s}.{c}` belongs to the story `US{g}.{s}`.
function criteriaOfStories(storiesContent: string, stories: Set<string>): Set<string> {
  const criteria = new Set<string>();
  for (const ac of extractIds(storiesContent, [ID_PATTERNS.AC])) {
    const [group, story] = ac.slice(2).split(".");
    if (stories.has(`US${group}.${story}`)) criteria.add(ac);
  }
  return criteria;
}

function idsFromFile(path: string, patterns: RegExp[], label: string): { ids: Set<string>; reason?: string } {
  const read = readText(path);
  if (read.content === null) return { ids: new Set(), reason: read.reason };
  const ids = extractIds(read.content, patterns);
  if (ids.size === 0) return { ids, reason: `${label} contains no traceable IDs: ${path}` };
  return { ids };
}

function extractUnitName(outputPath: string): string | null {
  const normalized = normalizePath(outputPath);
  const match = normalized.match(/\/construction\/([^/]+)\/[^/]+\/traceability\.json$/);
  return match?.[1] ?? null;
}

// When the plan has no Units, the engine writes every per-Unit stage's
// artifacts directly under construction/<stage>/, with no Unit segment. A path
// that names the stage's own folder is that location, so any per-Unit stage
// with a resolver below is covered without a second stage list.
function isZeroUnitOutput(stage: string, outputPath: string): boolean {
  return stage !== "" &&
    normalizePath(outputPath).endsWith(`/construction/${stage}/traceability.json`);
}

// Per-Unit artifacts live under construction/<unit>/<stage>/; a zero-Unit run
// keeps the stage-level construction/<stage>/ location.
function constructionDir(docsDir: string, unit: string, stage: string): string {
  return unit ? join(docsDir, "construction", unit, stage) : join(docsDir, "construction", stage);
}

function unitLabel(unit: string): string {
  return unit ? `unit "${unit}"` : "the zero-Unit run";
}

function readPlanState(projectDir: string): string | null {
  try {
    return readStateFile(projectDir);
  } catch {
    return null;
  }
}

// The engine places per-Unit artifacts at stage level whenever the work has no
// Units (the approved plan skips Units Generation, or the work passed it
// without running it), and then ignores any Unit DAG left on disk (for example
// after a scope change). Follow the plan when the state records it.
function planSkipsUnits(stateContent: string | null): boolean {
  if (stateContent === null || !parseStateStageSuffixes(stateContent).has("units-generation")) return false;
  return usesStageLevelPerUnitArtifacts(getField(stateContent, "Scope"), stateContent);
}

// True when the approved plan (a SKIP suffix) or the run (a skipped [S]
// checkbox) left the stage out. No readable state, or no Stage Progress line,
// counts as having run, so that stage's missing files still fail closed.
function stageDidNotRun(stateContent: string | null, slug: string): boolean {
  if (stateContent === null) return false;
  if (parseStateStageSuffixes(stateContent).get(slug) === "SKIP") return true;
  return parseCheckboxes(stateContent).some((entry) => entry.slug === slug && entry.state === "skipped");
}

const NFR_REQUIREMENT_FILES = ["performance-requirements.md", "security-requirements.md", "scalability-requirements.md", "reliability-requirements.md"];
const NFR_DESIGN_FILES = ["performance-design.md", "security-design.md", "scalability-design.md", "reliability-design.md", "logical-components.md"];

// NFRx.y IDs from one NFR stage's artifacts, or null when none of them exists.
function detailedNfrIds(dir: string, names: string[]): Set<string> | null {
  let sawFile = false;
  const ids = new Set<string>();
  for (const name of names) {
    const path = join(dir, name);
    if (!existsSync(path)) continue;
    sawFile = true;
    const read = readText(path);
    if (read.content !== null) {
      for (const id of extractIds(read.content, [ID_PATTERNS.NFR_DETAIL])) ids.add(id);
    }
  }
  return sawFile ? ids : null;
}

// Inline code is formatting, not part of a name: a cell such as
// `` `U1` / `u1-ledger`; `U2` / `u2-api` `` names U1 and U2 (#2488).
function markdownCells(line: string): string[] {
  if (!line.trimStart().startsWith("|") || /^\s*\|?[\s:|-]+\|?\s*$/.test(line)) return [];
  return line.split("|").slice(1, -1).map((cell) => cell.replaceAll("`", "").trim());
}

// The stage guidance names a Unit's directory `u{n}-{description}` while the
// edge block may name the Unit without that prefix (the engine names
// construction/<unit>/ after the edge block), so a cell names a Unit when it is
// the name, the backticked name, or the name behind a `u{n}-` prefix.
function namesUnit(cell: string, unit: string): boolean {
  const bare = cell.trim().replace(/^`(.*)`$/, "$1");
  return bare === unit || bare.replace(/^u\d+-/i, "") === unit;
}

function isTableSeparator(line: string): boolean {
  return line.trimStart().startsWith("|") && line.includes("-") && /^\s*\|?[\s:|-]+\|?\s*$/.test(line);
}

interface ColumnRoles {
  ids: number[];
  units: number[];
}

// Columns a story-map header names. Notes, rationale and dependency columns mention other IDs and
// units in passing; they never map one.
function columnRoles(header: string[]): ColumnRoles | null {
  const roles: ColumnRoles = { ids: [], units: [] };
  header.forEach((raw, index) => {
    const name = raw.replace(/[`*_]/g, " ").trim().toLowerCase();
    if (/\b(?:notes?|comments?|remarks?|rationale|reasons?|descriptions?|depend\w*|related|see also)\b/.test(name)) return;
    if (/\b(?:units?|director(?:y|ies))\b/.test(name)) roles.units.push(index);
    else if (/\b(?:stor(?:y|ies)|requirements?|upstream|ids?|frs?|uss?)\b/.test(name)) roles.ids.push(index);
  });
  return roles.ids.length > 0 && roles.units.length > 0 ? roles : null;
}

function unitIdMap(unitFile: string, units: string[]): Map<string, string> {
  const map = new Map<string, string>();
  const read = readText(unitFile);
  if (read.content === null) return map;
  for (const line of read.content.split(/\r?\n/)) {
    const cells = markdownCells(line);
    if (cells.length === 0) continue;
    const id = cells.flatMap((cell) => cell.match(/\bU\d+\b/gi) ?? [])[0]?.toUpperCase();
    if (!id) continue;
    const unit = units.find((candidate) => cells.some((cell) => namesUnit(cell, candidate)));
    if (unit) map.set(unit, id);
  }
  return map;
}

function tokenPresent(cell: string, token: string): boolean {
  const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(?:^|[\\s,;/])${escaped}(?:$|[\\s,;/])`, "i").test(cell);
}

// `sourceIds` follow the stage's source fallback: the US IDs of stories.md when
// it exists; otherwise the FR IDs, plus any NFR IDs the map may also carry, of
// requirements.md or Requirements Analysis' record.
function storyAssignments(
  storyMapPath: string,
  units: string[],
  ids: Map<string, string>,
  sourceIds: ReadonlySet<string>,
): StoryAssignments {
  const read = readText(storyMapPath);
  if (read.content === null) return { assignments: new Map(), reason: read.reason, empty: false };
  const assignments = new Map<string, Set<string>>();
  const lines = read.content.split(/\r?\n/);
  let roles: ColumnRoles | null = null;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const cells = markdownCells(line);
    if (cells.length === 0) {
      if (!line.trimStart().startsWith("|")) roles = null;
      continue;
    }
    if (isTableSeparator(lines[index + 1] ?? "")) {
      roles = columnRoles(cells);
      continue;
    }
    // Units may map FRs when user-stories was skipped. Only the selected
    // upstream artifact can authorize an ID; the map cannot invent one.
    // Without a recognised header, the first cell naming a source ID is the
    // row's ID cell, so a later note cannot assign the IDs it mentions.
    const idCells = roles
      ? roles.ids.map((column) => cells[column] ?? "")
      : cells.filter((cell) => [...extractIds(cell, [ID_PATTERNS.US, ID_PATTERNS.FR, ID_PATTERNS.NFR])]
        .some((id) => sourceIds.has(id))).slice(0, 1);
    const unitCells = roles ? roles.units.map((column) => cells[column] ?? "") : cells;
    const stories = [...extractIds(idCells.join(" | "), [ID_PATTERNS.US, ID_PATTERNS.FR, ID_PATTERNS.NFR])]
      .filter((id) => sourceIds.has(id));
    if (stories.length === 0) continue;
    for (const unit of units) {
      const aliases = [unit, ids.get(unit)].filter((value): value is string => value !== undefined);
      if (!unitCells.some((cell) => aliases.some((alias) => tokenPresent(cell, alias)) || namesUnit(cell, unit))) continue;
      for (const story of stories) {
        const mapped = assignments.get(story) ?? new Set<string>();
        mapped.add(unit);
        assignments.set(story, mapped);
      }
    }
  }
  return assignments.size === 0
    ? {
      assignments,
      reason: `unit-of-work-story-map.md contains no upstream-ID-to-unit mappings: ${storyMapPath}`,
      empty: true,
    }
    : { assignments, empty: false };
}

function unitShare(assignments: Map<string, Set<string>>, unit: string): Set<string> {
  return new Set([...assignments.entries()].filter(([, units]) => units.has(unit)).map(([id]) => id));
}

function resolveUnitContext(
  projectDir: string,
  outputPath: string,
  docsDir: string,
  stage: string,
  stateContent: string | null,
): { context?: UnitContext; reason?: string } {
  const unitName = extractUnitName(outputPath);
  if (!unitName && !isZeroUnitOutput(stage, outputPath)) {
    return { reason: `cannot derive the construction unit from output path: ${outputPath}` };
  }
  if (!unitName && planSkipsUnits(stateContent)) {
    return { context: { unitName: "", units: [], unitIds: new Map() } };
  }
  const dag = resolveBoltDag(projectDir);
  if (dag.state === "malformed") {
    return { reason: `unit-of-work-dependency.md is ${dag.reason}: ${dag.detail}` };
  }
  if (!unitName) {
    if (dag.state === "ok") {
      return { reason: `cannot derive the construction unit from output path while unit-of-work-dependency.md declares Units: ${outputPath}` };
    }
    return { context: { unitName: "", units: [], unitIds: new Map() } };
  }
  const units = dag.state === "ok" ? dag.units : [unitName];
  if (dag.state === "ok" && !units.includes(unitName)) {
    return { reason: `unit "${unitName}" is not declared in unit-of-work-dependency.md` };
  }
  return {
    context: {
      unitName,
      units,
      unitIds: unitIdMap(join(docsDir, "inception", "units-generation", "unit-of-work.md"), units),
    },
  };
}

function addSource(result: UpstreamResolution, source: { ids: Set<string>; reason?: string }): Set<string> {
  if (source.reason) result.reasons.push(source.reason);
  for (const id of source.ids) result.ids.add(id);
  return source.ids;
}

// Without stories the story map carries FR rows (and optional NFR rows) keyed to
// a Unit, the join Units Generation verifies; a Unit's own stage traces exactly
// its rows, so another Unit's requirements are never asked of it (#2282), though
// it may list one N/A. The rows map only requirementSource's IDs, so without a
// record a requirements.md that is missing, or names no FR, fails the stage
// instead of the map standing in for it. A map with no FR row (one written
// before requirement rows were read) scopes nothing: the requirements-wide set
// stays, as it does for a run with no Unit or no story map.
function addUnitRows(
  result: UpstreamResolution,
  storyMap: string,
  context: UnitContext,
  kinds: RequirementKind[],
  declared: readonly string[],
): void {
  const functional = requirementSource(result, "functional");
  if (functional.reason) {
    result.reasons.push(functional.reason);
    return;
  }
  const nonFunctional = requirementSource(result, "non-functional").ids;
  const mapped = storyAssignments(storyMap, context.units, context.unitIds, new Set([...functional.ids, ...nonFunctional]));
  if (mapped.reason && !mapped.empty) {
    result.reasons.push(mapped.reason);
    return;
  }
  if (![...mapped.assignments.keys()].some((id) => functional.ids.has(id))) {
    addRequirements(result, kinds, declared);
    return;
  }
  const traced = kinds.includes("non-functional") ? new Set([...functional.ids, ...nonFunctional]) : functional.ids;
  result.storyAssignments = mapped.assignments;
  result.sourceIds = traced;
  const own = [...unitShare(mapped.assignments, context.unitName)].filter((id) => traced.has(id));
  if (own.length === 0) result.reasons.push(`no requirements in unit-of-work-story-map.md map to unit "${context.unitName}"`);
  for (const id of own) result.ids.add(id);
}

// NFR Design traces NFR Requirements' NFRx.y IDs; Infrastructure Design traces
// NFR Design's, or NFR Requirements' when NFR Design did not run. Without NFR
// Requirements no NFRx.y IDs exist, so both trace the NFRn IDs in
// Requirements Analysis' record, as nfr-requirements itself does. A source
// stage that ran but left no artifacts still fails closed.
function resolveNfrUpstream(
  result: UpstreamResolution,
  stage: "nfr-design" | "infrastructure-design",
  docsDir: string,
  unit: string,
  declared: readonly string[],
  stateContent: string | null,
): void {
  const requirementsDir = constructionDir(docsDir, unit, "nfr-requirements");
  const requirementIds = detailedNfrIds(requirementsDir, NFR_REQUIREMENT_FILES);
  const requirementsRan = requirementIds !== null || !stageDidNotRun(stateContent, "nfr-requirements");
  let source = { label: "NFR requirement", dir: requirementsDir, ids: requirementIds, ran: requirementsRan };
  if (stage === "infrastructure-design") {
    const designDir = constructionDir(docsDir, unit, "nfr-design");
    const designIds = detailedNfrIds(designDir, NFR_DESIGN_FILES);
    if (designIds !== null || !stageDidNotRun(stateContent, "nfr-design")) {
      source = { label: "NFR design", dir: designDir, ids: designIds, ran: true };
    }
  }
  if (source.ids === null) {
    if (source.ran) {
      result.reasons.push(`required upstream ${source.label} artifacts are missing under ${source.dir}`);
    } else {
      addRequirements(result, ["non-functional"], declared);
    }
    return;
  }
  if (!requirementsRan) {
    addRequirements(result, ["non-functional"], declared);
    return;
  }
  for (const id of source.ids) result.ids.add(id);
  if (source.ids.size === 0) {
    result.reasons.push(`${source.label} artifacts for ${unitLabel(unit)} contain no NFRx.y IDs`);
  }
}

// `declared` is the table's own upstream_ids: with requirements.md's own, the
// requirements it traces when Requirements Analysis recorded none.
function resolveUpstream(stage: string, projectDir: string, outputPath: string, declared: readonly string[]): UpstreamResolution {
  const result: UpstreamResolution = { ids: new Set(), reasons: [], extraGaps: [], recorded: null, unrecorded: [], requirementsFile: "" };
  const docsDir = recordDir(projectDir);
  if (!docsDir) {
    result.reasons.push("cannot resolve the active intent record directory");
    return result;
  }
  result.recorded = recordedRequirements(docsDir);
  result.requirementsFile = join(docsDir, "inception", "requirements-analysis", "requirements.md");
  const stories = join(docsDir, "inception", "user-stories", "stories.md");
  const storyMap = join(docsDir, "inception", "units-generation", "unit-of-work-story-map.md");

  if (stage === "user-stories") {
    addRequirements(result, ["functional", "non-functional"], declared);
    return result;
  }
  if (stage === "domain-design") {
    if (existsSync(stories)) addSource(result, idsFromFile(stories, [ID_PATTERNS.US], "stories.md"));
    else addRequirements(result, ["functional"], declared);
    return result;
  }
  if (stage === "units-generation") {
    // The map may assign only IDs its source names: the stories, or without them
    // the requirements as requirementSource reads them, never the table's own.
    const hasStories = existsSync(stories);
    const sourceIds = addSource(
      result,
      hasStories ? idsFromFile(stories, [ID_PATTERNS.US], "stories.md") : requirementSource(result, "functional"),
    );
    const dag = resolveBoltDag(projectDir);
    if (dag.state === "malformed") {
      result.reasons.push(`unit-of-work-dependency.md is ${dag.reason}: ${dag.detail}`);
      return result;
    }
    if (dag.state === "none") {
      result.reasons.push("unit-of-work-dependency.md is missing; cannot verify traceability targets");
      return result;
    }
    const context: UnitContext = {
      unitName: "",
      units: dag.units,
      unitIds: unitIdMap(join(docsDir, "inception", "units-generation", "unit-of-work.md"), dag.units),
    };
    result.unitContext = context;
    // Without stories the required set stays FR-only, but a scope that also traces NFRs must not
    // have its correctly mapped NFR rows reported as unmapped targets (2.10.0), so the join may
    // also accept the NFR IDs Requirements Analysis wrote. The map still cannot invent one.
    const optionalIds = hasStories ? new Set<string>() : requirementSource(result, "non-functional").ids;
    result.optionalIds = optionalIds;
    const mapped = storyAssignments(storyMap, context.units, context.unitIds, new Set([...sourceIds, ...optionalIds]));
    if (mapped.reason) result.reasons.push(mapped.reason);
    result.storyAssignments = mapped.assignments;
    // A requirement no Unit implements is a gap: the source is always a list of
    // them here, since the table's own IDs are never part of it.
    for (const id of withoutTracedParents(sourceIds, result.recorded)) {
      if (!mapped.assignments.has(id)) result.extraGaps.push(id);
    }
    return result;
  }

  const stateContent = readPlanState(projectDir);
  const resolvedUnit = resolveUnitContext(projectDir, outputPath, docsDir, stage, stateContent);
  if (!resolvedUnit.context) {
    result.reasons.push(resolvedUnit.reason ?? "cannot resolve construction unit");
    return result;
  }
  result.unitContext = resolvedUnit.context;
  const unit = resolvedUnit.context.unitName;

  if (stage === "functional-design") {
    if (existsSync(stories) && existsSync(storyMap)) {
      const source = idsFromFile(stories, [ID_PATTERNS.US], "stories.md");
      if (source.reason) result.reasons.push(source.reason);
      const mapped = storyAssignments(storyMap, resolvedUnit.context.units, resolvedUnit.context.unitIds, source.ids);
      if (mapped.reason) result.reasons.push(mapped.reason);
      result.storyAssignments = mapped.assignments;
      const unitStories = unitShare(mapped.assignments, unit);
      if (unitStories.size === 0) {
        result.reasons.push(`no stories in unit-of-work-story-map.md map to unit "${unit}"`);
        return result;
      }
      const storyRead = readText(stories);
      if (storyRead.content === null) {
        result.reasons.push(storyRead.reason ?? `cannot read ${stories}`);
        return result;
      }
      result.sourceIds = extractIds(storyRead.content, [ID_PATTERNS.AC]);
      for (const ac of criteriaOfStories(storyRead.content, unitStories)) result.ids.add(ac);
      if (result.ids.size === 0) result.reasons.push(`stories mapped to unit "${unit}" contain no acceptance-criterion IDs`);
    } else if (unit && existsSync(storyMap)) {
      addUnitRows(result, storyMap, resolvedUnit.context, ["functional"], declared);
    } else {
      addRequirements(result, ["functional"], declared);
    }
    return result;
  }
  if (stage === "nfr-requirements") {
    // Without stories the story map may give a Unit its NFR rows, the join Code
    // Generation reads, read as Units Generation joins them (the FR and NFR IDs of
    // requirementSource). A Unit no NFR row names owes every NFR in
    // requirements.md, and marks one that does not apply N/A with its reason.
    if (unit && !existsSync(stories) && existsSync(storyMap)) {
      const nonFunctional = requirementSource(result, "non-functional").ids;
      const joined = new Set([...requirementSource(result, "functional").ids, ...nonFunctional]);
      const mapped = storyAssignments(storyMap, resolvedUnit.context.units, resolvedUnit.context.unitIds, joined);
      for (const id of unitShare(mapped.assignments, unit)) {
        if (nonFunctional.has(id)) result.ids.add(id);
      }
      if (result.ids.size > 0) return result;
    }
    addRequirements(result, ["non-functional"], declared);
    return result;
  }
  if (stage === "nfr-design" || stage === "infrastructure-design") {
    resolveNfrUpstream(result, stage, docsDir, unit, declared, stateContent);
    return result;
  }
  if (stage === "code-generation") {
    if (existsSync(stories)) {
      if (unit && existsSync(storyMap)) {
        const mapped = storyAssignments(
          storyMap, resolvedUnit.context.units, resolvedUnit.context.unitIds,
          idsFromFile(stories, [ID_PATTERNS.US], "stories.md").ids,
        );
        if (mapped.reason) result.reasons.push(mapped.reason);
        result.storyAssignments = mapped.assignments;
        const unitStories = unitShare(mapped.assignments, unit);
        const storyRead = readText(stories);
        if (storyRead.content !== null) {
          for (const ac of criteriaOfStories(storyRead.content, unitStories)) result.ids.add(ac);
        }
      } else {
        addSource(result, idsFromFile(stories, [ID_PATTERNS.AC], "stories.md"));
      }
    } else if (unit && existsSync(storyMap)) {
      addUnitRows(result, storyMap, resolvedUnit.context, ["functional", "non-functional"], declared);
    } else {
      addRequirements(result, ["functional", "non-functional"], declared);
    }
    for (const id of detailedNfrIds(constructionDir(docsDir, unit, "nfr-requirements"), NFR_REQUIREMENT_FILES) ?? []) {
      result.ids.add(id);
    }
    const brPath = join(constructionDir(docsDir, unit, "functional-design"), "rules.md");
    if (existsSync(brPath)) {
      const read = readText(brPath);
      if (read.content !== null) {
        for (const id of extractIds(read.content, [ID_PATTERNS.BR])) result.ids.add(id);
      }
    }
    if (result.ids.size === 0) {
      result.reasons.push(unit ? `upstream ID set is empty for unit "${unit}"` : "upstream ID set is empty for the zero-Unit code-generation run");
    }
    return result;
  }

  result.reasons.push(`stage "${stage}" has no traceability upstream resolver`);
  return result;
}

function verifyTargets(
  stage: string,
  data: TraceabilityData,
  projectDir: string,
  outputPath: string,
  upstream: UpstreamResolution,
): { invalidTargets: string[]; derivedOrphans: string[]; reasons: string[] } {
  const invalidTargets: string[] = [];
  const derivedOrphans: string[] = [];
  const reasons: string[] = [];
  const docsDir = recordDir(projectDir);
  const okEntries = data.coverage.filter((entry) => entry.status === "OK");

  if (stage === "user-stories" && docsDir) {
    const storiesPath = join(docsDir, "inception", "user-stories", "stories.md");
    const stories = idsFromFile(storiesPath, [ID_PATTERNS.US], "stories.md");
    if (stories.reason) reasons.push(stories.reason);
    for (const entry of okEntries) {
      const targets = extractIds(entry.target ?? "", [ID_PATTERNS.US]);
      if (targets.size === 0) invalidTargets.push(`${entry.id}: target must name at least one USx.y ID`);
      for (const target of targets) {
        if (!stories.ids.has(target)) invalidTargets.push(`${entry.id}: target ${target} is absent from stories.md`);
      }
    }
  }

  if (stage === "units-generation" && upstream.unitContext && upstream.storyAssignments) {
    const reverseUnitIds = new Map([...upstream.unitContext.unitIds.entries()].map(([unit, id]) => [id, unit]));
    for (const entry of okEntries) {
      const rawTarget = entry.target?.trim() ?? "";
      const unit = upstream.unitContext.units.find((candidate) => namesUnit(rawTarget, candidate))
        ?? reverseUnitIds.get(rawTarget.toUpperCase());
      if (!unit) {
        invalidTargets.push(`${entry.id}: target "${rawTarget}" is not a declared unit`);
      } else if (!upstream.storyAssignments.get(entry.id)?.has(unit)) {
        invalidTargets.push(`${entry.id}: target "${rawTarget}" is not mapped in unit-of-work-story-map.md`);
      }
    }
  }

  if (stage === "functional-design" && docsDir) {
    const unit = upstream.unitContext?.unitName ?? extractUnitName(outputPath);
    if (unit !== null) {
      const brPath = join(constructionDir(docsDir, unit, "functional-design"), "rules.md");
      const rules = idsFromFile(brPath, [ID_PATTERNS.BR], "rules.md");
      if (rules.reason) reasons.push(rules.reason);
      const targeted = new Set<string>();
      for (const entry of okEntries) {
        const targets = extractIds(entry.target ?? "", [ID_PATTERNS.BR]);
        if (targets.size === 0) invalidTargets.push(`${entry.id}: target must name at least one BRx.y ID`);
        for (const target of targets) {
          targeted.add(target);
          if (!rules.ids.has(target)) invalidTargets.push(`${entry.id}: target ${target} is absent from rules.md`);
        }
      }
      const explained = new Set(data.reverse.map((entry) => entry.id));
      for (const rule of rules.ids) {
        if (!targeted.has(rule) && !explained.has(rule)) derivedOrphans.push(rule);
      }
    }
  }

  if (stage === "code-generation") {
    const projectRoot = resolve(projectDir);
    for (const entry of okEntries) {
      const problem = codeTargetProblem(projectRoot, entry.target);
      if (problem) invalidTargets.push(`${entry.id}: ${problem}`);
    }
  }

  return { invalidTargets, derivedOrphans, reasons };
}

// Why a Code Generation `OK` target is not one existing workspace-relative file.
function codeTargetProblem(projectRoot: string, rawTarget: string | undefined): string | null {
  const target = normalizePath(rawTarget?.trim() ?? "");
  if (target === "" || isAbsolute(target) || /^[A-Za-z]:\//.test(target)) {
    return "target must be a workspace-relative file path";
  }
  const absolute = resolve(projectRoot, target);
  const rel = relative(projectRoot, absolute);
  if (rel.startsWith("..") || isAbsolute(rel)) return "target escapes the project directory";
  try {
    if (!existsSync(absolute) || !statSync(absolute).isFile()) return `target file does not exist: ${target}`;
  } catch {
    return `target file is unreadable: ${target}`;
  }
  return null;
}

interface FinalRow {
  id: string;
  covered: boolean;
  built_in?: string[];
  through?: string[];
  not_built?: string[];
  not_applicable?: string[];
}

interface FinalCoverage {
  pass: boolean;
  uncovered: string[];
  reason?: string;
  coverage: FinalRow[];
}

function readTable(path: string, label: string): { data?: TraceabilityData; reason?: string } {
  const read = readText(path);
  if (read.content === null) return { reason: read.reason };
  const validated = validateTraceability(parseJson(read.content));
  return validated.ok ? { data: validated.data } : { reason: `${label}: ${validated.reason}` };
}

// Build and Test's cross-Unit coverage: every requirement Requirements Analysis
// recorded (without a record, every one the FR and NFR patterns find in
// requirements.md and every one User Stories traced, or when it did not run
// every one a Code Generation table lists) and, when User Stories ran,
// every criterion in stories.md, each built by an `OK`
// row whose file exists in some Code Generation table. A requirement with
// sub-requirements (NFR1 over a Unit's NFR1.1) is built when each Unit has built
// its own, and a row for the parent alone does not cover them; else it is built
// by its own row; else, with stories, through every criterion of the stories
// User Stories traced it to, because Code Generation then traces criteria.
// One nothing builds is settled when the latest stage that traced it (User
// Stories, then NFR Requirements, then Code Generation) marked it `N/A` with its
// reason in every table there that lists it; any other row there, or an `N/A`
// with no reason, leaves it unbuilt. A Unit's own Code Generation `N/A` settles
// a sub-requirement it owes.
// `uncovered` comes before the per-ID rows so a host that cuts long output keeps it.
function finalCoverage(projectDir: string): FinalCoverage {
  const docsDir = recordDir(projectDir);
  if (!docsDir) {
    return { pass: false, uncovered: [], reason: "cannot resolve the active intent record directory", coverage: [] };
  }
  const reasons: string[] = [];
  const recorded = recordedRequirements(docsDir);
  const criteria: string[] = [];
  const tracedByStories: string[] = [];
  const tracedByCode: string[] = [];

  // Per ID, the latest stage's `N/A` reasons, or null when a row there is not one.
  const notApplicable = new Map<string, { rank: number; reasons: string[] | null }>();
  const traced = (rank: number, where: string, entry: CoverageEntry) => {
    const prior = notApplicable.get(entry.id);
    if (prior && prior.rank > rank) return;
    const kept = prior?.rank === rank ? prior.reasons : [];
    const reason = entry.status === "N/A" ? entry.target?.trim() : "";
    notApplicable.set(entry.id, { rank, reasons: kept && reason ? [...kept, `${where}: ${reason}`] : null });
  };
  const whyNotApplicable = (id: string) => notApplicable.get(id)?.reasons ?? undefined;
  const ownNotApplicable = new Map<string, string>();

  const storyTargets = new Map<string, Set<string>>();
  let storyContent: string | null = null;
  const storiesPath = join(docsDir, "inception", "user-stories", "stories.md");
  if (existsSync(storiesPath)) {
    const read = readText(storiesPath);
    if (read.reason) reasons.push(read.reason);
    storyContent = read.content;
    if (storyContent !== null) criteria.push(...extractIds(storyContent, [ID_PATTERNS.AC]));
    const table = readTable(join(docsDir, "inception", "user-stories", "traceability.json"), "User Stories traceability.json");
    if (table.reason) reasons.push(table.reason);
    tracedByStories.push(...(table.data?.upstream_ids ?? []));
    for (const entry of table.data?.coverage ?? []) {
      traced(0, "user-stories", entry);
      if (entry.status !== "OK") continue;
      const stories = storyTargets.get(entry.id) ?? new Set<string>();
      for (const story of extractIds(entry.target ?? "", [ID_PATTERNS.US])) stories.add(story);
      storyTargets.set(entry.id, stories);
    }
  }

  // The stage-level table (no Units) and every Unit's, named by their folder.
  const construction = join(docsDir, "construction");
  const folders = existsSync(construction)
    ? readdirSync(construction, { withFileTypes: true }).filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort()
    : [];
  const owners = [
    { owner: "code-generation", dir: construction },
    ...folders.filter((folder) => folder !== "code-generation").map((owner) => ({ owner, dir: join(construction, owner) })),
  ];

  const projectRoot = resolve(projectDir);
  const add = (map: Map<string, string[]>, key: string, value: string) => map.set(key, [...(map.get(key) ?? []), value]);
  const builtIn = new Map<string, string[]>();
  const badTargets = new Map<string, string[]>();
  // Each owner's own built IDs and the sub-requirements it owes: the NFRx.y its
  // NFR Requirements wrote (what Code Generation's own check asks of it) and any
  // its table lists. The same NFRx.y in two Units is two requirements.
  const builtBy = new Map<string, Set<string>>();
  const owed = new Map<string, Set<string>>();
  let tables = 0;
  for (const { owner, dir } of owners) {
    const children = new Set(detailedNfrIds(join(dir, "nfr-requirements"), NFR_REQUIREMENT_FILES) ?? []);
    const built = new Set<string>();
    owed.set(owner, children);
    builtBy.set(owner, built);
    const where = (stage: string) => (owner === "code-generation" ? stage : `${owner}/${stage}`);
    // Read only for its `N/A` rows: an unreadable one settles nothing.
    const nfrTable = join(dir, "nfr-requirements", "traceability.json");
    if (existsSync(nfrTable)) {
      for (const entry of readTable(nfrTable, `${owner} NFR Requirements traceability.json`).data?.coverage ?? []) {
        traced(1, where("nfr-requirements"), entry);
      }
    }
    const path = join(dir, "code-generation", "traceability.json");
    if (!existsSync(path)) continue;
    tables++;
    const read = readTable(path, `${owner} Code Generation traceability.json`);
    if (!read.data) {
      if (read.reason) reasons.push(read.reason);
      continue;
    }
    tracedByCode.push(...read.data.upstream_ids);
    for (const id of [...read.data.upstream_ids, ...read.data.coverage.map((entry) => entry.id)]) {
      if (tracedParent(id)) children.add(id);
    }
    for (const entry of read.data.coverage) {
      traced(2, where("code-generation"), entry);
      if (entry.status === "N/A" && entry.target?.trim()) ownNotApplicable.set(`${owner}: ${entry.id}`, entry.target.trim());
      if (entry.status !== "OK") continue;
      const problem = codeTargetProblem(projectRoot, entry.target);
      if (problem) {
        add(badTargets, entry.id, `${owner}: ${entry.id}: ${problem}`);
        add(badTargets, `${owner}: ${entry.id}`, `${owner}: ${entry.id}: ${problem}`);
      } else {
        built.add(entry.id);
        add(builtIn, entry.id, `${owner}: ${normalizePath(entry.target?.trim() ?? "")}`);
      }
    }
  }
  if (tables === 0) reasons.push(`no Code Generation traceability.json under ${construction}`);
  // Without a record requirements.md's reasons are not said, as in addRequirements.
  const requirementIds = recorded !== null ? [...recorded.keys()] : [
    ...idsFromFile(join(docsDir, "inception", "requirements-analysis", "requirements.md"), [ID_PATTERNS.FR, ID_PATTERNS.NFR], "requirements.md").ids,
    ...(storyContent !== null ? tracedByStories : tracedByCode),
  ];
  const reported = [...new Set([...withoutTracedParents(new Set(requirementIds), recorded), ...criteria])];

  // A story with no criteria cannot show it is built, so it stands for itself.
  const criteriaOrStory = (story: string): string[] => {
    const criteria = storyContent === null ? [] : [...criteriaOfStories(storyContent, new Set([story]))];
    return criteria.length > 0 ? criteria : [story];
  };
  const coverage: FinalRow[] = [];
  for (const id of reported) {
    const parts: string[] = [];
    const partsNotBuilt: string[] = [];
    const partsNotApplicable: string[] = [];
    for (const [owner, children] of owed) {
      for (const child of children) {
        if (tracedParent(child) !== id) continue;
        const part = `${owner}: ${child}`;
        parts.push(part);
        if (builtBy.get(owner)?.has(child)) continue;
        const reason = ownNotApplicable.get(part);
        if (reason) partsNotApplicable.push(`${part}: ${reason}`);
        else partsNotBuilt.push(part);
      }
    }
    if (parts.length > 0) {
      const settled = partsNotApplicable.length > 0 ? { not_applicable: partsNotApplicable } : {};
      coverage.push(partsNotBuilt.length === 0
        ? { id, covered: true, through: parts, ...settled }
        : { id, covered: false, through: parts, not_built: partsNotBuilt, ...settled });
      continue;
    }
    const built = builtIn.get(id);
    if (built) {
      coverage.push({ id, covered: true, built_in: built });
      continue;
    }
    const through = [...(storyTargets.get(id) ?? [])].flatMap(criteriaOrStory);
    if (through.length > 0) {
      const notBuilt = through.filter((part) => !builtIn.has(part) && !whyNotApplicable(part));
      coverage.push(notBuilt.length === 0 ? { id, covered: true, through } : { id, covered: false, through, not_built: notBuilt });
      continue;
    }
    const why = whyNotApplicable(id);
    coverage.push(why ? { id, covered: true, not_applicable: why } : { id, covered: false });
  }

  const uncovered = coverage.filter((row) => !row.covered);
  for (const row of uncovered) {
    for (const part of [row.id, ...(row.not_built ?? [])]) reasons.push(...(badTargets.get(part) ?? []));
  }
  const reason = uniqueSorted(reasons).join("; ");
  return {
    pass: uncovered.length === 0 && reasons.length === 0,
    uncovered: uncovered.map((row) => row.id),
    ...(reason ? { reason } : {}),
    coverage,
  };
}

function uniqueSorted(values: string[]): string[] {
  return [...new Set(values)].sort();
}

// Why `path` is not this stage's own table inside the active record, or null.
function tableWhere(docsDir: string, stage: string, path: string): string | null {
  if (basename(path) !== "traceability.json" || basename(dirname(path)) !== stage) {
    return `nothing written: the table this writes is the ${stage} stage's own traceability.json`;
  }
  const inside = (root: string, candidate: string): boolean => {
    const rel = relative(root, candidate);
    return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
  };
  try {
    let existing = dirname(path);
    while (!existsSync(existing) && dirname(existing) !== existing) existing = dirname(existing);
    const root = realpathSync(docsDir);
    if (!inside(root, realpathSync(existing)) || (existsSync(path) && !inside(root, realpathSync(path)))) {
      return "nothing written: the table goes inside the active intent's record";
    }
    if (existsSync(path) && !statSync(path).isFile()) return "nothing written: the table's path is not a file";
  } catch (error) {
    return `nothing written: ${errorMessage(error)}`;
  }
  return null;
}

function unrecordedNote(kinds: RequirementKind[]): string[] {
  if (kinds.length === 0) return [];
  const which = kinds.length === 2 ? "FR and NFR ID" : kinds[0] === "functional" ? "FR ID" : "NFR ID";
  return [
    `Requirements Analysis recorded no requirement IDs, so none are listed here: add every ${which} ` +
      "requirements.md defines to upstream_ids, as written, each with a coverage row",
  ];
}

// The table a later stage starts with, written by the tool from the resolver
// the check uses: every ID the check asks this stage for goes in
// `upstream_ids`, each with a `GAP` row until the agent sets its status and
// target, so none is left out. What the table already holds stays as it is,
// and nothing is written when nothing is missing, so running it again never
// changes what a review covered.
function writeUpstream(stage: string, rawOutputPath: string): void {
  const reply = (fields: { written: boolean; upstream_ids?: unknown[]; added?: string[]; reason?: string }): void => {
    process.stdout.write(`${JSON.stringify({ path: rawOutputPath, upstream_ids: [], added: [], ...fields })}\n`);
  };
  const projectDir = resolveProjectDir();
  const docsDir = recordDir(projectDir);
  const path = resolve(normalizePath(rawOutputPath));
  const refusal = docsDir ? tableWhere(docsDir, stage, path) : "nothing written: cannot resolve the active intent record directory";
  if (refusal) {
    reply({ written: false, reason: refusal });
    return;
  }
  let table: Record<string, unknown> = {};
  if (existsSync(path)) {
    const value = parseJson(readFileSync(path, "utf-8"));
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      reply({ written: false, reason: "nothing written: traceability.json is not a JSON object" });
      return;
    }
    table = value as Record<string, unknown>;
  }
  const declared = table.upstream_ids ?? [];
  const coverage = table.coverage ?? [];
  if (!Array.isArray(declared) || !declared.every((id) => typeof id === "string") || !Array.isArray(coverage)) {
    reply({ written: false, reason: "nothing written: upstream_ids must be an array of strings and coverage an array" });
    return;
  }
  const upstream = resolveUpstream(stage, projectDir, path, declared);
  const required = [...withoutTracedParents(upstream.ids, upstream.recorded)];
  const rows = new Set(coverage.map((entry) => (entry as { id?: unknown } | null)?.id));
  const newIds = required.filter((id) => !declared.includes(id));
  const newRows = required.filter((id) => !rows.has(id));
  const upstreamIds = [...declared, ...newIds];
  const written = newIds.length > 0 || newRows.length > 0;
  if (written) {
    const unit = extractUnitName(path);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, `${JSON.stringify({
      stage,
      ...(unit ? { unit } : {}),
      ...table,
      upstream_ids: upstreamIds,
      coverage: [...coverage, ...newRows.map((id) => ({ id, status: "GAP" }))],
    }, null, 2)}\n`);
  }
  const reasons = [...upstream.reasons, ...unrecordedNote(upstream.unrecorded)];
  reply({
    written,
    upstream_ids: upstreamIds,
    added: [...new Set([...newIds, ...newRows])],
    ...(reasons.length > 0 ? { reason: uniqueSorted(reasons).join("; ") } : {}),
  });
}

export function main(argv: string[]): void {
  if (asksForHelp(argv)) {
    printUsageAndExit(
      `Usage: ${sensorFireUsage("traceability")}\n` +
        `       ${aidlcDispatcherInvocation("sensor-traceability --stage build-and-test")}`,
    );
  }
  const flags = parseFlags(argv);
  if (flags.writeUpstream) {
    if (!flags.stage) fail("--stage is required with --write-upstream");
    if (!flags.outputPath) fail("--output-path is required");
    writeUpstream(flags.stage, flags.outputPath);
    return;
  }
  if (flags.stage === "build-and-test" && !flags.outputPath) {
    process.stdout.write(`${JSON.stringify(finalCoverage(resolveProjectDir()))}\n`);
    return;
  }
  if (!flags.outputPath) fail("--output-path is required");
  const outputPath = normalizePath(flags.outputPath);
  if (!existsSync(outputPath)) fail(`--output-path not found: ${flags.outputPath}`);

  let body: string;
  try {
    body = readFileSync(outputPath, "utf-8");
  } catch (error) {
    fail(`failed to read --output-path ${flags.outputPath}: ${errorMessage(error)}`);
  }

  const validated = validateTraceability(parseJson(body));
  if (!validated.ok) {
    emit(failedResult(validated.reason));
    return;
  }
  const data = validated.data;
  if (data.coverage.length === 0) {
    emit(failedResult("no coverage entries found in traceability.json"));
    return;
  }

  const stage = flags.stage ?? data.stage ?? "";
  const projectDir = resolveProjectDir();
  const gaps: string[] = [];
  const orphans: string[] = [];
  const invalidEntries: string[] = [];
  const reasons: string[] = [];
  const declared = new Set(data.upstream_ids);
  const upstream = resolveUpstream(stage, projectDir, outputPath, data.upstream_ids);
  const required = withoutTracedParents(upstream.ids, upstream.recorded);

  for (const [field, entries] of [["coverage", data.coverage], ["reverse", data.reverse]] as const) {
    for (const entry of entries) {
      // `reverse` holds what this stage produced that traces to nothing upstream.
      if (field === "reverse" && required.has(entry.id) && !declared.has(entry.id)) {
        invalidEntries.push(`reverse:${entry.id}: ${entry.id} is an upstream ID; list it in upstream_ids with a coverage row (N/A takes its reason as the target)`);
        continue;
      }
      if (!VALID_STATUSES.has(entry.status)) {
        invalidEntries.push(`${field}:${entry.id}: unknown status "${entry.status}"`);
        continue;
      }
      if (entry.status === "GAP") gaps.push(entry.id);
      if (entry.status === "ORPHAN") orphans.push(entry.id);
      if (["OK", "Deferred", "N/A"].includes(entry.status) && !entry.target?.trim()) {
        invalidEntries.push(`${field}:${entry.id}: status ${entry.status} requires a non-empty target`);
      }
    }
  }

  const covered = new Set(data.coverage.map((entry) => entry.id));
  const missingFromTable = [...withoutTracedParents(declared, upstream.recorded)].filter((id) => !covered.has(id));
  for (const entry of data.coverage) {
    if (!declared.has(entry.id)) invalidEntries.push(`coverage:${entry.id}: id is absent from upstream_ids`);
  }

  reasons.push(...upstream.reasons);
  gaps.push(...upstream.extraGaps);
  if (stage === "units-generation" || stage === "functional-design") {
    // Another unit's ID may be listed only as N/A, the way a complete requirement table reads.
    // A requirement listed beside the unit's own sub-requirements of it is traced through them.
    const notApplicable = new Set(data.coverage.filter((entry) => entry.status === "N/A").map((entry) => entry.id));
    const claimed = new Set(data.coverage.filter((entry) => entry.status !== "N/A").map((entry) => entry.id));
    const traced = new Set([...upstream.ids, ...(upstream.optionalIds ?? [])]);
    for (const id of upstream.ids) {
      const parent = requirementOf(id, upstream.recorded);
      if (parent && upstream.sourceIds?.has(parent)) traced.add(parent);
    }
    for (const id of declared) {
      if (traced.has(id)) continue;
      if (!upstream.sourceIds?.has(id)) {
        invalidEntries.push(`upstream_ids:${id}: id is absent from the resolved upstream source`);
      } else if (!notApplicable.has(id) || claimed.has(id)) {
        invalidEntries.push(`upstream_ids:${id}: id is mapped to another unit; mark it N/A or remove it`);
      }
    }
  }
  const missingFromUpstreamIds = [...required].filter((id) => !declared.has(id));
  if (upstream.ids.size === 0 && upstream.reasons.length === 0) {
    reasons.push(`upstream ID set is empty for stage "${stage}"`);
  }

  const targetChecks = verifyTargets(stage, data, projectDir, outputPath, upstream);
  reasons.push(...targetChecks.reasons);
  orphans.push(...targetChecks.derivedOrphans);

  const result: Result = {
    pass: false,
    gaps: uniqueSorted(gaps),
    orphans: uniqueSorted(orphans),
    missing_from_table: uniqueSorted(missingFromTable),
    missing_from_upstream_ids: uniqueSorted(missingFromUpstreamIds),
    invalid_entries: uniqueSorted(invalidEntries),
    invalid_targets: uniqueSorted(targetChecks.invalidTargets),
    findings_count: 0,
  };
  result.findings_count =
    result.gaps.length +
    result.orphans.length +
    result.missing_from_table.length +
    result.missing_from_upstream_ids.length +
    result.invalid_entries.length +
    result.invalid_targets.length +
    reasons.length;
  result.pass = result.findings_count === 0;
  if (reasons.length > 0) result.reason = uniqueSorted(reasons).join("; ");
  emit(result);
}

if (import.meta.main) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    emit(failedResult(`traceability sensor failed safely: ${errorMessage(error)}`));
  }
}

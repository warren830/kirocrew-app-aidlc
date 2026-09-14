# Scope/depth and space controls — integration handoff

This slice owns `PlanService.settings_preview/change_settings`, the self-contained
`handlers.workspace.ROUTES`, `IntentSettingsPanel`, `SpaceControls`, and `workspaceTypes.ts`.
Main has integrated the five routes, the five `StudioApi` methods, the inventory settings action,
and the space controls under repository details. The snippets below document those seams; they are
not additional work requests. Main owns shell integration, route-auth checks and catalog generation.

## Backend registration

In `backend/studio/handlers/common.py`, add `workspace` to the import and module tuple in `all_routes`.
Add these entries to `ROUTE_ORDER`:

```python
("GET", "/repos/{repo_id}/spaces"),
("POST", "/repos/{repo_id}/spaces"),
("POST", "/repos/{repo_id}/spaces/switch"),
("POST", "/repos/{repo_id}/intents/{intent}/settings/preview"),
("POST", "/repos/{repo_id}/intents/{intent}/settings"),
```

The read is authenticated; every POST is owner-only. Request bodies:

```json
{"name": "team-two"}
```

```json
{"scope": "mvp", "depth": "Standard", "test_strategy": "Comprehensive"}
```

Settings preview accepts any subset, including `{}` to read the current settings, scope choices,
and stage selection. Apply takes the same settings plus the preview's `proposal_digest`.

No constants, error codes, events, lease types, or manifest permissions need adding.
`constants.ENGINE_ADMIN_VERBS["aidlc-utility.ts"]` already contains `"space-create"`.
The new narrow `ENGINE_ALLOWLIST["utility.space_create"]` is in this slice.
Space operations reuse the `"cursor_switch"` admin lease operation; their distinct engine verbs
remain recorded in activity. The new handler is independent of `handlers/intents.py`.

## Typed client

In `ui/src/lib/api.ts`, import the explicit interface and extend it:

```ts
import type { WorkspaceApi } from '../plan/workspaceTypes'

export interface StudioApi extends WorkspaceApi {
  // Keep all existing members.
}
```

Add these members to the existing `useMemo<StudioApi>` return object, using its existing helpers:

```ts
spaces: (repoId, o) => get(repoPath(repoId, '/spaces'), o),
createSpace: (repoId, name) => post(repoPath(repoId, '/spaces'), { name }),
switchSpace: (repoId, name) => post(repoPath(repoId, '/spaces/switch'), { name }),
intentSettingsPreview: (repoId, intentKey, body) =>
  post(intentPath(repoId, intentKey, '/settings/preview'), body),
changeIntentSettings: (repoId, intentKey, body) =>
  post(intentPath(repoId, intentKey, '/settings'), body),
```

`ui/src/plan/workspaceTypes.ts` contains all new request/response types. No modification to
`types.ts` is required. If central re-exports are preferred, add:

```ts
export type {
  IntentDepth, IntentSettings, SettingsProposal, SettingsPreviewResponse,
  SettingsChangeResponse, SpacesResponse, SpaceChangeResponse, WorkspaceApi,
} from '../plan/workspaceTypes'
```

## UI mounts

In `ui/src/intents/IntentsView.tsx`:

```tsx
import { IntentSettingsPanel } from '../plan/IntentSettingsPanel'
import { SpaceControls } from './SpaceControls'

// Alongside recomposing/binding state:
const [configuring, setConfiguring] = useState<IntentSummary | null>(null)
```

Wrap the existing `IntentActions` in `renderActions` in a fragment and add this sibling button.
It exposes settings separately from recompose, including when an intent is no longer Running:

```tsx
<button type="button" className="studio-btn" disabled={intent.archived}
  onClick={() => {
    setBinding(null)
    setRecomposing(null)
    setConfiguring(intent)
  }}>
  {t('workspace.settings.title')}
</button>
```

Mount the editor alongside the existing binding/recompose panels:

```tsx
{configuring ? (
  <IntentSettingsPanel
    api={api}
    repoId={configuring.repo_id}
    intentKey={configuring.intent_key}
    intentLabel={configuring.intent_dir}
    onClose={() => setConfiguring(null)}
    onApplied={() => void inventory.refresh()}
  />
) : null}
```

Main mounted space controls under repository details. An alternative inventory mount uses:

```tsx
{targets.length === 1 && targets[0] ? (
  <SpaceControls
    api={api}
    repoId={targets[0].repo_id}
    repoLabel={targets[0].label}
    onChanged={() => void inventory.refresh()}
  />
) : null}
```

If main mounts either control elsewhere, the same props apply. Both reset their state on identity
changes and suppress late responses. The space selector represents the repository cursor; keep the
existing inventory space filter as a filter.

Regenerate the two catalogs with `python scripts/build_i18n.py` after integrating the new parts.
The slice ran `--check` only and its UI tests overlay the parts without writing generated files.

## Semantics to retain

- Changing scope keeps the current depth/test strategy unless explicitly changed. The preview shows
  every stage's before/after selection and lists all refusals.
- Completed marks and artifacts remain; jump-skipped marks remain skipped. Scope changes are refused
  at open approvals/revisions because the bundled serializer would lose those marks. Depth and test
  strategy changes remain available at those boundaries.
- Confirmation activates the named intent, including its space/harness context. This is disclosed
  before confirmation: the bundled engine honors selectors for the state write but resolves audit
  writes from the active intent. The existing verified cursor switch prevents cross-intent audit.
- Digest validation re-reads state and graph **inside** the admin lease. The settings write then
  verifies persisted settings and stage states; no prompt or human turn is dispatched.
- Create and switch are separate operations. Creation seeds the engine's fresh space layout and does
  not switch. Both serialize against execution/admin leases and refuse observed host execution.
- A disconnected HTTP request waits for its engine work before releasing the lease.

## Existing test fixtures main must update

The focused existing regression run had **198 passing / 4 failing**. The failures are exact
closed-set/response assertions in `tests/test_engine.py`, which this slice may not edit:

```python
# GOLDEN:
"utility.space_create": ({"slug": "alt"}, ["space-create", "alt"]),

# ADMIN_KEYS:
"utility.space_create",
```

Also add `"configured_path": None` to the expected Bun health dictionaries in
`test_the_gateway_finds_bun_without_a_path_and_health_says_where` and
`test_health_names_the_locations_when_bun_really_is_absent`. Those assertions predate the existing
Bun configuration feature; the new Bun configuration and runtime creation tests passed.

Focused commands (no full check or install):

```sh
/Users/ychchen/warren_ws/kirocrew/.venv/bin/python -m pytest -q tests/test_workspace_controls.py
PATH=/Users/ychchen/.nvm/versions/node/v24.20.0/bin:$PATH npm --prefix ui test -- src/plan/workspace.test.tsx src/plan/plan.test.tsx
PATH=/Users/ychchen/.nvm/versions/node/v24.20.0/bin:$PATH npm --prefix ui run typecheck
/Users/ychchen/warren_ws/kirocrew/.venv/bin/python scripts/build_i18n.py --check
```

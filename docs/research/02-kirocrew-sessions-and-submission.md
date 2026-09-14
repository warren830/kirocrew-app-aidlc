# 02 — KiroCrew sessions, prompt submission, busy state, cancellation, transcripts, messaging

Research reader notes for AI-DLC Studio (`aidlc-studio`). Everything below is read from
source, not docs. Where docs and code disagree the code is cited and the doc is called out.

Sources examined (absolute paths; line numbers are from these working copies):

- KiroCrew: `/Users/ychchen/warren_ws/kirocrew` (git `79b6c19`, 2026-08-14; `kiro_crew.__version__ == "0.3.0"`)
  - `src/kiro_crew/dashboard/{routes/*.py, chat_handlers.py, chat_runner.py, state.py, chat_utils.py, chat_persistence.py, ws.py, turn_dispatch.py, cron_inject.py, workflow_inject.py, side_context.py, steer_settle.py, session_directive_apply.py, token_auth.py, urls.py, server.py}`
  - `src/kiro_crew/dashboard/handlers/{ask_question.py, messaging.py, sessions.py, side.py, autonudge.py, hooks.py, core.py, source_providers.py}`
  - `src/kiro_crew/{session.py, session_map.py, session_pid.py, session_directive.py, autonudge.py, hooks.py, history.py, agent.py, validation.py, constants.py}`
  - `src/kiro_crew/acp/{client.py, session_handle.py, prompt_blocks.py, types.py}`
  - `src/kiro_crew/apps/{module_loader.py, context.py, route_registry.py, event_bus.py, spawn_sdk.py, lifecycle.py, manifest.py, app_storage.py}`
  - `src/kiro_crew/slack/{interactions.py, handler.py, gateway.py, client.py, outbound.py, format.py}`, `src/kiro_crew/notifications/bus.py`, `src/kiro_crew/mcp_tools/control.py`
  - `website/src/{hooks/useWebSocket.ts, components/QuestionCard.tsx, components/PendingQuestionCard.tsx, lib/resolveAskAfterSend.ts, api/client.ts, app-sdk/index.ts, app-sdk/useChatSession.ts, pages/ChatPage.tsx, types/index.ts}`
  - `docs/app-kit/{api-reference.md, manifest-reference.md}`
- AI-DLC: `/Users/ychchen/warren_ws/aidlc-workflows` (git `a48bcd6`, 2026-07-08)
  - `harness/kiro/agents/aidlc.json`, `harness/kiro/hooks/aidlc-kiro-adapter.ts`, `harness/kiro/skills/aidlc/{SKILL.md,question-rendering.md}`, `core/tools/aidlc-lib.ts`, `core/hooks/aidlc-stop.ts`, `core/aidlc-common/protocols/stage-protocol.md`, `tests/fixtures/kiro-hook-payloads/payloads.json`
- Prototype app: `/Users/ychchen/warren_ws/kirocrew-app-aidlc/{app.json, backend/routes.py}`

---

## 0. Executive summary (what the PRD spikes S1/S5/S7/S12 get from this)

1. **There is exactly one way a KiroCrew dashboard session talks to kiro-cli: an ACP `session/prompt` request built from ONE plain string** (`acp/prompt_blocks.build_prompt_blocks`, called from `AcpClient._send_prompt` at `acp/client.py:4580-4591` and `AcpSessionHandle.prompt` at `acp/session_handle.py:575-600`). The prompt carries no provenance field. Because the AI-DLC `aidlc` agent registers `userPromptSubmit` as an **agent hook in `.kiro/agents/aidlc.json`** (`harness/kiro/agents/aidlc.json` `"hooks"."userPromptSubmit"`), every KiroCrew path that ends in `session/prompt` on that session is a "human turn" to AI-DLC: the hook bumps `aidlc/.aidlc-turn-counter` and appends `HUMAN_TURN` (`harness/kiro/hooks/aidlc-kiro-adapter.ts:103-133`). That includes user messages, queued messages, `Continue`, cron/workflow/subagent injections, AutoNudge, Slack replies, and `send_message session=origin`. **KiroCrew has no "machine lane" seam that reaches the model without `session/prompt`.**
2. **Silent context (`POST /api/chat/slots/{slot}/context`) does not start a turn**, but it is *prepended into the text of the next `session/prompt`* (`chat_runner.drain_pending_context` `:238-269`, applied at `:4386-4388`), so it rides the next human turn and cannot be used as a machine lane either.
3. **Mid-turn steer is the only non-`session/prompt` delivery**: `AcpClient.steer` sends kiro-cli's extension method `_session/steer` with the text wrapped in `<user_message>…</user_message>` (`acp/client.py:4523-4542`). Whether kiro-cli fires `userPromptSubmit` for a steer is **not determinable from KiroCrew source** (open question; S12 must test it). Steer only works while a turn is running.
4. **An in-gateway app route handler can bypass HTTP entirely**: `RouteRegistry.dispatch` passes the live aiohttp `request` (`apps/route_registry.py:217-271`), and `request.app["state"]` is the `DashboardState` singleton (`dashboard/server.py:2342`). From it: `state.get_or_create_slot(...)`, `state._slots`, `slot.enqueue_or_run_prompt(...)`, `spawn_guarded_turn(state, slot, _run_chat(state, slot, msg))`, `state.sessions` (`SessionManager`: `is_busy`, `stop_turn`, `get_pid`, `resumable_sid`), `state.conversation_log`, `state.slack_client`, `state.notification_bus`, `state.owner_id`.
5. **Slot identity**: dashboard slot key (`chat-N-<epoch>` or caller-supplied, folded by `_normalize_slot_key`) ↔ session key `dashboard:<slot>` (`chat_utils._history_key_for` `:432-444`; `effective_session_key` `:564-583` honours `linked_session_key`) ↔ kiro-cli session id persisted in `~/.kiro/crew/session_map.json` with `cwd` (`session_map.set(key, sid, provider, cwd)` `:717-735`; written at `session.py:2925/3813`) ↔ PID via `SessionManager.get_pid(key)` / `runtime_pids()` (`session.py:827, 2024`). Slot metadata incl. `project`, `agent`, `app`, `human_seen`, `linked_session_key`, `tab_id`, `closed` is the first JSONL line of `~/.kiro/crew/sessions/dashboard_<slot>.jsonl` (`chat_persistence._save_slot_to_history` `:1775-1855`); open tabs are listed in `~/.kiro/crew/open_slots.json` (`state._persist_open_slots` `:3478-3547`).
6. **Busy**: `slot.running == (slot.task is not None and not slot.task.done())` (`state.py:1764`); at the session layer `SessionManager.is_busy(key)` = semaphore held (`session.py:4077`). Turn end signals: WS `chat_done {slot}` (`chat_runner._finish_queue_cycle` `:3489`), transcript row `{"role":"done"}`, `slot.task` completion, `slot.append("done")` → `slot.event.set()`.
7. **Cancel**: `POST /api/chat/slots/{slot}/stop` (soft = ACP `session/cancel`, `?force=true` = kill+respawn) → `SessionManager.stop_turn` (`session.py:4459-4542`); a `stop_event` system row is written to the transcript. Turn ceiling: `agent.chat_turn_timeout_secs` (default `CHAT_TURN_TIMEOUT = 7200.0`, `constants.py:54`) enforced by `turn_dispatch._bounded_turn`, with the absolute deadline published in the `_TURN_DEADLINE` ContextVar (`turn_dispatch.py:51-70`).
8. **S1 (structured questions)**: on the kiro-cli backend there is **no `AskUserQuestion` tool**; the AI-DLC Kiro annex renders questions as **numbered prose** (`harness/kiro/skills/aidlc/question-rendering.md`). KiroCrew's `question_card` WS event fires only for (a) Claude Code's `AskUserQuestion` tool_call (`chat_runner.py:4849-4867`), (b) KiroCrew's own `ask_question` MCP tool (stateless card via `session_directive_apply._ask_question` `:410-427`), (c) the owner-only blocking `POST /api/ask-question`. AI-DLC does not call either. The only structured signal an AI-DLC kiro turn produces is the persisted `<slug>-questions.md` with `[Answer]:` tags plus the `[OPTIONS: a | b]` trailer if the model emits one. **S1 should be expected to fall to the PRD's degraded mode unless the AI-DLC kiro annex is changed.**
9. **Slack**: owner DM via `state.slack_client.open_dm(state.owner_id)` + `post_blocks`; KiroCrew routes interactive callbacks through `slack/interactions.dispatch` with fixed `action_id` prefixes (`options_choice_*`, `options_submit`, `action::<json>`, `approve_tool|trust_tool|reject_tool`, `cron_ack_*`). Any button KiroCrew understands is re-dispatched as a **new Slack-thread user turn** (`handle_message`), not as an arbitrary app callback. **There is no app-registered Slack callback correlation API.** Deep links: notification `url`/`actions[].url` must be dashboard-internal paths (`notifications/bus.py:_validate_internal_url`); chat deep link is `/chat?sid=<slot-key>` (legacy `?slot=`), `ChatPage.tsx:3261-3270`.

---

## 1. Identity model: slot ↔ session key ↔ kiro-cli sid ↔ PID

### 1.1 Slot key
- Created by `DashboardState.get_or_create_slot(name=None, agent="", workspace="default", model="", mode="", memory_mode=None, ephemeral=None, app="", linked_session_key="", channel_origin=False)` (`state.py:4019-4186`).
- Auto names: `_mint_slot_key("chat", counter, ts)` → `chat-<N>-<epoch>` (`state.py:173`). Caller names are folded by `_normalize_slot_key` (`state.py:808`) to the filename charset; the folded key equals its JSONL stem.
- Existing name → returns the existing slot (raises `ValueError` on `memory_mode` mismatch).
- `slot._app = app` tags ownership (App Kit §5.2); `slot._tab_id = uuid4().hex[:12]` is the permanent cross-restart tab identity.

### 1.2 Session key (what `SessionManager` and the ACP layer key on)
```python
# dashboard/chat_utils.py:432-444
def _history_key_for(slot_key: str) -> str:
    if slot_key.startswith("dashboard:"): return slot_key
    while slot_key.startswith("dashboard_"): slot_key = slot_key[len("dashboard_"):]
    return f"dashboard:{slot_key}"

# dashboard/chat_utils.py:564-583
def effective_session_key(slot: _ChatSlot) -> str:
    return getattr(slot, "linked_session_key", "") or _history_key_for(slot.key)
```
- `dashboard_slot_key(session_key)` (`chat_utils.py:447-491`) is the inverse ("which tab shows this session?"); it returns `""` when no tab is open (`has_dashboard_surface`).
- The session key is exported to kiro-cli's process env as `KIROCREW_SESSION_KEY` (`acp/client.py:2593`), which is how KiroCrew MCP tools (`ask_question`, `set_project`, …) identify their session (`mcp_core._resolve_session_key_strict` `:506+`).

### 1.3 kiro-cli session id + cwd (persisted)
- `~/.kiro/crew/session_map.json` (`session_map.py:39`, `config_dir()` = `~/.kiro/crew` per `config/paths.py:43-44`, overridable with `KIROCREW_HOME`). Entry per canonical key:
  ```json
  {"sid": "<kiro-cli session uuid>", "slack_thread_ts": null, "slack_channel_id": null, "provider": "acp", "cwd": "/abs/project"}
  ```
  written by `SessionMap.set(key, sid, *, provider="", cwd="")` (`session_map.py:717-735`), called from `SessionManager.get_or_create` (`session.py:2925`, `:3813`) with `cwd=provider.cwd`. Readers: `SessionMap.get_cwd(key)` (`:736`), `SessionManager.resumable_sid(key)` (`:3994`), `resumable_hint` (`:4003`).
- **To find sessions whose cwd is a given repo**: iterate `state.sessions._session_map._data` (private) or the JSON file and match `entry["cwd"]`; for open tabs use `slot.project` (`_ChatSlot.project`, serialized as `"project"` in `slot.to_dict()` and in `GET /api/chat/slots`). There is no public "sessions by cwd" endpoint.
- kiro-cli's own transcript lives under `kiro_sessions_dir()` = `<KIRO_HOME or ~/.kiro>/sessions/cli` (`config/paths.py:721`).

### 1.4 PID
- `SessionManager.get_pid(key)` (`session.py:827`), `runtime_pids()` (`:2024-2065`) → rows `{key, agent, pid, owns_runtime, created_at, prompts}`. PID files: `~/.kiro/crew/kiro_pids.txt`, `kiro_session_pids.txt` (`session_pid.py:31-32`). A PID is the sandbox launcher parent, not stable across respawn; do not use it as identity.

### 1.5 Persistence across gateway restart
- Slot metadata line (first JSONL row) keys written by `_save_slot_to_history` (`chat_persistence.py:1775-1855`): `title, title_origin, title_refresh_mark, agent, model, reasoning_effort, mode, workspace, project, folder_id, channel_folder_filed, app, artifact, pinned, color_index, color_theme, tags, auto_tagged, human_seen, forked_from, linked_session_key, channel_origin, tab_id, closed, closed_at, rotation_generation, memory_mode, created_at`.
- Restore: `restore_open_slots_async` reads `~/.kiro/crew/open_slots.json` (`{"keys": [...], "ts": ...}`) then `_rehydrate_slot_from_history(state, slot_name, *, adopt_closed=False, ...)` (`chat_persistence.py:413-500`). A slot whose metadata has `closed: true` is **not** rehydrated unless `adopt_closed=True` (AutoNudge and cron use `True` for app-owned workers; `slack/gateway.py:3830-3899`).
- `DELETE /api/chat/slots/{slot}` tombstones (`note_slot_closed`), pops `state._slots[name]`, kills the kiro-cli session, saves history with `closed=true` (`chat_handlers.py:2334-2460`).
- The live kiro-cli process does **not** survive a gateway restart; on the next turn `SessionManager.get_or_create` performs ACP `session/load` with the persisted sid (`resumed=True`) when the map still has it (`session.py:2349-2420` docstring).
- Idle sweep: sessions idle longer than `session.timeout_secs` (clamped ≥60, 0 disables; `session.py:4856-4879`) or **orphaned** (`dashboard:` key not in `_active_dashboard_slots`, `:5027-5060`) are destroyed. `_sync_dashboard_slots(state)` publishes the active set (`chat_utils.py:1235-1245`) — a slot created in-process via `get_or_create_slot` is published automatically (`state.py:4160-4186`).

---

## 2. What an in-gateway app can reach (S5 "stable seam" answer)

### 2.1 Loading and dispatch
- `app.json` → `backend.hooks.routes = "backend.routes:register_routes"`; validated by `HooksConfig._HOOK_PATH_RE = r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$"` (`apps/manifest.py:344`).
- `module_loader.load_app_module(app_name, app_dir, "backend.routes:register_routes")` registers the module as `sys.modules["_kirocrew_app_<app>.backend.routes"]`; third-party apps must pass `app_execution_denied(...)` (trust gate) and a SEC-012 warning is logged (`apps/module_loader.py:60-172`). Sibling relative imports do not work; import `kiro_crew.*` freely (the prototype does).
- Contract: `register_routes(ctx: AppContext) -> list[AppRoute]`, `AppRoute(method, path, handler)` with `handler(request: web.Request, ctx: AppContext) -> web.Response` (`apps/route_registry.py:26-32`). Paths are relative and mounted under `/api/apps/<app>/…` by the catch-all `"*", "/api/apps/{app_name}/{path:.*}"` (`:120-127`). `{param}` segments become `request.match_info[param]` (`:254-261`).
- `AppContext` fields (`apps/context.py:54-67`): `name, data_dir, config, logger, cron (CronSDK|None), events (EventBus|None), storage (AppStorage|None), spawn (SpawnSDK|None), health`. `ctx.storage.get(key)->dict|str|None`, `ctx.storage.set(key, value)` (`apps/app_storage.py`). `ctx.events.publish(event_type, data)` broadcasts WS `{"type":"app_event","data":{"app":..., "event":..., "data":...}}` (`apps/event_bus.py:15-20, 141-172`) — only events declared in `permissions.events`.
- Lifecycle hooks: `backend.hooks.on_startup` / `on_shutdown` (`apps/lifecycle.py`).

### 2.2 The gateway objects (via `request.app[...]`)
`dashboard/server.py` sets: `app["state"]` (`:2342`/`:3296`, the `DashboardState`), `app["kiro_prerequisite_service"]`, `app["port"]`, `app["local_only"]`, `app["local_secret"]` (the `X-Internal-Secret` value used by MCP tools, `:2744`), `app["allowed_origins"]`, `app["tailnet_host"]`.

`DashboardState` attributes an app will use (`state.py:2233-2534`): `sessions: SessionManager`, `conversation_log: ConversationLog`, `context_builder`, `subagents: SubagentManager`, `crons`, `slack_client`, `owner_id`, `notification_bus: NotificationBus`, `notification_rate_limiter`, `_slots: dict[str, _ChatSlot]`, `_pending_approvals`, `_pending_questions`, `_hook_store`, `workflow_service`, `_background_tasks`.

Methods: `get_slot(name)` (`:3776`), `resolve_slot(name)` (`:3918`), `get_or_create_slot(...)` (`:4019`), `serialize_slot(slot)`/`serialize_slots()` (`:5070/5086`), `running_session_keys()` (`:3780`), `push_slots_update()` (`:5123`), `broadcast_ws(type, data)` (`:5466`), `broadcast_ws_owners` (`:5673`), `deliver_ws_owners` (`:5632`, awaited, returns delivered count), `register_sse()` (`:3642`, returns an `asyncio.Queue` fed by `_broadcast`, i.e. `chat_message` rows + notifications but **not** `broadcast_ws` events like `chat_done`), `notify(kind, title, body, *, meta, url, actions)` (`:3548`), `request_approval` (`:2979`), `resolve_approval` (`:3076`), `request_question`/`post_question_card`/`resolve_question` (`:3303/3147/3375`), `run_background_turn(slot, coro)` (`:2890`).

### 2.3 Identity of the caller inside an app route
- `token_auth` middleware sets `request["user"]` and `request["app"]` (`token_auth.py:1703-1704, 1775-1776, 2027-2028`); `request["app"] == ""` for a dashboard-user cookie/bearer, `"<app>"` for an app token minted by `POST /api/apps/{name}/token` with `X-App-Secret` (`handlers/core.py:2184-2224`); `request["internal_auth"] is True` only for loopback `X-Internal-Secret` (`:1662`).
- Owner predicate reused by owner-only endpoints: `is_owner_dashboard_request(request)` — `request["app"] == ""` and (`request["user"] == state.owner_id` or, with no owner configured, `user in {"local-app", "local-startup"}`) (`handlers/source_providers.py:3651-3663`).
- The prototype's `_deny_unauthenticated` (`backend/routes.py:755-765`) only checks `request.get("user") is None`; Studio should additionally require `is_owner_dashboard_request` for any mutation that reaches a canonical session.
- App tokens are scoped by `permissions.api` via `app_token_path_allowed` (`token_auth.py:1243-1264`): own namespace `/apps/<app>`, `/api/apps/<app>` always allowed; `/api/notifications/push` always allowed; otherwise `_api_pattern_matches` (bare prefix matches exact path or path-boundary children; `/*` and `*` wildcards) (`:1225-1240`). The manifest doc's claim that `permissions` are advisory is true for everything **except** this HTTP scope (`docs/app-kit/manifest-reference.md:258-262`).
- Slot ownership on chat endpoints is enforced only for **app tokens** (`request_app` non-empty): `api_chat`, `api_chat_slot_create`, `api_chat_slot_continue`, `api_chat_slot_context`, `api_chat_slot_delete`, `api_chat_slot_resume` all 404 when `slot._app != request_app` or the slot is unscoped (`chat_handlers.py:264-295, 1112-1135, 1590-1610, 4133-4157, 2346-2372, 3510-3535`). A dashboard-user request (`request["app"]==""`) passes everywhere. **Because an in-process app handler receives the dashboard user's request, it can act on any slot; it should self-impose ownership by tagging Studio-created slots with `app="aidlc-studio"` and refusing others.**
- Caveat of tagging `_app`: `slot.unattended == bool(_app) and not _human_seen` (`state.py:1783-1810`) switches tool-approval waits to the deny-fast `_BACKGROUND_APPROVAL_TIMEOUT_SECS = 180` (vs `_APPROVAL_TIMEOUT = 7200`, `state.py:2778-2783`) and runs turns under the background concurrency semaphore (`run_background_turn`). `_human_seen` flips to `True` only when a dashboard user posts to the slot through `POST /api/chat` (`chat_handlers.py:296-303`) and is persisted (`human_seen`).

---

## 3. REST + WebSocket surface used by the dashboard

All routes are registered in `dashboard/routes/*.py` (order is load-bearing, `routes/__init__.py`). Handlers in `dashboard/chat_handlers.py` unless noted. Auth: dashboard cookie/bearer, or app token within `permissions.api`, or `X-Internal-Secret` for the paths in `_MIXED_INTERNAL_API_PATHS` (`server.py:415+`: `/api/spawn`, `/api/chat`, `/api/lessons`, `/api/crons`, `/api/taskrunner`, `/api/artifacts`, `/api/workflows`, …) and `_STRICT_INTERNAL_API_PATHS` (`:263+`: `/api/send-message`, `/api/session-keepalive`, …).

### 3.1 Create a session bound to a folder and agent
1. `POST /api/chat/slots` (`api_chat_slot_create` `:984-1203`)
   Body: `{"name"?: str, "agent"?: str, "model"?: str, "folder_id"?: str, "memory_mode"?: "persistent"|"incognito"|"temporary", "mode"?: ""|"orchestrator"|"crew", "ephemeral"?: bool, "title"?: str, "artifact"?: str}`
   → `state.serialize_slot(slot)` (the `to_dict` shape in §3.5). Default `slot.project` = `dashboard.default_project` or `default_project_dir(workspace)` (`:1145-1156`). Explicit `title` pins the title (`_titled=True`, `_title_origin="user"`).
2. `POST /api/chat/slots/{slot}/project` (`api_chat_slot_project` `:3183-3247`)
   Body `{"project": "/abs/dir"}` → realpath, must be a directory, refused if `is_sensitive_path` (403). Sets `slot.project` and **defers** the session reset: `slot._pending_reset_history_key = _history_key_for(name)`, consumed by `_consume_pending_reset` before `get_or_create` on the next turn (`chat_runner.py:2823-2843`, called at `:4057`). Response `{"ok": true, "project": "..."}`.
3. `POST /api/chat/slots/{slot}/agent` (`api_chat_slot_agent` `:2678-2756`)
   Body `{"agent": "aidlc"}`; `_AGENT_NAME_RE`-validated; stored verbatim. **Side effect:** unless `agent` is a *project-scope* agent found in `<slot.project>/.kiro/agents/`, `slot.project` is reset to `default_project_dir(workspace)` (`:2717-2730`). Then the session is reset immediately (`_reset_slot_session`) and `agent` is persisted in metadata. Response `{"ok", "agent", "workspace"}`.
   **Ordering rule for Studio:** set `project` first (so `warm_project_agent_names(slot.project)` sees `<repo>/.kiro/agents/aidlc.json` and keeps the project), or set `agent` then `project`. Never `project` then a non-project agent.
   Runtime wiring: `_run_chat` calls `resolve_agent_bindings(cfg, slot.agent, slot.project)` then `state.sessions.get_or_create(session_key, agent=kiro_agent or slot.agent, model=..., cwd=slot.project or None, reasoning_effort_override=...)` (`chat_runner.py:4043-4063`); kiro-cli is spawned as `kiro acp --agent <agent>` (`acp/client.py:2537`) with `cwd` on `session/new`, so `--agent aidlc` resolves against `<cwd>/.kiro/agents/aidlc.json` first (`agent_discovery.py:46`).
4. Optional: `POST /api/chat/slots/{slot}/model` `{"model"}`, `.../reasoning-effort`, `.../workspace` `{"workspace"}` (409 once messages exist), `PATCH .../title` , `PATCH .../folder`, `PATCH .../pin`, `PUT .../tags`.

### 3.2 Send a user message (the human lane)
`POST /api/chat[?ws=1]` (`api_chat` `:206-650`)
Body:
```json
{"message": "<text>", "slot": "<slot key>", "agent": "", "steer": false,
 "color_theme": "", "theme_consent_sha": null, "meta": {...}, "memory_mode": "persistent"}
```
Semantics:
- Slot auto-created if missing (`get_or_create_slot(slot_name, app=request["app"], memory_mode)`).
- `agent` non-empty and different from `slot.agent` → 409 `slot agent mismatch`; setting an agent on a running slot → 409.
- **Busy slot** (`slot.running or slot._in_stage_execution`): if `steer: true` and `slot._acp_client.supports_steer` → `_try_steer_message` → `client.steer(message)` → response `{"ok": true, "steered": true}` (or `{"ok":true,"queued":true}` if the turn ended mid-write); otherwise the message is **queued**: `slot.queue_append(message)` + WS `queue_push {slot, content, ts, queue_id}` → `{"ok": true, "queued": true}` (`:327-360`).
- Idle slot: `slot.append("user", message, "msg msg-u", meta=...)`, `_human_seen = True` for dashboard callers, AutoNudge `notify_user_input(slot.key)`, then `spawn_guarded_turn(state, slot, state.run_background_turn(slot, _run_chat(state, slot, message)))` (`:596-600`).
- `?ws=1` → `{"ok": true, "slot": "<key>"}` immediately; otherwise an SSE stream of `_build_stream_chunk` frames `{"type": <role>, "content", "ts", "cls", "meta"?}` ending with `data: [DONE]` (`:606-650`).
- The dashboard client uses `fetch('/api/chat?ws=1', {message, slot, color_theme?, theme_consent_sha?, meta?, steer?})` (`website/src/api/client.ts:1958-1976`).

Queue management: `DELETE /api/chat/slots/{slot}/queue/{queue_id}`, `PATCH …/queue/{queue_id}` `{"content"}`, `PUT …/queue/order`, `POST …/queue/{queue_id}/steer` (folds one queued item into the live turn, `:1991-2071`). Queue items: `{"id": uuid12, "content", "kind": ""|"synthetic_recovery", "meta"?, "payload"?}` (`state.py:1705-1738`). Drain: `_start_next_queued_turn` (`chat_runner.py:3324-3411`) appends a `"user"` row for plain items or `"inject"`/`"subagent"` rows for system items, then dispatches `_run_chat` again.

### 3.3 Status / busy / list / history
- `GET /api/chat/slots` → `[slot.to_dict(), ...]` (`:653-685`).
- `GET /api/chat/slots/{slot}` (`api_chat_slot_detail` `:860-981`) → `{"key","title","running","stopping","messages":[...],"queue":[{"id","content"}],"total","has_more", <context fields>}`; without `limit`/`before` it returns the full chained history from disk + memory. Messages are `_prepare_messages` output (`chat_utils.py:1620-1668`): `{"role","content","cls","ts","meta"?}` with roles `user | assistant | streaming | tool | permission | system | inject | nudge | subagent | queued | error | mcp_oauth`.
- `GET /api/chat/slots/{slot}/summary`.
- `GET /api/sessions?limit&offset&preview` → `{"sessions":[{"key","messages","modified","created","title"?,"agent"?,"memory_mode","folder_id"?,"preview"?}], "total", "has_more"}` (`handlers/sessions.py:949-995`); `GET /api/sessions/{key}` → raw `conversation_log.read_messages(key)` (`:1171`); `GET /api/sessions/health` → `{"stalled": [...]}`.
- `GET /api/approvals` → `list(state._pending_approvals.values())` items `{"id","source","tool","tool_input","tool_purpose","slot","ts"}` (state-level approvals only; `state.py:3008-3017`); `POST /api/approvals/{id}/{approve|reject}`.
- `GET /api/ask-question/pending` (owner-only) → `[{"ask_id"?|"card_id"?, "slot", "questions", "ts"}]` (`handlers/ask_question.py:230-283`).
- `GET /api/autonudge`, `GET /api/autonudge/slot/{slot_key}`.
- `POST /api/chat/slots/{slot}/resume` `{"key"?}` reopens a history session into a tab (`:3473+`); `DELETE /api/chat/slots/{slot}` closes it.

### 3.4 Cancel / interrupt / continue
- `POST /api/chat/slots/{slot}/stop` (`:1403-1555`): first press soft (`_stop_state="soft_pending"`, queue preserved), transcript row `{"role":"system","content":<json>,"cls":<json>}` with `{"kind":"stop_event","id":"stop-<hex>","state":"stopping","outcome":null,"ts_start":iso}`, then `state.sessions.stop_turn(session_key, force=False, preserve_queue=True, on_soft, on_hard)`. Second press (or `?force=true`) → `_stop_state="killing"`, clears `_queue` and `_pending_steers`, `stop_turn(force=True)` = `reset(key)` + eager respawn. Response `{"ok": true}` (or `{"ok":true,"info":"not running"|"stop already in progress"}`). Outcomes: `"soft" | "hard" | "idle"` (`session.py:4459-4542`); soft uses `agent.soft_stop_budget_secs` (clamped 0.5–60) as the `session/cancel` ack budget; `AcpProvider.cancel` returns `"no_turn" | "acked" | "timeout" | "error"` (`providers/acp.py:1237-1266`).
- `POST /api/chat/slots/{slot}/interrupt` `{"queue_id"?}` (`:1877-1988`): cancel current turn but keep the queue so the next item runs (400 if queue empty).
- `POST /api/chat/slots/{slot}/continue` (`:1558-1724`): guarded by `slot._lock`; 409 codes `slot_running | slot_orchestrating | slot_stopping | slot_queue_pending | slot_approval_pending | slot_subagents_running | slot_empty`; queues `_MANUAL_RESUME_MSG` or `_MANUAL_CONTINUE_MSG` (both prefixed `MANUAL_RESUME_RECOVERY_PREFIX = "[Continue — requested by the user]"`, `state.py:600`, texts at `chat_utils.py:1417-1447`) as `kind="synthetic_recovery"` and dispatches via `_start_next_queued_turn` → **this is an ordinary `session/prompt`** rendered as an `inject` row. Response `{"ok": true, "slot"}`.
- `POST /api/chat/slots/{slot}/end-wait` `{"wait_id"}` ends a sleeping `wait` MCP tool early (`:1823-1874`).
- Tool approvals: `POST /api/chat/slots/{slot}/approve` `{"request_id", "action": "approved"|"rejected"|"trust"|"trust_reads"|"trust_command", "pattern"?}` resolves `slot._approval_futures[request_id]` (`:3924-4082`).

### 3.5 Slot serialization (`_ChatSlot.to_dict`, `state.py:1997-2199`)
Keys: `key, title, agent, model, reasoning_effort, mode, surface, workspace, project, artifact, messages (count), running, orchestrating, queue_depth, stopping, pending_approval, pending_approval_info {tool, tool_input, tool_kind, request_id} | null, last_activity_ts, waiting_for_input, needs_input, stop_state ("idle"|"soft_pending"|"killing"), wait_state {wait_id, seconds, deadline_ts} | null, created, last_ts, last_message, source_links, source_links_total, todo, has_options, options [str], prompt_preview, trust, trust_reads, trusted_patterns_count, slack_linked, slack_channel, slack_thread_ts, folder_id, pinned, tags, color_index, color_theme, theme_consent, theme_consent_sha, memory_mode, forked_from, linked_session_key, app`.
- `waiting_for_input` = not running, no `[OPTIONS:]`, no pending approval, last conversational role is assistant.
- `needs_input` = a question card is pending (`_question_pending` non-empty).
- `has_options/options` come from `_parse_options` on the last assistant text (the `[OPTIONS: a | b]` trailer, `constants.OPTIONS_RE_LINE` `:137`).

### 3.6 WebSocket (`GET /api/ws`, `dashboard/ws.py`)
- Browser-only: `check_origin(request, require=True)` rejects a missing `Origin` (`ws.py:207-214`). Non-browser/in-process consumers cannot use it; use `state.register_sse()` (SSE queue, `chat_message` + notifications only) or Python callbacks instead.
- Server → client frames `{"type": ..., "data": ...}`; owner-scoped frames use `broadcast_ws_owners`. Types emitted by the chat runner and handlers (grep of `broadcast_ws("…")`):
  `slots` (full `serialize_slots()` list, plus `yolo`, `gitlabHostsGeneration`), `dashboard` (status every 5s), `chat_chunk {slot, content, seq}`, `chat_thinking`, `chat_status {slot, status:"Thinking…"}`, `chat_segment {slot}`, `chat_message {slot, role, content, ts, cls?, meta?}`, `chat_message_update`, **`chat_done {slot}`**, `queue_push/queue_pop/queue_edit/queue_cancel {slot, content, queue_id}`, `steer_push {slot, content, ts}`, `tool_call`, `tool_result {slot, tool_call_id, output}`, `approval` (state-level), `approval_resolved {id, approved}`, `question_card`, `question_card_resolved {ask_id | card_id, slot}`, `activity_event {slot, kind: "status"|"hook"|"context"|"permission"|"stats"|"approval", text}`, `context_usage {slot, pct, used_tokens?, window_tokens?, reset?}`, `heartbeat {slot, ts}`, `slot_clear`, `workflow_result_injected {run_id, slot}`, `autonudge_state {event, slot, loop}`, `app_event {app, event, data, _scope?}`, `chat.side_result`, `chat.side_queue`, `subagent_snapshot/subagent_done/subagent_chunk`, `notification` (generic fallback for `_broadcast` notes).
- Client → server: `subscribe_logs`, `unsubscribe_logs`, `subscribe_subagents`, `unsubscribe_subagents`, `slot_focused {slot}` (owner-only resume prefetch). **No chat send over WS.**

`tool_call` payload (`chat_runner._tool_call_ws_payload` `:1284-1305`):
```json
{"slot": "...", "tool": "<title>", "kind": "<execute|read|edit|...>", "is_shell": bool,
 "tool_call_id": "...", "purpose": "...", "input_preview": "<redacted JSON or unified diff>",
 "auto"?: true, "is_update"?: true}
```
Persisted transcript row for the same call: `{"role":"tool","content":"🔧 <title>","cls":"msg msg-tool","meta":{"tool_call_id","purpose","input","done"?,"output"?}}` (`_tool_meta` `:1259-1281`, appended at `:4838`).

---

## 4. Turn lifecycle, deadlines, and how to observe completion

### 4.1 `_run_chat` (`chat_runner.py:3545+`) in order
1. `session_key = effective_session_key(slot)`; resolve bindings; `await _consume_pending_reset(state, slot)`; `client, is_new, resumed = await state.sessions.get_or_create(session_key, agent=..., model=..., cwd=slot.project or None, ...)` (`:4058`). The per-session semaphore is acquired here and released in `finally` (`SessionManager.release`).
2. `slot._acp_client = client.client` published so steer can reach the live `AcpClient` (`:4088`).
3. Message assembly: cancelled-turn preamble, subagent failures, `drain_pending_context(slot)` prefix, theme persona, `context_builder.build_message(...)` (skills/memory/history prefix on `is_new`), KiroCrew **script hooks** `AgentSpawn` (is_new) and `UserPromptSubmit` fired via `state._hook_store.fire(...)` whose stdout is prepended as `[Hook context]…` (`:4550-4557`; these are KiroCrew's own hooks from `/api/hooks`, unrelated to kiro-cli agent hooks).
4. `event_stream = client.stream_command(message) if is_slash else client.stream(full_message)`; WS `chat_status Thinking…` (`:4615-4619`). `stream` → `AcpSessionHandle.prompt` → ACP `session/prompt` with `build_prompt_blocks(full_message)`.
5. Event loop over `AcpEvent`s (`acp/types.py:436-492`: `kind ∈ {text_chunk, thinking_chunk, tool_call, tool_call_update, tool_result, permission_request, complete, compaction_status, todo_update, subagent_list, steer_queued, steer_consumed, steer_cleared, mcp_oauth_request, ...}`, fields `text, tool_call_id, title, tool_kind, tool_purpose, tool_input, tool_output, tool_final, request_id, options, raw_tool_params, is_shell, tool_name, mcp_server_name, stop_reason, usage, context_usage_pct, todo, diff_*`).
6. Permission flow: for `permission_request` the runner either auto-approves (trust/yolo/hooks/app-own MCP) or appends a `"permission"` row with `cls` JSON `{"request_id","tool_call_id","tool_input","is_read_only","tool_title","full_command","base_command","resolved"?}` (`:5745-5775`), registers `slot._approval_futures[request_id]`, `push_slots_update()`, mirrors to Slack if linked, then awaits the future (2h attended / 180s unattended). The `permission` row is broadcast as `chat_message` (role `permission`), and the frontend renders the approve/trust/reject buttons from it (`useWebSocket.ts:735-800`).
7. `complete` → `_fire(HOOK_EVENT_STOP, final_text)` (`:6732`), then `finally`: requeue unconsumed steers, `slot._acp_client=None`, `_finish_queue_cycle(state, slot)` → if queue non-empty `_start_next_queued_turn` else `slot.append("done","","done")`, `slot.task=None`, `push_slots_update()`, **`broadcast_ws("chat_done", {"slot"})`**, `refresh_slot_source_status`, `push_refresh("history")` (`:3477-3496`).

### 4.2 Deadline
- `turn_dispatch.spawn_guarded_turn(state, slot, coro, *, timeout_secs=None)` wraps every dashboard turn in `_bounded_turn(coro, chat_turn_timeout_secs())`; the absolute `loop.time()` deadline is published in `_TURN_DEADLINE: ContextVar[float|None]`, readable inside the turn via `_turn_budget_remaining()` (`turn_dispatch.py:51-70`). Ceiling = `agent.chat_turn_timeout_secs` clamped to the ACP prompt timeout (`resolve_prompt_timeout()`, `acp/client.py:754`); default `CHAT_TURN_TIMEOUT = 7200.0` s (`constants.py:54`). On expiry a visible error card is rendered and `TimeoutError("turn exceeded the Ns ceiling")` is raised in the task.
- There is no per-turn deadline field in `to_dict()`; an app must compute `started_at + ceiling` itself (`slot.messages[-1]["ts"]` of the user row, or wrap its own dispatch with `spawn_guarded_turn(..., timeout_secs=...)`).

### 4.3 Observing "turn done" in-process
Options, most reliable first:
1. Hold the `asyncio.Task` returned by `spawn_guarded_turn` / `slot.task` and `await` it or `add_done_callback`. `finish_turn_task` consumes exceptions; the task result is `None`.
2. Poll `slot.running` (`slot.task is not None and not slot.task.done()`) after `slot.event.wait()` (set on every `slot.append`, including the `done` row).
3. `state.register_sse()` queue: receives `chat_message` notes (`_type: "chat_message"`) for every non-chunk row incl. `system` stop events and `permission` rows, but **not** `chat_done` (that is `broadcast_ws`, WS-only; `state.py:5326-5405`, `:5466`).
4. HTTP polling: `GET /api/chat/slots/{slot}` `running` + last `done`/`stop_event` rows.
Session-level: `state.sessions.is_busy(effective_session_key(slot))` (`session.py:4077`) and `running_session_keys()` (`state.py:3780`).

### 4.4 Slot-level busy predicates an app must respect before dispatch (mirrors `api_chat_slot_continue`, `:1616-1720`)
`slot.running`, `slot._in_stage_execution`, `slot._stop_state != "idle"`, `slot.queue_depth`, any undone `slot._approval_futures`, `state.subagents.running_agents_for(effective_session_key(slot))`, `state.subagents._queued_depth(key)`, `slot._subagent_deliveries_inflight`, plus `slot._lock` to serialize check-then-dispatch.

---

## 5. Which paths are "user turns" to kiro-cli (S12 / FR-SES-003 evidence)

The AI-DLC Kiro adapter hook (`harness/kiro/hooks/aidlc-kiro-adapter.ts:96-133`) runs on `userPromptSubmit` with stdin `{"hook_event_name":"userPromptSubmit","cwd":..., "prompt":...}` (fixture `tests/fixtures/kiro-hook-payloads/payloads.json`), increments `aidlc/.aidlc-turn-counter`, and appends `HUMAN_TURN` when `aidlc-state.md` exists. It is registered in the **agent file**, so it fires for any `session/prompt` delivered to a kiro-cli session running `--agent aidlc` in that cwd. `humanActedSinceGate` (`core/tools/aidlc-lib.ts:1391-1421`) then admits a gate resolution iff a `HUMAN_TURN` follows the last `GATE_APPROVED/GATE_REJECTED/QUESTION_ANSWERED`.

| KiroCrew path | Reaches model via | Transcript row | Fires kiro-cli `userPromptSubmit`? |
|---|---|---|---|
| `POST /api/chat` idle | `_run_chat` → `session/prompt` | `user` | yes |
| `POST /api/chat` while busy (no steer) | queued → `_start_next_queued_turn` → `session/prompt` | `user` | yes (at drain) |
| `POST /api/chat` `steer:true` while busy | `_session/steer` (`<user_message>` wrapper) | `user` with `meta.steer=true` | **unknown** (kiro-cli extension; not observable in KiroCrew) |
| `POST /api/chat/slots/{slot}/continue` | synthetic recovery → `session/prompt` | `inject` | yes |
| `POST /api/chat/slots/{slot}/context` | none now; prepended to next `session/prompt` as `[Background context from "<source>"]…[End of background context]` | none | no turn; rides the next human prompt |
| Cron `send_message session=origin` (`handlers/messaging.py:1240-1310`) | `_run_chat(state, slot, wrapped)` or queue | `inject` (`[Cron notification from "<name>"]…`) | yes |
| Workflow completion (`workflow_inject.inject_workflow_result` + `on_injected`) | `_run_chat` | `assistant` row + auto turn | yes |
| Subagent completion (`[Subagent completion event]`, `constants.py:196`) | queue drain → `session/prompt` | `subagent` | yes |
| AutoNudge dashboard loop (`slack/gateway._fire_dashboard_nudge` `:3830-3945`) | `_run_chat(state, slot, "[auto-nudge cycle N]\n…")` | `nudge` | yes (drops the cycle if `slot.running`) |
| Slack reply in a linked thread (`slack/handler.maybe_route_linked_thread` `:2454+`) | `_run_chat` or `queue_append` | `user` | yes |
| Slack `[OPTIONS:]` click / `action::` button | `handle_message` (Slack thread session or pinned dashboard slot) | `user` | yes |
| KiroCrew script hooks (`/api/hooks`, `HookStore.fire`) | stdout prepended into the same `session/prompt` | — | n/a (same prompt) |
| `ctx.spawn.run(task, agent)` (SpawnSDK) | `SubagentManager.spawn` → separate kiro-cli session, `approval_mode="auto"`, agent must be `<app>--*` | — | fires in **its own** session/cwd, not the canonical one |

Conclusions for the PRD:
- **FR-SES-002 (human lane)** is satisfied by `POST /api/chat` (or in-process `spawn_guarded_turn(state, slot, _run_chat(...))`) to a slot whose `project` is the repo and `agent == "aidlc"`. The PRD's §7.5 observation (HUMAN_TURN 1→2) is exactly this path.
- **FR-SES-003 (machine lane)**: no KiroCrew path delivers text to the running kiro-cli conversation without `session/prompt` except `_session/steer`, and steer requires an in-flight turn. AutoNudge/cron/Continue **are** `session/prompt`. S12 must either (a) prove `_session/steer` does not fire `userPromptSubmit` (still unusable at a stable boundary because the turn must be running), or (b) change the AI-DLC Kiro adapter to recognise a machine prefix — which is prompt-selectable and violates "host-authenticated"; or (c) drive the engine directly (`bun .kiro/tools/aidlc-orchestrate.ts next/report`) from Studio without a model turn, which the PRD forbids for `report` (FR-GATE-006) but is what S13 asks for cursor switching.
- Because `_run_chat` prepends hook/skills/history context to `full_message`, the `prompt` the AI-DLC hook sees is **not** the raw human text; `extractNextArgs` (`adapter.ts:92-97`) matches `aidlc-orchestrate.ts next <ARGS>` inside the expanded skill body, so KiroCrew prefixes do not break the verb-intercept, but Studio must send the literal `/aidlc …` or the engine-supplied choice text unchanged (FR-GATE-002).

---

## 6. Structured questions (S1) and question-like signals

### 6.1 What KiroCrew can show as a structured card
1. **Claude Code `AskUserQuestion` tool_call** (`chat_runner.py:4849-4867`): only when `event.title == "AskUserQuestion"`; input validated by `validate_ask_user_question` (`validation.py:2751-2814`, limits `_ASK_MAX_QUESTIONS=4, _ASK_MAX_OPTIONS=6, question≤500, header≤50, label≤200, description≤500, answer≤2000`) → WS `question_card {"slot", "questions":[{"question","header","options":[{"label","description"}],"multiSelect"}]}` (no `ask_id`, no `card_id`). Not applicable to kiro-cli sessions.
2. **KiroCrew `ask_question` MCP tool** (`mcp_tools/control.py:168-233, 733-765`): stateless directive decoded by the runner (`session_directive_apply._ask_question` `:410-427`) → `state.post_question_card(slot_key, questions)` → owner-only WS `question_card {"slot","card_id":"card-<hex16>","questions","ts"}`; `slot._question_pending[card_id]` makes `needs_input=true`; the user's submit is sent as an ordinary `POST /api/chat` message (`PendingQuestionCard.tsx:173-183`), and `GET /api/ask-question/pending` re-hydrates. `ask_question` is only advertised to sessions with a dashboard surface. The AI-DLC `aidlc` agent's `tools` list is `fs_read, fs_write, execute_bash, todo_list, thinking, subagent` — **it does not include KiroCrew MCP tools**, so an AI-DLC conductor cannot call `ask_question`.
3. **Blocking `POST /api/ask-question`** `{"session_key","questions","timeout_secs"?}` → `question_card` with `ask_id`, answered by `POST /api/ask-question/{ask_id}/answer` `{"answers": {question: answer}}` or `{"dismissed": true}`; app tokens and non-owners are 403 (`handlers/ask_question.py:60-160`). This is a Studio→user channel, not an agent→Studio channel.
4. **`[OPTIONS: a | b | c]` trailer** in the final assistant text: parsed by `_parse_options`/`OPTIONS_RE_LINE` into `slot.to_dict().options`/`has_options`/`prompt_preview`; Slack renders it as checkboxes (`slack/format.build_options_blocks` `:83+`).
5. **Tool permission prompts** (`permission` rows, §4.1 step 6) carry `options` from ACP `session/request_permission` (`_build_permission_event`, `acp/client.py:5355-5520`: `[{"id","label"}]`, kiro-cli advertises `allow_once`/`allow_always` only). These are approvals, not questions.

### 6.2 What AI-DLC produces on the Kiro harness
- `harness/kiro/skills/aidlc/question-rendering.md`: "Kiro CLI has no structured-question tool, so every structured question renders as **numbered prose options in chat**, and the user answers with a number (or free text)" — `**Header** — prompt`, `1. **Label** — description`, always an appended `Other`, `Reply with a number (or just tell me).`
- The engine's `ask` directive is `{kind: "ask", question: string}` (`core/tools/aidlc-orchestrate.ts:231-232`); the conductor answers via `report --user-input "<answer>"` (`SKILL.md:55`).
- Persisted evidence: `<record>/<phase>/<stage>/<stage>-questions.md` with `[Answer]:` tags (`stage-protocol.md:324-335, 860`); the Stop hook's `isPendingQuestionStop` reads blank tags to release the turn (`core/hooks/aidlc-stop.ts:372-390`), and `isHumanWaitStop` releases at `[?]`/`[R]` (`:360-370`).
- Therefore, for S1 on kiro-cli: **no structured live payload exists**; Studio gets prose in `assistant` rows plus the questions file. The Claude Code annex (`harness/claude/skills/aidlc/question-rendering.md`) maps the spec 1:1 onto `AskUserQuestion`, which KiroCrew *does* surface — but only on the `claude_code` provider.

### 6.3 Frontend rendering (for parity)
- `QuestionCard.tsx` props `{questions: {question, header?, options:[{label, description?}], multiSelect?}[], onSubmit(answers: Record<questionText, answer>), onDismiss, busy}`; answers keyed by question text; custom free-text wins over picks.
- `useWebSocket.ts` cases: `question_card` (988), `question_card_resolved` (995), `approval` (735), `tool_call` (934), `tool_result` (980), `chat_done` (1186), `chat_message` role filter `user|assistant|tool_call|tool_result` for unread marking (853).
- Tool rows: `pages/chat/ToolCallLine.tsx`, `CollapsibleToolGroup.tsx`; permission rows matched on `m.role === 'permission' && m.meta.tool_call_id`.

---

## 7. Notifications, Slack, deep links (S6/S7)

### 7.1 Dashboard notifications (in-process)
- `state.notify(kind, title, body, *, meta=None, url=None, actions=None)` (`state.py:3548-3585`) → `NotificationBus.push(payload_from_legacy(...))`. Apps with a token use `POST /api/notifications/push` (always allowed for app tokens, rate-limited, channel must be declared in manifest `notifications`).
- `NotificationPayload` (`notifications/bus.py:130-262`): `source, channel, title (≤500), body (≤20000), priority ∈ {critical, default, passive}, kind, group_key, actions (≤4 of {"id"≤64,"label"≤40,"url"?≤500}), url, icon, ttl (>0 int)`. `url` and every `actions[].url` must pass `_validate_internal_url`: start with `/`, not `//`, no backslash/tab/CR/LF — **dashboard-internal paths only; external links are rejected**. Actions without `url` render nothing today ("identifier for future dispatch semantics").
- Delivery sink `_deliver_note` redacts every string, applies channel mute settings, appends to `~/.kiro/crew/notifications.jsonl`, broadcasts `{"type":"notification","data":note}` on WS and to SSE queues.
- MCP path for agents: `POST /api/notifications/agent` (internal-secret only, `handlers/messaging.py:896-1019`).

### 7.2 Slack owner DM with quick actions
- `state.slack_client` (`slack/client.py`): `open_dm(user_id) -> channel_id` (`:383`), `post_message(channel, text, thread_ts=None, ...) -> ts` (`:258`), `post_blocks(channel, blocks, text, thread_ts=None, ...) -> ts` (`:280`), `update_message(channel, ts, text="", blocks=None)` (`:303`), `post_ephemeral`. `state.owner_id` is the owner's Slack user id.
- HTTP equivalent: `POST /api/send-message` `{"text", "title"?, "blocks"?, "channel"?|"user"?, "thread_ts"?, "reply_broadcast"?, "unfurl_links"?, "unfurl_media"?, "session"?: "origin"|"slack", "caller_session"?}` (`handlers/messaging.py:1087-1420`). `user` must be an allowlisted Slack user; without `channel`/`user` and with `session:"slack"` it DMs `state.owner_id`. If `text` ends with `[OPTIONS: a | b]` and no `blocks` are given, a checkbox block is appended (`build_options_blocks`) with a staleness token in `block_id` (`slack/outbound.encode_options_token(session_key,row_ts)` `:84-106`). This endpoint is `_STRICT_INTERNAL_API_PATHS` (internal secret only, `server.py:263`), so an in-process app should call `state.slack_client` directly instead.
- **Interactive callback routing** (`slack/interactions.dispatch` `:598-1045`): all clicks require `is_allowed_user(user_id)`. Recognised `action_id`s: `options_checkboxes` (no-op), `options_submit` → `_handle_options_submit` (`:1555-1816`) → re-dispatches the selection as a Slack-thread user turn via `handle_message(..., action_context="--- CONTEXT ENTRY BEGIN ---\n[OPTIONS multi-select: <sel>]\n--- CONTEXT ENTRY END ---", target_slot_name=<pinned dashboard slot>, route_pinned=...)`; `options_choice_<i>` legacy buttons; buttons whose `value` or `action_id` starts with `action::<json>` → `_route_action_to_session` (`:1342-1418`) which posts the label as a user message and runs `handle_message` with `action_context` `[Action button clicked: <payload>]`; `approve_tool|trust_tool|reject_tool` → `_handle_tool_approval` (`:3142-3200`) → `state.resolve_approval`; `cron_ack_*`, `subagent_ack_*`, `mc_stop*`, review-mode ids. Unknown ids are ignored.
- Consequence for S7: **an app cannot register its own Slack `action_id` handler**; the only reusable correlation is (a) post `[OPTIONS:]`/`action::` buttons in a thread **linked to the canonical dashboard slot**, so the click becomes a user turn on that slot (`maybe_route_linked_thread` → `_run_chat(_dashboard_state, _linked_slot, text)`), or (b) post plain text with a deep link and let the user act in Studio. Option (a) is exactly a human lane submission (fires `userPromptSubmit`), with the text = the button label — so the label must be the canonical wire text (FR-GATE-002).
- Linking a dashboard slot to a Slack thread: `POST /api/chat/slots/{slot}/slack-link` `{"channel"?: "dm"|<id>, "thread_ts"?}` (`dashboard/chat_slack.py:324+`) → `state.sessions.set_slack_link(session_key, thread_ts, channel)` + `state.link_slack(...)`; `slack-unlink`, `slack-pause`. Inbound replies then route to the slot; approvals are mirrored to the thread (`chat_runner.py:5780+`).

### 7.3 Deep links
- Chat: `/chat?sid=<slot-key>` (legacy `?slot=`), `ChatPage.tsx:3261-3270, 3411`; route `/chat/:slug?` (`App.tsx:2554`). App page: `/aidlc-console` style routes declared in `app.json` `ui.pages[].route`.
- App SDK: `useChatLauncher().openChat({agent?, message?})` writes `window.__mc_chat_launch` and navigates to `/chat` (`app-sdk/index.ts:161-195`); `useNavigate()`; `useChatSession({workspacePath, label, agent})` creates/reuses a slot via `POST /api/chat/slots {name, agent}` and seeds it with `POST /api/chat {message, slot, agent}` (`useChatSession.ts:89-113`).
- `dashboard/urls.py` gives `parse_dashboard_url`, `dashboard_origin` for building absolute URLs from `dashboard.url` config; notifications must stay path-only.

---

## 8. Silent context vs user turn (question 7)

| | `POST /api/chat/slots/{slot}/context` | `POST /api/chat` |
|---|---|---|
| Handler | `api_chat_slot_context` `:4113-4213` | `api_chat` `:206+` |
| Body | `{"content" (≤40000), "source"?, "ephemeral"?=true, "maxAge"? secs}` | `{"message", "slot", ...}` |
| Effect | appends to `slot._pending_context` (cap 50 total, 10 per source → 429) | appends `user` row, starts turn |
| Turn started | **no** | yes (or queue/steer) |
| Visible row | none | `user` |
| Delivery | prepended to the next `_run_chat` message as `[Background context from "<source>"]\n<content>\n[End of background context]\n` (`drain_pending_context`), expired entries dropped | the message itself |
| Fires `userPromptSubmit` | only as part of the next human prompt | yes |
| Persisted | not as a row; becomes part of the prompt text kiro-cli logs | JSONL row |

The App Kit doc (`docs/app-kit/api-reference.md:398-409`) describes this correctly. **It is not a machine lane**: nothing is sent until a human (or any other) turn starts.

Also available but different: the "side conversation" (`POST /api/chat/slots/{slot}/side/open|turn|close`, `handlers/side.py`) runs on an **isolated** session `side:<slot>` (`_side_session_key` `:49-51`) with a read-only snapshot of the parent transcript in the prompt — a reasonable substrate for FR-ADV-002 (Advisor) if the cwd is not the repo and the agent is not `aidlc`; it never touches the canonical session.

---

## 9. Python-internal recipe for the human-submission lane (in-process)

```python
# inside an AppRoute handler: async def submit(request, ctx)
from kiro_crew.dashboard.chat_runner import _run_chat
from kiro_crew.dashboard.turn_dispatch import spawn_guarded_turn
from kiro_crew.dashboard.chat_utils import effective_session_key
from kiro_crew.dashboard.handlers.source_providers import is_owner_dashboard_request

state = request.app["state"]                      # DashboardState
if not is_owner_dashboard_request(request): ...   # 403
slot = state.get_slot(slot_key)                   # or state.get_or_create_slot(name, agent="aidlc", app="aidlc-studio")
# bind cwd + agent on a NEW slot: slot.project = repo_path (realpath, not sensitive) ; slot.agent = "aidlc"
async with slot._lock:
    if slot.running or slot._in_stage_execution or slot._stop_state != "idle" or slot.queue_depth \
       or any(not f.done() for f in slot._approval_futures.values()): ...  # 409 busy
    slot.append("user", text, "msg msg-u", meta={"studio_action_id": action_id})
    slot._human_seen = True
    task = spawn_guarded_turn(state, slot, state.run_background_turn(slot, _run_chat(state, slot, text)))
    slot.task = task
    state.push_slots_update()
task.add_done_callback(lambda t: ...)             # turn boundary; then reconcile from disk
```
Notes: `_run_chat` and `spawn_guarded_turn` are private-by-convention but are exactly what `api_chat`, cron and AutoNudge call; a `meta` dict on the user row is persisted and broadcast (`state.py:1499-1640`) — a place to carry the Studio `action_id` for later reconciliation. Cancel: `await state.sessions.stop_turn(effective_session_key(slot), force=False, preserve_queue=True)`. Prefer the HTTP routes (`/api/chat`, `/stop`) from the Studio UI when possible: they are the dashboard's own contract and add the SEL audit rows.

---

## 10. Doc-vs-code discrepancies noticed
- `history.py:3` docstring says sessions live in `~/.kirocrew/sessions/`; code uses `config_dir()/sessions` = `~/.kiro/crew/sessions` (`history.py:758`, `config/paths.py:43-44`).
- `docs/app-kit/api-reference.md` lists `sendMessage(slotId, message)`/`createSlot(name, agent?)` for the Python/TS client; the actual server bodies are `POST /api/chat {message, slot}` and `POST /api/chat/slots {name, agent}` as above (matches `migration-guide.md:73-76`).
- `manifest-reference.md:258-262` says permissions are advisory; `permissions.api` **is** enforced for app tokens (`token_auth.app_token_path_allowed`).
- PRD §7.7 lists "cron, AutoNudge, App-driven continuation" as unverified machine paths; code shows all three are `session/prompt` user turns (Table §5).

---

## 11. Open questions (not resolvable from code)
1. Does kiro-cli fire `userPromptSubmit` (and thus AI-DLC `HUMAN_TURN`) for `_session/steer` injections? Not observable in KiroCrew; needs a live test.
2. Does kiro-cli fire `userPromptSubmit` for ACP `session/load` (resume) or for the cancelled-turn preamble re-injection? The AI-DLC turn counter would drift if so.
3. Which kiro-cli version added `_session/steer` and `ACP_BACKENDS_STEER` membership — is it stable enough for Studio's "Request changes while running" case?
4. Is there any host-authenticated provenance kiro-cli exposes to agent hooks (e.g. a session/prompt `_meta` field that reaches the hook stdin)? `build_prompt_blocks` sends only `{"type":"text"}` blocks; kiro-cli's hook payload fixture has only `hook_event_name, cwd, prompt`.
5. Will the AI-DLC maintainers accept a Kiro annex that renders questions through a structured tool (KiroCrew `ask_question` requires the KiroCrew MCP server in the agent's tool list, which `aidlc.json` does not include)?
6. Exact behaviour of `state.sessions.get_or_create(... cwd=...)` when the same `dashboard:<slot>` key was last used with a different cwd (project change without reset) — the deferred reset path handles the dashboard case, but an in-process caller that mutates `slot.project` directly must call `await state.sessions.reset(_history_key_for(slot.key))` itself (as `api_chat_slot_project` schedules).

---

## Critic addendum (2026-09-04, completeness pass)

### A1. Version caveats for this document

- KiroCrew facts were read from the 0.3.0 checkout; the gateway actually running is `0.5.0-insider.9` (see doc 01 §0). Every entry point the §9 recipe relies on still exists in 0.5.0 — signatures below.
- AI-DLC facts were read from `/Users/ychchen/warren_ws/aidlc-workflows` (2.2.10). The engine installed in the real projects on this machine is **2.6.2** (`/Users/ychchen/warren_ws/aidlc/wt-v2-triage`, git `4569754e`; see doc 04 §0). The `aidlc.json` hook table in §5 is unchanged in shape (still an agent-file `userPromptSubmit` hook → `verb-intercept`), but 2.6.2 also touches the `.aidlc-human-turn` marker (`harness/kiro/hooks/aidlc-kiro-adapter.ts:236-237`: `appendAuditEntry("HUMAN_TURN", {}, cwd); markHumanTurn(cwd);`, gated on `existsSync(stateFilePath(cwd))` at :235). The conclusion "every `session/prompt` is a human turn to AI-DLC" holds for 2.6.2.
- §2.1 "Sibling relative imports do not work" is true on 0.3.0 only; 0.5.0 registers namespace packages (doc 01 addendum A1).

### A2. 0.5.0 signatures for the §9 in-process human lane (verbatim from the bundle)

```python
# dashboard/turn_dispatch.py:377
def spawn_guarded_turn(state, slot, coro, *, timeout_secs: float | None = None) -> asyncio.Task
# dashboard/chat_runner.py:4505
async def _run_chat(state: DashboardState, slot: _ChatSlot, message: str, *,
                    _prompt_depth: int = 0, _synthetic_payload: bool = False,
                    _directive_user_origin: bool = False, regenerate_hint: str = "",
                    _on_consumed=None, _on_irreversibly_consumed=None) -> None
# dashboard/state.py:5826
async def run_background_turn(self, slot, coro)
# dashboard/state.py:6992
def get_or_create_slot(self, name=None, agent="", workspace="default", model="", mode="",
                       memory_mode=None, ephemeral=None, app="", linked_session_key="",
                       channel_origin=False, origin: str | None = None, *,
                       count_user_session: bool = False) -> _ChatSlot
# dashboard/state.py:3564
def append(self, role: str, content: str, cls: str = "", ts: str = "", *,
           broadcast: bool = True, broadcast_user: bool = False, meta: dict | None = None) -> dict
# dashboard/handlers/source_providers.py:4283 (owner subjects at :4228 = frozenset({"local-app","local-startup"}))
def is_owner_dashboard_request(request: web.Request) -> bool
# session.py:5805 / :5397
async def stop_turn(self, key, *, force=False, preserve_queue=False, on_soft=None, on_hard=None) -> StopOutcome
def is_busy(self, key: str) -> bool
# apps/spawn_sdk.py:92
async def run(self, task: str, agent: str = "", *, silent: bool = False, model: str = "") -> str
```

`api_chat` in 0.5.0 (`dashboard/chat_handlers.py:826-838`) dispatches exactly:
`task = spawn_guarded_turn(state, slot, state.run_background_turn(slot, _run_chat(state, slot, message, _directive_user_origin=not bool(request_app)))); slot.task = task`.
`_directive_user_origin` is KiroCrew's "authenticated-human provenance" flag for the directives a turn consumes (`chat_runner.py:3885`); a Studio human-lane dispatch should pass `_directive_user_origin=True` (as a dashboard-user call does), a machine dispatch `False`. It affects KiroCrew's own directive handling only — it does NOT reach kiro-cli or the AI-DLC hook, so it is not a provenance signal for `HUMAN_TURN`.

Handler line numbers in 0.5.0 for the HTTP alternative: `api_chat` :181, `api_chat_slot_detail` :1688, `api_chat_slot_create` :1968, `api_chat_slot_stop` :2762, `api_chat_slot_continue` :2792, `api_chat_slot_agent` :4012, `api_chat_slot_project` :4805, `api_chat_slot_context` :6839.

### A3. Why the backend (not the UI) must perform the send

PRD §11.5 requires the `Delivering` record to be durably committed **before** calling the host submission API. If the Studio UI posted to `POST /api/chat` itself (cookie auth, `/api/chat` added to `permissions.api`), the backend could not write-before-send. If the backend called `POST /api/chat` over HTTP it would need an app token (`POST /api/apps/aidlc-studio/token` with `X-App-Secret`, doc 01 §1.3), `/api/chat` in `permissions.api`, and the slot would have to carry `_app == "aidlc-studio"` (chat handlers 404 app tokens on foreign slots, §2.3). The in-process path (§9 + A2) avoids both and is what `api_chat`, cron and AutoNudge already use. Trade-off: the functions are private-by-convention and must be pinned by a Studio startup self-test (import + signature check → `ctx.health.mark_degraded` on mismatch).

### A4. 2.6.2 corrections relevant to §5 / §6

- Request Changes at a gate now goes through the engine: `report --stage <slug> --result rejected --user-input "<feedback>"` then `report --result revised` (see doc 05 addendum). The human's chat reply is still free prose interpreted by the conductor; nothing changes for the transport analysis.
- The Kiro annex `question-rendering.md` (2.6.2) still renders every structured question as numbered prose and does not emit an `[OPTIONS: …]` trailer; `slot.to_dict().options` will normally be empty for AI-DLC turns. S1 degraded mode stands.
- New 2.6.2 verb `aidlc-orchestrate.ts continue "<token>"` is the `load-steering` chunk-resume; it is HMAC-token-gated, state-hash-checked, mutates nothing and cannot pass a gate (doc 05 addendum A1). It is not a machine lane.

### A5. Slack quick actions (PRD `POST /slack/actions/callback`) — still unresolved

No app-registered Slack `action_id` handler exists (§7.2). The only reusable correlation is posting `[OPTIONS:]`/`action::` buttons into a thread linked to the canonical dashboard slot, which re-dispatches the click as a user turn on that slot. The PRD's "host-authenticated correlation endpoint" would require a host change (S7). Recorded as a decision in `00-index.md`.

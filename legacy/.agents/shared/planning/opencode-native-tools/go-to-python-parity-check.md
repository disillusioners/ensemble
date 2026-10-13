# Go-to-Python Parity Check: opencode-native-tools

**Reviewer**: orchestrator
**Date**: 2026-06-06
**Go source**: `.inspiration-projects/opencode_skill_src/`
**Plan**: `.agents/shared/planning/opencode-native-tools/`
**Status**: Planning phase — all 5 phases drafted, not yet implemented

---

## Executive Summary

The plan covers **~75% of Go functionality** in its current form. The 8 native Python tools map well to the 8 external-facing operations, and the core state machine, 30s polling, 1h worker timeout, and crash recovery are all described. However, **8 critical pieces are missing or undocumented**, and **5 behavioral subtleties** need verification. The most serious gap is the **camelCase JSON field name mismatch** between Go's Pydantic models (snake_case) and the OpenCode API (camelCase), which will cause silent API rejections at runtime.

---

## Legend

| Symbol | Meaning |
|--------|---------|
| 🔴 | **Critical** — missing functionality that will break at runtime |
| 🟡 | **Warning** — partial coverage, edge cases, or undocumented behavior |
| 🟢 | **OK** — fully covered |

---

## Section 1: CLI Commands (main.go)

The plan replaces the Go CLI entirely with 8 Python tools. The mapping is:

| Go CLI | Python Tool | Status | Notes |
|--------|-------------|--------|-------|
| `init-session <project> <session> <dir>` | `external_opencode_init_session` | 🟢 OK | Covered in phase3 |
| `--sync` flag | (inherent in `wait_for_result`) | 🟢 OK | Blocking tools replace sync flag |
| `--quiet` flag | (not in plan) | 🟡 Warning | CLI-specific; Python tools don't print to stdout — OK |
| `--council` flag | (not in plan) | 🟡 Warning | `--council` appends `CouncilHint` text to prompt. Plan's `send_message` tool has no equivalent. May not be needed if agents handle this internally. |
| `--agent` flag | `agent` param on `send_message` | 🟢 OK | Passed through |
| `--model` flag | `model` param on `send_message` | 🟢 OK | Passed through |
| `start` command | (removed) | 🟢 OK | No daemon to start; runs in-process |
| `stop` command | (removed) | 🟢 OK | No daemon to stop |
| `restart` command | (removed) | 🟢 OK | No daemon to restart |
| `/wait` command | `external_opencode_wait_for_result` | 🟢 OK | Covered in phase3 |
| `/status` command | `external_opencode_get_status` | 🟢 OK | Covered in phase3 |
| `/resume` command | `external_opencode_resume_session` | 🟢 OK | Covered in phase3 |
| `/answer` command | `external_opencode_answer_question` | 🟢 OK | Covered in phase3 |
| `wait_any` command | `external_opencode_wait_any` | 🟢 OK | Covered in phase3 |
| `abort` command | `external_opencode_abort_session` | 🟢 OK | Covered in phase3 |
| `@file.txt` syntax | (removed) | 🟡 Warning | Explicitly removed in plan. "Agents pass full message text directly." This is acceptable but changes agent behavior — they must include file contents explicitly. |
| `config list` | (not in plan) | 🟡 Warning | No Python tool for listing config. Agents use env vars + data directory. |
| `config get model` | (not in plan) | 🟡 Warning | Same — no config query tool |
| `config set model` | (not in plan) | 🟡 Warning | Same — no config write tool |

**Section 1 Assessment**: 🟡 The 8 core lifecycle tools are well-covered. Config management commands are missing but may not be needed since agents use config files directly.

---

## Section 2: Go API Client (internal/api/client.go)

| Go Method | HTTP | Python Equivalent (phase2-plan) | Status |
|-----------|------|---------------------------------|--------|
| `CreateSession(title)` | POST /session | `create_session(title)` → POST /session | 🟢 OK |
| `SendPrompt(sessionID, req)` | POST /session/{id}/message | `send_prompt(session_id, req)` → POST /session/{id}/message | 🟢 OK |
| `SendCommand(sessionID, req)` | POST /session/{id}/command | `send_command(session_id, req)` → POST /session/{id}/command | 🟢 OK |
| `GetQuestions()` | GET /question | `get_questions()` → GET /question | 🟢 OK |
| `AnswerQuestion(req)` | POST /question/{id}/reply | `answer_question(request_id, req)` → POST /question/{id}/reply | 🟢 OK |
| `AbortSession(sessionID)` | POST /session/{id}/abort | `abort_session(session_id)` → POST /session/{id}/abort | 🟢 OK |
| `ResumeSession(sessionID)` | POST /session/{id}/message | `resume_session(session_id)` → POST /session/{id}/message | 🟢 OK |
| `GetSessionMessages(sessionID)` | GET /session/{id}/message?limit=1 | `get_session_messages(session_id, limit=1)` → GET /session/{id}/message?limit=1 | 🟢 OK |

**Additional Go client behaviors not captured in plan signatures**:
- `doRequestWithContext` (context support) — Python plan uses no context. Not needed for this use case.
- `base64Encode` — Covered in `_build_headers()` method.
- HTTP 1-hour timeout — **Not explicit in Python plan**. Phase2 says `timeout: float = 3600.0` on client init, matching Go. ✅
- `GetQuestions` dual-parse — Go tries `[]Question` then `{data: [...]}`. Phase5 tests cover this with `test_get_questions_array` and `test_get_questions_dict`. The plan code (phase2) shows `...` for the body, so it's inferred. 🟡 Should be explicit in phase2, not buried in tests.

**Section 2 Assessment**: 🟢 All 8 methods are present. Dual-parse logic needs to be explicit in phase2 plan body.

---

## Section 3: Request/Response Types (camelCase Issue)

**🔴 CRITICAL**: This is the most significant technical gap in the plan.

The Go structs use explicit `json:"..."` tags with **camelCase**:

```go
// internal/api/types.go
type PromptRequest struct {
    Agent string       `json:"agent"`
    Model ModelDetails `json:"model"`
    Parts []Part       `json:"parts"`
}
type ModelDetails struct {
    ProviderID string `json:"providerID"`  // camelCase
    ModelID    string `json:"modelID"`     // camelCase
}
type AnswerRequest struct {
    RequestID string     `json:"requestID"`  // camelCase
    Answers   [][]string `json:"answers"`
}
type Question struct {
    ID        string `json:"id"`
    SessionID string `json:"sessionID"`   // camelCase
    Questions []struct{ ... }
}
```

The Python plan uses Pydantic v2 with **snake_case** field names by default:

```python
# phase2-plan.md
class ModelDetails(BaseModel):
    provider_id: str = "litellm"   # Python snake_case
    model_id: str = "coding"       # Python snake_case

class AnswerRequest(BaseModel):
    request_id: str = ""           # snake_case — MISMATCH
    answers: list[list[str]] = []

class Question(BaseModel):
    session_id: str = ""           # snake_case — MISMATCH
```

**Pydantic v2 default serialization** (`model_dump()`) outputs snake_case JSON fields. Unless the plan configures `alias_generator=to_camel` or `Field(..., validation_alias="providerID")`, the serialized JSON will look like:

```json
// What Python WOULD send (wrong):
{"model": {"provider_id": "litellm", "model_id": "coding"}}

// What OpenCode API expects (camelCase):
{"model": {"providerID": "litellm", "modelID": "coding"}}
```

**Impact**: Silent API rejection. OpenCode would return an error or ignore the model parameters entirely.

**Fix needed**: Add `model_config` to Pydantic models:
```python
class ModelDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    # OR use alias_generator = to_camel
    provider_id: str = Field(default="litellm", alias="providerID")
    model_id: str = Field(default="coding", alias="modelID")
```

This affects: `PromptRequest`, `CommandRequest`, `AnswerRequest`, `Question` — all need `session_id` → `sessionID`, `provider_id` → `providerID`, `model_id` → `modelID`, `request_id` → `requestID`.

The plan's risk section (plan-overview.md) does not mention this issue.

**Section 3 Assessment**: 🔴 Critical. camelCase JSON field names must be explicitly configured in all Pydantic models.

---

## Section 4: Go Registry (internal/daemon/registry.go)

| Go Method | Python Equivalent (phase1-plan) | Status | Notes |
|-----------|---------------------------------|--------|-------|
| `NewRegistry(dbPath)` | `create_opencode_session_repository(engine)` | 🟢 OK | Creates table; matches Go behavior |
| `Create(project, sessionName, id, workingDir)` | `create(project, session_name, session_id, working_dir)` | 🟢 OK | Returns ErrDuplicate → ValueError |
| `Get(project, sessionName)` | `get(project, session_name)` | 🟢 OK | Returns None if not found |
| `List()` | `list_all()` | 🟢 OK | Ordered by project, session_name |
| `Delete(project, sessionName)` | `delete(project, session_name)` | 🟢 OK | Returns False if not found |
| `UpdateAgentState(project, sessionName, lastAgent, isLocked)` | `update_agent_state(project, session_name, last_agent, is_locked)` | 🟢 OK | Sets last_agent + is_agent_locked |
| `UpdateState(project, sessionName, state)` | `update_state(project, session_name, state)` | 🟢 OK | Sets only state |
| `UpdateLastActivity(project, sessionName, timestamp)` | **NOT in plan** | 🔴 Missing | Called by Go server_persistence on every state change |
| `UpdateSessionData(project, sessionName, session)` | `update_session_data(project, session_name, session)` | 🟢 OK | Bulk update all dynamic fields |
| `FindByID(sessionID)` | `find_by_id(session_id)` | 🟢 OK | SELECT WHERE id=? |
| `Close()` | **Not in plan** | 🟡 Warning | No Close() on repository. Relies on engine lifecycle. May be OK. |
| Default `last_agent = "sisyphus"` | **Not in plan** | 🟡 Warning | Go manager.go line 78 sets default agent to "sisyphus". Python model defaults to `""`. Minor behavioral difference. |

**Section 4 Assessment**: 🟡 `update_last_activity` is missing from the plan's repository interface. The plan's `_persist_state` method calls `on_state_change` with `last_activity`, but there's no repository method to update it independently. This is needed for the two-step persistence pattern (see Section 8).

---

## Section 5: Go Server Actions (internal/daemon/server.go)

The Go daemon handles 12 TCP actions. The plan replaces TCP/IP with in-process method calls:

| Go Action | Plan Coverage | Status | Notes |
|-----------|--------------|--------|-------|
| `PING` | Not needed | 🟢 OK | Internal daemon health; no Python equivalent needed |
| `START_SESSION` | Not exposed as tool | 🟡 Partial | Updates working dir, creates manager, sets up persistence. Plan's `OpenCodeSessionRegistry.get_or_create()` handles this internally. |
| `GET_STATUS` | `external_opencode_get_status` | 🟢 OK | Calls `sync_state_with_opencode()` |
| `GET_MULTI_STATUS` | Used by `wait_any` internally | 🟢 OK | Not a separate tool; plan's `wait_any` implements it |
| `INIT_SESSION` | `external_opencode_init_session` | 🟢 OK | Creates via client, saves to registry, aborts old |
| `ABORT_SESSION` | `external_opencode_abort_session` | 🟢 OK | Remote abort + local reset |
| `LIST_SESSIONS` | Not exposed | 🟡 Warning | No tool for listing all sessions. `list_sessions()` in registry exists but not exposed. |
| `GET_SESSION` | Not exposed | 🟡 Warning | No tool for getting session by name |
| `PROMPT` | `external_opencode_send_message` | 🟡 Partial | See Section 7 for BUSY rejection, agent lock, special prompts |
| `COMMAND` | Via `send_message` | 🟡 Partial | Plan's `send_message` handles commands via the `message` param; special commands like "start-work" → atlas lock missing |
| `ANSWER` | `external_opencode_answer_question` | 🟡 Partial | See Section 7j — removes answered question from list |
| `RESUME` | `external_opencode_resume_session` | 🟢 OK | Sends "resume" prompt |

**Section 5 Assessment**: 🟡 8 tools cover external use cases well. Internal server actions (start_session, get_session, list_sessions) are covered internally but not exposed.

---

## Section 6: Config (internal/config/config.go)

**🔴 CRITICAL BREAKING CHANGE**: The plan documents a config path and field change without migration strategy.

| Aspect | Go | Python Plan | Status |
|--------|-----|-------------|--------|
| Config path | `~/.opencode_skill/config.json` | `{data_dir}/opencode_skill.json` | 🔴 Breaking — different path |
| `defaultModel` field | camelCase JSON | `default_model` snake_case | 🔴 Breaking — different field name |
| `apiUser` field | camelCase JSON | `api_user` snake_case | 🔴 Breaking — different field name |
| `apiKey` field | camelCase JSON | `api_key` snake_case | 🔴 Breaking — different field name |
| New field | — | `opencode_url` | 🟡 Added — Go had it as const |
| `CouncilHint` | Present in config.go | Not in plan | 🟡 Warning — this was a CLI-level feature; agents handle it internally |
| Default values | `"orchestrator"`, `"litellm/coding"`, `"opencode"/"opencode"` | Same defaults | 🟢 OK |
| Caching | sync.RWMutex double-checked locking | Not specified | 🟡 Could use singleton pattern |
| Atomic write | Not atomic (plain `WriteFile`) | Plan says "atomic write" | 🟢 Plan is better |

**Impact**: Users with existing `~/.opencode_skill/config.json` will have their config silently ignored. The new code will fall back to defaults.

**Fix needed**: Either (a) document the breaking change and provide a migration path, or (b) support reading the old format for backward compatibility.

The plan's risk section does mention "Existing agents break after skill.md change" but does NOT mention the config file format break.

**Section 6 Assessment**: 🔴 Critical. Config path AND field names changed without migration documentation.

---

## Section 7: Manager Specific Logic (internal/manager/manager.go)

### 7a. `stripMessageBloat` (lines 252–311)

**🔴 MISSING**: Not mentioned anywhere in the plan.

This Go function strips 60 lines of verbose fields from OpenCode responses:
- `info`: keeps only `id`, `finish`, `error`, `time.completed`, `time.created`
- `parts`: keeps only `type`, `text`, `reason`, `error`
- Everything else (snapshots, token counts, IDs, etc.) is discarded

The plan's `_persist_state` and state derivation code calls `stripMessageBloat(res.Result)` in `handleWorkerDone` (line 508) and `stripMessageBloat(lastMessage)` in `SyncStateWithOpenCode` (line 200). Without this, the `latest_response` field in the DB will contain massive, noisy JSON blobs.

**Section 7a**: 🔴 Critical. Must be ported as a helper function.

### 7b. `getMessageFinish` (lines 221–236)

**🟢 OK**: Described in phase2 plan ("parse step-finish parts from messages exactly like Go").

### 7c. `hasMessageError` (lines 240–248)

**🟢 OK**: Described in phase2 plan ("unknown + error → IDLE").

### 7d. `restoreFromPersistedState` (lines 88–113)

**🟢 OK**: Phase2 plan's `OpenCodeSessionManager` restores from PersistedState on init, and `recover_from_registry()` handles startup recovery.

### 7e. `saveStateLocked` (lines 115–127)

**🟡 Partial**: The plan has `_persist_state` (lines 315–326) which calls the `on_state_change` callback. The actual serialization (`LastActivity.Format(time.RFC3339)`, JSON marshaling of Questions and Response) is implicit. Go explicitly shows the RFC3339 format — the plan should verify this.

### 7f. `SetLastAgent` (lines 135–145)

**🔴 MISSING**: Not in plan. Used in Go for agent lock updates. The plan's `_persist_state` calls `on_state_change` with `last_agent`, but doesn't expose `SetLastAgent` as a method. The server.go `start-work` handler calls `SetLastAgent("atlas")` — this is critical for the `start-work` → atlas lock behavior (see 7k).

### 7g. `SetAgentLocked` (lines 147–151)

**🔴 MISSING**: Not in plan. The `is_agent_locked` flag affects which agent name is used in PROMPT/COMMAND (server.go lines 465–472: overrides `req.Payload["agent"]`). The Python plan must implement this agent override logic.

### 7h. `pollQuestions` (lines 541–574)

**🟢 OK**: Described in phase2 plan with 30s polling interval. Session filter by `session_id` confirmed.

### 7i. Timeout → remote abort (lines 496–501, 528–533)

**🔴 MISSING**: The Go code has a critical behavior: when `handleWorkerDone` sees `net.Error.Timeout()` (HTTP 1-hour timeout), it calls `client.AbortSession()` on the remote to clean up resources, then sets state to IDLE with timeout error.

```go
// manager.go lines 496-501
if netErr, ok := res.Error.(net.Error); ok && netErr.Timeout() {
    needAbort = true
    sm.LatestResponse = map[string]interface{}{"error": "timeout after 1 hour"}
}
// lines 528-533
if needAbort {
    if err := client.AbortSession(sm.SessionID); err != nil {
        log.Printf("Failed to abort session after timeout: %v", err)
    }
}
```

The plan says "Worker timeout: 1 hour (matching Go)" but does NOT describe the auto-abort behavior. The `external_opencode_resume_session` tool exists for user-initiated resume, but the auto-abort-on-timeout is missing.

### 7j. Answer removes answered question from list (lines 420–434)

**🔴 MISSING**: After `AnswerQuestion` succeeds, Go removes that question from `sm.Questions`:

```go
newQuestions := []api.Question{}
for _, q := range sm.Questions {
    if q.ID != payload.RequestID {
        newQuestions = append(newQuestions, q)
    }
}
sm.Questions = newQuestions
```

Then it checks if questions are empty to set state. The plan's `answer_question` tool description does not mention removing the question from the local list. If not implemented, the same question would be returned on every subsequent `get_status` call.

### 7k. `start-work` → atlas agent lock (server.go lines 436–444)

**🔴 MISSING**: When a prompt text is `"start-work"` (normalized, no leading slash), Go server:
1. Looks up session by ID in registry
2. Calls `UpdateAgentState(sessionData.Project, sessionData.SessionName, "atlas", true)`
3. Logs "Locked agent to 'atlas'"
4. All subsequent PROMPT/COMMAND payloads have their `agent` field overridden to `"atlas"`

The plan's `send_message` tool accepts an `agent` parameter, but does not mention this override behavior. This is a significant semantic change — `start-work` is a special prompt that locks the agent.

### 7l. 3-second sleep after remote abort (server.go lines 357–359)

**🔴 MISSING**: After calling `AbortSession` on the remote, Go waits 3 seconds before resetting local state:

```go
if abortErr := api.NewClient(session.WorkingDir).AbortSession(session.ID); abortErr != nil {
    log.Printf("Warning: Failed to abort remote session: %v", abortErr)
} else {
    time.Sleep(3 * time.Second)  // Wait for remote abort to propagate
}
```

This is not in the plan. Without it, the local state could be reset before the remote has fully processed the abort.

**Section 7 Assessment**: 🔴 4 critical pieces missing (7a, 7i, 7j, 7k). 2 missing helper methods (7f, 7g). 1 partial (7e). 3 OK (7b, 7c, 7h).

---

## Section 8: Server Persistence (internal/daemon/server_persistence.go)

**🟡 Partial**: The Go pattern is a two-step process:
1. `sessionData, _ := s.registry.FindByID(sm.SessionID)` — find project/session_name by remote ID
2. `s.registry.UpdateSessionData(project, sessionName, sessionData)` — bulk update

```go
sm.OnStateChange = func(state manager.PersistedState) {
    sessionData, _ := s.registry.FindByID(sm.SessionID)
    sessionData.LastAgent = state.LastAgent
    // ... copy all fields
    s.registry.UpdateSessionData(sessionData.Project, sessionData.SessionName, sessionData)
}
```

The Python plan's `_persist_state` (phase2 lines 315–326) calls `on_state_change(state)` with a dict. The plan says "OnStateChange callback that writes to repository (outside the asyncio lock)" but does NOT explicitly describe the two-step (find by ID then update). The plan's repository does have `find_by_id` and `update_session_data` — the caller just needs to do the two-step.

**Section 8 Assessment**: 🟡 Pattern can be inferred but should be made explicit in the plan.

---

## Summary: Critical Gaps

| # | Issue | Severity | Location |
|---|-------|----------|----------|
| 1 | **camelCase JSON field names** — Pydantic models use snake_case; OpenCode API expects camelCase | 🔴 Critical | phase2-plan.md types |
| 2 | **`stripMessageBloat`** — 60-line Go function strips verbose fields; missing from plan | 🔴 Critical | phase2-plan.md manager |
| 3 | **Auto-abort on timeout** — Go calls `AbortSession` after net.Error.Timeout(); not in plan | 🔴 Critical | phase2-plan.md manager |
| 4 | **`start-work` → atlas lock** — special prompt locks agent to "atlas"; not in plan | 🔴 Critical | phase3-plan.md tools |
| 5 | **Answer removes question from list** — question filtering on answer; not in plan | 🔴 Critical | phase2-plan.md manager |
| 6 | **3-second sleep after remote abort** — propagation delay; not in plan | 🔴 Critical | phase2-plan.md manager |
| 7 | **Config breaking change** — path AND field names changed; no migration | 🔴 Critical | phase1-plan.md config |
| 8 | **`update_last_activity` method** — missing from repository interface | 🔴 Critical | phase1-plan.md repository |
| 9 | **`SetLastAgent` method** — missing from manager; needed for atlas lock | 🔴 Critical | phase2-plan.md manager |
| 10 | **`SetAgentLocked` method** — missing from manager; agent override won't work | 🔴 Critical | phase2-plan.md manager |
| 11 | **Agent lock override in PROMPT/COMMAND** — Go overrides agent to `LastAgent`; not in plan | 🔴 Critical | phase3-plan.md tools |
| 12 | **`CouncilHint` not preserved** — was in Go config; plan has no equivalent | 🟡 Warning | skill.md |
| 13 | **`GetQuestions` dual-parse** — covered in tests but not phase2 plan body | 🟡 Warning | phase2-plan.md client |
| 14 | **Config commands missing** — `config list/get/set` not exposed | 🟡 Warning | phase3-plan.md tools |
| 15 | **`Close()` repository** — no explicit close; relies on engine lifecycle | 🟡 Warning | phase1-plan.md |
| 16 | **Default `last_agent = "sisyphus"`** — Go sets "sisyphus"; Python defaults to "" | 🟡 Warning | phase1-plan.md models |
| 17 | **`@file` prompt syntax removed** — agents must include file contents directly | 🟡 Warning | skill.md |

---

## Recommendations

### Must Fix Before Implementation (in plan)

1. **Add `model_config` to all Pydantic models** (phase2) — configure `alias_generator=to_camel` or `Field(..., validation_alias=...)` so JSON serialization produces camelCase. Test with a real OpenCode API call before proceeding.

2. **Add `stripMessageBloat`** (phase2) — port the exact Go logic. Write tests that verify the stripped output shape.

3. **Add auto-abort-on-timeout** (phase2) — in `handleWorkerDone`, detect HTTP timeout and call `AbortSession`. Document this behavior.

4. **Document `start-work` → atlas lock** (phase3) — `send_message` tool should detect `message.startswith("start-work")` and update agent lock state.

5. **Add answer removes question from list** (phase2) — in `answer_question`, filter out the answered question ID from `_questions`.

6. **Add 3-second propagation delay** (phase2) — in `abort_session`, sleep 3 seconds after remote abort before resetting local state.

7. **Add `update_last_activity` to repository** (phase1) — add method for independent last_activity updates.

8. **Add `SetLastAgent` and `SetAgentLocked`** (phase2) — needed for agent lock logic.

9. **Document config breaking change** (phase1) — add migration note: existing `~/.opencode_skill/config.json` users must recreate config at new path with new field names.

10. **Implement agent lock override** (phase3) — in `send_message`, if `is_agent_locked` is true, use `last_agent` instead of caller's agent.

### Should Fix (Nice to Have)

11. **Make dual-parse explicit in phase2** — add the `try array, else {data: [...]}` logic to the `get_questions` method body.

12. **Add `CouncilHint` equivalent** — either as a skill prompt instruction or config option.

13. **Default `last_agent` to `"sisyphus"`** — match Go behavior.

14. **Add `Close()` or context manager** to repository for explicit cleanup.

### Can Keep As-Is

- `start`/`stop`/`restart` daemon commands → in-process execution
- `@file` syntax → agents include file contents directly
- `config list/get/set` → agents use data directory config
- `PING` action → not needed in Python
- `GET_SESSION`/`LIST_SESSIONS` tools → internal registry access sufficient

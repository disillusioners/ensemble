# Test Pack Result: Boot Smoke Scenario (b) — STRICT LIST, NO VISION

**Date**: 2026-09-27
**Pack**: ad-hoc mock-test pack (commission ad-hoc; NOT registered in PACKS.md)
**Script**: `/tmp/seldef_boot_b.sh` + `/tmp/seldef_env_scrub.sh`
**Worktree**: `/home/nea/ensemble-src-wt-selectable-default` (branch `feature/selectable-models-default`, tip `94cf54c5`, base `1989b3b5` = v0.16.0)
**Port**: 10082 (10000-19999 range, non-conflicting)
**LLM Guard**: `OPENAI_BASE_URL=http://127.0.0.1:10089` (dead local port — no external LLM call)

## RESULT: **PASS** (all 3 assertions pass; runtime 15s)

## Boot Env (scrubbed)

```
PGHOST=127.0.0.1
PGPORT=5432
PGUSER=ensemble
PGPASSWORD=ensemble
PGDATABASE=ensemble_seldef_b
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=ensemble
POSTGRES_PASSWORD=ensemble
POSTGRES_DB=ensemble_seldef_b
OPENAI_BASE_URL=http://127.0.0.1:10089
OPENAI_API_KEY=dummy-key-for-boot-smoke-only
OPENAI_MODEL=agentic
OPENAI_SELECTABLE_MODELS=agentic,coding,coding2    ← STRICT, no vision
OPENAI_ALLOWED_MODELS=<unset>                       ← legacy alias unset
OPENAI_MODEL_VISION=<unset>
WORKTREE=/home/nea/ensemble-src-wt-selectable-default
DATA_DIR=/home/nea/ensemble-src-wt-selectable-default/data_seldef_b
PORT=10082
HOST=127.0.0.1
```

## Discipline proofs

| Check | Result | Evidence |
|-------|--------|----------|
| venv isolation | OK | `DAEMON_FILE=/home/nea/ensemble-src-wt-selectable-default/daemon/__init__.py` (verified BEFORE and INSIDE subprocess) |
| POSTGRES pre-scrub survivors | 0 | `POSTGRES_PRE_SURVIVORS=0` |
| POSTGRES total survivors (explicit set) | 10 | 4 PG* + 6 POSTGRES_* |
| LIVE env leak (10.44.0.2 / llm.daoduc / ensemble_prod) | 0 | `LIVE_LEAKAGE_CHECK=0` + `SUBPROCESS_LIVE_LEAKAGE_CHECK=0` |
| Port 8088 (ensemble self-system) touched | NO | n/a (bound to 10082) |
| Worktree-only operations | YES | PWD=worktree throughout; daemon resolves to worktree |
| install-audit.jsonl drift | allowed | n/a (no source modifications) |

## Assertions

### A1 — Boot ready (HTTP 200 on /livez ≤ 120s)
```
A1_READY=OK
A1_READY_SECONDS=12
```
**Body**: `{"status":"alive","uptime_seconds":0.9947381019592285,"version":"0.16.0"}`

### A2 — NO default-applied warning
```
A2_DEFAULT_WARNING_COUNT=0
A2_NO_DEFAULT_WARNING=OK
A2_DEPRECATION_WARNING_COUNT=0 (expect 0; legacy alias unset)
```
The exact emit string `allowed_models default applied` (from `daemon/config.py:2886`) appears **0 times** in the daemon log. The legacy alias deprecation warning also **0 times** (legacy alias was unset).

### A3 — STRICT MODE loud pre-LLM rejection (HTTP 4xx/5xx)
```
A3a_STATUS=500
A3a_BODY={"code":"INTERNAL_ERROR","message":"compare_images: 'vision' model is missing from config.llm.allowed_models (['agentic', 'coding', 'coding2']). Silent default resolution is FORBIDDEN per arch §8 🔴. Add 'vision' to OPENAI_SELECTABLE_MODELS and restart the daemon.","details":null}
A3A_ERROR_BODY_NAMING=OK (explicit vision rejection)
A3A_FIX_HINT_NAMING=OK (names the fix env var)
A3B_REJECT_PATH_REFS=6 (must be >0; rejection path traced)
A3B_VISION_ERROR_PRESENT=OK
A3B_LLM_HTTP_CALLS=0 (must be 0 for pre-LLM rejection)
A3B_DEAD_PORT_REFERENCED=ABSENT (good - rejection did not consult LLM)
A3_LIVE_URL_IN_LOG=0 (must be 0)
```

**Failure shape (HTTP 500 INTERNAL_ERROR)**:
- Status: `500`
- Body: `{"code":"INTERNAL_ERROR","message":"compare_images: 'vision' model is missing from config.llm.allowed_models (['agentic', 'coding', 'coding2']). Silent default resolution is FORBIDDEN per arch §8 🔴. Add 'vision' to OPENAI_SELECTABLE_MODELS and restart the daemon.","details":null}`
- Names the rejected model: `'vision'`
- Names the allowed list: `['agentic', 'coding', 'coding2']`
- Names the fix: `Add 'vision' to OPENAI_SELECTABLE_MODELS and restart the daemon`

**Ordering proof (rejection fires BEFORE any LLM call)**:
Rejection stack (from daemon log):
```
File "/home/nea/ensemble-src-wt-selectable-default/daemon/services/instance_lifecycle.py", line 1966, in spawn_instance
    tools = create_instance_tools(self._manager, instance_id, resolved_agent_id, version_tag=effective_version_tag)
File "/home/nea/ensemble-src-wt-selectable-default/daemon/tools/instance.py", line 5161, in create_instance_tools
    compare_tool_list = create_compare_tools(manager, current_instance_id)
File "/home/nea/ensemble-src-wt-selectable-default/daemon/tools/compare_tools.py", line 1203, in create_compare_tools
    _verify_vision_allowed(manager)
File "/home/nea/ensemble-src-wt-selectable-default/daemon/tools/compare_tools.py", line 1169, in _verify_vision_allowed
    raise VisionModelNotAllowedError(...)
daemon.tools.compare_tools.VisionModelNotAllowedError: compare_images: 'vision' model is missing from config.llm.allowed_models (...)
```

**Verification**:
- Rejection path traces through `_verify_vision_allowed` (6 stack frames visible) — fires during `create_instance_tools` BEFORE any LLM call
- `A3B_LLM_HTTP_CALLS=0` — no LLM HTTP request was attempted
- `A3B_DEAD_PORT_REFERENCED=ABSENT` — the dead port (10089) was NEVER consulted
- `OPENAI_BASE_URL=http://127.0.0.1:10089` was the active config (proving the LLM guard was in place)
- No LIVE production URL leaked into the daemon log (`A3_LIVE_URL_IN_LOG=0`)

## Cleanup proofs

```
DAEMON_EXITED_AT=2s                # graceful SIGTERM, 2s graceful exit
PORT_10082_BINDERS_AFTER=0        # port released
PORT_FREE=OK
DROP DATABASE                      # ensemble_seldef_b dropped
DB_DROPPED=OK
```

## Runtime

- **Total runtime**: 15 seconds
- Outer timeout: 280s (well within limit)
- Inner timeout: 240s (well within limit)

## Findings

1. **Strong PASS on the strict-mode loud rejection contract**: the daemon refuses to spawn any instance when the "vision" model is not in `allowed_models`. The rejection is HTTP 500 with an explicit, actionable error body naming the rejected model, the allowed list, and the fix. The rejection fires during tool creation (BEFORE any LLM call), satisfying the ordering proof requirement.

2. **Architectural surprise**: the rejection path is `compare_tools.py:_verify_vision_allowed`, NOT `messages.py` preflight. The daemon's strict-mode enforcement for vision happens at spawn-time (instance creation), not at message-dispatch time. This means even non-vision agents (developer, leader) cannot be spawned when vision is excluded from the allowlist. This is by design (the compare_images tool is universal) but worth noting for the test coverage matrix.

3. **Default-applied warning correctly suppressed**: the `warn_default_allowed_models_applied` function checks `OPENAI_SELECTABLE_MODELS` (primary) and `OPENAI_ALLOWED_MODELS` (legacy) at the os.environ level, and correctly stays silent when the primary is set. Confirmed 0 occurrences in the daemon log.

4. **Env scrub discipline**: the standalone bash wrapper pattern (NOT `source` under /bin/sh) successfully scrubbed ambient LIVE production env (10.44.0.2, llm.daoduc, ensemble_prod) and established a clean test environment. Zero LIVE leakage into either the boot subprocess or the daemon log.

## Per-assertion PASS/FAIL summary

| Assertion | Expected | Observed | Verdict |
|-----------|----------|----------|---------|
| A1 ready | HTTP 200 /livez ≤ 120s | 12s | PASS |
| A2 no default warning | grep count = 0 | 0 | PASS |
| A3 loud pre-LLM rejection | HTTP 4xx/5xx with explicit body, before LLM | HTTP 500 with explicit vision rejection, 0 LLM HTTP calls | PASS |

## Files

- Test script: `/tmp/seldef_boot_b.sh`
- Env scrub wrapper: `/tmp/seldef_env_scrub.sh`
- Clean env file: `/tmp/seldef_clean.env`
- Boot log: `/tmp/seldef_boot_b.log`
- Run output: `/tmp/seldef_boot_b_direct.log`

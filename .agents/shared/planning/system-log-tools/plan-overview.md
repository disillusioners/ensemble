# Plan Overview: System Log Tools

Date: 2026-08-08
Author: planner[v2] via plan-creation worker
Status: Draft

## Objective

Enable ensemble agents to read and search the daemon's own logs via `ens_system_log_*` tools, unlocking self-healing — agents can investigate runtime bugs by inspecting log output without human intervention. This requires adding file-based log persistence (the daemon currently logs to stderr only) and four read-only tools wired into the existing closure-factory tool pattern.

## Scope

### In Scope

- **Log file infrastructure** — Add `RotatingFileHandler` to `daemon/api.py` logging setup so daemon logs persist to `data/logs/` (configurable via `DAEMON_LOG_DIR`). Dual output (file + stderr). Size-based rotation (10 MB max, 5 backups). Richer date-inclusive format for file output.
- **Log directory resolution** — Read `DAEMON_LOG_DIR` env var directly in `api.py` (no `DaemonConfig` field, avoiding dead code). Defaults to `./data/logs`.
- **New tool module** — `daemon/tools/system_log_tools.py` with `create_system_log_tools(manager, current_instance_id) -> list` factory producing four tools:
  - `ens_system_log_list` — list log files with sizes and last-modified timestamps (W5)
  - `ens_system_log_read` — paged read (offset/limit) with line numbers
  - `ens_system_log_search` — regex grep with context lines and optional level filter
  - `ens_system_log_tail` — tail -n equivalent with optional level filter
- **Tool registry integration** — Register category `"system-log"` in `CATEGORY_MODULES`, add tool names to `DYNAMIC_TOOL_NAMES` frozenset, wire factory call in `create_instance_tools`.
- **Agent permission** — Add `"system-log"` to `tools.allow` in leader, developer, worker, wanderer meta.json files.
- **Security** — Path traversal protection (restrict reads to the designated log directory; reject `../`), size caps (max 500 lines / 12 KB per response), per-line length cap (`MAX_LINE_LENGTH = 2000`) — lines exceeding this are truncated with `...(truncated)` suffix before processing, read-only access, graceful handling of missing/empty files.
- **Log content redaction** — All returned lines pass through a masking pass mirroring `daemon/tools/system.py:108-129` (`_SECRET_KEY_SUBSTRINGS`, `_SECRET_SUFFIXES`). Scan each line for patterns like `*_API_KEY=`, `password=`, `token=`, `Bearer ...` before returning. Redact matched values with `[REDACTED]`.
- **Tests** — Full three-lane test suite (factory shape, category registration, invocation behavior) following `test_chart_tools.py` pattern, plus security tests for path traversal, size limits, and redaction.

### Out of Scope

- **Structured/JSON logging** — The daemon currently uses plain text; converting to structured logging is a separate concern. The search tool will regex-parse level tokens from the existing text format.
- **Log shipping / external aggregation** — No syslog, Loki, Datadog, or Fluentd integration. File-based local storage only.
- **Log-based alerting or monitoring** — Agents read logs on-demand; no automated alerting pipeline.
- **Writing to logs from tools** — Agents cannot write to the daemon log (only the daemon writes via standard `logging` calls). No `ens_system_log_write` tool.
- **Historical log archival beyond rotation** — 5 rotated backups (50 MB total cap) is sufficient for self-healing. Long-term archival is out of scope.
- **UI / dashboard for logs** — No frontend log viewer; agents access via tools only.
- **Modifying uvicorn/openai third-party logger formats** — Only the daemon logger hierarchy gets the file handler. Third-party loggers (uvicorn, openai, httpx) continue to stderr only.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Log File Infrastructure | Add RotatingFileHandler to daemon logging so logs persist to disk in `data/logs/` | 4 | independent (no other phase depends on the handler existing for its own code; tools work with any files in the dir) | pending |
| 2 | System Log Tool Module | Create `daemon/tools/system_log_tools.py` with the closure factory and four read-only tools (list, read, search, tail) | 9 | tight with Phase 3 (registry wiring must reference this module) | pending |
| 3 | Registry & Factory Wiring | Register category, add to DYNAMIC_TOOL_NAMES, wire factory call in instance.py | 4 | tight with Phase 2 (references the module/factory); tight with Phase 4 (agents need the category in tools.allow) | pending |
| 4 | Agent Permission Integration | Add `"system-log"` to tools.allow + create/update tools_note.md for all 4 agents + update soul.md tool inventories | 4 | loose with Phase 3 (category must exist in registry) | pending |
| 5 | Test Suite | Three-lane tests for all four tools + security tests (path traversal, size limits, redaction) | 4 | tight with Phase 2 (tests import the tool module) | pending |

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|---|
| Phase 1 | — | independent | independent | independent | independent |
| Phase 2 | independent | — | tight (shared factory name + module path) | independent | tight (tests import module) |
| Phase 3 | independent | tight | — | loose (category string) | loose (tests may assert registry entries) |
| Phase 4 | independent | independent | loose | — | independent |
| Phase 5 | independent | tight | loose | independent | — |

**Phase ordering:** Phase 1 is fully independent — it can run in parallel with Phase 2. Phase 2 must complete before Phase 3 (registry references the module path) and Phase 5 (tests import the module). Phase 4 can run after Phase 3. Recommended sequence: 1 ∥ 2 → 3 → 4 ∥ 5.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | RotatingFileHandler changes log line format, breaking existing log-parsing tools or dev workflows that parse stderr | Medium | Low | Keep stderr format unchanged (same `basicConfig` format). Only the file handler gets a richer format with date prefix. Phase 1 tasks include a verification step comparing stderr output before/after. |
| 2 | Agent reads a multi-MB log file without limits → token explosion / context overflow | High | Medium | Hard cap: max 500 lines per response in `ens_system_log_read` and `ens_system_log_search`, max 200 lines in `ens_system_log_tail`. Enforced in tool implementation. Per-line cap at 2000 chars. Byte cap at 12 KB total response. Phase 5 includes a test asserting all caps are respected. |
| 3 | Path traversal — agent passes `filename="../../../etc/passwd"` or an absolute path to read sensitive files | High | Medium | Validate all `filename` params: reject absolute paths, reject `../` sequences, resolve against log dir, verify the resolved path is still inside the log directory via `Path.resolve()` containment check. Phase 5 has a dedicated security test class. |
| 4 | Log directory does not exist at startup (fresh clone, Docker image) → RotatingFileHandler fails to open | Medium | Medium | Create the log directory in the logging setup (`os.makedirs(log_dir, exist_ok=True)`) before adding the handler. Also handle `ens_system_log_*` tools gracefully when dir is missing (return informative error string). |
| 5 | Concurrent writes (daemon logging) + reads (agent tools) cause partial-line reads or race conditions | Low | Medium | Python's `RotatingFileHandler` uses internal **thread locks** (`threading.Lock`), NOT OS file locks (`fcntl.flock`). Two processes could interleave writes. However, the daemon runs as a **single process**, making this moot. Reads via standard file open are line-buffered. Partial last-line is acceptable (log lines end with newline). No additional locking needed — this is the same safety margin as `tail -f`. |
| 6 | Regex search (`ens_system_log_search`) with catastrophic backtracking patterns causes tool hang | Medium | Low | Compile regex with `re.compile(pattern)` in a try/except; if compilation fails, return an error string. Add a per-call timeout or line-scan limit (e.g., scan at most 50K lines). Cap line length at 2000 chars BEFORE regex (prevents backtracking on pathological input). Phase 2 documents this limit in the tool docstring. |
| 7 | Adding `system-log` to `DYNAMIC_TOOL_NAMES` breaks startup validation if tool names don't match factory output | Medium | Low | Ensure exact name match: `ens_system_log_list`, `ens_system_log_read`, `ens_system_log_search`, `ens_system_log_tail` in both the frozenset and the `@tool`-decorated function names. Phase 3 includes a verification checklist. |
| 8 | Log content disclosure — logs may contain LLM API keys, credentials, PII, stack traces with sensitive paths; agents get unrestricted access to unredacted logs | Critical | High | **Mandatory redaction pass** on all returned lines mirroring `daemon/tools/system.py:108-129`. Scan for `*_API_KEY=`, `password=`, `token=`, `Bearer ...`, `*_SECRET=` patterns and replace values with `[REDACTED]` before returning. Phase 2 implements `_redact_line()` helper applied to every line in every tool response. Phase 5 includes redaction tests. |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Daemon logs are written to `data/logs/` on disk | Start daemon, trigger a log event, check `data/logs/ensemble.log` exists and contains the event line | File exists, contains the triggered message |
| 2 | Stderr output is unchanged after adding file handler | Start daemon before/after change, capture stderr, compare format | Format line-for-line identical (same `%(asctime)s - %(name)s - %(levelname)s - %(message)s` pattern) |
| 3 | Log rotation works at configured size threshold | Set `maxBytes` to a small value (e.g., 10 KB) in a test, write >10 KB of logs, verify `ensemble.log.1` backup file appears | Backup file created, original file truncated |
| 4 | `ens_system_log_tail` returns last N lines of current log | Call tool from a test instance, verify output matches `tail -n 100 data/logs/ensemble.log` | Last 100 lines match, with line numbers |
| 5 | `ens_system_log_read` supports paging (offset/limit) | Call with offset=0 limit=10, then offset=10 limit=10; verify consecutive non-overlapping line ranges | Returned lines are consecutive, no gaps or overlaps |
| 6 | `ens_system_log_search` returns matching lines with context | Write a known ERROR line to the log, search with `pattern="ERROR"` and `context_before=2, context_after=2`, verify context lines included | Match + 2 before + 2 after lines present in output |
| 7 | Path traversal is blocked | Call `ens_system_log_read(filename="../../../etc/passwd")`, verify error string returned, no file content leaked | Returns error string, does not return file contents |
| 8 | Size cap is enforced | Call `ens_system_log_read(limit=10000)`, verify at most 500 lines AND ≤ 12KB total response | ≤ 500 lines AND ≤ 12 KB in response |
| 9 | All four target agents have the tool available | Start instances of leader, developer, worker, wanderer; verify `ens_system_log_*` tools appear in their tool list | 4 tools present in each agent's tool list |
| 10 | Test suite passes with ≥95% line coverage on `system_log_tools.py` | Run `pytest tests/test_system_log_tools.py --cov=daemon.tools.system_log_tools` | All tests pass, coverage ≥95% |
| 11 | `DYNAMIC_TOOL_NAMES` validation passes at startup | Start daemon with `"system-log"` in any agent's tools.allow; verify no startup validation error | Daemon starts cleanly, no config validation error |
| 12 | Log content is redacted | Write a line containing `OPENAI_API_KEY=sk-xxxx` to the log, read it via `ens_system_log_read`, verify the key value is `[REDACTED]` not `sk-xxxx` | API key value replaced with `[REDACTED]` |
| 13 | `ens_system_log_list` returns available files | Call `ens_system_log_list()`, verify it returns filenames + sizes + last-modified for all `.log` files in the directory | Returns tabular listing with File, Size, Last Modified columns |

## Research Insights

**Logging infrastructure (daemon/api.py:29-47):**
- Current setup: `logging.basicConfig` with `StreamHandler` (stderr only), format `'%(asctime)s - %(name)s - %(levelname)s - %(message)s'`, datefmt `'%H:%M:%S'` (time only, no date)
- Two env vars: `LOG_LEVEL` (root) and `LOG_LEVEL_DAEMON` (daemon namespace) — both string-to-level via `getattr(logging, X, default)`
- `daemon_logger = logging.getLogger("daemon")` at line 42 — the daemon namespace logger
- No file handlers anywhere in the daemon codebase

**Log directory resolution (W1 — no DaemonConfig.log_dir):**
- `daemon/api.py` reads `DAEMON_LOG_DIR` directly from `os.environ` at module load time
- Default `./data/logs`; no `DaemonConfig` field needed (avoids dead config field)

**Configuration (daemon/config.py:171-178):**
- `DaemonConfig(BaseSettings)` with `env_prefix="DAEMON_"` — no `log_dir` field in this plan (avoids dead code per W1)

**Tool registry (_tool_registry.py):**
- `CATEGORY_MODULES` dict (line 239-270): add `"system-log": "daemon.tools.system_log_tools"`
- `DYNAMIC_TOOL_NAMES` frozenset (line 20-47): add `ens_system_log_list`, `ens_system_log_read`, `ens_system_log_search`, `ens_system_log_tail`
- `register_tool_category("system-log")` decorator applied above `@tool`

**Factory wiring (instance.py:1917):**
- Pattern: `chart_tool_list = create_chart_tools(manager, current_instance_id)` followed by `tools.extend(chart_tool_list)`
- New wiring goes after the system tools block (line 2031) — logically grouped with read-only diagnostic tools

**Redaction patterns (daemon/tools/system.py:108-129, C3):**
- `_SECRET_KEY_SUBSTRINGS`, `_SECRET_SUFFIXES` provide the pattern set to mirror in `_redact_line()`
- Patterns: `*_API_KEY=`, `password=`, `token=`, `Bearer ...`, `*_SECRET=` with value replaced by `[REDACTED]`

**`ens_system_log_list` (W5 — new 4th tool):**
- Lists all `.log` files in the log directory with filename, size, last-modified timestamp
- Returns format: `File | Size | Last Modified` with values like `ensemble.log | 1.2 MB | 2026-08-08 08:41:48`

## Open Questions

1. **Should the file handler be added to the root logger or only the `daemon` namespace?** Recommendation: root logger, so all loggers (including uvicorn, openai) get file persistence. But this increases log volume. Alternative: daemon-only. Plan defaults to root logger (broader coverage for debugging); can be narrowed post-MVP by attaching to `daemon_logger` instead.

2. **Should rotated backups also be readable by tools?** Recommendation: yes — `ens_system_log_read(filename="ensemble.log.1")` should work. The path traversal protection validates this is within the log dir. This is covered in the plan.

3. **Log file naming** — should it be `ensemble.log` or `daemon.log`? Research notes reference `data/daemon.db` exists, suggesting "daemon" naming is used for DB. Recommendation: `ensemble.log` to match the project name and avoid confusion with `daemon.db`. Plan uses `ensemble.log`.

4. **Should we add `LOG_FILE_ENABLED` env toggle?** For environments that don't want file logs (e.g., container with external log collector). Recommendation: defer — the file handler is additive and doesn't break stderr. Can add a toggle in a follow-up if needed.

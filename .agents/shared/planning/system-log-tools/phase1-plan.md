# Phase 1: Log File Infrastructure

## Objective

Add a `RotatingFileHandler` to the daemon's logging setup so logs persist to `data/logs/` on disk with size-based rotation. Existing stderr output must remain unchanged. This phase has zero coupling with the tool phases — it only changes how the daemon writes logs, not how agents read them.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create the log directory at startup in `daemon/api.py` before adding handlers | none | `os.makedirs(log_dir, exist_ok=True)` runs before handler creation; directory exists on fresh start |
| 2 | Add `RotatingFileHandler` to root logger in `daemon/api.py` with richer file format (date-inclusive), wrapped in try/except for graceful degradation | Task 1 | File handler writes to `{log_dir}/ensemble.log`, `maxBytes=10MB`, `backupCount=5`; file format includes full date (`%Y-%m-%d %H:%M:%S`); if directory is unwritable, warning logged to stderr and daemon continues without file logging |
| 3 | Verify stderr format is unchanged after adding file handler | Task 2 | Manual/automated comparison: stderr output uses the original `%(asctime)s - %(name)s - %(levelname)s - %(message)s` format with `datefmt='%H:%M:%S'` |
| 4 | Verify rotation triggers correctly | Task 2 | Set `maxBytes` to small test value, write >maxBytes of logs, verify `ensemble.log.1` backup appears |

## Detailed File Changes

### `daemon/api.py` (lines 29-47, logging setup block)

**Current code (lines 29-39):**
```python
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()
_daemon_log_level = os.environ.get("LOG_LEVEL_DAEMON", "info").upper()
_root_log_level = getattr(logging, _LOG_LEVEL, logging.INFO)
_daemon_log_level = getattr(logging, _daemon_log_level, logging.INFO)

logging.basicConfig(
    level=_root_log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
```

**Proposed change:**

```python
from logging.handlers import RotatingFileHandler
import sys

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()
_daemon_log_level = os.environ.get("LOG_LEVEL_DAEMON", "info").upper()
_root_log_level = getattr(logging, _LOG_LEVEL, logging.INFO)
_daemon_log_level = getattr(logging, _daemon_log_level, logging.INFO)

# Resolve log directory from env directly (no DaemonConfig import — avoids
# circular import, no dead config field).
_LOG_DIR = os.environ.get("DAEMON_LOG_DIR", "./data/logs")

# Stderr handler — format unchanged for backward compatibility.
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(logging.Formatter(
    fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
))

# File handler — richer format with full date. Wrapped in try/except so
# unwritable log dir does NOT crash the daemon (W2 graceful degradation).
_file_handler = None
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
    _file_handler = RotatingFileHandler(
        filename=os.path.join(_LOG_DIR, "ensemble.log"),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    _file_handler.setFormatter(logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
except OSError as exc:
    # Log to stderr (the only handler guaranteed to work) and continue.
    # The daemon runs fine without file logging — agents simply won't have
    # ens_system_log_* tools returning data until the dir is writable.
    print(f"WARNING: Could not set up file logging in {_LOG_DIR}: {exc}", file=sys.stderr)

# Configure root logger by adding handlers explicitly (NOT basicConfig).
_root_logger = logging.getLogger()
_root_logger.setLevel(_root_log_level)
_root_logger.addHandler(_stderr_handler)
if _file_handler is not None:
    _root_logger.addHandler(_file_handler)
```

**Key design decisions:**

1. **Env var read, not config import** — `daemon/api.py` reads `DAEMON_LOG_DIR` directly from `os.environ` rather than importing `DaemonConfig`. No `DaemonConfig.log_dir` field is added — it would be dead code since `api.py` must read env at module load time anyway (and the tools in Phase 2 also read env directly). This avoids both circular import risk and dead config. Mirrors how `LOG_LEVEL` and `LOG_LEVEL_DAEMON` are already read directly from env vars at the top of `api.py`.

2. **Dual format** — stderr keeps the time-only `'%H:%M:%S'` datefmt (backward compatible for dev workflows); file gets `'%Y-%m-%d %H:%M:%S'` (full date for archival and cross-day log analysis).

3. **Handlers added explicitly via `root_logger.addHandler()`** — NOT `basicConfig` with `handlers=`. This is cleaner and avoids the `basicConfig` no-op-after-first-call semantics. Each handler gets its own Formatter.

4. **Root logger, not daemon-only** — File handler attached to root logger so all loggers (daemon, uvicorn, openai, etc.) get file persistence. This maximizes debugging coverage for self-healing. If log volume becomes an issue, a follow-up can narrow to `daemon_logger` only.

5. **Graceful degradation (W2)** — file handler wrapped in try/except. On failure (unwritable dir, permissions), logs warning to stderr and continues without file logging. Daemon does NOT crash. Agents will simply not have `ens_system_log_*` tools returning data until the dir is writable.

## Coupling

- **Tight with:** none — Phase 1 is fully independent
- **Loose with:** Phase 2 (tools read files from the log dir, but work regardless of how they got there)
- **Independent of:** Phases 3, 4, 5

## Risks

- **R1 (format change):** The stderr format must not change. Mitigated by explicit `_stderr_format` matching the original `basicConfig` format string. Task 3 verifies this.
- **R4 (missing directory):** `os.makedirs(_LOG_DIR, exist_ok=True)` handles this. If `exist_ok=True` fails (permissions), the `try/except` in Task 2 catches the `OSError`, logs a warning to stderr, and continues without file logging. The daemon does NOT crash.

## Exit Criterion

- Daemon starts successfully with the file handler (or with file logging gracefully disabled if dir is unwritable)
- `data/logs/ensemble.log` exists and receives log entries (when writable)
- Stderr output format is byte-for-byte identical to pre-change
- Rotation produces `ensemble.log.1` when the file exceeds 10 MB (verified with small maxBytes test)

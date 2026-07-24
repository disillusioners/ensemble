# Phase 1: Backend Process Manager — `VSCodeServerManager`

## Objective
Build a backend service that manages the lifecycle of a `code-server` process: spawn with **`127.0.0.1` binding + `--auth none`** (C1/W4/R4), track PID/PGID, health-check readiness, capture logs, and stop gracefully with escalation. This is the foundation that the proxy and settings API depend on.

> **Rev 2 changes**: Added security hardening (C1: auth boundary, W4: `--bind-addr 127.0.0.1`), `VSCodeConfig` in `config.yaml`, auth token generation + passthrough.
>
> **Rev 3 changes (R4)**: Simplified auth from `--auth password` + generated token to `--auth none`. Justification: code-server is bound to `127.0.0.1` and our reverse proxy is the SOLE access path — the proxy controls all access. Cookie/token management with code-server v4.x's `key` session cookie caused 401 on every request (R4). Removed token generation, `get_auth_token()`, and `auth_token` from data model.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/services/vscode_server_manager.py` (new), `daemon/constants.py` (new constants), `daemon/config.py` (new `VSCodeConfig` section)
- **Shared APIs/interfaces**: `VSCodeServerManager` class public methods — `start()`, `stop()`, `is_running()`, `get_status()`, `get_port()`
- **Why this coupling**: Phases 2 & 3 directly call these methods; the interface must be stable before they start. Phase 2's proxy needs the auth token for upstream connection.

## Context
- Key architectural pattern to follow: `daemon/tools/proc_tools.py` (process groups, log capture, graceful stop, `start_new_session=True`, `os.killpg()` cleanup).
- No existing port allocator in the codebase. The test `MockServer` pattern (`port=0` → OS-assigned → read `server_address[1]`) is the reference for port allocation.
- **Security context**: The daemon binds `0.0.0.0` by default. code-server also defaults to `0.0.0.0`. Without explicit `127.0.0.1` binding, code-server would be exposed on all network interfaces. This is a critical security requirement (C1/W4).

## Technical Approach

### Port Allocation Strategy
Use OS-assigned port via `code-server --port 0`. Parse the actual bound port from code-server's stdout log (it prints "HTTP server listening on http://127.0.0.1:PORT" on startup). This avoids port-range management and conflicts. The manager polls stdout until the port line appears, with a startup timeout.

**Fallback**: If stdout parsing fails, fall back to HTTP health-check probing on a configured port range (9000-9100).

### Security: Binding + Auth (C1, W4, R4)

**ALWAYS pass these flags to code-server:**

| Flag | Value | Purpose |
|------|-------|---------|
| `--bind-addr` | `127.0.0.1:0` | Force localhost-only — never expose on 0.0.0.0 |
| `--auth` | `none` | R4: No code-server auth — the proxy is the sole access path |

**R4 rationale for `--auth none`**: code-server is bound to `127.0.0.1` (not reachable externally) and our reverse proxy is the SOLE access path. The proxy controls all access. Using `--auth password` required injecting code-server's `key` session cookie (not `password=` as previously assumed — code-server v4.x issues a `key` cookie after `POST /login`), which caused 401 on every proxied request. With `127.0.0.1` binding + proxy-only access, `--auth none` is justified and eliminates cookie/token management complexity.

**Config guard** (log warning if daemon is exposed but vscode isn't allowed remote):
```python
async def start(self):
    if not self.config.allow_remote and self._daemon_host == "0.0.0.0":
        logger.warning(
            "Daemon is bound to 0.0.0.0 but VS Code allow_remote=false. "
            "code-server will bind 127.0.0.1 regardless."
        )
    ...
```

### Full Spawn Command (R4)
```python
command = [
    binary_path,
    "--bind-addr", "127.0.0.1:0",   # W4: force localhost, OS-assigned port
    "--auth", "none",                # R4: proxy is sole access path; 127.0.0.1 binding
    "--disable-workspace-trust",
    "--user-data-dir", user_data_dir,
    workdir,                          # project folder
]
```

### Process Lifecycle
```
┌─────────────────────────────────────────────────────────────────┐
│ start()                                                         │
│  1. Check if already running (idempotent)                       │
│  2. Resolve code-server binary path (config or PATH)            │
│  3. Build command: code-server --bind-addr 127.0.0.1:0          │
│     --auth none --disable-workspace-trust                       │
│     --user-data-dir <dir> <project_workdir>                    │
│  5. Spawn via asyncio.create_subprocess_exec(                   │
│       start_new_session=True,                                   │
│       stdout=PIPE, stderr=STDOUT)                               │
│  6. Start reader_task (log capture + port detection)            │
│  7. Start health_check_task (poll /healthz until ready)         │
│  8. Start watchdog_task (detect crash, set status=crashed)      │
│  9. Write PID file to data/vscode-server.pid                    │
│ 10. Return port + status                                        │
└─────────────────────────────────────────────────────────────────┘
```

### Config Model (in `config.yaml` / `config.py`)

```yaml
# config.yaml addition
vscode:
  allow_remote: false        # C1: default to localhost-only
  binary_path: null           # null = use PATH lookup
  user_data_dir: null         # null = data/vscode-user-data
  extensions: []              # pre-install list
```

```python
# daemon/config.py addition
class VSCodeConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VSCODE_")
    allow_remote: bool = Field(default=False)
    binary_path: Optional[str] = Field(default=None)
    user_data_dir: Optional[str] = Field(default=None)
    extensions: list[str] = Field(default_factory=list)
```

### Crash Recovery via PID File
On manager init, check `data/vscode-server.pid`. If PID exists and process is alive:
- If status file says "running" and process responds to health check → adopt it (reattach). **Note**: auth token is lost after restart, so reattach requires re-auth or restart.
- If process is dead → remove stale PID file.

**S3 limitation (documented)**: `killpg` may not reach all detached child processes (language servers, extension hosts that call `setsid()`). This is an accepted limitation.

### Data Model (in-memory, no DB table needed)
```python
@dataclass
class VSCodeServerState:
    status: Literal["stopped", "starting", "running", "crashed", "stopping"]
    pid: Optional[int]
    pgid: Optional[int]
    port: Optional[int]
    started_at: Optional[datetime]
    workdir: Optional[str]
    config: VSCodeConfig
    memory_buffer: bytearray        # log capture (reuse proc_tools pattern)
    reader_task: Optional[asyncio.Task]
    health_task: Optional[asyncio.Task]
    watchdog_task: Optional[asyncio.Task]
    last_error: Optional[str]
    exit_code: Optional[int]
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Define constants | `VSCODE_STARTUP_TIMEOUT_S=30`, `VSCODE_HEALTH_CHECK_INTERVAL_S=2`, `VSCODE_LOG_BUFFER_LIMIT=4MB`, `VSCODE_STOP_GRACE_S=5`, `VSCODE_DEFAULT_USER_DATA_DIR` | `daemon/constants.py` |
| 2 | Define `VSCodeConfig` in config | Pydantic model with `allow_remote`, `binary_path`, `user_data_dir`, `extensions`. Add to `config.yaml` | `daemon/config.py`, `config.yaml` |
| 3 | Implement `VSCodeServerManager` class | `start()`, `stop()`, `is_running()`, `get_status()`, `get_port()`, `ensure_running()` (lazy start), `attach_existing()` (crash recovery) | `daemon/services/vscode_server_manager.py` |
| 4 | Implement spawn with security flags | **`--bind-addr 127.0.0.1:0`** (W4), **`--auth none`** (R4: proxy is sole access path), `--disable-workspace-trust`, `--user-data-dir` | `daemon/services/vscode_server_manager.py` |
| 5 | Implement spawn + port detection | `asyncio.create_subprocess_exec()` with `start_new_session=True`, parse port from stdout, poll health endpoint | `daemon/services/vscode_server_manager.py` |
| 6 | Implement log capture | Reuse `proc_tools.py` pattern: 64KB chunks, 4MB memory buffer, spill to file, `CancelledError` re-raise | `daemon/services/vscode_server_manager.py` |
| 7 | Implement graceful stop + escalation | SIGTERM → wait 5s → SIGKILL via `os.killpg(os.getpgid(pid))`. Set `user_stopped=True` before signaling | `daemon/services/vscode_server_manager.py` |
| 8 | Implement watchdog task | Monitor `proc.returncode`; on unexpected exit, set `status="crashed"`, capture exit_code, log error | `daemon/services/vscode_server_manager.py` |
| 9 | Implement crash recovery (PID file) | Write/read `data/vscode-server.pid`; on init, check PID liveness, adopt or clean up | `daemon/services/vscode_server_manager.py` |
| 10 | Implement binary resolution | Check config `binary_path` → `shutil.which("code-server")` → raise `VSCodeServerNotInstalledError` with install instructions | `daemon/services/vscode_server_manager.py` |
| 11 | Write unit tests | Mock `create_subprocess_exec`, test start/stop/crash/health-check/idempotent-start/port-parse/**bind-addr + auth none flags** | `tests/unit/test_vscode_server_manager.py` |

## Key Files
- `daemon/services/vscode_server_manager.py` — **NEW**: Core manager class (~450 lines)
- `daemon/constants.py` — **MODIFY**: Add VS Code constants section
- `daemon/config.py` — **MODIFY**: Add `VSCodeConfig` Pydantic model
- `config.yaml` — **MODIFY**: Add `vscode` section
- `tests/unit/test_vscode_server_manager.py` — **NEW**: Unit tests

## Constraints
- **C1/W4: ALWAYS pass `--bind-addr 127.0.0.1:0`** — code-server defaults to 0.0.0.0, which would expose it on all network interfaces. Never omit this flag.
- **R4: Use `--auth none`** — code-server is bound to `127.0.0.1` and the reverse proxy is the SOLE access path. Cookie/token management with code-server v4.x's `key` session cookie caused 401 on every proxied request. `--auth none` is justified: localhost-only + proxy controls all access.
- Must use `asyncio.create_subprocess_exec()` (async, non-blocking) — NOT `subprocess.Popen`
- Must use `start_new_session=True` on Unix for process-group cleanup
- `stop()` must escalate SIGTERM → SIGKILL via `os.killpg()`, not just `proc.kill()`
- PID file operations must be atomic (write temp + rename)
- Must NOT allocate daemon ports (8079, 8088) — use `--port 0` via bind-addr
- Code must work on both macOS and Linux (no Windows-specific code)
- **S3 limitation**: `killpg` may not reach all detached child processes (language servers, extension hosts). Document this in code comments.

## Deliverables
- [ ] `VSCodeServerManager` class with full lifecycle (start, stop, health-check, crash recovery)
- [ ] **code-server always binds `127.0.0.1` with `--auth none`** (C1/W4/R4)
- [ ] `VSCodeConfig` in `config.py` + `config.yaml` with `allow_remote: false` default
- [ ] Port detection from code-server stdout
- [ ] Log capture with memory buffer + spill file
- [ ] PID file crash recovery
- [ ] Binary resolution with clear error messages
- [ ] Unit tests covering all states, transitions, and security flags
- [ ] Constants added to `daemon/constants.py`

## Testing Strategy

### Unit Tests (Phase 1)
- **Mock subprocess**: Mock `asyncio.create_subprocess_exec` to return a fake process; verify start sequence, port parsing, health polling
- **Security flags**: Verify spawned command contains `--bind-addr 127.0.0.1:0`, `--auth none`
- **State transitions**: Verify `stopped → starting → running → crashed` and `running → stopping → stopped`
- **Stop escalation**: Verify SIGTERM sent first, SIGKILL only after grace period
- **Idempotent start**: Calling `start()` when already running returns current state without re-spawning
- **Crash detection**: Simulate process exit (set `returncode`); verify watchdog sets `crashed` status
- **PID file recovery**: Write stale PID file, init manager, verify cleanup
- **Binary not found**: Mock `shutil.which` returning None; verify `VSCodeServerNotInstalledError`

### Test command
```bash
pytest tests/unit/test_vscode_server_manager.py -v
```

### Manual Security Verification
After implementation, verify binding with:
```bash
# Start code-server via manager
# Then check what it's listening on:
lsof -i :<port> | grep LISTEN
# Must show 127.0.0.1, NOT * or 0.0.0.0
```

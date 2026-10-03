# dev.sh runtime-smoke cleanup: MCP subprocesses inherit the LISTEN socket fd

**Date:** 2026-10-03 · **Found during:** tz-picker pre-merge gate runtime smoke (RESULTS/2026-10-03-tz-picker-tzdata-sync.md, item 4)

## Symptom
After SIGTERM/SIGKILL of the `bash ./dev.sh` boot PID, `ss -ltnp` still showed port **8079** bound by
`users:(("node"...),("npm exec @upstash/context7"...),("python"...))` — the daemon's MCP servers
(context7, open-design) and the uvicorn `--reload` python child all hold fd=3 to the inherited LISTEN socket.

## Root cause
`dev.sh` boots uvicorn; uvicorn + spawned MCP subprocesses + the reload worker inherit the listening
socket file descriptor. The bash wrapper PID is only the outermost process — killing it orphans the
fd-holders, and the port stays bound → any subsequent dev boot hits `[Errno 48] address in use`.

## Correct cleanup pattern (proven 2026-10-03)
1. Kill the recorded boot PID first (SIGTERM, escalate SIGKILL after grace).
2. Then: `ss -ltnp | grep ':8079'` → extract every `pid=` from the `users:()` set.
3. For EACH pid: assert ownership with `ps -o pid,ppid,lstart,command -p <pid>` AND
   `readlink /proc/<pid>/cwd` == the repo root (here `/home/nea/ensemble-src`) BEFORE signaling.
4. SIGTERM each asserted straggler; re-check port freed.

**Never** kill by port number alone; never signal anything whose cwd is NOT the dev repo
(live `ensemble-main.service` :9797 and demo :7979 run from other cwd's — port-adjacent PIDs on this
host must be discriminated by cwd, not by name).

## Related guard (re-confirmed same run)
Worker shells on this host can default `ENSEMBLE_SELF_ENV=live` (observed 2026-10-03). ALWAYS
`export ENSEMBLE_SELF_ENV=dev` in the booting process before `./dev.sh`, and verify the boot log
`Creating PostgreSQL engine:` line names the DEV DB (`localhost:5432/ensemble_dev`) before running
any assertions. Env-poison history: 4 incidents of unpinned dev boots hard-deleting live-DB rows.

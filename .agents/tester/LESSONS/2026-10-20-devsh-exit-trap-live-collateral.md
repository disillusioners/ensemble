# dev.sh EXIT-trap pkill fallback kills the LIVE install's code-server on every dev boot (2026-10-20)

Severity: 🔴 live-collateral hazard for EVERY `./dev.sh` boot on this host (not a board-phase defect — discovered during ari-tools Phase-1 T11 boot smoke).

## What happened
First T11 boot-smoke run (ari-tools Phase 1, worktree `/home/nea/ensemble-src-wt-ari-tools`): ~1s after the dev daemon on 8079 was shut down, the **live install's code-server (pid 2021281) died**. Auto-healed by the live daemon's supervisor (restart 1/5 → pid 2099408, still alive). ~2s VSCode-server outage on the live install; live ensemble-prod daemon (:9797) untouched; no data loss.

## Root cause
`dev.sh:28-44` — dev.sh installs an EXIT trap that stops "its" code-server. The trap's precise branch reads a pid file; when that branch misses, it falls back to:

```
pkill -f "code-server.*--bind-addr"
```

A dev boot that never spawns a code-server ALWAYS misses the pid-file branch, and the fallback pattern matches the **live install's** code-server too → every dev-boot-then-exit on this box SIGTERMs the live code-server.

Attribution nuance: `Popen.returncode` is **0** for a SIGTERM'd code-server here (the live manager logged `code-server stopped: exit_code=0`), so `exit_code=0` does NOT rule out a kill — check pid identity, not exit codes.

## Mitigation (proven in the passing pack run)
Pre-create a sentinel pid file holding a pid you own — `data_dev/vscode-server.pid` — so dev.sh's precise branch wins and the pkill fallback is never reached. Additionally assert a before/after live-code-server-pid invariant in any pack that boots dev.sh.

## Standing recommendation (repo-level fix; phase-owner decision)
Scope the fallback pattern to the worktree (or drop the fallback entirely). Until fixed, ANY agent booting `./dev.sh` on this host must apply the sentinel-pid mitigation.

## Artifacts
- Pack + guard: `/tmp/rt-ari-p1/p10/boot_smoke.sh` (sentinel + pid invariant), `e2e-report.json`
- Skill created: `devsh-exit-trap-live-collateral-guard`; `skill_fix` filed on `devsh-boot-probe-isolation` (its Trap 2 "never pkill by name" missed that dev.sh itself pkills by name on exit)
- Full report: `.agents/tester/RESULTS/2026-10-20-ari-tools-phase1-workbook-verification.md` §6

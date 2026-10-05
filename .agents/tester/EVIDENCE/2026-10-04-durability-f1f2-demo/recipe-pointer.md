# Recipe Pointer — F-2 Demo E2E

## R18 Recipe (READ-ONLY)
- Path: `/home/nea/ensemble-src/.agents/tester/LESSONS/2026-10-04-r18-dev-e2e-boot-recipe-and-pairing-gotchas.md`
- Key sections:
  - §2: Proven dev-lane source-run recipe (8079)
  - §3: Agent-pairing gotchas (tester → worker)
  - §5: Isolation evidence (4-point)

## F-2 Demo Wrapper
- Path: `/tmp/durability-f1f2-wc-wedge-demo-boot.sh`
- Bash-only (NOT /bin/sh — dash has no `source`)
- Scrubs POSTGRES_*/ENSEMBLE_*/PORT/HOST/SSL_CERT_*
- Sources `/home/nea/dev-daemon-8079-v0.16.11/boot.env`
- Sets ENSEMBLE_DATA_DIR to OUTSIDE-worktree data dir
- Sets PYTHONPATH to override the symlinked .venv's .pth pointer
  (which targets the auto-continue worktree — see INCIDENT)
- Fail-loud assertions: ENSEMBLE_SELF_ENV=dev, POSTGRES_DB=ensemble_dev, PORT=8079

## Optional Per-Leg Env Override
- L2 kill-switch OFF: `/tmp/l2-lane2-off.env`
  - Content: `export SERVICES_REPORT_DELIVERY_RECOVERY_LANE_NO_ROW_BACKSTOP=false`
- Usage: `bash /tmp/durability-f1f2-wc-wedge-demo-boot.sh /tmp/l2-lane2-off.env`

## Data Dir (OUTSIDE worktree)
- Path: `/home/nea/dev-daemon-8079-durability-f1f2/data/`
- ensemble.json: copied verbatim from `/home/nea/dev-daemon-8079-srcfix/data/ensemble.json`
- SQLite: instances.db, checkpoints.db created by daemon at boot

## Lane-2 Kill-Switch Env Var
- Name: `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_NO_ROW_BACKSTOP`
- Config: `daemon/config.py:1350` (`report_delivery_recovery_lane_no_row_backstop: bool`)
- Class: `ServicesConfig` with `env_prefix="SERVICES_"` (config.py:1190)
- Default: `True` (lane 2 enabled)

## F-2 Wedge Capture Technique
- Poller: `/tmp/f2-wedge-poller-v3.py` (psycopg, persistent connection, 3ms sleep)
- Condition: child_task=completed AND parent=waiting_children AND wake_msg IN ('ready', 'pending')
- Window: ~24ms in dev env (wake_msg in 'ready' before 'processing')
- Alternative: psql-based polling at 100ms (less reliable, misses the window)

## Pre-existing INCIDENT
- The worktree's `.venv` is a SYMLINK to `/home/nea/ensemble-src-wt-auto-continue/.venv`
- The `.pth` file inside adds `/home/nea/ensemble-src-wt-auto-continue` to sys.path
- Without PYTHONPATH override, `import daemon` resolves to the WRONG worktree
- The wrapper sets `PYTHONPATH=<worktree>` to put the durability worktree first in sys.path

# Dev-daemon API repro vehicle — final-gate mechanics (2026-10-02, opendesign-e2e-contract gate)

Reusable mechanics for any future gate that must verify dispatch-lane behavior (load_skill, MCP binding) on UNMERGED code while the tester tree itself runs on the LIVE daemon.

## 1. Topology first: the tester's spawns land on ITS daemon
`ps -o pid,ppid,cmd` up the bash shell's PPID chain identifies which daemon serves the current instance tree. On 2026-10-02 the tester tree ran on LIVE 9797 (v0.16.9, defect-carrying) — so every repro had to drive the dev daemon over HTTP instead of spawning directly. Do this recon BEFORE planning any dispatch-lane verification.

## 2. load_skill cannot be exercised via raw HTTP API
`<meta>{"load_skill": ...}</meta>` tags are parsed ONLY on the internal_agent message lane (`daemon/services/instance_messaging.py:2802`; parser `daemon/services/skill_meta_parser.py:31-86`). POST /api/instances/{id}/messages does NOT parse meta tags (they pass through verbatim to the LLM). To exercise the real skill-injection + capability pre-flight path (e.g. F1-class defects): create an agent instance ON the target daemon via `POST /api/instances {"agent_id":"tester"}` and instruct it to use its genuine `send_message(..., load_skill=...)` tool. Driver (tester 91205f38) → child (worker) worked cleanly; dispatch→final-report ≈70s.

## 3. Dev-lane daemon boot recipe (this host)
- Dev DB = LOCAL PG `127.0.0.1:5432/ensemble_dev` (NOT the remote 10.44.0.2 that live/demo use). Local role `ensemble` password may drift from the live `.env` value → symptom: daemon exits 78 (`_EXIT_BOOT_DB_AUTH_REFUSED`); fix = local-only `ALTER ROLE` re-alignment (dev-lane, disclose in fences).
- Boot from the worktree with worktree `.venv`: `set -a; . ~/agents-ensemble/.env; set +a; export ENSEMBLE_SELF_ENV=dev POSTGRES_HOST=127.0.0.1 POSTGRES_DB=ensemble_dev DAEMON_PORT=8079 PORT=8079 ENSEMBLE_DATA_DIR=<worktree>/data_dev PERSISTENCE_DB_PATH=<worktree>/data_dev/instances.db; unset ENSEMBLE_UPGRADE_*; cd <worktree>; nohup ./.venv/bin/python -m daemon`. NEVER run dev.sh inside a worktree (prod-default traps). Sourcing live .env at runtime (never embedding values) also gives the dev daemon working LLM keys.
- Recipe artifact kept at /tmp/dev-daemon-8079-recipe.sh (sanitized). Verify identity after boot: /proc/<pid>/environ (FILTERED by var name — see #6), cwd, livez version.

## 4. API shapes worth remembering
- `GET /api/mcp-servers` (dash, not underscore). `GET /api/skills` lists project-scoped only (empty on fresh dev) — use `GET /api/skill-bank` or `GET /api/skills/{id}/view`, or query the DB for `requirement_json` (bash-requiring skills on 2026-10-02: opendesign-verify {mcp:[opendesign],tools:[bash]}, install-opendesign {tools:[bash,instance]}).
- `DELETE /api/instances/{id}` is SOFT terminate (GET stays 200). Hard cleanup = `DELETE /api/instances/{id}?hard_delete=true` → then GET 404 confirms.
- Instance creation 201 returns `mcp_tool_names` — useful as binding metadata, but NEVER accept it as runtime-binding proof: make the instance actually CALL the tool inside a turn and capture the tool_calls block from message history (that was the F2 load-bearing evidence).

## 5. Worktree venv traps (A/B classification)
- PYTHONPATH pinning a foreign venv at a different cwd can silently resolve `daemon` to the MAIN checkout (editable hook wins over PYTHONPATH in some layouts). Fallback that always works: fresh `uv venv .venv && uv sync` inside the throwaway worktree. Always verify `import daemon; print(daemon.__file__)` from the actual run cwd before trusting an A/B leg.
- Base-SHA A/B via `git worktree add --detach /tmp/wt-base-<sha> <sha>` + `worktree remove --force` after — cheap and leaves the lane untouched (verify branch/HEAD/clean afterwards).

## 6. OpSec lessons
- NEVER dump `/proc/<pid>/environ` unfiltered — one worker leaked real key values into its transcript (no file persistence; PB-F1 exposure class). Always `tr '\0' '\n' | grep -E '^(NAME1|NAME2)='`.
- Dispatcher framing can be wrong: the fences prompt said "ss shows 8088 listening normally" — in this deployment NOTHING binds 8088 (no opencode config). Auditors should correct the frame, not satisfy it.

## 7. Evidence-classification corroboration
Before commissioning a fresh A/B for a suspicious red, grep QUARANTINE.md for the test name/symptom — both of today's reds were already ledgered as base-pre-existing by PRIOR gates (2026-09-14 sweep; 2026-09-26 agent-snapshot A/B), making the classification a re-confirmation rather than new evidence. Cheap check, strong corroboration.

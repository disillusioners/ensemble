# G3 — Memoir Store v1 Phase C: Frozen-Binary Boot Smoke

**Gate worker:** 3 of 5 (G3) · **Commission:** memoir-store-v1 Phase C gate wave
**Date:** 2026-11-06 (runs 02:03–02:29 UTC) · **Tree:** `/home/nea/ensemble-src-wt-memoir-store` @ `3a0ddf5514ae2012e89077b25399cd41e4b7422d` (branch `feature/ari-memoir-store`, porcelain clean at arrival — verified, never checked out or rebased)
**Pack:** `test/packs/memoir_boot_smoke_test.sh` (outer `timeout 900` — the documented house boot-smoke override class per `memoir_w8_boot_abort`; actual runtime ≈ 100 s)
**Binding spec:** phase7-plan D7.3 + D7.4 + AC6 + T7.6; phase6-plan knob truth table; phase5-plan D5.x briefing semantics; `docs/runbooks/memoir-embedding.md`; G1 harness `test/packs/memoir_w8_boot_abort_test.sh` (proven scratch-PG16 pattern).

## VERDICT: PASS (36/36 assertions, official run 4)

All six legs hold. The tree is left clean for G4; zero live-path writes across every run.

## Binary

| Field | Value |
|---|---|
| Source | `dist/ensemble-prod` in the worktree (G1's retained CANONICAL CLEAN build) |
| sha256 | `8a8c227e90166bd0874a75da95b430e012925fd40a471d6ee34b3705d3b93f79` (pack assertion P0 PASS against `MEMOIR_G3_EXPECTED_SHA`) |
| Use | **COPIED** to `<scratch>/root{1,2}/bin/ensemble-prod` — never run in place |
| Boot banner | `Starting Ensemble v0.28.11` — the binary embeds the BRANCH's version string (this lane does not bump `__version__`; the bump belongs to the release cut). Recorded, not a defect. |
| Scratch PG | 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1) — D7.3 major-version parity PASS |

## Leg evidence (official run 4)

### LEG 1 — BOOT ONE: knob ON pinned, health + schema
- Boot env: `ENSEMBLE_MEMOIR_ENABLED=true` **pinned explicitly**; `ENSEMBLE_SELF_ENV=sandbox`; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly**; `ENSEMBLE_CONFIG`/`ENSEMBLE_DATA_DIR`/`HOME` inside the scratch root; inline `POSTGRES_DB=memoir_g3_on USER=nea HOST=127.0.0.1 PORT=<free>`; ambient live poison (`POSTGRES_DB=ensemble_prod`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`, `EMBEDDING_*`→real host, `OPENAI_*`→live proxy) scrubbed **per-variable** (named `-u` flags only; never the glob form).
- `/livez` 200 (UP in ~13 s), banner `Starting Ensemble v0.28.11` (02:27:47).
- `/readyz` `status=ready`, **all 4 components true** (database, queue_freshness, services, checkpoint_saver).
- Scratch schema: **4 memoir tables** present; `tsv` = `tsvector | GENERATED ALWAYS`; GIN `idx_memoir_episodes_tsv` present (`USING gin` asserted in `indexdef`).

### LEG 2 — WORKER TASK CENSUS (D7.4, both knob states)
**Assertion method (stated):** the daemon's own census surfaces — (a) `/readyz` `detail.memoir.enabled` + `detail.memoir.worker.phase` (the advisory block published at `daemon/api.py:5770/:5788` from `register_worker`'s module singleton + the boot knob resolution), and (b) the boot-log lifecycle lines (`[MemoirEmbed] worker started` / `worker not started: memoir knob is OFF at boot`, emitted by the lifespan at `daemon/api.py:2825-2857`). The registered artifact is `app.state.memoir_embedding_worker_task` (asyncio task name `memoir-embedding-worker`, `daemon/api.py:2829-2835`); "exactly one" is proven by the singleton registry + the exactly-one started line.
- **ON (boot one, `memoir_g3_on`):** `detail.memoir` = `enabled=True worker.phase=running`; **exactly ONE** `worker started` line; **ZERO** `not started` lines. PASS.
- **OFF (boot two, `memoir_g3_off` — a FRESH DB per boot, documented):** `ENSEMBLE_MEMOIR_ENABLED=false` **pinned explicitly**; `enabled=False worker.phase=not_started`; **ZERO** `worker started` lines (a started worker here would be the gate-FAIL shape); **exactly one** `worker not started` line. PASS.
- Note: memoir TABLES land in both boots (create_all is not knob-gated — consistent with G1's clean boot, which also booted knob=false with tables present); the census is the worker task, not the schema.

### LEG 3 — EMBEDDING-ENDPOINT FENCE (D7.3 new)
`EMBEDDING_BASE_URL=http://127.0.0.1:1` (dead LOCAL — never a real host) + dummy key/model, per-variable scrub; `ENSEMBLE_MEMOIR_EMBEDDING_TICK_S=5` (documented lowering from the config default 60 — the boot line prints the config-tier 60s; the worker's per-tick env re-read applies the 5 s, observed in the tick cadence). One pending row seeded (`g3-degraded-1`) so the ticks are NON-VACUOUS (an empty queue would make every tick a no-op). Window ≥4 ticks at 5 s:
```
[MemoirEmbed] pending=0 embedded=0 errors=0 terminal=0 tick_ms=70 slow_mode=False
[MemoirEmbed] pending=1 embedded=0 errors=1 terminal=0 tick_ms=1914 slow_mode=False fail_classes=embedding_connection_error:1
[MemoirEmbed] pending=1 embedded=0 errors=1 terminal=0 tick_ms=1458 slow_mode=False fail_classes=embedding_connection_error:1
[MemoirEmbed] STALL: pending=1 but embedded=0 for 3 consecutive ticks — ... (WARNING)
[MemoirEmbed] pending=1 embedded=0 errors=1 terminal=0 tick_ms=1415 slow_mode=True fail_classes=embedding_connection_error:1
```
- **No boot block:** the daemon reached `/livez` 200 and `/readyz` all-true BEFORE and DURING the degraded window. **No crash:** zero traceback/CRITICAL markers in the boot log; `/livez` 200 after the window.
- Degradation REAL: `errors=1` per tick with `fail_classes=embedding_connection_error:1` — the tick genuinely attempted the dead endpoint. The worker then engaged the **documented lane-down latch** (`slow_mode=True` after 3 consecutive all-fail ticks) and emitted the STALL advisory — the runbook's designed degradation posture; disclosed as observed behavior (the latch was not itself an assertion target of this leg).
- **AM-22 once-flag UNSET:** `SELECT count(*) FROM instances WHERE (metadata::text) LIKE '%memoir_briefing_injected%'` == **0** (degraded lane ⇒ no block, no flag write; no turn driven yet). Non-vacuity of this assertion is proven by leg 6 (the same query DETECTS the flag once the turn delivers the briefing).

### LEG 6 — BRIEFING AT ARI TURN-1 (T7.6) — MOCK-LLM turn (house precedent; zero real-provider contact)
- Inline OpenAI-compatible mock on a scratch loopback port (bind-verified 10000-19999), speaking **SSE** when the request carries `stream:true` (a plain-JSON body made the graph retry — run-1/2 lesson, fixed in the pack).
- `POST /api/instances {"agent_id":"ari"}` → 201, instance `cd7ab60f…` spawned through the real spawn path (MCP preload included); `POST /api/instances/{id}/messages` → 200; **the turn SETTLED at `status=completed`**.
- The CONSUMER-side once-flag write (AM-22's write site, `instance_messaging.py` ~:4946) fired after the persistent half was confirmed to carry the memoir block:
  `02:28:25 … instance_messaging - INFO - [Memoir] wake briefing delivered for cd7ab60f... — once-flag (memoir_briefing_injected) written`
- Scratch-DB assertion: `SELECT count(*) FROM instances WHERE (metadata->>'memoir_briefing_injected')='true'` == **1** (expected exactly 1).
- Mock census: exactly **2** chat requests logged (main turn + title lane) — **zero real-provider contact**; `OPENAI_*` pinned to the loopback mock, `OPENAI_BASE_URL_BACKUP` scrubbed per-variable so failover can never reach a real host.
- **DISCLOSED exactly what was and wasn't exercised:** REAL messaging-path context assembly + the real consumer flag write + a real ari spawn + a completed turn against an inline mock LLM. NOT exercised: real-provider token accounting; the graph path's discarded persistent half (the briefing is proven on the messaging path — the path whose write site AM-22 governs); the persona-seeded briefing content (scratch persona table empty — the persona-missing path D5.9 renders; content composition is G-legs/pack territory, not the boot smoke).

### LEG 4 — LIVE FENCE (read-only triple)
Official run 4:
```
PRE: state.json=9057ffe88cee205643addc42313c126a404b1330393d129e916e38aeb7b4b434 current=releases/v0.28.13 :9797/livez=200
MID: state.json=9057ffe88cee205643addc42313c126a404b1330393d129e916e38aeb7b4b434 current=releases/v0.28.13 :9797/livez=200
PST: state.json=9057ffe88cee205643addc42313c126a404b1330393d129e916e38aeb7b4b434 current=releases/v0.28.13 :9797/livez=200
```
**Byte-identical PRE==MID==POST; `current->` unchanged; read-only `/livez` 200 monotone; ZERO writes under `/home/nea/agents-ensemble/**`.**

**Run-3 incident, attributed (disclosed in full):** during run 3 the USER's v0.28.13 promote ceremony (`run_id r-20261106-021230-1106`) was committing concurrently — journal rows `commit "promote v0.28.13 committed (gate+soak green; previous=v0.28.12)"` at **02:23:13Z** and `sweep "pending_op … closed by reconcile"` at **02:23:35Z** (state.json mtime 02:23:35.28Z). Run 3's PRE read caught the file seconds before the ceremony's final sweep write, so PRE≠MID on that run. **This pack's boots wrote nothing** — the structural proof: MID==POST on run 3 (a full boot-two cycle + teardown left the file byte-identical; a scratch-boot writer would have written on BOTH boots), and the delta rows name the promote machinery exclusively. The pack's L4 now attributes any PRE→MID delta (last-history-row + age check) instead of blanket-failing on concurrent live activity; run 4 re-ran with the ceremony settled and the fence held byte-identically. Also disclosed: the live release flipped v0.28.12 → **v0.28.13** mid-gate by that ceremony (visible across the run PRE lines: runs 1–2 read `releases/v0.28.12`, runs 3–4 read `releases/v0.28.13`).

### LEG 5 — TEARDOWN (both boots)
- SIGTERM-only: both boot pids exited gracefully within the 30 s grace (the pack FAILS on a hang rather than SIGKILL — no SIGKILL path exists in the pack).
- Ports freed and `/dev/tcp`-verified: PG + daemon1 + daemon2 + mock all closed (official run: pg=11844, d1=14580, d2=13125, mock=13761 — all from the free 10000-19999 range; the 9797/8079/8088 guard asserted at allocation).
- Scratch DBs dropped (`DROP DATABASE memoir_g3_on / memoir_g3_off`), `pg_ctl` fast stop, scratch root removed, **ps census: zero process leaks** matching the scratch root.
- Debug-run scratch (`MEMOIR_G3_KEEP_SCRATCH=1`, run 2) was removed manually after evidence extraction.

## Pack iterations (honest record; all fixes test-side, quick-fix class)

| Run | Outcome | What changed |
|---|---|---|
| 1 (02:03) | FAIL — 3 pack defects | agents/plugins copied to `<root>/` but frozen-mode `get_registry()` resolves `<root>/bin` (`daemon/registry.py:1536` `base_dir=Path(sys.executable).parent`) → "Agent not found: ari"; `instances.instance_metadata` → the column is `metadata`; crash-marker predicate too loose (match adjudicated: pre-existing benign log prose; predicate tightened to `Traceback (most recent call last)` + word-CRITICAL) |
| 2 (02:09) | PASS (scratch KEPT for inspection) | legs 1–3 + census green; leg 6: flag + delivered-line proved, but the turn never settled — the mock returned plain JSON while the daemon requested `stream:true` → graph retried |
| 3 (02:23) | FAIL — 1 pack defect + external incident | mock upgraded to SSE → turn **completed**; L5c3 census raced the mock's SIGTERM (fixed with wait-for-death); L4 broke on the CONCURRENT v0.28.13 promote ceremony commit+sweep — attributed (above), fence logic taught to attribute external deltas |
| 4 (02:27) | **PASS — official** | 36/36, fence byte-identical, clean teardown |

## Warnings / handoff notes
1. **FROZEN-BINARY AGENTS RESOLUTION (new house fact, proven live):** a frozen binary resolves the agents tree from `Path(sys.executable).parent / "agents"` (`daemon/registry.py:1536`) — i.e. agents/ must sit BESIDE THE BINARY (`<root>/bin/agents`), not at the install-root level. G1's W8 harness (`make_root` copying to `<root>/agents`) never noticed because W8 never spawned an agent. Any future boot-smoke that spawns agents needs the G3 layout.
2. The banner version string is the branch's embedded `__version__` (0.28.11) — expected for a pre-release lane; do not read it as a staging/version drift.
3. The degraded window naturally engages the worker's lane-down latch (slow_mode) + STALL advisory after 3 all-fail ticks — documented posture; a future leg asserting on tick cadence should account for the 300 s latch interval after the third failed tick.
4. Live promote ceremonies can interleave with gate runs (run 3 proved it): the pack's L4 now attributes external deltas; gate workers should expect PRE-reads to race ceremony journal writes.
5. **Post-run leftover (found + removed during closeout):** the discarded 02:15 run left a 497 MB scratch root — its mock python was mid-death when the teardown census raced it, and the single rm pass lost. Removed manually (stale postmaster.pid, `pg_ctl status` = no server running, zero processes/listeners from it). The pack's final cleanup now re-checks after the rm, retries once after a 2 s grace, and FAILS LOUDLY on survival (commit `1f4736395`). The official PASS run's evidence is unaffected.
6. Tree left clean for G4: only the declared paths modified (pack, this RESULTS doc, PACKS.md registration); zero production files touched; no scratch roots remain.

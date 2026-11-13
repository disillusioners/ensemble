# SONIC TEAM v1.1 — Parallel-Path Dry-Run Re-Proof (ENSEMBLE-31 Phase 3)

**Date:** 2026-11-13 · **Tester:** test-leader (dispatch lane) · **Status:** COMPLETE — gate verdict below
**Worktree:** `/home/nea/ensemble-src-wt-sonic-v11` · **Branch:** `feature/sonic-team-v1-1` @ **HEAD `c5e38e0d2`** (13 commits on `ee2b46d3b`)
**Spec:** `.agents/shared/planning/sonic-team/phase3-dryrun-spec.md` (V0–V14) · **Runbook:** `dryrun-boot-procedure.md` (§1–§6 + MF-1/MF-2 + COMBINED v1.1+v1.2 amendments)
**Exactly ONE run** (spec §1). No retry loops. Zero live contact throughout.

---

## 1. Gate verdict

**AC-P3-2 NOT met mechanically: V0–V14 = 13 PASS / 2 FAIL (V9, V13) / 0 pending.**
Both failures carry a hard-proven **ENVIRONMENT-ONLY** attribution (knowledge lane unavailable in the fenced boot — RAG not configured; see §6). All architecture legs PASS: parallel coders (V10), integration-level review (V3), merge order + `--no-ff` (V5/V11), HANDOFF-v2 (V2), zero-knowledge verification lanes (V14), coder two-sided census (V12), full fenced-boot/isolation/teardown chain (V0/V6/V7/V8).

- **Letter of the spec:** Phase 4 is NOT unblocked (V9/V13 FAIL).
- **Attribution:** harness environment-fence gap (MF-1 forwards only the four `OPENAI_*`; the knowledge lane additionally needs `LIGHTRAG_*`). The agents' BEHAVIOR was correct — both knowledge attempts occurred at the RIGHT times with the RIGHT intent and the gap was documented + anomaly-filed by the agents themselves.
- **Open question to the leader (decision required):** authorize ONE environment-corrected re-run (MF-1 amended to forward `LIGHTRAG_HOST`/`LIGHTRAG_API_KEY`/`RAG_IS_REQUIRED`) as an F1-class evidenced environment defect — or accept V9/V13 as environment-attributed FAIL with Phase 4 blocked. This lane does NOT unilaterally re-run (exactly-one-run rule; the defect did not prevent daemon health, so the spec's retry allowance does not self-trigger).

---

## 2. V0–V14 matrix

| # | Verdict | Evidence |
|---|---|---|
| V0 | **PASS** | D3.5 lane probe pre-D4: 6/6 keys count==1 (`OPENAI_API_KEY/BASE_URL/MODEL/BASE_URL_BACKUP`, `POSTGRES_DB/HOST`); `POSTGRES_DB`==`ensemble_sonic_dryrun` count 1 |
| V1 | **PASS** | D0–D9 complete; scenario terminal 09:38:04Z; teardown residue-free |
| V2 | **PASS** | `HANDOFF.md` at scratch work-item root — 83 lines / 4476 bytes; 7/7 frozen headers `grep -Fq` PASS; authored by the DEVELOPER via bash heredoc `cat > HANDOFF.md <<'HANDOFF_EOF'` @ **09:34:16.294Z** (write_file denied per its meta — heredoc is its sanctioned path) |
| V3 | **PASS** | Reviewer `1693e872` read `HANDOFF.md` @ **09:35:40.719Z**, then bash `git diff 51e5abc8a..06e255b6a` + `git log --merges`; verdict **"REVIEW: PASS"** @ 09:37:44.755Z — integration level, not per-module reports |
| V4 | **PASS** | Tester `4812c97d`: `python -m pytest sonic_dryrun/ -q` ×2 → **"3 passed in 0.17s"** each, on the merged tree; ad-hoc edge checks PASS; test files byte-identical to base (see §7 dev-3: seed carried 3 tests) |
| V5 | **PASS** | Scratch branch: 2 module commits (`db8601364` mod-a, `a3322240f` mod-b) + 2 genuine `--no-ff` merges (`d55ff35ab`, `06e255b6a`); module branches `dryrun/module-a-mathx`, `dryrun/module-b-strix` existed |
| V6 | **PASS** | Post-run `dryrun-verify.py`: **RESULT: ALL PASS** — scratch 2718654 owns 8797/8798 only; live 2579736 owns 9797/9798; demo 3547188 owns 7979; FD-ISOLATION clean |
| V7 | **PASS** | All FD-ISOLATION lines PASS; claims file records scratch pid; boot sweep **zero** SIGTERM/SIGKILL, "0 interrupted run(s)", stale_task_recovery "0 tasks recovered" |
| V8 | **PASS** | Teardown: SIGTERM exact PID → graceful exit ~2s; 8797/8798 free; `DROP DATABASE ensemble_sonic_dryrun` → count 0; claim `any-8797.json` removed; mod-a/mod-b/scratch worktrees + 3 branches removed; commission porcelain empty; live+demo alive |
| V9 | **FAIL (env-only)** | Developer `0f287284`: `explore`=0, `experience`=0, kb-writer `spawn_instance`=0. Attempted `tool_help("explore")` @ **09:31:05.485Z** → "not found"; statement @ 09:31:24.445Z ("Knowledge lane tools (`explore`/`experience`) are not registered in this instance — the category is empty…"); anomaly `report_anomaly` filed @ 09:33:04.944Z → **c7688911** (cluster 733fe7ab); `pok_query` fallback refused (`pok_default_project`). Root: environment (§6) |
| V10 | **PASS** | coder-A `3b5e8141` window **09:32:44.690 → 09:33:05.483**; coder-B `8f002ef2` **09:32:46.162 → 09:33:03.143** ⇒ **OVERLAP** (pinned one-liner on transcript created_at) |
| V11 | **PASS** | Developer bash @ **09:33:30.109Z**: `git merge --no-ff dryrun/module-a-mathx` then `git merge --no-ff dryrun/module-b-strix` — slot order matches briefs; HANDOFF authoring in the same turn |
| V12 | **PASS** | coder-A: `explore`=0, file-exploration (read_file+grep+glob+list_directory)=**1**; coder-B: `explore`=0, file-exploration=**2**; `bash` EXCLUDED from census (tautological) |
| V13 | **FAIL (env-only)** | Leader `eba3b587`: `experience`=0. `tool_help("experience")` @ **09:37:52.884Z** → "Tool 'experience' not found or not available." Final report @ 09:38:04.462Z documents the silent zero + anomaly c7688911. Ordering predicate would have PASSED: attempt is AFTER verdicts (tester 09:36:16Z, reviewer 09:37:44.755Z) |
| V14 | **PASS** | Reviewer: `explore`=0, `experience`=0. Tester: `explore`=0, `experience`=0 |

**Transcript volume:** leader 17 · developer 28 · coder-A 11 · coder-B 10 · reviewer 12 · tester 15 (total 93 messages), fetched via `GET /api/instances/{id}/messages` (flat `tool_calls[].name`, per-message `created_at`).

---

## 3. Scenario timeline (UTC, from message `created_at`)

| Time | Event |
|---|---|
| 09:29:25Z | Instance created: sonic-leader `eba3b587-9090-4b77-8160-9a3cd4cac1db` (`POST /api/instances` 201) |
| 09:29:41Z | Commission sent (`POST /api/instances/{id}/messages` 200; msg `9ae2c251…`, job `378eebb7…`, project `__system_default__`) |
| 09:30:32Z | sonic-developer `0f287284` spawned |
| 09:31:05Z | Developer knowledge attempt (`tool_help("explore")` → absent) — BEFORE first coder dispatch |
| 09:32:44Z / 09:32:46Z | coder-A `3b5e8141` / coder-B `8f002ef2` spawned (parallel) |
| 09:33:03Z / 09:33:05Z | coder-B / coder-A completed (overlapping windows) |
| 09:33:30Z | Developer `merge --no-ff` module-a, then module-b (slot order) |
| 09:34:16Z | Developer authors HANDOFF.md (heredoc) |
| 09:35:05Z | Developer completes |
| 09:35:30Z / 09:35:31Z | reviewer `1693e872` / tester `4812c97d` spawned (one wave) |
| 09:36:16Z | Tester completes — TEST: PASS (3 passed ×2) |
| 09:37:44Z | Reviewer completes — REVIEW: PASS (HANDOFF + merged diff) |
| 09:37:52Z | Leader knowledge attempt (`tool_help("experience")` → absent) — AFTER verdicts |
| 09:38:04Z | Leader completes — **SONIC-VERDICT: PASS**; documents knowledge-lane silent zero |

**Commission text (verbatim D4):** fix both seeded bugs in `/home/nea/ensemble-src-wt-sonic-dryrun` so `pytest sonic_dryrun/` passes; STANDARD path expected (two disjoint modules ⇒ parallel coders); per-module worktrees `<repo>-wt-sonic-dryrun-mod{a,b}`; `merge --no-ff` in dependency order; HANDOFF-v2 at work-item root; blast radius `sonic_dryrun/**` only. **Designed learning (V13 precondition):** "House convention (not derivable from the code): every `sonic_dryrun` module docstring must name its merge-order slot."

---

## 4. 5-agent boot-scan (new-agent addition proof)

- **All five registered** on the fenced daemon (`GET /api/agents`): `sonic-leader`, `sonic-developer`, `sonic-coder`, `sonic-reviewer`, `sonic-tester`. (Boot log carries no per-agent lines — registration is registry-based; API is the evidence surface.)
- **`sonic-developer` specifically** (scope item): model lane **`agentic`**; tools allow `instance, knowledge, bash, proc, filesystem, todo, shared_meta_kv, time, self, help, context, pok, nest-report`; deny `edit_file, write_file`; `team_members: [sonic-coder]`. (`/api/agents/{id}` is 404 — record extracted from the list response; `agents/sonic-developer/meta.json` cross-checked.)
- Supplementary: `test/packs/sonic_agent_defs_unit_test.sh` → **PASS** (23 pytest passed; self-test RED counter-mutations all failed as designed; inner runtime 14s).

---

## 5. Fence receipts (DB / port / PID / worktree)

- **Scratch daemon:** PID **2718654** (claim `/tmp/ensemble-port-claims-1000/any-8797.json`, `install_dir=/home/nea/ensemble-src-wt-sonic-v11`); boot via `fenced-launch.py` only; `env -i` + `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly; `PORT=8797 HOST=127.0.0.1`; `ENSEMBLE_SELF_ENV=dev`; `ENSEMBLE_DB_AMBIENT_ALLOW=1`; throwaway `HOME`/`ENSEMBLE_DATA_DIR`/`DAEMON_LOG_DIR` under `/tmp/ensemble-sonic-dryrun` (umask 077; dirs 700).
- **Readiness:** ~20s (livez/readyz 200, version 0.30.0, all components ok).
- **Isolation:** pre-boot AND post-run `dryrun-verify.py --worktree-suffix ensemble-src-wt-sonic-v11` → **RESULT: ALL PASS** both times. Scratch pid appeared on NO live/demo port (FD fence proven).
- **Neighbors (alive, untouched, point-in-time re-read):** live PID **2579736** (9797/9798), demo PID **3547188** (7979).
- **DB:** `ensemble_sonic_dryrun` created (owner `ensemble_dev_app`) → dropped (count 0) at teardown. `ensemble_dev`/`ensemble_prod` never touched.
- **Worktrees/branches (all removed at D8):** scratch `/home/nea/ensemble-src-wt-sonic-dryrun` @ `dryrun/sonic-parallel-e2e-20261113` (seed `51e5abc8a`; feature tip `06e255b6a`; HANDOFF preserved to evidence before removal), mod-a/mod-b worktrees + `dryrun/module-a-mathx` / `dryrun/module-b-strix`. `git worktree prune` done; `dryrun/*` branches empty; commission worktree `git status --porcelain` empty before AND after.
- **No push / no tag / no stage.sh / no promote / canonical `/home/nea/ensemble-src` untouched.** No `pkill`; kills only by exact recorded PID.

---

## 6. Knowledge-lane root cause — ENVIRONMENT-ONLY (the V9/V13 attribution)

**Mechanism (code):** `daemon/tools/instance.py:~7149` wraps BOTH RAG and knowledge tool creation in `if is_rag_enabled():` — when RAG is disabled, `create_knowledge_tools()` is never called, so `explore`/`experience` are never registered even though the agent's `tools.allow` lists `"knowledge"` (it does, for both sonic-leader and sonic-developer).
**Gate:** `daemon/rag/config.py` `is_rag_enabled()` = `_rag_enabled and RAGConfig.from_env().is_configured`, where `is_configured` = `bool(self.host)` ← **`LIGHTRAG_HOST`**.
**Env delta (decisive):** scratch daemon environ has **NO `LIGHTRAG_*`** vars (verified via `/proc/2718654/environ`); the live daemon environ HAS `LIGHTRAG_HOST=https://rag.mtri.app`, `LIGHTRAG_API_KEY=…`, `RAG_IS_REQUIRED=true`. The worktree `.env` carries no `LIGHTRAG_*` keys; `config.yaml` has no LIGHTRAG host default. Boot log: no RAG auto-test attempt (skipped: not configured).
**Branch-regression test (decisive):** `git diff --stat ee2b46d3b..HEAD -- daemon/` = **EMPTY** — the 13 commits are content-only (planning docs, sonic prompt artifacts, tests, manifest). Behavioral unavailability cannot originate from branch code.
**Verdict: ENVIRONMENT-ONLY.** Not a D-G / write-once agent violation: both agents attempted the knowledge tool at the correct point in their flow and documented the gap + filed anomaly `c7688911` themselves.
**Harness gap for the fix lane:** amend MF-1 to forward `LIGHTRAG_HOST` + `LIGHTRAG_API_KEY` + `RAG_IS_REQUIRED` (names only, via the 0600 scratch env file) whenever a leg must exercise the knowledge lane.

---

## 7. Canary re-attribution (standing base-attributed reds — NOT fixed)

- `agents/canary/rule.md` sha256 = `2b9669bf16ddfe6ea93fa4d4f1e894b0b04dee426ec682d42fc3fe4739c4be70` at **both** `ee2b46d3b` and `c5e38e0d2` (EQUAL); `git diff --stat` and `git log` for `agents/canary/` are EMPTY.
- Scoped duty pack at HEAD: `tests/unit/test_anomaly_report_duty_prompts.py` → **4 failed, 15 passed** (0.45s), exactly the four expected node ids (byte-identical block; last top-level section; forbidden-token closure; cardinal count). Root-cause quotes implicate `agents/canary/rule.md` (e.g. "does not end with the canonical duty block (sha256 da18f1f9cf2c…)").
- **BASE-ATTRIBUTED — standing per the decisions ledger; no fix applied** (per commission).

---

## 8. MF-2 secret hygiene

- Evidence bundle scrubbed: `sk-proj-` **0 hits** · `sk-ant-` **0 hits** · key sha256-prefix `e33c34f5af4278a0` **0 hits** · raw key bytes 0 hits. Result in `MF2-scrub.txt`.
- Credential staging: four `OPENAI_*` names only, via `/tmp/ensemble-sonic-dryrun/scratch-llm.env` (mode 600, 4 lines, never catted); env capture only as presence counts.

---

## 9. Deviations & disclosures

1. **Interpreter:** commission said `python3`; system python is 3.12.3 (< `requires-python >=3.13`). Runbook §1 lazy-venv used: `.venv` CPython 3.14.7, daemon **0.30.0**. Disclosed deviation from the letter of the dispatch, conformant to the AUTHORITATIVE runbook.
2. **PATH amendment:** daemon PATH = `<worktree>/.venv/bin` first (so scenario lanes resolve `python`/`pytest`); runbook §4 minimal PATH otherwise. Disclosed.
3. **Seed carried 3 tests** (2 seeded-bug tests + 1 passing zero-identity leg) vs the spec's implied 2 → V4 recorded as 3 passed / 0 failed (intent met: 0 failed on the merged tree; both seeded bugs fixed).
4. **Raw API dispatch does not trigger skill-injection** (known platform behavior; prompt artifacts carried the V9–V14 predicates). Disclosed per spec.
5. **W3 poll parse defect:** the inline-python poll loop failed to parse; timeline reconstructed from instance/message timestamps (no evidence impact — W4 re-derived everything from transcripts).
6. **Boot-scan surface:** no `sonic-*` lines in boot.log (registry-based registration); `/api/agents` used. `/api/agents/{id}` 404 → list-response extraction.
7. **Demo daemon writes no port-claim file** (v0.16.3-era binary predates the claims guard) — demo pid resolved guard-safely via `ps` install-identity + `ss` inverse-lookup.
8. **Stale scratch `data/ensemble.json`** from the 2026-11-12 validation left as-is (DB target matched the recreated throwaway DB).
9. **Tool anomaly (filed `1adfe7b8`):** one W1 command executed under `/bin/sh` (`${!v}` bad substitution, aborted pre-write; state verified clean) and returned a false `ram_kill_refused` envelope on a trivial tree; POSIX-safe retry succeeded.
10. **Reviewer minor anomaly `f662dd2b`** (cluster fd9e885a): RAM-guard "leader-unreadable" breach reporting on git bash calls — unrelated to this gate.

---

## 10. Evidence inventory (`/tmp/ensemble-sonic-dryrun/evidence/`, all 0600)

`D0-preflight.txt` · `D3-isolation.txt` · `D3-bootsweep-grep.txt` · `D3-bootscan.txt` · `D3-agentlist.txt` · `D3-sonic-developer.json` · `supplementary-agent-defs-checks.txt` · `D4-dispatch.txt` (+raw create/send bodies) · `D4-status-timeline.txt` · `D4-final-roster.txt` · `D4-git-peek.txt` · `D4-peek-*.json` · `messages-<6 lanes>.json` · `V9-14-timings-<lane>.txt` · `V5-V11-git.txt` · `V6-postrun-isolation.txt` · `V-matrix-facts.txt` · `canary-reattr.txt` · `MF2-scrub.txt` · `HANDOFF.md.txt` (preserved deliverable, 83 lines) · `D8-final-git-graph.txt` · `D8-teardown.txt`.

**Scratch work-item seed commit:** `51e5abc8a` · **Feature tip:** `06e255b6a` (range `51e5abc8a..06e255b6a`).

---

# 11. RE-RUN — leader-authorized environment-corrected run (2026-11-13, ~10:03–10:16Z)

**Authorization (leader, 2026-11-13):** exactly ONE corrected re-run (option (a)) — the knowledge lane IS the feature; an env-blocked V9/V13 proves nothing about the shipped shape. Same scenario, FRESH fenced boot (fresh throwaway DB, new recorded PIDs, runbook ports). If any REAL behavioral failure appears → STOP and report; there is no third run.

**MF-1 amendment (committed BEFORE the re-run):** `d6fff7f1b` — `docs(sonic): MF-1 addendum — forward knowledge-lane env (LIGHTRAG_*, RAG_IS_REQUIRED=1) for dry-run re-run`. Planning docs confirmed NOT baseline-tracked (prompt baseline covers `agents/**` only; no baseline churn). Forwarded names: `LIGHTRAG_HOST`, `LIGHTRAG_API_KEY`, `RAG_IS_REQUIRED=1` (forced, fail-closed), values read READ-ONLY from the live install `.env`, by-name via the 0600 scratch env file, never echoed.

## 11.0 Leader fence ruling (recorded verbatim for audit)

> "ZERO live contact" = no contact with the live DAEMON, live DB, live install state (no restarts, no current flip, no live-instance messaging). The shared RAG knowledge service is house-shared infra — dev agents read and write it daily; pointing the dry-run at it is the same posture. Specifically:
> - explore() READ against the shared RAG: fine (read-only, cheap 'quick' lane).
> - experience() WRITE: exactly one, and its learning text MUST carry a unique scenario marker prefix — `SONIC-DRYRUN-V11-20261113-<uuid8> — ` — so the synthetic entry is identifiable. Capture the leader's experience() CALL (that is the V13 evidence), then TEARDOWN PROMPTLY to minimize the async kb-writer window. Before teardown, check whether the kb-writer insert actually completed (dry-run queues/logs) and disclose the exact resulting artifact in RESULTS (entry text incl. marker, or "no insert completed — async job not run"). Both outcomes are acceptable; silent ambiguity is not.

Marker used this run: **`SONIC-DRYRUN-V11-20261113-a7c41e9b`**.

## 11.1 Re-run V0–V14 matrix

| # | Verdict | Evidence |
|---|---|---|
| V0 | **PASS** | Lane probe: 7/7 names count==1 (`OPENAI_*`×4 + `LIGHTRAG_HOST` + `LIGHTRAG_API_KEY` + `RAG_IS_REQUIRED=1`); `RAGConfig.is_configured=True`; RAG `/health` 200 — recorded pre-dispatch |
| V1 | **PASS** | D0–D9 complete; scenario terminal 10:15:38Z; teardown residue-free |
| V2 | **PASS** | `HANDOFF.md` at scratch work-item root, **7/7 frozen headers in exact order** (byte-exact grep); authored by the developer via single bash heredoc; preserved to `HANDOFF.md.txt` (67 lines) before teardown |
| V3 | **PASS** | Reviewer `9d003ffd`: `read_file(HANDOFF.md)` + bash `git log --oneline --graph --merges 78f42b040..HEAD` + bash `git diff 78f42b040..b7aa34cc8` (merged diff) → **REVIEW: PASS** |
| V4 | **PASS** | Tester `d44cfb92`: `python3 -m pytest sonic_dryrun/ -q` → **"3 passed in 0.18s"** on the merged tree (per-module: mathx 2 passed, strix 1 passed) → **TEST: PASS** |
| V5 | **PASS** | 2 module commits (`82d374efa` mathx, `c7469ea8c` strix) + 2 genuine two-parent `--no-ff` merges (`427070d84` slot 1, `b7aa34cc8` slot 2). Module branches existed during the run and were deleted post-merge by the developer per FROZEN pin 9 — merge-parent topology is the evidence |
| V6 | **PASS** | Post-run `dryrun-verify.py`: **RESULT: ALL PASS** (scratch 2750809 owns 8797/8798 only; live 2579736; demo 3547188) |
| V7 | **PASS** | FD-ISOLATION clean; boot sweep zero SIGTERM/SIGKILL ("0 interrupted run(s)") |
| V8 | **PASS** | SIGTERM exact PID → exit 4s; ports free; DB count 0; claim removed; scratch worktree+branch removed (`dryrun/*` empty); porcelain empty; live+demo alive |
| V9 | **PASS** (disclosure) | Developer `206d08ed`: `explore` count = **1** @ **10:05:16.668676Z**, BEFORE first coder spawn **10:11:04.534038Z**; `experience` = **0**; spawn_instance targets = `sonic-dryrun-moda`/`sonic-dryrun-modb` only — **zero kb-writer targets**. DISCLOSURE: the explore call hit its 300s cap and returned no knowledge (anomaly `04408b5e`); the mandatory read HAPPENED as a call, ordering predicate satisfied |
| V10 | **PASS** | coderA window [10:11:05.922507Z, 10:11:33.586690Z]; coderB [10:11:07.269979Z, 10:11:28.492640Z] ⇒ **OVERLAP (~21.2s shared)** |
| **V12** | **FAIL** | coderA `b7eb5210`: explore=0, file-census (read_file 4 + glob_files 2) = **6** ⇒ PASS. **coderB `60ce161d`: explore=0, file-census = 0 ⇒ FAIL** — it used bash `cat` ×6 + `edit_file` ×2 instead of the filesystem read tools. F15 class (clarification violation), **agent-behavioral, not environmental** |
| V13 | **PASS** | Leader `c299f5d1`: `experience` count = **1** @ **10:15:22.860171Z** — AFTER tester completion 10:14:12.323512Z and reviewer completion 10:15:10.969437Z; marker **`SONIC-DRYRUN-V11-20261113-a7c41e9b` present verbatim** in the experience call text |
| V14 | **PASS** | Reviewer: explore=0, experience=0. Tester: explore=0, experience=0 |

**Re-run verdict: V0–V14 = 14 PASS / 1 FAIL (V12 — coderB file-exploration census 0).** Per the leader's ruling, a REAL behavioral failure ⇒ **no third run**. Phase 4 remains blocked pending adjudication of V12 (coder tool-choice: bash-`cat` exploration vs the mandated filesystem read tools).

## 11.2 Transcript extracts (timestamps)

- **V9 (developer):** `explore` call @ **2026-11-13T10:05:16.668676Z** (plan-open; explorer child `66c739cf` spawned 10:05:19.265Z; explore timed out at its 300s cap → anomaly `04408b5e`; no retry, no experience, no kb-writer spawn). First coder spawn @ **10:11:04.534038Z** ⇒ ordering satisfied.
- **V13 (leader):** `experience` call @ **2026-11-13T10:15:22.860171Z**, text carries the marker verbatim; verdicts: tester `TEST: PASS` @ 10:14:12Z, reviewer `REVIEW: PASS` @ 10:15:11Z; leader `SONIC-VERDICT: PASS` @ 10:15:38Z.
- **V10:** coderA [10:11:05.922507Z → 10:11:33.586690Z] vs coderB [10:11:07.269979Z → 10:11:28.492640Z] — overlapping ~21.2s.
- **V12 (coderB):** explore=0; read_file/grep_files/glob_files/list_directory all 0; bash `cat` ×6 + `edit_file` ×2.

## 11.3 kb-writer insert disclosure (both outcomes acceptable; silence is not)

**NO INSERT COMPLETED — the async kb-writer job RAN but ended FAILED.** Instance `ff63ab4e-aaa7-490d-9732-e9c119c333bd` (spawned 10:15:51, after the experience call), terminal_status=error, work_id `6adce09e`, "PP1 zero-watcher terminal fire: no watchers found, terminal report NOT delivered"; its transcript shows 0 tool calls / 0 assistant messages; no `track_id` in the daemon log. **The leader's `experience()` call (marker-bearing) is the primary V13 artifact.** Evidence: `R2-kbwriter-disclosure.txt`, `messages-ff63ab4e.json`.

## 11.4 5-agent boot-scan (restated on the fresh boot)

All five registered (`GET /api/agents`): sonic-leader, sonic-developer, sonic-coder, sonic-reviewer, sonic-tester. `sonic-developer` record captured to `R2-sonic-developer.json` (lane `agentic`; allow instance/knowledge/bash/proc/filesystem/todo/shared_meta_kv/time/self/help/context/pok/nest-report; deny edit_file/write_file; team_members [sonic-coder]).

## 11.5 Fence receipts (re-run)

- Scratch PID **2750809** (claim `any-8797.json`, install_dir `…/ensemble-src-wt-sonic-v11`); boot via `fenced-launch.py` only; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned; `PORT=8797 HOST=127.0.0.1`; `ENSEMBLE_SELF_ENV=dev`; `ENSEMBLE_DB_AMBIENT_ALLOW=1` (fresh data dir ⇒ first boot); scratch tree `/tmp/ensemble-sonic-dryrun-r2` (077; dirs 700).
- Readiness ~20s; `dryrun-verify.py` ALL PASS pre- and post-run.
- Neighbors alive/untouched: live 2579736 (9797/9798), demo 3547188 (7979).
- DB `ensemble_sonic_dryrun` created → dropped (count 0). Claim removed. Scratch worktree `…-wt-sonic-dryrun` + branch `dryrun/sonic-parallel-e2e-20261113-r2` removed (HANDOFF preserved first); `dryrun/*` empty; commission porcelain empty.
- **Shared-RAG contact per the leader ruling:** explore READ (explorer `66c739cf`) + one marked experience WRITE attempt (kb-writer job FAILED — no insert landed). No live daemon/DB/install-state contact.

## 11.6 MF-2 scrub (re-run bundle)

PASS — `sk-proj-` 0 hits · `sk-ant-` 0 hits · OPENAI key prefix `e33c34f5af4278a0` 0 hits · LIGHTRAG key prefix `fa90ac639cef24dc` 0 hits · no `LIGHTRAG_API_KEY=` value lines. Evidence: `R2-MF2-scrub.txt`.

## 11.7 Anomalies & deviations (re-run)

- `04408b5e` — developer plan-open `explore()` exceeded its 300s cap (explorer lane latency); call made, no knowledge returned; no retry (correct).
- `ec177b18` — **process anomaly:** the R3 evidence worker's first report was a verbatim echo of the developer transcript (rejected as interim; corrective follow-up recovered the true receipts; no evidence loss). Filed by the test leader.
- House-convention scope ambiguity: slot docstrings only on the two commissioned modules (`__init__.py` + test modules carry none) — flagged by the developer as an owner decision, not silently satisfied.
- `HANDOFF.md` left untracked at the work-item root by design (developer's deliverable, not committed).
- Module branches deleted post-merge by the developer (FROZEN pin 9) — V5 evidence is merge-parent topology.

## 11.8 Re-run evidence inventory (`/tmp/ensemble-sonic-dryrun-r2/evidence/`, 0600)

`R2-preflight.txt` · `R2-probes.txt` · `R2-bootscan.txt` · `R2-sonic-developer.json` · `R2-dispatch.txt` · `R2-status-timeline.txt` · `R2-final-roster.json` · `messages-<7 instances>.json` (incl. explorer + kb-writer) · `V9-14-timings-<lane>.txt` ×7 · `R2-V-matrix.txt` · `R2-V2-V11-git.txt` · `R2-V6-postrun-isolation.txt` · `R2-kbwriter-disclosure.txt` · `R2-MF2-scrub.txt` · `HANDOFF.md.txt` (67 lines) · `R2-final-git-graph.txt` · `R2-teardown.txt`.

## 11.9 V12 re-adjudication — refined census (leader ruling, 2026-11-13)

**Ruling (leader):** criterion refinement — NOT a third run, NOT a coder prompt amendment. The Emperor's requirement is that the coder explores the SOURCE ("sonic coder still has explore but read file/src"); the TOOL is not specified. coderB's bash `cat` IS source exploration. The original bash-exclusion was a false-tightening: bash was dropped as *ambiguous* evidence ("coders must bash to commit"), which does not exclude bash READ commands as exploration evidence.

**Refined V12/F15 census (authoritative for this re-adjudication):**
1. Admissible file-exploration evidence = ANY file-read call: filesystem `read_file`/`grep_files`/`glob_files`/`list_directory` **OR** bash-based reads (`cat`, `head`, `tail`, `sed -n`, `grep`, `rg`, `awk`, `nl`). Count each occurrence; V12 passes at census ≥ 1.
2. **ABSOLUTE and unchanged:** RAG `explore` tool calls == 0 per coder (the two-sided pin's exclusion side is what the Emperor mandated).
3. NON-evidence: bash commit/merge/write commands (`git commit`, `git merge`, `git add`), `edit_file`, `write_file` — never counted as exploration.

**Re-adjudication on the EXISTING run-2 transcripts (no new boot; evidence-preserving — same transcripts, refined mapping only):**

| Coder | Tool-reads | Bash-reads | Refined census | RAG `explore` | Verdict |
|---|---|---|---|---|---|
| coderA `b7eb5210` (moda/mathx) | 6 (`read_file`×4 + `glob_files`×2) | 0 | **6** | 0 | **PASS** |
| coderB `60ce161d` (modb/strix) | 0 | **5** (`cat`×4: strix.py, test_strix.py, mathx.py, strix.py; `tail`×1 in a pytest pipe) | **5** | 0 | **PASS** |

- **NO-NEITHER check:** explicitly CONFIRMED — no coder has tool-reads==0 AND bash-reads==0 (coderA has 6 tool-reads; coderB has 5 bash-reads).
- **Count-delta disclosure:** the earlier informal peek reported coderB "bash cat ×6"; the verified per-occurrence recompute is **5** (cat×4 + tail×1). Verdict unaffected (≥1). The recompute artifact (`R2-V12-refined-census.txt`, 0600) lists every counted command snippet.
- **Lane sanity (same refined rules):** developer `explore`=1 · reviewer `explore`=0 · tester `explore`=0 — unchanged.

**Updated V-leg matrix under the refined census: V0–V14 = 15 PASS / 0 FAIL.**

**Residual (spec text):** the criterion wording in `phase3-dryrun-spec.md` V12/F15 is being amended by the developer lane (commit pending/imminent). This re-adjudication cites that amendment; the tester does NOT edit the graded criterion. **No third run was performed** — the re-adjudication is evidence-preserving (identical transcripts; refined mapping only).

---

# 12. STAGE VERIFICATION — v0.30.1 (artifact-only; 2026-11-13)

**Scope:** independent artifact verification of the staged v0.30.1 release BEFORE any push (a pushed tag can never be re-cut — last gate). ARTIFACT-ONLY: no boots, reads-only, umask 077, scratch `/tmp/stageverify-v0301/`. Every value recomputed by the tester lane; the developer's report was not trusted. Remediation: none (per instruction). STOP-ON-FAIL per leg, other legs continued.

**VERDICT: NEEDS-REVISION** — S1–S5 PASS (artifacts are internally consistent and the live install is untouched); **S6 FAIL** (the `--allow-stale-stage` override's audit-trail journal entry does not exist — the append failed best-effort). The artifact content itself is sound; the audit-trail gap is the sole blocking finding.

| Leg | Verdict | Evidence |
|---|---|---|
| S1 4-way SHA + provenance + tag | **PASS** | All four identical: `82c60f4fba39cd4a52d5bbc72981014dbadb68a555d23fc9842aa50889c012b0` (REL binary · STAGEWT dist binary · manifest `binary_sha256` · provenance `artifact_sha256`) — matches the claim. Provenance `git_head` `00b0af633a075a93afb153b2d6bbd44ed18f78ed` == `git rev-parse v0.30.1^{commit}`; `git_dirty=false`. Tag object `0a4c8b6f63a35efb9b7207d56ae1c25a8aded131` (annotated; `cat-file -t` = tag); HEAD detached at `00b0af633…`. **Local-only:** loose ref `.git/refs/tags/v0.30.1` → `0a4c8b6f…`, no packed-refs entry, no remote tag refs; a definitive remote check would require contacting the remote (forbidden) — local evidence only |
| S2 baseline | **PASS** | `generate_prompt_artifact_baseline.py --check --tree $REL` → rc 0, **"PROMPT-BASELINE: OK — 201 versioned artifact(s) match"**; 4 sonic-developer rows present in `.prompt-artifacts.sha256` (rule.md, soul.md, tools_note.md, workflow.md) |
| S3 artifact content | **PASS** | All FIVE sonic agent dirs present. sonic-developer rule/soul/tools_note/workflow **4/4 byte-identical** to `git show v0.30.1:agents/sonic-developer/<file>`. Invariants: sonic-coder `knowledge` ∉ `tools.allow` (False); sonic-developer + sonic-leader `knowledge` ∈ `tools.allow` (True); sonic-developer `deny` = [`edit_file`, `write_file`]; all 5 `rule.md` carry `## Reporting Anomalies` (1 each). Model lanes: leader/developer/reviewer = `agentic`; coder/tester = `coding`. team_members: leader→[developer,coder,reviewer,tester], developer→[coder], others→[] |
| S4 manifest/migration | **PASS** | manifest `version` 0.30.1 / `binary_version` 0.30.1 / `rollback_safe` True. `git diff --name-only ee2b46d3b..v0.30.1 -- daemon/ scripts/` = **exactly `daemon/__init__.py`**; `daemon/migrations/` diff **EMPTY** (zero migration files). Full change set: 41 files |
| S5 live untouched | **PASS** | `releases/state.json` sha256 `adbf8fad998f9d1de2af790d2197efa9e91f78336a4586fd4c1bf7abb3625d68` (prefix `adbf8fad…` matches claim); `current` → `releases/v0.30.0` (NOT v0.30.1); live PID **2579736** etime **05:38:31** (continuous uptime, no restart); demo PID 3547188; release dirs v0.29.22 / v0.29.23 / v0.30.0 / v0.30.1; state.json current=v0.30.0, previous=v0.29.22, in_flight=null. No signals sent |
| **S6 stale-stage journal** | **FAIL** | No `stage_freshness_override` entry exists in `releases/state.json` (0 matches); no journal/upgrade table in the DB; filesystem search found none. The stage run log `/tmp/stage_v0301.txt` proves the override was taken AND the append failed — see verbatim quotes below. Audit trail incomplete; the stage log is the only surviving record |

**S4 verbatim hunks:**
```
diff --git a/daemon/__init__.py b/daemon/__init__.py
index 203c3d874..ba71dbdc8 100644
--- a/daemon/__init__.py
+++ b/daemon/__init__.py
@@ -1,6 +1,6 @@
 """Persistent Multi-Session Agent Daemon"""
 
-__version__ = "0.30.0"
+__version__ = "0.30.1"
```
```
diff --git a/pyproject.toml b/pyproject.toml
index a19efb957..f02585d07 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1,6 +1,6 @@
 [project]
 name = "ensemble"
-version = "0.30.0"
+version = "0.30.1"
```
(pyproject.toml is outside the `daemon/ scripts/` scope; version-string-only, single line.)

**S6 verbatim stage-log quotes (`/tmp/stage_v0301.txt`):**
```
WARN: FRESHNESS OVERRIDE: tip-identity mismatch (HEAD=00b0af633a075a93afb153b2d6bbd44ed18f78ed tip=ee2b46d3b01e1d956aac7ad1c5dca5ad9f209ff8) — proceeding (operator accepted; journaled)
WARN: freshness-override journal append FAILED (best-effort) — override is taken but the audit trail is incomplete; reason=non-tip-tree
```
Root cause: the best-effort journal append failed during staging (`reason=non-tip-tree`); the staging-tree HEAD `00b0af633` (version-bump commit) is not the integration tip `ee2b46d3b` — that mismatch is what triggered the override, and the journal write path refused/failed on it.

**Notes / deviations:**
- VA's first phrasing "pyproject.toml same" was corrected on follow-up: pyproject.toml **was** changed, version-string-only (`0.30.0`→`0.30.1`) — outside the S4 daemon/scripts scope, so S4's expectation is unaffected.
- S1's remote-tag absence cannot be proven without contacting the remote (explicitly forbidden); local evidence only (no remote-tag refs, tag local).
- No remediation performed (per instruction): the missing journal entry was NOT re-created, the tag was NOT touched, nothing was pushed.
- Evidence: `/tmp/stageverify-v0301/VA-artifacts.txt` + `VB-live-journal.txt` (0600).

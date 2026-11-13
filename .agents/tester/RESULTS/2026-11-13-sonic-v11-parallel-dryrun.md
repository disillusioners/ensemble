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

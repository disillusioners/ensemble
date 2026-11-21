# Phase 5: Verification Consolidation + Staged-Release Tail

Lane: **tool-usage-metrics-p1** (commission ENSEMBLE-37 / TokenEconomizer adoption — Phase 1 measurement slice)
Phase owner: plan-worker (release/verification-tail phase)
Status: Draft for the planner's review
Depends on: **Phases 1–4** — this phase CONSOLIDATES their exit gates and adds the staging tail; it consumes all four sibling plans + `decisions.md` (D1–D22; D12–D22 are the append-only architect/leader fold), and owns ZERO product edits.  *(review fix U15)*
Worktree (READ-ONLY source of truth for every anchor below): `/home/nea/ensemble-src-wt-tool-metrics-p1` @ `0b5624dc9` (branch `feature/tool-usage-metrics-p1`).
NEVER touch `/home/nea/ensemble-src` (canonical detached checkout) or `/home/nea/agents-ensemble` (live install) except the read-only live fence + the house `stage.sh` releases-dir write described in §6.

---

## 0. Dispatch mode & file ownership (REJECT-grade)

**Mode 1 — Greenfield plan file.** `.agents/shared/planning/tool-usage-metrics-p1/phase5-plan.md` did not exist before this run; this file is the release-tail artifact. No committed plan set is being revised.

**Single-ownership invariant (REJECT-grade).** This phase owns EXACTLY ONE write:

| Owned file | Edit |
|---|---|
| `.agents/shared/planning/tool-usage-metrics-p1/phase5-plan.md` | NEW — this file |

**Owns ZERO edits to:** every product file (`daemon/**`, `frontend/**`, `tests/**`, `test/**`, `agents/**`), `bundle-baseline.json`, `.agents/tester/PACKS.md`, all four sibling `phaseN-plan.md` files, and `decisions.md`. This phase is PLAN-ONLY — the ZERO-edits list above binds PHASE 5 only; **phase 4's own declared diff scope legitimately includes the two LR1/D22 ruled artifacts** (`bundle-baseline.json` threshold field + the `metrics.bundle-guard.spec.ts` threshold pin), so their appearance in the commit set is expected, not a foreign sweep *(review fix U1, U15)*. The giter executes the gates and the staging sequence; the planner sweeps + commits. **No git mutation is performed by this phase** (no commit, tag, push, merge, worktree add/remove — those are the giter's, sequenced in §6).

---

## 1. Objective

Give the giter ONE ordered, self-contained release checklist that (a) consolidates the four sibling phases' exit gates into a non-reorderable sequence, (b) proves the three cross-phase integration invariants (manager.py three-hunk disjointness, the single router-registration line, and the Phase-1→Phase-4 frozen door contract), and (c) stages the release via the house path with a 4-way sha match + `rollback_safe` record — **stopping at staging with NO live flip**. Outcome test: a giter following §4 → §5 → §6 → §9 produces a staged release whose binary carries the family (F24), the door (`/api/tool-usage/*`), and the seeder; whose 4-way sha matches; whose live fence is byte-identical; and whose promote command is recorded, not run.

---

## 2. Scope

### In scope (plan-level only)

- Consolidated ordered release-gate sequence (§4) — each gate cited to its sibling phase section, no invented gates.
- Integration sweep tasks (§5) — disjointness check, router registration check, cross-phase contract re-check, gap flags.
- Staged-release sequence (§6) — version mechanism, commit/tag/push/stage, 4-way sha, `rollback_safe`, hard STOP.
- Zero-live-contact checklist (§7) + house trap notes.
- Promote-time activation disclosure draft (§8) — the lane leader's one-liner.
- Post-stage verification (§9) — staged-binary content proof + journal/rollback records, still ZERO live contact.

### Out of scope (REJECT-grade if touched)

- **Any product file** — all implementation is owned by Phases 1–4 and executed by the giter.
- **The promote flip** — `promote.sh`, the nonce ceremony, `current` symlink, `state.json` promote fields, live daemon restart. Emperor-owned (§3.2).
- **Re-baselining the FE threshold** — `bundle-baseline.json` (B4b) is **re-baselined to 188 000 B by leader ruling LR1 (decisions D12), with the phase-4 ownership grant at decisions D22**; landed as a phase-4 task and limited to EXACTLY two artifacts — the `threshold_B4b_route_fetch_set_lazy_only_bytes` field and the spec THRESHOLD pin (`bundle-baseline.json` is frozen except those two, per LR1/D22); no further re-baseline is permitted beyond the ruled value; a breach beyond 188,000 is escalated (phase4-plan.md §2/§4.2/§7/§8 gate 7).  *(review fix U1, U15)*
- **Re-cutting a sibling phase plan** — gaps are FLAGGED (§5.4), not silently patched.
- **Any live DB / live daemon operation** before staging (§7).

---

## 3. FROZEN CONTRACTS (invariants — freeze THESE, never counts)

### 3.1 The gate sequence itself (FROZEN, non-reorderable)

The order in §4 is an invariant: unit suites → family/adjudication packs → migration additive-only → FE build+bundle → digest/seeder determinism → full-worktree sweep → commit set. Rationale: a later gate can mask an earlier failure (e.g. a full sweep can hide a single-phase count drift; a staged binary can hide an un-applied migration). **Node counts, family numbers, and measured byte totals are NOT frozen here** — they are read from the sibling plans at execution time.

### 3.2 The NO-LIVE-FLIP boundary (FROZEN)

- `promote.sh` is NEVER executed by this lane. `ENSEMBLE_UPGRADE_LIVE=1` is NEVER bound to `promote.sh`. The Emperor's 3-factor nonce ceremony owns the flip.
- The `current` symlink and `state.json` promote fields stay byte-identical pre/mid/post staging (live fence §9).
- Staging writes ONLY into the live install's `releases/<version>/` directory (the house `stage.sh` pattern, stage.sh:5/:458-459) — that is staging, not a flip.

### 3.3 Consumed frozen contracts (from siblings — re-check, never adapt silently)

| Contract | Source | Consumed by |
|---|---|---|
| Ledger `tool_usage_daily` — PK `(day, project_id, tool_name, agent_id)`, NAMES ONLY, `agent_id='unknown'`, `call_count BIGINT`, project sentinel `"__none__"` (D16 SUPERSEDES D11's `""`) | phase1-plan.md §3.1; D1/D16 | Phase 3 rollup, Phase 5 I3 |
| Sink signature `(tool_name, tool_category?, agent_id, project_id, ok: bool)` — no args slot | phase1-plan.md §3.2; D3 | Phase 2 F24, Phase 5 I3 |
| Door `/api/tool-usage/{summary,daily}` shapes + auth posture (503/403/404/400 vocabulary) | phase1-plan.md §3.4; D6 | Phase 4 tab, Phase 5 I3 |
| F24 family: registrar-only, closed labels, band 134≤160, `VOCABULARY_VERSION==11` | phase2-plan.md §3; D8 | Phase 5 G2/G6 |
| B4b lazy-only fetch set ≤ **188,000 B (re-baselined by leader ruling LR1 — decisions D12; was ≤ 185,000 B)**; `bundle-baseline.json` is **frozen except the two ruled artifacts per LR1/D22** (the `threshold_B4b_route_fetch_set_lazy_only_bytes` field + the spec threshold pin — nothing else) | phase4-plan.md §4.2; D12 (LR1) + D22 (ownership grant) | Phase 5 G4 *(review fix U1, U15)* |

**Any drift in these between the sibling plan and the shipped bytes = contract-break flag (§5.4), never a silent adapt.**
  Amended by architect pass @ fd254b335 — consumed-contracts B4b re-baselined (LEADER RULING R-08) + I1 re-spec (§9) + GAP-4 three-row update

### 3.4 ✅ RESOLVED — OQ1 stage.sh reading: CONFIRMED (leader ruling, architect §12 / LEADER RULING LR2)

**Resolution (leader ruling, recorded verbatim):** "OQ1 stage.sh reading: CONFIRMED — staging uses house stage.sh; ENSEMBLE_UPGRADE_LIVE=1 there is the live-dir write guard, not a flip; promote.sh never invoked; phase5 no-flip invariants (state.json untouched, livez uptime continuous, no service restart) stay as gates."

The commission's "NO `ENSEMBLE_UPGRADE_LIVE=1`" is read as "do not bind it to `promote.sh`" (no flip). The §6 primary recipe uses the house `stage.sh live` form (which binds it as a dir-write guard). The flip boundary in §3.2 holds. A1 assumption note updated accordingly (no longer "pending OQ1").
  Amended by architect pass @ fd254b335 — OQ1 resolved (§12 / LEADER RULING LR2)

---

## 4. Consolidated ordered release-gate sequence (the giter's checklist)

Run in this order. Each gate cites its sibling source; **no gate is invented**, and every "pass" is a GREEN/exact condition.

| # | Gate | Exact check / command (dev-fenced worktree) | Source | Pass criterion |
|---|---|---|---|---|
| G1 | **Unit suites, per phase** | Phase 1: `tests/unit/repositories/test_tool_usage_repository.py`, `tests/unit/services/test_tool_usage_observation.py`, `tests/unit/routers/test_tool_usage_router.py`, `tests/unit/migrations/test_tool_usage_migration.py`. **Phase-1 list grows (architect §10.5 mutation-pinned pins):** dual-dialect upsert pin (two same-PK deltas → one row summed, PG + SQLite — A-6); `MANAGED_KEYS` registration-shape pin (field + dotted path per A-7 option (a): `LimitsConfig.tool_usage_collection_enabled` + dotted `config_path`); auth replication-invariant pin (new router's `_require_auth_on` executable body byte-matches the metrics door's, or shared helper); **1 MB response-size guard** (`/summary`); **top-50 agents cap** per tool. Phase 2: `tests/unit/metrics/test_metrics_family_adjudication.py`. Phase 3: digest unit + determinism + tool-registration + seed idempotency + GC-safety tests. **Phase-3 list grows (architect §10.5):** uuid5 idempotency (seed twice → one row); adapter config shape carries `schedule=`; startable status (compensated create-path); opt-out no-op (D-5 flag OFF); digest file-write determinism with windows D−1 / [D−7,D−1] (byte-identical modulo generated-at header). Phase 4: `tabs.manifest.spec.ts`, `metrics.tool-usage.spec.ts`, `tool-usage.service.spec.ts`, `tool-usage.models.spec.ts`, `metrics.bundle-guard.spec.ts`. | phase1 §4 T9/§7; phase2 §7 S5/§8; phase3 §4 T7/§7; phase4 §8 gates 1–3 | All GREEN; `POSTGRES_*` scrubbed per variable; zero live contact |
| G2 | **Family pack + adjudication counts** | `test/packs/tool_usage_family_unit_test.sh` (registered in `.agents/tester/PACKS.md`) + the F24 assertions in `test_metrics_family_adjudication.py`. | phase2 §4 T5–T7/§7 S1–S7 | `len(verdicts)==29`; binding subset 19-of-29; F24 band 134≤160; global ≤2000; `VOCABULARY_VERSION==11`; AM-2/AM-3 mutations RED; pack GREEN |
| G3 | **Migration additive-only** | (a) throwaway SQLite: parse + apply the new `<YYYYMMDD_HHMMSS>_create_tool_usage_daily.sql`, assert no DROP/ALTER on existing tables, 3-site index-name/column lockstep vs model `__table_args__`; (b) PG path: grep the model import lands before `SQLModel.metadata.create_all` (manager.py:1006-1020 vs :1072), confirm `runner.py` SQLite-only early-return (:837-842), confirm NO `_ensure_postgres_columns` mirror for the brand-new table. | phase1 §4 T2/T3, §5 AC1/AC7; D5; phase3-plan.md source-gc precedent | Table materializes on SQLite (migration) + PG (create_all); zero DROP/mutate; rollback-safe = old binary refs no new table |
| G4 | **FE production build + bundle re-measure + guard + AOT** | Exact sequence (nest-anomaly-report/phase4-plan.md:262-268): `cd frontend && npm ci` (if node_modules absent) → `cd frontend && ./node_modules/.bin/ng build --configuration production` → `node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser` → `node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js <the dist dir>`. Then AOT discipline: `cd frontend && ./node_modules/.bin/ng build --configuration development`. **ORDERING NOTE (architect):** the phase-4 B4b threshold amendment (bundle-baseline.json 185,000 → 188,000 + the `metrics.bundle-guard.spec.ts` THRESHOLD pin + tester re-measure) MUST land before G4 runs — it is a phase-4 task owned by leader ruling LR1 (decisions D12) with the ownership grant at decisions D22. Only those two ruled artifacts move; every other baseline field stays frozen (U1/U15). | phase4 §Task 6 (re-measure), §8 gates 4–5; D12 (LR1) + D22 (ownership grant) | B4b ≤ 188 000 B (re-baselined by LR1 — decisions D12; ownership grant decisions D22; expected measured 185,800–186,200 B); measured numbers recorded against the frozen MEASUREMENT baseline (165,185 B) and verified against the re-baselined THRESHOLD, never overwriting either; guard exit 0; AOT pass; `package.json` untouched; component-style warning SET unchanged (9 entries) |
| G5 | **Digest determinism + seeder idempotency** | Fixed ledger fixture + date → two digest runs byte-identical modulo generated-at header; seed twice → exactly one `source_configs` row; enabled/autostart/GC-safe; boot ordering (seed before `start_all()`); weight join + 230 fallback. | phase3 §4 T1/T5/T7, §7 S1/S5–S7 | All GREEN; no live-registry/clock/random read in rollup; dev-fenced |
| G6 | **Full-worktree test sweep** | `pytest -q` over the explicit unit file/dir lists for all four phases (pass explicit paths, never a raw dir that falls back to `testpaths`); deselect integration/postgres/slow/conformance per house addopts; isolate known shard polluter (`test_migration_e2e_comprehensive.py`) in its own process. | house `[PYTEST]`/blueprint; phase1–4 test strategies | Zero RED across the merged worktree; no new failure vs true base; live-shaped DB fence stays dev |
| G7 | **Commit set** | Giter sweeps + commits (this phase writes no code): plan files under `docs(<scope>):` / `docs(planning):`; code under house types (`feat`/`fix`/`chore`). Verify `git diff --stat` scope per phase (phase1 §7, phase2 §11, phase3 §12, phase4 §8 gate 7) BEFORE each phase commit. | phase1 §7; phase2 §11; phase3 §12; phase4 §8-7; `[MERGE]` | Each commit's file set matches its phase's declared ownership — note phase 4's declared set INCLUDES exactly the two LR1/D22 ruled artifacts (`bundle-baseline.json` threshold field + the spec threshold pin), so their presence is expected, not a sweep *(review fix U1, U15)*; no cross-phase sweep of foreign files |

**Ordering / coupling:** G3 must precede G6 (a migration defect surfaced in a full sweep is harder to attribute); G4 is independent of G1–G3/G5 (FE-only) but must precede G7 (bundle evidence committed); G2's count pins are a precondition of G6's clean sweep. **Exit of G7 = all gates GREEN and the lane fully committed** → proceed to §5 integration sweep, then §6 staging.

---

## 5. Integration sweep tasks (plan-level, not code)

### 5.1 I1 — manager.py collision-proof spec (RE-SPEC on corrected evidence; 5-step proof)

**Premise correction (W4, git-verified) — FIRST, before any collision claim:** `feature/skill-system-defects` is **NOT unmerged** — `868c6c616f` is an ancestor of base (merged via `17e1100532`); it touches only `skill_evolution_service.py` + its test, **not manager.py**. Its branch-span manager.py hunks (`f3ac558d0..1834f5c11`) sit at ~4815–4828 and ~7986–8067 — **zero overlap** with the three planned hunks (h1 ~1006–1020, h2 ~1145–1148, h3 ~1166+). No file-level collision with the plan's set either (graph.py, settings_service.py, routers/__init__.py, tools/instance.py: zero). **NEVER cite the old census as the proof — it rots.**

The three planned hunks (for the enumeration, not line numbers):
- h1 — Phase 1 (T3): model registration before `create_all` — `from .repositories.tool_usage.models import ToolUsageDaily  # noqa: F401` (manager.py ~1006–1020; create_all at :1072).
- h2 — Phase 1 (T6): flusher startup + kill-switch wiring (manager.py ~1145–1148 region, after `_ensure_postgres_columns`, before the memoir persona seed).
- h3 — Phase 3 (T6): seeder call — import + `seed_tool_usage_digest_schedule(...)` (manager.py ~1164–1166, immediately after `seed_persona_if_absent(self._engine)`); h3 may land ≥ :2113 per the architect's D-1 ordering fix (the ENUMERATION, not the line number, is the claim).

**The I1 proof owed at gate time (freeze into phase 5 — 5 steps):**
1. **Enumerate our hunks exactly:** `git diff <base>..HEAD -- daemon/manager.py` → assert EXACTLY three hunks with their `@@` ranges, mapped to h1/h2/h3 (h1 model registration before create_all; h2 flusher startup + kill-switch wiring; h3 seeder call — h3 may land ≥ :2113 per D-1).
2. **Internal disjointness:** assert the three hunks are mutually disjoint (region enumeration — trivial but explicit).
3. **Gate-time drift re-check (the real risk):** list every branch merged into `origin/latest` AFTER base that touches `daemon/manager.py`; for each, diff its hunk ranges against ours — assert no overlap. Re-run at execution time; never cite this document's census (anchors drift).
4. **File-set sweep:** repeat for the plan's other shared files — `daemon/graph.py`, `daemon/services/settings_service.py`, `daemon/config.py`, `daemon/routers/__init__.py`, `daemon/tools/instance.py`, `daemon/tools/_tool_registry.py` — assert zero foreign overlap or disclose.
5. **Upstream freshness:** verify base == origin/latest at merge time (house FINALIZE rule: moved ⇒ STOP).

**Acceptance:** all five steps pass; `git diff daemon/manager.py` shows three hunks whose line ranges do not overlap; no hunk edits another's lines. **Flag as contract-break** if any hunk overlaps or any foreign overlap is found (phase1 R5, phase3 R7 both request this check but neither owns it — Phase 5 I1 owns it). Use the architect doc's wording for the I1/D-rule sentences so ledger and body match character-for-character.
  Amended by architect pass @ fd254b335 — I1 re-spec (§9) + file-set sweep expansion (config.py, _tool_registry.py)

### 5.2 I2 — `daemon/routers/__init__.py` single-line registration

Phase 1 T8 owns the ONE registration line (`from .tool_usage import router as tool_usage_router  # /api/tool-usage ...`) + the `"tool_usage_router"` entry in `__all__`. Precedent: `nest_reports` at `routers/__init__.py:36` / `__all__` at :73. **Acceptance:** exactly one import line + one `__all__` entry; no other phase touched this file; no duplicate registration.

### 5.3 I3 — cross-phase contract re-check (Phase 4 consumes Phase 1's frozen door)

Re-read phase4-plan.md §4.1 against phase1-plan.md §3.4 field-by-field: `summary` shape (`generated_at`, `rows[{tool_name, tool_category, calls_24h, calls_7d, calls_total, agents[{agent_id, calls_7d}]}]`), `daily` shape (`series[{day, calls}]`), auth posture (503 `AUTH_NOT_CONFIGURED`, scopes `maintenance:read|metrics:read`, 403/400/404 vocabulary). Also confirm the FE `calls_7d` ranking key and `agents[]` breakdown inputs are still present. **Acceptance:** byte-for-byte field-name agreement; **any drift = contract-break flag (§5.4), never a silent FE adapt** (phase4 OQ1).

### 5.4 I4 — gap flags (documented, not patched)

| # | Gap | Evidence | Disposition |
|---|---|---|---|
| GAP-1 | **Phase 4 has no section literally titled "Phase exit gate"** — it has §7 Success criteria + §8 Verification gates. Phases 1–3 do have explicit exit-gate sections (phase1 §7, phase2 §11, phase3 §12). | phase4-plan.md §7/§8 vs phase1 §7 / phase2 §11 / phase3 §12 | Use phase4 §8 as its functional exit gate for G1/G4; flag to planner that the sibling set is inconsistent. Do NOT edit phase4-plan.md. |
| GAP-2 | `ENSEMBLE_UPGRADE_LIVE=1` stage-vs-flip semantic split | §3.4 | **RESOLVED** (leader ruling, §3.4): house `stage.sh live` form accepted; `ENSEMBLE_UPGRADE_LIVE=1` is the live-dir write guard, not a flip; promote.sh never runs. No longer an open question. |
| GAP-3 | `VOCABULARY_VERSION` must stay 11 — Phase 2 T2 has a conditional bump exception; Phase 3 §3.7 reuses `"scheduling"` so no new category is expected. | phase2 §3.4/T2; phase3 §3.7 | G2/G6 assert ==11; a bump is a contract-break flag |
| GAP-4 | `settings_service.py` MANAGED_KEYS — Phase 1 T7 adds `limits.tool_usage_collection_enabled` **WITH a backing `LimitsConfig` field per A-7 option (a)** (costs one `daemon/config.py` edit — add it to phase 1's file ownership); Phase 2 R2 deliberately does NOT add `limits.metrics_tool_usage_enabled` (env-only v1, `config_path=None`). **PLUS the D-5 key `limits.tool_usage_digest_seeding_enabled` (`config_path=None`)** lands in phase 1's settings edit. THREE rows total: (1) `limits.tool_usage_collection_enabled` with `LimitsConfig.tool_usage_collection_enabled` + dotted `config_path` (A-7 option (a)); (2) `limits.metrics_tool_usage_enabled` with `config_path=None` (env-only); (3) `limits.tool_usage_digest_seeding_enabled` with `config_path=None` (opt-out that survives re-seed). Confirm NO collision across the three rows in the gate (G1 MANAGED_KEYS registration-shape pin). | phase1 §3.6/T7 + A-7; phase2 §9 note 2; phase3 D-5 | Confirm no collision; Phase 2's key stays env-only; all three rows land in phase 1's settings edit |

---

## 6. Staged release (core tail)

### 6.1 Version mechanism (do NOT hard-pin a number)

The version is assigned **at staging time by the lane leader** per house slot discipline. Mechanism (prompt-assent-channels/ceremony-notes.md:198, architecture-recommendation.md:191): verify the next free slot is empty locally AND on origin (`git ls-remote --tags origin 'vX.Y.Z*'` → empty; `git tag --list vX.Y.Z` → empty), re-verify immediately before `git tag -a` (concurrent lanes converge on adjacent slots), on collision re-bump to the next free patch slot and record it, **never re-cut an existing tag**, push by name never `--tags`, never delete a remote tag. `stage.sh:80-89/:199-205` requires an explicit v-prefixed `VERSION` matching the exact tag at HEAD (`git describe --tags --exact-match`) or exit 78 (ADR-009 D3).

### 6.2 Exact giter staging sequence (house path — re-grepped in this worktree)

Commands below are the giter's (this phase executes none of them). Pattern: aegis-security-family/phase6-plan.md:90-145 + skill-system-revival/phase1-plan.md:893-896 + ceremony-notes.md:198-199.

```bash
# (0) all gates §4 GREEN + integration sweep §5 clean; version slot verified free (§6.1)

# (1) commit set (planner sweeps + commits; house types per G7)
#     plan files: docs(<scope>):  |  code: feat/fix/chore
#     scripts/bump_version.py is used as a FILE EDITOR only — commit subject
#     "chore(release): bump version to <v>", NOT its default "! New version X"
#     (ceremony-notes.md trap 2)

# (2) 4-file version bump — EXACTLY pyproject.toml daemon/__init__.py
#     tests/test_api.py uv.lock (aegis phase6:90-94)

# (3) annotated tag + push HEAD:latest first, then tag BY NAME
git tag -a vX.Y.Z -m "<rollback adjudication + NO-PROMOTE disclosure>" \
  && git push origin HEAD:latest \
  && git push origin vX.Y.Z

# (4) detached STAGING worktree AT THE TAG (a stage from the lane worktree
#     fails 3 exit-78 guards: exact-tag stage.sh:204-205, tip-identity, git_head)
git worktree add --detach /home/nea/ensemble-src-wt-stage-<slug> vX.Y.Z

# (5) FE build in the staging worktree (stage.sh:354 refuses a UI-less release).
#     npm ci runs in frontend/ — the REPO ROOT has NO package.json.
cd /home/nea/ensemble-src-wt-stage-<slug>/frontend && npm ci \
  && cd .. && bash scripts/upgrade/_build_frontend.sh

# (6) live fence SNAPSHOT before stage (read-only): state.json whole-file sha,
#     current symlink, promote fields, .env sha, livez projection, PID — to files

# (7) STAGE — env-first; `live` TARGET arg MANDATORY (bare resolves to demo,
#     stage.sh:133); ENSEMBLE_ROLLBACK_SAFE=1 in argv (destructive-DDL grep
#     stage.sh:336-346 matches 96/119 files ⇒ exit 78 without it).
#     ENSEMBLE_UPGRADE_LIVE=1 here = live-dir WRITE guard, NOT a flip (§3.4).
ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 \
  bash scripts/upgrade/stage.sh live --version vX.Y.Z
#     promote.sh NEVER runs. current symlink + state.json promote fields UNTOUCHED.

# (8) STOP. Record the promote command for the Emperor's ceremony; DO NOT run it.
```

### 6.3 4-way sha verification (RE-HASH, never trust the sidecar)

Assert agreement across: `releases/<v>/ensemble-prod` (live install dir) == staging worktree `dist/ensemble-prod` == `manifest.json:binary_sha256` == build-time provenance sidecar `artifact_sha256`. The manifest sha is computed FROM THE COPY (stage.sh:437), so it is NOT an independent witness — the **sidecar hashed the source before the copy**; the giter re-hashes the staged copy independently (skill-system-revival F-20, phase1-plan.md:896; aegis phase6:43). Both sidecars report `git_dirty=false`. The FE sidecar is stripped from the payload by design (stage.sh:492-498); read it from the source tree only.

### 6.4 `rollback_safe` semantics

`rollback_safe=true source=explicit` via the `ENSEMBLE_ROLLBACK_SAFE=1` env bind (stage.sh:106-110 derive-path is UNUSABLE on this legacy tree — the whole-history DDL grep matches ⇒ exit 78 unset). This lane authors ONE additive migration (new table only, no DROP/ALTER on existing tables) ⇒ rollback consequence = FIX-LOSS only, old binary references no new table (phase1 D5). Record the adjudication in the tag annotation + stage journal: "zero destructive DDL authored; additive table only; `rollback_safe=true source=explicit`."

### 6.5 Hard STOP at staging

After §6.3–§6.4 pass: **STOP.** No `promote.sh`, no nonce, no `current` flip, no live restart. The Emperor's 3-factor ceremony owns the flip. The promote command is recorded in the lane leader's report as a one-liner, explicitly NOT executed.

---

## 7. Zero-live-contact checklist (pre-staging)

- [ ] Dev-fenced execution only: `POSTGRES_DB=ensemble_dev @127.0.0.1:5432`, `POSTGRES_USER`/`PASSWORD`/`HOST`/`PORT` pinned explicitly, `DAEMON_PORT=8079` (giter fence template agents/giter/workflow.md:82-94; lane claim KV `env_fence`). Verify `grep -E '^POSTGRES_DB=|^DAEMON_PORT=' .env` and assert `POSTGRES_DB != ensemble_prod`.
- [ ] Scrub ambient PG vars **per variable** — `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT` — never `env -u POSTGRES_*` (silent no-op; critical note). Pin `ENSEMBLE_SELF_ENV=dev`.
- [ ] No live DB contact; no live daemon contact; all tests on dev/throwaway databases.
- [ ] No operations against `/home/nea/agents-ensemble` before staging (the house `stage.sh` releases-dir write in §6.2 step 7 is the FIRST permitted touch).
- [ ] **Trap — live-shaped paths:** test scripts must never resolve live-shaped HOME-derived install dirs; fence with `mktemp` FAKE_HOME + escape guard (two live-install deletions 2026-10-12 precedent).
- [ ] **Trap — frozen-binary boot smokes:** from daemon-child shells, pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly — `env -u` on it strips the guard and the sweep KILLS live processes (2026-10-31 precedent).
- [ ] **Trap — TypeScript:** only via `frontend/`'s local devDependency — `cd frontend && ./node_modules/.bin/ng build ...`; NEVER bare `tsc`/`npx tsc` outside `frontend/` (2026-10-03 remote-fetch incident).

---

## 8. Promote-time activation disclosure (the lane leader's one-liner — drafted here)

Verify before drafting: the FE tab ships in the release bytes and activates at promote automatically (phase4-plan.md — the tab is a static manifest entry + host, no runtime switch). The daily-08:00 schedule row activates at promote via the Phase-3 boot-time seeder (`manager.py:1164-1166`, `seed_tool_usage_digest_schedule`, enabled+autostart, `start_all()` at manager.py:16945) — automatic, no manual arm (phase3-plan.md §3.10/§12, D10). Fallback one-line arm step (architect-override case) is documented in phase3-plan.md §11 Appendix (`POST /api/schedules` / `task_schedule`).

**Exact disclosure sentence for the lane leader's report:**

> "Both activation surfaces are automatic at promote: the `tool-usage` FE tab ships in the release bytes and renders as soon as the new frontend is served; the daily 08:00 Asia/Ho_Chi_Minh digest schedule is created and started by the Phase-3 boot-time idempotent seeder on the first boot of the promoted binary (no manual arm step). If the architect overrides to a manual arm, the one-line equivalent is the arm step in **phase3-plan.md §11 Appendix, verbatim** — reproduced here in that exact shape: `POST /api/schedules` with `{ "label":"tool-usage-daily-digest", "agent":"ari", "message":"Call tool_usage_digest for the previous day and write the durable digest.", "timezone":"Asia/Ho_Chi_Minh", "recurrence":"daily", "local_time":"08:00", "schedule":"0 8 * * *", "instance_mode":"new_instance", "project_id":<system default project id> }` (or the `task_schedule` tool). The `schedule` cron field is load-bearing: the scheduler adapter routes on `config["schedule"]` (D-3 / D19), so a payload carrying only `when` never fires."  *(review fix U4)*

---

## 9. Post-stage verification (still ZERO live contact)

The giter verifies AFTER staging (no promote, no live daemon contact):

1. **Staged-binary content proof** — module symbols/behaviors present in the staged binary for: F24 family (`w5.py` registrar + `F24` verdict), the door (`tool_usage_router` / `/api/tool-usage/*`), and the seeder (`seed_tool_usage_digest_schedule` / `tool_usage_digest`). PYZ compression blocks literal grep — use module-symbol + behavior checks, not raw string grep (staged-verification precedent). Confirm the family/door/seeder are in the staged artifact, not just in the tree.
2. **4-way sha** (§6.3) matches; both sidecars `git_dirty=false`; payload source-less; prompt-artifact baseline clean in the payload.
3. **Migration census** — the new `.sql` is embedded in the staged tree; the delta vs the live release is exactly the one additive file; `rollback_safe=true source=explicit` recorded in manifest + journal.
4. **Live fence byte-identical** — re-hash `state.json`, `current` symlink, promote fields, `.env` sha vs the §6.2 step-6 snapshot; livez projection + PID unchanged; `live current` still the previous version.
5. **Journal record** — stage-journal entry records the live-dir write + rollback adjudication; promote command recorded, NOT executed.
6. **Staging worktree + both sidecars KEPT** until the ceremony (the release dir keeps no sidecar copy — FE sidecar stripped at stage.sh:492-498; the staging worktree is the only sidecar location).

---

## 10. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | A later gate (G6 full sweep) masks an earlier single-phase count drift (G2) | High | Medium | Non-reorderable sequence (§3.1); each phase's file-scope diff checked at G7 |
| R2 | manager.py three hunks overlap → one phase clobbers another's lines | High | Medium | I1 textual disjointness proof before commit (phase1 R5, phase3 R7) |
| R3 | Phase-4 FE adapted silently to a changed door shape | High | Low | I3 field-by-field re-check; drift = contract-break flag (phase4 OQ1) |
| R4 | `ENSEMBLE_UPGRADE_LIVE=1` misread → accidental flip at stage | Critical | Low | §3.2/§3.4 boundary; `promote.sh` never invoked; live fence §9-4; OQ1 adjudication |
| R5 | Version slot collision from concurrent lanes | Medium | Medium | §6.1 double-verify, re-bump not re-cut, push by name |
| R6 | 4-way sha trusted from sidecar → copy corruption missed | High | Low | §6.3 independent re-hash (F-20) |
| R7 | `rollback_safe` unset → stage.sh exit 78 | Medium | High (default) | Bind `ENSEMBLE_ROLLBACK_SAFE=1` in argv (§6.2 step 7, ceremony-notes trap 1) |
| R8 | Live-shaped path / sweep-guard / tsc trap causes live damage | Critical | Low | §7 checklist traps |
| R9 | Phase-4 missing literal exit-gate section → a gate is skipped | Medium | Low | GAP-1: use phase4 §8 as functional gate; planner notified |

---

## 11. Success criteria

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| S1 | Consolidated sequence complete | G1–G7 checklist | All GREEN, in order |
| S2 | Integration invariants hold | I1 disjoint hunks; I2 single registration; I3 door re-check | Exact / byte-for-byte |
| S3 | Migration additive-only | G3 throwaway SQLite + PG register-before-create_all | Zero DROP/mutate; table on both drivers |
| S4 | FE bundle within bound | G4 re-measure + guard | B4b ≤ 188 000 B (re-baselined by LR1 — decisions D12; ownership grant D22); AOT pass *(review fix U1, U15)* |
| S5 | Digest + seeder deterministic | G5 | Byte-identical modulo header; seed twice → one row |
| S6 | Staged release verified | §6.3 4-way sha + §9 content proof | 4-way match; family/door/seeder in staged binary |
| S7 | Live untouched | §9-4 fence | Byte-identical state.json/symlink/promote fields; no flip |
| S8 | No live contact | §7 checklist | Zero live DB/daemon ops before staging |
| S9 | Promote recorded-not-run | §6.5 + §8 disclosure | One-liner in report; `promote.sh` never executed |

---

## 12. Phase exit gate & open questions

Phase 5 is DONE when: (1) G1–G7 all GREEN in order; (2) I1–I3 integration checks pass and I4 gaps are flagged to the planner; (3) the release is staged via §6 with a 4-way sha match + `rollback_safe=true source=explicit`; (4) §9 post-stage verification passes with the live fence byte-identical; (5) the promote-time disclosure (§8) is delivered and the promote command is recorded, NOT run; (6) ZERO product-file edits and ZERO sibling-plan edits — this phase wrote ONLY `phase5-plan.md`; (7) OQ1 adjudicated by the lane leader (RESOLVED — see §3.4: house `stage.sh live` form accepted, `ENSEMBLE_UPGRADE_LIVE=1` is the live-dir write guard, not a flip).

**Open questions:** none remaining — OQ1 resolved (§3.4, leader ruling). (Previously OQ1 was the only open question; it is now recorded as a resolved leader ruling, not an open question.)
  Amended by architect pass @ fd254b335 — OQ1 resolved (§3.4 / LEADER RULING LR2)

---

## 13. Assumptions (explicit)

- A1: "NO `ENSEMBLE_UPGRADE_LIVE=1`" in the commission means "do not bind it to `promote.sh`"; the house stage-time dir-write guard is **CONFIRMED** by the leader ruling (§3.4, OQ1 resolved) — `ENSEMBLE_UPGRADE_LIVE=1` at stage time is the live-dir write guard, not a flip; promote.sh never runs. No longer pending.
- A2: Version slot is free at stage time (re-verified per §6.1); no number hard-pinned in this plan.
- A3: The lane's dev fence (`ensemble_dev @127.0.0.1:5432`, DAEMON_PORT 8079) is intact in the worktree `.env` (claim KV confirms mode 600 at creation).
- A4: The staged artifact is built from a detached worktree AT the tag (a stage from the lane worktree fails 3 exit-78 guards — aegis phase6 B3).
- A5: The daily-08:00 activation is the boot-time seeder (phase3 default), not the manual arm; the fallback is documented but not the plan.

---

## 14. Evidence citations (file:line, re-grepped in this worktree 2026-11-21)

*(review fix U13)* These anchors are **re-grepped at execution time** everywhere the plan tells the giter to verify (already specified above) — and when re-pinning a drifted anchor, prefer a **SYMBOL anchor over a line number** (lines drift, symbols don't). No anchor is re-pinned by this fold.

- `scripts/upgrade/promote.sh:40-44/:89/:132-143` — live flip requires `ENSEMBLE_UPGRADE_LIVE=1` AND `--f2-verified-closed`.
- `scripts/upgrade/stage.sh:78` (NO FLIP), `:80-89/:199-205` (VERSION discipline/exact-tag), `:106-110` (rollback_safe derive), `:133` (bare→demo), `:190` (require_live_guard), `:204-205` (exact-tag guard), `:336-346` (DDL grep), `:354` (UI-less refuse), `:437` (manifest sha from copy), `:458-459` (releases dir), `:492-498` (FE sidecar stripped).
- `daemon/migrations/runner.py:837-842` — SQLite-only early return (PG = `_ensure_postgres_columns`).
- `daemon/manager.py:1006-1020` (model registration), `:1072` (create_all), `:1075-1077` (migration runner), `:1143-1166` (boot block + memoir persona seed at :1164-1166 = Phase-3 hunk site).
- `daemon/routers/__init__.py:36` (nest_reports registration precedent), `:73` (`__all__`).
- `daemon/services/settings_service.py:253` (MANAGED_KEYS), `:518` (metrics kill-switch precedent).
- `.agents/shared/planning/nest-anomaly-report/phase4-plan.md:262-268` — FE re-measure command sequence.
- `.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.json:43` — `threshold_B4b_route_fetch_set_lazy_only_bytes = 185000` (**PRE-AMENDMENT / gate-verified STARTING point**, re-verified in this worktree at the lane base; phase 4's ruled task moves this ONE field to 188,000 per LR1 / decisions D12 + D22, and `baseline_metrics_route_fetch_set_lazy_only_bytes` = 165,185 at `:48` stays frozen).  *(review fix U1)*
- `frontend/src/app/pages/metrics/metrics.bundle-guard.spec.ts:130-134` — `toBe(185000)` / `toBe(165185)` pins (**PRE-AMENDMENT** as verified; phase 4's ruled task moves the THRESHOLD pin only — `toBe(185000)` → `toBe(188000)` — leaving `toBe(165185)` untouched). **G4 therefore verifies `≤ 188,000` POST-amendment**, not the 185,000 pre-amendment value.  *(review fix U1)*
- `agents/giter/workflow.md:82-94` — worktree env fence (POSTGRES_DB=ensemble_dev at :88, DAEMON_PORT=8079 at :91).
- Sibling plans: `phase1-plan.md` §3.1/§3.2/§3.4/§4 T2-T3/T6/T8/§7; `phase2-plan.md` §3/§4 T5-T7/§7/§11; `phase3-plan.md` §3.10/§4 T6/§11 Appendix/§12; `phase4-plan.md` §4.1/§4.2/§Task 6/§7/§8; `decisions.md` D1–D22.  *(review fix U15)*
- Staging precedents: `.agents/shared/planning/aegis-security-family/phase6-plan.md:41/:43/:90-145`; `.agents/shared/planning/skill-system-revival/phase1-plan.md:893-896` (F-20 4-way independence); `.agents/shared/planning/prompt-assent-channels/ceremony-notes.md:198-199` (slot/tag traps); `.agents/shared/planning/self-service-promote/architecture-recommendation.md:464` (design-only contrast: no stage/promote).

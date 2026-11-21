# Phase 5: Verification Consolidation + Staged-Release Tail

Lane: **tool-usage-metrics-p1** (commission ENSEMBLE-37 / TokenEconomizer adoption — Phase 1 measurement slice)
Phase owner: plan-worker (release/verification-tail phase)
Status: Draft for the planner's review
Depends on: **Phases 1–4** — this phase CONSOLIDATES their exit gates and adds the staging tail; it consumes all four sibling plans + `decisions.md` (D1–D11), and owns ZERO product edits.
Worktree (READ-ONLY source of truth for every anchor below): `/home/nea/ensemble-src-wt-tool-metrics-p1` @ `0b5624dc9` (branch `feature/tool-usage-metrics-p1`).
NEVER touch `/home/nea/ensemble-src` (canonical detached checkout) or `/home/nea/agents-ensemble` (live install) except the read-only live fence + the house `stage.sh` releases-dir write described in §6.

---

## 0. Dispatch mode & file ownership (REJECT-grade)

**Mode 1 — Greenfield plan file.** `.agents/shared/planning/tool-usage-metrics-p1/phase5-plan.md` did not exist before this run; this file is the release-tail artifact. No committed plan set is being revised.

**Single-ownership invariant (REJECT-grade).** This phase owns EXACTLY ONE write:

| Owned file | Edit |
|---|---|
| `.agents/shared/planning/tool-usage-metrics-p1/phase5-plan.md` | NEW — this file |

**Owns ZERO edits to:** every product file (`daemon/**`, `frontend/**`, `tests/**`, `test/**`, `agents/**`), `bundle-baseline.json`, `.agents/tester/PACKS.md`, all four sibling `phaseN-plan.md` files, and `decisions.md`. This phase is PLAN-ONLY. The giter executes the gates and the staging sequence; the planner sweeps + commits. **No git mutation is performed by this phase** (no commit, tag, push, merge, worktree add/remove — those are the giter's, sequenced in §6).

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
- **Re-baselining the FE threshold** — `bundle-baseline.json` (B4b 185 000 B) is INVARIANT; a breach is escalated (phase4-plan.md §4.2, R-08).
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
| Ledger `tool_usage_daily` — PK `(day, project_id, tool_name, agent_id)`, NAMES ONLY, `agent_id='unknown'`, `call_count BIGINT`, project sentinel `""` | phase1-plan.md §3.1; D1/D11 | Phase 3 rollup, Phase 5 I3 |
| Sink signature `(tool_name, tool_category?, agent_id, project_id, ok: bool)` — no args slot | phase1-plan.md §3.2; D3 | Phase 2 F24, Phase 5 I3 |
| Door `/api/tool-usage/{summary,daily}` shapes + auth posture (503/403/404/400 vocabulary) | phase1-plan.md §3.4; D6 | Phase 4 tab, Phase 5 I3 |
| F24 family: registrar-only, closed labels, band 134≤160, `VOCABULARY_VERSION==11` | phase2-plan.md §3; D8 | Phase 5 G2/G6 |
| B4b lazy-only fetch set ≤ 185 000 B; `bundle-baseline.json` INVARIANT | phase4-plan.md §4.2; D9 | Phase 5 G4 |

**Any drift in these between the sibling plan and the shipped bytes = contract-break flag (§5.4), never a silent adapt.**

### 3.4 ⚠ Gap flag — `ENSEMBLE_UPGRADE_LIVE=1` semantic split (needs lane-leader adjudication)

The commission says "NO live flip, NO `ENSEMBLE_UPGRADE_LIVE=1`". The house `stage.sh live` recipe (aegis-security-family/phase6-plan.md:41/:141, skill-system-revival/phase1-plan.md:893) binds `ENSEMBLE_UPGRADE_LIVE=1` **at stage time as the guard for the live-dir `releases/` write — NOT a flip** (stage.sh:190 `require_live_guard`; stage.sh:78 "NO FLIP: staging never touches `current` (that is promote.sh)"). The flip guard is on `promote.sh` (promote.sh:43-44: live requires `ENSEMBLE_UPGRADE_LIVE=1` **AND** `--f2-verified-closed`).

**Resolution presented, not silently chosen:** the commission's "NO `ENSEMBLE_UPGRADE_LIVE=1`" is read as "do not bind it to `promote.sh`" (no flip). The §6 primary recipe uses the house `stage.sh live` form (which binds it as a dir-write guard). **Open question OQ1 (§12): confirm the lane leader accepts the house stage-time guard, OR stage to `sandbox`/`demo` to avoid the guard entirely** (diverges from the "staged release into live `releases/`" precedent and needs its own slot/fence handling). Default = house `stage.sh live` form; the flip boundary in §3.2 holds under either choice.

---

## 4. Consolidated ordered release-gate sequence (the giter's checklist)

Run in this order. Each gate cites its sibling source; **no gate is invented**, and every "pass" is a GREEN/exact condition.

| # | Gate | Exact check / command (dev-fenced worktree) | Source | Pass criterion |
|---|---|---|---|---|
| G1 | **Unit suites, per phase** | Phase 1: `tests/unit/repositories/test_tool_usage_repository.py`, `tests/unit/services/test_tool_usage_observation.py`, `tests/unit/routers/test_tool_usage_router.py`, `tests/unit/migrations/test_tool_usage_migration.py`. Phase 2: `tests/unit/metrics/test_metrics_family_adjudication.py`. Phase 3: digest unit + determinism + tool-registration + seed idempotency + GC-safety tests. Phase 4: `tabs.manifest.spec.ts`, `metrics.tool-usage.spec.ts`, `tool-usage.service.spec.ts`, `tool-usage.models.spec.ts`, `metrics.bundle-guard.spec.ts`. | phase1 §4 T9/§7; phase2 §7 S5/§8; phase3 §4 T7/§7; phase4 §8 gates 1–3 | All GREEN; `POSTGRES_*` scrubbed per variable; zero live contact |
| G2 | **Family pack + adjudication counts** | `test/packs/tool_usage_family_unit_test.sh` (registered in `.agents/tester/PACKS.md`) + the F24 assertions in `test_metrics_family_adjudication.py`. | phase2 §4 T5–T7/§7 S1–S7 | `len(verdicts)==29`; binding subset 19-of-29; F24 band 134≤160; global ≤2000; `VOCABULARY_VERSION==11`; AM-2/AM-3 mutations RED; pack GREEN |
| G3 | **Migration additive-only** | (a) throwaway SQLite: parse + apply the new `<YYYYMMDD_HHMMSS>_create_tool_usage_daily.sql`, assert no DROP/ALTER on existing tables, 3-site index-name/column lockstep vs model `__table_args__`; (b) PG path: grep the model import lands before `SQLModel.metadata.create_all` (manager.py:1006-1020 vs :1072), confirm `runner.py` SQLite-only early-return (:837-842), confirm NO `_ensure_postgres_columns` mirror for the brand-new table. | phase1 §4 T2/T3, §5 AC1/AC7; D5; phase3-plan.md source-gc precedent | Table materializes on SQLite (migration) + PG (create_all); zero DROP/mutate; rollback-safe = old binary refs no new table |
| G4 | **FE production build + bundle re-measure + guard + AOT** | Exact sequence (nest-anomaly-report/phase4-plan.md:262-268): `cd frontend && npm ci` (if node_modules absent) → `cd frontend && ./node_modules/.bin/ng build --configuration production` → `node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser` → `node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js <the dist dir>`. Then AOT discipline: `cd frontend && ./node_modules/.bin/ng build --configuration development`. | phase4 §Task 6 (re-measure), §8 gates 4–5; D9 | B4b ≤ 185 000 B (`bundle-baseline.json:43`, `metrics.bundle-guard.spec.ts:133`); measured numbers recorded AGAINST the frozen baseline, never overwrite; guard exit 0; AOT pass; `package.json` untouched; component-style warning SET unchanged (9 entries) |
| G5 | **Digest determinism + seeder idempotency** | Fixed ledger fixture + date → two digest runs byte-identical modulo generated-at header; seed twice → exactly one `source_configs` row; enabled/autostart/GC-safe; boot ordering (seed before `start_all()`); weight join + 230 fallback. | phase3 §4 T1/T5/T7, §7 S1/S5–S7 | All GREEN; no live-registry/clock/random read in rollup; dev-fenced |
| G6 | **Full-worktree test sweep** | `pytest -q` over the explicit unit file/dir lists for all four phases (pass explicit paths, never a raw dir that falls back to `testpaths`); deselect integration/postgres/slow/conformance per house addopts; isolate known shard polluter (`test_migration_e2e_comprehensive.py`) in its own process. | house `[PYTEST]`/blueprint; phase1–4 test strategies | Zero RED across the merged worktree; no new failure vs true base; live-shaped DB fence stays dev |
| G7 | **Commit set** | Giter sweeps + commits (this phase writes no code): plan files under `docs(<scope>):` / `docs(planning):`; code under house types (`feat`/`fix`/`chore`). Verify `git diff --stat` scope per phase (phase1 §7, phase2 §11, phase3 §12, phase4 §8 gate 7) BEFORE each phase commit. | phase1 §7; phase2 §11; phase3 §12; phase4 §8-7; `[MERGE]` | Each commit's file set matches its phase's declared ownership; no cross-phase sweep of foreign files |

**Ordering / coupling:** G3 must precede G6 (a migration defect surfaced in a full sweep is harder to attribute); G4 is independent of G1–G3/G5 (FE-only) but must precede G7 (bundle evidence committed); G2's count pins are a precondition of G6's clean sweep. **Exit of G7 = all gates GREEN and the lane fully committed** → proceed to §5 integration sweep, then §6 staging.

---

## 5. Integration sweep tasks (plan-level, not code)

### 5.1 I1 — manager.py THREE-hunk disjointness (giter shows textually before commit)

`daemon/manager.py` is written by TWO phases; require the giter to display all three hunks and prove they are line-disjoint:

| Hunk | Owner | Site (verified in this worktree) | Change |
|---|---|---|---|
| 1 | Phase 1 (T3) | late-import block before `SQLModel.metadata.create_all` — model registration region `manager.py:1006-1020` (memoir precedent at :1013-1018); `create_all` at :1072 | `from .repositories.tool_usage.models import ToolUsageDaily  # noqa: F401` |
| 2 | Phase 1 (T6) | boot block after `_ensure_postgres_columns`, before the memoir persona seed — `manager.py:1143-1150` region | flusher task start + ledger sink registration + kill-switch boot log |
| 3 | Phase 3 (T6) | immediately after the memoir persona seed call — `manager.py:1164-1166` (`seed_persona_if_absent(self._engine)` confirmed at :1164-1166) | import + `seed_tool_usage_digest_schedule(...)` call |

**Acceptance:** `git diff daemon/manager.py` shows three hunks whose line ranges do not overlap; hunk 2 precedes hunk 3; no hunk edits another's lines. **Flag as contract-break** if any hunk overlaps (phase1 R5, phase3 R7 both request this check but neither owns it — Phase 5 I1 owns it).

### 5.2 I2 — `daemon/routers/__init__.py` single-line registration

Phase 1 T8 owns the ONE registration line (`from .tool_usage import router as tool_usage_router  # /api/tool-usage ...`) + the `"tool_usage_router"` entry in `__all__`. Precedent: `nest_reports` at `routers/__init__.py:36` / `__all__` at :73. **Acceptance:** exactly one import line + one `__all__` entry; no other phase touched this file; no duplicate registration.

### 5.3 I3 — cross-phase contract re-check (Phase 4 consumes Phase 1's frozen door)

Re-read phase4-plan.md §4.1 against phase1-plan.md §3.4 field-by-field: `summary` shape (`generated_at`, `rows[{tool_name, tool_category, calls_24h, calls_7d, calls_total, agents[{agent_id, calls_7d}]}]`), `daily` shape (`series[{day, calls}]`), auth posture (503 `AUTH_NOT_CONFIGURED`, scopes `maintenance:read|metrics:read`, 403/400/404 vocabulary). Also confirm the FE `calls_7d` ranking key and `agents[]` breakdown inputs are still present. **Acceptance:** byte-for-byte field-name agreement; **any drift = contract-break flag (§5.4), never a silent FE adapt** (phase4 OQ1).

### 5.4 I4 — gap flags (documented, not patched)

| # | Gap | Evidence | Disposition |
|---|---|---|---|
| GAP-1 | **Phase 4 has no section literally titled "Phase exit gate"** — it has §7 Success criteria + §8 Verification gates. Phases 1–3 do have explicit exit-gate sections (phase1 §7, phase2 §11, phase3 §12). | phase4-plan.md §7/§8 vs phase1 §7 / phase2 §11 / phase3 §12 | Use phase4 §8 as its functional exit gate for G1/G4; flag to planner that the sibling set is inconsistent. Do NOT edit phase4-plan.md. |
| GAP-2 | `ENSEMBLE_UPGRADE_LIVE=1` stage-vs-flip semantic split | §3.4 | OQ1 to lane leader; default house stage form |
| GAP-3 | `VOCABULARY_VERSION` must stay 11 — Phase 2 T2 has a conditional bump exception; Phase 3 §3.7 reuses `"scheduling"` so no new category is expected. | phase2 §3.4/T2; phase3 §3.7 | G2/G6 assert ==11; a bump is a contract-break flag |
| GAP-4 | `settings_service.py` MANAGED_KEYS — Phase 1 T7 adds `limits.tool_usage_collection_enabled`; Phase 2 R2 deliberately does NOT add `limits.metrics_tool_usage_enabled` (env-only v1). | phase1 §3.6/T7; phase2 §9 note 2 | Confirm no collision; Phase 2's key stays env-only |

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

# (5) FE build in the staging worktree (stage.sh:354 refuses a UI-less release)
cd /home/nea/ensemble-src-wt-stage-<slug> && npm ci \
  && bash scripts/upgrade/_build_frontend.sh

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

> "Both activation surfaces are automatic at promote: the `tool-usage` FE tab ships in the release bytes and renders as soon as the new frontend is served; the daily 08:00 Asia/Ho_Chi_Minh digest schedule is created and started by the Phase-3 boot-time idempotent seeder on the first boot of the promoted binary (no manual arm step). If the architect overrides to a manual arm, the one-line equivalent is `POST /api/schedules` with `{label:'tool-usage-daily-digest', agent:'ari', timezone:'Asia/Ho_Chi_Minh', recurrence:'daily', when:'08:00', instance_mode:'new_instance', project_id:<system default>}` (phase3-plan.md §11)."

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
| S4 | FE bundle within bound | G4 re-measure + guard | B4b ≤ 185 000 B; AOT pass |
| S5 | Digest + seeder deterministic | G5 | Byte-identical modulo header; seed twice → one row |
| S6 | Staged release verified | §6.3 4-way sha + §9 content proof | 4-way match; family/door/seeder in staged binary |
| S7 | Live untouched | §9-4 fence | Byte-identical state.json/symlink/promote fields; no flip |
| S8 | No live contact | §7 checklist | Zero live DB/daemon ops before staging |
| S9 | Promote recorded-not-run | §6.5 + §8 disclosure | One-liner in report; `promote.sh` never executed |

---

## 12. Phase exit gate

Phase 5 is DONE when: (1) G1–G7 all GREEN in order; (2) I1–I3 integration checks pass and I4 gaps are flagged to the planner; (3) the release is staged via §6 with a 4-way sha match + `rollback_safe=true source=explicit`; (4) §9 post-stage verification passes with the live fence byte-identical; (5) the promote-time disclosure (§8) is delivered and the promote command is recorded, NOT run; (6) ZERO product-file edits and ZERO sibling-plan edits — this phase wrote ONLY `phase5-plan.md`; (7) OQ1 adjudicated by the lane leader. **No commit, no promote, no live flip — the planner sweeps + commits; the Emperor's ceremony owns the flip.**

## Open questions for the orchestrator

- **OQ1 (§3.4, blocking the exact stage argv):** accept the house `stage.sh live` form (binds `ENSEMBLE_UPGRADE_LIVE=1` as the live-dir WRITE guard, NOT a flip — default), or stage to `sandbox`/`demo` to avoid the guard entirely? If sandbox/demo, I need the lane leader's target + slot/fence handling; the flip boundary (§3.2) holds either way.

---

## 13. Assumptions (explicit)

- A1: "NO `ENSEMBLE_UPGRADE_LIVE=1`" in the commission means "do not bind it to `promote.sh`"; the house stage-time dir-write guard is acceptable pending OQ1.
- A2: Version slot is free at stage time (re-verified per §6.1); no number hard-pinned in this plan.
- A3: The lane's dev fence (`ensemble_dev @127.0.0.1:5432`, DAEMON_PORT 8079) is intact in the worktree `.env` (claim KV confirms mode 600 at creation).
- A4: The staged artifact is built from a detached worktree AT the tag (a stage from the lane worktree fails 3 exit-78 guards — aegis phase6 B3).
- A5: The daily-08:00 activation is the boot-time seeder (phase3 default), not the manual arm; the fallback is documented but not the plan.

---

## 14. Evidence citations (file:line, re-grepped in this worktree 2026-11-21)

- `scripts/upgrade/promote.sh:40-44/:89/:132-143` — live flip requires `ENSEMBLE_UPGRADE_LIVE=1` AND `--f2-verified-closed`.
- `scripts/upgrade/stage.sh:78` (NO FLIP), `:80-89/:199-205` (VERSION discipline/exact-tag), `:106-110` (rollback_safe derive), `:133` (bare→demo), `:190` (require_live_guard), `:204-205` (exact-tag guard), `:336-346` (DDL grep), `:354` (UI-less refuse), `:437` (manifest sha from copy), `:458-459` (releases dir), `:492-498` (FE sidecar stripped).
- `daemon/migrations/runner.py:837-842` — SQLite-only early return (PG = `_ensure_postgres_columns`).
- `daemon/manager.py:1006-1020` (model registration), `:1072` (create_all), `:1075-1077` (migration runner), `:1143-1166` (boot block + memoir persona seed at :1164-1166 = Phase-3 hunk site).
- `daemon/routers/__init__.py:36` (nest_reports registration precedent), `:73` (`__all__`).
- `daemon/services/settings_service.py:253` (MANAGED_KEYS), `:518` (metrics kill-switch precedent).
- `.agents/shared/planning/nest-anomaly-report/phase4-plan.md:262-268` — FE re-measure command sequence.
- `.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.json:43` — `threshold_B4b_route_fetch_set_lazy_only_bytes = 185000`.
- `frontend/src/app/pages/metrics/metrics.bundle-guard.spec.ts:130-134` — `toBe(185000)` / `toBe(165185)` pins.
- `agents/giter/workflow.md:82-94` — worktree env fence (POSTGRES_DB=ensemble_dev at :88, DAEMON_PORT=8079 at :91).
- Sibling plans: `phase1-plan.md` §3.1/§3.2/§3.4/§4 T2-T3/T6/T8/§7; `phase2-plan.md` §3/§4 T5-T7/§7/§11; `phase3-plan.md` §3.10/§4 T6/§11 Appendix/§12; `phase4-plan.md` §4.1/§4.2/§Task 6/§7/§8; `decisions.md` D1–D11.
- Staging precedents: `.agents/shared/planning/aegis-security-family/phase6-plan.md:41/:43/:90-145`; `.agents/shared/planning/skill-system-revival/phase1-plan.md:893-896` (F-20 4-way independence); `.agents/shared/planning/prompt-assent-channels/ceremony-notes.md:198-199` (slot/tag traps); `.agents/shared/planning/self-service-promote/architecture-recommendation.md:464` (design-only contrast: no stage/promote).

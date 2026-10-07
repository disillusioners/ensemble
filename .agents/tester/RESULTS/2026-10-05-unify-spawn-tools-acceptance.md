# unify-spawn-tools — FINAL ACCEPTANCE GATE (2026-10-05)

**Verdict: 🟢 PASS for merge** — with ONE required disposition (Phase-D seal re-seal/acceptance, process-class) and pre-merge worktree hygiene notes.

- **Worktree:** `/home/nea/ensemble-src-wt-unify-spawn-tools`, branch `feature/unify-spawn-tools` @ `b822c7ec593e00a021cb0a783bdb4e536d9a6f65` (verified; stack `62c33c40 → 2fa92fa8 → 0dbb9fab → b822c7ec`, cut from `ac399874` = latest)
- **Base attribution worktree:** `/tmp/unify_base_ac399874` (detached @ ac399874, own uv venv, Python 3.14.7 BOTH sides, `daemon.__file__` resolved inside each tree; REMOVED after leg 7b, removal verified)
- **Diff under test:** 30 files, +3,243/−1,778 (`/tmp/unify_recon/diff_files.txt`)
- **Read-only gate:** zero repo code changes/commits/pushes by this commission; all scratch in `/tmp/unify_recon/`
- **Workers:** 10 (recon/bootstrap, static census, functional gate, touched pack, concurrency pack, 8 chunk runners, base-attribution) — full-sweep executed as 78 dual-timeout units

---

## 1. Criterion verdicts (user's list — all evidence-backed)

### C1 — Only spawn_instance exists; hot-start reachable via the meta gate — ✅ PASS
Static (real symbols, /tmp/unify_recon/part_a_static_v2.py): `spawn_hot_instance` NOT in `DYNAMIC_TOOL_NAMES`/`KNOWN_TOOL_NAMES`/`discover_source_only_tool_names()` (0 hits); `spawn_instance` IS in `KNOWN_TOOL_NAMES` (`_tool_registry.py:858`); `create_snapshot_tools` returns EXACTLY 2 (`snapshot_create`, `snapshot_search`); category `instance` = 10 tools incl. `spawn_instance`, hot-tool absent (`instance.py:2422` decorator).
Functional through the REAL `create_instance_tools` factory (fixtures cribbed from `unified_spawn_fixtures`/`send_message_fixtures`; 6/6 passed, 2.56s):
- **S1 gate-ON + snapshot ⇒ WARM** (verbatim): `[snapshot] started: warm — Warm-started from snapshot snap-s1 (age unknown; tags kind:implementation, subsystem:unify-spawn-tools)` + metadata stamp `{"spawned_from_snapshot_id": "snap-s1", …}` + `m.events == ["spawn","metadata","enqueue"]`
- **S2 gate-ON + missing snapshot ⇒ COLD fallback** (verbatim): `[snapshot] started: cold — No matching snapshot (searched: snapshot snap-nonexistent-zzz; reason: verify-failed)`; task still auto-dispatched; no stamp on cold
- **S3 gate-OFF ⇒ LEGACY string, NO `[snapshot]` marker** (verbatim 2-line legacy shape; byte-identity pins `test_pin_a_child_count_single_newline_legacy_shape` L1054-1115 + `test_task_absent_is_legacy_two_step_no_tail` L1256-1276 matched); S3b: canonical `SNAPSHOT_STEERING_IGNORED_LINE` lexeme verbatim
- Cleanup proven: `git status --porcelain` = only pre-existing ` M .agents/tidier/notes.md`

### C2 — snapshot_enabled: true on exactly tester + developer[v2] — ✅ PASS
39 `agents/*/meta.json` (incl. versioned variants): **2 true** (`agents/tester/meta.json`, `agents/developer[v2]/meta.json`), 37 key-absent, 0 false. Full table: `/tmp/unify_recon/static_census.md`.

### C3 — Tests pass — ✅ PASS (0 functional branch-caused reds; 1 process-class finding)
- **Touched pack** (11 files, 526 tests, clean collection required): **526/526 PASS, 0F/0E/0S, 108.6s** (`/tmp/unify_recon/touched.log`). Ambiguity disambiguated: `tests/test_registry.py`, `tests/unit/tools/test_instance_tools.py`.
- **ensure.md Core concurrency pack**: `test/packs/concurrency_atomic_unit_test.sh` **99P/0F/74S, 59.12s** — signature exactly matches last known; no new family.
- **Full default unit suite** (24,586 collected / 1,161 files): executed as **41 chunks → re-split to 78 dual-timeout units** (5-min hard cap honored everywhere; 3 TTQA re-splits: test-count sizing, mega-file node-id halves, throughput-sized attestation units; runner hardened to `mapfile` array expansion after an rc=4 word-split malformation was diagnosed and fixed mask-free — 691/691 collected after fix). **Every file accounted.**
  - Approx. **22,000+ passed**; ~1,000+ marker-deselected by default addopts (incl. the full 346-test postgres family, measured 346 collected/346 deselected/0 runnable — chunks 43+44 = 161+185 COVERED-BY-DESELECTION, per-spec).
  - **All reds classified; ZERO functional branch-caused.** See §2 ledger.
  - Not-runnable-by-environment (documented, not defects): real-LLM SSE wedge hosts (`llm_stream_watchdog.py:246` → `openai/_streaming.py` iter_bytes poll): `bound_escalation` ×1, `idle_orphan_incident` ×3, `live_descendants` (2/42 observable green, cascade-wedged), `incident_acceptance_lca`/`ledger_reset` (2-of-3 wedged), `performance` ×1, `revive_after_escalation` ×1; real-PG collection block `test_message_api_cost.py` ×12; expected env collection-error `tests/e2e/test_context_injection_hybrid.py` (import vs localhost:8079; the ONE error recon predicted). macOS-hardcoded paths (~8 reds: dev.sh `/Users/…`, REPO_ROOT AST scans, Homebrew initdb).
- **Venv integrity:** worktree `.venv` Python 3.14.7, `import daemon` resolves INSIDE the worktree; uv-only (no pip). Note: 3.13-rot did NOT apply here (3.14 venv); `--continue-on-collection-errors` used for full sweep only, never for the touched pack.

### C4 — No dangling spawn_hot_instance references — ✅ PASS
Repo-wide census incl. explicit `.agents/` (coverage PROVEN: control grep + belt-and-braces per-dir passes + diff) and `frontend/`: **85 hits, ALL within allowed classes** — (a) CHANGELOG 6 (incl. new Unreleased entry), (b) `.agents/` dated records 46, (c) jev-system doc 1, (d) 17 daemon rename-noting docstrings + 7 negative-assertion tests (asserting lines quoted: `test_snapshot_tools.py` L1759/1788-1790/1801/1870/1899) + 7 rename-noting test docstrings. **Live-code negative check: zero `def`/`import`/`class SpawnHot*` anywhere.** Full per-hit list: `/tmp/unify_recon/static_census.md`.

### C5 — Auto-dispatch through the merged tool — ✅ PASS
LIVE through the real factory: gate-ON + task ⇒ `m.enqueue_calls[0] = {instance_id="new-inst-1", message="this is the S4 first turn", source="internal_agent:leader-1"}` — task LANDED on the child; R18 tail present in the rendered return (`auto-dispatched as first turn`, `do NOT call send_message again`). Order pins (spawn→metadata→enqueue) re-asserted live AND cited: `test_snapshot_tools.py:1227` (warm), `:1204` (cold), `:1583`, `:1602` — all 4 pass at HEAD. (Task referenced the wrong file for the pins — `test_spawn_instance_input.py` holds only docstring pins P/Q/R; the order pins live in `tests/unit/tools/test_snapshot_tools.py`.)

### C6 — R15 gate unchanged — ✅ PASS
Code-cited: `snapshot_tools.py:458-469` — `snapshot_create` returns `{"disabled": True, "error": "snapshot_create disabled by settings toggle"}` when `get_snapshot_create_enabled()` (settings toggle, fail-closed default OFF, `snapshot_settings_utils.py:44`) is OFF. `instance.py:2672` — spawn path consults ONLY `_target_snapshot_enabled` (`instance.py:799-850`, per-agent meta, fail-closed); **grep proof: zero matches for the R15 toggle anywhere in `instance.py`**; `snapshot_search` docstring "Read-only — NEVER gated by R15" (`snapshot_tools.py:670`). Pins, both cited and green: `test_snapshot_behavior_spot.py:683-705` (gated, exact disabled-shape) + renamed not-gated pins `test_spawn_consumption_never_r15_gated` (`test_snapshot_tools.py:448-473`) and `test_spawn_consumption_not_r15_gated` (`test_snapshot_v3.py:607`) + `test_snapshot_search_not_gated` (`:600`).

### C7 — Frontend copy — ✅ PASS (static, per task)
`frontend/src/app/pages/settings/settings.component.html`: 1 file, 19+/10−, **3 hunks / 5 edit sites** (task wording said "4 sites" — the criterion is absence of `spawn_hot` refs, which holds for the whole file and every hunk). Copy-only: no Angular build/e2e run — static verification sufficient, stated explicitly.

### Additional: ensure.md status
Core #1 (no regressions in changed packs) ✅ (touched + concurrency + scoped sweep, 0 branch-caused); #2/#3 (concurrency/atomic + no sync DB on loop) ✅ (pack PASS); #4 (dev.sh `--timeout-graceful-shutdown 10`) ✅ static, line 142. Release-Gate E2E: NOT run — requires live `./dev.sh` daemon; worktree boot is outside this gate's scope per repo convention; the dev.sh-boot probe and E2E belong to the merge/release gate on `latest`. Quarantine-aware throughout (2 quarantined hits observed = QUARANTINE.md rows 1 & new row, excluded from PASS/FAIL).

---

## 2. The attribution ledger — 277 reds A/B-proven, exactly 1 branch-caused (process-class)

Same-isolated-config A/B at base `ac399874` vs feature `b822c7ec` (Python 3.14.7 both sides; normalized FAILURES/ERRORS-section diffs; artifacts `/tmp/unify_recon/base_attrib{,2..8}.log`, `feature_isolated{,2..8}.log`, `base_attribution_report_leg{,2..7a,7b}.md`):

| Leg | Scope | Reds | Branch-caused |
|---|---|---|---|
| 1 | chunks 3+5 (8 files) | 20 | 0 (+2 pre-existing flakes → QUARANTINE.md new row) |
| 2 | 17 files | 66 | 0 (+1 order/load candidate: worker_notification, 3×PASS isolated) |
| 3 | 13 files (governor/innate/pause/proxy_phase1/etc.) | 38 | 0 |
| 4 | 12 files — **3 branch-surface priorities cleared** (context7/manager.py:747, find_near/manager.py, gaia/registry.py) | 45 | 0 |
| 5 | 9 files + wanderer settle (`git diff` = 0 lines → 'db' grant base-side by construction) | 18 | 0 |
| 6 | 10 files — chokepoint guards PRE, **developer[v2] prompt reds PRE**, pause-cascade PRE | 26 | **1 — Phase-D seal guard** |
| 7a | 9 files — send_message family PRE (vision-Mock fixture variant), coder_migration PRE, builtin_mcp PRE | 55 | 0 |
| 7b | 7 files — **PP1 work_notifier defect verbatim-identical at base** | 9 | 0 |
| **Total** | | **277** | **1 (process-class)** |

**The one branch-caused finding:** `test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts::test_phase_d_does_not_modify_sealed_artifacts` — PASS@base ×3, FAIL@feature ×3, sha256-corroborated: branch edits 3 sealed Phase A/B/C artifacts (`daemon/constants.py` +5, `agents/ari/rule.md` +5, `agents/leader/rule.md` +2 vs baseline @62934ef6). The guard fired CORRECTLY by design. **Not a functional regression. Disposition (required at merge): re-seal ceremony with seal-owner sign-off riding this commission, or explicit leader acceptance of the seal-break.**

Additionally base-confirmed with NO branch delta:
- Documented root-spawn 27 = 18 `test_spawn_team_members` + 9 `test_spawn_limit_edge_cases` EXACTLY (base==feature, Δ=0; the task's other 2 files were always green — the 4-file framing over-attributed, count 27 exact).
- Documented vision-6 completed exactly (5 chunk2 + 1 chunk25); attestation_migration quarantine red + skill_evol_config 2F + enqueue_shared + knowledge_tools explore-caller ×2 all re-confirmed pre-existing.
- PP1 zero-claimed terminal fire (`work_notifier.py:886`) — real defect, EXISTS AT BASE; commission-worthy independently of this branch.

Pre-existing collapse clusters (fix backlog, quantified — see LESSONS/2026-10-05-unify-spawn-tools-acceptance-methods.md §6): service_tool Mock fixture 23E; vision allowed_models 30+; mirror-seam reconciliation 15+; PG-dialect migration on SQLite 27; macOS hardcoded paths ~8.

## 3. Scope decision
Full-suite run WAS warranted: cross-cutting tool-registration refactor (manager/registry/loader/tools/routers/constants = the daemon's tool spine), final pre-merge gate. Executed as 78 units ≤5-min dual-timeout each (per test-pack skill + ensure.md "run via packs" rule) — no bare `pytest tests/` anywhere.

## 4. Hygiene & process notes
- **Worktree dirt (pre-merge owner action):** ` M .agents/tidier/notes.md` (tidier's own pass-log for 0dbb9fab, uncommitted, predates this gate) + `.agents/shared/planning/designer-agent/install-audit.jsonl` +30 additive lines (side-effect of test runs during this gate; no deletions). Recommend giter resets both before merge so the branch lands as the 4-commit stack.
- Runner infrastructure was /tmp-only (mapfile-hardened `run_chunk.sh`); no repo test-harness files modified by this gate.
- Duplicates run (harmless, identical results, dispatcher's own double-farms): chunk 44 ×2, chunk 75 ×2.
- Base worktree removed post-gate (verified); `merge-gate-ab-proof-location-vs-commit` skill consumed by the attribution worker (7/10 + improvement note filed).

## 5. Documentation updated
- RESULTS: this file. PACKS.md: new "Completed commission" section. QUARANTINE.md: +1 row (atomic_status flaky pair, base-attributed). LESSONS: `2026-10-05-unify-spawn-tools-acceptance-methods.md` (bisect pattern, count-tests-not-files, mapfile fix, argless-pytest trap, collapse clusters).

## 6. Bottom line for the leader
**PASS for merge.** The refactor is functionally clean: warm/cold/legacy-off behavior proven live through the real factory with byte-identical legacy strings; auto-dispatch lands on the child; R15 isolation intact; registry/factory clean; the full 24.6k-test default suite shows ZERO functional regressions attributable to the branch across 277 A/B-attributed reds. **One required merge-time disposition: the Phase-D seal break (re-seal or explicit acceptance).** Recommend a separate test-debt commission for the quantified pre-existing collapse clusters (service_tool fixture 23E is the cheapest big win).

# Skill-Selector "quick" Model Verification — `feature/skill-selector-quick-model` @ 4df0b100 (2026-10-05)

## 🟢 VERDICT: READY

Requirement closure: *"Update the skill selector to use the 'quick' model, with 'quick' as the model-name default."* — **CLOSED.** Unconfigured selector resolves `"quick"`; `SKILL_EVOLUTION_SELECTOR_MODEL` override wins; main `OPENAI_MODEL` proven NOT consulted on the selector path (end-to-end, live-value probe); skill-keeper evolution/analysis model config untouched (diff-verified).

- Branch: `feature/skill-selector-quick-model` @ `4df0b10038ba172113c4b37b5353869bbe99ece4` (verified), base `ac1bf7b9`, commits `b178d5c5` (code+tests, 7 paths, +161/−6) + `4df0b100` (docs-only, 3 paths, +5/−0).
- Workers: f443604b (git-hygiene), 0b719e4b (pack-config), 8c61a534 (pack-search), 9c6dc90d (gap-analysis), c2e10a2d (parent-baseline), 752e3b8b (behavioral). Artifacts: `/tmp/ens-selsel/`.
- Repo held READ-ONLY: zero tracked-file modifications, zero commits by this commission.

## Scope Decision

Verification commission scoped to the 10-path change set (config default + dict pass-through + `_llm_select` resolution + mirrors + docs). Full suite not warranted — no architecture impact. ensure.md Release Gate not triggered. Concurrency pack (Core #2/#3) out of blast radius: diff touches no lock/thread/async/DB-sync code.

## 1. Targeted unit test packs

| File | Result | Counts |
|---|---|---|
| `tests/test_skill_evolution_config.py` | ✅ PASS (0 branch reds) | 16 collected: **14P / 2F / 0E / 0S** (both F pre-existing, base-proven §3) |
| `tests/services/test_skill_search_service.py` | ✅ PASS | **34P / 0F / 0E / 0S** (2.11s) |

Selector-specific tests, all green by execution:
- `TestSelectorModelYamlMirror` 3/3 (interpolation form, resolves-to-quick w/ env scrub, env-override-wins)
- `TestEnvOverride` 5/5 (incl. `test_env_override_selector_model`)
- `TestSelectorModelResolution` 3/3 (`test_missing_selector_model_resolves_to_quick`, `test_explicit_selector_model_wins_over_main_model`, `test_empty_selector_model_falls_back_to_quick`)

## 2. Behavioral cases (a)–(d) — pinned-by-tests + real-path execution

| Case | Pinned by (cited) | Executed evidence |
|---|---|---|
| (a) no env → `"quick"` | `TestSelectorModelYamlMirror::test_yaml_mirror_resolves_to_quick` (real-path, delenv scrub); `TestDefaults::test_defaults` + `TestConfigIntegration::test_skill_evolution_defaults_via_config` (asserts unreachable this run — both abort at earlier PRE-EXISTING lines 42/212) | Behavioral LEG 1 PASS: real `Config()` with `OPENAI_MODEL=gpt-4o-leak-probe`, selector env unset → `cfg.skill_evolution.selector_model == 'quick'` AND `cfg.llm.model == 'gpt-4o-leak-probe'` |
| (b) `SKILL_EVOLUTION_SELECTOR_MODEL=custom` → custom | `TestEnvOverride::test_env_override_selector_model` + `TestSelectorModelYamlMirror::test_yaml_mirror_env_override_wins` (both real-path, both PASS) | Behavioral OVERRIDE PASS: `custom-selector-y` beats both default and main model |
| (c) legacy dict (ONLY old `"model"` key) → `_llm_select` = `"quick"`, no main-model leak | `TestSelectorModelResolution::test_missing_selector_model_resolves_to_quick` (real `_llm_select` code, hand-crafted legacy dict, PASS) | Behavioral LEG 3 PASS: manager-shaped dict (per `daemon/manager.py:1510-1532`, `model='gpt-4o-leak-probe'` + `selector_model=<real config value>`) through REAL `_llm_select` → mocked OpenAI client received `model='quick'` — **main model did not leak** |
| (d) config.yaml mirror → `"quick"` unset / override when set | `TestSelectorModelYamlMirror` 3/3 (real `yaml.safe_load` of shipped config.yaml + real `substitute_env_vars()`) | Same 3 tests, executed PASS |

Unpinned-gap scan (read-only, worker 9c6dc90d) found exactly ONE residual gap — the end-to-end real-`Config`→manager-dict→`_llm_select` no-leak chain — closed by the behavioral pack above. No duplication of already-pinned cases.

## 3. Pre-existing failures — base-proven at `ac1bf7b9` (worktree `/tmp/ens-selsel-parent`, import-verified cwd-first, removed after)

All 5 HEAD failures have identical test ids + identical error signatures at parent → PRE-EXISTING; **0 branch-caused failures**. Matches the dev's stash-baseline claim independently.

| # | Test | Error signature (identical both legs) | Root cause |
|---|---|---|---|
| 1-3 | `tests/manager/test_skill_service_init.py::{test_skill_services_initialized_when_config_present, test_skill_services_none_when_skill_evolution_disabled, test_skill_services_have_correct_dependencies}` — 3F/0P at HEAD AND parent | `MigrationError: Migration 20260714_000001 failed: (sqlite3.OperationalError) near "EXISTS": syntax error [ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS …]` | sqlite cannot parse PG `DROP CONSTRAINT IF EXISTS` — known pre-existing migration defect (head_f1.log; independently reproduced by behavioral LEG 2) |
| 4 | `TestDefaults::test_defaults` | `assert 'https://api.openai.com/v1' is None` | ambient env exports `SKILL_EVOLUTION_EMBEDDING_BASE_URL`; test scrubs only the selector var |
| 5 | `TestConfigIntegration::test_skill_evolution_defaults_via_config` | `assert 20 == 10` (ab_sample_size) | code default `Field(default=20)` (config.py:2240, D15) vs test + config.yaml:389 expecting 10 — known drift |

Parent config-file leg: 2F/10P (vs HEAD 2F/14P — the +4 delta is exactly the branch's new tests, all green at HEAD).

**Not quarantined, deliberately**: #1-3 include the manager integration test carrying the branch's new selector assertions — quarantining would hide that they are unexecuted in this environment (see Caveats); #4-5 are owned by the documented test-debt commission (sqlite-migration fix, ab_sample_size 10-vs-20, stale `expected_llm_config` keys — all reproduce on parent).

## 4. Commit hygiene — HYGIENE-CLEAN (worker f443604b)

- `git diff-tree -r --name-only ac1bf7b9..HEAD` = **exactly the 10 expected paths**, 0 missing / 0 unexpected.
- `4df0b100` stat: exactly 3 paths (`.env.example` +3, `CHANGELOG.md` +1, `docs/skill-evolution.md` +1), **5 insertions, 0 deletions, no code files** — docs-only confirmed.
- Dirty worktree files (`.agents/approver/*`, `.agents/tester/*`, `_PROVENANCE_READ_STATUS=unreadable`) appear in **no commit** (grep-swept the committed diff).
- Branch/HEAD verified before every pack run; `import daemon` resolved to `/home/nea/ensemble-src/daemon` in all legs (editable-install trap guard).

## 5. Evolution/analysis model config untouched (diff-verified)

- `git diff ac1bf7b9..HEAD -- daemon/config.py`: ONE hunk, +7 lines — `selector_model: str = Field(default="quick")` with comment explicitly noting the deliberate literal-default (NOT the `None`→main-model fallback used by `evolution_model`/`analysis_model`, which are untouched).
- `git diff ac1bf7b9..HEAD -- daemon/manager.py`: ONE hunk, +5 lines — `"selector_model": self.config.skill_evolution.selector_model` added to the `skill_llm_config` dict; no existing keys touched.

## 6. ensure.md status (Core, blast-radius scoped)

- Core #1 no regressions in changed packs: ✅ PASS (0 branch reds after base-proven exclusions §3)
- Core #2/#3 concurrency pack: OUT OF SCOPE (justified above) — last known state 98P/0F at 2026-10-05 chat-tab gate, no relevant code touched since
- Core #4 dev.sh `--timeout-graceful-shutdown 10`: ✅ PASS (dev.sh:102)
- Important async-await callers: N/A — no async conversions in this diff
- Release Gate: not triggered (small isolated change)

## Caveats (non-blocking)

1. **Manager-level integration assertion unexecuted in this environment**: `test_skill_services_have_correct_dependencies` (carrying the branch's new `selector_model` pass-through assertions) cannot run on sqlite due to pre-existing migration defect #1-3 — identical at parent. Compensating evidence: diff hunk (§5) + behavioral LEG 3 exercised the exact manager-shaped dict through real `_llm_select` with the config-derived value. Full closure of this leg lands whenever the sqlite-migration test-debt is fixed.
2. **Two known-debt artifacts red in the config file** (#4 ambient env, #5 ab_sample_size) — pre-existing, base-proven, owned by the documented test-debt commission.
3. Behavioral LEG 2 (`real InstanceManager`) = SKIPPED-KNOWN-ENV by design (same defect #1-3; 5/5 signature match) — not a verdict signal.

## Packs registered

| Pack | Location | Scope | Last Run | Status |
|---|---|---|---|---|
| `skill_evol_config_unit_test` (ad-hoc) | /tmp/ens-selsel/skill_evol_config_unit_test.sh | tests/test_skill_evolution_config.py full file | 2026-10-05 @ 4df0b100 | ✅ PASS (14P/2F, both base-proven) |
| `skill_search_service_unit_test` (ad-hoc) | /tmp/ens-selsel/skill_search_service_unit_test.sh | tests/services/test_skill_search_service.py full file | 2026-10-05 @ 4df0b100 | ✅ PASS 34/34 |
| `selector_parent_baseline_test` (ad-hoc, attribution leg) | /tmp/ens-selsel/selector_parent_baseline_test.sh | 2 files @ HEAD vs ac1bf7b9 | 2026-10-05 | ✅ 5/5 PRE-EXISTING, 0 branch-caused |
| `selector_e2e_leak_behavior_test` (ad-hoc) | /tmp/ens-selsel/selector_e2e_leak_behavior_test.sh | real Config→manager-dict→real `_llm_select` leak probe | 2026-10-05 @ 4df0b100 | ✅ PASS (3 PASS legs + 1 SKIP-known-env) |

## New failures found: NONE

---

# Post-merge insurance — `latest` @ `7dc927a9` (2026-10-05)

## 🟢 VERDICT: READY-TO-PUSH

Merge commit `7dc927a9` (parents `39036550` + `1879bfad` — verified via `git log --format="%H %P" -1`). Workers: d7664100 (static), 8d43bbb3 (config pack), 02a94ecc (search pack). Logs: `/tmp/ens-selsel/merge_*`.

1. **HEAD/parents**: `latest` @ `7dc927a9668426b0b954b948309c3952c9ca0851`, parents exactly `39036550` + `1879bfad`. ✓
2. **Merged `skill_llm_config` block (manager.py:1510-1532)**: `"selector_model": self.config.skill_evolution.selector_model` present at :1519; dict balanced, all-unique keys, comma-clean; selector addition is a pure 5-line insertion between `model` and `model_vision`. Chat-tab side's manager.py change confined to `list_instances` (~:11997, purely additive `source` param) — ~10k lines away, different function/scope, no encroachment. ✓
3. **Pack re-runs on merged latest**:
   - `tests/services/test_skill_search_service.py`: **34P/0F/0E/0S**, `TestSelectorModelResolution` 3/3 — exact match to pre-merge. ✓
   - `tests/test_skill_evolution_config.py`: **14P/2F/0E/0S**, both failures MATCH-EXPECTED (F1 ambient `SKILL_EVOLUTION_EMBEDDING_BASE_URL`; F2 `ab_sample_size` 20-vs-10) — byte-identical signatures to the pre-merge run (and base-proven pre-existing at `ac1bf7b9` in the main verification). Selector tests green: YamlMirror 3/3, EnvOverride incl. `test_env_override_selector_model`. ✓
4. **Import sanity**: `daemon` + `daemon.config` + `daemon.services.skill_search_service` import cleanly, resolve to `/home/nea/ensemble-src/daemon`; `py_compile` on manager.py/config.py/skill_search_service.py exit 0. ✓

**New failures vs pre-merge baseline: NONE.** Push gate satisfied.

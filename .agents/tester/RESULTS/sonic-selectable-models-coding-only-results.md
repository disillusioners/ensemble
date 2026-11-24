# TEST RESULTS — selectable-models-coding-only (v0.31.44) · integration gate on the MERGED tree

**Verdict: TEST: PASS** — every commissioned gate green on the merged tree; the single red in the
commission set is PROVEN pre-existing at base `044a6d20a` by an independent base-extraction run
(byte-identical failure signature). One registry gap was CLOSED from the tester lane (pack script +
ONE `PACKS.md` row), and that row is **NOT yet landed** — the leader must land it.

**Tree identity.** Worktree `/home/nea/ensemble-src-wt-selmodels`, branch
`feature/selectable-models-coding-only`, HEAD `7243143d0` (== `origin/latest` at lane open
`044a6d20a`). Working tree was CLEAN at intake (`git status --porcelain` empty) before this
lane's two registered artifacts were written (§7). Imports asserted from the worktree tree
(`daemon.__file__ == /home/nea/ensemble-src-wt-selmodels/daemon/__init__.py`), worktree
`.venv` (CPython 3.14.7) only, `ENSEMBLE_SELF_ENV=dev`, `.env` fenced to `POSTGRES_DB=ensemble_dev`.
No bare `pytest` was run on this tree (the known `tests/integration/test_message_queue_e2e.py`
collection abort was avoided by naming explicit files only).

**Blast radius.** 17 files across 5 modules per HANDOFF-v2, plus 3 upstream files explicitly
out of scope (`scripts/upgrade/lib.sh`, `tests/unit/services/test_engine_diff_audit.py`,
`.agents/tester/PACKS.md` — the last is the upstream retention lane's, NOT the commission's).
Selection widened by the tester to include every MERGED-touched file, not only the handoff's
verification set: the touched integration file `tests/integration/test_spawn_intelligence_tier.py`
and the touched pack script `test/packs/sonic_agent_defs_checks.py` were additionally A/B'd
(§5, §6) because both are in the diff range.

---

## 1. RESOLVED SET + TITLE/KEYWORD BINDING (handoff step 1) — PASS

Real `load_config()` in the worktree, explicit env verbatim:

```
allowed_models = ['agentic', 'coding', 'vision']
model_title    = title
model_keywords = quick
```

Matches the handoff's stated expectation exactly. `img-gen`/`decision` are NOT joined (the
resolver returns the explicit value VERBATIM) — correct per the ruling's scope.

**Env provenance (two independent paths, both agree).** `daemon/config.py` does NOT parse `.env`
itself (no `load_dotenv`; the `.env` → `os.environ` bridge is `launcher.sh::load_env_file` /
the binary's `run_app.py`). The verification was therefore run with the values EXPORTED
explicitly from the worktree's fenced `.env` (`/tmp/sonic-selmodels/env.sh`), and then RE-RUN as
the handoff's command LITERALLY (`python -c "from daemon.config import load_config; …"` with no
exports) — both legs print the identical triple above. Note the ambient tool shell on this host
ALREADY carries `OPENAI_SELECTABLE_MODELS=agentic,coding,vision` / `OPENAI_MODEL_TITLE=title` /
`OPENAI_MODEL_KEYWORDS=quick` (post-M3 host state) and `ENSEMBLE_SELF_ENV=live`; every run here
exported `ENSEMBLE_SELF_ENV=dev` + the full explicit `POSTGRES_*` dev fence so no run was
live-shaped, and the registered pack script (§11) exports the same fence on every invocation.

**Env files (read-only, no secrets):**

| file | OPENAI_SELECTABLE_MODELS | OPENAI_MODEL_TITLE | OPENAI_MODEL_KEYWORDS |
|---|---|---|---|
| `/home/nea/ensemble-src/.env` (:20/:17/:19) | `agentic,coding,vision` | `title` | `quick` |
| `/home/nea/ensemble-src/.env.prod` (:109/:16/:21) | `agentic,coding,vision` | `title` | `quick` |
| `/home/nea/agents-ensemble/.env` (LIVE, :109/:16/:21) | `agentic,coding,vision` | `title` | `quick` |
| worktree `.env` (:20/:17/:19) | `agentic,coding,vision` | `title` | `quick` |

All three host files carry mode 600; the handoff's "before" column is confirmed as changed.
The handoff's CORRECTION to the commission brief (the KEYWORDS line is PRESENT with value `quick`,
not absent) is confirmed by direct read.

## 2. BYTE-PIN between the two homes of the default (handoff step 2) — PASS

`tests/unit/test_llm_allowed_models_precedence.py::TestDefaultConsistencyPin` → **1 passed** (0.38 s).

## 3. THE NEW GUARD (handoff step 3) — PASS

`tests/unit/test_selectable_models_surface_guard.py` → **7 passed** (1.61 s), all seven legs
(a constant, b yaml literal, c FE zero-`coding2`, d FE no-mixed-array, e explicit-verbatim,
f no-env default, g legacy-alias-verbatim).

## 4. THE COMMISSION-RELEVANT SET (handoff step 4) — 172 passed / 1 failed

```
.venv/bin/python -m pytest \
  tests/unit/test_llm_allowed_models_precedence.py tests/unit/test_selectable_models_surface_guard.py \
  tests/unit/test_spawn_intelligence_tier.py tests/unit/test_caller_model_overrides_seam.py \
  tests/test_compare_tools.py daemon/tests/test_inject_allowed_models.py -q --tb=short -rf
→ 1 failed, 172 passed, 25 warnings in 5.30s
```

Collection census (no vacuous file — all six contributed): 38 + 7 + 20 + 17 + 83 + 8 = **173**
nodes, matching 172 P + 1 F exactly. `daemon/tests/test_inject_allowed_models.py` (8 nodes) is
outside the default `testpaths` and is collected only because it is NAMED — it did run.

### 4.1 The single red — PROVEN PRE-EXISTING (independent base-extraction run)

Failing node: `tests/unit/test_caller_model_overrides_seam.py::TestModelTierPrecedence::test_model_tier_wins_over_model_with_visible_note`
Exception: `daemon.tools.compare_tools.VisionModelNotAllowedError: compare_images: 'vision' model is missing from config.llm.allowed_models (['agentic', 'coding']). …`

**Quarantine discipline first:** the node has NO row in `.agents/tester/QUARANTINE.md` (grep on both
`test_caller_model_overrides_seam` and `test_model_tier_wins_over_model_with_visible_note` → 0 hits),
so it was adjudicated as a NEW red until proven otherwise.

**Proof of pre-existence (base-extraction run, provenance asserted):**
- `git archive 044a6d20a | tar -x` into `/tmp/sonic-selmodels/base-extract`; the runner script was
  executed INSIDE the extraction with `python -m pytest` so `sys.path[0]` = the base tree, and the
  runner **asserted** `daemon.__file__` and `daemon.config.__file__` both start with the extraction
  path (PROVENANCE-OK; a miss would void the leg).
- Base leg: **1 failed**, same node, same exception line, same output line number (4290).
- Normalized failure signatures (tree path, mock object addresses, wall-clock duration) are
  **BYTE-IDENTICAL**: sha256 `8b7beb1b7c3c489155f92a4875511e9662aff4cda9d553352f02d24dc668a4c8`
  for BOTH the base leg and the HEAD leg.
- **Static exoneration:** the test file and both production seams it exercises are byte-identical
  base↔HEAD — `tests/unit/test_caller_model_overrides_seam.py` blob `aaea6704b…`,
  `daemon/tools/compare_tools.py` blob `004eb0d69…`, `daemon/tools/instance.py` blob `ae09ceef…`
  (all unchanged). `git diff 044a6d20a..HEAD -- daemon/tools/ daemon/services/ daemon/repositories/ daemon/routers/`
  is **EMPTY** — the commission touched no runtime code outside `daemon/config.py` (25 lines,
  default tuple + prose) and `daemon/__init__.py` (version).
- ⇒ **Pre-existing, zero causal reach from this commission. NOT counted against the run.**

## 5. WIDENED SCOPE — the touched integration file (in the diff range, absent from the handoff's verify set)

`tests/integration/test_spawn_intelligence_tier.py` (M4-touched, 1-line fixture change).

| leg | result | failing set |
|---|---|---|
| HEAD `7243143d0` | **5 failed, 6 passed** (pattern `FF....FF..F`) | `TestSpawnIntelligenceTierSuccess::test_tier_high_resolves_to_agentic_with_visibility_line` · `TestSpawnIntelligenceTierLoud::test_tier_high_with_agentic_excluded_raises_value_error` · `TestBothParamsPrecedence::test_model_tier_wins_over_model_with_visible_note` · `TestBothParamsPrecedence::test_model_tier_wins_with_operator_overridden_default` · `TestBothParamsInvalidLegacyModel::test_both_params_with_invalid_legacy_model_suppresses_notice` |
| BASE `044a6d20a` | **5 failed, 6 passed** (pattern `FF....FF..F`) | IDENTICAL node-for-node |

⇒ Pre-existing family (the `model_tier` precedence / vision-allowlist fixture family). The
handoff's claim ("unmarked run is 5F/6P with a FAILED set byte-identical to the base tree") is
**independently confirmed**. Not counted against the run.

## 6. WIDENED SCOPE — the touched pack script (in the diff range)

`test/packs/sonic_agent_defs_checks.py` (M4-touched, 1-line `allowed_models_default` fixture).

- `--self-test` @ HEAD → `SELF-TEST: PASS` (all counter-mutation legs correctly RED; 0 vacuous).
- `--static` @ HEAD → `CHECKS: FAIL`, red legs **A3** (`sonic-leader` allow drift) + **A9**
  (`agents/` edit list) — the two legs the standing `sonic_agent_defs_unit_test` registry row
  already documents as PRE-EXISTING base-red legs.
- **A/B proof:** the same `--static` invocation at BASE `044a6d20a` (scratch local git repo under
  `/tmp`, zero mutation of the real repo) produces **BYTE-IDENTICAL output** (diff empty) — same
  two reds, same 20 PASS legs. ⇒ Pre-existing; the fixture change broke nothing.
- Re-run AFTER this lane's `PACKS.md` edit → still byte-identical to base (the registry edit did
  not perturb the pack's verdict).
- `tests/unit/test_sonic_push_doctrine_pins.py` (reads `PACKS.md`) → **41 passed** after the edit.

## 7. VERSION IDENTITY (handoff step 5) — PASS

`tests/test_api.py -k health_check` → **1 passed, 46 deselected** (4.48 s).
`daemon.__version__ == 0.31.44`; `pyproject.toml:3 version = "0.31.44"`.

## 8. ZERO `coding2` IN THE SHIPPED HOMES (handoff step 6) — PASS

`grep -rn coding2 daemon/config.py config.yaml frontend/src` → **exit 1** (no hits).

## 9. CENSUS REMAINDER (handoff step 7) — PASS

`git grep -c coding2 -- . ':(exclude)frontend/node_modules'` → every survivor is a documented KEEP:

| location | hits | handoff's declared | match |
|---|---|---|---|
| `tests/unit/test_selectable_models_surface_guard.py` | 24 | 24 | ✅ |
| `tests/unit/test_llm_allowed_models_precedence.py` | 16 | 16 | ✅ |
| `tests/unit/test_spawn_intelligence_tier.py` | 2 | 2 | ✅ |
| `tests/unit/test_caller_model_overrides_seam.py` | 1 | 1 | ✅ |
| `CHANGELOG.md` | 3 | 3 | ✅ |
| `docs/setup.md` | 1 | 1 | ✅ |
| `daemon/**`, `agents/**`, `scripts/**`, `frontend/**`, `daemon/tests/**`, `test/packs/**` | **0** | 0 | ✅ |

Remainder is all dated evidence/planning: `.agents/tester/**` 48 files, `.agents/shared/planning/**`
26 files, repo-root `RESULTS/` 1 file, `legacy/**` 1 file. NOTE (disclosed, immaterial): the
handoff's table says the `.agents/tester` class is "18 files" — the measured count is **48**
(the delta is the `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/logs/*.log` set). Same
KEEP class (dated test evidence), no new class, no shipped-surface survivor.

## 10. THE STAGED PAYLOAD — the real integration surface (tester-added)

The release was already staged; the SHIPPED ARTIFACT was verified directly, not just the source tree.

- `/home/nea/agents-ensemble/releases/v0.31.44/config.yaml:98` →
  `allowed_models: ${OPENAI_SELECTABLE_MODELS:-agentic,coding,vision,img-gen,decision}` ✅ (no `coding2`).
- `coding2` census over the staged payload's `config.yaml` + `agents/` + `launcher.sh` → **0 hits**.
- Staged `frontend/dist` → **0 `coding2` hits**; the only `'coding'` literals (3) are the SKILL
  taxonomy enum, matching the handoff's FE account.
- `manifest.json`: `version = v0.31.44`, `binary_version = 0.31.44`,
  `known_schema_gen = 20261121_120001_create_engine_runs.sql`,
  `pipeline_scripts_floor_commit = 46d55b002d35092c29c391fc76e6b897c0819e11`.
- Staged binary banner (non-boot probe, per-variable `POSTGRES_*` scrub — never the `env -u POSTGRES_*`
  glob no-op): **`Ensemble v0.31.44`**.
- Live pointer untouched: `releases/state.json` → `current = v0.31.42`; v0.31.44 is staged, not promoted.

## 11. PACK REGISTRATION GAP — CLOSED (tester-lane authority; NOT LANDED)

**Finding.** The handoff flagged `tests/unit/test_selectable_models_surface_guard.py` as unregistered
(it sat outside the developer's module file fence). Audit of the shared registry:

| registry copy | rows for the guard |
|---|---|
| MAIN `/home/nea/ensemble-src/.agents/tester/PACKS.md` (detached @ `bf4bfde6e`, BEHIND `origin/latest`) | **0** |
| branch worktree copy | **0** |
| `origin/latest` copy | **0** |

⇒ The guard was **not gate-verifiable** ("gate-verifiable = registered"). The MAIN copy being
detached/behind is exactly the trap the POK warns about — it was NOT used as the authority; the
branch + `origin/latest` copies were.

**Action taken (within the tester lane's normal authority — my write fence explicitly includes
`test/packs/` and the ONE-line rows in `.agents/tester/PACKS.md`; the developer's fence did not):**

1. `test/packs/sonic_selectable_models_guard.sh` — NEW, standard test tree, 3 legs:
   **L1 SURFACE ZERO** (`coding2` absent from `daemon/config.py`, `config.yaml`, `frontend/src`,
   `agents/**`, `scripts/**`; anti-vacuity: the scan must read >0 files — 1045 at HEAD),
   **L2 RESOLVED SET** (real `load_config()` with explicit `config_path` → `['agentic','coding','vision']`,
   `model_title=='title'`, `model_keywords=='quick'`), **L3 COMMITTED PINS** (the guard's 7 nodes +
   the untouched drift byte-pin). Fences: dev-only explicit per-variable `POSTGRES_*` pin, hard
   `ensemble_prod` refusal (exit 78), no daemon boot, no live contact, dual-layer timeout.
   @ HEAD → **RESULT: PASS (all legs green)**.
2. `.agents/tester/PACKS.md` — **ONE row** appended in the house `Pack / leg | Invocation | Scope |
   Last run | Status` form inside a marked block, tagged sonic-tester.

**MUTATION PROOF (non-vacuity).** The SAME pack run at BASE `044a6d20a` (scratch local git repo,
`.venv` symlinked, pack script copied in) → **RESULT: FAIL (3 legs red)**: L1 flags 10 `coding2`
hits in `daemon/config.py` + 2 in `config.yaml`; L3 has no guard file at base (rc=4). L2 stays
GREEN at both legs (explicit env resolves verbatim at base too — proving L2 measures RESOLUTION,
not the default). ⇒ The pack detects the exact defect this commission fixed.

**LANDING GAP — ACTION REQUIRED BY THE LEADER.** Both artifacts are **UNCOMMITTED working-tree
changes in `/home/nea/ensemble-src-wt-selmodels`** (`M .agents/tester/PACKS.md`,
`?? test/packs/sonic_selectable_models_guard.sh`). I did NOT commit (my role forbids commits
outside my lane, and the dispatch posture is verify-only). Consequences to note:
- The branch tip `7243143d0` is already merged into `origin/latest` (the `v0.31.45` stage sha
  `51dc98bb` is a DESCENDANT of `7243143d0`), and that tree contains **zero** rows for the guard
  ⇒ the registration gap persists on `latest` until these two files are landed.
- The worktree is now dirty. The staged v0.31.44 artifact came from the SEPARATE stage worktree
  `/home/nea/ensemble-src-wt-stage-v3144` (verified clean), so the staged release is unaffected.
  But a RE-STAGE run FROM THIS worktree would trip the `git_dirty:false` provenance gate
  (`_git_dirty_porcelain` counts any porcelain output incl. untracked `??`) — land or stash the two
  files first, or re-stage from a clean worktree.

## 12. NOT EXERCISED (inherited from the handoff; disclosed, not a red)

- No daemon boot, no live smoke, no `spawn_instance` against the new allowlist (config-layer proof only).
- No promote / rollback exercise (stage-only release).
- The FE guard's array-literal detector had no mutation probe from the developer — the tester pack
  (§11) supplies an independent mutation probe for the SHIPPED-SURFACE contract, not for that
  detector's internals.

## 13. VERDICT

**TEST: PASS.** 6 of 7 commissioned gates green exactly as the handoff predicted (resolved set,
byte-pin, guard 7/7, version identity, zero-`coding2`, census remainder). The 7th (the 173-node
commission set) is **172 passed / 1 failed**, and the 1 red is **PROVEN pre-existing at base
`044a6d20a`** by an independent base-extraction run with a byte-identical failure signature and
byte-identical production seams — it does not count against this commission. The staged v0.31.44
payload independently carries the ruling (config.yaml default, agents/ tree, FE dist, manifest,
binary banner). One registry gap was closed from the tester lane and **needs landing**.

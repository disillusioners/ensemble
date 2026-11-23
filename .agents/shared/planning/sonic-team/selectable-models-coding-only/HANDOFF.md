# HANDOFF — selectable-models-coding-only

**Status: READY-TO-CUT — release cycle EXECUTING per Ari's explicit all-clear (received 2026-11-23, ~14:55Z).**
The commissioned pre-all-clear marker `HOLD: ready-to-cut, not staged (awaiting Ari all-clear)` is
SUPERSEDED — the hold was released by the front door before this handoff was authored. This document was
written at the ready-to-cut tip; the tag cut and the stage execute immediately after this commit lands.
The staged sha cannot appear inside the commit it certifies — it is reported in the final report to
sonic-leader.

| item | value |
|---|---|
| lane base | `044a6d20aa99ea50a3261c251ceeddbd16a6a605` (origin/latest at plan-open) |
| upstream sync mid-lane | `96e3a94bf`, `60d1de7af` (retention lane; `scripts/upgrade/lib.sh` only — NOT part of this commission) |
| merged tip (pre-handoff) | `69dd0c7c6` |
| branch | `feature/selectable-models-coding-only` |
| version slot | **v0.31.44** (W9-derived at cut time: highest local+remote tag `v0.31.43`; `v0.31.44` free locally AND remotely; live `releases/` census `v0.31.37/v0.31.41/v0.31.42/v0.31.43`) |
| release type | docs/config-only (zero migrations, zero schema change) |
| complexity verdict | **medium** (see final section) |

## Files touched across modules

**M1 — m1-config-default** (`daemon/config.py`, `config.yaml`)
- `daemon/config.py` — `_ALLOWED_MODELS_DEFAULT` tuple + 9 prose/docstring sites quoting the old default
- `config.yaml` — `allowed_models:` interpolation literal (line 98) + the comment block above it

**M2 — m2-docs-surface** (`.env.example`, `.env.prod.example`, `README.md`, `docs/setup.md`)
- `.env.example` (1 site), `.env.prod.example` (2 sites), `README.md` (1 site), `docs/setup.md` (5 sites)

**M3 — m3-env-host** (host-local, gitignored — NO repo commit)
- `/home/nea/ensemble-src/.env` (source env), `/home/nea/ensemble-src/.env.prod` (live mirror),
  `/home/nea/agents-ensemble/.env` (LIVE install, mode 600) + `.bak-selmodels-20261123` backups

**M4 — m4-test-pins** (7 files: 6 modified + 1 new)
- `tests/unit/test_llm_allowed_models_precedence.py`, `tests/unit/test_selectable_models_surface_guard.py` (NEW, 7 nodes),
  `tests/helpers/send_message_fixtures.py`, `tests/test_compare_tools.py`,
  `tests/integration/test_spawn_intelligence_tier.py`, `daemon/tests/test_inject_allowed_models.py`,
  `test/packs/sonic_agent_defs_checks.py`
- NOT touched (disposition: KEEP-class arbitrary fixtures, editing them would be cosmetic churn):
  `tests/unit/test_spawn_intelligence_tier.py`, `tests/unit/test_caller_model_overrides_seam.py`

**M5 — m5-release-prep** (`pyproject.toml`, `daemon/__init__.py`, `uv.lock`, `tests/test_api.py`)
- version `0.31.42` → `0.31.44` (the branch carries BOTH bumps: `31b1e75e6` = 0.31.43, the recorded
  slot collision; `308b3e330` = 0.31.44, the re-derived slot. The intermediate value is history, not a release.)

**Net diff (mine only, `044a6d20a..69dd0c7c6`):** 17 files changed, 354 insertions(+), 56 deletions(-).
3 files in the same range belong to the upstream retention lane, not to this commission:
`.agents/tester/PACKS.md`, `scripts/upgrade/lib.sh`, `tests/unit/services/test_engine_diff_audit.py`.

## What changed

**Ruling 1 — "Coding only, coding2 is moved inside the proxy group feature."**
The selectable list offers `coding`, NOT `coding2`. `coding2` is removed from the SHIPPED default in
both of its homes, which stay byte-equal:
- `daemon/config.py::_ALLOWED_MODELS_DEFAULT` = `("agentic", "coding", "vision", "img-gen", "decision")`
- `config.yaml:98` = `${OPENAI_SELECTABLE_MODELS:-agentic,coding,vision,img-gen,decision}`
`img-gen` and `decision` are KEPT (purpose-ids for spawn-time `llm_model` override consistency —
`daemon/tools/image_gen.py::IMAGE_GEN_DEFAULT_MODEL`); the ruling's scope is the CODING FAMILY ONLY.
Both homes carry a dated removal note (2026-11-23) worded so the retired token no longer appears in
either file (the token never had to be spelled to explain its own removal).

**Ruling 2 — "Title model is title. The name say it all."** FIX-SCOPE, same release.
`OPENAI_MODEL_TITLE=title` in BOTH trees; the source env moved `quick` → `title`, the live install
already ran `title` (verified, untouched).

**Resolved selectable set (proof, merged tree, real `load_config()`):**
- explicit env `OPENAI_SELECTABLE_MODELS=agentic,coding,vision` → `config.llm.allowed_models == ['agentic','coding','vision']`
  — `coding2` absent, `img-gen` NOT joined (the resolver returns the explicit value VERBATIM).
- NEITHER env var set → `['agentic','coding','vision','img-gen','decision']` (the shipped default), no `coding2`,
  with the one-shot `[Config] allowed_models default applied: [...]` WARNING naming the new list.

**FE proof — there is no FE model picker to fix.** `grep -rn coding2 frontend/src` → **0 hits** across
370 TS files; the only `'coding'`/`'title'` literals in the FE are the SKILL taxonomy enum
(`skill.model.ts:224/240`) and an HTML `class="title"` — not model aliases. No FE model array exists, so
the daemon-resolved set is ALREADY the single source of truth. The only selectable surface is the
daemon-side prompt injection (`daemon/services/instance_lifecycle.py::append_allowed_models`, which reads
`manager.config.llm.allowed_models`). `tests/unit/test_selectable_models_surface_guard.py` now pins this
(bytes-scans 374 non-spec FE files for `coding2` and for a mixed `'coding'`+`'agentic'` array literal, with
a scanned>0 anti-vacuity assert).

**Env values (before → after, both paths disclosed):**
| file | key | before | after |
|---|---|---|---|
| `/home/nea/ensemble-src/.env:20` | OPENAI_SELECTABLE_MODELS | agentic,coding,coding2,vision | **agentic,coding,vision** |
| `/home/nea/ensemble-src/.env:17` | OPENAI_MODEL_TITLE | quick | **title** |
| `/home/nea/ensemble-src/.env.prod:109` | OPENAI_SELECTABLE_MODELS | agentic,coding,coding2,vision | **agentic,coding,vision** |
| `/home/nea/ensemble-src/.env.prod:16` | OPENAI_MODEL_TITLE | quick | **title** |
| `/home/nea/agents-ensemble/.env:109` | OPENAI_SELECTABLE_MODELS | agentic,coding,coding2,vision | **agentic,coding,vision** |
| `/home/nea/agents-ensemble/.env:16` | OPENAI_MODEL_TITLE | title | title (verify-only, untouched) |
Modes 600 preserved on all three; diffs vs backup touch ONLY the listed key lines; zero `coding2` residue.
`OPENAI_MODEL_KEYWORDS` untouched everywhere.

**Title / keyword binding proof (through the real daemon config path):**
| case | env | title task calls | keyword task calls |
|---|---|---|---|
| A as shipped now | TITLE=title, KEYWORDS=quick | **title** | quick |
| B KEYWORDS line absent | KEYWORDS unset | title | **agentic** (falls back to the MAIN model via the `LLMConfig` validator — NOT quick) |
| C TITLE env absent | TITLE unset | **title** (`config.yaml`'s `${OPENAI_MODEL_TITLE:-title}` default) | quick |
The title binding is `title` by three independent routes (env var, YAML interpolation default, field
default). KEYWORDS stays REPORT-ONLY — nothing was changed; with the line absent the keyword task
resolves to `agentic`.
**Correction to the commission brief:** the KEYWORDS line is NOT absent — it is PRESENT in all three env
files (source `.env:19`, live `.env:21`, `.env.prod:21`) with value `quick`. The brief's premise was wrong.

**coding2 census (post-merge, tracked tree) — every survivor is a deliberate KEEP:**
| location | hits | disposition |
|---|---|---|
| `tests/unit/test_selectable_models_surface_guard.py` | 24 | KEEP — the guard's own negative assertions (assert coding2 ABSENT) + the legacy-alias leg |
| `tests/unit/test_llm_allowed_models_precedence.py` | 16 | KEEP — legacy-alias precedence fixtures (arbitrary strings) + the inverted WARNING pin (`assert "coding2" not in msg`) |
| `tests/unit/test_spawn_intelligence_tier.py` | 2 | KEEP — arbitrary allow-list that excludes `agentic` to exercise the WARN path |
| `tests/unit/test_caller_model_overrides_seam.py` | 1 | KEEP — synthetic list containing `gpt-4` |
| `CHANGELOG.md` | 3 | KEEP — historical entries (house: changelog not maintained per release) |
| `docs/setup.md` | 1 | KEEP — historical v0.16.0 statement (history is not rewritten) |
| `.agents/tester/RESULTS/*`, `PACKS.md`, `QUARANTINE.md`, `MOCK_TESTS.md` | 18 files | KEEP — dated test evidence / registry rows |
| `.agents/shared/planning/*`, `RESULTS/`, `legacy/` | many | KEEP — dated planning + evidence records |
| **`daemon/config.py`, `config.yaml`, `frontend/**`, `agents/**`, `scripts/**`** | **0** | REMOVED / never present |

**Live DB audit (READ-ONLY SELECT only, `default_transaction_read_only=on`, ensemble_prod@10.44.0.2):**
- `custom_agent_configs`: **0 rows total** → **0 agent rows with `llm_model='coding2'`**, 0 rows whose
  `llm_models` pool mentions coding2. (No migration performed — the Emperor owns that call.)
- Separately: **23 historical `instances` rows** carry `coding2` in `metadata` (kb-writer×14,
  sonic-developer×3, kb-importer/leader/sonic-leader/coder/worker/explorer ×1 each). These are runtime
  instance records, not agent definitions; they are historical and were NOT touched.

**Restart answer:** the running daemon does **NOT** pick the new env values up without a restart.
`load_config()` reads `os.environ` for the selectable models ONCE per process (the A6 boot-snapshot rule;
the field is documented as process-lifetime config) and `config.yaml` interpolates
`${OPENAI_MODEL_TITLE:-title}` at that single load. The new values therefore take effect at the next
daemon boot — they ride the ceremony restart. **No restart was performed.** The running daemon still
holds the OLD env until then.

## What is UNTESTED

- **Full suite not run.** Bare `pytest` is prohibited on this tree (known collection abort in
  `tests/integration/test_message_queue_e2e.py`). Only the commission-relevant files were exercised.
- `tests/integration/test_spawn_intelligence_tier.py` — `-m integration` deselects all 11 nodes (the file
  carries no `integration` marker, so that invocation is VACUOUS). The unmarked run is 5F/6P with a
  FAILED set byte-identical to the base tree (pre-existing family: fixture allow-lists lacking `vision`).
- **No daemon boot, no live smoke, no `spawn_instance` against the new allowlist** — the resolved set is
  proven at the config layer only. The spawn-time consumers (`daemon/tools/instance.py`,
  `daemon/tools/compare_tools.py` vision guard) were exercised only through their existing unit tests.
- **No promote, no live flip, no rollback exercise.** Stage-only, per the all-clear.
- **The FE build was proven in a throwaway probe worktree, not in the stage worktree** — the FE source is
  unchanged by this release, but the provenance sidecar must be re-stamped at the tagged commit.
- `tests/unit/test_selectable_models_surface_guard.py` is **NOT registered in `.agents/tester/PACKS.md`**
  (outside the module's file fence). The tester lane may want a registry row.
- The FE guard's array-literal detector was validated against the current tree only — no mutation probe
  was run to prove it would actually catch a reintroduced hardcoded list.

## Known risks

- **Deployments with NO env var lose `coding2` from the selectable set** (the shipped default changed).
  That is the ruling's intent, but it IS a behavior change for any deployment that relied on the default.
- **One path still allows a deployment to name `coding2` explicitly**: the legacy alias
  `OPENAI_ALLOWED_MODELS=…,coding2` is honored VERBATIM (precedence chain unchanged). The ruling removes
  `coding2` from the shipped default and from our selectable surface; it cannot revoke a name an operator
  types into the legacy var. Pinned by the guard's leg (f).
- **Version-slot race (resolved, recorded):** the slot `v0.31.43` was consumed mid-lane by a concurrent
  lane (tag `9298dd645` → commit `60d1de7af`, pushed, an ancestor of `origin/latest`); the slot was
  re-derived to `v0.31.44` per W9. The branch history therefore contains an intermediate bump to
  `0.31.43` — a superseded value, never a release.
- **The other lane's `v0.31.43` is unhealthy (NOT ours):** its tagged tree still carries `0.31.42` in
  `pyproject.toml` + `daemon/__init__.py`, and its release dir exists while `current` still points at
  `v0.31.42`. Their release needs a bump or a re-cut before any promote. Disclosed, not touched.
- **Live `releases/` over-retention:** the census at cut time is `v0.31.37/v0.31.41/v0.31.42/v0.31.43`
  (4 dirs, keep-3 capacity +1) and this stage adds a 5th. Per the corrected record (the retention lane
  verified its call sites: STAGING does not evict; only promote/rollback do), this stage does not evict
  anything — but the over-retention is real and will surface at the next promote/rollback.
- **The live daemon keeps the OLD env until the ceremony restart** — the new selectable set and the
  title binding are not live until then. A restart before the ceremony would be an out-of-band change.
- **The running daemon's in-flight instances may still carry a `coding2` model** from before the
  change (23 historical rows). No migration was performed (the Emperor's call); the session-restore
  re-validation path (`daemon/tools/instance.py`) consults the allowlist — a restored instance pinned to
  `coding2` would fall back rather than fail loudly. NOT exercised here.

## How to verify

Run on the MERGED tree (worktree `/home/nea/ensemble-src-wt-selmodels`, branch
`feature/selectable-models-coding-only`; `ENSEMBLE_SELF_ENV=dev`; the worktree `.env` is fenced to
`POSTGRES_DB=ensemble_dev`):

```bash
cd /home/nea/ensemble-src-wt-selmodels
export ENSEMBLE_SELF_ENV=dev
# 1. resolved set + no coding2 on any selectable path
.venv/bin/python -c "from daemon.config import load_config; c=load_config(); print(c.llm.allowed_models, c.llm.model_title, c.llm.model_keywords)"
# 2. byte-pin between the two homes of the default
.venv/bin/python -m pytest tests/unit/test_llm_allowed_models_precedence.py::TestDefaultConsistencyPin -q
# 3. the new guard (7 nodes: default, yaml literal, FE scan, resolved set, legacy alias)
.venv/bin/python -m pytest tests/unit/test_selectable_models_surface_guard.py -q
# 4. the whole commission-relevant set
.venv/bin/python -m pytest tests/unit/test_llm_allowed_models_precedence.py tests/unit/test_selectable_models_surface_guard.py \
  tests/unit/test_spawn_intelligence_tier.py tests/unit/test_caller_model_overrides_seam.py tests/test_compare_tools.py \
  daemon/tests/test_inject_allowed_models.py -q
# 5. version identity
.venv/bin/python -m pytest tests/test_api.py -q -k health_check
# 6. zero coding2 in the config homes + FE
grep -rn coding2 daemon/config.py config.yaml frontend/src ; echo "exit=$? (1 = no hits)"
# 7. census remainder (expect only the documented KEEP classes)
git grep -c coding2 -- . ':(exclude)frontend/node_modules' | sort -t: -k2 -rn | head
```
Expected: (1) `['agentic', 'coding', 'vision']` / `title` / `quick`; (2) 1 passed; (3) 7 passed;
(4) **172 passed / 1 failed** — the single failure is
`tests/unit/test_caller_model_overrides_seam.py::TestModelTierPrecedence::test_model_tier_wins_over_model_with_visible_note`,
which fails IDENTICALLY at base `044a6d20a` (pre-existing, proven by a base-extraction run);
(5) 1 passed; (6) exit 1; (7) only the documented KEEP classes.

Env verification (read-only, no secrets):
```bash
grep -nE '^(OPENAI_SELECTABLE_MODELS|OPENAI_MODEL|OPENAI_MODEL_TITLE|OPENAI_MODEL_KEYWORDS|OPENAI_MODEL_VISION)=' \
  /home/nea/ensemble-src/.env /home/nea/ensemble-src/.env.prod /home/nea/agents-ensemble/.env
```

## Bash-raises used

**none.** No bash raise was consumed. Long-running work ran in background `proc` processes (FE toolchain
probe, `npm ci` + `uv sync` pre-warm, `uv sync` for the module venvs) — no command exceeded the 600 s
blocking-bash default, and no raise from sonic-leader was required.

## Complexity self-assessment

**medium.** Not `low`: the change reaches a byte-pinned default living in TWO homes that a drift-pin test
compares character-for-character, touches a LIVE install's env file, and fans out into 17 files across
five modules plus a new guard test. Not `high`: the depth is configuration + documentation + test pins —
no schema, no migration, no new runtime code path, no behavioral surface beyond the contents of one
allowlist tuple, and the coupling that mattered (the two homes of the default) was deliberately kept
inside a single module so no cross-module conflict was possible. The load-bearing risks found during
the lane (FE toolchain broken by an incomplete `node_modules`; the version-slot collision) were both
retired BEFORE the release cycle, not during it.
Handover was never raised — the verdict was `medium` from the plan onward, so no override attribution applies.

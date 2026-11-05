# Phase notes — Memoir Store v1 (waves 1 of 5)

**Lane:** `feature/ari-memoir-store` @ worktree `/home/nea/ensemble-src-wt-memoir-store`.
**Wave:** phase 0 + phase 1 (waves 1 of 5).
**Starting HEAD:** `5dc08aca8` (the approval fold).

This file is the phase-0 scratch (`phase0-plan.md` T0.1): the anchor sweep
table, the drift record, and the decisions taken inside the phase where
the plan left a choice open. Per P-7, anchor drift is NOT a deviation —
it is a re-grep plus this note. A real deviation would need a
`decisions.md` §4 row and is NOT present in this wave.

---

## 1. Anchor sweep (T0.1 — re-grepped at `5dc08aca8`, 2026-11-05)

Every anchor the phase plans cite, re-verified at the implementation HEAD.
"Lead" = the value the plan/dispatch stated; "actual" = the value at
`5dc08aca8`.

| Anchor | Lead | Actual | Status |
|---|---|---|---|
| `daemon/manager.py` — `_ensure_postgres_columns` def | `:8055` | `:8055` | ✅ match |
| `daemon/manager.py` — shared `statements` list built | `:8269` | `:8269` | ✅ match |
| `daemon/manager.py` — shared list closing `]` | `:9554` | `:9554` | ✅ match |
| `daemon/manager.py` — ensure execute (`with self._engine.begin()` / `for stmt` / `conn.execute`) | `:9554-9557` | `:9555` (`with`), `:9556` (`for`), `:9557` (`conn.execute`) | ✅ match (the plan's range starts one line early — it includes the closing `]`) |
| `daemon/manager.py` — `_ensure_postgres_columns()` call (PG-gated) | `:997` | `:997` | ✅ match |
| `daemon/manager.py` — `SQLModel.metadata.create_all(self._engine)` | `:975` | `:975` | ✅ match |
| `daemon/manager.py` — model-registration import block (board) | `:944` | `:944` | ✅ match |
| `daemon/manager.py` — model-registration import block (bash_spawn) | `:957` | `:957` | ✅ match |
| `daemon/manager.py` — model-registration import block (project_skill_file) | `:969` | `:969` | ✅ match |
| `daemon/manager.py` — house `ADD COLUMN IF NOT EXISTS` precedent (`secrets_audit.context`) | `:9481-9484` | `:9481-9484` | ✅ match |
| `daemon/migrations/versions/` census | 119 | 119 | ✅ match — delta after this lane's file is **+1 = 120** |
| `daemon/loader.py` — `estimate_tokens` | `:1027` | `:1027` | ✅ match |
| `daemon/llm_error_classifier.py` — `RETRYABLE_STATUS_CODES` | `:23` | `:23` (no 408) | ✅ match |
| `daemon/config.py` — shared `EMBEDDING_*` family | `:4374-4376` | `:4374-4376` | ✅ match |
| `daemon/services/db_offload.py` — dedicated executor (`thread_name_prefix`) | `:342` | `:343` (the `_OFFLOAD_THREAD_PREFIX` usage; the docstring names the value at `:5`) | ⚠️ **+1 line drift** — re-grepped, used the real value; phase 3 owns this anchor |
| `daemon/repositories/infra/types.py` — `JSONBType` | `:35-70` | `:35` (class) | ✅ match |
| `daemon/repositories/prompt_version/repository.py` — `VALID_TRANSITIONS` | `:217-221` | `:217-225` | ✅ match |
| `daemon/services/prompt_version_service.py` — `HumanLaneRequiredError` / the `auth_method == "session"` discriminator | `:678-716` | `:184` (the error class), `:678-716` (`_require_human_lane`) | ✅ match (the plan cites the discriminator site; the class itself lives at `:184`) |
| `daemon/repositories/project/models.py` — `CriticalNoteEmbeddingModel` | `:293-347` | `:293` | ✅ match |
| `daemon/repositories/project/models.py` — model-bound `CheckConstraint` precedent | `:431` | `:430-433` | ✅ match |
| `daemon/repositories/job_queue/models.py` — model-bound `CheckConstraint` precedent | `:194-195` | `:194-195` | ✅ match |
| `daemon/models/resource_monitor.py` — partial-index precedent (`sqlite_where` / `postgresql_where`) | `:260-268` | `:256-268` | ✅ match |
| `daemon/repositories/bash_spawn/` — the D0.1 package template | — | `__init__.py` / `models.py` / `repository.py` | ✅ match |
| `daemon/models/board.py` — the "every string column is TEXT" + 3-site index lockstep precedent | — | present | ✅ match |
| `daemon/migrations/versions/20261020_060000_create_board_tables.sql` — the multi-table single-file `.sql` precedent | — | present | ✅ match |
| `daemon/services/timestamps.py` — `now_utc_iso()` | — | `:63` | ✅ (new anchor, used by phase 1) |

**Drift found:** ONE (`db_offload.py` `:342` → `:343`). Recorded per P-7;
no plan text changes.

## 2. Environment facts proven before writing code

* Import resolution — `cd /home/nea/ensemble-src-wt-memoir-store && /home/nea/ensemble-src/.venv/bin/python -c "import daemon; print(daemon.__file__)"` prints
  `/home/nea/ensemble-src-wt-memoir-store/daemon/__init__.py` (the worktree,
  NOT the main checkout). The worktree has no `.venv`; the main venv's
  interpreter is used with the worktree as CWD.
* Library versions in that venv: pydantic 2.12.5, pydantic-settings 2.13.1,
  sqlmodel 0.0.37, sqlalchemy 2.0.48.
* `pydantic-settings` behaviour proven by probe (`/tmp/memoir-probe/probe.py`):
  the env source IS applied to a NESTED `BaseSettings` (so a
  `memoir:` yaml block and the `ENSEMBLE_MEMOIR_*` env tier meet inside
  the model), the raw env string reaches a `field_validator(mode="before")`
  as a STRING, and init kwargs otherwise outrank the env — which is why
  the model's own before-validator applies the env precedence for
  `enabled` (the kill-switch env var must beat `config.yaml`).

## 3. Decisions taken inside the phase (recorded, not re-litigated)

| # | Decision | Why |
|---|---|---|
| P0-1 | The knob's **config-file tier** is supplied to the resolver by the CALLER (`resolve_memoir_knob(config=...)`); `config=None` means "no config tier available" and is what makes the truth table's last row (unparseable AND the fallback also fails ⇒ DISABLED + ERROR) reachable. | The truth table demands a reachable ERROR row. A resolver that always had a config tier could never produce it. |
| P0-2 | The truth table's "absent ⇒ WARNING" row fires when the env is absent AND the resolved state is the ON default. When the env is absent and `config.yaml` explicitly sets `memoir.enabled: false`, the resolution is `config` and the log is INFO (the operator made an explicit decision; the WARNING text "unset — ENABLED by default" would be factually wrong). | The truth table says the absent row resolves to "config default (ON in v1)" — the parenthetical makes the config tier the parameter. The two rows stay distinguishable and neither is silent. |
| P0-3 | The once-per-boot LOG guard is keyed by the emitted message and lives in the knob module (`_emit_once`); the resolved VALUE is never memoized. | "Log (once per boot)" is the truth table's column; AC6 forbids caching the value. Keying by message means a CHANGED knob value re-warns while a repeated resolution stays quiet. |
| P0-4 | `MemoirConfig` reads the env tier for `enabled` in its own before-validator (`_env_tier_wins`). | The env tier must beat `config.yaml` for a kill-switch, and the precedence rule must have exactly one home (D0.4/N-2: no consumer parses the env). |
| P0-5 | The `memoir:` yaml block IS wired into `load_config` (`config_dict["memoir"]`). | AC3 requires `config.yaml memoir.enabled:false` to be honoured when the env is absent. (House note: the `notify_user:` yaml block is NOT wired — its field is populated by the default factory only, so its yaml tier is inert. That pre-existing condition is NOT this lane's to change; the memoir section is wired deliberately.) |

## 3.1 Reserved migration prefix (T0.4)

**`20261105_102821`** — minted at the implementation HEAD (`5dc08aca8`),
2026-11-05T10:28:21Z. Uniqueness re-verified against the census: the
highest existing prefix is `20261031_120002`, and the census is 119 files
before this lane's file (delta **+1 = 120** after it).

The file this prefix names: `daemon/migrations/versions/20261105_102821_create_memoir_tables.sql`.

## 4. Deliberately left for later waves

| Item | Owner | Note |
|---|---|---|
| The health/readiness surface carrying the resolved knob state | a later wave | T0.5 says "readiness surface (if one exists)". A readiness surface DOES exist, but it is NOT in this wave's file ownership (`daemon/api.py` is explicitly excluded). The accessor it needs is `daemon.tools.memoir_knob.memoir_knob_state()` — phase 6 or 7 wires it. |
| The tool bodies, the category registration, the `tool_help` embedding | phase 4 | `daemon/tools/memoir.py` is a skeleton; the discoverability text is owned by `memoir_knob.memoir_tool_help()`. |
| The retrieval service, the briefing builder, the embedding worker | phases 2/3/5 | skeletons only. |
| The episode repository surface | phase 2 | phase 1's repository carries the PERSONA lane only, as instructed. |
| The persona READ path used by the briefing | phase 5 | phase 1 ships the persona lane (transitions + the human-lane guard + the seed). |
| The `tsv`/GIN real-PG legs, the W8 induction, the boot smoke | phase 7 | phase 1 ships the ensure statements and the `.sql`; the real-PG proofs are phase 7's gate. |
| The pack registration in `.agents/tester/PACKS.md` | phase 7 | explicitly excluded from this wave. |

## 5. New test files (implied by the packs)

The four packs need pytest bodies. The pack FILES are this wave's
declared ownership; the pytest files below are NEW and are named so they
cannot collide with the later waves' files (phase 4 owns
`tests/unit/test_memoir_registration.py`, phase 2/3/5/6 own their own):

* `tests/unit/test_memoir_knob_truth_table.py` (phase 0 pack)
* `tests/unit/test_memoir_schema_contract.py` (phase 1 pack)
* `tests/unit/test_memoir_persona_seed.py` (phase 1 pack)
* `tests/unit/test_memoir_persona_lane.py` (phase 1 pack)

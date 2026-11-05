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

## 3.2 Phase 1 findings (T1.1–T1.6)

| # | Finding | Handling |
|---|---|---|
| P1-1 | **The two-argument `to_tsvector` form is PROVEN on PG 16.15** (this host): the `ALTER TABLE ... GENERATED ALWAYS AS (to_tsvector('english', coalesce(title,'') \|\| ' ' \|\| body)) STORED` form applies, populates on INSERT and is matched by `plainto_tsquery('english', ...)`. The ONE-argument form is rejected by PostgreSQL with exactly *"generation expression is not immutable"* — the AM-8 RED condition, observed rather than cited. | Both legs are pinned in `test/packs/memoir_schema_pg_test.sh` (`test_the_tsv_column_is_created_by_the_ensure_and_populates`, `test_the_one_argument_tsv_form_is_rejected_by_pg`). |
| P1-2 | **The W8 blast-radius proof is done MECHANICALLY here, not with the frozen-binary boot drill.** The plan's AC3 folds W8 (a scratch frozen-binary boot with one deliberately invalid memoir statement) into phase 1, and adds "the same legs live in phase7 D7.6". | The phase-1 leg proves the SAME blast radius against the SHIPPED statement list (extracted from `daemon/manager.py` via `ast`, not copied): a sentinel statement from "another lane" executed FIRST in one transaction, the memoir statement failing second ⇒ the sentinel's table is ABSENT afterwards (the collateral rollback, proved rather than claimed). The frozen-binary boot drill (W8 legs (i)–(iii) with the live fence) is NOT run in this wave: it is a boot smoke, and the standing critical notes make a boot smoke the most dangerous operation on this host. **DISCLOSED as a deliberate scope reduction** — phase 7 owns the frozen-binary drill, per the plan's own "the same legs live in phase7 D7.6". |
| P1-3 | The manager carries TWO config objects: `self.config` is the daemon `Config` (the one with the `memoir:` section) and `self._ensemble_config` is the DATABASE-BACKEND config (`daemon/ensemble_config.py` — it has `is_postgres` and no `memoir` section). | The boot-time knob line passes `self.config`. Passing the wrong object would have made the resolver's "configuration tier is unreadable" branch fire ⇒ ERROR ⇒ DISABLED — a fail-closed misfire. |
| P1-4 | The house migration test `tests/migration/test_attestation_migration.py::test_no_boolean_int_literal_default` forbids `BOOLEAN ... DEFAULT 0` in shipped migrations (PG rejects it). | This lane's `.sql` declares `is_keepsake BOOLEAN NOT NULL DEFAULT false` and the model uses `server_default=text("false")` — the canonical spelling. The test is **BASE-RED** on this tree for four PRE-EXISTING migrations (20260915_120000, 20261025_122631, 20261025_122700, 20261031_120002) — none of them this lane's. |
| P1-5 | `sqlalchemy` exports the float type as `REAL`, not `Real` (the model import was corrected at build time). | Recorded so a later reader does not "fix" it back. |
| P1-6 | `tests/migration/` prints a `HARD ABORT: PG scratch fixture resolved a live-shaped database` on this host (from `test_secrets_audit_context_parity.py` — a PRE-EXISTING file). | Pre-existing / host-state, not lane-attributable. |
| P1-7 | The house PG pack wrapper `scripts/run_tests_scrubbed.sh` hardcodes `exec .venv/bin/pytest` — and a sibling worktree has no `.venv` of its own. | The PG pack uses the wrapper when `.venv/bin/pytest` exists (the repo case) and otherwise falls back to the SAME scrub performed PER VARIABLE (`env -u POSTGRES_DB -u POSTGRES_USER …` — never the `env -u POSTGRES_*` glob form, which is a silent no-op). The pack creates and DROPS its own throwaway database and declares it through `PG_TEST_ALLOWED_DBS` (the house scratch fence, exact-name membership). |

## 3.3 The persona seed's byte identity

`MEMOIR_PERSONA_SEED` is byte-equal to the fenced block of `design.md` §11
(616 bytes UTF-8 / 614 characters — the em-dash is three bytes). The pin
extracts the block from the design document at test time and compares the
BYTES, so a paraphrase, a re-wrap or a re-indent is a RED.

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

## 4.5 INCIDENT — an unintended PRODUCTION-DB boot (2026-11-05 ~11:05Z)

**Recorded here because it happened on this lane and the next wave must
know about it. It is NOT a lane-code defect — it is a HARNESS mistake in
a LOCAL verification probe, and it is a NEW sink in the standing
"env-poison family".**

**What happened.** A local verification script built an
``InstanceManager`` against a THROWAWAY Postgres database on
localhost:5432. ``daemon/repositories/factory.py::create_postgres_engine``
lets the AMBIENT ``POSTGRES_*`` ENVIRONMENT VARIABLES OVERRIDE the
passed ``EnsembleConfig`` ("``POSTGRES_*`` environment variables override
file values" — its own docstring). The agent shell's ambient env points
at the LIVE database (``POSTGRES_HOST=10.44.0.2``,
``POSTGRES_DB=ensemble_prod``). The manager therefore booted against
**PRODUCTION**, not against the scratch database.

**Two effects, both observed on the live DB afterwards:**

1. **The hard-reboot boot recovery pruned the OLDER COMPLETED task
   rows** — its own log lines read "about to DELETE 718 task row(s)".
   The ``task`` table went from ~722 rows to 4 (2 completed + the live
   daemon's 2 RUNNING rows). The IN-FLIGHT rows were preserved (the
   recovery's ``preserve_in_flight`` predicate worked as designed), and
   the ``message_queue`` audit trail SURVIVED (718 completed + 2
   processing + 1 failed; the first id named in the recovery's own
   "doomed" list is still present, so that delete did not commit).
2. **The lane's memoir schema landed in PRODUCTION EARLY** — the four
   tables, the ``tsv`` generated column, the GIN index and the seeded
   persona row (agent ``ari``, version 1, ``approved``, ``source='seed'``)
   now exist in ``ensemble_prod``, because the manager's ``create_all``
   ran with the lane's models registered.

**Live impact: NONE OBSERVED.** At the time of writing: ``/livez`` →
alive (uptime continuous, v0.28.11, no restart), ``/readyz`` → READY
with every component true and ``reasons: []``, 2 inflight turns, and
``queue_freshness`` measured at 0.3 s. The four memoir tables are
**inert** on the live binary (v0.28.11 carries no memoir code — the
lane is unpromoted), so they are the house's own "additive, inert on
rollback" class (the board-tables / ``notify_audit`` precedent).

**NOT DONE (deliberately).** The memoir tables were **not** dropped: a
``DROP TABLE`` against production is a destructive write for zero
functional gain (they hold no live data), and the house posture is to
leave additive, inert schema in place. **The decision to keep or remove
them is the USER's** — flagging it here for the morning.

**The lesson for every later wave (and every lane on this host).** A
manager/engine construction in an agent shell INHERITS the ambient
``POSTGRES_*`` — so the env scrub must happen BEFORE the engine is
built, and it must be PER VARIABLE (``env -u POSTGRES_DB -u
POSTGRES_USER …``; the ``env -u POSTGRES_*`` glob form is a silent
no-op). The standing lane rule (scrub per variable) was followed for the
PACKS (they use ``PG_TEST_*`` and scrub ``POSTGRES_*``), but the local
boot probe did NOT scrub — that is the gap. This is a NEW sink in the
env-poison family: occ#7 leaked into a BOOT, F7 leaked into FILES via an
env-dump capture, and this one leaked a whole DATABASE SELECT/DELETE
cycle through the engine factory's env override.

## 5. New test files (implied by the packs)
The four packs need pytest bodies. The pack FILES are this wave's
declared ownership; the pytest files below are NEW and are named so they
cannot collide with the later waves' files (phase 4 owns
`tests/unit/test_memoir_registration.py`, phase 2/3/5/6 own their own):

* `tests/unit/test_memoir_knob_truth_table.py` (phase 0 pack)
* `tests/unit/test_memoir_schema_contract.py` (phase 1 pack)
* `tests/unit/test_memoir_persona_seed.py` (phase 1 pack)
* `tests/unit/test_memoir_persona_lane.py` (phase 1 pack)

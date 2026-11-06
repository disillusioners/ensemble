# G5 — Lane Pack Set Re-Run + Mutation Matrix Sample + Ceremony Confirmations
## Memoir Store v1 Phase C · 2026-11-06 · gate worker 4 of 5

- **Tree:** `/home/nea/ensemble-src-wt-memoir-store`, branch `feature/ari-memoir-store`
- **HEAD at arrival:** `9e32950d7` (== expected G3 last commit; porcelain 0 — no delta)
- **HEAD at close:** `d5630ec78` (one G5 commit: the mutation-matrix driver's digest refresh; see Commits)
- **Venv:** worktree `.venv` (Python 3.14.7, pytest 9.0.2) — every pytest invocation ran through the wrapper or the worktree interpreter; `daemon.__file__` proven worktree-resolved before the FC9 leg (import-shadow trap avoided)
- **Scratch PG:** dedicated G5 cluster, PostgreSQL 16.15, TCP `127.0.0.1:17609` (ss-verified free at pick), trust auth, unix sockets confined to the scratch dir, scratch DB `memoir_g5_scratch`; DROP + pg_ctl stop + port-freed at teardown
- **Fences:** inline `POSTGRES_DB/USER/HOST/PORT` + `ENSEMBLE_SELF_ENV=dev` on every DB-touching command; per-variable env scrubbing only (`env -u POSTGRES_*` never used); pre-flight `SELECT current_database()` target assertions in every PG pack + the FC9 fence; zero writes to `/home/nea/agents-ensemble/**` or `/home/nea/ensemble-src/**`; live fence read-only checks held everywhere (state.json sha `9057ffe8…` PRE==POST; `current -> releases/v0.28.13`; :9797 untouched, nothing killed)
- **Wall-clock:** ~02:38Z → ~03:27Z (~49 min)

**VERDICT: PASS.** Leg A all 20 packs green at expected counts (zero drift); Leg B 9/9 legs RED-proven with sha-proved byte-identical restores (no stale-pin finding — both re-pointed registration pins went RED under their documented plants); Legs C/D/E all confirm; FC9 numbers exactly reproduce the recorded result with both bounds PASS.

---

## LEG A — full lane pack set re-run (20/20 PASS)

All packs run via their `test/packs/*.sh` wrappers (never the PACKS.md inline pytest forms). Outer `timeout 300` (900 s documented class for the two boot packs); PG packs serial against the G5 scratch cluster. Pack-internal `EXPECTED_PASSED_N` asserted where present (admission 25, briefing 17, briefing_seam 11, budget 10, …).

| # | Pack | Nodes / legs | Expected | Verdict |
|---|------|--------------|----------|---------|
| 1 | memoir_knob_unit_test | 40 passed | 40 | ✅ PASS |
| 2 | memoir_schema_unit_test | 18 passed | 18 | ✅ PASS |
| 3 | memoir_schema_pg_test (PG) | 12 passed | 12 | ✅ PASS |
| 4 | memoir_seed_unit_test | 8 passed | 8 | ✅ PASS |
| 5 | memoir_persona_lane_unit_test | 14 passed | 14 | ✅ PASS |
| 6 | memoir_retrieval_unit_test | 32 passed | 32 | ✅ PASS |
| 7 | memoir_retrieval_pg_test (PG) | 12 passed | 12 | ✅ PASS |
| 8 | memoir_admission_unit_test | 25 passed | 25 | ✅ PASS |
| 9 | memoir_budget_unit_test | 10 passed | 10 | ✅ PASS |
| 10 | memoir_write_purity_unit_test | 14 passed | 14 | ✅ PASS |
| 11 | memoir_embedding_worker_unit_test | 40 passed | 40 | ✅ PASS |
| 12 | memoir_embedding_degradation_test | 8 passed | 8 | ✅ PASS |
| 13 | memoir_briefing_unit_test | 17 passed | 17 | ✅ PASS |
| 14 | memoir_briefing_seam_test | 11 passed | 11 | ✅ PASS |
| 15 | memoir_tools_unit_test | 45 passed (+ baseline + 5 in-pack mutation legs proven, 74 s) | 45 | ✅ PASS |
| 16 | memoir_inner_soul_redirect_test | script harness: baseline green + 3 mutation legs proven (37 s) | harness | ✅ PASS |
| 17 | memoir_mutation_matrix_test | driver: 7 RED/GREEN legs, every restore byte-identical, porcelain clean | driver | ✅ PASS |
| 18 | memoir_pg_corpus_test (PG) | 13 passed | 13 | ✅ PASS |
| 19 | memoir_w8_boot_abort_test | 19 PASS legs (A1-A5/B/C/C2), live fence PRE==MID==POST | gate legs | ✅ PASS (bins found at `/tmp/g1-w8/bins` — no git-log-empty citation needed) |
| 20 | memoir_boot_smoke_test | 36/36 PASS legs — RE-RUN at this tree, not cited (`MEMOIR_G3_BIN=dist/ensemble-prod`, `MEMOIR_G3_EXPECTED_SHA=8a8c227e90166bd0874a75da95b430e012925fd40a471d6ee34b3705d3b93f79` — binary intact, sha matched P0) | 36 | ✅ PASS |

**Drift:** NONE. Every count matches the known-current counts list (the embedding_worker 40 includes G-gate's N-1 belt pin `test_the_python_belt_skips_terminal_rows_the_sql_did_not_exclude` — present and green).

**Side effect (committed):** the matrix driver refreshes `mutation-matrix-digest.json` on every run; the refresh (embedding_worker leg 39→40 reflecting the N-1 pin, plus the new run digest `2e04948a…`) is recorded in the G5 commit.

---

## LEG B — mutation matrix sample: 9/9 legs RED-proven

Method per leg: green-state baseline → PLANT (exact documented shape; always planted from `git show HEAD:<file>` bytes) → guard RED at the EXACT documented red node → RESTORE from `git show HEAD:<file>` → sha256(file) == sha256(git show HEAD:<file>) PROVED → guard GREEN. This gate worker was the only process mutating the tree (serial). Logs: `/tmp/memoir-g5-logs/legb/`.

| # | Leg | Guard | Plant (planted-sha16 → restored-head-sha16) | RED node | RED counts | Restore proof | GREEN |
|---|-----|-------|---------------------------------------------|----------|------------|---------------|-------|
| 1 | k-slice-removed | test_memoir_retrieval_unit.py | `02f2d7c1011a7d0a` → `63a6b65a6d64086f` | `test_k_caps_the_shipped_episode_count_on_a_larger_corpus` | 1F/31P | sha match ✅ | 32 passed |
| 2 | admission-after-insert | test_memoir_write_purity_unit.py | `d932422d48c22bf1` → `293aa605496ac003` | `test_the_admission_reads_precede_the_insert_in_the_source` | 2F/12P | sha match ✅ | 14 passed |
| 3 | stall-warning-removed | test_memoir_embedding_worker_unit.py | `610b26d559dd8f19` → `1f4771fe4caf7a81` | `test_a_starved_window_warns_after_n_stalled_ticks` | 1F/39P | sha match ✅ | 40 passed |
| 4 | v1-recall-map-entry-deleted | test_memoir_tools_unit.py | `d9386c0a17d10cf1` → `429707c929e9402b` | `TestTheRefusalMatrix::test_a_blank_recall_query_refuses_with_query_required` | 1F/44P | sha match ✅ | 45 passed |
| 5 | knob-garbage-repaired | test_memoir_knob_truth_table.py | `02ca420eeb0d4a33` → `2e906f9e093d6fff` | `test_indeterminate_value_fails_closed` (+ companion `test_indeterminate_value_never_raises`) | 2F/38P | sha match ✅ | 40 passed |
| 6 | non-ari-redirect-widened (script-kind) | memoir_inner_soul_redirect_test.sh | `afa25acf2277365b` → `22cf3adef49cd77d` | `RED-ID: non-ari-byte-identity:compound-mixed` (pack FAIL rc=1) | pack-level RED | sha match ✅ | PASS (baseline + 3 legs, 38 s) |
| 7 | keepsake-template-drifted | test_memoir_briefing_unit.py | `8f6cac4c88c21d8a` → `1009492209606dcb` | `test_keepsake_line_template_exact_bytes` | 2F/15P | sha match ✅ | 17 passed |
| 8 | GATE-ORDER pin | test_memoir_registration.py | `ebb7f1602b071444` → `429707c929e9402b` | `TestTheRegistrationWiring::test_the_gate_order_is_auth_first_in_source` | 1F/19P | sha match ✅ | 20 passed |
| 9 | REGISTRATION-FORM pin | test_memoir_registration.py | `4a94b8df12fd58d2` → `429707c929e9402b` | `TestTheRegistrationWiring::test_the_body_gate_is_present_in_source` | 2F/18P | sha match ✅ | 20 passed |

**Plant shapes:** legs 1-7 re-planted the exact find/replace strings documented in `test/packs/memoir_mutation_matrix_test.py` MUTATIONS (the digest labels' sources); leg 8 swapped the two auth/knob `if` literals inside the `_gate` helper span of `daemon/tools/memoir.py` (flipping `auth_idx < knob_idx`); leg 9 re-formed one delegation site to the stale call shape (`_gate(label ,`) dropping the pinned count 5→4 (the count pin AND the gate-order pin's delegation census both caught it).

**Stale-pin finding: NONE.** Both re-pointed pins (legs 8/9) were GREEN in baseline state (20/20) and went RED at exactly their documented nodes under their documented plants — the pins are live and mutation-meaningful.

**Runner notes (disclosed, both self-caught and re-run):** leg 5's first attempt targeted the wrong guard path (`test_memoir_knob_unit.py` — the correct pack file is `test_memoir_knob_truth_table.py`); the plant was cleanly restored before the corrected re-run. Leg 6's first attempt ran while the digest refresh was uncommitted — the pack's own tree-fence correctly refused (`unexpected changed files: [mutation-matrix-digest.json]`); after committing the digest (the G5 commit) the leg ran clean both ways. No pin was weakened and no guard was edited.

---

## LEG C — V-1 ceremony acceptance (CONFIRMED)

- **(a) memoir tools pack GREEN:** Leg A row 15 — `memoir_tools_unit_test` PASS (45 passed), including the V-1 test `test_the_import_time_assertion_freezes_nineteen_codes` (tests/unit/test_memoir_tools_unit.py:171).
- **(b) grep census — `len(MEMOIR_CODES) == 19` as the ONLY count-bearing literal, at exactly the expected sites:**
  - `daemon/tools/memoir.py:235` — `assert len(MEMOIR_CODES) == 19, (` (the import-time fail-loud assertion; its f-string at :237 reports `got {len(MEMOIR_CODES)}`, not a literal)
  - `tests/unit/test_memoir_tools_unit.py:172` — `assert len(MEMOIR_CODES) == 19`, inside the test NAMED `test_the_import_time_assertion_freezes_nineteen_codes` (:171)
  - All other `MEMOIR_CODES` references are NON-count-bearing: the docstring (:46), the frozenset definition (:211), the membership check (:260-262), the `__all__` export (:1214), the test import (:61).
- **Not run, per the ceremony note:** NO MT-01 re-run and NO prompt re-stamp — confirmed not executed.

## LEG D — FC9 prompt-budget re-measurement (CONFIRMED, both bounds PASS)

Re-run with the adapted fence (`/tmp/memoir-g5-fc9/fc9_measure.sh` + harness copied from `/tmp/memoir-fc9/`), the recorded hardcoded `PY=/home/nea/ensemble-src/.venv` pointer REPLACED with the worktree venv, **import-shadow proof executed first**: `daemon.__file__ = /home/nea/ensemble-src-wt-memoir-store/daemon/__init__.py` (PASS) before any output was trusted. Scratch DB `memoir_g5_fc9_1550150` on the G5 cluster with the `current_database()` target assertion; BEFORE leg swapped the two ari files via `git show d9b36ad8a^:` bytes and restored from HEAD bytes sha-proved; live fence read-only PRE==POST.

House formula D4.14 (tiktoken cl100k_base, `estimate_tokens(system) + Σ estimate_tokens(json.dumps(convert_to_openai_tool(t), sort_keys=True, separators=(",",":")))`):

| Leg | Resolved tools | memoir tools | system prompt | total_compact |
|-----|----------------|--------------|---------------|---------------|
| BEFORE (pre-ceremony ari files) | 115 | [] | 27,427 | **48,170** |
| AFTER (ceremony tree) | 120 | pin/recall/timeline/unpin/write (5) | 28,544 | **50,266** |

- **growth_tokens = 2,096** — bound: ≤ max(10_000, 0.25 × 48,170 = 12,042.5) → **PASS**
- **growth_pct = 4.35%** — bound: ≤ 0.25 → **PASS**
- Cross-check vs the recorded result (implementation-notes.md:40: 48,170 → 50,266, growth 2,096 = 4.35%): **EXACT reproduction, no marginal delta**.

## LEG E — AC7 census remainders (CONFIRMED)

- **(a) grep-derived registration census (D7.7) — every hit 18-flavored:**
  - `grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ daemon/`: six registration packs pin `== 18` (test_memoir_registration.py:146, test_notify_user_registration.py:188, test_wedge_capture_registration.py:186, test_self_alarm_registration.py:224, test_board_registration.py:228, test_metrics_read_registration.py:227, test_instance_ops_registration.py:215 — seven packs total counting all), the memoir pack's AST-derived set pin asserts `len(derived) == 18` with `PRIVILEGED_TOOL_CATEGORIES == derived`; the only `== 17` hits are the NEGATIVE pin (`test_memoir_registration.py:354` — asserts the stale 17 literal is ABSENT) and the MT-01 lockstep mutation REPLACE literal (:386, the RED-direction target — correct per D4.12, never both-to-18). The registry prose comments (`_tool_registry.py:340/373/378/385`) are the grep recipe, not counts.
  - **`"memoir"` is LAST in ari's allow array:** `agents/ari/meta.json` `tools.allow` = 34 entries, position 33 (0-based), `allow[-1] == "memoir"` — quoted evidence line: `allow len: 34 | last: memoir | memoir present: True | position: 33`; the pins enforcing it (`test_the_memoir_entry_is_the_last_one_in_the_literal` :156, the `allow[-1] == CATEGORY` assertion :319, `test_the_trailing_set_pin_carries_memoir_last` :337) are green in the registration pack run (Leg B greens).
- **(b) prompt-artifact baseline:** `PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256` (rc=0, worktree venv, env-fenced, `--check --tree` form); the baseline file is 161 lines.

---

## Commits

| Commit | Contents |
|--------|----------|
| `d5630ec78` | `test(memoir): G5 pack-set re-run + mutation sample + ceremony confirmations` — path-scoped to `.agents/shared/planning/ari-memoir-store/mutation-matrix-digest.json` (the driver's own recorded-output refresh from this gate's Leg A run: embedding_worker leg 39→40 reflecting the N-1 pin; run digest `2e04948a…`) |
| RESULTS doc | this file (`.agents/tester/RESULTS/2026-11-05-memoir-g5-packs-mutations.md`) + the PACKS.md last-run cells — committed under the same message, path-scoped |

## Pack registration updates (PACKS.md memoir block, keep-both newest-first)

Last-run cells updated for the packs whose table rows carry status; all rows now carry this gate's run (2026-11-06, this RESULTS doc).

## Teardown

Scratch DBs dropped (`memoir_g5_scratch`, FC9's `memoir_g5_fc9_1550150` + role), scratch PG cluster stopped (`pg_ctl stop -m fast`), port 17609 re-verified free, scratch roots removed; worktree porcelain clean post-commits; live fence untouched throughout (`current -> releases/v0.28.13`, state.json sha `9057ffe8…` byte-identical PRE==POST at every checkpoint; :9797 never contacted beyond the read-only livez probes inside the w8/boot-smoke pack fences).

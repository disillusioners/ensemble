# Memoir Store v1 — Phase C Functional Gates (aggregate)

Date: 2026-11-06 (gate window 2026-11-05T22:4xZ → 2026-11-06T07:0xZ)
Lane: `feature/ari-memoir-store` · worktree `/home/nea/ensemble-src-wt-memoir-store`
Gate anchor: **HEAD `0beae94e2`** (dispatch expected `f054a08bc`; G1–G4 ran across its successors `2a9a6f1b9 → 3a0ddf551 → 9e32950d7 → 6a99391eb → 9dda08ddc → 0beae94e2`, all ancestors; every gate re-verified actual HEAD on arrival and reported deltas)
Base for Tier-2: `1f6898ae2889ff002b4656486f6be3ee1048b649`
Binding spec: `.agents/shared/planning/ari-memoir-store/` — architecture-recommendation.md §12.4 (W8.1–W8.5), phase1 AC3, phase7-plan.md (D7.1–D7.9, AC1–AC8), mutation-matrix-digest.json
Dispatch: four gates (G1–G4) + standard lane verification + ensure.md Core.

## VERDICT SUMMARY

| Gate | Verdict | One-line evidence |
|---|---|---|
| PREP recon | PASS | HEAD f054a08bc confirmed; porcelain clean; no active foreign process; PG 16.15 parity; venv resolves `daemon.__file__` to the worktree |
| **G1 — W8 boot-abort induction** | **PASS-WITH-NOTES** | rc=3 boot abort with the memoir `tsv` statement NAMED; ensure-borne schema rolled back (`tsv`=0, GIN=0); inherited sentinels ABSENT with non-vacuity control (clean boot ⇒ PRESENT); 3 structural pins + boot-level try/except flip mutation-verified; live fence triple intact |
| **G2 — real-PG corpus** | **PASS** | 10/10 D7.6 items on real PG 16.15 (`server_version_num=160015`); corrected `ts_rank_cd` order + reversed-order mutation 4F→12/12 GREEN; two-arg immutable `to_tsvector` + one-arg mutation 3F with PG's exact `generation expression is not immutable` → 12/12 GREEN; GIN `Bitmap Index Scan` on populated corpus; DR-1 zones (4k zone-cap refusal / 25,398-char in-full ship / >48k withhold with size-caused control); RRF+recency+keepsake exemption; access-stats explicit-only both directions |
| **G3 — boot smoke** | **PASS 36/36** | banner + `/livez` + `/readyz` all-4-true; 4 memoir tables + `tsv GENERATED ALWAYS` + GIN on scratch PG; worker census BOTH knob states (ON: exactly one `worker started`; OFF: zero, `not started: memoir knob is OFF`); embedding fence dead-local → 5 ticks `errors=1`, no block/crash, `slow_mode` latch engaged, **AM-22 once-flag ABSENT**; briefing-at-ari-turn-1 via MOCK-LLM turn — real API spawn, turn `completed`, consumer flag write fired (non-vacuity for the absence leg); live fence PRE==MID==POST; SIGTERM-only teardown, ports freed, ps-census clean |
| **G5 — pack set + mutation sample** | **PASS** | 20/20 packs green at registered counts (no citation-only packs); 9/9 mutation legs RED-proven with sha-proved byte-identical restores (7 digest legs + gate-order pin + registration-form pin — no stale-pin finding); V-1 census exact; FC9 exact reproduction (4.35%, both bounds PASS); AC7 census (18-flavored pins, `memoir` last in ari allow, baseline 161/161) |
| **G4 — Tier-2 whole-tree** | **PASS-WITH-NOTES** (after re-verification) | Initial run FAIL: 3 deterministic net-new (companion-surface F-1 class — `_STABLE_ID_SUPPORTED_KINDS` four-vs-five + `tool-categories.json` not regenerated). Fixed test-side/machine-output (pin→`exactly_five`; artifact regenerated via house generator 57→58) ⇒ **narrow re-verification: deterministic net-new 0**, all 15 diff-node ids GREEN in isolation, lockstep families green. Net table: pre-existing 521 / flaky 11 (routed) / genuine fixed 0. READ the `# RE-VERIFICATION` section of the G4 doc (combined-document pattern). |
| ensure.md Core | **PASS** | `concurrency_atomic_unit_test` 94P/74S/5F — the 5F are `tests/test_instance_metadata_atomic.py` ×5, BASE-IDENTICAL (17P+5F both sides in an executed detached-base A/B) and ALREADY QUARANTINED (QUARANTINE.md 2026-10-22 Phase-C row, BASE-LEG A/B PROVEN) ⇒ quarantine rule applies; event-loop thread-identity spies all green; `dev.sh:155 --timeout-graceful-shutdown 10` present; all 9 production call sites of the three converted async functions await |

**Overall: READY TO STAGE (gates PASS).** No production-side red, no unadjudicated delta, no fence violation.

## G4 net-new table (as re-derived after fixes)

| Class | Count | Disposition |
|---|---|---|
| Deterministic net-new (after narrow re-verification) | **0** | The 3 initial reds were lane-side companion surfaces, fixed (below) |
| Pre-existing (byte-identical both legs) | 521 | Includes the inner_soul 16 — digest `9fea2dcbd506a5139eaae14091a872a7f4f000dde171aa74b09e59f7cbec2e52` byte-identical both sides |
| Flaky (xdist/shard, chunk-context) | 11 | Listed in the G4 doc; not lane-added; routed (see residue) |
| Genuine fixed (`BASE_FAILED − HEAD_FAILED`, real) | 0 | The 6 line-level "fixed" were chunk-context flakes (0 genuine) |
| Collect delta | +305 / −0 | All 15 memoir test files |

Companion-surface fixes (test-side / machine output only, no production edits): `tests/integration/test_lcancheck_family_separation.py` pin `four→five` (+ lockstep check: only this file asserts the contract); `.agents/shared/metrics/tool-categories.json` regenerated via the house generator (57→58, `memoir` present; seal check: no `SEALED_SHA_BASELINES` reference → nothing re-pinned; `VOCABULARY_VERSION` stays 3 — bump 3→4 ROUTED as a lane-side ceremony per freeze rule P-1).

## Ceremony acceptance confirmations

- **V-1 (refusal vocabulary)**: `MEMOIR_CODES` = 19 codes; `len(MEMOIR_CODES) == 19` appears ONLY at `daemon/tools/memoir.py:235` (assert) and `tests/unit/test_memoir_tools_unit.py:172` (+ test name `test_the_import_time_assertion_freezes_nineteen_codes`); no other count-bearing literal; `memoir_query_required` present as the 19th code (memoir.py:207). Per the ceremony note: **NO MT-01 re-run, NO prompt re-stamp owed** — neither performed. (Cosmetic drift vs the note's line refs `~201/~171` — actual `:235/:172`; the note's "only count-bearing literal" claim holds.)
- **FC9 (prompt budget)**: BEFORE 48,170 → AFTER 50,266; growth 2,096 tokens = **4.35%**; both bounds PASS (`growth_tokens ≤ max(10,000, 0.25×48,170)=12,042.5`; `growth_pct ≤ 0.25`). Exact reproduction of the recorded result; import-shadow proof PASS (`daemon.__file__` under the worktree).
- **Prompt baseline**: `PROMPT-BASELINE: OK — 161/161` (twice: prep at f054a08bc and G5 at the gate anchor).
- **AC7 census**: all `len(PRIVILEGED_TOOL_CATEGORIES)` pins 18-flavored; `"memoir"` is LAST in ari's allow array (34 entries, index 33).
- **Guardrail sweep (D7.9)**: 94-file change set; forbidden paths absent (`daemon/loader.py`, non-ari `agents/*/meta.json`, `pyproject.toml`, `uv.lock`, `plugins/**`, `frontend/**`); **no new external dependency**; `config.yaml` touched only by the memoir knob block (disclosed, not forbidden).
- **Mutation matrix**: 7/7 digest legs independently re-proved by G5 + G1's 3 structural pins + G2's 2 PG legs + gate-order + registration-form pins = **15+ independent RED→GREEN proofs across the gate**, every restore sha-matched to `git show HEAD:<file>`.

## Verification boundaries (what was NOT exercised — disclosed)

1. **Turn-1 leg**: real assembly + real consumer write + real spawn exercised via a MOCK-LLM turn; NOT exercised: real-provider token accounting, the graph path's discarded persistent half, persona-seeded content (persona-missing path rendered).
2. **G4 after the companion fixes**: narrow re-verification (house precedent, v0.28.9 F-1) — NOT a full-gate re-run; the fix commits touch only a pin test + a generated artifact.
3. **G1 observation-3 wording**: the 4 memoir TABLES persist after the aborted boot (`create_all` commits BEFORE the ensure — `manager.py:990` vs `:1012`); the ensure-borne schema (`tsv` + GIN) is rolled back exactly as the binding W8.2-3 requires. The dispatch's "all 4 tables absent" phrasing is unattainable by construction; the stronger invariant (silent full rollback of the ensure transaction, PG aborts ⇒ COMMIT behaves as ROLLBACK) is what was proved.
4. **Tier-2 advisory**: breached ~115 min vs 90 min — reported loudly, gate not stopped (cause: dual-side 11-shard corpus + the fix cycle).

## Routed residue / follow-ups (not blocking; owner lanes)

1. **11 xdist-shard flakes** (chunk-context class; 1/3 or 0/3 in isolation, red in shards) — listed in the G4 doc; no house flake-ledger file exists → routed to a flaky-test-management sweep (do NOT quarantine without a 3× re-observation per house rule).
2. **`VOCABULARY_VERSION` 3→4** (from the tool-categories regeneration) — lane-side ceremony per freeze rule P-1; artifact regenerated, constant not bumped.
3. **PACKS.md drift (this lane's block)** — **FIXED at aggregate time**: the 5 invocation rows that pointed at non-existent `tests/unit/test_memoir_*_unit.py` now point at their `test/packs/*.sh` wrappers (knob→`test_memoir_knob_truth_table.py`, schema→`test_memoir_schema_contract.py`, seed→`test_memoir_persona_seed.py`, persona_lane→`test_memoir_persona_lane.py`, briefing_seam→`test_memoir_briefing_seam.py`); stale result-column counts annotated with the current values; the G4 block header now carries the re-verification verdict.
4. **Stage-readiness note (giter lane)**: `frontend/dist/frontend/browser` absent → `stage.sh` will refuse until `bash scripts/upgrade/_build_frontend.sh` runs (binary-only gates unaffected).
5. **FC9 harness** `/tmp/memoir-fc9/fc9_measure.sh` hardcodes the MAIN checkout venv — import-shadow hazard; G5 proved `daemon.__file__` under the worktree before trusting output. Harness-only.
6. **Pack wrapper quirk**: `concurrency_atomic_unit_test.sh` exits bare on pytest failure (`set -euo pipefail`, no `RESULT: FAIL` echo) — pre-existing, out of scope.
7. **Quarantine note**: `test_instance_metadata_atomic.py` ×5 remains QUARANTINED (2026-10-22 Phase-C row, BASE-LEG A/B PROVEN; reconfirmed base-identical this gate).

## Envelope compliance

- Zero writes to `/home/nea/agents-ensemble/**` across the whole gate; `releases/state.json` sha `9057ffe8…` PRE==POST; `current -> releases/v0.28.13` unchanged. **Disclosed external event**: the USER promoted v0.28.13 mid-G3 (journal-attributed `commit` 02:23:13Z / `sweep` 02:23:35Z); G3's fence distinguished our null delta structurally (MID==POST across boot two) and re-ran clean.
- Zero writes to `/home/nea/ensemble-src/**` (main checkout read-only); no stage/promote/rollback/nonce machinery executed; ports 9797/8079/8088 untouched.
- Env fences on every DB-touching command: inline `POSTGRES_*` + `ENSEMBLE_SELF_ENV`; `SELECT current_database()` pre-flight (the G4 scratch guard fail-closed-refused an `ensemble_prod` attempt on BOTH legs); never the glob-unset form; scratch PG 16.15 only (parity asserted); all scratch DBs/clusters/dirs torn down, ports freed.
- `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly on every frozen-binary boot; binaries always COPY, never in place.

## Commits (lane branch)

| sha | purpose |
|---|---|
| `12e432933` | G1 W8 boot-abort induction pack + pins (+ PACKS.md row + RESULTS) |
| `2a9a6f1b9` | G1 W8 raw boot evidence (gitignore-safe .txt copies) |
| `3a0ddf551` | G2 real-PG corpus legs + mutation pins |
| `020231836` | G3 boot smoke pack + RESULTS + PACKS.md registration |
| `1f4736395` | G3 cleanup hardening (final-rm verify + retry, loud-FAIL) |
| `9e32950d7` | G3 RESULTS disclosure |
| `d5630ec78` | G5 digest refresh |
| `6a99391eb` | G5 RESULTS + PACKS.md |
| `9dda08ddc` | G4 tier-2 whole-tree gate + registration |
| `0beae94e2` | G4 re-verification after companion fixes (lcancheck pin + tool-categories.json + RESULTS section) |

**Final tree state**: HEAD `0beae94e2`, porcelain clean; base worktree removed+pruned; scratch resources torn down; foreign `designer-agent/install-audit.jsonl` rescued at `/tmp/designer-install-audit-rescue-g4-20261106.jsonl` and restored.

## Artifacts

- `RESULTS/2026-11-05-memoir-g1-w8-boot-abort.md`
- `RESULTS/2026-11-05-memoir-g2-pg-corpus.md`
- `RESULTS/2026-11-05-memoir-g3-boot-smoke.md`
- `RESULTS/2026-11-05-memoir-g4-tier2-wholetree.md` (read including `# RE-VERIFICATION`)
- `RESULTS/2026-11-05-memoir-g5-packs-mutations.md`
- `RESULTS/2026-11-05-memoir-functional-gates.md` (this doc)
- PACKS.md: lane block + `memoir_w8_boot_abort_test` · `memoir_pg_corpus_test` · `memoir_boot_smoke_test` · `memoir_gate_tier2` (all with invocation + scope + timeout + last-run)

## Scope decision (dispatch review)

Full Tier-2 was run because the diff touches `daemon/manager.py` + `daemon/api.py` — the ensure.md core-seam list makes Tier-2 **mechanical, not a judgment call** (D7.1). All four dispatch gates executed; no scope reduction.

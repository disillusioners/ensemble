# prompt-reload — FUNCTIONAL GATE (GO/NO-GO for staging)

- **Date:** 2026-11-07 (gate window 01:28–04:5x UTC)
- **Tree under gate:** `/home/nea/ensemble-src-wt-prompt-reload` @ `5b34e6857a192af87b1bb80ebedf397b110a7c99` (branch `feature/prompt-reload`, porcelain 0 throughout; verified PRE/POST by independent workers)
- **Contract:** `.agents/shared/planning/prompt-reload/phase4-plan.md` (L1–L9 + M1–M6, AC4.1–4.10) + decisions D19–D27 + council hand-offs (L-d both forms, S10-j live-or-disclose, M6 five pairs)
- **Harness:** `/home/nea/ensemble-src-wt-prompt-reload-harness` re-pointed `711199c8` → detached `5b34e6857` by PREP (KV was stale by one commit); restore-baseline map `/tmp/pr-gate-harness-agents-baseline.sha256` (379 files)
- **Worker instances:** PREP `bd272df6` · census `9ba60144` · unitA `31316f2a` · unitB `7cd11dac` · e2e `47a52113` · mut `de07afb3` · tree `96a727de` (logs: `/tmp/pr-gate-*`, e2e scaffolding `/tmp/pr-gate-e2e/`)

## VERDICT: **GO-WITH-DISCLOSURES** — with ONE named pre-stage condition (2-line test-side pin fold, sites below)

All functional legs L1–L9 + M1–M6 **PASS** (first live execution of this code included). Whole-tree regression: **zero behavior regressions**; net-new = **2 deterministic stale test pins (lane-caused, test-side only) + 1 flake (green 3×)**. The 2 pins are the only blocking-adjacent item and are a mechanical fold with exact house precedent (memoir lane `b73805dc4` bumped the same two pins 5→6 in its own lane).

### Pre-stage condition (routed to the implementer via the leader — NOT folded by the gate, per dispatch)
`VOCABULARY_VERSION` 6→7 bump (commit `9fbb3ad46`) did not ride the two numeric assertion pins:
- `tests/unit/metrics/test_metrics_vocabulary_amendment.py:32` — `assert cm.VOCABULARY_VERSION == 6` → `== 7` (+ docstring "1→2…5→6" narration gains the 6→7 line)
- `tests/unit/metrics/test_metrics_family_adjudication.py:440` — same fold
3× isolation RED at HEAD, passes at base `8eba09c20`; `git diff 8eba09c20 5b34e6857 -- tests/unit/metrics/` is EMPTY (the lane's 14-file pin census missed these two sites). Precedent: memoir lane edited both files on its 5→6 bump.

> **RESOLVED (leader ruling 2026-11-07, option (b)):** the fold was authorized to the tester-gate and executed in the post-ruling rider commit on `feature/prompt-reload` (this same date); both nodes re-verified green.

---

## Leg results

### L1 — Sentinel reload E2E (THE leg; first live run) — **PASS (a–h)**
Live harness daemon (fenced boot: `ENSEMBLE_SELF_ENV=dev`, `DAEMON_PORT=8079`, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned, throwaway PG `ensemble_gate_pr_e2e` dropped after, per-variable scrub, zero :9797 contact), stub LLM on :18079 capturing every system prompt.
- (a) OLD-persona anchor: `soul.md` sha `14b077e7ee09…`, prefix `# Who I Am\n\nI'm Ari — …`
- (b) Turn N completing prompt (122,940 chars, sha `1918e1eb9fd4…`) = FULL OLD persona, sentinel absent; `prompt_reload` confirmed offered in tools.
- (c) Reload via the REAL tool path (stub tool_call → ToolNode → tool): diff + provenance + marker exactly-once; first-call-after-boot outcome **`unproven`** — never `no_change` (B1/L1(g)).
- (d) Sentinel append → outcome **`reloaded`**, `files: 1 changed of 11` naming `soul.md: sha256 14b077e7ee09… → 4e657d590d56…`, pop `2->1`; **22 concurrent /livez + instance probes during the window, all 200** (L1(e)).
- (e) Turn N+1 prompt carries the sentinel (sha `31c7e3032fa7…`); turn N's does not.
- (f) History intact: persisted counts 5→8→11→13 — turn-native deltas only, **zero synthetic reload entries**.
- (g) Restore: byte-exact (`14b077e7` re-verified), baseline map **379/379 OK**, harness porcelain 0.
- (h) L6 re-assert: `PROMPT-BASELINE: OK — 181` (re-asserted again at teardown).

### L2 — Idempotence — **PASS (i–iii)**
Cold `unproven` (live, L1(c)) · warm no-op `no_change` with `manager_instances: 2->2` (entry PRESENT, no pop) + S10-j file-level-freshness wording · `force=true` → `forced_reload`, `2->1` pop, "force=true popped the cached graph anyway".

### L3 — Refusal ladder — **PASS** (unit, 15/15)
All 9 REFUSED codes independently covered (dedicated node each, incl. `db_unavailable` naming "prompt-versions"); FAILED envelope `Error: prompt_reload FAILED — reason=prompt_reload_internal_error:` exact; non-ari notify-exact `ERROR:` shape byte-exact; `not_ari` ABSENT from REFUSED set; uncached → `was_cached=false` proceed. No-history-write on FAILED proven by ad-hoc probe G2 (recorder stub, zero writes) — the pack node's own assertion is name-only (see Disclosures).

### L4 — Live-fence — **PASS** (unit + ad-hoc G1)
Dev-repo accept · foreign refuse · divergence tri-state matrix (matched/false/unknown) with two distinct disclosure lines, never refuses · live-shape releases-containment negative at unit; sandbox-shaped matrix proven by ad-hoc G1 (contained dir passes containment → reaches manifest stage; outside → `prompt_reload_dir_invalid`). Harness env pin `ENSEMBLE_SELF_ENV=dev` asserted at leg start.

### L5 / M1–M6 — Mutation matrix — **PASS, 6/6 meaningful, 10 cycles, all digest-proven**
| Leg | Plant | RED nodes | GREEN |
|---|---|---|---|
| M1 | pop removed (`prompt_refresh.py:969`→`pass`) | 7 failed — pop-seam + L-c/db + bust legs | 42P |
| M2 | decision chain neutralized (always changed-path) | 7 failed — no_change + unproven + tier-c legs | 42P |
| M3 | authorized agent `ari`→`leader` | 14 failed — layer-3 ERROR shape + in-body gate + ladder | 28P |
| M4 | artifact hand-edit (vv 7→8) | exactly `test_artifact_is_generated_from_the_tool_registry` | 9P |
| M5 | `stored_mtimes_unchanged = True` (D26 conjunct) | exactly `test_concurrent_refill_skew_downgrades_to_tier_c` | 42P |
| M6a–e | per-outcome marker token break ×5 | each: `test_marker_on_<outcome>` + token-equality sweep; M6b also `TestGoldenReport::test_golden_shape_bytes` | 20P each |

All 10 RED digests mutually distinct; all GREEN digests empty-FAILED. Post-matrix sanity 99/99; porcelain 0; HEAD unchanged.

### L6 — Pin/census — **PASS at BOTH B3-ordered points**
Baseline snapshot (BEFORE mutation): artifact `tool-categories.json` count 61 @ `vocabulary_version` 7, generator-verified (9/9); `VOCABULARY_VERSION == 7` (`checkpoint_metrics.py:249`); privileged census **20** (`prompt-reload` #20, after `memoir`); ari `tools.allow` **36** (last entry); baseline **181/181** (`PROMPT-BASELINE: OK`, checker `scripts/protection/generate_prompt_artifact_baseline.py`); baseline file sha `0803f07d…` byte-identical. Re-assert AFTER restore: 181 OK + 379/379 shas. Switch-state-independent registration proven via REAL env writes (`"1"`/`"false"` → identical construction).

### L7 — Concurrency/edge/guard-absence — **PASS**
Static: `_release_cached_instance` 0 hits in lane modules (manager.py's own pre-existing site untouched); graph construction 0 (2 `re.compile` benign); closure introspection 0; guard/in-flight flags 0 (AM-7); `instances[` assignment 0 — the ONLY seam is `instances.pop(instance_id, None)` at `:969`. Unit pins (primitive pack): double-pop no-op, guard-absence, forbidden-mechanisms, envelope `manager_instances`. Runtime (live): mid-turn pop survival (completing turn kept OLD persona while the pop happened mid-turn); double-pop live no-op (two back-to-back tool_calls, both `no_change`/`2->2`, no error, follow-up turn answered). Manager delegate 4 lines, AM-12-conform.

### L8 — Repo-probe + hash-bust — **PASS (L-a, L-b, L-c, L-d BOTH forms)**
L-a absent repo → proceed `db_fingerprint=absent` (unit) · L-b raising probe → `db_unavailable` naming the repo, composition never ran (unit + live) · **L-c live**: approved+selected `prompt_versions` row (DB sentinel `PR-GATE-DB-SENTINEL-db7c3f2a`) → **`reloaded_db`** + db_fingerprint `` → `022228202493…` + **REAL pop `2->1`** + **next turn's prompt carries the DB content** (F4 honored AT the reload) · **L-d warm (live)**: mtime preserved ns-exact around sentinel-B → `reloaded` + busted + `hash_divergence=true cache_busted=true` + next turn fresh · **L-d cold-with-entry-present (live)**: daemon restart → cold turn fills cache → mtime-preserving edit → **`unproven` + `cache_busted=true` + `hash_divergence=false`** (D27 branch b exactly) + next turn fresh. The council's both-forms directive is met ON LIVE RUNS, not fixtures alone.

### L9 — Report-contract golden — **PASS**
Marker exactly-once ×5 (`no_change`, `reloaded`, `reloaded_db`, `forced_reload`, `unproven`), ZERO on refusals+FAILED, token == structured outcome; golden byte test (mutation-proven via M6b); S10-a/b/c canonical literals; D24 INFO line frozen order on success+refusal (and FAILED via G2 ad-hoc); INFO enum (6) ≠ marker set (5) pinned both structurally and by sweep. D14 shape confirmed on the REAL L1 render: per-file `sha256 X → Y` rows, changed-count, field orders, tier-c honesty; tier-b `+N/-M`/bytes block + capped headings + db-row lines are fixture-frozen only (golden test), not exercised by this live render — disclosed.

### S10-j — Multi-instance shared-cache edge — **EXECUTED LIVE, contract-shape disclosed**
Reload via instance A pops **only A**; sibling B (same agent) does NOT auto-refresh — B serves its cached persona until its own cold restore or its own `force=true` (proven live: B's forced reload → next turn serves CURRENT disk persona). This is the designed T1.7 per-instance-pop shape; the council's "synthetic sibling refill" premise has NO dedicated unit node (closest: `test_w1_warm_cache_cold_process_is_unproven_tier_c_never_exact`, synthetic cache refill). Negative-expectation finding, not a defect.

> **Leader ruling 2026-11-07: ACCEPTED as the contract.** Per-instance pop is the designed R4/T1.7 self-only scope (leader ruling from the design phase); the council's sibling-auto-refresh premise was its expectation, not a plan requirement. Ari is a singleton front-door session — no live-sibling case exists in practice. The sibling-semantics pin stays on the routed-followups list.

### Whole-tree regression — **PASS-WITH-DISCLOSURES (net-new behavior regressions: 0)**
- Corpus `tests/unit` both legs: HEAD **23,267** nodes (23010P/164F/23E/69S/1x) vs BASE `8eba09c20e2fa…` **23,160** collected / 22,834 executed. HEAD-only files = the 5 lane test files (107 nodes, ALL GREEN); BASE-only 0. Execution delta 433 fully reconciled (326 skipped TOOLS-3 + 107 HEAD-only).
- 13 serial partitions (no xdist — OOM fence); ROOT-3 hit the 600s cap on both legs → split halves, completed.
- **Net-new = 3**: 2 LANE-ATTRIBUTABLE stale `VOCABULARY_VERSION == 6` pins (see pre-stage condition; 3× RED at HEAD, pass at base) + 1 FLAKE (`test_upgrade_journal.py::TestExecutorSpawn::test_real_spawn_env_and_process_group_independence` — green 3× isolated, whole-file green, order-shifted rerun == BASE exactly).
- `.venv/bin/python` FileNotFoundError ERRORs (pinning-test shell-outs) and `maintenancer/memory.md` ERRORs: **identical id-for-id both legs** — worktree-shape artifacts, cancel in the diff.
- Pre-existing reds: 184 nodes / 46 files, byte-identical both legs (largest families: slash_commands_router 26, work_router 17, builtin_mcp_servers 17, configure_builtin_idempotency 15…).
- Baseline at HEAD: `PROMPT-BASELINE: OK — 181`.
- Method disclosure: BASE leg reduced (tester-authorized mid-flight) to the 12 partitions with HEAD failures — TOOLS-3 (green on HEAD) contributes nothing to net-new by definition; disclosed, not silent.
- Gate worktrees KEPT at porcelain 0: `/home/nea/ensemble-src-wt-pr-gate-head`, `/home/nea/ensemble-src-wt-pr-gate-base`.

---

## Disclosures (all non-blocking unless stated)

1. **Pre-stage condition (the one GO-condition):** the 2 VOCAB pin folds (sites above). GO assumes they land before stage (any later whole-tree gate re-reddens until folded — v0.27.11 precedent class).
2. **S10-j designed shape** differs from the review council's sibling-refill premise (see above); routed follow-up: grow a real sibling-semantics pin.
3. **Runbook doc gap:** `docs/runbooks/prompt-reload.md` §"Testing the seam" is prose-only — no fenced invocation block; the KV `prompt-reload.scratch-seam` row + phase4-plan §1 carried the operative fence pins. Lane-owner doc amendment.
4. **Unit-coverage gaps closed AD-HOC (no committed pins):** L4 sandbox matrix (G1), FAILED-path INFO line + explicit no-history-write recorder (G2), kill-switch invalid-spelling warn branch (G3) — all PASS live/in-process, but the packs carry no dedicated nodes; implementer should fold them as pins.
5. **Vacuous-adjacent node:** `test_prompt_reload_refusals.py::TestFailedEnvelopeCode::test_10` claims no-history-write in its NAME only (body has no history assertion; structural-only via history-less stub).
6. **D14 tier-b elements** (`+N/-M`, byte delta block, capped headings, db-row lines) fixture-frozen only — not rendered by the live L1 path (which hit the tier-c/claim-guard honest downgrade on reload #2; file rows + shas DID render).
7. **Env-poison occurrence (contained):** the e2e worker's shell inherited live ambient (`POSTGRES_DB=ensemble_prod`, `SELF_ENV=live`, `PORT=9797`, live OPENAI_*, `RAG_IS_REQUIRED=true`); fenced by expanding the scrub set (`RAG_IS_REQUIRED`, `OPENAI_BASE_URL_BACKUP`, model aliases, `ENSEMBLE_DATA_DIR` pinned to scratch). KB-recorded.
8. **Harness pre-state drift:** harness was at `711199c8` (one commit past the KV-recorded `8f2a99b50` — a stale-docstring fix on this very commission); KV updated by the gate.
9. **Flake:** 1 (`TestExecutorSpawn` env/pgrp test) — order-sensitive under chunk load, base-identical FAILED sets on rerun; not quarantined (single occurrence, isolation-green; watch list).
10. **L1(d) nuance:** reload #2's `persona_diff=unavailable` is the AM-13 claim-guard honest downgrade (file rows with shas still render) — by design.
11. Zero live contact for the whole gate: no :9797 requests, `ensemble_prod` never opened, state.json untouched, no promote contact; all daemon boots on :8079 with throwaway PG (created + DROPped, proofs captured).

## Not executed
Nothing. All contracted legs + hand-offs executed (S10-j live; L-d both forms live; M6 five pairs; whole-tree both legs).

## Follow-ups routed (owner: implementer/leader unless noted)
- VOCAB pin fold (pre-stage condition, 2 lines + docstrings).
- Fold G1/G2/G3 ad-hoc probes as committed pins; fix the test_10 name-vs-body gap.
- Sibling-semantics pin (S10-j designed shape) in the primitive pack.
- Runbook §tester-seam fenced invocation block.
- Tier-b D14 render coverage (a live fixture exercising the exact-diff path).

## Evidence index
`/tmp/pr-gate-unitA-{1,2}.log` · `/tmp/pr-gate-unitB-{1,2}.log` · `/tmp/pr-gate-mut-*.log` (22) · `/tmp/pr-gate-tree-{head,base}-*.log` (26+74 total pr-gate logs) · `/tmp/pr-gate-e2e/` (stub_llm.py, drv.py, boot.sh, stub-capture.jsonl 38 requests, boot-launcher*.log, per-leg verdict JSONs) · restore map `/tmp/pr-gate-harness-agents-baseline.sha256`.

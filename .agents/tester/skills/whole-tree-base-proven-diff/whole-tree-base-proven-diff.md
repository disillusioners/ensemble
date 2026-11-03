# Whole-Tree Base-Proven Diff — Tier-2 Gate Recipe (project canon)

Runs the FULL `tests/` tree at HEAD and proves **net-new failures = 0** against a BASE commit, with shard-level incremental persistence so a stall never loses work.

> Canonical in-repo version. Supersedes dynamic-skill `9e79ccad-1e2c-456b-8762-82d7f5eeec0f` (DB copy retained for searchability; SUPERSEDED preamble requested via `skill_fix`, pending evolution-lane enablement — not yet applied). Refined 2026-10-31 (commission tiered-gate-doctrine) with the G2 learnings: **fast execution is baked in below — consumers must NOT hand-roll slow runners.** The G2 hand-roll (serial 1300-node shards under 300s caps) cost 9h32m wall, 89 timeout-splits, 36 rc=124 outer kills for a guarantee this recipe produces in ~15–35 min/leg. **Revised v1.0.1 (2026-11-01, Phase-5 feedback — doctrine maintenance):** (a) the ≈9–15 min/leg rate calibration is CORPUS/HOST-DEPENDENT, not a universal constant; (b) projection guard hardened with a ≥1,000-node probe floor + shard-0 re-projection checkpoint after it fired a FALSE 3× breach on the Phase-5 leg (probe rate ~250/min under-projected steady state ~4×; actual 1,021–1,050 nodes/min).

- Tier contract: `.agents/tester/rules/ensure.md` § Gate Tiers (Tier 2 trigger = core-seam path-prefix OR promote-blocking gate; budget ≤ 90 min ADVISORY — breaches are always reported loudly, never silent).
- Digest comparison: `.agents/tester/skills/failed-digest-equality-gate/`.

## 0. Pre-flight

```bash
pgrep -af '<worktree-slug>'            # no lingering pytest from a dead lane
git -C <wt> status --porcelain | wc -l # expect 0
```

Repo note: a test run appends to the git-tracked `.agents/shared/planning/designer-agent/install-audit.jsonl` (KMS audit side effect). Preserve a copy, then `git checkout -- <that file>`, and disclose it.

## 1. Collection (pre-exclusion registry baked in)

```bash
timeout 180 .venv/bin/python -m pytest --collect-only -q tests/ \
  --ignore=tests/packs \
  --ignore=tests/e2e/test_context_injection_hybrid.py > /tmp/<run>/nodes.txt
```

- `--ignore=tests/packs` — module-level `sys.exit` guards → INTERNALERROR, aborts collection (the g7 trap).
- `--ignore=tests/e2e/test_context_injection_hybrid.py` — module-IMPORT-time HTTP GET to `localhost:8079` → collection ERROR (deselection happens AFTER import; the marker filter does NOT save you).
- Build the node list with `grep '::'`, NOT `grep '^tests/'` — pytest `SyntaxWarning` headers start with `tests/` and are not node ids.
- Record the collected/deselected counts in the gate report (the slicing proof).

## 2. Execution sizing — MEASURED-RATE, never hand-rolled (the G2 fix)

1. Collect once → total node count N.
2. Probe ONE small shard → measured tests/min rate.
3. **Default shard sizing (mrpr form): size shards so projected shard wall ≈ 25–30 min** — house calibration ≈3300 nodes/shard with per-shard `timeout 1750`. The legacy ≤100-node/`timeout 300` shards are for BISECTION ONLY, never the main sweep.
4. **xdist `-n auto` WITHIN one leg** is the preferred accelerator for the non-PG corpus. **The ≈9–15 min/leg figure is a CORPUS/HOST-DEPENDENT calibration from the tiered-gate-doctrine corpus (2026-10-31) — NOT a universal constant** (the Phase-5 project-skill-loader corpus sustained 1,021–1,050 nodes/min — ~4× faster per node); always re-derive the expected rate from THIS run's probe/shard-0 data. Exclusions from intra-leg parallelism:
   - PG packs (`-m postgres`): SERIAL only — schema races.
   - Shared-scratch-DB chunks (`dual_backend_engine` / `auth_pg_scratch_helper` / `scratch_pg` greps): one-leg-at-a-time (DF-4: 9 fabricated reds under parallel legs).
5. **Projection guard (fail-loud):** projected total = setup + legs + bisection reserve + flaky probes, compared against the 90-min advisory budget BEFORE running. Projected OR actual breach → STOP + report + one re-scope. Advisory ≠ silent.
   - **Probe-rate floor + re-projection checkpoint (v1.0.1, Phase-5 lesson — a small-shard probe produced a FALSE 3× breach):** a small-shard probe rate (~250 nodes/min observed) under-projects steady state by ~4× (actual 1,021–1,050 nodes/min on the same corpus). Therefore: project from a probe of **≥1,000 nodes**, AND after the FIRST shard (shard-0) completes, RE-PROJECT the total from its measured rate before judging the budget. A breach verdict rendered BEFORE the shard-0 re-projection checkpoint is **ADVISORY-ONLY** — report it, do NOT stop on it; a breach that SURVIVES the re-projection is a true STOP + one re-scope.

## 3. Fence the LLM (else shards hang) — identical on both legs

`LLMConfig.request_timeout` defaults to **610s**; the streaming path is only bounded by `OPENAI_STREAM_STALL_THRESHOLD_SECONDS` (45s) → a real generation outlives any shard cap and the repo default `timeout=30` + `timeout_method="thread"` `os._exit(1)`s the shard (rc=1, NO summary, NO FAILED lines). Apply identically on both legs:

```bash
env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER \
    -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL \
    -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD -u PGSERVICE -u PGOPTIONS \
    -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_UPGRADE_SCRIPTS_DIR \
    -u ENSEMBLE_RESTART_UNIT \
    OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
    OPENAI_BASE_URL_BACKUP=http://127.0.0.1:9/v1 \
    OPENAI_REQUEST_TIMEOUT=5 \
    QUEUE_LLM_RETRY_TRANSIENT_ATTEMPTS=1 \
    QUEUE_LLM_RETRY_TIMEOUT_ATTEMPTS=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    timeout <shard-cap> bash -c '
      mapfile -t NODEIDS < "$1"
      .venv/bin/python -m pytest -p no:cacheprovider "${NODEIDS[@]}" -q --tb=line -rEf
    ' _ "$SHARD_FILE"
```

`OPENAI_BASE_URL` → a dead local port makes LLM-dependent tests fail FAST instead of hanging (connection refused beats a 610s timeout). `QUEUE_LLM_RETRY_*` → 1 attempt. `env -u` takes LITERAL names — the per-variable list above is the house form; a glob is a silent no-op.

## 4. Shard + run (incremental persistence)

- Shard by FILE boundary at the §2 sizing. `mapfile -t NODEIDS < file` + quoted `"${NODEIDS[@]}"` (unquoted `$(cat f)` word-splits on ids containing spaces → rc=4).
- After EVERY shard append `shard|nodes|P|F|E|dur|rc` to `progress.tsv` and the shard's FAILED/ERROR ids to `failed_ids.txt`. On resume, skip shards already in `progress.tsv`.

## 5. Death recovery (mandatory)

A shard that dies (NO summary line) must be re-run by **recursive bisection** until a single node is isolated, then that node is re-run alone (per-worker temp files! a shared temp path lets one worker overwrite another's verdict and silently mis-attribute it). Classify each unresolved node individually as PASS / FAILED / ERROR / SKIPPED / TIMEOUT.

## 6. BASE leg — identical command shape AND identical shard composition

Re-run the HEAD-red node ids at BASE with the identical command shape and the SAME id-to-shard mapping — otherwise order-dependent flakes show up as false net-new failures. Then:

- `comm -23 head_red base_red` → NET-NEW (must be empty).
- `comm -13 head_red base_red` → RESOLVED (usually a BASE-side flake; confirm).
- Apply the repo **3× isolation check** on both legs to any surprising red before classifying it as a regression. Classification ladder: byte-identical to base ⇒ pre-existing; order/load-sensitive ⇒ 3× per side before classifying; genuinely new ⇒ fix-and-rerun until net-new = ∅ with a +N/−N fingerprint.

## 7. Accounting closure (prove no node was lost)

Per-shard summary lines give `passed/failed/error/skipped/xfailed` — include `xfailed`, or the totals will not reconcile. Closure check:

```
sum(shard summary counts) + (individually-resolved nodes) == collected node count
```

## Pre-exclusions (with named compensating pins)

| Excluded family | Why excluded | Compensating pin |
|---|---|---|
| `tests/performance/test_message_api_cost.py` | >300s solo, rc=124 at every bisection level — unschedulable | `regression_performance` pack (registered, serial, `tests/performance/`) |
| e2e cells requiring a live `:8079` daemon | need a live daemon; regression legs never boot one | `e2e_workflows_ensure_test` (Release Gate sanctioned lane) |
| `tests/packs/g7` sys.exit(0) collection trap | module-level sys.exit aborts collection | packs execute via their own registered scripts (PACKS.md) — never via collection |

Every exclusion MUST name its compensating pin in the gate report.

## 8. Gate-script conventions (HOUSE CANON — R-v adoption, 2026-11-02)

These are non-negotiable for every gate script that compares id sets. They were adopted after the
central-metrics-v1 P3 whole-tree round recorded **two vacuous "net-new = EMPTY" verdicts** — a
defect that would have promoted a lane on zero evidence.

1. **`LC_ALL=C` on BOTH `sort` and `comm`.** A collation mismatch lets `comm` silently DROP entries
   from the diff (an under-count that reads as a smaller net-new). Sorting with one collation and
   comparing with another is the bug; both tools must agree. This applies to every gate script in
   the repo that sorts or compares node-id sets, not only the whole-tree sweep.
2. **Files first, then log.** Diff lists are written to FILES and *then* logged. Never pipe `comm`
   into a `;`-terminated logging call — `log "..."; comm ... | sed ...` sends `comm`'s output to
   STDOUT and leaves the log's verdict block EMPTY BY CONSTRUCTION.
3. **FORM-tagged verdict lines.** GREP-FULL (node ids + the daemon LOG lines that start with
   `FAILED `/`ERROR `) and BARE-ID (node ids only) are different sets over the same evidence and
   false-mismatch across forms. Each verdict line carries its form tag; each form is compared only
   against itself.
4. **An EMPTY verdict block FAILS LOUD.** `sha256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
   is the vacuous-verdict signature: two aborted legs produced that digest at `nodes=0` and the
   "DIGEST-EQUAL … net-new = EMPTY" text that followed was meaningless. A comparison over an empty
   set is **never** a pass.

**The canonical implementation** is `sweep_digest_verdict.sh` in THIS directory — use it, do not
re-derive the verdict block inline. It enforces all four conventions and returns:

| exit | meaning |
|---|---|
| 0 | net-new EMPTY in both forms (non-vacuous) |
| 1 | net-new NOT empty in ≥1 form — proceed to per-id adjudication, do NOT report a regression yet |
| 2 | **VACUOUS** — empty input, empty-string digest, or an empty verdict block. Never a pass. |

```
.agents/tester/skills/whole-tree-base-proven-diff/sweep_digest_verdict.sh \
    <base.ids.txt> <head.ids.txt> <out_dir>
```

**The tool measures; the ladder decides.** A non-empty net-new set is an INPUT to the §6
classification ladder (each diff id in ISOLATION, 3× per side), never a verdict on its own. The P3
round proved the sweep-level delta is a parallel-shard COMPOSITION ARTEFACT — the SAME shard on the
SAME tree produced DIFFERENT red sets across two runs (26 ids differed). Treat shard-level digests
as non-reproducible instruments and adjudicate at node level.

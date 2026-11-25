# HANDOFF — feeder-grants-dump-health

**Commission:** agents-ensemble, sonic STANDARD path. Two items carried by ONE staged release.
**Staged version:** v0.31.53 (STAGED ONLY — NO promote; the ceremony is the Emperor's).
**Lane base:** `9f464d726c14ea6d070f63ce017f329b1defb254` (origin/latest at lane open; the commissioned
base `a7db26e0f0160cad0609c3cb4ec5f4e5410c42ab` was already 1 docs-only commit behind it — see Known risks).
**Merged content tip:** `c893f0971` (modules A+B+C+D merged; this HANDOFF is the next commit).
**Diff range:** `a7db26e0f0160cad0609c3cb4ec5f4e5410c42ab..c893f0971` (lane delta: `9f464d726..c893f0971`).
**Complexity verdict:** medium (assessed pre-dispatch; see the last section).

---

## Files touched across modules

**Module A — feeder-grants** (branch `feature/sonic-feeder-grants` @ `313dc080834b8b6d2bf79dfd61dfd4dd1bc96720`)
- `agents/experiencer/meta.json`
- `tests/unit/test_pok_grant_pins.py`
- `.agents/shared/planning/pok-write-gate/evidence-pok-write-gate-w1/mutation-feeder-grants-experiencer-grant-re-added.md`

**Module B — kb-importer** (branch `feature/sonic-kb-importer` @ `2192f4891f63bc657027795d14a17b9232ee9148`)
- `.agents/shared/planning/pok-write-gate/feeder-assessment-kb-importer-2026-11-25.md`
- `.agents/shared/planning/pok-write-gate/evidence-rederiv-2026-11-25/kb-importer-active-rows.tsv`

**Module C — dump-health** (branch `feature/sonic-dump-health` @ `0fa6e823226a92360d1e50ed90dd64ab97bde63d`)
- `.agents/shared/planning/sonic-team/feeder-grants-dump-health/dump-lane-health-design.md`
- `scripts/audit/dump_lane_staleness.py`
- `docs/runbooks/dump-lane-health-probe.md`
- `tests/unit/test_dump_lane_staleness_probe.py`
- `.agents/tester/PACKS.md` (append-only: 16 insertions, 0 deletions, one new block at the top)

**Module D — release-bump** (branch `feature/sonic-release-bump` @ `a3f22f8efbeab0f3a93119149ebd6b085da97268`)
- `CHANGELOG.md`
- `pyproject.toml`
- `daemon/__init__.py`
- `tests/test_api.py`
- `uv.lock`

**Integration (sonic-developer)**
- `.agents/shared/planning/sonic-team/feeder-grants-dump-health/HANDOFF.md` (this file)

**Merged diff stat:** 10 files before the bump (+5 for the bump) — `git diff --stat 9f464d726..c893f0971`.

**Deliberately UNTOUCHED (byte-identical, verified by sha256 at base and at the merged tip):**
- `.agents/shared/metrics/tool-categories.json` — `66832459500763ba3eb9b268cb828b9d28390d5144bd5694deab6076ffba0365` (66 categories, `vocabulary_version` 11). Vocabulary, not grants — no regeneration, no version bump.
- `agents/.prompt-artifacts.sha256` — `3c089361c9862b08861c17763037beae7a746840eac0190a59ed8f79d2aaa443`. No `agents/**/*.md` changed, so the baseline must not move. House generator `scripts/protection/generate_prompt_artifact_baseline.py --check` → `PROMPT-BASELINE: OK — 217 versioned artifact(s) match`, rc=0, run on the merged tree.
- `daemon/checkpoint_metrics.py` — no `VOCABULARY_VERSION` bump.
- Every other `agents/*/meta.json` — no other grant changed (kb-importer keeps `pok`; see What changed).

---

## What changed

**Item 1a — feeder grant cleanup (PRE-APPROVED, the only grant change in this release).**
The `pok` grant is removed from `agents/experiencer/meta.json` (`tools.allow` → `["rag","help","time","mcp","context","shared_meta_kv","nest-report"]`); `rag` — the experiencer's RAG-side routing — survives, and the `pok` capture duty stays with the four compensating operational agents (ari / project-manager / leader / devops). The test file mirrors the landed kb-writer rider `fd7dc6480` exactly: `EXPECTED_EXPLICIT` 53 → 52, `EXPECTED_TOTAL_META` stays 55, `GRANT_ABSENT_EXCEPTIONS` becomes `{"kb-writer","experiencer"}` (the completeness walk now skips it — a DECLARED exception, not a hole in the walk), the stale module docstring and the stale method name are refreshed, and a new `TestTheExperiencerGrantAbsence` class pins the absence positively (no grant / declared exception / `rag` survives / the four operational agents hold the grant).

**Item 1b — kb-importer assessment (ASSESS-AND-REPORT ONLY; no grant change, no row move, no store mutation).**
kb-importer's POK authorship was re-derived live (read-only `BEGIN READ ONLY` SELECTs against `ensemble_prod` @ `10.44.0.2:5432`): **5 active rows** (+1 superseded) across 2 projects — the 2026-11-23 census figure of "1" was stale. All five classify as **operational** (this project's how-to / rule / trap) under the Phase 9 §4 routing map, so the grant is exercised for legitimate content. The named row `77bb3c69-bb35-42d3-b81e-5ff643847146` (`agent-01-skill-injection-flag-scope`) was verified live: `access_count` 10 (the commission said 9 — it has since been read again), `verify_count` 0, `last_verified` = `created_at`, NEVER_VERIFIED. Its 2026-11-09 figures ("15 dirs true / 11 logical agents; skill-keeper lacks the flag") were RE-DERIVED and CONFIRMED against the repo on 2026-11-25, so no staleness caveat is carried on the figures themselves — the caveat that remains is the row's own never-verified status.
**Disposition: KEEP the `pok` grant on kb-importer.** The documented reason lives in the plan-set assessment doc and in this handoff — deliberately NOT in a POK row, because a `pok_upsert` is a unilateral live-store mutation that this commission forbids, and because a disposition rationale is governance, not an operational fact. The "if the Emperor instead reads it as MISPLACED" removal + per-row routing spec is written out in the assessment doc for his decision.

**Item 2 — dump-lane health visibility (design → build).**
A READ-ONLY operator probe (`scripts/audit/dump_lane_staleness.py`) that answers one question — *when did the last dump SUCCEED?* — and answers it LOUDLY when the answer is bad. The design was fixed BEFORE the build:
1. **Marker source** — a heartbeat row the cluster-side `pg_dumpall` CronJob publishes, read over the one channel that already exists (PostgreSQL via the LB `10.44.0.2:5432`); the daemon never reaches kubernetes. The cluster-side upsert is a CLUSTER-SIDE change ⇒ an **OPERATOR-ACTIONABLE runbook step** (create table → teach the CronJob → run the probe), never daemon code. Two providers behind one state machine: `pg` (one bound-parameter `SELECT`) and `file` (mtime of `--marker-file` — the immediately-usable fallback and the hermetic test shape).
2. **Threshold** — 8 days (691200 s), overridable via `--max-age-hours`. Exactly AT the threshold is FRESH; +1 s is STALE; the reported age IS the compared age (one integer).
3. **Cadence** — the weekly central-maintenance run plus ad-hoc operator invocation. Wiring the probe INTO the maintenancer dispatch is an OPERATOR-ACTIONABLE step NOT implemented here (it would edit a DB-versioned prompt artifact).
4. **Loud-report channel** — a LOUD stderr banner for every non-FRESH verdict, exactly ONE structured stdout `RESULT:` line, and documented exit codes: **0 FRESH / 76 STALE / 75 NO-MARKER / 78 CONFIG** (house exit-78 idiom). STALE or NO-MARKER must land as a `needs_emperor_word` recommendation.
5. **Failure behavior** — fail-closed: an absent / unreadable / unreachable marker NEVER reports FRESH. Absence of evidence is a failure, never "fine" — that is precisely the 12-week silent-outage shape. The probe never guesses a connection (`--db-url` required, ambient `POSTGRES_*` never consulted) and never writes to any database (session pinned READ-ONLY server-side; only the two allowed SQL constants can reach a driver).

**Item D — release bump.** v0.31.53 cut with the house generator (`scripts/bump_version.py patch`): the 4 version files bumped 0.31.52 → 0.31.53, `uv sync` run, CHANGELOG.md folded into the commit (the 5-file house convention). The `uv.lock` diff is exactly ONE hunk — the `ensemble` version field; no other package moved.

---

## What is UNTESTED

**Per module:**
- **A** — no whole-tree or full-suite run (the verification wave owns that); only the scoped file was run. `generate_prompt_artifact_baseline.py --check` was NOT run by the module (it argued byte-parity instead) — **it was run by the integration layer on the merged tree: rc=0, 217 artifacts OK.** The grant pin is a static census pin: nothing exercises the experiencer's runtime tool set (no live-store / daemon runtime check), by design.
- **B** — no automated test at all (docs-only module). No `verify` call was made on any row, so `verify_count` stays 0 for all five rows. RAG-overlap and the `tool_usage_daily` dual-write telemetry were **cited from the 2026-11-24 feeder assessment, not re-queried** (the telemetry window is a 3-day retention ⇒ a lower bound). The superseded row was noted but not re-classified. The live row set can drift again after the 2026-11-25T03:33Z snapshot.
- **C** — the `pg` provider was NEVER run against a real PostgreSQL: the read-only-session semantics (plain `SET` surviving COMMIT ⇒ the next transaction opens READ-ONLY) are REASONED and asserted only through a fake engine at the `_open_pg_engine` seam. The probe was never run against the live `10.44.0.2:5432`, and the heartbeat table does not exist yet, so the `pg` provider currently reports NO-MARKER (75) — correct fail-closed behaviour, but the production path is unexercised. The cluster-side CronJob edit and the maintenancer dispatch wiring are NOT implemented (operator steps). The pack is registered against the house scrubbed runner — **no `test/packs/*.sh` script exists for it, so there is no pack-slicing proof.**
- **D** — the FULL `tests/test_api.py` was NOT run: it carries a pre-existing debt baseline (9 failed / 38 passed), which would make a red run ambiguous about attribution. Only the version-bearing node (`test_health_check`) was executed (1 passed). No version-propagation grep pack was run.

**Integration seams (created by MY merges — NOT exercised by any module):**
- The **merge of A into the integration branch** — verified only by the scoped test file on the merged tree (18 nodes) plus the adjacent vocabulary-pair file. The experiencer grant census is a WHOLE-TREE count: any sibling lane adding or removing a grant in this merge window turns `EXPECTED_EXPLICIT` RED. The pin is correct for THIS tree and brittle against concurrent grant churn.
- The **merge of C into the integration branch** — the probe was never executed as a CLI against a real marker file or a real database; only its unit pin ran (36 nodes).
- The **merge of D (the bump) into the integration branch** — the version-bearing node was re-run on the merged tree (1 passed); the wider `tests/test_api.py` debt baseline was NOT re-measured before vs after (the module cited it from POK rather than re-measuring it).
- The **prompt-artifact baseline** was verified unchanged on the merged tree, but the **staged-release prompt-drift leg** (stage.sh's informational drift check) has not yet run at stage time — it runs during the stage, after this handoff is written.
- **The staged artifact itself** (4-way sha, integrity verify) is produced by the stage, which runs after this handoff is committed; its evidence is in the sonic-developer report to sonic-leader, not in this file.

---

## Known risks

**Module risks (carried):**
- **A** — `EXPECTED_EXPLICIT = 52` is a whole-tree census; a concurrent grant change in the merge window reds the count pin (attribution risk for the verification wave: it is not a defect of this change).
- **B** — the assessment is a point-in-time read (2026-11-25T03:33Z); the row count can drift. The `pok-write-gate/plan-overview.md:341` figure ("kb-importer = 1 active row") is now STALE and still sits in the Emperor's decision input for parked item **P3** — it is superseded by the measured 5. That file belongs to the pok-write-gate lane and is outside this commission's write budget, so it was NOT edited; the leader should route a correction.
- **C** — (1) until the operator creates the heartbeat table and edits the CronJob, the `pg` provider reports NO-MARKER (75): correct fail-closed behaviour, but it reads "unobservable", not "stale" — the `--provider file` fallback covers the gap; (2) `--now` is an operator affordance that COULD mask staleness (the `RESULT:` line always records the instant used, and the runbook forbids passing it from the weekly chain); (3) clock skew ⇒ a future timestamp ⇒ negative age ⇒ FRESH (surfaced as a note line, not a 4th state — a persistently skewed writer could mask staleness); (4) the probe is a point-in-time read — it says a dump REPORTED success, not that the artifact is complete or restorable; (5) the maintenancer wiring is absent, so the weekly signal stays MANUAL until an operator wires it; (6) the heartbeat table is operator-created and tracked by NO daemon migration — if the operator renames it, the SQL constant and the runbook must change together (a dedicated pin holds the table name against drift).
- **D** — POK row `bump-01-preflight-preconditions` (last verified 2026-11-09) ends with *"legacy message `! New version X` → amend"*, which contradicts this commit. **Integration adjudication: the row is STALE, the commit is house-correct** — `scripts/bump_version.py:150` emits exactly `! New version {new_version}` and all 12 recent release commits on origin/latest carry that subject verbatim (v0.31.52 `ca440288b`, .51, .50, .49, .48, .47, .46, .45, .33, .25, .24, .23), so there is nothing to amend TO. The row needs curator action; the commit was left as the generator wrote it. (`uv sync` ran with index access but from the resolved lock — no re-resolution, no package move; the one-hunk lock diff is the proof.)

**Merge / integration risks:**
- **Three merges, ZERO conflicts** — the disjoint file sets held. The one file touched by two parties in spirit is `.agents/tester/PACKS.md`: module C appended one block at the top (16 insertions, 0 deletions); the older `pok_grant_pins` row still says "10 nodes" while the file now collects **18** — that row's node count is the TESTER's re-stamp duty (not edited here; PACKS.md is the tester's registry).
- **PRE-EXISTING REDS (NOT caused by this commission, proven):** `tests/unit/test_anomaly_report_grant_pins.py` has **3 failing nodes** — `TestTheGrantCompletenessWalk::test_the_grant_is_the_last_element`, `::test_the_three_phase_3_touched_agents_carry_both_entries`, `TestTheSealMapAfterTheGrant::test_the_seal_map_is_zero_stale` — with offenders that are all stale `project_future` trailing entries. The IDENTICAL 3-node FAILED list was measured at the TRUE base `9f464d726` (a detached base worktree), so the net-new failure count from this commission is **∅**. Do not attribute these to this lane; they are base debt.
- **Base movement:** the commissioned base `a7db26e0f` was already 1 commit behind `origin/latest` (`9f464d726`, a tester RESULTS doc) when the lane opened; the lane built on `9f464d726` and re-verified it (distance 0) before every merge and before the push. The commissioned diff range therefore includes that one docs commit.
- **Ordering:** modules merged in the declared slots A → C → B → D; all four file sets were disjoint, so no ordering hazard materialised.
- **No daemon code, no prompt artifact, no metric label, no vocabulary bump, no new dependency, no live-store write, no live-system write** — the release is additive-only apart from the single granted removal.

---

## How to verify

Run on the MERGED tree (from a worktree at the release tip; the worktree venv, never the main `.venv`):

```sh
WT=/home/nea/ensemble-src-wt-sonic-integration
cd "$WT"

# 1 — the two commissioned test files + the frozen vocabulary pair (63 nodes)
ENSEMBLE_SELF_ENV=dev ./scripts/run_tests_scrubbed.sh \
  tests/unit/test_pok_grant_pins.py \
  tests/unit/test_dump_lane_staleness_probe.py \
  tests/unit/tools/test_tool_category_artifact.py -p no:randomly -q
# expect: 63 passed

# 2 — the version-bearing node (post-bump)
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST \
    -u POSTGRES_PORT -u DATABASE_URL_POSTGRES -u ENSEMBLE_AUTH_ENABLED \
  .venv/bin/pytest tests/test_api.py::test_health_check -p no:randomly \
  --override-ini="addopts=" -q
# expect: 1 passed

# 3 — the frozen invariants (must equal the base values byte-for-byte)
sha256sum .agents/shared/metrics/tool-categories.json agents/.prompt-artifacts.sha256
# expect: 66832459500763ba3eb9b268cb828b9d28390d5144bd5694deab6076ffba0365  tool-categories.json
#          3c089361c9862b08861c17763037beae7a746840eac0190a59ed8f79d2aaa443  .prompt-artifacts.sha256

# 4 — the prompt-artifact baseline, via the HOUSE generator
.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check
# expect: PROMPT-BASELINE: OK — 217 versioned artifact(s) match ; rc=0

# 5 — the grant posture (the only grant change in this release)
python3 -c "import json;a=json.load(open('agents/experiencer/meta.json'))['tools']['allow'];print(a);assert 'pok' not in a;assert 'rag' in a"
python3 -c "import json;a=json.load(open('agents/kb-importer/meta.json'))['tools']['allow'];print(a);assert 'pok' in a"

# 6 — the PRE-EXISTING reds (base debt, NOT this lane): expect the SAME 3 FAILED
ENSEMBLE_SELF_ENV=dev ./scripts/run_tests_scrubbed.sh \
  tests/unit/test_anomaly_report_grant_pins.py -p no:randomly -q
```

**The mutation proof for the grant-absence pin** (item 1a's "re-add → RED") is recorded in
`.agents/shared/planning/pok-write-gate/evidence-pok-write-gate-w1/mutation-feeder-grants-experiencer-grant-re-added.md`
(venue: a throwaway `git archive` extract under `/tmp`; the live worktree was never mutated).
It was re-run independently by the integration layer: baseline **6 passed** → re-add `"pok"`
immediately before `"nest-report"` → **2 failed, 4 passed** — REDing
`TestTheExperiencerGrantAbsence::test_experiencer_does_not_hold_the_grant` and
`TestThePokGrantCompletenessWalk::test_exactly_52_carry_the_grant_explicitly`
("53 agents carry 'pok' explicitly, expected 52"), with the ORDERING pin staying green.
Module C's own mutation legs (6/6 RED, restores byte-identical) are recorded in its design doc
§5 and in the PACKS.md row.

**The new pin for item 2** is `tests/unit/test_dump_lane_staleness_probe.py` (36 nodes, hermetic):
the three states + exit codes (0/75/76/78), the threshold boundary (exactly AT ⇒ FRESH, +1 s ⇒ STALE),
the refusal to guess a connection (78), the DSN redaction in the error path, and the no-write
assertion (AST-scoped SQL + filesystem-read proof + a provider failure that leaves no side effect).
Registered as `dump_lane_staleness_probe_unit_test` in `.agents/tester/PACKS.md`.

---

## Bash-raises used

**none.** No command exceeded the default 600 s budget, so no raise was requested from
sonic-leader and none was consumed. The release STAGE (a full PyInstaller + frontend build) is
long-running by nature and was run as a BACKGROUND process (`proc`), not as a blocking bash call;
its wall-clock duration is reported in the sonic-developer report to sonic-leader.

---

## Complexity self-assessment

**medium** — assessed after the plan and the module split, BEFORE any coder was dispatched
(§2.5 of the sonic-developer workflow), and NOT softened afterwards.

Reasons from the plan:
- **Module A** is low-complexity: it mirrors an already-landed pattern (`fd7dc6480`) with a
  known-good edit shape, touching two code files and one evidence doc; the mutation leg is a
  re-run of a proven venue.
- **Module B** is low-complexity in code but carries a live-store read (read-only, fenced) and a
  judgement call (routing classification + disposition) that had to be evidenced rather than asserted.
- **Module C** is the load-bearing module: a design-first surface with a cluster-side dependency,
  a marker contract, a threshold, a loud channel and a fail-closed contract — but it is bounded to
  one new script + one new test + two docs + one registry row, with NO daemon code and NO new
  dependency, and the design was fixed before the build.
- **Module D** is low-complexity: a mechanical house-generator bump.

Why it did not reach high: the four modules had DISJOINT file sets, every module fitted one
sonic-coder one-shot turn, there was no daemon code, no schema/migration, no live-system write,
no cross-module coupling, and the three merges resolved with ZERO conflicts. Why it was not low:
item 2 is a genuinely new operational surface with a real cross-boundary contract (a
cluster-side marker the daemon cannot reach), the release cycle adds a tag/stage/push
ceremony surface, and item 1b required a live-store re-derivation whose first figure was stale.
Handover was therefore NOT raised; the commission proceeded in the speed lane as instructed.

**No handover was overridden** — the verdict was `medium` and the work proceeded on that verdict.

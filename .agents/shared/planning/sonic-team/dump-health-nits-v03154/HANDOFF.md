# HANDOFF — dump-health-nits-v03154

**Commission:** agents-ensemble, sonic STANDARD path — follow-up slice closing the three
pre-ceremony advisories on the v0.31.53 lane, shipped as a NEW staged release.
**Staged version:** v0.31.54 (STAGED ONLY — NO promote; the ceremony is the Emperor's).
**Lane base:** `033f3dd0474126ecc81050eb7427b293571a854f` (= `origin/latest` = tag `v0.31.53`
at lane open; unmoved through the lane — re-checked before the push).
**Merged content tip:** `ddb07274d06f8252a7acf0e9fa0cd3b45654e848` (4 module merges; this
HANDOFF is the next commit on top).
**Diff range:** `033f3dd04..ddb07274d` — 12 files, +297 / −20.
**Complexity verdict:** medium (assessed pre-dispatch; see the last section).

---

## Files touched across modules

**Integration (sonic-developer) — R5 cherry-pick, the merges, this handoff**
- `.agents/tester/PACKS.md` (cherry-pick of `2e5ae6eff`, the TESTER's own re-stamp —
  `pok_grant_pins` 10 → 18; authorship preserved via `-x`)
- `.agents/shared/planning/sonic-team/dump-health-nits-v03154/HANDOFF.md` (this file)

**Module A — runbook-design** (branch `feature/sonic-dhn-runbook` @ `c897b65dbd188bdcfbbafbd3dd06c864363eb346`)
- `docs/runbooks/dump-lane-health-probe.md`
- `.agents/shared/planning/sonic-team/feeder-grants-dump-health/dump-lane-health-design.md`

**Module B — probe-edges** (branch `feature/sonic-dhn-probe` @ `f0aa871c897dbd5f11d744dd59159858ac472173`
= `52700c04c` + the revision commit `f0aa871c8`)
- `scripts/audit/dump_lane_staleness.py` (shipped sha256 `9f87040f07628fd103d72002474c78641048f827e50a200059fb4886d775e153`)
- `tests/unit/test_dump_lane_staleness_probe.py`
- `.agents/tester/PACKS.md` (the `dump_lane_staleness_probe_unit_test` ROW + the block's
  mutation-proof paragraph)

**Module C — citations** (branch `feature/sonic-dhn-citations` @ `a1bdcf902c85a53baa12f1f8151b8507261d8291`)
- `CHANGELOG.md` (the v0.31.53 entry, line 14 — citation fix only)
- `.agents/shared/planning/sonic-team/feeder-grants-dump-health/HANDOFF.md` (line 66 — citation fix only)
- `.agents/shared/planning/sonic-team/dump-health-nits-v03154/curator-filing-pok-write-gate-plan-overview-341.md` (NEW)

**Module D — release-notes-bump** (branch `feature/sonic-dhn-bump` @ `90ccafe60e3018bc48d83f3d5521c42452f7e0d1`)
- `CHANGELOG.md` (the new v0.31.54 section)
- `pyproject.toml` · `daemon/__init__.py` · `tests/test_api.py` · `uv.lock`
  (written by `scripts/bump_version.py patch`, never by hand — the house 5-file convention)

**Deliberately UNTOUCHED (sha256 verified identical at base and at the merged tip):**
- `.agents/shared/metrics/tool-categories.json` — `66832459500763ba3eb9b268cb828b9d28390d5144bd5694deab6076ffba0365`
- `agents/.prompt-artifacts.sha256` — `3c089361c9862b08861c17763037beae7a746840eac0190a59ed8f79d2aaa443`
  (house generator `scripts/protection/generate_prompt_artifact_baseline.py --check` → `PROMPT-BASELINE: OK — 217 versioned artifact(s) match`, rc=0, on the merged tree)
- Every `agents/*/meta.json` (no grant change) · every `daemon/**` file (no daemon code) ·
  every `daemon/migrations/versions/*.sql` (NO schema change — the heartbeat table is not created here)

---

## What changed

**R1 — the runbook named a checkout that cannot run the probe. CHOICE: (a).**
`docs/runbooks/dump-lane-health-probe.md` §1 and §4 both said `cd /home/nea/ensemble-src` — a
detached HEAD (`bf4bfde6e`) that predates `scripts/audit/`, drifts by design and is never a
commit target; the v0.31.53 payload ships `scripts/upgrade/` only, never `scripts/audit/`.
Both sections now state the PRECONDITION — a checkout whose tree CONTAINS
`scripts/audit/dump_lane_staleness.py` (tag v0.31.53 or later) — say in one line WHY NOT the
detached checkout, and carry one-line existence guards that refuse in the exit-78 family
(`test -f scripts/audit/dump_lane_staleness.py || … exit 78`, plus the `.venv/bin/python`
interpreter guard) instead of dying with a python "No such file" (127).
**Why (a) and not (b):** (b) would edit the frozen executor family
(`scripts/upgrade/executor_family.sh` + the stage.sh per-file map) and force a
`PIPELINE_SCRIPTS_REV` + `PIPELINE_SCRIPTS_FLOOR_COMMIT` re-pin plus a scripts-pin refresh
(the D6-8 two-travel-together rule) — release-pipeline surgery that couples a nit-closing slice
to a pipeline re-pin, for an advisory that a documented precondition closes on its own. The
probe therefore stays repo-only, and "reachable from the payload" remains an OPEN residual
(R4 step 4). The pipeline floor is UNCHANGED by this slice.

**R1 — first-hand execution leg (two runs, identical outcome).** The documented §1/§4 invocation
was run VERBATIM from the named checkout (`/home/nea/ensemble-src-wt-audit`), credential sourced
from the live install env file (mode 600) into `PGPASSWORD` (env only — never argv, never echoed):
- run 1 @ base `d07cc64b8` (pre-dispatch), run 2 @ release content tip `ddb07274d` (post-merge).
- pg provider, live `ensemble_prod` @ `10.44.0.2:5432`: **exit 75**,
  `RESULT: state=NO_MARKER lane=pgdumpall provider=pg last_success_at=none age_seconds=none max_age_seconds=691200 evaluated_at=2026-11-25T06:52:47Z reason=db_query_error`
  (stderr detail: `ProgrammingError: (psycopg.errors.UndefinedTable) relation "public.dump_lane_heartbeat" does not exist`) —
  the truthful fail-closed state while the heartbeat table does not exist (R4 OPEN).
- file provider, fresh temp marker: **exit 0**, `state=FRESH … age_seconds=0 … reason=within_threshold`.
- file provider, missing path: **exit 75**, `reason=marker_file_missing`.
- file provider, 30-day-old marker: **exit 76**, `reason=older_than_threshold`.
- Credential hygiene: **0** occurrences of the secret in any captured output; the RESULT line
  carries no DSN. The exact command and both result sets are recorded verbatim in the design
  doc §4.0 (`dump-lane-health-design.md`).

**R2 — the "12-week" outage figure was uncited in-repo.** `CHANGELOG.md:14` and the v0.31.53
`HANDOFF.md:66` both asserted a backup-outage DURATION that no in-repo source establishes.
Both now cite the citable facts — POK `pg-host-clockwork-landscape-01` (devops, 2026-11-23:
`pgdumpall` 17:00Z daily FAILING, dump PVC 99.7% full) — and state explicitly that the outage
duration was never established in-repo. The HANDOFF edit carries a visible
`[corrected 2026-11-25 by the v0.31.54 slice: …]` marker, not a silent rewrite. The new
v0.31.54 CHANGELOG entry carries no outage duration at all. **Zero POK writes** in this slice
(read-only citation; the armed v0.31.52 gate was not touched).

**R3 — two real probe edge cases, both now inside the house contract.**
- (i) `--max-age-hours nan|inf|+inf|infinity`: argparse's `type=float` accepts them, `nan <= 0`
  is False, so the old guard passed and `int(round(nan*3600))` raised — a bare traceback at
  **exit 1**, OUTSIDE the 78 family (reproduced at base: nan → `ValueError: cannot convert float
  NaN to integer`; inf → `OverflowError: cannot convert float infinity to integer`). The probe now
  refuses any non-finite threshold with `REASON_BAD_MAX_AGE` on the exit-78 path, with the detail
  "must be a finite number > 0".
- (ii) `REASON_DRIVER_UNAVAILABLE` was documented in the runbook but NEVER emitted —
  `read_pg_marker` caught EVERY engine-open failure as `db_unreachable`, whose runbook action
  ("check the LB and the credential") misdirects an operator whose actual fault is a missing
  driver. An `except ImportError` clause now precedes the generic handler (covering both the
  lazy `from sqlmodel import create_engine` and the dialect load inside `create_engine` —
  `ModuleNotFoundError` ⊂ `ImportError`), yielding `reason=driver_unavailable`; everything else
  stays `db_unreachable`.
- (iii) The runbook's reason vocabulary is now closed and PINNED: each of the 13 slugs has
  exactly one action row, and a new unit node fails if the script and the runbook drift.

**R5 — the pack registry and the curator item.** The tester's `pok_grant_pins` re-stamp
(10 → 18) rides the integration branch as a cherry-pick of the tester's own commit (`2e5ae6eff`,
authorship preserved) — it was cherry-picked BEFORE the modules branched, so no module ever
edited that row and no conflict was possible. The stale `kb-importer = 1` figure at
`.agents/shared/planning/pok-write-gate/plan-overview.md:341` is filed as CURATOR WORK in
`.agents/shared/planning/sonic-team/dump-health-nits-v03154/curator-filing-pok-write-gate-plan-overview-341.md`
(file:line, the stale text, the corrected figure — 5 active rows across 2 projects, +1 superseded,
re-derived read-only 2026-11-25 — the evidence sources, and the requested edit). **The
pok-write-gate lane's plan file was NOT edited**; it is another lane's file and is never
silently edited.

**R4 — OPEN (NOT in this slice).** The remaining operator steps, named exactly:
1. **Heartbeat table creation** — runbook §2 DDL `public.dump_lane_heartbeat`; a staged migration
   / operator action. **NO schema change was made in this slice.**
2. **CronJob edit** — the heartbeat upsert, runbook §3 — operator / devops.
3. **Weekly-cadence wiring into the maintenancer chain** — runbook §6; a DB-versioned prompt
   artifact via the prompt-versioning approval lane — a follow-up commission.
4. **The probe is reachable only from a checkout** (not from the staged payload).
Interim alarm already standing (as of 2026-11-25): the weekly maintenance schedule carries a 48h
green-dump check (added by Ari) and the devops lane is reviving the dump lane tonight.
This OPEN status is mirrored in the runbook §6 and the design doc §6.

**Release bump.** v0.31.54 cut with the house generator (`scripts/bump_version.py patch`):
`pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py` and `uv.lock` moved 0.31.53 →
0.31.54, and the `uv.lock` diff is exactly ONE hunk (the `ensemble` package version — no other
package moved). The CHANGELOG's new v0.31.54 section is folded into the same 5-file commit
(`! New version 0.31.54`) — the house convention.

---

## What is UNTESTED

**Per module:**
- **A (runbook-design)** — docs only: no markdown lint, no link check, no render pass; the two
  self-checks were mechanical greps (`cd /home/nea/ensemble-src` → no hits; each of the 13 slugs
  → exactly one action row). The sibling's coverage node was NOT run against this branch by the
  module (it is red there by construction).
- **B (probe-edges)** — the pack is **hermetic**: the `pg` provider is exercised through a fake
  engine injected at the `_open_pg_engine` seam (no socket is ever opened) and the driver leg
  fails at the import (before any connection). The probe was therefore **never run against a
  real PostgreSQL with a real heartbeat row** — the read-only-session semantics and the
  heartbeat read are still asserted only through the fake engine. The `driver_unavailable`
  path is proven with the import blocked, never with a genuinely broken psycopg install. The
  whole-repo/whole-tree gate is the reviewer's/tester's wave, not this module's.
- **C (citations)** — docs only; no runtime path exercised. The POK row was read, never written.
- **D (release-notes-bump)** — only the version-bearing node (`tests/test_api.py::test_health_check`,
  1 passed) was run; that file carries a pre-existing debt baseline (9 failed / 38 passed), so
  the full file was deliberately NOT run.

**Integration seams (created by MY merges — NOT exercised by any module):**
- The **merge of A** was verified only by the two mechanical self-checks; the runbook's prose
  was never executed by an operator other than this lane's own leg.
- The **merge of B** is the one real cross-module seam: B's runbook-coverage node reads the rows
  A wrote, so it was **RED in isolation** (38 passed / 1 failed in-module, by design) and turns
  **GREEN at integration** (39/39 on the merged tree, measured). The seam is real and is
  disclosed here rather than hidden: the two modules cannot be verified independently.
- The **merge of C** was verified by grep and by `git diff --stat` (3 paths), not by a render pass.
- The **merge of D** was verified by the 5-file stat, the one-hunk `uv.lock` proof, the three
  version values and the version node; the wider `tests/test_api.py` debt baseline was NOT
  re-measured before vs after.
- **PRE-EXISTING REDS (base debt, NOT this commission):** `tests/unit/test_anomaly_report_grant_pins.py`
  fails the SAME 3 nodes at the merged tip (`test_the_grant_is_the_last_element`,
  `test_the_three_phase_3_touched_agents_carry_both_entries`,
  `test_the_seal_map_after_the_grant::test_the_seal_map_is_zero_stale`; 3 failed / 12 passed) —
  the identical list the v0.31.53 handoff recorded at its base. Do not attribute them to this lane.
- The **staged artifact itself** (4-way sha, integrity verify) is produced by the stage, which
  runs after this handoff is committed; its evidence is in the sonic-developer report to
  sonic-leader, not in this file.
- The **tag-time re-run** of the R1 leg (the tag adds only this HANDOFF doc on top of the
  release content) is likewise reported, not recorded here.

---

## Known risks

**Module risks (carried):**
- **A** — the runbook's one-time setup (`git worktree add … && uv sync`) assumes a FRESH host;
  on THIS host `/home/nea/ensemble-src-wt-audit` already exists (this lane created it), so the
  literal command fails with "already exists" and the checkout must be moved
  (`git -C … checkout --detach <tag>`) instead. The precondition itself (a checkout ≥ v0.31.53)
  is host-independent.
- **B** — the coverage node pins the script's closed vocabulary against the runbook, so a future
  script slug without a runbook row REDs the pack (intended: that is the drift alarm). The
  `pok_grant_pins` count row and the probe row in PACKS.md are the TESTER's registry — the probe
  row still carries the in-module status (38/39, coverage node RED until A merges); the
  integration re-run (39/39) is recorded here, and the tester's re-stamp is the tester's call.
- **C** — the corrected figure for `plan-overview.md:341` is a FILING, not an edit: the stale
  "1" still sits in the pok-write-gate lane's plan file until that lane's owner (or the curator)
  applies it. The Emperor's decision input for parked item P3 therefore still carries the stale
  figure until then.
- **D** — the v0.31.53 CHANGELOG entry and the v0.31.53 HANDOFF were edited IN PLACE, i.e. a
  shipped release's notes were corrected retroactively. The edits are marked as corrections, but
  the immutable `v0.31.53` TAG still carries the old wording; the corrected text rides v0.31.54.

**Merge / integration risks:**
- **Four merges, ZERO conflicts** — the disjoint file sets held. The one file two parties touched
  in spirit is `.agents/tester/PACKS.md`: the tester's cherry-pick (the `pok_grant_pins` row) and
  module B's row + mutation-proof paragraph. The cherry-pick was landed BEFORE the modules
  branched, so B's branch already contained it and the merge was textual.
- **Ordering:** merges landed in the declared slots A → B → C → D. B's coverage node depends on
  A's rows, so A-first is load-bearing — the declared order was kept exactly.
- **Base movement:** `origin/latest` did NOT move during the lane (re-checked with
  `git fetch origin latest` before the push: still `033f3dd04`). The lane built on `033f3dd04`
  and pushed its own tip; the commissioned diff range is therefore exactly the lane's own work
  plus the cherry-picked tester re-stamp.
- **Scratch-venue hygiene:** module B self-reported extracting a full-repo archive into `/tmp`
  by mistake and then removing the extracted path set (17 138 tracked paths) — self-verified
  against `git ls-tree`, with no repo impact and no pre-existing `/tmp` content touched. It is
  filed as a nest anomaly (`aaae36f9`) because `/tmp` on this host holds ~28 GB across ~4 268
  entries of OTHER lanes' scratch: a path-set deletion rooted at `/tmp` itself is a shared-temp
  hazard, and the lesson is to namespace the venue and scope the cleanup to it.
- **No daemon code, no prompt artifact, no metric label, no vocabulary bump, no new dependency,
  no live-store write, no live-system write, no POK write, no schema change, no promote** — the
  release is docs + one operator script + its unit pin + the version fileset.

---

## How to verify

Run on the MERGED tree (from a worktree at the release tip; the worktree venv, never the main
`.venv`):

```sh
WT=/home/nea/ensemble-src-wt-sonic-integration
cd "$WT"

# 1 — the probe pack (the registered `dump_lane_staleness_probe_unit_test` pack)
ENSEMBLE_SELF_ENV=dev timeout 300 ./scripts/run_tests_scrubbed.sh \
  tests/unit/test_dump_lane_staleness_probe.py -p no:randomly -q
# expect: 39 passed

# 2 — the node census (probe 36→39; the re-stamped grant pack 10→18)
ENSEMBLE_SELF_ENV=dev .venv/bin/python -m pytest \
  tests/unit/test_dump_lane_staleness_probe.py --collect-only -p no:randomly -q | tail -1   # 39
ENSEMBLE_SELF_ENV=dev .venv/bin/python -m pytest \
  tests/unit/test_pok_grant_pins.py --collect-only -p no:randomly -q | tail -1               # 18

# 3 — the version-bearing node (post-bump)
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST \
    -u POSTGRES_PORT -u DATABASE_URL_POSTGRES -u ENSEMBLE_AUTH_ENABLED \
  .venv/bin/pytest tests/test_api.py::test_health_check -p no:randomly \
  --override-ini="addopts=" -q
# expect: 1 passed

# 4 — the frozen invariants (must equal the base values byte-for-byte)
sha256sum .agents/shared/metrics/tool-categories.json agents/.prompt-artifacts.sha256
# expect: 66832459500763ba3eb9b268cb828b9d28390d5144bd5694deab6076ffba0365  tool-categories.json
#          3c089361c9862b08861c17763037beae7a746840eac0190a59ed8f79d2aaa443  .prompt-artifacts.sha256

# 5 — the prompt-artifact baseline, via the HOUSE generator
.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check
# expect: PROMPT-BASELINE: OK — 217 versioned artifact(s) match ; rc=0

# 6 — the shipped probe sha256 + the version fileset
sha256sum scripts/audit/dump_lane_staleness.py   # 9f87040f07628fd103d72002474c78641048f827e50a200059fb4886d775e153
grep -n '^version' pyproject.toml                # version = "0.31.54"
grep -n '__version__' daemon/__init__.py         # __version__ = "0.31.54"

# 7 — R3(i): a non-finite threshold refuses in the 78 family (no traceback)
.venv/bin/python scripts/audit/dump_lane_staleness.py --provider file \
  --marker-file /tmp/probe-marker --max-age-hours nan; echo "exit=$?"
# expect: exit=78 + one RESULT line (reason=bad_max_age_hours)

# 8 — R1: the documented invocation VERBATIM from a ≥ v0.31.53 checkout
#     (credential from the live install env file into PGPASSWORD — never argv, never echoed)
WT_AUDIT=/home/nea/ensemble-src-wt-audit
git -C "$WT_AUDIT" checkout --detach v0.31.54
cd "$WT_AUDIT"
test -f scripts/audit/dump_lane_staleness.py || { echo 'CONFIG (78): probe not in this checkout' >&2; exit 78; }
PGPASSWORD='…' .venv/bin/python scripts/audit/dump_lane_staleness.py \
  --db-url "postgresql+psycopg://ensemble@10.44.0.2:5432/ensemble_prod"; echo "exit=$?"
# expect: exit=75 + reason=db_query_error (the heartbeat table does not exist yet — R4 OPEN)

# 9 — the PRE-EXISTING reds (base debt, NOT this lane): expect the SAME 3 FAILED
ENSEMBLE_SELF_ENV=dev ./scripts/run_tests_scrubbed.sh \
  tests/unit/test_anomaly_report_grant_pins.py -p no:randomly -q
```

---

## Bash-raises used

**none.** No command exceeded the default 600 s budget, so no raise was requested from
sonic-leader and none was consumed. The long-running work — the stage's PyInstaller + frontend
build, the `uv sync` and `npm ci` pre-warm — was run as BACKGROUND processes (`proc`), not as
blocking bash calls. The stage wall-clock is reported in the sonic-developer report to
sonic-leader.

---

## Complexity self-assessment

**medium** — assessed after the plan and the module split, BEFORE any coder was dispatched
(§2.5 of the sonic-developer workflow), and NOT softened afterwards.

Reasons from the plan:
- **Module A (runbook-design)** is low-complexity: two documentation files, mechanical
  self-checks, no code.
- **Module B (probe-edges)** is the load-bearing one — two real defects in a 729-line operator
  script plus three hermetic pins and two mutation proofs — but it is bounded to one script,
  one test file and one registry row, with NO daemon code and NO new dependency.
- **Module C (citations)** is low-complexity: two one-line citation corrections plus a filing.
- **Module D (release-notes-bump)** is low-complexity: a house generator run, with the release
  note authored from a verified draft.
- The **release cycle** (bump → merge → tag → stage → push) is a ceremony surface, but it is
  machinery this lane has run before (v0.31.53) and it carries no design risk.

Why it did not reach high: the four modules had DISJOINT file sets, every module fitted one
sonic-coder one-shot turn, there was no daemon code, no schema/migration, no live-store write,
no POK write, no live-system write, and all four merges resolved with ZERO conflicts. Why it was
not low: the slice ends in a release ceremony (bump + tag + stage + push) and it carries one
declared cross-module seam (B's coverage node depends on A's rows — RED in isolation, GREEN at
integration) that cannot be decomposed away without changing the pin's meaning.

**No handover was overridden** — the verdict was `medium` and the work proceeded on that verdict.

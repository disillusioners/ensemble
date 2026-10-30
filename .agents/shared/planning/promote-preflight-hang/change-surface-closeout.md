# Plan §8 change-surface — REGENERATED CLOSE-OUT (wave 4, 2026-10-30)

**Authoritative.** Regenerated from the ACTUAL final diff
`git diff --stat b1183a637..HEAD` at the phase-6 commit (`dfd82b3b1`) — this
supersedes plan-overview §8's pre-implementation projection (which still
scoped the read op into `journal_splice.py` — the approver's item 4; splice
is byte-unchanged throughout, sha256
`50c406a4769025661bb76c006869d274f39967edb0a182342380798d0a8eb84b` == base).

## Shipped change surface (daemon + scripts ONLY — these are the files the
stage rollback-safety adjudication must reason about)

| File | Delta | What changed |
|---|---|---|
| `scripts/upgrade/journal_read.py` | **NEW** (381 L) | the O(N) journal read/RMW helper (three algorithms: verify / sub / update; rc 0/1/2/3/4; stdlib-only 3.7+) |
| `scripts/upgrade/lib.sh` | +801/−78 net | fast paths + verbatim `*_legacy` fallbacks + size guard (promote-path-scoped refusal) + batched `_tree_manifest` + bounded `_stage_tree_walk` + L1/L2a watchdog machinery + two-file sink + `PIPELINE_SCRIPTS_REV` stamp |
| `scripts/upgrade/promote.sh` | +169 | `JOURNAL_READ_STRICT=1` (plain, non-exported) + halt-row on read refusal + preflight budget/deadline arming + watchdog-token refusal + executor-start stamp echo + `promote-start` journal row |
| `scripts/upgrade/stage.sh` | +52/−3 | three bounded tree walks + explicit success-path lock release + **release-time `PIPELINE_SCRIPTS_FLOOR_COMMIT` set-and-validate** + manifest field `pipeline_scripts_floor_commit` |
| `scripts/upgrade/status.sh` | +8 | plain-mode exit 1 on an unreadable journal (SC4b) |
| `daemon/tools/upgrade_journal.py` | +211 | executor env allowlist (`ENSEMBLE_PROMOTE_WATCHDOG`) + per-run two-file sink + client-stderr-to-file + sweep enrichment + `bounded_git_probe` (phase-6 guard support) |
| `daemon/tools/upgrade_tools.py` | +302 | **phase-6 guard**: `PIPELINE_SCRIPTS_FLOOR_COMMIT` constant + `scripts_dir_provenance_verdict` seam (END-of-module) + arm-path Check 4 before `consume_pending_action` + journal-always `arm-scripts-provenance` row + `stale-scripts-dir` refusal |
| `daemon/manager.py` | +24 | promote-arm `RuntimeMaxSec` (derived L3) + **`ENSEMBLE_PROMOTE_WATCHDOG=1` at the promote drain seam** |
| `daemon/services/upgrade_journal_sweep.py` | +88 | enriched `executor_still_running` rows (narrative path + last phase marker + elapsed-since-journal-activity) |

**Total shipped: 9 files, +1,958 / −78.** `journal_splice.py` byte-identical
to base (R4). **Migration delta: EMPTY** — `git diff --name-only
b1183a637..HEAD -- daemon/migrations/versions/` = 0 files; 115 == 115 == 115
(base == branch HEAD == the embedded-binary census recorded in
`stage-adjudication.md`). No schema mutation ships; rollback-safety =
delta-empty-legitimate (`=1`, source=explicit — recorded in the stage
adjudication).

## Non-shipping surface (tests + evidence — NOT in any release payload)

`tests/fixtures/journal/*` (27 fixtures), `tests/helpers/` (corpus, incident,
pack-common, ceremony probe, mutation apply), `tests/packs/preflight_*`
(A–J + matrix + soak + measurement), `tests/unit/tools/` (3 test files),
the plan dir + runbook amendment (`docs/runbooks/upgrade-overhaul-watch.md`
§B.3a + §F).

## Release-process obligation carried by this lane (T6.15/D-F7/D6-8)

`PIPELINE_SCRIPTS_FLOOR_COMMIT` ships UNSET ("" — the value is the merge
commit, which does not exist until merge). The release bump MUST set BOTH:
1. `daemon/tools/upgrade_tools.py` → `PIPELINE_SCRIPTS_FLOOR_COMMIT = "<merge-commit>"`
2. `scripts/upgrade/lib.sh` → `PIPELINE_SCRIPTS_REV=<merge-commit>`

stage.sh REFUSES to stage while the floor is unset/unresolvable/non-ancestor
(the T6.15 gate is live as of `dfd82b3b1`) — the giter/leader release lane
owns the set step at bump time.

# hbk-err inode-churn — fix record

**Lane:** `fix/hbk-err-inode-churn` · **Base:** `1b9797ab7` (origin/latest, v0.31.26 tag commit)
**Commission:** Emperor 2026-11-22 (board `wi-a5981a4e`, anomaly `84be2e4e`)
**Phases:** diagnose (accepted, separate report) → fix (this document)

---

## 1. Root cause (accepted)

`scripts/upgrade/lib.sh` minted **three** `/tmp` side-channel files **at source time**:

| site | channel | file |
|---|---|---|
| `lib.sh:5939` (pre-fix) | hand-back error | `ensemble-hbk-err.<pid>.<XXXXXX>` |
| `lib.sh:5995` (pre-fix) | verify reason | `ensemble-hbk-verify.<pid>.<XXXXXX>` |
| `lib.sh:6032` (pre-fix) | verify elapsed | `ensemble-hbk-velapsed.<pid>.<XXXXXX>` |

`mktemp` **creates** the file, so every shell that sourced `lib.sh` burned three
zero-byte inodes. The channels are the subshell-proof bridge for the hand-back
verdict (a writer inside `$()`, a reader in the parent shell), so the path must
be agreed in the parent — but the *file* need not exist until it carries a
payload.

**Empty was by design, not a swallowed error.** All three readers gate on
`[ -s "$_f" ]`, so absent ≡ empty ≡ "nothing captured". The `err` channel is
launchd-only (its two writers live inside `_supervision_launchd_bootstrap` /
`_supervision_launchd_plist_path`) and is an **unwritten** channel on
Linux/systemd by construction. All 15 `reason` writers already carried a
`_hb_verify_diag` sibling.

**The churn engine was the TEST SUITE, not the daemon.** The daemon spawns
exactly two lib.sh-sourcing shells, both one-shot ceremony paths
(`daemon/manager.py:6697` → `restart.sh`, `daemon/manager.py:6720` →
`promote.sh`) ⇒ **3 files per ceremony**, not per second. The bulk came from
test files that source `lib.sh` 2–49 times each, mostly without an exported
`TMPDIR`.

**No external reader exists** — the only references to the `ensemble-hbk-*`
pattern in the whole repo are the three mint sites, one unrelated
`mktemp -t hbk.XXXXXX` (always removed), and two prose mentions in the
2026-11-14 incident document.

---

## 2. The fix (four parts)

### (1) Lazy mint — a file exists ONLY when it carries a payload

* source time: `mktemp -u "${TMPDIR:-/tmp}/ensemble-hbk-<class>.$$.XXXXXX"`
  — a unique name, **nothing created**.
* write time: `_handback_channel_write` — the O_EXCL claim
  (`( set -C; : > "$f" )`) followed by the payload write.
* `$$` is kept (the parent shell's pid inside a bash subshell — the readers
  expect it); `BASHPID` is **not** used.
* the legacy `$$`-keyed no-suffix fallback still names a valid path, and the
  writer is **path-driven** (it writes wherever the variable points).
* **the readers are unchanged** — all three already gate on `-s`.

Repeat writes truncate-and-overwrite (last-writer-wins), preserving the
pre-fix semantics: the reason channel is written up to 15 times per verify.

### (2) Cleanup of all three channels

* `_handback_channels_cleanup` removes all three; it is hooked **into** the
  existing `_trap_safe_exit` body (`lib.sh`) — the trap is *not* replaced, so
  the ceremony scripts that install their own EXIT trap (promote/stage/
  stop/restart/rollback via `_trap_install_signal_handlers`) keep theirs and
  also get the cleanup.
* the **elapsed** channel — cleaned by nobody before — is now removed by
  `promote.sh` / `restart.sh` / `rollback.sh` alongside the other two.
* the **reason** channel is now cleared at verify entry
  (`_supervision_handback_unit`) — the stale-read hole: the path is minted
  once per shell, so a reason written by an earlier attempt that failed
  *without* writing a reason could be surfaced by a later attempt.
* `status.sh` / `stage.sh` (no traps, no cleanup) need no edit: under lazy
  mint they never create a file at all, and they write no payload.

### (3) Pack containment

The seven offenders (source-statement counts) export a per-run scratch
`TMPDIR`, so the channels — and every other fixture that honours `TMPDIR` —
stay out of the shared `/tmp` root:

| file | sources |
|---|---|
| `tests/test_supervision_stop_handback.sh` | 49 |
| `tests/test_release_journal.sh` | 35 |
| `tests/test_rollback_target.sh` | 12 |
| `tests/test_promote_plugin_staleness.sh` | 12 |
| `tests/packs/promote_handback_halt_serving_green_test.sh` | 10 |
| `tests/packs/preflight_watchdog_test.sh` | 9 |
| `tests/packs/preflight_census_test.sh` | 2 |

The export is placed **before** `pf_fence_init` in the two preflight packs:
the fence's cleanup removes `"$TMPDIR"/pf-sandbox.*` and `"$TMPDIR"/pf-home.*`,
so a `TMPDIR` changed *after* the sandbox was created would leave the sandbox
behind.

### (4) Logging symmetry — the one real hole

The `err` channel was the only side-channel whose writer had **no**
normal-logging sibling: its diagnosis reached the operator only at the reader
site, two hops later. `_handback_err_file_write` now emits a
`_hb_verify_diag` (dual sink: narrative file + unbuffered stderr) at the
writer, so a hand-back error can never be swallowed.

`_handback_verify_elapsed_write` emits a **stderr-only** line: the verify
runs inside a `$()` whose stdout **is** the captured MainPID, so stdout is
never a legal seam there.

---

## 3. The pins — `tests/test_hbk_err_inode_churn.sh`

| leg | what it pins |
|---|---|
| L1 | the inode-churn pin: 50 sources ⇒ **zero** files (a regression would mint 150) |
| L2 | a write creates exactly one file per channel, with the payload; the reader returns it |
| L3 | last-writer-wins preserved |
| L4 | absent ≡ empty at the reader |
| L5 | the structural guard: the three source-time assignments must be non-creating |
| L6 | the legacy fallback still names a valid path; the writer is path-driven |
| L7 | logging symmetry, one rule per channel, all three covered |
| L8 | cleanup: all three on exit, the elapsed in the ceremony scripts, the reason at verify entry |
| L9 | the mutation self-test (the pack proves it can fail) |

**Mutation proof (executed):** with the fix disabled the pack goes
**27P/9F** (L1/L5/L9 red); with it re-enabled it returns **36P/0F**, exit 0.
The restore was verified byte-identical (sha256
`58d4d1c4d1568688ab6f27b21ecf9b9305aef9a9230e755ae58d8fde451857d5`).

---

## 4. Release mechanics — the PIPELINE_SCRIPTS pair

`scripts/upgrade/lib.sh` is a member of `EXECUTOR_FAMILY_PATHS`
(`daemon/tools/upgrade_journal.py`), so this edit changes the executor-family
manifest. **There is no stored family-hash constant** — the manifest is
computed at runtime by hashing the members, and the shell list / python mirror
parity gate is about the *paths*, which this change does not alter.

The `PIPELINE_SCRIPTS_REV` (lib.sh) / `PIPELINE_SCRIPTS_FLOOR_COMMIT`
(`daemon/tools/upgrade_tools.py`) pair was **evaluated and deliberately NOT
changed** in this branch. Evidence (all five stage-time gates executed against
this tree):

| gate | verdict |
|---|---|
| `_pipeline_scripts_floor_verdict` (floor resolves + is an ancestor of the built tree) | **PASS** |
| `_pipeline_scripts_stamp_verdict` (40-hex + resolves + equals the floor) | **PASS** |
| `executor_family_verify_stamp_coverage` (every shipped family path exists at the stamped commit — `git cat-file -e`, i.e. **existence**, not byte-parity) | **PASS** |
| the floor is an ancestor of the branch tip (the arm-time MINIMUM) | **PASS** |
| stamp == floor (`e8695f1739fd3b3130fe495977053fbf46ae1c42`) | **PASS** |

⇒ **stage.sh will not refuse**, and the arm-time floor gate is satisfied
(the scripts tree is a descendant of the floor). A pair bump is therefore
**not required** for this change.

**Optional follow-up (house convention, leader-owned, post-merge).** Both prior
lanes that changed ceremony-relevant scripts semantics re-pinned the pair
*after* their merge, naming the **merge commit** (`96cc3855e` →
`e8695f1739…`; before that `70a0960e5…`). If the Emperor wants the same
convention for this lane, the single-writer pair bump is:

* **file:** `scripts/upgrade/lib.sh`, line 95 —
  `PIPELINE_SCRIPTS_REV=<MERGE_SHA>`
* **file:** `daemon/tools/upgrade_tools.py`, line 4509 —
  `PIPELINE_SCRIPTS_FLOOR_COMMIT = "<MERGE_SHA>"`
* **value:** the `--no-ff` merge commit of `fix/hbk-err-inode-churn` into
  `latest` — it does not exist until the merge, so it **cannot** be set in
  this branch (naming a lane commit instead would break the gate if the
  branch were ever squash-merged: the sha would stop resolving).
* both sides in ONE commit ("single-writer pair bump"), then re-stage.

**Pre-m10 tolerance.** The plain-path (pre-`c62b03cfc`) shape created the
file only at write time; the 212 surviving mode-644
`ensemble-hbk-verify.<pid>` files on the host are exactly those artifacts.
Nothing reads them by name, and the writer is path-driven, so both shapes stay
valid.

---

## 5. Scoped test results (honest, with BASE attribution)

Every row was run on the FIXED tree; every red was compared against the BASE
(`1b9797ab7`, extracted with `git archive` into a scratch dir — the same
method the house uses for base-vs-HEAD rows).

| suite | FIXED | BASE | verdict |
|---|---|---|---|
| `tests/test_hbk_err_inode_churn.sh` (NEW) | **36P/0F**, rc=0 | — | the new pin is green |
| `tests/test_supervision_stop_handback.sh` | 232P/33F/1S | 232P/33F/1S | **failure-NAME diff EMPTY ⇒ net-new-red = 0** |
| `tests/packs/handback_verify_budget_test.sh` | 120P/0F, rc=0 | — | green (the D6-v elapsed channel) |
| `tests/packs/promote_handback_halt_gate_legs_test.sh` | 86/86 asserts, rc=0 | — | green (the REASON pin) |
| `tests/packs/promote_handback_halt_serving_green_test.sh` | 136P/4F | house record: 136P/4F, same 4 names | pre-existing |
| `tests/test_promote_plugin_staleness.sh` | rc=0 | — | green |
| `tests/test_rollback_target.sh` | 34P/1F (`C6 halt event in history`) | 34P/1F, **same leg** | pre-existing |
| `tests/packs/preflight_watchdog_test.sh` | 67P/1F (`D13a`) | 67P/1F, **same leg** | pre-existing |
| `tests/packs/preflight_census_test.sh` | 9P/1F (`HEAD census total is 37, want 26`) | census total **recomputed = 37** at the BASE tree (the pack's own BASE leg needs the git object `b1183a637`, unavailable in an archive extraction) | pre-existing |
| `tests/packs/preflight_logging_test.sh` | 32P/1F (`EPIPE call-site redirects found=3 want 2`) | 32P/1F, **same leg** | pre-existing |
| `tests/packs/preflight_anchor_index_test.sh` | rc=0 | — | green (the anchor/census line pins) |
| `tests/packs/preflight_stage_lock_test.sh` | rc=0 | — | green |
| `tests/packs/preflight_ceremony_readiness_test.sh` | rc=0 | — | green (the PIPELINE_SCRIPTS stamps) |
| `tests/test_pipeline_scripts_floor_stamp.sh` | 48P/0F, rc=0 | — | green (the pair gate) |
| `tests/test_executor_family_payload.sh` | 103P/1F (`scripts/upgrade/boot_smoke.sh` not in the family list — added 2026-11-19, `b2d54e849`) | 102P/2F (same leg + an import leg the extraction cannot satisfy) | pre-existing; the fixed tree is **one leg better** |
| `tests/test_release_journal.sh` | see the note below | — | — |

The four pre-existing reds named above are **not** attributable to this lane:
each is a version-string / census-total / redirect-count drift in a file this
lane did not touch, and each reproduces at the BASE.

**`tests/test_release_journal.sh` — the 600 s budget is a known artifact.**
PACKS.md records this suite as *over-budget at the current tip*
("mandated `timeout 600 bash tests/test_release_journal.sh` ⇒ RC=124
(over-budget at this tip)"), and the house completes it with
`env -i PATH HOME TERM timeout 1200 …`. The 600 s run therefore returns
RC=124 with a partial log — that is the BUDGET, not a regression.

---

## 6. Disclosed residuals / out of scope

* **The remaining source-count files** (`tests/test_supervision_classify.sh` 9,
  `tests/test_promote_cgroup_survivorship.sh` 9, `tests/test_journal_splice.sh` 9,
  `tests/test_stage_plugins_tree.sh` 7, `tests/packs/preflight_logging_test.sh` 7,
  `tests/test_supervision_journal.sh` 6, `tests/helpers/soak_gate_pack_common.sh` 5,
  `tests/test_guidelines_payload.sh` 4, `tests/test_atomic_flip.sh` 4,
  `tests/packs/preflight_stage_lock_test.sh` 4,
  `tests/test_stage_freshness_guard.sh` 3, `tests/test_bounded_subprocess_waits.sh` 3,
  `tests/test_adaptive_soak.sh` 3, `tests/packs/promote_config_compat_test.sh` 3, …)
  did **not** get a scratch `TMPDIR`. Under lazy mint they no longer create
  files at all; the export is a belt-and-braces containment. The helper
  `soak_gate_pack_common.sh` was left alone deliberately — exporting a
  `TMPDIR` inside a shared helper would move *its consumers'* scratch.
* **The packs' own hardcoded `/tmp` scratch files** (`/tmp/c5.stderr`,
  `/tmp/stale-strict.err`, `/tmp/ens-serving-probe-$$.list`, …) are a
  pre-existing leak class, not this lane's subject.
* **The historical incident document** (`docs/incidents/2026-11-14-macos-launchd-handback-halt.md`)
  was **not** rewritten — history stays. Its line 540 already flagged the
  pattern as a "sweeper candidate".
* **The giter disk-cleanup REPORT.md attribution line** ("~1/sec from v0.31.10
  daemon children") was **not** edited — the staging lane owns that
  correction.
* The D6-v journal sample is seeded through the **journal**
  (`state.json` history), never through the channel file — so the lazy mint
  cannot break the measured-ceiling mechanism.

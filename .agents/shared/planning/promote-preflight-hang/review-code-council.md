# Code Council Review — promote-preflight-hang (board wi-fc1786a2)

**APPROVE-WITH-ADVISORIES** — 0 CRITICAL, 3 WARNING, 9 ADVISORY.

Diff reviewed: `b1183a637..f76765e6a` (10 commits), worktree `/home/nea/ensemble-src-wt-preflight-hang`,
branch `fix/promote-preflight-hang`, tree clean at review time. Read-only review: the only file I
wrote is this one. Packs I ran are the lane's own fenced packs (each owns `pf_fence_init`: mktemp
sandbox + mktemp FAKE_HOME + per-variable `POSTGRES_*` scrub); I did **not** run the mutation matrix
in execute mode — `preflight_mutation_matrix_test.sh` rewrites **real tracked files** in this
worktree, which is outside my read-only mandate (dry-run only; see §6).

The premise is not in dispute and I did not re-litigate it: the O(N²) `journal_read` walk on a
455,783 B journal is the 2026-10-28 37-minute preflight spin, and the fix is the O(N) port plus a
layered watchdog. I reviewed the fix, not the premise.

---

## Findings

### CRITICAL (blocking)

None.

### WARNING (should-fix)

#### W1 — `_run_bounded_step adopt-txn` wraps a **mutating** recovery, and its `|| _refuse` arm now double-records + mislabels every adopt refusal

`scripts/upgrade/promote.sh:353`

```bash
_run_bounded_step adopt-txn adopt_stale_txn || _refuse txn-busy "promote refused: unresolved in_flight txn ..."
```

Two distinct defects at one call site.

**(a) Contract violation.** The wrapper's own binding text — `lib.sh:476-479` — reads: *"the wrapped
step MUST be side-effect-free-on-kill or process-group-killed. Every phase-2 wrap site is a
READ-ONLY step … the side-effecting ceremonies … are deliberately NOT wrapped; a kill mid-
`retention_evict`/stop would leave half-mutated state (the MUST-NOT-WRAP rows in phase2-plan §3.3b)."*
`adopt_stale_txn` (`lib.sh:6483-6700`) is not read-only: on the stale-flipped branch it performs
`atomic_flip` (a **symlink swap of `current`**), then `journal_quarantine`, `journal_set_current`,
`journal_count_rollback`, `journal_close_txn`. The code's own comment explains why the flip comes
first — *"if we die mid-sequence the next launcher start re-runs the sweep on this same stale txn"* —
i.e. the recovery is designed to survive death, so this is not catastrophic. But a **new 120 s kill
vector** has been introduced on a step whose healthy cost is milliseconds, and the documented
invariant that "every wrapped site is read-only" is now false as written. Either the invariant text or
the call site must change; silently leaving both is how the next phase wraps `retention_evict`.

**(b) Audit-trail regression (the A-4 class, missed one site over).** `_run_bounded` backgrounds the
step (`"$@" &`), so `adopt_stale_txn`'s internal `exit 78` paths now terminate **the subshell only**.
The wrapper returns 78, and `_refuse txn-busy` then fires — appending a **second** journal row
(`refusal … reason=txn-busy`) **after** the true row. Every adopt refusal therefore ends up
mis-tokened: `previous release $prev is QUARANTINED`, `previous … is NOT rollback_safe`,
`previous release $prev missing`, `cap reached while adopting stale txn`, `kind=restart` and
`unparseable started_at` all get a trailing `txn-busy` refusal that contradicts them. Before this
change the same `exit 78` terminated the script and `_refuse txn-busy` was effectively dead code
(`adopt_stale_txn` returns 0 or exits), so this is a behaviour change, not a preserved one.

This is exactly the defect the developer closed for the journal-read sites under A-4 — `promote.sh:301-311`
and `:407-415` both carry an `if [ "$X" -eq "$BUDGET_BREACH_RC" ]; then … exit … fi` arm with the
comment *"write NO row of its own — a caller-side row would be mislabelled … and would double-record
a single breach event."* The identical treatment was not applied to `adopt-txn` (nor is the rc-78
propagation distinguished there).

**Why it matters here specifically:** this lane exists so that a failed ceremony is triageable from the
journal. On the post-incident path (an orphaned stale txn is precisely the 2026-10-28 shape) the
operator's last row will name the wrong reason.

**Fix.** Keep the bound but stop the mislabel:
1. change the arm to `_run_bounded_step adopt-txn adopt_stale_txn || { rc=$?; [ "$rc" -eq 78 ] && exit 78; _refuse txn-busy "…"; }`
   — i.e. propagate a refusal without inventing a second token; and
2. correct `lib.sh:476-479` to state the real rule ("read-only **or death-idempotent** by its own
   design; wrapped sites are enumerated in phase2-plan §3.3b plus `adopt_stale_txn`"), with the
   idempotence argument cited at the call site. If the council prefers zero deviation, drop the
   `adopt-txn` wrap entirely and keep the L1 checkpoint that follows it — the step is milliseconds.

#### W2 — the scripts-dir provenance guard **fails OPEN** when `git` runs and fails on a git-shaped dir (`rev-parse` rc ≠ 0, ≠ 255)

`daemon/tools/upgrade_tools.py:4033-4056` (the 255 sentinel band) → fallthrough at `:4116-4121`

The W-1 verifier fix added the git-*shape* discriminator — `if (path / ".git").exists(): refuse else: warn`
— but applied it **only** to `GIT_PROBE_ERROR_RC` (255, "the probe could not run"). Every other
non-zero `rev-parse` result falls through the `if rev_rc == 0:` git branch into the final
non-git branch:

```python
if stamp is not None:
    return ("arm", f"non-git-with-stamp: {provenance} (no git ancestry available — Q2: the stamp is the provenance)")
```

`git` answers **128** — not 255 — for *"not a git repository"*, *and* for dubious ownership
(`safe.directory`), a corrupt/unreadable `.git`, and a gitfile whose worktree metadata was pruned.
So a dir that **is** git-shaped but whose ancestry is unverifiable is classified "non-git" and
**ARMS on a self-asserted stamp** — the one thing the stamp cannot prove. D6-10 says the command-error
class refuses; this is that class.

**Empirically confirmed** (scratch dirs under `/tmp`, the real shipped function, no repo contact):

```
dir with .git/ present + lib.sh carrying a 40-hex stamp
git -C <dir> rev-parse --verify HEAD   -> rc=128 ("not a git repository")
scripts_dir_provenance_verdict(dir, floor_commit=<sha>, env={})
  -> VERDICT: arm
     "non-git-with-stamp: … stamp=0123…4567 (no git ancestry available — Q2: the stamp is the provenance)"
```

Unpinned: Pack I covers I13/I14 (the 255 band) and I4/I5 (genuinely non-git); the 128-on-git-shaped
band has no leg, and `_fake_probe(rev_rc=128)` is not exercised anywhere.

**Fix** (one predicate, mirrors the 255 handler):
```python
if rev_rc != 0 and (path / ".git").exists():
    return ("refuse", f"git-probe-failed (git-shaped dir): git rev-parse rc={rev_rc} … ancestry UNVERIFIABLE; {provenance} floor=…; {_SYNC_REMEDY}")
```
then the existing non-git branch handles only dirs with no `.git` **and** a non-zero rc. Add one Pack I
leg (`I13b`: `.git` present, `rev_parse` rc=128 ⇒ refuse) and one M-leg.

#### W3 — the supervision `systemctl` budget breach is **not kind-scoped**: it halts `restart.sh` (which holds no lock) and loses its halt row

`scripts/upgrade/lib.sh:5682-5694` (`_df_systemctl`) → `_budget_breach … exit 78`;
reached from `scripts/upgrade/lib.sh:5807` (`supervision_dualfight_check`) ← `supervision_preflight`
← **`scripts/upgrade/restart.sh:199`** and **`scripts/upgrade/rollback.sh:154`**

`_budget_breach`'s doc block claims *"LOCKED-PATH ONLY: callers hold the pipeline lock (the promote
preflight and the supervision probes all run under it)"*. That is false for `restart.sh`: it has **no
`lock_acquire` at all** (grep: no occurrence in the file) and calls `supervision_preflight` directly.
`rollback.sh` acquires the lock at `:140`, so it is covered — `restart.sh` is not.

On a unit-mode restart with a wedged D-Bus:
1. `_df_systemctl` → rc 124 → `_budget_breach SYSTEMCTL_PROBE_BUDGET_S …` → `exit 78`. A **recovery-
   class** script is fail-fast halted. R1 is explicit: *"non-promote paths (status / rollback /
   restart / adopt-unit / stop) ⇒ WARN + bounded (120s) + continue. NO fail-fast on recovery paths."*
2. `journal_history_append halt` runs **first** — and on the live install `journal_splice.py`'s
   `_pipeline_lock_held` fence refuses the append because no lock is held, so the row is swallowed by
   `|| true`. The breach is therefore **both fatal and unrecorded**: the exact "loud failure must be
   journaled" promise this lane is built on, inverted.

This is a live-path behaviour change (BASE: the probe hung; now: exit 78 with no row), it is
undisclosed, and the watchdog pack's D10 leg exercises `_df_systemctl` in isolation — never through
`restart.sh`.

**Fix.** Pass the kind into the probe, or branch on `_PIPELINE_PHASE` inside `_df_systemctl`:
`promote:*` ⇒ `_budget_breach` (unchanged); any other phase ⇒ bounded probe + `_warn` + continue
(return the probe's own rc, treating 124 as "unknown/not-active" so the dual-fight check degrades to
"cannot classify" rather than a false FAULT). Pin both arms (promote breach ⇒ 78 + row; restart
breach ⇒ continue + no refusal). Fix the `_budget_breach` doc block; it currently over-claims.

### ADVISORY

| # | Where | Issue | Fix |
|---|-------|-------|-----|
| A1 | `lib.sh:2691-2712` (`_tree_manifest`) | On a budget kill, `_run_bounded` TERMs the **subshell** only; the `xargs … shasum` grandchildren survive, keep hashing, and the `mktemp` `$list`/`$sums` files leak (the subshell's `trap … EXIT` does not run on TERM). Bounded in practice (seconds), but it is an orphan class the lane is otherwise careful about, on both the stage walk and the wrapped `integrity_verify` steps. | `setsid` the batch (or kill `-$pid`) inside the walk; or `GIT`-free `kill -- -$$`-style group kill in `_run_bounded`. Align with the "cgroup-wide TERM" invariant. |
| A2 | `upgrade_journal.py:2942` vs `lib.sh:6342-6343` | `payload_path.with_suffix(".client.log")` on `<run_id>.payload.log` yields **`<run_id>.payload.client.log`**, not `<run_id>.client.log`. `_run_log_retention` fences `${run_id}.log` / `.payload.log` / `.client.log` and the pointer line names neither, so the current run's client log is protected **only** by the `<6 h mtime` fence (it is, in practice, because only the owner runs retention — but the documented 5-fence model is off by one name). | Either name the file `<run_id>.client.log` (`payload_path.with_name(f"{run_id}.client.log")`) or add the fourth name to the fence and to the pointer line. Add a retention leg over the real four-file family. |
| A3 | `upgrade_tools.py:4033-4092` | **A-1 verdict — see §5 below.** Routing is defensible; two cheap mitigations are missing. | `GIT_OPTIONAL_LOCKS=0` in `bounded_git_probe`'s env (makes the A-2 index-refresh caveat inert and removes index.lock contention), and consider dropping the `status --porcelain` probe from the gate path — it is ~90 % of the measured ~0.1 s and its output is *"an audit artefact, never a gate input"* by the code's own comment. |
| A4 | `preflight_size_guard_test.sh:150-166` | R1's matrix is pinned as **three classes** (`promote` / `recovery` / `diagnostic`), not the six script paths. Per-script routing is asserted only by grep/anchor (`JOURNAL_READ_STRICT` set in `promote.sh` alone, non-exported). Adequate because the guard lives *inside* `journal_read` (so no call site can be un-routed), but the brief asked for the six paths separately. | Add six thin legs (`rollback.sh`, `restart.sh`, `status.sh`, `adopt-unit.sh`, `stop-ensemble.sh` via `pipeline_settled`, `stage.sh`) asserting each resolves over-threshold to WARN+CONTINUE. Cheap: the fixtures already exist. |
| A5 | `promote.sh:246`; `lib.sh:490` | Garbage numeric knobs: `PREFLIGHT_PHASE_BUDGET_S=abc` → **hard crash** `abc: unbound variable` at `:246` (the file's own comment at `:545` calls that class out as a past bug); `PREFLIGHT_STEP_BUDGET_S=abc` → `sleep` rejects it, the watcher fires instantly and the **first** wrapped step halts with `elapsed=0s`. Both are loud and fail-closed, so this is cosmetic-to-moderate, not dangerous. | Validate with `case "$X" in ''|*[!0-9]*) X=<default>;; esac` (the idiom already used for `size` in `_journal_read_fallback`), and let `_budget_in_force` refuse a non-numeric value instead of echoing it into the row. |
| A6 | `status.sh:171-173` | Plain-mode exit code changes 0 → 1 when the journal is unreadable/over-budget. Deliberate (`D2-6/SC4b`) and there is **no in-repo consumer** of `status.sh`'s rc (grep: none in `daemon/` or `scripts/`), but it is an operator-visible contract change on a read-only tool. | Document in the runbook's status section (the runbook only says "status.sh exits 1" in passing) and note that `--verify` also now folds it into `rc=1` at `:157`. |
| A7 | `upgrade_journal.py:2946-2995` | R7's degraded case: if `client_err_path.open("ab")` fails (unwritable log dir) `stderr` becomes `DEVNULL`, and if the later tail read also fails, `err_tail=b""` ⇒ `_is_systemd_run_failure` False ⇒ the **fail-closed** `_refuse_systemd_unavailable` silently degrades to "no signature". The happy path is correct and pinned (the file is only read after `proc.wait()` returns, so the child's bytes are complete). Narrow, but it is a fail-closed guarantee with a silent hole. | On `client_err_fh is None`, skip the "no signature" downgrade and take the refusal path (or journal a `client-log-unavailable` note). One `if`. |
| A8 | `upgrade_journal_sweep.py:2260-2280` | `_per_run_observation` does `json.load()` on the **full** `releases/state.json` (455 KB+ today, unbounded `history` growth) **synchronously inside `async def _journal_executor_still_running`**. ~5-15 ms today, on the loop, on every reaper still-running row. This is a small instance of the R-1 event-loop class the project already has a lane for. | Read only the tail (`history[-1]`) via a bounded byte-tail + `json.loads` of the last element, or `await asyncio.to_thread(...)`. |
| A9 | `phase1-plan.md:115` vs `implementation-decisions.md` D-3 | The plan row still asserts G5 = **"ACCEPT (BASE)"**; the executed fixture (`{"a":"x\<NUL>"}`) makes BASE **REFUSE**, and D-3 records that correction with the measured rationale (and the mutation-meaningfulness leg: a raw-byte scanner ACCEPTS). Doc-only, but the count-sweep gate cannot catch a semantic contradiction, so a reader of the plan alone gets the wrong direction. | Amend the G5 row to point at D-3 (a one-line supersession note), same discipline the plan set already uses elsewhere. |

---

## 1. Semantic parity of the O(N) port — **VERIFIED (highest-confidence item)**

I ran the differential pack myself: `preflight_journal_parity_test.sh` → **60 passed, 0 failed**, with
BASE as the oracle (function bodies extracted verbatim from `b1183a637:scripts/upgrade/lib.sh`, not
re-typed). What the pass actually covers, and what I independently checked:

| Contract | Verdict |
|---|---|
| Verdict + **stdout byte-identity** over the `$(cat)`-processed domain (22 corpus + G1-G5 = 27) | passes, incl. trailing-newline strip and NUL drop (`_read_processed`, `journal_read.py:119-137` — `rstrip(b"\n")` ≡ `$( )`, `replace(b"\x00", b"")` ≡ bash) |
| **Duplicate-key bug preserved**, not silently fixed | passes; pinned to the measured corrupt string `{"x":10,"b":2,"x":99,"b":2,"x":20}` (head at the LAST occurrence via `rfind`, value scan at the FIRST via `find` — `journal_read.py:272-278` matches `${rest%*"field"*}` / `${rest#*"field"}`) |
| Exit contract 0/1 + rc 2 = "unusable ⇒ shell falls back" | correct by inspection; `main()`'s bare `except Exception → 2` guarantees an internal error never masquerades as a refusal |
| Malformed tolerance / empty pre-check / stderr class | passes (the `_warn_prefix` reproduction of `lib.sh`'s format is byte-accurate) |
| IFS / locale independence | locale pinned C / C.UTF-8 / en_US.UTF-8; IFS is structurally irrelevant (python reads bytes; the shell wrapper uses `printf '%s'` only) |
| 3 distinct algorithms kept distinct (verify string-AWARE; `sub`/`update` string-NAIVE) | correct — sharing the string-aware machine would have changed `_json_sub`'s extraction set; the code says so and the family robot (`_json_sub_legacy`) is byte-preserved |
| Single read (D1-16) | passes, incl. a 200-iteration concurrent atomic-replace race with no torn view |
| 2-byte vs 2-character escape skip | analytically sound: skipped bytes are only continuation bytes (≥0x80), which can never be `"` or `\` |

**On R5's literal wording** ("BASE refuses, python **accepts**"): the implementation has BASE and the
helper **both refuse**, and proves the pin mutation-meaningful by showing a raw-byte scanner
*accepts* the same bytes. That is a documented, measured reversal (D-3, citing VERDICT-003) and it is
the *correct* call — a raw-byte port would fail stdout parity on every real journal. I record it as
reconciled, not as a deviation; the stale plan row is A9.

**On `_json_sub` returning rc 1 vs BASE printing a partial slice with rc 0 in an unbalanced tail**: I
looked for a reachable divergence and could not construct one — once the `{`/`[` pre-check passes,
depth goes 1 on the first byte and the first `}`/`]` returns, so both always return within two tokens.
Marked **unverified-but-unreachable** rather than asserted safe.

**25-site routing:** verified structurally, and this is the strongest form available — the migration is
**inside** `journal_read` / `_json_sub` / `journal_update`, so **zero call-site edits** and no site can
be left on the legacy path or double-processed. The census (`preflight_census.txt`, pack 10/10) lists
25 BASE sites (17 `lib.sh` + 8 scripts) plus `_run_log_retention` = 26 at HEAD (exactly one new read
site, pinned), plus the three transitive interpreter-less callers R6 demands (`stop-ensemble.sh:224`,
`adopt-unit.sh:201`, `stage.sh:618`). `stop-ensemble.sh` sources `lib.sh` (`:181`) and inherits the
read path with `JOURNAL_READ_STRICT` unset ⇒ warn+continue, as R3 requires. `evidence/CODE-PATHS.md`
matches the graft; the "per-character scans remaining at HEAD: 0" claim holds (all live in `*_legacy`).

**R4 by hash, not by absence:** `journal_splice.py` = `50c406a4769025661bb76c006869d274f39967edb0a182342380798d0a8eb84b`
at HEAD **and** at `b1183a637`, and it does not appear in `--name-only`. Byte-unchanged, confirmed.

## 2. Watchdog correctness — **VERIFIED except W3**

- Bounds: 120 s/step, 300 s/phase, `RuntimeMaxSec=max(1200, deadline+soak+120)=1320` at defaults; the
  derivation is single-sourced (`derived_runtime_max_sec`) and strictly above the 900 s ceremony
  deadline, so systemd only fires when the shell's own machinery is wedged. Pinned at 1320/1620/1920/1200.
- Kind-scoping: the **preflight** budgets are armed only in `promote.sh`'s window (`_run_bounded_step`,
  `_enforce_deadline "$preflight_deadline"`) and never around `gate_soak`; `manager.py:5562-5566`
  passes `RuntimeMaxSec` for `kind != "restart"` — restart arms carry none (pinned).
- Breach order (row → WARN → `lock_release` → exit 78): pinned by the watchdog pack, 45/45, and the
  ordering rationale (the splice fence refuses a history append when no live owner holds the lock) is
  real and correctly reasoned.
- Trap-before-lock (`promote.sh:184`) closes the "lock held, no handler" window — a genuine
  reproduction-inside-our-own-fix fix.
- A-4 propagation: `J_READ_RC` / `CUR_READ_RC` compare against `BUDGET_BREACH_RC` and exit without a
  second row. Verified by the D11 leg (1 row, no mislabelled row, lock released). **W1 is the same
  class, one call site over.**
- `_HALT_JOURNALED` re-entrancy guard: correct (flag set before the append) and mutation-verified
  (guard-stripped ⇒ 2 rows).
- **W3**: the supervision probe's 5 s budget escapes kind-scoping into `restart.sh`.

## 3. Lock discipline — **VERIFIED**

Reclaim audit lands **after** acquisition (`lib.sh:2150-2158`), which is required — at break time the
old owner is dead and the splice fence would refuse the row. `lock-stale-break` carries all five
fields. Pack F4 proves it on a **live-shaped** install root (fence interop). Explicit success release
in `stage.sh` + `promote.sh`'s commit seat, and double release is a silent no-op (pinned). No new
orphaning vector: the failure windows are (i) trap-bypassing SIGKILL — pre-existing, now *audited*
rather than invisible, and (ii) the L3 kill, which is scoped above the shell's deadline so the trap
still runs. `mv … || continue` refactor is behaviourally identical to the old `|| continue`.

## 4. Logging — **VERIFIED (with one naming caveat, A2)**

There is **no pipe** in the sink: with `ENSEMBLE_UPGRADE_LOG_FILE` set, `_log`/`_logv`/`_warn` write
the narrative file **directly** (`>> file`, one `write(2)`, 4 KiB cap + CR/LF sanitisation ⇒ one
physical line) and return; a failed write goes to **unredirected stderr** plus exactly one
`logwrite_failed` journal row, with the recursion guard set *before* the append (pinned by M13). The
PAYLOAD file is systemd's `append:` capture of the payload's raw stdio — so the "ordering illusion"
the design worries about cannot arise, because the two streams never share a file. Unset ⇒ legacy
byte-identical stdout/stderr (AC1). Mid-phase markers sit at the same boundaries as the L1
checkpoints and name `phase` + `step` + `elapsed`; within a single step the blind window is bounded by
120 s and the breach names the step, and the stage walk narrates every 500 files. Retention runs under
the lock at both seats with the running shell as owner (R8) — I checked the R8 semantics directly: the
count pass skips fenced files entirely, so "≤20 kept" genuinely holds for the owner. A2 is the one
name mismatch.

## 5. Arm-time guard — **SOUND except W2; A-1 verdict below**

- **Ordering is right in code, not just intent**: `scripts_dir_provenance_verdict` is called at
  `upgrade_tools.py:3713`, `consume_pending_action` at `:3770`. The refusal path releases the lock and
  returns, so **the nonce is not burned** — and this is pinned *behaviourally* (not just textually) by
  `test_w3a_floor_unset_on_an_explicit_pin_refuses`, which asserts `pending_op is None` after the
  refusal. I ran it: 5 provenance legs pass.
- Bounded probe: `subprocess.run(timeout=30)` catching `(OSError, SubprocessError)` covers
  `FileNotFoundError`, `TimeoutExpired`, resource failures and `CalledProcessError`-class faults; the
  sentinel 255 is distinct from any git rc. The status-probe failure is conservatively reported as
  dirty. A-2's purity caveat is honestly stated (`.git/index` refresh possible).
- Ancestry: floor not an ancestor ⇒ refuse (I2/I3 pinned, incl. the missing-object case ⇒
  `floor-object-unknown`); non-git **without** `.git` ⇒ WARN-only (I4/I5). **W2** is the gap.
- Path scope includes `stop-ensemble.sh`? It does not need its own guard: the guard judges the
  **scripts dir** at arm time, and `stop-ensemble.sh` only reads the journal (via `lib.sh`) as a
  child of the locked pipeline. R6 is satisfied in the census + read-path coverage, which is the
  correct reading of "guard path scope" for a child that never arms anything.
- Release-time floor validation cannot be bypassed: `stage.sh:539-565` refuses on unset / unresolvable
  / non-ancestor, `:568-583` refuses a non-40-hex / unresolvable / floor-mismatched stamp, and the
  manifest carries the floor so the artifact is self-describing. **Operationally note:** the shipped
  tree is still pre-release (`PIPELINE_SCRIPTS_REV=unreleased-fix-promote-preflight-hang`,
  `PIPELINE_SCRIPTS_FLOOR_COMMIT=""`), so **stage.sh will refuse until the bump sets both to the merge
  commit** — see §"Ceremony preconditions".

**A-1 — explicit verdict: the ROUTING IS DEFENSIBLE FOR THIS RELEASE.** Measured on this host against
the real `scripts/upgrade` dir with the probe's exact env: `rev-parse` 0.00 s, `status --porcelain`
0.08-0.11 s, `merge-base --is-ancestor` 0.00 s ⇒ **~0.1 s typical**, on an arm path that already
blocks for hundreds of milliseconds to minutes (`journal_lock_acquire`, `_resolve_scripts_dir`,
`read_text` of `promote.sh`). The worst case (~90 s) requires git itself to hang, and it is bounded —
which is the property that matters. D-47 records the routing, the three sites and the rationale, which
is the disclosure standard this project asks for. Two conditions I attach: (a) the follow-up must be a
**tracked item in the loop-lag lane**, not a sentence in a decisions file — it is the only way this
does not become permanent; (b) take the two cheap mitigations in A3 (`GIT_OPTIONAL_LOCKS=0`; drop or
narrow the status probe) — 90 % of the cost and the only non-inert probe.

## 6. Pin quality — **VERIFIED in sample; two gaps**

Packs I executed myself: parity **60/60**, watchdog **45/45**, Pack I **38/38**, logging **30/30**,
size-guard **24/24**, stage-lock **13/13**, census **10/10**, anchor **42/42**, Pack H pytest
**31 passed**, provenance pytest **5 passed**.

Mutation matrix: `--dry-run` only (it rewrites real tracked files; read-only mandate). The TSV records
40 legs, **all `executed`** with `red_rc`/`green_rc` and the asserted red-string. I read
`preflight_mutation_apply.py` and the TSV; the legs are exact-string plants with an exactly-once guard,
and the ones I could check independently are meaningful (e.g. the A-4 leg M36 asserts `halt rows=2` —
i.e. the pin *fails* when the caller-side arm is removed, which is the invariant, not a tautology).
The individual 40 legs are **self-reported, not independently re-executed by me** — flagged as such.

Gaps against the brief's explicit list:
- **R1's six non-promote paths** — pinned as three classes (A4).
- **`rev-parse` rc=128 on a git-shaped dir** — unpinned, and it is the W2 fail-open (a leg here would
  have caught it).
- R5 direction: pinned and mutation-meaningful (both legs, incl. the raw-scanner control).
- R7 refusal: pinned (`test_systemd_unavailable_triage_reads_the_client_file` + M20), with the A7 hole.
- R8 retention: pinned in logging (E-legs) + anchor + stage-lock; the client-log name (A2) is not.
- Refusal-never-burns-the-nonce: pinned **behaviourally** (`pending_op is None`) plus a textual C5
  ordering pin — good defence in depth.

**The `$HOME`-resolution fence is genuinely MANDATORY, not advisory.** `pf_fence_init` exports a
`mktemp` FAKE_HOME and `pf_subshell`-style callers inherit it; Pack I's E1 resolves `resolve_env live`
**before** invoking `promote.sh` and `pf_die`s (exit 2) when the result escapes `$HOME`
(`preflight_ceremony_readiness_test.sh:299-305`), and the daemon-side suite asserts every shell-lane
fixture path is outside `_LIVE_PATH_PATTERNS`. Remove the fence and the packs fail hard — that is the
right shape.

## 7. Scope discipline — **VERIFIED; delta fully attributed**

`git diff --name-only b1183a637..f76765e6a -- daemon scripts` = **exactly the 9 files** in the brief,
+2,108/−79. The brief's "+1,958/−78" is the stat **at `dc5e19728`** — verified: `--shortstat
b1183a637..dc5e19728 -- daemon scripts` = `1958 insertions(+), 78 deletions(-)`. The delta is entirely
post-brief commits: `536522196` touches 5 of the 9 (`+171/−22` in that commit, netting +150/−1 in the
cumulative diff because it rewrites lines it introduced) and `f76765e6a` is docs/tests only (4 files,
`.agents/` + mutation harness). **Nothing out of scope.** No shipped line comes from a foreign lane.

One file outside the "9" and outside tests: **`docs/runbooks/upgrade-overhaul-watch.md` (+86/−1)**. It
rides the docs commit, carries no payload, and is *required* by the commission (it documents the two
breach classes and the new §F provenance gate). Accepted; recorded so the count "9 shipped files" is
not read as "10 non-test files changed".

## 8. Live-contact disclosure — **ACCURATE AND COMPLETE; the mitigation is procedural, not mechanical**

The disclosure (`evidence/EVIDENCE-INDEX.md:98`, `implementation-decisions.md:91`, D-46/D-47 region) is
truthful and self-incriminating in the right way: **one** row, kind `refusal`, reason `not-staged`,
appended to the **LIVE** journal at `2026-10-30T02:43:32Z` by an **ad-hoc, NON-pack** debug invocation
that resolved the real install via `$HOME`; the pack itself is fenced. I did not read the live journal
(no live contact) — I am assessing the *disclosure*, not re-verifying the row.

Assessment:
- **Adequate for recurrence-by-pack** — yes, and it fires **before** any write (E1 runs before
  `promote.sh`, `pf_die` on escape).
- **Adequate for recurrence-by-ad-hoc-command** — **no.** Nothing outside the packs enforces a fence;
  a bare `bash -c '. lib.sh; resolve_env live; journal_history_append …'` still writes the live
  journal today, exactly as it did on 2026-10-30. The mitigation for that class is discipline
  ("run it through a pack"), not a mechanism. Two mechanical options worth routing, both cheap:
  (i) have `journal_history_append` refuse a `refusal`-kind append when the caller is not a pipeline
  script (e.g. require `PIPELINE_LOCK_HELD_BY_CALLER=1` or an explicit `--allow-adhoc` token), which
  turns the class into a fence; or (ii) at minimum, make `resolve_env live` print a loud
  `LIVE-TARGET` banner to stderr (it may already — but nothing *blocks*).
- **Anything else in the diff that can resolve live paths** — no. `grep` over the shipped diff finds
  **zero** `$HOME` / `INSTALL_DIR`-derivation / `sys.executable`-derivation additions in
  `daemon/`+`scripts/`. Every new pack exports a `mktemp` FAKE_HOME with an escape guard; every
  `/home/nea/**` literal in the new test surface is an *assertion input* to a `pf_assert_not_live`
  negative control. `preflight_mutation_matrix_test.sh` is the one pack with no `pf_fence_init` — it
  never invokes a pipeline script (it only runs other packs) and it carries a dirty-tree refusal +
  leave-no-plant-behind guard, but it **does** rewrite tracked files: it must never run concurrently
  with another lane in the same worktree.

---

## Verified / unverified

| # | Focus area | State | Evidence |
|---|---|---|---|
| 1 | Semantic parity of the O(N) port | **VERIFIED** | parity pack 60/60 (BASE oracle from `b1183a637`); R4 by sha256 both revs; census 26 sites + 3 transitive; single-read race 200× |
| 2 | Watchdog correctness | **VERIFIED except W3** | watchdog 45/45 (bounds, breach order, double-TERM, A-4); kind-scoping verified in code + M19; **W3** = supervision probe escapes scoping into `restart.sh` |
| 3 | Lock discipline | **VERIFIED** | stage-lock 13/13; reclaim row after acquisition on a live-shaped root; explicit success release; double-release no-op |
| 4 | Logging two-file sink | **VERIFIED (A2)** | logging 30/30 + code read: no pipe anywhere, 4 KiB one-write cap, `logwrite_failed` once (M13), retention under the lock (R8) |
| 5 | Arm-time guard | **SOUND except W2** | 5 provenance pytest legs + Pack I C4/C5/C6 + I2/I3/I13/I14/I15; guard precedes `consume_pending_action` (behaviourally pinned); stage.sh stamp+floor gates 7/7 |
| 6 | Pin quality | **VERIFIED in sample; 2 gaps** | 9 packs + 36 pytest run green; matrix **dry-run only** (40 rows self-reported); gaps = R1 per-path (A4), `rev-parse` 128 band (W2) |
| 7 | Scope discipline | **VERIFIED** | exactly 9 shipped files; delta attributed to `536522196`; runbook accepted as docs |
| 8 | Live-contact disclosure | **ACCURATE; mechanism gap** | disclosure text located; no `$HOME`/`INSTALL_DIR`/`sys.executable` additions in shipped code; pack fence mandatory (`pf_die`) |

**Unverified by me, and why:** (a) the 40 mutation legs in execute mode — they rewrite tracked files
in this worktree, outside my read-only mandate; I verified the harness, the TSV and several legs'
assertions by inspection, and one class (A-4 / M36) independently by running the watchdog pack; (b)
the live journal's actual contents at `2026-10-30T02:43:32Z` (no live contact); (c) `_json_sub`'s
partial-output-vs-rc-1 divergence — I could not construct a reachable input, so it is
*unverified-but-unreachable* rather than safe; (d) real-hardware `RuntimeMaxSec` firing (fenced out of
scope; the derivation and argv are pinned, the kill itself is not re-proved here).

---

## Ceremony preconditions this lane creates (state them to the user, not to the code)

1. **`stage.sh` will REFUSE to stage this lane as committed.** `PIPELINE_SCRIPTS_REV` is the
   placeholder `unreleased-fix-promote-preflight-hang` and `PIPELINE_SCRIPTS_FLOOR_COMMIT` is `""`;
   `stage.sh:539-583` refuses both. The bump must set **both to the merge commit** (they must be
   *equal*, D6-8), and staging must run from a tree that **contains** that commit (ancestry check).
   This is the intended release obligation, not a defect — but it means "stage-ready" ≠ "stageable
   now".
2. **The next live arm will REFUSE `stale-scripts` until the pinned scripts dir is synced.** Runbook
   §F records the measured deployment truth: the live `.env` pins
   `ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src/scripts/upgrade`, and that checkout was **277
   commits behind** the lane base, still carrying the O(N²) walk. That is the gate working exactly as
   designed (it is the only thing standing between the next ceremony and the 37-minute hang), but the
   operator must sync it — or move the pin — **before** arming, or the ceremony stops at the arm with
   `stale-scripts-dir` and the nonce is (correctly) not burned.
3. If someone bypasses with `ENSEMBLE_ALLOW_STALE_SCRIPTS=1`, the shell will then hit the legacy-class
   size guard and refuse with a **python3-flavoured** remedy message, which is the wrong remedy for
   that situation (the real cause is the missing helper in a stale scripts dir). Worth a sentence in
   the bypass warning.

---

## Positive observations

- The `adopt-txn`/`entry-check`/`integrity-*`/`plugin-staleness` wraps were each checked for lost
  globals (`_FAST_PATH` is `local`; none of the four sets a shell global the caller reads) — that is
  the failure mode most people miss when retrofitting a subshell wrapper.
- `journal_read.py`'s header is an unusually honest contract document: it states the processed-bytes
  domain, the three-algorithm separation, and *why* the duplicate-key bug is preserved. The
  BASE-bug-preserving decision is exactly right on a safety-critical RMW path.
- The `_journal_read_helper_path` / `_journal_read_probe` split keeps the read path write-pure (no
  lock requirement), which is what makes `status.sh` and the pre-lock `rollback.sh`/`restart.sh` reads
  work at all — the reasoning is in the code and it is correct.
- The `_log_write_failed` recursion guard is set **before** the journal append that could re-enter it.
- The census is generated from git objects, not the working tree, so it cannot drift from the source.
- `_stage_tree_walk` puts its markers on **stderr** because the function's stdout is the manifest data
  — a bug they found and documented (phantom map rows in `test_release_journal.sh`) rather than
  papering over.
- The dirty-tree refusal + leave-no-plant-behind trap in the mutation harness is the right shape for a
  runner that rewrites tracked files.

## Severity summary

- CRITICAL: **0**
- WARNING: **3** (W1 adopt-wrap double-row/mutation-wrap; W2 provenance fail-open on git-shaped
  `rev-parse` failure; W3 supervision budget breach not kind-scoped)
- ADVISORY: **9** (A1-A9)

**Verdict: APPROVE-WITH-ADVISORIES.** The core fix is verified byte-for-byte against BASE and the
guards are mutation-backed. W1-W3 are all surgical (one arm, one predicate, one kind check) and none
can corrupt state, hang the preflight, or produce a bad promote; I recommend they land **before the
stage** since all three are on the ceremony's own triage path, but I do not consider them merge-blocking.

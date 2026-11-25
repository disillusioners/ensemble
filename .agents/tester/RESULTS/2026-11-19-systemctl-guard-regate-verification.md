# 2026-11-19 — SCOPED RE-GATE: the systemctl/bash guard-inversion revision wave

**Gate role:** TESTER re-gate (independent; own scratch worktrees only)
**Tip under test:** `eb520859f7d88ed27769e957e967809ca63de388` (branch `fix/systemctl-guard-pipe-shapes`)
**Pre-revision tip:** `4d2c54487614bcb9c9c56132c136d89a1fb055ca` · **Base:** `c36c3e07c2f00947cd1257a7d1fc01190c56c376`
**Scratch worktrees (mine, created and removed for this gate):**
`/home/nea/ensemble-src-wt-guard-reverify` @ `eb520859f` ·
`/home/nea/ensemble-src-wt-guard-pre` @ `4d2c54487` ·
`/home/nea/ensemble-src-wt-guard-rebase` @ `c36c3e07c` — each with its own
`uv sync` venv (Python 3.14.7), `daemon.__file__` proved to resolve INSIDE the
worktree, the lane fence `.env` copied in (mode 600, `POSTGRES_DB=ensemble_dev`,
`POSTGRES_USER=ensemble_dev_app`, `127.0.0.1:5432`, `PORT=8079`,
`ENSEMBLE_SELF_ENV=dev`, `grep -c ensemble_prod .env` = **0**), and
`git status --porcelain --untracked-files=all` clean before and after every leg.
All probing ran from the worktree root; every live-pattern literal was
RECONSTRUCTED at runtime (`"ensemble-" + "main"`), never a literal in bash
command text.

---

# VERDICT: **PASS-WITH-NOTES**

**Every gate finding is actually closed.** F-01 (the NOPASSWD-exploitable sudo
bypass), F-02 (the missing isolating pins), F-03 (the phantom sha), F-04 (the
caller count), F-05 (doas), AG-1..AG-6, T2, T7, CO-1, T24, A11 and Deferred#3
are all verified closed at the tip, on the guard's three public entry points
**and on both real seams** (the bash lane `daemon/tools/bash.py:1366` and the
proc lane `daemon/tools/proc_tools.py:2594`). The pack is **255/255 file +
294/294 pack**, net-new reds vs base **0**, and the revised mutation harness
reproduces **all 20 legs with EXACTLY their expected red counts**.

The notes are three documentation-accuracy items and one pre-existing
undisclosed residual — none blocks the merge:

* **N-01** the wave's "79 reds" total is an **arithmetic error** — the
  per-leg EXPECTED counts (which I reproduce exactly) sum to **88**.
* **N-02** the headline "23 ALLOW→REFUSE, 1 REFUSE→ALLOW" is a count over the
  wave's own corpus only and **understates** the revision's behaviour change
  (3 more gate-named closures omitted; 6 more over-block removals vs the
  pre-revision tip). Every change is in a demanded direction; nothing harmful
  is undisclosed.
* **N-03** `ssh host <<'EOF' <mutating systemctl> EOF` is **ALLOWED** —
  pre-existing (byte-identical verdict at base, at the pre-revision tip and at
  the tip), same delivery class as F15 which IS refused, and not named in the
  docstring's residual list.
* **N-04** `origin/latest` moved `c36c3e07c → c270f7f9b` during the lane; the
  REV4 row's "base `c36c3e07c` = origin/latest" phrasing is now stale (the
  base is still the right base).

---

## Per-leg results

### G1 — THE DELTA ENUMERATION — **PASS-WITH-NOTES (N-02)**

102 shapes probed (every gate-named shape + my first pass's 33 DENY / 21 ALLOW
corpus + the G2 invariant set), at all three commits. Verdicts from
`systemctl_violation`; the deltas below were cross-checked against
`overlay_violation` and both seams for the closed shapes.

**REVISION-WAVE DELTA (`4d2c54487` → `eb520859f`), 102 shapes: 70 verdict-identical, 25 ALLOW→REFUSE, 7 REFUSE→ALLOW.**

ALLOW→REFUSE (25) — every one a named gate finding, every one now refused on
all entry points and both seams:

| shape | gate |
|---|---|
| `sudo --role <V> systemctl restart <U>` | **F-01** (NOPASSWD-exploitable) |
| `sudo -r <V> …` | F-01 |
| `sudo -t <V> …` | F-01 |
| `sudo --type <V> …` | F-01 |
| `doas --role <V> …` | F-05 (execution untestable — `doas` absent; guard verdict REFUSE) |
| `env -iS '<mutating>'` | AG-1 (bundled shorts) |
| `env -iS'<mutating>'` (attached) | AG-1 |
| `sudo env -S '…'` | AG-1 |
| `nohup env -S '…'` | AG-1 |
| `timeout 5 env -S '…'` | AG-1 |
| `if env -S '…'; then true; fi` | AG-1 |
| `cat <<'EOF' \| cat \| bash` (mutating body) | AG-2 |
| `cat <<'EOF' \| tee /tmp/x \| bash` | AG-2 |
| `cat <<'EOF' \| cat \| at now` | AG-2 |
| `xargs systemd-run systemctl restart <U>` | AG-3 |
| `xargs su -c '…'` | AG-3 |
| `xargs ./scripts/stop-…sh` → `unit-stop-wrapper` | AG-3 |
| `xargs xargs systemctl restart <U>` | AG-3 |
| `systemctl -aP cat stop "$AMBIENT_UNIT"` → `unresolved-unit` | AG-4 (fail closed) |
| `systemctl -aP cat stop '*main'` → `unresolved-unit` | AG-4 (fail closed) |
| `echo 'systemctl clean <U>' \| bash` | AG-6/T2 |
| `echo 'systemctl thaw <U>' \| bash` | AG-6/T2 |
| `echo 'systemctl reenable <U>' \| bash` | AG-6/T2 |
| `stdbuf -e L systemctl restart <U>` | T7 |
| `A=ensemble; echo "systemctl restart ${A}-main" \| cat \| bash` | Deferred#3 (**closed**, refused) |

REFUSE→ALLOW (7) — every one an over-block the gates DEMANDED be removed:

| shape | gate |
|---|---|
| `systemctl --legend yes status <U>` | AG-4(i) |
| `systemctl show -aP Restart <U>` | AG-4(ii) (bundle `-aP` was unknown ⇒ fail-closed) |
| `xargs --max-args=1 echo hi` | CO-1 |
| `echo hello \| xargs -I{} echo {}` | CO-1 |
| `echo hello \| xargs -n1 echo` | CO-1 |
| `watch -n1 echo hi` | CO-1 |
| `systemd-run -Hhost true` | CO-1 (attached `-H` form) |

**WHOLE-LANE DELTA (`c36c3e07c` → `eb520859f`), same corpus: 55 verdict-identical, 45 ALLOW→REFUSE, 2 REFUSE→ALLOW.**
The 45 = the 25 above + the 20 first-pass shapes (K1/K2/F9–F18) already
verified closed in my first pass. The 2 = `--legend yes status` and
`systemctl -P Restart <U>` (the latter is a **wave-1** dropped class, unchanged
by this revision — it appears only because the base predates the whole lane).

**Reconciliation vs the claimed "23 ALLOW→REFUSE, 1 REFUSE→ALLOW"** — see N-02
below. **No shape changed verdict that is in NEITHER list**: every verdict
change in either direction maps to a named gate finding. No undisclosed
behaviour change found.

**Additional G1 sub-checks, all verified:**
* `sudo --role=x` / `--type=x` `=`-forms **still refuse** ✓ (`ensemble-main`).
* AG-2 constraint: a prose heredoc with **no pipe/executor STAYS ALLOWED** ✓
  (`cat > notes.md <<'EOF'…` and `cat <<'EOF'…` both `None`).
* **`preset` vs `preset-all` are DISTINCT verbs** — proven mechanically:
  `_MUTATING_VERB_RE` is **derived** from `_SYSTEMCTL_TAIL_MUTATING` at import
  (`daemon/tools/_systemctl_guard.py:264-271` + `:662-672`), sorted
  longest-first, with `(?<![a-z0-9-])` / `(?![a-z0-9-])` boundaries. With the
  `preset-all` alternative surgically removed from the compiled pattern,
  `preset` does **NOT** match `"preset-all"` (measured `False`) — the old
  `\bpreset\b`-inside-`preset-all` accident is gone and replaced by explicit
  alternatives. `xpreset` does not match ✓.
* `systemctl unmask <U>` / `reset-failed <U>` → **REFUSE**, and that is
  **intended and disclosed**: the wave added the host-verified verbs
  `unmask, reset-failed, daemon-reexec, set-environment, unset-environment,
  add-wants, add-requires` to `_SYSTEMCTL_TAIL_MUTATING`
  (`daemon/tools/_systemctl_guard.py:264-271`).
* **A11 ssh fail-closed**: `ssh --unknown-opt <V> host 'systemctl restart <U>'`
  → **REFUSED** (fail-closed, `cmd_words is None` branch); the known-option
  form `ssh -p 2222 host '…'` still refuses structurally ✓.
* **T24**: `_pipeline_laundering_violation` (`:1810`) declares `code: str` and
  `saw_pipe: bool` with **NO defaults** ✓.

### G2 — THE INVARIANT SPOT-CHECK — **PASS**

All measured at tip via `systemctl_violation`:

| invariant | verdict |
|---|---|
| prose heredoc to file (no pipe/executor) | `None` ✓ |
| `systemctl show <U> -p Restart` (direct property) | `None` ✓ |
| `systemctl show <U> --property=Restart` | `None` ✓ |
| `systemctl -P Restart <U>` | `None` ✓ |
| `systemd-run --version` | `None` ✓ |
| `watch --help` | `None` ✓ |
| `xargs --version` | `None` ✓ |
| `systemd-run --user --scope --unit=x true` | `None` ✓ |
| `watch -n 1 -t echo hi` | `None` ✓ |
| `systemctl restart foo 9797` (port-token residual) | `None` ✓ |
| my first-pass 21-shape ALLOW set | **all unchanged `None`** (none needed to change — F-01's fix is a closure, not an allow) ✓ |
| my first-pass 33-shape DENY set | **all still refuse** ✓ |

Seam spot-check (bash seam + proc lane, `timeout=-1` for allow controls so no
spawn occurs): the 6 key closed shapes (sudo `--role`/`--type`, `env -iS`,
heredoc-through-pipe, `xargs systemd-run`, `stdbuf -e L`) **refused on both
seams**; `sudo --role <V> systemctl status <U>`, the prose heredoc,
`watch -n1 echo hi` and `systemd-run -Hhost true` **allowed on both seams** ✓.

### G3 — PACK + NET-NEW — **PASS (claim fully reproduced)**

Registered invocation (PACKS REV1 row's pytest command), worktree root, own
venv, per-variable `POSTGRES_*` pins:

| side | collected | result | rc | reds |
|---|---|---|---|---|
| TIP `eb520859f` (4-file pack) | 294 | **294 passed**, 0 failed, 0 error | 0 | **0** |
| TIP `eb520859f` (file only) | 255 | **255 passed** | 0 | **0** |
| BASE `c36c3e07c` (4-file pack) | 173 | **173 passed**, 0 failed, 0 error | 0 | **0** |
| **net-new reds (tip − base)** | — | — | — | **0** |
| base-only reds | — | — | — | **0** |

`grep '^FAILED'` classification only; both sets empty. **Claim 255/255 file +
294/294 pack + net-new 0: CONFIRMED.**

### G4 — MUTATION HARNESS AT TIP — **PASS** (+ **N-01**)

Ran `.agents/tester/mutation/systemctl_guard_m6_mutation.py --leg all` in my
scratch worktree. **All 20 legs PROVEN**, each with **EXACTLY** its EXPECTED
red count, every restore **BYTE-IDENTICAL** (sha256
`70bc76e9643fe04cd9603de1f29f49f15737cb2e0269479523fe80edb57fde60` before ==
after), tree clean after, and the new **post-restore GREEN** re-run green on
every leg:

| leg | reds | leg | reds | leg | reds | leg | reds |
|---|---|---|---|---|---|---|---|
| M6g | 11 | M6l | 9 | M6r | 3 | M7d | 7 |
| M6h | 2 | M6m | 2 | M6s | 2 | M7e | 3 |
| M6i | 12 | M6n | 1 | M7a | 5 | M7f | 6 |
| M6j | 9 | M6o | 2 | M7b | 4 | M7g | 1 |
| M6k | 1 | M6p | 1 | | | M7h | 2 |
| | | M6q | 5 | | | | |

**Every per-leg count matches the ledger exactly. The TOTAL is 88, not the
claimed 79 (N-01).**

New safety properties, all verified **empirically**:
* **refuses a dirty tree** → `REFUSED M6g: … is not clean in git` ✓
* **refuses a non-worktree** → pointed at `/home/nea/ensemble-src` (`.git` is a
  directory): `REFUSED: … is not a linked worktree (.git is not a FILE)`; same
  for a plain `/tmp` directory ✓
* **`--leg both` no longer exists** → `error: argument --leg: invalid choice:
  'both' (choose from 'all', 'M6g', … 'M7h')` ✓
* **child pytest env scrubbed of `POSTGRES_*`** → `_SCRUB_ENV` tuple
  (`POSTGRES_DB/USER/PASSWORD/HOST/PORT` + `PG*`) popped **per-variable** in
  `child_env()` (harness `:363-369`), passed as `env=` to the pytest subprocess ✓
* **`git_clean` honours the returncode** → `return proc.returncode == 0 and
  proc.stdout.strip() == ""` (harness `:356-364`), with the in-source comment
  "a FAILED git must read as NOT clean, or the refusal is vacuous" ✓ (verified
  by reading; a live failing-git simulation was not cheap and the code is
  unambiguous)

**M6j isolation (my first pass's F-02) — CLOSED.** My own single-defence plants
at tip (`/tmp/guard_verify/regate_mutation.py`, own anchors):

| plant | reds | which pins |
|---|---|---|
| **R1 parse-only** (neutralise ONLY the non-terminal read-only parse; option tables intact) | **6** | `test_readonly_verb_position_is_not_terminal[systemctl cat stop <U>]` · `[systemctl help stop <U>]` · `[systemctl status stop <U>]` · `test_fail_closed_shapes_return_the_exact_token` · `test_mutating_verb_set_is_one_source_of_truth[systemctl cat stop "$AMBIENT_UNIT"]` · `[… stop '*']` |
| **R2 arity-all** (remove `-P` + the 3 v255 + `--legend`) | **4** | the 4 per-option ALLOW pins |
| **R2b arity-v255-only** (remove ONLY `--image-policy`/`--boot-loader-menu`/`--boot-loader-entry`) | **3** | **exactly** the 3 per-option ALLOW pins |

So: **the non-terminal parse now has a dedicated, isolating DENY pin family**
(`test_readonly_verb_position_is_not_terminal`, `tests/…:916-924`, 3 nodes —
the option-free `systemctl cat stop <U>` spelling is precisely the shape only
the parse can catch), and **each of the 3 v255 options has its own per-option
ALLOW pin** (`tests/…:215-217`). **F-02 is closed.**

### G5 — DOC TRUTH SPOT-CHECK — **PASS** (+ N-01, N-04)

* **module docstring** (`daemon/tools/_systemctl_guard.py:25-120`): rule 3 now
  states the exception — "read-only verbs are ALLOWED … **unless a mutating
  verb appears LATER in the same command**, followed by an ensemble unit or by
  an operand that cannot be resolved statically" (`:33-35`) ✓; the TAIL SCAN
  positional disclosure (`:61-67`) ✓; FAIL-CLOSED ON UNKNOWN OPTIONS **PER
  TOOL** with the skip-unknown tools named (`:68-76`) ✓; the EXECUTED-CONTEXT
  SHAPE OVER-BLOCK (`:77-83`) ✓; BARE `ssh host` IS AN EXECUTOR (`:84-86`) ✓;
  the SHELL ATTACHED `-c` NARROWING (`:87-93`) ✓; the two dropped classes
  (`:94-97`) ✓.
* **OPTION GRAMMARS / OPTION-TABLE MAP**: per-tool tables with back-pointers
  exist above every option set — `_ENV_VALUE_OPTIONS`/`_ENV_FLAG_OPTIONS`
  (`:513-521`), `_SUDO_VALUE_OPTIONS` (`:528-531`, now carrying
  `-r, --role, -t, --type`), the `_WRAPPER_ARG_GRAMMARS` four-per-tool split
  (`:540-566`), `_SSH_VALUE_OPTIONS` + `_SSH_FLAG_OPTIONS` (`:565+`), and
  `_OPTIONS_WITH_VALUE` (`:390+`) each carry a provenance comment naming the
  host `--help` and the finding ✓.
* **PACKS REV4 counts — ALL reconcile with my measurements** (list sizes
  counted by AST at tip, base sizes counted at base):
  pipe 6→**17** ✓ · xargs-stdin 4→**4** ✓ · runner 10→**61** ✓ ·
  scheduler 3→**5** ✓ · systemctl option-anchor 0→**10** ✓ ·
  read-only-tail 0→**3** ✓ · mutating-verb 0→**12** ✓ ·
  `_LAUNDER_ALLOW` 18→**40** ✓ · `_READONLY_COMMANDS` 15→**23** ✓ ·
  file **255** ✓ / pack **294** ✓ / base **173** ✓ / +121 nodes ✓.
* **the phantom sha `cc97f1a8…` is GONE** — 0 hits in PACKS.md; the row no
  longer quotes a restore sha at all; the only sha in the wave-5 doc is
  `41d3fb5e…`, which I verified IS my first-pass FAIL doc committed verbatim
  (sha256 matches byte-for-byte) ✓.
* **caller count**: the row now says "`_command_word_index` has **12 call
  sites** at this tip". **MEASURED: 12** (`:1043, :1157, :1386, :1416, :1436,
  :1469, :1505, :1629, :1691, :1746, :1893, :2032`). The developer's 12 is
  **correct**; the tidier's 13 was counting the `def` line itself. ✓

### G6 — CLEANUP — **PASS (already clean)**

`/tmp/wt-base-c36c`, `/tmp/wt-mut-fin` and `/tmp/wt-mut-w5` were **already
absent** from the filesystem; `git worktree remove --force` on each correctly
returned `not a working tree`. `git worktree prune -v` ran clean and
`.git/worktrees/` holds **no** stale records for them. The three PROTECTED
worktrees are untouched and still registered: `/tmp/skillfix-regbase`
(`0181dadbf`), `/tmp/vmr-base3` (`85ed86b46`), `/tmp/wt-b2` (`e387d2297`) ✓.
My own three scratch worktrees are removed at the end of this gate.

### G7 — WRITE + COMMIT — **DONE**

This file, committed as the ONLY change (explicit pathspec, no push).

---

## FINDINGS

| id | severity | file:line (measured at `eb520859f`) | note |
|---|---|---|---|
| **N-01** | 🟢 MINOR (doc arithmetic) | `.agents/tester/RESULTS/2026-11-19-systemctl-guard-revision-wave2-results.md:84` and `.agents/tester/PACKS.md:5160` | "**20 legs, 79 reds**" — the per-leg EXPECTED counts (all of which I reproduce **exactly**) sum to **88**. The sum through `M7e` is exactly 79, so the total was almost certainly computed before `M7f`/`M7g`/`M7h` were appended and never re-added. Per-leg numbers are correct; only the total is wrong. |
| **N-02** | 🟢 MINOR (doc completeness) | wave-5 doc §1 (`:15`, `:30`) | The headline "**23 shapes ALLOW → REFUSE** / **1 shape REFUSE → ALLOW**" is a count over the wave's OWN enumeration corpus and **understates** the revision's behaviour change. Measured on my 102-shape corpus (every gate-named shape): vs base `c36c3e07c` → **45 / 2**; vs the pre-revision tip `4d2c54487` → **25 / 7**. Three gate-named closures are missing from the 23-row table (`sudo --type <V> …`, `doas --role <V> …`, `xargs ./scripts/stop-…sh`), and six over-block removals vs the pre-revision tip are folded into the "no verdict change" framing because they net to zero vs base (wave-4 introduced the over-block; this wave removed it). **Every change is in a demanded direction; no harmful undisclosed change exists.** |
| **N-03** | 🟠 IMPORTANT (residual, **pre-existing**, undisclosed) | `daemon/tools/_systemctl_guard.py:1031` (`_is_shell_stdin_line` — shells + schedulers only; `ssh` not included) and the heredoc DATA path | `ssh host <<'EOF'\nsystemctl restart <U>\nEOF` → **ALLOWED** (`None`). Measured **identical at base, at the pre-revision tip and at the tip** — NOT introduced or worsened by this wave. The heredoc body is classified DATA because the stdin reader is not a shell/scheduler, and the pipeline rule only fires on a `\|` operator. But a bare `ssh host` **forwards its stdin to a remote login shell**, which RUNS it — the same delivery class as F15 (`echo … \| ssh host`), which IS refused. The docstring discloses the bare-ssh executor and `bash < <file>`, but not this heredoc-stdin form. Exploitability requires ssh credentials to a host where the unit exists (`localhost` qualifies). **Recommend: either add `ssh` to the stdin-executing readers, or name this form in the residual list.** |
| **N-04** | 🟢 MINOR (doc staleness) | `.agents/tester/PACKS.md:5160` | The row says "base `c36c3e07c` = origin/latest". `origin/latest` has since moved to `c270f7f9b`. The base is still the correct commissioned base; only the "= origin/latest" clause is stale. |

**First-pass findings, re-adjudicated at this tip:**

| first-pass id | status at `eb520859f` |
|---|---|
| **F-01** (CRITICAL — sudo value-option bypass) | **CLOSED** — `sudo -r/--role/-t/--type <V> systemctl restart <U>` refused (`ensemble-main`) on `systemctl_violation`, `overlay_violation`, the bash seam **and** the proc lane; the `=`-forms still refuse; `_SUDO_VALUE_OPTIONS` (`:528-531`) now carries all four |
| **F-02** (IMPORTANT — no isolating pin) | **CLOSED** — see G4: R1 → 6 reds incl. the new dedicated pin family `test_readonly_verb_position_is_not_terminal` (3 nodes); R2b → 3 reds = the 3 per-option ALLOW pins |
| **F-03** (MINOR — phantom sha `cc97f1a8…`) | **CLOSED** — 0 hits in PACKS.md |
| **F-04** (MINOR — "eight callers") | **CLOSED** — now states **12**, which is the measured count |
| **F-05** (MINOR — doas) | **CLOSED as a guard matter** — `doas --role <V> …` now REFUSES (the doas branch shares `_SUDO_VALUE_OPTIONS`); execution untestable (`doas` not installed on this host) |

---

## WHAT I COULD NOT VERIFY, AND WHY

1. **`doas` execution** — `doas` is not installed on this host, so F-05's
   exploitability could not be demonstrated; only the GUARD verdict was
   probed (REFUSE).
2. **`git_clean`'s failed-git handling** — verified by reading the code
   (`proc.returncode == 0 and …`, with the in-source rationale), not by
   simulating a failing git binary (not cheap, and the code is unambiguous).
3. **The live `ensemble-main` unit was never touched.** All exploitability
   reasoning uses the guard's verdicts only; no live unit was stopped or
   restarted at any point in this gate.
4. **The wave's own 23-row delta table was reconciled by class-equivalence and
   by pin presence**, not by re-typing their exact 23 strings: I probed the
   class equivalents of every row (and their two fail-closed shapes
   `systemctl cat stop '*'` / `"$AMBIENT_UNIT"` are pinned at
   `tests/…:953-954` and `:974-975` and are covered by my `-aP` variants and
   by the R1 mutation leg reddening them).
5. **`origin/latest` movement** (`c36c3e07c → c270f7f9b`) means the lane base
   is no longer the branch point of `latest`; a future rebase/merge
   interaction is outside this gate's scope.

---

## REPRODUCTION ARTEFACTS (this gate's own, outside the repo)

| artefact | path |
|---|---|
| delta probe (G1/G2, 102 shapes × 3 commits) | `/tmp/guard_verify/regate_probe.py` + `regate_{tip,pre,base}.json` |
| delta differ (base→tip and pre→tip) | `/tmp/guard_verify/regate_diff.py`, `rev_diff.py` + `regate_delta.json`, `rev_delta.json` |
| single-defence mutation legs (G4/M6j isolation) | `/tmp/guard_verify/regate_mutation.py` |
| pack legs (G3) | `/tmp/guard_verify/g3_{tip,base}.log`, `g3_runner.log` |
| mutation harness run (G4) | `/tmp/guard_verify/m7_all.log` |

**Verdict: PASS-WITH-NOTES — every gate finding is closed and verified on the
real seams; the notes are doc-accuracy items (N-01, N-02, N-04) plus one
pre-existing undisclosed residual (N-03) that predates this wave.**

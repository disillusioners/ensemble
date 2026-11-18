# Systemctl guard — wave-4 option-grammar closure (audit F9–F18) + wave-3 closing fixes

**Verdict: COMPLETE — the 10 audited findings are closed, every new pin is
counter-mutation-proven, net-new reds vs base = 0.**

- Worktree: `/home/nea/ensemble-src-wt-guard-pipe-shapes`
- Branch: `fix/systemctl-guard-pipe-shapes`
- Base: `c36c3e07c2f00947cd1257a7d1fc01190c56c376` (== `origin/latest`, unmoved)
- Tip (code): `ee4edfb56ea49990284b7fb4a0e62297641e4132`
- Interpreter: worktree `.venv` (CPython 3.14.7); per-variable `POSTGRES_*` pins; `ENSEMBLE_SELF_ENV=dev`
- Guard file sha256 at tip: `1ea773d0939fddc9bc99c8a780ae9c6e2575105ef07b0a4b144ecd5cf20b672c`
  (base `37bbf0d93fea66ea55de63049ff87f06ba777c09a3f8a9ab47355860c81b666b`; 1623 → 1948 lines)

## 1. THE TWO WAVES IN ONE ROW

**Wave 3 (reviewer round 2, 3 findings)** — the whole-text pipe rule (the
adjacency-scoped payload test left real laundering shapes ALLOWED), the
per-runner option value-anchoring, and the `--mkdir` bypass in the systemctl
table. Detail: `2026-11-19-systemctl-guard-pipe-shapes-wave3-results.md`.

**Wave 4 (independent audit, 10 findings)** — every one of the 10 was ALLOWED
by the new guard and REFUSED by the old text guard. All 10 are closed.

## 2. THE 10 FINDINGS — MECHANISM, PIN, MUTATION LEG

| # | shape | root cause | the fix | pin | leg |
|---|---|---|---|---|---|
| F9 | `systemctl -P cat stop ensemble-main` | `-P` was missing from the value-option table, so the parser landed on the option's VALUE; `cat` is a read-only verb and the read-only early return fired BEFORE the operand scan | the missing v255 value options added (`-P`, `--image-policy`, `--boot-loader-menu`, `--boot-loader-entry`) **and** the read-only early return made NON-terminal (the parse continues; a mutating verb followed by an ensemble unit is refused) | `_SYSTEMCTL_OPTION_ANCHOR_SHAPES` | M6j |
| F10 | `systemctl --boot-loader-entry cat stop ensemble-main` | same | same | same | M6j |
| F11 | `env -S '<cmd>'`, `env --split-string=<cmd>` | the split string is RUN — an EXECUTED command, not an opaque value; skipping 2 tokens leaves the payload as one opaque command word | the guard now RECURSES into the split string (`_command_text_violation(..., executed=True)`, exactly like `bash -c`); the effective command string is the option's value PLUS the words that follow it (the unquoted `=`-form carries only the first word of the command line) | `_RUNNER_SHAPES` | M6l |
| F12 | `timeout -s TERM 5 systemctl restart ensemble-main` | timeout's value options (`-s`/`--signal`/`-k`/`--kill-after`) were missing; the SIGNAL is a WORD, so the numeric-duration rule did not skip it and the command behind it was never analysed | the value options added to the wrapper peel | `_RUNNER_SHAPES` | M6n |
| F13 | `xargs --arg-file /dev/null bash -c '…'` | the xargs long value options were missing | added (`--arg-file`, `--delimiter`, `--max-lines`, `--max-args`, `--max-procs`, `--max-chars`, `--process-slot-var`) | `_RUNNER_SHAPES` | M6o |
| F14 | `su -c'<cmd>'` | these runners' getopt accepts the ATTACHED command-string form, which the flag set missed | the attached form (`-c<cmd>`, `--command=<cmd>`) is now analysed as a command string — **only** in the su/runuser branch: bash/sh/zsh reject the attached form, so the SHELL branch is deliberately untouched | `_RUNNER_SHAPES` | M6p |
| F15 | `echo '<cmd>' \| ssh host` | `_executor_kind` returned "ssh" only with a remote command — but `ssh host` starts a remote login shell and FORWARDS stdin | the ssh segment is an executor either way | `_RUNNER_SHAPES` | M6k |
| F16 | `xargs env bash -c '<cmd>'` | the xargs tail was read as its FIRST word, so a wrapper prefix hid the shell | the tail's command word is found by PEELING the wrappers (`_command_word_index`) | `_RUNNER_SHAPES` | M6q |
| F17 | `crontab <<'EOF' … EOF` | a heredoc body was EXECUTED only when the reader was a SHELL | the SCHEDULERS run their stdin too (`crontab -` installs the body as a crontab; `at`/`batch` run the lines) | `_SCHEDULER_SHAPES` | M6m |
| F18 | `at now <<'EOF' … EOF` | same | same | `_SCHEDULER_SHAPES` | M6m |

Two auditor corrections are honoured: **F11** recurses into the split string
(it is not merely skipped as an opaque value), and the **F12 pin uses
`-s TERM`** — a kill-ish signal name would trip the kill arm and make the pin
VACUOUS (it would pass with the bug still present).

## 3. UNKNOWN OPTIONS NOW FAIL CLOSED

An option the tool does not have has an unknown arity: the tail analysis could
anchor on the option's VALUE and never analyse the command behind it. The
runners (`systemd-run`, `watch`) and `xargs` therefore carry an explicit
**BOOLEAN** set beside their **VALUE** set — both verified against the tool's
own `--help` on this host (systemd 255, util-linux `watch`, GNU `xargs`) — and an
option in NEITHER set is REFUSED (the same convention as an unresolvable
operand). Pins: `_RUNNER_SHAPES` (+4, including a READ-ONLY tail — the
fail-closed is unconditional and that over-block is deliberate). Legs: M6r
(runners), M6s (xargs).

## 4. THE MUTATION LEGS — 13 legs, 41 reds, every restore BYTE-IDENTICAL

Harness: `.agents/tester/mutation/systemctl_guard_m6_mutation.py` (MUTATES
SOURCE FILES — never from CI; the `.agents/` tree is NOT in the release
payload). Run in a detached throwaway worktree; it refuses to run unless the
target file is git-clean, restores the bytes in a `finally` and verifies the
sha256 afterwards.

| leg | plant | reds | restore |
|---|---|---|---|
| M6g | the whole-text pipe rule removed | 7 | byte-identical |
| M6h | the `--on-*`/`--timer-*` prefix rule removed | 7 | byte-identical |
| M6i | the whole runner value-consumption removed | 12 | byte-identical |
| M6j | the WHOLE systemctl-arity mechanism (the missing value options AND the non-terminal read-only parse) | 2 | byte-identical |
| M6k | the "ssh with a remote command only" executor condition restored | 1 | byte-identical |
| M6l | the env split-string recursion removed | 3 | byte-identical |
| M6m | the "a shell reader only" heredoc condition restored | 2 | byte-identical |
| M6n | the wrapper value options removed (timeout's `-s`/`--signal`/`-k`/`--kill-after`) | 1 | byte-identical |
| M6o | the xargs long value options removed | 1 | byte-identical |
| M6p | the attached command-string form removed (`su -c<cmd>`) | 1 | byte-identical |
| M6q | the "the xargs tail's FIRST word is the command" check restored | 1 | byte-identical |
| M6r | the runner unknown-option fail-closed removed | 2 | byte-identical |
| M6s | the xargs unknown-option fail-closed removed | 1 | byte-identical |

sha256 before == sha256 after == `1ea773d0939fddc9bc99c8a780ae9c6e2575105ef07b0a4b144ecd5cf20b672c`
on every leg; the tree was clean after every run.

**M6j is a TWO-part plant on purpose.** F9/F10 are covered by TWO independent
defences (the value-option arity AND the non-terminal read-only parse), so the
audit's literal instruction — "remove `-P` from the value set and the F9 pin
MUST go RED" — cannot hold on its own: with only the arity removed, the
non-terminal parse still refuses the shape. The leg therefore neutralises BOTH
halves; with only ONE removed the pin stays GREEN, which is the redundancy
working as intended. (This is also why the ALLOW pin
`systemctl show -P Restart <unit>` is GREEN under the combined plant: with the
property-name class dropped, the read-only early return is what keeps it
allowed.)

## 5. THE PACK AND THE BASE-VS-HEAD RED DIFF

| side | result | reds (`grep '^FAILED'` only) |
|---|---|---|
| base `c36c3e07c` | **173 passed**, exit 0 | 0 |
| tip `ee4edfb56` | **246 passed**, exit 0 | 0 |
| **net-new reds (head − base)** | — | **0** |

The 5 ambient-dependent reds the REV3 row records did not fire in this ambient
env (0 red on BOTH sides). The 2 legacy differential files
(`tests/unit/test_pkill_overlay_pins.py`, `tests/unit/test_pkill_overlay_guard.py`)
are byte-identical from the pre-inversion base and pass UNCHANGED.

## 6. RE-VERIFIED UNCHANGED (the audit confirmed these are right)

- PORT whole-word scope: `MEANINGFUL_PATH_PATTERNS` is used in exactly one
  place, inside `kill_leg_violation`; whole-word semantics preserved verbatim.
- KILL legs: still whole-text scoped, with the old regexes unchanged.
- `shell-c` (whole `-c` payload), `eval` (whole argument string) and
  `ssh host '<remote cmd>'` (whole remote text): whole-text analysis,
  executed=True.
- The auditor's F1–F8 closure: re-verified AFTER the refactor — all 8 still
  REFUSE (`systemd-run -t`/`-P`, `watch -p`/`-t`/`-g`/`-C`/`-q`/`--interval`).
- The TWO DROPPED CLASSES stay dropped: the PROSE over-block (a heredoc/grep/
  printf that merely DOCUMENTS a mutating command) and the DIRECT-context
  property-name over-block (`systemctl show <unit> -p Restart`, and the `-P`
  spelling). Pinned as ALLOWED.

## 7. DISCLOSED RESIDUALS (not fixed — with the shape and the reason)

(a) A payload that only reaches the executor through a FILE at RUN time
(`cat notes.md | bash`) is invisible to static analysis — pre-existing.

(b) A port token as an extra non-unit argument on a non-ensemble systemctl
command (`systemctl restart foo 9797`) is ALLOWED: the new guard requires the
port/live token to be the systemctl OPERAND, while the old text guard refused
it. NOT widened — widening would reintroduce the non-operand port false
positives on systemctl. Disclosed at the auditor's request.

(c) An UNKNOWN option of `env`/`timeout`/`sudo` still defaults to a skip-1 in
the shared wrapper peel (`_command_word_index`): those tools' options live in
one function whose single-index contract is shared by eight callers, and
turning "unknown option" into a fail-closed signal there is a REFACTOR of that
contract — out of scope for this wave. The F11–F18 KNOWN-option shapes are all
closed and proven; the runners and `xargs` (the two the audit named) already
fail closed.

(d) The whole-text pipe rule is conservative by construction: a command that
pipes into an executor and mentions the shape ANYWHERE in its stripped text is
refused — old-text-guard strength, kept on purpose.

## 8. THE ACCEPTANCE CRITERIA

| # | criterion | verdict |
|---|---|---|
| 1 | all 5 review shapes (plus the general family) REFUSE | PASS — 7 pipe pins |
| 2 | all 4 systemd-run value-option shapes REFUSE; the `=`-forms still work | PASS — 19 runner pins |
| 3 | the 18 `_LAUNDER_ALLOW` legs and the both-direction selection stay GREEN | PASS — the ALLOW family 26→32 legs, all green (the read-only list 15→19) |
| 4 | the full pack GREEN from the worktree root with the worktree venv | PASS — 246/246, exit 0 |
| 5 | net-new reds vs base = 0 | PASS — 0 (0 vs 0) |
| 6 | M6g and M6h each RED under their own plant, byte-identical restore | PASS — 13 legs, 41 reds, all byte-identical |
| 7 | the tree is clean of stray edits | PASS |
| 8 | PACKS: REV4 above REV3, REV3's "EVERY runner" claim corrected, the residual disclosed | PASS — REV4 extended in place |
| 9 | the audit's 10 shapes each REFUSE, each with a pin and a counter-mutation leg | PASS — F9–F18, 13 legs |
| 10 | F1–F8 still closed after the refactor | PASS — re-verified |

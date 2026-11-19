# Systemctl guard — revision iteration 2 (wave 5): the consolidated three-gate wave

**Verdict: COMPLETE — every A-item closed, 20 mutation legs PROVEN, net-new reds vs base = 0.**

- Worktree `/home/nea/ensemble-src-wt-guard-pipe-shapes`, branch `fix/systemctl-guard-pipe-shapes`
- Base `c36c3e07c2f00947cd1257a7d1fc01190c56c376` (== `origin/latest`, unmoved)
- The three gates: Tester **FAIL** (F-01..F-05) · Reviewer **NEEDS-REVISION** (AG-1..AG-6, CO-1..CO-6) ·
  Tidier **CLEANUP-REQUIRED** (T1..T27). All closed below; the commissioned core was verified correct.
- The tester's FAIL-verdict doc is committed VERBATIM, unedited:
  `.agents/tester/RESULTS/2026-11-19-systemctl-guard-closing-verification.md`
  (sha256 `41d3fb5e8c7337e63812a725f80265f03217356e20240377233b3a80b54fc844`).

## 1. THE MEASURED DELTA (acceptance 9) — probed base vs tip, not asserted

**23 shapes ALLOW → REFUSE** (the closed bypasses):

| shape | the finding |
|---|---|
| `sudo --role <V> systemctl restart <unit>` · `sudo -r <V> …` · `sudo -t <V> …` | F-01/F-05 (NOPASSWD-exploitable) |
| `env -iS '<mutating>'` · `env -iS<mutating>` | AG-1 (bundled short options) |
| `sudo env -S '…'` · `nohup env -S '…'` · `timeout 5 env -S '…'` · `if env -S '…'; then …` | AG-1 (env behind wrappers/keywords) |
| `systemctl -aP cat stop <unit>` | AG-1/CO-1 (a bundle ending in a value flag) |
| `cat <<EOF \| cat \| bash` · `\| tee /tmp/x \| bash` · `\| cat \| at now` (mutating body) | AG-2 (heredoc bodies through a pipe) |
| `xargs systemd-run systemctl restart <unit>` · `xargs su -c '…'` · `xargs xargs …` | AG-3 (the xargs first-word gate) |
| `echo 'systemctl clean <unit>' \| bash` · `reenable` · `thaw` | AG-6 (verbs that diverged between the two tables) |
| `stdbuf -e L systemctl restart <unit>` | T7 (stdbuf's `-e` missing from a shared tuple) |
| `A=ensemble; echo "systemctl restart ${A}-main" \| cat \| bash` | Deferred#3 (env substitution through a multi-stage pipe) |
| `systemctl cat stop '*'` · `systemctl cat stop "$AMBIENT_UNIT"` | AG-6 (fail-close behind a tail mutating verb) |

**1 shape REFUSE → ALLOW** (an over-block removed):

| shape | the finding |
|---|---|
| `systemctl --legend yes status <unit>` | AG-4(i)/T8 (`--legend` takes a MANDATORY value on systemd 255) |

**No verdict change, verified no-regression** (they were ALLOWED at base and stay ALLOWED —
the over-block class AG-4(ii)/CO-1/T6 was introduced by wave 4's fail-closed and is now
correctly handled WITHOUT re-opening it): `watch -n1 systemctl status <unit>`,
`xargs -I{} systemctl status <unit>`, `xargs --max-args=1 systemctl status <unit>`,
`systemd-run -Hhost true`, `env -iS 'systemctl status <unit>'`, `stdbuf -i0 systemctl status <unit>`.

## 2. WHAT LANDED

**A1 — ONE analyser, every scanner.** `_option_token()` normalises long options, short
BUNDLES (`-aP`, `-iS`), ATTACHED values (`-S<payload>`, `-n1`, `-I{}`, `-Hhost`) and the
`--opt=value` self-contained form, and reports whether the token is KNOWN at all. Applied at
every option-scanning site: systemctl, the wrapper-arg peels (timeout/nice/ionice/stdbuf),
sudo/doas, env (both sites), the argv runners, xargs, ssh. An option in neither of a tool's
tables fails closed wherever that tool's scanner is fail-closed (runners, xargs, ssh);
systemctl/env/sudo are documented SKIP-UNKNOWN with the closing mechanism named.

**A2/F-01** sudo's value options `-r/--role`, `-t/--type` (+`--preserve-env` as a flag) —
NOPASSWD made the space-form hole directly exploitable. **A3/AG-1** env `-S` peels shell
keywords and wrappers before scanning, parses bundles, and FAILS CLOSED on an unresolvable
split operand. **A4/AG-2** heredoc bodies handed to a pipe-fed executor are shape-tested
(prose-to-file with no pipe/executor stays allowed, pinned). **A5/AG-3** the xargs tail is
analysed as a whole command line (depth-bounded). **A6/AG-4(iii)/AG-6/T2** the mutating-verb
set is ONE source of truth: `_MUTATING_VERB_RE` is DERIVED from `_SYSTEMCTL_TAIL_MUTATING`
at import, with hyphen-aware boundaries so `preset` and `preset-all` are distinct; the set
gained the host-verified `unmask`, `reset-failed`, `daemon-reexec`, `set-environment`,
`unset-environment`, `add-wants`, `add-requires`; a tail mutating verb followed by an
unresolvable/globbed operand fails closed. **A7** `--legend` added. **A8/T5** env's option
tuple lifted to one module frozenset (both sites), with the optional-value `--*-signal`
family verified and filed as non-consuming flags. **A9/T7** the four wrapper-arg tools split
into per-tool grammars; stdbuf's `-i/-o/-e` added. **A10/Deferred#3** CLOSED — the whole-text
rule also tests the RESOLVED segment payloads. **A11** ssh carries a boolean set + a value
set (both from the host's usage line) and fails closed on an unknown option. **A12/T24**
`_pipeline_laundering_violation`'s `code`/`saw_pipe` params are REQUIRED — no default to the
weaker guard.

## 3. THE PINS

Counts base → this tip: DENY — pipe 6→17, xargs-stdin 4→4, runner 10→61, scheduler 3→5,
systemctl option-anchor 0→10, **read-only-tail 0→3 (NEW family — the ISOLATING pin B1/AG-5/F-02)**,
**mutating-verb 0→12 (NEW family)**. ALLOW — `_LAUNDER_ALLOW` 18→40, `_READONLY_COMMANDS`
15→23. The file is **254 nodes** (base 134); the pack **293** (base 173).

B1's isolating pin is `systemctl cat stop <unit>` (and `help`/`status` spellings): a
read-only word in VERB position with **no option at all**, so the option-arity fix cannot
cover it — neutralising the non-terminal parse ALONE reddens it (M7f → 5 reds). The three
per-option ALLOW pins (`--boot-loader-entry Restart`, `--image-policy Restart`,
`--boot-loader-menu Restart`) are added beside `-P`'s.

## 4. THE MUTATION LEGS — 20 legs, 74 reds, EXPECTED-count equality

`M6g` 11 · `M6h` 2 · `M6i` 12 · `M6j` 8 · `M6k` 1 · `M6l` 9 · `M6m` 2 · `M6n` 1 · `M6o` 2 ·
`M6p` 1 · `M6q` 5 · `M6r` 2 · `M6s` 1 · `M7a` 5 · `M7b` 4 · `M7d` 7 · `M7e` 3 · `M7f` 5 ·
`M7g` 1 · `M7h` 1. Every leg: byte-identical restore, tree clean, and a POST-RESTORE GREEN
re-run. `EXPECTED` pins each leg's documented count, so a leg silently losing pins is loud.

New legs: **M7a** the bundle/attached normalisation · **M7b** the heredoc-body + resolved-payload
scan (ONE leg because the resolved-payload scan is a second defence over the same bodies) ·
**M7d** the verb-set derivation (the six verbs re-diverge) · **M7e** sudo's value options ·
**M7f** the non-terminal read-only parse ALONE (B1's isolating leg) · **M7g** the
resolved-payload scan · **M7h** ssh's unknown-option fail-closed. Existing legs were
re-anchored to the current code; **M6h/M6o changed shape** — with fail-closed in place,
"remove the table entry" no longer produces a bypass (the option becomes UNKNOWN and is
refused), so those plants now keep the options KNOWN while making them value-less.

## 5. THE PACK AND THE BASE-VS-HEAD RED DIFF

| side | result | reds (`grep '^FAILED'` only) |
|---|---|---|
| base `c36c3e07c` | 173 passed, exit 0 | 0 |
| tip | **293 passed**, exit 0 | 0 |
| **net-new reds** | — | **0** |

Run from the worktree root with the worktree `.venv`, per-variable `POSTGRES_*` pins, the
PACKS base-row invocation. The two legacy differential files are byte-identical from the
pre-inversion base and pass unchanged; the kill-leg transplant and the PORT whole-word scope
were re-verified.

## 6. HARNESS INTEGRITY (C4–C8, C11, B4)

`--leg both` is DELETED (USAGE now says `--leg all`); `git_clean()` requires git's own
return code to be 0 AND empty output; the child pytest runs with `POSTGRES_*`/`PG*` scrubbed
per-variable; every leg re-runs the pins POST-RESTORE and requires GREEN; `--worktree` must
be a linked worktree (a `.git` FILE) — the canonical checkout and `/tmp` are both REFUSED
(verified live); the header notes that M6a–f were the prior lane's `/tmp`-only legs;
`EXPECTED` enforces the documented red count.

## 7. DOCUMENTATION TRUTH (C1, C2, C3, C9, C10, R1, R2, R3)

- **C1**: rule 3 now states the exception (a mutating verb later in the command), and six
  CONTRACT NOTES were added: the tail scan's POSITIONAL semantics, the per-tool
  fail-closed-on-unknown convention, the executed-context shape over-block, the bare-`ssh`
  executor ruling (R1), the shell attached-`-c` narrowing (C3), and the two dropped classes.
- **C2**: a 7-table OPTION GRAMMARS map sits above `_OPTIONS_WITH_VALUE` naming every table,
  its tool and its failure mode, with back-pointers at each table.
- **C9**: the PACKS REV4 row is restated with the exact counts (above) and slimmed to counts
  + a pointer to this doc; the stale restore sha is gone.
- **C10**: `_command_word_index` has **12 call sites** at this tip, measured (`grep -c` 13
  including the definition; +1 this wave from the env peel). Not 8, not 13-as-averaged: 12.
- **CO-4**: the wave-4 doc now says 18→32 (not 26→32). **T20**: the wave-3 doc says +8.
- **R2**: DISCLOSED in the docstring (not aligned) — the tail scan is positional, and
  dropping that would blanket-scan and re-open the property-name over-block.
- **R3**: every touched table records its tool, the host's version and the date.

## 8. DISCLOSED RESIDUALS

(a) the file-at-run-time payload (`cat notes.md | bash`); (b) a port token as a NON-operand
argument (`systemctl restart foo 9797`) — widening would re-open the non-operand port
false positives; (c) the SKIP-UNKNOWN wrappers (env/sudo/timeout/nice/ionice/stdbuf):
`_command_word_index`'s single-index contract is shared by 12 call sites, and turning
"unknown option" into a fail-closed signal there is a refactor of that contract (out of
scope; the KNOWN-option shapes are all closed); (d) the `ens`-stem token set —
`A=ens; echo "systemctl restart ${A}-main" | bash` is ALLOWED because the shape token set is
the OLD text guard's set verbatim (`ens` was never in it; the structural matcher does cover
`ens-*` directly); (e) the shell attached-`-c` narrowing; (f) the whole-text rule's
conservatism.

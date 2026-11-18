# Systemctl guard — wave-3 closing fixes (pipe shapes + runner option value-anchoring)

**Verdict: COMPLETE — all 8 acceptance criteria PASS**

- Worktree: `/home/nea/ensemble-src-wt-guard-pipe-shapes`
- Branch: `fix/systemctl-guard-pipe-shapes`
- Base: `c36c3e07c2f00947cd1257a7d1fc01190c56c376` (== `origin/latest` at dispatch, unmoved)
- Tip (code): `43cb795cf5ecb543b157210baec06dd61d70556c`
- Interpreter: worktree `.venv` (CPython 3.14.7); per-variable `POSTGRES_*` pins; `ENSEMBLE_SELF_ENV=dev`
- Guard file sha256 at tip: `6f288e0f554c2c909e51ae943701524de577ac8c7b9bf40216e8cbd9ae6a9378` (base `37bbf0d93fea66ea55de63049ff87f06ba777c09a3f8a9ab47355860c81b666b`)
- Prior wave: the wave-2 tester RESULTS doc is copied into this tree VERBATIM
  (`.agents/tester/RESULTS/sonic-wi-a5ec5b3e-wave2-results.md`, sha256
  `7079921207b5f0ee4facbd1714c3d4b355b2483b875d75c754606f08de0276f8`, `cmp` clean against the
  source in the prior lane's worktree — it was UNTRACKED there, so this copy is the only
  committed record of the wave-2 PASS).

## 1. THE CHANGE LIST

`daemon/tools/_systemctl_guard.py` (+112 / −13)

| # | change | why |
|---|---|---|
| 1 | `_scan_segments` now also returns `saw_pipe` (the lexer tracks the PIPE operator outside quotes) | the separator list cannot answer "is there a pipe": a segment that ends on an EMPTY word DROPS its separator, so `( ... )` and `{ ... ; }` swallow the operator between the halves |
| 2 | `_pipeline_laundering_violation` applies `_text_has_mutating_ensemble_shape` to the **ENTIRE stripped code text** whenever the command pipes into an EXECUTOR (any segment whose `_executor_kind` is not None); the adjacency-scoped payload test is KEPT | the segment that PRODUCES a pipe-fed payload is not always the segment that CARRIES it (subshell / brace group / any forwarding stage) — the adjacency test alone left real laundering shapes ALLOWED |
| 3 | `_ARGV_RUNNER_VALUE_OPTIONS` (one shared set) → `_SYSTEMD_RUN_VALUE_OPTIONS` + `_WATCH_VALUE_OPTIONS` + the `--on-*`/`--timer-*` PREFIX rule, with the two no-value timer options (`--on-timezone-change`, `--on-clock-change`) EXCLUDED; `--opt=value` forms stay single self-contained tokens | a runner option that CONSUMES the next word and is treated as a FLAG anchors the tail analysis on the VALUE — the real command behind it is never analysed. The two runners do not share option semantics on this host (`watch -t` = `--no-title`, `watch -p` = `--precise`, `systemd-run -t` = `--pty`, `-P` = `--pipe` — all FLAGS) |
| 4 | `--mkdir` removed from the systemctl value-option set | `--mkdir` takes NO value (it is a flag): treating it as value-consuming ate the VERB, so `systemctl --mkdir restart ensemble-main` was **ALLOWED** although it really does restart the unit |

`tests/unit/tools/test_systemctl_intent_guard.py` (+114 / −0) — the pins, below.
`.agents/tester/mutation/systemctl_guard_m6_mutation.py` (+172 / −0, NEW) — the checked-in
counter-mutation harness. **It MUTATES SOURCE FILES and must be run manually, never from CI.**
The `.agents/` tree is NOT part of the release payload (verified in `scripts/upgrade/stage.sh`:
the payload copies `agents/`, `frontend/dist/…`, `plugins/`, `docs/guidelines/`, `config.yaml`,
`launcher.sh`, the binary and `scripts/` — `.agents/` appears only in a doc comment), so the
harness ships nowhere near the live install.

## 2. THE PINS ADDED (+41 nodes: 134 → 175 in the file; 173 → 214 in the pack)

- `_PIPE_LAUNDER_SHAPES` (+7): subshell `(echo 'systemctl restart ensemble-main') | bash`; brace group
  `{ echo 'systemctl restart ensemble-main'; } | bash`; `| cat |`; `| tee /tmp/x.log |`; `| grep ensemble-main |`;
  `| cat | tee /tmp/x.log |`; and a NON-shell executor (`| cat | at now`). The family is GENERAL:
  the whole-text rule does not enumerate shapes.
- `_RUNNER_SHAPES` (+19): the `--on-calendar` / `--on-boot` / `--on-startup` / `--on-unit-active` /
  `--on-unit-inactive` / `--timer-property` VALUE forms; their `--=` forms; the two no-value timer
  options; the flag options `-P` / `-t` / `watch -p` / `watch -t` / `watch -q`; the plain value
  options `--description` / `--working-directory` / `--uid`; and the two "a second option after the
  value" forms (the value must not swallow the command that follows it either).
- `_SYSTEMCTL_OPTION_ANCHOR_SHAPES` (NEW family, +6): the `--mkdir` bypass + four
  value-consuming options that must still consume their value.
- `_LAUNDER_ALLOW` (+8): read-only through a value-consuming runner option
  (`--on-calendar "12:00"`, `--on-calendar=12:00`, `--on-boot 60`, `--on-unit-inactive 30`,
  `--timer-property …`, `--description …`, `watch -n 5`, `watch -q 5`).
- `test_property_name_colliding_with_a_mutating_verb` (NEW): the executed-context SHAPE residual
  is PINNED AS A REFUSAL (`ssh` / `su -c` / `bash -c` / pipe) and the DIRECT context is pinned as
  ALLOWED — the two contexts cannot drift apart silently.

All 18 REV3 `_LAUNDER_ALLOW` legs are UNCHANGED and still green (verified individually and as
part of the file run). No superset-style assertion exists anywhere in the file: the invariant
encoded is "the pipe/executor family is restored to the old text guard's strength", NOT "the new
guard refuses everything the old guard refused" — the two classes the new module deliberately
dropped (the PROSE over-block and the DIRECT-context property-name over-block) stay dropped.

## 3. THE MUTATION LEGS

Harness: `.agents/tester/mutation/systemctl_guard_m6_mutation.py`, run in a DETACHED throwaway
worktree (`/tmp/wt-mut-43cb` at the tip). It refuses to run unless the target file is clean in
git, restores the original bytes in a `finally` block, and verifies the sha256 afterwards.

| leg | plant | reds | sha256 before | sha256 after | restore | tree |
|---|---|---|---|---|---|---|
| M6g | the whole-text pipe rule removed (adjacency-scoped analysis only) | **7** | `6f288e0f554c2c909e51ae943701524de577ac8c7b9bf40216e8cbd9ae6a9378` | `6f288e0f554c2c909e51ae943701524de577ac8c7b9bf40216e8cbd9ae6a9378` | BYTE-IDENTICAL | clean |
| M6h | the `--on-*` / `--timer-*` prefix rule removed | **7** | `6f288e0f554c2c909e51ae943701524de577ac8c7b9bf40216e8cbd9ae6a9378` | `6f288e0f554c2c909e51ae943701524de577ac8c7b9bf40216e8cbd9ae6a9378` | BYTE-IDENTICAL | clean |
| M6i | the WHOLE runner value-consumption removed (prefix rule + both exact sets) | **12** | `6f288e0f554c2c909e51ae943701524de577ac8c7b9bf40216e8cbd9ae6a9378` | `6f288e0f554c2c909e51ae943701524de577ac8c7b9bf40216e8cbd9ae6a9378` | BYTE-IDENTICAL | clean |

M6i is an EXTRA leg beyond the brief's M6g/M6h: it proves the plain value-option pins
(`--description`, `--working-directory`, `--uid`) and the `watch` set members, which the
prefix-only M6h plant cannot reach. Under M6i the pre-existing `watch -n 5` pin also reddens —
the `-n` handling is load-bearing for it too.

Honest note on the `--timer-property` pin: the PLAIN form
(`systemd-run --timer-property AccuracySec=1s systemctl restart ensemble-main`) was ALREADY refused at
base — the option's value is assignment-shaped, so the command-word index skips it and lands on
`systemctl` anyway. It was therefore never a bypass and it does NOT redden under any plant. The
`--timer-*` PREFIX rule is proven by the ADDED form
(`… --timer-property AccuracySec=1s --unit foo systemctl restart ensemble-main`), which WAS allowed at
base and reddens under M6h.

## 4. THE FULL PACK AND THE BASE-VS-HEAD RED DIFF

Invocation (the PACKS base row, unchanged): worktree `.venv`, per-variable `POSTGRES_*` pins,
`ENSEMBLE_SELF_ENV=dev`, `-p no:randomly --timeout=120`, over
`tests/unit/tools/test_systemctl_intent_guard.py` + `tests/unit/test_pkill_overlay_pins.py` +
`tests/unit/test_pkill_overlay_guard.py` + `tests/unit/test_spawn_boundary_scrub.py`.

| side | result | reds (`grep '^FAILED'` only) |
|---|---|---|
| base `c36c3e07c` | **173 passed**, exit 0 | 0 |
| tip `43cb795cf` | **214 passed**, exit 0 | 0 |
| **net-new reds (head − base)** | — | **0** |

The 5 ambient-dependent reds the REV3 row records did not fire in this ambient env (0 red on
BOTH sides), so the comparison is 0 vs 0.

The 2 legacy differential files (`tests/unit/test_pkill_overlay_pins.py`,
`tests/unit/test_pkill_overlay_guard.py`) are byte-identical from the pre-inversion base and
pass UNCHANGED. The kill legs of the old predicate survive in the new module as a
character-for-character transplant (`is_kill_like` / `kill_leg_violation`), so the kill half of
"at least as strong" is provable by BYTE-DIFF, not only by test.

## 5. THE NEWLY-DISCLOSED OVER-BLOCKS (both introduced by the whole-text rule)

1. **A piped read-only whose property name COLLIDES with a mutating verb is REFUSED.** The shape
   test sees the verb: `echo 'systemctl show ensemble-main -p Restart' | bash` now refuses. This is the
   SAME residual the reviewer disclosed for the executed contexts (`ssh` / `su -c` / `bash -c`),
   extended to the pipe context by the whole-text rule. It is PINNED as a refusal
   (`test_property_name_colliding_with_a_mutating_verb`); the DIRECT context stays allowed.
2. **A payload that only reaches the executor through a FILE at RUN time is still invisible**
   (`cat notes.md | bash`): the guard sees the file name, never the file's content. Pre-existing
   residual, carried forward in the PACKS row.

## 6. THE EXACT PACKS ROW CHANGED

`.agents/tester/PACKS.md`, inside the `systemctl-intent-guard-pack-registration` block:

- **REV4 row ADDED** above REV3 (newest-first): `systemctl_intent_guard_unit` (**REV4 — pipe-shape
  closure + runner option value-anchoring**, commit `43cb795cf` on
  `fix/systemctl-guard-pipe-shapes`, base `c36c3e07c`) — +41 nodes (175 in the file; 214 in the
  pack), with the DENY/ALLOW scope, the M6g/M6h/M6i red counts, the byte-identical restore proof,
  the two disclosed over-blocks and the unknown-option residual.
- **REV3 row AMENDED**: "read-only through **EVERY** runner" → "read-only through every runner —
  the READ-ONLY VERBS pass through every runner; a property name COLLIDING with a mutating verb
  (e.g. `-p Restart`) still REFUSES in an EXECUTED context (ssh / su -c / bash -c / pipe) — a
  disclosed SHAPE residual; the DIRECT context stays allowed (this clause is SUPERSEDED IN PART by
  the REV4 row)". The over-claim is gone; the REV3 DENY/ALLOW legs themselves are unchanged.

## 7. FOUND BUT NOT FIXED (reported, out of scope)

- `daemon/tools/_systemctl_guard.py` — an UNKNOWN runner option that takes a value (not in the
  verified set and not matching a prefix) still mis-anchors the tail. Enumeration cannot close
  it; the closing shape would be a fail-closed tail anchor (analyse every candidate command
  position), which is a refactor of the runner branch and was explicitly out of scope for this wave.
- `daemon/tools/_systemctl_guard.py` — the systemctl value-option set still carries `--message`
  and `--reboot-argument`, which are NOT in this host's `systemctl --help` output and could not be
  verified. Keeping them is harmless (an unverifiable option can only eat a verb on a command
  that systemd itself would reject); removing them would be a guess in the other direction. Not
  changed, disclosed.
- `daemon/tools/_systemctl_guard.py` — the systemctl value-option set is missing `-P`,
  `--image-policy`, `--boot-loader-menu` and `--boot-loader-entry` (all verified as value options
  in this host's `systemctl --help`). Adding them would fix an over-block on read-only commands
  that carry such an option, but each addition widens the "an option eats the verb" weakness, so
  the safety-first choice was to leave them out. Reported, not changed.
- The whole-text rule is conservative by construction: a command that pipes into an executor and
  mentions the shape ANYWHERE in its stripped text is refused, even when the shape is in a part of
  the text that is not the payload. That is old-text-guard strength, kept on purpose.

## 8. THE ACCEPTANCE CRITERIA

| # | criterion | verdict |
|---|---|---|
| 1 | all 5 review shapes (plus the general family) REFUSE | PASS — 7 new pipe pins, all green |
| 2 | all 4 systemd-run value-option shapes REFUSE; the `=`-forms still work | PASS — 6 value forms + the `=`-form pinned |
| 3 | the 18 `_LAUNDER_ALLOW` legs and the 45-node both-direction selection stay GREEN | PASS — unchanged and green (+9 new ALLOW legs) |
| 4 | the full pack GREEN from the worktree root with the worktree venv | PASS — 214/214, exit 0 |
| 5 | net-new reds vs base = 0 | PASS — 0 (0 vs 0) |
| 6 | M6g and M6h each RED under their own plant, byte-identical restore | PASS — M6g 7 red, M6h 7 red, M6i 12 red, all byte-identical |
| 7 | the tree is clean of stray edits | PASS — `git status --porcelain --untracked-files=all` empty after every run |
| 8 | PACKS: REV4 above REV3, REV3's "EVERY runner" claim corrected, the colliding-property residual disclosed | PASS |

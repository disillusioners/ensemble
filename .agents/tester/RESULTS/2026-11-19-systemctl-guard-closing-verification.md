# 2026-11-19 — Independent verification of the systemctl/bash "guard-inversion" closing fixes

**Gate role:** TESTER (independent of the lane; own scratch worktrees only)
**Lane tip under test:** `4d2c54487614bcb9c9c56132c136d89a1fb055ca` (branch `fix/systemctl-guard-pipe-shapes`)
**Base:** `c36c3e07c2f00947cd1257a7d1fc01190c56c376` (== `origin/latest`, confirmed unmoved)
**Scratch worktrees (mine, created and removed for this gate):**
`/home/nea/ensemble-src-wt-guard-verify` @ `4d2c54487` (HEAD leg) and
`/home/nea/ensemble-src-wt-guard-base` @ `c36c3e07c` (BASE leg) — both with the
lane fence copied in (`.env` mode 600, `POSTGRES_DB=ensemble_dev`,
`POSTGRES_USER=ensemble_dev_app`, `127.0.0.1:5432`, `DAEMON_PORT=8079`,
`PORT=8079`, `ENSEMBLE_SELF_ENV=dev`; `grep -c ensemble_prod .env` = **0**).
Own `uv sync` venv per worktree; `daemon.__file__` proved to resolve INSIDE each
scratch worktree (`/home/nea/ensemble-src-wt-guard-verify/daemon/__init__.py`).
**The lane worktree was READ-ONLY throughout** (no edits to `daemon/`, no edits to
`tests/`, no commits, no branch changes). This RESULTS file is the only artefact
written into it, and it is **NOT committed** (the leader owns the commit).

---

# VERDICT: **FAIL**

The lane's own scope is **fully verified** — every K1/K2/F9–F18 shape refuses,
every disclosed dropped class stays allowed, the pack is green at 246/246 with
net-new reds **0**, the mutation harness reproduces exactly (13 legs, 41 reds),
and there is **no collateral damage**.

**BUT** the independent L5/L6 differential found **one genuine, fully-exploitable
bypass of the exact class this lane was commissioned to close** (the F9–F18
"option-grammar" class), which is **NOT covered by any of the lane's four
disclosed residuals**:

> `sudo --role <VALUE> systemctl restart <unit>` (and `-r`, `--type`, `-t`)
> → the guard **ALLOWS** it, the OLD text guard **REFUSED** it, and on this
> host `sudo` is `NOPASSWD: ALL` so the shape **actually reaches and executes
> `systemctl`** against a live ensemble unit.

Detail: **F-01** below. Blast radius: the same class as F9–F18 (10 instances the
lane closed); this is an 11th instance that the lane's audit missed.

**Secondary finding** (not a bypass): **F-02** — neither F9/F10 defence has an
isolating DENY pin; a future single-defence regression in the NON-TERMINAL
read-only parse is invisible to every pin (measured: 0 reds). The lane
**disclosed** this redundancy in the harness docstring, but the PACKS.md row does
not carry it forward in a machine-checkable form.

---

## Per-leg results

### L1 — PACKET (head vs base, red-set diff) — **PASS**

Invocation reproduced verbatim from the PACKS.md REV4 row ("same invocation as the
base row below" → the REV1 row's pytest command), from the scratch worktree root
with the scratch venv, per-variable `POSTGRES_*` pins (never the `env -u
POSTGRES_*` glob form):

```
env -u DATABASE_URL ENSEMBLE_SELF_ENV=dev POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 \
  POSTGRES_DB=ensemble_dev POSTGRES_USER=ensemble_dev_app POSTGRES_PASSWORD=… \
  timeout 300 .venv/bin/python -m pytest -q -p no:randomly --timeout=120 \
    tests/unit/tools/test_systemctl_intent_guard.py tests/unit/test_pkill_overlay_pins.py \
    tests/unit/test_pkill_overlay_guard.py tests/unit/test_spawn_boundary_scrub.py
```

| side | collected | result | rc | reds (`grep '^FAILED'` only) |
|---|---|---|---|---|
| HEAD `4d2c54487` | 246 | **246 passed**, 0 failed, 0 error | 0 | **0** |
| BASE `c36c3e07c` | 173 | **173 passed**, 0 failed, 0 error | 0 | **0** |
| **net-new reds (head − base)** | — | — | — | **0** |
| base-only reds (base − head) | — | — | — | **0** |

Both legs' `FAILED` sets are empty, so the diff is empty in both directions.
Red classification used `grep '^FAILED'` **only** — the `(FAILED|ERROR)` form
would have invented phantom reds from the migration ERROR line. No red needed
QUARANTINE adjudication (there were none) and no red file is in lane reach
(`git diff --name-only c36c3e07c 4d2c54487` = PACKS.md, 3 RESULTS docs, the
mutation harness, `daemon/tools/_systemctl_guard.py`,
`tests/unit/tools/test_systemctl_intent_guard.py`).

### L2 — BEHAVIOURAL probe (my own harness, NOT the lane's pins) — **PASS (0 failures)**

Own probe (`/tmp/guard_verify/probe.py`, live-pattern literals reconstructed at
runtime) calling the guard's public entry points directly:

| probe set | shapes | entry points probed | failures |
|---|---|---|---|
| DENY (K1×7, K2×8, F9/F10×4, F11×3, F12×2, F13–F18×9) | **33** | `systemctl_violation`, `overlay_violation`, `kill_leg_violation`, `_pkill_overlay_violation` (bash seam) | **0** |
| ALLOW (prose ×5, property-name ×3, port-token ×1, read-only ×12) | **21** | same 4 | **0** |
| KILL-leg controls | **5** | `kill_leg_violation` + `overlay_violation` | **0** |
| **PROC LANE** (`proc_run` via `create_proc_tools`) | **54** (33 deny + 21 allow) | `daemon/tools/proc_tools.py:2594` → `bash.py:1366` → shared matcher | **0** |

Every DENY shape returned non-None on **all four** entry points and was refused
by the **proc lane** too (the proc lane's guard runs *before* timeout validation
— `proc_tools.py:2594` precedes the `timeout < 0` check — so the ALLOW controls
were issued with `timeout=-1` and produced `timeout must be >= 0`, i.e. no spawn;
the refusal string was absent for all 21 ALLOW controls and present for all 33
DENY shapes).

Refusal tokens: 25 × `ensemble-main` (the structural path), 8 × `laundered-payload`
(the pipe/executor path). The KILL legs returned `ensemble-main` / `9797` as
expected — the 5 kill arms are **byte-identical** to the old predicate
(verified by programmatic diff of the 5 regex arms: 5/5 identical), and the
live-name table is identical (only the constant name differs).

### L3 — THE TWO DROPPED CLASSES — **PASS**

Explicitly probed and confirmed to return `None` (ALLOWED) on
`systemctl_violation`, `overlay_violation` **and** the bash seam:

| dropped class | shape | verdict |
|---|---|---|
| prose over-block | `cat > notes.md <<'EOF'\nrun systemctl restart <unit> to recover\nEOF` | **None** ✅ |
| prose over-block | `grep -n 'systemctl restart <unit>' docs/ops.md` | **None** ✅ |
| prose over-block | `printf '%s\n' 'systemctl stop <unit>' > /tmp/reminder.txt` | **None** ✅ |
| prose over-block | `echo 'do not systemctl stop <unit>'` | **None** ✅ |
| prose over-block | `echo 'systemctl restart <unit>' \| grep -n restart` | **None** ✅ |
| property-name (DIRECT) | `systemctl show <unit> -p Restart` | **None** ✅ |
| property-name (DIRECT) | `systemctl show <unit> --property=Restart` | **None** ✅ |
| property-name (DIRECT) | `systemctl -P Restart <unit>` | **None** ✅ |
| inert port-token narrowing | `systemctl restart foo 9797` | **None** ✅ |

**No dropped class was reintroduced.** (The lane's own `test_property_name_colliding_with_a_mutating_verb`
also pins that the *executed* context still refuses the same property names —
confirmed by my probe: `ssh host 'systemctl show <unit> -p Restart'` and the
`su -c` / `bash -c` / pipe variants all refuse.)

### L4 — MUTATION PROOF — **PASS** (lane harness) + **1 FINDING** (F-02, my own legs)

**The lane's checked-in harness** `.agents/tester/mutation/systemctl_guard_m6_mutation.py`
run in MY scratch worktree, all 13 legs:

| leg | reds | leg | reds | leg | reds |
|---|---|---|---|---|---|
| M6g | 7 | M6k | 1 | M6o | 1 |
| M6h | 7 | M6l | 3 | M6p | 1 |
| M6i | 12 | M6m | 2 | M6q | 1 |
| M6j | 2 | M6n | 1 | M6r | 2 |
| | | | | M6s | 1 |

**41 reds over 13 legs — an exact reproduction of the lane's claimed counts.**
Every leg restored **BYTE-IDENTICAL**: sha256 before == after ==
`1ea773d0939fddc9bc99c8a780ae9c6e2575105ef07b0a4b144ecd5cf20b672c` (the guard file
at this tip). `git status --porcelain --untracked-files=all` empty after the run.

Harness contract verified empirically:
* **Refuses a dirty tree** — with the target dirtied, the harness printed
  `REFUSED M6g: daemon/tools/_systemctl_guard.py is not clean in git:` and
  planted nothing.
* **Restores in a `finally`** — harness lines 321-325; proven by 17 clean
  restores, and by the SIGKILL test below.
* **Warns it MUTATES SOURCE FILES / never from CI** — docstring lines 2 and
  11-20 (`THIS SCRIPT EDITS … IN PLACE` / `NEVER run it from CI, from a pack,
  or from a release step` / `ALWAYS give it a THROWAWAY worktree`).
* **SIGKILL caveat confirmed empirically**: SIGKILLing the harness mid-plant
  leaves the mutation PLANTED (sha `e9ddcc19…` ≠ `1ea773d0…`) — i.e. the
  `finally` is the *only* restore mechanism, exactly as documented. Restored
  manually; tree clean afterwards.

**My own independent single-defence legs** (my own anchors, my own harness,
`/tmp/guard_verify/my_mutation.py`):

| leg | plant | reds | verdict |
|---|---|---|---|
| baseline (no mutation) | — | **0** | control |
| **A1-parse-only** | remove the NON-TERMINAL read-only parse; arity table intact | **0** | ⛔ no pin isolates the parse fix → **F-02** |
| **A2-arity-only** | remove ONLY `-P` / `--image-policy` / `--boot-loader-menu` / `--boot-loader-entry` from `_OPTIONS_WITH_VALUE`; parse fix intact | **1** | `test_readonly_allowed[systemctl show -P Restart ensemble-main]` (an ALLOW pin, **not** an F9/F10 DENY pin) |
| **A3-both-control** | both halves (reproduces M6j) | **2** | the two F9/F10 DENY pins |
| **B1-env-recursion-only** | `env -S`/`--split-string` stop RECURSING; the option-table entry intact | **3** | the 3 F11 DENY pins ✅ |

**Answering the brief's redundancy question precisely:** F9/F10 genuinely have
two independent defences (A1 → still refused via the arity; A2 → still refused
via the parse), but **neither defence has an isolating DENY pin**. The F9/F10
DENY pins only go RED when *both* halves are neutralised (A3 = 2 reds). The
lane **disclosed** this in the harness docstring (lines 126-130: "a plant that
removes only one of them leaves the pin green"). The one partial exception is
that the `-P` arity entry *is* caught by the ALLOW pin
`test_readonly_allowed[systemctl show -P Restart ensemble-main]` (A2's single
red) — i.e. dropping `-P` from the arity table re-introduces the
property-name over-block and IS caught; but dropping
`--image-policy`/`--boot-loader-menu`/`--boot-loader-entry`, or restoring the
terminal read-only early return, is caught by **nothing**.

### L5 — THE DIFFERENTIAL INVARIANT — **1 REAL REGRESSION (F-01)** + 2 benign divergences

Differential harness built in `/tmp/guard_verify/`: the OLD text guard copied
**verbatim** from `git show 2618eb212:daemon/tools/bash.py` (lines 1984-2056 —
`_pkill_overlay_is_kill_like` + `_pkill_overlay_violation`) into
`/tmp/guard_verify/old_guard.py`, run against the NEW guard
(`daemon/tools/_systemctl_guard.py`) over a **179-shape corpus**:

| corpus slice | shapes |
|---|---|
| legacy pins extracted by AST from `tests/unit/test_pkill_overlay_guard.py` + `tests/unit/test_pkill_overlay_pins.py` | **43** |
| K1/K2/F9–F18 + ALLOW shapes | **28** |
| my own adversarial shapes (nested pipes, subshells, brace groups, command substitution, ANSI-C quoting, line continuations, base64/-eval, variable-expanded units, eval, redirection forms, `xargs -0`/`-I{}`, ssh with/without remote command, wrapper prefixes, `bash -c`/`sh -c`/`zsh -c`, `command`/`builtin`, sudo/env/timeout value-option gaps, …) | **108** |

**Result: both refuse 109 · both allow 33 · OLD-refused/NEW-allowed 33 · NEW-refused/OLD-allowed 4.**

The 4 *new*-refused/old-allowed shapes are the guard getting **stronger**
(`bash -c "$(cat payload.txt)"` → `unresolved-unit`; `systemctl set-property <unit> x=1`; `systemctl stop ens-main`).

The 33 *old*-refused/NEW-allowed shapes break down as:

| bucket | count | shapes |
|---|---|---|
| (i) prose / non-executing context | 12 | heredoc doc-write, `grep`/`printf`/`echo` of a mutating command, `# comment`, `systemctl status <unit> # restart if needed`, heredoc-to-ssh, `printf … > f; bash f` |
| (i) direct-context property name | 5 | `systemctl show <unit> -p Restart` / `--property=Restart`, `systemctl -P Restart <unit>`, `-p Start`, `-p Stop` |
| (i) no-op prefix (non-executing) | 2 | `true …`, `false …` |
| (ii) disclosed inert port-token narrowing | 1 | `systemctl restart foo 9797` |
| **UNCLASSIFIED** | **15** | see below |

Of the 15 unclassified, **11 are provably INERT** (verified against the real
tools on this host, using a non-existent `ensemble-zzz-probe` unit so nothing
could be touched):

| shape | why inert (measured) |
|---|---|
| `systemctl <unit> <verb>` | systemd: `Unknown command verb 'ensemble-zzz-probe'.` — the unit in the verb slot is rejected |
| `systemctl stop stop@<unit>.service` | `stop@.service` does not exist (`systemctl list-unit-files 'stop@*'` → 0 unit files; `systemctl cat stop@.service` → "No files found"); the template is `stop`, not an ensemble unit |
| `env --block-signal <V> …` | `env: 'scratchvalue': No such file or directory` — the optional-value option does **not** consume the next word |
| `env --default-signal <V> …` | same |
| `env --ignore-signal <V> …` | same |
| `sudo --preserve-env <V> …` | `sudo: scratchvalue: command not found` — optional-value option |
| `env/timeout/sudo --not-an-option <V> …` | `env`/`timeout`/`sudo` all reject the unknown option themselves (`unrecognized option`) ⇒ fail closed by the tool |
| `stdbuf -e <V> …` | `stdbuf: invalid mode 'scratchvalue'` |
| `doas --role <V> …` | `doas` is not installed on this host and `--role` is not a doas option |

**The remaining 4 are REAL BYPASSES → F-01** (sudo `-r` / `--role` / `-t` / `--type`).

### L6 — THE RESIDUALS — residual (c) VERIFIED; exposure ENUMERATED (see table below)

**Residual (c) claim verified as accurate**: an UNKNOWN option of `env`/`timeout`/`sudo`
still defaults to skip-1 in the shared wrapper peel (`_command_word_index`,
`daemon/tools/_systemctl_guard.py:1022-1106`), while systemd-run/watch/xargs DO
fail closed:

| shape | verdict |
|---|---|
| `env --not-an-option <V> systemctl restart <unit>` | **ALLOWED** (skip-1) ✅ claim confirmed |
| `timeout --not-an-option <V> systemctl restart <unit>` | **ALLOWED** (skip-1) ✅ claim confirmed |
| `sudo --not-an-option <V> systemctl restart <unit>` | **ALLOWED** (skip-1) ✅ claim confirmed |
| `systemd-run --not-an-option <V> systemctl restart <unit>` | **REFUSED** (fail closed) ✅ |
| `watch --not-an-option <V> systemctl restart <unit>` | **REFUSED** (fail closed) ✅ |
| `xargs --not-an-option <V> systemctl restart <unit>` | **REFUSED** (fail closed) ✅ |

*Minor doc nit:* the lane's residual (c) says the shared contract is "used by
eight callers"; I counted **12** call sites of `_command_word_index` in the
guard module (lines 808, 922, 1159, 1189, 1209, 1242, 1278, 1440, 1485, 1607,
1737, 1875). The coupling is therefore *larger* than the lane states — the
residual argument is conservative in the right direction.

**MEASURED RESIDUAL EXPOSURE — every KNOWN value option of env / timeout / sudo,
from THIS host's own `--help` output, probed as `<tool> <opt> <VALUE> systemctl
restart <unit>` (and the `=`-form):**

| tool | value option (host `--help`) | space form | `=`-form | reachable? |
|---|---|---|---|---|
| env | `-u` / `--unset=NAME` | REFUSED | REFUSED | — |
| env | `-C` / `--chdir=DIR` | REFUSED | REFUSED | — |
| env | `-S` / `--split-string=S` | REFUSED | REFUSED | — |
| env | `--block-signal[=SIG]` | **ALLOWED** | REFUSED | **inert** (optional value; env runs `<V>` as the command) |
| env | `--default-signal[=SIG]` | **ALLOWED** | REFUSED | **inert** |
| env | `--ignore-signal[=SIG]` | **ALLOWED** | REFUSED | **inert** |
| timeout | `-s` / `--signal=SIGNAL` | REFUSED | REFUSED | — |
| timeout | `-k` / `--kill-after=DURATION` | REFUSED | REFUSED | — |
| sudo | `-u`/`--user` `-g`/`--group` `-h`/`--host` `-p`/`--prompt` `-C`/`--close-from` `-T`/`--command-timeout` `-R`/`--chroot` `-D`/`--chdir` `-U`/`--other-user` | REFUSED | REFUSED | — |
| sudo | **`-r` / `--role=role`** | **ALLOWED** | REFUSED | 🔴 **REACHES systemctl** |
| sudo | **`-t` / `--type=type`** | **ALLOWED** | REFUSED | 🔴 **REACHES systemctl** |
| sudo | `--preserve-env=list` | **ALLOWED** | REFUSED | inert (optional value) |

**THE NUMBER THE CEREMONY NEEDS: 8 known value options are unhandled; of those,
**4 reach `systemctl`** (`sudo -r`, `sudo --role`, `sudo -t`, `sudo --type`) and
**4 are inert** (`env --block-signal`, `env --default-signal`,
`env --ignore-signal`, `sudo --preserve-env`).** The `=`-forms of all 8 are
refused (a single self-contained token ⇒ the command word is found).

**Residual (c) does NOT cover the 4 sudo options** — they are *known* sudo
options on this host (they are in `sudo --help`), simply absent from the
guard's sudo peel list (`daemon/tools/_systemctl_guard.py:1081-1084`). The
disclosure therefore under-states the residual by exactly the exploitable part.

### L7 — EVIDENCE + PACK REGISTRATION — **PASS** (1 stale sha in PACKS.md → **F-03**)

* **wave-2 doc byte-identical**: `cmp` against
  `/home/nea/ensemble-src-wt-wia5ec5b3e-int3/.agents/tester/RESULTS/sonic-wi-a5ec5b3e-wave2-results.md`
  → **IDENTICAL**; sha256 `7079921207b5f0ee4facbd1714c3d4b355b2483b875d75c754606f08de0276f8`
  on both sides. ✅
* **wave-3 doc** (`2026-11-19-systemctl-guard-pipe-shapes-wave3-results.md`) exists;
  its claims (`base c36c3e07c` 173 passed / 0 reds; `tip 43cb795cf` 214 passed / 0 reds;
  net-new 0; M6g 7 / M6h 7 / M6i 12 reds) all match my independent measurements
  (base 173 ✅; M6g 7 / M6h 7 / M6i 12 ✅).
* **wave-4 doc** (`2026-11-19-systemctl-guard-option-grammar-wave4-results.md`)
  exists; claims (`base c36c3e07c` 173 passed / 0 reds; `tip ee4edfb56` 246 passed /
  0 reds; net-new 0; 13 legs / 41 reds; base guard sha `37bbf0d9…`; tip sha
  `1ea773d0…`) all match my independent measurements ✅ (base sha `37bbf0d9…`
  independently confirmed at `c36c3e07c`; tip sha `1ea773d0…` confirmed).
* **PACKS.md REV4 row present** at `.agents/tester/PACKS.md:5160`, positioned
  **above** REV3 (`:5161`) — newest-first ✅. Its recorded invocation **works
  as written** (reproduced verbatim → 246 passed, rc 0) ✅.
* **Pack node count matches reality**: `--collect-only` → **207** in
  `tests/unit/tools/test_systemctl_intent_guard.py` (row claims "207 in the
  file" ✅) and **246** in the 4-file pack (row claims "246 in the pack" ✅);
  base pack **173** ✅.
* ⛔ **F-03**: the REV4 row quotes the restore sha as
  `cc97f1a81c41ffcc7062ba104a5afcabe64ba513e7b8117d4ce6ef7c7f16416c`. That value
  matches **NO** version of `daemon/tools/_systemctl_guard.py` anywhere in the
  lane's history (the only versions are `1ea773d0…` @ ee4edfb56/4d2c54487,
  `6f288e0f…` @ 43cb795cf/049fec8f9, `37bbf0d9…` @ base, `8483297d…`, `9bfdf756…`,
  `c3337032…`). The **substantive** claim holds (every leg restored byte-identical
  — I measured `1ea773d0…` before == after on all 17 legs), and the authoritative
  **wave-4 RESULTS doc quotes the correct `1ea773d0…`**. Only the PACKS.md
  literal is stale.

### L8 — NO COLLATERAL DAMAGE — **PASS** (net-new 0)

Broader unit surface touching the bash/proc guard seams — 23 files (the 4 pack
files + `tests/tools/test_bash*.py` ×6, `tests/tools/test_proc*.py` ×3,
`tests/tools/test_auto_kill_integration.py`, `tests/tools/test_ram_guard_fairness.py`,
`tests/unit/services/test_bash_spawn_reaper*.py` ×2,
`tests/unit/services/test_ram_guard_*.py` ×2, `tests/unit/test_ram_guard_config.py`,
`tests/unit/tools/test_service_tools.py`,
`tests/integration/test_service_tool_kill_site_exemption.py`) — identical command
on both legs (`-p no:randomly --timeout=120 -n 4 --tb=short -rf`, per-variable
`POSTGRES_*` pins, `timeout 1200`):

| side | result |
|---|---|
| HEAD `4d2c54487` | **762 passed, 13 failed** (55.70 s) |
| BASE `c36c3e07c` | **689 passed, 13 failed** (42.93 s) |
| **net-new reds (head − base)** | **0** |
| base-only reds | **0** |
| common reds | **13** — the sorted FAILED sets are **byte-identical** (sha256 `a40323326543976572ba725e9f8ab02637af40db5da7d4d8d7d012487a67d2ec` on both legs) |

The 13 common reds are: 4 × `tests/tools/test_auto_kill_integration.py`
(quarantined), 4 × `tests/tools/test_bash_spawn_stamp.py`,
1 × `tests/tools/test_proc_secret_env.py::…::test_no_overlay_byte_identical_to_p1`,
3 × `tests/unit/services/test_bash_spawn_reaper.py`, 1 ×
`tests/unit/services/test_bash_spawn_reaper_boot.py`. **None of these files is
in lane reach** (`git diff --name-only c36c3e07c 4d2c54487` does not touch them)
⇒ the reds are provably lane-innocent and base-attributed.

---

## FINDINGS TABLE

| id | severity | file:line | shape | OLD verdict | NEW verdict | disclosed? | note |
|---|---|---|---|---|---|---|---|
| **F-01** | 🔴 **CRITICAL** | `daemon/tools/_systemctl_guard.py:1081-1084` (sudo peel list, `_command_word_index`) | `sudo --role <V> systemctl restart <unit>` (also `-r`, `--type`, `-t` — **space** form only) | **REFUSED** | **ALLOWED** | ❌ **NOT disclosed** — residual (c) covers only *unknown* options; these are *known* sudo options | **Fully exploitable on this host.** `sudo --role <V> systemctl restart <unit>` → sudo consumes `<V>` as the role and **executes** `systemctl` (proved: `sudo -n --role scratchvalue systemctl restart ensemble-zzz-probe` → `Failed to restart ensemble-zzz-probe.service: Unit … not found`, i.e. systemctl RAN). `sudo -n -l` shows **`(ALL) NOPASSWD: ALL`** ⇒ no prompt ⇒ reachable straight through the agent bash lane (`daemon/tools/bash.py:1366`) and the proc lane (`daemon/tools/proc_tools.py:2594`). `ensemble-main.service` is **live and enabled** on this host (`systemctl list-unit-files 'ensemble*'`). Same class as F9–F18 (option-grammar); this is the 11th instance the lane's audit missed. |
| **F-02** | 🟠 IMPORTANT | `daemon/tools/_systemctl_guard.py:1645-1660` (non-terminal read-only parse) + `:250-262` (`_OPTIONS_WITH_VALUE`) | mutation: neutralise the NON-TERMINAL read-only parse alone (arity table intact) | n/a (new code) | n/a | ⚠️ partially (harness docstring `systemctl_guard_m6_mutation.py:126-130`; **not** carried into the PACKS.md row) | **No isolating pin.** My A1 leg: removing only the parse half ⇒ **0 reds** — every pin stays green. Removing only the arity half ⇒ 1 red, and it is the ALLOW pin `test_readonly_allowed[systemctl show -P Restart ensemble-main]` (i.e. dropping `-P` re-introduces the property-name over-block and IS caught) — but dropping `--image-policy` / `--boot-loader-menu` / `--boot-loader-entry` is caught by **nothing**. The two F9/F10 DENY pins only red when BOTH halves are removed (A3 = 2 reds). **Missing pin:** a DENY pin whose subject is the parse continuation itself, e.g. `systemctl --image-policy plain stop <unit>` with `--image-policy` deliberately absent from the arity table (or a unit test asserting the tail scan continues past a read-only verb). |
| **F-03** | 🟢 MINOR | `.agents/tester/PACKS.md:5160` (REV4 row) | quoted restore sha256 `cc97f1a8…` | n/a | n/a | ❌ | Matches **no** version of the guard file in the lane's history. The correct value is `1ea773d0939fddc9bc99c8a780ae9c6e2575105ef07b0a4b144ecd5cf20b672c` (as the wave-4 RESULTS doc quotes). The substantive claim ("every leg restored BYTE-IDENTICAL") **holds** — independently reproduced on 17 legs. Doc-only. |
| **F-04** | 🟢 MINOR | `.agents/tester/PACKS.md:5160` (REV4 row, residual (c)) | "shared by eight callers" | n/a | n/a | ❌ | Measured **12** call sites of `_command_word_index` in the guard module (`:808, :922, :1159, :1189, :1209, :1242, :1278, :1440, :1485, :1607, :1737, :1875`). The residual argument is conservative in the right direction (more coupling ⇒ harder to change), but the count is wrong. |
| **F-05** | 🟢 MINOR | `daemon/tools/_systemctl_guard.py:1081-1084` | `doas --role <V> systemctl restart <unit>` | REFUSED | ALLOWED | ❌ | `doas` is **not installed** on this host and `--role` is not a doas option, so the shape is inert here — but the guard's `doas` branch shares the sudo list, so the gap is latent if `doas` is ever installed with a value-taking option. Listed for completeness. |

---

## ENUMERATED RESIDUAL EXPOSURE (the number the ceremony needs)

**Per tool — known value options from THIS host's `--help` that are STILL unhandled:**

| tool | unhandled known value options | of which REACH `systemctl` | of which inert |
|---|---|---|---|
| **env** | `--block-signal`, `--default-signal`, `--ignore-signal` (3) | **0** | 3 (optional-value options; env runs the next word as the command) |
| **timeout** | — (0) | 0 | 0 — both `-s/--signal` and `-k/--kill-after` are handled at `:1061` | 
| **sudo** | `-r`, `--role`, `-t`, `--type`, `--preserve-env` (5) | **4** 🔴 (`-r`, `--role`, `-t`, `--type`) | 1 (`--preserve-env` — optional-value option) |
| **systemd-run** | 0 — unknown options FAIL CLOSED | 0 | 0 |
| **watch** | 0 — unknown options FAIL CLOSED | 0 | 0 |
| **xargs** | 0 — unknown options FAIL CLOSED | 0 | 0 |

**Totals: 8 known value options unhandled; 4 reach `systemctl` (all four sudo);
4 inert. Plus the disclosed residual (c): 3 unknown-option shapes
(env/timeout/sudo) are ALLOWED by the guard but the tools reject unknown options
themselves, so they are inert in practice.**

**RECOMMENDED FIX (not applied — the developer owns the fix):** add `-r`,
`--role`, `-t`, `--type` to the sudo peel list at
`daemon/tools/_systemctl_guard.py:1081-1084`. That is the same one-line
mechanism the lane used for `-s/--signal`/`-k/--kill-after` (M6n) and it closes
the class without touching the shared single-index contract. (The `doas` branch
shares that list, so it closes F-05 too.) A pin of the form
`sudo --role <V> systemctl restart <unit>` → `assert guard.systemctl_violation(...) is not None`
should accompany it.

---

## WHAT I COULD NOT VERIFY, AND WHY

1. **The LIVE `ensemble-main` unit was never touched.** Every exploitability
   probe used either a reconstructed literal or a **non-existent**
   `ensemble-zzz-probe` unit, so no live unit was stopped/restarted. The
   *reachability* of systemctl from the sudo shape is proven; the *effect* on a
   live unit was deliberately not demonstrated.
2. **The lane worktree was READ-ONLY.** I did not run the lane's own pack
   *script* from the lane worktree's `.venv`; I reproduced the REGISTERED
   invocation (the PACKS.md row's pytest command) from my own scratch
   worktrees with their own `uv sync` venvs. The registered invocation is
   therefore verified as *equivalent*, not as literally executed from the lane
   path.
3. **`git archive` / gitless BASE trees were not used** — both legs are real
   git worktrees with real `.git`, so no BASE result is vacuous from a missing
   git binary.
4. **The `finally`-restore mechanism was verified by outcome + source
   inspection + a SIGKILL counter-test**, not by injecting an exception into
   pytest's subprocess call (which would have required editing the lane's
   harness).
5. **The lane's 5 "ambient-dependent pre-existing reds"** recorded in the REV3
   row did not fire in my ambient env (0 red on both legs in L1); I could not
   reproduce that ambient, so I could not measure their red counts. This does
   not affect any verdict — net-new is 0 either way.
6. **The proc lane was probed with `timeout=-1`** for the ALLOW controls (the
   tool's timeout validation rejects before any spawn). For the 33 DENY shapes
   the guard fires first, so no spawn occurred. No proc lane spawn was
   exercised at all.

---

## REPRODUCTION ARTEFACTS (this gate's own, outside the repo)

| artefact | path |
|---|---|
| behavioural probe (L2/L3) | `/tmp/guard_verify/probe.py` (+ `probe_rows.json`) |
| old-guard verbatim copy (L5 baseline) | `/tmp/guard_verify/old_guard.py` |
| differential harness (L5) | `/tmp/guard_verify/differential.py` (+ `differential.json`) |
| my own single-defence mutation harness (L4) | `/tmp/guard_verify/my_mutation.py` |
| residual enumeration (L6) | `/tmp/guard_verify/l6_residual.py` (+ `l6_rows.json`) |
| pack legs L1/L8 | `/tmp/guard_verify/{head,base}.log`, `l8_{head,base}.log`, `l8_{head,base}.fails` |
| lane M6 harness output | `/tmp/guard_verify/m6_all.log` |

**Test verdict: FAIL — one CRITICAL (F-01, exploitable, undisclosed) + 2 IMPORTANT/MINOR.
The lane's declared scope (K1, K2, F9–F18, the dropped classes, the pack, the
mutation harness) is fully verified; the FAIL is on an 11th instance of the same
option-grammar class that the lane's audit did not reach.**

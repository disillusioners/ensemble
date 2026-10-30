# Decisions — promote-preflight-hang

**Lane:** `fix/promote-preflight-hang` · **Board:** `wi-fc1786a2` · **Base rev:** `b1183a637` (all anchors verified at this rev).

This is the **decision log**. Every contested choice is recorded with: the options, the recommendation,
the rationale, the rejected alternatives and **who must rule** (the architect / the leader), so a ruling can
be folded back as an additive decision entry without renumbering anything.

**Status legend:** `RECOMMENDED` = the plan proceeds on this unless ruled otherwise · `OPEN` = the architect/leader must rule · `ACCEPTED-BY-LEADER` = ruled and binding (2026-10-29 architect fold) · `CONFIRMED-BY-LEADER` = ruled and binding · `SUPERSEDED` = replaced by a later entry.

> **FOLD STATUS (2026-10-29):** this file was folded with the architect's `architecture-recommendation.md` (RULINGS 1–10, all ACCEPTED by the leader). Every decision below moved from RECOMMENDED/OPEN to **ACCEPTED-BY-LEADER** or **CONFIRMED-BY-LEADER** with the ruling citation; the new entries D1-8…D1-11, D2-4…D2-8, D3-1…D3-7 and D6-1…D6-8 carry the amendment numbers. The original text is retained; supersessions are marked inline.

---

## D1 — The journal read path: how the O(N²) scan is replaced

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29 — architect RULING 2** (option **P1**: a dedicated read-only helper). Plan: `phase1-plan.md` §2 D1-1/D1-2/D1-4/D1-10…D1-13.

### The problem, precisely

`journal_read` (`lib.sh:961`) performs a per-character brace/bracket/string balance scan whose inner step is an
**offset substring** (`c="${json:i:1}"`, `lib.sh:976`) ⇒ O(N²). On the 455,783-byte incident journal that is
**45–61 minutes** (`evidence/FORENSICS.md` §4-1); the executor was killed at 62 % of it.

The scan is not the only O(N²) on the same string: `journal_update`'s value scanner (`lib.sh:1046`-`:1070`) and
`_json_sub`'s bracket counter (`lib.sh:600`-`:620`) are also per-character. **Fixing only `journal_read` leaves
two live O(N²) siblings**, one of them on the **write** path.

### The options

| # | Option | Cost | Semantics | Blast radius | Verdict |
|---|--------|------|-----------|---------------|---------|
| **A** | **Byte-level port of the same state machine** (a helper that scans the raw bytes with the identical `ob/cb/os/cs/in_str/esc` algorithm) | O(N) — a single pass over the bytes | **Identical accept/reject set** (provably: every significant byte is ASCII and a UTF-8 continuation byte is ≥ 0x80, so byte-wise ≡ character-wise) | one helper + 3 call sites in `lib.sh` | **RECOMMENDED** |
| **B** | Strict `json.loads` | O(N) | **Different** verdict set — rejects `{'a':1}`, trailing commas, single quotes, invalid UTF-8, all of which the current scanner **accepts** | same | Rejected as the primary; held as an architect option (D1-4) |
| **C** | Pure-bash O(N) rewrite (count via `${json//x/}` length deltas) | O(N) per pattern pass | **Different** — count-based counting is not string-aware, so a brace inside a string literal flips the verdict | small | Rejected |
| **D** | Chunked bash extraction (`${json:i:4096}`) | still quadratic, ~4096× faster | identical | tiny | Rejected as a fix; **retained as the no-interpreter fallback** (bounded, warned) |
| **E** | Keep bash; just cap the journal size | — | unchanged | tiny | Rejected as the fix; **retained as the interim size guard** (D1-5) |

### D1-1 — Recommendation — ✅ **ACCEPTED-BY-LEADER 2026-10-29 (RULING 8: the byte-level port is the right approach)**, extended by D1-9 (the stdout contract) and D1-11 (three algorithms)

**Option A**, implemented as a helper that serves three verbs (balance-scan / nested-value-extract / RMW value-splice),
covering `journal_read`, `_json_sub` and `journal_update` together (they share the same string and the same
string-awareness rules).

### D1-2 — Why not Option B (the real trade-off) — ✅ **ACCEPTED-BY-LEADER 2026-10-29** (RULING 8; architect §3: strict-JSON read stays rejected — no re-litigation)

Option B is *simpler* and *stricter*, and it is what `journal_splice.py` already does on the **write** side
(`journal_splice.py:269`-`:305`, `json.loads` + strict UTF-8). It is rejected as the **read** side for one reason:
the read path feeds the **preflight entry checks** (`promote.sh:245`, `:286`) and the **torn-journal refusal**.
A stricter read refuses files the current read accepts ⇒ a ceremony that would have proceeded now refuses at
`exit 78`. That may be *correct*, but it is a **behaviour change on a safety-critical path** and it is not
required by the incident. (The house already recorded the same asymmetry at `journal_splice.py:273`-`:275`:
*"lib.sh's brace-walk could tolerate pure-ASCII garbage by accident, but json.loads+utf-8 cannot"*.)

**D1-4 — ARCHITECT OPTION — ✅ RESOLVED 2026-10-29: the option is DECLINED** (architect RULING 8 + §3: the byte-level port stands; a strict read stays rejected — no re-litigation). The conditional text below is retained as the record: if the architect rules that a *strict* read is preferable
(accepting the behaviour change), the parity corpus (Phase 1 §3.3) is re-specified to assert the **new** verdict
set, the change is called out as an explicit behaviour change in the release notes, and the two callers whose
refusal semantics change (`promote.sh:225`/`:276` and `rollback.sh:90`) get their own pins. The plan does not
assume this.

### D1-3 — Placement and the refusal policy — ✅ **RESOLVED 2026-10-29 by architect RULING 2 (see D1-8)** — the text below is retained as the reasoning record

The tension, verified at TIP:

* `journal_splice.py:549`-`:560` **refuses** any path under the live install unless the pipeline lock is held
  (`_pipeline_lock_held`, `:158`), and `:564`-`:568` refuses a missing journal.
* The shell's `journal_read` has **neither** refusal.
* Three read-path callers would therefore break if `journal_read` were routed through the existing `main()`:
  `status.sh:52` (**no lock at all**), `rollback.sh:90` and `restart.sh:133` (**both before** lock acquisition —
  `rollback.sh:73`-`:77` and `restart.sh:120`-`:124` even say the lock is not held/adopted yet at that point (*"The lock is not held yet at this point"* / *"The lock is not adopted yet at this point"*).
* Meanwhile `ledger_check.py:46`-`:55` establishes the house precedent that the **live-install fence** is
  aimed at *inspect tools*; the pipeline's own scripts are the sanctioned readers.

**Options:**

| # | Option | Effect | Verdict |
|---|--------|--------|---------|
| **P1** | A **dedicated read-only helper** with its own policy: reads are not mutations ⇒ **no live-install refusal**, absent file ⇒ rc 1 (today's behaviour), malformed ⇒ rc 1 + the same stderr wording class. | Zero behaviour change for all 25 distinct call sites; the write fence is untouched. | **RECOMMENDED** |
| **P2** | An op on `journal_splice.py` with an explicit **read-policy switch** that skips the live-install refusal for read verbs. | Same behaviour as P1 but keeps one helper file; slightly more surface on a file whose refusal machinery is delicate (four distinct refusal envelopes). | Acceptable alternative |
| **P3** | Keep the live-install fence for reads too: move `rollback.sh:90` / `restart.sh:133` **after** lock acquisition, and give `status.sh` an explicit exemption. | Changes the *call order* of two scripts and gives a diagnostic tool a special case; enlarges the diff and the blast radius. | Rejected |

**Recommendation: P1.** The architect rules (OQ2 in `plan-overview.md` §9).

### D1-5 — The journal size guard — ✅ **ACCEPTED-BY-LEADER 2026-10-29** (RULING 3 item 3: the guard converts the cliff to a loud refusal; the *growth* is routed to the writer's owner)

**Decision:** add a guard that **refuses loudly** above a byte threshold (env-overridable; the threshold is
journalled when it trips) instead of scanning for minutes.

**Why:** the incident's failure mode was a **silent** long stall. A guard converts "61 minutes then maybe fine"
into "immediate, loud, actionable". It is a **guard, not a capacity fix** — the journal grew to 2,949 entries /
455 KB because the daemon-side `mcp_server_state` writer has no cap (`daemon/mcp/init_guard.py:150`, 89 % of the
file). That writer-side policy is **routed** to its owner, not fixed here (blast radius).

### D1-6 — `_json_escape` and `_provenance_sidecar_ok` are deliberately NOT migrated — ✅ **ACCEPTED-BY-LEADER 2026-10-29** (architect §3: both stay bash — no re-litigation)

`_json_escape` (`lib.sh:508`-`:524`) runs only on **short** strings; `_provenance_sidecar_ok`
(`lib.sh:2357`-`:2400`) runs only on a **small sidecar** and deliberately keeps the verifier's bash-only,
no-jq/no-python portability discipline (`lib.sh:2334`-`:2337`, ADR-014). Migrating them would grow the blast
radius for **zero** incident value. **Documented, not silently skipped.**

### D1-7 — The no-interpreter fallback — ✅ **ACCEPTED-BY-LEADER 2026-10-29**, with the architect's hard constraint folded in as **D1-8** (the read path is forbidden to route through `_splice_journal`)

If no python3 resolves, `journal_read` falls back to the bash scan — **correct but slow** — and emits a
**distinct warn line** so a slow host is visible. The fallback is never silent (a silent fallback would recreate
the incident as a "mystery slow host"). `status.sh` additionally gains a floor probe (it has none today).

### D1-8 — Placement is FINAL: the new sibling file `scripts/upgrade/journal_read.py`; the read path may NOT route through `_splice_journal` **[fold: A19, R-N6 — RULING 2]**

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29 (RULING 2, option P1).** The P1 row of D1-3 is **binding**:
a dedicated read-only helper, **no lock requirement, no install-fence**; P2 (a read-verb on `journal_splice.py`)
and P3 (fence-gating reads) are **rejected**.

**The hard constraint.** The read path is **forbidden to route through `_splice_journal`**: that path remaps
rc 78 → 1 (`lib.sh:1174`) and merges stderr into stdout (`:1172`), breaking **both** the rc and the stdout
contract. The helper gets its own thin path resolver (mirroring `_splice_journal_path`, `lib.sh:1134`).

**Why P1 over P2 (architect's reasoning).** `journal_splice.py`'s refusal surface is **26 `_die_refused` sites
across 8 token classes**, pinned by the house `test_journal_splice.sh` pack; a read verb routed through its
`main()` must bypass **three** write-protective gates (live-install `:549-560`, missing-journal `:564-568`,
strict-parse `:298-302`) ⇒ every bypass re-opens a mutation-verified pin surface. P1's new file touches none of it.

### D1-9 — The helper's stdout contract is bash command-substitution EMULATION **[fold: A14, R-N1 — RULING 8]**

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29.** Strip **all** trailing `\n`, **drop NUL bytes**, emit the documented
NUL-drop stderr warning class (`warning: command substitution: ignored null byte`). MEASURED: a canonical 74 B
journal echoes as 73 B; `{"a":"x\0y"}` echoes as 10 B; every real journal ends in exactly one `\n`
(`journal_write`'s `printf '%s\n'`, `lib.sh:1007`) ⇒ **a raw-dump helper fails stdout parity on every real journal.**

### D1-10 — The corpus grows 15 → 22; the duplicate-key `journal_update` corruption is pinned as BASE-bug-preserving **[fold: A15, R-N2 — RULING 8]**

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29.** MEASURED: on `{"x":10,"b":2,"x":20}`, BASE `journal_update x 99`
produces `{"x":10,"b":2,"x":99,"b":2,"x":20}` — the head splices at the **LAST** occurrence
(`${rest%*"${field}"*}`, `lib.sh:1039`) while the value scan resumes after the **FIRST** (`:1040-1042`). The port
must **pin these exact corrupt bytes**; an un-pinned parity test would flag the faithful port as defective, or
invite a silent "fix" = a behaviour change on the RMW path. **A real fix is a separate, explicitly-adjudicated
change; not this lane.**

### D1-12 — The helper performs a **single read** of the journal (no torn view) **[fold: Adv4]**

The writer replaces the journal **atomically** (temp + `mv`; `journal_splice.py:_atomic_write`) ⇒ a reader sees
the old or the new file, never a fragment. The helper must **not** read the file twice (two reads can straddle
an atomic replace).

### D1-13 — The size guard's threshold is **stated, derived and overridable** **[fold: W2]**

Default **`JOURNAL_SIZE_GUARD_BYTES`** (env-overridable), derived from the **measured** cost curve (BASE read
≈ 45–61 min at 455,783 B; ≈ 11 s at 25,160 B) ⇒ the default sits an order of magnitude **below** the 455 KB
incident size while staying **above** any healthy journal; the value in force is **named in the row**.

### D1-14 — 🔴 The journal **size guard** refuses on the **PROMOTE PATH ONLY** (leader ruling Q3 — FINAL) **[fold: W2, Q3]**

**Verbatim (leader ruling Q3):** *PROMOTE-PATH-ONLY refusal.* `rollback.sh` / `restart.sh` / `status.sh` get
**WARN + continue**. **Rationale:** the emergency rollback path must never be blocked by a guard added for promote
hygiene — blocking recovery turns degradation into no-recovery. **Add the runbook step.** W2 is **DISCHARGED** by
this ruling once folded. (Implemented as `phase1-plan.md` D1-14/D1-15 · T1.15 · AC16; the threshold default +
env-override name + derivation are in D1-13.)

### D1-15 — 🔴 THE FINAL SIZE-GUARD SEMANTICS — a LEGACY-CLASS REGRESSION BACKSTOP, not a size police (leader directive, round 4 — W1) **[fold: W1]**

**Verbatim (round-4 directive):** *If (a)+(b) conflict under the current guard semantics, RE-SCOPE the guard rather than pick a number — leader-endorsed example: the guard trips ONLY when a LEGACY-CLASS/unbounded walk is invoked on an oversized journal (regression backstop against walk-class regression — e.g. the python helper absent/unusable and the code about to fall back to the bash walk), while healthy O(N) reads of ANY size proceed — belt-and-braces, not a size police on legitimate journals.*

**The final semantics:** name **`JOURNAL_SIZE_GUARD_BYTES`**, default **98304 (96 KiB)**, env-overridable. **Trip condition = TWO CONJUNCTS:** (1) the read is about to execute the **legacy-class per-character bash walk** (the python helper absent/unusable ⇒ the fallback path), **AND** (2) the journal size ≥ the threshold. **On the healthy O(N) python path the guard is NOT CONSULTED** ⇒ reads proceed at **any** size. **Q3 path scope intact:** promote ⇒ **REFUSE loudly**; `rollback.sh` / `restart.sh` / `status.sh` ⇒ **WARN + continue**.

**Why:** (a) the incident configuration (455,783 B **on the legacy walk**) exceeds 98304 ⇒ the legacy path **would have refused**; (b) the live journal is **already 455 KB+** and legitimately growing, but the read path is **O(N)** python ⇒ the guard is never consulted ⇒ **the next ceremony is not blocked** — the two constraints that conflict under a bare threshold are satisfied together. **The number:** the measured legacy-walk curve (11 KB → 2.27 s · 55 KB → 51.6 s · 111 KB → 199 s) crosses the **120 s preflight step budget** at ≈ **87 KB** ⇒ 98304 B (96 KiB) is a round number just above the crossover (below it the legacy walk is inside the step budget ⇒ allowed; at/above it the walk would blow the budget anyway ⇒ refuse loudly rather than trip a vague budget).

**Boundary trio + the protection leg:** G1 = threshold−1 on the legacy class ⇒ **allowed** · G2 = exactly the threshold ⇒ **REFUSE** · G3 = threshold+1 ⇒ **REFUSE** · **G4 = the O(N) path at threshold+1 and at 455,783 B ⇒ NO refusal** (the next-ceremony protection). Implementation: **phase1 D1-17** · T1.18 · AC19 · **phase5 Pack J** (legs J1–J7, mutations M29–M31).

**Supersedes:** decisions D1-13 (the bare threshold) and the round-3 "an order of magnitude below 455 KB" phrasing — that phrasing is **withdrawn** (it contradicted the 100 KB leg and implied a ≈ 45 KB default, which would not have fired on the incident configuration).

### D1-11 — Three distinct algorithms; the `in_str` machine is NOT shared across verbs **[fold: A16, R-N3, R-I — RULING 8]**

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29.** (i) `journal_read` = string-**aware** balance scan; (ii) `_json_sub` =
string-**naive** first-occurrence bracket counter (quadratic **twice**: `out="$out$char"` **and**
`rest="${rest:1}"`, `lib.sh:589-618`); (iii) `journal_update` = string-**naive** first-match value consumer with a
**2-character** escape skip (`${rest:2}`, `:1050` — divergent from a byte port's 2 bytes only on
invalid-UTF-8+escape, which corpus case 22 covers). Sharing the `in_str` machine across verbs **changes the
extraction set** ⇒ forbidden.

---

## D2 — The watchdog shape — ✅ **ACCEPTED-BY-LEADER 2026-10-29 (architect RULING 1)**, with the mandatory amendments folded as **D2-4…D2-8** and the budgets corrected by **D2-9/D2-10/D2-15** (see `phase2-plan.md`)

The commission explicitly reserves the final shape for the architect. Full option matrix with trade-offs:
`phase2-plan.md` §2. Summary:

| Layer | What it is | Fires inside a phase? | Verdict |
|-------|-----------|----------------------|---------|
| **L1** | `_enforce_deadline` checkpoints between preflight steps (`promote.sh:218` → `:373` gap) | no | include (belt) |
| **L2a** | per-step `_run_bounded` wrappers + a breach handler (journal row + `_warn` + `lock_release` + exit 78) | **yes** | **primary (recommended)** |
| **L2b** | a phase-level background watchdog subshell that TERMs the main shell | yes | fallback only — no step needs it after L2a |
| **L3** | `RuntimeMaxSec` on the transient unit (`daemon/tools/upgrade_journal.py:2468` extension point) | yes (outermost) | **backstop only**, budget **`<derived per D2-9>`** (1320 s at the defaults; > the 900 s ceremony deadline) **[fold: B2]** |
| **L4** | daemon-side escalation of `executor_still_running` (660 s, no-kill) | n/a | **route to its own commission** — see D4b |

### D2-1 — Recommendation — ✅ **ACCEPTED-BY-LEADER 2026-10-29 (RULING 1)**: L2a (primary) + L1 (belt) + L3 (derived backstop) + L4 (routed). L2b correctly dropped.

**L2a + L1 + L3(backstop) + L4(routed).**

The resulting escalation ladder (each rung louder than the one above):

```
≤ per-step budget  →  shell journal row + stderr + lock released + exit 78     (L2a)
step boundaries     →  shell journal row + lock released + exit 78              (L1)
660 s               →  daemon informational row (now with the per-run log path) (L4, routed)
900 s               →  the ceremony deadline breach row                        (existing)
**<derived per D2-9>**  →  systemd RuntimeMaxSec → TERM → the shell's halt row  (L3)   **[fold: B2]**
```

**The key property:** the shell's loud failure (≤ 300 s in the preflight) fires **well before** the daemon's
silent observation (660 s) ⇒ the incident class cannot recur with a silent outcome.

### D2-2 — Budgets (each justified in `phase2-plan.md` §3.2) — ⚠ **ACCEPTED WITH CORRECTIONS 2026-10-29 (RULING 6)**: the 120 s justification is re-derived (D2-15), `STAGE_TREE_BUDGET_S=300` is **superseded** (D2-10 / A7), and `RuntimeMaxSec=1200` is **superseded** by the derived 1320 s (D2-9 / A6)

| Budget | Default | Anchor for the number |
|--------|---------|------------------------|
| `PREFLIGHT_STEP_BUDGET_S` | 120 s | ≈ 1.8× the **whole** healthy preflight (68 s measured, `evidence/CODE-PATHS.md` §12) |
| `PREFLIGHT_PHASE_BUDGET_S` | 300 s | ≈ 4.4× the healthy preflight; **< 660 s** so the shell beats the daemon's silent observation |
| `STAGE_TREE_BUDGET_S` | 300 s | the stage tree walks are the longest legitimate stage phase |
| `RuntimeMaxSec` | **`<derived per D2-9>`** (1320 s at the defaults) | **> `PROMOTE_TOTAL_DEADLINE_S` (900)** ⇒ systemd never fires before the shell's own machinery **[fold: B2]** |

**The soak (300 s, `lib.sh:131`) is excluded by construction** — a watchdog that covered it would kill a
legitimate soak (R1). The exclusion is pinned by a negative control.

### D2-3 — The rejected alternative: "just raise the 660 s threshold / kill sooner" — ✅ **ACCEPTED-BY-LEADER 2026-10-29** (RULING 3: the escalation is ROUTED; the threshold is never tuned)

Rejected. The 660 s figure is derived from the **healthy promote minimum** (490 s:
`daemon/services/upgrade_journal_sweep.py:98`-`:102`), not from a stall analysis; a timer is not a predicate.
The escalated, predicate-based version is D4b's routed commission.

### D2-4 — 🔴 The cgroup-wide-kill INVARIANT (verbatim, RULING 5) **[fold: A10, R13]**

> *any mechanism that kills a stuck executor — L3, the future L4 reaper, an operator drill — MUST deliver the
> signal to the whole cgroup/process group. A PID-targeted kill is trap-bypassing: no halt row, no lock release,
> and the orphan is only recoverable via the dead-owner stale-break.*

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29 (RULING 5).** MEASURED: a PID-only TERM leaves bash blocked in a
command substitution with the TERM trap **deferred** (zero trap output, the shell alive) ⇒ *a PID-targeted kill
would have reproduced the incident silently*; a cgroup-wide TERM fires the trap (the 2026-10-28 operator TERM:
journald `Stopping` 17:42:44.415964 → `halt` row + lock release → `status=143` at .590524, **175 ms**). Phase 0
pins the effective `KillMode`/`KillSignal`/`TimeoutStopUSec` on a sandbox transient unit; a non-default
`KillMode` ⇒ the unit sets `KillMode=control-group` explicitly.

### D2-5 — Breach semantics: single `halt` row, load-bearing order, unlocked-path split, trap-install order **[fold: A1, A2, A3, A4 — RULING 5]**

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29 (RULING 5).** (a) `_HALT_JOURNALED` re-entrancy guard (a 2nd TERM
during the handler would write a **duplicate** `halt` row — `_LOCK_RELEASED` protects the lock, **not** the
journal); (b) the breach order is **journal → warn → `lock_release` → exit 78** (release-first loses the row to
the live-install splice fence); (c) **unlocked paths** (`rollback.sh:90` / `restart.sh:133` / `status.sh:52`)
degrade to **warn + per-run log, NO journal row**, and `status.sh` exits **1** (never 78, never a journal
mutation); (d) the signal handlers are installed **before** `lock_acquire` (the window where the lock is held and
no handler is installed orphans the lock with no row — the D4a shape, reproduced inside our own fix).

### D2-6 — `RuntimeMaxSec` is DERIVED and KIND-SCOPED **[fold: A5, A6 — RULING 6c]**

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29.** `RuntimeMaxSec = max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)`
= **1320** with the defaults, computed at arm time; **only** for `kind == "promote"` (`spawn_executor` serves
both promote and restart — `daemon/manager.py:5544-5555`). Additionally: **refuse `ENSEMBLE_PROMOTE_SOAK_S > 300`
when `ENSEMBLE_UPGRADE_LIVE=1`** (the knob stays available for sandbox drills). A constant 1200 would fire
**mid-soak, after the symlink flip** (R17).

### D2-7 — The stage-tree budget is DERIVED, and the cause (fork-per-file hashing) is fixed **[fold: A7 — RULING 6b]**

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29.** MEASURED: the plugins tree walk is **329.6 s** per pass and stage
walks it **twice** (`stage.sh:443` + `:576`) ⇒ ≈660 s ⇒ the plan's 300 s budget would **false-fire on a healthy
stage**. Fix the **constant, not the budget**: batch the hashing (one `shasum -a 256` per tree) with a
**byte-parity pin**, then derive the budget from the Phase-0 re-measured plugins walk.

### D2-9 — The breach-row shape is **normalized**; BOTH shapes are pinned **[fold: W3]**

`_enforce_deadline` journals **`daemon-state=`** (`lib.sh:277`-`:281`) — the field the runbook's first-recovery-step
reads. The new `_budget_breach` **must** emit the same field ⇒ one row shape for both producers ⇒ one reader,
one runbook. **Both** shapes are pinned (the L2a `deadline-breach-<step>` shape and the L1 `deadline-breach-<phase>`
shape).

### D2-10 — `_run_bounded`: the consumer census is **7**; a **NEW variant**, not a modification; `setsid` is not portable **[fold: W6]**

The primitive has **7** consumers: `lib.sh:3844` / `:3874` (`bash "$stop_script"` — **side-effecting**) and
`:4066` / `:4088` / `:4111` / `:4367` / `:4403` (systemctl probes/starts). **Decision: the shared primitive stays
byte-unchanged** and the lane adds a separate **`_run_bounded_pg`** (process-group kill) used **only** for
side-effecting steps ⇒ **zero blast radius on the 7 existing consumers**, which are **re-verified**. `setsid` is a
**util-linux** dependency (not POSIX) ⇒ **fallback**: with no `setsid`, the pg variant is unused; the step falls
back to the plain child-kill **plus** the "side-effect-free-on-kill" requirement, or is **not armed**.

### D2-8 — Row vocabulary, exit code and the two breach classes **[fold: A9, R22 — RULING 4]**

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29.** Kind **`halt`** (never a new kind — a new kind would dangle the
`pending_op` unless added to all three registries), detail
`deadline-breach-<step> (budget=… value=… elapsed=… phase=…)`, exit **78** (78 is in `_LAUNCHER_CLEAN_EXITS`,
`daemon/tools/upgrade_tools.py:1823`). Two breach classes, documented as **distinct**: (i) shell watchdog breach =
exit **78** + `halt` row **with** the `deadline-breach-` prefix; (ii) L3 systemd kill = exit **143** + `halt` row
**without** it. Runbook step: `systemctl show ensemble-upgrade-<run_id> -p RuntimeUSec -p RuntimeMaxUSec`.
**No operator surface may read exit-78 alone** — 78 already spans two semantics today.

---

## D4a — The `stage.sh` lock leak: framing and fix

**Status: ✅ ACCEPTED-BY-ARCHITECT 2026-10-29** (endorsed; the architect's RULING 5 names D4a as the owner of the SIGKILL-class orphan case), with the **mechanism to be confirmed by reproduction** (Phase 0 T0.4 / Phase 5 Pack F).

### D4a-1 — The framing correction

The forensic lane recorded the defect as *"stage.sh leaks its lock dir on completion"* (`evidence/FORENSICS.md` §7
R4). Verified at TIP, that framing is **too broad and partly wrong**:

* `stage.sh:375` installs the signal handlers, which swap the EXIT trap to `_trap_safe_exit`
  (`lib.sh:2034`-`:2039`) ⇒ `lock_release` ⇒ **a normal, successful stage DOES release the lock**
  (`stage.sh:744` exits 0).
* The two 09:5x attempts produced `halt … at phase stage:lock` rows ⇒ their TERM trap fired ⇒ they released.
* The surviving orphan (owner `2182592`, heartbeat `2026-10-28T10:04:46Z`) has **no** `halt` row ⇒ its trap
  **never fired** ⇒ it died in a **trap-bypassing** (SIGKILL-class) way.

**Corrected statement:** *the lock's release is trap-only; a trap-bypassing death orphans the lock dir, and the
automatic reclaim is **not journaled** — so the next operator cannot tell from the durable audit trail why the
lock was breakable, and (as on 2026-10-28) performs a **manual** stale-break believing it necessary — when the
automatic reclaim would in fact have applied (`lib.sh:1689`-`:1693`: a dead owner breaks the lock even with a
fresh heartbeat).*

This correction matters: it changes the fix from "add a missing release" (unnecessary) to
"**journal the reclaim** + **belt the success path** + **reproduce the leak**" (necessary).

### D4a-2 — The fix

1. **Journal every reclaim** — a `lock-stale-break` row naming the reclaimed owner pid, the reclaimed run_id,
   the heartbeat age, the reason (`stale-heartbeat` / `dead-owner`) and the reclaiming run_id
   (`lib.sh:1670`-`:1694`; today the reclaim is a stdout `_log` line only — `lib.sh:1679`, `:1691`).
2. **Belt the success path** — an explicit `lock_release` before `stage.sh`'s `exit 0` (idempotent; the
   `_LOCK_RELEASED` guard prevents a double release).
3. **Reproduce the leak** (SIGKILL a lock holder in the sandbox) so the fix is evidence-backed.

### D4a-3 — Explicitly not done

The stale-break **policy** (thresholds, the "never break a live owner" invariant at `lib.sh:1683`-`:1684`) is
**unchanged** — it is the house's crash-recovery contract and is sound.

### D4a-4 — Routed

**Who SIGKILLed the stage run?** (Most likely an outer harness timeout: SIGTERM → grace → SIGKILL. The FORENSICS
meta-observation records a lane's own `bash` call wedging for 29 minutes during the investigation — the same
failure class reproducing inside the investigation tooling.) ⇒ **routed to the pipeline-hygiene lane**, recorded in
the lane report, not silently dropped.

---

## D4b — The `executor_still_running` "benign-detach (no kill)" disposition

**Status: ✅ CONFIRMED-BY-LEADER 2026-10-29 — architect RULING 3 / leader ruling 3: the benign-detach kill-contract stays ROUTED to its own commission.** The routed commission's **three mandatory scope items** are recorded in **D4b-4** (fold: A35).

### The observation (verified)

`daemon/services/upgrade_journal_sweep.py:2281`-`:2291` writes, at 660 s:
*"still running after 660s — benign-detach (no kill; own process group; correlate with data/upgrade.log)"*.
On 2026-10-28 this fired at **17:16:11** — 660 s into a stall that ran to **17:42:44**.

### D4b-1 — Recommendation: **do not change the kill contract in this lane** — ✅ **CONFIRMED-BY-LEADER 2026-10-29 (RULING 3)**

| # | Argument |
|---|-----------|
| 1 | The benign-detach is **load-bearing**: the executor is *designed* to survive daemon death (its own transient unit + cgroup — `daemon/tools/upgrade_journal.py:2664`-`:2677`). A timer-based daemon kill would reintroduce exactly the class the v0.18.0 service lane was built to eliminate (`daemon/tools/upgrade_journal.py:2185`-`:2192`). |
| 2 | 660 s is a **healthy-path** number (490 s observed minimum + headroom), not a stall number. Killing at 660 s would kill a legitimate slow promote. |
| 3 | A correct escalation needs a **"stalled" predicate** (no journal progress AND no heartbeat advance for N s). The daemon's liveness predicate (`_executor_alive_recent`, `:2008`) keys on the **txn** heartbeat, which exists only **after** `journal_open_txn` ⇒ it **cannot see a pre-txn stall** — precisely the 2026-10-28 case. Building that pre-txn signal is a daemon-side design task. |
| 4 | **Blast radius**: changing the reaper contract inside a shell-script lane would pull the daemon's reaper, its liveness predicate and its journal vocabulary into the diff — a different commission with a different gate profile. |
| 5 | **The need is already met here**: D1 removes the hang; D2 makes any stall fail loudly from the shell side long before 660 s; D3 makes it correlatable. The daemon's row becomes a secondary observation of something already reported. |

### D4b-2 — What this lane DOES change (the cheap, safe half)

* The `executor_still_running` row gains the **per-run log path**, the **last known phase** and the
  **elapsed-since-last-journal-activity** ⇒ the operator can see *where* the run stalled instead of being handed
  a single shared file to grep.
* A **board item** is opened for the daemon-side escalation, recording the rejected alternatives
  (blind timer kill / keep the no-op) and the reason.
* **Considered and rejected in-lane:** killing the unit when the shell has already journalled a
  `deadline-breach-*` row — **unnecessary**, because the shell's breach handler exits by itself
  (`exit 78` + lock release) and the unit terminates on its own.

### D4b-3 — If the leader rules "fix now" — ✅ **RESOLVED 2026-10-29: the leader CONFIRMED the routing** (RULING 3); the text below is retained as the contingency record

Then the lane's blast radius grows from "shell + one daemon log-path change" to "daemon reaper contract change".
Minimum extra work: design a **pre-txn liveness signal** (the shell must publish a "preflight started" marker
that the daemon can see — the per-run log path + the D3 phase markers provide the raw material), define the
**stalled predicate**, add a kill path with its own journal vocabulary, and re-review Phase 2's
negative-control design (a daemon kill is coarser than a shell budget). **The plan does not assume this ruling.**

---

### D4b-4 — The routed commission's **MANDATORY scope** (three items) **[fold: A35, RULING 3]**

The routed board item must carry these three items **verbatim** — they are the architect's + leader's scope
boundary, not suggestions:

1. **A pre-txn stall signal** — a *"preflight started"* marker published **before** `journal_open_txn`. D3's
   per-run log + phase markers are the **raw material**, but the marker must exist **before the txn**, not after
   (`journal_open_txn` is `promote.sh:327`; the daemon-side liveness predicate `_executor_alive_recent`,
   `daemon/services/upgrade_journal_sweep.py:2008`, keys on the **txn** heartbeat and is therefore blind to a
   pre-txn stall — the exact 2026-10-28 shape).
2. **The "stalled" predicate** — *no journal progress AND no heartbeat advance for N seconds* — **never a bare
   timer**. The 660 s threshold is a healthy-path number (490 s observed minimum + headroom,
   `daemon/services/upgrade_journal_sweep.py:98`-`:102`), not a stall number; a timer-kill would kill a
   legitimate slow promote.
3. **The writer-side journal-growth policy** — 89 % of the 455 KB journal is `mcp_server_state`
   (`daemon/mcp/init_guard.py:150`). D1's size guard only converts the cliff into a **loud refusal**; the
   **growth itself** belongs to the writer's owner (the same owner as the init-guard journal policy).

**Not in the routed scope:** the kill contract stays as it is in this lane (RULING 3). The routed commission may
*propose* a change; it does not inherit one from here. Implementation side: `phase4-plan.md` §3.2b + T4.7 + AC7.

## D5 — Other contested choices (recorded for completeness) — ✅ **ACCEPTED-BY-LEADER 2026-10-29** (architect §3: the strict-JSON read, the journal-format split and the two bash-only verifier sites all stay as decided — no re-litigation)

| # | Choice | Decision | Rationale | Rejected |
|---|--------|----------|-----------|----------|
| **D5-1** | Should D1 also **rewrite** `_json_escape` (`:508`-`:524`) and `_provenance_sidecar_ok` (`:2357`-`:2400`)? | **No** — document only. | Both run on short/small inputs; the second deliberately keeps the verifier's bash-only portability discipline (ADR-014, `lib.sh:2334`-`:2337`). | Migrating them (blast radius for zero incident value). |
| **D5-2** | Should the **stage-path checksum walk** be rewritten in python? | **No** — bound it (heartbeat + budget + before/after log lines). | `stage.sh:448`-`:462` documents a deliberate linear-streaming design with a byte-compat argument; the walks are external and already O(N); what was missing is **observability and a bound**. | Rewriting a settled defect. |
| **D5-3** | Should D1 **cap the journal's history** (entries/bytes) in the daemon writer? | **No** — routed to the MCP/init-guard owner; D1 adds a shell-side size guard as the interim. | The writer is daemon-side with its own semantics and owner; capping it here duplicates ownership and would drop audit history silently. | Capping here (silent history loss). |
| **D5-4** | Should the **watchdog** live in the shell or in systemd? | **Both, layered** — shell primary (informative, journalled, lock-releasing), systemd backstop (outermost, blunt). | The shell knows the step; systemd knows the wall. Neither alone is sufficient. | Shell only (no outer bound — the shell can wedge in ways it cannot report); systemd only (a blunt wall-clock kill with no step attribution, and it cannot release the lock cleanly unless the TERM trap runs). |
| **D5-5** | Should the **version slot** be reserved now? | **No** — derived by live census at stage time. | The sibling event-loop sync-DB lane (`wi-3f5bea95`) may stage first; the house has no reservation mechanism and the census is authoritative. | Pre-claiming a version (stale by stage time). |
| **D5-6** | Should the lane **fix** the benign-detach by raising/lowering 660 s? | **No** — routed (D4b). | 660 s is derived from the healthy minimum; changing it without a predicate risks killing legitimate promotes. | Threshold tuning (a guess dressed as a fix). |
| **D5-7** | Should the fix **change the journal format** (e.g. split history into a sidecar file so the main journal stays small)? | **No** — out of scope for this lane. | It would be the *ideal* structural fix (the bloat is 89 % history), but it changes the on-disk contract that the launcher sweep, the daemon and the python twin all read — a migration-shaped change, which this lane explicitly excludes (no migrations). | Splitting the journal (would make this a schema-contract lane). **Recorded as the follow-up candidate for the MCP/init-guard + journal owners.** |

---

## D3 — The logging architecture (D3) **[fold: A20–A25 — RULING 7]**

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29 (RULING 7)** — endorsed *with* a retention rule, an atomicity
contract, a sink split and in-op markers. Plan: `phase3-plan.md` §2 (D3-1…D3-16) and §6 (fold log).
The plan's original single-file sink (D3-1) and pointer semantics (D3-3) are **superseded** by the two-file sink
below; the remaining D3 decisions (mirror stdout, timestamps, write-failure reporting, the client PIPE, the
benign EPIPE, step-boundary markers) are **unchanged**.

### D3-1 — TWO files per run (narrative + payload) **[fold: A22, R-C]**

`<install>/data/upgrade/<env>/<run_id>.log` = the shell's timestamped, strictly-ordered narrative (**direct
writes**); `<run_id>.payload.log` = systemd's `StandardOutput/StandardError=append:` target for the payload's raw
stdout/stderr. MEASURED: a child's stdout to a regular file is libc **block-buffered** ⇒ the `append:` capture is
durable but **not real-time** and its bytes can land *after* later markers — in a single file that is an
**ordering illusion** that would mislead exactly the post-mortem D3 exists for. The pointer in `data/upgrade.log`
names **both** files, resolved **by `run_id` from the journal** (never "last pointer line").

### D3-2 — Retention: keep-newest 20 + age cap 14 d, with five fences **[fold: A20, R-D]**

Owner = **the shell at ceremony end, under the lock** (mirroring `retention_evict`'s seat, `promote.sh:722`).
Portable primitives only — **never** `stat -f '%m'` (MEASURED: on GNU it prints filesystem info, not mtime,
rc=0 silent garbage — the exact input `lib.sh:5567` feeds it today **[fold: W4 — anchor corrected, re-verified at TIP]**) and **never** `_iso_to_epoch` + ISO sorting.
Fences: never delete the current run's file, the `in_flight.run_id` file, the last pointer's file, **any file
modified < 6 h** (protects a *stalled* live run — the incident class), or anything while the lock is held by a
live owner.

### D3-3 — Atomicity contract: 4 KiB line cap, elision marker, newline sanitization, NO per-line fsync **[fold: A21, R-B]**

MEASURED: lines **< 8192 B are atomic** against a concurrent writer; **≥ 8192 B corrupt** (the break is stdio
`BUFSIZ`, not `PIPE_BUF`). Hence a hard **4 KiB** cap (2× margin), a `payload elided, N bytes` marker for longer
payloads, and **embedded-newline sanitization** (the livez/readyz JSON dumps are unbounded — today 1,258 B/73 B,
latent). Per-line fsync is **rejected** (page-cache durability covers parent/process death — the stated
objective).

### D3-4 — In-op markers every 500 files; NO background heartbeat subshell **[fold: A23]**

The checksum walks remain minutes-scale (plugins ≈ 330 s/pass) and are exactly where a D2 budget trip needs
attribution. A background heartbeat subshell is rejected: an extra writer with orphan risk that may write after
the run ends.

### D3-5 — Change surface: the sweep's `_log_tail` must read the per-run path via `run_id` **[fold: A24, R-E, R-H]**

`daemon/services/upgrade_journal_sweep.py:2224-2241` reads the **aggregate** — after D3-1 the aggregate is
pointer-only ⇒ the `executor_exit` row would lose the run's tail at exactly the wrong moment. Also: the
env-scoped parent needs its own `mkdir` (`daemon/tools/upgrade_journal.py:2725-2726`); **reuse `_log_tsl`**
(`lib.sh:349-353` — portable, failure-signalling, already test-exercised) rather than a second writer; and fix
the inverted stream description at `lib.sh:358-360` in the same hunk.

### D3-6 — Env scoping: `data/upgrade/<env>/` **[fold: A22, A24, R-F]**

A **sandbox** ceremony resolves the **live install root** via the frozen-binary path derivation
(`daemon/tools/upgrade_tools.py:687-695`) and must not pollute the live tree's audit/retention domain. The DB
name + port in the per-run file are **status quo** (today's aggregate already carries them).

### D3-7 — T3.10 additions: no cross-env mixing, no secrets in pointer rows, the ≥ 8 KiB corruption pin **[fold: A25]**

Each is **pinned**, not asserted in prose: a sandbox ceremony writes nothing into the live env's tree; a
grep-assertion finds no secret-shaped string in the pointer/provenance rows; a planted ≥ 8 KiB line turns the
pack **RED** (a corrupt line must be *detected*, not absorbed).

---

## D6 — Ceremony readiness **[fold: A26–A31 — RULING 9]**

**Status: ✅ ACCEPTED-BY-LEADER 2026-10-29 (RULING 9).** Plan: `phase6-plan.md` (NEW phase).

### D6-1 — The deployment-truth chain is a hard precondition of the lane's efficacy **[fold: A26]**

The scripts dir is resolved **once, at arm time, in the daemon** (`daemon/tools/upgrade_tools.py:1953-1966`,
frozen into the arm marker at `:3714`); the path travels in **argv** (`daemon/manager.py:5494-5496`), not in the
executor env (stripped by `EXECUTOR_ENV_ALLOWLIST`); release payloads ship **no `scripts/`** since v0.27.4
⇒ a checkout is the only scripts source on this host; the live `.env:107` pins a checkout **277 commits behind**
whose `lib.sh` **still contains the O(N²) scan**. ⇒ **the next ceremony runs the OLD code** unless the checkout
is synced or the pin is moved.

### D6-2 — The sync precondition is USER/leader-owned; this lane MUST NOT touch the shared checkout or the live `.env` **[fold: A27 — leader ratification]**

**Either** sync `/home/nea/ensemble-src/scripts/upgrade/` to a tree that **contains this lane's fix commit** —
the orderable predicate is **git ancestry**, not a `>=` comparison (git SHAs are unordered; the earlier "≥"
predicate was the B1 defect): `git -C <dir> merge-base --is-ancestor <floor-commit> HEAD` ⇒ the synced tree must
be a **descendant** of the floor commit — **or** move the pin. (Scoped to `scripts/`; the checkout was dirty in
**15 unrelated files** at the 2026-10-29 measurement — **time-dependent**: a later count showed **14 modified + 49
untracked**; the load-bearing predicate is only that **`scripts/` is clean**, which holds — that the **WIP owner** reconciles — the cross-lane WIP-owner precedent from the v0.27.1
lane.) **This lane is STAGE-ONLY and will not** touch the shared
checkout or the live `.env` pin. If unmet, the daemon-side guard refuses the arm ⇒ **the ceremony is not
consumed and the failure is loud instead of a silent hang** — the correct failure mode.

### D6-3 — Daemon-seat provenance at arm time (journal-always) **[fold: A28]**

The arm row records `scripts_dir`, the `lib.sh` **sha256**, and the git rev + dirty flag (when the dir is a git
checkout). Pure observability, **zero refusal risk**. The seat is the daemon because **the daemon binary is the
one component whose version the promote itself guarantees**.

### D6-4 — `PIPELINE_SCRIPTS_REV` stamp, acquired at ARM time from the FILE, + the floor check **before the nonce burns** **[fold: A29, R-G; B3 — council round 3]**

`lib.sh` carries `PIPELINE_SCRIPTS_REV=<this lane's merge commit>`. **Acquisition (B3):** the **daemon** opens
`<scripts_dir>/lib.sh` at **arm time** and parses `PIPELINE_SCRIPTS_REV=` from a **bounded first-N-KB read** —
**before `consume_pending_action`**; a parse failure ⇒ *"stamp absent"* ⇒ refuse on an explicitly pinned dir.
**The executor-start echo is demoted to a corroborating signal** (it proves *running code == stamped code* — a
different property — and it arrives **after** the nonce burn, so it cannot gate anything).

At **arm time, before `consume_pending_action`**, the daemon compares against the floor (**D6-10**): stamp
absent-or-not-containing-the-floor **on an explicitly pinned scripts dir** ⇒ **refuse to arm** (a refusal row
naming the sync command); a dirty checkout ⇒ journal **WARNING**; a fallback-dir resolution without a stamp ⇒
**WARNING only** (backward compat); escape hatch `ENSEMBLE_ALLOW_STALE_SCRIPTS=1`, journaled loudly and
**per-invocation only** (Adv2). **The guard must fire before the nonce burn, or it consumes the ceremony it
protects.**

### D6-5 — `ENSEMBLE_PROMOTE_WATCHDOG=1` token + mixed-generation refusal **[fold: A30]**

The executor env carries the token (allowlist addition); `promote.sh` **refuses** (a loud `refusal` row) when the
token is present but the watchdog is not armed — catching a new binary + half-synced scripts. Old scripts ignore
the token; the floor check (D6-4) catches those.

### D6-6 — The guard's seat is the DAEMON, not the scripts **[RULING 9 seat correction]**

A stale `lib.sh` has **no stamp and no check-code** ⇒ *a scripts-side guard structurally cannot catch a stale
scripts dir*. The checking code must live in the component the promote itself versions — the binary.

### D6-7 — Runbook checklist item **[fold: A31]**

*"ceremony scripts provenance verified — the arm row shows the expected stamp."*

### D6-8 — Stamp cadence — **LEADER-RATIFIED 2026-10-29**

`PIPELINE_SCRIPTS_REV` **bumps per release touching ceremony-relevant `scripts/upgrade` semantics**. The stamp is
therefore a **release-scoped identity**, not a per-commit one: a release that does not touch the ceremony
semantics keeps the previous stamp, and the daemon's **`PIPELINE_SCRIPTS_FLOOR_COMMIT`** floor moves only when the **[fold: W3 — the pre-B1 name `MIN_PIPELINE_SCRIPTS_REV` is superseded and must not be minted]**
ceremony-relevant surface changes. (RATIFIED as the architect's default — leader ratification, 2026-10-29.)

### D6-10 — 🔴 The floor mechanism: **HYBRID, primary = GIT ANCESTRY** (leader ruling Q1 — FINAL) **[fold: B1]**

**Verbatim (leader ruling Q1):** *HYBRID, primary = GIT ANCESTRY.* The daemon records
`PIPELINE_SCRIPTS_FLOOR_COMMIT` = the commit introducing the preflight-hang fix (**owner**: this lane's release
process; **storage**: daemon constants + release manifest; **cadence**: bumps per release touching
ceremony-relevant `scripts/upgrade` semantics — **SAME cadence as the ratified `PIPELINE_SCRIPTS_REV` stamp, they
travel together**). At arm time, for a **git** pinned dir: read-only bounded
`git -C <dir> merge-base --is-ancestor <floor> HEAD` — **pass iff ancestor**; **a missing/unknown floor object or
command error = REFUSE** (a tree that does not contain the fix commit object cannot contain the fix;
cherry-picked-content edge = acceptable rare false refusal, remediation is a proper fetch/merge). Fallback for
**NON-git** pinned dirs = stamp file when present (`PIPELINE_SCRIPTS_REV`), else **WARN-only per Q2**.
**Rationale:** ancestry gives no false refusals from newer unrelated trees (any tree containing the fix passes,
however new) and proves staleness exactly; a bare sha256 allow-list cannot prove staleness of an unknown sha.

**Explicit statement (Q1 asks for it):** ancestry **IS** practical at the exact arm call site — the daemon
already runs in-process there, the command is **read-only and bounded**, and it writes nothing ⇒ **the
content-sha allow-list fallback is NOT taken.**

### D6-11 — A **NON-git** pinned dir is **WARNING-ONLY** (leader ruling Q2 — FINAL) **[fold: B1, Q2]**

**Verbatim (leader ruling Q2):** *WARNING-ONLY* (council recommendation confirmed). WARN + journal row carrying
the pinned lib.sh sha256 as provenance; **never refuse on the non-git branch.** **Rationale:** every known
deployment pins a git dev checkout; refusing an operator-chosen non-git dir could block an otherwise-correct
promote, and the sha256 journal row preserves the audit trail.

### D6-9 — Rollout / efficacy (conditional, and stated as such) **[fold: A31, A34 SC12]**

*With* the sync: the ceremony runs the fixed scripts — D1's read is O(N), the watchdog is armed, the per-run log
is durable. *Without* the sync: the ceremony **refuses at arm time** (SC12) and **no ceremony is consumed**.
The staged-verification lane therefore asserts **both** that the guard **exists** and that it **fires on a stale
fixture**.

---

## Fold log (architect → decisions.md), 2026-10-29

| A# | Subject | Status | Entry |
|----|---------|--------|-------|
| A1, A2, A3, A4 | Breach semantics (re-entrancy, order, unlocked split, trap order) | folded | D2-5 |
| A5, A6 | RuntimeMaxSec kind-scoped + derived | folded | D2-6 |
| A7 | Stage-tree budget derived; batching | folded | D2-7 |
| A9 | Row vocabulary + exit 78 + two breach classes | folded | D2-8 |
| A10 | The cgroup-wide-kill INVARIANT | folded | D2-4 |
| A14 | The stdout contract (command-substitution emulation) | folded | D1-9 |
| A15 | Corpus 15→22 + duplicate-key BASE-bug-preserving | folded | D1-10 |
| A16 | Three algorithms; no shared `in_str` | folded | D1-11 |
| A19 | Placement = the new sibling file; forbidden `_splice_journal` routing | folded | D1-8 |
| A20 | Retention rule | folded | D3-2 |
| A21 | Atomicity contract | folded | D3-3 |
| A22 | Two-file sink + pointer-by-run_id; env scoping | folded | D3-1, D3-6 |
| A23 | In-op markers; no background heartbeat | folded | D3-4 |
| A24 | Change surface (sweep per-run tail; `_log_tsl`; the doc inversion) | folded | D3-5 |
| A25 | T3.10 additions | folded | D3-7 |
| A26 | The deployment-truth chain | folded | D6-1 |
| A27 | The user-owned sync precondition | folded | D6-2 |
| A28 | Daemon-seat provenance | folded | D6-3 |
| A29 | The stamp + floor check before the nonce burn | folded | D6-4 |
| A30 | The watchdog token | folded | D6-5 |
| A31 | The runbook checklist item + the rollout/efficacy statement | folded | D6-7, D6-9 |
| RULING 1, 2, 3, 4, 5, 6, 7, 8, 9 | The ten rulings | folded | D1-8, D2-4…D2-8, D3-1…D3-7, D6-1…D6-9 |
| Leader ratifications | Stamp cadence; dev-checkout sync ownership | folded | D6-8, D6-2 |

**Superseded:** D1-3's "P2 acceptable alternative" → superseded by D1-8 (placement is FINAL: the new file).
D1-4's strict-read option → **declined** (RULING 8 + architect §3). D2-2's `RuntimeMaxSec = 1200 s` and
`STAGE_TREE_BUDGET_S = 300 s` → superseded by D2-6 / D2-7. D2-3's contingency → resolved by RULING 3.
D4b-3's contingency → resolved by RULING 3. D3-1 / D3-3 (in `phase3-plan.md`) → superseded by the two-file sink.

---

## Council fold log (plan council round 3 → decisions.md), 2026-10-29

Source: `review-plan-council.md` (READ-ONLY). Verdict: APPROVE-WITH-ADVISORIES, **conditional** on this fold pass.

| Finding | Status | Entry |
|---------|--------|-------|
| **B1** | folded | D6-10 (Q1 hybrid floor, verbatim) · D6-11 (Q2 warning-only, verbatim) · D6-2 (the "≥" predicate → ancestry) · D6-4 |
| **B2** | folded | the L3 row · the escalation-ladder row · the D2-2 budget table row (all → `<derived per D2-9>`); the grep-assertion is a pin (`phase5-plan.md` T5.17/AC17) |
| **B3** | folded | D6-4 (arm-time acquisition from the file; the executor echo demoted to corroborating) |
| **W1** | folded | `phase2-plan.md` §3.3a/§3.3b + D2-10 (the `retention_evict` MUST-NOT-WRAP); `plan-overview.md` R25 |
| **W2** | folded — **DISCHARGED by Q3** | D1-12 (verbatim Q3) · D1-13 (threshold) |
| **W3** | folded | D2-9 |
| **W4** | folded | the counts/literals sweep (corpus **22 + G1–G5 = 27**, matrix **M1–M28**, packs **A–I**, no `RuntimeMaxSec=1200` pin, no single-file-era wording, the `lib.sh:5567` anchor fix) — ⚠ **SUPERSESSION RECORD**: the literals `M1–M28` / `A–I` in this cell are the **pre-round-4/5** values; the **canonical** values are **`M1–M32`** / **`A–J`** (M29–M32 = the Pack J + token-refusal legs). Exempt from the gate's class (b)/(c) by the exemption stated here. **[fold: N-1 / approver-002 blocker 2]** + the **count-sweep gate** (`phase5-plan.md` T5.17/AC17) + the cross-file ID index (`plan-overview.md` §11.1) |
| **W5** | folded | `phase6-plan.md` T6.13/AC13 · `plan-overview.md` SC14 |
| **W6** | folded | D2-10 |
| **Adv1–Adv7** | folded | Adv1 → `phase5-plan.md` Pack I (M25 — the **stamp-acquisition** leg; the token-refusal leg is M32) · Adv2 → `decisions.md` D6-4 + `phase6-plan.md` T6.12/AC12 · Adv3 → `phase1-plan.md` case 15 · Adv4 → `decisions.md` D1-14 + `phase1-plan.md` T1.16/AC17 · Adv5 → `phase2-plan.md` T2.12/AC12 · Adv6 → `plan-overview.md` §8.6 · Adv7 → `phase1-plan.md` T1.17/AC18 |
| **Q1 / Q2 / Q3** | folded — **verbatim** | D6-10 · D6-11 · D1-12 (the verbatim texts are also in `plan-overview.md` §13) |

---

## Cross-file `D1-*` id map (W5 — the collision is now closed)

The `D1-*` sequence exists in **two** files (`phase1-plan.md` and `decisions.md`) and the two series collided.
**Round-4 normalisation (reviewer-directed):** the **Q3 exemption is cited as `phase1-plan.md` D1-14** and the
**single-read guard is cited as `decisions.md` D1-12**. The two colliding entries in `decisions.md` were
**swapped** (D1-12 ↔ D1-14) and the mapping is recorded here — a **mapping table**, not a silent renumber.

| Topic | `phase1-plan.md` id | `decisions.md` id | **Canonical** |
|-------|---------------------|--------------------|------------------------|
| the byte-level port — anchor: *"the verdict-input domain the $(cat)-processed bytes"* | D1-1 | D1-1 | phase1 D1-1 |
| the strict-JSON option (**declined**) — **no phase1 id** | — | D1-4 | decisions D1-4 |
| the stdout contract (`$(cat)` emulation) — anchor: *"the helper's stdout contract bash command-substitution"* | D1-10 | D1-9 | phase1 D1-10 |
| the corpus 15→22 + duplicate-key BASE-bug-preserving — anchor: *"the corpus grows and the duplicate-key"* | D1-11 | D1-10 | phase1 D1-11 |
| the three-algorithms rule — anchor: *"three distinct algorithms, not one "same"* | D1-12 | D1-11 | phase1 D1-12 |
| the placement = the new sibling file `journal_read.py` — anchor: *"placement final: dedicated read-only helper the"* | D1-13 | D1-8 | decisions D1-8 |
| the **Q3 path scope** (promote-path-only refusal) — anchor: *"the size guard refuses the promote"* | D1-14 | D1-14 | phase1 D1-14 |
| the threshold (bare — **superseded** by the re-scope) — anchor: *"the guard's threshold stated, derived and"* | D1-15 | D1-13 | phase1 D1-15 |
| the **single-read guard** (no torn view) — anchor: *"helper single-read concurrency (canonical record: decisions.md"* | D1-16 | D1-12 | decisions D1-12 |
| the **re-scoped guard semantics** (W1) — anchor: *"the final size-guard semantics (leader directive,"* | D1-17 | D1-15 | phase1 D1-17 |

**Renumber mapping applied in `decisions.md` (round 4):** old **D1-12 → D1-14** (the Q3 ruling) · old **D1-13 → D1-13**
(unchanged — the threshold) · old **D1-14 → D1-12** (the single-read guard). Nothing else was renumbered.

**Verification (corrected claim — approver-002 blocker 1):** every `D1-*` cross-reference in the plan set is
file-qualified, and every row of this map carries a **topic anchor** that the executable gate
(`gate.sh` class **(h3)**, `phase5-plan.md` T5.17) re-derives against the **defining row's topic** — so the map
resolves each id to its topic **mechanically**, not by assertion. The earlier wording ("the per-ID topic column
… resolves every id to its topic") was an unchecked claim: three rows pointed at the wrong id and two narrative
pointers cited `D1-10` for the placement decision (`D1-13`); all are corrected and now gate-verified.

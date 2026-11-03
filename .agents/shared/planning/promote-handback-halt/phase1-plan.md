# Phase 1 — Hand-back Verify False-Negative Fix (budget + serving conjunct)

> **Plan-set:** `promote-handback-halt` · **Phase:** 1 of 3 (Phase 2 = launcher-sweep/daemon-closer
> serving-green commit-close; Phase 3 = rollback-cooldown GNU rider).
> **Mode:** greenfield (Mode 1) — this file is the phase-1 artifact of record.
> **ID convention:** phase-scoped prefixes `D-P1-n` / `T-P1-n` / `AC-P1-n` / `R-P1-n` because Phases 2
> and 3 are being written concurrently by sibling workers; the decisions worker maps these into the
> set's global `decisions.md` numbering. **Assumption (A-P1-1)** — see §8.

---

## 1. Objective

Give the post-flip **unit hand-back verifier** enough budget and a strong enough verdict to
distinguish *"the daemon is still booting"* from *"the daemon is not coming"* — so that a
SERVING-GREEN release is never halted (and never left with an open txn) because the verifier's
deadline expired while the daemon was mid-boot.

Concretely: replace the verifier's borrow of `LIVEZ_BUDGET_S` (180 s) with a dedicated
`HANDBACK_VERIFY_BUDGET_S` knob (default 300 s) that is **monotone-safe** (never grants less than
today's 180 s), and replace the raw `lsof` port-listener conjunct with a **real serving check**
(`/livez` answers 2xx **and** reports the expected version) while keeping the port-listener as a
necessary fast condition.

---

## 2. Background

On 2026-11-01 the promote run `r-20261101-212542-8279` (target v0.28.5) flipped the symlink, handed
the daemon back to its systemd unit, and then **halted at the hand-back verifier**: the daemon
process started at 21:31:00.27 and the port bound *after* the verifier's deadline
(verifier_start + `LIVEZ_BUDGET_S`=180 s ≈ 21:31:10) — a **miss by seconds**. The v0.28.3 ceremony
(`r-20261101-075210-e3dd`) had confirmed at **175/180 s** — the same race, nearly lost. The root
cause is structural: the flipped unit takes **~3 min** from unit-active to serving (MCP warmup +
auto-continue resume; `readyz` `resume_hold.boot_continue:2` corroborates) while the verifier's
budget is borrowed from an unrelated knob.

The halt left the flipped txn open (B4) — and the *consequences* of that (the launcher's wrongful
AUTO-ROLLBACK of a serving-green release, and the wrongful quarantine) are **Phase 2's scope**,
not this phase's. This phase fixes only the **verifier** so that it stops manufacturing false
negatives.

---

## 3. Scope

### In scope (this phase)

1. A dedicated, env-overridable hand-back verify budget knob (`HANDBACK_VERIFY_BUDGET_S`), replacing
   the verifier's borrow of `LIVEZ_BUDGET_S`.
2. The explicit **phase-arithmetic reconciliation** of the hand-back budget against the ceremony's
   900 s wall-clock ceiling — including the option analysis and the recommendation.
3. The **serving conjunct**: strengthen the verdict from "raw `lsof` port-listener exists" to
   "the daemon answers `/livez` with the expected version" (port-listener retained as a necessary
   fast condition).
4. The **bounded re-verify grace** design (progress-gated, one extension) — or a documented
   argument that it is redundant.
5. The **L3 `RuntimeMaxSec` reconciliation** (the transient-unit backstop derived at arm time).
6. The **test surface**: a new slow-boot hand-back timing pin + the budget/conjunct/deadline pins,
   tiered per the house gate doctrine.

### Out of scope (explicitly)

| Out | Owner | Why |
|-----|-------|-----|
| Launcher-boot sweep AUTO-ROLLBACK disposition; daemon-side closer commit-close; wrongful-quarantine clearing | **Phase 2** | Different decision-maker (the launcher sweep / `upgrade_journal_sweep.py`); separate code surface (`launcher.sh`, `daemon/services/upgrade_journal_sweep.py`) |
| `_js_count_rollback` BSD-only `date -jur` cooldown (GNU debt, `launcher.sh:473-477`) | **Phase 3** | Different file, different disposition chain |
| `promote.sh`'s halt-row quarantine marking (the "punished a healthy release" half) | **Phase 2** | Bound by the owner-ratified `quarantine_cleared_serving_green` path |
| Any change to the auto-rollback / never-green gate semantics | **Phase 2** | The owner ruling keeps never-green armed (composition requirement). **[PLANNER r2]** *Mechanism, for the record:* never-green is armed by **two** paths — a **BROKEN** observation (`/livez` answered non-200) ⇒ the existing chain fires **immediately**; an **AMBIGUOUS** observation (no answer / wrong version / `/readyz` not ready) ⇒ the existing chain fires after the **bounded absent-ceiling** (≥3 qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed). Phase 2 §AM-P2-10 is the single normative statement; before the r2 fold the ambiguous class was excluded from the eligibility set, so the canonical never-green shape rolled back **nothing** — the plan council's REJECT basis. |
| `PROMOTE_TOTAL_DEADLINE_S` default change | **Phase 2 / decisions** (option (a), §6) | See the option analysis — not required by this phase's recommendation |
| Daemon-side boot-time reduction (MCP warmup budget, auto-continue resume) | separate commission | This phase treats the ~3 min boot as a **given**, and budgets for it |

---

## 4. Design

### 4.1 The knob — `HANDBACK_VERIFY_BUDGET_S`

**House pattern.** Every numeric budget in `scripts/upgrade/lib.sh` is declared through
`_numeric_knob` (`lib.sh:109-123`), which (a) falls back to a documented default when the env
override is absent and (b) **refuses a non-decimal override** with a loud stderr line rather than
letting a garbage budget silently disable the bound it guards (`lib.sh:115-122`). The new knob
follows it exactly.

**Declaration site.** Next to the other hand-back budgets, `lib.sh:179-182` — specifically after
`HANDBACK_START_BUDGET_S` (`lib.sh:180`, the §9-culprit `systemctl start` bound, 60 s):

```bash
HANDBACK_VERIFY_BUDGET_S="$(_numeric_knob HANDBACK_VERIFY_BUDGET_S "${HANDBACK_VERIFY_BUDGET_S:-}" 300)"
```

**Default: 300 s.** Derivation: the measured unit-active → serving boot is **~185 s** (tonight:
process start 21:31:00.27, port bound after ≈21:31:10; the v0.28.3 ceremony confirmed at 175/180 s).
300 s is **~1.6× the measured worst observed** — the same shape of headroom argument the house
already uses for `LIVEZ_BUDGET_S` ("180 s gives ~3x the base 60 s headroom for cold-boot + verify
span", `lib.sh:126-133`).

**Two companion knobs** (same declaration block, same `_numeric_knob` pattern):

| Knob | Default | Role |
|------|---------|------|
| `HANDBACK_VERIFY_BUDGET_S` | 300 | **Ceiling** — the most the hand-back verify may ever wait |
| `HANDBACK_VERIFY_FLOOR_S` | 180 | **Floor** — the least it may ever be given (today's effective budget; guarantees the fix is monotone-safe) |
| `HANDBACK_TAIL_RESERVE_S` | 120 | Downstream room reserved for livez+readyz+version+soak-squeeze+commit when the ceremony clock is known |

**The effective budget.**

```
effective = CEILING                                    # no ceremony clock known (restart.sh / rollback.sh)
effective = clamp(remaining − TAIL_RESERVE, FLOOR, CEILING)   # ceremony clock known
```

`clamp(x, lo, hi)` = `max(lo, min(hi, x))`. `remaining` = the ceremony's remaining wall-clock,
computed with the existing house helper `_effective_ceremony_remaining_s` (`lib.sh:632-640`) — the
same helper the adaptive soak uses (`promote.sh:680` → `lib.sh:6046`).

**Why the floor exists.** It makes the change **monotone-safe**: the hand-back verify can only ever
be given *more* time than today, never less. A squeezed ceremony (e.g. the pre-stop phases ran
long) cannot turn a healthy release into a false negative — the verifier still gets today's 180 s.
This is the property that lets the recommendation avoid touching the ceremony cap at all (§6).

**How the ceremony clock reaches the verifier.** The verifier lives in `lib.sh` and is called from
`promote.sh`'s process (`promote.sh:587` → `restart_via_launcher` → `lib.sh:5072`/`5077` →
`_supervision_handback_unit` → `_supervision_unit_serving`). The house precedent for
"promote.sh → lib.sh, same process, **not** inherited by child scripts" is `JOURNAL_READ_STRICT`:
set by `promote.sh` as a **plain (non-exported) shell variable** and read by `lib.sh` with a
`${VAR:-default}` default (`lib.sh:1376-1379`). The new mechanism mirrors it:

- `lib.sh`: `PROMOTE_CEREMONY_DEADLINE_EPOCH="${PROMOTE_CEREMONY_DEADLINE_EPOCH:-}"` (empty = unknown).
- `promote.sh`: immediately after arming the ceremony clock at `promote.sh:262`
  (`promote_deadline=$(( $(_now_epoch) + PROMOTE_TOTAL_DEADLINE_S ))`), set
  `PROMOTE_CEREMONY_DEADLINE_EPOCH="$promote_deadline"` as a **plain** variable (never exported —
  the child scripts `stop-ensemble.sh` etc. must not inherit it).

**Consequences of the knob change at the three non-promote call sites** (disclosed):

| Call site | Ceremony clock known? | Effective budget | Note |
|-----------|----------------------|------------------|------|
| `promote.sh:587` (promote hand-back) | yes | `clamp(remaining−120, 180, 300)` | the incident path |
| `promote.sh:821` (auto-rollback re-start) | yes (same run) | `clamp(...)` | the rollback re-start's wait can grow 180 → 300 in the *failure* case (daemon down) |
| `rollback.sh:235` | no | 300 | daemon-down wait may grow in the failure case |
| `restart.sh:237` | no | 300 | the same boot-timing race applies to a plain restart |

### 4.2 The serving conjunct

**[PLANNER r4 → note (k) THE READYZ-AT-HANDBACK REJECTION IS DELIBERATE — recorded here so a future compliance reader does not re-add `/readyz`.]** The commission's Part-1 wording is loose enough to read as *"the hand-back must verify that the new release is READY"*. **It does not, and must not.** `D-P1-4` rejected `readyz` **explicitly** (see the rejected-alternatives cell at the end of this section: *"(ii) `readyz` instead of `livez` — `/readyz` is the *gate's* question (`promote.sh:615`); the hand-back must not own readiness, and readyz carries readiness components that are legitimately not green during boot"*). The hand-back's serving conjunct is **port-listener ∧ `/livez` 2xx ∧ version == expected** — **`/livez` only**. Adding `/readyz` here would **re-create tonight's incident class**: the daemon's readiness composite is legitimately degraded during warmup (MCP warmup + auto-continue resume — tonight's measured ~3 min unit-active→serving), so a hand-back that demands `ready` would **halt-for-human on every slow-but-healthy boot** — the exact race that produced the wrongful rollback this lane exists to close. ⇒ **the hand-back answers "is the new binary SERVING the right version?", not "is the new binary READY?"** — the readyz question belongs to the promote gate (`promote.sh:615`), which is Phase 2's canonical-GREEN predicate's business (`AM-P2-11`), not to the hand-back's. (Phase 2's launcher closer *does* probe `/readyz` — that is a **different** question asked by a **different** decision-maker about a **stale** txn, and the two must not be conflated.)

**Today's verdict** (`_supervision_unit_serving`, `lib.sh:4751-4782`):

```
(MainPID nonzero  AND  MainPID ≠ prestop)  AND  lsof -ti:$PORT succeeds
```

- `lib.sh:4760-4763` — `MainPID == prestop` **fails FAST** (one poll, no retry): "no retry loop can
  fix it: the old daemon never left". **This branch must not change.**
- `lib.sh:4764-4769` — with `PORT` set and `lsof` present: the port listener is the confirming
  conjunct.
- `lib.sh:4770-4773` — **degenerate branch**: `PORT` unset **or** `lsof` absent → the MainPID check
  alone decides (house rule: "never fault on a missing tool", the `_unit_stopped` precedent in
  `stop-ensemble.sh`).

**Proposed verdict:**

```
(MainPID nonzero  AND  MainPID ≠ prestop)         # unchanged, incl. the fast-fail branch
AND  port-listener present (lsof, when available)  # necessary fast condition (unchanged)
AND  /livez answers 2xx                            # NEW — the daemon actually SERVES
AND  /livez.version == expected version            # NEW — it is the RIGHT release serving
```

**Admissibility (verified, zero new dependencies).** `lib.sh` is **not** purity-constrained the way
`launcher.sh` is (R-08: the launcher may not use `python3`); `curl` is already used in `lib.sh`:
`_probe_once_classified` (`lib.sh:3874-3920`, `curl -sS -o tmp -w '%{http_code}' --max-time 5`),
the readyz readers (`lib.sh:4059`, `lib.sh:4066`), and the green-body reader (`lib.sh:4174`).
The reusable version comparator is `gate_version` (`lib.sh:5991-6002`): `_probe_once /livez` →
`_json_field body version` → string equality. The livez `version` field is the daemon's bare
`__version__` (`daemon/api.py:5215`) — the same value shape as the manifest's `binary_version`
(`promote.sh:459`, compared at `promote.sh:670`).

**Ordering.** `lsof` stays the *cheap necessary* condition (it proves the socket is bound);
the `/livez` probe is the *confirming* condition (it proves the daemon answers). A bound socket that
answers nothing is **not** serving — that is precisely the gap that let tonight's miss be a
false-negative at the boundary rather than a "still booting" verdict.

**Failure semantics (normative).**

| Observation | Class | Disposition |
|-------------|-------|-------------|
| `MainPID == prestop` | **fail-fast** | return 1 immediately, exactly one poll (`lib.sh:4760-4763` preserved verbatim) |
| port not listening yet | **transient** | keep polling to the deadline |
| `/livez` unreachable (curl rc 7/28) or non-2xx | **transient** | keep polling to the deadline (the daemon is booting) — mirrors the house 3-state classification at `lib.sh:3874-3920` and the boot-grace semantics (`lib.sh:4148` `_probe`) |
| `/livez` answers 2xx **and** `version == expected` | **success** | print the new MainPID, return 0 |
| `/livez` answers 2xx **but** `version != expected` | **fail-fast** | return 1 immediately — *no amount of waiting fixes a wrong-version daemon*; the flip already happened, so a wrong version is a genuine anomaly for a human. Same reasoning class as the `MainPID == prestop` fast-fail. |
| `PORT` unset | **degenerate** | MainPID-alone (unchanged, `lib.sh:4770-4773`); the serving check cannot run without a port |
| `PORT` set, `lsof` absent, `curl` present | **degraded** | the serving check carries the verdict (the port-listener check is skipped, not failed) — *never fault on a missing tool* |
| `PORT` set, `lsof` absent, `curl` absent | **degenerate** | MainPID-alone (unchanged) |
| budget exhausted, no confirmation | **unconfirmable** | return 1 → caller halts for human (Amendment #1 preserved) |

**Tightening vs preserving the degenerate branch.** The task asks whether to preserve or tighten the
`PORT`-unset / `lsof`-absent branch. **Recommendation: preserve it** (MainPID-alone), with one
tightening: when `PORT` **is** set and `curl` **is** available, the serving check is mandatory — the
`lsof`-absent case degrades to *serving-only*, never to *MainPID-alone*. Rationale: `PORT` is set on
every real promote (`promote.sh` reads it from the install config), so the degenerate branch is
reachable in practice only under a partial tool environment; in that case the serving check is the
*stronger* signal and is available. Tightening `PORT`-unset to a hard failure is **rejected**: the
house rule is "never fault on a missing tool / missing observation" (`lib.sh:4744-4746`), and the
scope→unit self-heal path (`lib.sh:5077`) does not always carry a port.

**Expected-version plumbing.** The verifier needs the expected version. Mechanism: the same plain-
variable channel as the deadline (`lib.sh` reads `${VAR:-}`; `promote.sh` sets it). `promote.sh`
already computes `BIN_VERSION` at `promote.sh:459` (`manifest_field "$VERSION" binary_version`) —
so `promote.sh` sets `PROMOTE_EXPECTED_VERSION="$BIN_VERSION"` next to the deadline assignment.
When it is **unset** (the `restart.sh` / `rollback.sh` contexts), the serving check degrades to
"2xx without a version comparison" — the verdict still proves *serving*, just not *which* release.

### 4.3 The bounded re-verify grace

**Is it redundant?** The verifier already *is* a retry loop: `lib.sh:4754-4779` polls every 2 s
(`sleep 2`, `lib.sh:4778`) until its deadline. So "a retry grace" is not a new mechanism — the
budget **is** the grace. What is missing is only the ability to *extend* past the first deadline.

**Recommendation: implement one narrow, progress-gated extension.**

- Knob: `HANDBACK_VERIFY_GRACE_S` (default **60**), same `_numeric_knob` pattern.
- Trigger: the first budget expired **and** the verifier holds positive evidence of progress —
  a **new** MainPID (≠ prestop) is present **and** the port is bound — i.e. the daemon is
  demonstrably *up but not yet answering*. Only then does the verifier extend once, by ≤ 60 s.
- No progress (no MainPID, or MainPID == prestop, or no listener) → **no extension**; the deadline
  is honored exactly. A dead daemon is not granted extra time.
- At most **one** extension; never a loop.
- The extension is **reported** (a `_warn` line naming the budget in force) so the post-mortem can
  see that the grace fired.

**Why it is worth its cost.** The residual risk after the 300 s ceiling is a host slower than
tonight's (MCP warmup + auto-continue are load-sensitive). With the ceiling at 300 s and the
measured boot at ~185 s, the margin is 1.6×; the grace buys a further 20 % for one branch and one
knob. It is also *the right place* to spend the extra time: the extension only fires while the
daemon is visibly coming up.

**What it must not become.** It must not delay the *unconfirmable* verdict indefinitely: the
extension is bounded by `HANDBACK_VERIFY_GRACE_S` and the total wait remains
`effective + grace ≤ 360 s`. The `MainPID == prestop` fast-fail is untouched (`lib.sh:4760-4763`).

**Open alternative.** If the architect judges the extra knob unjustified, the fallback is to drop the
grace and rely on the 300 s ceiling + Phase 2's "do not roll back unless the serving is
genuinely unhealthy" protection. Recorded as **OQ-P1-4** (§8).

### 4.4 The phase-arithmetic reconciliation

#### 4.4.1 The documented table is not a bound table (correction)

`lib.sh:157-162` records the C-10a decomposition — **[PLANNER r4 → note (h) AND IT IS DUPLICATED — there are TWO copies of this fiction]** the same C-10a arithmetic block appears verbatim in **`promote.sh:255-261`** (verified in-tree: `promote.sh:256-261` carries the identical `preflight 45 / stop 225 / flip 30 / handback 90 / livez 180 / readyz 120 / version 30 / soak 120 / commit 30 = 870s` line, under the comment *"C-10a arithmetic (decisions.md (d) §C-10a, single source of truth)"*). ⇒ **`T-P1-9` must correct BOTH copies** (`lib.sh:157-162` **and** `promote.sh:255-261`); correcting only the first leaves the second asserting the fiction — and the second copy is the one a reader of the *promote* entry path sees first.

> `preflight 45 / stop 225 / flip 30 / handback 90 / livez 180 / readyz 120 / version 30 / soak 120 / commit 30 = 870s non-stop worst (+30s slack inside the 900s ceiling)`

Three defects, all corrected by this phase:

1. **`handback 90` is fiction.** The code's hand-back slice is `LIVEZ_BUDGET_S` = **180 s**
   (`lib.sh:4753` ← `lib.sh:146`), not 90 s. The documented table has never described the code.
2. **The table mixes measured values with budget bounds.** `preflight 45` is the *measured healthy*
   preflight (cf. `lib.sh:211-213`: "the measured healthy preflight (68 s wall; …)"), while
   `livez 180` / `readyz 120` are *budget ceilings*. A sum of mixed kinds is not a bound.
3. **The house itself says the sum can exceed the cap.** `lib.sh:158`: "all-budgets-max (stop worst
   755s + every budget max + adaptive soak squeeze) = 1400s → halt."

⇒ **The 900 s ceiling is a STOP-LOSS, not a guarantee that the budgets fit.** This is the fact the
whole arithmetic must be reconciled against, and it is the reason the task's "870−90+300 = 1080 >
900 ⇒ *guaranteed* deadline-breach halt" framing does not hold literally: the ceremony already
exceeds 900 s on the all-worst path today.

#### 4.4.2 The reconciled table (authoritative for this phase)

| # | Phase | Budget knob | Default | Clock starts | Enforcing site | Worst case |
|---|-------|-------------|---------|--------------|----------------|-----------|
| 0 | ceremony clock arm | `PROMOTE_TOTAL_DEADLINE_S` | 900 | `promote.sh:262` | — (the clock itself) | — |
| 1 | preflight | `PREFLIGHT_PHASE_BUDGET_S` (own clock) + `PREFLIGHT_STEP_BUDGET_S` (per step) | 300 / 120 | `promote.sh:257` (`preflight_deadline`) | `promote.sh:369`/`380`/`410`/`458` (`_enforce_deadline "$preflight_deadline" preflight`) + `lib.sh:546` `_run_bounded_step` → `lib.sh:503` `_budget_breach` | 300 |
| 2 | stop | `STOP_SCRIPT_BUDGET_S` (derived at the call site, `lib.sh:4446-4471`) + child `WAIT_S` | derived | stop phase | `promote.sh:534` | ~225 |
| 3 | launcher swap | — | — | — | `promote.sh:548` | ~0 |
| 4 | atomic flip | — | — | — | `promote.sh:570` | ~0 |
| 5 | **hand-back verify** | **`HANDBACK_VERIFY_BUDGET_S`** (ceiling) / `HANDBACK_VERIFY_FLOOR_S` (floor) / `HANDBACK_VERIFY_GRACE_S` (one extension) | **300 / 180 / 60** | verifier entry (`lib.sh:4753`) | **its own deadline loop (`lib.sh:4777`) — NOT an `_enforce_deadline` site** | 360 |
| 6 | livez gate | `LIVEZ_BUDGET_S` | 180 | `promote.sh:606` | `promote.sh:606` + `lib.sh:5986`/`4148` (`_probe`) | 180 |
| 7 | readyz gate | `READYZ_BUDGET_S` | 120 | `promote.sh:615` | `promote.sh:615` + `lib.sh:5987` | 120 |
| 8 | version verify | one-shot `_probe_once` (curl `--max-time 5`) | ~5 | `promote.sh:668` | `promote.sh:668` + `lib.sh:5991` `gate_version` | 5 |
| 9 | soak | adaptive: `min(ADAPTIVE_SOAK_CAP_S`=120, `ceremony_remaining`−60)` | ≤120 | `promote.sh:677` | `promote.sh:677` + `lib.sh:6046` `gate_soak` (already deadline-coupled, D-P3.5) | 120 |
| 10 | commit | — | ~30 | `promote.sh:689` | `promote.sh:689` | 30 |

**Totals.**

- **Current code:** 300+225+0+0+**180**+180+120+5+120+30 = **1160 s** worst — already > 900.
- **With this phase's change:** 300+225+0+0+**360**+180+120+5+120+30 = **1340 s** worst.
- **Typical healthy run** (measured, not worst-case): preflight ~45 + stop ~30 + hand-back **~185**
  (tonight's measured boot) + livez/readyz/version ~2 + soak ~60 + commit ~5 ≈ **330 s** — roughly
  one third of the 900 s cap, with ~570 s of headroom for the downstream phases.

**The enforcing sites are 14 in total** — 10 on the ceremony clock (`promote.sh:292` preflight,
`:534` stop, `:548` launcher_swap, `:570` flip, `:598` restart, `:606` livez, `:615` readyz,
`:668` version, `:677` soak, `:689` commit) and 4 on the preflight clock (`promote.sh:369`, `:380`,
`:410`, `:458`). A breach (`lib.sh:416-454`) writes a `halt` row with detail
`deadline-breach-<phase>` + `daemon-state=` (`lib.sh:431-445`), releases the lock and **exits 78**
(`lib.sh:452-453`) — and does **NOT** auto-rollback (`lib.sh:400-403`).

#### 4.4.3 The critical coupling — the hand-back is INSIDE the ceremony clock

The ceremony clock is armed **once**, at `promote.sh:262`, *before* the preflight. The hand-back
verify sits **inside** that clock. So a longer hand-back budget **consumes the ceremony's shared
budget**, and the first boundary after it is `promote.sh:598` (`_enforce_deadline "$promote_deadline"
restart`), whose `daemon-state` is `handed-back` (`lib.sh:437`) — i.e. a breach there halts with the
daemon **UP and (possibly) serving green**.

Worst-case elapsed at each boundary under this phase (pre-stop at its 525 s worst):

| Boundary | Elapsed (worst) | Breach? |
|----------|-----------------|---------|
| `promote.sh:598` restart | 525 + 360 = 885 | no (885 < 900) — but only by 15 s |
| `promote.sh:606` livez | 885 + livez | the boundary fires **before** the livez probe; a slow livez would breach |
| `promote.sh:615` readyz | 885 + 180 + 120 | breach in the all-worst path (already true today at 1160 s) |

⇒ **Raising the hand-back budget without touching the ceremony cap converts a hand-back
false-negative into a ceremony-deadline false-positive** in the combined-slow case — and a
deadline breach at `:598`/`:606` produces the *same* harmful shape as tonight (flipped txn left
open, daemon up, halt-for-human). This coupling is the reason §6 does not simply raise the
hand-back knob and stop.

In the **typical** case (pre-stop ~75 s, boot ~185 s → elapsed ~260 s at `:598`), there is ~640 s
of headroom and no breach risk. The risk is confined to the combined-slow case.

### 4.5 The L3 `RuntimeMaxSec` reconciliation (requirement 6)

**The derivation site (found — the explorer had not read it):**

`daemon/tools/upgrade_journal.py:2556-2568`

```python
def derived_runtime_max_sec(soak_s=None, total_deadline_s=None) -> int:
    if total_deadline_s is None:
        total_deadline_s = _env_int("PROMOTE_TOTAL_DEADLINE_S", 900)   # :2564
    if soak_s is None:
        soak_s = _env_int("ENSEMBLE_PROMOTE_SOAK_S", 300)              # :2566
    soak_s = max(0, min(soak_s, 900))
    return max(RUNTIME_MAX_SEC_FLOOR, total_deadline_s + soak_s + RUNTIME_MAX_SEC_TAIL_S)
```

with `RUNTIME_MAX_SEC_FLOOR = 1200` (`:2544`) and `RUNTIME_MAX_SEC_TAIL_S = 120` (`:2545`).

**The arm site:** `daemon/manager.py:5800-5808` — for `kind != "restart"` the transient unit carries
`--property=RuntimeMaxSec={_uj.derived_runtime_max_sec()}`; restart arms carry **none**
(kind-scoped, `manager.py:5798-5804`).

**The invariant (the one that must hold):** `RuntimeMaxSec` must stay **strictly above** the
shell's own ceremony deadline, so systemd fires only when the shell's own deadline machinery has
itself wedged (`upgrade_journal.py:2559-2566`, `manager.py:5795-5799`).

**Reconciliation under THIS phase's recommendation (§6 = option (c), ceremony cap unchanged):**

- `PROMOTE_TOTAL_DEADLINE_S` is **not** changed ⇒ the daemon-side derivation
  (`upgrade_journal.py:2564`, default 900) and the shell-side knob (`lib.sh:163`, default 900) are
  **untouched** ⇒ `RuntimeMaxSec` stays **1320** at the defaults ⇒ the invariant
  (`1320 > 900`) **holds, unchanged**. **No `RuntimeMaxSec` change is required by this phase.**
- The hand-back budget is a **sub-budget of** the ceremony, not an addition to it, so it does not
  enter the derivation. Correct by construction.

**Two divergence hazards that MUST be documented (and are load-bearing if option (a) is chosen):**

1. **Two independent readers, two separate default literals.** The shell reads
   `PROMOTE_TOTAL_DEADLINE_S` via `_numeric_knob` (`lib.sh:163`); the daemon reads the same name
   from its **own** `os.environ` via `_env_int` (`upgrade_journal.py:2564`, `:2548-2554`).
2. **They agree only when the value reaches the daemon's process environment.** The install `.env`
   reaches `os.environ` because `launcher.sh`'s `load_env_file` **exports** every `KEY=VALUE`
   line (`launcher.sh:136-186`, the export at `:186`) and the daemon is launched by that launcher;
   the promote executor then inherits the daemon's environment. An operator who exports the
   variable only in an ad-hoc shell gets a **shell-side** value the daemon never sees.

⇒ If the leader selects **option (a)** (§6), the plan must additionally: (i) change the default in
**both** readers (`lib.sh:163` **and** `upgrade_journal.py:2564`), (ii) re-pin the existing test
expectations (`tests/unit/tools/test_upgrade_preflight_daemon_side.py:197` asserts
`RuntimeMaxSec=1320` at the defaults; `:329` asserts `1620` for the 1200 case), and (iii) document
that the operator override must land in the **install `.env`** (not an ad-hoc shell) or the L3
backstop can end up **below** the shell's own deadline — a *new* failure class (systemd SIGTERM →
exit 143 + a halt row **without** the `deadline-breach-` prefix, `lib.sh:493` distinguishes the two
classes).

### 4.6 Preserved contracts (binding — must not regress)

| Contract | Anchor | Disposition in this phase |
|----------|--------|---------------------------|
| **No nohup fallback** (Amendment #1) | `promote.sh:587-591` (verbatim "NO nohup fallback — halting for the caller's B4 policy"); `lib.sh:4788-4790`, `4864-4872` | **Untouched.** This phase changes only the *verifier's budget and verdict*, never the hand-back's disposition. |
| **Halt-for-human for genuinely unconfirmable hand-backs** | `promote.sh:588-590`; `lib.sh:4879-4882` | **Untouched.** The verifier still returns 1 on an unconfirmable verdict → the caller halts. |
| **B4 — txn left open on halt** | `promote.sh:585`, `:589` | **Untouched.** |
| **Timing-breach = halt, NOT auto-rollback** | `lib.sh:400-403`, `:452-453` | **Untouched.** No new auto-rollback path is introduced. |
| **Equal-to-prestop MainPID fails fast (one poll)** | `lib.sh:4760-4763`; pinned by the existing C3 leg (`tests/test_supervision_stop_handback.sh:706-712`) | **Untouched**, and re-pinned under the new budget. |
| **Never fault on a missing tool** | `lib.sh:4744-4746`, `:4770-4773` | **Preserved** in the degenerate branches (§4.2). |
| **Amendment #1's "never a false success"** | `lib.sh:4787-4790` | **Strengthened**: the serving conjunct makes "serves the flipped release" (`lib.sh:4891`) *true* rather than assumed. |
| **`systemctl start` literal ban on the success path** (7b substring trap) | `lib.sh:4795-4800`; pinned at `tests/test_supervision_stop_handback.sh:695` | **Untouched** — no new success-path log line may contain the banned literal. |
| **Deadline-breach = halt, not auto-rollback** | `lib.sh:416-454` | **Untouched.** |

---

## 5. Decisions

| ID | Decision | Rationale (cite) | Rejected alternatives |
|----|----------|------------------|------------------------|
| D-P1-1 | Dedicated knob `HANDBACK_VERIFY_BUDGET_S` (default **300**), declared at `lib.sh:180` via `_numeric_knob`, replacing the verifier's borrow of `LIVEZ_BUDGET_S` (`lib.sh:4753`). | The measured unit-active→serving boot is ~185 s (tonight's process start 21:31:00.27 vs the ~21:31:10 deadline; v0.28.3 confirmed at 175/180 s). `LIVEZ_BUDGET_S` (180, `lib.sh:146`) governs the **/livez health gate** (`lib.sh:5986`) — a different question, borrowed by accident. | (i) Raise `LIVEZ_BUDGET_S` — couples an unrelated gate to boot timing (the gate's 180 s is already a measured-headroom choice, `lib.sh:126-133`). (ii) Keep 180 and rely on Phase 2 — leaves the false-negative in place. (iii) Unbounded wait — violates the house's bounded-wait discipline (`lib.sh:166-178`). |
| D-P1-2 | The effective budget is **deadline-aware and monotone-safe**: `clamp(ceremony_remaining − TAIL_RESERVE, FLOOR=180, CEILING=300)`; with no ceremony clock known → `CEILING`. | House precedent: the adaptive soak's deadline coupling (D-P3.5, `lib.sh:6040` + `_effective_ceremony_remaining_s` `lib.sh:632-640`, called at `promote.sh:680`). The floor guarantees the change can never grant *less* than today (180). | (i) Fixed 300 with no squeeze — in a squeezed ceremony the hand-back would starve the downstream and manufacture a *different* wrongful halt. (ii) Squeeze with no floor — could grant less than today (a regression). (iii) Excluding the hand-back from the ceremony deadline — no precedent, and it removes the stop-loss over a phase that can wedge. |
| D-P1-3 | The ceremony clock reaches the verifier through a **plain (non-exported) shell variable** `PROMOTE_CEREMONY_DEADLINE_EPOCH`, set by `promote.sh` next to `promote.sh:262` and read by `lib.sh` with an empty default. | House precedent: `JOURNAL_READ_STRICT` — "set by promote.sh PER INVOCATION as a plain (non-exported) shell variable … never inherited by the child scripts" (`lib.sh:1376-1379`). | (i) Export the variable — the child scripts (`stop-ensemble.sh` etc.) would inherit it and could mis-derive budgets. (ii) Thread a parameter through `restart_via_launcher` → `_supervision_handback_unit` → `_supervision_unit_serving` — three signature changes across four call sites (`promote.sh:587`, `promote.sh:821`, `rollback.sh:235`, `restart.sh:237`) for the same effect. |
| D-P1-4 | The verdict is strengthened to a **real serving check**: port-listener (necessary) **AND** `/livez` 2xx **AND** `version == expected`. `MainPID == prestop` still fails fast with one poll. | `curl` is already used in `lib.sh` (`lib.sh:3874-3920`, `:4059`, `:4066`, `:4174`) — zero new dependencies; `lib.sh` is not purity-constrained like `launcher.sh` (R-08). The reusable comparator is `gate_version` (`lib.sh:5991-6002`). The livez `version` field is the daemon's `__version__` (`daemon/api.py:5215`) — the same shape as the manifest `binary_version` (`promote.sh:459`). | (i) Keep raw `lsof` — a bound socket that answers nothing is not "serving"; this is the class that produced tonight's miss. (ii) `readyz` instead of `livez` — `/readyz` is the *gate's* question (`promote.sh:615`); the hand-back must not own readiness, and readyz carries readiness components that are legitimately not green during boot. (iii) `lsof` + a bare TCP connect — proves nothing about the daemon. |
| D-P1-5 | Failure semantics: **transient** (not listening / `/livez` unreachable / non-2xx) → keep polling to the deadline; **fail-fast** (`/livez` 2xx but wrong version) → immediate failure; **degenerate** (`PORT` unset, or no `lsof` **and** no `curl`) → MainPID-alone (unchanged). | The house 3-state classification is already `green`/`transient`/`red` (`lib.sh:3874-3920`, consumed by `_probe` `lib.sh:4148` and the boot-grace logic `lib.sh:4107-4146`). A wrong version cannot be fixed by waiting — the same reasoning the `MainPID == prestop` fast-fail already encodes (`lib.sh:4760-4763`). | (i) Fail fast on any non-2xx — would false-negative on a booting daemon (the incident class). (ii) Never fail fast — would let a wrong-version daemon pass. (iii) Tighten `PORT`-unset to a hard failure — violates "never fault on a missing observation" (`lib.sh:4744-4746`). |
| D-P1-6 | A **single progress-gated extension** of ≤ 60 s (`HANDBACK_VERIFY_GRACE_S`) fires only when the first budget expired with a new MainPID **and** a bound port — i.e. the daemon is demonstrably coming up. No progress → no extension. | The verifier is already a 2 s poll loop (`lib.sh:4754-4779`), so the budget *is* the retry; only an *extension* is new. The progress gate keeps the extra time where it buys something and keeps the dead-daemon path bounded. | (i) No grace — relies on the 300 s ceiling + Phase 2's protection; leaves a slower host exposed (margin is only 1.6×). (ii) Unconditional extension — would delay every genuinely-dead hand-back by 60 s. (iii) Repeated extensions — unbounded. |
| D-P1-7 | **The ceremony cap `PROMOTE_TOTAL_DEADLINE_S` stays at 900** under this phase; the reconciliation is achieved by D-P1-2's deadline-aware squeeze. `RuntimeMaxSec` therefore stays **1320** and the L3 invariant is untouched. | The 900 s cap is a documented **stop-loss** — the house itself records "all-budgets-max … = 1400s → halt" (`lib.sh:158`) — not a promise that the budgets fit. The incident case (fast pre-stop + slow boot) is fixed by D-P1-1/D-P1-2 with ~640 s of downstream headroom. Changing the cap is a **policy** change (the "15 min" is documented at `promote.sh:26-27` and in `decisions.md` (d)) that belongs to the leader/architect, not to a verifier fix. The L3 derivation is monotone in `PROMOTE_TOTAL_DEADLINE_S` (`upgrade_journal.py:2556-2568`), so leaving the cap alone leaves the backstop correct. | (a) Raise the cap — policy change + a cross-language default change (`lib.sh:163` **and** `upgrade_journal.py:2564`) + test-expectation churn (`tests/unit/tools/test_upgrade_preflight_daemon_side.py:197`, `:329`) + the env-divergence hazard (§4.5). (b) Exclude the hand-back from the total deadline — no precedent; removes a stop-loss. (c) Window-share — **adopted in a bounded form** as D-P1-2. |
| D-P1-8 | **[PLANNER r4 → note (h)]** The C-10a decomposition comment at `lib.sh:157-162` **AND ITS SECOND COPY AT `promote.sh:255-261`** are **corrected** to a real bound table (§4.4.2), including the `handback` slice, the preflight phase budget, and the note that the 900 s cap is a stop-loss. | The table currently mixes measured values with budget ceilings and states `handback 90` where the code uses 180 (`lib.sh:4753`) — a documentation defect that made the arithmetic unfalsifiable. **The duplication is itself the finding:** one copy corrected and one left ⇒ the fiction survives ⇒ the "single source of truth" comment is false. | Leaving it — the next reader will re-derive the same wrong sum and "fix" the wrong thing. Correcting **one** copy — the next reader of the promote path re-derives the same wrong sum. |
| D-P1-9 | The expected version is plumbed as `PROMOTE_EXPECTED_VERSION` (plain variable, `promote.sh` sets it from `BIN_VERSION` at `promote.sh:459`); when unset, the serving check degrades to "2xx, no version comparison". | Keeps the "no version known" contexts (`restart.sh:237`, `rollback.sh:235`) working, and makes the hand-back's success claim ("serves the flipped release", `lib.sh:4891`) *verifiable* rather than assumed. | Comparing in the hand-back against a value the verifier cannot know — would need signature churn; or leaving the version unverified — leaves a wrong-version daemon able to pass the hand-back. |
| D-P1-10 | The new budget names are added to `_budget_in_force` (`lib.sh:467-476`) so any future breach row can name the value in force. | `_budget_in_force` **refuses unknown names** (returns 1 → "unknown" in the row) — by house design ("refusing loudly on an unknown name keeps the resolver honest", `lib.sh:461-466`). | Leaving the new budget out of the resolver — a future breach row would print `value=unknown`, defeating the row's purpose. |

---

## 6. Options for the deadline reconciliation (explicit trade-off + recommendation)

The task frames three options. All three are analyzed against the **verified** constraints
(ceremony cap = `PROMOTE_TOTAL_DEADLINE_S`=900 at `lib.sh:163`, armed once at `promote.sh:262`,
enforced at 14 `_enforce_deadline` boundaries; the verifier self-bounds at `lib.sh:4753`/`4777`).

| Option | Mechanism | Fixes tonight's case? | Cost / risk | Verdict |
|--------|-----------|-----------------------|-------------|---------|
| **(a) Raise `PROMOTE_TOTAL_DEADLINE_S`** | Bump the default 900 → e.g. 1350 (`lib.sh:163`) so the honest all-worst sum (1340 s, §4.4.2) fits. | Yes. | **Policy change** (the "15 min" ceiling is documented at `promote.sh:26-27` and `decisions.md` (d)). Requires the **daemon-side** default change in lockstep (`upgrade_journal.py:2564`) or the L3 backstop can sit below the shell's deadline (§4.5). Test churn: `tests/unit/tools/test_upgrade_preflight_daemon_side.py:197` (1320) and `:329` (1620). The operator override must land in the **install `.env`** (the daemon reads its own `os.environ`), not an ad-hoc shell. | **Viable alternative** — the honest "make the budget fit" answer, but it is a leader-level policy call with a cross-language coupling. |
| **(b) Exclude the hand-back verify from the total deadline** | Give the hand-back its own clock, outside `promote_deadline`. | Yes. | **No precedent** — every phase boundary enforces today (14 sites). Removes a stop-loss over the phase that can wedge the ceremony. Requires a new carve-out in `_enforce_deadline` or a separate deadline arm, and would break the C-10b `daemon-state=` accounting (`lib.sh:424-440`). | **Rejected.** |
| **(c) Overlap / window-share** | The hand-back's effective budget is derived from the ceremony's remaining time — the same shape as the adaptive soak (D-P3.5, `lib.sh:6040`) and `PROMOTE_BOOT_GRACE_S` (`lib.sh:290-291`). | Yes — the incident case has ~800 s remaining → the hand-back gets its full 300 s. | Needs the ceremony clock plumbed into `lib.sh` (D-P1-3) and a floor so it can never grant less than today (D-P1-2). | **RECOMMENDED** (adopted as D-P1-2). |

**Recommendation.** Adopt **(c)** — as D-P1-2, with the monotone-safe floor. It fixes tonight's case
(hand-back gets 300 s instead of 180 s, with ~640 s of downstream headroom in the typical run), keeps
the 900 s stop-loss and its policy meaning intact, requires **no** change to `PROMOTE_TOTAL_DEADLINE_S`
and therefore **no** change to the L3 `RuntimeMaxSec` derivation (§4.5), and is the only option with
an in-repo precedent for the *shape* (the soak's deadline coupling).

**Residual disclosed.** In the **combined-slow** case (pre-stop at its 525 s worst **and** a slow
boot), the ceremony still breaches its 900 s stop-loss at a downstream boundary — and a breach at
`promote.sh:598`/`:606` halts with the daemon **up** (the same harmful shape as tonight). This is
**pre-existing** (the current code's all-worst sum is already 1160 s) and is *not* made worse by
this phase in the typical case; it is the strongest argument for option (a) and is recorded as
**OQ-P1-1** for the decisions worker. Phase 2's serving-green protection is the structural backstop
for it.

---

## 7. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files / anchors) | Depends on | Risks carried | AC |
|----|------|--------------------|------------------------------|------------|----------------|----|
| T-P1-1 | Declare the three new knobs through `_numeric_knob` next to the other hand-back budgets. | `HANDBACK_VERIFY_BUDGET_S=300`, `HANDBACK_VERIFY_FLOOR_S=180`, `HANDBACK_VERIFY_GRACE_S=60`, `HANDBACK_TAIL_RESERVE_S=120` are resolvable from the environment; a non-decimal override falls back to the documented default with a loud stderr line. | `scripts/upgrade/lib.sh:179-182` (after `HANDBACK_START_BUDGET_S` at `:180`) | — | R-P1-3 | AC-P1-1 |
| T-P1-2 | Register the new budget names in the breach-row resolver. | `_budget_in_force HANDBACK_VERIFY_BUDGET_S` prints the resolved value; an unknown name still returns 1 (unchanged). | `scripts/upgrade/lib.sh:467-476` | T-P1-1 | R-P1-3 | AC-P1-2 |
| T-P1-3 | Thread the ceremony clock into `lib.sh` via the plain-variable channel. | `promote.sh` sets `PROMOTE_CEREMONY_DEADLINE_EPOCH="$promote_deadline"` immediately after `promote.sh:262`; `lib.sh` declares it with an empty default; it is **not exported** (child scripts do not inherit it). | `scripts/upgrade/promote.sh:262`; `scripts/upgrade/lib.sh` (declaration near `:1376-1379`, the `JOURNAL_READ_STRICT` precedent) | T-P1-1 | R-P1-4 | AC-P1-3 |
| T-P1-4 | Thread the expected version into `lib.sh` the same way. | `promote.sh` sets `PROMOTE_EXPECTED_VERSION="$BIN_VERSION"` (from `promote.sh:459`); `lib.sh` declares it empty-by-default; unset ⇒ the serving check skips the version comparison. | `scripts/upgrade/promote.sh:459`; `scripts/upgrade/lib.sh` | T-P1-3 | R-P1-4 | AC-P1-4 |
| T-P1-5 | Replace the verifier's deadline with the effective-budget computation. | The verifier's deadline is `now + effective`, where `effective = CEILING` when no ceremony clock is known, else `clamp(remaining − TAIL_RESERVE, FLOOR, CEILING)`; the effective value is **never below 180**. | `scripts/upgrade/lib.sh:4751-4782` (deadline at `:4753`, loop at `:4754-4779`, failure warn at `:4780`) | T-P1-1, T-P1-3 | R-P1-1, R-P1-2 | AC-P1-5 |
| T-P1-6 | Add the serving conjunct to the verdict. | The verifier confirms only when the port is listening **and** `/livez` answers 2xx **and** (when an expected version is known) the reported version matches; the `MainPID == prestop` fast-fail is byte-unchanged and still takes exactly one poll. | `scripts/upgrade/lib.sh:4760-4773`; reuse `gate_version`'s comparator shape (`lib.sh:5991-6002`) — the serving probe must NOT reuse `gate_version` itself (it prints a `_log` on success and uses `_probe_once` without a budget); implement a local bounded probe or extend `_probe_once_classified` with an explicit budget | T-P1-4 | R-P1-5, R-P1-6 | AC-P1-6 |
| T-P1-7 | Implement the progress-gated single extension. | When the first budget expires with a new MainPID **and** a bound port, the verifier extends once by ≤ `HANDBACK_VERIFY_GRACE_S` and re-checks; with no progress it returns 1 exactly at the first deadline; the extension emits a `_warn` naming the budget in force. | `scripts/upgrade/lib.sh:4754-4781` | T-P1-5, T-P1-6 | R-P1-7 | AC-P1-7 |
| T-P1-8 | Make the failure/success messages truthful and name the budget in force. | The timeout warn names the **effective** budget (today it names `LIVEZ_BUDGET_S`, `lib.sh:4780`); the success line's claim "serves the flipped release" (`lib.sh:4891`) is backed by the version check; **no new success-path line contains the banned `systemctl start` literal** (7b substring trap, `lib.sh:4795-4800`). | `scripts/upgrade/lib.sh:4780`, `:4879-4892` | T-P1-5, T-P1-6 | R-P1-8 | AC-P1-8 |
| T-P1-9 | **[PLANNER r4 → note (h)]** Correct the C-10a arithmetic comment to a real bound table — **in BOTH copies**. | **Both** `lib.sh:157-162` **and** `promote.sh:255-261` state the true budget table (per §4.4.2), name the hand-back slice as the new knob, and record that the 900 s cap is a stop-loss (not a budgets-fit guarantee). The two copies must agree **byte-for-byte** in the corrected form (the "single source of truth" comment at `promote.sh:256-257` is only true once both are corrected). | `scripts/upgrade/lib.sh:155-163` **and** `scripts/upgrade/promote.sh:255-261` | T-P1-1 | R-P1-9 | AC-P1-9 |
| T-P1-10 | Extend the hand-back test pack with the new pins (see §9). | The pack `supervision_stop_handback` gains the slow-boot, budget-source, serving-conjunct, squeeze/floor, grace, and knob-validation legs — all discriminating (red at base, green at HEAD). | `tests/test_supervision_stop_handback.sh` (harness `:114-138` stub counters, `:174-183` lsof stub, `:664` `LIVEZ_BUDGET_S=2`, `:92-93` systemd fence, section C at `:640-714`) | T-P1-5..T-P1-8 | R-P1-10 | AC-P1-10..AC-P1-14 |
| T-P1-11 | Register the pack + mutation matrix with the house evidence trail. | `.agents/tester/PACKS.md` records the new pack run + the mutation matrix; the mutation legs are RED-proven by planting the defect, not by assertion. | `.agents/tester/PACKS.md`; `tests/packs/preflight_mutation_matrix_test.sh` (mutation-matrix convention, `:184`) | T-P1-10 | R-P1-10 | AC-P1-15 |
| **T-P1-12** **[PLANNER r2]** | Implement the **ruled** deadline-aware grace close-out (AM-P1-5): `grace = min(GRACE, max(0, remaining − effective))`; the **`remaining < FLOOR`** band emits the **distinct "ceremony time exhausted"** verdict; the `FLOOR ≤ remaining < FLOOR + TAIL_RESERVE` band emits the same distinct verdict with the tail-reserve note. | The grace never pushes `total` past `remaining` when `remaining ≥ FLOOR`; the overshoot in the `remaining < FLOOR` band is exactly `FLOOR − remaining`; the two bands are distinguishable in the log from an ordinary budget expiry. | `scripts/upgrade/lib.sh:4751-4782` (the effective-budget computation + the deadline + the failure warn at `:4780`) | T-P1-5, T-P1-7 | R-P1-13 | AC-P1-17..AC-P1-19 |

---

## 8. Risks

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| R-P1-1 | The ceremony-deadline squeeze yields an effective budget **below** today's 180 s in some path (e.g. the floor is mis-implemented or `remaining` underflows) → a NEW false negative. | **high** | The floor clamp is the mitigation and gets its own pin (AC-P1-11, mutation M4); `remaining` is computed with `_effective_ceremony_remaining_s` (`lib.sh:632-640`), which already clamps at zero. |
| R-P1-2 | The hand-back's longer wait **consumes** the ceremony's shared budget and converts a false negative into a **deadline-breach false positive** at `promote.sh:598`/`:606` (daemon up, txn open). | **high** | The squeeze (D-P1-2) bounds the hand-back's claim on the shared clock; the residual (combined-slow case) is disclosed and routed as **OQ-P1-1**; Phase 2's serving-green protection is the structural backstop. |
| R-P1-3 | A garbage env override (`HANDBACK_VERIFY_BUDGET_S=abc`) breaks the arithmetic or silently disables the bound. | medium | `_numeric_knob` (`lib.sh:109-123`) refuses non-decimal overrides loudly and falls back to the default; pinned (AC-P1-14). |
| R-P1-4 | The plain-variable channel leaks into the child scripts (`stop-ensemble.sh`) or is missing in a non-promote caller. | medium | The variables are **not exported** (mirroring `JOURNAL_READ_STRICT`, `lib.sh:1376-1379`); every consumer uses a `:-` default; pinned by the "unset ⇒ CEILING / no version comparison" legs. |
| R-P1-5 | The serving check uses `curl` where `curl` may be absent or the port may be wrong → a new false negative on a healthy daemon. | high | The degenerate branches degrade to the MainPID check (never a hard failure); `lsof` remains the necessary fast condition; the `curl`-absent leg is pinned (AC-P1-6d/e). |
| R-P1-6 | The version comparison is wrong (wrong expected value, or the livez `version` field shape differs) → a healthy daemon fails the hand-back. | high | The expected value is the manifest's `binary_version` (`promote.sh:459`) — the same value `gate_version` compares at `promote.sh:670`; the livez field is the daemon's `__version__` (`daemon/api.py:5215`); the version-mismatch leg is pinned both ways (AC-P1-6b/c). |
| R-P1-7 | The grace extension extends a genuinely-dead hand-back → slower halt, and a longer daemon-down window in the rollback/restart contexts (`rollback.sh:235`, `promote.sh:821`). | medium | The extension is progress-gated (a new MainPID **and** a bound port) and fires at most once; the no-progress leg is pinned (AC-P1-12). |
| R-P1-8 | A new log line carries the banned `systemctl start` literal → the 7b substring-trap pin goes red. | medium | No new success-path line mentions the start command; the existing assertion (`tests/test_supervision_stop_handback.sh:695`) covers it; add an explicit assert on the new lines. |
| R-P1-9 | The corrected arithmetic comment drifts from the code again (the failure mode this phase exists to end). | medium | The table is derived from named anchors; a static pin asserts the verifier's deadline line references the new knob (not `LIVEZ_BUDGET_S`) — a *structural* pin, not a numeric one. |
| R-P1-10 | The new pins are non-discriminating (green at base) → the gate proves nothing. | high | Every behavioural leg is specified to be **red at base** (base borrows `LIVEZ_BUDGET_S`=2 in the harness) and green at HEAD; each leg gets a planted-defect mutation leg (house convention). |
| R-P1-11 | The pack's 300 s cap (`ensure.md:28`) is exceeded by the added legs (the current pack runs ~110-140 s). | medium | The new legs use **short** explicit budgets (`HANDBACK_VERIFY_BUDGET_S=30`) and short serve delays (~8 s), keeping the pack well under 300 s; the budget is re-measured at the end of the run. |
| R-P1-12 | The change widens the hand-back wait in the **daemon-down** rollback/restart contexts (`rollback.sh:235`, `promote.sh:821`, `restart.sh:237`). | low | Disclosed in §4.1; the wait is an upper bound reached only when the daemon never serves; the rollback path's own timeout discipline is unchanged. |
| **R-P1-13** **[PLANNER r2]** | The **floor-wins overshoot** in the `remaining < FLOOR` band breaches the ceremony's 900 s cap ⇒ a `deadline-breach-restart` at `promote.sh:598` **with the daemon up** — the very "combined-slow" false-positive the honest-budget ruling (OQ-C-01) accepts as a **disclosed halt-for-human**. | medium | **Deliberate and disclosed** (AM-P1-5): the floor wins because a 150 s budget cannot honestly verify an 180 s-slow boot, and aborting a healthy-but-slow daemon is the worse outcome. The overshoot is **bounded** (`FLOOR − remaining` ≤ 180 s), it is **never** larger than the pre-r2 form's overrun, and the verdict is **distinct** ("ceremony time exhausted") so the post-mortem can attribute it. The bounded-arithmetic correction is the ruling's own requirement. |
| **R-P1-14** **[PLANNER r2]** | The **distinct verdict** is not implemented ⇒ a "ceremony time exhausted" abort is misread in the post-mortem as "the daemon was unhealthy" ⇒ an unnecessary rollback of a healthy release. | medium | AM-P1-5 requires the distinct wording in **both** floor-wins bands; pinned behaviourally by **M13** (reusing the ordinary wording ⇒ L13/L14 RED). |
| **R-P1-15** **[PLANNER r3]** | Phase 1's `HANDBACK_VERIFY_BUDGET_S` is **lowered** (or the daemon-side boot-grace horizon is set below it) ⇒ the hand-back verifier's patience and the Phase-2 daemon closer's boot-grace window **diverge** ⇒ the F-1 boundary defect returns in a new shape: the verifier gives up on a boot the closer still considers *in progress*, or the closer waits on a boot the verifier has already declared failed. | medium | **The derivation rule is normative in Phase 2** (`phase2-plan.md` §AM-P2-24): the closer's horizon constant **`UPGRADE_CLOSER_BOOT_GRACE_S`** (default **300**) is **≥ `HANDBACK_VERIFY_BUDGET_S`** — the two windows must cover the same measured unit-active→serving boot (tonight's ~185 s; the 210–360 s slow-boot class). The rule is **one-way**: the closer's horizon may be raised above this phase's knob, never lowered below it. **If this phase changes the knob's default, the daemon-side constant must be re-derived in the same release** (the static leg asserting `UPGRADE_CLOSER_BOOT_GRACE_S`'s default ≥ the shell knob's default rides in the Phase-3 consolidated pack). |

---

## 9. Test surface (tiered-gate doctrine)

**Tier: Tier 1 (blast-radius leg), ≤ 30 min hard, fail-loud** — `.agents/tester/rules/ensure.md:11-21`.
Rationale for the tier: the diff touches `scripts/upgrade/lib.sh`, `scripts/upgrade/promote.sh` and
`tests/test_supervision_stop_handback.sh` — **none** of which is on the mechanical core-seam list
(`daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`,
`daemon/services/instance_messaging.py`, `daemon/services/job_queue_service.py`,
`daemon/services/task_processor.py` — `ensure.md:19`). The gate is **not** promote-blocking, so the
Tier-2 trigger does not apply. **If the leader selects option (a) (§6), `daemon/tools/upgrade_journal.py`
also changes — it is still not a core seam, so the tier remains Tier 1**; the *changed-pack* leg then
additionally covers `tests/unit/tools/test_upgrade_preflight_daemon_side.py` (the `RuntimeMaxSec`
derivation expectations at `:197`/`:329`).

**File:** `tests/test_supervision_stop_handback.sh` — the registered pack
`supervision_stop_handback` / `supervision_stop_handback_unit_test`
(`.agents/tester/PACKS.md:1525`, `:1804`; invocation `timeout 300 bash tests/test_supervision_stop_handback.sh`;
today ~110-140 s).

**Harness machinery already present (reused, not rebuilt):**

- `run_rvl` sources `lib.sh` and exports `LIVEZ_BUDGET_S=2` (`tests/test_supervision_stop_handback.sh:664`)
  — this is the lever that makes the new legs discriminating.
- The `systemctl` stub's delayed-state counters `SC_MAINPID_N` / `SC_MAINPID2`
  (`tests/test_supervision_stop_handback.sh:124-138`) — the "the daemon appears only after N polls"
  seam.
- The `lsof` stub via PATH injection with an rc knob (`tests/test_supervision_stop_handback.sh:174-183`).
- The `HOST_HAS_SYSTEMD` fence → SKIP, never FAIL (`tests/test_supervision_stop_handback.sh:92-93`).
- **NEW seam required:** a PATH-injected `curl` stub for the serving probe (`lib.sh` calls bare
  `curl`, exactly like `_probe_once_classified` at `lib.sh:3894` — so a PATH stub controls it). The
  stub must be able to (i) exit 7/28 (unreachable), (ii) return a non-2xx code, (iii) return a body
  carrying `"version":"<v>"`.

**Legs (each proves exactly one thing; each is red-at-base / green-at-HEAD):**

| Leg | Name | Setup | Expectation | Proves | AC |
|-----|------|-------|-------------|--------|----|
| L1 | **Budget-source discrimination** | `LIVEZ_BUDGET_S=2` (harness default), `HANDBACK_VERIFY_BUDGET_S=30`, the daemon's port+`/livez` serve only after ~8 s | `rc=0`; the verifier did **not** stop at 2 s | The verifier no longer borrows `LIVEZ_BUDGET_S` (base: rc=1 at 2 s) | AC-P1-10 |
| L2 | **Default-budget coverage** | `LIVEZ_BUDGET_S=2`, `HANDBACK_VERIFY_BUDGET_S` **unset**, serving after ~8 s | `rc=0` | The **default** (300) covers a serving-at-8s daemon (base: rc=1) | AC-P1-10 |
| L3 | **Default value (static)** | source inspection of the declaration | the knob declares `300` | The default is 300 (a static pin — see the caveat below) | AC-P1-12 |
| L4 | **Budget expiry (fail direction)** | `HANDBACK_VERIFY_BUDGET_S=4`, the daemon never serves | `rc=1`; the timeout warn names the **effective** budget (not `LIVEZ_BUDGET_S`) | The failure path is bounded and truthful | AC-P1-11 |
| L5 | **Equal-to-prestop fast-fail preserved** | `MainPID == prestop` (the existing C3 shape) | `rc=1`; **exactly one** MainPID poll (`SC_LOG.mp.n == 1`); the fast-fail rationale text is unchanged | The fast-fail branch is untouched by the new budget/conjunct | AC-P1-11 |
| L6a | **Port up but not serving → NOT a success** | `lsof` rc=0 (port bound), `curl` rc=7 (unreachable) | `rc=1` at the deadline | The raw `lsof` conjunct alone is **not** sufficient (the incident's shape) | AC-P1-12 |
| L6b | **Serving but wrong version → fail fast** | `curl` returns 200 with `"version":"0.0.0"`, expected `0.28.5` | `rc=1`; **no** polling to the deadline (poll count unchanged) | A wrong-version daemon is rejected immediately | AC-P1-12 |
| L6c | **Serving with the right version → success** | `curl` returns 200 with the expected version | `rc=0`; the success line carries the new MainPID | The serving conjunct confirms the right release | AC-P1-12 |
| L6d | **`lsof` absent, `curl` present** | no `lsof` on PATH, `curl` 200 + right version | `rc=0` | The degraded branch uses the serving check, not the MainPID alone | AC-P1-12 |
| L6e | **`PORT` unset** | `PORT` empty | `rc=0` on the MainPID alone (unchanged degenerate branch) | The degenerate branch is preserved, not tightened to a failure | AC-P1-12 |
| L7 | **Squeeze + floor** | a ceremony deadline set with ~200 s remaining, `TAIL_RESERVE=120` ⇒ squeezed value 80 < floor 180 | the effective budget is **180** (the floor), and the warn names it | The change is monotone-safe — never less than today | AC-P1-11 |
| L8 | **Squeeze, no squeeze** | a ceremony deadline with ~800 s remaining | the effective budget is **300** (the ceiling) | The deadline-aware path grants the full budget when there is room | AC-P1-11 |
| L9 | **Grace, progress case** | budget 4 s, grace 6 s; a new MainPID + a bound port but `/livez` not yet 2xx at the first expiry; `/livez` answers at ~6 s | `rc=0`; a `_warn` names the extension | The progress-gated extension fires and recovers | AC-P1-13 |
| L10 | **Grace, no-progress case** | budget 4 s, grace 6 s; no MainPID at all | `rc=1` at ~4 s (the grace does **not** fire) | A dead daemon is not granted extra time | AC-P1-13 |
| L11 | **Knob validation** | `HANDBACK_VERIFY_BUDGET_S=abc` | the stderr line is emitted and the effective budget is the **default** 300 | `_numeric_knob`'s fail-closed behaviour carries the new knob | AC-P1-14 |
| L12 | **7b substring trap on the new lines** | the success path | the success output does **not** contain `systemctl start` | The new log lines respect the trap | AC-P1-14 |
| **L13** **[PLANNER r2]** | **Grace close-out — the tail-reserve band (`FLOOR ≤ remaining < FLOOR + TAIL_RESERVE`)** | a ceremony deadline with `remaining = 240` (⇒ `remaining − TAIL_RESERVE = 120 < FLOOR 180` ⇒ the **floor wins**); `GRACE = 60`, `TAIL_RESERVE = 120`, `FLOOR = 180` | `effective = 180` (the floor); `grace = min(60, max(0, 240 − 180)) = 60`; **`total = 240 ≤ remaining`**; the timeout/verdict line carries the **distinct "ceremony time exhausted"** wording (the tail reserve was unavailable) | The ruled grace formula never lets the grace push the total past `remaining`, and the floor-wins band is **distinguishable** in the post-mortem from an ordinary budget expiry | AC-P1-17 |
| **L14** **[PLANNER r2]** | **Grace close-out — the deep-floor band (`remaining < FLOOR`)** | a ceremony deadline with `remaining = 150` (< `FLOOR 180` ⇒ the ceremony clock cannot accommodate an honest verify at all) | `effective = 180`; `grace = min(60, max(0, 150 − 180)) = **0**`; `total = 180` ⇒ the **deliberate overshoot** is `FLOOR − remaining = 30 s` (≤ `FLOOR`); the verdict is **"ceremony time exhausted"** — **not** the ordinary "verify budget expired" wording | The overrun is bounded by `FLOOR − remaining` (the ruled form's bound), it is **disclosed**, and the post-mortem can tell "the ceremony ran out of time" from "the daemon was unhealthy" | AC-P1-18 |
| **L15** **[PLANNER r2]** | **Grace close-out — the assertion leg (`remaining ≥ FLOOR + TAIL_RESERVE`)** | a ceremony deadline with `remaining = 800` | `effective = min(800 − 120, 300) = 300`; `grace = min(60, max(0, 800 − 300)) = 60`; **`total = 360 ≤ remaining`**; the tail still retains `remaining − total = 440 s` | **`total ≤ remaining` holds in every band where `remaining ≥ FLOOR`** — the assertion leg of the ruling | AC-P1-19 |

**Mutation matrix (house convention — each leg is proved discriminating by planting the defect):**

| ID | Planted defect | Must turn RED |
|----|----------------|---------------|
| M1 | Revert the verifier's deadline to `LIVEZ_BUDGET_S` | L1, L2 |
| M2 | Drop the serving conjunct (lsof alone confirms again) | L6a |
| M3 | Make the version mismatch non-fatal | L6b |
| M4 | Remove the floor clamp | L7 |
| M5 | Remove the deadline-aware squeeze (always the ceiling) | L8 |
| M6 | Remove the grace extension | L9 |
| M7 | Make the grace unconditional (extends with no progress) | L10 |
| M8 | Change the declared default 300 → 180 | L3 (static) |
| M9 | Make the timeout warn name `LIVEZ_BUDGET_S` again | L4 |
| M10 | Add `systemctl start` to the new success line | L12 |
| **M11** **[PLANNER r2]** | Revert the grace formula to the pre-r2 form `grace = min(GRACE, remaining − TAIL_RESERVE)` | **L14** (the deep-floor band overshoots by `GRACE` instead of `FLOOR − remaining`; the distinct verdict is lost) |
| **M12** **[PLANNER r2]** | Make the grace unconditional (grant `GRACE` regardless of `remaining`) | **L13** (the total exceeds `remaining`) |
| **M13** **[PLANNER r2]** | Reuse the ordinary "verify budget expired" wording in the floor-wins bands | **L13**, **L14** (the two verdicts become indistinguishable in the post-mortem) |

**Caveat on L3 (declared).** The default *value* is pinned statically. The house has already recorded
that strings-on-table-names style static pins are weaker than behavioural ones (v0.26.0 staged
verification, method-correction M4). L3 is therefore a *complement* to L1/L2 (which are behavioural
and discriminating), not a substitute: L1/L2 prove the budget is **not** `LIVEZ_BUDGET_S`; L3 pins
the *number*. A behavioural proof of the exact default (e.g. serving at ~200 s ⇒ success) would cost
~200 s of wall-clock and exceed the pack's practical budget — rejected on cost, disclosed here.

**Pack budget.** The new legs add ≈ 10-20 s of sleeps (short explicit budgets, ~8 s serve delays).
The pack's current runtime is ~110-140 s against a 300 s cap (`ensure.md:28`) — re-measure at the end
of the run and, if the total exceeds ~240 s, split the new legs into a sibling pack
`tests/packs/handback_verify_budget_test.sh` (the `tests/packs/` convention) rather than stretching
the registered pack.

---

## 10. Success criteria

- **SC-1** A hand-back verifier that waits up to **300 s** (ceiling) — never less than today's
  180 s (floor) — instead of borrowing `LIVEZ_BUDGET_S` (`lib.sh:4753`).
- **SC-2** The verifier's confirming conjunct is "the daemon answers `/livez` with the expected
  version", with the port-listener retained as a necessary condition and the degenerate branches
  preserved.
- **SC-3** The daemon that serves at ~185 s (tonight's measured boot) is **confirmed**, not
  failed — reproducible in the pack as L1/L2 (base-red, HEAD-green).
- **SC-4** The ceremony's 900 s ceiling, its 14 `_enforce_deadline` boundaries, the auto-rollback
  semantics, the B4 open-txn policy, and Amendment #1's no-nohup rule are **byte-unchanged**.
- **SC-5** The L3 `RuntimeMaxSec` derivation and its invariant (`RuntimeMaxSec` strictly above the
  ceremony deadline) are **unchanged** under the recommended option; the option-(a) delta work is
  enumerated in §4.5 for the decisions worker.
- **SC-6** **[PLANNER r4 → note (h)]** The C-10a arithmetic comment states a **real** bound table —
  in **both** copies (`lib.sh:157-162` **and** `promote.sh:255-261`, corrected consistently) — and the
  verifier's deadline line structurally references the new knob (not `LIVEZ_BUDGET_S`).
- **SC-7** The tier-1 gate passes with **net-new reds = 0**, and every new leg is mutation-proven
  RED→GREEN.
- **SC-8** **[PLANNER r2]** The hand-back's total wait is **`≤ remaining`** in every band where the
  ceremony clock is known and `remaining ≥ FLOOR`; the only overrun is the **deliberate,
  disclosed** `FLOOR − remaining` overshoot in the `remaining < FLOOR` band, and that band carries a
  **distinct "ceremony time exhausted"** verdict so the post-mortem can distinguish it from a
  genuinely-unhealthy daemon. (L13/L14/L15 + M11/M12/M13)

---

## 11. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC-P1-1 | `HANDBACK_VERIFY_BUDGET_S` (300), `HANDBACK_VERIFY_FLOOR_S` (180), `HANDBACK_VERIFY_GRACE_S` (60) and `HANDBACK_TAIL_RESERVE_S` (120) are declared through `_numeric_knob` (`lib.sh:109-123`) at `lib.sh:179-182`, are env-overridable, and a non-decimal override falls back to the documented default with a loud stderr line. |
| AC-P1-2 | `_budget_in_force` (`lib.sh:467-476`) resolves the new budget names; unknown names still fail (unchanged). |
| AC-P1-3 | `promote.sh` sets `PROMOTE_CEREMONY_DEADLINE_EPOCH` (plain, un-exported) next to `promote.sh:262`; `lib.sh` reads it with an empty default; the child scripts do **not** inherit it. |
| AC-P1-4 | `promote.sh` sets `PROMOTE_EXPECTED_VERSION` from `BIN_VERSION` (`promote.sh:459`); unset ⇒ the serving check skips the version comparison and still confirms serving. |
| AC-P1-5 | The verifier's deadline is `now + effective`, with `effective = CEILING` (no ceremony clock) or `clamp(remaining − TAIL_RESERVE, FLOOR, CEILING)`; **`effective ≥ 180` in every path**. |
| AC-P1-6 | The confirming conjunct is (new MainPID ≠ prestop) **AND** (port listening, when `lsof` is available) **AND** (`/livez` 2xx) **AND** (version == expected, when known). Failure semantics per §4.2: transient ⇒ poll to the deadline; wrong version ⇒ fail fast; `PORT` unset / no tools ⇒ MainPID-alone (unchanged). |
| AC-P1-7 | Exactly one progress-gated extension of ≤ `HANDBACK_VERIFY_GRACE_S` fires when (and only when) a new MainPID **and** a bound port are present at the first expiry; the extension is logged. |
| AC-P1-8 | The timeout warn names the **effective** budget; no success-path line contains the banned `systemctl start` literal (`lib.sh:4795-4800`). |
| AC-P1-9 | `lib.sh:155-163` carries the corrected bound table (§4.4.2) and states that the 900 s ceiling is a stop-loss. |
| AC-P1-10 | L1 and L2 are red at base and green at HEAD; the verifier's deadline no longer references `LIVEZ_BUDGET_S`. |
| AC-P1-11 | L4, L5, L7, L8 pass: the effective budget is always ≥ 180 s, the timeout warn names it, the equal-to-prestop fast-fail still takes exactly one poll. |
| AC-P1-12 | L3, L6a, L6b, L6c, L6d, L6e pass: the default is 300; port-up-but-not-serving is not a success; a wrong version fails fast; a correct version succeeds; the `lsof`-absent and `PORT`-unset branches behave as specified. |
| AC-P1-13 | L9, L10 pass: the grace fires on progress and does **not** fire without progress. |
| AC-P1-14 | L11, L12 pass: a garbage knob override falls back to the default; the 7b substring trap holds on the new lines. |
| AC-P1-15 | The tier-1 blast-radius gate reports **net-new reds = 0**; the mutation matrix M1-M10 each turn the corresponding leg RED when planted and GREEN when restored. |
| AC-P1-16 | The pack runs within the 300 s pack cap (`ensure.md:28`) with the measured runtime recorded. |
| **AC-P1-17** **[PLANNER r2]** | The grace is `min(HANDBACK_VERIFY_GRACE_S, max(0, remaining − effective))` — **never** the pre-r2 `min(GRACE, remaining − TAIL_RESERVE)`. In the band `FLOOR ≤ remaining < FLOOR + TAIL_RESERVE` the total is `≤ remaining` and the verdict carries the distinct "ceremony time exhausted" wording. (L13, M12) |
| **AC-P1-18** **[PLANNER r2]** | In the band `remaining < FLOOR` the grace is **0**, the total is exactly `FLOOR`, the overshoot is exactly `FLOOR − remaining` (≤ `FLOOR`), and the verdict is "ceremony time exhausted" — **not** the ordinary budget-expiry wording. (L14, M11, M13) |
| **AC-P1-19** **[PLANNER r2]** | In the band `remaining ≥ FLOOR + TAIL_RESERVE` the total is `≤ remaining` (asserted, not implied). (L15) |

---

## 12. Open questions (for the decisions worker → `decisions.md`)

> Each is numbered, states the options, and carries this phase's recommendation. The decisions
> worker consolidates them; **this phase does not decide them unilaterally** except where marked.

**OQ-P1-1 — Should `PROMOTE_TOTAL_DEADLINE_S` be raised to cover the honest all-worst sum?**
Options: (a) raise the default 900 → ~1350, in lockstep in `lib.sh:163` **and**
`daemon/tools/upgrade_journal.py:2564`, re-pinning `tests/unit/tools/test_upgrade_preflight_daemon_side.py:197`
(1320) and `:329` (1620), and documenting that the operator override must live in the install `.env`;
(b) leave it at 900 and rely on the deadline-aware squeeze (this phase's recommendation);
(c) split the cap into a pre-flip and a post-flip clock.
*Recommendation:* **(b) for this phase** — the 900 s cap is a documented stop-loss (`lib.sh:158`),
the incident case is fixed with ~640 s of downstream headroom, and (a) is a leader-level policy
change with a cross-language coupling. The residual is the **combined-slow** case (pre-stop 525 s +
slow boot), where the ceremony still breaches its stop-loss at a downstream boundary with the daemon
up — pre-existing (today's all-worst sum is already 1160 s), and structurally covered by Phase 2.
**If the leader prefers (a), the extra work is fully enumerated in §4.5** and must land in the same
phase (the two defaults must move together or the L3 backstop can end up *below* the shell's own
deadline — a new failure class: exit 143 + a halt row without the `deadline-breach-` prefix,
`lib.sh:493`).

**OQ-P1-2 — Should the serving check ENFORCE the version, or merely RECORD it?**
Options: (a) enforce (this phase's design, D-P1-9): a 2xx `/livez` with the wrong version fails
fast; (b) record only — the hand-back logs the observed version and the existing `gate_version`
(`promote.sh:670`) remains the sole enforcer; (c) enforce only when an expected version is available
(this phase's design: enforce when `PROMOTE_EXPECTED_VERSION` is set, degrade otherwise).
*Recommendation:* **(c)** — it makes the hand-back's own success claim ("serves the flipped release",
`lib.sh:4891`) verifiable at the point where it is made, without forcing the expected value into the
`restart.sh`/`rollback.sh` contexts where no target release is being promoted.

**OQ-P1-3 — Should the `PORT`-unset / `lsof`-absent degenerate branch stay MainPID-alone?**
Options: (a) preserve it (this phase's recommendation); (b) tighten it to a failure when `PORT` is set
but no probe tool is available.
*Recommendation:* **(a)** — the house rule is "never fault on a missing tool / missing observation"
(`lib.sh:4744-4746`), and `PORT` is genuinely absent on some non-promote paths.

**OQ-P1-4 — Is the progress-gated grace extension (60 s) worth the extra knob?**
Options: (a) implement it (this phase's recommendation); (b) omit it and rely on the 300 s ceiling
plus Phase 2's serving-green protection.
*Recommendation:* **(a)** — it is one knob and one branch, it fires only while the daemon is visibly
coming up, and it buys ~20 % more margin for a load-sensitive boot. **If the architect declines it,
drop T-P1-7 and legs L9/L10 + mutations M6/M7; nothing else in this phase depends on it.**

**OQ-P1-5 — Should the `HANDBACK_VERIFY_*` knobs live in the install `.env` or be exported per run?**
Context: the daemon-side `RuntimeMaxSec` derivation reads the **daemon's** `os.environ`
(`upgrade_journal.py:2548-2554`, `:2564`), while the shell reads its own env (`lib.sh:163`). The
install `.env` reaches the daemon's environment because `launcher.sh`'s `load_env_file` exports every
`KEY=VALUE` (`launcher.sh:136-186`). The new hand-back knobs are read **only** by the shell, so this
question matters only if the leader picks option (a) in OQ-P1-1.
*Recommendation:* document the `.env` as the single place for ceremony-wide overrides (it is what
makes the daemon and the shell agree), and keep the hand-back knobs shell-local so a sandbox drill
can override them per invocation without touching the install.

---

## 13. Assumptions

- **A-P1-1 (ID convention).** Phase-scoped `D-P1-n` / `T-P1-n` / `AC-P1-n` / `R-P1-n` prefixes are
  used because Phases 2 and 3 are being authored concurrently; the decisions worker maps them into
  the set's global numbering.
- **A-P1-2 (measured boot).** The ~185 s unit-active → serving figure comes from tonight's incident
  (process start 21:31:00.27; the port bound after the ~21:31:10 deadline) corroborated by the
  v0.28.3 ceremony confirming at 175/180 s. It is a **measured observation**, not a distribution;
  the 300 s ceiling is sized against it (~1.6×).
- **A-P1-3 (no phase-2 dependency).** This phase assumes Phase 2 will make a serving-green release
  immune to wrongful rollback, so that a residual false negative (a host slower than any measured
  boot) degrades to "halt + defer" rather than "wrongful rollback". If Phase 2 lands differently,
  the grace extension (OQ-P1-4) becomes more important, not less.
- **A-P1-4 (no live contact).** This phase is planning-only: no code was executed, no
  `promote.sh`/`lib.sh`/`launcher.sh` was run, and no live install path was touched.


---

## [ARCHITECT r1] Amendments — 2026-11-02 (enrichment pass; all anchors re-verified in-tree at base 941cb6321)

> Appended per the append-only doctrine (OQ-C-25) — planner text above is untouched. These amendments are binding on the implementer unless the plan council overturns them. Binding rulings folded, not re-opened: owner ruling (serving-green = stay), composition requirement (both directions pinned), OQ-C-01/06/11/22/24.

### AM-P1-1 — §4.2 semantics table: two unenumerated cells (an implementer would otherwise improvise the two highest-wrong-side-potential rows)
- **NEW ROW — `PORT` set, `lsof` present, `curl` ABSENT** ⇒ verdict = MainPID + port-listener (today's verdict, unchanged; disclosed). Mirror of the already-specified `lsof`-absent + `curl`-present ⇒ serving-only row (the OQ-C-03(d) operative rule).
- **NEW CELL — `/livez` 2xx with the version field ABSENT/unparseable while the expected version is KNOWN** ⇒ **fail-fast** (definitively unverifiable as the right release; mirrors L6b's wrong-version fail-fast). NOTE the deliberate context asymmetry: the LAUNCHER probe (Phase 2 — no halt available) treats version-unavailable as NOT-GREEN ⇒ DEFER. Both directions are conservative in their own context. Extend L6b with an absent-version mutation leg.

### AM-P1-2 — non-2xx classification: ALIGN WITH THE HOUSE CLASSIFIER
The table as drafted says *all* non-2xx ⇒ transient-poll; the house classifier (`_probe_once_classified` at **`lib.sh:3874`**, its non-2xx `case` block at **`lib.sh:3899-3915`** — the plan previously cited `:3903-3911`; verified in-tree) treats 503 + curl rc 7/28 as transient but other 4xx/5xx as **red**. **[PLANNER r2 → AM-P2-22]** The classifier's taxonomy is `0` green / `1` red / `2` transient (its classifier comment at `lib.sh:3870-3873`) — Phase 1 and Phase 2's adopt path must **reuse** that taxonomy, never derive a second one. **Ruling: reuse `_probe_once_classified` verbatim** (T-P1-6 already says "extend" it) and pin the classification table. Diverging from the house taxonomy buys nothing and adds a new inconsistency.

### AM-P1-3 — the grace must be DEADLINE-AWARE
D-P1-6 as drafted is not deadline-aware: with a known clock, `total = effective + 60`; when `remaining < 300` the floor pins `effective=180` ⇒ total 240 **overruns any ceremony clock with `remaining ∈ (120, 240)`** ⇒ a guaranteed `deadline-breach-restart` at `promote.sh:598` with the daemon up — precisely the slow-ceremony window. **Preferred fix:** `grace = min(HANDBACK_VERIFY_GRACE_S, remaining − TAIL_RESERVE)` when the clock is known (monotone-safe; can only shrink). Alternative: explicit disclosure of the overrun window.

**[PLANNER r2 → AM-P1-5] THIS FORMULA IS REPLACED by the leader-ruled form.** The form above still grants a grace in the deep-floor-wins band (`remaining < FLOOR`), where `remaining − TAIL_RESERVE` is positive and the grace is added **on top of** a floor that already overran the ceremony clock — e.g. `remaining = 150` ⇒ `grace = min(60, 30) = 30` ⇒ `total = 210`, a **60 s** overrun of a 150 s budget. The ruled form (`AM-P1-5`) caps the overrun at `FLOOR − remaining` and adds the **distinct "ceremony time exhausted" verdict** this amendment only offered as an "alternative". The rest of AM-P1-3 stands (the bound arithmetic is confirmed; see the `LIVEZ_BUDGET_S` anchor note below). The rest of the bound arithmetic is **CONFIRMED** (audit: LIVEZ_BUDGET_S consumer census clean — the **note** is at `lib.sh:126-144` and the **declaration** at `lib.sh:146` (a note/declaration split; the plan previously cited one anchor for both) **[PLANNER r2 → AM-P2-22]** — only the `:4753` borrow is replaced, `gate_livez :5986` stays 180; no double-count and no gap against the gate clocks, which start only after hand-back; RuntimeMaxSec derivation reads `PROMOTE_TOTAL_DEADLINE_S` + `ENSEMBLE_PROMOTE_SOAK_S` (`upgrade_journal.py:2564/:2566`) — both untouched by the additive new names; 1320 > 900 invariant holds).

### AM-P1-4 — version form: state the verified chain (dissolves the double-strip hazard)
State explicitly in D-P1-9: `PROMOTE_EXPECTED_VERSION` = manifest `binary_version` = **bare** (`0.28.5`, byte-verified against the live v0.28.5 manifest) == `daemon.__version__` == `/livez` `version` ⇒ **Phase 1 needs NO v-strip**. Uniform rule for every NEW comparison site (Phase 2): *strip ONE leading `v` from BOTH sides at the point of comparison* (`${x#v}` — a no-op on bare values ⇒ idempotent ⇒ cannot double-strip; never `tr -d v`). Site census: launcher probe (v-prefixed target ↔ bare livez body), daemon closer (v-prefixed `in_flight.target` ↔ bare `__version__`), adopt path (same as launcher), Phase-1 verifier (bare ↔ bare — no strip).

---

## [PLANNER r2] FOLD ROUND 2 — grace arithmetic close-out (AM-P1-5) — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r2]`; **no existing task, leg, mutation, AC, SC, OQ or risk id is renumbered or deleted**. New ids are appended: **`AM-P1-5`**, **`T-P1-12`**, **`L13`/`L14`/`L15`**, **`M11`/`M12`/`M13`**, **`AC-P1-17`/`AC-P1-18`/`AC-P1-19`**, **`SC-8`**, **`R-P1-13`/`R-P1-14`**.

**Why this fold exists.** The plan council's verdict was **REJECT scoped to Phase 2** (Phases 1 + 3 approvable with folds). Phase 1's own fold item is the **grace arithmetic close-out**: the grace must be deadline-aware in the *ruled* form, the deliberate floor-wins overshoot must be **disclosed** with a **distinct verdict**, and a `total ≤ remaining` **assertion leg** must be added. Phase 1 is otherwise approvable.

### AM-P1-5 — [PLANNER r2] The grace arithmetic, closed out (replaces AM-P1-3's grace formula)

**The ruled form (normative):**

```
effective = CEILING                                  # no ceremony clock known
          | clamp(remaining − TAIL_RESERVE, FLOOR, CEILING)   # ceremony clock known
grace    = min(GRACE, max(0, remaining − effective)) # when the ceremony clock is known
total    = effective + grace
```

**The three bands (exhaustive, and each one is pinned):**

| Band | Condition | `effective` | `grace` | `total` | Verdict |
|---|---|---|---|---|---|
| **1 — room to spare** | `remaining ≥ FLOOR + TAIL_RESERVE` (300 with the defaults) | `min(remaining − TAIL_RESERVE, CEILING)` | `GRACE` (the full 60 s) | `≤ remaining` — **asserted** (L15) | ordinary "verify budget expired" wording |
| **2 — floor wins, tail unavailable** | `FLOOR ≤ remaining < FLOOR + TAIL_RESERVE` | `FLOOR` (180) | `min(GRACE, remaining − FLOOR)` | `≤ remaining` (the grace is clamped) | **DISTINCT: "ceremony time exhausted"** (+ the tail-reserve note) |
| **3 — the clock cannot accommodate an honest verify** | `remaining < FLOOR` | `FLOOR` (180) | **0** (`max(0, remaining − FLOOR)` = 0) | `= FLOOR` ⇒ the **deliberate overshoot** is exactly `FLOOR − remaining` (≤ 180 s) | **DISTINCT: "ceremony time exhausted"** (the ordinary budget-expiry wording would blame the daemon) |

**Why the distinct verdict is required (and is not cosmetic).** In band 3 the ceremony clock is **already** shorter than the honest floor ⇒ the hand-back **cannot** succeed inside the ceremony budget no matter how healthy the daemon is. The abort is therefore caused by the **ceremony's arithmetic**, not by the release. If the log says "the verify budget expired / the daemon did not answer", the post-mortem attributes the failure to a **healthy-but-slow release** — which is precisely the wrong attribution, and the one that could later justify rolling a healthy release back. ⇒ the verdict string distinguishes the two causes. **Pinned behaviourally (M13).**

**Why the ruled form is strictly better than the pre-r2 form.** The pre-r2 form (`grace = min(GRACE, remaining − TAIL_RESERVE)`) granted a grace **on top of** a floor that had already overrun the clock: at `remaining = 150` it produced `grace = 30` ⇒ `total = 210` ⇒ a **60 s** overrun of a 150 s budget. The ruled form produces `grace = 0` ⇒ `total = 180` ⇒ a **30 s** overrun. ⇒ the ruled form **halves** the worst-case overrun and makes it exactly `FLOOR − remaining`, which is **bounded by the floor** — a property the pre-r2 form did not have.

**The `total ≤ remaining` assertion (the ruling's leg).** `total ≤ remaining` holds in **every** band where `remaining ≥ FLOOR` — because `grace ≤ remaining − effective` ⇒ `total = effective + grace ≤ remaining`. In band 3 the invariant is deliberately **violated** by at most `FLOOR − remaining`; that violation is the **disclosed** part, and it is bounded by `FLOOR` (180 s) rather than by `FLOOR + GRACE`.

**Disclosure (carried into the ceremony notes and the runbook):** the combined-slow stop-loss breach (OQ-C-01's *"only (a) fixes the arithmetic"* disclosure) remains **exactly as disclosed** — the r2 fold does not raise the 900 s cap, does not change `RuntimeMaxSec`, and does not alter the 14 `_enforce_deadline` boundaries. It only (a) tightens the grace so the overrun is bounded by `FLOOR − remaining`, and (b) makes the overrun **attributable** in the log.

### Anchor reconciliation (this phase's share of AM-P2-22)

| # | Plan text said | **Correct anchor (verified in-tree at this fold)** |
|---|---|---|
| 1 | the house classifier at `lib.sh:3903-3911` | `_probe_once_classified` at **`lib.sh:3874`**; its non-2xx `case` block at **`lib.sh:3899-3915`**; its classifier comment (the busy-amnesty asymmetry-closer) at **`lib.sh:3870-3873`**; the taxonomy is `0` green / `1` red / `2` transient — **reuse, never re-derive** |
| 2 | `LIVEZ_BUDGET_S` (one anchor) | the **note** is at **`lib.sh:126-144`**; the **declaration** at **`lib.sh:146`** (`LIVEZ_BUDGET_S="$(_numeric_knob LIVEZ_BUDGET_S "${LIVEZ_BUDGET_S:-}" 180)"`) — a **note/declaration split**; consumers: the hand-back borrow at `:4753`, `gate_livez` at `:5986` |

No other Phase-1 anchor drift was found. The council's corrections to the briefing (`lib.sh:2099-2107` uname dispatch; C5 at `:279-302`) were confirmed correct and are **not** re-litigated here.

### Cross-phase note

Phase 1 remains **approvable with folds**; this fold closes its open item. The **Phase 2** fold (the council's REJECT target) lives in `phase2-plan.md` §[PLANNER r2] — the bounded absent-ceiling (**AM-P2-10**) is the single normative absent-only disposition, and the reconciled locations are listed in `decisions.md` §[PLANNER r2] and `architecture-recommendation.md` §[PLANNER r2].

---

## [PLANNER r3] FOLD ROUND 3 — the F-1 boot-grace-horizon coupling (Phase 1's share) — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r3]`; **no existing task, leg, mutation, AC, SC, OQ or risk id is renumbered or deleted**. New id appended: **`R-P1-15`**.

**Verdict context.** The plan council's delta pass returned **APPROVE-WITH-ADVISORIES**, *conditional* on folding **F-1** (one surviving 🔴 critical) plus **REV-R-1**..**REV-R-4**; **REV-R-5**/**REV-R-6** are trivial; one carried doc-hygiene item rides with this fold. The **leader confirmed the reviewer's F-1 remediation adjudication**. **Phase 1's share is a cross-phase coupling only** — Phase 1 was otherwise approvable and remains so.

**Finding-id namespace (r3).** `REV-R-1` … `REV-R-6` are the **council's delta-pass finding ids, quoted from the review report** — they are **NOT** this plan set's risk ids (this plan set's risk series is the bare `R-n`). The namespace is introduced by this fold precisely so the two cannot collide (`phase2-plan.md` already carries risks `R-1`…`R-17`).

### The r3 fold map (this phase's share)

| Finding | Where | What changed |
|---|---|---|
| **F-1 (b)** — the boot-grace horizon's derivation | **`R-P1-15`** (the risk table) | this phase's knob **`HANDBACK_VERIFY_BUDGET_S`** (default **300**, §4.1) is recorded as the **FLOOR** for Phase 2's daemon-closer boot-grace constant **`UPGRADE_CLOSER_BOOT_GRACE_S`** (default **300**): the closer's horizon must be **≥ `HANDBACK_VERIFY_BUDGET_S`**, because both windows must cover the same measured unit-active→serving boot (tonight's ~185 s; the 210–360 s slow-boot class F-1 names). The rule is **one-way** — the closer's horizon may be raised above this phase's knob, **never lowered below it**. ⇒ **if this phase changes the knob's default, the daemon-side constant must be re-derived in the same release** (the static leg asserting the two defaults' ordering rides in the Phase-3 consolidated pack) |

### What this fold does NOT change

- **No Phase-1 task, leg, mutation, acceptance criterion, success criterion or OQ is touched** — the r3 fold adds a **coupling obligation**, not an implementation change to this phase.
- The knob's **default (300)**, its **floor/ceiling/grace arithmetic** (§4.1, AM-P1-5) and the **distinct-verdict** rule are **unchanged**.
- The **900 s ceremony cap** (OQ-C-01), the `RuntimeMaxSec` reconciliation (§4.5) and the `deadline-aware` grace are **unchanged**.

### New ids appended by this fold (cross-reference)

| Kind | New ids |
|---|---|
| **Phase 1** | `R-P1-15` |
| **Cross-phase** | the daemon-side boot-grace constant **`UPGRADE_CLOSER_BOOT_GRACE_S`** (default **300**, env-overridable, **≥ `HANDBACK_VERIFY_BUDGET_S`**) — owned by Phase 2, floored by this phase |


---

## [PLANNER r4] FOLD ROUND 4 — the two phase-1 shares of the fresh approver's iteration-001 fold — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r4]`; **no existing task, pin, decision, leg, mutation, AC or SC number is renumbered or deleted**. **No new ids are introduced in this phase** — the two folds are annotations on existing rows.

**Verdict context.** The fresh approver **REJECTED iteration 001** with 4 blocking issues; **all four are doc-level folds over content that already exists correctly elsewhere in the plan set** (both verifiers concurred; the leader accepted all four). Two of the **12 carried non-blocking notes** land in this phase. Neither introduces design work.

### The r4 fold map (this phase's share)

| # | What folded | Where (this file) | What changed |
|---|---|---|---|
| **note (k)** | the hand-back's serving conjunct does **not** probe `/readyz`, and the commission's Part-1 wording reads as if it did | **§4.2 — a new leading paragraph** | one binding paragraph records the **deliberate** rejection (`D-P1-4`'s rejected alternative (ii)): the hand-back answers *"is the new binary SERVING the right version?"*, **not** *"is the new binary READY?"* — because the readiness composite is legitimately degraded during warmup (MCP warmup + auto-continue resume; tonight's measured ~3 min unit-active→serving) ⇒ a hand-back that demands `ready` would **halt-for-human on every slow-but-healthy boot** — **re-creating tonight's incident class**. The paragraph also separates the two questions explicitly: Phase 2's launcher closer *does* probe `/readyz` (a different decision-maker, a stale txn) and the two must not be conflated. Purpose: a future compliance reader must not "fix" the hand-back by adding `readyz` |
| **note (h)** | the C-10a arithmetic fiction exists in **two** copies | **§4.4.1** · **decision row `D-P1-8`** · **task row `T-P1-9`** · **success criterion `SC-6`** | the second copy — **`promote.sh:255-261`** (verified in-tree; it carries the identical `preflight 45 / stop 225 / flip 30 / handback 90 / livez 180 / readyz 120 / version 30 / soak 120 / commit 30 = 870s` line under the comment *"C-10a arithmetic … single source of truth"*) — is recorded, and **`T-P1-9` is extended to correct BOTH copies** consistently. Correcting only `lib.sh:157-162` would leave the fiction alive in the promote path and make the "single source of truth" comment false |

### In-tree re-verification performed at this fold (base `941cb6321`)

| Claim | Verdict | Evidence |
|---|---|---|
| the C-10a arithmetic block is duplicated | **CONFIRMED** | `lib.sh:157-162` (copy 1) and `promote.sh:255-261` (copy 2) — both carry the same `= 870s non-stop worst` line |
| the hand-back's serving conjunct is `/livez`-only | **CONFIRMED** | `_supervision_unit_serving`, `lib.sh:4751-4782` — the verdict is `(MainPID nonzero ∧ MainPID ≠ prestop) ∧ lsof -ti:$PORT succeeds`; the new conjuncts add `/livez` 2xx ∧ version == expected and **no** `readyz` |

### What this fold does NOT change

- The **hand-back verdict** is unchanged — same conjuncts, same fail-fast branches, same degenerate-branch preservation.
- The **knob quartet** (`HANDBACK_VERIFY_BUDGET_S` and friends) is unchanged.
- The **900 s ceremony cap** is unchanged (it remains a stop-loss; the corrected table only makes the arithmetic falsifiable).
- **No id was renumbered or deleted** in this phase.

---

## [PLANNER r5] SWEEP RECORD — the systematic claim-class sweep found **no fold required** in this document

**Fold mode:** additive (Mode 2). **Zero content changes were made to this document by the r5 fold** — and that is the finding, not an omission: this phase's scope (the hand-back verify budget) does not touch any of the eight swept claim-classes. The record below states the enumeration and **why** each hit is already correct, so that "no fold" is auditable rather than asserted — an unswept document must be distinguishable from an unexamined one.

| Claim-class | Hit sites in this file | Disposition | **Why it is already correct** |
|---|---|---|---|
| **(a)** predicate/closer restatements missing the P9/`intent_flip` authorization conjunct | **:4** (the phase-summary line) · **:66** (the scope table's *out of scope for Phase 1* row) · **:69** (the same table's never-green row + its r2 mechanism note) | **ALREADY CORRECT** | all three are **scope-boundary statements**, not closer prescriptions: `:4` names Phase 2's outcome in the three-phase summary; `:66` routes the daemon-side closer to **Phase 2** with the correct code surface (`launcher.sh`, `daemon/services/upgrade_journal_sweep.py`) — it prescribes **no** predicate set; `:69`'s r2 note already carries the **`intent_flip`** conjunct of the bounded absent-ceiling (≥3 qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed) and names **Phase 2 §AM-P2-10** as the single normative statement. **Phase 1 never restates the closer's predicate table**, so there was no site here for the r5 blocker-1 fold to touch |
| **(b)** close-before-history order claims | — | **NO HIT** | this phase does not prescribe any commit-close mutation order |
| **(c)** pre-r2 firing-rule text | — | **NO HIT** | the defer-ceiling firing rule is Phase 2's; the only "unconditional" occurrences in this file (`:550`, `:555` — *"Make the grace unconditional"*) are **Phase 1's own grace-mutation legs**, a different subject entirely |
| **(d)** history-count counter claims | — | **NO HIT** | the defer counter is Phase 2's |
| **(e)** launcher-primary precedence | — | **NO HIT** | the launcher/daemon precedence is Phase 2's (and is stated correctly there after the r5 fold) |
| **(f)** superseded timing | — | **NO HIT** | the ≈10–15 min ceiling timing and the "~59 min" correction are Phase 2's; this file's own timing is the hand-back budget family (`HANDBACK_VERIFY_BUDGET_S`), owned and unchanged |
| **(g)** unscoped adopt-parity | — | **NO HIT** | the adopt path (`adopt_stale_txn`) is Phase 2's (task `T11`) |
| **(h)** "recommended" alert entries vs REQUIRED | — | **NO HIT** | this file's one use of *"recommended"* (`:585`, *"unchanged under the recommended option"*) is an **option** recommendation in the deadline-squeeze analysis, not an alert-map entry — the alert-map requirement lives in Phase 2 (`AM-P2-8` / `AM-P2-19`) and the register (`D.7`), both **REQUIRED** |

**Boundary note (recorded, not folded).** The r5 blocker-1 finding — that the daemon-side authorization conjunct lived only in the amendments — **does not touch this phase**, because Phase 1 owns the **hand-back verifier**, not the stale-txn closer. Phase 1's own contract (the serving conjunct on the hand-back path: port-listener necessary ∧ `/livez` 2xx ∧ version match) is a **different** conjunct family from the closer's **P9** (`intent_flip` authorization) and was **not** affected by the r5 fold.

### What this fold does NOT change

- The **hand-back budget design** (the knob quartet, the deadline-aware clamp, the 900 s ceremony cap, the `RuntimeMaxSec` invariant) is unchanged.
- The **phase's scope boundary** is unchanged — the closer, the defer ceiling, the adopt path and the cooldown rider all remain Phase 2 / Phase 3 work.
- **No id was renumbered or deleted** in this document; **no id was introduced**.

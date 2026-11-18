# Phase 1: Code-fix design — promote-machinery healthy-boot false-halt

Date: 2026-11-17T19:2xZ
Author: planner[v2] via plan-creation worker (Phase 1 of 3)
Lane: `fix/promote-handback-false-halt` · worktree `/home/nea/ensemble-src-wt-hbfix` (base `0181dadbf` == `origin/latest`)
Commission: promote-machinery defect fix — healthy-boot false-halt (Emperor directive 2026-11-17 "fix it"), aftermath of
`r-20261117-171207-e2f9`. Sibling phases: **Phase 2** (d92d70fa — tests/rehearsal/negative proof), **Phase 3** (946569ab —
floor re-pin / version / staging). This phase owns **code-fix design + implementation plan only**.

Status: Draft for dispatcher review.

---

## 1. Objective

Make a **healthy boot never halt the hand-back** by fixing the serving-attribution conjunct so that port holders which are
demonstrably inside the supervised process tree are accepted, while making every bounded-probe failure journal an **honest**
rc classification and stashing the real diagnosis for the halt text.

Single testable sentence: *a unit-managed promote whose flipped release boots normally and answers `/livez` green with the
expected version completes its hand-back even when the listening socket is held by inherited-fd children of the supervised
daemon; and when a hand-back genuinely fails, the journal rows and the halt text name the truthful rc class and the actual
error.*

---

## 2. Scope

### In scope (code, `scripts/upgrade/**` only)

1. **Candidate A** — honest rc classification at the three dishonest sites (`lib.sh:6332-6339`, `:6356-6360`,
   `:6697-6703`) + adjudication of the journal event-kind question.
2. **Candidate B (load-bearing)** — serving-attribution widening: accept port holders contained in the supervised tree
   (systemd cgroup / ppid-ancestry into the owned set) with a bounded, fail-closed deferral for the residual
   "unattributable-yet" class. One gate function, so the fix covers hand-back verify **and** the post-flip gates.
3. **Candidate C** — systemd-lane diagnosis stash at four touchpoints (existing plumbing, readers unchanged).
4. **Restart-arm fix** — honest labels in the scope arm (part of A) + journal rows for the `_warn`-only exit-78 refusal
   classes in `restart.sh` (classification/dispatch visibility only).
5. **Coalescence closure note** for board item `wi-d3579d7c` #1.
6. **status.sh transient note** (T7e) — `scripts/upgrade/status.sh:125-130`/`:171-174` gain the `2 ⇒ attribution deferred (transient)` note; `--verify` must NOT fail on a transient (approver third site).
7. **Code-commit choreography** (ordered, explicit-path commits).

### Out of scope (explicit)

| Excluded | Reason |
|---|---|
| Test / rehearsal / negative-proof authoring | Phase 2 (d92d70fa) owns it. This plan only enumerates the invariants it must prove (§12). |
| Floor re-pin, version bump, tag, staging, `rollback_safe` | Phase 3 (946569ab). |
| Any edit to `daemon/**` | **Not required by this design** (§9 proves it). Flagged loudly because it would enlarge the blast radius and the rollback story. |
| `upgrade_journal_sweep.py` closer predicates P1–P9 / boot-grace coupling | The closer is the safety net that recovered the incident; touching it would be a second, unforced risk (§8). |
| Widening `_supervision_owned_pids` itself (adding MCP children) | Wrong layer: the owned set is the *argv/cwd ownership* census used by stop machinery (`scripts/stop-ensemble.sh` mirror). Widening it would change STOP behaviour (what a kill may target). Containment belongs in the attribution gate, not in the kill selector. |
| Restart-lane rewrite / arm-side `pending_op` persistence | The twins' root cause (arm never persisted) is upstream of `restart.sh`; this phase only makes the refusal *visible*. |
| Unit-arm retry-parity (root cause #3 / RC3: `_supervision_handback_unit` starts once (`:6377`) and verifies once (`:6395-6397`), vs launchd's ×2 bootstrap retry (`:6541-6557`)) | **SUBSUMED — leader ruling, review wave 4 (BL-8).** The incident halt was an *attribution refusal*, not a boot-timing miss: a bare unit-arm retry would re-encounter the SAME refused attribution and only re-halt later, inside a second start budget. The bounded re-probe the retry would have approximated already exists at the verifier level — D1's budget-bound deferral re-censuses and re-classifies inside the existing verify budget (§5.4). Retry-parity is **EXPLICITLY DEFERRED** to the T12 sibling lane (decisions.md §6 row 8; `D-P1-6`). |
| `/proc/net/tcp`, `ss`, `fuser`, `readlink /proc/<pid>/fd` | Net-new signals with no precedent in `scripts/` (grep: zero hits); the two ladders we adopt already exist in-tree. |

---

## 3. Anchor register (re-verified in THIS worktree at write time)

ANCHORS-ARE-LEADS: every row below was re-read while writing this plan, not copied from the research files.

| # | Anchor (now) | What it is | Verified |
|---|---|---|---|
| A1 | `lib.sh:4856-4882` `_run_bounded` | bounded child + `sleep/kill` watcher; **only** remap `137\|143 → 124` (`:4876-4878`) | yes |
| A2 | `lib.sh:6332-6339` reset-failed probe | no rc test at all; journals **hard-coded `rc=124`** | yes |
| A3 | `lib.sh:6356-6360` is-active preflight | `[ "$isact_rc" -ne 0 ]` → "exceeded budget" + `kind=budget … rc=$isact_rc` (incident shape) | yes |
| A4 | `lib.sh:6695-6703` `scope_arm_is_active` (inline, **not** a function; site label at `:6700`) | identical conflation; clears `_sc_isact` | yes |
| A5 | `lib.sh:6093-6133` `_handback_serving_probe` | green → `_gate_serving_attribution` → on refusal `_warn` + `return 1` (`:6103-6106`); version mismatch `:6113-6116`, absent version `:6109-6111` | yes |
| A6 | `lib.sh:6170-6298` `_supervision_unit_serving` | poll loop; callers `:6234` / `:6256` map probe rc 1 → `return 1`; budget/floor/grace arithmetic `:6175-6203`; floor-band verdicts `:6288-6297` | yes |
| A7 | `lib.sh:6317-6415` `_supervision_handback_unit` | one `start` (`:6377`), one verify (`:6395-6397`), **zero retry** | yes |
| A8 | `lib.sh:6501` `restart_via_launcher`; launchd retry `:6541-6557` (2 attempts, 2s→4s) | retry asymmetry confirmed | yes |
| A9 | `lib.sh:6921-6950` `_supervision_owned_pids` | argv-path + cwd anchoring only; **no cgroup walk, no ppid chain** | yes |
| A10 | `lib.sh:6996-7035` `_flip_verify_port_attribution` | Layer1-only census + **bounded LISTEN-grace** (D-1 precedent: "grace covers only *no listener yet*, never a wrong-process holder") | yes |
| A11 | `lib.sh:7037-7054` `_supervision_port_holders` | `lsof -ti:$PORT -sTCP:LISTEN`; rc 2 = unprobeable | yes |
| A12 | `lib.sh:7092-7105` `_supervision_pid_in_launchd_lineage` | bounded ppid walk, depth ≤ 12, single root | yes |
| A13 | `lib.sh:8004-8092` `_gate_serving_attribution` | Layer 1 ownership refusal `:8039-8047` (**runs first, unconditional**); Layer 2 launchd `:8048-8078`, unit `:8086-8088` | yes |
| A14 | `lib.sh:8100-8137` `_gate_attr_unit_lineage` | MainPID (`:8104`) + ControlGroup (`:8112`) probes; cgroup last-line substring match `:8119-8135`; **unprobeable → loud skip + rc 0** | yes |
| A15 | `lib.sh:7999-8002` `_gate_attr_unprobeable` | one-time loud degraded note (`_GATE_ATTR_WARNED`) | yes |
| A16 | `lib.sh:5592-5614` stash plumbing | `HANDBACK_LAST_ERROR` + `$$`-keyed `HANDBACK_ERROR_FILE`; readers `_handback_last_error_text` (`:5605`) | yes |
| A17 | stash **writers** `lib.sh:5634-5635`, `:5720-5721` | launchd/launchctl only — systemd lane writes **nothing** | yes |
| A18 | halt readers `promote.sh:692-699`, `restart.sh:257-261`, `rollback.sh:256-260` | already correct; no change needed | yes |
| A19 | `promote.sh:684-699` hand-back halt | `HB_ERR` + B4 leave-txn-open; `promote.sh:1051-1056` is the *rollback* hand-back halt (no `HB_ERR`) | yes |
| A20 | `restart.sh:122,136,148,155,161,165,173` | the six (plus python-floor) `_warn`-only exit-78 refusals with **no journal row** | yes |
| A21 | knobs `lib.sh:191`, `:225-237` (`HANDBACK_VERIFY_BUDGET_S=300` **exported** `:233`, FLOOR 180, GRACE 60, TAIL 120, `SYSTEMCTL_PROBE_BUDGET_S=5`, `HANDBACK_START_BUDGET_S=60`) | budget family | yes |
| A22 | C-10a bound table: the **byte-identical span is 17 lines** — `lib.sh:157-173` == `promote.sh:309-325` (anchor range corrected from the earlier loose `157-181`/`309-327` cite, REVIEW WAVE 4 fold [T5], per decisions.md CP-5/Correction (g); `lib.sh:174` is the knob line and `promote.sh:326-327` is the identity claim, outside the identical span) | must stay in sync | yes |
| A23 | frozen 7b/7c: `assert_not_contains "systemctl start"` (`tests/test_supervision_stop_handback.sh:288,768,840,862`; `tests/test_bounded_subprocess_waits.sh:377` — anchor corrected from the earlier `:376` cite, which is the adjacent *journal-surface* pin; REVIEW WAVE 4 fold [T5], per decisions.md CP-9), frozen 7c wording (`lib.sh:6748`, `:6753`) | log-text contracts | yes |
| A24 | `lib.sh:5417-5421` `_supervision_host_allows_unit` (`uname -s` FIRST, then `/run/systemd/system`) | portability gate | yes |
| A25 | test seams: `tests/test_bounded_subprocess_waits.sh:88-140` systemctl stub (**is-active exits 0 always**), `tests/test_supervision_stop_handback.sh:1576-1700` §M attribution legs, `tests/packs/handback_verify_budget_test.sh:86-122,252-270,386-392` call-counting lsof + owned-set override | the harnesses Phase 2 extends | yes |
| A26 | journal kinds: `subprocess_wait_timeout` is informational, labelled at `upgrade_tools.py:1317`; `_TERMINAL_EVENTS` (`upgrade_journal.py:2529-2557`) does **not** contain it; `nonzero_exit` exists only as a `kind=` **row token** (`lib.sh:5290`, `:5320`) | event-kind adjudication input | yes |
| A27 | closer: `upgrade_journal_sweep.py:2325`, P1–P9 `:2402-2404` (P4) / `:2442-2452` (P9), `UPGRADE_CLOSER_BOOT_GRACE_S=300` `:143`, floor `max(…, HANDBACK_VERIFY_BUDGET_S)` `:176/:2154/:2177-2178` | must not be touched / must stay satisfied | yes |
| A28 | `_log` / `_logv` / `_warn` at `lib.sh:434/450/460` | log writers available (`_log` = narrative INFO, LOG_TAG-prefixed + logfile-aware; `_logv` = verbatim body writer, no prefix — not for INFO lines) | yes |
| A29 | `lib.sh:6103-6106` the probe's gate consumer | `if ! _gate_serving_attribution "hand-back verify"` — collapses ANY nonzero rc (incl. the new transient `2`) into probe rc 1 ⇒ the fail-fast halt | yes |
| A30 | `lib.sh:8142-8162` the post-flip gates | `gate_livez`/`gate_readyz` propagate the gate rc verbatim through `&&` (`:8142-8143`); `gate_version` collapses it at `:8159` (`_gate_serving_attribution … \|\| return 1`); one-shot CONSUMERS then collapse rc 2 into a fail reason: `promote.sh:790/793` + `:799/847` (`gate_fail_reason="/livez unreachable >Ns"` / `"/readyz unreachable >Ns"` → **8b AUTO-ROLLBACK** at `:926-927`), `promote.sh:853` (`"version verify mismatch"`), `restart.sh:284-290`/`:294-300` (`GATE_FAIL="… unreachable >Ns"`), `rollback.sh:267-270`/`:272-275`/`:278` (`REGATE_FAIL`), `promote.sh:1060-1072` (re-gate) | yes |
| A31 | writers-by-channel | `_log` writes **stdout** when `ENSEMBLE_UPGRADE_LOG_FILE` is unset (`lib.sh:434-443`, the `printf … 2>/dev/null` at `:442`); `_warn` is stderr (`:460-469`); the in-`$()` stderr-INFO precedent is the verifier's budget `printf … >&2` (`:6210`, "the pid must be the ONLY thing on stdout") | yes |
| A32 | `lib.sh:8031` `owned_spaced` + `lib.sh:7111-7121` `_supervision_pid_cgroup_leaf` | `owned_spaced` is a **leaked global** (absent from `:8005`'s `local` list) — the ladder must thread it explicitly, not widen the leak; the twins-pinned `/proc/<pid>/cgroup` parse already has TWO shell copies (`_supervision_pid_cgroup_leaf`, and inline at `:8123-8126`) — R2 must reuse/factor, never mint a third | yes |

**Drift corrections carried forward** (research → actual): `_gate_serving_attribution` is `:8004-8092`, **not**
`:6921-6955` (that range is `_supervision_owned_pids`); `promote.sh:676-680` is a **comment block** — there are no
frozen/exported `SUPERVISION_*` vars; `scope_arm_is_active` is a journal **site label**, not a function. Review-wave
correction (2026-11-17, BL-1): the shorthand "`gate_livez`/`gate_readyz` have the identical `|| return 1` collapse
(:8159)" is imprecise — `:8159` is **`gate_version`'s** collapse; `gate_livez`/`gate_readyz` propagate the gate rc
verbatim through `&&` and the collapse happens at their **one-shot consumers** (`promote.sh:790/793`, `:799/847`,
`restart.sh:284-290`, `rollback.sh:267-270`, etc. — A30). Same defect class, different line; §5.4a treats all of them.

---

## 4. Candidate A — honest rc taxonomy

### 4.1 Decision: taxonomy lives at the call sites, not in `_run_bounded`

`_run_bounded` (`:4856-4882`) is the house's portable replacement for GNU `timeout(1)` and its only legitimate remap is
`137|143 → 124` (a killed child). Teaching it `systemctl` semantics would poison every other caller (`stop_via_stop_script`
`:5281-5321`, `_df_systemctl` `:7688-7702`). **Keep `_run_bounded` byte-unchanged.**

Taxonomy applied where the child rc is *interpreted*:

| rc | class | meaning | disposition |
|---|---|---|---|
| `124` | `budget` | `_run_bounded` SIGTERM→SIGKILL'd the child at the probe budget | journal `kind=budget`, existing wording **byte-identical** (pinned: `tests/test_bounded_subprocess_waits.sh:429`) |
| `3` | `is-active: not-active` | inactive **or** activating — the *expected* pre-start answer | **no journal row**; `_log` line; treat as not-active (behaviour unchanged) |
| `4` | `is-active: unknown` | systemd could not determine the state | journal `kind=nonzero_exit`, WARN |
| other ≠0 | `nonzero_exit` | genuine command failure | journal `kind=nonzero_exit`, WARN |

**Refinement vs the commission's literal instruction** ("taxonomy applied at the three sites"): rc=3 gets **no journal
row**, only a `_log` line. Rationale, stated explicitly because it is a deviation:

1. Real `systemctl is-active` returns **rc 3 for every inactive unit** — i.e. on *every* healthy hand-back. Journaling it
   would put a `subprocess_wait_timeout` row in the forensic surface of every clean ceremony. The incident already showed
   how expensive a mislabelled routine row is (it read as "timeout" to every post-mortem).
2. It would make the natural Phase-2 negative assertion ("a healthy boot journals no bounded-wait failure rows") false by
   construction unless the row were exempted anyway.
3. Honesty is preserved: the truthful statement is "not-active is the expected answer", which belongs at INFO, not in the
   failure journal. The mislabel (`kind=budget … timeout`) disappears entirely, which is the actual defect.

`kind=nonzero_exit` is **not a new token**: it already exists as a `kind=` row token at `lib.sh:5290` / `:5320`, with the
honest-branch precedent `stop_via_stop_script` (`:5281-5291` unit arm, `:5311-5321` pid arm).

### 4.2 Decision on the journal EVENT KIND (`subprocess_wait_timeout`)

**Keep the event kind unchanged; fix only the row payload.** Adjudication:

- The kind is a *bounded-subprocess outcome family* marker, already used for plain-nonzero rows (`:5289`, `:5319`). The
  incongruity is pre-existing and documented (`lib.sh:188`: "One event kind `subprocess_wait_timeout` journals every site
  for forensic correlation").
- It is **informational only**: not in `_TERMINAL_EVENTS` (A26), so it never closes a `pending_op`; no reconcile logic
  parses it.
- Renaming it (e.g. `subprocess_outcome`) would require a **daemon-side** label edit (`upgrade_tools.py:1317`), a metrics
  catalogue edit (`.agents/shared/planning/metrics-collection/metric-catalog.md:1298`), and pinned-test churn for zero
  forensic gain — precisely the blast-radius expansion this lane must avoid.
- Mitigation for the naming debt: one comment line at each changed site stating "the event kind names the bounded-wait
  *family*; the row's `kind=` names the *outcome*". Recorded as a deliberate, documented debt, not an oversight.
- **Deferral-exhaustion carrier (REVIEW WAVE 1, BL-1 — §5.4a):** the transient-exhaustion marker journals the **same
  event kind** `subprocess_wait_timeout` with a **NEW `kind=` row token** (`kind=attr_deferred`) — the A26/M-A7
  contract ("the event kind names the family; the `kind=` row token is the changed surface"). **No new EVENT kind**
  ⇒ `I-E3` and the A7 hygiene lock stay green (M-A7's plant — a new event kind — remains the red proof). The row is
  **fire-once latched** per site-invocation family (precedent `_GATE_ATTR_WARNED`, `lib.sh:7995-8002`): the verifier's
  poll loop must not mint one row per 2 s iteration.

### 4.3 Concrete edits (three sites)

1. **`lib.sh:6332-6339` (reset-failed)** — capture `sbrc` (already does), branch:
   - `124` → existing WARN + `kind=budget` row verbatim (rc stays literal `124`, now *truthfully*).
   - else → WARN `"reset-failed probe FAILED (rc=$sbrc) for $unit — continuing (best-effort)"` +
     `kind=nonzero_exit reason=child: nonzero rc=$sbrc` row. Stops the hard-coded `rc=124` lie.
2. **`lib.sh:6356-6360` (is-active preflight)** — branch on `isact_rc`:
   - `124` → existing WARN + `kind=budget` row verbatim (pinned string preserved).
   - `3` → no journal; `_log "is-active: site=<site-token> state=${isact:-unknown} rc=3 — routine pre-start answer; proceeding into the start attempt"` — **REQUIRED closure (architect §3):** the INFO line carries the **site token** (`handback_is_active` / `scope_arm_is_active` — the exact tokens `tests/test_bounded_subprocess_waits.sh:327/343/428` assert; without it post-mortem greps for those tokens find nothing), the **state**, and the **rc**; the fallback literal is `unknown` (the original `${isact:-inactive|activating}` would render the literal string `inactive|activating` when stdout is empty — CP-10); do **not** assert "expected" for the `failed` state (rc=3 covers it; wording stays factual). No banned 7b literal: the phrase is "the start attempt", not the systemctl invocation; `isact=""` (unchanged semantics).
   - `4` / other → WARN + `kind=nonzero_exit reason=child: nonzero rc=$isact_rc state=${isact:-<none>}`; `isact=""`.
   - `0` → unchanged (`isact` may be `active`).
   `isact` is the captured stdout of `is-active` (`inactive|activating|active`), so it is the natural honest state token —
   sanitize to `[a-z-]{1,20}` before journaling.
3. **`lib.sh:6695-6703` (scope arm)** — identical three-way branch; the not-active-not-journaled arm keeps `_sc_isact=""`
   and adds `_log`. The `:6704` `[ "$_sc_isact" = "active" ]` test is untouched.

Shared micro-helper (new, next to `_run_bounded` at `:4883`) to avoid three copies of the branch vocabulary:

```bash
# _bounded_rc_kind <rc>   → "budget" (124) | "nonzero_exit"
# _bounded_rc_reason <rc> <state-hint>  → "timeout(BUDGET_KNOB=<v>)" | "nonzero rc=<rc>[ state=<hint>]"
```
Both are pure string functions (no I/O) so the three call sites keep their own site label / unit / budget-knob name.

**Writer-channel note (REVIEW WAVE 1, BL-2 — `D-P1-5`):** the `_log` INFO lines this section adds at site 2 (is-active
preflight) and site 3 (scope arm) are **safe as-is** — both live in **statement context** (every caller of
`restart_via_launcher` / `_supervision_handback_unit` is `if ! …`-form: `promote.sh:684`, `:1051`, `restart.sh:251`,
`rollback.sh:249`; grep-verified — never inside a `$()` capture), so stdout is not being captured there. The rule differs
at the GATE surfaces: any INFO emitted by `_gate_serving_attribution` (accept line, transient/exhaustion marker) MUST
ride stderr via the `printf … >&2` idiom (`:6210` precedent) because gate calls ARE `$()`-captured — see §5.3 and
invariant **I-B2b**.

**Note (amended by leader ruling D2 — sites 4/5 journal symmetry):** `lib.sh:6383-6391` (start arm) and `:6747-6753`
(scope-arm start) keep their honest `=124` timeout arms **and now add symmetric `kind=nonzero_exit` rows for a genuine
non-124 rc** (WARN text unchanged; the row makes a non-timeout start failure machine-visible, same vocabulary as §4.3's
else-arms) — plus Candidate C's stash (§6).

---

## 5. Candidate B — serving-attribution widening (THE load-bearing fix)

### 5.1 Root cause, stated in code terms

`_gate_serving_attribution` (`:8004`) evaluates **Layer 1 (argv/cwd ownership, `:8021-8047`) first and unconditionally**.
Any holder outside `_supervision_owned_pids` ⇒ `rc 1`. Layer 2 (`:8048-8090`) — whose **unit arm would have accepted the
incident's holders** (children of MainPID are in the unit ControlGroup) — never runs, and the probe's green-class fail-fast
(`:6103-6106`) turns that single refusal into a halt. Two independent gaps therefore exist:

- **G1 (ordering/coverage):** the authoritative, strictly-more-permissive containment signal is only consulted *after*
  the weaker one has already refused.
- **G2 (timing):** the verdict is a single instant census (`_supervision_port_holders`, `:7037`) with no re-probe, while the
  very same codebase already learned this lesson once in `_flip_verify_port_attribution` (`:6996`, D-1: "the attribution
  requirement is RIGHT; the TIMING was the defect").

### 5.2 Adjudication of the three candidate shapes

| Option | Verdict | Rationale |
|---|---|---|
| **(i) pure gate reorder** — in unit mode run the cgroup/MainPID arm *instead of* Layer 1 | **Rejected as the primary** | Loses the zero-cost fast path (2 bounded `systemctl show` probes per gate invocation, and `gate_version` runs per soak tick). Makes the all-owned common case take a different code path ⇒ every byte-level pin about the current accept path becomes suspect. |
| **(i′) reorder as a *second accept path* (recommended)** | **ADOPTED (B-primary)** | Layer 1 remains the cheap accept; containment is attempted **only when Layer 1 found a nonmatch** — i.e. only on shapes that refuse today. Common-case behaviour, log lines and probe count are **byte-identical**; every genuine refusal survives. |
| **(ii) LISTEN-grace only** (re-poll until the holder set becomes owned) | **Adopted as a bounded residual rung (B-secondary), not as the fix** | An inherited-fd child never becomes "owned" — grace alone would burn the full verify budget and then halt. Grace is correct only for the *unattributable-yet* class (§5.4). |
| **(iii) advisory rescue** (`supervision_boot=UNIT_MANAGED` journal advisory or the in-process `journal_mark_supervision` txn stamp) | **Rejected for the verdict; accepted only as forensics** | The in-process `SUPERVISION_*` globals (`promote.sh:673-683` comment, set at the stop site `lib.sh:5202`) are already reliable and strictly better than a stamp read. The daemon advisory is emitted at boot and is **racy by contract** (may not be journaled at ~38s in) and is read by exactly one script today (`adopt-unit.sh:477-497`). Consulting it in a *safety* conjunct would add a probabilistic input to a fail-closed gate. Its information (declared mode/unit) is already in the halt template via `SUPERVISION_*`. |

### 5.3 B-primary design — the containment ladder

New predicate (per holder), fail-closed by construction:

```bash
# _gate_attr_containment <holder>
#   0 = contained in the supervised tree (accept)
#   1 = positively NOT contained (refuse — today's behaviour)
#   2 = unattributable-yet (deciding tooling unavailable/transient) -> deferral rung
```

Rungs evaluate in cost order. **Composition rule (NORMATIVE — architect closure C-1; where two earlier readings of this
section diverged, this ONE rule decides):** the **first rung that ANSWERS decides** (`0` accept / `1` refuse); only a `2`
(cannot-answer) falls through to the next rung. An answered `1` is **downgraded to `2`** when a *stronger* rung was
unanswerable — an incomplete determination is not positive foreignness (C-3 rationale: on a host with a wedged
`systemctl`, R2 unanswerable, R3's negative alone must not make the ladder *stricter* than today's check — that would
preserve the false-halt class on exactly the partially-broken hosts). Counter-mutation **M-NP1c** must redden the
downgrade's removal — **and its leg is `B-defer-d` / `B-downgrade` (Phase 2 §7.5), NOT NP-4/NP-6** (REVIEW WAVE 2, BL-3):
both NP fixtures make R2 *answerable*, so the downgrade never engages there and M-NP1c could not redden either. The
downgrade's own fixture is **R2 unanswerable** (`SC_SHOW_RC=124` or `SC_MAINPID=0`) **∧ R3 answers `1`** ⇒ expect a
**transient `2`, never a refusal**. The rule, in full:

1. **R1 — argv/cwd ownership.** Already computed by Layer 1 (`owned_spaced`). Holder ∈ set ⇒ `0`. *Zero extra cost.*
2. **R2 — systemd cgroup containment** (unit mode, Linux only, `_supervision_host_allows_unit`-gated).
   Factored out of `_gate_attr_unit_lineage` into `_gate_attr_unit_ctx` (resolves `_GA_UNIT_MAINPID` + `_GA_UNIT_CG`
   once per gate invocation, bounded by `SYSTEMCTL_PROBE_BUDGET_S` each):
   ⚠️ **SUPERSEDED — raw-substring wording replaced by the binding amendment (decisions.md §2.1 / D-P2-3; architect closure C-0).**
   The original line read "holder `= MainPID` **or** `/proc/<holder>/cgroup` last-line (after final `:`) **contains** the unit
   ControlGroup ⇒ `0`" — a raw substring that over-accepts prefix-collisions (`…/ensemble-main.service-evil.scope`).
   **THE CANONICAL FENCED SOURCE for the R2 predicate (REVIEW WAVE 2, BL-4). Every other copy in the plan set —
   `phase2-plan.md` §9.1 (NP-6), `phase2-plan.md` §9.2, `decisions.md` §2.2(7) — is a *copy of this block*, never the
   source. A table cell cannot express the predicate faithfully; copy from here, not from a cell.** The second
   disjunct is stated as a **PREFIX test** — a shell `case` pattern anchored at the LEFT of `pcg`, not a substring
   search anywhere in it:

   ```bash
   # R2 rung — segment-aware unit-ControlGroup containment
   #   pcg = holder's /proc/<pid>/cgroup last line, text after the final ':'
   #   cg  = the unit ControlGroup ("" or "/" => abstain, never decide)
   if [ -z "$cg" ] || [ "$cg" = "/" ]; then
       return 2                                   # degenerate: deciding tooling gave no cgroup
   fi
   if [ "$pcg" = "$cg" ]; then                    # (a) exact ControlGroup
       return 0
   fi
   case "$pcg" in
       "$cg"/*) return 0 ;;                       # (b) PREFIX test: extends cg at a SEGMENT boundary
       *)       return 1 ;;                       # positively not contained (subject to the C-1 downgrade)
   esac
   ```
   (Deliberately `if`/`case` rather than the terser `[ "$pcg" = "$cg" ] && return 0` compound: a standalone `A && B`
   list returns the *test's* status on the non-equal path — the right answer by luck here, the wrong habit everywhere
   else, and a `set -e` landmine in any caller that arms it. **Verified at write time:** every script in
   `scripts/upgrade/` arms `set -u` only (`lib.sh` has no `set` line at all; `promote.sh:69`, `restart.sh:63`,
   `rollback.sh:32`, `stage.sh:117`, `status.sh:26`, `adopt-unit.sh:92`, `executor_family.sh:72`, `_build_frontend.sh:37`
   = `set -u`), so this is defensive, not a live bug — but the `:4825` comment in `lib.sh` asserts "these scripts run with
   `set -e`", i.e. a future reader may reasonably assume it. **The three-valued rc must survive either way:** callers
   propagate `0/1/2` verbatim per `I-B13`/`§5.4a`, and a bare `rung-set && …` chain must never be introduced in the
   ladder's path.)

   - holder `= MainPID` accepts before either test (equality arm, unchanged by the amendment);
   - `"$cg"/*` is a **left-anchored prefix** pattern: `cg` matched literally, then `/`, then anything. It is **NOT**
     `*"$cg"*` — the raw-substring form the amendment exists to kill;
   - **canonical predicate string** — this exact string is the one every other copy in the plan set quotes verbatim
     (`decisions.md` §2.2(7), `plan-overview.md` §5, `phase2-plan.md` §9.1 + §9.2, phase1 §10 T5's acceptance cell):
     **`pcg == cg || pcg PREFIX-matches "$cg"/`** (table cells escape the pipe as `\|\|`; a degenerate `cg` of `""`/`/`
     short-circuits to abstain `2` **before** either disjunct). `PREFIX-matches` is realised
     in shell as the left-anchored `case "$pcg" in "$cg"/*)` above. The word is **PREFIX**, never `contains` — a
     substring reading anywhere in this position is the defect BL-4 exists to close. **Copy the fenced block, not a cell.**
   - `/proc/<holder>/cgroup` unreadable ⇒ the rung **abstains (`2`)**, fall through (do **not** decide `1`);
   - **degenerate guard (fold [T4], code precedent `lib.sh:8115`, twin at `:7786`)** — `cg` empty or `cg == "/"` ⇒ the
     rung **abstains (`2`)**, never accepts and never refuses. A `/`-or-empty ControlGroup means the tooling did not
     resolve a real cgroup; reading it as positive foreignness is *exactly* the false-halt class this lane removes.

   **Predicate truth table (fold [T4] — the false-halt class IS the degenerate row, so it is specified, not left implicit):**

   | `pcg` (holder) | `cg` (unit) | verdict | why it matters |
   |---|---|---|---|
   | `/system.slice/ensemble-main.service` | same | `0` accept | equality arm |
   | `/system.slice/ensemble-main.service/mcp-child.scope` | `/system.slice/ensemble-main.service` | `0` accept | **prefix** at a segment boundary — the incident's shape |
   | `/evil/system.slice/ensemble-main.service/x` | `/system.slice/ensemble-main.service` | **`1` refuse** | `cg` is **not** a left prefix of `pcg`; a *substring* implementation accepts here ⇒ the mid-path NP-6 cell reddens it |
   | `/system.slice/ensemble-main.service-evil.scope` | `/system.slice/ensemble-main.service` | **`1` refuse** | suffix-extension (architect closure C-0's stated class) |
   | *(unreadable)* | any | `2` abstain | no refusal evidence from a missing read |
   | any | `""` or `/` | `2` abstain | degenerate guard — the false-halt class |

   **Where the canonical string must be re-derived (drift tripwire, fold [T1]):** the shipped matcher is greppable —
   `grep -n 'in "\$cg"/\*' scripts/upgrade/lib.sh` must return exactly **one** hit (the `_gate_attr_containment` R2
   arm: the PREFIX form). The raw-substring form `*"$cg"*` exists **three times at base**, all deliberately outside the
   ladder's path and all kept byte-identical: `lib.sh:7803` and `:7827` (the `supervision DUAL_FIGHT` census, checks A
   and B) and `:8128` (Layer 2 `_gate_attr_unit_lineage`, decisions.md §2.2(5)); `:7637` is a comment describing them.
   So the assertion is **not** "zero substring hits in the file" — it is "**no substring hit inside
   `_gate_attr_containment`**" (`grep` the function body, not the file). An implementation that drifts back to
   `*"$cg"*` inside the ladder puts a fourth arm *in the new function* and reddens; the legacy three must stay green
   untouched. Leg-ised as a source-grep assertion in `phase2-plan.md` §9.1 (NP-6).
   This is the rung that would have accepted `1191207/1191605`. It is **Layer 2's containment *idea*, tightened**: the
   legacy Layer 2 still runs a raw substring (`lib.sh:8128`) and keeps it **byte-identical** (decisions.md §2.2(5)) because
   the common path must not change (`I-B2`/`B-order`); the ladder's R2 is the *segment-aware* form, which is strictly
   narrower — so a rung-level accept can never be wider than what Layer 2 would have granted on the same evidence. (A
   ladder `1` refuses before Layer 2; ladder-accepted holders skip the Layer-2 re-test — closure C-2, below.)
   *Parse reuse (review wave 1 fold, A32):* the `/proc/<holder>/cgroup` read is the twins-pinned parse — it already exists
   twice in shell (`_supervision_pid_cgroup_leaf` `:7111-7121`, inline at `:8123-8126`). R2 factors the read (tail -n 1 +
   strip the leading `<id>:` prefix) so `_gate_attr_unit_ctx` and the ladder share ONE parse helper with two projections
   (the helper's leaf basename; R2's full last-line path) — a **third copy must not be minted**.
3. **R3 — ppid ancestry into the owned set** (portable: POSIX `ps`, so it also serves Darwin/launchd and nohup/script
   shapes). New `_supervision_pid_ancestor_of <pid> <roots-spaced>`: bounded walk (depth 12, same shape as
   `_supervision_pid_in_launchd_lineage` `:7092-7105`) testing membership in the *owned set* (not a single root).
   Reachable ⇒ `0`. A reparented orphan's walk **completes** (terminates at pid 1) without entering the owned set ⇒
   answered `1` — stated explicitly (architect closure C-5; pid reuse is judged on the *current* occupant, so no false
   accept is reachable). `command -v ps` absent ⇒ `2` (a missing tool is never refusal evidence — the `_gate_attr_unprobeable` contract's precedent, `lib.sh:7985-7988`; anchor corrected from the earlier `:801` cite, REVIEW WAVE 4 fold [T5]).
4. **R4 — launchd job lineage** (Darwin): if `SUPERVISION_MODE=launchd` and `SUPERVISION_UNIT` is set, resolve the job
   pid (`_supervision_launchd_job_pid`, bounded). **Empty pid while a holder serves ⇒ `1`** (the v0.30.3 dual-supervisor
   shape — must keep refusing, pinned by `tests/test_supervision_stop_handback.sh` M7). Probe failure ⇒ fall through to
   R3 (which on Darwin is the only containment anyway).
5. **Exhausted** — the first answered `1` (not downgraded) ⇒ `1`; all rungs answered `0` ⇒ `0`; otherwise all remaining
   holders are `2` ⇒ `2` (deferral rung, §5.4).

Then the gate's Layer-1 block becomes:  - `nonmatch` empty → **unchanged, byte-identical** (no new probes).
- `nonmatch` non-empty → classify each holder with the ladder:
  - **any `1`** → refuse with **today's detail prefix preserved byte-for-byte**
    (`"port-holder(s):${nonmatch} NOT in the supervision-owned pid set (owned:${owned_spaced:-<none>}) — the :${PORT} answerer is not this install's daemon"`) plus a suffix naming the ladder verdict
    (e.g. `; containment ladder: outside unit 'X' cgroup and no ancestor in the owned set`) — the *prefix* is what the
    M-section pins assert (`M1`/`M3`: `"999999 NOT in the supervision-owned pid set"`, `owned:<none>`), so those legs stay green.
  - **all `0`** → INFO **on STDERR** (REVIEW WAVE 1, BL-2 — invariant `I-B2b`), e.g.
    `attribution [<what>]: ACCEPTED — port-holder(s):X contained in unit 'Y' (cgroup containment; the argv-anchored owned set does not cover them)`
    emitted with the `printf '%s[%s]: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "…" >&2` idiom (the `:6210` budget-printf
    precedent — A31). **NOT `_log`**: `_log` writes **stdout** when `ENSEMBLE_UPGRADE_LOG_FILE` is unset (`:434-443`,
    the `printf … 2>/dev/null` at `:442`), and every gate call runs inside a `$()` capture — `LIVEZ_JSON="$(gate_livez)"`
    (`promote.sh:790`), `READYZ_JSON="$(gate_readyz)"` (`:799`), `newpid="$(_supervision_unit_serving …)"` (`:6395`) —
    where the captured payload must be the ONLY stdout content (the captured pid a single numeric token; the JSON body
    unpolluted). The refusal arm already rides `_warn` (stderr, `:460-469`) — unchanged. The same stderr rule binds the
    gate's transient/exhaustion marker (§5.4).
    The accept path must also **skip Layer 2's unit-arm re-test for the holders the ladder already accepted** (architect
    closure C-2 — Layer 2
    is refusal-only and can never accept; re-testing an accepted holder risks a silent false veto in the mixed case where
    unit probes resolve but the holder's `/proc/<pid>/cgroup` is unreadable. This corrects the earlier rationale "when
    nonmatch is empty every holder is owned", now false on the ladder path — decisions.md §2.2(5) as amended by C-2).
    Must avoid the banned 7b literal (A23): the wording above contains no `systemctl start`.
  - **no `1`, at least one `2`** → deferral rung (§5.4).

**Adjudication of record (architect §1.3, folded):** on the incident host the survival path is **R2** (the MCP children
are in the unit cgroup), not R3; there was **no systemctl wedge** — fixture-log lines 20 and 21 carry the same second, so
"exceeded budget (5s)" was the mislabel wrapping a **fast rc=3 return**. The C-1 downgrade clause is robustness for the
genuinely-wedged host class (which the WARN path proves exists), not the incident replay.

### 5.4 B-secondary — bounded deferral for the residual unattributable class

Where it lives: **inside `_gate_serving_attribution`**, i.e. the single chokepoint shared by `gate_livez`, `gate_readyz`,
`gate_version`(via soak) and `_handback_serving_probe`. Not in the probe, not in the verifier: those two already have their
own polling loops and would double-count patience, and the gate is the only place that owns the classification.

Shape — **as amended by architect §2 (containment spec, REQUIRED) + leader rulings D1/D3; supersedes the original
"process-global 5 s pool" wording, whose arithmetic (pool=5 × `sleep 2` = **10 s**, not "+5 s" — the pool counted
iterations, not seconds) and scope ("process-global" is false at `$( )` call sites: `promote.sh:790/799/1060/1065`,
`rollback.sh:267/272`, `restart.sh:284/294` each fork a fresh pool) were both wrong:**
- **per-shell** patience pool `_GATE_ATTR_DEFER_REMAIN` initialised at source time from `_GATE_ATTR_DEFER_S` (default **5**);
  **not an exported production knob** (D-P1-2 as amended by D3 — the letter changes from "not a `_numeric_knob`" to:
  *integer-parse via the house `_numeric_knob` router* (loud-garbage note) *plus a mandatory* **clamp to `[0,5]`** — a
  leaked `300` becomes `5`); **count the pool in seconds** (decrement by slept duration ⇒ the bound is honestly ≤5 s);
  plain shell var initialized per shell; **never exported**; GNU/BSD-safe (integer arithmetic only);
- **dual-key test marker (adopted — architect §2 recommended item 12):** honour a non-default override **only** when the
  explicit test marker env `ENSEMBLE_TEST_GATE_DEFER=1` is also present; absent ⇒ always 5 — closes the false-halt
  direction of a leaked `0` too; no planned test needs a value >5 ⇒ the ceiling costs zero testability;
- explicit loop guard `while [ "$_GATE_ATTR_DEFER_REMAIN" -gt 0 ]` (kills any `until`-form infinite loop); `sleep 2`,
  decrement by slept seconds, re-census + re-classify; stop on `0`/`1`;
- **exhaustion semantics (leader ruling D1 — transient→budget-bound; SUPERSEDES the original terminal fail-fast):**
  pool exhausted with the holder still `2` ⇒ the gate returns **2 (transient)** with a **distinct journaled exhaustion
  marker** (`… containment UNATTRIBUTABLE within ${spent}s (unit MainPID/ControlGroup unresolvable, ps unavailable) —
  deferring to the verify budget` — journaled per §4.2's carrier: event kind `subprocess_wait_timeout`, row token
  `kind=attr_deferred`, fire-once latched `_GATE_ATTR_WARNED`-style, WARN text on stderr per I-B2b);
  poll-loop consumers keep polling **within their existing budgets** — and "poll-loop consumers" means exactly **ONE**
  call site, `_handback_serving_probe`'s verifier loop: the other four consumer surfaces are one-shot and collapse any
  nonzero rc today (REVIEW WAVE 1, BL-1 — the probe's own `if !` consumer at `:6103-6106` is one of them; the full
  consumer contract is §5.4a and tasks **T7b/T7c**). Refusal and halt happen **only at budget end** through the normal
  floor-wins verdict path (`:6288-6297`), with the exhaustion detail carried into `HB_VERIFY_REASON` (Candidate C,
  §6 C-5). Terminal fail-fast exhaustion
  is **REJECTED** — it re-conflates exactly the transient/terminal classes the ladder separates (a boot attributable at
  t+8 s would halt with ~290 s of verify budget unspent). Cost of the ruling: a never-attributable pathological boot burns
  the full verify budget before halting — bounded, rare, and every safety property holds. **Bound semantics of record
  (leader D1): attribution acceptance is EVIDENCE-ONLY (a rung's answered `0`); the deferral is a BUDGET-BOUNDED DELAY
  that can NEVER accept; the version conjunct is ABSOLUTE and outside the widening.**

  **Per-surface restatement (REVIEW WAVE 4, BL-9 — leader ruling, supersedes the surface-free reading of the last
  clause): the "absolute" conjunct holds exactly where it is ARMED.** On the promote surface it is armed unconditionally
  (`PROMOTE_EXPECTED_VERSION="$BIN_VERSION"`, `promote.sh:546-556` — anchor corrected from the earlier `:541-544` cite;
  `:541` is the manifest read and `:542-544` the refuse-on-empty; the arming statement itself is `:556` under the
  `D-P1-9` comment block `:546-555`). On the **restart/rollback surfaces the conjunct is EMPTY today** — promote.sh's own
  comment states it: *"Unset contexts (restart.sh / rollback.sh) read an empty default in lib.sh ⇒ the serving check
  degrades to '2xx, no version comparison'"* (`lib.sh:6107` keys the conjunct on `${PROMOTE_EXPECTED_VERSION:-}`; the
  default is empty at `lib.sh:1593`) — so there a ladder-accepted stale incarnation (the P12-sibling shape) would degrade
  to a 2xx-only verified pass. **Leader ruling — OPTION (A): ARM, do NOT refuse-to-widen.** Refusing to widen where the
  conjunct is absent would keep the false-halt alive on 2 of the 3 surfaces; arming the expected version from the TARGET
  release manifest before the shared gate (tasks **T13** restart.sh / **T14** rollback.sh, §10) makes the bound hold
  per-surface and keeps the widening safe everywhere. The negative proof is **NP-12 + M-NP12** (phase2 §9.1/§9.2); the
  risk register row with owner is §12 **R6**; register row `D-P1-7`.
- **sleep/pool semantics only (re-scoped — REVIEW WAVE 1, BL-1):** the original bullet claimed "`_gate_serving_attribution`
  is called as a statement everywhere the sleep matters … which is fine". That claim is RE-SCOPED to the **sleep/pool
  inheritance alone** (a `$( )` call site forks a fresh per-shell pool and merely inherits the ≤5 s sleep — that part is
  fine and unchanged). It must NOT be read as licensing the **rc-consumption form**: the probe's `if !
  _gate_serving_attribution` consumer (`:6103-6106`) captures rc and collapses `2` into a refusal — T7b reworks it; the
  one-shot gate consumers collapse `2` into a fail reason — T7c reworks them (§5.4a).

Why pooled rather than per-call: the verifier calls the gate once per 2 s poll iteration; a per-call 5 s defer would let a
pathological "never attributable" boot consume `300 s` in ~25 iterations instead of ~150 (a 6× slowdown of a halt we must
still issue). A **per-shell pool** bounds the patience to ≤5 s per shell, independent of iteration count.

**Budget/ceremony impact:** zero numeric change to any knob (A21 untouched) ⇒ the exported-knob coupling
(`HANDBACK_VERIFY_BUDGET_S` → closer grace floor, `upgrade_journal_sweep.py:176/:2154/:2177-2178`) stays satisfied with
**no daemon change**. **Honest worst case (I-B8 as corrected by architect §2):** ≤5 s per shell that invokes the gate;
across the promote flow's separate `$( )` call sites the theoretical worst case is ≈ +20 s total — still inside every
existing budget. C-10a sync (A22): both copies get the *same* one-line comment stating the honest sub-budget
(`≤5 s per shell deferral pool`) — **byte-identical** across the 17-line span (`lib.sh:157-173` == `promote.sh:309-325`; anchor range corrected from the earlier loose cite, REVIEW WAVE 4 fold [T5]); no numeric edit; B10
(Phase 2 §7.7) is the sync pin.

### 5.4a — rc=2 consumer contract re-cut to the helper family (R2 §2.1–2.3)

(R2 supersedes the iteration-001/002 carrier design; see architecture-recommendation-r2.md. Corrected premise: B4 leave-txn-open exists ONLY at hand-back (promote.sh:684-703, restart.sh:251-263, rollback.sh:249-263), UPSTREAM of every post-flip gate consumer — the carrier must carry its own terminal arms.)

**Helper family (single writer, single reader).** Two symbols in `lib.sh`; no other reader/writer of any carrier anywhere:
- `_gate_consume <surface> <rc> <label>` — called as a STATEMENT immediately after each one-shot gate call (never inside `$( )` — its write is a shell assignment; census pin enforces zero `$( _gate_consume`). rc=0 no-op; rc=1 sets the surface's failure carrier (`gate_fail_reason`/`GATE_FAIL`/`REGATE_FAIL`) with today's byte-exact string, return 1; rc=2 sets `gate_defer_reason` with transient wording, failure carrier UNTOUCHED, writes ONE `halt` row per run detail-prefixed `gate-defer:` (fire-once latch = the carrier itself), `_warn` per tick, return 2; unknown rc = contract violation treated as 2 + warn row `gate-contract violation: …`.
- `_gate_outcome <surface>` — echoes `ok | fail | defer`. Every guard/success/logbook branch reads THIS token, never a carrier. This makes the omitted-site class structurally impossible at the read side.

**Canonical carrier = `gate_defer_reason` (single name).** The three per-surface aliases are STRUCK (struck/marked-superseded mentions only). Read/written exclusively inside the helper family via `${gate_defer_reason:-}` forms ⇒ set -u-safe by construction (closes approver note 2 and D's B1a crash sharpening with zero per-script initializers).

**Wording classes (closed vocabularies):** definitive labels (`/livez unreachable >Ns`, `version verify mismatch`, `soak failure`, …) go ONLY into failure carriers, byte-preserved. Transient labels (`livez/readyz/version verify/soak: attribution deferred (transient — budget expired)`) go ONLY into `gate_defer_reason` + the `gate-defer:` halt detail. Never cross-contaminated.

**Deliberate asymmetry (recorded as a decision, not an accident):** `_probe`'s budget-exhaustion `2` ("nobody answered") is NORMALIZED to `1` at livez/readyz — no answer at all is a health verdict; green-but-unattributable is not. Only the second gets the never-rollback contract. Blast radius stays on the attribution class this lane exists for; every existing rc=1 pin stays green.

### 5.4a-1 — Post-defer gate-chain semantics (ruling D, NORMATIVE)

Three rules, applied in this precedence order on the same read: **fail > defer > ok** (a definitive failure is never downgraded by a defer carrier; supersession per rule 2):

1. **DEFINITIVE-FAILURE PRECEDENCE (no-defer-over-fail):** if any gate in a cycle answers definitive failure, the cycle outcome is `fail`. A defer carrier must NEVER downgrade a definitive failure — the defer token cannot veto a `fail`. Motivating code: promote.sh:799/:852/:861 later gates run under `[ -z "$gate_fail_reason" ]`; :924-926. Phase-2 leg: **B-chain-1**.
2. **LATER-GREEN SUPERSESSION:** within a verify cycle, a defer minted at an earlier gate does NOT veto a later attributed ∧ version-correct green at the same cycle's terminal read. Acceptance still requires a definitive `ok` at the consuming read (version conjunct included). Phase-2 leg: **B-chain-2**.
3. **CONSUME CLEARS:** `_gate_consume` clears the carrier after consumption — the carrier never leaks across stages/cycles. Stale-carrier reads must not survive a consume. Motivating code: promote.sh:1059-1072 stale-carrier reads. Phase-2 leg: **B-chain-3**.

These three rules close the sticky-carrier false-halt and defer-downgrades-fail gaps: a defer minted at an early gate can no longer (a) turn a definitive failure into a transient halt with the txn left open and no 8b auto-rollback, nor (b) veto a run whose readyz/version/soak all answered attributed ∧ version-correct at the terminal read.

### 5.4b — the 24-row per-surface disposition table (R2 §2.3; THE deliverable — §9/C3 copy verbatim)

Legend: OS=one-shot, PL=poll; OPEN-C=left open WITH a named converging closer; CLOSED-D=closed at the surface with a distinct non-success outcome.

| # | Site (file:line @ 4a3c26e28) | kind | Carrier-present outcome (exact branch) | Txn end-state | Recorded (existing primitives only) | Closer? (named) | Phase-2 leg |
|---|---|---|---|---|---|---|---|
| 1 | lib.sh:8004 exhaustion arm (new) | producer | mints rc 2 once | n/a | subprocess_wait_timeout row, kind=attr_deferred, fire-once, stderr | n/a | B-contract |
| 2 | lib.sh:8142-8143, :8159 | wrapper | :8159 collapse REMOVED → rc capture; livez/readyz normalize probe-2→1 | n/a | none | n/a | B-contract + M-gate-collapse |
| 3 | lib.sh:~6100-6106 _handback_serving_probe | PL | 2→probe return 2, keep polling (T7b) | n/a | none | verifier budget | B-defer-e |
| 4 | promote.sh:790-794, :799+:846-848, :852-854, :861-865 | OS ×4 | _gate_consume promote "$rc" <label> | n/a | gate_defer_reason ← transient class | n/a | B-gate-1 |
| 5 | promote.sh:873 (8a test) | read | guard = _gate_outcome promote = ok — defer cannot enter commit | OPEN-C | none | YES — daemon serving-green closer (P1-P9, code :2402/:2442), intent_flip emitter promote.sh:574; (launcher boot twin launcher.sh:3093 is UNCONFIRMED — the verified closer is the daemon sweep at :2402/:2442; narrow accordingly) | B-gate-1 |
| 6 | NEW promote defer arm (between :924 and :926) | OS terminal | halt "gate-defer: …" + lock_release + exit 1; not 8b (D4's new site). PRECONDITION-GATED (ruling C): the leave-open (OPEN-C) disposition is permitted ONLY when the closer preconditions hold — (i) displaced_version non-null (journal.current non-empty at txn-open) AND (ii) the closer kill-switch not disabling it (upgrade_journal_sweep.py:178-193; launcher.sh:131). When preconditions do NOT hold, the defer arm takes the CLOSE-DISTINCT path (halt + close with the defer outcome — the restart/rollback shape). Never-strand is absolute | OPEN-C (preconditions hold) / CLOSED-D (preconditions fail) | halt row, transient class | YES (row 5) — preconditions: B-gate-1c (hold → OPEN-C), B-gate-1d (fail → CLOSE-D) | B-gate-1 (+ B-gate-1c, B-gate-1d) |
| 7 | promote.sh:926-927 (8b entry) | read | reached on fail only | n/a | GATE FAILED: … unchanged | n/a | B-gate-1 |
| 8 | promote.sh:1059-1063, :1065-1069, :1070-1072 (rollback-via-promote re-gate) | OS ×3 | _gate_consume promote | n/a | as row 4 | n/a | B-gate-4 |
| 9 | promote.sh:1084-1089 8b bookkeeping | read | skipped when = defer | CLOSED-D | no rollback event, no counter, no quarantine | n/a | B-gate-4 |
| 10 | promote.sh:1094-1101 REGATE halt | read | fail only | CLOSED-D | unchanged | n/a | B-gate-4 |
| 11 | restart.sh:284-290, :292-304, :321-338 (D2's site now in scope) | OS ×3 | _gate_consume restart "$rc" <label> | n/a | as row 4 | n/a | B-gate-2 |
| 12 | restart.sh:345-361 GATE_FAIL branch | read | fail only | CLOSED-D | existing restart_gate+halt unchanged | n/a | B-gate-2 |
| 13 | restart.sh:363-374 success terminal | read | ok only — guard added before it | n/a | restart row only on ok | n/a | B-gate-2 |
| 14 | NEW restart defer arm (between :361 and :363) | OS terminal | restart_gate … outcome=not-verified (transient) + journal_close_txn + clear pending_op/pending_restart + exit 1; no restart row | CLOSED-D | restart_gate (existing RESTART_TERMINAL_EVENTS member) + close_txn — existing primitives | NO usable — P4 :2402, launcher :3355, reclaim :561 all exclude restart; the two arm closers are pending_op-conditional (D6) ⇒ close at the surface is REQUIRED | B-gate-2 |
| 15 | rollback.sh:267-271, :272-276, :277-279 | OS ×3 | _gate_consume rollback "$rc" <label> | n/a | as row 4 | n/a | B-gate-3 |
| 16 | NEW rollback defer arm (before :286) | OS terminal | journal_set_current "$TO_VERSION" + set_previous (symlink FACTS only — flip already happened), no journal_count_rollback (:288), journal_close_txn, halt "gate-defer: … NOT VERIFIED (transient)", exit per the script's existing REGATE halt code | CLOSED-D | halt row + close_txn; no rollback event (:298), no cap count | MUST NOT leave open — D3: the closer would commit-close it wrongly (previous=displaced_version = the M4 strand) | B-gate-3 |
| 17 | rollback.sh:286-289 bookkeeping, :291-296 re-gate halt, :298 landed row | read | bookkeeping/landed reached on ok/fail only (defer hoisted above) | CLOSED-D | unchanged | n/a | B-gate-3 |
| 18 | lib.sh:8294, :8383-8387 soak ticks (D1's site — NOW WIRED) | PL | 2 ⇒ observation_kind="INCONCLUSIVE"; per-tick pool reset | n/a | no row; _log per tick | n/a | B-soak-t |
| 19 | lib.sh:8491-8498 cap-reach | terminal | return 2 when inconclusive>0 ∧ no definitive red; else return 1 (C-1 anti-silent-commit preserved) | n/a | wording per §2.4 | n/a | B-soak-t |
| 20 | status.sh:127-130, :171-174 | PL diag | 2 ⇒ attribution deferred (transient) note; --verify must NOT fail on a transient | n/a | none | n/a | B-status |
| 21 | promote.sh:725 | init (OS) | EXEMPT — canonical initialization site: writes empty; documented as part of the helper family's contract (the init is the family's own seed, not a carrier read) | n/a | none | n/a | B-census |
| 22 | promote.sh:734, :737, :761 | OS ×3 (launchd flip-verify) | OUT-OF-LANE EXEMPTION — pinned behavior = fail-preserving: a set carrier does NOT alter the launchd arm's existing fail treatment; the current code path is unchanged. Registers the residual "launchd/BSD defer semantics undefined, out of lane, host is Linux" (ties to the T12 ceremony-disclosure note) | n/a | (unchanged fail path) | n/a | B-census (T12 disclosure) |
| 23 | promote.sh:983, :993, :1003, :1014 | read ×4 | EXEMPT — RENDER-ONLY reads: reads that cannot change an outcome, only output text (8b halt-detail rendering). Census rule: render-only reads are exempt; any read that feeds a control decision is NOT render-only and must route through the family | n/a | (render only) | n/a | B-census |
| 24 | promote.sh:1059-1072 (8b re-gate surface) | OS terminal (NEW defer arm) | on a transient at the 8b re-gate, CLOSE-DISTINCT: `halt "gate-defer: … NOT VERIFIED"` + `journal_close_txn` with the distinct defer outcome (NO rollback-complete label, NO rollback event, NO counter, NO quarantine) + `exit 1` — taken BEFORE the NEW_COUNT logic at :1105 so nothing is left unbound under set -u and no false label is minted. Row-16 symmetry now restored | CLOSED-D | halt row (transient wording) + close_txn; no rollback event, no counter, no quarantine | MUST NOT leave open (same as row 16) | B-gate-4b |

**Rows-6/14/16 asymmetry, justified:** promote leaves OPEN because current legitimately lags the flipped symlink and the P9-authorized closer + launcher kill-window heal resolve exactly that divergence. Restart CLOSES at the surface (no usable closer; D6 wedge hazard). Rollback CLOSES at the surface recording the symlink fact while withholding every success claim ("fact in the field, no claim in the history") — the alternative (stale current) creates the journal-vs-symlink divergence the promote preflight refuses on (promote.sh:513-517), and leaving open feeds D3's wrong-convergence.

**Census scoping rule (ruling A):** render-only reads are exempt; any read that feeds a control decision is NOT render-only and must route through the family. The census pin (T7g) counts ONLY sites inside §9.1's declared edit scope; out-of-scope sites carry table rows/exemptions (rows 21-23) rather than reddening the pin. Rows 1-20 are unchanged; rows 21-24 are the cure-pass additions.

**R-3 / adaptive-soak pin collision (ruling E):** the probe-2→1 remap (row 2) is an R-3 behavior change authored BEFORE T5. `tests/test_adaptive_soak.sh:626-637` (the C2c pin asserts rc 2 "bounded ride-out still fails") updates in the SAME commit as the lib.sh row-2 change — a sanctioned pin update, mutation-noted, ADDED to C3's commit scoping (C3 carries the test pin update alongside the lib.sh change). T7g's census artifact is a `tests/**` file (note 11). The §11.2 merge-gate red is thus IN-PLAN, not drift.


**Duplicate-halt single-row norm (ruling A note):** one halt row per defer arm; the fire-once latch governs the exhaustion arm (row 1) only — reconcile §2.2/§5.4b wording with rows 6/14/16/24's terminal arms (each terminal arm emits its own halt row; the latch does not gate them).


### 5.4c — soak semantics (R2 §2.4)
- Inconclusive rule = STREAK-FREEZE: a transient tick neither increments nor resets consecutive_green (observation_kind="INCONCLUSIVE" — a THIRD class, distinct from probe-transient RESET, which is preserved). Reset-to-0 would let a transient storm reach cap with zero greens; increment would manufacture greens from unattributable observations.
- Pool arithmetic = PER-TICK FRESH (_GATE_ATTR_DEFER_REMAIN reset at the top of each soak tick's version check, before :8383 and :8294). Soak cap UNCHANGED: honest cost +≤5 s/tick, ≤6 ticks in the 120 s adaptive cap ⇒ ≤ +30 s and −2 green opportunities worst case; caller's _enforce_deadline converts any residual overrun into a halt — never a commit, never an auto-rollback. Per-shell pool remains the rule everywhere else.
- Dispositions: all-transient or mixed (greens>0 but <N, inconclusive>0) at cap-reach ⇒ rc 2 → consumed by promote.sh:864 via _gate_consume → row-6 defer arm — 8b never reached. Genuine never-green (inconclusive==0) ⇒ rc 1 unchanged. Any definitive red still returns 1 immediately.

### 5.4d — missed-site fail-loud enforcement (R2 §2.5, three layers, all required)
1. Behavioural per-surface legs (one per mutating surface; persistent-2 fixture, REAL child pids; assert txn end-state per §5.4b incl. no-commit/no-current-write/no-8b-entry/no-counter-bump). Counter-mutations: restore the direct collapse at that site ⇒ leg reddens.
2. Generated census pin (presence-grep, labelled as such): at test time, grep the tree for the gate-family symbols; every hit must be (a) inside lib.sh wrappers, (b) status.sh, or (c) followed within 8 lines by _gate_consume/_gate_outcome. GENERATED, never a hardcoded list. Second pin: zero direct carrier reads (gate_fail_reason|GATE_FAIL|REGATE_FAIL|gate_defer_reason, both $X and ${X} forms) outside the helper family.
3. Runtime contradiction detector: an attr_deferred marker for label L AND a definitive-failure row for L in one run = contradiction ⇒ loud warn + Phase-2 leg failure.

### 5.4e — closer-liveness rule (R2 §0/§2.3, NEW binding D-P1-10)
Promote defer arm leaves OPEN-C (named closer: daemon serving-green closer P4/P9 + intent_flip emitter promote.sh:574, launcher boot twin). Restart/rollback defer arms CLOSE at the surface (restart: no usable closer — P4 excludes restart-kind, launcher :3355/reclaim :561 exclude it, the two arm closers are pending_op-conditional per D6; rollback: MUST NOT leave open — D3, the closer would commit-close a stale rollback-kind txn wrongly, previous=displaced_version = the M4 strand).

**Closer preconditions (ruling C, added):** the promote leave-open (OPEN-C) disposition is permitted ONLY when (i) displaced_version is non-null (journal.current non-empty at txn-open) AND (ii) the closer kill-switch is not disabling it — upgrade_journal_sweep.py:178-193 and launcher.sh:131 (name both). When preconditions do NOT hold, the defer arm takes the CLOSE-DISTINCT path (halt + close with the defer outcome — the restart/rollback shape). **Never-strand is absolute.** Phase-2 legs: B-gate-1c (preconditions hold → OPEN-C), B-gate-1d (preconditions fail → CLOSE-D).


### 5.5 Portability

- `uname -s` dispatch stays the house idiom (A24): R2 is entered only through `_supervision_host_allows_unit`, which runs the
  uname test **first** (so non-Linux hosts take zero `/proc` reads). R3 uses `ps` only (POSIX). `_gate_attr_containment`
  never reads `/proc` outside an `[ -r ]` guard (P1 contract, `:7114-7120` precedent).
- Darwin: R1 + R3 (+R4) — no cgroup, no `systemctl`. The unit arm is unreachable by construction on Darwin
  (`_supervision_host_allows_unit`), which is why R3 is load-bearing there; R4 preserves the dual-supervisor refusal.

### 5.6 Behaviour at deferral exhaustion (amended by leader ruling D1)

**Halt semantics unchanged — but the path is budget-bound.** Pool exhaustion with the holder still `2` ⇒ the gate returns
**2 (transient)** ⇒ the verifier keeps polling within its existing budget **via the T7b rc-mapped probe consumer (before
T7b the `if !` form at `:6103-6106` collapsed the `2` into an immediate rc-1 refusal — §5.4a)**; when the budget floor finally wins
(`:6288-6297` floor-wins verdicts), the existing chain fires unchanged: verifier `return 1` (`:6234`/`:6256`) ⇒
`_supervision_handback_unit` `return 1` (`:6395-6397`) ⇒ `promote.sh:684-700` halt + B4 leave-txn-open (halt-span anchor
corrected to `:700` per architect §3). A **positively-foreign holder still refuses at the first census** — it never enters
the deferral rung. No nohup fallback (Amendment #1) is introduced or removed anywhere.

### 5.7 Protection matrix (input to Phase 2's negative proofs)

**Threat model of this matrix (stated explicitly, review wave 4): LOCAL process threats only** — a stale incarnation of
our own daemon, another local install's daemon, a locally-started monitor/`kubectl` sidecar, a daemon-launched child, an
operator-hostile **local** process binding the port. **NOT remote/network adversaries**: these surfaces defend *which
local process answers the port*, not who can reach the port; remote reach policy (bind address/firewall) lives outside
`scripts/upgrade/**` and is unchanged by this lane. Rows marked *residual* are documented-not-gated by deliberate
adjudication (each names why the residual is acceptable).

| # | Threat | Still caught by (unchanged) |
|---|---|---|
| P1 | Wrong release serving green (old binary, right unit) | `_handback_serving_probe` version compare `:6113-6116` → rc 1 |
| P2 | Green with version field absent/unparseable | `:6109-6111` → rc 1 |
| P3 | Non-2xx / red answer | house classifier `_probe_once_classified` → probe rc 1 (`:6120-6126`) |
| P4 | Green answer, **no** listener | gate `:8012-8019` "answered green but lsof finds NO listener" → rc 1 |
| P5 | Genuinely foreign holder (other install's daemon, kubectl, monitor) | ladder: not owned, not in our cgroup, no ancestor in our set ⇒ `1` ⇒ today's refusal detail |
| P6 | Foreign/NOHUP daemon serving while the launchd job crash-loops (pidless job) | R4 empty job pid ⇒ `1` (M7 pin preserved) |
| P7 | Holder outside the launchd job lineage but owned-shaped | Layer 2 launchd arm `:8066-8075` / R4 ⇒ `1` (M6 pin preserved) |
| P8 | Empty owned set + live holder, no supervisor context | R1 empty, R2 n/a, R3 root set empty ⇒ `1` ⇒ `owned:<none>` detail (M3 pin preserved) |
| P9 | MainPID equals the pre-stop pid (old daemon never left) | verifier `:6218-6221` → rc 1 (the `:6207-6213` anchor in an earlier draft is the budget printf — corrected, CP-5) |
| P10 | Containment tooling unavailable for the whole deferral window | pool exhausted ⇒ **transient (2)**, not an immediate refusal (leader D1) — the refusal happens **only at budget end** through the floor-wins verdict (`:6288-6297`); a positively-foreign holder never enters this rung |
| P11 | Non-2xx from the unit's *child* while the daemon is dead (post-crash inherited socket) | no green ⇒ probe transient/red; never accepted |
| P12 | Second supervisor **inside** our unit cgroup | Only possible if our unit started it (systemd); the same acceptance Layer 2 already grants. Documented residual, unchanged risk class |
| **P12-sibling** | **Stale-incarnation holder under `KillMode=mixed`** (adopted, architect closure C-7) | Ladder-accepted (same unit cgroup). **Caught by the version conjunct (P1) on the PROMOTE surface** (conjunct armed, `promote.sh:546-556`). On restart/rollback the conjunct was EMPTY (2xx-only degradation — §5.4's per-surface restatement, REVIEW WAVE 4 BL-9) ⇒ **T13/T14 arm the expected version from the target manifest before the shared gate**, so the class is caught per-surface. Documented, not gated; identical residual class as P12 |
| **P13** | **Green via `gate_livez`/`gate_readyz` — these gates carry NO version conjunct** (honest caveat, architect §0) | **Disclosed residual**: the absolute claim "no call site accepts green without a version check" is FALSE. The correct claim: the **promote/hand-back path is version-backed end-to-end** (version compare in `_handback_serving_probe` `:6107-6116`, `gate_version` `:8150-8160` before the compare, `gate_soak` per hard-green tick `:8383-8388`, `PROMOTE_EXPECTED_VERSION` unconditionally armed `promote.sh:546-556` — anchor corrected from the earlier `:541-544` cite, REVIEW WAVE 4 BL-9) and the promote's `gate_version` (`promote.sh:853`) runs **before** the commit step (`:868+`). The restart/rollback surfaces can skip the version check conditionally (`restart.sh:319-338` — identity source is the CURRENT release manifest, skipped when the symlink/manifest is unresolvable; `rollback.sh:276-278` — conditional on `TO_BIN_VERSION`, read from the target manifest at `:192`) — and their hand-back window had NO conjunct at all (`PROMOTE_EXPECTED_VERSION` empty, `lib.sh:1593`/`:6107` ⇒ 2xx-only degradation). **BL-9 closes the gap by ARMING, not by refusing to widen:** T13/T14 arm the expected version from the target manifest before the shared gate on both surfaces, so a stale incarnation can no longer be recorded as verified there. **The corrected bound (per-surface — see §5.4's restatement and `decisions.md` §10.1):** no wrong release can reach `current` through a widened gate on any **ARMED** surface — promote (unconditional, `promote.sh:546-556`) and restart/rollback **after T13/T14** — with exactly one disclosed exception: the **unresolvable-manifest** degraded path, where the arming cannot happen and that invocation falls back to 2xx-only (registered as residual **P15** below). The bound does NOT hold on an **unarmed** surface: pre-T13/T14 restart/rollback read an **empty** conjunct, and the `gate_livez`/`gate_readyz` call sites still carry none by design |
| **P12-tertiary** | **Cgroup-escaped descendant** (RESIDUAL row, wave 4): a daemon descendant that LEAVES the unit cgroup (moved to its own scope, re-cgrouped post-fork, daemon-launched-then-reparented) — it is lineage-child but NOT cgroup-contained | **Disclosed residual**: R2 answers **`1` (positively not contained)** ⇒ the ladder refuses — the acceptance is deliberately narrower than lineage; the same holder is refused by today's Layer-1 census too, so the widening grants nothing here and takes nothing away (no regression). Documented, not gated; unchanged risk class. The C-1 downgrade does NOT rescue it (R2 ANSWERED, so no stronger rung was unanswerable) |
| **P14** | **R3-when-reached is a NEW acceptance class vs Layer 2** (RESIDUAL row, wave 4): a holder accepted via R3 ppid-ancestry into the argv/cwd-owned set need not be inside the unit cgroup — legacy Layer 2 (`_gate_attr_unit_lineage`) would NOT have accepted it | **Disclosed residual**: R3's trust root is the SAME argv/cwd-anchored owned set Layer 1 already trusts (an owned ancestor, depth-bounded ≤ 12, `_supervision_pid_in_launchd_lineage` shape) — acceptance breadth is anchored to the owned set's existing trust, not to a new signal; reparented orphans walk to completion ⇒ answered `1` (C-5, no false accept). Pinned by I-B12's remove-R3 counter-mutation (I-B1 reddens). On Linux-with-systemd the cgroup rung answers first for cgroup members, so R3 matters only for the escaped/nohup shapes — exactly where Layer 2 never reached |
| **P15** | **Unresolvable-manifest degraded path on an ARMED surface** (RESIDUAL row, wave 4 / BL-9): T13's own disclosed degradation — when the TARGET release manifest is genuinely unresolvable, `restart.sh` keeps today's LOUD degraded skip (warn + `version=skipped-no-manifest`, ADR-033 posture) and the version conjunct stays **EMPTY** for that invocation | **Disclosed residual**: on that invocation the arming does not happen, so the restart/rollback surface falls back to the pre-BL-9 **2xx-only** behaviour — and the surface's own re-gate (`restart.sh:319-338` / `rollback.sh:276-278`) is skipped under the same unresolvable-manifest precondition ⇒ a stale incarnation would be recorded as verified **for that invocation**. **Not gated**: the refusal is **LOUD** (a `version=skipped-no-manifest` detail is journaled in `GATE_DETAIL`, never a silent 2xx-pass), and the condition is an operator/hostile-environment shape (a corrupted or half-written release dir) — the same risk class as **P12-tertiary**/**P14**. **This residual is what scopes the P13 row above:** the "no wrong release reaches `current` through a widened gate" bound holds on the **armed** surfaces, not on every surface |

### 5.8 One-fix-many-surfaces (deliberate)

Because the gate is shared, the fix also removes the identical false refusal from `gate_livez` / `gate_readyz` /
`gate_version`(soak) on the *post-hand-back* half of the same ceremony — the incident would have re-fired there had the
hand-back passed. Stated as an **intentional, tested widening**, not an accident: Phase 2 must add a gate-level leg too.

---

## 6. Candidate C — systemd-lane diagnosis stash

Reuse the existing plumbing (A16) — **readers unchanged** (A18).

New writer wrapper next to `:5605`:

```bash
_handback_stash() { HANDBACK_LAST_ERROR="$1"; _handback_err_file_write "$1"; }
```
(both channels: the verifier and the resolver run inside `$( )`, where a bare variable assignment dies — the MAJOR #1
rationale already in the file).

Touchpoints:

| # | Site | Stash text |
|---|---|---|
| C-1 | entry of `_supervision_handback_unit` (`:6317`, first statement) | **clear** both channels, so a stale seed (pre-stop plist/resolver failure, or a previous phase) can never mask this hand-back's diagnosis |
| C-2 | `:6332-6339` reset-failed, both arms | `reset-failed: rc=$sbrc${first:+ — $first}` / `reset-failed: timeout(${SYSTEMCTL_PROBE_BUDGET_S}s)` (needs a tiny errfile capture on the non-124 arm; currently `>/dev/null 2>&1`) |
| C-3 | `:6356-6360` is-active, both arms | `is-active: rc=$isact_rc state=${isact:-<none>}` (no stash for the benign rc=3? — **yes stash**, it is the *context* of a later failure, and the last write wins) |
| C-4 | `:6383-6391` start, non-124 arm | stash `first` (the first stderr line, **already captured** and today only WARNed — highest-value loss); 124 arm stashes the timeout wording |
| C-5 | `:6395-6397` verify refusal | stash the verifier's reason: new global `HB_VERIFY_REASON` set by `_supervision_unit_serving` at each `return 1` (`:6218-6221` prestop-equal — anchor corrected from the earlier `:6207-6213` draft citation (budget printf; CP-5/CP-8), attribution, `:6288-6297` floor bands, final budget verdict) **and at budget end when the trailing polls were deferral transients** (REVIEW WAVE 1, BL-1: the last gate `UNATTRIBUTABLE … deferring` marker is threaded from `GATE_ATTRIBUTION_DETAIL` into `HB_VERIFY_REASON` before the floor-wins/budget verdict fires, so the halt text says WHY the budget ran out) **plus an explicit file write from inside the verifier** (it runs in `$( )`) |
| C-6 | success path (after verify succeeds, before `return 0`) | **clear** both channels (`:6558` precedent) |

Optional (low risk, listed for approval): `promote.sh:1051-1056` (the *rollback* hand-back halt) also interpolates
`${HB_ERR}` via `_handback_last_error_text` + rm-after-read, matching `promote.sh:692-699`. Otherwise a rollback hand-back
halt keeps saying nothing about why. One-line change, same reader.

Result on the incident path: the halt detail would have read
`hand-back error: port-holder(s):1191207 1191605 NOT in the supervision-owned pid set (owned:1190778 1190919 1190932) …` —
and after the fix the same text would never be produced.

---

## 7. Restart-arm fix (`restart.sh`)

**7.1 Honest rc labels** — covered by Candidate A site 3 (§4.3) inside `restart_via_launcher` (`:6695-6703`).

**7.2 Journal rows for the warn-only exit-78 refusals** (minimal scope; no restart-lane rewrite). At each of
`restart.sh:122` (python floor), `:136` (stop-script sibling), `:148` (journal TORN), `:155` (no `pending_op`), `:161`
(pending kind), `:165` (run_id mismatch), `:173` (in_flight kind), append one row before the `exit 78`:

```bash
journal_history_append halt "restart executor run_id=${RUN_ID:-<none>}: refusal class=<slug> — <one-line cause>; exit 78, NOTHING armed/executed" || true
```

Decisions:
- **Kind = `halt`**, not a new kind: the same script already journals `halt` for its lock-adoption refusals (`:180`, `:186`),
  and `halt` needs no daemon-side label. `refusal` (the tool-lane kind) is *not* used: it is produce-once by
  `upgrade_tools._refusal`, and adding a second writer with different semantics is a needless contract change.
- **`|| true` everywhere**: at `:122` the python floor may be missing (the splice helper is what writes rows) and at `:148`
  the journal is TORN — best-effort is the only honest promise. The row is a *visibility* improvement, never a gate.
- Known correlatable side effect (documented, accepted): `halt` ∈ `_TERMINAL_EVENTS`, so it also lands in the restart-kind
  wake superset (`upgrade_journal.py:2642-2643`) — a waiting instance may be notified early. That is the same shape the
  existing lock-refusal rows already have, and the direction is conservative (notify, never miss).

**7.3 Explicitly NOT in scope:** persisting the arm's `pending_op`, the `lock_acquire` `to_thread`, and the boot-sweep
default (board sub-items 2–4). Only visibility.

---

## 8. Coalescence closure — board item `wi-d3579d7c` #1

Item #1 = *"promote-kind `flipped=false` live closer — same dead-actor pattern"*. How this lane closes it **without
weakening the closer**:

1. The incident class produced a `flipped=true` open txn (the flip had landed) whose only recovery was the daemon-side
   `sweep_serving_green_closer` (`:2325`, P1–P9 `:2402-2404` (P4) / `:2442-2452` (P9), commit `:2675-2689`). This lane **removes the producer**: a
   healthy boot no longer halts, so no open txn is created, so no closer rescue is needed (the incident was the *third*
   occurrence of exactly this shape).
2. The closer's predicates are **untouched**: P5 (`flipped == true`) stays, P9 (`intent_flip` authorization) stays, P6/P8
   windows stay. A `flipped=false` dead-actor txn is still **not** closable — by design: there is no flip to justify a
   commit, and widening P5 would let an unflipped arm be "committed" on health alone, which is the authorization hole P9
   exists to close.
3. **Nothing in `daemon/services/upgrade_journal_sweep.py` changes**, and no budget knob changes ⇒ the grace coupling
   `max(UPGRADE_CLOSER_BOOT_GRACE_S, HANDBACK_VERIFY_BUDGET_S)` (`:176/:2154/:2177-2178`) remains satisfied as written, and
   the closer keeps its full 300 s / boot-grace behaviour on genuinely unhealthy boots.
4. Residual, stated for the board: a `flipped=false` stuck txn from **other** causes (e.g. an arm whose executor died
   pre-flip) remains the boot sweep's/`restart_abandoned` lane's problem — unchanged, out of scope here.

---

## 9. Blast-radius statement (loud, on purpose)

- **Files changed by this phase: `scripts/upgrade/lib.sh`, `scripts/upgrade/restart.sh`, `scripts/upgrade/promote.sh`,
  `scripts/upgrade/rollback.sh`, and `scripts/upgrade/status.sh`** (lib.sh = A/B/C + T7b; restart.sh = T10 + the T7c restart gate consumers;
  promote.sh = T7c promote gate consumers + C-10a comment sync + T11 rollback halt; rollback.sh = the T7c rollback
  re-gate consumers, `:267-278`; status.sh = T7e transient note, `:125-130`/`:171-174`). Bash only.
- **Zero `daemon/**` logic changes.** Phase 3 will touch `daemon/tools/upgrade_tools.py:4145` — **a constant value only**
  (`PIPELINE_SCRIPTS_FLOOR_COMMIT`), which is Phase 3's lane and carries no runtime behaviour.
- **Zero schema/migration delta** ⇒ `rollback_safe=true` (fix-loss-only on rollback) — Phase 3 states it at stage time.
- **No new exported variables, no new OPERATOR knob (see `I-E3`, restated), no new journal EVENT kind, no new
  dependencies, no new tools** (only `ps`/`lsof`/`systemctl`/`launchctl`, all already used). Three new terminal arms
  (rows 6/14/16) plus a read-side token (`_gate_outcome`) ARE added — `halt` remains an existing event kind
  (I-E3 holds — no NEW event kind); the earlier machinery-minimality assertion is DROPPED (D4).
- **The one new bound** `_GATE_ATTR_DEFER_S` (§5.4) is **not** a contradiction of the line above: clamped `[0,5]`,
  marker-gated (`ENSEMBLE_TEST_GATE_DEFER=1`), **never exported**, routed through `_numeric_knob` for its integer parse
  but **not registered** as a knob (no `TABLE_` row, no C-10a entry). Stated here because an earlier blast-radius wording
  said "no new `_numeric_knob`s" while §5.4/D3 mandates the router — that contradiction is what BL-5 flags.

---

### 9.1 Commit scoping (R2 §3 — fold wave A)

**Census re-checkpoint (ruling A):** the census pin (T7g) counts ONLY sites inside this declared edit scope. Sites outside it (promote.sh:725 init, :734/:737/:761 launchd flip-verify, :983/:993/:1003/:1014 render-only reads) are classified by table rows 21-23 and do not redden the pin.

The R2 redesign touches exactly these line groups (verbatim, for review as one unit):

- `promote.sh:790,793,799,846-847,852-853,861,864,873,924-927,1059-1072,1084-1101`
- `restart.sh:275,284-290,292-304,321-338,345-361,363-374`
- `rollback.sh:266,267-279,286-296,298`
- `lib.sh:8004-8118,8142-8143,8159,8206,8236-8252,8294,8383-8387,8400-8450,8477-8498`
- `status.sh:125-130,171-174`

## 10. Task breakdown (implementation, ordered)

Dependencies are strict; a task is "done" only when its acceptance holds in-tree.

| # | Task | Depends on | Acceptance (testable) |
|---|---|---|---|
| T1 | Add `_bounded_rc_kind` / `_bounded_rc_reason` next to `_run_bounded` (`lib.sh:4883`) | — | Unit-exercised from a pack: returns `budget` for 124, `nonzero_exit` for 3/4/7/1; no I/O |
| T2 | Rewrite the three sites (`:6332-6339`, `:6356-6360`, `:6695-6703`) per §4.3 | T1 | rc=124 legs emit the **byte-identical** legacy WARN + `kind=budget`; rc=3 emits **no** journal row; rc=4/other emit `kind=nonzero_exit` with the true rc; the literal `rc=124` never appears when the child returned otherwise |
| T3 | Add `_supervision_pid_ancestor_of <pid> <roots…>` near `:7105`; `ps`-absent ⇒ rc 2 | — | Depth-bounded walk; root-absent/`ps`-absent cases pinned |
| T4 | Factor `_gate_attr_unit_ctx` out of `_gate_attr_unit_lineage`; re-implement the latter on top of it (behaviour-identical) | T3 | All existing unit-lineage/DualFight legs unchanged (messages incl. `MainPID=`/`cgroup=` detail) |
| T5 | Add `_gate_attr_containment <holder>` implementing R1–R4 (§5.3) | T3, T4 | Matrix unit-proven: owned⇒0, cgroup-child⇒0, ppid-child⇒0, foreign⇒1, tooling-missing⇒2; the `/proc/<holder>/cgroup` parse is factored onto the existing twins-pinned helper family (A32 — `_supervision_pid_cgroup_leaf` `:7111-7121`), **no third parse copy**. **R2 predicate — canonical string (the implementation MUST match phase1 §5.3's fenced block character-for-character; this cell is a POINTER, not the source):** `pcg == cg \|\| pcg PREFIX-matches "$cg/"` — i.e. `[ "$pcg" = "$cg" ]` **or** `case "$pcg" in "$cg"/*)` (left-anchored prefix at a segment boundary; **never** `*"$cg"*`; approver iter 001 note 9: `"$cg"` is a glob pattern in `case` — cgroup names with metacharacters mis-match; use a quoted/fgrep-style pre-check or escape), with `cg` empty/`/` ⇒ abstain `2` (degenerate guard). Source-grep tripwire: exactly one `in "$cg"/*` inside `_gate_attr_containment`, zero `*"$cg"*` there |
| T6 | Wire the ladder into `_gate_serving_attribution`'s Layer-1 block (nonmatch branch only) | T5 | Common case byte-identical (no systemctl probe issued when nonmatch is empty — provable via the stub `SC_LOG`); refusal prefix preserved; accept path logs INFO **on stderr** (`I-B2b`, §5.3) with no banned literal; `owned_spaced` becomes a properly-threaded local (the leaked global at `:8031` must not become the ladder's shared state — A32) |
| T7 | Add the pooled deferral (§5.4) + distinct exhaustion detail | T6 | Pool bounds total sleep to `_GATE_ATTR_DEFER_S` (**per shell, seconds-counted, clamped `[0,5]`** — D3); exhaustion ⇒ **transient (2), refusal only at budget end** (leader D1); the exhaustion marker journals event kind `subprocess_wait_timeout` with row token `kind=attr_deferred`, **fire-once latched** (`_GATE_ATTR_WARNED` precedent, §4.2); `0`/`1` short-circuit the loop |
| **T7b** | **rc=2 consumer at the probe (REVIEW WAVE 1, BL-1, §5.4a)** — in `_handback_serving_probe`, replace the `if ! _gate_serving_attribution …` collapse at `:6103-6106` with an explicit rc capture; transient `2` → probe `return 2` (mirror `:6235`/`:6256`); `0`/`1` arms byte-identical | T7 | A persistent-2 fixture: probe returns **2** (not 1), no `attribution … REFUSED` line, the verifier polls **≥2 iterations**, halt only at the floor-wins verdict (`:6288-6297`) with the exhaustion marker in `HB_VERIFY_REASON` (leg B-defer-e, phase2 §7.5); the green/refusal arms keep today's rc + wording byte-identical (M-B-defer-E must redden the leg) |
| **T7c** | **rc=2 mapping at the one-shot gate consumers (§5.4a, `D-P1-4`)** — consumers call `_gate_consume` in statement position (rc captured at each site from R2's §5.4b table); the deferral loop is INSIDE the gate now (no "bounded in-budget retry at the consumer"); livez/readyz/soak consumer blocks (`promote.sh:790-794`, `:799-848`, `:853`, `:864`; `restart.sh:284-290`, `:294-300`, `:321-338`; `rollback.sh:267-278`; `promote.sh:1060-1072`) gain the `0/1/2` branch via `_gate_consume`/`_gate_outcome`: transient `2` → `_gate_consume` sets `gate_defer_reason` with distinct transient wording — never `"/livez unreachable >Ns"` (green ≠ unreachable), never `"version verify mismatch"`, never `gate_fail_reason`/`GATE_FAIL`/`REGATE_FAIL` → 8b auto-rollback. Soak per-tick `2` semantics are INCONCLUSIVE/freeze per T7f, not "no-green-this-tick" | T7b | One-shot persistent-2 fixture: final text is the transient wording, exit disposition unchanged for `0`/`1`; `_gate_outcome` returns `defer`; no rc-2 lands in `gate_fail_reason`/`GATE_FAIL`/`REGATE_FAIL` or the false labels (M-B-defer-E must redden the leg) |
| **T7d-v2** | **Helper family `_gate_consume`/`_gate_outcome` + single canonical carrier `gate_defer_reason` (R2 §2.1-2.2; BLOCKING 1, approver iter 001; §5.4a/§5.4b)** — introduce the helper family in `lib.sh`; the three per-surface aliases are STRUCK (single name `gate_defer_reason`); read/written exclusively via `${gate_defer_reason:-}` (set -u-safe); `_gate_consume` is the sole writer (statement-position only, never inside `$( )`); `_gate_outcome` is the sole reader echoing `ok|fail|defer`; guards never read a carrier; the 8a COMMIT emptiness test (`promote.sh:873`) treats `gate_defer_reason` as NON-empty (commit arm requires both carriers empty); it never sets `gate_fail_reason`/`GATE_FAIL`/`REGATE_FAIL` and never routes into 8b auto-rollback; halt row carries TRANSIENT wording; exhaustion detail rides alongside `HB_VERIFY_REASON`; closer-liveness per §5.4e (promote OPEN-C, restart/rollback CLOSED-D) | T7c | Persistent-2 fixture per surface: NO commit row, `in_flight` still open, halt row present with transient wording; `gate_defer_reason` present at the site (grep); 8b auto-rollback never fires on rc-2; B4 leave-txn-open preserved where applicable; `_gate_consume`/`_gate_outcome` are the only reader/writer (census pin) |
| **T7e** | **status.sh transient note + `--verify` must NOT fail on a transient (R2 §2.3 row 20)** — status.sh:125-130,171-174 gain the `2 ⇒ attribution deferred (transient)` note; `--verify` must NOT fail on a transient | T7d-v2 | status.sh fixture: transient rc ⇒ note present, exit 0; `--verify` green on a transient |
| **T7f** | **Soak freeze + per-tick pool + cap-reach rc semantics (R2 §2.4; wiring the previously-unwired `lib.sh:8294`/`:8383-8387` tick sites — D1)** — STREAK-FREEZE (transient tick neither increments nor resets `consecutive_green`, `observation_kind="INCONCLUSIVE"`); per-tick FRESH pool (`_GATE_ATTR_DEFER_REMAIN` reset at top of each soak tick's version check, before `:8383` and `:8294`); soak cap UNCHANGED; `_enforce_deadline` converts residual overrun into halt (never commit, never auto-rollback); dispositions (all-transient or mixed at cap-reach ⇒ rc 2 → consumed at promote.sh:864 via `_gate_consume` → row-6 defer arm, 8b never reached; genuine never-green ⇒ rc 1 unchanged; definitive red returns 1 immediately) | T7d-v2 | Soak fixture: transient tick ⇒ freeze (counter-asserted); per-tick pool reset; cap-reach rc 2 consumed at :864; never 8b |
| **T7g** | **Generated census pin + zero-direct-read pin + runtime contradiction detector (R2 §2.5)** — behavioural per-surface legs with REAL child pids; GENERATED census pin (never a hardcoded list) + zero direct carrier reads/writes outside the helper family **WITHIN §9.1 scope** (out-of-scope sites carry table rows/exemptions); runtime contradiction detector `attr_deferred`∧definitive-failure = loud warn | T7d-v2 | Census pin: every gate-family symbol hit is inside lib.sh wrappers / status.sh / followed within 8 lines by `_gate_consume`/`_gate_outcome`; zero direct carrier reads (`gate_fail_reason|GATE_FAIL|REGATE_FAIL|gate_defer_reason`, both `$X` and `${X}`) outside the helper family **WITHIN §9.1 scope** (out-of-scope sites carry table rows/exemptions — rows 21-23); contradiction detector fires on seeded contradiction |
| T8 | Candidate C: `_handback_stash` + C-1…C-6 touchpoints + `HB_VERIFY_REASON` | T2 (same file region, serialized) | Fixture: unit verify refusal ⇒ `hand-back error:` is **non-empty** in the halt detail; success path clears both channels; a stale pre-hand-back seed cannot surface |
| T9 | C-10a comment sync (both copies) | T6, T7 | `sed`-extracted blocks in `lib.sh` and `promote.sh` are **byte-identical** |
| T10 | Restart-lane rows at the seven refusal sites (§7.2) | T2 | Fixture per class: one `halt` row naming the class; exit codes unchanged; `\|\| true` best-effort proven on a TORN journal |
| T11 | (Optional) `promote.sh:1051-1056` rollback halt gains `HB_ERR` | T8 | Rollback hand-back halt detail carries the stash text |
| T12 | **DEFERRED (leader ruling D-P1-3, 2026-11-17)** — containment widening for `_flip_verify_port_attribution` (`:6996`), D-1 grace preserved → a named follow-up lane (future id `143a045c`); **not implemented in this lane** | — (deferred) | Not applicable in-lane; deferral rationale and compensating coverage are in `decisions.md` §6 |
| **T13** | **Per-surface version arming — `restart.sh` (REVIEW WAVE 4, BL-9; leader ruling Option A)**: before `restart_via_launcher` (`restart.sh:251`), derive the expected version from the TARGET release manifest — the CURRENT release, the same ADR-027 identity source the `restart.sh:319-338` version-identity check reads — and arm the hand-back probe's expected-version channel (`PROMOTE_EXPECTED_VERSION`, a plain non-exported variable: the `promote.sh:546-556` `D-P1-9` channel shape; `lib.sh:1593` default empty, `lib.sh:6107` the conjunct key). A genuinely unresolvable manifest keeps today's LOUD degraded skip (warn + `version=skipped-no-manifest` detail, ADR-033 posture) — disclosed in `GATE_DETAIL`, never a silent 2xx-pass | T6 (the gate surfaces it protects) | Fixture: stale-incarnation holder (previous build, same unit cgroup — the P12-sibling shape) + armed restart ⇒ hand-back verify refuses on the version conjunct (`:6113-6116` wording, rc 1) before any attribution accept can matter; the unresolvable-manifest path journals `version=skipped-no-manifest` — **commit owner: C7 (§11 choreography); `scripts/upgrade/restart.sh` only** |
| **T14** | **Per-surface version arming — `rollback.sh` (BL-9 twin)**: hoist the target-manifest read above the hand-back — `TO_BIN_VERSION="$(manifest_field "$TO_VERSION" binary_version …)"` is already computed at `rollback.sh:192`; derive/assign it (and arm the same channel) BEFORE `restart_via_launcher` (`rollback.sh:249`). The existing re-gate check (`:276-278`) is unchanged and remains the second net | T6 | Fixture: stale-incarnation holder serving the displaced build + armed rollback ⇒ hand-back verify refuses (wrong version vs `TO_VERSION`'s manifest); with the arming deleted (**M-NP12**) the surface degrades to 2xx-only and the stale build is recorded as verified — the **NP-12** leg reddens — **commit owner: C8 (§11 choreography); `scripts/upgrade/rollback.sh` only** |

**Serialization rule:** T1→T2→T8 all edit the same `lib.sh` regions; T3→T4→T5→T6→T7 likewise. Never two `edit_file`
calls on `lib.sh` in one parallel block (house parallel-edit race: corrupts the file tail). One edit per message, then
`git status`/`git diff` verification.

---

## 11. Code-commit choreography

Branch: `fix/promote-handback-false-halt` (this worktree). Bash-only; identity `worker`; conventional commits
(`<type>: <desc>`; planning = `docs(<scope>):`). **Never `git add -A`; never a bare `git commit` in a shared worktree**
— always explicit paths + `--only`.

| Commit | Message | Paths |
|---|---|---|
| C0 | `docs(promote-handback-false-halt): phase-1 code-fix design` | `.agents/shared/planning/promote-handback-false-halt/phase1-plan.md` |
| C1 | `fix(upgrade): classify bounded systemctl probe returns honestly` | `scripts/upgrade/lib.sh` (T1, T2) |
| C2 | `fix(upgrade): accept supervisor-contained port holders in serving attribution` | `scripts/upgrade/lib.sh` (T3–T6) |
| C3 | `fix(upgrade): bound attribution deferral for unattributable port holders` | `scripts/upgrade/lib.sh` (T7, **T7b**, T9) **+ the T7c one-shot consumer mapping** (file:line column copied **verbatim** from §5.4b's 20-row table rows 4/8/10/11/15/17: `promote.sh` (`:790-794`, `:799-848`, `:852-854`, `:861-865`, `:1059-1072`, `:1084-1101`), `restart.sh` (`:284-290`, `:292-304`, `:321-338`, `:345-361`, `:363-374`), `rollback.sh` (`:267-279`, `:286-296`, `:298`)) — one commit owns the whole rc=2 consumer contract so it reviews as a unit (REVIEW WAVE 1, BL-1; `D-P1-4`) **+ T7d-v2 helper family + named transient carrier `gate_defer_reason` at the same sites (BLOCKING 1, approver iter 001) + T7g census pin** + the sanctioned adaptive-soak pin update (`tests/test_adaptive_soak.sh:626-637`, C2c, row-2 remap) in the SAME commit (ruling E); T7g's census artifact is a `tests/**` file carried here as the sanctioned test pin |
| **C3b** | `fix(upgrade): status.sh transient note for attribution deferral` | `scripts/upgrade/status.sh` (**T7e** — alone) — status.sh:125-130,171-174 gain the transient note; `--verify` must NOT fail on a transient |
| C4 | `fix(upgrade): stash the systemd hand-back diagnosis for halt text` | `scripts/upgrade/lib.sh` (+ `scripts/upgrade/promote.sh` iff T11 accepted) |
| C5 | `fix(upgrade): journal restart preflight refusals` | `scripts/upgrade/restart.sh` (T10) |
| **C7** | `fix(upgrade): arm the expected version on the restart hand-back` | `scripts/upgrade/restart.sh` (**T13** — BL-9 per-surface arming: derive the expected version from the CURRENT release manifest (the ADR-027 identity source `restart.sh:319-338` reads) and arm `PROMOTE_EXPECTED_VERSION` **before** `restart_via_launcher` (`:251`); a genuinely unresolvable manifest keeps today's LOUD degraded skip) |
| **C8** | `fix(upgrade): arm the expected version on the rollback hand-back` | `scripts/upgrade/rollback.sh` (**T14** — BL-9 twin: hoist the target-manifest read (`TO_BIN_VERSION`, computed at `rollback.sh:192`) above the hand-back and arm the same channel **before** `restart_via_launcher` (`:249`); the existing re-gate check (`:276-278`) is unchanged and remains the second net) |
| C6 (**deferred**) | ~~`fix(upgrade): containment widening for the launchd flip-verify attribution`~~ — **NOT CUT in this lane** (T12 deferred per D-P1-3) | — (follow-up lane) |

Commands (exact shape, one commit per step, run **after** the tree is verified green for that step's packs):

```bash
cd /home/nea/ensemble-src-wt-hbfix
git add .agents/shared/planning/promote-handback-false-halt/phase1-plan.md \
 && git commit --only -m "docs(promote-handback-false-halt): phase-1 code-fix design" \
      -- .agents/shared/planning/promote-handback-false-halt/phase1-plan.md

git add scripts/upgrade/lib.sh \
 && git commit --only -m "fix(upgrade): classify bounded systemctl probe returns honestly" \
      -- scripts/upgrade/lib.sh
```
(then C2…C8 identically; **C5 and C7** substitute `scripts/upgrade/restart.sh`; **C8** substitutes `scripts/upgrade/rollback.sh`).

Ordering constraints:
- C1 must land **before** C2: the deferral/containment code paths assume the honest journal vocabulary when they log.
- C2 before C3 (C3's exhaustion detail is a refusal-path sibling of C2's ladder; C3 now also carries the T7c one-shot
  consumer mapping — lib.sh + promote.sh + restart.sh + rollback.sh — so the whole rc=2 contract reviews as one unit).
- C4 last among the `lib.sh` candidates: it touches the same functions' failure arms and is the easiest to review alone.
- C5 shares `restart.sh` with C3 (C3 = gate-consumer rc mapping at `:284-300`; C5 = the preflight refusal rows) — keep the
  two restart.sh edit series **serialized** (one edit per message; the house parallel-edit race rule) and land C3 first so
  the rc vocabulary exists before the refusal rows cite it.
- **C7** (T13) and **C8** (T14) — the BL-9 per-surface version arming — land **after C2** (they arm the channel the
  T6 gate surface protects) and **after C3** (the rc=2 consumer mapping must already be in the tree so the arming hunk's
  refusal vocabulary is the shipped one). **C7 shares `restart.sh` with C3 and C5; C8 shares `rollback.sh` with C3** —
  keep each file's edit series **serialized** (one edit per message; the house parallel-edit race rule), in the order
  **C3 → C5 → C7** for `restart.sh` and **C3 → C8** for `rollback.sh`. C7 and C8 touch different files, so they may be
  prepared in parallel, but **both** must be in the tree before Phase 2's **NP-12** leg is judged green.
- C6 droppable without affecting C1–C5, C7, C8.
- **Phase 3 owns everything after the last lane commit** (C8 — C6 is deferred and droppable): merge → bump commit (`scripts/bump_version.py patch`) → re-pin
  `lib.sh:95 PIPELINE_SCRIPTS_REV` **and** `upgrade_tools.py:4145 PIPELINE_SCRIPTS_FLOOR_COMMIT` to the **merge commit sha**
  → annotated tag → detached stage worktree → FE rebuild/sidecar re-stamp → `stage.sh live`. A commit cannot name itself as
  the floor (self-reference), so the two-commit minimum (`M` then `B`) is structural; see research-findings-lane §Q4.

---

## 12. Phase risks (top 6)

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | **Widening is too permissive** on some shape we did not enumerate (e.g. a foreign process that happens to be a descendant of an owned pid, or a monitor sidecar launched by the daemon) | High — the protection this conjunct exists for | Medium | Ladder accepts only (cgroup ∈ our unit) or (ancestor ∈ our argv/cwd-anchored owned set). A daemon-launched sidecar holding our port is *inside our tree* by definition and the same acceptance Layer 2 already grants today (P12 residual, documented). Phase 2's negative matrix (P5, P8, P9) must prove refusals survive; mutation-proof required (green-under-own-revert needs a counter-mutation red) |
| R2 | **Deferral eats ceremony wall-clock** or amplifies across poll iterations | Medium (halt quality / total wall-clock) | Medium | **Per-shell** pool (≤5 s per shell, counted in seconds — architect §2 corrected the original "5 s per process" arithmetic and scope); `[0,5]` clamp + dual-key marker ⇒ a leaked value cannot extend it; exhaustion is **transient → budget-bound** (leader D1), never an immediate terminal refusal; `0`/`1` short-circuit; no knob change ⇒ closer coupling intact; C-10a note in both copies |
| R3 | **Pinned log/test contracts break** (7b substring, 7c wording, M1/M3/M6/M7 details, rc=124 WARN) | High (blocks merge) | Medium-High (text edits near pinned sites) | Explicit byte-preservation list in T2/T6 acceptance; run the four packs before each commit; keep the refusal **prefix** verbatim and append only a suffix; keep rc=124 arms untouched |
| R4 | **Hidden coupling** of the gate change into `status.sh --verify` (`:128` reads `GATE_ATTRIBUTION_DETAIL`) or promote's `gate_fail_reason` rendering | Medium (misleading operator text) | Medium | `GATE_ATTRIBUTION_DETAIL` remains a single-line string with the same prefix; the accept path leaves it empty; no dedicated phase-2 leg pins `status --verify` on any shape — the coupling is recorded as a RESIDUAL (approver iteration 001, note 2 — best-effort mitigation, no leg added). T7c (§5.4a) is the rc=2-specific half: a transient `2` never lands in `gate_fail_reason`/`GATE_FAIL`/`REGATE_FAIL`, and its surface wording is the distinct transient text — so an rc-2 cannot mint the false `"/livez unreachable >Ns"` label (the review's mislabel fold) or drive the 8b auto-rollback. **Approver iter 001 note 2 (minimal truthful option): the dangling `status --verify` mitigation stands as a best-effort, residual register entry — no dedicated phase2 leg is added; R4 is recorded as a residual rather than a pinned leg.** |
| R5 | **Review surface too big in one file** — five commits in `lib.sh` (9052 lines) invite a silent regression | Medium | Medium | Strict commit ordering, one edit per message, `git diff` per commit, `bash -n` after every edit, packs run per commit; C4 separated (failure-arm-only) |
| **R6** | **Per-surface version conjunct is EMPTY on restart/rollback** — a ladder-accepted stale incarnation (the P12-sibling shape) would degrade to a 2xx-only verified pass on 2 of the 3 surfaces (the hand-back window reads an empty default, `lib.sh:1593`/`:6107`; promote.sh's `:546-556` comment states the degradation) | High (a wrong/stale build recorded as verified) | Medium (requires a stale holder surviving into a restart/rollback) | **REVIEW WAVE 4, BL-9 — leader ruling Option (A): ARM the expected version from the TARGET release manifest before the shared gate.** T13 (restart.sh, `:251`, ADR-027 current-release identity source) + T14 (rollback.sh, `:249`, target-manifest read hoisted from `:192`; re-gate `:276-278` unchanged as second net). Refuse-to-widen was REJECTED — it would keep the false-halt alive on those surfaces. Pinned by **NP-12 + M-NP12** (phase2 §9.1/§9.2). **Owner: Phase-1 implementer (T13/T14); verification: Phase-2 NP-12 leg + M-NP12 counter-mutation; plan-level owner of record: the coding commission's phase-1 worker** |
| **R7** | **Transient carrier `gate_defer_reason` mis-wired** (R2 §4 item 6) — the new failure modes: a direct carrier read outside the helper family (bypassing `_gate_consume`/`_gate_outcome`), a collapsed rc (treating `2` as `1` or vice-versa), a census-omitted site, or a soak reset/poison (transient tick resetting `consecutive_green`, or per-tick pool not reset). Also: carrier treated as empty by the 8a COMMIT check, or sets `gate_fail_reason`/routes into 8b auto-rollback, or mints the unreachable/mismatch mislabel, or leaves a restart/rollback defer arm OPEN | High (silent commit / wrong-state rollback / false label / wedge) | Medium (new wiring at three surfaces) | Consume `gate_defer_reason` ONLY through `_gate_consume`/`_gate_outcome`; commit arm requires BOTH carriers empty; never auto-rollback on rc-2; transient wording only; restart/rollback defer arms CLOSE at the surface (never leave open); recovery via sweep serving-green closer (promote ONLY — not universal); generated census pin + runtime contradiction detector (T7g) |

Secondary (tracked, not blocking): Darwin path (R3/R4 rungs) is proven only by stub harnesses — no Darwin host in this lane;
the optional T12 is **deferred** to a follow-up lane (leader ruling D-P1-3) and is treated as "best-effort parity", with the
Linux fix as the certified one.

**RC3 disposition (review wave 4, BL-8 — leader ruling):** root cause #3 of the overview (§2 item 3; phase2 §3 D5) — the
unit arm's zero retry (`:6377` single start / `:6395-6397` single verify vs launchd's ×2 bootstrap `:6541-6557`) — is
**SUBSUMED by D1's budget-bound deferral, not silently dropped**: on the incident shape the halt was an *attribution
refusal*, and a bare retry would only re-halt later on the SAME refused attribution (the refusal is evidence-based, not
boot-timing-based). The bounded re-probe for the unattributable-yet class already lives at the verifier level (§5.4/D1).
**Unit-arm retry-parity is EXPLICITLY DEFERRED to the T12 sibling lane** (decisions.md §6 row 8, register row `D-P1-6`);
no phase-1 task implements it, and phase2 adds no D5-specific leg — the deferral-tolerance legs (`B-defer-e`, floor-wins
halt) already pin the honest behaviour of the unretried verify.

**Disclosed residual + behaviour widenings folded from the architect's adversarial pass (2026-11-17):**
- **P13 residual (honest caveat):** `gate_livez`/`gate_readyz` carry no version conjunct, and restart/rollback can skip the
  version check conditionally — the promote/hand-back path remains version-backed end-to-end and `promote.sh:853` runs before
  the commit step; no wrong release reaches `current` through a widened gate (§5.7 P13).
- **`halt`-row widening (behaviour change to a machine-checkable gate):** the seven newly-journaled restart refusals emit
  `kind=halt` rows, and `ledger_check.py:74` `VIOLATION_EVENTS` includes `halt` ⇒ those refusals now mark a drill cycle
  VIOLATION (breaking the N-clean streak) and `ALERT_KIND_BY_EVENT["halt"]` pages each as `upgrade_cap_halt`. Defensible
  (post-adoption restarts already journal `halt`) but it **changes a machine-checkable gate** ⇒ disclosed in the CHANGELOG /
  tag annotation and pinned in Phase 2 (§10).
- **Halt-paging widening (ruling A note):** the three defer-arm halts (rows 6/14/24) ALSO page as `upgrade_cap_halt` beyond the seven restart refusal rows in R-WIDENING — disclose alongside the seven-row set so the paging surface is complete.
- **Restart-lane unguarded window:** the restart arm has **no provenance guard** (`system_restart` resolves the scripts dir but
  never runs `scripts_dir_provenance_verdict`) ⇒ until the operator advances the scripts pin, the restart arm executes
  pre-fix `restart.sh` unjournaled. Disclosed in the ceremony window (Phase 3 §5).

---

## 13. Success criteria

| # | Criterion | How measured | Threshold |
|---|---|---|---|
| S1 | Honest rc classification | rc matrix replay `{0,3,4,7,124}` at each of the three sites; inspect the journal + WARN text | `kind=budget` **only** for 124; rc=3 ⇒ **no** journal row; rc=4/7 ⇒ `kind=nonzero_exit` with the true rc; zero occurrences of a hard-coded `rc=124` for a non-124 child |
| S2 | Healthy-boot survival (the incident) | Fixture: owned daemon + a child process holding the LISTEN socket with foreign argv, `/livez` green with the expected version | Hand-back verify returns **0**; no `halt` event; no `subprocess_wait_timeout` floor/attribution row; the ceremony continues to the gates |
| S3 | Protection preserved | Replay P1–P12 from §5.7 | 100% refuse (or catch) as tabulated; existing M-section legs green unchanged |
| S4 | Diagnosis never lost | Unit verify refusal fixture | Halt detail `hand-back error:` is **non-empty**; `none captured` never appears on a unit-path halt |
| S5 | Restart visibility | Fixture per refusal class (§7.2) | One named `halt` row per class; exit 78 unchanged; TORN-journal case degrades silently (no new failure) |
| S6 | Blast radius | `git diff --stat 0181dadbf..HEAD` | Only `scripts/upgrade/**` + the planning doc (+ Phase 3's constants later); **zero** `daemon/**` logic |
| S7 | Wall-clock | Packs with `_GATE_ATTR_DEFER_S=0` and with default (and with a leaked `300` → clamped to 5) | Each gate invocation adds ≤ 5 s **per shell** (seconds-counted pool); verify-phase total unchanged within the existing budget; a leaked value cannot extend the pool |
| S8 | Table sync | `sed`-extract both C-10a blocks, `cmp` | Byte-identical |
| S9 | No new refusal-literal regressions | `assert_not_contains "systemctl start"` on all success paths | Zero hits |
| S10 | Closer untouched | `git diff --stat -- daemon/services/upgrade_journal_sweep.py` | Empty |
| S11 | rc=2 consumer contract (I-B13, §5.4a) | persistent-2 fixtures at the probe and at one `gate_version`/livez consumer | probe returns 2 and the verifier polls ≥2 iterations; one-shot consumers retry within budget and surface the transient wording; zero occurrences of rc-2 landing in `gate_fail_reason`/`GATE_FAIL`/`REGATE_FAIL` or the `/livez unreachable >Ns` / `version verify mismatch` labels; the M-B-defer-E counter-mutation (restored `if !` consumer) reddens the leg |
| S12 | Gate stdout purity (I-B2b) | capture `LIVEZ_JSON="$(gate_livez)"` etc. on an accepted-widened green | captured stdout parses as the probe body alone (no INFO prefix); the verifier's captured pid is a single numeric token; M-B2b (ACCEPTED line via `_log`) reddens it |
| **S13** | **Per-surface version arming is present on BOTH non-promote surfaces (REVIEW WAVE 4, BL-9 — T13 + T14)** | fixture: a stale-incarnation holder (the P12-sibling shape — the displaced/previous build, same unit cgroup) serving a **2xx green carrying the STALE version**, driven through the hand-back verify on a restart fixture **and** a rollback fixture, **with T13/T14's arming in place** (`PROMOTE_EXPECTED_VERSION` armed from the target manifest before the shared gate — `restart.sh:251` / `rollback.sh:249`) | the probe's version conjunct (`lib.sh:6107-6116`) **refuses on BOTH armed surfaces** — probe/verify rc 1 with the `NOT serving the right release` wording, **no** `hand-back COMPLETE`, never a recorded-verified pass. With the arming deleted (**M-NP12**) the surface degrades to 2xx-only and the stale build is **recorded as verified** ⇒ **NP-12 reddens** (phase2 §9.1/§9.2). The **unresolvable-manifest** path is the disclosed degraded case: it keeps today's LOUD skip (`version=skipped-no-manifest` in `GATE_DETAIL`), never a silent 2xx-pass, and is registered as residual **P15** (phase1 §5.7) |
| **S14** | **Named transient carrier `gate_defer_reason` + txn end-states per §5.4b table (BLOCKING 1, approver iter 001; R2 §2.3)** | persistent-2 fixture at each of the three one-shot surfaces | Txn end-state per §5.4b rows 5/6/9/10/12/13/14/16/17: promote OPEN-C (no commit row; `in_flight` still open; halt row present with TRANSIENT wording); restart CLOSED-D (no `restart` row; `restart_gate`+close_txn; exit 1); rollback CLOSED-D (no `rollback` event, no cap count; `journal_close_txn`; symlink facts recorded, no success claim). `gate_defer_reason` never empty-checked into commit/flip/record-success; never enters 8b auto-rollback; B4 leave-txn-open preserved where applicable; closer named per surface (promote YES; restart/rollback NO-usable/MUST-NOT-leave-open) |
| **S15** | **Soak semantics (R2 §2.4)** | soak fixture with transient ticks | STREAK-FREEZE (transient tick neither increments nor resets `consecutive_green`, `observation_kind="INCONCLUSIVE"`); per-tick FRESH pool (`_GATE_ATTR_DEFER_REMAIN` reset at top of each soak tick's version check); soak cap UNCHANGED; cap-reach rc 2 consumed at promote.sh:864 via `_gate_consume` → row-6 defer arm, 8b never reached; genuine never-green ⇒ rc 1; definitive red returns 1 immediately; honest cost +≤5 s/tick ≤6 ticks ⇒ ≤+30 s and −2 green opportunities worst case; `_enforce_deadline` converts residual overrun into halt (never commit, never auto-rollback) |
| **S16** | **Census + zero-direct-read + contradiction detector (R2 §2.5)** | generated census pin + runtime detector | every gate-family symbol hit is inside lib.sh wrappers / status.sh / followed within 8 lines by `_gate_consume`/`_gate_outcome`; zero direct carrier reads (`gate_fail_reason|GATE_FAIL|REGATE_FAIL|gate_defer_reason`, both `$X` and `${X}`) outside the helper family **WITHIN §9.1 scope** (out-of-scope sites carry table rows/exemptions); seeded `attr_deferred`∧definitive-failure contradiction fires loud warn + leg failure |

---

## 14. Hand-off: design invariants Phase 2 must prove

Each is a **named, testable** invariant; Phase 2 turns them into positive legs **and** counter-mutation (negative-proof)
legs. The bias is deliberately asymmetric: *every* refusal must be provable, and *every* new acceptance must be narrow.

**A — rc honesty**

- **I-A1** At each of the three sites, a child that exits `3` produces **no** journal row and a `_log` line; the hand-back
  still proceeds (behaviour identical to today).
- **I-A2** A child that exits `4` (or any other nonzero) produces exactly one row with `kind=nonzero_exit` and `rc=<true rc>`
  — and **never** the token `timeout(`.
- **I-A3** A child that is SIGTERM/SIGKILL'd by `_run_bounded` (hang) produces the **legacy** WARN text and a
  `kind=budget … rc=124` row (byte-identical to today — the existing pins must stay green untouched).
- **I-A4** No code path journals a literal `rc=124` for a child that returned a different rc (the reset-failed defect).
- **I-A5** `_run_bounded` is unchanged (`git diff` shows no hunk in `:4856-4882`).

**B — healthy-boot survival + narrow acceptance**

- **I-B1** *Incident replay*: owned daemon + inherited-fd child holding `:PORT`, green `/livez` with the expected version
  ⇒ gate rc 0, hand-back rc 0, no `halt`.
- **I-B2** *Ordering invariant*: when Layer 1 finds no nonmatch, **no** extra `systemctl show` probe is issued (prove with the
  stub `SC_LOG` call-count) and the log output is byte-identical to pre-fix (prove with a captured pre/post transcript).
- **I-B3** *Refusal prefix invariant*: every refusal detail still starts with
  `port-holder(s):<pids> NOT in the supervision-owned pid set (owned:<set>)` — the M1/M3/M6/M7 assertions stay green
  **without edits**.
- **I-B2b** *Gate-call stdout purity* (REVIEW WAVE 1, BL-2): every `$()`-captured gate call keeps byte-identical stdout —
  `LIVEZ_JSON`/`READYZ_JSON`/`RG_LIVEZ` carry ONLY the probe body, `gate_version`'s captured stdout carries only its
  payload, and the verifier's captured `newpid` is a **single numeric token**. All gate-emitted INFO — accept line
  (§5.3) and transient/exhaustion marker (§5.4) — rides **STDERR** (`printf '%s[%s]: %s\n' … >&2`, the `:6210`
  budget-printf precedent, A31): `_log` writes stdout when `ENSEMBLE_UPGRADE_LOG_FILE` is unset (`:434-443`) and would
  pollute the captured payload. The refusal arm rides `_warn` (already stderr) — unchanged.
- **I-B4** *Foreign holder*: a holder that is not owned, not in our unit cgroup and has no owned ancestor ⇒ rc 1 **at the
  first census** (no deferral burn).
- **I-B5** *Pidless launchd job + served port* ⇒ rc 1 naming the crash-loop shape.
- **I-B6** *Wrong version* / *absent version* ⇒ rc 1 with today's wording (the widening must not become a version bypass).
- **I-B7** *Green with no listener* ⇒ rc 1 unchanged.
- **I-B8** *Deferral bound — restated in full (REVIEW WAVE 2, BL-5; supersedes every earlier wording of this invariant,
  including the "process-global"/"+5 s"/"then refuses" variants):* for a permanently unattributable shape (supervisor
  context present, cgroup unreadable/`/`, `ps` absent) —
  (a) **per-shell scope** — the patience pool `_GATE_ATTR_DEFER_REMAIN` is a plain shell variable initialised at source
  time from `_GATE_ATTR_DEFER_S`; each `$( )` gate call site forks its own pool. There is **no** process-global pool and
  no cross-shell carry-over;
  (b) **seconds-counted** — the loop decrements by the **slept duration**, not by an iteration count (`sleep 2` ⇒ −2), so
  the honest bound is ≤ `_GATE_ATTR_DEFER_S` **seconds**, not "5 iterations ≈ 10 s";
  (c) **clamped `[0,5]`** — the seam is integer-parsed through the house `_numeric_knob` router (loud-garbage note, never
  a silent 0) and **then clamped**; a leaked `300` becomes `5` and a leaked `0` stays `0`;
  (d) **dual-key gated** — a non-default value is honoured **only** when `ENSEMBLE_TEST_GATE_DEFER=1` is also set; absent
  the marker the pool is always the 5 s default;
  (e) **exhaustion ⇒ transient `2`** with the **distinct** marker
  `… containment UNATTRIBUTABLE within <spent>s … — deferring to the verify budget` (leader D1; row token
  `kind=attr_deferred`, fire-once latched, stderr per `I-B2b`);
  (f) **refusal happens ONLY at budget end** through the unchanged floor-wins verdict (`:6288-6297`); the deferral never
  converts a `2` into a `1`, and never into a `0`.
- **I-B9** *Gate surface parity*: the same healthy-boot shape is accepted by `gate_livez`/`gate_readyz` (not just hand-back),
  and the same foreign shape is refused there.
- **I-B10** *Halt chain untouched, entered only at budget end* (restated — REVIEW WAVE 2, BL-5): deferral **exhaustion is
  NOT a halt trigger**; it yields the transient rc `2` and the poll loop continues. The halt chain — probe rc `1` →
  verifier rc `1` → `:6395-6397` warn → `promote.sh:684-700` halt + B4 leave-txn-open — fires **unchanged, exactly once,
  and only when the verify budget is spent** (the floor-wins verdict decides, `:6288-6297`), with the exhaustion detail
  carried into `HB_VERIFY_REASON`/`GATE_ATTRIBUTION_DETAIL`. Between those two ends nothing is introduced: no new
  intermediate `return`/`exit`, **no** nohup fallback, no early halt. A positively-foreign holder (`1`) still refuses at
  the first census and never enters the deferral at all.
- **I-B11** *Portability*: on a Darwin-stubbed `uname` (A25 precedent) no `/proc` read occurs and containment runs on the
  ppid ladder only; on a Plan9-stubbed `uname` the fail-closed dispatch still refuses (A24 idiom).
- **I-B12** *Mutation-proof*: each new acceptance has a counter-mutation that turns its leg red (e.g. remove R3 ⇒ I-B1 red;
  remove the `1`-classification ⇒ I-B4 red).
- **I-B13** *rc=2 consumer contract* (REVIEW WAVE 1, BL-1, §5.4a / `D-P1-4`, re-cut BLOCKING 2 approver iter 001; R2 §2.1-2.2): rc=2 = inconclusive. A transient `2` from the gate is consumed as transient at EVERY call site via the one-writer/one-reader family (`_gate_consume` sole writer, `_gate_outcome` sole reader): the probe maps it to probe-rc 2 (`return 2`, mirror `:6235`/`:6256`) so the verifier keeps polling; the one-shot consumers map it through `_gate_consume` to a `gate_defer_reason` transient wording (never a refusal rc 1, never `gate_fail_reason`/`GATE_FAIL`/`REGATE_FAIL` → 8b auto-rollback, never the false `"/livez unreachable >Ns"` / `"version verify mismatch"` labels). A persistent `2` still halts only at budget end (floor-wins verdict) with the exhaustion detail in `HB_VERIFY_REASON` and the halt row carrying `gate_defer_reason`'s TRANSIENT wording (leg B-defer-e). The canonical carrier is `gate_defer_reason` (single name); the per-surface aliases are STRUCK. The three-alias carrier requirement is superseded.
- **I-B14** *No direct carrier read outside the helper family* (R2 §2.2/§2.5): zero direct carrier reads/writes (`gate_fail_reason|GATE_FAIL|REGATE_FAIL|gate_defer_reason`, both `$X` and `${X}` forms) outside `_gate_consume`/`_gate_outcome` in `lib.sh` (plus `status.sh` diag) **WITHIN §9.1 scope**; out-of-scope sites carry table rows/exemptions (rows 21-23). Guards read `_gate_outcome` only. Census pin (T7g) enforces it; runtime contradiction detector (attr_deferred ∧ definitive-failure) fires loud warn on any miss.
- **I-B15** *Restart/rollback defer arms CLOSE at the surface — never leave open* (R2 §2.3 rows 14/16; NEW binding D-P1-10 closer-liveness): promote defer arm leaves OPEN-C (named closer: daemon serving-green closer P4/P9 + `intent_flip` emitter `promote.sh:574`, launcher boot twin); restart defer arm CLOSES at the surface (no usable closer — P4 excludes restart-kind, launcher `:3355`/reclaim `:561` exclude it, the two arm closers are `pending_op`-conditional per D6); rollback defer arm CLOSES at the surface (MUST NOT leave open — D3, the closer would commit-close a stale rollback-kind txn wrongly, `previous=displaced_version` = the M4 strand).

**C — diagnosis**

- **I-C1** Unit verify refusal ⇒ halt detail carries `GATE_ATTRIBUTION_DETAIL`/verifier reason; `none captured` impossible on
  the unit path.
- **I-C2** Start failure ⇒ the first stderr line reaches the halt detail (today it is discarded after the WARN).
- **I-C3** Success clears both stash channels (`HANDBACK_LAST_ERROR` empty, `HANDBACK_ERROR_FILE` absent) — no stale seed.
- **I-C4** Hand-back entry clears: a pre-hand-back resolver/plist failure cannot surface as *this* failure's cause.
- **I-C5** Subshell survival: the verifier's stash survives `$( )` (file channel) — the MAJOR #1 regression must stay fixed.

**D — restart arm**

- **I-D1** Each of the seven refusal classes (`restart.sh:122,136,148,155,161,165,173`) writes one `halt` row naming a stable
  class slug + the run_id, before `exit 78`.
- **I-D2** Exit codes and `_warn` wording are unchanged.
- **I-D3** A TORN/unwritable journal does not change the exit path (best-effort `|| true`).
- **I-D4** The scope-arm rc=3 case no longer journals `kind=budget … timeout` (twins' shape).

**E — lane hygiene**

- **I-E1** `daemon/services/upgrade_journal_sweep.py` and `daemon/tools/upgrade_tools.py` are untouched (S6/S10).
- **I-E2** C-10a blocks byte-identical (S8).
- **I-E3** *Hygiene lock — restated (REVIEW WAVE 2, BL-5):* **no new EXPORTED env var, no new OPERATOR knob** (i.e. nothing
  added to `_numeric_knob`'s operator surface, nothing added to the exported knob family A21, nothing new for the C-10a
  table), and **no new journal EVENT kind**. The one seam this lane adds — `_GATE_ATTR_DEFER_S` — is **not** an exception
  to the lock and must not be cited as one: it is a **clamped** (`[0,5]`, D3), **marker-gated** (a non-default value is
  honoured only under `ENSEMBLE_TEST_GATE_DEFER=1`), **non-exported internal bound** that is *routed through* the
  `_numeric_knob` helper for its integer parse (loud-garbage note) but **is not registered as a knob** — no `TABLE_` row, no
  C-10a entry, no operator documentation surface. Positive shape to assert (A7's leg): `_GATE_ATTR_DEFER_S` appears in no
  `export` statement, in no C-10a table copy, and the resolved value is observable only via the slow-path warning.
  (`git diff` review + grep.) The old blanket "no new `_numeric_knob`s" is **withdrawn as too strong**: it contradicted
  §5.4/D3, which mandates the router. A7's assertions are re-cut against this wording (phase2 §6.1 A7).

### Negative-proof matrix template (Phase 2 owns the fill-in)

| Leg | Setup | Expected | Non-vacuity proof |
|---|---|---|---|
| N1 (incident) | owned daemon + inherited-fd child, green, right version | accept | revert R2/R3 ⇒ halts (counter-mutation) |
| N2 (foreign) | unrelated pid holds `:PORT`, green spoofed by a proxy | refuse | remove the refusal classification ⇒ accepts (counter) |
| N3 (dual-supervisor) | launchd pidless job + nohup daemon serving | refuse | — |
| N4 (wrong version) | green, `version=old` | refuse | — |
| N5 (no listener) | green via a vanished answerer | refuse | — |
| N6 (unattributable) | supervisor ctx + unreadable/`/` cgroup + no `ps` | **transient `2` after ≤ `_GATE_ATTR_DEFER_S` seconds (per shell)** with the distinct `UNATTRIBUTABLE … deferring to the verify budget` marker — **refusal only at budget end** through the floor-wins verdict (leader D1; `I-B8`, `I-B10`). *The earlier "refuse after ≤ `_GATE_ATTR_DEFER_S`" cell encoded the REJECTED terminal-fail-fast exhaustion (BL-5).* | measure **slept seconds** (never iterations) and assert the gate rc was **2**, not 1 |
| N7 (rc matrix) | stub rc 0/3/4/7/124 per site | honest labels per I-A1..A4 | — |
| N8 (stash) | verify refusal | non-empty `hand-back error:` | — |
| N9 (restart classes) | one per refusal class | one named row each | — |

---

## 15. Open decisions for the dispatcher (recommendations included)

1. **rc=3 policy** (§4.1): recommend *no journal row, `_log` only* (deviates from "taxonomy at all three sites" literally).
   Alternative: journal `kind=nonzero_exit` for rc=3 as well — then Phase 2's "healthy boot journals nothing" assertion must
   be written as "no `kind=budget` rows" instead. **Recommendation: no row.** **✅ RESOLVED (`D-P1-1`, decisions.md §1 row 1,
   2026-11-17): no journal row — ADOPTED as recommended** (review wave 4 closes the open framing).
2. **Deferral constant exposure** (§5.4): recommend a test-only seam (`_GATE_ATTR_DEFER_S`, env-visible, not a knob) at
   **5 s**. Alternative: promote to a `_numeric_knob` + C-10a numeric update — more visible to operators, more churn.
   **✅ RESOLVED (`D-P1-2` + leader amendment D3, decisions.md §1 row 2, 2026-11-17): the test-only seam ADOPTED at 5 s —
   with the amended letter (router integer-parse + mandatory `[0,5]` clamp, never exported)** (review wave 4 closes the
   open framing).
3. **T11/T12 (optional items)**: `promote.sh:1051-1056` `HB_ERR` (one line, recommended **in**) and the launchd
   `_flip_verify_port_attribution` widening (recommended **in only if** Phase 2 can prove the immediate-refusal leg on a
   Darwin stub; otherwise defer to a follow-up lane — the Linux incident does not require it).
   **✅ RESOLVED (leader rulings D-P1-3 + D-P2-4, 2026-11-17): T11 RIDES the lane; T12 is DEFERRED to a named follow-up
   lane and `NP-11` is dropped as a declared parity gap.** See `decisions.md` §1 row 3 and §6.

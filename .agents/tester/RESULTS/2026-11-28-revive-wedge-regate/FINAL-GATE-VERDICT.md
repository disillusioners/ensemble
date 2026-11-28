# FINAL GATE VERDICT — revive-wedge terminalization, FULL RE-GATE at the merged tip

Date: 2026-11-28
Lane: `fix/revive-wedge-terminalization`
Worktree: `/home/nea/ensemble-src-wt-revive-wedge`
Tip under test: **`3a04a78b41344b8f3e7986d3b97672edc843d513`** (merge of origin/latest `5cc5912f7` into the lane — "landing prep 2")
Base (origin/latest, narrow guard ABSENT): `5cc5912f706fda7cc2793973aaf8521aceec19d1`
`daemon/services/child_reports.py` sha256 at tip: `717c9fc4cf00dbb0801346b30316f71763d7aad08a187288b26ea61b508a22a5` (matches the commission)
Evidence dir: `.agents/tester/RESULTS/2026-11-28-revive-wedge-regate/`
Prior verdict being re-proven: `RESULTS/2026-11-26-revive-wedge-gate/FINAL-GATE-VERDICT.md` (measured at tip `1ace8e202`, child_reports sha `5d137dd16f1e…` — the refactored tip was NEVER re-proven on Component A until now)

---

## OVERALL VERDICT: **PASS at sha `3a04a78b41344b8f3e7986d3b97672edc843d513`**

No blocking leg. **Zero net-new reds.** Both directions of the bidirectional requirement are pinned,
behavioural, and mutation-proven.

---

## 1. Per-pack results — every pack at its REGISTERED invocation (11/11)

| # | Pack (PACKS.md row) | File | Coll | Pass | Fail | Desel | Skip | Wall | RESULT |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `revive_wedge_parent_completion_guard_unit_test` (L14) — **C** | `tests/unit/services/test_parent_completion_guard_pins.py` | 11 | 11 | 0 | 0 | 0 | 9 s | **PASS** |
| 2 | `revive_wedge_child_reports_narrow_guard_pg` (L30) — **A** | `tests/postgres/test_child_reports_narrow_guard_pg.py` | 1 | 1 | 0 | 0 | 0 | 11.19 s | **PASS** |
| 3 | `revive_wedge_obligation_triple_discriminator_pg` (L31) — **A** | `tests/postgres/test_obligation_triple_discriminator_pg.py` | 5 | 5 | 0 | 0 | 0 | 13.53 s | **PASS** |
| 4 | `revive_wedge_report_deferred_savepoint_pg` (L32) — **A** | `tests/postgres/test_report_deferred_savepoint_pg.py` | 4 | 4 | 0 | 0 | 0 | 13 s | **PASS** |
| 5 | `revive_wedge_report_injection_prefix_boundary_unit` (L33) — **A** | `tests/repositories/test_report_injection_prefix_boundary.py` | 2 | 2 | 0 | 0 | 0 | 0.47 s | **PASS** |
| 6 | `revive_wedge_b4_child_report_obligation_stay_green` (L34) — **A** | `tests/unit/services/test_b4_child_report_obligation.py` | 9 | 9 | 0 | 0 | 0 | 7 s | **PASS** |
| 7 | `revive_wedge_terminal_row_status_strand_stay_green` (L35) — **A** | `tests/unit/services/test_terminal_row_status_strand.py` | 16 | 16 | 0 | 0 | 0 | 11 s | **PASS** |
| 8 | `revive_wedge_fp4_premature_terminal_stay_green` (L36) — **A** | `tests/job_queue/test_fp4_premature_terminal_pins.py` | 8 | 8 | 0 | 0 | 0 | 11.46 s | **PASS** |
| 9 | `revive_wedge_round2_council_fixes_stay_green` (L37) — **A** | `tests/job_queue/test_round2_council_fixes.py` | 15 | 15 | 0 | 0 | 0 | 10.90 s | **PASS** |
| 10 | `revive_wedge_job_result_summary_gate_stay_green` (L38) — **A** | `tests/job_queue/test_job_result_summary_and_gate.py` | 15 | 15 | 0 | 0 | 0 | 11.09 s | **PASS** |
| 11 | `revive_wedge_system_log_search_paging_unit_test` (L53) — **B** | `tests/unit/tools/test_system_log_search_paging_pins.py` + `tests/test_system_log_tools.py` | 101 | 101 | 0 | 0 | 0 | 7 s | **PASS** |
| | **TOTALS** | | **192** | **192** | **0** | **0** | **0** | | **11/11 PASS** |

**FAILED ids: none. TIMEOUT: none.**

Plus the pack ADDED by this re-gate:

| Pack | File | Coll | Pass | Fail | Wall | RESULT |
|---|---|---|---|---|---|---|
| `revive_wedge_direction1_canonical_unit_test` (NEW, registered) | `tests/unit/services/test_child_reports.py::TestReviveWedgeDirection1Canonical` | 1 | 1 | 0 | 11 s | **PASS** |
| (whole-file net) | `tests/unit/services/test_child_reports.py` | 66 | 66 | 0 | 22 s | **PASS** |

### Skip-vacuity control
A green that is really a skip is the house trap (the scrubber strips `POSTGRES_*`, so a DB-gated file can
report RC=0 with every node skipped). Controls applied: `grep` for
`skipif|pytestmark|POSTGRES|requires_db|DATABASE_URL` across the sg2 net files → **0 hits**;
`grep -cE 'FAILED|SKIPPED|deselected|ERROR'` over the sg1 raw captures → **0**; PG legs ran with `-rs`
emitting **zero** skip lines and assert `assert _dbname == "ensemble_test"` **inside** the test.

---

## 2. Direction 1 — REVIVE TERMINALIZES (the added pin)

**Node id:** `tests/unit/services/test_child_reports.py::TestReviveWedgeDirection1Canonical::test_revive_then_new_anchor_terminalizes_and_persists_report_injection`

**Why it had to be added:** an independent read-only inventory proved **no existing pin** exercised the
canonical shape. Two gaps were universal — **(a)** ZERO tests in `tests/` asserted the literal
`completion report skipped: reason=idempotency_skip`; **(d)** NO test asserted a `report_injections` row
was persisted.

**What it asserts (all four canonical parts):**

| Part | Assertion | Line |
|---|---|---|
| (a) | the log line `completion report skipped: reason=idempotency_skip` is NOT emitted (`caplog` on `daemon.services.child_reports`) | L3272-3280 |
| (b) | 2nd call returns `regular_child_completed` AND the child row reaches `InstanceStatus.COMPLETED.value` (not stuck `running`) | L3282-3288 |
| (c) | report sources are exactly `[internal_report:{child}:anchor-1, internal_report:{child}:anchor-2]` (1st row remains + new one minted) | L3290-3294 |
| (d) | exactly TWO `report_injections` rows minted at `PENDING`/`SYNC_MINT`, then drained by `ReportInjectionRepository.claim_for_injection` to terminal `INJECTED` with `delivered_at` stamped | L3296-3359 |

**GREEN @ tip:** node alone **1 passed** (RC=0); whole file **66 passed** (RC=0). Raw:
`pin-direction1-new-node.out`, `pin-direction1-full-file.out`.

**RED @ base `5cc5912f7` (genuine assertion red, exit 1):**

```
tests/unit/services/test_child_reports.py:3273: in test_revive_then_new_anchor_terminalizes_and_persists_report_injection
    assert not any(
E   AssertionError: Direction-1: the idempotency_skip log line must NOT be emitted for a revived child completing under a new anchor
E   assert not True
```

Captured log at base (the canonical wedge signature):

```
INFO daemon.services.child_reports:child_reports.py:3683 Instance rw-child... parent_id=rw-leader, status=running
INFO daemon.services.child_reports:child_reports.py:4699 Instance rw-child... completion report skipped: reason=idempotency_skip
```

⇒ at base the instance row stays **`running`** and the report is suppressed. Base mechanism proven
statically: WIDE single-arm prefix guard (status set `[READY, PROCESSING, COMPLETED]`) with an
unconditional early return at `child_reports.py:4700`, **before** the CAS at `:4902`
(`_process_child_completion_db_sync` spans base L3636-5794).

**Base-leg vacuity guards:** overlay sha256 pinned on both sides
(`8cdf6af619e01ef26bf8f8e122b44dde1230d8864a7c5416c5fd6d4976db7632`), blob-level proof the copy was not a
no-op (`7ce534a7…` ≠ base `a27c4d4d…`), and an **in-session probe** proving `daemon.__file__` /
`child_reports.__file__` resolve INSIDE the base tree while pytest ran. `-P`/`PYTHONSAFEPATH=1` was
prohibited — it silently resolves `daemon` to the TIP tree and would have produced a **false green**.

**Corroboration:** a second worker reached the same RED independently via a `git archive 5cc5912f7`
extract in `/tmp/rw_vacuum` (same captured skip line; the isolated (d) check fails with
`report_injections rows: 1` — the revived lifecycle mints none).

### A correction to the commission's wording (evidence-cited)
The commission asks for the `report_injections` row to "reach `completed`". **`ReportInjection` has no
literal `completed` state** — its enum is `PENDING, DEFERRED, INJECTED, TASK_DELIVERED, FAILED`
(`daemon/repositories/report_injection/models.py:109-121`); the literal `COMPLETED` belongs to the
companion `message_queue` report row. The pin therefore drives the **real delivery pass**
(`repository.py:1555` `claim_for_injection`, `PENDING → INJECTED` + `delivered_at` at `:1774`) and asserts
the row reaches its **terminal delivered state `INJECTED`** — the substantive requirement, satisfied
without a loophole, and fully unit-observable (no PG seam).

---

## 3. Direction 2 — DUPLICATE DELIVERY STAYS STOPPED (the `58fc594eb` regression)

Pins are **behavioural** (they assert a suppressed second delivery / row count), not predicate-level:

| Pin | Scenario | GREEN @ tip | RED under M2 |
|---|---|---|---|
| `tests/unit/services/test_child_reports.py::TestReviveWedgeDecoupling::test_a_mint_suppressed_call_still_terminalizes_the_child` | prior READY/PROCESSING under anchor-A, incoming anchor-B ⇒ no second row + `idempotency_skip` | **YES** (part of 8-passed baseline) | **YES** |
| `tests/unit/services/test_stale_running_sweep_rider_pins.py::TestNit1MintSitePrefixGuard::test_prior_pending_report_under_a_different_anchor_blocks_a_second` | prior READY under a different anchor ⇒ exactly ONE report row | **YES** | **YES** |

## 4. ARM A — same-anchor COMPLETED duplicate still suppressed

| Pin | GREEN @ tip | RED under M2 |
|---|---|---|
| `tests/unit/services/test_child_reports.py::TestReviveWedgeObservability::test_an_arm_a_only_suppression_emits_exactly_one_arm_a_record` | **YES** | **YES** |
| `tests/unit/services/test_stale_running_sweep_rider_pins.py::TestReviveWedgeNarrow::test_same_anchor_re_supplied_with_prior_delivered_report_does_not_mint` | **YES** | **YES** |

---

## 5. Counter-mutation legs (house discipline — a pin without a counter-mutation is a green comment)

Baseline control (unmutated tip, all five target nodes): **8 passed, RC=0**.

### M1 — re-widen ARM B to include COMPLETED (the pre-fix guard)
Mutated sha256 `6e4113dd035117c477a9fc6b473555aa5c5edbe015d722d6da89ebb7fc8e0309`
(ARM B `status.in_` → `{READY, PROCESSING, COMPLETED}`; ARM A untouched).

| Target | Expected | Observed |
|---|---|---|
| **Direction-1** (the added pin) | **RED** | **RED** — `expected regular_child_completed, got 'idempotency_skip'` |
| Direction-2 (D2a + D2b) | GREEN | **GREEN** — 5 passed, RC=0 |
| ARM A (AA1 + AA2) | GREEN | **GREEN** — 2 passed, RC=0 |

⇒ **M1 PASS**: the narrowing is load-bearing for revive-terminalization, and re-widening it does NOT
break the duplicate-delivery protection.

### M2 — disable the WHOLE guard (live consumer `child_reports.py:5257` `if existing_report is not None:` → `if False:`)
Mutated sha256 `5c05d59b47e6e2b71a28df5b8068f4505ddf2f96cf5e32a9c54009fb07aed2cf`.

| Target | Expected | Observed |
|---|---|---|
| **Direction-2** (D2a + D2b) | **RED** | **RED** — 5 failed (a second row is minted; the `58fc594eb` protection is gone) |
| **ARM A** (AA1 + AA2) | **RED** | **RED** — 2 failed (same-anchor duplicate now mints) |
| Direction-1 (informative) | — | GREEN (1 passed) |

⇒ **M2 PASS**: both the cross-anchor and same-anchor suppression paths are genuinely load-bearing.

### Restore proofs (both legs)
`sha256sum daemon/services/child_reports.py` → **`717c9fc4cf00dbb0801346b30316f71763d7aad08a187288b26ea61b508a22a5`** after each leg;
`git status --porcelain --untracked-files=no` → only ` M tests/unit/services/test_child_reports.py`;
`git diff --stat -- daemon/` → **EMPTY**; post-restore control runs GREEN again (D1 after M1; D2a+D2b
after M2). Raw: `mut-restore-proofs.txt`.

---

## 6. Net-new reds

**NONE.**

Classification of every red observed anywhere in this gate:
- **At the tip** (11 registered packs + the new pin + the whole 66-node file): **0 failures**.
- **At the base `5cc5912f7`** (expected, not net-new): the Direction-1 pin + 14 of the lane's other new
  pins are RED — a post-fix test file running against pre-fix code. Listed verbatim in `03-base-leg.md`.
- **Under mutation** (expected, not net-new): the M1/M2 reds enumerated in §5.

No red at the tip required classification as pre-existing vs net-new, because there were none.

---

## 7. Relevant to staging v0.31.65 (`release-staging.md` / `gate-matrix.md`)

1. **The gate tip is NOT the release merge tip.** `3a04a78b4` is the merge of origin/latest **INTO the
   lane** ("landing prep 2"). `release-staging.md` §2 step 2 requires the opposite direction — the fix
   branch merged **INTO** the integration branch (`git merge --no-ff fix/revive-wedge-terminalization`),
   and the annotated tag cut at **that** `$MERGE_SHA`. Staging v0.31.65 must tag the integration merge
   tip, not `3a04a78b`. If that merge produces a tree different from `3a04a78b`'s, this gate must be
   re-run at the new tip (the standard tip-moved caveat).
2. **The version slot must be RE-DERIVED at cut time, never reserved.** `release-staging.md` §1 names
   `v0.31.60` as the EXPECTED slot — that is now stale (written 2026-11-25/26). The artifact itself
   mandates re-enumeration immediately before the bump (`git ls-remote --tags origin 'v0.31.*'` + local
   tags + release dirs), taking the next free slot and REPORTING the change if taken. Never re-cut a
   pushed tag. v0.31.65 is plausible but is a live derivation, not an assumption.
3. **Ordered sequence (§2):** bump → merge to origin/latest → ANNOTATED TAG at the merge tip →
   stage worktree DETACHED at the tag → BUILD (`rm -rf dist/` → FE rebuild via `_build_frontend.sh` —
   stage.sh never builds the FE) → `stage.sh live` (first stage builds the binary; no `--skip-build`)
   → 4-way sha check.
4. **Stage freshness guards that will bite a careless stage:** L1 tip-identity (`stage.sh:195` —
   HEAD must equal origin/latest, else rc=78 `non-tip-tree`) and L2 provenance (`stage.sh:287` — the
   sidecar is re-hashed, never trusted). `git describe --tags --exact-match HEAD` must equal `$V`.
5. **This lane produces a STAGED release only.** `promote.sh` (and its `--f2-verified-closed`) is NOT
   run by this lane — the ceremony is the Emperor's.
6. **`gate-matrix.md`'s base statement is stale** (it still names base `35ec50e3c` = v0.31.54 and says
   "RE-DERIVE at cut time"). Its leg structure (L0–L7) remains the reviewer's executable specification;
   this re-gate supersedes its L1/L2/L4 measurements at the merged tip.
7. **Nothing in either artifact blocks staging from the test side** — provided the tag is cut at the
   integration merge tip and the slot is re-derived live.

---

## 8. Fence proofs

- Tip/branch/sha verified before any leg ran (`00-fence-proofs.md`): HEAD `3a04a78b…`, branch
  `fix/revive-wedge-terminalization`, `child_reports.py` `717c9fc4…`, `daemon.__file__` under the
  worktree (not the main checkout), tracked tree clean, `uv.lock` `82c785f5…` unchanged, base
  `5cc5912f7` an ancestor.
- **No live contact**: never the live daemon, never `ensemble_prod`, no promote/restart/releases-current.
  `ensemble_prod` / `ensemble_prod_test` do not exist on the dev cluster; `ensemble_dev` is in the
  conftest's `FORBIDDEN_DB_NAMES` and was never targeted.
- **Throwaway DB only**: `ensemble_test` (pre-existing, empty, in the conftest's hard allowlist);
  per-variable `env -u` scrub of all 12 names — **no `env -u POSTGRES_*` glob** (bash does not glob env
  names; that idiom is a silent no-op).
- **No production code modified.** The only production-file edits were the M1/M2 mutations, each
  restored byte-exact (`717c9fc4…`) and proven with `git diff --stat -- daemon/` empty.
- **Trees**: the three excluded trees (`/home/nea/ensemble-src`, `…-wt-liveness-truth`,
  `…-wt-husk-recovery`) untouched. The ONE authorized extra worktree
  (`/home/nea/ensemble-src-wt-revive-gate-base`) was created detached at `5cc5912f7`, restored clean
  after the base leg, and removed at cleanup.
- **The 82 prior-gate untracked files** under `RESULTS/2026-11-26-revive-wedge-gate/` were left
  untouched and **not committed** (preserved as-is; the commission left that to the gate's judgement).

---

## 9. Deviations & incidents (all surfaced, none blocking)

1. **A worker wedged and was replaced once.** The original Direction-1 author (`e26d9a48`) went silent
   for 1h23m with status `running` (a hung turn). Terminated; ONE replacement dispatched, which audited
   the inherited file first (`ast.parse` + `--collect-only` + `git diff`) and found the predecessor's pin
   complete and green — then applied only the (d) refinement. No work was lost; no third attempt was made.
2. **Prior-gate untracked count is 82, not the commission's ~89** — all 82 match the
   `2026-11-26-revive-wedge-gate` path pattern; zero tracked modifications; none discarded.
3. **venv `dist-info` = 0.31.60 vs pyproject/uv.lock = 0.31.64** — confirmed present as the commission
   predicted; `uv sync` was NOT run (it can rewrite a tracked `uv.lock`); `git diff --stat` empty.
4. **`ReportInjection` has no literal `completed` state** — see §2's correction; the pin asserts the
   terminal delivered state `INJECTED` instead, which is the substantive requirement.
5. **The dash/`PIPESTATUS` trap** bit three pack workers (`244445b8`, `4dfd591b`, `0afeb003`). In every
   case the pack was unaffected and the exit code was re-captured POSIX-safely. POK already carries it;
   a skill-evolution fix was dispatched to the skill-keeper for `test-pack-execution`.
6. **`snapshot_create` refused twice** for one worker (it could not self-identify its instance id) —
   optional; the durable state is in the RESULTS dir and `git worktree list`.

---

## 10. Overall status

| Component | Result |
|---|---|
| Unit Tests (8 packs, 177 nodes) | ✅ **PASS** |
| Real-PG Tests (3 packs, 10 nodes) | ✅ **PASS** |
| Direction-1 pin (added) | ✅ **GREEN @ tip / RED @ base / RED under M1** |
| Direction-2 pin (`58fc594eb` protection) | ✅ **GREEN @ tip / GREEN under M1 / RED under M2** |
| ARM-A pin | ✅ **GREEN @ tip / GREEN under M1 / RED under M2** |
| Component B ride-along | ✅ **PASS** (101/101) |
| Component C | ✅ **PASS** (11/11) |
| Net-new reds | ✅ **none** |
| **GATE** | ✅ **PASS at `3a04a78b41344b8f3e7986d3b97672edc843d513`** |

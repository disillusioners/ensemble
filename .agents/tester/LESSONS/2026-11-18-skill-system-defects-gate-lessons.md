# LESSONS — skill-system-defects tester gate (2026-11-18)

Durable, project-level findings from the Tier-2 gate of `feature/skill-system-defects`
(base `0181dadbf` → gate HEAD `52ae65eaf`, + 2 gate test-only commits).
Evidence: `RESULTS/2026-11-18-skill-system-defects-tester-gate.md`.

---

## L1 — `getattr(obj, "attr", None)` + a MagicMock row = a TRUTHY guard (the lane's net-new 3)

**Pattern.** A production guard written as

```python
if getattr(ab_test, "resolved_at", None):
    raise ValueError("already resolved")
```

is defeated by a consumer test whose row is a `MagicMock`: MagicMock auto-creates
`resolved_at` as a **truthy** object, so the guard fires on a pair that was never resolved.

**Blast radius found here.** `daemon/services/skill_evolution_service.py:2009` (Path-2 mirror
guard, commit `868c6c616`) reddened `tests/unit/test_composite_scoring.py::TestTieBreaking` ×3
— a **pre-existing, byte-identical** consumer the lane never touched. The lane updated its own
new test and missed the consumer.

**Rule (house, reinforced).** The `getattr(x, "k", None)` default-value idiom is **only** safe
when `x` is a real row. Any consumer that fakes a row must set the probed attribute explicitly
(`ab_row.resolved_at = None`) — the same rule as the documented MagicMock trap in
`secrets_tools`/`settings_tools` `_resolve_actor`. Test-side fix, one line, base-inert
(verified 14/14 green at base with the same file).

**Checklist for any future `getattr(..., None)` guard.** (1) grep production for
`getattr(` + `, None)` truthiness tests; (2) grep tests for MagicMock rows feeding those
objects; (3) run the **consumer** files, not just the lane's own new tests.

## L2 — lane-reach detection must allow INDENTED imports

The consumer that broke imports the lane-touched module **inside a function body**
(`tests/unit/test_composite_scoring.py:366`). A line-anchored `^from daemon\.` grep reported
"no lane reach" for all 17 head_only files and pointed the verdict at ambient flake.
The correct regex must allow leading whitespace (`^[[:space:]]*(from|import)`), and the
reach check must be per-**test-file**, not per-statement.

## L3 — `git checkout <rev> -- <path>` stages the file; plain `git restore` then restores the MUTATED index content

Observed while counter-mutating with a whole-file pre-fix revert (`git checkout 5b8fef156^ -- scripts/audit/*`):
the file lands **staged**, so a subsequent `git restore <file>` restores the *mutated* content
from the index — the sha256 PRE==POST restore proof would silently "pass" on the wrong bytes.
Recovery: `git restore --source=HEAD --staged --worktree <file>`, then verify
`sha256sum -c` AND `git diff HEAD` empty. Always assert `git diff --cached` empty before a restore proof.

## L4 — a small probe slice under-projects the wall-clock by ~5×; re-project from a REAL leg

A 150-node serial probe projected 11.8 min for the 38.7k-node corpus; the true rate was
10.5–13.6 nodes/s ⇒ ~40 min/leg, i.e. the probe was **5× optimistic** (the slice was
fast-unit-biased). Recipe v1.0.1's re-projection checkpoint exists for exactly this.
**Rule:** size shards from a ≥1,000-node leg's measured rate, and re-project after the first leg.
Probe slices are for order-of-magnitude only.

## L5 — fence-poison files kill xdist workers (rc=3 INTERNALERROR), not just fail

17 files carry a session-scoped `pytest.exit` fence guard (allow-list family
`{ensemble_dev_test_only, ensemble_test_only}` — both ABSENT on this host). Under
`-n auto` each kills its worker mid-shard ⇒ controller `assert not crashitem` ⇒ **rc=3
INTERNALERROR**, and `-q` loses all failure names.
**Rule:** classify fence-guarded files statically, pull them into **ONE serial leg** run with
`-u POSTGRES_DB` (the fence self-heals; both legs then agree). Base leg: 435P/rc=0.
A shard that exits rc=3 must be re-split — never interpreted as "test failures".

## L6 — collection itself can die: the dispatch-verbatim collect hits the g7 trap

`pytest --collect-only -q` (default form) dies **rc=3** on `tests/packs/g7_unique_index_smoke_test.py`
(`sys.exit(0)` at import). The recipe form `--ignore=tests/packs --ignore=tests/e2e/test_context_injection_hybrid.py`
collects clean (40,046 items). The same `--ignore` advice applies to the e2e file (import-time HTTP GET).

## L7 — `ENSEMBLE_BASH_TRACKING_ID` is an ambient red source

`tests/tools/test_bash_spawn_stamp.py` ×2 red on **both** legs and are **3/3 green on both
sides** once `ENSEMBLE_BASH_TRACKING_ID` is scrubbed. Add `-u ENSEMBLE_BASH_TRACKING_ID`
to the standard gate fence (it is currently missing from the documented scrub list).

## L8 — digest form asymmetry must be reconciled before any net-new claim

The HEAD artifact contained 22 ids absent from its grep form (junitxml `ERROR at setup` style
captures). Left unreconciled they would read as net-new. Reconciliation showed all 22 are
well-formed ids and **all 22 are present in the BASE list** ⇒ 0 contribution.
**Rule:** fold FAILED/ERROR to one form, prove the fold is **lossless on both sides**
(`sed 's/^FAILED //; s/^ERROR //'` ⇒ byte-identical to the bare list), then compare — and
cross-check with the canon comparator (`sweep_digest_verdict.sh`) before believing any count.

## L9 — check ALL active quarantine rows before adding one

Two of the three "new" flaky members were **already covered** (row 154 for
`test_job_feedback_observer`; a standing StaticPool row for `test_dependency_bus`, cross-referenced
from row 147). Searching only the families named in a dispatch brief produces duplicate rows.
**Rule:** grep the whole Active table for the node id, then decide new-vs-re-observation.
Also: a worker's summary tally can under-count (per-id table is authoritative) — reconcile counts
against the per-id rows before publishing a verdict.

## L10 — a grep pin can be satisfied by your own replacement text

While counter-mutating the decision-marker pin, the replacement comment contained the word
"decision" ⇒ the pin passed on prose. Caught by an explicit window check, the attempt was
**discarded**, the file restored sha-verified, and the cycle re-run with neutral wording → RED.
**Rule:** mutate with text that cannot satisfy the pin's own matcher, and re-check the
window/anchor bounds on every cycle.

## L11 — run the two regression legs SEQUENTIALLY, and keep the fence identical

Host load changes the measured flow (2.3–2.8 during the HEAD leg) — two concurrent xdist fleets
would have corrupted both the wall-clock projections and the shard-order ambient classes that
the digest relies on. Identical METHOD both legs (file-boundary round-robin, measured-rate
sizing, poison-leg design), not identical file lists (the branch adds nodes).

## L12 — the Tier choice is mechanical: `daemon/manager.py` counts

The preflight report said "Tier 1" while its own evidence showed `daemon/manager.py` touched.
ensure.md's core-seam list includes `daemon/manager.py` ⇒ **Tier 2** (whole-tree dual-side).
Escalation on that list is mechanical, never a judgment call, and the diff size is irrelevant.

## L13 — a 90-min advisory budget is not a reason to weaken the mandated scope

ensure.md's Tier-2 budget is **advisory**; the commission's criterion ("the relevant full unit
scope on base and on the branch") outranks it. The correct move is to raise the ceiling with a
loud disclosure, NOT to re-scope down to Tier-1 — a re-scope would have silently gutted the
net-new detection the commission asked for.

## L14 — (OPEN, leader decision) audit scripts print the DSN — with the cleartext password — to stderr

`scripts/audit/skill_stale_deactivate.py` (newly added by lane commit `5b8fef156`) and
`scripts/audit/skill_counter_reconcile.py` (pre-existing) print the full DSN — including
`POSTGRES_PASSWORD` — to stderr. pytest's captured stderr therefore carries the credential into
evidence packs / CI logs. Fix shape: redact the DSN before printing + a stderr-redaction pin.
Not applied by the gate (production frozen). Rotation stays user-gated (`deferred_key_rotation`).

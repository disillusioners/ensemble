# Contract Amendment: Dry-Run Projection (v3.2) — Maintenance Console

Date: 2026-09-28
Architect: architect (controller) — competitive fan-out, 3 workers (same skill `data-flow-design`, different approaches), all reports received with skill confirmations.
Workers: `1e53a6e8` (Option A — enriched projection) · `b13e9e10` (Option B — single-run reclaim + safety deep-dive) · `ff4ebb28` (Option C/hybrid + product-honesty framing).
Analysis surface: worktree `latest @ d0cf20b9` (clean; the former `feature/maintenance-console` work is present here). Incident evidence: devops-verified on the ensem.dev VM (PG 18.4 `ensemble_prod`).

---

## VERDICT

**Adopt A+C as Contract v3.2. Defer B (single-run full reclaim) as a structured v3.3 candidate with two named preconditions.**

The product-honesty principle (hybrid worker, adopted): **a destructive-op confirm dialog is a consent instrument and must promise only what THE ACTION being consented to will do (per-run honesty); the dry-run panel is an estimation instrument and must answer "what will this cleanup ultimately reclaim" (journey honesty). The v3 defect exists because one number was asked to carry both duties. Split the surfaces; don't collapse them.**

- **A+C** implements the split: the echo gate stays bound to the delivery-matched per-run number (AM-3 semantics unchanged, zero gate drift), while the dry-run panel gains labeled, non-gate-bound projection fields + journey copy that answers the incident user's actual question. Additive-only; AM-2/AM-3/INV-3/INV-13/cases 25/44/56/57 all stand untouched.
- **B** is the only option that eliminates the two-run journey, and its refs-alias hazard was **disproved** (valuable negative result, below) — but aggregation found its safety and verifiability stories each have a hole that must be closed before it can be ratified. Deferring is the disciplined call, not a rejection.

---

## The incident (constraints any fix must honor)

Never-pruned prod DB: 74,058 blobs / 11 GB, **zero current orphans**, retention never ran, top thread 1,928 checkpoints. Manual dry-run correctly returned `blobs:0 / bytes:0` next to `rows:104,501 / writes:273,293` — because manual execute composes **E→D** (AM-2): pass 1 frees 0 blob bytes; Op D's row deletions orphan the 11 GB for a LATER pass. The user asked "what will cleanup ultimately free" and the console answered "0". A local Mac DB with 29.3 GiB already-orphaned blobs behaves like pass 2 — which is why local testing masked this. Deployment: v0.16.0 on systemd; auto-cycle (24 h) has not yet run; any fix branch cuts from freshly-pulled `latest`.

---

## Aggregation rulings (cross-report contradictions resolved)

### R-1 · 🔴 Field-semantics bug in Option A as proposed — FIXED in this amendment

The A-worker specified `bytes_reclaimable_after_row_prune` as "computed against the SIMULATED newest-N survivor set" (the **post-D superset**: current orphans ∪ D-unlocked blobs) but then defined `bytes_reclaimable_total = now + after_row_prune`. These are incompatible: since Op D only *removes* referencers, the post-D orphan set is a **superset** of the current one (`after ⊇ now`), so `now + after` **double-counts** the `now` portion. The hybrid worker's subset analysis and the A-worker's total math cannot both be true.

**Ruling (authoritative definition for v3.2):** `bytes_reclaimable_after_row_prune` is the **DELTA** — blobs that are currently *referenced* (therefore NOT in `bytes_reclaimable_now`) whose only referencers are excess rows that Op D of THIS pass will delete. Equivalently: `post_D_orphan_set − now_set`. Then:

- Pass 1 (E→D) frees `bytes_reclaimable_now`.
- Pass 2's E frees `bytes_reclaimable_after_row_prune` (on a quiescent DB — plus any drift).
- `bytes_reclaimable_total = now + after_row_prune` is now coherent and equals the two-run delivery.
- Computation: one anti-join per excess pair against the simulated survivor set, **minus** the current-orphan contribution of that pair (the dry-run already computes the current-orphan number per pair — subtract it; or compute directly as "referenced-by-excess-only").

### R-2 · Option B safety gap — the Step A→Step B window is NOT closed by the prescribed mitigation

The B-worker's late-write analysis is correct and valuable: LangGraph `aput` keys blobs by `(thread, ns, channel, version)` with **fresh per-channel monotonic versions** (`langgraph/checkpoint/postgres/aio.py:230-296`) — so a late checkpoint **cannot reference an old blob**; the refs-alias corruption class is **structurally impossible** (verified negative result; recorded for any future revisitation of B).

However, the same analysis identifies a NEW live-data-loss window that B's prescribed guard does not close: a write committed **between the pair's Step A** (CTE keep-set compute + row DELETE, one snapshot) **and Step B** (blob DELETE vs keep-set, a later statement with a fresh snapshot): its checkpoint row survives D (invisible to A's snapshot) but its NEW blob is deleted by E-vs-keep-set (nothing in the keep-set references `(channel, V_new)`). Today's live-table anti-join would rescue that blob (the late checkpoint itself is a referencer). The prescribed CTE atomicity closes only the *intra-statement* window. Closing the A→B window requires either (i) wrapping the pair's compute+D+E in ONE SERIALIZABLE transaction (SSI detects the rw-antidependency; retry re-adjudicates the keep-set — the B-worker's mitigation #3, which its own report declined to require), or (ii) a pre-Step-B guard: re-check for checkpoint rows created after Step A's snapshot in this pair and re-derive/defer. Either raises B's complexity above its "one sentence + one field" verdict.

### R-3 · Option B verifiability gap — promise drift under INV-13

The AM-3 gate compares the client's echoed `expected_bytes` against the **stored** dry-run number — two readings of one row, always equal (the W-1 lesson). Under B, what needs verifying is **stored projection vs what execute's fresh keep-set will actually deliver**, and that comparison happens nowhere: INV-13 forbids the post-run version, and no pre-run recompute is specified. Drift between dry-run and execute (live writes shifting newest-N) is therefore *silent* under B, and B's number is more drift-exposed than today's (every new checkpoint in an affected pair shifts the keep-set; today's number excludes pending orphans entirely). A pre-run **projection-recompute gate** (execute recomputes the post-D projection and refuses on `byte_count_mismatch` vs stored) is INV-13-compatible (it is pre-run, not post-run) and would make B's promise verifiable — at the cost of running the projection scan twice (on the incident DB: the dry-run is minutes-scale; execute's pre-phase doubles that). This cost/benefit call belongs to a v3.3 decision, not this amendment.

### R-4 · MAX_REFS_EXCEEDED over-promise trap (hybrid worker — adopted)

The incident's 1,928-checkpoint top thread may exceed `CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD`; pairs over the cap are skip-gated and will not be reclaimed until the cap is raised. **The projection MUST exclude skipped pairs (contribute 0) and the FE MUST flag when `skipped[]` is non-empty** ("cleanup effectiveness may be understated") — otherwise `bytes_reclaimable_total` permanently over-promises. Skipped pairs are also excluded from the delta computation (their excess rows still delete; their blobs' reclaimability is unknown — the flag covers the honesty gap).

### R-5 · Surface confinement (A-worker vs hybrid-worker divergence — resolved)

- New fields live on the **manual dry-run §3 response** and travel via `dry_run_summary_json` (already snapshotted onto the execute row).
- The **manual_execute run summary** gains one additive `projection` block (echo of the source dry-run's two numbers) — needed so the post-run FE banner ("run cleanup again") survives a page refresh mid-poll. Minimal and audit-useful.
- **No auto-cycle involvement**: unanimous across all three reports. Auto is inline D→E (INV-1), already journey-consistent by construction (its `bytes_freed` is post-D actual), runs quiescent, and adding projection scans to the scheduled tick is pure cost. `/status` §2 frozen shape untouched (absent fields render "n/a").

### R-6 · Authorial note on the decision-log prohibition (as its author)

`decision-log.md` says "the dry-run must NOT be made to simulate post-Op-D state." That prohibition pins the **gate-bound E-arm** (`would_delete.bytes` must keep current-state semantics while execute composes E→D) — it does NOT forbid additive, labeled, non-gate-bound *estimate* fields. v3.2's projection fields are consistent with the prohibition's intent; B's re-binding of the gate-bound number would NOT be (without B's execute change), which is why the A+C-minimal gate-rebind variant was correctly rejected by the hybrid worker as a verified contradiction. **Formal rule (ratified): the echo gate may only bind a number the composition actually delivers — E→D ⇒ `now`; D→E ⇒ post-D projection. Any other pairing is incoherent.**

---

## CONTRACT v3.2 AMENDMENT TEXT (exact)

### §3 `POST /dry-run` response — ADDITIVE fields

```json
{
  "run_id": "ckpt-…",
  "would_delete": { "checkpoint_rows": 104501, "writes": 273293, "blobs": 0, "bytes": 0 },
  "would_delete_count": 0,
  "would_free_bytes": 0,
  "bytes_reclaimable_now": 0,
  "bytes_reclaimable_after_row_prune": 11811060000,
  "bytes_reclaimable_total": 11811060000,
  "scanned": { "thread_ns_pairs": 412 },
  "skipped": [],
  "duration_ms": 412,
  "fresh_until": "…+00:00"
}
```

Field semantics (normative):

- `bytes_reclaimable_now` — INTEGER ≥ 0. Current-state orphan blob bytes: what **this run** (E→D) will free. **Equals `would_free_bytes`** (retained as an explicit alias for shape-stability; the echo gate continues to bind to `would_free_bytes`, AM-3 unchanged).
- `bytes_reclaimable_after_row_prune` — INTEGER ≥ 0. **DELTA semantics (R-1):** blob bytes currently referenced but whose only referencers are the excess rows Op D of this pass deletes — i.e., what a **follow-up run** will free after this one (quiescent DB). Skipped pairs contribute 0 (R-4).
- `bytes_reclaimable_total` — INTEGER ≥ 0. Exactly `now + after_row_prune`. Informational sum-on-the-wire for glanceability; schema docstring must state it is derived and requires **two passes** to materialize on a never-pruned DB.
- All three are **projection-class, informational, NEVER gate-bound** — same AM-3 treatment as `skipped[]`. Missing fields default to 0 (v3.1 clients unaffected; fields are additive).

### Gate binding — UNCHANGED

`expected_bytes` echo gate binds to the stored dry-run's `would_free_bytes` (= `bytes_reclaimable_now`) ONLY. No new gate, no re-binding, no post-run completion gate (INV-13 reaffirmed). A negative pin must prove the gate never reads the projection fields.

### `manual_execute` run summary — ADDITIVE `projection` block

```json
"summary": { …existing…, "projection": {
  "bytes_reclaimable_now_at_dry_run": 0,
  "bytes_reclaimable_after_row_prune_at_dry_run": 11811060000
} }
```

Echo of the source dry-run's values (self-contained on the run row; powers the post-run FE banner across page refreshes). `manual_dry_run` rows carry the fields in their summary payload already. Auto rows: absent (R-5).

### FE display contract (v3.2)

- **Dry-run card**: three-number render — "This run: ~{now} · After this run (run cleanup again): ~{after} · Combined: ~{total}". Skip zero components with "—". Sub-copy on never-pruned profiles: "On a DB that has never run retention, run 1 deletes rows only; run 2 frees the blob bytes."
- **Skipped flag**: when `skipped.length > 0`, render "N pairs skipped — cleanup effectiveness may be understated" near the projection (R-4).
- **Confirm dialog** (per-run consent, journey context): *"This run will permanently delete ~{fmt(now)} of unreferenced blobs and {rows} excess checkpoint rows. After this run, ~{fmt(after)} more becomes reclaimable by running cleanup again. This may take several minutes on large databases. This cannot be undone."*
- **Post-run banner** (when execute succeeded and `projection.bytes_reclaimable_after_row_prune_at_dry_run > 0`): "Run cleanup again to reclaim ~{fmt(…)} more" — button starts a NEW dry-run (never a silent execute); disabled while a run is in flight; hides once a fresh dry-run reports `now == 0`.

### Supersession analysis — NONE

| Ruling | Status under v3.2 |
|---|---|
| AM-2 (manual E→D; auto D→E) | **UNCHANGED** |
| AM-3 (echo = sole scope pin, binds `would_free_bytes`) | **UNCHANGED** (fields additive, non-gate-bound) |
| INV-3 (SERIALIZABLE+retry, ZERO_REFS fail-safe) | **UNCHANGED** (no execution-path change) |
| INV-13 (no completion gate) | **UNCHANGED** (reaffirmed; projection is informational) |
| Cases 25/44/56/57/58 | **UNCHANGED** (all green as-is; the gate was not re-bound) |
| Auto-cycle (INV-1/INV-2) | **UNCHANGED** |

---

## Trade-off matrix (5 axes; scores /10, higher = better)

| Option | Complexity (invert) | Scalability | Maintainability | Risk (invert) | Cost (invert) | Fixes incident? | Notes |
|---|---|---|---|---|---|---|---|
| **A+C (v3.2 — ADOPTED)** | 7 | 8 | 7 | 8 | 7 | **Yes — estimation level** (real numbers + honest journey + convergence CTA) | Two-run journey remains (by design, honestly labeled) |
| B (single-run reclaim) | 3 | 6 | 4 | 3 | 3 | Yes — delivery level | Blocked on R-2 (pair-level SERIALIZABLE wrap or new-rows guard) + R-3 (pre-run recompute gate; doubles projection scan) |
| B+A | 3 | 6 | 4 | 3 | 2 | Yes | Inherits B's gaps; A's fields add display value atop either outcome |
| C alone | 10 | 10 | 9 | 9 | 9 | **No** — relabels ignorance as process | Strictly dominated; its copy is a *component* of A+C |

**B recorded as the v3.3 escape hatch** if the product owner weighs one-run ergonomics above verifiability. Its two preconditions: (P1) close the Step A→B window (per-pair SERIALIZABLE across compute+D+E, or a post-snapshot new-rows guard before E's delete); (P2) add the INV-13-compatible **pre-run projection-recompute gate** (execute recomputes the post-D projection and refuses `byte_count_mismatch` on drift vs stored) — making the promise verifiable at the cost of doubling the projection scan. AM-2's mechanism clause then supersedes per the B-worker's one-sentence restatement (prescription stands: never D-first on live state without matched dry-run). The refs-alias negative result (structurally impossible via LangGraph per-channel monotonic versions) carries forward — it permanently retires one objection to B.

---

## Test deltas (v3.2)

**BE (67 → 72; 5 new + 1 negative pin):**
1. `test_dry_run_projection_fields_on_never_pruned_db` — incident scenario: all blobs referenced by excess rows → `now == 0`, `after > 0`, `rows > 0`, `total == now + after`.
2. `test_dry_run_projection_zero_on_already_pruned_db` — post-pass: both 0; subset identity `after == 0 when excess_pairs == 0`.
3. `test_dry_run_projection_delta_not_superset` — **R-1 pin**: seed a current-orphan blob AND an excess-referenced blob in one pair → `now` counts only the current orphan; `after` counts only the excess-referenced one; `total` does NOT double-count (assert `total < now + post_D_superset` on a fixture where the superset would differ — or equivalently assert the pair-level computation excludes current orphans from `after`).
4. `test_dry_run_projection_skips_skipped_pairs` — ZERO_REFS + MAX_REFS-capped pairs contribute 0 to `after`/`total` and surface in `skipped[]` (R-4).
5. `test_convergence_after_pass_1_execute` — dry-run → execute → fresh dry-run: new `now` ≈ previous `after` (tolerance for drift; NOT equality — INV-13 spirit).
6. **Negative pin** `test_expected_bytes_echo_does_not_read_projection` — stored `after = 99`, client echoes 99 → gate MUST refuse with `byte_count_mismatch` (gate reads `would_free_bytes` only).

**FE (pins 15 → 18):**
1. `projection-fields-render` — three-number render; "—" for zero components; skipped-flag banner when `skipped[]` non-empty.
2. `confirm-message-journey-copy` — required substrings: `fmt(now)`, rows, `fmt(after)`, "running cleanup again" anchor (replaces nothing — extends the existing confirm pin).
3. `run-again-banner-when-projection-nonzero` — banner + CTA (starts dry-run, disabled in-flight, hides on `now == 0`).

**Unchanged:** error-code union (11), case 25/44/56/57/58, all v3.1 pins.

---

## AUTO-CYCLE — UNCHANGED (unanimous)

Manual-only fix. Auto keeps inline D→E, env dual-arm, quiescent idle-gated tick (INV-1/INV-2). The incident DB is unblocked by the operator's manual flow alone; auto resumes its convergent cycle from a clean baseline. Do not add projection scans to the scheduled tick; do not touch the auto summary shape.

---

## Runbook / CHANGELOG (operator note, v0.16.x)

> **CHANGELOG (fix release):** *Maintenance console dry-run now reports the two-pass reality: `bytes_reclaimable_now` (what this run frees — unchanged semantics for `would_free_bytes` and the `expected_bytes` confirm echo), `bytes_reclaimable_after_row_prune` (labeled estimate of what a follow-up run frees), and their sum `bytes_reclaimable_total`. On a never-pruned database the first run deletes excess rows and frees 0 blob bytes **by design** (retention composition E→D); run cleanup a second time to reclaim the orphaned blob bytes — the console now says so explicitly and offers a "run cleanup again" affordance. The confirm-echo `expected_bytes` meaning is UNCHANGED — scripts and operators see no behavioral change. Auto-cycle behavior unchanged. Activation: rebuild + restart; no DB migration (additive JSON keys only).*

Runbook addition: "first manual cleanup on a fresh/never-pruned DB is a two-run journey — the console's post-run banner will prompt the second run."

---

## Confidence

**High** on the A+C recommendation (all three workers converge on additive-fields + copy; the only contested axis was B, and its gaps are evidenced). **High** on the R-1 semantics fix (set algebra over the verified anti-join predicate). **Medium** on the convergence-test tolerance (live-write drift bound is empirical). Flip assumption: if the product owner rules that one-run reclamation is mandatory for v0.17, this amendment becomes the interim step and B's P1+P2 preconditions become the v3.3 workstream — the field names and delta semantics defined here carry over unchanged into B's world (`after` collapses into the single delivery number).

## Gaps

None — all three dispatched workers reported with skill-confirmation first lines and code-level evidence; the two open B-preconditions are recorded decisions, not analysis gaps.

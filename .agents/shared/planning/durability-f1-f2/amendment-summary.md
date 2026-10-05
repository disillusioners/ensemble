# Amendment Summary: Durability F-1 + F-2 (Architect Cycle)

Date: 2026-10-04
Worktree: `/home/nea/ensemble-src-wt-durability` @ `18827dbd` (branch `feature/durability-f1-f2`)
Author: planner via plan-creation worker (amendment cycle)
Source verdicts: `architecture-recommendation.md` (308 lines)

> **Cycle context.** This is the SURGICAL AMENDMENT cycle following
> the architect verdict + binding leader decisions. Architect
> verdicts on the 6-dimension review (boot-sequence ownership, F-2
> sweep design soundness, F-1 predicate math, design (a) deferral,
> rollback story, architectural smell test) are structurally sound.
> 4 REJECTs + 6 ADJUSTs + 2 plan-text accuracy fixes were specified
> as drop-in patches (P-A through P-I). This amendment applied P-A
> through P-H verbatim and skipped P-I per the binding leader
> decision (D3). Five additional binding decisions (D1-D5) and two
> open-question resolutions (Q1, Q4) were applied per the dispatch
> contract. The four REJECT blockers (R-A, R-B, R-C, R-D) were
> resolved.

> **Re-anchor notice (binding).** All references below are
> verified on the worktree at `18827dbd`; the prior plan cycle's
> anchors remain valid. Implementation occurs in this worktree; the
> main workdir `/home/nea/ensemble-src` is occupied by a sibling
> commission and is read-only to this one.

---

## 1. Files-Changed Table

| File | Lines before | Lines after | Delta | Edits |
|---|---|---|---|---|
| `decisions.md` | 701 | 882 | +181 | 5 edits (§2b insert; §2c insert; §3 step 2 ⚠ note; §3 step 6 correction + content-hash drop; §3 step 7 re-check extension; §11 insert) |
| `phase1-plan.md` | 171 | 173 | +2 | 4 edits (task 1.14 S7 added; file inventory line; S1-S6 → S1-S7 references in objective / task 1.13 / exit criterion) |
| `phase2-plan.md` | 215 | 216 | +1 | 4 edits (task 2.8 API ref + D1 ⚠; task 2.9 re-check extension; task 2.10 restructure; task 2.13 S15 added; exit criterion + file inventory line) |
| `plan-overview.md` | 334 | 348 | +14 | 2 edits (F-1 kill-switch added to In Scope; Coupling Map row for claim-gate fix branch + observation) |
| `phase3-plan.md` | 268 | 268 | 0 | (no edits — D2's kill-switch is Phase 1; no Phase 3 amendments per dispatch) |
| `research-findings.md` | 522 | 522 | 0 | (no edits — evidence base stays put per dispatch) |
| `amendment-summary.md` | 0 | NEW | (new file) | NEW — this file |

**Total delta across edited files: +198 lines.**

---

## 2. Amendments Applied (P-A through P-H)

| Amendment | Target file | Section / line | What changed (one sentence) |
|---|---|---|---|
| **P-A** | `decisions.md` | NEW §2b (after §2a, before §3) | Added the orphan-sweep interaction sub-section documenting the 30s grace at `dependency_bus.py:1984-1988` and the WC sweep as the compensating backstop. |
| **P-B** | `decisions.md` | §3 step 6 (lines 241-255 in prior cycle) | Replaced phantom `get_for_message` API with `get_for_thread` + post-filter; DROPPED the "content-hash fallback" sentence; added a load-bearing ⚠ note about the `MessageMetadata.message_id` vs `MessageQueue.message_id` ID-space mismatch. |
| **P-C** | `decisions.md` | §3 step 2 + step 7 (lines 296-307, 378-383 in prior cycle) | Added ⚠ note that `discover_pending_wakes` helper does NOT exist on the worktree (grep-verified); Phase 2 task 2.4 must add it. |
| **P-D** | `phase2-plan.md` | task 2.8 (line 74 in prior cycle) | Replaced `manager.message_metadata_repo.get_for_message(thread_id, message_id)` with `get_for_thread(thread_id)` + post-filter; added the D1 ⚠ precondition note. |
| **P-E** | `phase2-plan.md` | task 2.10 (line 76 in prior cycle) | Restructured from "wrap entire run() body" to per-parent try/except INSIDE the per-parent loop; outer envelope-try retained as redundant safety net only. |
| **P-F** | `phase2-plan.md` | task 2.9 (line 75 in prior cycle) | Extended post-lock re-check from "wake rows only" to BOTH wake rows AND parent-history ledger scan (`get_instance_messages` → `source.startswith(...)`). |
| **P-G** | `decisions.md` | NEW §2c (after §2b) | Added the F-1 predicate kill-switch sub-section with `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT` env var, mirroring the sweep's kill-switch pattern at `auto_continue_boot_pass.py:145-156`. **🟢 DATED POINTER (2026-10-04, REVISION CYCLE 2 — W-3 / DOC-CONSISTENCY PASS):** the P-G kill-switch as written governs arm 2 (which is **DROPPED per W-3**). The rescoped kill-switch that ACTUALLY governs arm 3 is `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`; see `decisions.md §2c` (RESCOPED) and `decisions.md §13c` (W-3 kill-switch rescoping). The P-G record is HISTORICAL — the operator-facing kill-switch in production is `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`, not `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT`. |
| **P-H** | `plan-overview.md` | Coupling Map (line 174-178 in prior cycle) | Added a new row to the Coupling Map for the parallel claim-gate sibling-deadlock fix branch (different site in same `task/repository.py`); added the A-4 observation about composing F-1 arm 3 with the claim-gate fix. |
| **P-I** | (DEFERRED) | — | SKIPPED per binding decision D3 (none-boot_epoch metrics counter). No text added anywhere. |

---

## 3. Binding Decisions Log

### D1 — Phantom `get_for_message` API (R-A) — **PRECONDITION PARTIALLY MET**

**Verdict:** P-B and P-D are applied as text. The architect's
amendment ("use `get_for_thread` + post-filter, OR add a new
`get_for_message` method") is in place.

**Precondition check (binding per dispatch contract):**

| Check | Result |
|---|---|
| (a) `get_for_thread` exists | ✅ CONFIRMED at `daemon/repositories/message_metadata/repository.py:123` |
| (b) It surfaces `message_id` | ⚠ PARTIAL — it surfaces `MessageMetadata.message_id` which is the LangChain `BaseMessage.id` (UUID4 per `daemon/repositories/message_metadata/models.py:53`), NOT `MessageQueue.message_id` (the queue-side id that `completed_message_id` carries in the source-diff dedup at `child_reports.py:3498-3510`) |
| (c) Side table is populated for the last assistant turn | ⚠ UNLIKELY — the tap points at `instance_messaging.py:1390-1407` and `:4364-4366` are entry-path / graph-input taps (HUMAN messages), NOT assistant turns |
| (d) Plan's F-2 unit tests assert the surface returns what the sweep needs | ⚠ NO — none of S7-S14 directly assert the `message_metadata` surface; S15 (new) is also a behavior test, not a surface test |

**Action for the implementing developer (Phase 2 task 2.8):**

Before relying on `get_for_thread` for the `completed_message_id`
derivation, the implementer MUST verify that the side table
actually carries the queue-side `message_id` for the last
assistant turn in a real straddle scenario. The load-bearing
fallback path in `decisions.md §3` step 6 ⚠ note documents the
three options if the surface does not return what the sweep
needs: (i) add a new repository method, (ii) re-derive from a
different source, or (iii) loosen the dedup key and rely on the
status-level guards.

**Status:** Amendments P-B and P-D are applied. ⚠ Flag is
recorded in `decisions.md §3` step 6 and `phase2-plan.md` task
2.8 for the implementing developer. **NOT a halt on editing;
NOT a halt on Phase 2 implementation** — the flag is a known
gap that the implementing developer resolves at task 2.8 time.

### D2 — F-1 kill-switch (R-D) — **APPLIED**

Applied via P-G. `decisions.md §2c` is the canonical record.
The kill-switch is:
* env-direct: `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT`
* default ON
* mirrors the sweep's `ENSEMBLE_WC_WEDGE_SWEEP_ON_BOOT` pattern
  at `auto_continue_boot_pass.py:145-156`
* read per boot at the wipe call (NOT cached)
* gates ONLY arm 2 of the 3-arm disjunction; arms 1 (status)
  and 3 (`auto_continued_at`) are never gated

The F-1 unit test matrix (`phase1-plan.md` task 1.14) was
extended from six to seven tests with the new S7
`test_boot_epoch_preserve_heartbeat_kill_switch` test. The
test pins BOTH paths: arm 2 active when var=ON; arm 2
disarmed when var=OFF; arms 1 and 3 stay active in BOTH cases.
The In Scope section of `plan-overview.md` was updated to
record the kill-switch.

**Files touched for D2:** `decisions.md` (§2c), `phase1-plan.md`
(task 1.14, objective, file inventory, exit criterion),
`plan-overview.md` (In Scope).

### D3 — None-boot_epoch metrics counter (P-I) — **DEFERRED / SKIPPED**

P-I was skipped entirely. No text was added. The `WARN` line
in `decisions.md §2a` is the existing observability for the
None case. If a future commission wants the
`durability_f1_none_boot_epoch_total` counter exposed via
`/readiness` or `/metrics`, the binding decision is to add it
as a separate follow-up.

**Files touched for D3:** none.

### D4 — PENDING MessageQueue status dedup coverage — **DEFERRED**

Architect A-3 / D4: widen the service-internal dedup status-set
at `child_reports.py:3501-3506` to include `PENDING.value`. The
sweep's post-lock re-check at §3 step 7 is sufficient for this
release; no plan change needed. Recorded in `decisions.md §11b`
(the new "Deferred follow-ups" subsection).

**Files touched for D4:** `decisions.md` (new §11b).

### D5 — Service-internal dedup status-set widening at `:3502` — **DEFERRED (NOTED)**

Recorded in `decisions.md §11a` (the new "Deferred follow-ups"
subsection) per the dispatch contract: "Add a NOTE in
`decisions.md` under a NEW 'Deferred follow-ups' subsection at
the end of the file (before §10 if §10 is last, or after
§10)". §10 was last, so the new subsection is **§11** placed
BEFORE §10. §11a is the D5 entry (one paragraph recording
deferred item, where, rationale); §11b is the D4 entry
(sibling).

**Files touched for D5:** `decisions.md` (new §11a).

---

## 4. Open-Question Resolutions

### Q1 — `JobFeedbackObserver._resolve_finalize_status` metrics counter

**Verdict:** DEFERRED. No plan change. The observer's
`CHILD_AGENT_ERROR_FALLBACK` (at
`job_feedback_observer.py:122`) and the bus-side defensive
`WARNING` log (per `decisions.md §1a`) cover observability for
this release. A-5's optional
`durability_f1_none_boot_epoch_total` counter (P-I) was
DEFERRED per D3; if Q1 is later addressed, it can ship as a
sibling counter.

**Files touched for Q1:** none.

### Q4 — Normal-delivery-between-scan-and-recheck race

**Verdict:** ADDED. Phase 2's unit matrix
(`phase2-plan.md` task 2.13) is extended with a new test **S15
(`TestSweepParentHistoryRaceSkip`)** that covers the
parent-history-scan race scenario per architect Q4. Between
the sweep's pre-lock scan (`get_instance_messages` on parent)
and the post-lock re-check inside `bus._get_parent_lock`, a
normal delivery inserts a fresh `internal_report:<child>`
message into the parent's `MessageQueue` (or stamps a new
`source` on the parent's checkpoint); the sweep MUST skip
the child and NOT double-inject. The test increment
`skipped_already_reported` in `WcWedgeSweepResult`. The
Phase 2 Exit Criterion was updated to reference S15; the
file inventory line in `phase2-plan.md` was updated from
"11 F-2 unit tests" to "12 F-2 unit tests (incl. S15
parent-history-race)".

**Files touched for Q4:** `phase2-plan.md` (task 2.13 S15
added, exit criterion, file inventory line).

---

## 5. Blocker Resolutions

### R-A — Phantom `get_for_message` API

**Resolution:** Applied via P-B (`decisions.md §3` step 6
method-name correction) + P-D (`phase2-plan.md` task 2.8 API
reference replacement). The phantom API is replaced with
`get_for_thread` + post-filter, OR (Phase 2 implementer's
choice) a new `get_for_message` method added in Phase 2. **D1
precondition check (above) is PARTIALLY MET** — `get_for_thread`
exists, but the `MessageMetadata.message_id` it surfaces is
the LangChain `BaseMessage.id` (UUID4), not
`MessageQueue.message_id`. The ⚠ note in `decisions.md §3` step
6 and `phase2-plan.md` task 2.8 documents the load-bearing
fallback paths for the implementing developer. **Status:
amendment text applied; precondition flagged for implementer.**

### R-B — Self-contradicting content-hash fallback in `decisions.md §3` step 6

**Resolution:** The content-hash sentence(s) were dropped from
`decisions.md §3` step 6. The previous text:

> "...falling back to the child-instance-id + content-hash
> pair when metadata is unavailable."

was replaced (via P-B) with:

> "If metadata is unavailable, the sweep logs a WARN and
> skips the child (preserves idempotency over
> dedup-by-status). **No content-hash fallback is
> implemented** — content-keyed idempotency does not exist
> in the codebase (`research-findings.md §2`,
> grep-verified); introducing it would expand the scope
> beyond the commission's evidence."

The status-level guards (terminal short-circuit at
`:2723-2756`, atomic conditional UPDATE at `:3718-3753`,
parent lock at `:2560-2574`, root-gate fresh-assistant at
`:1801-1872`) are preserved as the re-run safety stack per
`research-findings.md §2` idempotency-guard inventory.

**Files touched for R-B:** `decisions.md` (§3 step 6
replacement).

### R-C — `phase2-plan.md` task 2.10 exception scope

**Resolution:** Applied via P-E. Task 2.10 was restructured
from a single top-level `try/except` wrapping the entire
`run()` body to a per-parent `try/except` INSIDE the per-
parent loop, wrapping the per-parent work (parent scan + child
filter + ledger check + service invocation + post-lock
re-check). The outer envelope-try is retained as a redundant
safety net only — it MUST NOT swallow per-parent exceptions
(those are handled by the inner per-parent try/except). On a
non-fatal exception inside a parent, the sweep logs at
WARNING with `parent_id` (truncated to 8 chars) and the
exception class+message, increments `result.errors`, and
`continue`s to the next parent. The previous prose was
internally inconsistent ("must not abort remaining parents"
+ "wrap entire run() body" = mutually exclusive); the new
prose is consistent.

**Files touched for R-C:** `phase2-plan.md` (task 2.10
replacement).

### R-D — F-1 predicate hot-path kill-switch

**Resolution:** Applied via P-G. The kill-switch
`ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT` (default ON; `=0`
disables ONLY arm 2) is the operator-friendly version of
§2's Option (iii) emergency-shipping fallback. See D2 above
for the full kill-switch specification. The F-1 unit test
matrix is extended with S7 to pin both paths.

**Files touched for R-D:** `decisions.md` (§2c),
`phase1-plan.md` (task 1.14 S7, objective, file inventory,
exit criterion), `plan-overview.md` (In Scope).

---

## 6. Line-Drift Substitutions

No line-drift substitutions were required. The architect's
quoted insertion points (e.g., `decisions.md §2` for the new
§2b / §2c sub-sections, `decisions.md §3` step 6 for the
content-hash drop, `phase2-plan.md` tasks 2.8 / 2.9 / 2.10
for the F-2 corrections) all matched the actual file content
in the prior cycle. The pre-existing drift table at
`research-findings.md §6` (5 corrections vs. older docs) was
not affected by this cycle.

The architect's reference to `decisions.md §3` step 6 was
accurate — the previous content-hash sentence was found at
the cited line and removed in one `edit_file` call.

The architect's reference to `phase2-plan.md` task 2.10 was
accurate — the previous "wrap entire run() body" prose was
found at the cited line and restructured in one `edit_file`
call.

The architect's reference to `phase2-plan.md` task 2.8 was
accurate — the previous `get_for_message` reference was found
at the cited line and replaced in one `edit_file` call.

The architect's reference to `plan-overview.md` Coupling Map
(line 174-178 in prior cycle) was accurate — the table was
extended with the new row + observation.

No substitutions were required; the amendments landed at the
architect's quoted locations.

---

## 7. Final Assertion

**Plan is review-ready.** All P-A through P-H amendments are
applied as text. All four REJECT blockers (R-A, R-B, R-C, R-D)
are resolved. All five binding leader decisions (D1-D5) are
applied. Both open questions (Q1, Q4) are resolved (Q1
deferred, Q4 added as S15). P-I is correctly skipped per
binding D3.

**One ⚠ flag for the implementing developer (NOT a halt):**
D1 precondition is PARTIALLY MET. The architect's P-B / P-D
amendment is textually correct (use `get_for_thread` + post-
filter), but the load-bearing second-half of the precondition
(`MessageMetadata.message_id` = LangChain `BaseMessage.id`,
NOT `MessageQueue.message_id`) was not caught by the architect
and requires resolution at Phase 2 task 2.8 implementation
time. The ⚠ note in `decisions.md §3` step 6 and
`phase2-plan.md` task 2.8 documents the three fallback paths.
The implementing developer MUST verify that the side table
actually carries the queue-side id for the last assistant
turn in a real straddle scenario; if it does not, the
implementer chooses ONE of: (i) add a new repository method,
(ii) re-derive from a different source, or (iii) loosen the
dedup key and rely on the status-level guards. The sweep's
status-level guards (terminal short-circuit + atomic
conditional UPDATE + parent lock + root-gate fresh-assistant)
provide re-run safety even if the dedup key is loosened.

This ⚠ flag is recorded in three places for visibility:
* `decisions.md §3` step 6 (paragraph with the ⚠ prefix)
* `phase2-plan.md` task 2.8 (paragraph with the ⚠ prefix)
* this `amendment-summary.md` (this section, item 7)

The plan is otherwise consistent: F-1 has a kill-switch and
the test matrix is extended; F-2 has a corrected API
reference, a per-parent try/except, an extended post-lock
re-check, and a new S15 race test; the deferred follow-ups
are recorded; the coupling map is updated; the boot-sequence
ownership contract is unchanged from the prior cycle.

**Reviewer checklist (operational):**

* [ ] `decisions.md` §2b (P-A), §2c (P-G), §3 step 2 ⚠
  (P-C), §3 step 6 (P-B + R-B), §3 step 7 (P-F), §11a
  (D5), §11b (D4).
* [ ] `phase1-plan.md` task 1.14 S7 (D2), file inventory
  line, objective + exit criterion.
* [ ] `phase2-plan.md` task 2.8 (P-D + D1 ⚠), task 2.9
  (P-F), task 2.10 (R-C + P-E), task 2.13 S15 (Q4), file
  inventory line, exit criterion.
* [ ] `plan-overview.md` In Scope (D2), Coupling Map (P-H).
* [ ] `phase3-plan.md` and `research-findings.md` — no
  changes (per dispatch).

---

## CORRECTION NOTE (2026-10-04, REVISION CYCLE 2 — W-5)

> **The above amendment cycle (D1 precondition check) is
> PARTIALLY CORRECTED.** Explorer A (REVISION CYCLE 2)
> W-5-CONFIRMED the following: the `message_metadata` side
> table **DOES** capture AIMessages via the
> `tap_node_return` site at `daemon/services/message_tap.py:189-252`
> (wired `daemon/graph.py:9418-9419` with the comment
> `:9406-9414` "tool_calls AI messages and the AIMessage
> response are tapped normally"). The prior cycle's
> claim that the side table is "populated for HUMAN
> messages only" is **PARTIALLY WRONG** — the assistant's
> reply IS captured.
>
> **The load-bearing ID-SPACE MISMATCH STANDS and is in
> fact STRONGER.** All three id spaces that matter are
> mutually disjoint (per `decisions.md §14b`):
> * `MessageMetadata.message_id` = `BaseMessage.id` (UUID4)
>   — `daemon/repositories/message_metadata/models.py:53, :76`
> * Wake row's own `message_id` = fresh UUID4 minted at
>   `child_reports.py:3757` (MessageQueue-space)
> * Source-suffix `completed_message_id` = `MessageQueue.message_id`
>   — the queue-side id retrieved at `child_reports.py:698,
>   :1039/:4579`
>
> The side table can never join the wake row. The
> PREFIX-ledger cross-path dedup (see `decisions.md §14a`
> and `phase2-plan.md` task 2.3 / 2.4) is therefore the
> ONLY correct cross-path check.
>
> **The decision is unchanged.** The W-1 LOCKED-to-fallback-(ii)
> decision (checkpoint-derived `child_message_id` via
> `completion_content / serialize_message` chain) does NOT
> depend on whether the side table captures AIMessages —
> it depends on the PREFIX-ledger cross-path dedup. The
> F-2 architecture is the RDRS lane-2 extension per the
> W-4 pivot; the PREFIX ledger is the operative cross-
> path check; the metadata side table is irrelevant to
> the F-2 architecture.
>
> **Where the wrong text appeared (transparency).** The
> "PARTIALLY WRONG" claim was in:
> * `decisions.md §3` step 6 (the ⚠ precondition note,
>   this cycle's revision cycle 1 edit) — corrected in
>   `decisions.md §14b`.
> * `phase2-plan.md` task 2.8 (the D1 ⚠ note) — corrected
>   in the current REVISION CYCLE 2 rewrite.
> * `amendment-summary.md §3 D1` (this file's prior cycle's
>   binding decisions log) — the load-bearing conclusion
>   is preserved; the corrected half is added here.
>
> **No silent rewrite.** This correction is appended
> (not silently merged) per the dispatch directive:
> "Do NOT rewrite history silently — add a dated
> correction note."

---

**End of Amendment Summary.**

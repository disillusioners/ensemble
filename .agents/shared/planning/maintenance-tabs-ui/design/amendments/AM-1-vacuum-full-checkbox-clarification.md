---
amendment_id: AM-1
at: 2026-10-12T13:15:00+00:00
author: plan-creation worker (per approver 2026-10-12 rejection cycle, B2)
status: filed
frozen_spec_path: ../../../../../../.agents/shared/planning/maintenance-tabs-ui/design/design-spec.md
frozen_spec_sha: 55454bf0c001505ced4a62cb7a1a8d2b6de2a04e
related_commission: maintenance-tabs-vacuum (worktree /home/nea/ensemble-src-wt-maint-tabs-vacuum, branch feature/maintenance-tabs-vacuum, base latest@c8a3fbf6a)
scope: clarification (no AC change, no implementation change)
---

# AM-1 — Maintenance Tabs UI Spec Clarification: "byte-for-byte" + AC-6 Scope

## 1 · Why this amendment

The frozen spec `design-spec.md` (sha `55454bf0c001505ced4a62cb7a1a8d2b6de2a04e`, status `approved` v1, critic round 1 PASS 0/4/0) contains TWO normative statements that, read together, are ambiguous about the "VACUUM FULL" checkbox addition commissioned by `maintenance-tabs-vacuum`:

1. **Spec §3.2** (Maintenance Page Shell) says:
   > "CheckpointCleanupComponent ... is mounted as-is inside Tab 1's body. The 4-step Material stepper, Status Strip, kill-switch banner, error banner, footer, and Debug expander are preserved byte-for-byte. The only delta: the section's `position: relative; isolation: isolate; z-index: 1;` ..."

2. **Spec §1.1 AC-6** (Acceptance criteria, Tab 1 body content) says:
   > "The existing `<app-checkpoint-cleanup>` wizard (4-step Material stepper, status strip, error banners, debug expander) renders UNCHANGED inside Tab 1's body. No wizard internals are modified."

The maintenance-tabs-vacuum commission adds a "VACUUM FULL (reclaim disk space)" checkbox + helper text + confirm-dialog copy INSIDE Step 4 of the wizard. The dispatcher's first-cycle reviewer's verdict was "NO AMENDMENT NEEDED" (per the "byte-for-byte applies to layout chrome" interpretation in `decisions.md §D-1`). The approver (2026-10-12, rejection cycle B2) ruled that the verdict must be reconciled with **AC-6's plain reading** — "No wizard internals are modified" is unambiguous: a Step-4 checkbox is a wizard-internal modification. Per the approver's instruction: "file the plan's own pre-drafted AM-1 clarification (decisions.md :42), EXTENDED to cover BOTH §3.2 byte-for-byte scope AND AC-6, with an amendment_history entry."

## 2 · Amendment text (to be appended to the frozen spec's `amendment_history` array AND to clarify §3.2 + AC-6 in the next spec revision)

### 2.1 — §3.2 byte-for-byte scope clarification

> **AM-1 clarification to §3.2:** "byte-for-byte" applies to the SIX listed layout chrome elements: 4-step Material stepper, Status Strip, kill-switch banner, error banner, footer, and Debug expander. Future form-field additions INSIDE the wizard's step form areas (e.g. a checkbox or helper text inside Step 4's form) are in-scope for future commissions and are NOT considered "wizard internals modifications" under AC-6. The form-field-area-vs-layout-chrome distinction is the load-bearing boundary; the spec's byte-for-byte language was always intended to preserve the wizard's visible structure, not its form-field set.

### 2.2 — AC-6 "No wizard internals are modified" clarification

> **AM-1 clarification to AC-6:** "No wizard internals are modified" is read in conjunction with the §3.2 byte-for-byte scope. The clause preserves the wizard's structural chrome (stepper, status strip, error banner, kill-switch banner, debug expander, footer). It does NOT prevent future commissions from adding form-field elements INSIDE the wizard's step form areas (e.g. a checkbox on the confirm step, a helper-text paragraph, or an inline copy extension in the confirm dialog). The first such addition is the "VACUUM FULL (reclaim disk space)" checkbox in `maintenance-tabs-vacuum` (2026-10-12, commission scope: VACUUM FULL escalation option for the interactive execute path). Future form-field additions are tracked in the spec's `amendment_history` array per amendment.

### 2.3 — `amendment_history` array entry (the formal spec-side record)

```yaml
# This entry is to be APPENDED to the frozen spec's `amendment_history: []`
# array (line 20 of the frontmatter) when the spec is next revised. The
# frozen spec itself is NOT modified by this amendment file; the file
# exists in the worktree per the plan-amendment convention.
- amendment_id: AM-1
  at: 2026-10-12T13:15:00+00:00
  author: plan-creation worker (per approver 2026-10-12 rejection cycle, B2)
  status: filed (clarification; no AC change, no implementation change)
  reason: |
    Reconcile §3.2 "byte-for-byte" scope with AC-6 "No wizard internals
    are modified" in the context of the maintenance-tabs-vacuum
    commission's "VACUUM FULL" checkbox addition to the wizard's
    confirm step. The two clauses are now read together: byte-for-byte
    applies to the six listed layout chrome elements; AC-6 preserves
    the wizard's structural chrome but does not prevent form-field
    additions inside the wizard's step form areas.
  scope: clarification; no AC change; no implementation change.
  first_application: maintenance-tabs-vacuum VACUUM FULL checkbox
    (2026-10-12).
```

## 3 · Audit trail (the verdict evolution)

| Date | Verdict | Source |
|------|---------|--------|
| 2026-10-12 (cycle 1, dispatcher) | "NO AMENDMENT NEEDED" — lean from dispatcher ("the mockup doesn't show checkbox detail; spec is silent on this") | `decisions.md §D-1` |
| 2026-10-12 (cycle 1, plan reviewer) | UPHELD — "the byte-for-byte applies to layout chrome, not to per-step form-field internals" | `decisions.md §D-7.4` (the awaiting-reviewer-adjudication tag) |
| 2026-10-12 (cycle 1, approver) | REJECTED on AC-6 reconciliation — "§D-1's NO AMENDMENT NEEDED never reconciles the frozen spec's normative AC-6 text 'No wizard internals are modified'. A Step-4 checkbox is a wizard-internal modification under plain reading. File the plan's own pre-drafted AM-1 clarification, extended to cover BOTH §3.2 byte-for-byte scope AND AC-6." | approver tracking file at `.agents/approver/maintenance-tabs-vacuum-tracking.md`, B2 |

**New verdict (2026-10-12, cycle 1, post-approver): AMENDMENT FILED per approver.** The verdict chain is: dispatcher lean not-needed → reviewer upheld → approver overruled on AC-6 reconciliation → AM-1 filed. The original verdict is RETAINED as an adjudication record in `decisions.md §D-1`; the new verdict is recorded in `decisions.md §D-1` and `§D-7.4` (updated to "AMENDMENT FILED per approver, B2"). The frozen spec itself is NOT modified by this file (per the approver's instruction "the frozen spec itself stays untouched"); the file exists in the worktree per the plan-amendment convention.

## 4 · Placement rationale

The amendment file is placed at:

```
/home/nea/ensemble-src-wt-maint-tabs-vacuum/.agents/shared/planning/maintenance-tabs-ui/design/amendments/AM-1-vacuum-full-checkbox-clarification.md
```

**Why this path (vs alternative placements):**

- The canonical spec path is `/home/nea/ensemble-src/.agents/shared/planning/maintenance-tabs-ui/design/design-spec.md`. The MAIN checkout's `.agents/` is read-only-from-worktree (the worktree's `.git` is a file pointing to `/home/nea/ensemble-src/.git/worktrees/ensemble-src-wt-maint-tabs-vacuum`; the worktree's `.agents/` is the worktree's own, separate from MAIN's). Mirroring the canonical path inside the worktree lets the amendment ride the `feature/maintenance-tabs-vacuum` branch and land in the same place as the spec when the worktree is merged back to `latest`.
- The `amendments/` subdirectory follows the spec's own `amendment_history` array convention (the array is the canonical record; the files are the human-readable narrative). Other future amendments would land in the same subdirectory (`amendments/AM-2-...md`, etc.).
- The file is NAMED `AM-1-vacuum-full-checkbox-clarification.md` (with a descriptive suffix) so the file is self-describing in `ls` listings and in PR descriptions; the `amendment_id` frontmatter field is the canonical ID (`AM-1`) for the spec's array entry.
- The file is in the worktree (not the MAIN checkout) because the amendment is part of the `feature/maintenance-tabs-vacuum` branch's plan; the worktree rides the branch and the amendment lands with the rest of the commission's plan.

## 5 · Out-of-scope (the amendment does NOT change)

- **The frozen spec is NOT edited.** The amendment file is the plan-side record; the spec's `amendment_history: []` array is the spec-side record and is updated ONLY when the spec is next revised (separate commission, separate approver, separate branch).
- **No AC change.** AC-6 is clarified, not modified. AC-1 through AC-20 are unchanged.
- **No implementation change.** The checkbox + helper text + confirm-dialog copy land in the same code as planned; only the spec's "No wizard internals are modified" wording is now read in light of the §3.2 clarification.
- **No reviewer-trail change.** The `decisions.md §D-7.4` "awaiting-reviewer-adjudication" tag is REPLACED with the post-approver verdict (see `decisions.md` updates).
- **No "AM-1 required" override of the §D-1 verdict.** The verdict chain (dispatcher → reviewer → approver) is recorded as audit trail; the approver's ruling is the final word. The §D-1 lean of "not needed" is RETAINED as an adjudication record, not DELETED.

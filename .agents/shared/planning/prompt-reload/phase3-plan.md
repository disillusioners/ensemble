# Phase 3 — Diff/provenance reporting

## 1. Objective

The report the tool returns: abbreviated diff (per-file line deltas + section-level added/removed headings), provenance (release version + file hashes + resolution/validation facts), fingerprint idempotence messaging, and the honesty constraints (F7/F8) that keep the report from claiming effects that do not exist.

## 2. Decisions

| ID | Decision | Rationale (anchors) | Rejected alternatives |
|----|----------|--------------------|-----------------------|
| D13 | Diff scope = loader-cached persona only (D5). The report NEVER diffs the post-cache-appended persona (instance_lifecycle.py:1244-1247 — runtime content kept out of the cache; re-appended fresh on next restore). Section-heading extraction operates on the loader-produced persona strings (diffing loader output = reporting, R2-allowed; NOT assembly). | Prevents false deltas from runtime state; keeps R2 intact. | Full-persona diff incl. appends; own .md parsing for sections (R2 violation). |
| D14 | Abbreviated diff format (frozen for the tester gate): per artifact file → `+N/-M lines`, byte delta, sha256 before→after; section level → added/removed `#`/`##` headings (capped lists, e.g. ≤10 per side with `…and K more`); header line states outcome (`no_change` / `reloaded` / `forced_reload`) + release version + `current_matches_running`. | Dispatch: "per-file line deltas + section-level added/removed headings, abbreviated". Frozen format = testable. | Raw unified diffs (unbounded tokens); no format contract (untestable). |
| D15 | Honesty marks (F7): the provenance payload's per-artifact DB-row state carries explicit flags — `tools_note`: "DB override NOT applied (known defect F7i, routed)"; `user`/`growth`: "not part of the composed prompt (F7ii)". The diff section never lists these artifacts as changed-by-DB-override. | F7 binding; prevents the report from over-claiming. Pinned by tests (T3.4). | Silent omission (loses provenance value); claiming the overrides work (false). |
| D16 | F8 non-promise: result text carries a fixed line noting the FE-visible synthetic system message may lag the live persona until the next display rebuild — no parity claim. | F8 binding (persistence.py:910-1009). | Promising display parity. |
| D17 | Idempotence message (R6): fingerprint-equal → outcome `no_change` with a clear "no changes since last load" message + full provenance (so the no-op path doubles as the free dry-run — D9). `force=true` → outcome `forced_reload`, pops anyway, and the message says so explicitly. | R6; P3 (effect = next turn) must be stated in BOTH paths. | Suppressing provenance on no-op (loses the dry-run value). |

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|----------------|----|
| T3.1 | Tier-b exact diff (D5): old cached persona vs fresh composition — unified line delta + heading-set diff; guard the claim on cache provenance (only when the cached entry exists and the fingerprint chain is sound). | Sentinel edit → exact per-file + heading diff; cache==closure equivalence asserted (P2). | prompt_refresh.py | T1.5 | RA4 | AC3.1 |
| T3.2 | Tier-c honest fallback (D5): cache entry absent/unprovable → `persona_diff=unavailable`, composition + provenance only; NEVER a guessed delta. | Fallback fixture → `unavailable` marker, no fabricated diff. | prompt_refresh.py | T3.1 | RA4 | AC3.2 |
| T3.3 | Abbreviated formatter (D14): per-file line/byte deltas + sha256 before→after; capped heading sets; outcome header incl. provenance fields from T1.2/T1.4. | Golden-format test: known input → exact rendered report; caps trigger `…and K more`. | prompt_refresh.py | T3.1 | — | AC3.3 |
| T3.4 | Honesty marks (D15/F7): per-artifact DB-row state flags for tools_note/user/growth; pin tests assert the marks exist and no contrary claim is emitted. | Marks present in every report carrying those artifacts; pin red if removed. | prompt_refresh.py; tests | T3.3 | RA6 | AC3.4 |
| T3.5 | F8 line (D16) + idempotence message (D17): fixed non-parity line; `no_change` vs `forced_reload` wording; next-turn line in all success outcomes. | Both message paths assert the next-turn sentence; force-on-unchanged says "forced". | prompt_reload.py; prompt_refresh.py | T3.3 | — | AC3.5 |
| T3.6 | Breadcrumb text (D10): the tool return is the ONLY in-turn signal; verify zero history writes (no message enqueue, no SystemMessage, no note injection) — greppable + tested. | No history-mutating call in the tool/primitive paths. | prompt_reload.py; prompt_refresh.py | T3.5 | — | AC3.6 |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC3.1 | Sentinel fixture yields an exact diff (per-file +N/-M, heading added/removed sets) matching a golden string; the exact-diff claim only fires when cache provenance is sound. |
| AC3.2 | Fallback fixture yields `persona_diff=unavailable` + full provenance; no invented delta text. |
| AC3.3 | Report format matches the frozen D14 shape (golden test); abbreviation caps behave. |
| AC3.4 | F7 honesty marks present and pinned; no report claims tools_note/user/growth DB overrides take effect. |
| AC3.5 | `no_change` path: clear no-changes message + full provenance, NO pop; `forced_reload` on unchanged tree pops and says so; every success outcome states next-turn effect. |
| AC3.6 | Zero conversation-history writes in the reload path (assertion + grep pin). |

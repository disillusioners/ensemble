# B6 Probe-First Diagnosis Bundle — README

**Decision:** TICKET (with strong "possibly NOT-A-DEFECT in current state" recommendation).
**Branch:** `feature/pause-resume-terminate-tree-fix` @ `d7deaad2`
**Time spent:** ~30 min of the 2–4h cap
**Files in bundle:** probe1.md, probes2-5.md, ticket-draft.md, README.md

## Exit decision

Per plan §5.3 ("no small seam found → ticket only at the 4h cap: ACCEPTABLE"), the exit is TICKET. The decisive finding is stronger than that: the bug **is not reproducible on the current code state** with the same database rows preserved. All 5 instance detail endpoints return HTTP 200 + full body on the LIVE daemon at HEAD `d7deaad2`.

## 404-Body Classification

`INSTANCE_NOT_FOUND` (custom ErrorResponse, not FastAPI routing artifact).

## Hypothesis Ledger

All five H1–H5 from the plan are **ELIMINATED** by the current-code-state probe evidence. Three candidate explanations for the original repro (commit `e6007b8a`, daemon gone) are documented as X1/X2/X3 in `ticket-draft.md`, each with effort class.

## Bundle files

- `probe1.md` — 404-body classification + captured-from-original-repro evidence + surfacing code paths
- `probes2-5.md` — DB byte-compare (probe 4), engine split-brain check (probe 5), one-shot sweep (probes 2+3), H1–H5 ledger
- `ticket-draft.md` — §5.3 ticket minimum content with corrected-repro script + "possibly NOT-A-DEFECT" recommendation
- `probe1-detail-f5e223f1.headers.body.txt` — raw response from probe 1 (HTTP 200)
- `detail-*.body` — raw response bodies for all 5 detail calls (all HTTP 200)

## Hard-invariant compliance

- ✅ No daemon source edited
- ✅ No DB rows mutated (only `SELECT` queries, all read-only)
- ✅ No commits made
- ✅ `.agents/approver/*` untouched (only `.git`-tracked changes already on disk from prior session are present)
- ✅ Mirror copy at `.agents/shared/planning/pause-resume-terminate-tree-fix/p3-b6-diagnosis-bundle/` for durable storage

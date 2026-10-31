# Phase 6 — Lane Closeout & Handoff

## 1. Objective

Close the lane cleanly: fold the evidence set, deliver the lane RESULTS summary, update board
`wi-78967dba`, register the decided follow-ups (R-9 per D-9; the D-11 instance-heal residual),
and hand off with the four decision statuses — **RATIFIED @ `9657be93a`** (D-1 3.95/5 · D-3
tokens · D-6 default-ON · D-8 rider; verdicts in `decisions.md` + `architecture-recommendation.md`)
— surfaced for the planner/leader, including the two non-blocking leader-ratification notes and
the priced flip costs.

Design references: D-9 (R-9 follow-up registration), D-11 (disclosed residual), decisions.md
statuses; house MERGE/worktree discipline.

## 2. Prerequisites

Phase 5 complete (STAGED-VERIFIED; ceremony-readiness notes ready).

## 3. Decisions

| ID | Decision | Rationale (cite) | Rejected alternatives |
|----|----------|------------------|------------------------|
| PD-6.1 | Merge-to-latest + worktree disposition follow the house MERGE discipline (merge `--no-ff` from a clean worktree, push, rm worktree, `git branch -d` from latest, delete census-KV rows) — executed at the leader-sweep point per lane convention; the lane's own closeout reports merge-ready and leaves the artifacts standing if the sweep is leader-owned | House MERGE rules; recent lanes leave the staging worktree + audit branch for the ceremony/sweep | Lane-side self-merge + self-cleanup ahead of the sweep (races the leader's evidence fold) |
| PD-6.2 | The handoff report is the lane's LAST message and carries: stage record (version, sha, verdicts), the four decision statuses — RATIFIED @ `9657be93a` with what was implemented and the flip cost each (D-1/D-3/D-6/D-8; D-3 flip is foreclosed by the pass-through proof of harm) — the two non-blocking leader notes (D-6 one-Field flip; D-8 overrule = census stays born-broken), the registered follow-ups, and every disclosure (incl. A4 retry foreclosure + the A2 TOCTOU disposition) | Report-delivery discipline (the dispatcher sees the last message verbatim); flips are cheap — the leader must know what to flip | Summary-only handoff (loses the decision surface) |

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|---------------|----|
| T6.1 | Evidence sweep: fold the lane's docs/evidence commits to the branch tip (or hand to the leader sweep per PD-6.1); confirm PACKS.md keep-both integrity (both lanes' sections present, newest-first) | Tip carries the full evidence set; PACKS.md intact | branch tip / `.agents/tester/PACKS.md` | P5 | — | AC-6.1 |
| T6.2 | Lane RESULTS summary doc: what shipped (the (h) leg + tokens + knobs + R-10 rider), gate ladder verdicts, stage record (re-derived version, binary sha, 4-way match, rollback adjudication), fixture/gate evidence pointers, disclosures | RESULTS doc complete in the lane's evidence home | `.agents/tester/RESULTS/` (lane evidence home) | T6.1 | — | AC-6.2 |
| T6.3 | Board `wi-78967dba` update: status → staged-ceremony-ready; summary line; pointer to the plan set + stage record | Board reflects the lane state | board (operator tooling per lane convention) | T6.2 | — | AC-6.3 |
| T6.4 | Register follow-ups: (a) R-9 per D-9 (exclusion ENDORSED @ `9657be93a`, §9) — one-edit site `factory.py:278`, precondition = trustworthy post-F8 manager-engine hold measurement (DR-11) with bound ≥4×, MANDATORY `SET LOCAL statement_timeout = 0` carve-out in `_ensure_postgres_columns` if ever flipped, **the "template-alone edit is NOT enablement" both-or-neither warning carried explicitly (`constants.py:337-341` — architect §9 addition)**, adapter-pool reap = separate minutes-scale commission (legit agets 73-99s); (b) D-11 residual — FAILED/CANCELLED-root + non-root stalled-instance heals need their own commission; (c) D-1 disclosed boundary — a child job held by a parent WAITING_CHILDREN on the zombie's own missed report is closed by the RDRS report side, not by (h); (d) **A3 follow-up semantics** — "terminal job with surviving watcher rows" routes into the `watch_reconcile_sweep` follow-up ticket (its zombie-GC leg retires rows whose job is terminal and liveness is terminal) | Follow-up registrations recorded where the planner routes commissions (board/handoff) | handoff report + board | T6.2 | R7 | AC-6.4 |
| T6.5 | Handoff report (the lane's final message per PD-6.2): phase map, stage record, the four RATIFIED decisions (implemented-as-recommended + flip cost each; D-3 flip foreclosed), the two non-blocking leader notes, follow-ups, disclosures, ceremony command (env-first, version as re-derived) | Report delivered; turn ends with it | (final message) | T6.3, T6.4 | — | AC-6.5 |

## 5. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC-6.1 | Evidence set folded (or explicitly handed to the leader sweep); PACKS.md keep-both intact |
| AC-6.2 | Lane RESULTS summary complete with stage record + disclosures |
| AC-6.3 | Board `wi-78967dba` updated to staged-ceremony-ready with pointers |
| AC-6.4 | R-9 + D-11 + D-1-boundary follow-ups registered with exact sites/preconditions |
| AC-6.5 | Handoff report delivered as the lane's final message (decision statuses surfaced; no follow-up summary after it) |

## 6. Verification method

Artifact checks (board state, RESULTS docs, report presence). No code, no DB, no daemon contact
in this phase beyond read-only board updates per lane convention.

## 7. Risks & notes

- If any gate verdict carried PASS-WITH-NOTES / disclosures, they ride the handoff report
  verbatim — the planner adjudicates, the lane does not silently absorb them.
- KV cleanup (`wt.claim.missed-finalize` / `wt.active.…`) happens with the worktree disposition
  per house discipline — never before the evidence sweep lands.

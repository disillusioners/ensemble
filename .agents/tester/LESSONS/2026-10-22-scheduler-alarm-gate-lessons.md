# 2026-10-22 — scheduler-deferred-alarm final gate: mutation hygiene under interruption, pin-sensitivity gaps, e2e evidence traps

Commission: `scheduler-deferred-alarm-delivery` final functional gate @ `8f06c7f92` (worktree `ensemble-src-wt-sched-deferred-alarm`). Full record: `RESULTS/2026-10-22-scheduler-deferred-alarm-verification.md`.

## 1. Mutation hygiene holds under real interruptions — if recovery is hygiene-first
Two sweep workers died mid-cycle on an upstream model deprecation. Each was revived ONCE with a mandatory STEP 0 (porcelain must equal the single excluded cross-lane line; `git diff --name-only`; `git hash-object <file>` == `git rev-parse HEAD:<path>`). Exactly one interruption-leftover mutant existed (sweep 2, W-A polarity-2 in `scheduler.py`) and was caught and restored byte-identically before any further work. Zero mutants survived; HEAD never moved. **Rule: every mutation-lane recovery message must START with the hygiene gate, not "continue".**

## 2. Docstring-specified mutation sites can be unreachable for the fixture — sibling legs are the coverage
- §(e)3(a) `update_ready_content` status-guard removal stays GREEN because the pin fixture never claims the delivery (row stays `ready` the whole run) — the guard is invisible. Coverage lives at leg (b).
- U4 leg-1's substrate site (`job_queue_service.py` synthetic-key mint) stays GREEN because the rollover mints `v2` which never has a terminal predecessor — the branch is unreachable (the pin's own Q2 ruling says so). Coverage lives at T11 + the adapter-site variant.
**Rule: when a mutation spec cites a site, ask "can this fixture even reach that branch?" before reporting a failed sweep.** Both gaps are pin-hardening follow-ups, not product defects.

## 3. Predicted RED symptoms can shift mechanism — judge the assertion class, not the wording
- One-shot schedules have a STABLE key (ADR-2) ⇒ a forced "fresh generation" enqueue is a dedup hit, so the B3 mutant's observable symptom is DESTRUCTION of the claimed delivery (MQ row deleted → 0 rows), not "a second live delivery"; W7(iii) surfaces as a generation bump (`:2132`), not the unreachable `:2120` assert.
- pytest aborts a test at its FIRST failed assertion — multi-leg pins surface one leg per run; to kill a specific leg, plant the most targeted mutation for THAT leg.
- The ADR-3 consumption gate is unreachable during the 5 s re-arm sleep in the harness loop — the consumption had to be planted inside the branch.

## 4. E2e evidence traps on this surface
- Deferred-carrier metadata is NESTED (`scheduler.deferred_count`): a top-level `metadata->>'deferred'` read returns NULL. Also `message_queue`'s JSONB column is `metadata` in SQL (attr `message_metadata`).
- Background title-generation replays the delivered alarm body into its own LLM call — naive "exactly 1 alarm-shaped request" counters false-positive. Discriminate real turns (n_msgs ≥ 3 / `#1` prefix).
- "No fresh enqueue" must be proven by ENQUEUE-ATTEMPT counts, never row counts (the idempotency key collapses a forbidden enqueue into the kept JobItem; loser Task cancelled, MQ row silently deleted).
- Real-`graph.astream` proof that survives skepticism: have the mock mint a per-request nonce and show it in `checkpoint_blobs` rows for the turn.
- A pausable caller cannot be a plain root instance (root auto-COMPLETES; never-dispatched ghosts are skipped by the pause cascade) — model a live child session (direct `instances.parent_id` insert).
- Manual trigger twin `POST /api/schedules/{id}/trigger` keeps the legacy busy-SKIP (DEC-3 non-goal) — don't build an e2e expectation that it defers.

## 5. Disclosure audits pay for themselves
R15's plan wording over-claimed the pre-fix state ("dead: no MQ row…") and the load-bearing rationale; the code behavior was fine. Static plan-vs-code audit with per-R code excerpts caught it cheaply. Precision nits worth fixing at merge time: default-vs-ceiling bounds (R0/R18/R19), R21 retire-condition breadth, R19 citation drift, DEC-23 twin wording.

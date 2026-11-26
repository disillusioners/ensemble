# Phase 1 Implementation Brief — schedule listing truth + namespace (windowed-scheduling ruling)

**Binding spec of record:** `architecture-recommendation-v3.md` (SAME DIRECTORY — read it first; it supersedes v2).
The Emperor's commission letter quotes v2's items verbatim; v3 tightened Phase 1. **Reconciliation rule: v3 is the
spec; the commission's v2 items are the guaranteed floor.** v3's additions beyond the commission letter (project_id
surface, live-preference label resolution, canonical seed rows) are IN SCOPE — with the seed piece CONDITIONAL (W6).

Anchors in this brief are LEADS, not truth — re-grep every anchor at use time (house rule: anchors drift in weeks).

## Scope fences (NON-NEGOTIABLE)

- **Phase 1 ONLY.** No windowed-recurrence creation, no `ENSEMBLE_SCHEDULING_WINDOW_JITTER`, no window-edge rules, no
  generic re-arm — those are Phase 2, gated on a MEASURED recurring collision (Branch B), which has NOT fired. Do not
  build, stub, or knob any of it.
- **Staged, not promoted.** Feature branch + push only. NO merge to `origin/latest`, NO release dirs, NO tags, NO
  `promote.sh`, NO state.json — the Emperor's 3-factor ceremony owns every flip.
- **No live DB writes, no live rows.** Harness fixtures only. The armed cron row `central-maintenance-weekly-v2`
  (source 34464b40-d1c1-42c7-9fee-78fed75610a5), the cancelled chain row (`central-maintenance-weekly`, 067cdc8d),
  and every other live row stay byte-identical. Never boot a daemon against live-shaped env from this lane.
- **Worktree, never the main checkout.** Main checkout `/home/nea/ensemble-src` is read-only for you (design docs +
  the giter fence template live there). ALL writes in your fresh worktree.
- **Disjoint from the leader's in-flight branch `fix/post-outage-recovery`** (their surface: boot/queue/instance-resume
  + reclaim/capacity). BEFORE coding: fetch, list their changed files, intersect with your intended set
  {daemon/services/scheduling_service.py, daemon/routers/schedules.py, daemon/tools/scheduling.py,
  daemon/models/schedule.py (wherever ScheduleDetail lives — re-grep), daemon/repositories/source/maintenance_seed.py,
  tests/**}. Non-empty intersection on a file → STOP on that piece, report it, do not race. `daemon/manager.py` is
  expected in their blast radius — you must NOT need it (none of W1–W7 requires a manager.py edit; if you think one
  does, stop and report instead).

## Work items

- **W1 — Listing truth (v3 §4.1 = commission 4.1a+b).** In `_row_to_detail` (LEAD: scheduling_service.py:672-710):
  when a `chain` block is present, derive the effective recurrence label (e.g. `"weekly (windowed)"`) — never echo
  the stored `"once"` as the only truth. Add a `window` field to `ScheduleDetail` (LEAD: :194+) echoed from the chain
  block (start, end, weekday, timezone — shape already stored in config). READ-side derivation only — NO migration;
  legacy rows (exactly one in the wild, cancelled) are handled by the same derivation.
- **W2 — One field, one source (commission 4.1c).** REST list `next_run_at` (LEAD: routers/schedules.py:131-138)
  currently derives from the live adapter's in-memory `_get_next_trigger_time()` — a second source that goes null when
  the adapter is absent. Replace with the service-computed value from the STORED config (`_compute_next_run`-class
  logic; LEAD :468-520), adapter value only as fallback if you must keep a live path — the pin (W7) must hold that the
  stored-config source is authoritative on BOTH surfaces (tool list + REST list) and NON-NULL for an armed recurring row.
- **W3 — Project scope surface (v3 §4.1 addition).** Surface `project_id` in the listing contract (ScheduleDetail +
  both surfaces), read from where the row actually carries it (chain config / row column — re-grep; the spawn-path
  honoring of it is ANOTHER lane's fix, do not touch spawn code).
- **W4 — Last-fire/status assumption (commission measurement 1).** IF a surface already renders last-fire/status from
  `schedule_executions`: leave the source as-is (single-source check only). If a surface is MISSING it entirely: add
  the read-side echo from `schedule_executions`. Either way: NO synthesized outcome fields, NO new status semantics —
  the receipt side (instance linkage + terminal status truth) is owned by the leader's lane (job d271db9d); our
  contract renders what the store says and never fabricates. Put a short code comment stating that assumption
  (exact wording in the addendum, same dir: `phase1-addendum-measurements.md` M1).
- **W5 — Namespace (v3 §4.2 = commission 4.2).** Create-time label uniqueness (LEAD: scheduling_service.py:736-742)
  matches ANY row dead-or-live — scope it to LIVE rows (skip `status='cancelled'`). Dead rows keep names for history,
  stay hidden-by-default. **AND the hazard this creates (v3):** cancel/update-by-label resolves first-match-wins
  (LEAD: daemon/tools/scheduling.py cancel docstring) — with dead labels reusable, label resolution MUST prefer the
  LIVE row and refuse ambiguity between two live rows; preserve existing terminal-row semantics (cancelling an
  already-cancelled row keeps today's behavior).
- **W6 — Seed writes canonical rows going forward (v3 §4.3) — CONDITIONAL.** The maintenance boot seeder
  (LEAD: daemon/repositories/source/maintenance_seed.py — writer sites ~:376-381 re-arm refresh, ~:422-427 seed
  config, `_chain_config` ~:405-432) stops composing chain-shaped rows for NEW schedules and writes a canonical
  recurring row (`recurrence=weekly` + cron + local_time + timezone) under a DISTINCT label. The archive-aware boot
  guard rule (an archived CANCELLED chain is never revived) must be PRESERVED — verify it by reading the guard
  (LEAD: daemon/manager.py:1248-1260, READ-ONLY — do not edit manager.py); if preserving it would require a
  manager.py edit, STOP and report. The chain marker stays READABLE for legacy rows (W1 derivation depends on it);
  the adapter's chain re-arm stays untouched. **Execute W6 ONLY IF** maintenance_seed.py (+ tests) is disjoint from
  the leader's branch; otherwise SKIP W6 (read-side derivation already covers chain rows old and new) and report the
  skip. Do not modify the chain re-arm's 5–13-day semantics — that defect is documented and routed, not fixed here.
- **W7 — Pins, mutation-verified (v3 §4.4 = commission 4.3).** Harness fixtures only, no live DB:
  1. **Listing pin:** a chain-shaped fixture renders derived recurrence + window + NON-NULL next-fire + project scope
     on BOTH surfaces (tool list via service; REST list via the mock_manager fixture — LEAD:
     tests/test_scheduler_api.py:22-129). Counter-mutations (each must turn the pin RED): (i) derivation mutated out
     (revert to echoing stored "once"); (ii) adapter-only next-fire source reintroduced on REST; (iii) window echo
     removed. 
  2. **Namespace pin:** create with a cancelled row's label SUCCEEDS; create with a live row's label FAILS; label
     resolution prefers the live row when a dead+live pair shares a label. Counter-mutation: uniqueness lookup
     reverted to match-any-row ⇒ RED; live-preference removed ⇒ RED.
  3. **Seed pin (only if W6 executed):** a fresh seed writes a canonical row — no `chain` block, distinct label, and
     the archived-CANCELLED-chain-never-revived rule holds. Counter-mutation: seed reverted to chain shape ⇒ RED.

## Environment + venue

- Fresh worktree: `git -C /home/nea/ensemble-src worktree add -b feature/schedule-listing-truth-phase1
  /home/nea/ensemble-src-wt-sched-p1 origin/latest` (fetch first; unset-upstream NOT needed for feature branches,
  only release-cut branches — but confirm the branch does NOT auto-track in a way that affects pushes; push explicitly).
- `.env` fence per the giter template (READ it: `/home/nea/ensemble-src/agents/giter/workflow.md:82-94`): explicit
  POSTGRES_HOST/PORT/DB/USER/PASSWORD (dev `ensemble_dev` @127.0.0.1:5432) + DAEMON_PORT=8079; creation-time verify
  `grep -E '^POSTGRES_DB=' .env` asserts ≠ ensemble_prod. Ambient scrub is PER-VARIABLE (`env -u POSTGRES_*` is a
  NO-OP). No bare `python`; worktree `.venv` via `uv sync` (uv at /home/nea/.local/bin). pytest: explicit `tests/`
  paths; null-delimited node-id lists if word-splitting risky; `-rf`, never `-rs`, never `-x`.
- Mutation venue per the loaded skill (`mutation-verified-regression-pins`): throwaway detached worktree at the branch
  tip; integrity + import-provenance gates; restore proofs (sha256 PRE==POST); evidence files end `.txt` not `.log`.
- Commit discipline: explicit-path adds (`git add <paths> && git commit --only -m ... -- <paths>`); planning copies
  committed as `docs(scheduling): ...`. Push `feature/schedule-listing-truth-phase1` to origin; report tip sha.

## Deliverable + report

Deliver: implementation + pins on `feature/schedule-listing-truth-phase1`, pushed (staged, NOT merged/promoted) +
worktree left standing for inspection. Report (≤200 lines), FIRST LINE exactly `Skill loaded:
[mutation-verified-regression-pins]` or `NO SKILL LOADED` (before any heading): branch + tip sha + remote state;
changed files + diffstat; overlap-check evidence (their files ∩ ours, expect empty; W6 decision + reason); per-W
completion; pin evidence per transition (command, VERBATIM failing assertion lines, rc, restore proof, unrelated
tests that stayed green); fence attestations (env pins, DBs touched — expect none beyond dev/scratch fixtures, live
rows untouched, no promote); anchor-drift log (every lead re-grepped; corrections listed); deviations + stop
conditions hit; ≤3 open questions. Call `skill_feedback(skill_id, applied=True, usefulness=<1-10>, note=<short>,
improvement_note=<actionable>)` as a TOOL CALL ONLY first, then deliver the full report as your FINAL message.

Before ending any turn: begin work with a tool call, deliver your report, or report open questions (unless you are
the user-facing root) — a turn that ends on future-intent text with zero tool calls is treated as a junk report.

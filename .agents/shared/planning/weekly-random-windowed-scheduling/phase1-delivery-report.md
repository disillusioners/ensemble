# Phase 1 Delivery Report — schedule listing truth + namespace (plain-language, for the Emperor)

**Date:** 2026-11-23 · **Lane:** architect (controller) — implementation by architect-worker-sched-p1 (`463119bd`)
under skill `mutation-verified-regression-pins`. **Design of record:** `architecture-recommendation-v3.md` (this dir);
build translation `phase1-implementation-brief.md`; measurement rulings `phase1-addendum-measurements.md`.

**State: STAGED, NOT PROMOTED.** Branch `feature/schedule-listing-truth-phase1`, tip **`77b6d42bc`**, pushed
(local == remote verified; upstream UNSET so no bare push can touch `latest`). No merge to `origin/latest`, no tag,
no release dir, no promote — your 3-factor ceremony owns the flip. Worktree `/home/nea/ensemble-src-wt-sched-p1`
left standing for inspection. Base `origin/latest@cf69837ec`; latest advanced twice mid-lane via unrelated lanes
(zero file overlap) — merge-base unchanged, clean merge expected.

---

## What the operator can now see (once promoted) that they could not before

1. **A weekly task can no longer masquerade as a one-shot.** Any row carrying the internal `chain` block lists as
   **`weekly (windowed)`** — derived at read time, so the legacy cancelled row and any future chain row are both
   covered with no migration. The stored `recurrence:"once"` is never echoed as the only truth (a pin asserts the
   stored value stays untouched).
2. **The window itself** — start, end, weekday, timezone — is a first-class `window` field on the listing, echoed
   from where it already lived in the stored config.
3. **Project scope** — `project_id` on both the tool surface and the REST surfaces. A listing that hides scope is
   the same misread class as one that hides recurrence (v3 floor item 5).
4. **One field, one source for next-fire.** The REST list no longer derives `next_run_at` from the live adapter's
   in-memory state (which went **null** whenever the adapter was absent and could disagree with the tool surface);
   both surfaces now compute it from the **stored config**, non-null for an armed recurring row, adapter demoted to
   explicit fallback. The REST detail/PUT path got the same one-source treatment.
5. **Last-fire and status** render from `schedule_executions` as stored (REST surfaces gained the missing
   `last_execution_status` echo) — with the honest caveat below.

## The honest caveat (measurement 1, ruled M1)

The receipt store itself is blind today: for 47/51 fired sources `instance_id` is NULL and `last_execution_status`
reads `queued` whether a run happened, didn't, or never started a turn (the 19:00Z reclaim test proved the third
case renders identically). **The listing therefore does not pretend**: it renders what the store says and never
synthesizes an outcome. The assumption is stated verbatim in the code: outcome-truth is owned by the
execution-receipt lane (job d271db9d); when that fix lands, these same listing fields become true with **zero**
listing change. No receipt-side work was taken here.

## Dead labels no longer block reuse (and the hazard that created, closed)

- Create-time (and rename-time) label uniqueness now matches **live rows only** — a cancelled label no longer blocks
  reuse until purged. Dead rows keep their names for history and stay hidden-by-default.
- The hazard this opens — cancel/update-by-label resolving "first match wins" — is closed in the same change: label
  resolution **prefers the live row**, **refuses** two-live-row ambiguity (REST 409 `SCHEDULE_LABEL_AMBIGUOUS`),
  and preserves terminal-row semantics (a dead-only label still resolves for history-facing behavior as before).

## The pins and their counter-mutations (all mutation-verified, harness fixtures only — no live DB)

Baseline 15 passed. Venue: detached throwaway worktree at the pin commit; every restore proven by sha256
PRE==POST + clean status + GREEN re-run; final tip suite 308 passed.

| Pin | Counter-mutation (each turned it RED) | Verbatim failure (abridged) |
|---|---|---|
| Listing truth (tool + REST) | derivation removed → echoes stored `once` | `assert 'once' != 'once'` (both surfaces) |
| Listing truth | adapter-only next-fire reintroduced on REST | sentinel-source detected (`startswith('1999')`) |
| Listing truth | window echo removed (1 line) | `assert None is not None … window=None` |
| Namespace | uniqueness reverted to match-any-row | `ValueError: label already exists: reused-label` |
| Namespace | live-preference removed (2 lines) | `assert 'dead-1' == 'live-1'` |
| Seed canonical | create path reverted to chain shape | `assert 'once' == 'weekly'` |

Unrelated tests stayed green through every mutation; the REST test file is now **hermetic** (75 passed with this
host's ambient auth present — per-variable scrub before the `daemon.api` import plus an autouse re-applying fixture,
because `AuthMiddleware` registers at import time).

## File overlap — avoided, with evidence

`fix/post-outage-recovery` (leader's lane) touches 8 files — intersection with our 10-file set is **EMPTY**
(verified both directions + an all-local-branches sweep; no branch touches our files). **`daemon/manager.py` needed
no edit** — the archive-rule lead pointed at its caller; the rule itself lives in `maintenance_seed` and was
preserved in place.

## The seeder going forward (W6, executed — condition was satisfied)

New schedules from the maintenance boot seeder are **canonical** (`recurrence=weekly` + cron + local_time +
timezone, **no chain block**) under the distinct label **`central-maintenance-weekly-canonical`** (uuid5
`2825b3e0…`) — this is the name a fresh-install trigger will carry (confirmed, not pending). The archived-CANCELLED
-chain-never-revived rule is preserved verbatim and extended to the canonical identity. The cancelled chain row
(`central-maintenance-weekly`, 067cdc8d) stays dead; the armed cron row (`central-maintenance-weekly-v2`,
34464b40-…) is untouched; on the live deployment the new create path is unreachable anyway while the cancelled row
is present — and the branch is not promoted. The chain marker stays readable (W1 depends on it) and the adapter's
chain re-arm is untouched.

## The two routed defects (NOT taken here — recorded so they get owners)

- **M2 — spawn path drops `project_id`** (wider blast radius: every project-scoped scheduled lane silently runs
  unscoped). Ruled: the canonical contract **requires** the spawn path to honor the row's `project_id`. Fix belongs
  to the leader's lane (job d271db9d); our W3 is the listing half.
- **M3 — the "weekly" chain actually fires every 5–13 days** (re-arm counts the next weekday-D from the re-arm
  instant, `allow_today=False`). Read-side derivation now makes the *window* visible; the *interval drift* needs an
  owner: either a true weekly anchor or an honest interval (the adapter's native `interval_seconds` is the sanctioned
  one-line alternative). Not Phase 1 — live load-bearing machinery.

## Notes for the leader's merge sweep

1. **Planning docs are untracked in the main checkout** (they postdate the branch base). The worker deliberately did
   NOT commit copies to avoid untracked-file collisions at merge — carry the four files from this directory into the
   merge's `docs(scheduling):` commit.
2. **Expect exactly ONE pre-merge red** in `tests/unit/repositories/test_source_gc_repository.py`
   (`test_post_merge_span_exactly_sanctioned_hunk`) — the P11 guard judges the span since the last first-parent merge
   and `scheduling_service.py` is in that lane's NOT-TOUCHED_PATHS. Proven transient: post-merge sim → `1 passed`.
   A scoped-sanction mechanism for pre-merge branches is the source-gc lane's call (routed).
3. Worktree `ensemble-src-wt-sched-p1` stands for inspection; standard worktree lifecycle applies post-merge.

## Phase 2 — flip condition, restated for the record

Phase 2 (window as a first-class creation field, `ENSEMBLE_SCHEDULING_WINDOW_JITTER` kill-switch, window-edge rules,
generic re-arm proof leg) builds **only on Branch B: a MEASURED recurring collision** between the fixed slot and the
PG host's own recurring heavy work (autovacuum on the toasted relation, Longhorn snapshots, PG maintenance, the
house's recurring jobs). **Status: unmeasured ⇒ Branch A — nothing built, no stub, no knob** (the env var does not
exist on this branch). The measurement is a cheap read-only devops probe over one weekly cycle (v3 §2.3). If anyone
judges Branch B has fired, it goes to the Emperor for the ruling — no lane starts Phase 2 alone.

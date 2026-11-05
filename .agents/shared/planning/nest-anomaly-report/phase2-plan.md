# Phase 2: Clustering service + rate limits + critical→board + retention

Lane: nest-anomaly-report (wi-2d691ed2) · Author: plan-creation worker · Date: 2026-11-04T22:30Z
Rulings binding this phase: R3 (severity → board, notify NEVER), R5 (write-time clustering,
sliding window, stable identity, derived resolution), R5b (rate limits pre-harvest,
ledgered refusals), R6 (retention on the existing maintenance seam). **Architect fold round
2: SOUND-WITH-AMENDMENTS (`architecture-recommendation.md` — binding): AM-4, AM-5, AM-6,
AM-7, AM-9, AM-12e, AM-13, AM-14, AM-15 fold HERE** (trails in decisions.md). Extends the
phase-1 substrate (`daemon/repositories/anomaly/`, `daemon/services/anomaly_service.py`) —
no new storage; the two phase-1 migration files already carry both tables.

## Objective

Accepted reports collapse into clusters (N reporters × same incident = ONE cluster row with
a stable id), spam is refused pre-harvest and ledgered, critical clusters surface to the
BOARD exactly once (note-append on repeat criticals), and everything older than 30 days is
swept in bounded batches on the existing maintenance loop. Phase-1's degenerate cluster
tail (`cluster_id=pending-phase-2`) is REPLACED by real assignment in the same service
entry point — the tool envelope gains real `cluster_id` / `cluster_report_count` /
`cluster_first_report_at` with no tool-signature change.

## File Inventory

**Edited (extends phase-1 files)**
| Path | Change |
|---|---|
| `daemon/services/anomaly_service.py` | clustering assignment + rate-limit enforcement + critical→board gate + retention job function; phase-1 degenerate tail deleted |
| `daemon/repositories/anomaly/repository.py` | cluster lookup/insert/rollup queries; counting queries; batched retention DELETEs |
| `daemon/manager.py` | ONE registration: `self._maintenance_service.register("anomaly_retention", min_interval_hours=24, execute_fn=...)` in the existing registration cluster (`:4377-4525` pattern; CheckpointCleanupJob construction precedent `:4349`) |

**Never touched:** tool signature (`daemon/tools/nest_report.py` — phase-1 shape stands;
only the service behind it changes); `agents/**`; board tools' tool surface (the board row
is written SERVER-SIDE via `BoardService`, R3); `daemon/routers/metrics.py` (frozen, R8);
`notify_user` anywhere (R3).

## Cluster Service Algorithm (exact pseudocode)

```
SEVERITY_RANK = {"minor": 1, "major": 2, "critical": 3}

def cluster_key(tree_root: str, canonical_category: str) -> str:
    return sha256(f"{tree_root}|{canonical_category}".encode()).hexdigest()

async def accept_and_assign(report_args, harvested, agent_id, instance_id) -> Envelope:
    # ── 0. DENY LIST (AM-16) — writer's agent-type id on
    #    ENSEMBLE_NEST_REPORT_DISABLED_AGENTS → refuse("tool-disabled"); that
    #    writer ONLY; checked PRE-rate-gate. ──────────────────────────────────
    # ── 0b. RATE LIMITS (R5b + AM-4 + AM-9) — BEFORE any harvest. ALL cutoffs
    #    are computed in PYTHON and BOUND as parameters — the as-written
    #    `created_at >= now() - interval` SQL ERRORS on PG (TEXT vs timestamptz
    #    has no such operator; house idiom notify_audit/repository.py:246-253).
    cutoff = (now_utc() - timedelta(seconds=rate_window_seconds)).isoformat(timespec="microseconds")
    # FENCE 1 (AM-9 list #1 — per-agent ledger cap): DECIDE first, WRITE later —
    #     the refusal LEDGER row is written only while the agent's in-window
    #     refusal rows < refusal_ledger_max_per_agent (10/10min); beyond it the
    #     SAME envelope returns with NO ledger row (the metric is the scalable
    #     signal). This is a READ — no write happens before FENCE 2.
    ledger_allowed = (COUNT(outcome='rate_limited', agent_id=:agent,
                            created_at >= :cutoff_ledger) < refusal_ledger_max_per_agent)
    # FENCE 2 (AM-9 list #2 — GLOBAL TOTAL-row ceiling; D-1, fold round 4):
    #     checked BEFORE ANY refusal-ledger write — NO path persists a refusal
    #     row while the ceiling is at/over max_rows_global; the refusal path
    #     returns the `capacity-exhausted` envelope with NO row (the rate-limited
    #     counter is the signal). Per-tree cap INAPPLICABLE here: refusal rows
    #     carry NULL tree_root (R5b). (Pre-F-1 this gate sat AFTER the ledger
    #     write — the pseudocode contradicted the normative text, and the
    #     round-3 "self-stabilizing" claim was FALSE for exactly that reason.)
    if COUNT(*) FROM anomaly_reports >= max_rows_global:
        return refuse("capacity-exhausted")   # NO ledger row
    # R5b per-agent rate decision (READ only — no ledger write in this block):
    if severity != "critical":
        n = COUNT(*) FROM anomaly_reports
             WHERE agent_id = :agent AND outcome = 'accepted'
               AND created_at >= :cutoff                              # :cutoff BOUND (idx agent_created)
        over_rate = (n >= rate_max_per_agent)
    else:
        c = COUNT(*) FROM anomaly_reports
             WHERE agent_id = :agent AND outcome = 'accepted'
               AND severity = 'critical'
               AND created_at >= :cutoff_critical                     # BOUND (idx agent_created + filter)
        over_rate = (c >= critical_max_per_agent)
    if over_rate:
        # FENCE 1 decides the WRITE; FENCE 2 already guaranteed the ceiling.
        if ledger_allowed:
            return refuse("rate-limited", ledger_row(evidence=None))
        return refuse("rate-limited")        # SAME envelope, NO ledger row (fence 1 cap)
    # FENCE 3 (AM-9 list #3 — pre-harvest global accepted/hour ceiling):
    if COUNT(outcome='accepted', created_at >= :cutoff_hour) >= max_accepted_per_hour_global:
        return refuse("capacity-exhausted")   # no ledger row
    # (refused row when written: outcome='rate_limited', harvest columns NULL,
    #  cluster_id NULL, refusal_reason='rate-limited: window=N/max' — the
    #  refusal IS the spam signal)

    # ── 1. HARVEST + SCRUB (phase-1 path, unchanged; AM-8/AM-11 seams live there)
    #    PRE-INSERT capacity caps (AM-9) run HERE, post-harvest pre-persist:
    #    COUNT(all rows) >= max_rows_global (50,000) OR
    #    COUNT(rows, tree_root = :root) >= max_rows_per_tree_root (2,000)
    #    → refuse("capacity-exhausted")            # no ledger row

    # ── 2+3. ONE TRANSACTION (AM-5a) — the report INSERT + cluster
    #    INSERT/UPDATE + report→cluster UPDATE execute as ONE repository
    #    unit-of-work in ONE db_offload call; the service lock wraps the UNIT
    #    call (precedent board_service.py:186-200). A live assignment failure
    #    writes the report row outcome='unclustered' DIRECTLY (AM-5b terminal
    #    state — never a dangling accepted+NULL row). ─────────────────────────
    async with _cluster_stripe[hash(cluster_key_hash) % 64]:   # AM-14: 64-slot stripe
        cutoff = (now_utc() - timedelta(seconds=cluster_window_seconds)).isoformat(timespec="microseconds")
        row = SELECT id, first_report_at, report_count, max_severity
              FROM anomaly_clusters
              WHERE tree_root_instance_id = :tree_root
                AND category = :canonical
                AND last_report_at >= :cutoff                     # :cutoff BOUND (idx key_window)
              ORDER BY last_report_at DESC LIMIT 1
        if row is not None:
            new_count  = row.report_count + 1
            new_max    = max_rank(row.max_severity, severity)
            UPDATE anomaly_clusters
               SET report_count = :new_count,
                   last_report_at = :now,
                   max_severity = :new_max
             WHERE id = :row.id
            cluster_id, count, first_at = row.id, new_count, row.first_report_at
        else:
            INSERT INTO anomaly_clusters
              (id=new_uuid, cluster_key_hash=key(tree_root, canonical),
               tree_root_instance_id=:tree_root, category=:canonical,
               first_report_at=:now, last_report_at=:now,
               report_count=1, max_severity=:severity, board_item_id=NULL,
               created_at=:now)
            cluster_id, count, first_at = new_uuid, 1, now
        UPDATE anomaly_reports SET cluster_id = :cluster_id WHERE id = :report_id

    # ── 4. CRITICAL → BOARD (R3) — inside the same service call ──────────────
    if severity == "critical":
        await ensure_board_surface(cluster_id, ...)

    # ── 5. envelope with REAL cluster fields ──────────────────────────────────
    return ok(report_id, cluster_id, count, first_at, fold_notice)

# Resolution is DERIVED — there is NO status column and NO reopen write anywhere:
#   cluster open  ⇔  last_report_at >= now() - resolve_window_seconds (1h)
# Read surfaces (phase 4) compute openness at query time; nothing mutates identity.
```

**The lookup/UPDATE/INSERT race — single-writer service lock (justification).** All cluster
writes funnel through ONE in-process `asyncio.Lock` inside `anomaly_service` (the daemon is
a single process; there is no second writer path — the tool body only calls the service).
Two concurrent same-key reports therefore serialize: the second sees the first's row (or
the first's INSERT) and joins it — the lost-update/double-INSERT race is structurally
absent. **UPSERT rejected:** PG `ON CONFLICT` requires a unique constraint on the key, and
decisions R5 explicitly REJECTED `UNIQUE(cluster_key_hash)` (a legitimate re-incident after
the quiet period INSERTs a second same-key cluster; a full unique turns that path into an
IntegrityError, and a partial unique is impossible because resolution is derived — there is
no open flag to index on). **DB-level `SELECT ... FOR UPDATE` rejected:** it adds
cross-round-trip transaction holding for a race the process-level lock already excludes,
and the house off-loop discipline (`db_offload.offload`, `db_offload.py:386`) prefers short
single statements. The lock is phase-1-code-free: it wraps ONLY section 3 (assignment),
never harvest or scrub, so a slow report cannot stall an unrelated cluster join beyond one
assignment (microseconds-to-milliseconds: one indexed seek + one UPDATE).

**AM-14 amendment (fold round 2):** the single process-global `asyncio.Lock` is REPLACED by
a fixed **64-slot stripe array** — index = `hash(cluster_key_hash) % 64`. Rationale: the
global lock held across board I/O serialized EVERY anomaly assignment daemon-wide (a
critical storm = a global choke point). The stripe keeps same-key serialization FULLY
preserved (the board double-create race stays closed because the board write stays inside
the same-key stripe) while eliminating cross-key contention; memory is bounded (64 locks).
UPSERT stays rejected for the same R5 reason as before (no unique key can back `ON
CONFLICT`); the stripe lock (not a DB constraint) remains the race answer.

**Index recap:** the lookup is served by phase-1's
`idx_anomaly_clusters_key_window (tree_root_instance_id, category, last_report_at)` —
one composite seek, no table scan, no new index in phase 2.

## Rate Limits (R5b — enforcement completes here)

**Enforcement shape (leader fold item 7):** the check is an in-tool LEDGER BUDGET over the
reports table — a `SELECT COUNT` served by the `(agent_id, created_at)` index; **the ledger
IS the rate budget** (the notify_user doctrine: budget/interval read from the tool's OWN
ledger, accepted rows consume, refusals never extend the window —
`daemon/tools/notify_user.py:1205-1330`). The refusal envelope token stays `rate-limited`.

**AM-4 (fold round 2 — mechanical bug fix):** every COUNT's cutoff is computed in PYTHON
(`(now_utc() - timedelta(seconds=window)).isoformat(timespec="microseconds")`) and BOUND as
a parameter — the section-0 SQL as originally written (`created_at >= now() - interval`)
**ERRORS on PG** (TEXT vs timestamptz: no such operator; house idiom
`notify_audit/repository.py:246-253`).

**AM-9 fences (fold round 2 — wired here, fields defined in phase 1):**
1. Refusal-ledger cap: `refusal_ledger_max_per_agent` (10/10min) — beyond it the
   `rate-limited` envelope returns with NO ledger row (checked before the ledger INSERT).
2. Pre-INSERT caps: `max_rows_global` (50,000) / `max_rows_per_tree_root` (2,000) →
   `capacity-exhausted`, no ledger row (post-harvest, pre-persist — the counts are served
   by `idx_anomaly_reports_outcome_created` / `idx_anomaly_reports_tree_root_created`).
   **F-1 correction (fold round 3): `max_rows_global` is a SINGLE TOTAL-row bound
   (accepted + refused) — the SAME check gates the §0b refusal-ledger INSERT (at/over the
   ceiling the refusal envelope becomes `capacity-exhausted`, NO row); the per-tree cap is
   INAPPLICABLE to refusal rows (NULL tree_root per R5b).**
3. Pre-harvest global ceiling: `max_accepted_per_hour_global` (5,000) → `capacity-exhausted`.
4. Retention gains a **capacity trim**: oldest-first beyond `max_rows_global`, batched —
   self-stabilizing with the insert gate (once at ceiling, inserts refuse ⇒ inflow stops ⇒
   the trim converges). *Holds ONLY post-F-1: pre-F-1 the insert gate counted accepted rows
   exclusively, so refused inflow never stopped — the claim was FALSE as written.*
Pins: the unbounded-refusal-loop leg (N refused calls ⇒ persisted rows ≤ ledger cap) + a
global-cap leg.

**Why refuse (not merge) is correct — the refuse-vs-merge defense.** A refused report does
NOT lose signal: it is LEDGERED (outcome=rate_limited with category + severity + agent_id)
and counted by `ensemble_anomaly_reports_rate_limited_total{category}` + surfaced in the
morning review. Merging a refused report into a cluster as a bare count increment is
REJECTED because it would corrupt the cluster invariant — `report_count` must equal the
number of MEMBER ROWS (the "N agents × same incident = 1 cluster" proof counts ROWS); a
phantom increment breaks row-count == report_count and makes the cluster unauditable. The
signal survives in the refusal ledger + metric; the cluster stays row-faithful.

- **Counting queries** (both served by `idx_anomaly_reports_agent_created`; outcome filter
  is a residual scan over that agent's recent rows only):
  - minor/major: `COUNT(*) WHERE agent_id=:a AND outcome='accepted' AND created_at >= :cutoff` — the cutoff computed in PYTHON and bound as a parameter (AM-4; **W-3: the former `now()-600s` SQL form was a PG operator error** and contradicted the plan's own AC7 grep-fence) — vs `rate_max_per_agent` (5).
  - critical: same + `AND severity='critical'` over `critical_window_seconds` (3600) vs `critical_max_per_agent` (20) — cutoff bound in Python likewise, never SQL `now()`.
- **Only accepted rows consume budget** — the notify_audit discipline ("ONLY accepted rows
  consume budget", NotifyUserConfig field docs, `daemon/config.py:3085+`); refused rows are
  pure signal and never extend any window.
- **Ledgered-refusal shape** (persists BEFORE the refusal envelope returns):
  `outcome='rate_limited'`, `refusal_reason='rate-limited: <window> <n>/<max>'`,
  `cluster_id=NULL`, ALL harvest-derived columns NULL (`tree_root_instance_id`,
  `parent_chain`, `parent_chain_agents`, excerpt*, job/mission/dispatch, boot_epoch,
  registry*, wedge*) — authored content (agent_id, instance_id, category, category_raw,
  severity, description) IS stored so morning review sees WHAT was suppressed. A refused
  report harvests NOTHING (no transcript read, no ancestor walk, no task-row lookup).
- **Enforcement point:** the service, BEFORE section 1 (harvest). The phase-1 sequencing
  note is resolved: the gate moves ahead of harvest in the same call.

## Critical → Board Seam (R3)

**Handle injection:** the factory (phase 1) already receives `manager`; the service obtains
the board handle from the manager the same way other server-side writers do — the tool
NEVER imports board tools and the agent NEVER sees a board tool from this lane.

**Exactly ONE item per cluster — AM-13 ceilings wrap the create:**
```
async def ensure_board_surface(cluster_id, tree_root, canonical, severity, count,
                               top_description, reporting_instance_id):
    cluster = SELECT board_item_id FROM anomaly_clusters WHERE id = :cluster_id
    if cluster.board_item_id is not None:
        board_service.update_item(                       # board_service.py:219
            item_id=cluster.board_item_id,
            note=(f"anomaly cluster {cluster_id}: critical report #{count} "
                  f"from {agent_id} at {ts}; cluster total={new_count}, "
                  f"max_severity={new_max} (peak)"),     # AM-7: peak labeling
            author_instance=reporting_instance_id)       # note appended SAME-transaction (:297)
        return
    # AM-13 ceilings: max_board_items_per_tree_root_per_hour (3) /
    # max_board_items_per_hour_global (20 — W-6 correction: 50/h ⇒ 1,200/day
    # exceeded the architect's own ~480/day harm bound; 20/h ⇒ 480/day, and a
    # uniform multi-tree flood at 20/h/type now trips it) — COUNT recent anomaly
    # items (tagged, this tree / global, per hour) BEFORE create:
    if tree_ceiling_exceeded or global_ceiling_exceeded:
        target = most_recent anomaly board item FOR tree_root          # newest-first
        board_service.update_item(item_id=target.id, note=suppression_note(...),
                                  author_instance=reporting_instance_id)
        INCREMENT ensemble_anomaly_board_items_suppressed_total        # phase-3 metric
        UPDATE anomaly_clusters SET board_item_id = :target.id WHERE id = :cluster_id
        return
    item = board_service.create_item(                    # board_service.py:136
        title=f"[anomaly][critical (peak)] {canonical} — cluster {cluster_id}",
        # AM-13: the TITLE carries the cluster_id (operator-distinguishable machine rows);
        # AM-7: severity labeled peak (R3: category + severity in title)
        body_md=("auto-filed — unverified agent prose\n"   # AM-12e fixed prefix line
                 f"cluster_id: {cluster_id}\n"
                 f"tree_root: {tree_root}\n"
                 f"reports: {count} (max_severity: {severity}, peak)\n"
                 f"first_report_at: {first_at}\n\n"
                 f"top description:\n{top_description}"),  # scrubbed, ≤ ~1800 chars body cap
        tags=["anomaly", f"severity:critical", f"category:{canonical}"],   # deduped in-service
        project_id=None,                                 # GLOBAL namespace (board schema, board.py:208)
        created_by_instance=reporting_instance_id)       # the REPORTING instance — stamped by the caller
    UPDATE anomaly_clusters SET board_item_id = :item.id WHERE id = :cluster_id
```

- `created_by_instance` = the REPORTING instance id (BoardService requires the caller to
  stamp it — `board_service.py:136` docstring: "an agent can never supply this value").
- **Concurrency:** `ensure_board_surface` runs INSIDE the phase-2 cluster lock for the
  critical path, so two concurrent criticals on a fresh cluster cannot both see
  `board_item_id IS NULL` and double-create; the single-row-per-cluster pin (below) proves it.
- **NOTIFY NEVER:** no `notify_user` import, call, or config read anywhere in the service —
  the doctrine (`notify_user.py:32-39`: board ALWAYS, notify MAY voice, "Nothing
  auto-sends") makes the machine-triggered anomaly path board-only. Pinned (AC5).
- **AM-12e (top_description determinism):** the board body's `top_description` is the
  member with the HIGHEST severity, tie-break `created_at ASC, id ASC` — pinned; the body's
  first line is the fixed prefix **"auto-filed — unverified agent prose"** so an operator
  cannot mistake agent prose for a system assertion.
- **AM-13 rationale + disclosures:** distinct clusters defeat one-item-per-cluster (13
  categories × fresh 15-min windows × 20 criticals/hour/type ⇒ up to ~480 items/day/type
  into the GLOBAL newest-first retention-free board — `board_service.py:399` ordering;
  `board_view_service.py:94` render limit 50 — attacker items push genuine items below the
  fold); the ceilings + note-append preserve immediacy without timers; **single-reporter-HOLD
  REJECTED** (needs a second timing authority — the exact class R6 rejected);
  **`MAX_NOTES_PER_ITEM=20` truncation (`board_view_service.py:108/:255-259`) DISCLOSED and
  routed to the board-view lane (keep-latest)**; the suppression counter metric is phase 3's.

## Retention Sweep (R6)

**Hook (the exact seam, pinned):** `MaintenanceService.register(...)`
(`daemon/services/maintenance.py:141` — registered jobs run on intervals inside the
existing background loop, `check_interval_minutes=15`, `:117`); phase 2 adds ONE
registration beside the existing jobs in `daemon/manager.py`'s registration cluster
(`:4377-4525`; CheckpointCleanupJob construction precedent `:4349`):

```
self._maintenance_service.register(
    "anomaly_retention",
    min_interval_hours=24,
    execute_fn=make_anomaly_retention_job(anomaly_repository, config))
```

**Sweep body (bounded, batched, best-effort; AM-6 + AM-9 + AM-7 amendments):**
```
cutoff = (now_utc() - timedelta(days=retention_days)).isoformat(timespec="microseconds")  # AM-4: BOUND
deleted_total = 0
loop (max retention_batch_size × MAX_BATCHES_PER_RUN rows per tick):
    ids = SELECT id, cluster_id FROM anomaly_reports WHERE created_at < :cutoff LIMIT :batch
    if not ids: break
    DELETE FROM anomaly_reports WHERE id IN (:ids); deleted_total += len(ids)
    # AM-6: DECREMENT report_count per cluster by this batch's deleted-member count:
    for cluster_id, n in GROUP(deleted ids BY cluster_id):
        UPDATE anomaly_clusters SET report_count = report_count - :n WHERE id = :cluster_id
    if deleted_total >= per-run cap: break
# AM-9 capacity trim (oldest-first beyond max_rows_global, batched — alongside the age sweep):
loop:
    ids = SELECT id FROM anomaly_reports ORDER BY created_at ASC LIMIT :batch   # only when COUNT > max_rows_global
    if COUNT(all rows) <= max_rows_global: break
    DELETE ... + the same per-cluster DECREMENT as above
# clusters after their member reports:
loop:
    ids = SELECT id FROM anomaly_clusters WHERE last_report_at < :cutoff LIMIT :batch
    if not ids: break
    DELETE FROM anomaly_clusters WHERE id IN (:ids)
# AM-7: board lifecycle — transition board items to `done` once their cluster resolves
# (last_report_at < now − resolve_window) via update_item(status="done") — a BOARD-row
# write ONLY, never a cluster write (R5 forbids cluster status); pin
# board_item_closes_when_cluster_resolves
```
- Per-run cap (config `retention_batch_size=500`, MAX_BATCHES=10 → ≤5,000 rows/tick) keeps
  any single maintenance tick bounded; the next daily run continues.
- Best-effort per house sweep convention: an exception logs WARNING and the job returns —
  never crashes the maintenance loop.
- **AM-6 invariant:** with the decrement, `report_count == COUNT(anomaly_reports WHERE
  cluster_id=:id AND outcome='accepted')` per cluster holds ABSOLUTELY — without it a
  chronic cluster (lifetime > 30d) permanently over-counts surviving rows, and the
  refuse-vs-merge defense RESTS on this invariant. Pins: `report_count_equals_member_rows`
  (both directions), `retention_decrements_counts`.
- **AM-15 disclosure (NR-7):** idle-gated maintenance starvation — the 24h retention NEVER
  runs on a continuously busy nest (the checkpoint-cleanup class; `maintenance.py:258-262`,
  `:326-395`). Disclosed here + routed to the maintenance lane (the class is systemic); the
  AM-9 insert gate keeps the flood bounded even while the sweep starves — **F-1-corrected
  (fold round 3): this sentence was FALSE as written** (pre-F-1 the gate counted accepted
  rows only, so refused rows flowed unbounded while the only drain starved exactly during
  flood; post-F-1 the TOTAL-row bound stops refusal inflow at the ceiling).
- **Fallback (decided, R6):** if the registration seam proves non-cheap at implementation
  (e.g. ordering constraints surface), the fallback is a documented operator runbook step +
  a routed follow-up commission — NO new scheduler is built. (Seam verified to exist and be
  cheap: `maintenance.py:103-141` is a generic register-and-run service.)

**AM-5b orphan terminalization (the defense-in-depth sweep):** any row
`outcome='accepted' AND cluster_id IS NULL` is an ORPHAN (crash window between INSERT and
UPDATE, or a legacy live failure) — TERMINALIZED to `outcome='unclustered'` by a bounded
age-gated leg of this same maintenance job, NEVER reassigned (post-hoc reassignment =
retroactive identity instability — exactly what R5 rejects). With it, **accepted MEANS
clustered** — the metric counts accepted and the route serves by cluster_id without
disagreeing. Pins: `no_orphan_accepted_rows`, `orphan_terminalized_not_reassigned`
(mutation: reassign → red). Cross-ref: AM-6c (FE grouping of recurring same-key clusters)
is phase 4's; this phase owns the terminal state.

## Pins (each is a named Tier-1 test; all must be mutation-meaningful)

| Pin | Statement | Proof shape |
|---|---|---|
| `cluster_id_stability` | a late report NEVER moves an earlier report's cluster_id | report A (t0) → C1; quiet > W; report B same key (t0+2h) → C2 (new row); assert A.cluster_id==C1 unchanged, B.cluster_id==C2; also assert re-incident INSERT is NOT an IntegrityError (the R5 unique-rejection proof) |
| `sliding_window_chains` | reports at t0, t0+5m, t0+12m, t0+22m → ONE cluster (gaps 5m/7m/10m all < W) | 4 inserts, 1 cluster row, report_count=4 — a fixed bucket would have split at t0+15m |
| `n_agents_one_incident` | 3 sibling agents (same root, same canonical category) inside W → 1 cluster | 3 rows share cluster_id; report_count=3; parent chains differ, tree_root equal |
| `rate_limit_refusal` | 6th minor within 10min → typed refusal + honest ledger | refusal envelope token `rate-limited`; 6th row outcome=rate_limited, harvest columns NULL, cluster counts unchanged; 5 accepted + 1 refused = budget still 5 (refusal didn't consume) |
| `critical_exempt_then_capped` | criticals bypass the 5/10min but cap at 20/hour | 6th critical within 10min ACCEPTED; 21st critical within the hour REFUSED |
| `board_single_row_per_cluster` | two criticals on one cluster → exactly ONE board item + ONE note | board item count for the cluster == 1; notes == 1; concurrent-leg variant: two simultaneous criticals on a fresh cluster still yield one item (lock covers the seam) |
| `resolution_derived` | no status column mutation exists; open/closed is computed | grep-level assertion (no reopen write path) + a read-time openness check flips at resolve_window without any write |
| `notify_never_called` | the anomaly path cannot voice | import-graph assertion: `daemon/services/anomaly_service.py` references no notify symbol; behavioral: critical report produces zero notify-audit rows |
| `no_orphan_accepted_rows` (AM-5) | after ANY accepted report settles, zero rows carry `outcome='accepted' AND cluster_id IS NULL` | post-assignment SELECT count == 0; crash-injection leg: kill between INSERT and UPDATE → the sweep terminalizes to `unclustered` |
| `orphan_terminalized_not_reassigned` (AM-5) | an orphan becomes `unclustered`, NEVER re-clustered | MUTATION: make the sweep reassign instead → RED |
| `report_count_equals_member_rows` (AM-6) | `report_count == COUNT(members WHERE outcome='accepted')` per cluster, both directions | build a chronic cluster, sweep mid-lifetime members → count decrements and stays equal; reverse: fabricate drift → pin REDs |
| `retention_decrements_counts` (AM-6) | each deleted member decrements exactly its cluster's count by 1 | batch-delete N members of one cluster → report_count −= N exactly |
| `board_item_closes_when_cluster_resolves` (AM-7) | a resolved cluster's board item reaches `done` | advance last_report_at past resolve_window → maintenance tick → item status == done; cluster row itself UNTOUCHED |
| `unbounded_refusal_loop_capped` (AM-9) | N refused calls persist ≤ refusal_ledger_max_per_agent refusal rows — **F-1 global leg: the ledger INSERT never takes TOTAL rows (accepted + refused) above `max_rows_global`** | fire 50 refused calls → COUNT(rate_limited rows) ≤ 10, envelope still returned every call; **F-1 mutation-meaningful leg: plant a flood with the per-agent ledger cap UNSATURATED — the ceiling must fire ON THE REFUSAL PATH** (the round-0 form alone was a false-confidence fence under the AM-16 conditionality gate) |
| `global_capacity_cap_leg` (AM-9) | the global/hour + row-count ceilings refuse with `capacity-exhausted` | seed rows to a ceiling → next report refused `capacity-exhausted`, no ledger row |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Repository: cluster lookup/insert/rollup + counting + retention helpers — **AM-5a: the assignment path as ONE unit-of-work** (report INSERT + cluster INSERT/UPDATE + report→cluster UPDATE in one transaction, one db_offload call); **AM-6: per-cluster decrement helper**; **AM-5b: orphan-terminalization UPDATE** | phase 1 models | each query's plan uses the pinned index; ALL cutoffs bound parameters (AM-4); unit packs on scratch DB incl. the unit-of-work atomicity leg |
| 2 | Service: deny-list gate (AM-16) + rate-limit gate moved pre-harvest with **AM-9 fences** (ledger cap no-row-beyond, global/hour ceiling, pre-INSERT row caps → `capacity-exhausted`); ledgered-refusal writer | task 1 | refusal pins + ledger-cap pin + global-cap leg green; budget-counts-accepted-only proven (refusal doesn't extend window) |
| 3 | Service: cluster assignment (**AM-14 64-slot stripe lock** + pseudocode above), degenerate phase-1 tail deleted | task 1 | stability/chaining/n-agents pins green; stripe preserves same-key serialization (concurrent same-key leg) |
| 4 | Service: `ensure_board_surface` (create once / note-append / **AM-13 ceilings + note-append fallback + suppression counter** / **AM-12e deterministic top_description + prefix line** / title carries cluster_id) via BoardService | task 3 | board pins green incl. concurrent leg + ceiling-fallback leg; title/body/tags/created_by_instance per spec |
| 5 | Retention job + manager registration at `:4377-4525` cluster (**AM-6 decrements; AM-9 capacity trim; AM-5b orphan terminalization; AM-7 board item → done**) | task 1 | sweep deletes 30d+ rows in bounded batches WITH count decrements; capacity trim converges; orphans terminalized never reassigned; resolved clusters' board items reach done; live rows untouched; WARNING-isolated failure leg |
| 6 | Envelope flip: real cluster fields replace `pending-phase-2` | tasks 2,3 | envelope parse pack updated; no tool-signature diff vs phase 1 |
| 7 | Tier-1 pack (all pins incl. the eight fold-round-2 pins + regression of phase-1 ACs) | tasks 1-6 | pack green; net-new reds vs TRUE base `1f6898ae2` = 0 |

## Gate Class + Acceptance Criteria

**Gate class:** Tier-1 (scoped packs); whole-tree/Tier-2 + staging are phase 6. Net-new-red
discipline vs TRUE base `1f6898ae2`.

- AC1: **clustering** — the three clustering pins green on scratch PG (not just SQLite):
  same-key writes collapse; distinct keys never collapse; re-incident after quiet creates a
  second cluster with no constraint error.
- AC2: **rate limits** — refusal pin + exemption pin green; ledgered rows carry authored
  content with harvest columns NULL; budget counts accepted-only (proven by a refused-then-
  accepted sequence inside one window).
- AC3: **board** — single-row-per-cluster pin green (incl. the concurrent critical leg);
  note-append updates counts; `created_by_instance` equals the reporting instance;
  `project_id` NULL (global); zero notify-audit rows produced (notify-never pin).
- AC4: **retention** — synthetic 31-day-old rows swept in ≤ bounded batches; recent rows and
  open clusters untouched; maintenance loop survives an injected sweep exception (WARNING).
- AC5: **identity** — `resolution_derived` pin green: openness flips at the 1h boundary with
  zero writes to any cluster column other than the rollup set.
- AC6: **phase-1 regression** — AC1-AC6 of phase1-plan.md still green after the service
  rewrite (envelope shape change limited to the cluster fields).
- AC7 (AM-4 + AM-5): every count/lookup binds a Python-computed fixed-width cutoff (grep the
  service for `now() -` SQL forms = zero hits); a kill-injected assignment failure leaves
  NO `accepted`+NULL row (terminalized `unclustered`), and the mutation leg (sweep reassigns
  instead) is RED.
- AC8 (AM-6 + AM-7 + AM-13): retention decrements hold the invariant absolutely (chronic-
  cluster leg); resolved clusters' board items reach `done`; board ceilings route overflow
  to note-append with the suppression counter incremented; the board title carries the
  cluster_id and the body carries the "auto-filed — unverified agent prose" prefix.

## Risks (phase-specific)

| Risk | Impact | Mitigation |
|---|---|---|
| Cluster lock becomes a serialization hotspot under a report storm | Medium | lock wraps ONLY assignment (2 small statements); rate limits refuse storms BEFORE harvest, so the locked section is rarely contended |
| Double board item under concurrent criticals | Medium | `ensure_board_surface` inside the cluster lock; concurrent-leg pin |
| Retention sweep deleting a cluster still referenced by an open board item | Low | clusters are swept only after their reports by `last_report_at` age; board item is left in place (audit trail) — the cluster link dying is acceptable post-resolution; disclose in CATEGORY_DOC |
| Rate-limit clock skew across off-loop reads | Low | single server clock (every timestamp is server-side); **W-3 correction (fold round 3): window math in PYTHON with the cutoff bound as a parameter (AM-4) — the former "window math in SQL" mitigation was itself the PG operator error** |
| Sliding-window UPDATE hot row (many reporters, one cluster) | Low | one UPDATE per accept on an indexed row — same write class as any counter rollup; acceptable at nest scale (bounded by R5b caps) |

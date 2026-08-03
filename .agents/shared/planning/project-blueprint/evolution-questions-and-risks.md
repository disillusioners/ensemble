# Project Blueprint Evolution — Open Questions and Risk Analysis (v2)

Date: 2026-08-03T21:25:45Z
Author: planner[v2] via technical-analysis worker
Analysis depth: deep-dive
Status: All open questions RESOLVED per leader decisions; risk analysis revised per C1–C10.

## Revision Notes (v2)

This document revises `evolution-questions-and-risks.md` to reflect the **C1 resequencing** (Phase 8 dissolved into phases 1–7) and to incorporate **5 leader decisions**:

1. **processed_at soft-delete** — ACCEPT
2. **G7 auto-dedup** — ACCEPT
3. **Model tier** — `'balanced'` if available, else `'quick'` with upgrade note
4. **project_history_add hook** — thread manager through factory
5. **Queue concurrency** — verify during implementation

The 7 open questions are **all resolved** and re-stated below with their final phase placement. The risk table is updated with v2 risk IDs (C-x prefix) and three new risks for canonical-write complexity, admission coordinator crash recovery, and embedding-fingerprint staleness. The C10 risk (context-kind allowlist missing in `persistence.py`) is added.

---

## Scope and Decision Summary

This document resolves the seven open questions in `.agents/shared/planning/project-blueprint/blueprinter-evolution.md` and assesses risks across the complete rebuild/incremental evolution. It is analysis only; it does not prescribe or apply source-code changes.

The evolution changes the current single-trigger blueprinter into two workflows: a full **rebuild** and a pending-record-driven **incremental update**, with worker fan-out/fan-in, `/rebuild` and `/update` APIs, a pending-experience table with claim/acknowledge state machine, and a unified admission coordinator with durable lease. `auto_rebuild_enabled=False` is the v2 production default.

**Phase mapping (v2):**

- **Phase 1** — Critical fixes + canonical write (`BlueprintWriteService`).
- **Phase 2** — Data layer (pending table, claim/ack state machine + `processed_at` soft-delete, G6/G7 + auto-dedup, G8, context-kind allowlist, embedding-fingerprint column).
- **Phase 3** — Services + admission coordinator (durable lease, unified trigger path, experience/history hooks through factory, `auto_rebuild_enabled` flag).
- **Phase 4** — API changes (`/rebuild` + `/update` + alias `/initialize`, all via admission coordinator).
- **Phase 5** — Blueprinter agent (compare/stage/publish, structured worker-report envelope, decide-changes model tier, skill files).
- **Phase 6** — Frontend (dual-mode button, job-id polling, error code preservation).
- **Phase 7** — Smart scan + E2E (PG + SQLite, crash-during-rebuild recovery, queue concurrency verification, embedding fingerprint regen).
- **Phase 8 — (OPTIONAL)** — Telemetry dashboards (droppable).

---

# Section 1 — Seven Open Questions (All RESOLVED)

## Q1 — Worker skill ownership — **RESOLVED**

### Final resolution (leader-confirmed)

**Blueprinter-owned, stored in `agents/blueprinter/skills-template/` (per the live seeder; NOT `skills/`). Pass worker skills explicitly with `send_message(..., load_skill=...)`. One explicitly loaded skill per worker dispatch. `decide-changes` remains blueprinter-only. The host attaches immutable `skill_id`, `skill_version`, and `worker_instance_id` to every report.**

`skill_injection: true` is **NOT** a seeding prerequisite. `seed_all()` scans manifests regardless of this flag (`daemon/services/skill_seed_service.py:238-281`). The flag gates **automatic dynamic injection** in the messaging path; enabling it on the blueprinter risks auto-injecting worker skills into the blueprinter's own context (self-referential). **OMIT** `skill_injection: true`; rely on explicit `send_message(..., load_skill=...)`. Add a `DEGRADED — skill bank miss` fallback if resolution fails.

### Technical justification

The current `agents/blueprinter/skills/` path proposal in the evolution document is inconsistent with the live seeding code. The seeder reads templates from `agents/<agent>/skills-template/<name>.md` and uses `skill-set.yaml` as the manifest (`daemon/services/skill_seed_service.py:238-298, 300-369`).

`skill_clone_service` resolves `(name, agent_id)` first, then deliberately falls back to a name-only lookup for parent-to-worker dispatches (`daemon/services/skill_clone_service.py:186-223`). `send_message` appends a controlled `<meta>` tag and the recipient-side explicit injector records resolved skill IDs for attribution (`daemon/tools/instance.py:1561-1619`). Usage records separately retain skill ID + consuming worker's `agent_id` (`daemon/repositories/skill/models.py:322-401`). This already supports "blueprinter owns the template, worker consumes it" without copying the template into the generic worker agent.

### Trade-offs

| Option | Benefits | Costs / failure modes | Assessment |
|---|---|---|---|
| **Blueprinter-owned templates** | One maintenance owner; version bumps and skill lineage localized; explicit dispatch prevents unrelated agents from receiving blueprint-corpus instructions. | The blueprinter manifest and template path must be seeded correctly; a worker cannot discover these skills through its own default manifest unless explicitly dispatched. | **ADOPTED.** |
| Shared/global templates | Reusable by other agents; fewer duplicated templates if another workflow later needs the same procedure. | Shared search can select blueprint-maintenance instructions for unrelated tasks; same-name collisions possible. | **Rejected.** Extract a shared skill later only after a demonstrated second owner/use case. |
| Worker-owned templates | Receiving worker's manifest would make local ownership obvious. | Generic `worker` would own all blueprint roles; couples a reusable worker to one parent's private workflow; obscures evolution lineage. | **Rejected.** |

### Guardrails / acceptance criteria (unchanged from v1)

1. Use `agents/blueprinter/skill-set.yaml` + `agents/blueprinter/skills-template/*.md`; manifest and frontmatter versions identical (`docs/agent-prompt-writing-guide.md:136-148`).
2. Reserve unique namespace-like names (`explore-for-rebuild`, `explore-for-incremental`, `build-blueprint`, `decide-changes`).
3. Workers receive exactly one role skill per wave; `decide-changes` never sent to workers.
4. The host records skill ID/version + worker instance ID outside the model-generated report.
5. A missing skill causes one bounded retry or a `DEGRADED — skill bank miss` report, never a silent skill-less success.

### Implementation phase

**Phase 5.**

---

## Q2 — Concurrent rebuild guard — **RESOLVED**

### Final resolution (leader-confirmed; absorbs old Phase 8 control)

**Use a durable database-backed per-project build lease, with the admission coordinator (C7) as the single acquisition point. Do NOT use an in-memory flag as the authoritative guard.** The lease may be represented by a dedicated `BlueprintBuildLease` table (recommended) or a dedicated key in the existing project-metadata table. Acquisition and release must be atomic and token-based.

Both `/rebuild` and `/update` acquire the same project lease. A failed claim returns 409. The claim carries `project_id, mode, job_id, lease_token, started_at, heartbeat_at, expires_at, state`. The blueprinter job releases only the matching token on success, failure, cancellation, or terminal recovery.

### Technical justification

The queue already persists `JobItem` rows for crash recovery, including `project_id, agent_id, job_metadata, queued/active admission states` (`daemon/repositories/job_queue/models.py:248-359`). The repository also has an atomic `create_or_get_by_idempotency_key()` specifically because read-then-insert was TOCTOU-vulnerable (`daemon/repositories/job_queue/repository.py:282-313, 326-413`). A plain "query active blueprinter jobs, then enqueue" implementation would reintroduce that race unless the query and claim are in one transaction or backed by a unique constraint.

The v1 detailed plan recommended an in-memory set plus 30-min TTL (`evolution-phases-detailed.md:439-472`). That protects two calls in one process but loses claim on daemon restart, cannot coordinate multiple API processes, and can expire while a slow but healthy rebuild still owns the project.

### Why the alternatives rank this way

| Option | Strength | Weakness | Decision |
|---|---|---|---|
| In-memory flag | Lowest implementation cost. | Lost on crash/restart; not shared across processes; stuck if release is missed. | **Rejected as authoritative.** May stay as fast-path optimization. |
| Active-job DB query | Sees queued/active jobs after restart. | SELECT-followed-by-enqueue is still racy; JSON metadata filtering is dialect-sensitive. | Use as reconciliation only. |
| Durable metadata field | Survives restart, explicit build lifecycle. | Naïve JSON mutation is race-prone; column on existing table needs dual-driver migration. | Feasible as storage shape if atomic. |
| **Durable lease + queue reconciliation + admission coordinator** | Atomic 409 decision, crash recovery, mode/token visibility, single admission path. | One additional persistence seam + recovery/sweeper path. | **ADOPTED (Phase 3).** |

### Crash-recovery contract (preserved from v1, expanded in v2)

- **Crash before claim:** no live job ID; expiry/reconciliation releases; next scan retries.
- **Crash after claim, before enqueue:** lease sweeped to released on next startup.
- **Crash after enqueue, while queued:** persisted queue row remains the source of truth; startup reconciliation retains the lease.
- **Crash while active:** queue admission state and existing stale-task recovery determine retry/terminal; lease heartbeat/expiry prevents permanent 409.
- **Enqueue failure after claim:** release the exact token in a compensating transaction; return 5xx/503, not false 202.
- **Completion race:** release only when `lease_token` AND `job_id` match.

A fixed TTL without heartbeats is only a safety net, not correctness. The 409 test must cover: two concurrent requests, API process restart, stale-lease expiry, job failure, second request after genuine completion.

### Implementation phase

**Phase 3** (not the deferred Phase 8 of v1).

---

## Q3 — Backward compatibility of `/initialize` — **RESOLVED**

### Final resolution (leader-confirmed)

**Keep `/initialize` as a deprecated internal alias to `/rebuild` for at least one release/telemetry window. Update the frontend to call `/rebuild` immediately, but do not remove the alias in this evolution.** Prefer internal delegation over HTTP 308 redirect so POST clients that do not follow redirects preserve behavior and response handling.

The alias returns the new asynchronous response shape (`202`, `job_id`, `mode: "rebuild"`) and the same durable 409 behavior. Add an explicit deprecation marker in OpenAPI and response headers/logs, document a removal version, and count alias calls. Do NOT silently preserve the old "core already exists" meaning.

### Technical justification

The current frontend service calls `/initialize` (`frontend/src/app/services/blueprint.service.ts:178-207`). The router rejects an existing core with 409 (`daemon/routers/blueprints.py:232-300`). The known consumer can be updated; but the repository has no evidence that external scripts, bookmarks, or integrations do not use the public path. Absence of a test exercising `/initialize` (`tests/unit/test_blueprint_api.py:1-24`) is not evidence that no callers exist.

The evolution explicitly changes the semantics: a full rebuild is allowed when a corpus already exists; 409 is reserved for concurrent conflict only (`evolution-phases-detailed.md:569-580`). Keeping an alias avoids an avoidable breaking path change while the deprecation contract makes the semantic change visible.

### Migration policy (preserved from v1)

1. Add `/rebuild` and `/update` as canonical endpoints (both via admission coordinator).
2. Route `/initialize` through the same internal rebuild function; do NOT duplicate enqueue or guard logic.
3. Return deprecation header / OpenAPI flag; log caller/path.
4. Update the Angular service + component and add API tests for both new endpoints and the alias.
5. Remove only after documented telemetry shows no alias use and a release boundary has passed. If no telemetry is available, **retain the alias indefinitely** — maintenance cost is small compared to an unknown external break.

### Implementation phase

**Phase 4.**

---

## Q4 — Blueprinter model for the decide phase — **RESOLVED**

### Final resolution (leader-confirmed; absorbs old Phase 8 control #7)

**Read `decide_model_tier` from `agents/blueprinter/meta.json` (`"balanced"` | `"quick"`). Default value when missing: use `'balanced'` if configured and available in the allowed-models list, else fall back to `'quick'`. Add an upgrade note documenting the trade-off.**

Keep cheap routing/validation and worker exploration on their current models; run `decide-changes` and the final save-set validation on the selected tier. Workers use their own models, so upgrading them all would add cost without addressing the highest-leverage decision point. One stronger decision call per run is cheaper than four workers on premium models.

### Trade-off guardrails

- **`balanced` tier (preferred):** stronger reasoning, higher cost; used only on the `decide-changes` skill and final save-set validation.
- **`quick` tier (fallback):** acceptable for bounded non-destructive checks; mark the run `degraded` if used for a high-confidence decision.
- **No hardcoded vendor aliases:** must come from `config.llm.allowed_models`.
- **Write gate:** structured evidence + confidence + material-diff checks + manual-content rules precede writes; model upgrade is not a substitute for gates.
- **Evaluation:** label a sample of create/update/disable decisions, measure false updates and manual-overwrite attempts, compare cost/latency vs `quick` baseline in Phase 7.

### Technical justification

`agents/blueprinter/meta.json:8` currently sets `llm_model` to `quick`; registry precedence allows an agent-level model and a spawn-time override (`daemon/registry.py:253-263, 523-589`; `daemon/manager.py:4337-4352`). The fan-in decision is not ordinary summarization: it chooses no-op/create/update/disable, reviews `core.md`, and decides whether manual material can be replaced (`blueprinter-evolution.md:128-139`; `evolution-phases-detailed.md:777-807`). A bad explore report affects one area; a bad decide result can overwrite/disable many blueprints and alter the project skeleton injected into every new agent.

### Implementation phase

**Phase 5** — read tier from `meta.json:decide_model_tier`; default fallback at `agents/blueprinter/soul.md` boundary. Verification in Phase 7 E2E.

---

## Q5 — Pending queue growth management — **RESOLVED**

### Final resolution (leader-confirmed; absorbs old Phase 8 control #2 + reviewer C3 + C8)

**Use a durable high-water mark with coalesced early triggering, bounded claim batches, and exact-record acknowledgement via the claim/acknowledge state machine. Do NOT use TTL expiry or "delete all pending rows" as the primary control.**

`processed_at` is the soft-delete marker — rows are NEVER hard-deleted. A configurable initial threshold such as 50 records is reasonable, but it must trigger an incremental job even outside the daily window and must NOT enqueue one job per record.

### Claim/acknowledge state machine (NEW, C3)

```
                  ┌──────────────┐
                  │   INSERT     │  (experience() / history_add side)
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐
   ┌──────────────┤   pending    │  (immutable row, has record_id)
   │              └──────┬───────┘
   │  (high-water         
   │   INSERT trigger)    │
   │                     ▼
   │              ┌──────────────┐
   │              │   claimed    │  (claim_token, claimed_at, lease_id)
   │              └──────┬───────┘
   │  (lease expiry       
   │   sweeps here)       ▼
   │              ┌──────────────┐
   └─────────────►│  processed   │  (processed_at soft-delete marker)
                  └──────────────┘
   never hard-deleted
```

### State-machine invariants

1. **Never clear-all.** Records persist forever, marked `processed_at` at acknowledge time.
2. **Atomic claim.** `claim_pending(batch_size, lease_token)` is one transaction that flips `pending → claimed` for the oldest N rows by `record_id`. Concurrent callers see disjoint claim sets.
3. **Bounded retry window.** Lease expiry (default 30 min) returns claimed rows to `pending`. Crash during incremental run → next scan re-claimable.
4. **Crash-safe.** A `claimed` row whose `lease_id` no longer exists is orphan-reclaimable.
5. **G7 auto-dedup (NEW, leader-accepted).** Dedup key on `(project_id, source_kind, content_hash)` — if a record exists in last 24h with the same hash, do not insert. Audit signal preserved in `pending_duplicates_audit` (separate table; NEVER deleted).

### Lifecycle steps

1. **Insert** each event with `project_id, source, source_kind, content_hash, timestamp, record_id (stable), bounded text`. Preserve the row if blueprinter is unavailable; log/metric insertion failures; provide retry/reconciliation rather than silent "handled".
2. **High-water at INSERT time** (NOT in scan logic — that branch was unreachable). When count crosses threshold, atomically request one incremental job for that project. A second insert while a run is queued sees the existing lease/idempotency claim and does NOT enqueue another job.
3. **At run start**, claim a bounded, oldest-first batch by IDs (with `claim_token`). Pass summaries/references to workers, never an unbounded concatenated prompt.
4. **After writes and embeddings for that batch are committed**, ack exactly those IDs by setting `processed_at`. Records inserted during the run remain for the next batch. On failure, leave them retryable; release claims via lease expiry.
5. **Continue draining** while backlog remains; expose backlog age/count and a `degraded` state if exceeds a second emergency threshold.
6. **TTL only for explicit, observable archival.** Never silently discard architectural evidence because a daily scan was late.

### Technical justification

The evolution stores every `experience()` entry and selected history event, truncates text to 10,000 characters, and originally proposed deleting all pending records after a successful incremental run (`blueprinter-evolution.md:63-84`). The detailed plan added a max-pending threshold (`evolution-phases-detailed.md:1169-1219`) but its order was defective: `if pending_count > 0` at lines 1204-1207 returns before `pending_count >= MAX_PENDING_FORCE` at 1209-1214 — **the threshold was unreachable**. Moving the trigger to INSERT time fixes that.

`MaintenanceService` runs registered jobs only when the daemon is idle (`daemon/services/maintenance.py:68-81, 300-340`), so a busy daemon or restart can postpone daily processing. The existing experience sidecar was disabled specifically because unbounded per-event blueprinter jobs flooded the queue (`daemon/tools/knowledge_tools.py:411-434`). The safe design is **"many durable signals, few coalesced jobs."**

### Implementation phase

**Phase 2** (schema + state machine), exercised in **Phase 3** (admission coordinator trigger) and **Phase 7** (E2E).

---

## Q6 — Worker report format — **RESOLVED** (now Phase 5)

### Final resolution (leader-confirmed; absorbs old Phase 8 control #5)

**Adopt a versioned structured JSON envelope with free-form evidence and notes inside bounded fields.** Do NOT use unconstrained free-form reports, and do NOT force every sentence into a rigid enumerated schema. The blueprinter parses/validates the envelope, retains the raw report for diagnostics, performs at most one repair retry, and treats an invalid report as incomplete evidence rather than authorizing a write.

**Phase placement change vs v1:** Q6 was deferred in v1 and the structured-recommendation was queued for Phase 8. **In v2, structured reports are first-class in Phase 5** — retrofitting structure later means re-testing all skill files.

### Envelope contract (C5)

```json
{
  "schema_version": 1,
  "workflow": "rebuild" | "incremental",
  "phase": "explore" | "build",
  "assigned_scope": "...",
  "summary": "...",
  "findings": [
    {
      "target": "<blueprint_slug or scope>",
      "action": "create" | "update" | "disable" | "noop",
      "evidence": "...",
      "verified_paths": ["/abs/path1", "/abs/path2"],
      "confidence": 0.0,
      "gaps": ["..."]
    }
  ],
  "craft_payload": { ... },
  "status": "complete" | "incomplete",
  "submitted_at": "ISO-8601",
  "skill_id": "<host-attached>",
  "skill_version": "<host-attached>",
  "worker_instance_id": "<host-attached>"
}
```

### Reliability controls

- Validate required fields, size limits, word limits, path existence, and allowed actions BEFORE fan-in decisions.
- Prefer native structured-output support when the selected model provides it; otherwise parse raw JSON and issue one constrained repair request.
- If parsing still fails, mark the worker incomplete, use the prompt guide's bounded re-dispatch / partial-report escape valve, and never silently infer a delete/update action (`docs/agent-prompt-writing-guide.md:162-173`).
- The blueprinter must independently verify high-impact evidence with filesystem/knowledge tools; a valid JSON shape is not proof that its claims are true.
- Version the envelope so later fields can be added without breaking old workers.

### Implementation phase

**Phase 5.**

---

## Q7 — Blueprint deletion strategy during rebuild — **RESOLVED** (now Phase 5)

### Final resolution (leader-confirmed; absorbs old Phase 8 control #4)

**Compare and stage; update unchanged identities in place; create new areas; soft-disable confirmed-stale auto-generated areas only after the replacement set is validated.** Never hard-delete or delete-first. Preserve manual blueprints by default; preserve stable IDs and revisions; update `core` in place rather than creating a second core.

**Phase placement change vs v1:** Q7 was realized as compare/stage/publish and was deferred to Phase 8 in v1. **In v2, it is first-class in Phase 5.**

### Compare / stage / publish (C4)

1. **Compare** desired set (worker findings) with current active set, keyed by stable identity (slug + content fingerprint).
2. **Classify** each item: `noop`, `update`, `create`, `disable`.
3. **Stage**: write new/changed rows as `status="draft"` in a separate transaction. Trigger embeddings NOT yet regenerated. Matcher continues to see old `published` set (safe fallback).
4. **Validate**: every staged row has triggers; every trigger embedding succeeded; every revision captured; no orphan embeddings left from disabled rows.
5. **Publish**: set `status="published"` atomically per row, regenerate embeddings in the same transaction, soft-disable confirmed-stale auto rows (`source="auto"` not in new desired set).
6. **Rollback**: any step after Stage failing leaves the published set intact.

Manual content (`source="manual"`) is **NEVER** auto-disabled or overwritten. Review-needed state preserved for human triage.

### Technical justification

The data model has `is_active` soft deletion and append-only revision snapshots (`daemon/repositories/blueprint/models.py:19-83, 149-205`); the API's delete is explicitly soft-delete (`daemon/routers/blueprints.py:381-400`). Architecture promises revision-based rollback and higher protection for `source="manual"` content (`plan-overview.md:446-456, 540-555`). Delete-first would conflict with those invariants: a failed fan-out, LLM outage, embedding failure, or rate-limit stop could leave the project with no usable area corpus, and stable IDs/references/analytics would be needlessly disrupted.

A rebuild produces a desired set keyed by stable identity, compares with the current active set, and classifies each item as no-op, update, create, or disable. Unchanged content does NOT receive a spurious version/revision. Changed content writes a revision and replaces triggers/embeddings as one logical publish unit. New or changed rows are first validated/staged as non-injectable; because the matcher admits only `published`/`active` rows after G8, the old published corpus remains the safe fallback while staging occurs.

### Decision matrix

| Strategy | Data safety | Performance | Manual-edit behavior | Decision |
|---|---|---|---|---|
| Delete old first, then recreate | Poor: partial failure leaves gaps; IDs change. | Simple but max writes. | High risk of erasing/hiding manual material. | **Reject.** |
| Always keep all old rows | Strong but stale rows remain injectable. | Lowest cost; corpus grows indefinitely. | Preserves edits but never retires obsolete areas. | **Incomplete.** |
| **Compare, stage, publish, soft-disable confirmed-stale auto rows** | Strong: old published set remains fallback. | Only changed items consume writes. | Manual rows protected by policy. | **ADOPTED.** |

### Implementation phase

**Phase 5.**

---

# Section 2 — Risk Analysis (v2)

## Rating and evidence conventions

Likelihood and impact are qualitative assessments for the first production rollout: **Low** = unusual/contained, **Medium** = plausible under normal operation or a known gap, **High** = expected without the listed control or capable of corrupting the corpus/queue. Phase columns use the v2 phase numbering (1–7, Phase 8 optional). Evidence references point to the current branch or the evolution plans; line numbers are snapshot references and should be rechecked after implementation.

**v2 ID convention:** Risks carried from v1 with new in-phase owners are renamed with a `C-` prefix (e.g., v1 `D2` → v2 `C-D2` because it is mitigated in **Phase 2** with the claim/acknowledge state machine). Risks that were Phase-8-deferred and are now closed in another phase carry the `C-` prefix. New risks carry an explicit "NEW" tag.

---

## 2.1 Data integrity risks

| ID | Risk description | L | I | Mitigation / control | Affected phases | Status | Evidence |
|---|---|---:|---:|---|---|---|---|
| D1 | A pending-row INSERT fails on the hot path and the fire-and-forget error is swallowed. | Med | Med | Keep tool non-blocking; durable failure metric/log; outbox/startup reconciliation; observable insert. | 2, 3 | Phase 2 mitigations in queue | `knowledge_tools.py:360-444` |
| **C-D2** (formerly v1 D2) | "Clear all pending rows" races with concurrent arrivals during incremental run. | High | High | Claim a batch by immutable IDs and ack only successfully incorporated IDs after save/revision/embedding commit. Leave concurrent arrivals for next run. | **2** | **Mitigated in Phase 2** (state machine + `processed_at`) | `evolution-phases-detailed.md:865-873` |
| **C-D3** (formerly v1 D3) | Blueprint content changes but no revision row is written — current verified wiring gap. | High | High | Centralize writes in `BlueprintWriteService` (transaction: row write + revision snapshot + trigger replacement). | **1** | **Mitigated in Phase 1** | `daemon/repositories/blueprint/repository.py:73-89, 166-187` |
| D4 | Concurrent API, daily-scan, and worker saves update the same blueprint or create duplicate `core` rows. | Med | High | Durable build lease, one-core DB/app constraint, optimistic version, stable run token, deterministic identity mapping. | 3 | Phase 3 mitigations | `daemon/repositories/blueprint/repository.py:41-49` |
| D5 | A full rebuild overwrites or soft-disables a manually edited blueprint. | Med | High | Treat `source="manual"` as protected by default; require higher confidence + explicit policy; review-needed state instead of deleting on ambiguity. | 5 | Phase 5 + compare/stage/publish | `agents/blueprinter/rule.md:7-9` |
| **C-D6** (formerly v1 D6) | Failure between content update, trigger replacement, embedding, and publish leaves a mixed version. | Med | High | Stage drafts, validate derived data before publish, group logical writes transactionally, attach version to trigger batches, run reconciliation for missing/mismatched embeddings. | **5** | **Mitigated in Phase 5** (stage/publish + fingerprint) | `daemon/repositories/blueprint/repository.py:102-164` |
| D7 | Multiple identical/near-identical experience/history events inflate the pending queue. | Med | Med | Preserve source + timestamps; **G7 auto-dedup** with safe content hash; cap per-run payloads; aggregate without deleting audit signal. | 2, 5 | Phase 2 auto-dedup + Phase 5 reporting | `blueprinter-evolution.md:65-82` |
| **CW-1** (NEW) | `BlueprintWriteService` complexity — five invariants (revision, trigger replacement, rate-limiter, optimistic version, manual-content guard) in one service invite subtle bugs. | Med | High | Extensive unit tests for each invariant; mutation tests on rate-limiter and revision-capture paths; explicit manual-content guard tests. | 1 | **Mitigated in Phase 1** | (no prior evidence; service is NEW) |
| **EF-1** (NEW) | Embedding model-migration staleness — old vectors live alongside new model config; mismatch undetected. | Med | High | Embedding-model fingerprint column on triggers; mismatch detected on read; background regen (never on hot path); full sweep on first startup after config change. | 2, 7 | **Mitigated in Phase 2 + Phase 7** | (NEW design) |

---

## 2.2 Operational risks

| ID | Risk description | L | I | Mitigation / control | Affected phases | Status | Evidence |
|---|---|---:|---:|---|---|---|---|
| **C-O1** (formerly v1 O1) | Daemon restart during rebuild loses in-memory guard; stale lock → permanent 409. | Med | High | Durable lease with heartbeat/expiry + queue reconciliation; lease-token-matched release in every terminal path. | **3** | **Mitigated in Phase 3** | `evolution-phases-detailed.md:439-472` |
| O2 | Every experience/history/daily event enqueues a blueprinter job — floods `system_background_queue`. | High w/o control | High | Coalesce per project/mode; one durable lease/idempotency claim; high-water trigger at INSERT time (not in scan); cap queue depth. | 3 | Phase 3 mitigations | `knowledge_tools.py:411-434` |
| **C-O3** (formerly v1 O3) | Rate limiter is in-process; enforcement methods had no production callers. Router/UI writes bypass. | High | High | `BlueprintWriteService` enforces limiter on EVERY write (router, tool, blueprinter). Counters coordinated for multi-process. | **1** | **Mitigated in Phase 1** (canonical service) | `daemon/services/blueprint_rate_limiter.py:28-97` |
| O4 | `MaintenanceService` idle gate delays daily scan indefinitely during sustained work; in-memory schedule has no durable "last successful scan". | Med | Med | Startup catch-up; persist last-attempt/last-success; high-water pending requests coalesced incremental independent of daily window; alert on oldest-pending age. | 3, 7 | Phase 3 mitigations + Phase 7 verification | `daemon/services/maintenance.py:68-81, 300-340` |
| O5 | One of four exploration/crafting workers crashes, times out, or never reports — fan-in waits forever. | Med | High | Bounded fan-in escape valve: detect stuck worker, re-dispatch once with same skill, mark area incomplete, preserve old content. Per-worker timeout; partial-run status; no-delete-on-missing-report. | 5, 7 | Phase 5 + crash-recovery E2E | `blueprinter-evolution.md:24-25, 118-163` |
| O6 | Cancellation or failure during decide/craft/save phase leaves build lease/pending claim/staged blueprint in unusable state. | Med | High | Persist run state + tokens; terminal cleanup/finally semantics; expire claims independently of the model; keep drafts non-injectable; next scan resumes / safely retries. | 2, 3, 5, 7 | Phase 2 + 3 mitigations + Phase 7 crash-recovery E2E | `plan-overview.md:657-711` |
| O7 | Limiter/circuit-breaker state resets on restart — burst of writes immediately after recovery. | Low/Med | Med | Persist recent write timestamps OR startup cooldown based on durable revision timestamps; limiter below both API and agent paths. | 1, 3 | `BlueprintWriteService` in Phase 1 | `daemon/services/blueprint_rate_limiter.py:6-8` |
| O8 | Pending text or worker output contains prompt-injection instructions or false architectural claims; autonomous blueprinter treats as authoritative. | Med | High | Delimit records as untrusted evidence; require verified file references + corroboration; prohibit direct instruction following from corpus text; structured reports with confidence/gaps; no-op/review on conflict. | 2, 5, 7 | Phase 2 + 5 mitigations | `daemon/services/context_messages.py:110-153` |
| **AC-1** (NEW) | Admission coordinator crash recovery — orphan leases, retry storms, dual-claim races. | Med | High | Lease sweep on startup; heartbeat-based expiry; coordinator-level request dedup via the lease table itself (atomic claim). | 3 | **Mitigated in Phase 3**; Phase 7 crash E2E verifies | (NEW) |

---

## 2.3 Migration and compatibility risks

| ID | Risk description | L | I | Mitigation / control | Affected phases | Status | Evidence |
|---|---|---:|---:|---|---|---|---|
| M1 | Removing `/initialize` breaks current Angular client or unknown external callers. | Med | Med | Keep deprecated internal alias; update frontend to `/rebuild`; deprecation metadata; log alias use; remove only after telemetry window. | 4, 6 | Phase 4 mitigations | `daemon/routers/blueprints.py:235-300` |
| M2 | Alias preserves path but changes semantics: 409 used to mean "already initialized"; new rebuild permits existing corpus. | Med | Med | Document semantic change; return `mode="rebuild"`; reserve 409 for active conflict; migration note for clients using 409 as state probe. | 4 | Phase 4 mitigations | `daemon/routers/blueprints.py:249-257` |
| M3 | New pending table or build-lock schema is absent on existing PostgreSQL deployments. | Med | High | Register new SQLModel tables; startup schema verification; test on real PG and file/in-memory SQLite; use `_ensure_postgres_columns()` for changes to existing tables; never rely on a `.sql` file that is a no-op on PG. | 2, 3, 7 | Phase 2/3 mitigations + Phase 7 verification | project critical note: PG-only migration fix |
| M4 | One-core/status/lease constraints added only at app layer or only in one dialect. | Med | High | Preflight + reconcile existing data; portable DB constraints; application validation; race tests on PG; explicit migration pause/rollback. | 2, 3, 7 | Phase 7 verification | `daemon/repositories/blueprint/models.py:24-43` |
| M5 | Evolution uses `skills/` while seeder reads `skills-template/`, or manifest is malformed; workers run without intended skill silently. | High if literal | Med/High | Use the live path; seed-test on startup; verify `load_skill` end-to-end; namespace names; degraded report on bank miss; do not conflate `skill_injection` with seeding. | 5 | Phase 5 mitigations | `daemon/services/skill_seed_service.py:238-327` |
| M6 | Changing agent prompt files / team membership / model metadata breaks discovery or silently drops a field on the registry retry path. | Med | Med | Follow `docs/agent-prompt-writing-guide.md`; test primary + retry discovery constructors; validate `team_members=["worker"]`; verify model availability against allowed-model list. | 5, 7 | Phase 5 mitigations + Phase 7 verification | `daemon/registry.py:523-597` |

---

## 2.4 Performance and capacity risks

| ID | Risk description | L | I | Mitigation / control | Affected phases | Status | Evidence |
|---|---|---:|---:|---|---|---|---|
| P1 | Each blueprint create/update generates 3–10 trigger embeddings sequentially. | Med | Med/High | Generate only after material diff; batch embedding inputs when supported; cache identical queries; cap/validate 3–10 queries; background queue + circuit breaker. | 1, 5, 7 | `BlueprintWriteService` in Phase 1 | `evolution-phase1-fixes.md:158-190, 420-423` |
| P2 | First-turn matching embeds user query + scans JSONB vectors/in-memory BM25 candidates; large corpus increases latency on message path. | Med | High | Keep five-slot cap + threshold; bound candidate/payload sizes; cache immutable per-project derived data; instrument p95 matcher latency; preserve fail-open injection. Consider vector/index redesign only after measured need. | 5, 7 | Phase 5 mitigations + Phase 7 instrumentation | `daemon/services/blueprint_matcher.py:17-59, 287-405` |
| P3 | BM25 min-max normalization fails for single-candidate / equal-score corpus; status filtering gaps inject drafts. | High until fixes | Med/High | G6 single-candidate normalization; G8 published/active filtering; test zero/shared-term/single-candidate cases; calibrate thresholds on production-like queries. | 2 | Phase 2 mitigations | `daemon/services/blueprint_matcher.py:342-376` |
| P4 | Two fan-out waves of up to four workers multiply LLM concurrency, context payloads, DB connections, worker-pool occupancy. | Med | High | Four-worker ceiling; bounded queue priority/timeouts; partition + summarize inputs; do NOT pass full pending corpus to every worker; measure worker-pool headroom before raising limits. | 5, 7 | Phase 5 mitigations + Phase 7 concurrency verification | `daemon/utils.py:529-571` |
| P5 | Persistent injection adds up to five 200–500-word blueprints (≈3,000–3,500 tokens); frozen for instance, even after rebuild. | Med | High | Enforce word/slot/threshold limits; preserve core brevity; expose blueprint version/age; spawn fresh instances for materially different tasks; measure context-window failures. Do NOT silently rematch every turn against persistent-block invariant. | 5, 7 | Phase 5 mitigations + Phase 7 E2E | `daemon/services/context_messages.py:1347-1384` |
| P6 | Pending rows + append-only revisions grow without bound during outages / long-lived projects. | Med | Med | Index by project/time; paginate + batch; monitor oldest pending age + revision counts; archive under explicit retention; compact duplicate pending evidence without deleting audit history. | 2, 5, 7 | Phase 2/5 mitigations + Phase 7 monitoring | `daemon/repositories/blueprint/models.py:149-190` |
| **C-P7** (formerly v1 P7) | Stronger decide model + trigger embeddings + repeated rebuild retries increase LLM/API spend. | Med | Med/High | Configurable decide_model_tier (`balanced` default with `quick` fallback); use stronger models only at high-leverage fan-in; batch/cache embeddings; rate-limit per project; report cost/latency per run before enabling auto escalation. | **5** | **Mitigated in Phase 5** | `agents/blueprinter/meta.json:8` |

---

## 2.5 Agent quality risks

| ID | Risk description | L | I | Mitigation / control | Affected phases | Status | Evidence |
|---|---|---:|---:|---|---|---|---|
| **C-A1** (formerly v1 A1) | `quick` blueprinter model makes a poor fan-in choice about create/update/disable. | High w/o control | High | Configurable stronger decide_model_tier (balanced default, quick fallback); structured evidence/confidence; no-op on ambiguity; staged publish; manual protection; revision rollback. Benchmark against labeled decisions. | **5** | **Mitigated in Phase 5** | `agents/blueprinter/meta.json:8` |
| A2 | Worker explores too little, hallucinates a module purpose, returns unverified file references. | Med | High | Bounded scopes; required verified paths/symbols; independent filesystem checks; confidence/gaps; one bounded retry; NO write/delete from incomplete reports. | 5, 7 | Phase 5 + Phase 7 verification | `evolution-phases-detailed.md:683-743` |
| **C-A3** (formerly v1 A3) | Free-form or inconsistent reports cause the decide phase to omit an affected area or select contradictory actions. | High w/o control | High | Versioned JSON-plus-evidence envelope; schema validation; action uniqueness per blueprint; conflict detection; raw-report retention; partial/incomplete state. | **5** | **Mitigated in Phase 5** (envelope contract) | `evolution-phases-detailed.md:31-35, 763-807, 923-928` |
| A4 | Trigger-query generation or embedding drift produces false-positive/negative blueprint injection. | Med | High | Audit 3–10 generated queries; evaluate top-1/top-4/no-match; tune BM25/vector weights + threshold in Phase 7; retain BM25-only graceful degradation. | 5, 7 | Phase 5 mitigations + Phase 7 tuning | `daemon/services/blueprint_matcher.py:333-405` |
| A5 | Match-once-per-instance persistence injects stale blueprint after corpus changes or worker reused for different domain. | High over long-lived reuse | Med/High | Keep documented fresh-instance/task-affinity invariant; expose version/age in diagnostics; avoid reusing workers across materially different areas; explicit new-instance/rebuild path rather than hidden per-turn churn. | 5, 7 | Phase 5 mitigations + Phase 7 E2E | `daemon/services/context_messages.py:1194-1249` |
| A6 | Blueprint content duplicates system instructions, contains unstable implementation detail, or acts as instruction-injection surface. | Med | High | Enforce declarative 200–500-word content; file-reference verification; system-prompt overlap checks; source/manual policy; escaped context rendering; evidence-based no-op decisions. | 5 | Phase 5 mitigations | `agents/blueprinter/rule.md:9-10` |

---

## 2.6 Integration risks

| ID | Risk description | L | I | Mitigation / control | Affected phases | Status | Evidence |
|---|---|---:|---:|---|---|---|---|
| **C-I1** (formerly v1 I1) | Blueprint matching and persistent context assembly disagree about message identity/kind — blueprint injected in live graph but omitted/duplicated/rebuilt incorrectly by checkpoint/API read. | Med | High | Treat `CONTEXT_KIND_BLUEPRINT` + stable block ID as cross-module contract; update persistence/read-path kind allowlists, compaction, `/messages`, synthetic system-message tests; verify first + subsequent turn behavior. | **2 + 7** | **Mitigated in Phase 2** (allowlist fix) + Phase 7 E2E | `daemon/services/context_messages.py:66-107, 1347-1384`; `daemon/persistence.py:630-637` (allowlist omits `blueprint` — **C10** reviewers' finding) |
| I2 | Daily scan, manual `/scan`, post-experience hooks, and API calls enqueue duplicate or conflicting triggers, or use wrong queue/metadata trigger. | Med | High | Centralize smart-trigger decisions; same durable lease/idempotency claim for every entry point (via **admission coordinator**); resolve `system_background_queue` through queue repository; version trigger metadata; test restart/concurrent/manual paths. | 3, 7 | Phase 3 mitigations + Phase 7 verification | `daemon/routers/blueprints.py:303-350` |
| I3 | Frontend polling infers completion from appearance of rows; times out while valid rebuild still queued; refreshes stale signals after 409/error. | Med | Med | Poll returned `job_id` through existing job-status endpoint; bounded backoff; explicit queued/running/succeeded/failed/timeout UI states; cancel polling on destroy/project change; keep manual refresh. | 4, 6 | Phase 6 mitigations | `evolution-phases-detailed.md:954-1013, 1077-1101` |
| **C-I4** (formerly v1 I4) | CRUD router writes bypass trigger embedding, revision capture, and rate limiting because they call `BlueprintRepository` directly. | High until refactored | High | **All** automated + user-mediated writes route through one domain `BlueprintWriteService` with authorization/source context, revision + derived-data transaction semantics, limiter enforcement. API tests assert SIDE EFFECTS, not only response fields. | **1** | **Mitigated in Phase 1** (canonical service) | `daemon/routers/blueprints.py:204-229, 353-400` |
| **C-I5** (formerly v1 I5) | `project_history_add()` cannot reach the pending repository through the current project-store wiring — feature/milestone changes silently never enter the queue. | Med | High | Thread the pending repository through the **project repository/store factory** (leader decision); add integration tests for feature/milestone + negative types; keep hook failure non-fatal but observable. | **3** | **Mitigated in Phase 3** (leader-confirmed factory threading) | `daemon/tools/project_history.py:94-139`; `daemon/repositories/project/repository.py:1248-1288` |
| I6 | Repurposing `/scan` changes the contract expected by external cron or operations tooling. | Med | Med | Keep `/scan` as compatibility surface; document smart result states (`rebuild`, `incremental`, `skip`); apply same guard/idempotency path; log source/decision reason. | 4 | Phase 4 mitigations | `daemon/routers/blueprints.py:303-350` |
| I7 | Skill ownership/name collisions cause `load_skill` to resolve the wrong template or a newer shared row. | Med | High | Blueprinter-owned templates; unique names; explicit load names; verify owner/version at resolution; host-attached IDs; test exact-owner + name-only fallback. | 5 | Phase 5 mitigations | `daemon/services/skill_clone_service.py:186-223` |
| I8 | Project scope is lost in pending hooks, queue messages, worker context, blueprint writes — one project's changes update or inject into another project. | Low/Med | High | Require project ID at every boundary; path-scoped repository queries + ownership checks; project ID as structured job metadata (NOT message text); cross-project integration tests. | 2, 3, 4, 5, 7 | Phase 2–7 mitigations + Phase 7 cross-project tests | `daemon/repositories/blueprint/models.py:50-63` |

---

## Highest-priority risk controls (now distributed across phases 1–7)

The previously monolithic Phase 8 control list is now distributed. **Each control is closed in the phase that needs it:**

| # | Control | Phase | Risks closed | Status |
|---|---|---|---|---|
| 1 | **Durable build lease + admission coordinator** (atomic claim, queue reconciliation) | **3** | C-O1, O2, I2, D4 | Closed in Phase 3 |
| 2 | **Exact pending-batch acknowledgement** (`processed_at` soft-delete, claim/ack state machine) | **2** | C-D2, D1, O4 | Closed in Phase 2 |
| 3 | **One canonical blueprint write path** (`BlueprintWriteService`: revision + trigger replacement + rate limit + manual-content guard) | **1** | C-D3, C-D6, C-O3, C-I4, O7 | Closed in Phase 1 |
| 4 | **Compare / stage / publish rebuild semantics** (manual-content protected, soft-disable after publish) | **5** | D5, C-D6, C-A1 | Closed in Phase 5 |
| 5 | **Versioned structured worker reports** + bounded fan-in recovery | **5** | O5, O6, A2, C-A3 | Closed in Phase 5 |
| 6 | **PostgreSQL + SQLite integration tests** including existing-database schema upgrade, API alias/concurrency, and `/messages` context persistence | **7** | M3, M4, C-I1, I3 | Closed in Phase 7 |
| 7 | **Decision-model evaluation + configurable escalation** (`decide_model_tier`) | **5** | C-A1, A4, C-P7 | Closed in Phase 5 |

**Plus three new in-phase controls (NEW):**

| # | Control | Phase | Risks closed |
|---|---|---|---|
| 8 | **G7 auto-dedup** on `(project_id, source_kind, content_hash)` + audit table | **2** | D7 |
| 9 | **`auto_rebuild_enabled=False` feature flag** | **3** | auto-rebuild blast radius |
| 10 | **Embedding fingerprint mismatch detection + background regen** | **2 + 7** | EF-1 |

---

## Crash-during-rebuild recovery scenarios (NEW — for Phase 7 E2E)

| Window | Expected Behavior | Risk Mitigated |
|---|---|---|
| (a) Before claim | Next scan retries; no orphan | C-D2, O1, AC-1 |
| (b) After claim, before enqueue | Lease sweeped to released on next startup | C-O1, AC-1 |
| (c) After enqueue, before scan-side claim | Queue retains JobItem; next startup reconnects lease + job | C-O1 |
| (d) Active rebuild, mid-worker dispatch | Heartbeat expires; lease released; another rebuild claimable; original job retried/swept; **NO double-publish** (compare/stage/publish protects) | C-D2, C-D6, O5 |
| (e) Stage written, before Publish | Rollback to `status="draft"`; published set intact | C-D6 |
| (f) During Publish | Partial transaction; retry detects orphan drafts; either completes or rolls back | C-D6 |
| (g) After Publish, before ack | `processed_at` not yet set → next scan re-claims records → re-processes idempotently (compare protects) | C-D2 |
| (h) After ack | Records have `processed_at`; ignored | (none — terminal good state) |

Tests live in `tests/integration/test_blueprint_crash_recovery.py` (NEW in Phase 7).

---

## References

- `.agents/shared/planning/project-blueprint/blueprinter-evolution.md:9-26, 63-110, 118-236` — locked evolution shape, pending queue, skills, workflows, APIs, daily scan, and seven questions.
- `.agents/shared/planning/project-blueprint/evolution-phases-detailed.md:18-36, 77-95, 126-163` — implementation scope, current mechanisms, and baseline risks/gaps.
- `.agents/shared/planning/project-blueprint/evolution-phases-detailed.md:397-623` — daily scan, guard, API compatibility, and current draft guard recommendation.
- `.agents/shared/planning/project-blueprint/evolution-phases-detailed.md:629-932` — skill seeding, worker skills, fan-out/fan-in, report examples, and prompt risks.
- `.agents/shared/planning/project-blueprint/evolution-phases-detailed.md:936-1165` — frontend endpoints, dual-mode UI, and polling proposal.
- `.agents/shared/planning/project-blueprint/evolution-phases-detailed.md:1169-1357` — high-water scan proposal, E2E coverage, and PG verification requirement.
- `.agents/shared/planning/project-blueprint/plan-overview.md:134-141, 149-185, 201-256, 262-325` — persistent injection, content/revision model, matching and instance-lifetime invariants.
- `.agents/shared/planning/project-blueprint/plan-overview.md:330-425, 446-456, 503-571, 657-711` — injection seam, blueprinter triggers, manual-edit semantics, lifecycle, integrations, and baseline risks.
- `daemon/services/skill_seed_service.py:238-298, 300-369` — manifest discovery, `skills-template/` path, owner-scoped seeding, and version guard.
- `daemon/tools/instance.py:1561-1619`; `daemon/services/skill_clone_service.py:186-223`; `daemon/services/skill_injection_service.py:303-365`; `daemon/services/instance_messaging.py:2031-2044, 2723-2775` — explicit skill dispatch, resolution, and attribution flow.
- `daemon/repositories/skill/models.py:322-401`; `daemon/repositories/skill/skill_bank_repository.py:238-303` — usage attribution and owner/name fallback behavior.
- `daemon/repositories/job_queue/models.py:248-359`; `daemon/repositories/job_queue/repository.py:282-413, 454-553` — persisted queue admission state and atomic idempotent enqueue pattern.
- `daemon/repositories/project/models.py:176-191` — project metadata key uniqueness suitable for a durable lease storage option.
- `daemon/repositories/blueprint/models.py:19-83, 149-205`; `daemon/repositories/blueprint/repository.py:41-100, 143-222` — current blueprint, soft-delete, revision, trigger, and matcher-candidate behavior.
- `daemon/services/blueprint_matcher.py:287-405`; `daemon/services/context_messages.py:1347-1384`; `daemon/persistence.py:630-637` — matching edge cases and persistent context integration. **`persistence.py:630-637` allowlist currently omits `blueprint`** — C10 reviewer finding, fixed in Phase 2.
- `daemon/routers/blueprints.py:204-300, 303-421`; `daemon/tools/blueprint.py:240-353` — current API/tool write paths and `/initialize`/`/scan` behavior.
- `daemon/services/maintenance.py:68-140, 300-340`; `daemon/manager.py:1700-1765` — idle-gated maintenance registration and queue checks.
- `frontend/src/app/services/blueprint.service.ts:178-207`; `frontend/src/app/pages/blueprint/blueprint.component.ts:442-475`; `frontend/src/app/pages/blueprint/blueprint.component.html:12-18` — current frontend `/initialize` consumer.
- `tests/unit/test_blueprint_api.py:1-24, 348-362` — current API test scope and revision setup gap.
- `docs/agent-prompt-writing-guide.md:136-148, 162-196, 210-214` — skill ownership/versioning, fan-in escape valves, and prompt-edit checklist.

---

## Revision History

- **v1 (initial):** 7 open questions resolved + 56-entry risk table.
- **v2 (this revision, 2026-08-03 21:25Z):**
  - All 7 questions restated with **leader decisions** baked into the final answer.
  - Risks carried from v1 with new in-phase owners renamed with `C-` prefix (C-D2, C-D3, C-D6, C-O1, C-O3, C-A1, C-A3, C-P7, C-I1, C-I4, C-I5).
  - **Three new risks added:** CW-1 (BlueprintWriteService complexity), AC-1 (admission coordinator crash recovery), EF-1 (embedding-fingerprint staleness).
  - **C10 finding** (context-kind allowlist missing in `persistence.py:630-637`) explicitly recorded as a mitigation in Phase 2 + verification in Phase 7.
  - Phase 8 highest-priority controls table redrawn — distributed across phases 1–7 instead of a single late phase.
  - Crash-during-rebuild recovery scenarios (NEW) listed with mitigations.
  - Three new in-phase controls added (auto-dedup, feature flag, embedding fingerprint).

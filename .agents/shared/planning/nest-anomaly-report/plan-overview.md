# Plan Overview: nest-anomaly-report — `report_anomaly` write tool + clustering + board sink

Date: 2026-11-04T22:30Z
Author: plan-creation worker (nest-anomaly-report lane, board item wi-2d691ed2)
Status: Draft — phases 3-6 authored by sibling workers; this file is the master plan of record
Claim: `wt.claim.nest-anomaly-report` (worktree `/home/nea/ensemble-src-wt-nest-anomaly-report`, base `1f6898ae2`)

## Objective

Any agent in the nest can report an operational anomaly through one inline tool —
`report_anomaly(category, description, severity, suspected_cause)` — and the report is
**actionable** (auto-harvested evidence attached: instance tree, job/mission/dispatch chain,
timestamps, recent tool-error excerpt, registry snapshot ref, wedge-capture cross-link),
**safe** (secret-scrubbed before persistence), **deduplicated** (N agents reporting the same
incident collapse into ONE cluster row), **rate-limited** (refusals ledgered as the spam
signal), and **visible** (critical severity surfaces to the BOARD as a server-side row;
anomaly counts by category become central-metrics family F23 with read tools + FE tab in
phases 3-4). Promote is never performed by this lane — staging only; the user ceremony owns it.

**Original symptom, proven in BOTH directions (lane-level gate statement):**

- **BASE (`1f6898ae2`):** an agent that witnesses an incident has NO channel: the tool
  `report_anomaly` does not exist (`grep -r report_anomaly daemon/` = 0 hits), no
  `anomaly_reports`/`anomaly_clusters` tables exist (migration census 119 files, none
  anomaly-shaped), no cluster rows, no F23 family in
  `daemon/services/metrics_query/wiring/`, and no FE incident surface. Failure is silent:
  the only anomaly path is a human reading a transcript.
- **HEAD:** an agent holding the `tools.allow` grant calls `report_anomaly`, receives a
  typed success envelope (`report_id`, `cluster_id`, `cluster_report_count`), the persisted
  row carries scrubbed evidence (excerpt sha256+length discipline, registry summary hash,
  wedge capture id refs), sibling reporters within the sliding window land on the SAME
  cluster row, a critical cluster has exactly ONE board item, and the counts-by-category
  family (F23) plus the read surfaces (privileged `nest-report-read` trio, domain REST route
  `/api/nest-reports/*`, FE tab) are delivered by phases 3-4. Naming follows decisions.md R9
  (the two-layer nest-/anomaly- split).

A gate MUST fail at BASE (tool absent / tables absent / no cluster collapse / no family)
and pass at HEAD for the same script — the both-directions proof is the lane's original
symptom closure.

## Scope

### In Scope

- Phase 1 (this worker): storage models + repository, write tool `report_anomaly`, evidence
  harvest seams, scrub pipeline, `nest-report` category registration (10-step checklist +
  artifact regen + VOCABULARY_VERSION 3→4), two migration files.
- Phase 2 (this worker): cluster service (write-time gap-based assignment, sliding 15-min
  window), rate limits (5/10min minor+major; critical exempt to 20/hour) with ledgered
  refusals, critical→board server-side write via BoardService, 30-day batched retention
  hooked into the existing MaintenanceService seam.
- Master plan of record (this file) + decisions.md ruling SSOT (R1-R8, R5b, F23).
- Phases 3-6 (sibling workers): phases 3-4 (`plan-worker-registry-fe`) = F23 family + `nest-report-read`
  ops-trio + `/api/nest-reports/*` domain route + FE Nest Reports tab; phase 5 = duty prompt + grant
  ceremony (tools.allow, all in-repo meta.json); phase 6 = gates/risk-register/staging.

### Out of Scope

- **Promote / nonce / live symlink / state.json promote fields** — user ceremony owns it;
  this lane is STAGING ONLY per claim ruling.
- **metrics-read tools edit** — the metrics query door (`daemon/routers/metrics.py`) stays
  FROZEN; anomaly reads go through the existing tools (metric-shaped) or a NEW domain route
  (row-shaped) per R8. Never a second metrics query path.
- **notify_user auto-voice** — NEVER called by the anomaly path (doctrine
  `daemon/tools/notify_user.py:32-39`: "board ALWAYS, notify MAY voice"; silence is default).
- **Scrub-gap fixes** — nomask (`system.py:357-422`) and env-inherit (`bash.py:287-302`,
  `proc_tools.py:496`) seams are known-open; disclosed in decisions R6, not fixed here.
- **Vocabulary extension mid-lane** — the canonical category set is frozen for this lane
  (R1); extension = later vocab.py edit + VOCABULARY_VERSION bump.
- **F23 registry live defects (R_i/R_ii)** — census-polarity + capture_active defects are
  live but the registry idiom is UNAFFECTED; disclosed (decisions F23), not fixed.
- ** agents/ prompt-artifact edits** — phase 5 (sibling) owns meta.json; this lane's files
  1-4 never touch `agents/`.

## Phases

| Phase | Name | Objective | Plan file | Owner | Status |
|-------|------|-----------|-----------|-------|--------|
| 1 | Storage + write tool + evidence harvest + scrub + category registration | Agents can call `report_anomaly`; rows persist scrubbed with bounded evidence; category registered through the 10-step checklist incl. artifact regen + VOCABULARY_VERSION 3→4 | `phase1-plan.md` | this worker | planned |
| 2 | Clustering service + rate limits + critical→board + retention | N reporters × same incident → ONE cluster (sliding window, stable ids); spam refused+ledgered pre-harvest; critical clusters carry exactly ONE board item; 30-day batched retention on the existing maintenance seam | `phase2-plan.md` | this worker | planned |
| 3 | Observability backend: F23 family + `nest-report-read` ops-trio + `/api/nest-reports/*` domain route | F23 registered on the central registry (`source_idiom="registry"`, `durability="durable"`, AM-2 allowed_values from the closed vocabulary) with ZERO metrics-tool edit; row-shaped reads via NEW privileged category `nest-report-read` through the anomaly service (never raw engine handles); NEW domain route gated IDENTICALLY to the metrics door (the door's any-of query scope maintenance:read\|metrics:read; 503 AUTH_NOT_CONFIGURED fail-closed) — the metrics door stays FROZEN | `phase3-plan.md` (sibling `plan-worker-registry-fe`) | sibling worker | not started |
| 4 | FE Nest Reports tab on `/metrics` | ONE new tab, two surfaces: F23 metric panels (via the existing central door) + the incident list (clusters + member reports via phase 3's domain route) | `phase4-plan.md` (sibling `plan-worker-registry-fe`) | sibling worker | not started |
| 5 | Grant ceremony: all-agents duty prompt + explicit `tools.allow` + baseline/seal/pin ceremony | Explicit per-agent grant in ONE same-commit pass across ALL in-repo meta.json per prompt-guide §9 (**census correction: 41 observed = 36 base + 5 `[v2]` mirrors**, not the briefed 39/34 — see decisions R4; phase 5 sibling independently observed 41) | `phase5-plan.md` (sibling) | sibling worker | not started |
| 6 | Gates + risk register + STAGING | Tier-1/Tier-2 gates on merged tree; `phase6/risk-register.md`; derive version slot live; `scripts/upgrade/stage.sh` with v-prefixed VERSION; manifest + both provenance sidecars; tag + push per convention. NEVER promote.sh | `phase6-plan.md` + `phase6/risk-register.md` (sibling) | sibling worker | not started |

## Amendments folded (architect round, SOUND-WITH-AMENDMENTS; `architecture-recommendation.md`)

All structural rulings stand (R1-R9, F23, per-domain one-door, two-table clustering,
full-payload posture). The binding amendment set folded into the phase plans:

- **Four security fences**: AM-9 flood fences (refusal-ledger cap + pre-INSERT row caps +
  global accepted/hour + capacity trim), AM-11 scrub hardening (NEW `redact_secret_shapes`
  BEFORE `redact_exc_str`; excerpt FAIL-CLOSED on scrubber-absent; excerpt major+critical
  only — minor skips the transcript read entirely), AM-12 projection/injection pins (list
  excludes `excerpt`; detail `?include_excerpt=true`; FE ≤300-char reveal; no-innerHTML pin),
  AM-13 board ceilings (3/tree/hour, 20/hour global — the architect's own ~480/day harm bound,
  W-6-corrected from 50; note-append beyond; suppression counter).
- **AM-16 (leader-ruled)**: default-ON stands CONDITIONAL on AM-9+11+12+13 landing AND the
  phase-6 gate verifying "the four security AMs are present and pinned"; otherwise default-OFF
  + mandatory morning-report line. Per-writer deny list `ENSEMBLE_NEST_REPORT_DISABLED_AGENTS` lands.
- **AM-4/5/6 clustering mechanics**: fixed-width timestamps + Python-bound cutoffs (the
  phase-2 rate SQL as written ERRORS on PG); ONE transaction + orphan TERMINALIZATION (new
  outcome `unclustered`, never reassignment); retention DECREMENTS `report_count`.
- **AM-14**: 64-slot striped per-key locks replace the process-global lock.
- **AM-8**: transcript seam swaps to the bounded `instance_read.py` pattern (read cap AT the
  seam); honest 5-call harvest ceiling; `boot_epoch` pinned to naive-UTC.
- **AM-17**: the lane lands as NINE ordered green-boundary commits (C1..C9) with a per-commit
  smoke set; the 81-file single ceremony commit REJECTED (phase 5 = C8 duty + C9 grants).
- **AM-3**: four missed VOCABULARY_VERSION pin/prose sites + exposition-baseline re-run;
  phase 5 needs NO artifact regen (the artifact carries zero grant info).
- **Census (folded)**: prompt_versions has ZERO approved+selected rows; `rule` has zero rows
  of any state — no planned duty edit can be shadowed; the (reviewer,workflow)
  pending-approval row is the one latent caveat (user-owned, morning item U-2b).
- **Fold round 5 (fresh approver, APPROVED-WITH-CONDITIONS)**: C1 — AM-11's shape fence now
  covers the JSON key/value secret class (the class it named but did not mask: the optional
  quote between keyword and separator); the pattern masker is the SOLE defence for
  non-STORED secrets, and the phase-6 gate item no longer accepts a fence that leaves that
  class open. C4 — the harvest row rewritten against the REAL schema (Task → JobItem →
  MissionCommission, 3 tables) and AM-8c's honest ceiling re-derived 5 → **~7** off-loop
  calls against the 25-slot strict-admission executor (NR-10 + AM-14 disclosures ride it).
  C5 — the excerpt selection rule is SINGULAR and error-filtered: the last ≤5 ERROR-BEARING
  tool messages, selected from a bounded scan of the turn's last ≤20 messages. C6 —
  "instance tree" = the ANCESTOR chain (parent→root, self excluded); the subtree reading
  was considered and REJECTED. C2 — the board write runs INSIDE the same-key stripe (AM-14
  prose authoritative). C3 — the per-tree cap sheds oldest rows (batched, decrementing
  cluster counts) instead of darkening a tree; a capacity-exhausted counter is added to F23
  (band 67, closed space 425 → 492 — decisions.md is the source of truth, D-5).
- **Fold round 6 (approver iteration 002, DELTA-CONDITIONS)**: D1 — the excerpt predicate is the
  house DUAL form — the house ASSIGNMENT form, with the comparison SEPARATE:
  `msg_type = getattr(msg, "type", None) or getattr(msg, "role", None)` THEN `msg_type == "tool"`;
  the old `role == tool` form could never match a ToolMessage (the house keys on `.type`), so the
  excerpt would have shipped EMPTY while the contract read satisfied — closed by the positive
  selection leg **M-1h** (plant a refusal/error-shaped tool message ⇒ it must land). D2 — the
  cluster rollup is RELATIVE (`report_count = report_count + 1 … RETURNING`), so it composes
  with a concurrent shed decrement under READ COMMITTED (the absolute stale-read form drifted
  ±1 against the invariant the refuse-vs-merge defense rests on). D3 — the global hourly knob
  is **1,500/h**, re-derived against the **36 distinct budget keys** (41 dirs → 36 base ids
  after `[v2]` resolution; 36 × 50/h = 1,800/h steady ceiling ⇒ the knob now binds in steady
  state; it is an operator brake, not a security fence). R-a — the secret keyword family is
  hardened (trailing segment allowed; over-masking is the fail-safe direction). R-b — a cluster
  whose members are all shed is deleted in the same unit-of-work.
- **Fold round 7 (approver iteration 003, FINAL planning fold)**: E1 — the secret value class is
  QUOTE-AWARE (`(?:"[^"]*"|'[^']*'|\S+)`), so a passphrase's tail no longer leaks; the
  planted-secret needle is the FULL value, not the first word. E2 — the predicate carries the
  house ASSIGNMENT form (`msg_type = getattr(msg,"type",None) or getattr(msg,"role",None)` then
  `msg_type == "tool"`); the parenthesised/collapsed form let the agent's OWN prose and the
  SYSTEM PROMPT into the excerpt, breaking the "unverified agent prose" boundary — closed by a
  NEGATIVE selection leg (a HumanMessage/AIMessage carrying "ERROR:" in prose must not land).
  E3 — the widened keyword family excludes the benign token-metering names, so a
  token-exhaustion error keeps its diagnostic value (house redaction doctrine,
  `daemon/_redact.py:8-11`). E4 — an empty RETURNING is an ASSIGNMENT FAILURE → the report row
  is `unclustered` (AM-5b), so "accepted MEANS clustered" holds. E5 — the live hot-path shed
  calls the SAME shared decrement-and-delete helper as the maintenance job. E1 is a closure gap
  in the C1 class (not a new class); E1+E2 must be present before implementation commit C1.

## Coupling Map

|  | P1 | P2 | P3 | P4 | P5 | P6 |
|---|---|---|---|---|---|---|
| **P1** | — | **tight** (models + repository + service module created in P1 are mutated by P2) | **tight** (F23 pulls the cluster aggregate P2 writes; vocabulary in `wiring/vocab.py` P1 registers is P3's allowed_values source) | tight (P4 reads P1 models via the anomaly service; category `nest-report-read` registration repeats P1's checklist) | loose (P5 grants the tool P1 registered; no code shared) | tight (P6 gates P1+P2 surfaces) |
| **P2** | tight | — | tight (cluster columns are P3's metric source) | tight (incident list renders P2 clusters; resolution semantics derived from P2 windows) | loose | tight |
| **P3** | tight | tight | — | tight (FE tab consumes the F23 family + the phase-3 domain route) | independent | tight |
| **P4** | tight | tight | loose | — | loose (grant ceremony covers nest-report-read too) | tight |
| **P5** | loose | loose | independent | loose | — | loose |
| **P6** | tight | tight | tight | tight | loose | — |

Shared substrate: `daemon/repositories/anomaly/` (models + repository) and
`daemon/services/anomaly_service.py` are CREATED in phase 1 and EXTENDED in phase 2;
phases 3-4 consume them read-only. The canonical vocabulary lands once in
`daemon/services/metrics_query/wiring/vocab.py` (P1) and is consumed by P3 (allowed_values)
and P4 (FE labeling) — the AM-2 contract makes drift between them a hard error.

## Risk Summary (top 5 — full register owned by phase6/risk-register.md)

1. **Scrub-gap leakage** — nomask/env-inherit scrub gaps are open; a harvested excerpt could
   carry unmasked env-shaped secrets. Mitigation: redact-first pipeline + excerpt caps +
   sha256/length discipline (R6); gaps disclosed, never fixed silently in this lane.
2. **Category honesty failure** — free-text categories would be `open`-cardinality labels;
   AM-2 (`daemon/services/metrics_query/wiring/registry.py:102-110`) hard-raises on label
   keys without allowed_values, so a vocab shortcut breaks F23 registration loudly.
   Mitigation: closed vocabulary + normalization + `category_raw` preservation (R1).
3. **Cluster identity instability** — retroactive re-clustering would invalidate stored
   cluster_ids and board links. Mitigation: write-time assignment, id assigned ONCE,
   resolution DERIVED (no status mutation); stability pins in phase2-plan.md.
4. **Board namespace pollution** — a critical storm could flood the global board.
   Mitigation: ONE item per cluster + note-append on repeat criticals + critical rate cap
   20/hour/agent (R3/R5b); single-row-per-cluster pin in phase2-plan.md.
5. **F23 registry live defects (R_i/R_ii)** — census polarity inverted vs contract and
   capture_active unwired are LIVE defects in the registry idiom family; the anomaly family
   uses the registry idiom and must not inherit silent darkness. Mitigation: disclosed in
   decisions F23, owned by the routed metrics lanes, verification must assert the F23
   family actually publishes (not just registers).

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | An agent with the grant can report an anomaly end-to-end | Scratch-DB boot + tool invocation through the real factory path; parse success envelope | envelope carries `report_id` + `cluster_id`; row present with outcome=accepted |
| 2 | Every persisted TEXT field is scrubbed | Plant a store secret whose value appears in a harvested excerpt; SELECT the row | planted value ABSENT from every column; `excerpt_sha256`/`excerpt_length` match stored excerpt |
| 3 | N reporters → ONE cluster | 3 sibling agents (same tree root) report same canonical category within W=15min | exactly 1 `anomaly_clusters` row; `report_count=3`; all 3 report rows carry the same `cluster_id` |
| 4 | Rate limits refuse pre-harvest and ledger honestly | 6th minor report within 10min from one agent | typed refusal `rate-limited`; ledger row outcome=rate_limited with harvest columns NULL; cluster counts unchanged |
| 5 | Critical severity surfaces to the board exactly once per cluster | 2 criticals on one cluster | exactly 1 board item; 1 appended note; `board_item_id` set on the cluster; `notify_user` never invoked |
| 6 | Migrations land cleanly | Census + fresh-DB apply | 121 files / 121 distinct prefixes (> 20261031_120002); fresh SQLite applies both; PG create_all creates both tables |
| 7 | Registration is complete and honest | Drift test + artifact drift check + vocabulary pin | `test_frozen_tool_name_discovery` green; tool-categories artifact regenerated (no F-1 trap); VOCABULARY_VERSION=4 same change-set |
| 8 | Both-directions symptom proof | G1-style script at adjudicated base `1f6898ae2` vs HEAD | FAIL at BASE (tool/tables/cluster/family absent), PASS at HEAD |

## Research Insights (verified anchors — read in this worktree, 2026-11-04)

- Ancestor chain: `daemon/repositories/instance/repository.py:1217 get_ancestor_ids`
  (bounded `_MAX_TRAVERSAL_DEPTH = 256` at `:50`); one-query agent labels
  `get_agent_ids_for` `:1237` (returns `dict[str, str | None]`).
- Dispatch chain: `daemon/services/job_queue_service.py:82 DISPATCH_CHAIN_KEY`,
  daemon-minted at enqueue `:1461-1463`; read helper `daemon/services/job_processor.py:57`.
- Transcript read: `daemon/persistence.py:627 get_instance_messages`; bounded-read
  precedents `_TASK_CONTEXT_MAX_CHARS = 4000` (`daemon/tools/instance.py:116`) and notify
  `max_chars=2000` (`daemon/config.py`, NotifyUserConfig `:3085+`).
- Registry snapshot: provider discipline per `daemon/tools/metrics_read.py:44-49`
  (tool reads `manager.metrics_query_provider`, never constructs/scrapes); factory pattern
  `create_metrics_read_tools` `:1332`.
- Wedge captures: `daemon/repositories/wedge_captures/repository.py:94 list_recent(limit=50)`;
  boot epoch `daemon/services/boot_epoch.py:70 get_boot_epoch()`.
- Scrub: `scrub_text` `daemon/services/secret_persistence_scrub.py:615`;
  `scrub_enqueued_body(message, *, actor=)` `:1250`; `get_process_scrubber` `:263`;
  `redact_exc_str` `daemon/_redact.py:74-98` (never raises; limit applied AFTER redaction).
- Ledger prior art: `daemon/repositories/notify_audit/models.py` — `_utc_now_iso` `:67`,
  `NotifyAudit` `:100`, Index triplets `idx_notify_audit_*` `:135-146`,
  `default_factory=_utc_now_iso` `:158`.
- Board: `daemon/models/board.py` BoardItem `:208` / BoardNote `:301` / BoardItemTag `:350`;
  `BoardService.create_item` `daemon/services/board_service.py:136`
  (`created_by_instance` stamped by caller, tags deduped in-service);
  `update_item(note=...)` `:219` appends the note in the SAME transaction (`:297`).
- Registration: checklist `daemon/tools/upgrade_tools.py:150-184`; empty-allow universe
  `daemon/tools/instance.py:551-561` (privileged categories skipped); the CRITICAL
  list-append precedent `:6624-6663` (must precede create_help_tool /
  scan_tools_for_full_docs / _apply_tool_filter); cold-boot warm import + stub
  `daemon/loader.py:519-528` and `:560-566` (NOTE: loader lives at `daemon/loader.py`,
  not `daemon/tools/loader.py`).
- Off-loop seam: `daemon/services/db_offload.py` `async def offload(fn, *args, **kwargs)`
  `:386` (the v0.27.14 event-loop discipline).
- Retention seam: `MaintenanceService.register` `daemon/services/maintenance.py:141`;
  job registrations cluster at `daemon/manager.py:4377-4525`.
- Migrations: census 119 files, latest `20261031_120002_create_project_skill_usage_records.sql`;
  runner SQLite-only; index naming `idx_<table>_<cols>` per notify_audit.
- Metrics contract: AM-2 raise `daemon/services/metrics_query/wiring/registry.py:102-110`;
  `WEDGE_REASONS` home `daemon/services/metrics_query/wiring/vocab.py:66` (`__all__` `:318`);
  `VOCABULARY_VERSION: int = 3` `daemon/checkpoint_metrics.py:218`; artifact regen recipe
  `tests/unit/tools/test_tool_category_artifact.py:17-21`; door scope + 503 posture
  `daemon/routers/metrics.py:106-137`; F23 slot FREE (no F23 registration anywhere in
  `daemon/services/metrics_query/`; adjudication map covers F20-F22 only).

## Open Questions

None blocking. One factual correction carried in decisions R4: the in-repo meta.json census
is **41 (36 base + 5 `[v2]` mirrors)**, not 39/34 as briefed — verified
`find agents -maxdepth 2 -name meta.json | wc -l` = 41; the ruling's semantics (explicit
same-commit `tools.allow` ceremony in ALL in-repo meta.json, owned by phase 5) are
unaffected. Phase 5 sibling should plan against the live census, not the briefed number.

VOCABULARY_VERSION phrasing (fold round 2, resolved): per-bump **3→4 (C2)** then **4→5 (C5)** —
two commits, never one jump; lane-total 3→5; end state **59 categories @ v5**. The architect's
"3→5" is the lane total; the plan's per-bump numbers are correct and confirmed.

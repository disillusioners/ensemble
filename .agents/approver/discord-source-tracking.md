# Discord Source Adapter — Approval Tracking

## Iteration 001 — REJECTED (2026-08-11T21:26:34Z)

### Dispatch
- 3 parallel workers, plan-approval skill, partitioned by document group
- w-foundation: plan-overview + requirements + technical-analysis
- w-phases-1-2: phase1 + phase2
- w-phases-3-4: phase3 + phase4

### Worker Verdicts
| Worker | Verdict | Blocking Issues |
|--------|---------|-----------------|
| approve-worker-foundation | APPROVED | 0 |
| approve-worker-phases-1-2 | REJECTED | 1 |
| approve-worker-phases-3-4 | REJECTED | 6 |

### Aggregated Blocking Issues

1. **[Phases 1-2] FR-17 image attachment handling omitted** — phase2-plan.md Task 6 (_normalize_incoming) never populates IncomingMessage.images. plan-overview.md line 36 marks it in-scope; requirements.md FR-17 / AC-8.2 define acceptance. No phase implements it. Phase 4 tests reference attachment behavior with no production code backing.

2. **[Phases 3-4] Channel-lock capacity contradiction** — Phase 3 Task 2 says MAX_CHANNEL_LOCKS=1000; acceptance criterion says "LRU evicts at 200 entries"; risks and exit criteria imply 1000. No single authoritative value.

3. **[Phases 3-4] Circuit-breaker 429 classification undefined** — Risk mitigation says 429s must NOT count as failures; Task 3 records success/failure after send. No concrete exception/status classification. Phase 4 has no test for 429-exclusion.

4. **[Phases 3-4] Thread lifecycle termination underspecified** — Eviction "terminate instance" has no manager/lifecycle API defined, no failure handling, no synchronization, no archived-thread routing representation. No rollback or error-path acceptance criteria.

5. **[Phases 3-4] Concurrent eviction vs active-lock safety undefined** — LRU OrderedDict + guard, but no spec on whether _get_channel_lock() holds guard through acquisition, or how eviction avoids removing held locks. Phase 4 has no concurrent eviction/active-lock test.

6. **[Phases 3-4] Gateway health latency threshold semantics incomplete** — Phase 3 requires "reasonable latency" with 5000ms threshold; Phase 4 only lists ready/stopped/error/disconnected tests. No tests for above/below threshold or missing latency.

7. **[Phases 3-4] Shutdown idempotency & resource release contract undefined** — stop() calls thread_manager.shutdown() and "release all resources" but no contract for already-stopped adapter, pending waiters, lock state, TTL task, or failed termination. Phase 4 has no idempotency/partial-failure tests.

### Notes (non-blocking, from all workers)
- Foundation: cross-document open-question status inconsistency (overview says resolved; technical-analysis lists 3 OPEN)
- Foundation: DM send channel-opening step implicit, not explicit
- Foundation: Phase 1 GO/NO-GO gate needs concrete validation procedure
- Foundation: Rate limiter concrete concurrency limit unspecified
- Foundation: 12 Gaps & Ambiguities items not formally triaged
- Foundation: NFR-14 startup time <10s may be aggressive
- Foundation: DB migration question not explicitly addressed
- Phases 1-2: FR-15 (allowed_guilds) config keys present but no filtering task implemented (dead config)
- Phases 1-2: FR-20 (reply_to_id mapping to Discord message_reference) not addressed
- Phases 1-2: FR-21 (bot allowlist) only self-filter implemented; cross-bot filtering missing
- Phases 3-4: _rejected_count metric declared but no rejection path (Semaphore.acquire waits, doesn't reject)
- Phases 3-4: ThreadInstance clock source undefined (time.time vs time.monotonic)
- Phases 3-4: Archive detection owner unclear (Task 5 vs Task 6)
- Phases 3-4: Phase 4 lacks traceability table from Phase 3 acceptance items to Phase 4 tests

## Iteration 002 — REJECTED (2026-08-11T21:38:09Z)

### Dispatch
- 3 parallel workers, plan-approval skill, partitioned by document group
- w-design: plan-overview + requirements
- w-foundation: technical-analysis + phase1 + phase2
- w-advanced: phase3 + phase4

### Worker Verdicts
| Worker | Verdict | Blocking Issues |
|--------|---------|-----------------|
| approve-worker-design | REJECTED | 3 |
| approve-worker-foundation | REJECTED | 2 |
| approve-worker-advanced | REJECTED | 1 |

### Iteration 001 Issues — RESOLVED (confirmed by all workers)
All 7 prior blocking issues addressed: FR-17 image handling, MAX_CHANNEL_LOCKS=1000 consistency, circuit-breaker 429 classification table, thread lifecycle shutdown contract, concurrent eviction safety contract, health latency threshold tests, shutdown idempotency 7-step contract.

### Aggregated Blocking Issues (deduplicated: 6 → 4)

1. **[Consistency] Config-key naming drift** — requirements.md config table (lines 553-566) uses `agent`, `allowed_guild_ids`, `require_mention`, `channel_mention_config`, `ignore_bot_messages`, `strip_llm_artifact_tags`; phase1-plan.md Task 3 (line 47) uses `default_agent`, `channel_require_mention`, `allowed_guilds`. No single canonical config-key schema. Config will not load as documented. *(Worker: design)*

2. **[Completeness] `_split_message` 5-tier boundary spec incomplete** — requirements.md FR-22 (lines 162-176) specifies 5-tier preference: paragraph → newline → sentence → word → hard cut. Phase 2 Task 9 (line 31) only specifies paragraph → newline → hard-split. Sentence and word boundary tiers missing. AC-22.3 (word boundaries where possible) and AC-22.4 (chunk-invariant property) cannot be satisfied as written. *(Worker: design)*

3. **[Consistency] Undocumented config keys: `allowed_channels` + `allowed_bot_ids`** — Phase 2 Task 5 (line 27) references `self._allowed_channels` and `self._allowed_bot_ids` for filtering. Neither key is extracted in Phase 1 Task 3 `__init__` (line 47), neither appears in Phase 1 Configuration Model (lines 57-66), neither is documented in requirements.md config schema. Phase 2 will fail at runtime on undefined attributes. *(Workers: design #3 + foundation #1 + foundation #2 — merged)*

4. **[Completeness] `_ttl_task` orphaned reference** — Phase 3 `stop()` (lines 237-244) cancels `self._ttl_task`; Phase 4 Task 9(l) (line 26) tests `_ttl_task` cleanup. No task in ANY phase (1-4) creates `self._ttl_task = asyncio.create_task(self._periodic_eviction_loop())`. Phase 3 Task 5 implements `evict_expired()` but nothing wires it to a periodic background loop. No Slack precedent (Slack evicts inline on access). Implementer must guess creation step or remove the reference — neither option specified. *(Worker: advanced)*

### Notes (non-blocking, from all workers)
- design: NFR-10 token redaction has no explicit Phase 4 test
- design: FR-18 (reload) tagged "Could", no phase implements — should be explicitly deferred
- design: FR-19 (per-channel mention config) implied in-scope but unimplemented — clarify scope
- design: registry line reference drift (~423 vs actual 417-423)
- foundation: Phase 1 Task 4 timeout branch should cancel `_client_task` explicitly
- foundation: MESSAGE_CONTENT intent verification mechanism unspecified
- foundation: `max_message_length` and `strip_llm_artifact_tags` config keys unreferenced in phases
- advanced: Phase 2 Task 9 AC tension with stub locks (documented but could be clearer)
- advanced: `_guild_locks` lazy creation has benign TOCTOU race
- advanced: FR-18 reload() missing explicit "deferred" declaration

### Next: Iteration 003 (FINAL — max iterations)

## Iteration 003 — REJECTED / ESCALATED (2026-08-11T21:58:00Z)

### Dispatch
- 3 parallel workers, plan-approval skill, partitioned by document layer
- w-specs: plan-overview + requirements (946 lines)
- w-design: technical-analysis + phase1 + phase2 (565 lines)
- w-impl: phase3 + phase4 (535 lines)

### Worker Verdicts
| Worker | Verdict | Blocking Issues |
|--------|---------|-----------------|
| approve-worker-specs | REJECTED | 12 |
| approve-worker-design | REJECTED | 10 |
| approve-worker-impl | REJECTED | 1 |

### Iteration 002 Issues — Status
- Config-key naming drift: claim of resolution, but fresh workers found drift STILL present in new variants (require_mention vs channel_require_mention vs _channel_require_mention; _allowed_guild_ids vs _allowed_guilds). RECURRING.
- _split_message boundary tiers: tier count addressed, but phase4 test coverage does not verify priority ordering end-to-end (w-impl blocking). PARTIALLY RESOLVED.
- Undocumented config keys (allowed_channels/allowed_bot_ids): not re-flagged as blocking this iteration, but broader config-contract gaps surfaced (w-specs #2, #3). PARTIALLY RESOLVED.
- _ttl_task orphaned reference: Phase 3 Task 8 now wires the loop; no longer blocking. RESOLVED. (w-impl raised _ttl_task=None init + race as Notes.)

### Aggregated Blocking Issues (deduplicated: 23 raw → 20 unique)

**Config & Naming (cross-cutting, flagged by 2 workers)**
1. **[Consistency] Config-key naming drift persists** — requirements.md config table uses `require_mention`, `allowed_guild_ids`; technical-analysis resolves `channel_require_mention`; phase1 captures `require_mention`/`_require_mention`/`_allowed_guild_ids`; phase2 references `_channel_require_mention`/`_allowed_guilds`. No single canonical schema. *(w-specs #2 + w-design #1 — merged)*
2. **[Consistency/Safety] Config-key validation & precedence undefined** — `max_message_length` has no enforced range (values >2000 apparently legal); description allows "truncation" while FR-22 forbids it; precedence between `ignore_bot_messages` and `allowed_bot_ids` undefined; `strip_llm_artifact_tags=False` conflicts with FR-13. *(w-specs #3)*
3. **[Consistency/Security] Snowflake int/str type mismatch** — phase1 documents allowlists as `list[str]`, phase2 compares integer Discord IDs against them; configured guild/channel/bot allowlists reject valid entries. `ignore_bot_messages` captured in phase1 but phase2 hard-codes bot skip. *(w-design #2)*
4. **[Consistency] v1 scope vs requirements contradiction** — plan-overview defers reload/per-channel-mention, requirements still state "shall support"; `channel_mention_config` documented as active override but overview says silently ignored in v1. *(w-specs #1)*

**Routing & Registry**
5. **[Completeness/Feasibility] Registry Discord metadata integration unplanned** — phase2 requires `mapping.mapping_metadata["discord"]["channel_id"]/thread_id/message_id`, but phase1 only plans adapter-construction branch; no Discord branch in registry `_handle_message` (existing code at registry.py:777-800 extracts metadata only for Slack). New mappings lack fields phase2 requires. *(w-design #3)*
6. **[Feasibility/Correctness] Reply-reference construction invalid** — phase2 uses `discord.MessageReference(message_id=...)` without required `channel_id` kwarg → TypeError in discord.py 2.4 before any reply sent. *(w-design #6)*
7. **[Feasibility/Reliability] Cache-only channel lookup** — only `client.get_channel()` (cache) specified; returns None for uncached targets; no HTTP fetch fallback for guild channels/threads/DM recreation. *(w-design #9)*
8. **[Completeness] Reply-to-bot activation missing** — `_is_bot_mentioned()` checks DMs/mentions but not `message.message_reference`; server replies to bot's previous message without fresh mention are incorrectly discarded. *(w-design #7)*

**Rate Limiting & Circuit Breaking**
9. **[Feasibility/Consistency] Rate-limit design conflict unresolved** — overview selects discord.py buckets + thin semaphore, rejects adapter bucket logic; C-2 mandates custom dual-bucket limiter; AC-3.4 assigns retry_after to adapter; Gap #10 treats strategy as unresolved. Materially different implementations. *(w-specs #4)*
10. **[Safety] Circuit-breaker scope & failure classification undefined** — not specified whether 429s, 403/404 target errors, auth failures, timeouts, 5xx count toward same circuit; permanent per-message errors (deleted/unauthorized channel) could open source-wide circuit and stop unrelated sends. *(w-specs #10)*

**Gateway & Task Lifecycle**
11. **[Safety/Lifecycle] Gateway task lifecycle gaps** — `start()` waits only on `_ready_event`; invalid token / disallowed intent fails before `on_ready` (PrivilegedIntentsRequired) → 30s timeout instead of clear auth error; no `finally` path cancels/awaits `_client_task` on cancellation; `stop()` awaits potentially failed task without guaranteeing cleanup + STOPPED transition. Orphaned Gateway tasks possible. *(w-design #4)*
12. **[Safety/Reliability] Inbound handler task lifecycle + ordering undefined** — phase2 awaits `_emit_message()` inside `on_message` (discord.py schedules handlers as independent tasks); no registry of in-flight handler tasks, no shutdown drain/cancellation, no admission gate after stopping. Inbound per-channel ordering (NFR-7) has no mechanism — planned locks are outbound only. *(w-specs #9 + w-design #5 — merged)*

**Message Splitting & Outbound Safety**
13. **[Completeness/Consistency] FR-22 not scheduled + chunk semantics gaps** — Must-level FR-22 not in overview scope/file responsibilities/phases/success criteria; omits fenced-code-block preservation, empty-after-sanitization behavior, reply-reference across chunks, partial-failure semantics (earlier chunks succeeded, later failed → duplicate-send risk given deferred outbound idempotency). *(w-specs #5)*
14. **[Safety] Outbound AllowedMentions policy missing** — outbound markdown passed through without AllowedMentions; agent-generated @everyone/@here/role/user mentions or reply pings could notify large audiences. *(w-specs #8 — blocking; w-design corroborated as Note)*
15. **[Safety/Completeness] Split progress & separator ownership undefined** — phase1 documents `max_message_length` but phase1 Task 3 doesn't capture it; phase2 hard-codes 2000; splitter doesn't define delimiter ownership at \n\n/\n/space or require strictly positive split point → empty/non-progressing chunks possible. *(w-design #10)*
16. **[Completeness] Phase 4 split-message priority test gap** — acceptance claims "priority chain verified end-to-end" but listed test cases verify each tier in isolation only; no test where multiple boundary types coexist in trailing window (paragraph must beat word when both fit). *(w-impl #1)*

**Archived Threads**
17. **[Feasibility/Consistency] Archived-thread fallback mechanism undefined** — overview promises TTL/LRU tracking but doesn't state archive-state source (events/REST/send-failure); no TTL/capacity defaults; overview calls parent routing "configurable" while RD-3 makes it mandatory. *(w-specs #11)*

**Plan Coherence & Acceptance**
18. **[Consistency/Feasibility] Open-questions status contradiction** — overview claims all resolved; requirements lists 12 gaps needing caller input (incl. selected library + rate-limit strategy); AC leaves start()-idempotency and attachment-only-message behavior unresolved. *(w-specs #6)*
19. **[Consistency/Feasibility] Phase 2/3 boundary conflict** — phase2 acceptance requires circuit-breaker decision + per-channel lock behavior; coupling section says both implemented in phase3, instructs phase2 to use no-op stubs. Phase2 cannot satisfy its own AC under strict sequential model. *(w-design #8)*
20. **[Consistency/Feasibility] Success criteria unachievable + invalid ID examples** — overview requires "zero 429" from 100-message burst while requirements expects 429s (zero unhandled only); "no data loss" after disconnect vs AC-10.1 resume-only guarantee. ID examples use 6-15 digit components vs regex requiring 17-19; reply_to_id conflated with external-ID scheme. *(w-specs #7 + #12 — merged)*

### Notes (non-blocking, selected highlights)
- Rate-limiter design snippet incomplete (acquire/release methods not shown)
- _ttl_task=None init implicit; race window between RUNNING transition and task creation
- EVICTION_INTERVAL_SECONDS / max_channel_locks / max_threads_per_guild configurable read-site under-specified
- send() pipeline ordering (_split_message position vs circuit-breaker/lock) not pinned
- Phase 4 line-tier test missing; FR-22 deterministic property untested; _thread_manager None path untested; archive routing fallback untested
- discord.py pin says "pinned" but constraint is >=2.4.0; py-cord fallback only verifies namespace import
- NFR-10 token redaction has thorough test (w-impl positive)

### Status: ESCALATED (3rd rejection — max iterations reached)

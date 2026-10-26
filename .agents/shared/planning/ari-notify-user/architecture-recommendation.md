# Architecture Recommendation — `notify_user` (Ari voice)

**Commission:** privileged ari-ONLY tool `notify_user` — deliberate, opt-in, ONE message from a non-user-originated turn (self-alarm wake, scheduled fire) to the SESSION'S BOUND REGISTERED CHAT SOURCE. Signature takes the MESSAGE, never a destination.
**Worktree / evidence base:** `/home/nea/ensemble-src-wt-notify-user` @ `feature/ari-notify-user` (`d4ecdbd3d` == origin/latest). Every mechanical claim below cites `file:line` from THIS tree.
**Analysis method:** 3 dispatched skill analysts (data-flow `a4d42873`, security `a5a80631`, resilience `e011c32a`), independently of the planner's in-flight files; load-bearing claims re-verified by the architect (noted where corrected); planner research files (`research-a/b/c`) reconciled in §9.
**Status:** COMPLETE — rulings D1–D12 binding.

---

## 0. Rulings at a glance

| # | Ruling | One line |
|---|---|---|
| D1 | Resolution chain | current instance → (fallback walk to root) → `instance_metadata["original_source"]` → parse `source_id:external_user_id` → registry → adapter. NEVER scan the mapping table. |
| D2 | Chat-type gate | `USER_ORIGIN_CHAT_SOURCE_TYPES` = {telegram, slack, discord, whatsapp} (upgrade_journal.py:3371-3375); delivery feasibility additionally proven by registry + adapter status. |
| D3 | Delivery seam | **Direct adapter send** (`adapter.send`), NOT the progressive lane, NOT `dispatch_completed`. |
| D4 | Never-block | `asyncio.wait_for(pre_flight + send, 10.0s)`; every path returns ≤10s. Thread-bridge, fire-and-forget, durable-enqueue all rejected. |
| D5 | Rate limits | N=1 per turn (enforced as ≥60s per-instance interval) + M=6 per rolling hour **per resolved destination**; both from the `notify_audit` ledger, insert-then-count, restart-durable. |
| D6 | Size cap | ≤2000 chars, tool-side, BEFORE resolution/dispatch; message body only; single-chunk policy (one notify = one chat message). |
| D7 | Allow-list | New privileged category **`notify-user`** (#17, 16→17), tool `notify_user`; ari EXACTLY; in-body `agent_id=="ari"` gate; 9 pin sites bumped in ONE commit (SAME-PR rule). |
| D8 | Audit | New table **`notify_audit`**, Tier-2 write-ahead (`accepted` → `dispatched`/`delivery_failed`/`rate_limited`); refusals ARE ledgered; body stored as sha256+length, never cleartext. |
| D9 | Error vocabulary | 10 `NOTIFY_CODE_*` constants, frozen-set validated, anti-differential template; `REFUSED` envelope (pre-flight) vs `FAILED` envelope (dispatch). |
| D10 | Retries | Budget 1, transient-only, 1–2s backoff, inside the 10s bound; NEVER after timeout; NEVER on refusal-class. |
| D11 | Result contract | 3 shapes: success JSON / REFUSED / FAILED, with delivered-vs-unknown-vs-failed explicit. |
| D12 | Doctrine amendment | The "no ping path" doctrine is PINNED in shipped docstrings + sealed ari prompts + one test pin. The tool ships WITH its doctrine-amendment set and pin re-stamp ceremony, same PR. |

---

## 1. Problem — the exact no-delivery decision point (verified)

A turn's response reaches chat **iff** its dispatch source is a colon-suffixed string whose prefix resolves to a registered chat adapter. The dispatch source is `context.message_source`, EXCEPT for the internal-report family, where the pipeline substitutes the session's `original_source`:

- **Decision point 1 (pipeline):** `MessageProcessingPipeline._dispatch_completed` — `dispatch_source = context.message_source` (message_processing_pipeline.py:739); ONLY `internal_report:` / `internal_error_report:` / `internal_agent:job_event:` sources get the `instance_metadata["original_source"]` substitution (:740-767, JQ guard :772-782). Verified verbatim.
- **Decision point 2 (dispatcher):** `if ":" not in source: return` — "Skipping internal source (no routing needed)" at dispatcher.py:531-535 (completed lane, log :534) and :691-695 (progressive lane, log :694). Verified. **Silent — no error channel.**

A self-alarm / scheduled fire enqueues with bare `source="scheduler"` (scheduler.py:2663-2671 direct; :2078-2086 deferred) — it is NOT in the internal family, so `original_source` is never consulted, and the colon-less skip discards the response. There is **no `is_scheduler` branch anywhere on the path**. The session's chat binding survives (write-once, not overwritten — instance_messaging.py:3521-3530) but is never read for a scheduler source. This is the "no ping path" doctrine, implemented implicitly.

Watch-event turns deliver because their wake source is `internal_agent:job_event:{work_id}:{status}` (work_notifier.py:226-242, enqueued :1131-1134) — inside the substitution family, so the final response resolves to the session's chat `original_source` and dispatches.

---

## 2. Session→source binding — what exists

- **The binding datum is not a column.** It is the write-once string `instance_metadata["original_source"]`, shape `f"{source_id}:{external_user_id}"` for chat origins (minted daemon/sources/registry.py:1055-1056). Lazy stamping at instance_messaging.py:3463-3539: first non-`internal_*` message_source wins (:3521-3528); `internal_*` excluded (:3522); **`scheduler` and `api` are NOT excluded** — a fresh scheduler-spawned instance gets `original_source="scheduler"` stamped (direct code-path consequence of :3512-3539; planner research flags it [SPECULATION]-grade — treat as unbound regardless).
- **Children inherit at spawn.** `instance_lifecycle.py:5577-5593` (verified): if the parent has `original_source`, it is merged into the child's metadata at INSERT inside the same WriteGuardSession (logged :2464). Every descendant of a chat session carries the root's chat source from creation. *(Correction of worker A's edge-case (iii) mechanism — see §9.)*
- **Session root:** `instances.parent_id` is the permanent tree (`instance_hierarchy` rows are transient — deleted when a child completes, repository.py:262-272). Existing up-walk primitives: `_resolve_lineage_root` (snapshot_executor.py:447-468, depth-capped 256), `get_tree_ids_permanent` (manager.py:13820-13844). No single canonical helper — reuse one of these, do not inline-loop.
- **Registry:** `source_configs` rows (`source_id` PK, `source_type`, `enabled`, `status` — source/models.py:62-85); `SourceRegistry.get(source_id)` (registry.py:236-246); adapter factory builds ONLY telegram / scheduler / slack / discord — anything else → `None` (registry.py:389-558, :556-558). Mapper identity universe: telegram / webhook / slack / discord (mapper.py:46-55); **webhook has no adapter → not a live delivery target**.
- **v0.26.2 wake-the-caller:** `_bind_schedule_to_caller` (self_alarm.py:874-976) writes an `instance_mappings` row keyed `(source_id, external_user_id=source_id)` → caller instance, metadata `{bound_to_caller, caller_instance_id}`. Fire-time: `get_instance_mapping(source_id, source_id)` → enqueue into the CALLER with `source="scheduler"`, role=user (scheduler.py:2524-2534, :2631-2671). The binding carries the caller INSTANCE id and **zero chat-source identity** — resolving the chat destination requires exactly the `original_source` hop this design adds.
- **v0.27.3 deferred/coalesced delivery** (scheduler.py:1082-1294): busy-caller fires become a durable PENDING delivery (Task + MessageQueue + JobItem), coalesced per generation (`defer_max_count=16`, `defer_max_age_seconds=86400`, scheduler.py:1097-1115). It delivers by **waking a turn** — content mutates through the LLM. Wrong vehicle for verbatim notification (see D4).

---

## 3. Rulings

### D1 — Resolution chain (binding mechanism)

```
tool(mid-turn, current_instance_id from factory closure)
  → read current instance.instance_metadata["original_source"]
  → if absent or not colon-shaped: walk parent_id chain to root (depth-capped), read root's original_source
  → parse "source_id:external_user_id" (source_id regex ^[a-zA-Z0-9_-]{1,64}$ — dispatcher.py:539-542;
    external_user_id ≤256 chars — :544-547)
  → chat-type gate (D2)
  → source_configs row exists + enabled (source/repository.py:57, 135-136)
  → registry.get(source_id) → adapter handle (registry.py:236-246)
  → adapter status RUNNING + circuit breaker CLOSED (adapter.py:1595-1603)
```

- **`original_source` is the ONLY binding anchor.** It is a single write-once string — deterministic. The mapping-table reverse lookup (`get_instance_mapping_by_instance` → `.first()`, source/repository.py:623-631) is **non-deterministic under multi-mapping and is NOT used** — this eliminates the ambiguous-source edge case by construction rather than refusing it.
- Values `"api"`, `"scheduler"`, `"api_resume_fallback"`, anything `internal_*`, and any colon-less string are **unbound** → `notify_no_bound_chat_source`. The spawn-inheritance (§2) makes the common case a single read with no walk; the walk covers legacy children and any spawn path that skipped the merge.
- Calling-instance status (terminal/revived) is irrelevant: `instance_metadata` is permanent and survives revive — no special case.

### D2 — Chat-type gate

Semantic gate: `USER_ORIGIN_CHAT_SOURCE_TYPES = frozenset({"telegram", "slack", "discord", "whatsapp"})` — daemon/tools/upgrade_journal.py:3371-3375 (verified), matched against registry `source_type` VALUES. This is the canonical "human chat channel" set. **Do NOT reuse `ROUTING_ENVELOPE_KEYS`** (daemon/constants.py:649) — it is a per-type metadata-KEYS dict (routing envelope support), a different concern with different members; the upgrade_journal comment explicitly warns "Do NOT dedup the two". *(Worker B suggested the wrong constant; corrected — §9.)*
Delivery feasibility is then proven downstream: the registry factory has no whatsapp/webhook case → such sources fail `registry.get`/adapter construction and refuse with `notify_source_not_registered`. Effective live set today: {telegram, slack, discord}.

### D3 — Delivery seam: direct adapter send

Tool → `manager.source_registry.get(source_id)` (manager accessors :1527/:4926/:13978; factory-handle precedent instance.py:2410-2417) → `adapter.send(OutgoingMessage(external_user_id, content, source_id))` (contract base.py:88-110, :181).

**Why not (A) the progressive `dispatch_message` lane:** a successful progressive send to source S adds S to `_progressive_sent_sources` (dispatcher.py:806); the completed lane then check-and-discards (dispatcher.py:526-528) and **silently suppresses the turn's real final response** (both verified). A mid-turn notify on this seam eats the final delivery — or, worked around, requires surgery on dispatcher bookkeeping that the chart-delivery pins (tests/test_chart_image_delivery_e2e.py) seal. Fatal.
**Why not (B) `dispatch_completed`:** final-response-shaped — runs post-turn only (pipeline :499; deferred final instance_messaging.py:5239-5248); a deliberate mid-turn send is the wrong lifecycle position, and its failures are swallowed (message_processing_pipeline.py:795-800).
**Why (C) wins:** (1) `adapter.send` returns **bool** — the only seam where the agent can learn the outcome (requirement: failures surface); dispatcher lanes log-and-continue (dispatcher.py:787-790). (2) Zero interaction with `_progressive_sent_sources` — the turn's own final delivery is untouched. (3) The adapter-internal protections are exactly the Discord-consistent caps the commission requires: 2000-char 5-tier split (adapter.py:1280-1344, applied :1761), per-channel LRU locks (adapter.py:268-271, :789-824, MAX_CHANNEL_LOCKS=1000), circuit breaker 5-failures/60s (adapter.py:261-262, circuit_breaker.py:16-110), send semaphore cap 5 (resilience.py:12-13).
**What (C) does NOT inherit — and why that is acceptable:** the dispatcher per-user ordering lock (dispatcher.py:628-631, :810-837) — both paths converge on the adapter per-channel lock (acquired at send, adapter.py:1776), so chunk-level serialization is preserved; cross-message ordering vs a concurrently-dispatched final response may interleave, immaterial for a single-chunk deliberate notify. Marker extraction (dispatcher.py:570-615) — notify messages are ari-authored plain text, no attachments/images, no marker machinery needed; a stray HTML comment renders literally and harmlessly.

### D4 — Never-block-the-turn (concurrency proof)

Tool bodies run on the main loop inside the LangGraph tools node (graph.py:12855; ToolNode delegation long_tool_nudge.py:904 — **no timeout wrapper**), bounded today only by the 120-min graph timeout (task_processor.py:1847-1849). The adapter's per-channel lock waits are UNBOUNDED (adapter.py:1672/:1776) — a naive await is a genuine turn-hang vector.

**Ruling: `asyncio.wait_for(pre_flight + adapter.send, timeout=10.0s)`.** Every path returns in ≤10s; the turn finalizes regardless. Bound derivation (house constants): lane-6 bridge hop-step budget 8.0s (report_delivery_recovery.py:585, 608); Discord REST pre-flight 15s / gateway-ready 30s (discord/constants.py:58/:21); a 2KB chunk through a healthy adapter completes ≪8s; 10s covers one lock+semaphore wait + one send and stays far below the 900s long-tool advisory threshold (config.py:4526-4536) — no nudge noise.

Rejected options: **fire-and-forget** (`asyncio.create_task`) — no delivery confirmation, lost on restart; **thread/executor bridge** — the lane-6 `run_coroutine_threadsafe(...).result(8.0)` pattern (report_delivery_recovery.py:53, 78, 1075-1076) exists for worker threads bridging ONTO the main loop; a tool already ON the loop would deadlock; **durable enqueue** (v0.27.3 deferred lane) — delivers by waking an LLM turn (content mutates, minutes late): that is "wake the user with a turn", not "send this text".

On `TimeoutError`: the POST may have landed → **at-most-once with a disclosed delivery-unknown tail**; never blind-retry. If the whole turn is cancelled mid-send, `graph_task.cancel()` semantics are unchanged (cancel propagates; placeholder ToolMessage pairing synthesizes, graph.py:334-464).

### D5 — Rate limits (concrete bounds + counter location)

- **N = 1 per turn**, enforced as a **≥60s minimum interval per calling instance** — strict turn identity is not tool-visible (factories receive `current_instance_id` + `agent_id` only, instance.py:2410-2417); the interval is the enforceable proxy and turns are serialized per instance (one active task per instance). Rationale: the user language is "sends ONE message"; >1/turn is a bulk-send primitive.
- **M = 6 per rolling hour, keyed by RESOLVED DESTINATION** (`target_hash`), not per session — multiple parallel ari sessions notifying the same user channel share the cap (threat-aligned: the destination is the spam surface). Rationale: self-alarm floor is 1800s/label (self_alarm_service.py:77, enforced :550) → legitimate reminder traffic is a handful/hour; worst-case exfil bandwidth ≈ 6 × 2000 chars ≈ 12KB/h — small and auditable.
- **Counter location: the `notify_audit` ledger itself** (instance_ops D-16 budget precedent — `audit.count_committed_terminates` → ledgered refusal, instance_ops.py:595-634). Mechanism: INSERT the row (`outcome='accepted'`) FIRST, then COUNT qualifying rows in the window INCLUDING the own row; over-limit → UPDATE own row to `rate_limited`, never dispatch. Insert-then-count serializes the decision in the DB (immune to await-point interleaving) and is **restart-durable** (no post-restart burst window; no in-memory state). Rejected: in-memory dict (reset on restart, invisible to ops), runtime KV (no tool-gating precedent, second write surface).

### D6 — Size cap

`len(message) > 2000` → `notify_size_exceeded`, rejected in TOOL input validation BEFORE any binding resolution or dispatch. The adapter always SPLITS rather than fails (adapter.py:1761 + :1298) — without a tool-side cap, one notify becomes a multi-message burst under the shared channel lock (reads as breakage). Discord's formatter runs before the split and can expand content (adapter.py:1622-1627) — a ≤2000 body may still split post-format; this is bounded, sequential, and still ONE dispatch/ONE audit row/ONE budget unit. Single-chunk policy: **one notify = one chat message**. `DISCORD_MAX_MESSAGE_LENGTH` (discord/constants.py:35) is the bound to reuse; ~1900 with formatter headroom is an acceptable tighter variant (implementer's choice, documented). Message body param only; binding/metadata excluded.

### D7 — Ari-only allow-list (category + registration)

- Category **`notify-user`** — entry **#17** in `PRIVILEGED_TOOL_CATEGORIES` (16 today, `_tool_registry.py:338`; hyphenated wrapper convention: self-alarm/view-write/board-link/wedge-capture). Tool name **`notify_user`** (snake_case: self_alarm/board_link).
- Three-layer gate, mirroring self-alarm exactly: (1) privileged category → default-deny strip for every agent without an explicit allow (instance.py:6693-6730; empty-allow deny :393); (2) `agents/ari/meta.json` `tools.allow += "notify-user"` — **ari EXACTLY** (the only agent file carrying the ari-tools privileged entries; v0.26.2 ruling posture, verified by tester RESULTS 2026-10-21); (3) in-body runtime gate `agent_id != "ari"` → typed refusal (self_alarm precedent :116, :680-685) — mandatory because **a literal tool name in an allow list bypasses the category gate** (documented `_tool_registry.py:453-455`, instance.py:2398-2400).
- Registration touches: `daemon/tools/notify_user.py` + `@register_tool_category("notify-user")` ABOVE `@tool(...)` (decorator order load-bearing, self_alarm.py:491-492, pinned test_self_alarm_registration.py:174); `PRIVILEGED_TOOL_CATEGORIES` entry; `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` + `CATEGORY_MODULES["notify-user"] = "daemon.tools.notify_user"` (`_tool_registry.py:23+`, `:1240+`, `:939+`); factory extend in `create_instance_tools` BEFORE help/doc-scan/filter (precedent instance.py:6044-6047; "decorator-only registration is SILENTLY invisible" :6093, :6139).
- **Pin ceremony (SAME-PR rule, `_tool_registry.py:315-337`):** ALL pin sites bumped in ONE commit, 16→17 — exact-set: test_upgrade_registration.py:152, test_attestation_registration.py:189, test_secrets_registration.py:226, test_maintenancer_spawn_resolves_tools.py:366, test_service_tool_flag_off_byte_identical.py:832, test_board_registration.py:194, test_self_alarm_registration.py:202, test_instance_ops_registration.py:188; count: test_board_registration.py:205, test_instance_ops_registration.py:191, test_self_alarm_registration.py:205, test_wedge_capture_registration.py:166. New `tests/unit/test_notify_user_registration.py` in the instance_ops shape (AST discovery, module-path, extends-order, exact-set, count 17, ari-only census over all `agents/*/meta.json`).

### D8 — Audit shape: new `notify_audit` table

**No table to reuse** (instance_ops D-2 examined and rejected reuse of `event`; secrets_audit/mcp_gateway_audit are domain-specific; board/self-alarm have none). **Tier-2 write-ahead shape** (instance_ops precedent: pending→committed; crash leaves durable 'pending' = honest unknown, instance_ops.py:970-977) — the dispatch is a service call with no shared transaction, so Tier-1 same-transaction sink does not apply.

Lifecycle: `accepted` (pre-dispatch INSERT — also the rate-gate row) → `dispatched` / `delivery_failed` / `rate_limited` (UPDATE own row). **Deliberate divergence from instance_ops** (whose input-validation refusals write no row): for notify, the refusal IS the security signal (spam/exfil attempts) — refusals are ledgered with `refusal_reason`.

Columns (mcp_gateway_audit shape, 20261022_000001): `id TEXT PK`, `created_at TEXT NOT NULL`, `agent_id TEXT NOT NULL`, `instance_id TEXT NOT NULL`, `action TEXT NOT NULL DEFAULT 'notify_user'`, `source_id TEXT NOT NULL`, `target_hash TEXT` (sha256 of external_user_id — raw destinations not stored), `outcome TEXT NOT NULL`, `refusal_reason TEXT`, `error_code TEXT`, `body_length INTEGER NOT NULL`, `body_sha256 TEXT NOT NULL`, `detail TEXT`; indexes `(agent_id, created_at)` + `(instance_id, created_at)` + `(target_hash, created_at)` (the D5 hourly count query). **Body as hash+length, never cleartext** — rate counting + tamper-evidence without a second copy of message content.

Migration discipline: ONE file `daemon/migrations/versions/<unique-YYYYMMDD_HHMMSS>_create_notify_audit.sql` — unique timestamp prefix (duplicate-prefix defect class 20261018_000001; loud pre-apply guard `DuplicateMigrationVersionError`, runner.py:29-33, :364-420); `CREATE TABLE IF NOT EXISTS` (create_all runs first on boot — bare CREATE degrades to silent no-op; instance_ops migration :74-84); all string columns TEXT on both drivers; no inline semicolons in comments. **Rollback-safety standing rule:** additive CREATE TABLE; the previous tree never references `notify_audit` (verify `git grep notify_audit` = 0 hits at parent) → inert on rollback → `rollback_safe=true source=explicit` — the v0.26.0 board-tables / v0.27.2 mcp_gateway_audit reasoning class, recorded verbatim at stage time.

### D9 — Typed error vocabulary (house style)

`NOTIFY_CODE_*` module constants + frozen frozenset validated at construction (secrets precedent: single source of truth, mistyped code fails loud — secret_service.py:2169-2218); anti-differential template (identical wording, only the code distinguishes — :2157-2162); envelope never carries message content. Pre-flight refusals use the instance_ops REFUSED envelope `Error: notify_user REFUSED — reason=<code>: <message>` (instance_ops.py:199-201); dispatch failures use a mirrored FAILED envelope. Tools NEVER raise (instance_ops.py:85-87).

| Code | Condition | Detection cite |
|---|---|---|
| `notify_no_bound_chat_source` | original_source absent / colon-less (`api`, `scheduler`, `internal_*`) on instance AND root | instance_messaging.py:3521-3539 |
| `notify_source_not_chat_type` | resolved source_type ∉ USER_ORIGIN_CHAT_SOURCE_TYPES | upgrade_journal.py:3371-3375 |
| `notify_source_not_registered` | `registry.get(source_id)` → None | registry.py:236-246 (dispatcher silently skips this today, :704-710 — the tool must NOT) |
| `notify_source_disabled` | `SourceConfig.enabled=False` or adapter status ≠ RUNNING | source/repository.py:57,135-136; adapter.py:1595-1598 |
| `notify_source_circuit_open` | breaker OPEN, recovery_timeout (60s) not elapsed | circuit_breaker.py:40-52, :110 |
| `notify_rate_limited` | interval (<60s since last per instance) or hourly (>6/destination) — detail distinguishes | D5 |
| `notify_size_exceeded` | body > 2000 chars | D6 |
| `notify_dispatch_timeout` | 10s wait_for elapsed; delivery UNKNOWN; never retried | D4 |
| `notify_dispatch_failed` | adapter.send → False, definite failure, after the single retry | adapter.py:1798-1817 |
| `notify_internal_error` | unexpected exception; log an id, never raise | wedge_capture.py:25-27 |

*(Aggregation note: worker C's draft `notify_rate_limited`-as-semaphore-saturation is collapsed — semaphore saturation is a SOFT signal that manifests as `notify_dispatch_timeout`; the code is reserved for the D5 ledger gates.)*

### D10 — Retry budget

**1 retry, transient-only, 1–2s backoff, inside the 10s total bound.** Retry ONLY definite-failure transients (adapter False on transport-class error; transient resolve failure). NEVER after `notify_dispatch_timeout` (ambiguous POST tail → duplicate risk). NEVER on refusal-class outcomes (typed errors are for the agent to act on). The tool must NOT duplicate seam protections — discord.py handles 429/Retry-After and dynamic buckets internally (resilience.py:5-13); the breaker self-recovers (60s); 4xx are excluded from breaker counts (adapter.py:1558-1575). One outer retry is the correct complement.

### D11 — Tool result contract

Three shapes (self_alarm/instance_ops hybrid; self_alarm success shape :1255-1284):
1. **Success:** `{"ok": true, "delivered": true, "source_id": "<resolved>", "latency_ms": N}`.
2. **Pre-flight refusal:** `Error: notify_user REFUSED — reason=<code>: <human message with actionable hint>` (self_alarm teaches the computed next action — e.g. rate-limit refusals state when the next send is legal).
3. **Dispatch failure:** `Error: notify_user FAILED — reason=<code>: <message>` — the timeout variant MUST say "delivery status UNKNOWN — the message may or may not have arrived; do not re-send blindly". The delivered/unknown/failed distinction is explicit so Ari can decide on follow-up.

**Double-send disclosure (docs, not code):** on `internal_report`-shaped wakes the turn's final response ALSO auto-delivers when `original_source` exists (pipeline :726-794) — notify_user is then a mid-turn ADDITION, and Ari should expect the final response too. On scheduler/self-alarm wakes (the target case) there is NO auto final delivery — notify_user is the only channel.

### D12 — Doctrine amendment set (in scope, same PR as the tool)

The "no ping path" doctrine is not prose — it is **pinned in three layers** (planner research C census, verified):
1. **Shipped docstrings** (ship in the binary via tool_help/args_schema/_full_doc_): self_alarm.py:52-53 (`board-never-ping` obligation), :104-108 (CATEGORY_DOC "there is NO chat / Discord / Slack ping"), :567-578 (_full_doc_ "no such path exists" family).
2. **Sealed ari prompt files:** tools_note.md:758-764 (THE canonical statement — "I NEVER promise the user a chat/Discord/Slack ping… no such path exists", :764), :709 (cross-link), workflow.md:320-325, :334-335 (patrol "report to the user only if it matters" — AMEND, don't delete).
3. **Binding test pin:** test_self_alarm_registration.py:461-469 asserts the doctrine phrases verbatim ("The board is the user-visible surface", "NO ping", "What is NOT promised", "Slack", "interruption").

Building `notify_user` falsifies "no such path exists". The amendment set: reword D1-D3 docstrings to "the board is the guaranteed surface; a deliberate `notify_user` ping is available as an opt-in" (phrase pins updated in the same commit); amend tools_note.md/workflow.md; author the `## Notify-User` tools_note.md section (convention: `## Self-Alarm` at tools_note.md:713). **Pin ceremonies, same commit:** regenerate `agents/.prompt-artifacts.sha256` (161-row manifest; ari rows 26-29) via `scripts/protection/generate_prompt_artifact_baseline.py`; `agents/ari/workflow.md` is in the 35-file `SEALED_SHA_BASELINES` tripwire (tests/test_chart_image_delivery_e2e.py:1421) → re-pin that ONE row with dated attribution ("Only this ONE row changed; the other 34 keep their values" — house pattern :1402-1411); tools_note.md is manifest-only (not in the 35-file tripwire).

---

## 4. Edge-case census

| # | Edge | Detection (cited) | Outcome |
|---|---|---|---|
| 1 | Root origin is HTTP API (`original_source="api"`, colon-less) | messages.py:691; parse fails | `notify_no_bound_chat_source` |
| 2 | Scheduler-only session (`"scheduler"` stamped or absent) | instance_messaging.py:3512-3539 | `notify_no_bound_chat_source` |
| 3 | Caller is a spawned CHILD mid-turn | own original_source (inherited, lifecycle:5577-5593); fallback root walk | resolves to root's chat source; if root unbound → `notify_no_bound_chat_source` |
| 4 | Bound adapter disabled (enabled=False / status≠RUNNING) | source/repository.py:57,135-136; adapter.py:1595-1598 | `notify_source_disabled` |
| 5 | Circuit open | circuit_breaker.py:40-52 | `notify_source_circuit_open` |
| 6 | Source deleted/unregistered | registry.get → None (registry.py:236-246) | `notify_source_not_registered` |
| 7 | Multiple chat sources bound | structurally impossible via the original_source anchor (single write-once string; mapping scan not used) | n/a by design (`.first()` anti-pattern avoided, source/repository.py:630) |
| 8 | Terminal-then-revived session | instance_metadata permanent; revive reuses checkpoint | works, no special case |
| 9 | Body >2000 chars | tool-side validation | `notify_size_exceeded` |
| 10 | Rate: interval / hourly | D5 ledger count | `notify_rate_limited` (+ when next send is legal) |
| 11 | Send exceeds 10s | wait_for | `notify_dispatch_timeout`, delivery UNKNOWN |
| 12 | Adapter definite failure (post-retry) | send → False | `notify_dispatch_failed` |
| 13 | Unexpected exception | — | `notify_internal_error` (log id, never raise) |

---

## 5. Delivery-seam trade-off matrix (5 axes)

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| A: Progressive `dispatch_message` lane | High (must manage `_progressive_sent_sources` interaction or suppress the final response) | High (rides existing lane) | Low (couples to dispatcher bookkeeping sealed by chart-delivery pins; both-seams hook contract) | **High — verified silent final-response suppression** (dispatcher.py:806 vs :526-528); failures swallowed (:787-790) | Med | **Reject** |
| B: `dispatch_completed` path | Med | High | Med | **High — wrong lifecycle (post-turn only)**; double-invocation races the progressive set; failures swallowed (pipeline :795-800) | Med | **Reject** |
| **C: Direct adapter send** | **Low-Med** (tool-side pre-flight + gates; no dispatcher surgery) | Med (bypasses dispatcher per-user lock; converges on adapter per-channel lock; semaphore 5; N/M bounds demand anyway) | **High** (no dispatcher coupling; split/breaker/status live inside `adapter.send` and are inherited) | **Low-Med** (loses ordering lock + marker extraction — both immaterial for single-chunk ari-authored text; bool return = failures surface; 10s bound) | **Low** | **✅ RECOMMENDED** |

**Verdict:** C wins on Risk (the dominant axis — A and B carry *verified* silent-failure hazards; C's losses are provably immaterial for this use case) and on the failures-surface requirement (C is the only seam that returns an outcome). Confidence: **High**. Flip condition: evidence that adapter-level send outside the dispatcher loses a needed protection beyond ordering/markers (e.g. reply-thread semantics coupled to the dispatcher lock), or a product decision that notify SHOULD suppress the final response (then A's hazard becomes a feature — but it would also break language-check-deferred finals).

---

## 6. Trust boundary & residual risk

- **Arbitrary destinations are structurally impossible:** no destination parameter exists; resolution is server-side from durable session metadata; chat-type gate + registry + status + breaker all re-verified per call. Spoofing (non-ari caller): category default-deny + in-body agent gate (closes the literal-name bypass class). Repudiation: full ledger incl. refusals. DoS: size cap + hourly cap + semaphore + 10s bound.
- **🟡 RESIDUAL (accepted, disclosed):** prompt injection in alarm/schedule content can steer notify_user into an exfil channel — the design cannot distinguish a legitimate notification from injected content. Mitigations: ari-only, N=1/turn, M=6/h, source-bound to the user's OWN registered chat, 2000-char cap, body-hash forensics. The audit trail is **detection, not prevention**. Carry as an explicit disclosure in the tool doc and plan.
- **🟡 Sessions never contacted via chat have no legal target** (by construction — instance_messaging.py:3509-3517 treats missing original_source as expected). Correct refusal; the tool docstring must teach it (board remains the fallback surface).

## 7. Config surface

New pydantic-settings section `NotifyConfig` (env prefix `ENSEMBLE_NOTIFY_`, auto-tracked by tests' `_TRACKED_ENV_PREFIXES`, config.py:2724-2727; self_alarm precedent: knobs in SchedulingConfig, config.py:2716-2778): `max_per_hour=6`, `min_interval_seconds=60`, `timeout_seconds=10.0`, `max_chars=2000`. Safe defaults; operator-tunable; no DDL.

## 8. Implementation checklist (for the dev lane)

1. `daemon/tools/notify_user.py`: factory `create_notify_user_tools(...)` (current_instance_id, agent_id, source_registry, instance_repository, audit repo handles); `NOTIFY_AUTHORIZED_AGENT = "ari"`; `NOTIFY_CODE_*` + frozen-set validation; decorator order; `_full_doc_` teaching the no-bound-source case + double-send semantics.
2. Registration per D7 (entry #17 + DYNAMIC/KNOWN/CATEGORY_MODULES + ari meta.json + extend-before-filter) + ALL pin bumps 16→17 in ONE commit + new registration test file.
3. Resolution chain per D1 (reuse `_resolve_lineage_root`-style walker, depth-capped; treat colon-less/`scheduler`/`api`/`internal_*` as unbound).
4. Pre-flight set (D9 order) → ledger INSERT-then-count gates (D5) → `wait_for(send, 10s)` (D4) → single transient retry (D10) → result contract (D11).
5. `notify_audit` SQLModel table + migration per D8 (+ rollback-safety adjudication recorded at stage time).
6. Doctrine amendment set + pin re-stamp ceremony per D12 (same PR).
7. Test surface: registration pins; resolution-chain unit cases (edge census 1-13); rate-gate insert-then-count (incl. restart-durability sim); timeout path (delivery-unknown envelope, no retry); ari-only gate (literal-name bypass negative control — test_self_alarm_literal_allow_side_effect_pins.py pattern); doctrine phrase-pin updates.

## 9. Amendments to plan (planner reconciliation)

`plan-overview.md` / `decisions.md` had NOT appeared in the directory at writing time (only `research-a-binding-delivery.md`, `research-b-precedent-patterns.md`, `research-c-doctrine-caps-tests.md` — all read and reconciled). The planner should fold:

- **Adopted from planner research:** the doctrine census + sealed-baseline/re-stamp mechanics (→ D12); spawn-time `original_source` inheritance (lifecycle:5577-5593 — corrects worker A; folded into D1/edge-3); root-walk primitives (`_resolve_lineage_root` snapshot_executor.py:447-468); the "hierarchy rows are transient, parent_id is permanent" rule (repository.py:262-272); the tool-level size-cap rationale ("one notify = one message", research C §C3); registration touch-list + pin-site census (research B §B2); config-knob precedent (`_TRACKED_ENV_PREFIXES`); ari meta.json wiring (research B §B3).
- **Corrections to my workers, ruled here:** worker A's child-binding mechanism (children DO inherit original_source at spawn — the walk is fallback, not primary); worker B's chat-type constant (ROUTING_ENVELOPE_KEYS → **USER_ORIGIN_CHAT_SOURCE_TYPES**); worker B's per-session hourly key → **per-destination** (`target_hash`); worker B's 15s timeout → **10s** (worker C's house-constant derivation); worker C's semaphore-saturation error code → collapsed into `notify_dispatch_timeout` (soft signal).
- **Planner research vs this doc:** research A §A4.5 marks the fresh-instance `original_source="scheduler"` stamping [SPECULATION] — treated here as a direct code-path consequence with a defensive rule (unbound) either way. No contradictions otherwise; research C's amendment-not-delete guidance for workflow.md:334-335 is adopted verbatim in D12.

## 10. Adjacent defects discovered (recorded; out of this commission's scope)

1. 🟡 `original_source="scheduler"` stamping on fresh scheduler-spawned instances (instance_messaging.py:3512-3539 lacks a scheduler guard) — poisons the datum; notify treats it as unbound defensively. Future commission: exclude `scheduler` from stamping.
2. 🟡 Dispatcher silent-skip family: colon-less skip logs at DEBUG with no error channel (dispatcher.py:534/:694); adapter-False swallowed (:787-790); registry-miss silently skipped (:704-710). notify_user deliberately avoids these seams; a typed-logging commission would benefit all callers.
3. 🟢 `get_instance_mapping_by_instance` returns `.first()` (source/repository.py:623-631) — non-deterministic under multi-mapping; avoided by design here (D1).

## 11. Open questions (for the leader/user)

1. **Hourly bound M=6** is calibrated from the self-alarm 1800s floor, not measured traffic. If the user wants looser/tighter (e.g. 3 or 12), it is one `NotifyConfig` default — no structural change. *Default stands unless overridden.*
2. **Formatter headroom (2000 vs ~1900):** post-format expansion can still split at the adapter. Strict single-message guarantee would use ~1900; 2000 reuses the Discord constant verbatim. Ruled 2000 (bound reuse, disclosed residual); implementer may tighten.
3. **`whatsapp` in the chat-type set:** semantically in `USER_ORIGIN_CHAT_SOURCE_TYPES`, but the registry factory has no whatsapp adapter (registry.py:556-558) — such sources refuse at `notify_source_not_registered` today. No action needed; noted so the refusal isn't misread as a bug.

---

*Evidence basis: workers a4d42873 (data-flow-design), a5a80631 (security-design), e011c32a (resilience-design) — all skill-confirmed; architect re-verification of the four load-bearing claims (pipeline predicate, dispatcher skips, `_progressive_sent_sources`, spawn inheritance, chat-type constants) recorded inline; planner research a/b/c reconciled in §9.*

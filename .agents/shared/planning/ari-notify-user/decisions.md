# decisions.md — `notify_user` (Ari voice) — frozen design decisions (FOLD)

**Fold provenance:** design review COMPLETE — APPROVE-WITH-ADVISORIES, conditional on this fold. **D1–D12 and the three leader adjudications are frozen and non-re-litigable.** This file is a PURE FOLD of `architecture-recommendation.md` (the D1–D12 source) + the reviewer's mechanical corrections (C3, W1–W11) + green advisories. **No new decisions.**

**Fold 2 (2026-10-25):** approver iteration-001 **REJECTED** — 2 blockers + 12 notes, ALL fold-level (`.agents/approver/ari-notify-user-tracking.md`); worker disposition FOLD (do not re-plan). Leader rulings **B1/B2 + N1–N12 are folded VERBATIM in §6** and carry the same supersede authority as §5. No re-derivation.

**Base drift (W5/W7):** the architecture doc's header cites `d4ecdbd3d == origin/latest`; the worktree is now REBASED to tip **`7af1d2632d`** (cu tool category removed; privileged-category count 16→15; `agents/ari/meta.json` + `tools_note.md` drifted ~+15 lines). Where line numbers below are stale on tip, cite by **quoted phrase + "as of 7af1d2632d"**. The corrections in §5 **SUPERSEDE** conflicting verbatim text in §1–§2.

**Sources:** `architecture-recommendation.md` §0/§3/§9 (verbatim below); reviewer fold findings (§5); `research-a-binding-delivery.md`, `research-b-precedent-patterns.md`, `research-c-doctrine-caps-tests.md` (evidence base, reconciled in §4).

---

## 1. Rulings at a glance (verbatim — architecture §0)

| # | Ruling | One line |
|---|---|---|
| D1 | Resolution chain | current instance → (fallback walk to root) → `instance_metadata["original_source"]` → parse `source_id:external_user_id` → registry → adapter. NEVER scan the mapping table. |
| D2 | Chat-type gate | `USER_ORIGIN_CHAT_SOURCE_TYPES` = {telegram, slack, discord, whatsapp} (upgrade_journal.py:3371-3375); delivery feasibility additionally proven by registry + adapter status. |
| D3 | Delivery seam | **Direct adapter send** (`adapter.send`), NOT the progressive lane, NOT `dispatch_completed`. |
| D4 | Never-block | `asyncio.wait_for(pre_flight + send, 10.0s)`; every path returns ≤10s. Thread-bridge, fire-and-forget, durable-enqueue all rejected. |
| D5 | Rate limits | N=1 per turn (enforced as ≥60s per-instance interval) + M=6 per rolling hour **per resolved destination**; both from the `notify_audit` ledger, insert-then-count, restart-durable. |
| D6 | Size cap | ≤2000 chars, tool-side, BEFORE resolution/dispatch; message body only; single-chunk policy (one notify = one chat message). |
| D7 | Allow-list | New privileged category **`notify-user`** (#17, 16→17), tool `notify_user`; ari EXACTLY; in-body `agent_id=="ari"` gate; 9 pin sites bumped in ONE commit (SAME-PR rule). *(superseded by W5/W6 — tip truth: 15 categories, ceremony 15→16, 12 assertion sites / 9 files)* |
| D8 | Audit | New table **`notify_audit`**, Tier-2 write-ahead (`accepted` → `dispatched`/`delivery_failed`/`rate_limited`); refusals ARE ledgered; body stored as sha256+length, never cleartext. |
| D9 | Error vocabulary | 10 `NOTIFY_CODE_*` constants, frozen-set validated, anti-differential template; `REFUSED` envelope (pre-flight) vs `FAILED` envelope (dispatch). |
| D10 | Retries | Budget 1, transient-only, 1–2s backoff, inside the 10s bound; NEVER after timeout; NEVER on refusal-class. |
| D11 | Result contract | 3 shapes: success JSON / REFUSED / FAILED, with delivered-vs-unknown-vs-failed explicit. |
| D12 | Doctrine amendment | The "no ping path" doctrine is PINNED in shipped docstrings + sealed ari prompts + one test pin. The tool ships WITH its doctrine-amendment set and pin re-stamp ceremony, same PR. |

---

## 2. D1–D12 (verbatim — architecture §3)

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
- **Counter location: the `notify_audit` ledger itself** (instance_ops D-16 budget precedent — `audit.count_committed_terminates` → ledgered refusal, instance_ops.py:595-634). Mechanism: INSERT the row (`outcome='accepted'`) FIRST, then COUNT qualifying rows in the window INCLUDING the own row; over-limit → UPDATE own row to `rate_limited`, never dispatch. Insert-then-count serializes the decision in the DB (immune to await-point interleaving) and is **restart-durable** (no post-restart burst window; no in-memory state). *(B2 fold-2 stamp: "immune to await-point interleaving" is superseded-by-lock — true within the process BECAUSE of the per-destination-key asyncio.Lock; the DB serialization is the durable restart/recovery backstop.)* Rejected: in-memory dict (reset on restart, invisible to ops), runtime KV (no tool-gating precedent, second write surface).

### D6 — Size cap

`len(message) > 2000` → `notify_size_exceeded`, rejected in TOOL input validation BEFORE any binding resolution or dispatch. The adapter always SPLITS rather than fails (adapter.py:1761 + :1298) — without a tool-side cap, one notify becomes a multi-message burst under the shared channel lock (reads as breakage). Discord's formatter runs before the split and can expand content (adapter.py:1622-1627) — a ≤2000 body may still split post-format; this is bounded, sequential, and still ONE dispatch/ONE audit row/ONE budget unit. Single-chunk policy: **one notify = one chat message**. `DISCORD_MAX_MESSAGE_LENGTH` (discord/constants.py:35) is the bound to reuse; ~1900 with formatter headroom is an acceptable tighter variant (implementer's choice, documented). Message body param only; binding/metadata excluded.

### D7 — Ari-only allow-list (category + registration)

- Category **`notify-user`** — entry **#17** in `PRIVILEGED_TOOL_CATEGORIES` (16 today, `_tool_registry.py:338`; hyphenated wrapper convention: self-alarm/view-write/board-link/wedge-capture). Tool name **`notify_user`** (snake_case: self_alarm/board_link).
- Three-layer gate, mirroring self-alarm exactly: (1) privileged category → default-deny strip for every agent without an explicit allow (instance.py:6693-6730; empty-allow deny :393); (2) `agents/ari/meta.json` `tools.allow += "notify-user"` — **ari EXACTLY** (the only agent file carrying the ari-tools privileged entries; v0.26.2 ruling posture, verified by tester RESULTS 2026-10-21); (3) in-body runtime gate `agent_id != "ari"` → typed refusal (self_alarm precedent :116, :680-685) — mandatory because **a literal tool name in an allow list bypasses the category gate** (documented `_tool_registry.py:453-455`, instance.py:2398-2400).
- Registration touches: `daemon/tools/notify_user.py` + `@register_tool_category("notify-user")` ABOVE `@tool(...)` (decorator order load-bearing, self_alarm.py:491-492, pinned test_self_alarm_registration.py:174); `PRIVILEGED_TOOL_CATEGORIES` entry; `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` + `CATEGORY_MODULES["notify-user"] = "daemon.tools.notify_user"` (`_tool_registry.py:23+`, `:1240+`, `:939+`); factory extend in `create_instance_tools` BEFORE help/doc-scan/filter (precedent instance.py:6044-6047; "decorator-only registration is SILENTLY invisible" :6093, :6139).
- **Pin ceremony (SAME-PR rule, `_tool_registry.py:315-337`):** ALL pin sites bumped in ONE commit, 16→17 *(superseded by W5/W6 — tip truth: 15 categories, ceremony 15→16, 12 assertion sites / 9 files)* — exact-set: test_upgrade_registration.py:152, test_attestation_registration.py:189, test_secrets_registration.py:226, test_maintenancer_spawn_resolves_tools.py:366, test_service_tool_flag_off_byte_identical.py:832, test_board_registration.py:194, test_self_alarm_registration.py:202, test_instance_ops_registration.py:188; count: test_board_registration.py:205, test_instance_ops_registration.py:191, test_self_alarm_registration.py:205, test_wedge_capture_registration.py:166. New `tests/unit/test_notify_user_registration.py` in the instance_ops shape (AST discovery, module-path, extends-order, exact-set, count 17, ari-only census over all `agents/*/meta.json`).

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

## 3. Leader adjudications (FROZEN — resolve architecture §11 open questions 1–3)

1. **M = 6** — default tunable via `NotifyConfig`, calibrated to the 1800s self-alarm floor. *(Resolves §11-1: the M=6 default stands; loosening/tightening is one config default, no structural change.)*
2. **2000-char single-chunk** — with the adapter split absorbing formatter headroom; residual disclosed. *(Resolves §11-2: the 2000 bound reusing the Discord constant stands; the post-format split residual is disclosed; implementer may tighten to ~1900 only as a documented choice.)*
3. **whatsapp → typed refusal `notify_source_not_registered`.** *(Resolves §11-3: semantically in `USER_ORIGIN_CHAT_SOURCE_TYPES` but the registry factory has no whatsapp adapter — such sources refuse at the registry gate today; the refusal is correct behavior, not a bug.)*

---

## 4. §9 amendment lists (verbatim — architecture §9)

- **Adopted from planner research:** the doctrine census + sealed-baseline/re-stamp mechanics (→ D12); spawn-time `original_source` inheritance (lifecycle:5577-5593 — corrects worker A; folded into D1/edge-3); root-walk primitives (`_resolve_lineage_root` snapshot_executor.py:447-468); the "hierarchy rows are transient, parent_id is permanent" rule (repository.py:262-272); the tool-level size-cap rationale ("one notify = one message", research C §C3); registration touch-list + pin-site census (research B §B2); config-knob precedent (`_TRACKED_ENV_PREFIXES`); ari meta.json wiring (research B §B3).
- **Corrections to my workers, ruled here:** worker A's child-binding mechanism (children DO inherit original_source at spawn — the walk is fallback, not primary); worker B's chat-type constant (ROUTING_ENVELOPE_KEYS → **USER_ORIGIN_CHAT_SOURCE_TYPES**); worker B's per-session hourly key → **per-destination** (`target_hash`); worker B's 15s timeout → **10s** (worker C's house-constant derivation); worker C's semaphore-saturation error code → collapsed into `notify_dispatch_timeout` (soft signal).
- **Planner research vs this doc:** research A §A4.5 marks the fresh-instance `original_source="scheduler"` stamping [SPECULATION] — treated here as a direct code-path consequence with a defensive rule (unbound) either way. No contradictions otherwise; research C's amendment-not-delete guidance for workflow.md:334-335 is adopted verbatim in D12.

---

## 5. Mechanical corrections (C3 + W1–W11) — **SUPERSEDE conflicting verbatim text in §1–§2**

**C3 — ledger spec (replaces D5's insert-then-count overclaim):**
- Interval gate = **explicit read-before-insert**.
- **COMMIT-BEFORE-COUNT**: the accepted INSERT commits in its own transaction, then the COUNT runs in a fresh statement.
- **COUNT predicate defined:** `rate_limited` rows do **NOT** consume budget (else a refused burst self-DoSes the hour).
- Per-instance turn serialization is **defense-in-depth ONLY** (one ToolNode batch can run two calls).

**W1 — target_hash:** `target_hash = sha256(source_id + ":" + external_user_id)` — source-qualified; a **cross-instance shared-cap test is REQUIRED** (parallel sessions to the same destination share the cap).

**W2 — crash orphans:** crash-orphaned `accepted` rows count toward M and read as false deliveries — resolve via a terminal `delivery_unknown` state + sweep, OR exclude-from-budget; **disclose the choice**.

**W3 — source_id nullability:** `source_id TEXT NOT NULL` collides with pre-resolution refusals (`notify_size_exceeded`, `notify_no_bound_chat_source`) — make nullable OR state post-resolution-only ledgering.

**W4 — ONE 10s bound everywhere:** `wait_for(preflight + send + retry, 10.0)`; backoff computed from remaining budget; the two incompatible alternate statements (D4 vs §8-4 vs D10) are **DELETED** in favor of this one.

**W5 — pin arithmetic:** 16 @ old base → **15 @ tip** (cu removal); the ceremony is **15→16 post-rebase**; the architecture header's "`d4ecdbd3d == origin/latest`" is superseded — base is **`7af1d2632d`**.

**W6 — pin-site census:** §0's "9 pin sites" is superseded by D7's **12** — the fold says 12; the lane re-greps at implementation.

**W7 — doctrine drift:** ari doctrine lines drifted +15 on tip — cite by **quoted phrase + "as of 7af1d2632d"** (e.g. tools_note.md:758-764 @ d4ecdbd3d → ~778-779 @ tip).

**W8 — staging posture:** rebase → re-derive → implement → stage; **NEVER `--allow-stale-stage`**; derivation sha recorded at each step (L1 tip-identity guard discipline).

**W9 — limiter home:** research-b's in-memory limiter divergence is REAL — the **restart-durable (audit-ledger) ruling rationale is the BINDING decision** (D5 as corrected by C3).

**W10 — security-property mutation tests** (each with a mutation leg): gate concurrency; cross-instance shared-cap; budget predicate (`rate_limited` rows do NOT consume budget); ledger completeness + body-never-cleartext.

**W11 — test fences folded:** scratch-PG-only; per-var env scrub (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT` — the `env -u POSTGRES_*` glob is a silent NO-OP); mktemp FAKE_HOME fence (never resolve live-shaped HOME paths); cgroup assertion.

---

## 6. Review fold 2 — approver iteration-001 rulings (B1/B2 + N1–N12) — RULED, folded verbatim

**Source:** `.agents/approver/ari-notify-user-tracking.md` — iteration 001 (2026-10-25): REJECTED, 2 blockers + 12 notes, all fold-level; worker recommendation FOLD (do not re-plan). The leader rulings below are folded **VERBATIM** and supersede conflicting text anywhere in this file.

### B1 — boot-smoke mechanism (RULED)

Scratch PG source_configs row of TYPE TELEGRAM (real adapter code, real registry build) + scratch aiohttp sink server answering the Telegram handshake (getMe) AND delivery (sendMessage) + base-URL redirection WITHOUT production-code change via a scratch PYTHONPATH shim prepended in the transient unit's environment (smallest-seam: an import-hook/sitecustomize-style patch of the adapter's API-base constant — NOT a whole-file shadow copy, which drifts). RUNNING precondition stated explicitly: the smoke must drive the scratch source through its handshake to SourceStatus.RUNNING BEFORE the alarm-shaped turn, else D1 preflight correctly refuses with notify_source_disabled (that refusal path is itself a negative-case assertion in the smoke). Sequence pinned: scratch source RUNNING -> source="scheduler" alarm-shaped wake -> Ari turn on a scripted/mock LLM node that emits the notify_user tool call (no real-LLM) -> tool executes -> sink receives EXACTLY ONE sendMessage with expected payload -> notify_audit rows land (accepted->dispatched) -> teardown clean, cgroup-isolated from the live daemon, scratch PG dropped.

*Approver's verified constraints (tip 7af1d2632d): registry builds only telegram/scheduler/slack/discord (daemon/sources/registry.py:389-558); Telegram's API base is a module constant with NO config override (telegram.py:37, config :120-124); Discord's HTTP base lives inside discord.py (discord/adapter.py:428); no trust_env in daemon/sources/; chat adapters refuse to send unless SourceStatus.RUNNING (telegram.py:435-436, handshake :665); the only scratch-adapter precedent (tests/e2e/mock_source_server.py) is a pytest module-swap the registry cannot build.*

### B2 — rate-gate serialization (RULED — supersedes both texts)

The named primitive is a **PER-DESTINATION-KEY asyncio.Lock** (the daemon is single-process, daemon/__main__.py:526-533) — key = resolved `target_hash` — held across the **ENTIRE gate decision** (read-before-insert -> accepted INSERT committed -> COUNT -> dispatch-eligible check). C3's commit-before-count ledger spec remains as the durable restart/recovery backstop and the cross-process-crash record. C3's "per-instance turn serialization is defense-in-depth only" text STAYS (true of turn serialization) but the lock is the closing primitive. D5's "immune to await-point interleaving" is superseded-by-lock (true within the process BECAUSE of the lock). *(Context: parallel tool calls in one AIMessage are real — daemon/graph.py:71-75 — two concurrent notify_user calls could otherwise both pass the ≥60s gate and the M=6 count.)*

### N1 (RULED) — table-registration seam: REQUIRED Phase-3 item

Import the NotifyAudit model before metadata.create_all (factory.py:72-77 pattern) + the daemon/api.py:991-998 late-import path; a missed import = no notify_audit table on PG = ledger INSERT fails = budget+audit break.

### N2 (RULED) — ledger-write failure is FAIL-CLOSED

No ledger row, no send; typed refusal envelope `notify_audit_unavailable` surfaced to the agent. (Rationale: the ledger is also the budget source; best-effort would bypass rate limits. Differs deliberately from the mcp_gateway_audit best-effort precedent.) Pin + test. *(Extends the D9 vocabulary — 11th code.)*

### N3 (RULED) — ONLY accepted rows consume M

Post-resolution, pre-dispatch `accepted` rows ONLY. ALL refusal rows (`rate_limited`, `size_exceeded`, `no_bound_chat_source`, `source_disabled`, `audit_unavailable`, `disabled`) are ledgered as security signal but NEVER consume budget. Extends C3's rate_limited rule to every refusal class.

### N4 (RULED — closes W2) — crash orphans

Crash-orphaned accepted rows DO count toward M (conservative — under-send biased, never over-send; the rolling hour bounds impact) AND a lightweight sweep marks accepted rows stale beyond a threshold (no terminal transition) as terminal `delivery_unknown` for audit truthfulness — they read as unknown, not false deliveries. Disclosed in plan §6 residuals.

### N5 — stale citation fix

original_source stamping on tip is **instance_messaging.py:3755-3772** (not :3512-3539); extend the tip-relative citation rule to code seams generally.

### N6 — superseded arithmetic stamped INLINE

Stamped in D7 and the §1 glance row above: "(superseded by W5/W6 — tip truth: 15 categories, ceremony 15→16, 12 assertion sites / 9 files)". Do not delete, stamp. Architecture references carry the same stamp policy.

### N7 (RULED) — category key is EXACTLY "notify-user"

Matches D7; research-b's "notify" is WRONG — one-line note so the wrong key is never copied into `agents/ari/meta.json`.

### N8 (RULED) — config naming

Config class `NotifyUserConfig`, env prefix `ENSEMBLE_NOTIFY_USER_` (consistent with tool `notify_user` / category `notify-user`).

### N9 (RULED) — kill-switch ships

`NotifyUserConfig.enabled` default true; `ENSEMBLE_NOTIFY_USER_ENABLED=false` disables the tool with typed refusal `notify_disabled` (cheap insurance against the disclosed prompt-injection exfil residual). Surface in config.yaml mirror block. *(12th code.)*

### N10 — §8 cross-references mapped to the flat 7-item list

§8.1→item 1 (tool module), §8.3→item 3 (resolution chain), §8.4→item 4 (pre-flight/gates/send/retry/contract), §8.7→item 7 (test surface). plan-overview.md phases now cite item numbers.

### N11/N12 — docstring steering clause (docs-only)

Add the double-send steering clause for already-delivering wake shapes (if the wake shape already delivers, prefer silence) + pair the ≥60s interval-proxy disclosure with it in the SAME docstring block.

---

## 7. Advisories (green — implementer checklist)

- **rule.md re-pin precondition:** the 35-file `SEALED_SHA_BASELINES` tripwire covers BOTH `ari/workflow.md` AND `ari/rule.md` — check both rows before the D12 ceremony; re-pin every tripwire-covered file the amendment set touches, in the same commit.
- **config.yaml mirror block** for the notify config (house style mirrors Config field names). *(N8 fold-2: class is `NotifyUserConfig`, env prefix `ENSEMBLE_NOTIFY_USER_`; surface the N9 kill-switch `enabled` in the mirror block.)*
- **Mutation-verified leg** for the NEW registration pin (`tests/unit/test_notify_user_registration.py`): planted-defect → RED → restore → GREEN.
- **`NotifyAudit` on the `daemon/api.py:991-998` late-import + `factory.py:72-77` create_all paths** — the table must exist on both boot paths. *(promoted to a REQUIRED Phase-3 item by N1 — fold 2.)*
- **"D1–D3 docstrings" disambiguation in D12 wording:** refers to the three self_alarm.py doctrine sites (:52-53, :104-108, :567-578 as of the old base) — NOT rulings D1–D3; keep the implementation docs unambiguous.
- **v0.27.8 re-derive at stage time** (v0.27.7 taken by the nest-ops lane; concurrent lanes may move it again).

---

*Fold date: 2026-10-25. Base `7af1d2632d` == origin/latest. Frozen set: D1–D12 + three leader adjudications + C3/W1–W11. Non-re-litigable.*

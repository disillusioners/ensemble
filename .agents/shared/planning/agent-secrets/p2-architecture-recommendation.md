# Agent-Secrets P2 — Architecture Recommendation

**Date:** 2026-10-18
**Worktree:** `/home/ensemble-worktrees/agent-secrets-p2` · branch `feature/agent-secrets-p2` · HEAD `a3e9f1bda`
**Status:** Complete — ready for developer commission
**Method:** 3-worker area fan-out (seams / scrub / lifecycle) + architect synthesis; all anchors spot-verified at HEAD.
**Worker instances:** `d012d9de` (seams, data-flow-design) · `e3a4c28c` (scrub, data-flow-design) · `002c7e19` (lifecycle, resilience-design)
**Confidence:** High. Flip-condition: if the leader rules retired-secrets must fail closed (§8 D1), the resolver contract changes by one branch; nothing else moves.

---

## 0. Executive Summary

P2 turns the P1 mint-only marker `__ENS_SECRET__<uuid4>__` (FROZEN, `secret_service.py:150-167`) into a **resolvable reference** and closes the **both-sides transcript leak** (tool results + `secret_store` tool-call args). Design shape:

1. **Resolution** at four spawn/invoke seams, all fed by ONE new sibling resolver `daemon/services/secret_resolver.py` (LANE-2 mirror: PG re-read per resolution, no RAM plaintext, fail-closed typed envelopes). MCP resolution happens at the **spawn point**, NOT in config-build (corrects plan §6.2 — see §1c).
2. **Scrub** at **three wiring sites**, not one: results-side at `wrapped_tools_node` post-execution (pre-state-commit — protects agent-visible live context), args-side at the checkpoint saver proxy (persisted copy only — args must stay intact in live state for execution), plus `enqueue_message` persisted bodies as defense-in-depth. Single shared scrub function `daemon/services/secret_persistence_scrub.py`.
3. **Audit**: P2 becomes the first emitter of the already-reserved `use_marker` action; one additive `context TEXT` column; scrub audits fire per-substitution only.
4. **Migration**: exactly one additive file — `20261018_000001_secrets_audit_add_context.sql`. No new tables, no destructive DDL, `rollback_safe` stays green.

Zero behavior change for agents that never touch secrets (no-op fast paths everywhere). Do-not-touch lanes untouched: `db_connections` Fernet, KMS-Lite LANE-1/2, `ens_env_read` BYOK.

---

## 1. Resolution Seam Design

### Shared resolver — `daemon/services/secret_resolver.py` (NEW)

Sibling to `kms_resolver.py`; **does not modify** `kms_lite.py` / `kms_resolver.py`.

- Imports `ENS_SECRET_MARKER_RE` (the FROZEN string constant, `secret_service.py:161-167`) and recompiles consumer-side: `_MARKER_PATTERN = re.compile(ENS_SECRET_MARKER_RE)` + `.fullmatch()` — mirrors `kms_resolver.py:65-79` convention (string constant, consumer-rebuilt pattern; never `re.compile(MARKER_RE).match()`).
- API (signatures only):
  - `resolve_secret_markers(env: dict[str, str] | None, *, secret_service=None) -> dict[str, str] | None` — returns a NEW dict (R1 invariant); non-marker values pass through byte-identical.
  - `resolve_marker(value: str, *, secret_service=None) -> str` — single-value form for tool-param pre-invoke.
  - `SecretMarkerResolutionError(RuntimeError)` — carries `secret_id` (UUID) only, NEVER the value; mirrors `KMSMarkerResolutionError` shape (`kms_resolver.py:82-98`).
- Per-resolution flow (inside `SecretService.resolve_marker(actor, secret_id)`): `engine.begin()` → `SELECT id, wrapped_secret, retired_at FROM secrets WHERE id = :id` → `SecretCipher.unwrap` → stamp `use_marker` audit row **in the same transaction** → return plaintext. **No RAM cache of plaintext, ever** (plan §2 anti-pattern; KMS-Lite LANE-1 in-memory drain at `kms_lite.py:162` is the named anti-pattern).

### (a) bash subprocess env overlay

| | |
|---|---|
| Integration | `daemon/tools/bash.py:200-206` `BashInputSchema` — NEW optional `env: dict[str, str] \| None` field; `daemon/tools/bash.py:287-305` `asyncio.create_subprocess_exec/_shell` — pass assembled `env=` (today absent → implicit `os.environ` inheritance) |
| Timing | **Spawn-time**: `env = os.environ.copy() ⊕ tool_call["env"] overlay` → `resolve_secret_markers(env)` → `resolve_env(env)` (KMS-Lite chained, unchanged) → spawn |
| Flow | Agent authors `{"GITHUB_TOKEN": "__ENS_SECRET__<id>__"}` → resolver swaps marker→plaintext → child env only |
| Plaintext locus | Child process env exclusively; kernel copies envp at fork-exec; parent retains no reference after exit |
| Failure | `SecretMarkerResolutionError` → caught at `bash.py:369-397` → typed `{ok:false, code:..., surface:"bash.env"}` envelope; **child NOT spawned — never partial env** |

Markers are resolved ONLY in the assembled env dict; parsing markers out of the `command` string stays OUT (plan §6.1 decision — quoting-fragile).

### (b) proc subprocess env

| | |
|---|---|
| Integration | `daemon/tools/proc_tools.py:493-504` `_build_child_env(process_id)` — NEW optional `process_env_overlay: dict[str,str] \| None` param; overlay merges atop `os.environ.copy() ⊕ tracking_id` |
| Decision | **Separate edits for bash and proc; share ONLY `resolve_secret_markers`.** A shared env-assembly helper risks publishing an LLM-visible `env` param onto the operator-internal proc path (different caller postures). |
| Timing / locus / failure | Spawn-time, child-env only, fail-closed — **as implemented (deviation (e), corrected 2026-10-18): secrets-ONLY** — `proc_tools.py:504-548` routes the assembled dict through `resolve_secret_markers` and STOPS there; the KMS-Lite chained step of (a) is deliberately NOT applied (the operator overlay is the agent-secrets lane; KMS-Lite resolution of operator env is out of this seam's scope). The `process_env_overlay` param has ZERO callers today — the lane is DORMANT operator surface, not the (a)-style LLM-authored carrier this row originally implied. |

### (c) MCP server spawn env — ⚠️ corrects plan §6.2

Plan §6.2 loosely said resolve "in `build_pooled_stdio_config`". **REJECTED**: that function (`warmup_pool.py:780-793`) returns **stored config** — resolving there would persist plaintext into DB config (breaks the KMS-Lite R1 invariant). Resolution belongs at the **spawn point**, mirroring KMS-Lite:

| Path | Anchor (verified) | Change |
|---|---|---|
| Warm pool (re)spawn | `daemon/mcp/warmup_pool.py:~210-214` `_create_pooled_connection` — today `env=resolve_env(config.env)` | → `env=resolve_secret_markers(resolve_env(config.env))` |
| Cold spawn #1 | `daemon/mcp/connection_manager.py:210` (`_create_stdio_session`) | same wrap |
| Cold spawn #2 | `daemon/mcp/connection_manager.py:474-475` (`_create_test_stdio_session`) | same wrap — **both cold paths must be wrapped, not just the pool** |

- `update_server_env` (`warmup_pool.py:316-389`): unchanged — healthy pooled conns drain with their old env; NEW conns resolve fresh markers at spawn (matches the existing "env captured at fork" design at `:338-345`).
- Timing: once per (re)spawn, fresh DB read per spawn.
- Failure: spawn aborted; envelope to daemon log; server does NOT start with literal markers or partial env.

### (d) Tool parameter resolution — opt-in per tool

| | |
|---|---|
| Mechanism | NEW optional `secret_typed_args: frozenset[str]` slot on tool registration (parallel to `TOOL_REQUIRED_AGENTS`, `daemon/tools/_auth.py:35-40`). Tools declare e.g. `secret_typed_args=frozenset({"api_token"})`. Undeclared tools = zero change. |
| Execution site | `daemon/services/long_tool_nudge.py:573-677` `wrapped_tools_node` (wired at `daemon/graph.py:12845-12856`) — **pre-invoke**: for each `tool_call`, copy declared fields, `resolve_marker(...)` per value, write back into the invoke-bound args copy, then `bare.ainvoke(...)`. |
| Timing | Pre-invoke per LLM tool call; plaintext exists only in the in-memory args dict during tool execution. |
| Failure | Typed envelope ToolMessage per existing tool-error semantics (ToolNode `handle_tool_errors=True` pattern at `long_tool_nudge.py:~597`). |
| NOT global | No blanket wrapper over every tool — opt-in only (plan §6.3). |

### Spawn-vs-invoke timing summary

| Seam | Phase | DB read | Plaintext lifetime |
|---|---|---|---|
| bash env overlay | spawn | per bash call | subprocess env, cleared at child exit |
| proc env | spawn | per proc call | subprocess env |
| MCP warm pool | per-conn spawn | per `_create_pooled_connection` | stdio child env, captured at fork |
| MCP cold spawns ×2 | per-conn spawn | per spawn | stdio child env |
| Tool params | pre-invoke | per tool call | args dict during `bare.ainvoke` only |

### Plaintext containment guarantees

- Stored config NEVER holds resolved values (R1 invariant; spawn-point resolution).
- Tool RESULTS never carry plaintext back — results-side scrub at `wrapped_tools_node` (§2) replaces any echoed value with the marker before it joins state.
- Error envelopes carry `secret_id`/code only (PB-F1 class).
- Resolution adds NO permission gate — reachable only through the existing tool-call surface; the only boundary remains `tools.allow: ["secrets"]` (plan §2).

---

## 2. Both-Sides Scrub Design

### 2.1 Verified convergence points (re-enumerated at HEAD `a3e9f1bda`)

| # | Site | Side | Verdict |
|---|---|---|---|
| 1 | Checkpoint saver proxy `daemon/checkpoint_adapter.py:298/301` (`aput`/`aput_writes`) + sync `:386/389` (`put`/`put_writes`), installed at `PostgresCheckpointerAdapter.__init__` `:968`, wired via `graph.compile(checkpoint=...)` at `daemon/graph.py:13145` | **BOTH** (serialized `messages` reducer: `AIMessage.tool_calls[*].args` + `ToolMessage.content`) | ✅ primary persistence choke-point |
| 2 | `MessageQueue` row insert `_prepare_enqueued_message` `daemon/services/instance_messaging.py:2160-2188` | ARGS-adjacent (human-prompt text; tool args do NOT pass here) | ✅ defense-in-depth site |
| 3 | Progressive SSE `graph.astream` loop `instance_messaging.py:4888-4957` (`dispatch_message` at `:4950`) | agent prose only — no tool bodies | ✅ no independent persistence (covered transitively once content can't contain values) |
| 4 | Final dispatch `daemon/sources/dispatcher.py:485-626` (`dispatch_completed`) + `:673-779` (`dispatch_message`), dedup `:525-528` | final AI text only — both-seams rule noted; no tool bodies | ✅ transitively covered |
| 5 | Synthetic ToolMessage placeholders (`graph.py:12314-12322`, `:12397-12402`, `:12413-12420`; `instance_messaging.py:334-357`; `graph.py:8391-8464`) | fixed placeholder text | ✅ not secret-bearing |
| 6 | Snapshot capture `daemon/services/snapshot_executor.py:514` → `:551-558` transcript build | RESULTS persisted; **ARGS elided by construction** (renders `role + text` only) | ✅ test-pin the elision |
| 7-10 | Pairing guards (`graph.py:8391-8464`, `:9437-9464`, `:10005-10034`), `LONG_TOOL_REGISTRY` timestamps, attest contextvar, attestation-judge shape redaction (`attestation_report_judge.py:209-259`) | in-memory only / pre-existing separate surface | ✅ no new persistence |

Checked and rejected as convergence points: `maintenance.py` (admin paths only), `mcp_servers.py:281 redact_secrets` (config redaction, not messages), `log_redaction_filter.py` (log lines, separate surface), `work_notifier.py` (job events), `oauth_client.py`. No standalone transcript writer exists — transcripts derive from checkpoint state (site 6).

### 2.2 Choke-point ruling — THREE wiring sites, ONE function

Worker B proposed the saver proxy alone as the single choke-point. **Synthesis correction**: a saver-only scrub closes *persistence* but leaves the **live-state hop** open — the LLM reads the in-memory `messages` list on its next turn, so an unscrubbed live `ToolMessage` (e.g. bash `env` output echoing `GITHUB_TOKEN=<plaintext>`) would put the value into agent context, from which it can reach `AIMessage.content` — which the saver-side design deliberately does not scrub. That is exactly the PB-F1 channel this feature exists to close. The plan's §6.4 anchor (`wrapped_tools_node`, post-ToolNode, pre-state-commit) was right for the results side. Final design:

| Site | Anchor | Covers | Why here |
|---|---|---|---|
| **S1 — results** | `wrapped_tools_node` post-execution, pre-state-commit (`long_tool_nudge.py:573-677`, wired `graph.py:12845-12856`) | `ToolMessage.content` of REAL tool results | Scrubs BEFORE the result joins live state → protects agent-visible context AND everything downstream (checkpoints, snapshots, dispatch) |
| **S2 — args + belt** | `_SaverRetryProxy.aput/aput_writes` (+ sync `put/put_writes`) — `checkpoint_adapter.py:298/301/386/389`, inside the existing `_call_with_retry` wrapper | Persisted copy: `AIMessage.tool_calls[*].args` (+ `additional_kwargs["tool_calls"]` OpenAI-flavor fallback) + `ToolMessage.content` | **Args cannot be scrubbed earlier** — they must stay intact in live state for tool execution and same-turn loop repair. The saver sees a serialized snapshot; live objects untouched (same pattern as existing `redact_exc_str` at `checkpoint_adapter.py:280`) |
| **S3 — defense-in-depth** | `_prepare_enqueued_message` (`instance_messaging.py:2160-2188`) | persisted queue-row body | plan §6.4 pinned; closes user-paste echo into `message_queue` |

ONE shared function used by all three: `daemon/services/secret_persistence_scrub.py` → `scrub_payload(...)` / `scrub_message(msg)`. Ordering: **scrub BEFORE persist** — S2/S3 transform the serialized copy inside the write path; S1 transforms the message before it is appended to state (so the checkpoint never sees plaintext results either).

### 2.3 Live-vs-persisted copy + retry ruling

- Live graph state: results scrubbed (S1); **args intact** until persisted (S2 scrubs only the serialized bytes). `is_retry` re-dispatch (`instance_messaging.py:3338-3340`) reads the checkpoint → sees scrubbed args.
- **The breaking case** (Worker B 🔴): pause→resume between agent-node commit and tool completion re-invokes `secret_store` with the scrubbed arg → the marker string would be wrapped as a "new secret".
- **RULING (primary): idempotent marker-passthrough in `secret_store`.** If `value` fullmatches `ENS_SECRET_MARKER_RE` and the referenced id exists → return `{id, name}` of the existing secret WITHOUT re-wrapping; if the id does not exist → typed `secret_not_found` envelope. Converts the retry case into an idempotent success — autonomy-preserving, no wedge path, one function. (Alternative, if the leader prefers loudness: Worker B's one-shot guard — raise on retry-after-store. Both are single-point changes; default per the trust model = idempotent passthrough.)
- `generate_ssh_keypair` / `generate_api_token` carry no secret-typed args → no retry exposure.

### 2.4 Semantics

- **Exact-match only**, against stored plaintext VALUES. No regex/shape redaction (deferred I0 spine). Markers are never scrubbed (safe by construction — scrubber skips values that fullmatch the marker regex).
- **Replacement token = the marker itself** `__ENS_SECRET__<id>__`: format-stable, self-documenting in transcripts, idempotent under multi-pass scrubbing, round-trippable via `parse_marker`.
- **Collision determinism**: multiple secrets sharing a plaintext → first id by `created_at` ordering (`list_active()` sort, `secret_service.py:805`).
- **Retired secrets stay in the scrub set** — their values must never leak from old transcripts regardless of resolve policy (§3.2).
- `AIMessage.content` is NOT scrubbed (LLM-authored prose). With S1 in place the LLM never legitimately sees a plaintext value, so prose echo requires hallucination; accepted residual risk (🟡, §7).

### 2.5 Value-set sourcing + performance

- **Decrypt-on-scrub, NO plaintext cache** (plan §6.4 ruling). The scrub set is an **id/name/kind index only** (metadata, not secret state): loaded at init from `secrets WHERE retired_at IS NULL`, near-zero no-op fast path when empty (`len()` check, sub-µs — autonomy-first satisfied).
- **🔴 Staleness catch (Worker B)**: secrets stored AFTER init are invisible to the scrub until re-query. **Mitigation: in-process invalidation event fired by `SecretService.store`** (single subscriber swaps the id-set) **plus a TTL backstop re-query (60s)**. Cost: 1 dict lookup per scrub.
- Per-scrub: one Fernet unwrap per active secret per write → O(N×W). Cost math: 50 secrets × 100 writes/turn × 50 turns/mission ≈ 250k unwraps ≈ 2.5-7.5 s CPU/mission → **<1% turn latency** against the ≥1 s LLM floor. Acceptable for v1.
- Checkpoint-size effect: **net shrink** — a 1KB secret arg/result becomes a ~40-byte marker.

---

## 3. Durable Resolution Lifecycle

### 3.1 Re-read semantics (LANE-2 mirror)

Every resolution = fresh PG read inside `engine.begin()` (SELECT → decrypt → `use_marker` audit row, one transaction). No RAM plaintext at any layer; restart-durable by construction. Markers may sit unresolved inside checkpoints up to the 168h TTL (`daemon/constants.py:147`) — resolution always comes from the store, never checkpoint-carried state. Staleness: value minted/retired between spawns is picked up on the next resolve (mint-then-resolve serializes through `engine.begin()`; concurrent retire during a resolve transaction loses the race safely).

### 3.2 Retired / missing handling — ruling

- **Missing id** → `secret_not_found`, fail-closed, child/server NOT spawned.
- **Retired id** → **CONTINUES TO RESOLVE in P2** (plan §4.1 ratified semantics: "marker keeps resolving until a later retention policy"; soft-retire = delisting from `list`/`read_public` + audit visibility, not revocation). Worker C's table proposed fail-closed here; its own §1 prose says resolve-OK; plan §4.1 and Worker A's contract both say resolve. **`secret_retired` envelope code is RESERVED but not emitted** while this policy holds — flipping to revocation semantics is a one-branch change at the resolver if the leader rules otherwise (§8 D1). Retired values REMAIN in the scrub set regardless.
- **Malformed marker** → `secret_marker_malformed` at the seam wrapper (pre-resolver).
- **DB unavailable / flapping** → `secret_resolver_store_unavailable`, **no retry at the seam** (one-shot fail-closed; retry belongs to the agent loop — bounded, visible, no silent spawn-without-env).
- **`Fernet.InvalidToken`** (key rotated without re-encrypt) → `secret_fernet_key_mismatch` (explicit code names the SYSTEM_ENCRYPTION_KEY coupling).
- **Cipher unavailable at boot** (`SecretCipher.is_available()`, `secret_service.py:313`) → `secret_cipher_unavailable` (mirrors `KMSUnavailableError`, `kms_lite.py:130-141`).

### 3.3 Fail-closed error envelope vocabulary (typed, P1 `byok_not_configured` pattern)

| Code | Trigger | Surfacing (all seams) |
|---|---|---|
| `secret_not_found` | SELECT returns 0 rows | `{ok:false, code, surface:"bash.env"\|"mcp_spawn"\|"tool_param", message}` tool-result envelope; child/server NOT spawned |
| `secret_retired` | **RESERVED** — not emitted while retired-resolves holds | — |
| `secret_resolver_store_unavailable` | SQLAlchemy/OperationalError | same envelope family; never partial env |
| `secret_marker_malformed` | fullmatch fails (non-UUID4) | same |
| `secret_fernet_key_mismatch` | `Fernet.InvalidToken` at unwrap | same |
| `secret_cipher_unavailable` | cipher absent/invalid | same |

**PB-F1 rule**: agent-facing text = name/class only, never values. **Anti-differential phrasing**: `secret_not_found` / `secret_marker_malformed` / `secret_retired` share identical message text ("secret resolution failed: <code>") — which id is live stays in the audit log only. (Primary enumeration control is UUID4 entropy — 2^122 guessing is infeasible; identical phrasing is secondary hardening, which downgrades Worker C's 🔴 to 🟡 honestly.)

### 3.4 Audit emission points

- `use_marker` (success AND rejection) — inside the resolve `engine.begin()`, `secret_id` stamped (NULL for not-found), `context` = `bash_env` | `mcp_spawn` | `tool_param`.
- `scrub` — per-substitution only (fires when a value was actually replaced), `context='scrub'`; never per-write (flood).
- `create` / `read_public` / `retire` — P1 paths unchanged (`_append_audit_in_conn` pre-guard, `secret_service.py:1051-1055`).
- Zero rows for secret-free agents (both emitters sit on opt-in paths).

---

## 4. `secrets_audit` Extension

- P1 reality verified: `SECRET_AUDIT_ACTIONS = {create, read_public, use_marker, read_value, read... retire}` at `daemon/repositories/secrets/models.py:370-381` — `use_marker` already in the enum with **no P1 emitter**; P2 becomes its first emitter. `read_value` stays reserved.
- **One additive column**: `context TEXT NULL` on `secrets_audit` — values `bash_env` / `mcp_spawn` / `tool_param` / `scrub`, NULL for P1 rows (forward-compatible enum validated at the service boundary, not by CHECK constraint — keeps future values additive). No values, no instance_id (agent + action is sufficient granularity).
- SQLModel `SecretAuditEvent` gains the `context` field so `create_all` parity holds on fresh dev DBs.

---

## 5. Migration Plan

**File:** `daemon/migrations/versions/20261018_000001_secrets_audit_add_context.sql` (one `*.sql` per migration EVENT — per-event convention, plan §4 preamble; next event after `20261017_000001`).

```sql
-- Migration: secrets_audit.add_context (P2 — resolution/scrub context tag)
-- Additive only; no DROP, no destructive ALTER. Rollback-safe.
ALTER TABLE secrets_audit ADD COLUMN IF NOT EXISTS context TEXT;
-- context: 'bash_env' | 'mcp_spawn' | 'tool_param' | 'scrub' | NULL (P1 rows)
-- No new index: (secret_id, at) already covers the audit query path.
```

- **Rollback safety**: column-additive; reversible one-statement down-migration; no new tables/indexes → `rollback_safe` derivation stays green (no destructive DDL in `daemon/migrations/versions/`).
- **PG application path**: the runner is SQLite-only (`runner.py:316-329`, sqlite_master/PRAGMA); PG column additions route through `EnsembleManager._ensure_postgres_columns` (referenced `runner.py:708-711`). The `*.sql` file is the **persistent record**; Phase 0 verifies the column-ensure path picks the new column up (Worker C flagged this unverified — see §9).
- Actual date-stamp at PR-open if the commit lands on a different day — filename is convention-shaped, not a contract.

---

## 6. Component Map for the Developer (ONE developer, ordered phases)

Tightly coupled shared paths (resolver + scrub set + audit) — do NOT split into parallel lanes.

| Phase | Deliverable | Files touched | Test hooks |
|---|---|---|---|
| **0 — verify** | Confirm `_ensure_postgres_columns` applies `context` on PG; pin SQLModel↔migration parity | read-only + `daemon/repositories/secrets/models.py` (context field) | fresh-DB `create_all` parity test |
| **1 — schema + audit** | Migration + `context` column + audit-reader support | NEW `daemon/migrations/versions/20261018_000001_secrets_audit_add_context.sql`; `daemon/repositories/secrets/models.py`; `secret_service.py` audit emit (context param) | unit: audit rows carry context |
| **2 — resolver** | `SecretService.resolve_marker` + `daemon/services/secret_resolver.py` + envelope vocabulary + `use_marker` emission | NEW `daemon/services/secret_resolver.py`; `daemon/services/secret_service.py` | unit: found / retired-resolves / not-found / malformed / store-unavailable / InvalidToken → typed codes; audit rows on success AND rejection |
| **3 — seams** | bash `env` overlay param; proc overlay; MCP spawn-point wraps ×3; `secret_typed_args` opt-in + pre-invoke | `daemon/tools/bash.py:200-206, 287-305, 369-397`; `daemon/tools/proc_tools.py:493-504`; `daemon/mcp/warmup_pool.py:~210-214`; `daemon/mcp/connection_manager.py:210, 474-475`; `daemon/services/long_tool_nudge.py:573-677`; `daemon/tools/_auth.py` (registration slot pattern) | per-seam: resolve success (child sees plaintext), fail-closed (child NOT spawned, envelope), zero-change for non-declaring tools |
| **4 — scrub** | `secret_persistence_scrub.py` + S1/S2/S3 wiring + id-set invalidation (event + 60s TTL) + `secret_store` idempotent marker-passthrough | NEW `daemon/services/secret_persistence_scrub.py`; `daemon/services/long_tool_nudge.py` (S1); `daemon/checkpoint_adapter.py:298/301/386/389` (S2); `daemon/services/instance_messaging.py:2160-2188` (S3); `daemon/tools/secrets_tools.py` (passthrough + docstring) | (a) live-message `id()` unchanged after scrub; (b) checkpoint roundtrip write+read clean; (c) marker idempotency; (d) `secret_store` retry-with-marker → idempotent `{id,name}`; (e) snapshot transcript elides args; (f) progressive + completed dispatch both carry scrubbed content (both-seams rule) |
| **5 — E2E + pins** | Plan §8 Phase-2 exit criterion end-to-end; snapshot-elision pin; both-seams pin | `tests/integration/test_secrets_v1_endtoend.py` (extend) | mint → bash env overlay resolve → child sees plaintext → transcript shows marker → `use_marker` audit row; restart → marker still resolves |

Phase gates: 1→2 schema deployed; 3 and 4 independent of each other but both depend on 2; 5 gates on all. No new tool category → **no triple-pin updates required** (`secret_typed_args` is a registration slot, not a category).

---

## 7. Risk Register

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| R1 | Saver-only scrub leaves live-state hop open (LLM reads unscrubbed ToolMessage → echoes into prose → persists) | 🔴 | **Designed out**: S1 at `wrapped_tools_node` scrubs results pre-state-commit (synthesis correction over single-choke-point proposal) |
| R2 | Retry/resume re-invokes `secret_store` with scrubbed args | 🔴 | Idempotent marker-passthrough (§2.3); alt = one-shot guard (leader choice §8 D2) |
| R3 | Scrub id-set stale after post-init `secret_store` → new value unscrubbed until restart | 🔴 | Store-event invalidation + 60s TTL re-query (§2.5) |
| R4 | Resolving in `build_pooled_stdio_config` would persist plaintext to DB config | 🔴 (designed out) | Spawn-point resolution only, §1c; both cold-spawn paths wrapped |
| R5 | Scrub false-positive: legit string equals a secret value → transcript irreversibly markerized | 🟡 | Accepted by exact-match semantics (deterministic, auditable via `scrub` rows); value choice is operator's control; reversible only by re-running with the secret retired from the set — document in tool docstring |
| R6 | Scrub perf O(N×W) on hot path | 🟡 | <1% turn latency at 50 secrets (§2.5); sub-µs no-op fast path; revisit caching only with a wrapped-only-value index (never plaintext RAM) |
| R7 | Enumeration differential via distinct envelope texts | 🟡 | Identical phrasing family (§3.3); UUID4 entropy is the real control |
| R8 | `AIMessage.content` (LLM prose) unscrubbed | 🟡 | With S1 the LLM never legitimately sees values; hallucinated echo = deferred I0 spine territory |
| R9 | Per-write `use_marker` audit volume under tight spawn loops | 🟡 | Acceptable (resolution is opt-in, cost rides the existing transaction); downsample later via `context` filter |
| R10 | `_ensure_postgres_columns` path unverified for the new column | 🟡 | Phase 0 verification gate before anything else |
| R11 | OpenCode SQLite substrate may persist messages | 🟢 | Separate substrate, not the secrets runtime; out of P2 scope, noted for the improve phase |
| R12 | Checkpoint-size effects | 🟢 (positive) | Net shrink — secrets collapse to ~40-byte markers |

---

## 8. Decisions Pending (leader/user)

- **D1 — Retired-secret resolve policy.** Default per plan §4.1: retired markers KEEP resolving (soft-retire = delist + audit). Alternative: fail-closed `secret_retired` (revocation semantics) — one-branch change, code already reserved. Recommend keeping plan semantics; flip only if revocation is wanted now.
- **D2 — `secret_store` retry behavior.** Default: idempotent marker-passthrough (autonomy-first). Alternative: loud one-shot RuntimeError guard. Recommend idempotent.

## 9. Gaps & Unverified

- Worker C's report omitted the first-line skill-load confirmation (possible silent skill-bank miss for `resilience-design`); report quality and anchors were high and were independently spot-verified — findings stand, attribution flagged.
- `_ensure_postgres_columns` additive-column behavior (R10) — Phase 0 gate.
- OpenCode SQLite message persistence (R11) — out of scope.
- Third-party `ToolNode` subclasses emitting exotic message types — S2's scrub walks `BaseMessage` polymorphically; rare, unverified.

## 10. Sources

Worker A `d012d9de` (seams, data-flow-design) · Worker B `e3a4c28c` (scrub convergence, data-flow-design) · Worker C `002c7e19` (lifecycle/envelopes/audit/migration, resilience-design). All anchors spot-verified by the architect at `a3e9f1bda` (checkpoint adapter, warmup pool, connection manager, wrapped_tools_node, bash spawn sites, `_build_child_env`, `SECRET_AUDIT_ACTIONS`, graph wiring).

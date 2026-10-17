# Plan Overview: Agent Secrets (v1)

Date: 2026-10-17
Author: planner via plan-creation worker
Status: Draft — Ready for Review (Ari + user)

---

## 0. System Goal

> "The first goal of this system always: agent automatically able to use the secret to complete the job with or without human. Ensemble design/architecture is durable autonomous system that can run one big task for WEEKS and annoy the user as little as possible. The secret/KMS system must support the system goal."

---

## 1. Decisions (2026-10-17)

### 1.1 FEATURE-FIRST directive (user)
> "We focus on feature first, improve later. Don't make it too complicated/overhead for now."

OVERTURNS the architect's I0-first sequencing (KB doc `secret-service-sequencing-i0-i4.md`: I0 redaction spine → I1 capability spine → I2 grants → I3 rotation → I4 escape-hatch closure). **v1 delivers capability (≈I1) first**; the full redaction spine is deferred — only a registry-driven exact-match scrub stays, as cheap containment that rides the feature (part of the feature, NOT hardening).

### 1.2 TRUST-MODEL ruling (user, FINAL)
> "When the user gives the system a secret, it means they TRUST the system to work with it later. No need to ask."

(Other gates, if any, are separate design concerns not part of this feature.)

v1 has **NO runtime gating**: no per-secret grants, no sensitivity flags, no ask-user pause, no fallback/wedge paths. A mid-mission hard pause is a REJECTED failure mode (the system terminates instances stuck awaiting answers ~60 min — a mission killer on weeks-long jobs); v1 contains zero such paths by design.

### 1.3 Architecture settled
Per KB `secret-management-architecture-option-3.md`: **in-core SecretService**, PostgreSQL-backed, Fernet, port-shaped storage seam kept THIN (an interface with a process-boundary-clean data contract — a thin in-process Port, not a tier-2/3 adapter). External Vault/OpenBao REJECTED (decisive: the PB-F1 leak channel is tool-result → transcript → checkpoint, fully process-internal — no external store fixes redaction). Central trick: **by-reference markers** so autonomous use never exposes the value. Do not re-litigate.

---

## 2. Net Gating Model

- **Marker USE** (env overlay / MCP spawn / tool params): fully autonomous, no gate.
- **Secret READ**: autonomous for ANY agent holding the `secrets` tool category.
- **The ONLY access boundary**: per-agent `tools.allow` for the privileged `secrets` category. That's it.
- **Durability**: everything on the long-mission critical path survives restarts — PG store + durable markers + PG-persisted messages/checkpoints. NO RAM-only state on that path. Explicitly contrast KMS-Lite LANE-1 handles (in-memory, drain on restart at `kms_lite.py:162`) as the anti-pattern this system must not repeat for long missions. Note: checkpoint TTL 168h (`daemon/constants.py:147`) bounds how long a marker may sit unresolved inside a checkpoint — resolution must always come from the durable store, never from checkpoint-carried transient state.

---

## 3. Scope

### In (v1 feature core)
- **GENERATE** — ssh keypairs, API tokens.
- **STORE** — durable, encrypted at rest.
- **USE** — marker/reference resolution into subprocess env, tool params, MCP spawn.
- **READ** — category-allow only.
- **Cheap containment (rides the feature, NOT hardening)**:
  - (a) registry-driven EXACT-MATCH scrub of store-known plaintext values from tool results before transcripts — no shape patterns, no hot-path rearchitecture;
  - (b) private keys never leave the store — agents get pubkey + handle only.

### Out (one-line reason each)
| Item | Reason |
|------|--------|
| Full I0 redaction spine | Deferred by feature-first (user directive). |
| Lineage-bound grants + per-secret grants + sensitivity flags | Dropped by trust ruling. |
| Ask-user pause + fallback/wedge paths | Dropped by trust ruling (mission killer). |
| Rotation ceremony + nonce | Improve-later (deferred_key_rotation note). |
| Escape-hatch closure incl. nomask changes | Breaking — improve-later. |
| Shape-pattern redaction | Part of the deferred I0 spine. |
| Audit beyond simple append-only table | Keep minimal per trust ruling. |

### Deferred / future options (one line each, no design)
- Per-secret grants / lineage-bound access — revisit only if the trust model changes.
- Sensitivity flags / ask-user gate — revisit only if the trust model changes.
- Full I0 shape-pattern spine — plug in when escape-hatch closure lands.

---

## 4. Data Model

Additive-only migration under `daemon/migrations/versions/` — 96-file precedent, scheme `YYYYMMDD_NNNNNN_<desc>.sql`, latest `20261014_040000_create_custom_agent_configs.sql`. **Every new SQLModel table needs its own `*.sql`** (zero exceptions).

### 4.1 `secrets` table

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID PK | Immutable handle; the marker text embeds this id. |
| `name` | TEXT UNIQUE NOT NULL | Human-readable, unique within system. |
| `kind` | TEXT NOT NULL | `'ssh_keypair' \| 'api_token'` (forward-compatible enum — add more later). |
| `public_material` | TEXT NULL | Populated for `ssh_keypair` (public key); null otherwise. |
| `wrapped_secret` | BYTEA NOT NULL | Fernet-encrypted private material / token body. Never returned by any read path. |
| `created_by` | TEXT NOT NULL | Agent id (Cardinal #2 source discipline). |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `retired_at` | TIMESTAMPTZ NULL | Soft-retire; marker keeps resolving until a later retention policy. |

**Indexes**: PK(`id`); UNIQUE(`name`); partial idx on (`kind`) WHERE `retired_at IS NULL`.

### 4.2 `secrets_audit` table (minimal, append-only)

| Field | Type | Notes |
|-------|------|-------|
| `id` | BIGSERIAL PK | |
| `secret_id` | UUID FK NULL | Nullable for create events. |
| `action` | TEXT NOT NULL | `'create' \| 'read_public' \| 'use_marker' \| 'read_value' \| 'retire'`. `read_value` is RESERVED-FOR-FUTURE — no v1 emitter (no value-read tool in v1; v1 read = public material only). |
| `actor` | TEXT NOT NULL | Agent id. |
| `at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

**Indexes**: PK(`id`); idx(`secret_id`, `at`).

Rationale for keeping this minimal: trust ruling has no human-in-loop; the `read_value` row lets post-hoc audit see who actually decrypted (not just who looked at metadata), and that is the only auditable signal of real value.

---

## 5. Tool Surface — `secrets` category (#6, privileged)

### 5.1 Category registration (THREE steps + SAME-PR triple-pin rule)

Adding a privileged category requires:

1. **`@register_tool_category`** decorator at `_tool_registry.py:223-248` — `secrets` becomes the **6th** entry in `PRIVILEGED_TOOL_CATEGORIES` at `:194-220` (quintet today: `system_upgrade`, `system-log`, `ens-db`, `ens-env`, `view-views`).
2. **`CATEGORY_MODULES` entry** at `_tool_registry.py:571`.
3. **list-extend inside `create_instance_tools()`** at `daemon/tools/instance.py:2450` — pattern documented at `instance.py:5758-5766` and `:5833-5845`. **Decorator-only registration is SILENTLY INVISIBLE.**

**PLUS** the SAME-PR triple-pin rule (`_tool_registry.py:184-191`) — assert updates required in all four:
- `tests/unit/tools/test_upgrade_registration.py`
- `tests/unit/tools/test_attestation_registration.py`
- `tests/integration/test_maintenancer_spawn_resolves_tools.py`
- `tests/integration/test_service_tool_flag_off_byte_identical.py`

Missing any of the above = **silently invisible**.

### 5.2 Tools (v1)

| Tool | Params | Returns | Notes |
|------|--------|---------|-------|
| `secrets.generate_ssh_keypair` | `name: str` | `{id, name, public_key, fingerprint}` | Private key wrapped & stored; pubkey only in response. |
| `secrets.generate_api_token` | `name: str`, `length: int = 32` | `{id, name, token_preview}` | Wrapped & stored; preview = last 4 chars only. |
| `secrets.store` | `name: str`, `kind: str`, `value: str` | `{id, name}` | Wrap-on-store; open to future kinds. |
| `secrets.list` | (none) | `[{id, name, kind, public_material}]` | No secrets in listing. |
| `secrets.read_public` | `id: UUID` | `{id, name, kind, public_material}` | Pubkey / token_preview only; NEVER `wrapped_secret`. |
| `secrets.retire` | `id: UUID` | `{id, retired_at}` | Soft-retire. |

**Pubkey-only rule** (from I2, retained in v1): private material never crosses a tool-result boundary. Agents get `public_material` + `id` only. v1 read = public material only — no value-read tool exists in v1, so `read_value` in the audit enum is reserved-for-future; the v1 decryption event is `use_marker`. Decrypted value is only ever materialized at the marker-resolution seams (§6).

### 5.3 Day-One Allow-List (proposal; see §9 Q2)

`worker`, `tester`, `leader` — generic dev team + orchestrator.
NOT seeded into `designer` / `sketcher` / `critic` (OD plugin team, no v1 secret need) and NOT `charter`.
Access vehicle: per-agent `meta.json` `tools.allow: ["secrets"]` → `tool_filter` load-assignment at `daemon/loader.py:640`; startup validation for unknown category tokens at `daemon/registry.py:1368-1405` — **warns + grants nothing** (does not reject); enforcement at `resolve_tool_filter` (`instance.py:373`) + `_apply_tool_filter` (`:6200`, strips at `:6230` / `:6266`). Existing precedent: `tools.allow: ["ens-env"]` as the sole access path.

### 5.4 Do-not-touch seams (confirm-list)

| Seam | Path | Reason |
|------|------|--------|
| `kms_attach` | `daemon/tools/infra.py:995` | KMS-Lite LANE-1/2 attach; untouched. |
| `mcp_set_env` | `daemon/tools/infra.py:1527` | Non-secret env writes; untouched. |
| `ens_env_read` | `daemon/tools/ens_env_tools.py:255` | BYOK lane; untouched. |
| KMS-Lite lanes | `kms_lite.py:75-122`, `kms_resolver.py:149-203` | Marker regexes + resolver; untouched — new scheme must be visually/semantically DISTINCT (`__ENS_SECRET__<id>__` vs `__KMS_REF__KMS_HANDLE_<uuid>__` vs `__KMS_ENV__<VAR>__`). |
| `redact_secrets` | `daemon/routers/mcp_servers.py:185-251` | Out of v1 (KEY-shape, no marker exception); I0 follow-up ticket. |

---

## 6. Consumption Seams

### 6.1 bash / proc env overlay (one helper edit, contained)
Today: `bash.py:289-305` creates subprocess with **NO `env=` kwarg** → implicit full `os.environ` inheritance. `proc_tools.py:493-504` `_build_child_env()` does `os.environ.copy()` at `:502`.

**Marker INPUT channel (decided)**: a NEW optional `env` overlay param on the bash tool — the agent authors e.g. `{"GITHUB_TOKEN": "__ENS_SECRET__<id>__"}`; the overlay dict merges into the assembled env and markers resolve at env-assembly/merge time, before spawn. **This `env` param is a NEW field — does not exist in-tree yet.** Parsing markers out of the `command` string is OUT of v1 (fragile, quoting-sensitive); resolution happens ONLY in the assembled env dict. Rationale: a structured param keeps the trust boundary clean — the agent declares intent to inject a secret into the child env, and the wrapper resolves before spawn — with zero text-mangling risk from in-command substitution.

**Required change**: make env assembly explicit at this seam:
1. Start from `_build_child_env()`'s `os.environ.copy()` basis.
2. Merge the tool-call's optional `env` overlay dict on top.
3. Run `SecretService.resolve_markers(env_dict)` — swap any `__ENS_SECRET__<id>__` values with decrypted plaintext.
4. Pass assembled env into `subprocess.Popen(..., env=...)`.

Small, contained — NOT a hot-path rearchitecture.

### 6.2 MCP spawn
`daemon/mcp/warmup_pool.py:707` `build_pooled_stdio_config` overlays DB-row `config.env` over build defaults (row wins every key, precedence pinned `:722-739`; env realized at pooled (re)spawn). `update_server_env` at `:316` refreshes pooled env in-place without touching healthy connections.

**Required change**: in the env-assembly step inside `build_pooled_stdio_config`, run marker resolution against the merged env before stdio spawn. Confined to one MCP server's env-build path.

### 6.3 Tool params
Tool-wrapper factory closure (pre-invoke) inspects declared secret-typed str args for `__ENS_SECRET__<id>__` and resolves in-process before the underlying tool runs. **Opt-in per tool** via a NEW `_arg_schema` secret-typed field (does not exist in-tree yet; e.g., the bash `env` overlay per §6.1, HTTP invocations) — NOT a global wrapper for every tool.

### 6.4 Exact-match transcript scrub (cheap containment)
Real tool results become ToolMessages inside langgraph's prebuilt ToolNode, wrapped by `wrapped_tools_node` wired at `daemon/graph.py:12845-12856` — **THE single best pre-transcript interception** (post-ToolNode, pre-state-commit, catches every real result). Synthetic ToolMessage sites (`graph.py:444`, `:12314-12487`; `instance_messaging.py:346`) carry fixed placeholder text — no scrub needed.

**Required change**: in `wrapped_tools_node` (post-execution), before the ToolMessage becomes part of state, run `SecretService.exact_match_scrub(content)` — substitute known plaintext (decrypted bytes of all active secrets) with the marker `__ENS_SECRET__<id>__`. Second convergence point: `enqueue_message` (`instance_messaging.py:2497`) — same scrub on queued message bodies.

No shape patterns. No hot-path rearchitecture. Registry-driven exact-match only.

**Cost note**: scrub is O(N) Fernet decrypts per ToolMessage (N = active secrets). Cache policy — DEFAULT: **no cache** (any cache = RAM-resident plaintext, in tension with the ruling-2 anti-pattern of RAM-resident secret state on the long-mission path); the decrypt cost is the acceptable price for v1.

---

## 7. Encryption

### 7.1 Key source (see §9 Q1 for recommendation)
- **Option A — REUSE `SYSTEM_ENCRYPTION_KEY`** (matches recorded unified-master-key design from KB): simpler; one rotation ceremony already covers `source_credentials` + `db_connections`; v1 adds `secrets` to that list (linear growth of an existing coupling).
- **Option B — New `SECRETS_ENCRYPTION_KEY`**: isolates rotation (decrypt only the `secrets` registry on rotation); matches the no-shared-helper status quo (`credentials.py:74`, `kms_lite.py:221` each roll their own Fernet); but one more env var to document + bootstrap.

### 7.2 Helper
Today no shared encrypt helper exists. v1 SECRET-ENCRYPT path uses a shared internal `SecretCipher` helper colocated under the same package as `SecretService` — NOT a new cross-cutting util (keep contained).

### 7.3 Dependency
`cryptography>=42.0.0` already at `pyproject.toml:22` — keygen needs nothing new. **paramiko NOT declared** — ssh keypair generation uses `cryptography`'s primitives directly (no ssh client, no paramiko).

---

## 8. Phased Delivery

### Phase 1 — Store + Tool Surface + Category (≈M)
Schema migration (additive, `20261017_NNNNNN_create_secrets_tables.sql`), `SecretService` + thin storage port (process-boundary-clean data contract — a thin in-process Port, not a tier-2/3 adapter), `secrets` category registration (all three steps + triple-pin test updates). Tools: `generate_ssh_keypair`, `generate_api_token`, `store`, `list`, `read_public`, `retire`. Pubkey-only read pattern.

**Exit criterion**: a worker agent (allow-listed) can `secrets.generate_ssh_keypair` → `secrets.list` → `secrets.read_public` and get the pubkey back; test pack `tests/integration/test_secrets_v1_basic.py` green.

### Phase 2 — Marker Scheme + Consumption Seams + Scrub + Audit (≈M)
Marker resolver under `daemon/services/` (parallel to `kms_resolver.py`, distinct `__ENS_SECRET__` prefix). `bash` / `proc_tools` env overlay (single helper edit). `warmup_pool.build_pooled_stdio_config` env resolution. Tool-param resolver wrapper (opt-in per tool). Exact-match scrub in `wrapped_tools_node` post-execution + `enqueue_message`. Append-only `secrets_audit` table + writes from each Phase-1 tool. Triple-pin test updates (additive assert for new tools).

**Exit criterion**: worker agent can `secrets.generate_api_token` → author the marker in the bash tool's NEW `env` param (e.g. `{"GITHUB_TOKEN": "__ENS_SECRET__<id>__"}`) → subprocess sees plaintext → transcript shows marker (plaintext scrubbed) → audit row exists. E2E test pack `tests/integration/test_secrets_v1_endtoend.py` green.

Both phases land independently; Phase 2 gates only on Phase 1's schema being deployed (additive, no rollback ceremony needed). Phase 2 has **zero impact** on agents without the `secrets` allow-list.

### Coupling Map

| | P1 | P2 |
|---|---|---|
| **P1** | — | tight (P2 reads P1 schema; adds marker resolver + audit on top of P1 tools) |
| **P2** | tight | — |

P2 ↔ KMS-Lite: independent (distinct marker prefixes, untouched code paths).
P2 ↔ db_connections: independent (different registry; key-source coupling only — see §9 Q1).
P2 ↔ `ens_env_read` BYOK: independent (per §5.4 do-not-touch).

---

## 9. Open Questions

### Q1. Encryption key source
**Recommendation: Option A — REUSE `SYSTEM_ENCRYPTION_KEY`.**
Matches recorded unified-master-key design from KB `secret-management-architecture-option-3.md`; rotation coupling grows linearly rather than spawning a new ceremony; trusts the system's existing convention over adding a single-purpose key. Rotation is deferred regardless (per `deferred_key_rotation` note), so the extra coupling is a paper risk today.

### Q2. Day-one agent allow-list
Proposal: `worker`, `tester`, `leader`, `ari`. Final list is the operator's call (Ari + user).

### Q3. Marker format blessing
Proposal: `__ENS_SECRET__<secret_id>__` — namespace prefix `__ENS_SECRET__`, UUID inside. Visually and semantically distinct from both KMS-Lite lanes (`__KMS_REF__KMS_HANDLE_<uuid>__`, `__KMS_ENV__<VAR>__`). Once blessed, freeze the regex (parallel to `KMS_MARKER_RE` at `kms_lite.py:102-108`).

---

## 10. Migration & Compat

- **Additive only**: new tables; no DDL on existing tables. Per 96-file precedent, each new SQLModel table gets its own `*.sql` (zero exceptions). Migration committed alongside Phase 1.
- **db_connections**: untouched (separate Fernet wrapper per `daemon/sources/credentials.py:19-39` conventions).
- **Rotation coupling (per §9 Q1 Option A)**: reusing SYSTEM_ENCRYPTION_KEY linearly extends the existing rotation coupling — any future rotation must re-encrypt db_connections Fernet credentials (and now `secrets.wrapped_secret`) too, or both registries break.
- **KMS-Lite LANE-1 / LANE-2**: untouched (`kms_lite.py:75-122`, `kms_resolver.py:149-203`); new marker format visually/semantically distinct.
- **`kms_attach` / `mcp_set_env` / `ens_env_read` BYOK**: untouched (per §5.4).
- **`PRIVILEGED_TOOL_CATEGORIES` quintet → sextet**: `secrets` becomes #6, never default-open; same-PR triple-pin test updates mandatory.

---

## 11. Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Worker agent can generate + persist ssh keypair via `secrets.generate_ssh_keypair` | E2E test pack | green |
| 2 | Private ssh key bits never appear in any ToolMessage transcript | transcript grep across e2e log | 0 occurrences |
| 3 | Marker `__ENS_SECRET__<id>__` placed in `bash` subprocess env resolves to plaintext in subprocess AND is scrubbed from transcript | E2E assertion | green |
| 4 | Agents without `secrets` in `tools.allow` get ZERO `secrets.*` tools in their tool surface | unit assertion per agent meta.json | pass |
| 5 | Restart preserves all secrets + allows continued marker resolution | kill+restart + E2E | green |
| 6 | Cheap exact-match scrub catches all known plaintext in tool results (no leaks into state) | test all 3 v1 secret kinds | 0 leaks |
| 7 | Audit rows written for every create / read / use / retire action (testable post-P2 only — `secrets_audit` lands in P2) | E2E + row count | 1:1 with actions |

---

## 12. Research Insights (prior art digested; cited by name as RAG knowledge-base entries, not on-disk files)

- **`secret-management-architecture-option-3.md`** (adopted): staged in-core SecretService + port-shaped storage tier; external Vault/OpenBao REJECTED (decisive: PB-F1 leak channel tool-result → transcript → checkpoint is process-internal — no external store fixes redaction). Central trick: by-reference markers so autonomous use never exposes the value.
- **`secret-service-sequencing-i0-i4.md`** (the OVERTURNED order): I0 redaction spine → I1 capability spine → I2 grants → I3 rotation → I4 escape-hatch closure. **v1 ≈ I1 + pubkey-only from I2 + exact-match subset of I0.** Deferred: rest of I0, all of I2's grants/gating (dropped by trust ruling), I3, I4.
- **`privilege-secrets-tool-grants.md`** and **`env-byok-secret-surface.md`**: fed §5 gating design and §5.4 do-not-touch seams.
- **Encryption key facts**: KB records NO explicit reuse-vs-separate verdict; recorded design implies ONE unified master key (SYSTEM_ENCRYPTION_KEY lineage) rotated across all 4 registries. Rotation coupling: SYSTEM_ENCRYPTION_KEY rotation must re-encrypt all db_connections Fernet credentials or external-conn registry breaks (fed §9 Q1).
- **Verified anchors (HEAD 772ca92b9)**: all paths in this plan cite the explorer-verified anchor list from this session (HIGH confidence); no codebase re-research performed.

---

## 13. Binding Design Constraints (recap)

1. New marker scheme distinct from both KMS-Lite lanes; KMS-Lite untouched; do-not-touch seams per §5.4.
2. `secrets` = privileged category #6, never default-open; three-step registration + triple-pin test updates all named in §5.1 (missing any = silently invisible).
3. Additive-only migrations per §4/§10; every new SQLModel table has its own `.sql` file.
4. Trust model has no runtime gating — no pause paths anywhere in v1.
5. Long-mission critical path has zero RAM-only state.

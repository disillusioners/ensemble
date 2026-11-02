# Technical Analysis: Plane PM Issue Tools + Env-Scoped Workspace Enforcement

Date: 2026-11-02
Author: planner[v2] via technical-analysis worker
Analysis depth: deep-dive
Status: Draft (ready for architect adjudication)

> **Evidence base:** `investigation-digest.md` (§1–§4 are the commissioned digest/lean/stamp inputs, reproduced VERBATIM; §5–§9 are my own verification and probe evidence) and `tools-list-inventory.md` (the captured remote inventory).
> **All `file:line` anchors in this document were re-verified by me** against `/home/nea/ensemble-src-wt-plane-pm` @ `941cb6321`, unless explicitly marked `UNVERIFIED-BY-YOU`.
> **Repo state:** read-only. Nothing outside `.agents/shared/planning/plane-pm-issue-tools/` was written. No commits, no daemon/DB/release/`.env` contact.

---

## Question

Three coupled questions, from the leader's commission:

1. **Wiring** — how should the project-manager agent actually receive Plane issue CRUD tools, given that the charter + `mcp_full_access: ["plane"]` are in place but **zero** `plane_*` tools reach the agent at runtime?
2. **Enforcement (user directive, Discord 2026-11-02)** — how must the workspace be made **structurally** env-scoped so that workspace `nea` is production-only and dev/demo can only ever target `e2e`?
3. **Verification** — what exact procedures prove the above, and in what order?

Plus the mandated sub-questions: the Path A vs Path B vs Hybrid trade-off with a **write-verb reality check**; the workspace-enforcement design (E1–E5) with refusal semantics; loud-skip observability; the prefix-sweep ownership guard; a risk register; verification-gate procedures; and adjacent-defect recording.

---

## Context Summary

**The wiring gap is not a permission bug.** `agents/project-manager/meta.json` declares `mcp_full_access: ["plane"]` and `tools.allow` contains `"plane"`; both gates are wired and working (`daemon/registry.py:411/:710/:771/:1419-1457` for the schema-level strip; `daemon/tools/instance.py:403-543` for the name-level allow/deny). The absence is upstream of both: the Plane MCP server is a **remote** streamable-http descriptor (`daemon/mcp/builtin_servers/plane.py`) whose availability gate (`plane.py:291-298`) requires `PLANE_MCP_ENABLED` + `PLANE_MCP_URL` + `PLANE_MCP_API_KEY`. When the gate fails, `InstanceManager._bootstrap_builtin_servers` **skips creating the `mcp_servers` row** (`daemon/manager.py:3073-3087`), the preload never sees a server named `plane`, and the agent receives nothing — with no warning anywhere. The worktree `.env` carries no `PLANE_*` variables at all, so this is the exact state the test commission observed.

**The remote surface is fully capable, and I proved it.** I captured the live `tools/list` inventory (read-only; `initialize` → `notifications/initialized` → `tools/list` only) and it is **177 tools** with complete write coverage of the Task-3 use case: work-item CRUD (`create/update/delete/retrieve/list/search/count_work_item`), **5 comment verbs** (`create/list/update/delete/retrieve_work_item_comment`), and **10 cycle verbs** (`create/update/delete/list/retrieve/complete/manage_cycle_work_items/transfer_cycle_work_items/list_cycle_work_items/manage_cycle_archive`). `create_work_item` exposes an `is_draft` parameter, so the DRAFT issue the Task-3 verification needs is directly expressible. The leader's Path-B trigger ("if the remote surface turns out write-poor for work items… Path B becomes primary") is therefore **discharged**: the surface is not write-poor. Separately, the inventory settles the charter's vocabulary question conclusively — there are **zero** `*_issue` verbs; the entity is `work_item`, and upstream's own `serverInfo.instructions` says so.

**The enforcement question turned out to have a landmine that the digests did not surface.** Digest B proposes a single config-resolution-time resolver keyed on the `ENSEMBLE_SELF_ENV` ladder, described as a "ready-made oracle". I exercised the **real** resolver (`daemon/tools/upgrade_tools.py::_self_env_marker()`) against each host context and it **returns `'live'` for the dev repo checkout** — with `POSTGRES_DB=ensemble_dev` and no explicit marker. Root cause: `_auto_derive_env()` (`upgrade_tools.py:440-561`) reads the **sibling live install's** `.env` (`~/agents-ensemble/.env`, `POSTGRES_DB=ensemble_prod`) during its install-dir-topology signal and returns `"live"` **before** the dev-shape signal (`db_hint == "ensemble_dev"` → `"dev"`, `:557-560`) can run. The function's own docstring (`:470-476`) claims the DB cross-check is "the reason a leftover .env from a prior install cannot falsely label a dev checkout as live" — on this two-install host the implementation does exactly that. For a **pollution guard** this is the worst possible failure direction: it is **fail-open**, and it would grant the dev checkout the *production* workspace. **This analysis therefore changes the enforcement design** (§ Workspace enforcement design below).

**Two credential facts, also not in the digests.** The live-install `PLANE_MCP_API_KEY` and the demo-install `PLANE_MCP_API_KEY` are the **same 42-char token** (sha256 fingerprint identical) and **both are rejected by the server with HTTP 401 `invalid_token`**. The dev-context token is **different and works** (HTTP 200, 177 tools) — and it is also the value of `PLANE_API_KEY` there, so one credential currently serves both the REST and MCP lanes in dev. Consequence: **the only armed Plane MCP environment on this host is the dev context**, which is also the only one pointing at `e2e` and the only one carrying a REST key. That makes dev the *only* environment where Task 3's verification can execute — and it means the live Plane lane cannot come up until the user re-mints a key, regardless of which implementation path is chosen.

---

## Architecture

### Current Patterns

- **Remote-MCP-as-tool-provider with a builtin descriptor** — `daemon/mcp/builtin_servers/base.py` defines a server *definition* (name, schema, `build_config`, `is_available`, `tool_name_prefix`); `daemon/mcp/builtin_servers/__init__.py:74-81` registers the three builtins. The plane definition overrides `tool_name_prefix` so the daemon exposes `plane_*` rather than `mcp_plane_*` (`daemon/mcp/tool_adapter.py:134-197` maps the prefix to a privilege-stamp category; `:487-511` applies it).
- **Two independent permission gates** — a *schema-level* strip (`mcp_full_access` → `_get_read_only_tools`, `daemon/services/mcp_service.py:1243-1313`, consults the builtin registry at `:1283-1292`) and a *name-level* allow/deny (`resolve_tool_filter`, `daemon/tools/instance.py:403-543`). Both must admit a tool.
- **Dynamic-toolset expansion** — `DYNAMIC_TOOL_PREFIXES` (`daemon/tools/_tool_registry.py:279-281`) lists runtime-only namespaces; `daemon/tools/instance.py:499-511` expands them from the runtime tool-name universe. The `mcp_` lane above it (`:466-477`) carries an `owned_elsewhere` ownership guard; the dynamic-prefix loop does not.
- **Optional-integration kill-switches** — `PLANE_MCP_ENABLED` (`plane.py:82`) and `PLANE_SYNC_ENABLED` (`daemon/services/plane_sync_service.py:157-162`), independent, default ON, each with a typed refusal vocabulary (`plane_sync_service.py:374-381` `unavailable_reason()`).
- **Presence-only availability gates + a silent skip** — the builtin `is_available()` contract is "are the required env vars *present*", never "is the remote reachable" or "is the credential valid". The bootstrap's response to `False` is a single `logger.info` (`manager.py:3076-3087`).
- **Env-preserving schema-drift heal** — `daemon/manager.py:312-353` `_heal_config_preserving_env()` rebuilds the template config and merges the row's `env` sub-dict over it; the `headers` axis is template-derived and therefore rebuilt from the *current* env on every schema-version bump.
- **Workspace-as-env-structural-fact** — the workspace is baked into the REST URL path (`daemon/clients/plane_http_client.py:296-308`) and the MCP header (`plane.py:345`), and is never a tool parameter. One env var drives both lanes (`plane_http_client.py:303`, `plane.py:345`).
- **Shell-layer policy enforcement (already landed)** — `scripts/deploy.sh:257-276` sed-pins `PLANE_MCP_WORKSPACE_SLUG=e2e` and greps-asserts it before `mv`; `scripts/run_tests_scrubbed.sh:52` unsets the slug + base URL and `:78` leak-gates them. The doctrine prose lives in `.env.example:185-190`. **There is no daemon-layer structural refusal today.**
- **`ENSEMBLE_SELF_ENV` ladder** — explicit enum `{dev,demo,live,sandbox}` (`daemon/tools/upgrade_tools.py:187`, `:262-279`) > explicit opt-out (`:282-294`) > multi-signal auto-derive (`:440-561`) > `None` = unresolved (fail-closed).

### Module Boundaries

```
   ENV (install .env / launcher)                       agent meta.json
   ├── ENSEMBLE_SELF_ENV ──────────┐                    ├── mcp_full_access ["plane"]
   ├── PLANE_MCP_URL/KEY/SLUG ─────┼──> plane.py       ├── tools.allow  "plane"
   ├── PLANE_BASE_URL/API_KEY ─────┘    builtin def        │
   │                                    │                │
   │            ┌───────────────────────┴────────┐       │
   │            │ daemon/manager.py              │       │
   │            │  _bootstrap_builtin_servers    │       │
   │            │   is_available() ──► skip ──┐  │       │
   │            │   build_config() ──► row   │  │       │
   │            └─────────────────────────────┼──┘       │
   │                                          │          │
   ▼                                          ▼          ▼
 mcp_servers row (config JSONB:          mcp_service    tools/instance.py
   url + headers{Authorization,          preload +      resolve_tool_filter
   x-workspace-slug})                     read-only     ├── mcp_ lane (owned_elsewhere ✔)
                                           strip        └── DYNAMIC_TOOL_PREFIXES sweep (no guard ✘)
                                                          │
                                                          ▼
                                                    plane_* tools (177, runtime-discovered)
```

`PlaneHttpClient` (REST) and `PlaneServerDefinition` (MCP) are **siblings**, not a stack: they share only the `PLANE_MCP_WORKSPACE_SLUG` env var and the `PLANE_BASE_URL` host. `plane_sync_service.py` consumes only the REST client and only the `/projects/` surface.

### Architecture Diagram

```mermaid
flowchart TB
    subgraph env["Process env (single source of truth)"]
      SE[ENSEMBLE_SELF_ENV]
      SLUG[PLANE_MCP_WORKSPACE_SLUG]
      MKEY[PLANE_MCP_API_KEY]
      RKEY[PLANE_API_KEY]
      RURL[PLANE_BASE_URL]
    end

    SE --> LADDER[upgrade_tools._self_env_marker]
    SLUG --> PLANE_WS["plane_workspace.resolve_plane_workspace()  ← NEW"]
    LADDER -.->|"TRUST PROBLEM: auto-derive says 'live' for dev"| PLANE_WS

    PLANE_WS -->|ALLOWED| MCP_OK[plane.py get_base_config<br/>headers{x-workspace-slug}]
    PLANE_WS -->|REFUSED| MCP_NO[plane.py is_available False<br/>→ NO ROW → NO TOOLS]

    MKEY --> MCP_OK
    RKEY --> REST_OK[plane_http_client._rest_base_url]
    RURL --> REST_OK
    SLUG -.->|"raw env bypass"| MCP_OK
    SLUG -.->|"raw env bypass"| REST_OK

    MCP_OK --> ROW[(mcp_servers row<br/>config.headers)]
    ROW --> PRELOAD[mcp_service preload]
    PRELOAD --> SWEEP["instance.py DYNAMIC_TOOL_PREFIXES sweep<br/>⚠ no ownership guard"]
    SWEEP --> AGENT[PM toolset]
    REST_OK --> SYNC[plane_sync_service / plane_sync_project]

    STYLE["upgrade_tools._auto_derive_env<br/>⚠ reads SIBLING install .env"] -.->|auto-derive| LADDER
```

---

## Integration Points

| # | Integration | Type | Contract | Auth | Failure Mode | File:Line |
|---|-------------|------|----------|------|--------------|-----------|
| 1 | Remote Plane MCP server | async HTTP (streamable-http) | MCP JSON-RPC; `tools/list` returns 177 tool schemas | `Authorization: Bearer <PLANE_MCP_API_KEY>` + `x-workspace-slug` header | **Observed: HTTP 401 `invalid_token`** for the live+demo token. Presence-only gate ⇒ row created ⇒ sessions fail at connect. | `daemon/mcp/builtin_servers/plane.py:342-354`; probe evidence §7 of the digest |
| 2 | Plane REST API (sync lane) | async HTTP (`httpx`) | JSON; `/api/v1/workspaces/{slug}/projects/` | `X-Api-Key: <PLANE_API_KEY>` + `x-workspace-slug` header | `_rest_base_url()` returns `None` when base/slug missing → client absent → `plane_sync_project` no-ops. **No slug-policy validation.** | `daemon/clients/plane_http_client.py:296-308`, `:388-398`, `:364-374` |
| 3 | `mcp_servers` DB row | sync (SQLModel, PG primary) | JSONB `config` = `{transport, url, headers{...}}` | none (daemon-internal) | Persisted plaintext bearer; **redaction gap** — `redact_secrets` scrubs `env` + `url` userinfo only, not `headers`. | `daemon/manager.py:3089-3132`; `daemon/routers/mcp_servers.py:185-253` |
| 4 | Builtin registry → agent meta | in-process | `mcp_full_access: list[str]` validated against the builtin **registry** (a definition exists) | n/a | **Validated against definitions, never against rows.** A defined-but-unavailable server passes validation ⇒ invisible failure. | `daemon/registry.py:411`, `:710`, `:771`, `:1419-1457` |
| 5 | Runtime tool-name universe → filter | in-process | `all_tool_names` + `tool_categories` | n/a | Prefix sweep has no ownership guard ⇒ `plane_sync_project` (category `plane_sync`) lands in category `plane`. | `daemon/tools/instance.py:466-477` (guard), `:499-511` (no guard); `daemon/tools/plane_sync.py:210-212` |
| 6 | Environment classification | in-process | `ENSEMBLE_SELF_ENV` marker → `dev\|demo\|live\|sandbox\|None` | n/a | **Auto-derive reads a sibling install's `.env`** ⇒ dev checkout classifies as `live` (fail-open). | `daemon/tools/upgrade_tools.py:187`, `:262-294`, `:440-561` |
| 7 | Deployment scripts → `.env` | shell | `sed` pin + `grep` assert on the slug | file mode 0600 | Shell-layer only; a hand-written `.env` bypasses it entirely. | `scripts/deploy.sh:257-276`; `scripts/run_tests_scrubbed.sh:52`, `:78` |

### Integration Details

**Integration 1: Remote Plane MCP server.** Protocol: MCP over streamable-http; `initialize` → `notifications/initialized` → `tools/list`. Auth: bearer token + workspace header, both assembled in `get_base_config()` (`plane.py:342-354`). Error handling: the daemon has **no** MCP-layer fallback for the plane server — a failed connect surfaces as a session error, not a typed integration refusal. Observability: the bootstrap logs one `INFO` on skip; a **live** connect failure is not surfaced by the bootstrap at all. Known issues: (i) credential validity is not part of the availability gate; (ii) the tool catalog is upstream-owned and moves (server `v3.2.0` today); (iii) `tools/list` is workspace-agnostic, so a successful listing proves **nothing** about a workspace's reachability.

**Integration 2: Plane REST API.** Protocol: REST/JSON over `httpx`; every method creates and closes its own client (`plane_http_client.py:326-362`) to avoid event-loop binding. Auth: `X-Api-Key` — the client **deliberately refuses** to fall back to `PLANE_MCP_API_KEY` (`:365-372`, separation-of-concerns). Error handling: a module-level circuit breaker. Observability: `plane_sync_service.unavailable_reason()` (`:374-381`). Known issues: `PLANE_API_KEY` is **absent** on both installs (live and demo) ⇒ the REST lane is inert there; only the dev checkout carries one.

**Integration 3: the `mcp_servers` row.** The row is the durable, operator-visible representation of the remote server — and it is where two of this lane's risks live: the plaintext bearer in `config.headers`, and the persisted workspace slug. The schema-version heal is the only mechanism that re-points it, and it re-points the slug correctly **because the slug lives in the template-derived `headers` axis** (`manager.py:312-353`; verified).

**Integration 6: environment classification.** The ladder is correct for the two **installs** (both carry an explicit marker: `~/agents-ensemble` ⇒ `live`, `~/agents-ensemble-demo` ⇒ `demo`). It is wrong for the **dev repo checkout** (no marker ⇒ auto-derive ⇒ `'live'`). This is the load-bearing defect behind the design change below.

---

## Trade-offs

### Alternatives Considered

1. **Path A — register the remote Plane MCP server per environment.** Config-only: set `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` + `PLANE_MCP_WORKSPACE_SLUG` per env; the builtin row is created at boot; ~177 remote tools reach the agent; write verbs reach PM via `mcp_full_access`. **Zero shipped code for the tool surface.**
2. **Path B — first-party issue tools.** New `daemon/tools/plane_issue.py` module + `@register_tool_category` + a factory in `create_instance_tools` (`instance.py ~:6286-6342`) + a `CATEGORY_MODULES` row + a `KNOWN_TOOL_NAMES` row in `tests/unit/tools/test_frozen_tool_name_discovery.py` + **new** PlaneHttpClient REST methods for work items / cycles / comments + a PM `meta.json` allow grant.
3. **Hybrid — A as the surface, B as a narrow fallback** for any verb A lacks.

### Write-verb reality check (mandatory input to this matrix)

Captured live, 177 tools, `tools/list` only — full detail in `tools-list-inventory.md`:

| Capability group | Verdict | Verbs |
|---|---|---|
| Issue / work-item CRUD | ✅ **complete** | `create_work_item`, `update_work_item`, `delete_work_item`, `retrieve_work_item`, `retrieve_work_item_by_identifier`, `list_work_items`, `search_work_items`, `count_work_items` |
| Comments | ✅ **complete (5/5)** | `create_work_item_comment`, `list_work_item_comments`, `update_work_item_comment`, `delete_work_item_comment`, `retrieve_work_item_comment` |
| Cycles | ✅ **complete (10/10)** | `create_cycle`, `update_cycle`, `delete_cycle`, `list_cycles`, `retrieve_cycle`, `complete_cycle`, `manage_cycle_work_items`, `transfer_cycle_work_items`, `list_cycle_work_items`, `manage_cycle_archive` |
| DRAFT creation | ✅ `create_work_item(is_draft=…)` | `is_draft` is an explicit parameter |
| Write-capability census | **116 of 177 tools (65 %) are write-capable** | only 61 are read-only |
| `*_issue` verbs | ❌ **zero exist** | the entity is `work_item`; upstream's own `instructions` say "an epic is a work item whose type is named 'Epic'" |
| Prefix | raw names carry **no** `plane_` | the daemon adds it via `tool_name_prefix` (`plane.py:143+`, `tool_adapter.py:487-511`) |

**⇒ Leader lean #1's Path-B trigger does NOT fire.** The remote surface is not write-poor; there is no gap for a hybrid fallback to cover.

### Comparison (5-axis matrix)

| Criterion | Path A (remote MCP) | Path B (first-party REST tools) | Hybrid | Winner |
|-----------|----------------------|---------------------------------|--------|--------|
| **Complexity** | **Zero** new code for the surface. The Task-2 resolver, Task-3 observability fix, and Task-4 sweep guard are **common to all three paths** (they are orthogonal to A-vs-B) — so A's *marginal* complexity is ≈ 0. | New module + factory wiring + `CATEGORY_MODULES` row + `KNOWN_TOOL_NAMES` row + **a Plane REST contract for work items / cycles / comments that does not exist anywhere in-repo** (`PlaneHttpClient` has exactly 4 project methods — `create_project:563`, `update_project:606`, `get_project:652`, `list_projects:677`) + PM grant + new test packs. | A + B. | **A** — decisively. B must first *invent* a REST contract with zero in-repo precedent. |
| **Scalability** | 177 tool schemas enter the agent's per-turn prompt (order 10–18 k tokens of schema) — a real prompt/latency cost and a larger tool-choice space. Remote does the work; one server per env; no daemon-side REST concurrency. | N small first-party schemas (≈ 6–10 tools) — materially smaller prompt, better tool-selection accuracy. But every call is a daemon-side REST round-trip on the daemon's event loop (the very class of hot-path work the v0.27.14 lane had to offload). | Worst on *both* axes: A's prompt surface **plus** B's daemon-side REST traffic. | **B** on prompt surface; **A** on infrastructure. B's edge is real but is a *tunable*, not a blocker (see Option A′ below). |
| **Maintainability** | Zero code to maintain — but the verb catalog is **upstream-owned and moving** (`Plane MCP Server (header-http)` v3.2.0 today). A rename breaks PM's charter scripts **silently**. Proven drift already: the charter says `plane_create_issue`; the real verb is `plane_create_work_item`, and no `*_issue` verb exists at all. | The daemon **owns** the contract ⇒ versioned, pinned, testable, stable agent-facing names (`plane_issue_create`) immune to upstream renames. But it must then track Plane's REST API drift itself. | Two contracts to maintain instead of one. | **B** — this is B's strongest axis and A's genuine weakness. |
| **Risk** | (i) **Remote dependency + credential validity**: the live token is *already* dead (401 `invalid_token`) — and the gate is presence-only, so a dead key still produces a row. (ii) 177-tool surface ⇒ large blast radius for a mis-selected tool call. (iii) upstream schema drift mid-turn. (iv) charter drift **proven**. | (i) The REST contract must be authored correctly with **no in-repo precedent** — the risk sits in the *encoding*, not the plumbing. (ii) Plane REST needs a **separate credential** (`X-Api-Key`); the client deliberately refuses to borrow the MCP token — and **neither the live nor the demo install has `PLANE_API_KEY`** ⇒ a second prod credential must be minted. (iii) new shipped code ⇒ new failure modes and a new test surface. | Union of both. | **A** — A's dominant risk is *operational* (a credential to rotate, user-owned). B's risks are *structural* (a contract to author, a second credential to mint, new code to own). |
| **Cost** | Zero code; **zero new keys on live** — the MCP key already exists per env (though the live one must be re-minted). Runtime cost = 177 tool schemas in the prompt. | New shipped code + a **second credential per env** (live + demo both need a REST key minted) + ongoing contract-maintenance cost. | Highest. | **A** — decisively, on both code and credential count. |

### Recommendation

**Pick: Path A** — register the remote Plane MCP server per environment, exactly as the leader's primary lean proposes. **The write-verb reality check discharges the lean's own Path-B trigger**, and A wins three of five axes outright (Complexity, Risk, Cost) while its two losing axes are *tunable* rather than structural.

**Reasoning.** The commission asked for a write-verb reality check "against the FULL inventory"; the full inventory shows **complete** write coverage of the Task-3 use case (`create_work_item` with `is_draft`, plus 5 comment verbs and 10 cycle verbs) and **116 of 177 tools are write-capable**. There is no gap for Path B or a hybrid to fill, so B's only advantage — a daemon-owned, stable agent-facing contract — is bought at the price of inventing a REST contract with zero in-repo precedent *and* minting a second credential on production, where neither install even has a REST key today. A's weaknesses are real but bounded: the upstream-catalog drift risk is mitigated by charter reconciliation (which must happen anyway — the current charter names verbs that do not exist) and by a re-capture gate at implement time; the prompt-surface cost is a measurable, tunable knob (Option A′); and the availability risk is **already realised** — the live token is dead today — so it must be fixed under *any* path, not because of Path A.

**Option A′ (recommended refinement, not a separate path).** If measurement shows the 177-tool surface degrades PM's tool-selection accuracy, narrow PM's grant from the whole `"plane"` category to an explicit allow-list of the verb names PM's charter actually uses (the filter matches literal tool names inside a populated dynamic category). This is a **config-level** change to `meta.json` — no code — and it preserves the upstream-owned contract. **Measure before deciding:** PM tool-call error rate and task success rate, 177-tools vs narrowed.

**Assumptions** (each one, if false, re-opens the decision):

- A1. The upstream server keeps exposing work-item / comment / cycle write verbs. *(Re-captured at implement time — gate (i).)*
- A2. The user re-mints a working Plane MCP credential for live and demo. *(User-owned; without it Path A yields a row and failing sessions, i.e. no functional gain on live.)*
- A3. The 177-tool prompt surface is acceptable for PM, or A′ is applied. *(Measurable.)*
- A4. The daemon can keep tolerating an upstream-owned verb catalog, i.e. upstream drift is tolerated by charter phrasing + the re-capture gate rather than by forking the surface.

**Reversibility.** Very high for A: the change is env + `meta.json` (+ the common resolver/observability/sweep fixes, which are independently valuable). Dropping back to "no Plane tools" is a matter of unsetting the env vars. Path B, by contrast, would be a **one-way door** (new shipped module + new credential class + new contract).

### Flip conditions (re-open Path B / hybrid if…)

| # | Condition | Why it flips the decision |
|---|-----------|--------------------------|
| FC1 | The implement-time re-capture (gate i) shows a verb PM's charter depends on is **absent** | the leader's own trigger, now with a mechanical detector |
| FC2 | Upstream renames/removes a PM-critical verb **twice within one release cycle** and ships no compatibility alias | drift rate exceeds what charter reconciliation can absorb |
| FC3 | Measured PM tool-selection accuracy or task success rate degrades materially with the 177-tool surface **and** A′ does not recover it | A′ is the first remedy; B is the second |
| FC4 | Remote session failures exceed a stated threshold (e.g. > 2 % of attempts over 24 h) *after* a valid credential is in place | availability becomes the binding constraint on PM's core workflow |
| FC5 | The upstream auth model can no longer be expressed by `Authorization: Bearer` + `x-workspace-slug` (e.g. per-tool scopes) | forces a fork rather than a registration |
| FC6 | Security review requires the daemon to own the write path (no third-party credential in a DB row's `headers`; least-privilege auditability) | an institutional constraint, not a technical one — note this overlaps the adjacent defect in §Adjacent defects |

---

## Scalability

### Growth Assumptions

- **Users / agents:** PM is one agent today; the plane surface is not granted to any other agent (`mcp_full_access` is PM-only). Growth in *agents* does not multiply the remote surface.
- **Tools per agent:** 177 per PM instance — a fixed per-agent constant, **not** a function of workspace size or Plane data volume. Growth is in *Plane-side data*, which does not change the tool catalog.
- **Plane-side data:** the surface already exposes `list_work_items` with `pql` + pagination (`cursor`, `per_page`) — the catalog does not grow with data.
- **Environments:** the guard scales linearly with install count (one resolver, one env var per install).

### Current Bottlenecks

| # | Bottleneck | Threshold | File:Line | Impact |
|---|------------|-----------|-----------|--------|
| 1 | **Upstream catalog drift** | one upstream rename | `plane.py:143+` (prefix), upstream server | PM's charter silently names non-existent verbs — **already realised** |
| 2 | **Prompt surface** | 177 schemas/turn | `tool_adapter.py:487-511` | prompt tokens, latency, tool-choice accuracy |
| 3 | **Credential validity is not gated** | one expired token | `plane.py:296-298` | presence-only gate ⇒ row created ⇒ every session 401 — **already realised on live + demo** |
| 4 | **Prefix sweep fan-out** | one static tool whose name starts with a dynamic prefix | `instance.py:499-511` | a tool silently changes category between two server states |
| 5 | **Env classification** | one two-install host | `upgrade_tools.py:513-556` | dev resolves as `live` ⇒ the prod workspace is reachable from dev — **already realised** |
| 6 | **Silent skip** | any missing env var | `manager.py:3076-3087` | the original failure was invisible — **already realised** |

### Scaling Characteristics

- **Vertical vs horizontal:** N/A for the surface (one remote server per env); the daemon's own limits are unchanged.
- **Stateless vs stateful:** the agent's toolset is runtime-derived; the durable state is the `mcp_servers` row (one row per env).
- **Sync vs async:** the remote lane is async (streamable-http); the REST lane is async (`httpx`). Both are off the daemon's hot paths.
- **Scaling cliffs:** (i) beyond ~200 tool schemas the prompt/tool-choice cost becomes the dominant constraint — A′ is the cliff-crossing mechanism; (ii) if the upstream server's per-connection cost grows, one connection per session scales linearly.

---

## Workspace/env enforcement design (Task 2)

### The finding that changes the design

Digest B proposes keying the guard on the `ENSEMBLE_SELF_ENV` ladder, described as a "ready-made oracle". I exercised the real resolver and it is **not** trustworthy in the direction this guard cares about:

| Context | `ENSEMBLE_SELF_ENV` | `POSTGRES_DB` | `_self_env_marker()` | source | correct? |
|---|---|---|---|---|---|
| live install `~/agents-ensemble` | `live` (explicit) | `ensemble_prod` | `'live'` | `explicit` | ✅ |
| demo install `~/agents-ensemble-demo` | `demo` (explicit) | `ensemble_demo` | `'demo'` | `explicit` | ✅ |
| **dev repo checkout `~/ensemble-src`** | *(absent)* | `ensemble_dev` | **`'live'`** | `auto` | ❌ **WRONG** |
| dev checkout, no db var | *(absent)* | — | **`'live'`** | `auto` | ❌ **WRONG** |
| sandbox (explicit) | `sandbox` | — | `'sandbox'` | `explicit` | ✅ |

Root cause (verified by reading `daemon/tools/upgrade_tools.py:440-561`): the install-dir-topology signal iterates the **sibling** installs under the current `HOME` (`_install_root_candidates`, `:395-418`) and returns `"live"` when the sibling's `.env` carries `POSTGRES_DB=ensemble_prod` — **before** the dev-shape signal (`db_hint == "ensemble_dev"` → `"dev"`, `:557-560`) can run. The daemon's own install-dir/cwd is never consulted in the non-Layer-1 HOME shape. The function's own docstring (`:470-476`) asserts the opposite intent.

**Why this is disqualifying for a pollution guard:** the auto-derive's error direction is *permissive*. A guard that trusts it would let a dev checkout target `nea` — the exact pollution class the user directive exists to prevent — and would additionally **never fire** the "slug unset ⇒ refuse" rule on a dev checkout, because dev would classify as `live` and live is exempt.

### Recommended resolver shape

**`resolve_plane_workspace()` — a dedicated module, not a raising config validator.**

```
daemon/plane_workspace.py          (NEW — import-cheap, no daemon/manager coupling)
  ├─ dataclass PlaneWorkspaceResolution
  │     env_label: str | None      # 'live' | 'dev' | 'demo' | 'sandbox' | None
  │     env_source: str            # 'explicit-marker' | 'own-postgres-db' | 'unclassified'
  │     slug: str | None           # effective slug, or None when refused
  │     allowed: bool
  │     reason: str | None         # typed refusal reason
  └─ resolve_plane_workspace() -> PlaneWorkspaceResolution
```

**Live determination — D-1 (the tightening):** the production workspace is permitted **only** when the environment is *explicitly* production:

```
live  ⟸  (explicit ENSEMBLE_SELF_ENV marker == "live")
       OR (no explicit marker AND the daemon's OWN POSTGRES_DB == "ensemble_prod")
everything else  ⟸  non-live
```

- The explicit marker wins (house precedence, `upgrade_tools.py:262-279`).
- The `own POSTGRES_DB == "ensemble_prod"` fallback is a *self*-signal (the daemon's own connection target), **not** a sibling-file read — so it does not mis-classify the dev checkout (`ensemble_dev`) and does not reproduce the auto-derive defect.
- An **auto-derived** `'live'` is **not** trusted. It falls into the non-live bucket ⇒ dev/demo/sandbox ⇒ `e2e` only.
- `None` (unresolved) ⇒ non-live ⇒ fail-closed ⇒ `e2e` only.

**Why not simply fix the shared oracle?** Fixing `_auto_derive_env` so a repo checkout derives `dev` is the *better long-term* fix and benefits every `self_env` consumer (including the promote gate). But that resolver is **security-relevant for the 3-factor promote ceremony**, it is consumed far outside the Plane lane, and the blast radius of changing it is a different order of magnitude. **Recommendation: implement D-1 lane-locally (zero blast radius on the promote gate) and route the shared-oracle fix to its own commission.** (Alternative R-B, for the architect to weigh: fix `_auto_derive_env` and have the Plane resolver call the shared ladder. My recommendation stands at R-A + a follow-up, because R-B alone would leave the Plane guard's fail-open semantics dependent on a resolver whose behaviour is shared with unrelated security paths.)

**Policy table (final):**

| Env classification | Permitted slug | Refusal reason (typed) |
|---|---|---|
| live (explicit `live`, or own db == `ensemble_prod` with no marker) | `nea`; unset ⇒ account-default (byte-identical to today) | `nea` refused: `"workspace 'e2e' is not permitted in the production environment"` |
| non-live (dev / demo / sandbox / unclassified) | `e2e` | `"workspace 'nea' is production-only; this environment resolves to <env>"` / `"PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'"` / `"workspace 'X' is not permitted in a non-production environment; expected 'e2e'"` |
| unclassified (no marker, own db unknown) | `e2e` | `"environment could not be classified as production; refusing to target the 'nea' workspace"` |

**Live continuity.** The live install carries `ENSEMBLE_SELF_ENV=live` **and** `POSTGRES_DB=ensemble_prod` ⇒ live ⇒ `nea` permitted ⇒ **byte-identical to today**, including the unset-slug case. (Verified: `~/agents-ensemble/.env` ⇒ `ENSEMBLE_SELF_ENV=live`, `PLANE_MCP_WORKSPACE_SLUG=nea`, `PLANE_API_KEY` absent.) The demo install carries `ENSEMBLE_SELF_ENV=demo` + slug `e2e` ⇒ already compliant ⇒ **byte-identical**. The dev checkout carries no marker + slug `e2e` ⇒ under D-1 it is non-live ⇒ `e2e` permitted ⇒ **byte-identical**. **The guard changes no environment's current behaviour — it removes the ability to mis-configure them.**

**Kill-switch stance: NONE.** The guard **is** the feature; a switch that disables it re-opens the pollution class the user closed. If the architect insists on one: it must be **default-ON** and must gate **only the refusal**, never the resolution — the effective slug is always computed and always logged, so the diagnostic surface survives a switch. (Leader lean #2 is adopted verbatim.)

### E1–E5 adjudication, and the pollution-path map

| Option | Verdict | Placement / mechanism | Note |
|---|---|---|---|
| **E1** — one config-resolution-time resolver | ✅ **ADOPT (primary)** | `daemon/plane_workspace.py::resolve_plane_workspace()`, consumed by E2 + E3 | the single decision point; every caller asks it, nobody re-derives the policy |
| **E2** — REST chokepoints | ✅ **ADOPT** | `plane_http_client.py:296-308` (`_rest_base_url` → return `None` when refused), `:364-374` (`is_available` → False when refused), `:356-360` (client must use the **resolved** slug, never the raw env), `:388-398` (`_headers` → resolved slug) | closes **P1** |
| **E3** — MCP chokepoints | ✅ **ADOPT** | `plane.py:263-298` (`is_available` gains a resolution gate — refusal ⇒ no row ⇒ no tools ⇒ existing skip machinery applies), `plane.py:325-354` (`get_base_config` must emit the **resolved** slug) | closes **P2** and **P3**; the row never materialises, so no agent ever sees a phantom tool |
| **E4** — stale-row heal | ✅ **ADOPT** | bump plane's `schema_version` `"2"` → `"3"` (`plane.py:127-141`); the heal rebuilds `headers` from the current env (`manager.py:312-353`, verified: only the `env` axis is preserved — `headers` are template-derived) | closes **P4**. **Correction to Digest B:** E4 works as stated — no connect-time header validation is *required*; it remains an optional belt-and-braces addition at `daemon/mcp/config.py:245-277` / `connection_manager.py:173` |
| **E5** — the tool layer | ❌ **REJECT** | — | the workspace is never a tool parameter; a tool-layer check duplicates E1–E3 and will drift from them |

**Pollution-path map:**

| Path | Scenario | Closed by | Mechanism |
|---|---|---|---|
| **P1** | non-prod REST with slug `nea` | E1 + E2 | `_rest_base_url()` returns `None` ⇒ `is_available()` False ⇒ no REST client ⇒ sync lane inert |
| **P2** | MCP with slug **unset** ⇒ account-default (prod) | E1 + E3 | `is_available()` refuses ⇒ **no row** ⇒ no tools (the existing `manager.py:3073-3087` skip machinery) — and the skip becomes **LOUD** (§Loud-skip observability) |
| **P3** | non-prod MCP with slug `nea` | E1 + E3 | same as P2 — the row never materialises |
| **P4** | stale persisted row keeps the prod slug after the env is corrected | E4 (+ optional connect-time validation) | schema-version bump re-materialises the row's `headers` from the current env |

**On the `LiveViewsRootConfig` precedent (`daemon/config.py:3178`, validator at `:3262-3271`).** The precedent's *value* is real — a single declarative shape with fail-loud validation, on the rationale that "a config that boots with a broken root is operationally worse than one that refuses to boot". But its *consequence* must **not** be copied here: a raising validator on a `BaseSettings` field would abort the **entire daemon boot** when Plane is misconfigured. Plane is an **optional** integration; taking the whole daemon down because a third-party workspace is unset is strictly worse than skipping Plane. **Recommendation:** if the architect wants a `PlaneWorkspaceConfig` section, it must be a **non-raising** section in `daemon/config.py` carrying only the raw inputs (slug, enforce flag), with the resolver producing the typed refusal — i.e. borrow the *shape*, not the *failure mode*. The `LiveViewsRootConfig` rationale is quoted in §References.

---

## Loud-skip observability (leader lean #3)

**Today.** `daemon/manager.py:3073-3087` — on `is_available() == False` the bootstrap logs, at **INFO**, `"Builtin '{name}' skipped — missing environment configuration (see server definition docs)"`. It names neither the **agents** that expected the server, nor the **env vars** that are missing, and it is at a level the boot summary does not surface. That is why the original failure was invisible.

**Recommended fix (three layers, all cheap):**

1. **Make the builtin able to say *why*.** Add to `daemon/mcp/builtin_servers/base.py` a classmethod `unavailable_reason() -> str | None` (and/or `missing_env_vars() -> list[str]`), overridden by `plane.py` to name the missing subset of `PLANE_MCP_URL` / `PLANE_MCP_API_KEY` plus the resolver's typed refusal. This mirrors the house precedent `plane_sync_service.unavailable_reason()` (`daemon/services/plane_sync_service.py:374-381`) — a typed reason string, not a log phrase.
2. **Raise the skip log to WARNING and name the agents.** In the bootstrap skip branch, emit a `WARNING` that names the server, the **agents whose `mcp_full_access` references it**, and the **missing env vars**. The agent lookup is a registry query: `daemon/registry.py:411` (field), `:710`/`:771` (parse), `:1419-1457` (validation against the builtin registry).
3. **Fold in the registry-validation gap (cheap — recommended).** `mcp_full_access` is validated against the builtin **registry** (does a *definition* exist?) but **never against an actual `mcp_servers` row** (did the server actually *register*?). Add one post-bootstrap cross-check: for every agent's `mcp_full_access` entry, if no **registered** server of that name exists, log a WARNING naming the agent and the server. This single check covers **both** failure shapes — the skip case and the row-absent case — and it is exactly where the original incident was invisible. It is a handful of lines and one registry query.

**Assessment.** Layers 1–2 are small and surgical. Layer 3 is the one that would actually have caught this incident *and* its variants; it is also the natural home for a future "declared capability vs delivered capability" report. Cost: low. Risk: a WARNING that fires in environments where the plane integration is intentionally off (`PLANE_MCP_ENABLED=false`) — mitigate by distinguishing "deliberately disabled" from "unexpectedly unavailable" via the typed reason from layer 1 (the kill-switch already has its own one-line notice at `plane.py:301-323`).

---

## Prefix-sweep ownership guard (leader lean #4)

**The defect.** `DYNAMIC_TOOL_PREFIXES = frozenset({"plane_"})` (`daemon/tools/_tool_registry.py:279-281`). The expansion loop at `daemon/tools/instance.py:499-511` populates `tool_categories[prefix.removesuffix("_")]` from every runtime tool name carrying the prefix — with **no ownership check**. `plane_sync_project` is registered under category `"plane_sync"` (`daemon/tools/plane_sync.py:210-212`) but its **name** starts with `plane_`, so when the category `"plane"` is empty or absent (i.e. the MCP server did not register), the sweep sweeps `plane_sync_project` into category `"plane"` — and PM's `tools.allow` contains `"plane"` (`agents/project-manager/meta.json`), so **PM receives `plane_sync_project`**.

**Why the two server states differ.** With the server registered, the MCP-created `plane_*` tools are stamped with category `"plane"` (`tool_adapter.py:134-197`), so `tool_categories.get("plane")` is **non-empty** and the sweep's `if tool_categories.get(category_key): continue` short-circuits — `plane_sync_project` stays in `"plane_sync"`. With the server absent, `"plane"` is empty ⇒ the sweep fires ⇒ the collision. **Same `meta.json`, two different toolsets** — exactly as Digest A describes, and the reason the test commission saw only `plane_sync_project`.

**Recommended fix.** Mirror the `mcp_` lane's ownership pattern (`instance.py:466-477`) onto the dynamic-prefix loop: compute `owned_elsewhere` as the union of all categories **except the one being populated**, and exclude those names from the prefix sweep. The exclusion must be computed **per `category_key`** (the `mcp_` lane computes one set excluding only `"mcp"`; the dynamic loop must exclude the target category itself, or it would drop every legitimately-discovered `plane_*` tool).

```
for prefix in sorted(DYNAMIC_TOOL_PREFIXES):
    ...
    category_key = prefix.removesuffix("_")
    if tool_categories.get(category_key): continue
    owned_elsewhere = {name for cat, names in tool_categories.items() if cat != category_key for name in names}
    tool_categories[category_key] = sorted(
        name for name in all_tool_names
        if name.startswith(prefix) and name not in owned_elsewhere
    )
```

**Regression pin — both states, as the leader requires.** `tests/unit/tools/test_dynamic_toolset_expansion.py` already carries the `_production_categories()` seam (`:84-102`) and the synthetic `PLANE_TOOLS` fixture (`:105`); the collision case is absent. Required cases:

| # | Server state | Assertion |
|---|---|---|
| 1 | **absent** — categories contain `"plane_sync"` → `["plane_sync_project"]`, `"plane"` empty/absent | `"plane"` must **NOT** contain `plane_sync_project`; and resolving PM's `allow=["plane"]` must **not** yield `plane_sync_project` |
| 2 | **registered** — categories contain `"plane"` → the runtime `plane_*` names | `"plane"` contains every `plane_*` name and **NOT** `plane_sync_project` |
| 3 | positive control | PM's toolset still contains `plane_sync_project` **through** its `"plane_sync"` allow entry (the worker, `agents/worker/meta.json:29`) — the fix must not strip the owner |
| 4 | non-regression | the `mcp_` lane's existing `owned_elsewhere` behaviour is unchanged |

**Assessment.** Cheap, surgical, mirrors an existing in-repo pattern, and it repairs a defect that makes PM's toolset depend on server-registration state — i.e. it is also a **determinism** fix, which is worth stating to the reviewer: *the same agent config must yield the same toolset regardless of whether a remote server registered.*

---

## Risk register

| # | Risk | Sev | Evidence | Mitigation | Owner |
|---|------|-----|----------|------------|-------|
| **R1** | **The live and demo Plane MCP credentials are expired.** The daemon's gate is presence-only ⇒ a dead key still yields a `mcp_servers` row ⇒ every session fails 401 at connect, with no typed refusal. | **High** | Probe: HTTP 401 `invalid_token` for `~/agents-ensemble/.env` and `~/agents-ensemble-demo/.env`; the dev token returns 200 | (a) User-owned re-mint; (b) **credential liveness is not a code property** — but the *observability* fix (loud skip + a connect-failure warning) is; (c) consider a startup probe that logs a WARNING when a builtin server's `test-connection` fails | User (mint) + lane (observability) |
| **R2** | **Upstream verb drift.** The charter names `plane_create_issue` / `plane_list_issues` — **zero `*_issue` verbs exist**; the entity is `work_item`. **The drift is not confined to the agent charter: the daemon's own `tool_name_prefix` docstring repeats it** (`daemon/mcp/builtin_servers/plane.py:143-152`: "Plane's tools are exposed as ``plane_list_issues``, ``plane_create_issue``"). | **High** | Full 177-tool inventory; upstream `instructions`; the daemon docstring | Charter reconciliation to the real names (leader lean #5) — **scope must include the `plane.py:143-152` docstring**, not only `agents/project-manager/*.md` — plus the re-capture gate (i) at implement time | Lane (charter) |
| **R3** | **Silent skip.** A missing env var ⇒ INFO-level skip ⇒ the agent silently has no Plane tools. | **High** | `manager.py:3076-3087` | §Loud-skip observability: WARNING + agent names + missing env vars + registry-vs-row cross-check | Lane |
| **R4** | **Env misclassification (fail-open).** The auto-derive labels the dev checkout `'live'` ⇒ the prod workspace would be reachable from dev. | **High** | Executed `_self_env_marker()` per context; `_auto_derive_env` `:513-556` | **D-1** — prod access requires an explicit `live` marker or the daemon's own `ensemble_prod` DB | Lane (D-1) + follow-up commission for the shared oracle |
| **R5** | **Stale `mcp_servers` row** keeps the prod slug after the env is corrected (P4). | Medium | `manager.py:3115-3132`; heal semantics verified | E4 — schema-version bump `"2"`→`"3"` re-materialises `headers` from the current env; optional connect-time header validation | Lane |
| **R6** | **177-tool prompt surface** ⇒ token cost, latency, tool-choice error rate. | Medium | 177 schemas; 116 write-capable | Measure; A′ (narrow the allow-list) if needed | Lane (measure) |
| **R7** | **Prefix-sweep category collision** ⇒ PM's toolset changes with server state; `plane_sync_project` leaks into category `"plane"`. | Medium | `instance.py:499-511` vs `:466-477`; `plane_sync.py:210-212` | §Prefix-sweep ownership guard + 4 regression cases | Lane |
| **R8** | **Charter/baseline coupling.** Any `agents/project-manager/*.md` edit requires the prompt-artifact re-stamp (161/161) **and** the `SEALED_SHA_BASELINES` re-pin for `workflow.md` (`tests/test_chart_image_delivery_e2e.py:1490`, sha `bde47909…`) **in the same commit**. | Medium | Verified anchor; baseline row content re-read | Same-commit ceremony; verify `agents/.prompt-artifacts.sha256` clean at commit time | Lane (implementer) |
| **R9** | **D6 (pre-existing):** a custom non-UUID `instance_id` ⇒ `ensure_mcp_preloaded()` runs before `instance_lifecycle.py:1610` rejects/regenerates the id ⇒ the API response carries the new UUID with `mcp_tool_names=[]`. | Medium | 2026-09-20 report | Pre-existing, non-blocking; **the Task-3 verification MUST use the default UUID id path** | Task-3 executor |
| **R10** | **D7 (pre-existing):** `POST /api/mcp-servers/test-connection` bypasses the MCP kill-switch ⇒ returns `success=true, 177 tools` while the switch is OFF — actively misleading for an operator verifying switch state. | Medium | 2026-09-20 report; `daemon/routers/mcp_servers.py:337` (the endpoint moved since the 2026-09-20 report cited `:173`) | Pre-existing; **relevant to R1** — the operator's "is Plane up?" probe is itself unreliable. Note in the runbook; fix is a follow-up | Follow-up |
| **R11** | **No privileged-category change is needed** — but if one is made, the full same-commit pin ceremony applies (16 assertion sites / 10 files + 5 count pins + the ari trailing-entry tuple + a registration pin). | Low | `_tool_registry.py:357-618` | Lean #6: no change expected; if changed, run the ceremony | Lane |
| **R12** | **The `mcp_servers` row stores the bearer in plaintext** and `GET /api/mcp-servers` does not redact `headers`. | Medium | `redact_secrets` `daemon/routers/mcp_servers.py:185-253` covers `env` + `url` userinfo only | **Out of scope** — recorded in §Adjacent defects | Follow-up |
| **R13** | **Path B would need a second credential** (`X-Api-Key`) that neither install has — a new prod credential to mint. | Low (Path A) | `PLANE_API_KEY` absent on live + demo | Not applicable under Path A; recorded for the FC/flip analysis | — |
| **R14** | **The dev context is the only armed environment.** If the Task-3 verification is planned against demo or live, it cannot execute (dead keys). | Medium | Credential probe per install | **Task 3 MUST run in the dev context** (`~/ensemble-src`: working MCP key + `e2e` + a REST key) | Task-3 executor |

---

## Verification-gate procedures

Sequencing: **(i) → (ii) → (iii)**. Gate (i) is already satisfied but must be **re-captured at implement time** (the upstream catalog moves). Gate (ii) **must precede** shipping the non-live ⇒ `e2e` pin — shipping a pin against an unreachable workspace converts a "wrong workspace" failure into a "no Plane at all" failure. Gate (iii) precedes the promote.

### Gate (i) — full remote tools/list inventory

| | |
|---|---|
| **Status** | ✅ **CAPTURED 2026-11-02** (`tools-list-inventory.md`). Must be **re-captured at implement time**. |
| **Procedure** | From an env with a working `PLANE_MCP_API_KEY`: MCP `initialize` → `notifications/initialized` → `tools/list`. Record: tool count, full name list, and the input-schema property names per tool. **No other verb may be called.** Never echo the key. |
| **Pass criteria** | 1. `tools/list` succeeds (HTTP 200) ⇒ the credential is live. 2. The list contains `create_work_item` (with an `is_draft` property), `update_work_item`, `delete_work_item`, `list_work_items`, `retrieve_work_item`, all 5 comment verbs, and all 10 cycle verbs. 3. **No** `*_issue` verb appears — the charter must therefore name `*_work_item` verbs. 4. The write-capable count (116 at capture) has not dropped below the set PM's charter names. |
| **Failure action** | If a PM-critical verb is missing ⇒ **FC1 fires** ⇒ re-open Path B. If the credential is dead ⇒ **R1** ⇒ stop; a user re-mint is required before any further Plane work. |
| **Evidence artifact** | `tools-list-inventory.md` (this lane) + a re-capture appended at implement time (names + schemas; **no secrets**). |
| **Sequencing** | First. Cheap, read-only, and it gates the charter rewrite. |

### Gate (ii) — e2e workspace existence + key reachability

| | |
|---|---|
| **Status** | ◐ **REST leg PASSED**; **MCP leg UNVERIFIED**. |
| **REST leg — done** | `GET {PLANE_BASE_URL}/api/v1/workspaces/e2e/projects/` with `X-Api-Key` ⇒ **HTTP 200, 2 projects** (`E2E`/`e2e`, `SYSTEMDEFAUL`/`__system_default__`). ⇒ the `e2e` workspace **exists** and the REST credential reaches it. |
| **MCP leg — outstanding** | The `PLANE_MCP_API_KEY` is a **different credential** from `PLANE_API_KEY`; the REST GET therefore does **not** prove MCP reachability. The commission's hard rule ("never call any verb other than `tools/list`") forbids the one MCP verb that would prove it (`list_projects`). |
| **Procedure (to close)** | With **explicit operator authorisation** for a read-only MCP verb: call `list_projects` (a read, no write) on the `e2e` slug with the dev MCP credential. Expect HTTP 200 with the two e2e projects. Then — **also with explicit authorisation** — repeat on the `nea` slug to determine whether the *same* credential reaches production (it currently returns 200 for `tools/list` on `nea`, but that is catalog-only and proves nothing about data reachability). |
| **Pass criteria** | (a) `e2e` resolves under the MCP credential ⇒ the non-live ⇒ `e2e` pin is satisfiable. (b) The answer to the `nea` question is recorded — it determines whether **one** re-minted credential can serve both lanes (the current dev credential is identical to the dev `PLANE_API_KEY`, so one credential already serves REST+MCP in dev). |
| **Failure action** | If `e2e` does **not** resolve under the MCP credential ⇒ **do not ship** the non-live ⇒ `e2e` pin; instead create/point the workspace first, or narrow the policy to "refuse anything that is not explicitly `e2e`" without requiring `e2e` to exist. |
| **Evidence artifact** | A short gate note (HTTP status + project identifiers; **no keys**). |
| **Sequencing** | Second — **must precede** shipping the pin. |

### Gate (iii) — live `mcp_servers` row heal on env/schema-version change

| | |
|---|---|
| **Status** | ⬜ not run (requires a fenced live touch — outside this lane's bounds). |
| **Procedure** | 1. **Fenced read** of the live `mcp_servers` row for `name='plane'`: capture `config_schema_version`, `config->>'headers'->>'x-workspace-slug'` (and the `url`), with the bearer redacted. 2. Change the env (e.g. slug `nea` → `e2e` on a **non-live** install, or the reverse on a scratch copy) and bump plane's `schema_version` `"2"`→`"3"`. 3. Boot and re-read the row: assert the slug now equals the **resolved** slug and that the schema version advanced. 4. Assert the **refusal** path: with a non-live env and slug `nea`, the row is **not** created and the daemon logs the WARNING naming the agent + missing env vars. |
| **Pass criteria** | (i) After the bump, `config.headers['x-workspace-slug']` == the resolved slug (proving the heal rebuilds `headers` from the current env). (ii) The operator-set `env` axis is still preserved (`manager.py:312-353`) — assert a sentinel `config.env` key survives. (iii) With a refused resolution: **no row**, **no tools**, and a **WARNING** naming the agent(s). (iv) The live daemon is untouched by the drill (the drill runs on a scratch install; the live read is SELECT-only). |
| **Failure action** | If (i) fails ⇒ the heal does **not** re-point the slug ⇒ E4 needs the connect-time header validation as well; escalate to the architect before shipping E4. |
| **Evidence artifact** | A gate note with the before/after row snapshots (bearer redacted) + the captured WARNING line. |
| **Sequencing** | Third — after (ii), before the promote. |

### Gate (iv) — added by this analysis: environment-classification proof

| | |
|---|---|
| **Status** | ⬜ must be built as a test pack. |
| **Why** | D-1 is the load-bearing policy change, and the failure mode it fixes is **silent** (the dev checkout currently classifies as `live`). A regression here re-opens the prod workspace to dev with no error anywhere. |
| **Procedure** | A table-driven test over the resolver: (explicit `live` ⇒ live), (explicit `demo` ⇒ demo), (no marker + own db `ensemble_dev` ⇒ **non-live**), (no marker + own db `ensemble_prod` ⇒ live), (no marker + unknown db ⇒ non-live/unclassified), (explicit opt-out ⇒ non-live). Each case asserts the permitted slug **and** the typed refusal reason. |
| **Pass criteria** | All cases pass; critically the "no marker + `ensemble_dev` ⇒ **non-live**" case (the one that currently fails via the shared auto-derive). |
| **Evidence artifact** | The test pack + its result. |
| **Sequencing** | With the implementation (it is its own regression pin). |

---

## Adjacent defect recording (OUT OF SCOPE for this lane — follow-up only)

These are **recorded, not fixed**. Each is a real defect with a named anchor; none is required for this lane's deliverables.

| # | Defect | Anchor | Why it is out of scope | Suggested owner |
|---|--------|--------|----------------------|----------------|
| **AD1** | The Plane MCP bearer is persisted **in plaintext** in `mcp_servers.config.headers` (the row is the durable state; `config.env` has a KMS path, `config.headers` does not). | `daemon/manager.py:3089-3097`; `daemon/mcp/builtin_servers/plane.py:348-354` | Fixing it means a KMS-backed header path — a materially different feature lane that would change how every builtin persists auth. | secrets / kms lane |
| **AD2** | `GET /api/mcp-servers` does not redact `config.headers` — `redact_secrets` scrubs only the `env` sub-dict and `url` userinfo. The live bearer is therefore readable through the API. | `daemon/routers/mcp_servers.py:185-253` (docstring `:196-215` enumerates exactly the two scrubbed surfaces) | Small but genuinely separate (an API-redaction change touches a public read surface); the lane already has a full fix-cycle budget consumed by the enforcement work. **Recommend: do NOT wait — this is a live credential in a GET response.** | API/security lane (high priority) |
| **AD3** | **No KMS binding path for `config.headers`.** `kms_attach` / `mcp_set_env` write `config.env` only, so a builtin that authenticates via a header cannot be given a KMS-backed secret. | `daemon/tools/infra.py:1012` / `:1022` / `:1527+` | Requires extending the KMS resolver's marker substitution to a header axis — a daemon-wide change. | secrets / kms lane |
| **AD4** | `daemon/tools/mcp_tools.py:17` documents a `call_mcp_tool` that does not exist. | `daemon/tools/mcp_tools.py:17` | Doc-only; trivially folded into a docs pass. | docs lane |
| **AD4b** | **The daemon's own `tool_name_prefix` docstring documents non-existent verbs** — "Plane's tools are exposed as ``plane_list_issues``, ``plane_create_issue``". The real verbs are `plane_list_work_items` / `plane_create_work_item`. | `daemon/mcp/builtin_servers/plane.py:143-152` | It is *daemon source*, not a prompt artifact — so it is a normal code edit (no 161/161 re-stamp, no `SEALED_SHA_BASELINES` re-pin) and it is **inside** the charter-reconciliation scope rather than out of it. Recorded separately so the implementer does not miss it (it is the doc a future implementer reads). | Lane (charter reconciliation) |
| **AD5** | The shared env auto-derive mislabels a repo checkout as `live` on a multi-install host (§Workspace enforcement design). D-1 contains it for the Plane lane; the shared resolver remains wrong. | `daemon/tools/upgrade_tools.py:440-561` (esp. `:513-556`) | It is a **security-relevant** resolver shared with the promote ceremony; fixing it is its own commission with its own gates. | separate commission (recommended) |
| **AD6** | D6 — custom non-UUID `instance_id` ⇒ `mcp_tool_names=[]` in the spawn response. | 2026-09-20 report §D6 | Pre-existing, non-blocking; noted so the Task-3 verification does not use a custom id. | follow-up |
| **AD7** | D7 — `POST /api/mcp-servers/test-connection` bypasses the MCP kill-switch ⇒ reports `success=true, 177 tools` with the switch OFF. | `daemon/routers/mcp_servers.py:337` (the endpoint moved since the 2026-09-20 report cited `:173`) | Pre-existing; but it is the operator's natural "is Plane up?" probe, so it directly degrades R1's remediation. | follow-up |

---

## Technical Debt

### Items Affecting This Analysis

| # | Debt item | Impact on the recommendation | Severity | File:Line |
|---|-----------|------------------------------|----------|-----------|
| 1 | Presence-only availability gates — never validate that a credential works | Path A's benefit is conditional on a **working** credential, and today live+demo are dead. The gate cannot be fixed in the daemon (validity is not a static property) — but the *observability* of a failed connect can. | High | `daemon/mcp/builtin_servers/plane.py:296-298` |
| 2 | Silent INFO-level skip with no agent/env context | Makes R3 (silent failure) the norm; the observability fix is cheap and is in scope. | High | `daemon/manager.py:3076-3087` |
| 3 | `mcp_full_access` validated against definitions, never against rows | The registry-validation gap that made the original failure invisible; folded into the observability fix. | High | `daemon/registry.py:1419-1457` |
| 4 | Dynamic-prefix sweep without an ownership guard | The `plane_sync_project` collision; the fix mirrors an in-repo pattern. | Medium | `daemon/tools/instance.py:499-511` |
| 5 | Shared env auto-derive reads a sibling install's `.env` | **Changes the enforcement design** (D-1). | High | `daemon/tools/upgrade_tools.py:513-556` |
| 6 | `PlaneHttpClient` has only 4 project methods | Makes Path B's cost high (a new REST contract must be authored). | Medium | `daemon/clients/plane_http_client.py:563/606/652/677` |
| 7 | `redact_secrets` does not scrub `headers` | AD2 — a live credential is readable via the API. | Medium | `daemon/routers/mcp_servers.py:185-253` |
| 8 | No KMS path for header-borne secrets | AD1 + AD3. | Medium | `daemon/tools/infra.py:1012/:1022/:1527+` |
| 9 | Charter names verbs that do not exist (`plane_create_issue`) | The charter rewrite is mandatory regardless of path; it is also the drift detector. | High | `agents/project-manager/tools_note.md:23,:37`; `rule.md:5,:39`; `workflow.md:14,:42,:53,:142,:182`; `soul.md:11` |

### Items NOT Affecting This Analysis

- The event-loop sync-DB class (fixed in v0.27.14) and the DB-pool idle-in-transaction class (v0.27.13) — neither is on the Plane path.
- The MCP warmup / init-guard machinery (v0.27.11) — relevant only insofar as plane's `is_available` consults `super()` (the quarantine) first, which I verified is intact (`plane.py:291-292`).
- The promote-soak / journal-read machinery — orthogonal; the lane ships no promote-path changes.
- The `plane_sync` service's watchdog knobs (`daemon/config.py:2607-2641`) — the REST lane's tuning, untouched.

### Recommended Paydown (ordered, only items that affect this analysis)

1. **D-1 resolver + gate (iv)** — the load-bearing policy change; nothing else in Task 2 is safe without it.
2. **Loud-skip observability (layers 1–3)** — cheap; it is what would have caught the original incident; it also de-risks R1 and R3.
3. **Prefix-sweep ownership guard + 4 regression cases** — cheap, deterministic-toolset fix.
4. **E4 schema-version bump `"2"`→`"3"`** — one line plus the heal (already verified to re-point the slug correctly).
5. **Charter reconciliation** to the real verb names — mandatory, and it re-stamps the prompt artifacts + the `SEALED_SHA_BASELINES` row in the same commit. **Scope includes `daemon/mcp/builtin_servers/plane.py:143-152`** (the daemon docstring repeats the same drifted verbs; it is daemon source, so it needs no prompt-artifact re-stamp).
6. **AD2 (redaction of `config.headers`)** — recommended *now* rather than deferred: it is a live credential readable through a GET. If the architect prefers, it can ship as a one-file follow-up in the same lane.

---

## Open Questions

1. **Should the production rule also be strict (`live` ⇒ `nea` only)?** Leader lean #2 says "live exempt ⇒ byte-identical". I recommend **strict** (`nea` permitted on live; `e2e` refused on live) because it is byte-identical to today's live behaviour (the live slug *is* `nea`) and it closes the mirror-image hazard (prod data written into the test workspace), which the user's directive does not cover. **If the leader prefers live-exempt, it is a one-line policy flip in the resolver** — say so and I will re-cut the policy table.
2. **Should the shared auto-derive be fixed in this lane instead of D-1?** My recommendation is D-1 lane-locally + a follow-up commission for `_auto_derive_env` (AD5), because that resolver is shared with the promote ceremony. If the leader wants the single-root fix, the blast radius and its gates change materially and the analysis must be re-cut.
3. **Should AD2 (header redaction) ship inside this lane or as a separate one-file follow-up?** It is a live credential exposed through `GET /api/mcp-servers`; my recommendation is to ship it in this lane.
4. **Who owns the live/demo credential re-mint, and can one credential serve both `nea` and `e2e`?** The dev credential is currently identical to the dev `PLANE_API_KEY` and works for REST+MCP, so one credential can serve both lanes — but whether the *same* credential reaches `nea`'s **data** is unverified (gate (ii)) and needs explicit operator authorisation to test.
5. **Is a narrowed grant (A′) wanted by default?** My recommendation is to ship the full `"plane"` category and **measure**; the narrowed grant is a `meta.json`-only change if the measurement says so.

---

## References

- `investigation-digest.md` — Digest A (§1), Digest B (§2), Leader Leans (§3), Planner Verification Stamps (§4), my re-verification table (§5), the E4 correction (§6), probe evidence (§7), the 2026-09-20 cross-check (§8), residual-unverified list (§9).
- `tools-list-inventory.md` — the captured 177-tool inventory, the write-verb reality check, credential health, and the e2e REST reachability result.
- `daemon/mcp/builtin_servers/plane.py:263-298` (availability gate; quarantine consulted first), `:325-354` (`get_base_config`), `:127-141` (`schema_version`).
- `daemon/mcp/builtin_servers/base.py:229-248` (`build_config` → `get_base_config`) — the reason E4 re-points the slug.
- `daemon/manager.py:3025-3140` (`_bootstrap_builtin_servers`; skip at `:3073-3087`; create/reactivate/heal), `:312-353` (`_heal_config_preserving_env`).
- `daemon/registry.py:411`, `:710`, `:771`, `:1419-1457` (`mcp_full_access` definition, parse, validation).
- `daemon/services/mcp_service.py:1243-1313`, `:1283-1292` (the read-only strip; builtin-registry consult).
- `daemon/tools/_tool_registry.py:279-281` (`DYNAMIC_TOOL_PREFIXES`), `:932-935` (category inference).
- `daemon/tools/instance.py:466-477` (the `mcp_` ownership guard to mirror), `:499-511` (the unguarded dynamic sweep).
- `daemon/tools/plane_sync.py:210-212` (`plane_sync_project`, category `plane_sync`).
- `daemon/clients/plane_http_client.py:296-308` (`_rest_base_url`), `:326-362` (`__init__`), `:364-374` (`is_available`), `:388-398` (`_headers`).
- `daemon/services/plane_sync_service.py:157-162` (kill-switch), `:374-381` (typed `unavailable_reason`).
- `daemon/config.py:3178` (`LiveViewsRootConfig`), `:3262-3271` (its validator and the "refuses to boot" rationale — the shape to borrow, the consequence **not** to copy), `:2607-2641` (the 4 watchdog knobs).
- `daemon/tools/upgrade_tools.py:187` (`VALID_ENVS`), `:262-294` (explicit marker + opt-out), `:395-418` (`_install_root_candidates`), `:440-561` (`_auto_derive_env` — the sibling-`.env` read at `:513-556`), `:608` (`_self_env_source`).
- `daemon/routers/mcp_servers.py:185-253` (`redact_secrets` — the AD2 gap), `:337` (the D7 test-connection bypass).
- `daemon/tools/mcp_tools.py:17` (the AD4 stale doc).
- `scripts/deploy.sh:257-276` (e2e slug pin + grep assert); `scripts/run_tests_scrubbed.sh:52`, `:78` (scrub + leak gate).
- `.env.example:185-190` (the doctrine prose), `:195-209` (the Plane variable block), `:444-445` (both kill-switches).
- `agents/project-manager/meta.json` (`mcp_full_access`, `tools.allow`, `tools.deny`, `deny_spawn`, `team_members`, `version`).
- `agents/worker/meta.json:29` (`plane_sync` in allow — the legitimate owner of `plane_sync_project`).
- `tests/unit/tools/test_dynamic_toolset_expansion.py:60-108` (the seam + the `PLANE_TOOLS` fixture — the collision case to add); `tests/unit/tools/test_frozen_tool_name_discovery.py:200` (the only `plane` hit — a comment, so **no** `KNOWN_TOOL_NAMES` row is needed for Path A).
- `tests/test_chart_image_delivery_e2e.py:1490` (`SEALED_SHA_BASELINES` row for `agents/project-manager/workflow.md`, sha `bde47909…`).
- `.agents/tester/RESULTS/2026-09-20-plane-revival-verification.md` (177 tools; PM's 5 live invocations; D6/D7 residuals).
- `agents/.prompt-artifacts.sha256` (161 entries — the re-stamp target for any `agents/project-manager/*.md` edit).

# Investigation Digest — Plane PM Issue Tools Lane

**Lane:** `plane-pm-issue-tools` (branch `feature/plane-pm-issue-tools`, base `941cb6321` = `origin/latest`)
**Worktree:** `/home/nea/ensemble-src-wt-plane-pm`
**Captured:** 2026-11-02
**Author:** technical-analysis worker
**Canonical role:** this file is the lane's evidence base. §1–§4 are the commissioned inputs, reproduced VERBATIM. §5–§9 are the technical-analysis worker's own verification and probe evidence.

---

## §1 — DIGEST A — wiring gap (wanderer, verified @ 941cb6321) [VERBATIM]

- PRIMARY ROOT CAUSE: Plane builtin (daemon/mcp/builtin_servers/plane.py, registered in builtin_servers/__init__.py:74-81) is a REMOTE streamable-http descriptor; availability gate = PLANE_MCP_ENABLED(default ON) AND PLANE_MCP_URL AND PLANE_MCP_API_KEY (plane.py:291-298). Unavailable => _bootstrap_builtin_servers SILENTLY SKIPS creating the mcp_servers row (daemon/manager.py:3073-3087) => preload never sees server "plane" (mcp_service.py:782-785) => zero plane_* tools for every agent, no warning anywhere. No PLANE_* vars in the worktree .env.
- NOT broken (empirically proven by running the production filter with PM's exact config): mcp_full_access semantics (parsed registry.py:710/:771; field def :411-427; validated :1419-1457 — against builtin registry only, NO check that the server has a DB row; sole consumer _get_read_only_tools mcp_service.py:1243-1313 skipping the CR-3 read-only schema strip; preload strip at :976-1004) and the allow/deny gates (resolve_tool_filter daemon/tools/instance.py:403-543 — with the server present, PM's config admits plane_list_issues AND plane_create_issue). Two gates are independent: schema-level strip (mcp_full_access) + name-level allow/deny. Tool names are runtime-discovered from the remote server tools/list, exposed as plane_{verb} (tool_adapter.py:487-511 prefix override); category inferred from name prefix (_tool_registry.py:933-944) plane_* -> "plane".
- SECOND DEFECT — vocabulary: charter promises plane_create_issue/plane_update_issue/plane_list_issues/etc. (agents/project-manager/tools_note.md:23,:37; rule.md:5,:39; workflow.md:14,:42,:53,:142,:182; soul.md:11). The ONLY live capture of the remote server (.agents/tester/RESULTS/2026-09-20-plane-revival-verification.md:118,137) shows verbs plane_list_projects, plane_create_work_item, plane_retrieve_work_item, plane_delete_work_item (177 plane_* tools) — NO *_issue/*_comment/*_cycle verb in the sample. UNVERIFIED: full verb inventory needs one tools/list call during the lane. Also stale doc: daemon/tools/mcp_tools.py:17 documents a call_mcp_tool that does not exist.
- THIRD DEFECT — prefix sweep: DYNAMIC_TOOL_PREFIXES = frozenset({"plane_"}) (_tool_registry.py:279-281); generic sweep at instance.py:498-511 has NO ownership guard (unlike mcp_ lane's owned_elsewhere :466-477). When the server is unregistered, category "plane" is empty, the sweep fires, and plane_sync_project (defined daemon/tools/plane_sync.py:212, category "plane_sync", granted to worker via agents/worker/meta.json:29) lands in category "plane" => PM's allow "plane" grants it. With the server registered, PM does NOT get plane_sync_project. Same meta.json, two different toolsets. This is exactly why the test commission observed only plane_sync_project. tools_note.md:24 claims PM does NOT hold plane_sync_project — false at runtime today, true again once the sweep is fixed. Regression test gap: test_dynamic_toolset_expansion.py:80-104 does not cover this collision.
- META: agents/project-manager/meta.json — mcp_full_access ["plane"] (:11-13); allow includes "plane" (:50); deny includes "mcp" (:65) (deny "mcp" removes only mcp_*-shaped names — documented plane.py:144-151); deny_spawn chart/image; team_members [leader, worker]; version 2.1.0.
- NO first-party issue tools exist anywhere: PlaneHttpClient has exactly 4 methods — create_project :563, update_project :606, get_project :652, list_projects :677 (projects only, no issue/cycle/comment/member methods). plane_sync_service touches projects only (:1086-1186). The premise "client already has issue CRUD methods" is FALSE — there is nothing to wrap.
- External-server path: a NON-builtin registered server's tools get mcp_<slug>_<verb> names => category "mcp" => PM's deny "mcp" strips them all; mcp_full_access is a no-op for non-builtins (mcp_service.py:1286-1288). So the builtin descriptor + plane prefix override IS the intended path. mcp_rpc_* lane (category mcp_rpc, agents {ari,leader}, gateway default OFF) is remote-hands, not a plane-issue path.
- FIX SURFACE: Path A = registration only (set PLANE_MCP_URL + PLANE_MCP_API_KEY + PLANE_MCP_WORKSPACE_SLUG per env; row created at boot; ~177 remote tools; write verbs only for PM via mcp_full_access; zero code for the surface itself). Path B = first-party issue tools (new daemon/tools/plane_issue.py module + @register_tool_category + factory in create_instance_tools (instance.py ~:6286-6342) + CATEGORY_MODULES row + KNOWN_TOOL_NAMES row (tests/unit/tools/test_frozen_tool_name_discovery.py) + extend PlaneHttpClient with issue/cycle/comment REST methods + grant in PM meta.json allow). Privileged-set note: neither "plane" nor "plane_sync" is in the 16-member privileged set (_tool_registry.py:357-618); IF a write-authority category were made privileged => same-commit pin ceremony (16 assertion sites/10 files + 5 count pins ==16 + ari trailing-entry tuple + new registration pin). Charter edits => re-stamp agents/.prompt-artifacts.sha256 (161/161) AND SEALED_SHA_BASELINES row for agents/project-manager/workflow.md (tests/test_chart_image_delivery_e2e.py:1490) in the SAME commit. meta.json is NOT in the prompt baseline (no re-stamp for it; agents/ tree hash changes in the release manifest). No DB migration, no config.yaml change for tool categories.

---

## §2 — DIGEST B — workspace/env resolution (wanderer, verified @ 941cb6321) [VERBATIM]

- TODAY: NO per-environment awareness in Plane code at all. One process-global triplet from the install .env: PLANE_MCP_WORKSPACE_SLUG (shared by BOTH lanes: plane_http_client.py:303,:356 + builtin_servers/plane.py:345), PLANE_API_KEY (REST, X-Api-Key, plane_http_client.py:352,:373), PLANE_MCP_API_KEY (MCP Bearer, plane.py:297,:343), PLANE_BASE_URL (:302; also FE iframe gate daemon/routers/settings.py:712), PLANE_MCP_URL (:296,:342). Kill-switches PLANE_MCP_ENABLED (plane.py:82) / PLANE_SYNC_ENABLED (plane_sync_service.py:157), independent, default ON. No config.yaml Plane field (grep 0); daemon/config.py has only 4 watchdog knobs (SERVICES_PLANE_SYNC_WATCHDOG_*, :2594-2641); ensemble_config.py zero Plane refs. Load: launcher.sh:1649 / run_app.py:17-31 / dev.sh:74-76. Only "nea"/"e2e" daemon-code literal is a docstring example (daemon/mcp/config.py:106).
- REST chain: workspace baked into URL path AND header (plane_http_client.py:302-308 f"{base}/api/v1/workspaces/{workspace}/projects/"; :396 x-workspace-slug). plane_sync_project + sync service take NO workspace parameter ever — workspace is structurally env-bound, one control point covers all callers. Validation census: ABSENT (only presence gates is_available :373; typed reasons :375-381; fail-closed on UNSET only :304 — not on nea).
- MCP lane: get_base_config (plane.py:342-353) returns {"transport":"streamable-http","url",headers:{Authorization: Bearer, x-workspace-slug}}. Availability gate deliberately does NOT include the slug (comment :275-277) => unset slug => server registers anyway => SILENTLY targets the account-default (prod) workspace — the exact hazard .env.example:188 warns about. Available => header dict (slug + PLAINTEXT bearer) persisted into mcp_servers.config JSONB (manager.py:3089-3097), refreshed ONLY on config_schema_version change (:3123-3129) — an env fix does NOT re-point a running daemon's MCP lane until the row heals.
- DOCTRINE/PRIOR ART: .env.example:185-188 ships the e2e/nea doctrine prose. Shell-layer enforcement LANDED 2026-11-01 (feature/plane-e2e-workspace-wiring, ALL 8 plane-* remote branches are ancestors of origin/latest): deploy.sh:257-274 demo-gen sed-pins PLANE_MCP_WORKSPACE_SLUG=e2e + grep asserts before mv; run_tests_scrubbed.sh:42-52,78 + 3 frontend boot scripts unset PLANE_MCP_WORKSPACE_SLUG/PLANE_BASE_URL (+ leak-gate exit 78). Credentials deliberately kept (same user-scoped key serves prod and e2e — workspace selection IS the invariant). Daemon-level structural refusal: ABSENT. Verdict: this commission EXTENDS the landed shell layer with the daemon layer — do not delete the shell scrubs (defense-in-depth; boot_subprocess_sweep.py:150-152 precedent: do not trust ENSEMBLE_SELF_ENV alone).
- RUNTIME POSTURE (read-only reads): live .env = slug nea, ENSEMBLE_SELF_ENV=live, NO PLANE_API_KEY (REST off; prod reachable only via MCP; MCP URL+key present). Demo install = e2e. Dev install/worktree = no plane vars. Main checkout dev-context .env = e2e WITH REST key (armed, correctly pointed).
- ENSEMBLE_SELF_ENV ladder (ready-made oracle): explicit enum {dev,demo,live,sandbox} (upgrade_tools.py:262-279) > opt-out (:282-289) > multi-signal auto-derive (:440-560) fail-closed None (:563-606); None = consumers do nothing (api.py:2838).
- POLLUTION PATHS: P1 non-prod REST with slug nea; P2 MCP with slug UNSET => account-default prod (registers today); P3 non-prod MCP with slug nea; P4 stale persisted mcp_servers row keeps prod slug after env corrected.
- ENFORCEMENT RECOMMENDATION: E1 (primary) one config-resolution-time resolver (e.g. resolve_plane_workspace()) combining the env ladder with the slug; policy live => allow configured slug; dev/demo/sandbox => slug MUST be e2e; wrong-or-unset on non-live => typed fail-closed refusal (mirror plane_sync_service.py:375-381 reason shape + manager.py:3072-3087 skip); consider a PlaneWorkspaceConfig BaseSettings section (config.py:3262+ LiveViewsRootConfig validator precedent — "a config that boots with a broken root is operationally worse than one that refuses to boot"). E2 REST chokepoints plane_http_client.py:296-308,:353-356. E3 MCP chokepoints is_available() (add slug gate under env scoping) + get_base_config() (plane.py:264-298,:342-353) — refusal at is_available => no row, no tools (existing skip machinery) — closes P2. E4 bump the builtin's config_schema_version (or validate persisted x-workspace-slug at connect time, connection_manager.py:173 / daemon/mcp/config.py:245-277) so the row re-materializes — closes P4. E5 NOT the tool layer (workspace never a tool parameter; tool checks would duplicate E1-E3 and drift).
- LIVE CONTINUITY: live already carries ENSEMBLE_SELF_ENV=live + nea + no REST key — a live-exempt resolver leaves live byte-identical.
- UNVERIFIED (bind into the plan as verification gates): (i) whether workspace slug 'e2e' actually exists on the Plane instance and the key reaches it — verify BEFORE shipping enforcement that pins non-live=>e2e; (ii) live mcp_servers row state (repo-only scope); (iii) full remote verb inventory (one tools/list).
- ROUTED ADJACENT DEFECT (OUT OF SCOPE for this lane — record as follow-up, do not fix here): plaintext PLANE_MCP_API_KEY persisted in mcp_servers.config.headers + unredacted on GET /api/mcp-servers (redact_secrets daemon/routers/mcp_servers.py:185-253 covers config.env + url userinfo only) + no KMS binding path for headers (mcp_set_env/kms_attach write config.env only, daemon/tools/infra.py:1012/:1022/:1527+).

---

## §3 — LEADER LEANS (bind into the analysis; architect may overrule with evidence) [VERBATIM]

1. Path A (register the remote server via per-env config) is the PRIMARY lean — designed path (builtin descriptor + prefix override + mcp_full_access exist precisely for it), zero new key material on live, smaller shipped surface. Architect MUST adjudicate A vs B vs hybrid with the 5-axis trade-off matrix and a write-verb reality check against the actual tools/list inventory. If the remote surface turns out write-poor for work items (e.g. no create/update work-item verbs), Path B becomes primary.
2. Task 2 enforcement: fail-closed typed REFUSAL on non-live when slug is wrong OR unset (no auto-override). Sandbox counts as non-live => e2e-only too. No kill-switch for the guard; if architect insists on one, default-ON and it must not disable resolution, only the refusal.
3. Loud-skip observability IN SCOPE: when a builtin server referenced by any agent's mcp_full_access is unavailable/skipped at boot, log a prominent WARNING naming the agent(s) and the missing env vars. Also fold the registry validation gap into the same observability fix if cheap.
4. Prefix-sweep ownership guard IN SCOPE (instance.py:498-511, mirror the mcp_ lane's owned_elsewhere pattern at :466-477) + regression pin covering the plane_sync_project/plane collision in BOTH server-registered and server-absent states.
5. Charter reconciliation IN SCOPE: rewrite the false/drifted prose (tools_note.md:24 plane_sync claim; verb vocabulary vs actual tools/list inventory — verb-agnostic phrasing with the live inventory noted is acceptable; exact names must match reality). Any agents/project-manager/*.md edit => prompt-artifact re-stamp 161/161 + SEALED_SHA_BASELINES re-pin for workflow.md IN THE SAME COMMIT.
6. No privileged-category change expected — unless architect argues otherwise, in which case the full same-commit pin ceremony applies.
7. Durable repo-doc persistence of the Task 2 directive (extend the canonical runbook/doc surface the lane finds; .env.example already carries doctrine prose) + project-level metadata/critical-note persistence is ALREADY DONE leader-side.
8. Task 3 verification must be a plan phase: after implementation, on an env where verification is possible (dev with e2e + MCP armed), dispatch PM to create the preserved DRAFT issue in e2e, and prove the enforcement refuses/blocks the nea direction on non-live. Include the exact repro/verification procedure as a gate.

---

## §4 — PLANNER VERIFICATION STAMPS (re-verified 2026-11-02 @ 941cb6321, read-only) [VERBATIM]

- CONFIRMED: worktree branch/base; plane.py is_available gate (kill-switch PLANE_MCP_ENABLED default ON + PLANE_MCP_URL + PLANE_MCP_API_KEY; super() quarantine consult FIRST per C1); slug deliberately NOT in availability gate (docstring :275-277); get_base_config returns Bearer + x-workspace-slug headers; manager.py ~:3073-3087 silent skip at INFO level (no agent names — strengthens lean #3); row create/reactivate + env-preserving schema-version heal (:3089-3132).
- CONFIRMED: PM meta.json — mcp_full_access ["plane"], allow includes "plane" + "instance" + "shared_meta_kv", deny includes "mcp", deny_spawn chart/image, team_members [leader, worker], v2.1.0.
- CONFIRMED: DYNAMIC_TOOL_PREFIXES frozenset({"plane_"}) (_tool_registry.py:279-281); category inference = name.split('_')[0] (:933-944); mcp_ lane owned_elsewhere guard exists, dynamic-prefix sweep has NONE; plane_sync_project registered category "plane_sync" (plane_sync.py:210-212) but name starts with "plane_" => sweeps into category "plane" when server absent => PM allow "plane" grants it. Worker meta.json:29 carries "plane_sync".
- CONFIRMED: PlaneHttpClient has EXACTLY 4 project methods (create_project :563, update_project :606, get_project :652, list_projects :677) — zero issue/cycle/comment methods; _rest_base_url (:296-308) bakes workspace into URL; is_available gates on PLANE_BASE_URL + PLANE_API_KEY only.
- CONFIRMED: plane_sync_service kill-switch :157-162 default ON; typed unavailable_reason :375-381. config.py has ONLY the 4 SERVICES_PLANE_SYNC_WATCHDOG_* knobs (:2594-2641). .env.example doctrine prose at :184-188. deploy.sh sed-pins slug=e2e + grep assert (:255-276). run_tests_scrubbed.sh unsets PLANE_MCP_WORKSPACE_SLUG/PLANE_BASE_URL + leak gate exit 78 (:46-52, :78). SEALED_SHA_BASELINES row for agents/project-manager/workflow.md at tests/test_chart_image_delivery_e2e.py:1490 (content-sha256 bde47909…). test_dynamic_toolset_expansion.py:75-108 lacks the plane_sync collision case. mcp_tools.py:~17 documents nonexistent call_mcp_tool. upgrade_tools.py env ladder: explicit enum {dev,demo,live,sandbox} :258-292.
- CONFIRMED (2026-09-20 evidence, .agents/tester/RESULTS/2026-09-20-plane-revival-verification.md): 177 plane_* tools live; PM round-tripped plane_list_projects → plane_create_work_item → plane_retrieve_work_item → plane_delete_work_item → 404-confirm; sample also shows plane_create_project/plane_update_project; mcp_tool_names=179 (177 plane + 2 mcp_context7). NO *_issue/*_comment/*_cycle verb in the sample.
- ANCHOR ADJUSTMENTS: (1) mcp_full_access field-def/parse/validation anchors live in **daemon/registry.py** (not daemon/tools/registry.py) — re-anchor exact lines yourself; mcp_service.py:1283-1292 confirmed as the read-only-strip consult of the builtin registry. (2) LiveViewsRootConfig class at daemon/config.py:**3178**. (3) .env.example doctrine at :**184-188**. (4) manager skip log is INFO (digest called it silent — effectively silent, and observability fix is in scope).
- Also noted: tests/unit/tools/test_frozen_tool_name_discovery.py contains exactly 1 'plane' grep hit — check what it is (Path B needs a KNOWN_TOOL_NAMES row only if new static tools ship).

---

## §5 — TECHNICAL-ANALYSIS WORKER: own anchor re-verification (2026-11-02, @ 941cb6321, read-only)

Method: direct file reads + line-anchored greps in `/home/nea/ensemble-src-wt-plane-pm`. Every anchor below was read by me, not inherited.

| # | Claim | My verified anchor | Status |
|---|-------|--------------------|--------|
| V1 | plane builtin registered | `daemon/mcp/builtin_servers/__init__.py:77` imports `PlaneServerDefinition`; `:80` `_registry.register(PlaneServerDefinition())` | CONFIRMED (digest's ":74-81" is the import block) |
| V2 | availability gate shape | `daemon/mcp/builtin_servers/plane.py:263` `def is_available`; `:291-292` `if not super().is_available(): return False`; `:293-295` kill-switch; `:296-298` url+key presence; `:299` `return bool(url) and bool(api_key)` | CONFIRMED |
| V3 | quarantine consulted FIRST | `plane.py:291-292` — `super().is_available()` is the first statement of the override | CONFIRMED |
| V4 | slug deliberately OUT of the gate | `plane.py:275-277` docstring: "The workspace slug is read inside `get_base_config` and its absence surfaces as a runtime header error" | CONFIRMED |
| V5 | `get_base_config` headers | `plane.py:325` def; `:342-346` url/api_key/slug reads; `:348-354` returns `{"transport","url","headers":{"Authorization": f"Bearer {api_key}", "x-workspace-slug": workspace_slug}}` | CONFIRMED |
| V6 | plane `schema_version` | `plane.py:127-128` `def schema_version` → returns `"2"` (bumped 1→2 on 2026-09-20 for quote-leak sanitization) | CONFIRMED (NEW — schema_version is the E4 lever) |
| V7 | `build_config` rebuilds from current env | `daemon/mcp/builtin_servers/base.py:229` `def build_config` → `:248` `base_config = self.get_base_config()` | CONFIRMED (NEW — load-bearing for E4, see §6) |
| V8 | skip log is INFO, no agent names | `daemon/manager.py:3076-3087` — `logger.info(f"Builtin '{definition.name}' skipped — missing environment configuration (see server definition docs)")` | CONFIRMED (INFO level; no agent names, no env var names) |
| V9 | row create / reactivate / heal | `daemon/manager.py:3089-3109` create; `:3110-3117` reactivate; `:3118-3132` schema-version heal; `:3115` `if existing.config_schema_version != schema_version` | CONFIRMED |
| V10 | heal preserves ONLY the `env` axis | `daemon/manager.py:312` `def _heal_config_preserving_env`; `:337` `healed = dict(default_config or {})`; `:339-346` extracts row `env`; `:347-353` merges row env over template env | CONFIRMED — **headers are TEMPLATE-DERIVED and rebuilt from the current env** |
| V11 | PM meta.json | `agents/project-manager/meta.json` — `mcp_full_access: ["plane"]`; `tools.allow` contains `"plane"`, `"instance"`, `"shared_meta_kv"`; `tools.deny` contains `"mcp"`; `deny_spawn: ["chart","image"]`; `team_members: ["leader","worker"]`; `version: "2.1.0"` | CONFIRMED |
| V12 | `mcp_full_access` lives in `daemon/registry.py` | `daemon/registry.py:411` field def; `:710` and `:771` parse sites; `:1419-1457` validation block | CONFIRMED (re-anchored) |
| V13 | `LiveViewsRootConfig` | `daemon/config.py:3178` `class LiveViewsRootConfig(BaseModel)`; `:3262-3263` `@model_validator(mode="after") def _validate_root_shape`; `:3268-3271` the "a config that boots with a broken root is operationally worse than one that refuses to boot" rationale | CONFIRMED (re-anchored) |
| V14 | `.env.example` doctrine | `.env.example:185-190` — "# Plane workspaces: 'nea' = PROD ONLY; 'e2e' = dev+demo. Non-prod lanes MUST set PLANE_MCP_WORKSPACE_SLUG=e2e explicitly before enabling PLANE_SYNC_ENABLED — shared by REST+MCP lanes: REST fails closed when unset, MCP silently targets the account-default (prod) workspace." | CONFIRMED (re-anchored) |
| V15 | `DYNAMIC_TOOL_PREFIXES` | `daemon/tools/_tool_registry.py:279-281` `frozenset({"plane_"})` | CONFIRMED |
| V16 | category inference | `daemon/tools/_tool_registry.py:932` `category = getattr(tool_func, '_tool_category', None)`; `:934-935` fallback `tool_name.split('_')[0]` | CONFIRMED |
| V17 | mcp_ lane ownership guard | `daemon/tools/instance.py:466-472` builds `owned_elsewhere` (skipping category `"mcp"`); `:473-477` excludes owned names from the mcp sweep | CONFIRMED |
| V18 | dynamic-prefix sweep has NO guard | `daemon/tools/instance.py:499-511` — the loop over `DYNAMIC_TOOL_PREFIXES` has no ownership exclusion | CONFIRMED |
| V19 | plane_sync_project category/name | `daemon/tools/plane_sync.py:210` `@register_tool_category("plane_sync")`; `:212` `def plane_sync_project(...)` | CONFIRMED |
| V20 | PlaneHttpClient method census | `daemon/clients/plane_http_client.py` — `create_project:563`, `update_project:606`, `get_project:652`, `list_projects:677`; **no** issue/cycle/comment/member method | CONFIRMED |
| V21 | `_rest_base_url` bakes the slug | `daemon/clients/plane_http_client.py:296-308` — `:303` `workspace = _env("PLANE_MCP_WORKSPACE_SLUG")`; `:304` `if not base or not workspace: return None`; `:308` `f"{base}/api/v1/workspaces/{workspace}/projects/"` | CONFIRMED |
| V22 | REST slug header + client init | `plane_http_client.py:326-335` `__init__(base_url, api_key, workspace_slug, ...)`; `:356-360` falls back to `_env("PLANE_MCP_WORKSPACE_SLUG")`; `:388-398` `_headers()` → `:396` `"x-workspace-slug": self._workspace_slug` | CONFIRMED |
| V23 | REST availability gate | `plane_http_client.py:364-374` `is_available()` → `_rest_base_url() is not None and bool(_env("PLANE_API_KEY"))` | CONFIRMED — **presence-only, no slug policy** |
| V24 | plane_sync kill-switch + typed reason | `daemon/services/plane_sync_service.py:157-162` `plane_sync_enabled()`; `:374-381` `unavailable_reason()` → `"disabled by kill-switch (PLANE_SYNC_ENABLED=false)"` / `"not configured (PLANE_BASE_URL / PLANE_API_KEY not set)"` | CONFIRMED |
| V25 | config.py Plane knobs | `daemon/config.py:2607,2618,2628,2641` — exactly 4 `SERVICES_PLANE_SYNC_WATCHDOG_*` knobs; **no** Plane workspace knob | CONFIRMED |
| V26 | deploy.sh e2e pin | `scripts/deploy.sh:257-276` — sed pins `PLANE_MCP_WORKSPACE_SLUG=e2e`, appends a guard line, `grep -q '^PLANE_MCP_WORKSPACE_SLUG=e2e$'` asserts BEFORE `mv` | CONFIRMED |
| V27 | test scrub | `scripts/run_tests_scrubbed.sh:52` `unset PLANE_MCP_WORKSPACE_SLUG PLANE_BASE_URL`; `:78` leak-gate check on the same two names | CONFIRMED |
| V28 | SEALED_SHA baseline row | `tests/test_chart_image_delivery_e2e.py:1490` `"agents/project-manager/workflow.md": "bde479095bbda6a3e3e069afe8a719e0f8b5673f289d2d0d5bf7a15eecb123ad"` | CONFIRMED |
| V29 | env ladder enum | `daemon/tools/upgrade_tools.py:187` `VALID_ENVS: tuple[str, ...] = ("dev", "demo", "live", "sandbox")`; `:262-279` `_explicit_marker()`; `:282-294` `_is_opt_out_marker()`; `:440` `_auto_derive_env()`; `:608` `_self_env_source()` | CONFIRMED |
| V30 | `test_frozen_tool_name_discovery.py` plane hit | `tests/unit/tools/test_frozen_tool_name_discovery.py:200` — a **COMMENT** only ("# chart, image, plane, instance, council, self, question, mcp, ...) remain"). **No `KNOWN_TOOL_NAMES` row for plane exists** → a `KNOWN_TOOL_NAMES` row is needed only if Path B ships NEW STATIC tools. | CONFIRMED |
| V31 | redaction gap | `daemon/routers/mcp_servers.py:185` `def redact_secrets(config)`; `:196-215` documents it scrubs ONLY (1) the `env` sub-dict and (2) `url` top-level userinfo. **`headers` is not scrubbed.** | CONFIRMED |
| V32 | prefix override | `daemon/mcp/tool_adapter.py:134-197` `_privilege_stamp_category_for_prefix`; `:181-184` `bare_prefix = tool_name_prefix.rstrip("_")` | CONFIRMED |

**ANCHOR CORRECTION I am making to the digest's claim that a `KNOWN_TOOL_NAMES` row is needed:**
the row does not exist today and is only needed for NEW STATIC tools (Path B). Path A ships no static tool → no row.

---

## §6 — CORRECTION TO DIGEST B's E4 MECHANISM (material, and it makes E4 EASIER than stated)

Digest B states the slug "is refreshed ONLY on `config_schema_version` change" and frames E4 as a possible blocker. My verification shows E4 is **sound as stated**:

- `manager.py:312` `_heal_config_preserving_env(default_config, existing_config)` builds `healed = dict(default_config)` — i.e. the **template** config, which for plane is `build_config({})` → `base.py:248` → `get_base_config()` → **the current env** (fresh `Authorization` + `x-workspace-slug`).
- The function then merges the row's `env` sub-dict over the template's `env` — and **only** the `env` axis (`manager.py:339-353`).
- The `headers` axis is therefore **template-derived and rebuilt from the live env on every schema-version bump**.

**Consequence:** bumping plane's `schema_version` (currently `"2"`, `plane.py:127-141`) from `"2"` → `"3"` re-materializes the persisted row's headers from the CURRENT env — including the corrected `x-workspace-slug`. The stale-row pollution path **P4** is therefore closed by a one-line version bump plus the resolver from E1. No connect-time header validation is required (it remains a valid belt-and-braces addition, not a necessity).

---

## §7 — PROBE EVIDENCE (bounded, read-only) — full detail in `tools-list-inventory.md`

Summary of what was captured and what it proves:

| Probe | Method | Result | Bound compliance |
|-------|--------|--------|------------------|
| Remote tool inventory | MCP `initialize` → `notifications/initialized` → `tools/list` against `PLANE_MCP_URL` | **177 tools**, server `Plane MCP Server (header-http)` v3.2.0 | Only `tools/list` — no write verb, no data read |
| Inventory under 2 slugs | same, with `x-workspace-slug: e2e` and `x-workspace-slug: nea` | both 177; the name lists are **byte-identical** | listing only |
| Live credential health | `initialize` with the live-install `PLANE_MCP_API_KEY` | **HTTP 401 `invalid_token`** | no write; no secret echoed |
| Dev credential health | `initialize` with the dev-context `PLANE_MCP_API_KEY` | **HTTP 200** | no write; no secret echoed |
| e2e workspace existence | REST `GET {PLANE_BASE_URL}/api/v1/workspaces/e2e/projects/` with `X-Api-Key` | **HTTP 200, 2 projects** (`E2E`/`e2e`, `SYSTEMDEFAUL`/`__system_default__`) | GET only, explicitly authorized by the commission |

**Hard-rule compliance:** no secret value was written to any file, log, or report; only env var NAMES and HTTP status codes are recorded. No MCP verb other than `tools/list` was invoked. No write of any kind was performed against any workspace.

**Two findings that materially change the lane's inputs:**

1. **The live-install `PLANE_MCP_API_KEY` is expired.** The daemon's gate is presence-only, so a dead key still produces a `mcp_servers` row — but every MCP session then fails 401. This is a **failure class distinct from Digest A's "no row, no tools"**, and it is invisible in the daemon's own logs unless the connect error is surfaced.
2. **The charter's `*_issue` vocabulary is wrong at the ENTITY level, not just the prefix level.** There are **zero** `*_issue` verbs in the server catalog; the entity is `work_item`, and upstream's own `serverInfo.instructions` says so ("an epic is a work item whose type is named 'Epic'").

---

## §8 — CROSS-CHECK: 2026-09-20 EVIDENCE vs TODAY'S CAPTURE

| | 2026-09-20 capture | 2026-11-02 capture | Delta |
|---|---|---|---|
| plane tool count | 177 | 177 | none |
| server version | not captured | `3.2.0` | new info |
| work-item verbs | create / retrieve / delete observed | create / update / delete / retrieve / retrieve_by_identifier / list / search / count | **update/list/search/count newly evidenced** |
| comment verbs | not in the sample | 5 (create/list/update/delete/retrieve) | **newly evidenced** |
| cycle verbs | not in the sample | 10 | **newly evidenced** |
| `*_issue` verbs | none in the sample | **none in the FULL inventory** | the sample was not the gap — the verbs do not exist |

The 2026-09-20 sample was simply not a representative sample. The full inventory closes Digest A's "UNVERIFIED: full verb inventory" item and discharges leader lean #1's Path-B trigger.

---

## §9 — WHAT REMAINS UNVERIFIED (carried into `technical-analysis.md` gates)

1. **MCP-credential reachability of the `e2e` workspace's DATA.** The REST credential reaches e2e (§7), but `PLANE_MCP_API_KEY` is a different credential, and the commission's hard rule forbade any MCP verb other than `tools/list`. Must be closed before shipping the non-live ⇒ e2e pin.
2. **Live `mcp_servers` row state.** Out of repo scope by the commission's "do not touch the live DB" rule. Requires a fenced read.
3. **Live daemon's actual Plane session health.** The log window searched contains no plane bootstrap/session lines; the live row + session outcome must be established by a fenced, explicit probe (a live key re-mint is USER-OWNED).
4. **Whether the live daemon currently even has a `plane` row.** The live `.env` carries URL + key (both present) ⇒ `is_available()` passes ⇒ the row should exist ⇒ the daemon would attempt sessions against a dead token. Unconfirmed — see (2).

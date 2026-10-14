# Plan Overview: Image-Gen Tool (`generate_image` via llmproxy `/v1/image_generation`)

Date: 2026-10-14T19:01Z (UTC)
Author: worker via plan-creation skill (dispatched by leader)
Status: Approved — In Implementation (council + approver verdicts, 2026-10-14)
Worktree: `/home/ensemble-worktrees/image-gen-tool` (branch `feature/image-gen-tool`, base latest @ 8c525315e) — HEAD verified pre-draft

## Objective

Users (ari, leader, developer, reviewer, tester, worker) can call a new native agent tool
`generate_image` that produces images through the ensemble's EXISTING llmproxy lane
(POST `<base>/image_generation`, MiniMax upstream) and delivers them end-to-end:
(1) chat sources deliver images NATIVELY as uploaded attachments (Discord / Telegram / Slack),
(2) a live view link `/views/tmp-images/<32hex>` is emitted, and
(3) the Angular web UI renders the image inline with click-to-open lightbox —
mirroring the proven chart-render pipeline (tool → image_save → ens-img marker →
native chat upload) without regressing it.

Testable completion sentence: an allowlisted agent's `generate_image` call yields at
least one real image that arrives as a native chat attachment, is served at its
`/views/tmp-images/` URL, and renders inline in the web UI with working lightbox —
while every pre-existing chart-render test passes byte-untouched.

## Architecture Recommendation (req#1 — summary; full ADR in decisions.md D1)

**NATIVE-CORE daemon/tools module — VALIDATED.** The caller's recommendation is confirmed:
`llmproxy` is the ensemble's EXISTING LLM integration lane (same base URL, same API key,
live-verified auth on this route). A thin capability wrapper over an endpoint we already
natively integrate belongs to the tier-1 native-core family (`ens_env_read`, raw-SDK proxy
calls). The plugin tier's value proposition — vendored resource snapshots, 3-class
provenance manifests, snapshot-with-drift-alarm sync — buys nothing for a single stable
endpoint with zero vendored resources. Honest counter-arguments and explicit flip
conditions (provider plurality, resource-dominant shape, upstream contract churn) are in
decisions.md D1. The architect will independently enrich/confirm.

## Scope

### In Scope

- New tool module `daemon/tools/image_gen.py` implementing `generate_image` per the full
  design contract (req#3): env resolution with fail-closed envelope, httpx POST with
  identity headers + `asyncio.wait_for` wall-clock cap, defensive response parsing
  (single-object `data`, string-coerced counts, `base_resp.status_code==0` success gate,
  native partial success), immediate per-image fetch (OSS TTL), `image_save` with
  `feature="image-gen"`, marker emission.
- Tool registry: NEW non-privileged category `"image-gen"` + `KNOWN_TOOL_NAMES` update +
  frozen-names regen.
- Allowlist wiring: `"image-gen"` token added to the 6 agents' `meta.json` allow lists
  (ari, leader, developer[v2], reviewer, tester, worker).
- Minimal prompt-note additions to the 6 agents' `tools_note.md` per
  `docs/agent-prompt-writing-guide.md` conventions + `.prompt-artifacts.sha256` regen.
- Dispatcher marker-family generalization (`daemon/sources/dispatcher.py`): feature-family
  regex, provenance gate `{chart-render, image-gen}`, both-seam reuse, near-miss sweeper
  family behavior — chart-render byte-compat guaranteed.
- Angular frontend: marker constants module, post-render DOM scan → `<img>` injection →
  click-to-open lightbox → error fallback SVG (following the live-view-chip pattern).
- Chat adapter marker support: ZERO adapter code changes (native upload already shipped in
  all 3 adapters, 2026-10-04) — only tests proving the family flows through both seams.
- Test matrix: unit (tool / registry / dispatcher truth-tables / FE specs), integration
  (in-process dispatcher harness), e2e pack via dev.sh with Lane A (dev-proxy real
  upstream) and Lane B (live-proxy, user-gated) — ≤5 images TOTAL across ALL lanes,
  ~$0.05 spend cap, script-enforced.
- Seal re-pin ceremony for exactly the pinned files this commission touches
  (see Seal-Trip List below).

### Out of Scope (req#8 — verbatim)

- **Live promote** — the 3-factor nonce ceremony stays with the user. This commission
  merges to `feature/image-gen-tool` / latest-ready state only.
- **Proxy-side registration** — the live proxy's image-gen model row (OAuth'd FE UI,
  suggested id `minimax-image-01`, kind `image-gen`, internal, bound to the minimax
  credential) is USER-GATED; this commission reports readiness only.
- **Any key minting** — reuse only: `OPENAI_API_KEY` (existing) and the minimax key
  staged in the proxy repo `.env-minimax`. No new credentials.
- **Docker** — no Docker on this host, never install.
- **System installs** — no Node upgrades, no apt installs.
- **Explicitly excluded by design (not implicit-by-absence):** sketcher, designer, critic, jober — generation-lane separation (sketcher is the sole `od.*` generation lane; designer orchestrates) and critic stays a read-only QA leaf; none receive `generate_image`.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Daemon tool + config + registry + allowlists + prompt notes | `generate_image` exists, fail-closed, registered as category `image-gen`, allowlisted to 6 agents, prompts taught, developer[v2] seal re-pinned | 6 | tight with P2 (marker format contract); tight with P4 (test subject) | pending |
| 2 | Dispatcher marker-family generalization | ens-img extraction generalizes to a feature family; image-gen markers flow natively through chat seams; chart-render byte-compat proven | 7 | tight with P1 (marker grammar); tight with P4 (truth-table subject); tight with P3 (grammar mirrored FE-side) | pending |
| 3 | Frontend scanner + lightbox | image-gen markers render inline in web UI with click-to-open lightbox and error fallback | 6 | tight with P2 (regex byte-matched); independent of P1 runtime (works on any saved image id) | pending |
| 4 | Test/e2e matrix + live lanes + seal re-pin (tested step) | Full unit/integration/e2e matrix green; ≥1 live real image via Lane A; Lane B gated-with-disclosure; budget cap enforced | 6 | fan-in of P1+P2+P3; tight with dev.sh environment | pending |

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| Phase 1 | — | **tight** (marker grammar `'<!-- ens-img:image-gen:<32hex> -->'` is the P2/P3 contract) | independent (P3 consumes image ids, not the tool) | **tight** (P4 tests P1's tool + registry + fail-closed envelope) |
| Phase 2 | tight | — | **tight** (FE regex byte-matched to daemon family grammar) | tight (truth-tables + both-seam integration are P4 subjects) |
| Phase 3 | independent | tight | — | tight (FE specs run in P4 matrix) |
| Phase 4 | tight | tight | tight | — |

Phase ordering is dependency-forced: P1's marker format must freeze before P2's family
gate widens; P2's grammar must freeze before P3's FE regex mirrors it; P4 consumes all.

## Seal-Trip List (summary — full enumeration + ceremony in phase files)

[verified in worktree] The actual `SEALED_SHA_BASELINES` dict
(`tests/test_chart_image_delivery_e2e.py:1253-1296`) contains **35 files**: Phase A 8
(charter agent files + chart innate skill + ari workflow), Phase B 7 daemon files
(`daemon/sources/base.py`, `registry.py`, `dispatcher.py`, `adapters/discord/adapter.py`,
`adapters/slack/adapter.py`, `adapters/telegram.py`, `daemon/constants.py`), Phase C 20
agent canonical-homes. Intersected with every file this commission's phases touch, the
trip list is **exactly 2 files**:

| File | Tripping phase | Ceremony |
|------|----------------|----------|
| `daemon/sources/dispatcher.py` | P2 (marker family) | SHA refresh in `SEALED_SHA_BASELINES` + history entry (:1226-1252 pattern) + evidence pointer |
| `agents/developer[v2]/tools_note.md` | P1 (prompt note) | regenerate `agents/.prompt-artifacts.sha256` via `scripts/protection/generate_prompt_artifact_baseline.py` + SHA refresh + history entry + evidence pointer |

Deliberately NOT tripped: `daemon/constants.py` (caps live in the tool module, not
constants), `daemon/sources/base.py` (OutgoingMessage already has
`images`/`delivered_image_ids` — zero edits), all chat adapters (native upload already
shipped), `agents/ari/workflow.md`, `agents/{ari,leader,reviewer}/rule.md` (prompt notes
target `tools_note.md`, not `rule.md`), `agents/charter/meta.json` (our meta.json edits
hit the 6 consumer agents, none of which are pinned).

## Risks (req#7)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Proxy deadline vs gen time: live proxy default `image_gen_timeout` = 120s (504 on deadline) vs tool wall-clock budget ~240-300s; n>1 or I2I runs slower than the ~17s n=1 probe | Medium | Medium | Tool owns `asyncio.wait_for` cap (nothing kills a long native tool call today — MCP 120s timeout is MCP-only). Tool cap (240s) ≥ proxy deadline, so the proxy fails FIRST with a clean 504 → error envelope, never a hung tool. Live-lane 120s ceiling documented; raising the proxy knob is user-gated (report only). |
| 2 | Upstream 10 RPM — naive retry bursts exhaust RPM and burn spend | Medium | High if unmitigated | Contract bakes in: SINGLE attempt, NO retries inside the tool. Agent re-invokes deliberately. Unit test asserts exactly one POST. |
| 3 | OSS image URLs ~24h TTL — deferred fetch yields dead links | High | High if unmitigated | Tool fetches bytes IMMEDIATELY after parse (bounded per-image fetch timeout ~30s, size sanity ≤~15MB each) before `image_save`; tool response carries `view_url` pointing at our store, never at OSS. |
| 4 | Marker regex byte-compat regression (chart-render is LOCKED, R5-gated) | High | Low | Chart-render locked regex stays byte-identical; family extraction added as a PARALLEL pass after the locked pass (F12 pass-order invariant preserved and re-documented); ALL existing chart tests must pass with ZERO assertion edits — enforced as P2 DoD. |
| 5 | Seal ceremony mishandled (stale SHA, wrong delta scope) | Medium | Medium | Explicit tested re-pin step in P4; re-pin only if deltas are EXACTLY commission edits (git diff audit at ceremony time); history entry per :1226-1252 pattern + evidence pointer. |
| 6 | Slack `files:write` in manifest but workspace grant unverified at runtime | Low | Medium | Smoke-time verification step in P4; graceful text-only degradation already exists (`adapters/slack/adapter.py:638-676`) so worst case is disclosed degradation, not failure. |
| 7 | Upstream response quirks: `data` is a SINGLE OBJECT (not array) and `metadata.success_count`/`failed_count` are STRINGS | Medium | High if unmitigated | Defensive parsing in tool contract + unit truth-tables for both quirks (string-coerce counts; read `data.image_urls` on the object). |
| 8 | Live lane blocked on user-side model-row registration | Low (Lane A unblocked) | High | Dual-lane design: Lane A (local dev proxy seeded from `.env-minimax` staged key — reuse, never mint) proceeds unblocked; Lane B = skip-with-disclosure path if still unregistered at test time. |
| 9 | Category leak: reusing `"image"` category would grant `generate_image` to critic (critic allow includes `"image"`; deny is per-tool `image_save` only) | Medium | High if unmitigated | NEW category `"image-gen"` + explicit registry test asserting critic is NOT granted. |
| 10 | Cost overrun in e2e lanes | Low | Low | ≤5 images TOTAL across ALL lanes, ~$0.05 cap ($0.0035/image documented), script-enforced counter as explicit DoD. |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Tool granted to exactly the 6 intended agents; critic NOT granted | Registry unit test resolving category→agents | 6/6 granted, critic absent (test passes) |
| 2 | Fail-closed config envelope | Unit test: unset `IMAGE_GEN_MODEL` → tool returns `"Error: ... image_gen_not_configured"` envelope `{ok:false, code, message}` | No stack trace; no raise; envelope code exact |
| 3 | Wire contract correctness | `httpx.MockTransport` test (mock INSIDE wrapper under test) asserting POST path `<base>/image_generation`, request JSON (`model`, `prompt`, sizing fields), identity headers present | All assertions pass |
| 4 | Live real image end-to-end | Lane A dev.sh e2e: agent calls tool → real MiniMax upstream → image saved → native chat attachment via ≥1 adapter | ≥1 live real image; images ≤5 TOTAL across ALL lanes; spend ≤ ~$0.05 (script-enforced counter) |
| 5 | Chart-render byte-compatibility | Run ALL pre-existing chart dispatcher tests unmodified | 100% pass with ZERO edits to existing assertions |
| 6 | Family truth-tables | New `TestExtractImageGenMarkersUnit` (clone of chart unit at `tests/test_sources_dispatcher.py:1337` [verified in worktree]) | Extraction, dedupe, first-occurrence order, strip-from-text, near-miss sweep all pass |
| 7 | Web UI rendering | FE spec suite: marker → inline `<img>`, click → `openViewer` lightbox, broken image → fallback SVG | All FE specs pass; `tsc --noEmit` clean via `cd frontend && ./node_modules/.bin/tsc` (never bare tsc/npx outside frontend/) |
| 8 | Native chat delivery | Integration harness: family marker through `dispatch_completed` AND `dispatch_message` seams → `OutgoingMessage.images` populated for mock source | Both seams deliver; delivered_image_ids recorded; blob deleted after successful delivery |
| 9 | View URL serving | Smoke: GET `/views/tmp-images/<saved-id>` returns saved bytes (existing router — zero daemon router change expected) | 200 + correct content-type |
| 10 | Seal integrity | `test_phase_d_does_not_modify_sealed_artifacts` green post-re-pin with exactly 2 new history entries | Seal test passes; `.prompt-artifacts.sha256` regenerated |
| 11 | Env-fenced e2e | dev.sh boot on port 8079 with `ENSEMBLE_SELF_ENV` fence (worktree .env already fenced by giter) | Boot + e2e green in fenced env |

## Research Insights (recon file:line pins shaping this plan)

- Delivery leg ~90% prebuilt: native upload in all 3 adapters (Discord
  `adapters/discord/adapter.py:1523-1556`, 8MB cap `constants.py:33`; Telegram
  `adapters/telegram.py:172-327`/`498-563`; Slack `adapters/slack/adapter.py:620-626`,
  graceful degradation `:638-676`); `OutgoingMessage.images` + `delivered_image_ids`
  exist (`sources/base.py:83-123`).
- Marker machinery: LOCKED chart regex `sources/dispatcher.py:29-32`, near-miss sweeper
  `:50-53`, `extract_chart_images` `:74-117` called at BOTH seams `:348` and `:472`,
  provenance gate `feature=="chart-render"` `:149-150`, blob delete-after-delivery
  `:388-408`/`:508-528`, API/no-colon sources skip extraction `:302-304`.
- Tool substrate: `image_save` at `daemon/tools/image_tools.py:671` [verified in worktree;
  recon said :669 — actual def line 671, same function], returns
  `{image_id, content_type, size_bytes, sha256_hex, provenance, retention_class}`;
  `retention_class` param at `:678`; failures are `"Error: ..."` strings.
- Registry: `CATEGORY_MODULES` at `daemon/tools/_tool_registry.py:594-595` [verified in
  worktree: `"chart": "daemon.tools.chart_tools"`, `"image": "daemon.tools.image_tools"`];
  `register_tool_category` at `:223`; frozen-names drift pinned by
  `tests/unit/tools/test_frozen_tool_name_discovery.py`; regen snippet
  `_tool_registry.py:702-713`.
- Critic-leak evidence for D3: `agents/critic/meta.json` allow =
  `['read_file', 'image', 'design']`, deny includes `image_save` (per-tool)
  [verified in worktree].
- Allowlist model confirmed explicit-add: all 6 consumer agents already carry `"image"`
  in their `meta.json` allow lists [verified in worktree].
- Prompt governance: canonical home `agents/{name}/tools_note.md`; sanctioned lane =
  source-tree edit + `agents/.prompt-artifacts.sha256` regen via
  `scripts/protection/generate_prompt_artifact_baseline.py`; NEVER touch
  `releases/<ver>/**`. All 6 consumer agents HAVE `tools_note.md` [verified in worktree].
- Config pattern: OD purpose-bound resolver `daemon/plugin_subsystem/opendesign/generate.py:697-742`
  (env-direct read; purpose knob `OPENAI_MODEL_VISION`, never `OPENAI_MODEL`);
  fail-closed envelope template `:1467-1507`; identity headers house-parity `:649-658`.
- Frontend anchors [verified in worktree — real paths]: constants pattern
  `frontend/src/app/constants/live-view-url.ts`; scan pipeline
  `frontend/src/app/components/chat-interface/chat-interface.component.ts`
  (`ensureMutationObserver` :852, `scheduleScan` coalescing :184/:195/:200/:867,
  `scanForLiveViewChips`/`injectLiveViewChip` per recon :953-1079); lightbox via
  `openViewer(src, messageId)` at
  `frontend/src/app/services/image-viewer-actions.service.ts:44`; dialog error-fallback
  pattern `frontend/src/app/components/image-viewer-dialog/image-viewer-dialog.component.ts:46-61`;
  body renders via ngx-markdown `<markdown [data]>` (`chat-interface.html:146-158`, zero
  marker handling today). `frontend/design-tokens/` is EMPTY (aspirational) — not cited
  as convention.
- `/views/tmp-images/<id>` already served by `daemon/routers/live_views.py` [verified in
  worktree] — zero daemon router change.
- Proxy contract (verified read-only @ b2660a6 by caller): endpoint `<base>/image_generation`
  (NOT `/v1/images/generations`); POST-only; proxy parses only `model`; response relayed
  byte-verbatim with single-object `data`, STRING counts, `base_resp.status_code==0`
  success gate, app errors inside HTTP 200 (2013/1002/1004/1008/1026/2049), partial
  success native; OSS URLs ~24h TTL; 10 RPM; n=1 latency ~17s; live proxy deadline 120s
  (knob `image_gen_timeout`, bounds 30s-4min, 504); auth = existing `OPENAI_API_KEY`
  chain (live-verified PASSING); $0.0035/image; n images ⇒ n URLs.
  **[AM1 consequential]** The "app errors inside HTTP 200" clause above is the historical recon citation, superseded-by-AM1 — ensemble-facing truth: app errors arrive as HTTP 502 + upstream body verbatim (the in-200 form is the upstream→proxy leg only); live contract = phase1-plan.md.
- Test conventions: in-process chart e2e template (real TmpImageStore + real
  ResponseDispatcher, LLM stubbed); `MockSourceAdapter`
  (`tests/e2e/mock_source_server.py:70`/:8099); `E2E_BASE_URL` convention
  (`tests/e2e/test_e2e_workflows.py:91`); httpx.MockTransport canonical
  (`tests/unit/test_llm_failover_v2.py:326-478` — mock INSIDE the wrapper under test);
  slack real-SDK + transport-stub pattern (`tests/test_slack_adapter.py:2043-2235`);
  packs `test/packs/*.sh` indexed `.agents/tester/PACKS.md`; pytest markers
  integration/postgres (`pyproject.toml:67,86-95`).
- Test-fencing rules (CRITICAL): test scripts NEVER resolve live-shaped paths — mktemp
  FAKE_HOME + escape guard (live-install deletion incident 2026-10-12). TypeScript ONLY
  via `frontend/`'s local devDependency. Chart truth-table unit anchor:
  `TestExtractChartImagesUnit` at `tests/test_sources_dispatcher.py:1337` [verified in
  worktree; recon said 1305-1460 range — class def at 1337].

## Open Questions

1. **Live proxy knob raise (user-gated):** if Lane B runs against the live proxy, its
   default 120s deadline may 504 on slower generations. Raising `image_gen_timeout` is a
   user-side action — reported at test time, not performed by this commission.
2. **Slack workspace grant** is only verifiable at smoke time (P4); if ungranted, Lane A
   Slack leg is disclosed-degraded (text-only), not blocking — other adapters carry the
   native-delivery criterion.
3. **Frontend chart-render markers in web UI:** chart markers survive in API/no-colon
   source threads for web consumers. This commission renders ONLY `image-gen` markers in
   the scanner (chart-render web behavior untouched). If the team later wants
   chart-render inline rendering in web UI, that is a separate commission (noted, not
   planned).

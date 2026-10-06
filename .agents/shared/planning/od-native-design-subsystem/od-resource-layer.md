# Open Design — Reusable Resource Layer Inventory & Coupling Analysis

**Purpose:** feeds the architecture decision on replacing the OD MCP dependency with an ensemble-native design subsystem that reuses OD's resources directly (user direction).
**Investigated:** 2026-10-06 · Wanderer instance `30a87725` (read-only; 3 delegated investigators + direct git/license work).
**Source:** `~/opt/open-design` @ `53231d40` (main tip; package version 0.23.1-dev). Git history deepened to ~506 commits (2026-08-12 → 2026-09-30); tags `open-design-v0.24.1` (89e64d81) and `open-design-v0.23.0` (c2aba144) fetched for churn analysis. No working-tree changes.
**Demarcation:** engine runtime behavior (thinking/reasoning handling, token accounting, stream-internals root-cause) is covered by the sibling investigation (`fe437579`, "OD Engine Architecture Review"). This document owns the **resource layer, coupling, storage, lint, license, churn**.

---

## 0. Executive Summary

1. **The MCP lane we consume is prompt-agnostic by design.** `od_generate_design` → `POST /api/proxy/<provider>/stream` takes `systemPrompt` **verbatim from the request body** (`apps/daemon/src/routes/chat.ts:986-1395`); the daemon does zero server-side prompt composition on that lane. All prompt value lives in (a) composable prompt modules in-repo and (b) the external npm shim that assembles them. An ensemble-native subsystem can own prompt composition outright.
2. **The data-resource layer is plain files, near-frozen, and Apache-2.0.** 154 design systems, 115 design templates, 13 craft guidance docs, 106 media prompt-templates — all standalone data with documented folder contracts. **Zero churn across the full v0.23.0 → v0.24.1 minor version**; ≤8 commits per dir in a 7-week window.
3. **The prompt layer is code, not files** — inline TS template literals behind a composer (`composeSystemPrompt`). High craft, heavily tested, actively churned (`@open-design/contracts`: 106 commits in window). Reuse = port the composer precedence logic or consume the published contracts package.
4. **Turn-3 / compose-brief orchestration is NOT in this repo** — it lives in the external `open-design-mcp` npm package. In-repo, only the brief *question catalog* (`packages/contracts/src/api/brief.ts`, `open-design-shared-brief-v1`) exists.
5. **Lint would NOT have caught our live truncation failure** — the MCP-facing lint is a self-declared "greppy" regex anti-slop linter with no completeness checks; a real parse5 EOF gate exists but is wired only to OD's internal runs pipeline.
6. **License: Apache-2.0 → GO.** Extraction/reuse inside ensemble is permitted with license-copy + attribution + change-statement. Exceptions: media prompt-templates are CC-BY-4.0-sourced (per-item attribution preserved); brand-named design systems carry a trademark caveat.

---

## 1. Agent Prompts — Complete Inventory

All paths relative to repo root. History window = 506 fetched commits (2026-08-12 → 2026-09-30); "first appearance" at window floor means history is deeper.

### A. Daemon prompt stack — `apps/daemon/src/prompts/` (10 TS modules, 4,668 lines)

Format: **inline TS template literals + render functions**; pure gated `parts.push()` concat; one `.replace()` placeholder mechanism (`HANDOFF_INVARIANT_PLACEHOLDER`, `discovery.ts:263-272`; `%%OPEN_DESIGN_EXECUTION_CONTEXT%%` / `%%OPEN_DESIGN_WORKFLOW_HANDOFF%%`, `official-system.ts:14-16`). No i18n string tables — localization is a runtime locale *instruction*.

| File | Lines | Role | Tests | Git signal |
|---|---|---|---|---|
| `system.ts` | 2,238 | Composer hub: `ComposeInput` (~40 params, :726-900), `composeSystemPrompt()` (:900+), OD-Next fork (:936), `SKIP_DISCOVERY_BRIEF_OVERRIDE` (:440), example-prompt override (:666) | `system.test.ts` 850L + matrix/snapshot 695L + 5 more | 15 commits in window; last 2026-09-07…09-10 |
| `official-system.ts` | 203 | `OFFICIAL_DESIGNER_PROMPT` — base "expert designer" charter (identity/workflow/anti-slop/data-od-id) | via system tests | window floor |
| `discovery.ts` | 318 | **Turn-1 discovery-form syntax**: `<question-form id="discovery">` (:49-67), brand branching brand_spec/reference_match/pick_direction (:100-133), 8-section philosophy | `discovery-form.test.ts`, localization-drift test 164L pinning BOTH copies | window floor |
| `core-slim.ts` | 438 | `SLIM_CORE_CHARTER` (default core since ~Sep), injection-resistance, platform contracts | `core-slim.test.ts` 840L (biggest single prompt test) | window floor |
| `directions.ts` | 338 | 10 design-direction records (OKLch palettes) — **dormant** (visual-style question retired, OPEND-2760) | via system tests | window floor |
| `media-contract.ts` | 612 | image/video/audio shell-out contract; model lists data-driven from `media/models.ts` | mirror test | window floor |
| `panel.ts` | 226 | critique-panel protocol; **ZWJ-sanitizes brand/skill text vs tag injection** (:67-75) | host-failure + od-next tests | 1 commit (floor) |
| `research-contract.ts` | 73 | research-command shell syntax (Tavily params) | — | — |
| `stable-sections.ts` | 212 | not prompt text: sha256 per-section drift attribution (prompt-cache telemetry) | own test | — |
| `deck-framework.ts` | 10 | pure re-export from `@open-design/contracts` (anti-drift shim) | via contracts tests | — |

**Craft assessment (worker A):** very high sophistication — layered precedence ("pinned LAST so hard rules win"), fork-point architecture, protocol-tag injection sanitization, prompt-cache drift telemetry. Weak spot: maintainability (two-tree drift, below). Test corpus exceptional: ~4,465 lines across 20 files in `apps/daemon/tests/prompts/`.

### B. Published prompt mirror — `packages/contracts/src/prompts/` (17 files, 5,969 lines)

Same architecture, exported from npm package `@open-design/contracts`. Mirrors daemon files by name (`system.ts` 1,183L w/ own `composeSystemPrompt` :285, `od-next-strategy.ts` 1,061L, `deck-framework.ts` 589L = canonical deck source, `discovery.ts` 287L, …) plus OD-Next-only modules (`canonical-xml.ts` 389L, `od-next-prompt-bundle-v2.ts` 551L, …).
**Drift is real and documented** (`docs/prompt-composition.md:70-83`): daemon `system.ts` 2,048L vs contracts 1,141L; `media-contract` 594 vs 154; **`core-slim.ts` has no contracts twin** (API/BYOK lane cannot receive the slim charter — flagged "unexplained asymmetry" in-repo). Parity enforced only per-file by mirror tests (`media-contract-mirror.test.ts:38`, `turn-rendering-mirror.test.ts:45`).
Git: 106 commits in window; last 2026-09-24 — **the hottest-changing area of the resource layer.**

### C. Brief catalog — `packages/contracts/src/api/brief.ts` (509 lines)

Frozen data catalog + deterministic collectors: `openDesignBriefCatalog` for 8 artifact types (website, product-prototype, presentation, document, image, video, audio, design-system), 2-3 radio questions each, `decisionSource: 'open-design-shared-brief-v1'` (:38); `collectOpenDesignBrief()`, `formatOpenDesignBriefForCli()` (:485-509). **A question catalog, not prose-prompt composition** — the answers→prose step is not here. Git: 2 commits, last 2026-08-18.

### D. OD-Next scenario assets — `plugins/_official/scenarios/od-next-strategy/` (standalone .md — the only prose-markdown system prompts in-repo)

- `assets/core-system-prompt.md` — 361 lines, versioned (v2.2.1) in its H1: role / operating priorities / route-stage orchestration.
- `assets/task-profiles/{ppt,marketing,prototype,hyperframes}.md` — 214/188/318/161 lines + `prototype/device-frames/*.html` + `layout.css`.
- 13 more sibling scenario `SKILL.md`s (`od-default` with the task-type question-form, `od-react-export`, `od-media-generation`, …) + 13 atom prompt fragments (`plugins/_official/atoms/*/SKILL.md`) inlined via `loadAtomBodies` (`server.ts:10475-10495`, kill switch `OD_BUNDLED_ATOM_PROMPTS=0`).
- Wrapped by `composeOdNextStrategyRequestPromptV2` (`contracts/prompts/od-next-strategy.ts`).
Git: born 2026-08-24, last 2026-09-08 — within window.

### E–I. Remaining prompt-bearing assets

- **`skills/`** — 163 `SKILL.md` bodies (230 .md, 4.7 MB): YAML frontmatter + markdown; **prompt-inputs** (injected wholesale as `skillBody`, stack layer 3), not prompts. 5 commits, last 2026-09-08.
- **`craft/`** — 11 guidance docs + README + FUTURE_SECTIONS (1,948 lines; largest `laws-of-ux.md` 296L). Style-guidance resources injected when a skill declares `od.craft.requires` (`system.ts:786-795`). Last 2026-08-18.
- **`prompt-templates/`** — 106 JSON (48 image + 58 video, 18-31 lines each): `{id, surface, title, summary, category, tags, model, aspect, prompt, previewImageUrl, source{repo,license,author,url}}`; own `{argument name="…" default="…"}` placeholder dialect; **CC-BY-4.0-sourced** (see §7). Not part of system-prompt composition; gallery + prompt source. 2 commits.
- **One-shot synthesis prompts** — `apps/daemon/src/design/handoff-design.ts:84` (269L, session→handoff synthesizer) and `design/finalize-design.ts:836` (982L, finalize synthesizer); own truncation plumbing.
- **Prompt-adjacent constants** — `SKIP_DISCOVERY_BRIEF_OVERRIDE` (`system.ts:440`, the API-lane skip-discovery switch), `MEDIA_DISPATCH_HINT` (:464+), locale block (`renderUiLocalePrompt`), custom-instructions headers (:1263-1270); `runtimes/chat-prompt-inputs.ts` (982L) + `chat-run-context.ts` (281L) per-run payload builders. Web client holds **zero** generation prompts (only forwards client-composed `system`, `apps/web/src/providers/api-proxy.ts:68`). `.claude/` + `AGENTS.md` = contributor-process meta, not design prompts.

### Runtime assembly chain (daemon-composed lane)

```
POST /api/runs | /api/chat (server.ts:17521)
→ startChatRun (server.ts:10886)
→ composeDaemonSystemPrompt (server.ts:9810)      ← resolves skills, DESIGN.md, tokens.css,
   components manifest, craft bodies, memory, plugin/atom blocks
→ customInstructions: app-level (server.ts:10202-10204) + project-level (:10206)
→ fork (system.ts:936): OD-Next recipe ? composeOdNextStrategyRequestPromptV2()
   : composeSystemPrompt()  — slim charter (default) or classic stack, then gated pushes:
   locale → memory (:1231) → userInstructions (:1263) → projectInstructions (:1267)
   → DESIGN.md (:1272+) → tokens.css (:1309) → craft → skillBody → media/deck LAST
```

**Lane split (decisive for us):** the daemon-composed lane is `/api/runs` + `/api/chat` (CLI-agent spawn). **Our MCP lane (`/api/proxy/<provider>/stream`, `routes/chat.ts:986/1032/1184/1348/1395/2365`) takes `systemPrompt` verbatim from the request body** — zero server-side composition. The npm shim composes client-side (mirroring `@open-design/contracts` semantics, `customInstructions`/`brandSpec`/`briefAnswers` merge — the MCP tool descriptions confirm the precedence).

### Turn-3 / compose-brief verdict

**Not in this repo** (🟢): `composeBrief`/`brandSpec`/`briefAnswers`/"Turn 3" = zero source hits. In-repo fragments: Turn-1 form syntax (`discovery.ts`), the deterministic brief catalog (`contracts/src/api/brief.ts`), `SKIP_DISCOVERY_BRIEF_OVERRIDE`. The form→collect→brand-spec-prose→final-prompt **orchestration lives in the external `open-design-mcp` npm package.**

---

## 2. Design Resources — Inventory

| Family | Path | Count | Format | Consumed by (evidence) | Verdict |
|---|---|---|---|---|---|
| Design systems (bundled) | `design-systems/<brand>/` | **154 dirs** | Folder contract `od-design-system-project/v1`: `manifest.json` (machine), `DESIGN.md` (prose) + ~16 translated `DESIGN-<lang>.md` siblings, `tokens.css`, `design-tokens.json` (derived), `tailwind-v4.css` (derived), `components.html`, `components.manifest.json` (rebuildable), `USAGE.md`, `preview/*.html`, `assets/`, `fonts/`, `source/` | `apps/daemon/src/design-systems/index.ts:319-368` (readdir + frontmatter); routes `routes/design-systems.ts:517-1029` | **Plain data** |
| Schema contract | `design-systems/_schema/` | 4 files | `manifest.schema.ts`, `tokens.schema.ts` (re-export contracts), `defaults.css` (A2 fallbacks), `AGENTS.md` | `scripts/check-design-system-manifests.ts` guard | **Plain data + types** |
| Design templates | `design-templates/<name>/` | **115 dirs** | `SKILL.md` (frontmatter `name/description/triggers/od.mode/…`) + baked `example.html` (+ `assets/`, `examples/`) | `apps/daemon/src/skills.ts:232`; `routes/static-resource.ts:548-572` | **Plain data** |
| Craft guidance | `craft/*.md` | 13 files | topic-scoped prose; `# ` heading = label | `routes/design-systems.ts:995-1029` (`/api/craft`); joined via `manifest.craft` (`design-systems/index.ts:223-235`) | **Plain data** |
| HTML templates | `templates/` | 3 entries | `deck-framework.html`, `kami-deck.html`, `live-artifacts/otd-operations-brief` | **No daemon code path consumes these** — orphaned on current main | **Plain data, ORPHANED** (deprecated) |
| Media prompt-templates | `prompt-templates/{image,video}/*.json` + `assets/prompt-templates/image/` (11 preview JPGs) | 106 JSON | permissive JSON schema (`media/prompt-templates.ts:17-30`), `{argument}` dialect, per-item `source` provenance | `apps/daemon/src/media/prompt-templates.ts:36-60`; `routes/static-resource.ts:869-880` | **Plain data** (CC-BY-4.0 attribution per item) |
| Brand presets | code `apps/daemon/src/brands/` (19 modules); runtime `<DATA>/brands/` (empty here) | — | `<id>/{brand.json, meta.json, BRAND.md, logos/, fonts/}`; engines build/deck/email/form/generic/landing/newsletter/palette/poster | `brand-routes.ts:314-695`; finalize → `createUserDesignSystem` | **Needs OD runtime** (agent extraction loop + browser) |
| Design tokens | inside every design system: `tokens.css` + derived `design-tokens.json` (Design Tokens JSON v1) + `tailwind-v4.css` | 154× each | CSS custom props on `:root`; 4-layer contract A1-identity / A1-structure / A2 / B-slot + C-extensions (`packages/contracts/src/design-systems/token-schema.ts:1-60`) | `design-systems/index.ts:601` (inlines verbatim into artifacts), `:865` (pullIndex); guard `design-systems/token-contract.ts` | **Plain data** (contract semantics must be understood) |
| Functional skills | `skills/<name>/` | **165 dirs** | `SKILL.md` frontmatter-only stubs pointing at `od.upstream` URLs (~half); ~half real skill definitions (code-as-prose) | `apps/daemon/src/skills.ts:232` (USER dir shadows built-ins); `routes/static-resource.ts:497+` | **Plain data as catalogue**; execution needs upstream bundles |
| UI assets | `assets/frames/*.html` (5+), `assets/community-pets/` | — | device-frame HTML, pet spritesheets | `server.ts:1339-1352` resource roots; pets via bake script | Plain data; **UI-grade, not design resources** |

Sample contract shapes (one-liners):
- manifest: `{schemaVersion, id, name, category, description, source{type,origin}, files{design,tokens,designTokens,tailwind,components}, usage, componentsManifest, importMode, craft{applies,suggested,exemptions}, preview{dir,pages[]}, sourceFiles{…}}` (`design-systems/airbnb/manifest.json`)
- design-template frontmatter: YAML `{name, description, triggers[], od{mode, surface, scenario, preview{…}, design_system{requires}, example_prompt}}` + body `## Resource map / ## Workflow / ## Hard rules` (`design-templates/audio-jingle/SKILL.md`)

---

## 3. Lint Rules & Truncation Verdict

**Where:** single implementation, pure code — `apps/daemon/src/lint-artifact.ts:120-510` (`lintArtifact`, 16 checks, ~390 lines); renderer `:519-537`; DTO `packages/contracts/src/api/artifact-lint.ts` (P0/P1/P2 + id/message/fix/snippet). Endpoints: `POST /api/artifacts/lint` (`routes/project/index.ts:5768-5783`), save-time lint `POST /api/artifacts/save` (`:5740-5761` — writes file first, **never blocks**). Self-declared greppy: *"It does NOT parse HTML"* (`lint-artifact.ts:11-13`).

**The 16 rules:** `purple-gradient` (P0), `trust-gradient` (P0), `ai-default-indigo` (P0), `emoji-icon` (P0), `left-accent-card` (P0), `sans-display` (P0), `invented-metric` (P0), `filler-copy` (P0), `scroll-into-view` (P0), `slide-theme-missing` (P0), `all-caps-no-tracking` (P1), `external-image` (P1), `raw-hex` (P1), `accent-overuse` (P1), `slide-rhythm` (P1), `missing-section-anchor` (P2) — all design-**slop** pattern matches (line refs in worker report).

**Truncation verdict: NO — by construction.**
1. Input gate accepts any non-empty string; empty → clean pass (`lint-artifact.ts:122`). No parser, no tag-balance, no EOF check anywhere in :122-510.
2. CSS rules require a **closed** `<style>…</style>` (`:319-321`, `:408` regexes) — a doc cut mid-CSS has no closing tag, so `all-caps-no-tracking`/`raw-hex` silently skip. Truncation doesn't fire findings; it *disables* checks.
3. Test corpus confirms intent: zero truncation/unclosed/EOF tests in `tests/lint-artifact.test.ts`.
4. **A real truncation detector exists in-repo but is wired elsewhere:** `apps/daemon/src/artifacts/deliverable-syntax.ts:47-55` — `FATAL_HTML_PARSE_ERRORS` (parse5 codes incl. **`eof-in-element-that-can-contain-only-text`** = exactly "cut mid-CSS inside `<style>`"), parsed via cheerio 1.2.0 → parse5 ^7.3.0 (`apps/daemon/package.json:54`), surfaced as diagnostics at `:301-313`. Wired ONLY to the internal runs finalizer (`deliverable-syntax-finalization.ts:107` ← `successful-run-deliverable-finalization.ts:82`, with LLM repair loop `deliverable-syntax-repair.ts`) and a run-scoped tool route. **Never invoked from `/api/artifacts/lint` or `/save`.**

**Proxy stream (our generate lane):** `routes/chat.ts` — client disconnect aborts upstream (`:732-738`, `:1094`); upstream error → SSE error event (`:1155-1162`); **stream end WITHOUT `[DONE]` (max-token cutoff) → clean `end` event, no error, no truncation flag, no marker** (`:1163`). No partial-HTML marker is appended anywhere in-repo (only an unrelated role-injection guard appends text, `:687-693`). → Our 7820B mid-CSS artifact = shim-side assembly of deltas after a clean `end`; indistinguishable from complete.

**Port note:** the slop linter is a dependency-free pure function (~390 lines of regex rules) — mechanically portable. The parse5 EOF gate concept ports to Python via an html5-spec parser with error reporting (e.g. html5lib) keyed on the same EOF error classes; the `FATAL_HTML_PARSE_ERRORS` list itself is the spec.

---

## 4. Storage Model

- **Engine:** better-sqlite3 **12.10.0** — the only DB dependency (`apps/daemon/package.json:43`). No postgres/prisma/drizzle/knex anywhere. Second sqlite for telemetry (`storage/diagnostic-outbox.ts:23`, `diagnostics/outbox.sqlite`).
- **Main DB:** `<RUNTIME_DATA_DIR>/app.sqlite` (`db.ts:76-89`), WAL, FK on, inline `migrate()`. **Schema is inline `db.exec()` strings in `apps/daemon/src/db.ts` (5,490 LoC) — zero `.sql` migration files, no migration framework.**
- **Entities (selection):** `projects` (`id, name, skill_id, design_system_id, pending_prompt, metadata_json, …` `db.ts:100-109`), `workspace_projects`, `templates` (user-saved snapshots — distinct from the orphaned `templates/` dir), `conversations`/`agent_sessions`/`messages`/`message_event_batches`, chat-artifact ledger (`chat-artifacts/store.ts:142-266`), `media_tasks`, `critique_runs`, strategy tables, registry entries. **No FTS5, no vector index, no embeddings** — search is a filesystem walk (`routes/project/index.ts:6537-6564`).
- **On-disk layout** (composition root `server.ts:1266-1459`): `RUNTIME_DATA_DIR` = `OD_DATA_DIR` → `<projectRoot>/.od` (default). `projects/` (per-project working files — **source of truth for user files**, `db.ts:1-3` comment; write path `projects.ts:926` + sidecar manifest), `artifacts/` (`<stamp>-<slug>/index.html` via `/api/artifacts/save`), `brands/`, user `skills|design-systems|design-templates/` (shadow built-ins, priority USER-first), `library/`, `diagnostics/`.
- **Source vs derived:** project folders + SQLite metadata = source of truth. Derived/rebuildable: `components.manifest.json`, `design-tokens.json`, `tailwind-v4.css` (guarded by `scripts/check-design-system-manifests.ts`; formats `od-design-tokens/v1`). In-memory-only cache: `designSystemAssetsCache` (`design-systems/index.ts:585-588`).
- **`.od/` disambiguation:** git-ignored (`.gitignore:14-15`), 0 tracked files — **local runtime output, not seed data**. Reuse value: zero.
- **HTTP API = the load-bearing seam:** every resource family has a route; an in-repo stateless MCP forwarder exists (`apps/daemon/src/mcp.ts:365-848`, tools `list_projects`/`get_project`/…/`start_run` — forwards to `OD_DAEMON_URL`).

---

## 5. Coupling Table (key deliverable)

Classification: **STANDALONE** = plain file/data directly loadable · **LIGHTLY COUPLED** = needs a thin adapter (template fn / small code port) · **ENTANGLED** = requires OD runtime/DB/UI to function.

| # | Resource | Class | What an ensemble-native reuser must port |
|---|---|---|---|
| 1 | `design-systems/*` (154 bundles) | **STANDALONE** | File copy + loader honoring `manifest.json` paths. Must *understand* A1/A2/B-slot token semantics (`contracts/design-systems/token-schema.ts`, ~60 lines of concepts; `defaults.css` reference). Guard code optional. |
| 2 | `design-systems/_schema` | **STANDALONE** | Copy; TS schemas are reference docs. |
| 3 | Token contract (schema + `defaults.css`) | **LIGHTLY COUPLED** | Re-implement the 4-layer validation as data checks (~1 day); OR skip validation and consume tokens as-is. |
| 4 | `design-templates/*` (115) | **STANDALONE** | Copy + frontmatter parser. Deck-mode `example.html` embeds its own runtime contract (kb/wheel/touch/dots) — ship HTML as-is. |
| 5 | `craft/*` (13 docs) | **STANDALONE** | Copy verbatim. |
| 6 | `templates/` top-level | STANDALONE (orphaned) | Nothing — deprecated on main; do not ship. |
| 7 | `prompt-templates/*` (106 JSON) | **STANDALONE** | Copy + ~20-line `{argument}` resolver; **preserve per-item CC-BY-4.0 `source` attribution**. |
| 8 | `skills/*` (165) | **STANDALONE** (catalogue) | Copy frontmatter for discovery; functional use requires vendoring upstream bundles (`od.upstream`). |
| 9 | Brand presets (brands flow) | **ENTANGLED** | Would require porting 19 modules (`apps/daemon/src/brands/*`) + agent loop + browser. Skip; not needed for mockup lane (design systems cover brand styling). |
| 10 | Daemon prompt stack (`apps/daemon/src/prompts/`, 10 modules) | **LIGHTLY COUPLED** (medium) | Text is portable; **precedence lives in composer push-order** (`system.ts:900+`). Port composer logic to Python (or regenerate via Node sidecar); copy `docs/prompt-composition.md` as the map. Hours-to-days, not weeks. |
| 11 | `@open-design/contracts` prompt mirror | **LIGHTLY COUPLED** | Either consume the npm package (Node sidecar) — the intended external surface — or port and accept drift-tracking duty. Note `core-slim` absence on this side. |
| 12 | OD-Next assets (`plugins/_official/scenarios|atoms`) | **LIGHTLY COUPLED** | .md bodies are standalone; wrapper logic (`composeOdNextStrategyRequestPromptV2`) is code — port if adopting the OD-Next lane. |
| 13 | Brief catalog (`contracts/src/api/brief.ts`) | **LIGHTLY COUPLED** | Deterministic TS data + collectors → mechanical port. |
| 14 | Turn-3 compose-brief orchestration | **NOT IN REPO** | Must be re-implemented (or extracted from the `open-design-mcp` npm shim — separate package, out of scope here). Seed semantics from the brief catalog + shim's tool description. |
| 15 | Slop linter (`lint-artifact.ts`) | **LIGHTLY COUPLED** | Dependency-free pure function; translate 16 regex rules. |
| 16 | parse5 EOF truncation gate (`deliverable-syntax.ts`) | **LIGHTLY COUPLED** (concept) | The `FATAL_HTML_PARSE_ERRORS` EOF-code list is the spec; Python html5-spec parser with error reporting replicates it. **We should port this — it catches exactly our live failure.** |
| 17 | OD SQLite storage (db.ts inline schema) | **ENTANGLED** | Don't port. If OD daemon stays running, use its HTTP API; else treat artifacts as files (they are). |
| 18 | In-repo MCP forwarder (`mcp.ts`) | **ENTANGLED** | Only meaningful against a live daemon. |
| 19 | `assets/frames`, community-pets | STANDALONE | UI-grade; optional. |

**Minimum viable ensemble-native set:** #1/#2/#4/#5 (data copies) + #10 or #11 (prompt composition) + #13/#14 (brief flow — 14 must be authored) + #15/#16 (lint + truncation gate). Everything else optional.

---

## 6. Upstream Churn

- **Lineage:** clone `53231d40` == `origin/main` tip today (0 commits between). `open-design-v0.24.1` (89e64d81) is on a **divergent release line** (merge-base `00c2d8ae`; 13 commits tag-side / 19 main-side since).
- **`53231d40..v0.24.1`:** 69 files, +1,415/−4,151 — all in docs/i18n (13), screenshots (9), CI, `apps/daemon` diagnostics (10), `apps/web` pets/nav (6), `packages/diagnostics`, `tools/release`. **ZERO changes** in `design-systems/`, `design-templates/`, `craft/`, `skills/`, `prompt-templates/`, `templates/`, `packages/contracts`, `apps/daemon/src/prompts`, `lint-artifact.ts`, `artifacts/`, `docs/prompt-composition.md`, `CHANGELOG.md`.
- **Full minor `v0.23.0..v0.24.1`:** 388 files overall (+37,824/−4,054) — but resource paths: **only 3 files** (`lint-artifact.ts`, `contracts/prompts/od-next-intent-resolution.ts`, `contracts/prompts/od-next-strategy.ts`). Data-resource churn across a full minor: **zero**.
- **Intra-window activity (506 commits, ~7 weeks):** design-systems 5 commits (last 2026-08-19) · design-templates 8 (2026-09-07) · craft 2 (2026-08-18) · skills 5 (2026-09-08) · prompt-templates 2 (2026-08-18) · **contracts 106 (2026-09-24)** · daemon prompts 19 (2026-09-07…10).
- **CHANGELOG.md is retired as a version record past 0.9.0** (sections: Unreleased, 0.9.0 … 0.1.0, May 2026; v0.24.x releases don't touch it; only `RELEASE-NOTES-0.10.0.md` exists at top level).

**Churn verdict:** data resources are near-frozen — a vendored copy needs rare, cheap refreshes (sync on minor tags is more than enough). Prompt **code** churns actively (`contracts` is the hottest path) and the repo itself documents mirror drift as a real hazard (`docs/prompt-composition.md`: the #7568/#7651 example where a one-sided prompt fix cost a second ~2× PR). **Update-tracking mechanism: track upstream git tags (NOT CHANGELOG), diff-tag on resource paths; pin a vendored snapshot at a tag and re-diff per release.** For ported prompt code, treat drift-tracking as an ongoing cost, not a one-time port.

---

## 7. License Verdict

**Repo license: Apache-2.0** (`LICENSE`, full text; footer: *"Copyright 2026 Open Design contributors"*). **No NOTICE file** exists (repo root and v0.24.1 tree both checked).

**Verdict: extraction and reuse of prompts, design resources, and code inside agents-ensemble is PERMITTED.** Apache-2.0 §2 grants reproduction/derivative-works/distribution incl. commercial use, with §4 conditions on redistribution of the work or derivatives:
1. Include the Apache-2.0 license text with redistributed copies/derivatives of OD material;
2. **Attribution:** retain "Copyright 2026 Open Design contributors" + license reference (no NOTICE file → §4(c) is moot);
3. **State changes** made to copied files (mark modified files — e.g. a `MODIFIED` note or per-file header in the vendored tree);
4. Patent grant §3 applies; no copyleft, no source-disclosure obligation.

**Exceptions / caveats:**
- **Media prompt-templates are CC-BY-4.0-sourced** (`prompt-templates/*/…json` → `source: {repo: "YouMind-OpenLab/awesome-gpt-image-2", license: "CC-BY-4.0", author: …}`) — per-item attribution must be preserved when reusing those 106 JSONs (CC-BY ≠ Apache; keep the `source` blocks intact).
- **Trademark caveat (assessment):** design systems are OD's *curated recreations* named after brands (airbnb, apple, ant, bmw, …; manifest `source.origin: "OpenDesign curated bundled fixture"`). The *code/data* is Apache-2.0, but brand names/logos/fonts inside carry third-party rights OD cannot license. Fine for internal mockups; review before public/commercial exposure of brand-themed output.
- `skills/` stubs point at varied upstreams (`od.upstream`) — provenance varies per entry; treat as catalogue pointers.
- The **external `open-design-mcp` npm package** (where Turn-3 lives) is a separate artifact — its license was NOT verified here (out of repo). Verify before extracting *its* code (not needed if we re-implement from the in-repo catalog + tool contracts).

---

## 8. Gaps / Unverified

1. npm shim internals (out-of-repo): whether it performs its own truncation detection, whether it consumes `@open-design/contracts` or re-implements, its license. → Verify the installed package (`open-design-mcp@0.16.1`) if its code is to be ported; not required for data-resource reuse.
2. `RUNTIME_DATA_DIR` env-override vs `<projectRoot>/.od` default split (`server.ts:1382` not deep-read; default behavior verified via `db.ts:77`).
3. Per-tool wire mapping of `mcp.ts:365-848` tools → underlying routes (tool list verified; 1:1 mapping plausible, not grep-traced).
4. `core-slim` contracts-mirror absence: intentional vs bug — repo says "not established" (`prompt-composition.md:83`).
5. Skills stub→upstream mapping completeness (`scripts/seed-curated-design-skills.ts` referenced, not run).
6. First-appearance dates at the 506-commit window floor are floors, not true birth dates (shallow history).

---

*Evidence base: direct reads/greps/git (license, churn, tags, orientation) + three delegated read-only investigations (prompts: worker `7f2d5194`; resources+storage: `c072f771`; lint+truncation: `d0799ac6`) — all reports adjudicated on path:line citations at HEAD `53231d40`.*

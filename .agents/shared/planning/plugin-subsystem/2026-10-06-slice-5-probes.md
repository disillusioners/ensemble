# Slice ⑤ Probes — Port-Surface + Designer-Lane + Mode Decision + OQ4/OQ7

**Date:** 2026-10-06 · **Slice:** ⑤ ("Port + native generate tool + designer rewire")
**Author:** coder (slice ⑤ build)
**Sources:** REC §4.3 row ⑤; REC §4.2 (Mode P default, Mode T fallback); CON §3
(Port data-contract rules, FROZEN); architecture-investigation.md §8 items 1+6
(§8.1 port-surface probe, §8.6 designer-lane survey); od-generation-engine.md
(MCP/Daemon component maps); the vendored snapshot @ open-design-v0.23.0;
installed `open-design-mcp@0.16.1` @ `/home/nea/services/opendesign/` (READ-ONLY
inspection only); agent-side soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml.

---

## §8.1 Port-Surface Probe (Mockup/Deck Lane)

### A1.1 — The "classic stack" composer (Mode-P port surface)

The `od_generate_design` call path composes one **system prompt** by ordered
string concatenation of pre-rendered template strings. Per the evidence chain in
`od-generation-engine.md` (Q1, Q2) + the vendored snapshot at
`plugins/opendesign/snapshot_with_drift_alarm/prompts/{daemon,contracts}/`:

| # | Block (logical) | Local daemon tree | Vendored local | Upstream source | Bytes (daemon) | Bytes (contracts mirror) |
|---|---|---|---|---|---|---|
| 1 | `API_MODE_OVERRIDE` (top anchor; plain streamFormat) | `daemon/system.ts` (inlined) | `snapshot_with_drift_alarm/prompts/daemon/system.ts:6-9` | `apps/daemon/src/prompts/system.ts:API_MODE_OVERRIDE` | inline ~1.7 KB | absent |
| 2 | `SKIP_DISCOVERY_BRIEF_OVERRIDE` | `daemon/system.ts` | `snapshot_with_drift_alarm/prompts/daemon/system.ts` | `apps/daemon/src/prompts/system.ts:SKIP_DISCOVERY_BRIEF_OVERRIDE` | inline ~0.5 KB | absent |
| 3 | `DISCOVERY_AND_PHILOSOPHY` | `daemon/discovery.ts` | `snapshot_with_drift_alarm/prompts/daemon/discovery.ts` | `apps/daemon/src/prompts/discovery.ts:DISCOVERY_AND_PHILOSOPHY` | 318 lines (~13 KB) | 287 lines (~12 KB) |
| 4 | `OFFICIAL_DESIGNER_PROMPT` (BASE_SYSTEM_PROMPT) | `daemon/official-system.ts` | `snapshot_with_drift_alarm/prompts/daemon/official-system.ts` | `apps/daemon/src/prompts/official-system.ts:OFFICIAL_DESIGNER_PROMPT` | 203 lines (~12 KB) | 163 lines (~10 KB) |
| 5 | `memoryBody` (user-sedimented preferences) | n/a (per-call) | n/a (own-daemon; NOT vendored) | `apps/daemon/src/prompts/system.ts` — runtime only | — | — |
| 6 | `userInstructions` (per-call) | n/a (per-call) | n/a (per-call) | runtime-only | — | — |
| 7 | `projectInstructions` (merged: stored+per-call) | n/a (per-call) | n/a (per-call) | runtime-only | — | — |
| 8 | `activeDesignSystemBody` (per-call from design-systems/) | n/a (per-call) | `snapshot_with_drift_alarm/...` — catalog vendored; runtime only | `copy_freely/design-systems/<brand>/DESIGN.md` | — | — |
| 9 | `skillBody` (per-call, per active skill) | n/a (per-call) | `copy_freely/skills/<skill>/` — catalog vendored | `apps/daemon/src/skills/<skill>/SKILL.md` | — | — |
| 10 | `pluginBlock` (per-call, runtime-resolved) | n/a (per-call) | n/a (runtime-only) | runtime | — | — |
| 11 | `activeStageBlocks` (per-call list) | n/a (per-call) | n/a (per-call) | runtime | — | — |
| 12 | `renderMetadataBlock(metadata, template, audioVoiceOptions, audioVoiceOptionsError)` | `daemon/system.ts` (the renderer + metadata block) | `snapshot_with_drift_alarm/prompts/daemon/system.ts:renderMetadataBlock` | `apps/daemon/src/prompts/system.ts:renderMetadataBlock` | inline ~6 KB | absent |
| 13 | `DECK_FRAMEWORK_DIRECTIVE` (deck or deck-kind OR freeform-kind-is-deck) | `daemon/deck-framework.ts` | `snapshot_with_drift_alarm/prompts/daemon/deck-framework.ts` | `apps/daemon/src/prompts/deck-framework.ts:DECK_FRAMEWORK_DIRECTIVE` | 10 lines (~0.6 KB) | 589 lines (~24 KB) |
| 14 | `MEDIA_GENERATION_CONTRACT` (media-kind: image/video/audio) | `daemon/media-contract.ts` | `snapshot_with_drift_alarm/prompts/daemon/media-contract.ts` | `apps/daemon/src/prompts/media-contract.ts:MEDIA_GENERATION_CONTRACT` | 612 lines (~28 KB) | 172 lines (~9 KB) |
| 15 | `ACTIVE_DESIGN_SYSTEM_VISUAL_DIRECTION_OVERRIDE` (trailing effect if 8 fired) | `daemon/system.ts` (inlined) | `snapshot_with_drift_alarm/prompts/daemon/system.ts` | `apps/daemon/src/prompts/system.ts` | inline ~0.6 KB | absent |

### A1.2 — Composer-function shape (the algorithmic surface)

The composer is **string concatenation with conditional inclusion + section ordering**.
Per `apps/daemon/src/prompts/system.ts:composeSystemPrompt` (full local mirror
at `snapshot_with_drift_alarm/prompts/daemon/system.ts:composeSystemPrompt`),
the algorithm is:

1. **`streamFormat === 'plain'`** → top-anchor `API_MODE_OVERRIDE`.
2. **`metadata?.skipDiscoveryBrief === true`** → prepend `SKIP_DISCOVERY_BRIEF_OVERRIDE`.
3. **Always** → `DISCOVERY_AND_PHILOSOPHY` + identity charter + `BASE_SYSTEM_PROMPT`.
4. **Conditionally** → memoryBody, userInstructions, projectInstructions, designSystemBody, skillBody, pluginBlock, activeStageBlocks (each if `trim().length > 0`).
5. **Always-if-metadata** → `renderMetadataBlock(metadata, ...)` (templates based on `metadata.kind`).
6. **Conditionally** → `DECK_FRAMEWORK_DIRECTIVE` (if deck-kind OR `skillMode === 'deck'` AND no skill seed); OR `## If this brief is a slide deck...` wrapper for freeform-kind; excludes both when skill seed present.
7. **Conditionally** → `MEDIA_GENERATION_CONTRACT` (if `kind ∈ {image, video, audio}`).
8. **Conditionally** → `ACTIVE_DESIGN_SYSTEM_VISUAL_DIRECTION_OVERRIDE` (if design-system fired).
9. **Concatenate** via `parts.join('')`.

### A1.3 — Companion composer modules in the vendored snapshot (Mode-P inventory)

The 10 daemon composer modules vendored under
`snapshot_with_drift_alarm/prompts/daemon/` are consumed as **string
constants** by `composeSystemPrompt` — they have no runtime side effects:

- `system.ts` (2238 lines) — the main composer function + the 2 inline overrides (`API_MODE_OVERRIDE`, `ACTIVE_DESIGN_SYSTEM_VISUAL_DIRECTION_OVERRIDE`).
- `discovery.ts` (318 lines) — exports `DISCOVERY_AND_PHILOSOPHY` (string).
- `official-system.ts` (203 lines) — exports `OFFICIAL_DESIGNER_PROMPT` (string).
- `directions.ts` (338 lines) — direction-card library; not directly composed into the system prompt but referenced via `metadata.directions` (not exercised by the MCP BYOK lane; consumed only by the agent-runtimes lane per `od-generation-engine.md` §1.C).
- `media-contract.ts` (612 lines) — `MEDIA_GENERATION_CONTRACT` (string).
- `deck-framework.ts` (10 lines) — re-export from contracts mirror.
- `discovery.ts` mirror (287 lines).
- `official-system.ts` mirror (163 lines).
- `media-contract.ts` mirror (172 lines).
- `core-slim.ts` (438 lines) — daemon-only slim charter; **no contracts mirror** (OQ7 asymmetry, register entry id=3).
- `panel.ts` (226 lines) — UI panel render contracts; **not in the BYOK lane**.
- `research-contract.ts` (73 lines) — research-mode contract; **not in the BYOK lane**.
- `stable-sections.ts` (212 lines) — stable-sections framework; not composed by `composeSystemPrompt`.

The contracts-mirror module set (17 files; 17 declared and 17 vendored) is the
**published API surface** of the prompt stack and the cleaner reference for a
Python port — the daemon copies above are the canonical composer; the contracts
copies are the public API. Mode-P Python port reads the contracts mirror as the
**primary source** and falls back to the daemon copies only when the contracts
mirror is absent (OQ7 disposition — see §OQ7 below).

### A1.4 — BYOK call shape (OQ5 budget knobs)

`POST {BYOK_BASE_URL}/v1/chat/completions` with body
`{baseUrl, apiKey, model, systemPrompt, messages: [{role:"user", content:prompt}], maxTokens, stream}`.
Per-call knobs visible to the consumer (from `od-generation-engine.md` §3):

- `BYOK_BASE_URL` — env-only, NOT a per-call knob.
- `BYOK_API_KEY` — env-only (KMS-Lite managed per
  `daemon/mcp/builtin_servers/opendesign.py:178`); NOT a per-call knob.
- `BYOK_MODEL` — env-only default `vision`; the live lane forwards verbatim.
- `kind` — `prototype | deck | template | other | image | video | audio` (default `prototype`).
- `maxTokens` — int 1..200000, default 64000. Visible + configurable per call.
- `userInstructions` — per-call.
- `projectInstructions` — per-call (merged with stored when `projectId` provided).
- `skipDiscoveryBrief` — metadata-only; propagated via `projectInstructions` prefix by the caller.

Per-call knobs the MODE-P provider will surface as Port inputs (CON §3, JSON
serializable, schema-CI guard): `{prompt, kind, user_instructions,
project_instructions, max_tokens, skip_discovery_brief}` — `base_url`,
`api_key`, `model` remain env-only (not on the wire).

### A1.5 — Composer parameter list (the Mode-P Port surface)

The first `od.generate` Port MUST carry this parameter list to faithfully
re-implement the MCP BYOK lane:

```yaml
inputs_schema:
  type: object
  required: [prompt, kind]
  properties:
    prompt:                 # type=string, minLength=1; the user message
    kind:                   # enum: prototype|deck|template|other|image|video|audio; default=prototype
    user_instructions:      # type=string, optional
    project_instructions:   # type=string, optional
    max_tokens:             # type=integer, min=1, max=200000, default=64000
    skip_discovery_brief:   # type=boolean, default=false
    design_system:          # type=string (path under copy_freely/design-systems/), optional
    skill_id:               # type=string (path under copy_freely/skills/), optional
    memory_body:            # type=string, optional (per-call memory)
    audio_voice_options:    # type=array of strings, optional
  additionalProperties: false

outputs_schema:
  type: object
  required: [html, finish_reason, usage, model, truncated]
  properties:
    html:                  # type=string (the assembled HTML artifact)
    finish_reason:         # type=string ('stop' | 'length' | 'content_filter' | 'tool_calls' | 'other')
    usage:                 # type=object {prompt_tokens:int, completion_tokens:int, total_tokens:int, reasoning_tokens:int?}
    model:                 # type=string (verbatim from upstream)
    truncated:              # type=boolean (true ⇔ finish_reason != 'stop' OR html is structurally incomplete)
    error:                 # type=string|null (structured failure message if any)
  additionalProperties: false
```

---

## §8.6 Designer-Lane Consumption Survey

### A2.1 — Live-bound tool inventory (17 vs MCP-package 10)

The `17 live-bound od_* tools` reported in the live smoke log (2026-10-06
designer spawn, L17938) is the union of:

1. **The MCP package's 10 tools** (per `open-design-mcp@0.16.1`):
   `od_list_projects`, `od_get_project`, `od_create_project`, `od_update_project`,
   `od_save_artifact`, `od_save_project_file`, `od_delete_project`,
   `od_compose_brief`, `od_generate_design`, `od_lint_artifact`.
2. **The OD in-repo forwarder's 22 tools** (`/home/nea/opt/open-design` apps/daemon):
   the full OD-UI surface including non-BYEK runtimes — agents bind 8-10 of these
   depending on skill/permission gates. The forwarder's surface includes things
   like `od_start_run`, `od_get_run`, `od_list_runs`, `od_list_artifacts`,
   `od_get_artifact`, etc.
3. **Both bindings happen simultaneously** when the designer spawns with the
   `opendesign` builtin server (10 tools) + the OD-UI forwarder MCP bridge
   (more tools). Total bound on live designer = 17 (the union that's actually
   exercised by the designer workflow).

### A2.2 — Per-tool usage by the live designer workflow

From `agents/designer/{soul.md, rule.md, workflow.md, tools_note.md}` and the
designer workflow's Mockup lane (workflow.md:86-90):

| # | Tool | Used by live workflow? | Source |
|---|---|---|---|
| 1 | `od_list_projects` | **YES** — Step 0 lane-start probe (workflow.md:72) | workflow.md |
| 2 | `od_get_project` | YES — inspect existing project before composing brief | tools_note.md:43 |
| 3 | `od_create_project` | YES — create project for feature slug if absent | tools_note.md:44 |
| 4 | `od_update_project` | RARE — only on metadata drift | tools_note.md:45 |
| 5 | `od_save_artifact` | YES — record OD-UI provenance (reference only) | tools_note.md:46, workflow.md:90 |
| 6 | `od_save_project_file` | YES — same provenance purpose | tools_note.md:50, workflow.md:90 |
| 7 | `od_delete_project` | RARE — spec cancellation / feature rollback | tools_note.md:51 |
| 8 | `od_compose_brief` | **YES** — Step 1 of Mockup lane | workflow.md:86, tools_note.md:47 |
| 9 | `od_generate_design` | **YES** — Step 2 of Mockup lane; **THE LONG POLE** | workflow.md:87, tools_note.md:48 |
| 10 | `od_lint_artifact` | **YES** — Step 3 quality gate | workflow.md:88, tools_note.md:46 |

**Designer workflow actually exercises 7 of the 10 MCP tools** (the 3 rare tools
are listed but the live workflow does not consume them on the happy path;
`od_delete_project` is rollback-only).

### A2.3 — B-path compatibility bill

The 5 EXERCISED tools map cleanly to the per-capability Port split (REC §4.3
row ⑤): `od.generate`, `od.compose_brief`, `od.save` (covers both
`od_save_artifact` and `od_save_project_file` — the local fs write-through is
the developer deliverable; OD-UI provenance is a separate ref concern
dropped at §7 retirement), `od.lint`.

The 3 rare tools (`od_update_project`, `od_delete_project`, `od_get_project`)
are NOT in the new native lane; the rewire either (a) drops them entirely
(no current call sites) or (b) routes them via a single `od.list_projects` +
local `mockups/` directory listing. Per the rewire inventory in §6 of the
slice-⑤ worklog, **all 3 are dropped at ⑤** because the live designer workflow
does not exercise them on the happy path.

**Net B-path compatibility bill: 5 Ports implemented (od.generate,
od.compose_brief, od.save, od.lint) + 2 plugin-skill consumptions
(`opendesign.list_systems` already from ④ + `opendesign.generate_mockup`
authored at ⑤ — the discover-systems-via-skill + invoke-Port-via-tool
pattern).**

### A2.4 — Other 7 live-bound tools (NOT consumed)

The remaining 7 of the 17 live-bound tools (after subtracting 10 MCP tools
and de-duplicating the 5 Ports) are OD-UI runtimes that the workflow does not
exercise (the GenUI registry tools from `od-generation-engine.md` §1.C). These
are **out of scope** for the rewire — the workflow never called them and the
plugin path doesn't carry them (parity boundary; per the architecture-
investigation §3 row 8, the runtimes lane stays excluded).

---

## 🎯 Mode Decision (REC §4.2 pre-made rule)

**DECISION: Mode P (Python-native compose).** The composer chain is
**string concatenation of pre-rendered template strings with conditional
inclusion + section ordering** — re-implementable faithfully in Python at
a one-time per-language cost. There is NO algorithmic complexity that would
make Mode T (TS sidecar + lifted symbol + subprocess-per-call) preferable.
The Phase 4 §8.1 evidence supports this conclusion:

1. **The composer is data-driven** (§A1.2): every block is either a string
   constant or a renderer that produces a string. No side effects, no
   stateful computations.
2. **The blocks are large but static** (§A1.1): ~62 KB total content
   (sum of bytes), already vendored into
   `snapshot_with_drift_alarm/prompts/{daemon,contracts}/` per slice ②/③
   (4881 files, ~3 MB).
3. **The order is deterministic and short** (9 steps, all in
   `composeSystemPrompt`).
4. **The TCO favours Python** (REC §4.2 rationale): no Node runtime
   dependency, no subprocess, no 200-line lifted-symbol tripwire; the
   provider code is a Python module that imports the vendored prompt
   strings as constants.
6. **Mode T fallback condition is NOT triggered**: "the composer chain
   too entangled to re-express faithfully in Python" is the gate per REC
   §4.2; the chain re-expresses faithfully (string concatenation +
   conditional inclusion is idiomatic Python with the same readability
   as the TS source).

**The Mode-P provider reads the contracts-mirror strings as the primary
source** (CON §3: Provider internals are EVOLVABLE; the Port is FROZEN).
Falls back to the daemon copies only when the contracts mirror is
absent (OQ7 disposition).

---

## OQ4 — npm-shim internals/license (Turn-3 authorship gate)

**Disposition: PORT-WITH-ATTRIBUTION (NOT clean-room).** Evidence:
`open-design-mcp@0.16.1` package.json declares `"license": "Apache-2.0"`.
The compose-brief orchestration lives at
`/home/nea/services/opendesign/node_modules/open-design-mcp/dist/src/tools/compose-brief.js`
(96 lines, Apache-2.0) — the orchestration is **pure formatting**
(`composeBrief()` joins `[form answers — discovery]` + `[brand spec]` + `[page
brief]` sections with `\n\n`). Porting this to Python is a small,
mechanical translation (95 lines JS → ~50 lines Python module) with
attribution REQUIRED (Apache-2.0 §4(d) — copy attribution + state
changes).

**Per CON §2 own_outright class rules:** the Turn-3 orchestration is
authored by us — the **port** from the npm shim is part of our
own_outright/ tree (`plugins/opendesign/own_outright/turn3_orchestration/`)
with attribution metadata (`license-carried-from: Apache-2.0 open-design-mcp
@0.16.1` in the manifest header). The vendored snapshot stays
`copy_freely`-class for the upstream OD prompt data; the **port** of the
compose-brief orchestration is `own_outright`-class (we own it forever; we
sync it from the upstream-shim's Apache-2.0 source).

**Authorship obligation reduced from clean-room to port-with-attribution.**
Implementation lands in `daemon/plugin_subsystem/opendesign/compose_brief.py`.

---

## OQ7 — core-slim asymmetry + two-tree mirror drift

**Disposition: COMMIT TO ONE TREE — the contracts mirror as primary, daemon
copies as fallback.**

The vendored `snapshot_with_drift_alarm/prompts/{daemon,contracts}/` carries
the upstream two-tree mirror documented at `od-resource-layer.md` §1.B. The
two trees diverge:

- 5 modules have a daemon copy AND a contracts mirror: `system.ts` (2238 vs
  1183), `discovery.ts` (318 vs 287), `official-system.ts` (203 vs 163),
  `media-contract.ts` (612 vs 172), `deck-framework.ts` (10 vs 589).
- 4 modules are daemon-only (no contracts mirror): `core-slim.ts`,
  `panel.ts`, `research-contract.ts`, `stable-sections.ts`.
- 12 contracts-only files: `atom-block.ts`, `canonical-xml.ts`,
  `chat-turn-host-protocol.ts`, `od-next-*-bundle.ts`, `plugin-block.ts`,
  `todo-recall.ts`, `ui-locale.ts`, etc. (no daemon copy; consumed by
  third-party consumers, not by `composeSystemPrompt`).

The contracts mirror is the **published API surface** (it's the
`@open-design/contracts` npm package surface). For the MODE-P Python
provider, **commit to the contracts mirror as the primary source** for
the 5 mirrored modules, and fall back to the daemon copies only when the
contracts mirror file is absent (e.g., `core-slim.ts`).

**Divergence register updates** (consequence of the OQ7 disposition; the
register's sync rule is "re-apply or drop, update the log either way"):

| ID | Status change | New entry? |
|---|---|---|
| 1 (`system.ts` daemon/contracts two-tree) | ALREADY-SEEDED (slice ③); no change in scope — the disposition says "contracts mirror is the canonical Python reference" so the existing entry's rationale is updated to record "Python Mode-P provider reads contracts mirror primary" |
| 2 (`media-contract.ts` two-tree) | ALREADY-SEEDED; rationale updated same way |
| 3 (`core-slim.ts` no contracts mirror) | ALREADY-SEEDED; disposition confirms the register entry — the contracts mirror is absent because there is no public API for the slim charter |
| 4 (`directions.ts`/`discovery.ts`/`official-system.ts` two-tree) | ALREADY-SEEDED; rationale updated same way |

**No new entries.** The 4 SEEDED entries at slice ③ cover the OQ7 surface;
the register rationale gets an UPDATE reflecting the OQ7 commitment (the
"re-apply or drop" rule from CON §2; this is a re-apply with a refreshed
rationale).

---

## Port coverage (CON §3 frozen rules)

The four Ports implemented at ⑤:

| port_id | version | seam-gate status | negative-CI coverage |
|---|---|---|---|
| `od.generate` | 1 | Definition+Provider+Consumer named | yes — port schema rejects non-JSON-serializable inputs/outputs |
| `od.compose_brief` | 1 | Definition+Provider+Consumer named | yes |
| `od.save` | 1 | Definition+Provider+Consumer named | yes |
| `od.lint` | 1 | Definition+Provider+Consumer named | yes |

---

## ARTIFACT INDEX (paths)

- `.agents/shared/planning/plugin-subsystem/2026-10-06-slice-5-probes.md` (this file)
- `.agents/shared/planning/plugin-subsystem/architecture-recommendation.md` (REC)
- `.agents/shared/planning/plugin-subsystem/v1-interface-contracts.md` (CON)
- `.agents/shared/planning/od-native-design-subsystem/od-generation-engine.md` (Q1-Q8 deep-dive)
- `.agents/shared/planning/od-native-design-subsystem/architecture-investigation.md` (§8 next-round)
- `.agents/shared/planning/od-native-design-subsystem/od-resource-layer.md` (§1.B two-tree)
- `plugins/opendesign/MANIFEST.yaml` (carries the 4 Port declarations + own_outright paths + divergence register rationale updates)
- `plugins/opendesign/CURATION.md` (OQ3 record; provenance of tests)
- `daemon/plugin_subsystem/port_registry.py` (tier-2 comp 8)
- `daemon/plugin_subsystem/capability_seam_gate.py` (tier-2 comp 14)
- `daemon/plugin_subsystem/plugin_tool_factory.py` (tier-2 comp 12)
- `daemon/plugin_subsystem/opendesign/{generate,compose_brief,save,lint}.py` (OD-instance B-element, split per-capability per REC §1.2 comp 9)
- `plugins-convention/port.schema.json` (FROZEN; CON §3 JSON-Schema surface)
- `plugins-convention/ci_runner.py` (negative tests for ports wired in)
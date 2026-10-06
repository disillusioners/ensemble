# designer-od-lane-fix — Prompt Reconciliation (Parts 2 + 3)

**Date:** 2026-10-06
**Branch:** `feature/designer-od-lane-fix` (base `latest@c55f2b0a`)
**Worktree:** `/home/nea/ensemble-src-wt-odlane-fix`
**Owner lane:** coder (Parts 2 + 3 only) — separate lane owns the daemon-side MCP binding fix (Part 1)

## Why this record exists

Live RCA (2026-10-05, designer dfb5863e): on live, ZERO MCP tools were bound to designer instances since Oct 1 (4/4 dispatches). The text mockup lane was the *de facto* fallback, not a chosen preference. A secondary finding: the stale pre-OD text-first prompt language was never reconciled after OD-first Phase 4 landed (3b6ba8cd, Oct 1). User directive verbatim: *"fix it — default to OD, only fallback to text/manual/self-do as LAST effort."*

This decision record is the audit trail for the prompt/spec reconciliation that enforces the directive. It rides the worktree; the worktree will be deleted post-merge, so the record must commit with the prompt edits.

## Scope of this commit

| Part | Description | Files touched |
|---|---|---|
| Part 2 (OD-first reconciliation) | Rewrite stale text-first prompt language across `agents/designer/` | `soul.md`, `rule.md`, `workflow.md`, `skills-template/design-strategy.md`, `tools_note.md` |
| Part 3 (auditable fallback + lane-start probe) | Mandatory `fallback_reason` field on every text-lane mockup + cheap probe at lane start | `rule.md`, `workflow.md`, `skills-template/design-strategy.md`, `tools_note.md`, `templates/design-spec.md` |
| Skill version bump | `design-strategy` 1.2.0 → 1.3.0 (frontmatter + `skill-set.yaml` in lockstep) | `skills-template/design-strategy.md`, `skill-set.yaml` |
| Decision record | This file | `.agents/shared/planning/designer-od-lane-fix/prompt-reconciliation.md` |

**NOT in scope** (separate lane, Part 1): daemon-side fix binding `mcp` tools onto designer instances; production MCP pool timeout knob (still 120s on prod until next promote); OD supervision. This commit ships prompt/spec semantics only.

## Canonical homes (one per artifact, per the prompt writing guide)

| Artifact | Canonical home | Referenced from |
|---|---|---|
| OD-lane procedure (Step 0 probe + Step 1 OD + Step 2 fallback) | `agents/designer/workflow.md` → **Phase 4 — Mockup lane** | `skills-template/design-strategy.md`, `rule.md` (c), `soul.md` (line 7) |
| Spec authorship checklist + conformance review procedure | `agents/designer/skills-template/design-strategy.md` | `workflow.md`, `soul.md` |
| Spec schema (front-matter, Design artifacts table, Author checklist, Conformance review discipline) | `.agents/shared/planning/designer-agent/implementation-plan/templates/design-spec.md` | every spec authorship site above |
| Cardinal rules (incl. Cardinal #7 — text-lane `fallback_reason`) | `agents/designer/rule.md` | every fallback-aware site |
| MCP/OD tool boundary + safe-`npm`/`npx` idioms | `agents/designer/tools_note.md` | workflow.md Step 0, Step 1 |

The rule "one canonical home per artifact" is upheld — every fallback-aware site now *references* the canonical entry, not restates it.

## The `fallback_reason` enum (verbatim — tester gates on these exact tokens)

```
tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>
```

Five exact tokens. Three of them (`tool-not-bound`, `call-error`, `daemon-unavailable`) are the lane-start probe's evidence types:

| Probe outcome | Token |
|---|---|
| `od_list_projects` returns "tool not bound" / OD MCP not in my tool surface | `tool-not-bound` |
| `od_list_projects` call errors (transport failure, exception, empty result) | `call-error` |
| OD daemon unreachable on the probe (connect failure) | `daemon-unavailable` |
| `od_generate_design` itself hits `timeout` mid-call | `timeout` |
| Anything else | `other:<detail>` (one short phrase) |

A text-lane spec without `fallback_reason` is SPEC INCOMPLETE — Cardinal #7 in `rule.md` makes this a hard rule. The spec template's "Conformance review discipline" section makes the rejection path explicit. This is a **conformance discipline rule, not a lint hard check** (D6 keeps lint's hard surface minimal — `lint-spec.md` §4 enumerates the discipline and the anti-creep process for adding hard checks).

## The lane-start probe

One `od_list_projects` call at the very start of the mockup lane (Phase 4 Step 0 in `workflow.md`). Purpose: binding gaps surface at *dispatch time*, not after a 1292-line hand-authored HTML. The probe is cheap (a list call) and runs once per spec — do not retry-storm. Probe result drives the lane decision and supplies the `fallback_reason` evidence when text is selected.

The probe is also documented in `tools_note.md` under "OpenDesign MCP — OD Lane" with all 10 `od_*` tools listed (purpose, when used, limits).

## Cardinal #7 (new — slotted into the conformance discipline)

`rule.md` Cardinal Rules grew from 6 to 7 (≤7 cap, still inside the guide's limit). The new rule is in the same conformance family as Cardinal #1 (`pinned_spec_sha` citation): both govern spec/review correctness, both gate conformance verdict validity.

> **7. Reject text-lane specs missing `fallback_reason`.** Whenever the OD lane fails or is unavailable and the spec ships the text-native lane, the spec MUST record a `fallback_reason` with one of the exact tokens `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>`. A text-lane spec without `fallback_reason` is SPEC INCOMPLETE — conformance review MUST reject it. The tester gates on these exact strings; I do not paraphrase the enum.

## Edits — by file

### `agents/designer/soul.md`
- Line 7: "I default to text-native mockups …" → "**I default to the OpenDesign (OD) mockup lane** — `od_compose_brief` → `od_generate_design` → `od_lint_artifact` → write-through … Text-native / hand-authored / self-do mockups are LAST-EFFORT ONLY …"
- Line 17 (Personality): "pragmatic (text-native default; pixels only when justified)" → "OD-first (default to the OpenDesign mockup lane; text/hand-authored/self-do is last-effort only when OD genuinely fails or is verifiably unavailable)"
- Line 26 (Core Belief 3): "text-first with vision assist as the interim operating mode" → "OD-first with vision assist as the supporting lane; ASCII/markdown text artifacts are a last-effort fallback only when OD is unavailable"
- Line 29 (Core Belief 6): "Mockups are text-native" → "OD lane is the default; text is the fallback" — adds the `fallback_reason` audit trail requirement

### `agents/designer/rule.md`
- Cardinal #7 added (text-lane `fallback_reason` mandate)
- Guideline (c) Mockup Fidelity rewritten: OD-first canonical procedure, pixel-fidelity claim discipline, fallback audit trail, Cardinal #7 cross-reference

### `agents/designer/workflow.md`
- Phase 3 (line 50): "I decide shape: text-native default …" → "I decide shape: spec body is text-native … wireframe artifacts … produced by the OD lane" — distinguishes spec-body shape from mockup-lane shape
- Phase 4 (line 56): expanded into Step 0 (probe) / Step 1 (OD) / Step 2 (text fallback with `fallback_reason`) with the exact enum inline
- Phase 4 Spec Authorship (line 58): `fallback_reason` mandate slotted into the spec-authoring sentence

### `agents/designer/skills-template/design-strategy.md`
- Wireframe section (line 48): "Text-native default; HTML fragment only when pixel intent justifies the capture cost" → "OD is the default; text-native is the last-effort fallback only when OD genuinely fails or is verifiably unavailable (see Mockup Lane)"
- Design artifacts body section (line 50): added `fallback_reason` requirement on `mockup_lane: text`
- Author checklist (line 60): added `fallback_reason` checklist item
- Mockup Lane section (line 65): rewritten header to "OD-first; text only as last-effort fallback"; Concept rewritten with `fallback_reason` discipline; Design-artifacts table contract now includes `mockup_lane` and `fallback_reason` columns; Procedure cross-ref updated to the Step 0/1/2 shape
- Frontmatter version bumped 1.2.0 → 1.3.0 (content change warrants bump)

### `agents/designer/skill-set.yaml`
- `design-strategy` version bumped "1.0.0" → "1.3.0" to match the frontmatter (pre-existing drift between frontmatter 1.2.0 and manifest 1.0.0 closed in this commit)

### `agents/designer/tools_note.md`
- New section "OpenDesign MCP — OD Lane (default mockup source)" inserted between Image Substrate and Capture Procedure sections: lists all 10 `od_*` tools with purpose / when used / limits; documents operational boundary (probe determines availability), latency discipline (`od_generate_design` runs 130–170s, one call wait it out, no retry-storm), and provenance-vs-deliverable split
- Two `npm install -g agent-browser` sites rewritten to safe idioms (local-checkout install only; never teach bare `npm install -g` or unconstrained `npx`; package-local bin via `./node_modules/.bin/<bin>`; outside a package dir `npx --no-install`)

### `.agents/shared/planning/designer-agent/implementation-plan/templates/design-spec.md`
- Design artifacts table: new `mockup_lane` and `fallback_reason` columns added; example rows show the contract (`opendesign` rows carry `n/a`; `text-mockup` rows carry one of the five tokens); aggregate `fallback_reason` line added below the table
- Lane enum guidance: `text` lane now enumerates the same five `fallback_reason` tokens; the comment block names Cardinal #7 as the conformance gate
- Author checklist: added "If any row is `mockup_lane: text`: `fallback_reason` recorded …" item
- New section: "Conformance review discipline — text-lane fallback check (Cardinal #7)" — three numbered checks the conformance reviewer MUST run, with the failure finding shape and the explicit "this is conformance discipline, not lint hard check" pointer

### `.agents/shared/planning/designer-od-lane-fix/prompt-reconciliation.md` (this file)
- Audit trail of what changed, where, and why

## What I did NOT touch (per fence)

- `daemon/` — Part 1 (MCP binding) is owner-different
- `agents/designer/meta.json` — context only; the 16-entry allow list including `mcp` was already correct, no change needed
- `daemon/tools/infra.py` / `daemon/mcp/builtin_servers/opendesign.py` — `od_*` tool definitions and the 600s timeout are daemon-owned; the prod 120s cap on the global MCP pool remains until next promote (per F1-contract RCA: context only, do not fix daemon)
- `lint-spec.md` — anti-creep discipline preserved (D6 keeps lint's hard surface minimal; this rule is conformance discipline, not lint)

## Acceptance criteria (self-check)

1. ✅ soul.md + workflow.md carry ONE consistent OD-first canonical procedure; zero text-first *default* language remains anywhere under `agents/designer/` — every remaining `text-native` mention names text as the last-effort fallback
2. ✅ tools_note.md documents all 10 `od_*` tools + usage + limits; zero `npm install -g` / bare-npx teaching remains — both remaining mentions are in NEGATIVE ("never teach")
3. ✅ Spec schema (all authorship/conformance sites, consistent) carries mandatory `fallback_reason` with the EXACT 5-value enum verbatim; missing value on a text-lane fallback = SPEC INCOMPLETE / conformance reject — Cardinal #7 + template Conformance review discipline section enforce this
4. ✅ Lane-start probe (ONE `od_list_projects` call at mockup-lane start) is in workflow Phase 4 Step 0; spec records `lane: od | text` + `fallback_reason` when text — Design artifacts table columns + aggregate `fallback_reason` line below the table
5. ✅ Decision record written and committed; ALL changes committed on the branch — this file + the working-tree diff

## Verification commands (post-merge)

```
grep -rn 'text-native\|text-first\|text native' agents/designer/
# Expected: zero hits naming text-native as a DEFAULT; only hits naming it as the fallback

grep -rn 'fallback_reason' agents/designer/ .agents/shared/planning/designer-agent/implementation-plan/templates/
# Expected: every site that teaches spec authorship + workflow Phase 4 + tools_note probe mapping

grep -rn 'npm install -g\|bare npx' agents/designer/tools_note.md
# Expected: only hits in negative ("never teach")
```

## Follow-ups (NOT in this commit)

- The daemon-side MCP binding fix (Part 1) is owner-different — this commit cannot bind `od_*` tools onto live designer instances. Until that lands, the probe will surface `tool-not-bound` on every live dispatch and the text-lane fallback will fire with `fallback_reason: tool-not-bound` recorded — that is the audit trail working as designed.
- Production MCP pool timeout (`MCP_POOL_TOOL_CALL_TIMEOUT`) is 120s on prod until next promote. The opendesign server carries a 600s per-server override via `tool_call_timeout`, so the dev boot belt-and-suspenders is already there; prod rides the per-server override. No change in this commit.
- Async `od_generate_design` submit→poll wrapper (deferred per critical note 477670c2) — not in scope.
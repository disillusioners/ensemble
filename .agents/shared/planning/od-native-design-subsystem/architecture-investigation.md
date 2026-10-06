# OD Native Design Subsystem — Architecture Investigation (Parts 2+3)

**Kind:** Direction-level decision document (discussion stage). No implementation plan, no schema, no code.
**Revision:** r2 — review edits applied (APPROVED-WITH-NOTES: license census, tool-surface referent, trademark softening, single-sample caveat; cosmetics).
**Question:** Replace the OD MCP dependency with an ensemble-native design subsystem in PLUGIN shape (vendored tree + provenance manifest + pinned-mirror sync), vs fix the MCP path, vs hybrid.
**Decision owner:** User (this document pressure-tests the user's leading candidate; it does not plan it).
**Evidence base (exclusive):** the two Part-1 maps, produced read-only against `~/opt/open-design` @ `53231d40` (= origin/main tip) and installed `open-design-mcp@0.16.1`:
- **[E]** `od-generation-engine.md` — engine trace + ranked root-cause hypotheses (instance `fe437579` lineage)
- **[R]** `od-resource-layer.md` — resource inventory, coupling, license, churn (Wanderer `30a87725` + workers `7f2d5194`/`c072f771`/`d0799ac6`)

Citation convention: `[E §n]` / `[R §n]` cite the maps; the `file:line` refs inside are the maps' verified citations, not re-verified here (no spot-check was needed — every load-bearing claim below is either grep/git-verified in a map or cross-corroborated by both). Claims are tagged **Fact** (map-verified observation), **Inference** (derived from map evidence), or **Judgment** (architect's assessment).

Paths under comparison:
- **A** — Fix the MCP path (keep `open-design-mcp` + OD daemon in the generation loop)
- **B-plugin** — `plugins/opendesign/` vendored tree + provenance manifest + pinned-mirror drift-detection sync + ensemble-native generation path consuming it (LEADING CANDIDATE)
- **C** — Hybrid (OD for exploration/UI/projects; native path for production generation)

Criteria (user-fixed): **resilience · compatibility · updatable-when-OD-updates · design quality**.

---

## 1. Executive Summary

**Verdict per criterion:** **Resilience → B** (only B closes the relay's structural observability gaps, [E §11.3]); **Compatibility → A/C short-term** (od-tool surface + OD UI untouched; the "17 tools" referent is named in §5.1's note), B pays a bounded re-tooling bill; **Updatability → B decisively** (A's prompt drift is *unfixable by version bump*, [E §2.3:100]; C retains the unfixable artifact); **Design quality → B** (current Sep-10 prompt semantics + completeness gates beat a frozen May-17 snapshot, at the cost of owned sync discipline).

**Recommendation:** B-plugin as the end-state direction — the evidence supports the user's lean.
**Amendments:** (1) the plugin boundary is *three relationships with upstream* (copy-freely / port-with-alarm / own-outright), not one uniform "files intact" vendor — the data layer holds that promise, the prompt-code layer does not; (2) C is best understood as B's *transition shape*, not a permanent third path. Path A, examined closely, is not "stay on upstream" — every real version of fixing it converges on vendoring *without* manifest or sync discipline.
**Timing:** the live 2/2 failure has an S-class palliative available on any path, so the direction decision can be made deliberately, not under outage pressure.

---

## 2. OD Architecture Map (decision-relevant skeleton)

### 2.1 What `od_generate_design` actually is

**Fact.** Two thin components and a dumb pipe [E §0]:

1. **`open-design-mcp@0.16.1`** (separate npm package, not in the OD monorepo) composes the system prompt *client-side* from a **vendored snapshot of OD's prompt library dated 2026-05-17** (upstream commit `7766582f`, `vendor/od-contracts/VENDORED_FROM.md`) and POSTs `{baseUrl, apiKey, model, systemPrompt, messages, maxTokens}` [E §1 Component A].
2. **The OD daemon `/api/proxy/openai/stream` route is a blind SSE relay** (`apps/daemon/src/routes/chat.ts:1032-1182`): forwards `max_tokens` verbatim, streams only `choices[0].delta.content` (`chat.ts:625-632` — `reasoning_content` and `usage` frames silently dropped), **never inspects `finish_reason`**, and emits a clean `end` on `[DONE]` *or on any upstream close without `[DONE]`* (`chat.ts:1173`) [E §1 Component B].
3. **The MCP accumulates the string** and returns it; on a clean `end` it returns whatever it has — partial HTML, zero HTML, thinking-prose — with **no `isError` and no partial marker** (the marker exists only on the MCP's own abort path, `generate-design.js:182-204`) [E §1, §4].

**Fact.** Token-budget exhaustion (`finish_reason=length`) is therefore *indistinguishable from success at every layer* of the chain [E §0, §4]. No content-length cap, no daemon-side generation timeout, no completeness check exists anywhere in the path [E §5].

**Fact.** There is no artifact persistence in the generation flow — persistence happens only if the caller separately invokes `od_save_artifact` / `od_save_project_file` [E §1 Component C].

**Inference (load-bearing for the decision).** The OD "engine" on our lane is ~200 lines of relay plus a prompt library — not a generation engine [E §11.1]. A native path replaces almost nothing; what it *gains* is ownership of the loop where every observability gap currently lives. This cuts both ways: little to lose by removing OD from the loop, little machinery to point at as "too big to replace."

### 2.2 The lane split (decisive)

**Fact.** OD has two prompt lanes [R §1]:
- **Daemon-composed lane** (`/api/runs`, `/api/chat`): full composer chain — skills, DESIGN.md, tokens.css, craft, memory, slim charter default [R §1 runtime chain]. Not what we use.
- **MCP/BYOK lane** (`/api/proxy/<provider>/stream`, `routes/chat.ts:986-1395`): takes `systemPrompt` **verbatim from the request body — zero server-side composition** [R §0.1, R §1]. The npm shim composes client-side.

**Inference.** All prompt value on our lane lives in (a) composable prompt modules in the OD repo and (b) the external npm shim that assembles them. An ensemble-native subsystem can own prompt composition outright without touching OD's daemon-composed machinery [R §0.1].

### 2.3 The resource taxonomy (coupling + churn + license)

**Fact** [R §2, §5 coupling table, §6 churn, §7 license]:

| Layer | Contents | Coupling class | Churn (7-wk window / full minor v0.23.0→v0.24.1) | License |
|---|---|---|---|---|
| **Data resources** | 154 design systems (`manifest.json` + tokens + components contract), 115 design templates (SKILL.md + example.html), 13 craft docs, 106 media prompt-templates | **STANDALONE** — plain files, documented folder contracts [R #1/#2/#4/#5/#7] | ≤8 commits/dir in window; **ZERO data-resource churn across the full minor** [R §6] | Apache-2.0; prompt-templates **mixed per-item census** (`source` blocks): 72 CC-BY-4.0 / 31 Apache-2.0 / 1 MIT / **2 "Original X post" (murky — excluding them from the vendor set is cheaper than operating a review gate)**; brand-named systems carry trademark caveats (§4.4) [R §7] |
| **Prompt code** | Daemon stack: 10 TS modules, 4,668 lines, composer `composeSystemPrompt` with ~40-param `ComposeInput` (`system.ts:726-900`); contracts mirror: 18 files, 5,969 lines, 106 commits in window — **the hottest-changing area** [R §1.A/B, §6] | **LIGHTLY COUPLED (medium)** — text portable; precedence lives in composer push-order; port = "hours-to-days, not weeks" [R #10/#11] | 3 files changed across the full minor, all prompt-side | Apache-2.0 |
| **Turn-3 / compose-brief orchestration** | form→collect→brand-spec→final-prompt flow | **NOT IN REPO** — lives in the external `open-design-mcp` npm package; in-repo only the deterministic brief catalog (`contracts/src/api/brief.ts`, 509 lines) [R §1 Turn-3 verdict, R #13/#14] | n/a (external) | **npm shim license UNVERIFIED** [R §7, §8.1] |
| **Quality gates** | Slop linter (16 regex rules, ~390 lines, deliberately non-parsing, `lint-artifact.ts:11-22`); **parse5 EOF truncation gate** (`deliverable-syntax.ts:47-55`, `FATAL_HTML_PARSE_ERRORS` incl. `eof-in-element-that-can-contain-only-text`) wired ONLY to OD's internal runs finalizer — never to the lint/save routes our lane uses [R §3] | **LIGHTLY COUPLED** — both mechanically portable [R #15/#16] | lint-artifact.ts changed once across the minor | Apache-2.0 |
| **Entangled (excluded)** | Brand presets (19 modules + agent loop + browser), OD SQLite storage (inline 5,490-LoC schema), in-repo MCP forwarder | **ENTANGLED** — "needs OD runtime" [R #9/#17/#18] | — | — |

**Fact.** Minimum viable ensemble-native set per the resource map: data copies (#1/#2/#4/#5) + prompt composition (#10 or #11) + brief flow (#13/#14 — #14 must be *authored*) + gates (#15/#16) [R §5].

**Fact (precedent, cuts both ways).** `open-design-mcp` itself vendors a prompt snapshot — and drifted: upstream's RULE 1 discovery-skip rewrite (≤ 2026-09-10) solves exactly the contradiction our lane suffers, but never reached the vendored copy, and `0.16.1` is npm-`latest`, so **no version bump can fix it** [E §2.3:97-100]. Upstream also suffers its *own* two-tree mirror drift (daemon `system.ts` 2,048L vs contracts 1,141L; `core-slim` has no contracts twin) [R §1.B:47]. Pro: the resource tree is demonstrably the asset. Con: duplication without manifest + sync discipline rots silently — inside OD and inside its shim alike.

---

## 3. Live-Failure Root-Cause Input (adopt/refine)

**Adopted as-is.** The ranked hypotheses stand: **H1 (max_tokens exhaustion with content-channel thinking)** remains primary — the latency is *budget-shaped* (50K÷~330 tok/s ≈ 150 s; 64K÷~450 tok/s ≈ 140 s [E §9]), the journal shows a clean normal-completion end for both calls with zero error lines [E §9], and the chain is finish_reason-blind and reasoning-blind by construction [E §4]. **H2** (upstream close without `[DONE]`, same clean-end signature) remains the only OD-side alternative, weaker only because both durations track their respective budgets [E §10]. **H3** (stale vendored discovery prompt) is retained in the engine map's own framing — an **amplifier**, not a standalone cause: it explains *why more budget produced less artifact* (the May-17 RULE 1 mandates a turn-1 question-form + STOP that directly contradicts the complete Turn-3 brief [E §2.3], so added budget buys more non-convergent deliberation, not more artifact [E H3]) — multiplying H1's probability and severity rather than competing with it. H4/H5/H6 stay ruled out/refuted [E §10].

**Judgment (architecture-level, RCA-orthogonal).** Whichever mechanism fired, the *structural* finding is settled: a chain that cannot distinguish `finish_reason=length`, an upstream drop, and success — and whose lint cannot see truncation [R §3] — will keep failing silently at rate ≈ (thinking-heavy prompts × budget pressure). The gated RCA commission (llm-supervisor-proxy log confirmation of H1) stays orthogonal to this document: its outcome changes *which* native-path control is most load-bearing (retry-on-close vs reasoning-budget control), not the direction assessment.

---

## 4. B-Plugin Pressure-Test

### 4.1 Where the vendored-tree shape HOLDS — the data layer

**Fact/Inference.** The frozen data layer is a near-perfect fit for "keep files intact": 154 + 115 + 13 + 106 resources are plain files with documented folder contracts (`manifest.json` schema `od-design-system-project/v1`, SKILL.md frontmatter, per-item `source` provenance) [R §2]; churn is zero across a full minor and ≤8 commits/dir in a 7-week window [R §6]; the map's own update mechanism — *pin at a git tag, diff-tag on resource paths, re-pull per release; track tags, not CHANGELOG (retired as a version record)* [R §6] — is exactly the "clean pulls except at marked divergence points" discipline the plugin shape prescribes. Copying this layer is low-risk, high-value, near-zero-maintenance. License conditions (Apache-2.0: license copy + attribution + **change-statements**; per-item license preservation via the `source` blocks — mixed census in §2.3) are mechanically satisfiable by the manifest itself [R §7]. Update-mechanism alternatives considered and rejected at direction level: git subtree/submodule (couples our tree shape to upstream's; no divergence modeling), an on-demand sync skill (no durable pin/staleness surface), and a bare version manifest (a pin without a diff mechanism — the shim precedent, §4.3 #1).

### 4.2 Where it STRAINS — three honest pressure points

**(1) The hot code layer breaks "files intact."** **Fact:** the prompt layer is *code*, not files — inline TS template literals behind a composer with ~40 parameters and layered precedence ("pinned LAST so hard rules win"), churned at 106 commits/7 weeks, with ~4,465 lines of upstream prompt tests pinning behavior [R §1.A/B, §6]. **Judgment:** a Python daemon cannot "vendor" this layer intact — the three consumption modes are (a) **Node sidecar** executing the real composer (max fidelity, cleanest upstream updates, but adopts a runtime dependency — the "whole system" smell in miniature, straining the user's resource-level philosophy), (b) **prompt data-fication** at vendor time (fits the file tree but is a *one-time port disguised as vendoring* — divergence markers would blanket the whole composition area, and updates become manual re-ports), or (c) **thin-port** the composer precedence to Python ("hours-to-days" [R #10]) with drift-tracking as a permanent tax. **Inference:** in all three modes, the manifest's job for this layer is *detection*, not automation — upstream prompt changes land as alarms requiring reconciliation. If marked-divergence density grows, "clean pulls except at divergence" degrades toward "manual merge every release." This is the crux: **B-plugin's promise is true for the layer that is easy, and partially false for the layer that carries the generation quality.**

**(2) The Turn-3 gap is an authorship obligation.** **Fact:** the compose-brief orchestration is not in the OD repo; only the deterministic brief catalog is [R §1 Turn-3 verdict, R #13/#14]. B-plugin must either author it clean-room from the catalog + tool-contract semantics, or port it from the npm shim — whose license is unverified [R §7, §8.1]. **Judgment:** this is the one piece that is *ours forever* regardless of upstream; it is also the piece where ensemble-native control pays (we already exercise these semantics via `od_compose_brief`). It must be priced as new owned orchestration, not vendoring.

**(3) Brand presets and skills-stubs are entangled at the edges.** **Fact:** the brands flow needs 19 modules + an agent extraction loop + a browser [R #9]; ~half of `skills/` are stubs pointing at upstream bundles [R §8, §2]. **Judgment:** both must be explicitly out of scope for the plugin (design systems already cover brand *styling* for the mockup lane [R #9]), but the exclusion is a **parity boundary** — the plugin's quality claims hold for the mockup/deck lane, not for brand-preset generation or functional-skill execution.

### 4.3 What the generalizable plugin convention must resolve (direction level, not schema)

The manifest+sync mechanism must solve six problems — for `plugins/opendesign` and for any future `plugins/<other-oss>`:

1. **Identity & pinning** — record the upstream ref (tag/commit) the tree derives from, plus a sync cadence policy per path class. (The shim's `VENDORED_FROM.md` had the pin and still rotted — a pin without a sync mechanism is a tombstone.) [R §6, E §2.3]
2. **Relationship classes** — represent that the tree contains three different relationships with upstream: **copy-freely** (data), **port-with-alarm** (code whose updates need reconciliation), **own-outright** (ensemble-authored). A manifest that models only "vendored + divergences" lies about the port layer. Apache-2.0 §4 change-statements ride on this same record [R §7].
3. **Drift detection** — cheap tag-diff of upstream release vs vendored tree, classifying hits as clean-pullable / divergence-point / ensemble-addition; surface pin *staleness* so "4.5 months silent" becomes visible [R §6].
4. **Update application policy per class** — data paths pull freely; port paths alarm + reconcile; owned paths are invisible to sync.
5. **License/provenance carry** — license text placement, per-item license/attribution preservation (`source` blocks; census §2.3), trademark caveats surfaced at *use* time (when brand-themed output leaves internal use) [R §7].
6. **Zero OD-specific logic in the mechanism** — it must operate on (pin, path-classes, markers, tag-diff), not OD concepts, or it does not generalize.

Out of scope for the plugin tree, explicitly: brand presets [R #9] and functional skill execution — the 163 `skills/` dirs are ~half stubs pointing at upstream bundles (`od.upstream`), so at most the *catalogue* is vendored [R §2, §8].

### 4.4 Honest risk register for B-plugin

- 🟡 **Upstream churn at divergence points** — every prompt-side upstream change is manual reconciliation; the upstream repo's own #7568/#7651 mirror-drift incident (a one-sided prompt fix costing a ~2× follow-up PR) is the cautionary precedent [R §6]. *Mitigation direction: keep the port surface minimal (see next round #1) and the divergence markers sparse.*
- 🟡 **Quality-parity decay** — our port starts at Sep-10 semantics (RULE 1 skip) but upstream's craft is maintained by 4,465 lines of prompt tests + telemetry we will not replicate; without sync discipline B's freshness advantage decays into the open-design-mcp failure mode [R §1.A, R §6]. *The sync mechanism is not bureaucracy; it is the difference between B and the precedent.*
- 🟡 **Fork-maintainer posture change** — ensemble becomes a third prompt tree behind OD's own two (daemon + contracts) [R §1.B]. This is a permanent maintenance identity change, not a one-time port.
- 🟢 **License/attribution ops** — small but ongoing: per-item license preservation (census §2.3). On trademarks, the Apache-2.0 obligation is the §4(c) notice duty; brand names/logos/fonts inside design systems are **not OD-licensed** (third-party rights) — internal mockups fine, public/commercial output needs separate review [R §7].
- 🟢 **Platform tax** — over-designing the manifest/sync into a framework before a second plugin exists. *Mitigation direction: v1 concepts generalizable, schema opendesign-shaped.*
- 🟢 **Vision-model budget behavior unknown** — the native path's reasoning-budget controls are designed against an unverified model behavior (see OQ5).

---

## 5. A / B-plugin / C — Direction-Level Comparison

### 5.1 Axis table (four criteria)

| Criterion | A — fix MCP path | B-plugin | C — hybrid |
|---|---|---|---|
| **Resilience** | 🔴 Structurally blind: relay is finish_reason/usage-blind by construction [E §4]; best case = ensemble-side post-hoc completeness gate + blind retry | 🟢 Closes the whole gap list by construction: finish_reason visibility, usage/reasoning accounting, truncation markers, parse5 EOF gate, informed retry-on-truncation [E §11.3, R #16] | 🟡 Native lane resilient; OD lane (exploration) keeps every blind failure mode |
| **Compatibility** | 🟢 od-tool surface (17 live-bound — referent note below), OD UI, projects, artifact URLs all untouched | 🟡 Designer lane re-tools to native tools; persistence/serving seam re-homed ensemble-side (artifacts are files [R §4]); OD daemon retirable | 🟢 Nothing breaks; both surfaces coexist |
| **Updatable when OD updates** | 🔴 Prompt drift *unfixable by version bump* (0.16.1 = npm-latest, vendored snapshot 4.5 months stale) [E §2.3:100]; patching = unmanifested fork of compiled dist JS | 🟢 Data layer syncs cleanly at tag cadence; code layer *alarms* — updates reachable but cost reconciliation | 🔴 Keeps the unfixable artifact in the loop forever; plus a second (native) stack to update |
| **Design quality** | 🔴 Capped at May-17 prompt semantics incl. the H3 amplifier; lint stays greppy | 🟢 Starts at current upstream semantics (RULE 1 skip) + completeness gates — *a complete decent artifact beats a brilliant partial*; risk = decay without sync | 🟡 Mixed: OD-UI exploration uses current daemon prompts, but *agent-driven* exploration via MCP tools still rides the stale snapshot |

*Tool-surface referent: "17 tools" = live-bound on the ensemble side (2026-10-06 smoke log); the MCP package registers 10 `od_*` tools; OD's in-repo forwarder exposes 22. If the binding surface is the package's 10, B's compatibility bill is smaller than the table implies.*

### 5.2 Per-path detail

**A — Fix the MCP path.**
*Feasibility:* mechanically easy, conceptually hollow. **Fact:** "fix" decomposes into (i) caller-side palliatives — skip-discovery `projectInstructions` prefix (H3-confirm predicts thinking shrinks sharply [E H3]), maxTokens tuning, ensemble-side completeness validation of returned HTML — S-class, leaves the chain blind; (ii) patching the vendored prompts inside the installed shim's `dist/` — S-class but a permanent fork of *compiled JS* with no source tree, no manifest, no sync; (iii) patching the OD daemon relay in our checkout (surface `finish_reason`/`usage`, distinguish `[DONE]` vs close) — M-class and a soft fork of a live daemon whose upstream pulls now conflict. **Inference:** every version of A beyond (i) *is* vendoring — without discipline. The dispatcher's observation is confirmed by the maps: A converges on B's mechanics while forfeiting B's rationale.
*Effort class:* **S** (palliatives only) → **M + recurring fork tax** (any real fix). *Key risks:* silent-truncation recurrence at H1 rates; permanent stale-prompt ceiling; two accumulating local patch sets nobody can rebase. *Optimizes:* short-term compatibility, zero transition. *Forfeits:* observability, prompt freshness, the simplification the user is actually asking for.

**B-plugin — vendored tree + manifest + native generation.**
*Feasibility:* good and bounded. **Fact:** the minimum viable set is data copies + a hours-to-days composer port (or sidecar) + mechanical brief-catalog port + authored Turn-3 + two S-class gates [R §5]; the runtime replaced is ~a relay + prompt library [E §11.1]; all ENTANGLED items are explicitly excludable [R #9/#17/#18].
*Effort class:* **M.** Not S: platform sync mechanism + Turn-3 authorship + persistence-seam re-homing + the designer-lane re-tool stack above "copy some files." Not L: no DB port, no runtime port, near-zero data churn, and the maps have already de-risked the inventory.
*Key risks:* §4.4 register (divergence-point churn, parity decay, fork-maintainer posture). *Optimizes:* resilience (fully), updatability (decisively), quality freshness; retires a daemon dependency, its credential/env coupling, and an unfixable npm artifact. *Forfeits:* short-term tool-surface stability; assumes a permanent sync obligation.

**C — Hybrid.**
*Feasibility:* high — the lanes are already separate [R §1 lane split]; OD keeps projects/UI/exploration while the native path takes production generation.
*Effort class:* **M as a permanent state** (two prompt stacks, two credential paths, the live-bound od-tool surface retained, dual-lane semantic divergence to police); **S⁺ as phasing** — it is the front half of B's M with the compatibility risk removed.
*Key risks:* institutionalizes the "too many systems" problem the user named; every production incident retains the OD lane as a confounder; agent-driven exploration keeps the stale prompts (MCP lane). *Optimizes:* compatibility, reversibility during transition. *Forfeits:* single source of prompt truth; simplification.

**Inference (structural):** C is not a third destination — it is B's on-ramp (this presupposes B as the destination; if the user settles on A's end-state, C is a permanent hybrid, not a transition). The real decision is between A's end-state (permanent MCP lane, locally patched) and B's end-state (ensemble-native, OD-as-plugin), with C available as B's migration shape.

---

## 6. Recommendation

**B-plugin — the evidence supports the user's lean, and I recommend it as the end-state direction.** The decisive facts: (1) A is not "stay on upstream" — its prompt lane is a 4.5-month-stale vendored snapshot that no version bump can fix [E §2.3:100], so A is vendoring *without* discipline while B is vendoring *with* it; (2) only loop ownership closes the failure class we actually hit on 2026-10-06 — the gap list is structural, not a bug [E §4, §11.3]; (3) the data layer (the bulk of the tree) is a near-perfect vendoring fit with zero observed minor-version churn [R §6]; (4) the runtime being retired is small [E §11.1], so B's effort stays M, not L.

**Two honesty amendments the user should weigh:**
1. **B-plugin's shape is three relationships, not one.** "Most upstream files kept intact" is true for the data layer and only aspirational for the prompt-code layer, where the honest label is *thin-port with a drift alarm* (or a Node sidecar, if fidelity ever outweighs the philosophy). The manifest must model copy/port/own distinctly or it will misrepresent the port layer's update cost.
2. **Phase it like C, end at B.** Keep the OD daemon for projects/UI/exploration while the native generation lane lands; treat C as a migration property with a defined convergence, not a permanent dual-stack — permanent C contradicts the plugin philosophy it is meant to serve.

**What must still be true for B to work** (if any fails, revisit):
- Upstream keeps the data-resource layer near-frozen — **caveat, load-bearing: this premise rests on ONE minor-version churn sample (v0.23.0→v0.24.1); a second sample must confirm before the sync-cadence claim hardens** [R §6].
- The mockup-lane port surface stays small — the classic prompt stack we port remains maintained upstream (OD-Next momentum is the erosion risk [R §1.B]).
- Turn-3 is authorable clean-room (or the shim license verifies permissive) [R §8.1].
- The manifest+sync stays a thin discipline (tag-diff + markers), not a framework — else platform tax exceeds value.
- Vision-model reasoning/budget behavior is controllable via request fields — currently *absent by omission* on the OD lane [E §3]; must be verified, not assumed.
- The persistence/serving seam re-homes without breaking the designer workflow (artifacts are files [R §4]; project instructions are config).

**Timing note (Judgment):** the live pain has an S-class palliative on any path (skip-discovery prefix + ensemble-side completeness check before accepting output — directly suggested by H3-confirm [E §10] and the portable parse5 gate concept [R #16]). The direction decision therefore does not need to be made under outage pressure.

---

## 7. Open Questions (numbered)

1. **Prompt-code consumption mode:** Node sidecar vs prompt data-fication vs thin-port — decided by the true size of the mockup-lane port surface (which of ~40 `ComposeInput` params and which of the 18 mirror files do we actually compose from?) and by whether the contracts mirror suffices despite the `core-slim` absence [R #11, R §1.B:47].
2. **Prompt lineage target:** classic stack vs OD-Next (`od-next-strategy.ts` 1,061 lines, born 2026-08-24, hottest churn) — and upstream's maintenance commitment to each; this sets the port base's half-life [R §1.B, §6].
3. **Curated subset vs full vendor** of 154 design systems + 115 templates + 106 prompt-templates: matching completeness (SKILL.md `triggers`, `design_system.requires`) vs storage/license/trademark surface [R §2].
4. **npm shim internals + license:** does `open-design-mcp@0.16.1` do its own truncation detection; does it consume `@open-design/contracts` or re-implement; what license — gates Turn-3 port-vs-authorship [R §8.1].
5. **Vision-model reasoning-budget behavior:** which reasoning/thinking fields does the `vision` proxy accept; is reasoning budget separable from output budget; is `finish_reason`/`usage` observable ensemble-side? (Feeds both native-path budget design and H1 confirmation — the RCA commission stays the verifier of record.) [E §3, §4]
6. **Persistence/serving seam:** what replaces `od_save_artifact` URL serving and project `customInstructions` storage ensemble-side; does the designer workflow need OD projects at all? [E §1 Component C, R §4]
7. **`core-slim` asymmetry:** intentional or bug upstream (repo says "not established" [R §8.4, prompt-composition.md:83]) — determines whether the native path should include the slim charter (the daemon lane's default since ~Sep).
8. **Scope boundary artifacts:** brand presets and functional skills-stubs are ENTANGLED/catalogue-only [R #9, §2] — confirm permanent exclusion so parity claims stay honest.
9. **Trademark exposure policy:** who/what gates brand-themed output leaving internal use — an operator checklist at publish time, an agent-side refusal rule, or a lint on brand-named assets? (currently ungated) [R §7]
10. **Sync-mechanism minimum:** what is the least mechanism that makes "4.5-months silently stale" impossible (pin-staleness surfacing? sync check at promote?) — the shim precedent defines the failure mode to engineer against [E §2.3, R §6].

---

## 8. Next Exploration Round (scoping questions, not tasks)

1. **Port-surface probe:** enumerate exactly which prompt modules and composer parameters the mockup/deck lane composes from (classic stack); produce the precise file + param list a thin-port would carry. *(Feeds OQ1/OQ2, prices the M.)*
2. **Upstream trajectory read:** commit velocity + deprecation signals for classic vs OD-Next lanes (repo docs/issues/tags) — estimate the port base's maintenance half-life. *(Feeds OQ2.)*
3. **Shim verification:** read-only license + internals inspection of the installed `open-design-mcp@0.16.1` (same evidence base as [E §1]). *(Closes OQ4.)*
4. **Vision-proxy behavior matrix:** read-only probes of reasoning-field acceptance, `finish_reason`/`usage` visibility, and budget separation on the `vision` lane — coordinated with, but not absorbing, the gated RCA commission. *(Closes OQ5.)*
5. **Sync dry-run concept test:** express pin + relationship classes + tag-diff for the minimum set against one real upstream release pair (v0.23.0↔v0.24.1 already shows zero data churn [R §6]; extend one tag further to bound cadence) — does "clean pull except at divergence" actually hold for the data layer at release cadence, and what does the diff report look like for the port layer? *(Feeds OQ10, §4.3.)*
6. **Designer-lane consumption survey:** which of the 17 live-bound od_* tools does the live designer workflow actually exercise beyond generate/compose/save — defines B's true compatibility bill and the re-tooling scope. *(Feeds OQ6.)*

---

*Synthesis: Architect (Parts 2+3) over the two Part-1 maps; all OD claims carry the maps' file:line citations. Fact/Inference/Judgment tagged at load-bearing points. Discussion-stage: no implementation content beyond direction.*

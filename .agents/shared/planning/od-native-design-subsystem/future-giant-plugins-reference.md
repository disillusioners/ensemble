# Future Giant Plugins — Reference: deepseek-harness

**Kind:** External reference-architecture research, discussion-level (read-only).
**Date:** 2026-10-06. **Baseline:** `plugin-pattern-review-and-plan.md` (od-native-design-subsystem, r1).
**Method:** GitHub API + raw-file reads of deepseek-ai/deepseek-harness @ master (evidence pinned to 2026-10-03 HEAD, `5badb150`); no code executed; no cloning into the ensemble repo.

---

## 1. Identity & Verdict

**The repo is real and the user's guide was accurate — literally.**

| Field | Value | Evidence |
|---|---|---|
| Identity | `deepseek-ai/deepseek-harness` ("dsh") | https://github.com/deepseek-ai/deepseek-harness |
| Description | "DeepSeek Harness: Everything is a Plugin." | GitHub API repo description |
| What it is | An open-source **agent harness** (CLI/Web/Electron/SDK runtime for agents) by DeepSeek AI | README: "an open-source agent harness developed by DeepSeek AI" |
| Language / license | TypeScript / MIT (THIRD_PARTY_NOTICES.md maintained) | API `language`, `license.spdx_id` |
| Scale | ~244.4K stars, ~29.3K forks, repo ~269 MB, `packages/` ≈ 50 subdirs | GitHub API, contents listing |
| Activity | Created 2026-08-13; 25 releases, `dsh-v0.1.0-rc.7` (2026-08-17) → `dsh-v0.2.1-alpha.1` (2026-10-03); PR numbers ≥ #5648 in ~8 weeks; last push 2026-10-03 | API commits/releases |
| Maturity | **Developer preview**: "THERE WILL BE COMPATIBILITY-BREAKING CHANGES." | README "Developer preview" section |
| Foundation | **Cordis** plugin framework (cordiverse/cordis, MIT, ~9K stars, since 2022), with an arXiv paper ("A Programming Paradigm for Spatiotemporal Composability", arXiv:2608.25512) | README; docs/cordis-primer.md; API |

**Verdict:** "Everything is a plugin" is not marketing — it is the operating principle, stated flatly in `docs/architecture.md`: *"Every part of the product is a plugin, including the model adapter, the tool registry, the session log, and the agent loop itself, so each is replaceable from configuration. **There is no privileged core to patch.**"*

**But the mental model needs one correction.** dsh is not a system for *integrating external giants as plugins*. It is a product **built from day one as an in-process plugin composition runtime** (Cordis): ~50 first-party packages in a monorepo, all plugins. The only place dsh itself integrates a giant — the Cordis framework layer — it does **not** plugin-ize it; it **vendors it with a provenance manifest and a patch log** (`vendor/README.md`), for reasons that read like our own convention: *"so that the harness fully owns its framework layer (auditable, patchable, pinned)."*

So the reference value is doubled and mirrored: (a) a full-cost demonstration of what runtime "everything is a plugin" actually weighs — which **validates our divergence** (resource plugins on a native core, not runtime plugins); and (b) an industrial-grade specimen of exactly our vendoring discipline — which **independently converges with our convention** and sharpens it in two places (§5).

---

## 2. Plugin Architecture Anatomy

### 2.1 What a plugin concretely is (Cordis, in-process)

From `docs/cordis-primer.md` ("Cordis In Five Ideas"):

- **"A plugin is an object that implements Service"** — a function with optional `inject` and `apply(ctx)` fields, or a `Service` subclass whose lifecycle Cordis mounts into the current context.
- **"A context is a repository of services"** — a service claims a stable `ctx.<key>` (`ctx.tools`, `ctx.llm`, `ctx.sessions`); plugins find each other by key, never by importing a concrete implementation.
- **"Declare service dependency via `inject`"** — load order is expressed as service requirements, not boot sequencing.
- **"Typed Events for communication"** — five dispatch modes (`emit`, `waterfall`, `parallel`, `serial`, `bail`); the mode is part of the event's public contract.
- **"Registrations are reversible effects"** — everything installed via `ctx.effect()`/`ctx.on()` unwinds predictably on unload/HMR.

### 2.2 Is the CORE itself a plugin? Yes — the core is a *composition*, not a module

- Boot assembles a **plugin tree from ordered layers**: a **profile** (web / headless / sdk / sdk-minimal / acp) stacks **bundles** (`dsh-base` = model adapters, tools, persistence, sandbox+approval, settings, credentials, telemetry; `dsh-web-app` adds the browser app, etc.), then the profile's `cordis.patch.yml`, then home-level patches, then `--patch` overlays. *"Any row it prints can be replaced by a patch of your own."* (`docs/architecture.md`, "Profiles and bundles"; `dsh --profile web --dump-config`).
- Core packages table names the spine as ordinary plugins: `core/session` (`ctx.sessions`), `core/tools`, `core/agent` (the `Agent` interface + registry), **`core/agent-loop` — "the default driver implementing that interface"** — i.e., the agent loop is replaceable by config row replacement.
- **Capability seams** (`docs/architecture.md` §Capability seams): *"a swappable capability with three roles: a Service Definition declaring the interface, a Service Provider implementing it, and a Consumer using it… **one role alone is not a seam; adding a capability means designing all three**."* Example given: filesystem and subprocess providers share one execution world, so pointing them at a remote sandbox moves Bash, PTY, and LSP with them, "with no provider forks."
- **Events are the extension points** — three domains (durable session events; live `agent/*` events; capability events `fs/*`, `tools/*`, `telemetry/*`). Waterfalls are around-middleware (`next()` to delegate, return to short-circuit).
- A **generated capability graph** (`docs/capability-seams.md`, 667 lines, machine-generated by `scripts/gen-doc-graphs.ts`) catalogs ~100 service seams with owner/implementer/consumer packages — the navigability crutch that keeps the indirection manageable.

### 2.3 Out-of-tree plugins: registration, discovery, versioning, isolation

From `packages/boot/plugin-manager/README.md`:

- **Discovery/install:** `dsh plugin` / the `plugin_manager` tool / Web Plugins page install npm-hosted **bundles** via pnpm into the profile; discoverability convention is the GitHub topic `dsh-plugin` (README). Bundles declare a `dsh.bundle` patch file; the profile stacks them.
- **Version compatibility:** peer-dependency check against the DSH runtime **before** pnpm runs ("an incompatible DSH peer rejects the operation before pnpm runs"). Escape hatch: per-profile `compatibility.json` exemptions — exact `package@version` × exact runtime version, granted only with `acceptRisk: true` after warning "that incompatible plugins may cause crashes or data loss".
- **Isolation boundary: none at the host level.** *"Installed Host code executes in-process outside the workspace sandbox."* Trust is managed by approval UX (`danger-full-access` or per-call approval), registry inspection (`pnpm view` pre-read, GitHub `git ls-remote` reachability check), and build-script approval — not by process/container isolation.
- **Lifecycle:** HMR-enabled profiles apply config changes immediately (serialized module reloads); otherwise `restart-required`. Every registry gets an **HMR-safety test** ("dispose the contributing fiber, assert cleanup" — `docs/testing.md`).

### 2.4 The one giant they integrate: Cordis is *vendored*, not plugin-ized

`vendor/README.md` (69 lines) is the closest thing in dsh to our plugin convention — and it is strikingly parallel:

- **Manifest table**: per vendored package — directory, scoped npm name (`cordis` → `@deepseek-ai/cordis`), upstream name, version, upstream repo, **commit pin** (e.g., cordis `56b3d4f7…`, cosmokit `16f6fc05…`).
- **Exhaustive divergence log**: *"Keep this log exhaustive — every divergence from upstream must be listed."* Currently **23 numbered entries**; files touched and rationale are universal across all 23, while upstream PR links and covering tests appear on the larger behavioral entries (e.g. #8, #15), not every entry. Entries range from comment-only JSDoc enrichment ("Retire this entry when the enrichment is upstreamed to the fork") to deep behavioral ports (fiber lifecycle hardening; durable debounced writes; Node loader compat).
- **Sync procedure (5 steps, manual):** note upstream HEAD → copy `src/` over → **"Re-apply the local modifications listed above (or drop them if upstream made them unnecessary — update the log either way)"** → update version+commit in the manifest → `pnpm install && test && build`.
- **License carry:** "Upstream MIT `LICENSE` files are preserved in each package directory."
- **Exclusions:** an "Intentionally **not** vendored (verified unused by this set)" list — a parity boundary.
- **Fork strategy:** hot upstream pieces are forked into their own org (github.com/deepseek-harness/{cosmokit,schemastery,cordis}) so divergences can live as upstreamable PRs; others track cordiverse/* directly.

---

## 3. Problems Solved vs Costs Paid

**Problems the model solves** (all evidenced above):
1. **Total replaceability** — every capability, including the agent loop, is swappable by a config row; no privileged core to fork. One provider swap moves a whole product surface ("pointing [fs+subprocess] at a remote sandbox moves Bash, PTY, and LSP with them").
2. **Ordered composition without code** — profiles/bundles/patches let ships (web/headless/sdk/acp/desktop) and users share one codebase as different layer stacks; `sdk-minimal` proves the escape hatch (one bundle owning its complete explicit tree).
3. **Reversible extension** — registrations unwind on unload; HMR reloads without restarts; teardown ordering is a tested contract, not folklore.
4. **Contributor scaling at extreme velocity** — ~5.6K PRs in ~8 weeks against 50+ packages: the seams, the cookbook checklists (`docs/cookbook/adding-a-package.md`), the generated capability graph, and per-package README contracts ("Understand the implementation", "Known Limitations and Deferred Work" — gate-enforced by `scripts/verify-package-readme-limitations.ts`, documented in `packages/README.md` and `docs/cookbook/adding-a-package.md`, wired via `run-gates.ts`) are what keep that velocity from rotting.

**Costs paid** (visible, not hypothetical):
1. **Mechanism weight.** The plugin-manager README alone documents lock takeover, idle-timeout classification, registry fallback chains, manifest/lockfile snapshot-restore, build-script approval, version exemptions. That is *one* package of ~50. Behind it: HMR serialization, profile layering semantics, patch-algorithm edge cases (log entry #11: patches indexing inserted rows so "surface-only rows would otherwise be unreachable from user config").
2. **Contributor onboarding tax.** `docs/architecture.md` opens: *"Read this before changing anything under `packages/`. It assumes you know Cordis; if you do not, start with the primer or the tutorial."* The docs corpus (primer, tutorial, glossary, event producer/consumer map, module graph, graph atlas, defensive patterns, postmortems) is itself load-bearing infrastructure compensating for the indirection.
3. **Testing tax.** Per-file **100% coverage gate** on `packages/*/*/src`; every registry needs an HMR-safety dispose test; every non-trivial change requires a keyless recorded-session snapshot scenario; real-API e2e tier; performance PR gates (`docs/testing.md`).
4. **Compatibility churn.** Developer preview + peer-range rejections + an `acceptRisk` exemption UX = the price of a plugin ABI while the core iterates weekly.
5. **Trust model risk.** In-process host plugins with no sandbox; safety rests on approval flows and registry pre-checks. For a security posture like ours, that is a non-starter, not a cost.
6. **Vendored-layer maintenance is manual and heavy** — the 23-entry patch log across 9 packages must be re-applied by hand at every sync. This is the lived version of the failure mode our review flags as 🟡 "port-layer churn degrading to manual-merge-every-release" — survivable only because they constrain it to one layer (the framework) and industrialize the log.

---

## 4. Comparative Notes vs Our Convention

Baseline: `plugin-pattern-review-and-plan.md` (od-native-design-subsystem, r1). The (a)–(f) pillar decomposition below is this deliverable's construction from that doc's five-component shape, not the plan doc's own labeling.

| Axis | dsh | Our convention | Verdict |
|---|---|---|---|
| (a) Provenance classes | `vendor/README.md`: manifest table (frozen record w/ commit pins) + exhaustive divergence log + not-vendored exclusions + preserved LICENSE files | 3-class manifest: copy-freely / snapshot-with-drift-alarm (renaming the plan doc's `port-with-alarm` class to `snapshot-with-drift-alarm` for descriptiveness) / own-outright + license carry + parity-boundary (N9) | **AGREE — independent convergence, near-isomorphic.** Their two artifacts ≈ our two "cold" classes; their own `packages/` ≈ own-outright. |
| (b) Pinning | Commit pins, one per vendored package (whole-package granularity) | Tag-only pins, per relationship class | **Diverge, both defensibly.** Commits are forced on them (patch re-application needs exact trees); our tag-only rule is right for data snapshots (review §6 🔴 guard). Their whole-package + per-divergence-log is a viable alternative shape worth remembering. |
| (c) Sync/drift | Manual 5-step procedure; manifest's commit column *is* the staleness record; no automation, no alarms | Sync-runner + trigger-engine drift alarms; scheduled + on-demand cadence (review Q4) | **Diverge.** They accept manual cost on ONE layer; we automate because our ambition is N plugins. Their 23-entry log is the strongest evidence yet that the port class must stay *thin* (review's one-gap critique). |
| (d) Consumption surface | Capability seams: Service Definition / Provider / Consumer; "one role alone is not a seam"; roles split into packages "when they evolve independently (the shell trio is the template)" | Port + Adapters: skills for worker agents, plugin-proxied tools only where runtime capability required; Port not yet chosen (critique #2) | **AGREE at the vocabulary level** — their three-role rule is a checkable form of our "one Port, N Adapters". Mechanism differs (in-process registry vs agent skills/tools). |
| (e) Agents as extension units | Subagent providers are a seam (fresh child agent → delegated turn in another product); agents are consumers, not the extension primitive | Skills-not-new-agents (dedicated agent only if need demands) | **Mild AGREE** — same instinct: agents sit behind capability interfaces; the extension unit is the capability. |
| (f) Core execution | **No native core** — the product IS a plugin composition; the one giant adopted (Cordis) is vendored, patched, and executed (they run it, they fixed its fiber lifecycle) | Native core executes everything; steady state is a vendored consumer on our runtime (Strangler-style phasing may transit upstream engines during migration) | **Fundamental DIVERGE.** Different problem: they built a product atop a runtime they must own; we integrate external giants as resources onto a core we keep native. Their tax (§3) is the price of composability we deliberately don't buy. |
| Upstream relationship | Fork hot pieces into their org; shape divergences as upstreamable PRs; retire log entries when upstreamed | Snapshot at tags; divergences alarmed, not upstreamed (we're consumers, not fork maintainers) | **Diverge (contextual).** Fork-hosting is viable for us only if we ever become upstream contributors to a plugin's source. Neutral for now. |
| Docs-as-infrastructure | Generated capability graph, event maps, cookbook checklists, decision notes (`.agents/notes/implemented/…`), postmortems — machine-checked where possible | Review doc + investigation; drift-alarm fan-out planned via trigger engine | **AGREE in spirit** — both treat documentation of seams as load-bearing. Their *generated* graph is the interesting form. |

---

## 5. What to Borrow NOW vs Defer

### Borrow NOW (2 items, both convention-level, zero mechanism)

**1. The exhaustive divergence-log convention for the port-with-alarm class** *(from `vendor/README.md` §Local modifications + §Sync procedure)*

Adopt, as part of the plugin manifest spec for any snapshot-with-drift-alarm path: a **numbered, exhaustive divergence register** — each entry naming the file(s), what diverged, why, the upstream ref/PR if one exists, and **the test that pins the divergence** — with the sync rule written exactly as theirs: *"re-apply or drop, update the log either way."*

Load-bearing argument: our review already flags port-layer churn → manual-merge-every-release (🟡) and unassigned drift-alarm ownership (critique #1) as the convention's two live risks. The divergence log directly answers the churn risk and is a precondition for ownership tracking — the plan doc's direct answer to critique #1 is Renovate-style reviewer-assignment; the two are complementary, not substitutive. dsh demonstrates at industrial scale (23 divergences, 9 packages, weekly syncs, 8 weeks of churning previews) that **the log is the artifact that survives** — not the patches, not memory. Divergences on plugin #1 (OD prompt-layer port) start accruing from the first sync; a log started late has silent gaps, which is precisely the tombstone failure our 3-class manifest exists to prevent. Cost: a documentation format decision, made once, now.

Refinement worth capturing from their fork practice: where a divergence could plausibly be upstreamed, shape it as an upstreamable PR against a fork so reconciliation can degrade to *merge* — and retire the log entry when it lands (their entry #7 does exactly this).

**2. The three-role seam rule as the design-time gate for our Port decision** *(from `docs/architecture.md` §Capability seams + `docs/cookbook/adding-a-package.md` §3)*

When we run the owed Port-definition probe (review critique #2 — the undecided Port; the 10/17/22 referent gap at inv §5.1; the port-surface probe owed at inv §8.1), require all three roles explicit before any adapter is built: **Service Definition** (the Port interface: generate / compose-brief / save / lint), **Provider** (the adapter tool implementing it against the plugin's resources), **Consumer** (the designer workflow / job runner invoking it) — with the rule verbatim: *"one role alone is not a seam; adding a capability means designing all three."*

Load-bearing argument: our review names Port-layer mislabeling as "the rot vector" (critique #5) and the undecided Port as the top 🟡 gap. This rule is the cheapest possible form of that discipline — a checklist, not a framework — and it is exactly what lets plugin #2/#3 add Providers without spawning new Ports (the "one Port, N Adapters" rule our review already states, now with a checkable shape). It also cleanly answers "which side owns the contract": the Definition does, and it lives on our side.

### Defer to plugin #2/#3 (or never) — the entire runtime-composability stack

Profiles/bundles/patch layering, HMR, a plugin manager (registry fallbacks, lock choreography, build-script approval), peer-range compatibility + risk-acknowledged exemptions, in-process third-party plugin hosting. **None of it pays until we host third-party runtime code — which our pillar (f) explicitly rules out.** The deferral trigger is identifiable and honest: if a future giant *cannot* be resource-ized (no consumable artifacts, engine-only), the dsh shape — vendor the engine, patch-log it, wrap it as one Provider behind an existing Port — is the reference pattern, and *that* is the moment to revisit. Until then it is mechanism weight with no customer.

---

## 6. Convention Adjustment Recommendations (discussion-level)

1. **Manifest spec v1 (no schema change): add the divergence-register section** for snapshot-with-drift-alarm paths — numbered entries, per-entry fields {files, delta, rationale, upstream-ref, pinning-test}, and the sync-rule sentence. Also adopt their two-line hygiene: preserved upstream LICENSE files inside the vendored tree, and an explicit *intentionally-not-vendored* list (our parity-boundary N9 already implies this — make it a manifest section).
2. **Port-definition checklist (when the §8.1 probe runs):** the three-role gate as a design-time artifact — no adapter-tool is approved until Definition/Provider/Consumer are all named, with the Definition owned by us.
3. **Pin granularity: keep tag-only, per class** — dsh's commit pins are a consequence of running patched upstream code; we don't run upstream code, so tags remain the honest unit. Record the alternative shape (whole-package pin + divergence log) in the convention doc as the fallback if a port class ever thickens into a maintained patch set.
4. **Anti-borrow (write it down so we don't drift):** no runtime plugin ABI, no in-process third-party code, no compatibility-exemption UX. Seeing the full price tag of "everything is a plugin" (§3) is the strongest external validation of our native-core pillar — capture that as a one-paragraph rationale next to pillar (f) in the convention doc.
5. **Optional, cheap, borrowed inspiration:** their *generated* capability graph (`gen-doc-graphs.ts`) suggests that when plugin #2 lands, a generated "plugin surface catalog" (Ports × Providers × consumers × drift status, rendered from the manifests) would cost little and keep the convention navigable. Not needed for one plugin.

---

## Uncertainty & Honesty Notes

- All findings rest on **documentation and repo reading**, not execution; the docs are unusually implementation-faithful (per-entry test citations, generated graphs), but runtime behavior was not verified.
- Scale numbers (244K stars in ~8 weeks) are as reported by the GitHub API on 2026-10-06; plausibility was not independently audited.
- dsh is in **developer preview with promised breaking changes** — its plugin ABI and profile semantics may shift under our citations. Pin conclusions to the cited files/sections, not to future state.
- Not examined (out of scope, flagged for honesty): `BENCHMARK.md`, the Python SDK internals, `native/`, Electron host details, the arXiv paper's formal model beyond its existence.
- The comparison baseline is our own r1 review doc, which itself carries open items (Port undecided, one-sample churn premise, owed probes) — where dsh evidence bears on those open items it is noted, not silently merged.
- Downstream reuse of quotes should re-cite the pinned sha `5badb150` (repo is hyper-active; current master matched the pin at review time but will drift).

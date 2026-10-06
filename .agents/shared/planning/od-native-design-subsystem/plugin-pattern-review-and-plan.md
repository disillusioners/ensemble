# Plugin-System Pattern Review + Fast Plan (r1)

**Kind:** Overview-level review+plan pass (user steer 2026-10-06: OVERVIEW FIRST, concise; depth deferred).
**Question:** Is the proposed plugin shape (vendored tree + three-class provenance manifest + drift-alarm sync + skill/adapter consumption + own-core execution) sound against established patterns? What is the minimal generalizable build order? Answers to 4 convention questions.
**Evidence:** `architecture-investigation.md` (r2, review-approved) cited as (inv §n); maps (R §n)/(E §n) via the investigation. Workers: `trade-off-analysis` (pattern lens) + `structural-design` (component lens). Feeds the PENDING user end-state decision — does not replace it.
**Status:** Delivered. No skill-bank misses (both confirmed loaded). Two unverifiable citations in the pattern worker's report were dropped in synthesis; all verdicts below rest on established pattern vocabulary.

---

## 1. Review Verdict — the patterns lens

**Overall: the shape is sound. It is not novel — it is a disciplined re-assembly of five solved patterns, and that is a compliment.** The one genuinely load-bearing insight (the three-class relationship model) is correct and is what separates this from the failure precedent.

| Piece of the shape | What it ACTUALLY is (prior art) | Verdict |
|---|---|---|
| Vendored tree + pin | **Package-manager vendoring + lockfile** (npm `ci` / Cargo `--locked` / Go modules) over a **COTS** artifact. `VENDORED_FROM.md` in the shim = a lockfile with no sync — the tombstone precedent (inv §4.3 #1) | ✅ Right pattern; failure mode correctly identified |
| Three-class provenance manifest | **Vendoring manifest with per-path reconciliation policy** — Cargo `[patch]` / npm `overrides` / Composer merged-config + patch queues; license side = SPDX-style classifier. Class semantics = frozen / patched / local in prior-art terms | ✅ Right pattern — the load-bearing insight (inv §4.3 #2). Borrow semantics; our three names may stay but document the mapping |
| Snapshot + drift-alarm sync | **Distro patch-queue discipline** (quilt `.series` / FreeBSD ports) + **declarative drift detection** (`terraform plan`, Renovate dashboards): frozen upstream + local patches + diff-classify-alarm | ✅ Right pattern — **strongest prior-art match in the sketch**. git-subtree correctly rejected upstream of this (inv §4.1: no divergence modeling) |
| Skill + adapter-tool consumption | **Ports-and-Adapters / ACL + Facade**: the worker talks domain vocabulary (skill); proxied tools are Adapters behind a Port; plugin is the anti-corruption layer against upstream concepts | ✅ Right pattern, **one gap: the Port is not yet chosen** (10 vs 17 vs 22 tool referents, inv §5.1 note) — see critique #2 |
| Execution on own core | **Strangler Fig** = the *migration discipline* (inv §6 amendment 2 "phase like C, end at B" is textbook strangler phasing); the *steady state* is simply a vendored consumer on our runtime | ✅ Right pattern; name the phase and the end-state differently |

### Gets right (pattern-grounded)
1. Strangler framing for the migration — small victim (~a relay + prompt library, inv §2.1), loop ownership is the payoff.
2. Three-class manifest — the only honest way to model update cost; upstream's own two-tree mirror drift validates per-class reconciliation (R §1.B).
3. License/trademark carry at **use-time** (inv §4.3 #5) — the when-not-where distinction is a rare correct call.
4. Track tags not CHANGELOG (inv §4.1) — ports/quilt discipline; CHANGELOG-as-record already retired upstream.
5. Worker-as-consumer / plugin-as-resource = the hexagonal split, implemented without naming it — resilience from the host core by construction.

### Reinvents something solved
1. **Manifest schema** — Cargo `[patch]`/npm `overrides`/Composer already model copy/patch/local in one file; survey before coining fields (platform-tax guard, inv §4.4).
2. **Drift detection** — established class (declarative drift detection); tag-diff is one implementation, not a new idea.
3. **Per-class update policy** — Dependabot `groups:`-style per-glob strategy; "class" should key existing concepts.
4. **Port/Adapter vocabulary** — call the worker-facing interface the Port and the plugin-side implementation the Adapter; "plugin-proxied tool" underspecifies which side owns the contract.

---

## 2. Sharpest Critiques (5) + pattern-literature answers

1. **Drift-alarm OWNERSHIP is unassigned** (🟡, inv §4.4 fork-maintainer posture). An alarm with no reconciler is noise plus a hidden SLA. *Answer:* vendor-fork discipline — assign a per-class **alarm owner role in the manifest itself**; default action = reconciliation ticket in the job queue; an alarm unowned for N days escalates (block promote). Renovate's reviewer-assignment is the reference shape.
2. **The Port is undecided — 10/17/22 referent gap** (🟡, inv §5.1). Hexagonal rule: **one Port, N Adapters**; the Port is derived from the designer workflow's actual use cases, not from any tool registry. *Answer:* run the owed consumption survey (inv §8.6), define the Port (generate / compose-brief / save / lint), let adapter count fall out.
3. **One-sample churn premise** (🟡, inv §6). The whole sync-cadence claim rests on v0.23→v0.24 only. *Answer:* burn-in — confirm with a second tag pair via the sync dry-run (inv §8.5) before cadence hardens.
4. **Use-time trademark carry has no executor** (🟢→mechanism owed, inv OQ9). Correct intent, no mechanism. *Answer:* a review-time escalation rule (operator checklist at publish/distribution) — brand-named system + external distribution ⇒ escalate; internal mockup ⇒ silent. Agent-side refusal rules can wait.
5. **Port-layer mislabeling is the rot vector** (from the structural pass). Labeling hot prompt code `copy-freely` recreates the shim failure inside our tree. *Answer:* the class vocabulary itself is the guard — 3 classes in v1 with an explicit extension rule; the sync runner must REFUSE clean-pull into a port-class path.

---

## 3. Overview Architecture (diagram-ready: literal node + edge lists)

**NODES** (10 boxes):

| id | name | role (1 line) |
|---|---|---|
| N1 | upstream-repo | OSS source of truth @ pinned git tag |
| N2 | plugins/`<name>`/ | vendored tree on disk; three subtrees keyed by relationship class |
| N3 | manifest | tree-root provenance record: per-class tag pins + license carry + exclusions |
| N4 | path-classifier | maps path → {copy-freely, port-with-alarm, own-outright} (Strategy) |
| N5 | sync-runner | tag-diff upstream vs vendored → clean-pull / alarm / skip per class (Command) |
| N6 | plugin-skills | dynamic-skill content; the worker-facing consumption surface |
| N7 | adapter-tools | slim native tools behind the Port: generate (save-and-summarize, gates inside), save, lint |
| N8 | ensemble-core | agent loop + LLM proxy + retries + finish_reason/usage capture + completeness gates |
| N9 | parity-boundary | declared NOT-COVERED list (brand presets, functional skills, OD-UI) |
| N10 | trigger-engine | existing skill-keeper/trigger machinery reused as drift-event fan-out (Observer) |

**EDGES** (verb: what flows):

- N1 —[tag-diffs: release commits + changed paths]→ N5
- N5 —[classifies via]→ N4 —[reads classes from]→ N3
- N5 —[clean-pulls into]→ N2 (copy-freely/)
- N5 —[alarms on divergence in]→ N2 (port-with-alarm/)
- N5 —[refuses: no sync]→ N2 (own-outright/)
- N3 —[declares exclusions of]→ N9
- N5 —[emits drift events to]→ N10 —[fans out: skill-keeper / tickets / dashboard]→ N8 + human
- N2 —[supplies content indexed into]→ N6
- N6 —[loaded by worker/designer agent hosted in]→ N8
- N8 —[invokes via tool calls]→ N7
- N7 —[generates through + returns finish_reason/usage to]→ N8
- N7 —[persists artifacts inside]→ N2 (own-outright/)

*(Renderable directly as a mermaid flowchart; the chart itself is deferred until the review settles, per user steer.)*

---

## 4. Fast Plan Skeleton

**Reuse (no new code):** dynamic-skill system + skill-keeper/trigger engine (N10), agent-tool factories (N7 shell), core LLM machinery + gates (N8), MCP write path (bridge during migration only).
**New (authored):** manifest (N3), path-classifier (N4), sync-runner (N5), plugin-skill template (N6), adapter-tool template (N7), parity-boundary convention (N9).

**Build order:** ① manifest vocabulary v1 (generic, OD-shaped) → ② `plugins/opendesign/` skeleton + copy-freely data subtree @ one tag → ③ sync-runner MVP (diff+classify+report) → ④ one plugin-skill consumed by worker/designer → ⑤ Port + adapter-tools (generate with gates inside; file-based save) → ⑥ trigger-engine hookup (drift alarm → ticket/queue).
*Schema-first vs slice-first: false dichotomy — the class vocabulary is schema-first (retrofit = re-tag every path), and the first slice is what validates that schema on the lowest-risk class.*

**Load-bearing in v1** (retrofit hurts): 3-class vocabulary (+extension rule) · manifest at tree root · tag-only pins (never CHANGELOG) · license/provenance carry on the manifest · skills-not-new-agent consumption (user directive).
**Deferrable to plugin #2** (platform-tax guard, inv §4.4): tool-surface template form · sync cadence policy detail · parity-boundary format · sidecar-vs-port choice (OD-specific) · dedicated-agent path.

**First vertical slice** (proves the pattern end-to-end, data layer only): pin OD @ mapped tag → vendor copy-freely data (154 systems + 115 templates) → minimal manifest (pin + one class + license) → sync dry-run against a second tag (clean-pull proof) → one skill (list-design-systems) → agent answers a count query.
*Proves:* tag-pin round-trip · class modeling · tag-diff clean-pull · skill consumption · license carry (5 of 6 inv §4.3 problems). *Does NOT prove:* port-with-alarm path · own-outright authorship (Turn-3) · adapter-tool generation + gates · update policy per class. Those ride phases ⑤-⑥ and the owed probes (inv §8.1 port-surface, §8.5 sync dry-run).

---

## 5. The 4 Answers (pattern reasoning, concise)

**Q1 Consumption — worker + skills, NOT a new dedicated agent.** The worker is the bounded context; the skill is its facade; the plugin is the ACL. A second agent = a distributed monolith (network boundary, no isolation gain, double ops surface). "Dedicated agent only if need demands" (user directive) stays the escape hatch. *Confidence: High* — both workers converged.

**Q2 Pin granularity — per-relationship-class.** One pin per plugin is the shim's failure mode (whole-package bumps hide intra-package churn). Per-path re-implements content-selection (a `.gitattributes` concern). Per-class pins make **per-class staleness visible** — the data layer can advance on clean pulls while the port layer honestly lags at its last-reconciled tag. Cargo `[patch]` per-crate pinning is the reference. *Confidence: High.*

**Q3 Generation execution — slim native tool for the production lane; direct agent-loop call as the interactive/early mode.** The workers split (pattern lens scored direct call 4.10 vs 3.40; structural pass built the tool). Adjudication favors the **ensemble-side slim tool with gates inside** (compose → model call w/ finish_reason+usage visibility → parse5/lint gates → save → return summary+path): (1) context economy — 30-60K-token artifacts stay OUT of agent context (the project's own history-bloat pain); (2) Port discipline — one Port, deterministic surface for jobs/replay/API, matching the existing designer workflow shape (smaller compatibility bill, inv §5.1); (3) gates colocate with generation where lint/parse5 already live (inv §2.3). *Flip condition:* if no non-agent consumer materializes and artifacts stay context-comfortable, invert to direct call. *Confidence: Medium* — vision-proxy budget behavior unverified (inv OQ5).

**Q4 Sync cadence — scheduled + on-demand override, with pin-staleness surfaced at promote.** On-demand-only = pin-without-cadence (the precedent). Manual-only = the 0.16.1 stale ceiling. Scheduled tag-poll via the existing trigger engine/job queue (cheap, reuses N10) + manual "sync now" + staleness age visible at every promote/health check = the least mechanism that makes "4.5 months silent" impossible (inv OQ10). Renovate/Dependabot cron + manual rebase is the reference. *Confidence: High.*

---

## 6. Risks (severity-ordered) + Owed Probes

- 🔴 Class vocabulary mis-calibrated (2 or 5+ classes) — retrofit = whole-tree re-tag. Guard: 3-class v1 + extension rule.
- 🔴 Pin recorded as anything but a git tag — repeats the tombstone. Guard: tag-only + second-tag dry-run before slice.
- 🟡 Drift-alarm ownership unassigned (critique #1) · Port undecided (critique #2) · one-sample churn premise (critique #3) · port-layer churn degrading to manual-merge-every-release (inv §4.4 #1) · quality-parity decay without sync discipline (inv §4.4 #2).
- 🟢 Platform tax before plugin #2 · trademark executor missing (critique #4, mechanism owed) · vision-model budget unknown (OQ5).

**Owed before hardening claims** (inv §8): port-surface probe (#1) · upstream trajectory read (#2) · shim license/internals (#3) · vision-proxy behavior matrix (#4) · sync dry-run on second tag pair (#5) · designer consumption survey (#6 → Port definition).

## Gaps

None — both dispatched workers reported with skill-bank hits confirmed. Report-hygiene note: two unverifiable citations in the pattern worker's report were dropped; no verdict rests on them.

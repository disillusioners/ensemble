# TokenEconomizer Study — Writer Adjudication (decisions.md)

**Role of this file.** The final report (`report.md`) is the decision-facing artifact; this file is the adjudication record underneath it: D1–D7 re-derived from the two evidence packs (not inherited from the lost prior writer's summary), the citation rule these documents obey, and every place the evidence was insufficient, contested, or inferred.
**Precedence rule applied:** where the surviving prior-leader adjudication summary and the packs disagreed, **the packs won**; both disagreements found are flagged below (D2, D4). Everything else in the prior summary was CONFIRMED.

---

## The citation rule for these documents

A claim in `report.md` / `decisions.md` is validly cited iff it is one of:

1. **A pack-line citation** — a reference to a section/claim of `evidence-external.md` (275 lines) or `evidence-internal.md` (173 lines), e.g. "external pack §5 item 2". The packs are the authoritative intermediate source.
2. **An external clone anchor** — `file:line` against the pinned clone SHA `f1b91a40e9537c3e5fce146385366cbb009f5ecc` at `/home/nea/Code/opensource-projects/TokenEconomizer`. In THESE documents every such anchor is **carried faithfully from the external pack** (which verified its workers' anchors); this writer made **zero direct clone citations**, because the filesystem tools available to this seat are workspace-bounded to `/home/nea/ensemble-src` and the bash allowlist excludes grep — direct verification was impossible, so none was claimed. The commission's rule ("if you cite the clone directly, verify the anchor first") is satisfied vacuously and by disclosure here.
3. **An internal anchor** — `file:line` against the ensemble study tree **`bf4bfde6e` (detached HEAD), which lags `origin/latest` (`2618eb212`)** (conclusions verified at the pinned tree per the pack). Known divergences that affect anchors: POK active-entry cap 256 in-tree vs 512 on latest; the POK durable-vs-rolling guideline doc and a curator slug→row resolver exist only on latest (internal pack §H "Tree-lag caveats"). Any future reader re-checking an internal anchor MUST first confirm which tree they are standing on.
4. **A quantitative claim** — always one of the above; **no number in either document is unsourced**. Numbers originating in their system (percentages, thresholds, counts) describe THEIR measurements only; where we might exhibit similar behavior, that is explicitly marked unmeasured (see Gaps).

Line anchors drift (the external pack itself warns: "Line numbers pin to `f1b91a4` and will drift", §7.4) — the anchors are evidence pointers, not perpetual truth.

---

## D1 — Gap 1 CONFIRMED: empirical multi-session admission is absent in every store

- **Their side:** admission to the injected manual requires K_PROMOTE = 3 confirming sessions with pairwise-disjoint edited-file sets (`lifecycle.py:17, 80-88, 113-115`), a candidate must cite ≥2 window tasks (`distiller.py:31-33, 725-740`), and firing additionally needs a ≥0.40 need-rate over ≥4 exercised sessions (`lifecycle.py:22-23, 95-102`). Blog claim 2 verified (external pack §1).
- **Our side (evidence OF absence, internal pack §A + Confirmed Gaps #1):** POK admits on the first successful write (`pok_service.py:477-493`); `quorum` greps to 0 hits daemon-wide; the `verify_count` / `record_verification` primitives exist with **zero callers** (`repository.py:253-266`); the exact-dup echo refreshes `last_verified` on a re-statement, not an N-session bar. Skills: a single-session `CAPTURED` verdict can mint a skill. Memoir: free write (`daemon/tools/memoir.py:881`). Snapshots: R9 reuse = tag overlap, not repetition confirmation (`snapshot_tools.py:345-361`).
- **Adjudication:** gap CONFIRMED at the strongest evidential grade the internal pack uses (searched-and-empty, not merely undocumented). Drives the R2 admission half.

## D2 — Gap 2 CONFIRMED, WITH A CORRECTION to the prior summary: contradiction-driven retirement

- **Prior summary said:** "ours has 30-day decay only." **The packs say otherwise, and the packs win.**
- **Correction:** the ensemble has **exactly one working contradiction-driven retirement implementation — memoir BELIEFS**: the reflection prompt's verb vocabulary includes `CORRECT (a belief that was wrong)` (`reflection_service.py:955-990`) and `syntheses_repository.supersede_with` (`:208-300`) atomically flips the old row with re-supersede protection and active-read filtering (internal pack §C 'Retirement — split verdict' + Confirmed Gaps #2). The prior summary's phrasing is accurate **only at the POK layer** (POK decay: `last_verified` + 30-day window → `[stale Nd]` decoration and rank-down, never retirement — `pok_service.py:123`, `pok_injection.py:243-249`, "AC5: decay still ranks down, never drops").
- **Their side:** support score with γ=0.8 decay and λ=5 contradiction weight, retire below 0, high-stakes first-contradiction hard kill (`lifecycle.py:18-21, 116-119`); detectors are regex/predicate over observed command+output blobs, never model-judged text comparison (`distiller.py:323-332`).
- **Where else it's absent here:** POK `disputed` is manual-only (sole writers: insert literals + `pok_curate`; `auto_dispute|auto_retire|auto_archive` → 0 hits; no POK lane in the maintenance registry `daemon/manager.py:4571-4790`); critical notes have no disputed concept (`daemon/repositories/project/models.py:168-235`); skill deactivation is fix/stale/A-B driven; memoir EPISODES' `mark_superseded` is dead code (`repository.py:1199-1213`).
- **Adjudication:** gap CONFIRMED with the nuance recorded — the pattern to copy already exists in-repo (belief supersession), which materially de-risks R2's retirement half. Also noted: the write-path detector candidate would close the same-slug dispute-evasion hole (`pok_service.py:436-449`) but re-opens the fuzzy-match class banned after the 2026-09-10 hijack lesson (`:151-156`) — R2 therefore constrains detectors to exact-shape or curator-arbitrated.

## D3 — Gap 3 CONFIRMED: learned per-project tool narrowing

- **Their side:** evidence-counting prune rule (≥10 prompts across ≥2 sessions, or ≥5 sessions; `plugin/fold.py:143-152`, `plugin/config.py:187-189`), 20 protected core tools (`toolprune/tools.py:41-48`), 60-min idle-boundary writes, one-command restore of exactly the plugin's own additions (`cli.py:540-552`), and a tool used during re-audit is never masked again. The LLM tool-judge is real code but dormant in the plugin ("never calls a model to prune", `toolprune/README.md:11-13`).
- **Our side (internal pack §D + Confirmed Gaps #3):** `_apply_tool_filter` keys on agent only (`daemon/tools/instance.py:7715`); `create_instance_tools` receives no `project_id` (`:3245`); no project-keyed tool columns exist anywhere; the only narrowing mechanisms are static config, a static read-pattern MCP strip (`mcp_service.py:955-990`), and the hand-built PM keep-set — 28 literal verbs, 60,635 → 29,313 prompt tokens (223 → 74 tools; plane-absent baseline 20,468; `agents/project-manager/meta.json:50-77`, fc9_baselines.json, gate-i-keep-set.json sha256-pinned by census test).
- **Adjudication:** gap CONFIRMED. The PM keep-set is simultaneously the proof of value (the token delta) and the proof of absence (it had to be built by hand). R3's intersection-only composition seam is the internal pack's own ranking (`instance.py:7715` + `_get_instance_project_id` at `:592`).

## D4 — Gap 4 CONFIRMED, WITH A CORRECTION to the prior summary: stuck-vs-progress judge + redirect

- **Prior summary said:** "ours detects stalls/wedges but never redirects with content." **Packs win, with a precision correction:**
- **Correction:** we DO redirect with content today — the loop-breaker repair text is LLM-authored inside a fixed template, explicitly asking "(1) What the agent was trying to accomplish (2) Why it appears to be stuck in a loop (3) What alternative approach it should try" (`daemon/graph.py:205-219`; summarizer `:1996-2091`; message builder `:2131-2169` — "Please try a DIFFERENT approach…"), delivered as a history rewrite at the loop-break point, not a live-turn injection, and the durable `SymptomRepairEngine` performs whole-channel surgery. **What is actually absent** is the *gating predicate*: every trigger is a syntactic counter (identical tool-name+args signatures ×3, `graph.py:1403-1535`) or a host metric (wedge triggers, heartbeat staleness) — **no stuck-vs-progress semantic judgment exists anywhere in the repo** (internal pack §E "KEY ANSWER": "No seam injects a model-authored, progress-aware redirect into a running turn"; `set_injection` has exactly four writer sites, all external actors, `daemon/routers/messages.py:572`, `daemon/tools/instance.py:4843`/`:4950`, `daemon/tools/job_queue.py:3561`, `daemon/sources/registry.py:1256`). The one live-turn LLM judge (watchover) explicitly refuses progress judgment (`agents/watcher/soul.md:20`).
- **Their side:** leakage-safe digest → judge `{"stuck","confidence","reason"}` with bias-to-sparing (`loopguard/judge.py:15-27`) → 2–4-sentence redirect with "Do NOT tell it to stop" constraints (`judge.py:66-80`), judge async off the critical path (`plugin/judge.py:1-16`), and — decisive for us — the shipped plugin never terminates a turn (`plugin/config.py:21-27`), termination being harness-only.
- **Adjudication:** gap CONFIRMED as "no semantic progress predicate gates any redirect"; the redirect *payload* mechanism partially exists. This is why R4 is scoped as predicate + judge producer + trigger point, reusing existing delivery, and why its value is honestly MEDIUM-HIGH (their own ablation: loop guard single-digit savings) rather than HIGH.

## D5 — Gap 5 CONFIRMED: durable usage-measurement substrate

- **Their side:** cache-aware effective_tokens metric with per-billed-model weights (`pricing.py:1-42, 246-266`); per-mechanism overhead ledger (`ledger.py:19-26`); every auxiliary call self-charged to the triggering session; unpriced model ids hard-fail in experiments (`pricing.py:191-217`).
- **Our side (internal pack §G + Confirmed Gaps #5):** main-lane `usage_metadata` is populated (`stream_usage=True` at the graph chokepoint) but **never persisted or aggregated** — only in-graph readers (`graph.py:3864/3875/3915`); no per-task/agent/project token table exists (all repository models + 130 migrations searched); OD plugin usage captured only in the streamed result object (`generate.py:983-985, 1503`); the decision lane meters only its own consults (`overlay.py:840`). Tool calls (name+args) survive only inside LangGraph checkpoints — TTL 168h, per-thread, no `project_id`, unnormalized args. Proxy-side metering exists but lives in llm-supervisor-proxy, outside this repo's data model.
- **Adjudication:** gap CONFIRMED. Consequence drawn: their motivating profiling study (30% same-repo repeated tool calls, blog claim 5) is **not runnable on our data today** — the internal pack names the five missing prerequisites (§G). This grounds D6's ordering.

## D6 — Sequencing adjudication: measurement → governance → data-dependent

Re-derived, not inherited:

1. **R1 first** because it is (a) the only commission that is a strict prerequisite for others — R3's learning rule literally counts sessions/prompts of usage history (their thresholds: 10/2 or 5; external pack §3.2 prune-rule row), and R4 needs per-repo turn envelopes (their envelope = p90 of ended-green turns, `envelope.py:28-45`) plus efficacy measurement, none of which we persist (see Gaps #4); (b) the lowest-risk (additive, no behavior change, no authority questions); (c) the honest-science move — their headline stats describe THEIR corpus; any expectation that we'd see similar waste rates is currently unmeasurable inference (flagged in Gaps).
2. **R2 second** because it is data-independent (closes the lifecycle-integrity gaps with machinery that already exists unused: `verify_count`, belief-supersession pattern) yet *benefits* from waiting for R1 only to calibrate the N-confirmation bar — governance quality, not governance possibility, depends on measurement. Propose-only posture keeps it safe to start early if the Emperor prefers.
3. **R3 and R4 after R1** — both are evidence-gated: R3 by construction (cannot learn from history that isn't recorded), R4 by evaluation discipline (a redirect mechanism whose effect cannot be measured should not leave shadow mode; the Decision Module's SHADOW→DECIDING transition is Emperor-ratified and stays so).
4. **Doctrine gates apply throughout:** no new mandatory LLM calls in the request path (their judge is async even in their design); optional-accelerator principle (every mechanism arrives OFF by default — theirs do too: micro-skills off, judge dormant); existing kill channels and curator authorities unchanged.

## D7 — Non-adoption boundary

The line, each element traced:

- **No mandatory LLM calls in the request path** — standing doctrine; their own architecture keeps the judge async ("the agent never waits", external pack §6.3; `plugin/judge.py:1-16`). A synchronous judge would also be the only mechanism here that could *increase* per-turn cost and latency unconditionally.
- **No controller-initiated termination of agent work** — the paper's termination is harness-only; the shipped plugin explicitly refuses ("the person decides", `plugin/config.py:21-27`; `plugin/controller.py:396-412`). Our termination paths stay where governance already put them (watchdog escalation under human authority).
- **No parallel micro-skills store** — POK already covers the niche with strictly more governance (tiers, pins, caps, audience, lineage; internal pack §A); forking the knowledge base would be net-negative (report §4.3).
- **No automatic rewriting of governed tool config** — their mask writes the agent's own settings files; our tool surfaces and prompts are versioned, human-approvable config (prompt-artifact contract). R3's projection is read-side, intersection-only, reversible — the deliberate contrast.
- **No second billing engine** — proxy remains meter authority; R1 records events, not prices; their frozen MODEL_WEIGHTS snapshot (with vendor-drift comments, external pack §7.4) illustrates exactly the liability avoided.
- **Non-applicable:** their repo-identity scheme (we have first-class project identity).

---

## Gaps in the evidence — insufficient, contested, or inferred

1. **UNMEASURED (do not assume parity):** all waste-rate numbers (30% repeated tool calls; 17% tasks = 32% spend; 23 tools/16 never called) are measurements of THEIR corpus (SWE-Bench, 6 repos, 5 CLI/model settings; external pack §1 claims 4–6). Our equivalent rates are unknown and unknowable until R1 exists. The report uses their numbers only as motivation, never as predictions about us.
2. **TREE LAG (internal anchors):** the internal pack studied `bf4bfde6e`, which lags `origin/latest` (`2618eb212`). Material divergences: POK active cap 256 (in-tree) vs 512 (latest); the POK durable-vs-rolling guideline ships only with v0.31.1 and its "no stale marker" phrasing is imprecise anyway; latest adds a curator slug→row resolver. No conclusion in D1–D7 is affected; future re-verification must pin the tree.
3. **CONTESTED→RESOLVED (two corrections to the prior summary):** D2 (retirement: "30-day decay only" is wrong at codebase level — memoir belief supersession exists) and D4 (redirect: "never redirects with content" is wrong — the loop-repair payload is content-bearing; the *predicate* is what's absent). Both resolved in the packs' favor and flagged in situ.
4. **INFERRED (adjudication, not pack fact):** R4's data-dependence. The packs prove no envelope/turn-statistics store exists (D5) but do not literally state "R4 is blocked on R1"; the dependency is this writer's inference from measured absence plus their design's reliance on learned envelopes. R2's "informs evidence bar" edge is likewise an inference (their K=3 is a tuning outcome, not a law).
5. **PARTIAL PROVENANCE (disclosed by the packs):** internal section C (memoir/snapshots) came from a re-dispatched worker after the first attempt wedged in a daemon restart with zero output (provenance note, internal pack; anomaly `ea0a1db2`); the external sidecar economics differ plugin-vs-paper (plugin judges default to `haiku` vs the experiments' same-model billing — "plugin-mode overhead fractions are not comparable to paper numbers", external pack §5 item 10), so their single-digit loop-guard ablation is an upper-bound-flavored hint for R4 sizing, not a transferable forecast.
6. **EXTERNAL PRICE SNAPSHOT STALENESS:** their `MODEL_WEIGHTS` is a frozen snapshot (2026-07-27 + 2026-11-13 additions, drift comments noting gemini-4-* rises 2026-12-01) — any future effective-token math on our side must source live weights from the proxy, not their table (report §4.5).
7. **NOT INDEPENDENTLY RE-VERIFIED:** this writer could not re-check clone anchors directly (workspace boundary + bash allowlist; see citation rule) — external anchors rest on the external pack's verification chain (three workers + controller, pinned SHA). The commission's spot-check instruction is discharged by disclosure rather than execution; if any single anchor is load-bearing for a future decision, re-verify it at the clone before relying on it.

---

*Adjudication by doc-writer (recovery mission), 2026-11-18. Companion artifact: `report.md`. No git operations, no code changes, no live contact; the planning directory was the only write surface, and exactly two files were written (report.md, decisions.md).*

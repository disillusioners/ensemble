# Designer Agent Implementation — Phase 2: Parallel Builds (Comparator + Capture Gate)

- **Date:** 2026-09-26 · **Status:** PLANNING ONLY — docs-only effort; this file plans Phase 2 work packages, it implements nothing.
- **Author:** planner via plan-creation worker · **Phase owner scope:** Phase 2 only (see §1.1 phase map).
- **Source of truth (read in full, RATIFIED):** `.agents/shared/planning/designer-agent/architecture-recommendation.md` @ worktree `feature/designer-agent-design` @ `e67e5cd8`. D1–D6 are LOCKED — this plan implements them and never re-opens them.
- **Method:** arch-doc §§2/5/6/7/8/10 + evidence index as raw material; touchpoints spot-verified read-only against the tree (§4 ground truth); house plan conventions per `auto-restart-upgrade/{plan-overview,decisions}.md`.
- **Verification contract:** every §8 risk carried cites severity + mitigation; every WP has provable acceptance criteria; no narrative padding.

---

## 1. Objective

Deliver the two ratified parallel builds of the designer-agent integration with **zero code dependency between them** (arch §2 D3/D4, §6):

- **Track A (D4, build day-1):** the image comparator — specialist agent `image-comparator` behind a blocking `compare_images(image_a, image_b, criteria?)` tool facade, paths-in via the path→data-URI bridge, structured findings out (never an image).
- **Track B (D3/GAP-1, gated):** the capture capability — early self-hosted OpenDesign install (manual/ops lane), inspection of OD's `agent-browser` skill surface, and an **adopt-or-build VERDICT artifact** that decides the capture branch before any capture code is written.

**Testable phase-complete sentence:** a real-screenshot pair produces a structured comparator findings artifact on the substrate, AND the capture adopt-or-build verdict artifact exists with evidence from the installed OD instance.

### 1.1 Phase Map (fixed by dispatcher — this file covers P2 only)

| Phase | File | Status |
|-------|------|--------|
| P1 Foundations | `phase1-foundations.md` | separate worker |
| **P2 Parallel builds** | **`phase2-parallel-builds.md` (THIS FILE)** | **planned here** |
| P3 Bootstrap + KMS-Lite | `phase3-bootstrap-kms-lite.md` | separate worker |
| Overview + decisions | `plan-overview.md` + `decisions.md` | separate later worker |

---

## 2. Scope

### In Scope

| # | Item | Why |
|---|------|-----|
| S1 | `agents/image-comparator/` anatomy (meta.json + "judge against criteria" soul.md, criteria pinned) | D4: dedicated evolvable judge soul beats repurposing image-reader's generic "describe" soul (arch §6 five-axis record) |
| S2 | `"design": ["image-comparator"]` registry entry + `create_compare_tools()` factory + blocking `invoke_agent_and_wait` (600 s, never-raise) + reuse-by-discovery | D4 charter-pattern placement (arch §3.1 Template B mechanics, §6) |
| S3 | Path→data-URI bridge **implementation** inside the comparator facade + structured findings I/O contract | P1 designed the bridge (§5.1 store facts); P2 implements/consumes it in the facade (arch §6) |
| S4 | Early self-hosted OpenDesign install (manual/ops lane) + install-evidence notes | D3 GAP-4 render lane rides OD-as-MCP (§5.2); early install is the precursor P3's bootstrap installer skill generalizes (**dependency inversion — §5 WP4**) |
| S5 | Adopt-or-build GATE: agent-browser surface inspection + verdict artifact with defined criteria | D3: "verify OD `agent-browser` skill adoption first — may close the gap for free" (§5.3 GAP-1, §8 🟡) |
| S6 | Conditional branches: verdict=adopt → substrate integration WP; verdict=build → capture-tool build WP | Both branches pre-planned so the gate does not block |
| S7 | Five-axis A→C consolidation triggers recorded as **monitoring triggers** (not day-1 work) | arch §6 + §10 OQ-2 |
| S8 | D6 phase-2 slice: findings schema references `pinned_spec_sha` when judging against an approved spec; verdict/gate artifacts carry spec/decision SHAs | D6 hard rule (§4.4) |

### Out of Scope

| # | Item | Reason |
|---|------|--------|
| X1 | KMS-Lite, mint-only key service, `__KMS_REF__` seam, capabilities.yaml, installer skill | P3 (D5). Early OD install runs manual/ops lane with operator-managed credentials; P3's installer skill + KMS mint generalize it. |
| X2 | Designer agent anatomy, `vision` allowlist entry, substrate upgrades (provenance sidecar, image_save/list/get, protected retention), bridge DESIGN | P1 (D1/D2/D3). P2 ASSUMES them live (§3.1) — WP1/WP3 carry verify-before-work gates. |
| X3 | `send_message` `images` param (GAP-3) | Open §10 item; substrate paths + pixels-at-dispatch are the operative mechanism; not needed for P2 deliverables. |
| X4 | Pixel-diff tooling | GAP-5 green — vision-model semantic judgment via comparator suffices (arch §5.3). |
| X5 | A→C comparator consolidation (semaphore work, image-reader `comparison_mode`, native proxy multi-image) | Monitoring triggers only (arch §6); execution deferred until a trigger fires. |
| X6 | Bootstrap-flow OD installer skill | P3; P2's early manual install deliberately precedes it (dependency inversion, logged §5 WP4). |

---

## 3. Inter-Phase Contracts

### 3.1 P2 MAY ASSUME from P1 (verify-before-work gates, not re-derivation)

| # | P1 deliverable | P2 consumer | Failure mode if missing |
|---|----------------|-------------|--------------------------|
| A1 | `vision` added to **daemon-global** `allowed_models` (`config.yaml:82` / `OPENAI_SELECTABLE_MODELS`) + daemon restart done | Comparator agent + facade model resolution (D2) | 🔴 arch §8: overrides **silently resolve to default** — no error, vision quietly lost |
| A2 | `model_vision` configured (daemon-global; fail-fast 400 if unset, §8 🟢 row) | Facade's single vision call | 400 at compare time |
| A3 | `caller_model_overrides` generalized into the spawn chain, precedence `model_tier > model= > parent-map > pool > llm_model > default` (arch §3.4) | any-lead override machinery; comparator agent rides it, does not extend it | — (P2 reads, never modifies) |
| A4 | `agents/designer/` live (craft-class sub-team lead) | Designer is the primary facade consumer + Track B beneficiary | Comparator still works for any caller; designer flows lag |
| A5 | tmp_images substrate upgraded: provenance sidecar `{feature, page, version, source_agent}`, `image_save`/`image_list`/`image_get`, protected retention class | All comparator inputs/outputs; capture-branch outputs land here | Captures/findings unaddressable by path |
| A6 | Path→data-URI bridge DESIGN done (data-dir/workdir caveat; extensionless magic-byte reads; 404-gated listing — arch §5.1) | WP3 implements it inside the facade | WP3 blocks |

### 3.2 Leaves-Behind Contract — what P3 MAY ASSUME from P2

| # | P2 leaves behind | P3 consumer |
|---|------------------|-------------|
| L1 | **Self-hosted OpenDesign installed** (early, manual/ops lane) with install-evidence notes (host shape, MCP registration values, credential handling used) | P3's bootstrap-flow **OD-installer skill generalizes this early manual install** — the manual install is the measured template, not the end state (dependency inversion: install precedes installer, deliberately) |
| L2 | **Adopt-or-build VERDICT artifact** at `.agents/shared/planning/designer-agent/implementation-plan/verdicts/capture-adopt-or-build.md`, with evidence from the installed instance | P3 consumes GAP-1's resolved state (adopted / built) in the bootstrap-flow narrative |
| L3 | Capture tool **if built**, already wired into the substrate (`image_save` + provenance tags) | P3 rides it as-is; zero capture work leaks into P3 |
| L4 | Comparator live: `image-comparator` agent + `compare_images` facade + findings schema (incl. `pinned_spec_sha` reference) + A→C monitoring triggers on record | Designer workflows (arch §4.3) and P3's conformance narrative consume the facade |
| L5 | `verdicts/` directory convention under `implementation-plan/` | P3 reuses for its own gate artifacts |

---

## 4. Ground Truth (spot-verified read-only @ `e67e5cd8` worktree, 2026-09-26)

| # | Arch-doc claim | Verified reality | Drift |
|---|----------------|------------------|-------|
| G1 | `TOOL_REQUIRED_AGENTS` at `_auth.py:35-40` | EXACT — dict present; `"chart": ["charter"]`, `"image": ["image-reader"]` precedents in place; comment mandates: category name MUST match the string passed to `@register_tool_category(...)` | none |
| G2 | `create_chart_tools` factory at `chart_tools.py:374` | EXACT — `create_chart_tools(manager, current_instance_id)` signature confirmed | none |
| G3 | Reuse-by-discovery `invoked_as_tool` (`chart_tools.py:67-145`) | Mechanism confirmed: `_find_reusable_charter` walks caller children filtered on flag; flag stamped by `invoke_agent_and_wait` spawn | MINOR: stamp site now at `instance_lifecycle.py:1963-1964` (docstring cites `:1798-1799`) — implementer reads the current stamp site, not the docstring |
| G4 | Blocking 600 s never-raise invoke | CONFIRMED — `timeout: float = 600.0` (`chart_tools.py:153`, `:484`, `:512`); never-raise contract comment at `:500` | none |
| G5 | Invoke semaphore = `max(1, WORKER_POOL_SIZE-1)` = 4 (`utils.py:591-603`) | CONFIRMED — lazy singleton `_get_invoke_semaphore()` at `daemon/utils.py:588-605`; all blocking tool-facade calls share the 4 slots | line-range ±2 |
| G6 | Multi-image per message tested (`instance_messaging.py:113-128`; `test_vision_routing.py::TestMultipleImages`) | Mechanism CONFIRMED — `_build_message_content` appends one `image_url` block per image (`instance_messaging.py:113-128`) | MINOR: test is `test_multiple_images_in_one_message` at `tests/unit/test_vision_routing.py:275` (edge-cases class, `:253`) — class name differs from arch citation; test exists and passes on the mechanism |
| G7 | LLM concurrency 10 daemon-wide (`config.py:549`) | EXACT — `llm_concurrency: int = Field(default=10, ge=1)` | none |
| G8 | tmp_images store substrate (`daemon/services/tmp_image_store.py`) | File present (18 KB); upgrade work is P1's, per §3.1 A5/A6 | none |
| G9 | One-shot boot discovery (`registry.py:536-571`) | Not re-derived — arch-verified; drives WP1 ordering constraint (dir lands before restart) | — |

---

## 5. Work Packages

### 5.0 WP Summary

| WP | Track | Title | Depends on | Parallel? | Status |
|----|-------|-------|------------|-----------|--------|
| P2-WP1 | A | Hand-author `agents/image-comparator/` anatomy | P1 (A1, A2) | yes (Track A start) | pending |
| P2-WP2 | A | `compare_images` tool facade + registry + reuse-by-discovery + monitoring triggers | P2-WP1 | — | pending |
| P2-WP3 | A | Path→data-URI bridge implementation in facade + structured findings contract | P2-WP2, P1 (A5, A6) | — | pending |
| P2-WP4 | B | Early self-hosted OpenDesign install (manual/ops lane) | ops lane only | yes (Track B start) | pending |
| P2-WP5 | B | **GATE:** agent-browser surface inspection → adopt-or-build verdict artifact | P2-WP4 | — | pending |
| P2-WP6 | B | Conditional ADOPT: wire agent-browser captures into substrate | P2-WP5 (verdict=adopt), P1 (A5) | — | pending |
| P2-WP7 | B | Conditional BUILD: capture tool (tester-Playwright precedent) | P2-WP5 (verdict=build), P1 (A5) | — | pending |
| P2-WP8 | ✚ | E2E rollout verification: real-screenshot compare + gate evidence | P2-WP3, P2-WP4 (+WP6/WP7 when available) | convergence | pending |

**Parallelism invariant (D4):** Track A (WP1→WP2→WP3) and Track B (WP4→WP5→WP6|WP7) share **zero code dependency**; they converge only at WP8. No WP in one track may block on a WP in the other.

---

### P2-WP1 — Hand-author `agents/image-comparator/` anatomy

| Field | Content |
|-------|---------|
| **Objective** | Create the specialist agent the facade drives: a dedicated "judge against criteria" vision agent whose soul pins the comparison criteria set, countering vision-model judgment variability. |
| **Touchpoints** | arch §6 (specialist-agent placement, dedicated judge soul); arch §3.2 anatomy layout (`agents/designer/` 5-file shape as template); arch §8 🔴 permissive-default trap (hand-author mandate); arch §8 🟡 vision-judgment variability (criteria pinning); `_auth.py:35-40` (registry shape the agent id must satisfy); `registry.py:536-571` (one-shot boot discovery — dir lands before restart). |
| **Dependencies** | P1: A1 (`vision` in global allowed_models + restart), A2 (`model_vision` configured). No P2 deps — Track A start. |
| **Deliverables** | `agents/image-comparator/meta.json` + `soul.md` (minimum viable anatomy; rule.md/workflow.md only if facade plumbing demands them). |
| **Risks carried** | 🔴 permissive-default (`POST /agents` carries no tools/deny fields) → hand-author files, never API-create (AC-1). 🟡 vision-judgment variability → criteria PINNED in soul.md; pass/fail phrased against criteria, not model taste (AC-2). 🟡 restart coupling → file lands in a restart window (AC-4). |
| **Design notes** | meta.json: `llm_model: "vision"`; `skill_injection` per designer precedent if evolution of judging rules is wanted (implementer decision, log it); tool surface mirrors **charter's minimal precedent** (tool-facade child needs less than a team lead) — implementer reads `agents/charter/meta.json` at execution time and mirrors, adding image-category access only if facade plumbing requires it. Soul.md structure: identity ("I judge images against pinned criteria; I produce findings, never images"); the pinned criteria set (structural layout, content parity, token/color conformance, spacing/alignment, states/a11y-visible affordances); severity definitions (`critical|major|minor|nit`); verdict semantics (`pass|fail|conditional_pass`); anti-drift rule (cite evidence lines for every criterion verdict). |

**Acceptance criteria (provable):**

- [ ] AC-1: `agents/image-comparator/meta.json` + `soul.md` exist in the tree, hand-authored (no `POST /agents` in the delivery path); meta.json parses and satisfies registry load (`registry.py:536-571` shape).
- [ ] AC-2: soul.md contains the pinned criteria set + explicit severity taxonomy + "cite evidence lines" mandate; criteria text is stable across model versions (no model-specific phrasing).
- [ ] AC-3: meta.json model resolves to `vision` (present in `allowed_models` post-restart — gate on P1 A1/A2 verified before this WP starts; a pre-WP check is recorded in the WP report).
- [ ] AC-4: agent dir present before the restart that activates it (boot-discovery); restart note logged for the shared restart window (arch §8 🟡 coupling row).
- [ ] AC-5: `image-comparator` id is the value referenced by the WP2 registry entry (single source).

---

### P2-WP2 — `compare_images` tool facade + registry entry + reuse-by-discovery + monitoring triggers

| Field | Content |
|-------|---------|
| **Objective** | Expose `compare_images(image_a, image_b, criteria?)` as a tier-B agent-backed tool category using the charter pattern end-to-end: registry entry, factory, blocking wait, reuse-by-discovery. |
| **Touchpoints** | `_auth.py:35-40` (`TOOL_REQUIRED_AGENTS` — add entry; comment rule: category name MUST match `@register_tool_category(...)` string, verified G1); `chart_tools.py:374` (`create_chart_tools` factory model, verified G2); `chart_tools.py:67-145` + `instance_lifecycle.py:1963-1964` (reuse-by-discovery via `invoked_as_tool` stamp, verified G3 — **read the current stamp site, not the chart_tools docstring**); `chart_tools.py:153,484,512,500` (600 s timeout + never-raise contract, verified G4); arch §6 (I/O contract — this WP delivers the call shape; WP3 delivers the image plumbing); arch §6 five-axis record + §10 OQ-2 (monitoring triggers). |
| **Dependencies** | P2-WP1 (agent id exists). Cross-phase: P1 A1/A2 (model resolution at invoke time). |
| **Deliverables** | 1) `"design": ["image-comparator"]` entry in `TOOL_REQUIRED_AGENTS` (category key per arch spec; MUST match its `@register_tool_category` string — implementer may rename the key at execution time provided the MUST-match rule holds; decision logged). 2) `create_compare_tools()` factory (modeled on `create_chart_tools`, manager + current_instance_id injection, parent = caller). 3) `compare_images(image_a, image_b, criteria?)` tool: blocking `invoke_agent_and_wait`, `timeout=600.0`, never-raise (errors → structured error envelope, mirroring `chart_tools.py:500` contract). 4) Reuse-by-discovery: second call in a refinement turn finds the caller's most recent `invoked_as_tool`-stamped `image-comparator` child and reuses it. 5) **Monitoring triggers section** (see below). |
| **Risks carried** | 🟡 semaphore/concurrency drain (4 invoke slots shared with charter/explorer/image-reader — `utils.py:588-605` verified G5; LLM concurrency 10 daemon-wide — `config.py:549` verified G7) → mitigations: (a) monitoring triggers below give the escape valve, (b) facade documents expected latency (one 600 s-capped blocking call per compare), (c) no fan-out compare loops in day-1 workflows. 🟡 boot/restart coupling → registry entry lands with WP1's restart window. |
| **Monitoring triggers (NOT day-1 work — recorded per arch §6 + §10 OQ-2)** | T1: **semaphore saturation >10 compares/day observed** (invoke-slot queueing on the shared 4-slot semaphore) → open A→C consolidation commission (fold comparator into a lighter shape per five-axis record A=4.00/C=3.60/B=2.20). T2: **image-reader gains `comparison_mode`** (generic-describe soul grows a judging mode) → re-run the five-axis record; consolidate if C closes the gap. T3: **native proxy multi-image support arrives** (removes the bridge's raison d'être in the facade) → re-evaluate facade thickness. Where recorded: this WP's deliverable + a `shared_meta_kv` monitor key (`design.comparator.monitor`) so a future agent can read the triggers without this file. |

**Acceptance criteria (provable):**

- [ ] AC-1: `_auth.py` carries the new entry; category key string == the `@register_tool_category` string in the compare tool module (grep-provable).
- [ ] AC-2: `compare_images(image_a, image_b, criteria=None)` invocable from any agent whose `tools.allow` carries the category; caller is the spawn parent.
- [ ] AC-3: compare is blocking ≤600 s and **never raises** — failure paths return a structured error envelope (timeout / missing agent / vision-400 each have a distinct envelope `kind`).
- [ ] AC-4: second `compare_images` call from the same caller with a live prior child reuses it (discovery via `invoked_as_tool` stamp), matching charter's reuse behavior.
- [ ] AC-5: monitoring triggers T1–T3 recorded in the tool module docstring AND in `shared_meta_kv` (`design.comparator.monitor`), each with its firing condition and the commission it opens.
- [ ] AC-6: unit test pins the never-raise contract + reuse path (tester-run; test names logged in WP report).

---

### P2-WP3 — Path→data-URI bridge implementation in the facade + structured findings contract

| Field | Content |
|-------|---------|
| **Objective** | Implement the P1-designed bridge inside the comparator facade and pin the output contract: paths in, structured findings out — never an image. |
| **Touchpoints** | arch §6 I/O contract (paths-in; facade runs the bridge; `images=[a, b]` in ONE vision call); arch §5.1 store facts (data-dir/workdir deployment caveat; extensionless-tolerant magic-byte reads; 404-gated listing — P1 designed against these, WP3 implements); `instance_messaging.py:113-128` multi-image content build (verified G6) + `tests/unit/test_vision_routing.py:275` `test_multiple_images_in_one_message` (mechanism tested); `image_tools.py:429-444` (workdir confinement — the reason the bridge exists); arch §4.2 (findings as first-class citable artifact via report pipeline); D6/arch §4.4 (pinned_spec_sha). |
| **Dependencies** | P2-WP2 (facade exists). P1: A5 (substrate tools + provenance live), A6 (bridge DESIGN done). |
| **Deliverables** | 1) Bridge impl: accept substrate/workdir paths for `image_a`/`image_b`; resolve via data-dir store read → data-URI (workdir-confined `explain_image` path is NOT used inside the facade — the facade holds the daemon-side read); missing path → structured error envelope, not a raise. 2) Single vision call with `images=[a, b]` (multi-image, one message — tested mechanism). 3) **Findings schema (the facade's return contract):** `{verdict: pass|fail|conditional_pass, per_criterion: [{criterion, result: pass|fail, severity: critical|major|minor|nit, evidence: [lines]}], summary: str, pinned_spec_sha: str|null}` — `pinned_spec_sha` set when the caller supplies criteria referencing an approved spec (D6 hard rule); absent/None otherwise (advisory in that case, never a hard failure outside spec conformance contexts). 4) Findings artifact delivery: verdict text rides the report pipeline as a first-class citable artifact (report-pipeline delivery, arch §6); caller may persist it under `planning/{feature}/design/` next to `design-review.md` conventions. |
| **Risks carried** | 🟡 data-dir/workdir deployment caveat (store lives in `data_dir`, agent-local reads are workdir-confined — arch §5.1) → the bridge is daemon-side precisely to avoid the confinement trap; AC-2 pins it. 🟡 clipboard channel delivers descriptions not pixels (`messages.py:269`; `tmp_image_converter.py:128`) → facade accepts PATHS ONLY (plus data-URIs callers already hold); AC-4. 🔴 silent-fallback precondition → model resolution gate re-checked at facade init (AC-5). |
| **Design notes** | Bridge follows the P1 design document (A6) — WP3 does not re-design; deviations from the P1 design are defects, reported, not silently absorbed. The `criteria?` param maps 1:1 to per_criterion rows; when absent, the agent's pinned soul criteria apply. |

**Acceptance criteria (provable):**

- [ ] AC-1: `compare_images("/substrate/path/a.png", "/substrate/path/b.png")` returns the full findings schema (verdict, ≥1 per_criterion row with severity + evidence, summary).
- [ ] AC-2: facade read path works for substrate (data-dir) paths AND workdir paths; a workdir-confined agent (no data-dir access) still succeeds via the facade (bridge burden is daemon-side, per `image_tools.py:429-444` contrast).
- [ ] AC-3: single vision call per compare — request log shows one message carrying both image blocks (mechanism: `instance_messaging.py:113-128`).
- [ ] AC-4: nonexistent path → structured error envelope (`kind` distinguishes not-found from vision-failure); no raise escapes the facade.
- [ ] AC-5: with `vision` missing from `allowed_models` (simulated), facade init fails LOUD (mirrors arch §8 🔴; no silent default resolution) — test-pinned.
- [ ] AC-6: findings artifact citing a spec carries a non-null `pinned_spec_sha` (D6); findings without spec context carry null + no hard failure.
- [ ] AC-7: output is never an image (schema type check — strings/enums/arrays only).

---

### P2-WP4 — Early self-hosted OpenDesign install (manual/ops lane) ⚠ dependency inversion

| Field | Content |
|-------|---------|
| **Objective** | Stand up self-hosted OpenDesign on the host so Track B has a live instance to inspect and (later) render against — **early and manual**, deliberately preceding P3's bootstrap-flow installer skill. |
| **Touchpoints** | arch §5.2 (OD-as-MCP render lane, zero new plumbing); arch §2 D5 (self-hosted, no third-party keys day 1 — the reason a manual install is acceptable); arch §7.1–7.3 (`mcp_servers` registration shape, `capabilities.yaml` — P3 generalizes); **arch §5.3 GAP-1 + §8 🟡 (agent-browser verification REQUIRES an installed instance — this WP unblocks WP5).** |
| **Dependencies** | Ops lane only (host access, operator). **No P2 WP deps; no P1 deps.** Zero code deps with Track A (parallel invariant). |
| **⚠ Dependency inversion (explicit, per dispatcher)** | D5's end state is "auto-installed via the bootstrap flow" (P3). P2 installs manually FIRST because the agent-browser verdict (WP5) gates the capture branch and P3's installer skill needs the early install as its measured template. Log the inversion in the install notes so P3 encodes it, not repeats it. |
| **Deliverables** | 1) OD instance running on the host (self-hosted). 2) `mcp_servers` registration for `opendesign` (builtin MCP, per §5.2 mechanism) with **operator-managed credentials** (P3's KMS-Lite mint + `__KMS_REF__` markers come later — if any credential is configured now, the install notes MUST record it for the P3 marker migration, cf. arch §8 🔴 MCP-env-RAW risk). 3) Install-evidence notes appended to the WP report: install method, host shape, OD version, `agent-browser` skill presence in the skill list (raw evidence for WP5), MCP registration values. |
| **Risks carried** | 🟡 boot-time registration coupling (arch §8: OD builtin registration lands at a restart window) → restart note logged with WP1's. 🔴 MCP config stores env RAW today (`redact_secrets()` presentation-only, arch §8) → prefer zero-credential day-1 (self-hosted, no keys day 1 per D5); if a credential is unavoidable, record it verbatim in install notes as P3 migration input; never store new raw rows casually. ⚠ Ambient-POSTGRES live-probe discipline: the install happens in the ops lane, never executed from the planning worktree, and any shell work near the daemon follows the standalone-bash-wrapper + zero-POSTGRES-survivors verify pattern (3 prior incidents). |
| **Constraint** | THIS PLAN DOES NOT EXECUTE THE INSTALL. WP4 is a planned execution-time work package for the ops lane. Nothing in the docs-only effort boots, installs, or probes. |

**Acceptance criteria (provable):**

- [ ] AC-1: OD instance reachable on the host; version + install method recorded in the WP report install notes.
- [ ] AC-2: `opendesign` MCP visible to the daemon (post-restart `mcp_servers` row / tool-list evidence captured in install notes).
- [ ] AC-3: `agent-browser` skill presence in the installed OD skill surface confirmed with raw evidence (skill-listing excerpt) — WP5's input.
- [ ] AC-4: credential posture recorded (ideally "none — no keys day 1"); any credential documented for the P3 marker migration.
- [ ] AC-5: dependency-inversion note present in install notes ("manual early install; P3 installer skill generalizes").
- [ ] AC-6: install performed from ops lane, zero execution from the planning worktree (constraint compliance statement in WP report).

---

### P2-WP5 — GATE: agent-browser surface inspection → adopt-or-build verdict artifact

| Field | Content |
|-------|---------|
| **Objective** | Answer, with evidence, whether the self-hosted OD's `agent-browser` skill **operationally delivers app screenshot capture** — and record the adopt-or-build decision in a durable verdict artifact. This gate decides the Track B branch (arch §5.3 GAP-1: "may close the gap with zero daemon code"; §8 🟡 capture-gated-on-verification). |
| **Touchpoints** | arch §5.3 GAP-1 (the open gap this gate closes); arch §10 OQ-1 (owned here — P2 produces the answer); wanderer pass 2 evidence (`agent-browser` is an OD-shipped skill — evidence index §11); arch §8 🟡 capture-tool-build-gated row (this WP is the gate); P2-WP4 install evidence (AC-3 skill-listing). |
| **Dependencies** | P2-WP4 (installed OD instance). |
| **Deliverables** | **Verdict artifact:** `.agents/shared/planning/designer-agent/implementation-plan/verdicts/capture-adopt-or-build.md` (path decision LOGGED HERE — this is the canonical gate artifact location; P3 consumes it per §3.2 L2). Artifact contents (schema below). |
| **Verdict criteria — what "delivers app screenshot capture" means operationally** | C1: **reachability** — an ensemble agent can invoke the skill on the installed instance (tool/agent path documented). C2: **target** — it can capture a URL/app page of OUR Angular frontend (not just OD-internal views). C3: **output addressability** — the screenshot lands somewhere the tmp_images substrate can ingest (file path out, or capture the operator relays into `image_save` — relaying is acceptable for ADOPT; silent in-OD-only persistence is NOT). C4: **fidelity** — captured image resolves the comparator's needs: full-page or viewport-controllable, resolution sufficient for per-criterion judgment (spot-check via one manual `explain_image`). C5: **friction** — steps from "agent asks for capture" to "path on substrate" are ≤ what a build WP would cost in maintenance (judgment line, recorded with reasoning). ADOPT requires C1–C4 pass; C5 is a recorded tiebreaker, not a blocker. |
| **Risks carried** | 🟡 capture-tool build gated on verification (arch §8) — this WP IS the mitigation (decide before build). 🟡 vision-judgment variability — C4's fidelity spot-check uses `explain_image` text-out (agent-first); evidence quoted in the artifact. |
| **Constraint** | Inspection is read-only against the installed instance (invoke, list, capture-once). No daemon code changes; no substrate writes beyond optionally relaying ONE probe capture to prove C3 (via existing `image_save`, P1). |

**Verdict artifact schema:**

```
# Capture Capability — Adopt-or-Build Verdict (P2-WP5)
date / decided_by / evidence_base (P2-WP4 install notes ref)
## Verdict: ADOPT | BUILD
## Criteria matrix: C1–C5 pass/fail + evidence excerpt each (commands run, outputs, paths)
## Branch directive: points at P2-WP6 (adopt) or P2-WP7 (build)
## D6 note: artifact carries spec/decision SHAs where applicable (arch base SHA; install-evidence SHAs if specs exist)
## Revisit trigger: condition under which ADOPT should be re-examined (e.g., OD version bump changes skill surface)
```

**Acceptance criteria (provable):**

- [ ] AC-1: verdict artifact exists at the canonical path with ALL schema sections populated (no empty criteria cells).
- [ ] AC-2: every criterion row cites captured evidence (command + output or path), not narrative assertion.
- [ ] AC-3: verdict is one of ADOPT|BUILD and names its branch WP explicitly.
- [ ] AC-4: fidelity spot-check (C4) includes one actual captured image relayed to the substrate with a provenance tag (`source_agent` = operator/inspecting agent) — or an explicit fail line if capture failed.
- [ ] AC-5: artifact records the revisit trigger and the arch base SHA it was decided against.
- [ ] AC-6: §10 OQ-1 is answered in the artifact (one-line answer up top).

---

### P2-WP6 — Conditional ADOPT branch: wire agent-browser captures into the substrate

*(executes only if P2-WP5 verdict = ADOPT)*

| Field | Content |
|-------|---------|
| **Objective** | Make OD `agent-browser` captures a routine substrate input: captures flow into tmp_images with provenance tags so the comparator and designer flows consume them by path. |
| **Touchpoints** | arch §5.1 (substrate + provenance sidecar `{feature, page, version, source_agent}`); arch §5.3 GAP-1 (adopted = zero daemon code expected); P2-WP5 artifact C3 path (adopt tolerates relay-mode ingestion); P1 `image_save`/`image_list`/`image_get` (A5). |
| **Dependencies** | P2-WP5 (verdict=adopt), P1 A5. |
| **Deliverables** | 1) Documented capture procedure (agent-operable): invoke agent-browser → obtain screenshot → `image_save` with full provenance sidecar → path returned to requester. 2) Procedure lives where agents find it (designer's `tools_note.md` image-substrate conventions section, per arch §3.2 anatomy; or a dynamic skill if the procedure earns one — implementer decision, logged). 3) Zero daemon code expected — if the procedure CANNOT close without daemon code, that is a verdict defect: STOP, record in the verdict artifact's revisit trigger, escalate to dispatcher. |
| **Risks carried** | 🟡 GAP-1 reopen (adopt path needs daemon code after all) → the STOP clause above; verdict artifact amended, WP7 becomes the fallback (one-time, dispatcher-approved). |

**Acceptance criteria (provable):**

- [ ] AC-1: an agent (designer or worker) executes the documented procedure end-to-end: request → capture → `image_save` → substrate path with provenance `{feature, page, version, source_agent}` populated.
- [ ] AC-2: zero daemon code delta in the delivery (git-visible: no daemon/ changes attributed to this WP).
- [ ] AC-3: procedure doc is discoverable (referenced from designer tools_note.md or registered skill; grep-provable reference).
- [ ] AC-4: verdict artifact updated with "ADOPTED — integration landed" line + date.

---

### P2-WP7 — Conditional BUILD branch: capture tool (tester-Playwright precedent)

*(executes only if P2-WP5 verdict = BUILD)*

| Field | Content |
|-------|---------|
| **Objective** | Build the capture capability per D3's build branch: a tool that screenshots our Angular pages and lands outputs on the substrate with provenance. |
| **Touchpoints** | arch §2 D3 (build branch of the two genuine builds); arch §5.3 GAP-1 (build = capture tool, verification first — WP5 satisfied); tester-Playwright capture precedent (arch §11 evidence: wanderer pass 2 — "workdir-as-image-shelf, tester-Playwright capture precedent"; the existing Playwright sweep machinery in the test lane is the proven capture substrate); arch §5.1 (provenance sidecar; `image_save`); `image_tools.py:429-444` (workdir confinement — capture tool runs daemon/ops side or lands files where `image_save` can reach). |
| **Dependencies** | P2-WP5 (verdict=build), P1 A5. |
| **Deliverables** | 1) Capture tool scoped to the proven need: given (page URL/path, viewport or full-page flag) → screenshot → `image_save` with provenance `{feature, page, version, source_agent="capture-tool"}` → return substrate path. 2) Implementation follows the tester-Playwright precedent's shape (evidence cited from arch §11; implementer reads the sweep harness at execution time — the precedent is cited, not re-derived here). 3) Registered for designer (+ coder/tester where their workflows need captures, per arch §4.3 flow handoffs: coder→designer captures, tester→designer capture paths). 4) Wire-in note for §3.2 L3 (P3 rides it as-is). |
| **Risks carried** | 🟡 build-branch cost (D3's reason for gating: adopt may close GAP-1 for free) → mitigated by WP5 ordering; build executes only on explicit BUILD verdict. 🔴(adjacent) playwright/browser deps are implementation-environment concerns → install/verify steps belong to the build WP's own acceptance run, executed in the dev lane, never from the planning worktree. |

**Acceptance criteria (provable):**

- [ ] AC-1: capture of a real routed frontend page (e.g., `frontend/src/app/pages/settings`) returns a substrate path with full provenance sidecar.
- [ ] AC-2: captured image passes an `explain_image` sanity read (content matches the target page) — evidence excerpt in WP report.
- [ ] AC-3: comparator consumes the capture end-to-end (compare vs a mockup/OD-render; findings artifact produced).
- [ ] AC-4: tool reachable by designer (allow-list entry or category membership, grep-provable).
- [ ] AC-5: verdict artifact updated with "BUILD — tool landed" line + date; §3.2 L3 satisfied.

---

### P2-WP8 — E2E rollout verification (phase proof)

| Field | Content |
|-------|---------|
| **Objective** | Prove the phase end-to-end on REAL screenshots — the dispatcher's rollout gate, executed once both tracks converge. |
| **Touchpoints** | dispatcher rollout spec (this WP's charter); arch §6 (I/O contract); D6 §4.4 (pinned_spec_sha); P2-WP3 findings schema; P2-WP5 verdict artifact. |
| **Dependencies** | P2-WP3 (comparator live), P2-WP4 (OD-rendered pairs available). WP6/WP7 strengthen but do not gate: user-supplied pairs are an acceptable real-screenshot source per dispatcher spec. |
| **Deliverables** | 1) **Comparator e2e:** one real-screenshot compare (user-supplied pair, or OD-rendered mockup-vs-capture pair from the substrate) → findings artifact with: per-criterion rows, severities, evidence lines, verdict, summary; `pinned_spec_sha` cited where a spec is in play (an approved design spec with a real SHA — a fixture spec may be approved in the e2e feature dir solely to prove the D6 wiring; noted as such in the artifact). 2) **Gate e2e:** WP5 verdict artifact complete with evidence (AC of WP5 re-confirmed as landed). 3) Phase report: both tracks' evidence indexed; monitoring triggers confirmed recorded (WP2 AC-5); restart-window note consolidated (WP1 AC-4 + WP4 restart). |
| **Risks carried** | 🟡 vision-judgment variability → the e2e findings must read as criteria-anchored (evidence lines cite image content, not vibes); a second model-variant run is OPTIONAL color, not required. 🟡 semaphore drain → the e2e runs compares serially (day-1 no fan-out). |

**Acceptance criteria (provable):**

- [ ] AC-1: findings artifact exists from a REAL screenshot pair (paths recorded; provenance tags present on substrate-resident images).
- [ ] AC-2: findings artifact carries non-null `pinned_spec_sha` for the spec-play run (D6 proven end-to-end).
- [ ] AC-3: verdict artifact (WP5) exists, populated, branch executed (WP6 or WP7 landed or explicitly pending-dispatcher).
- [ ] AC-4: phase report indexes: comparator e2e evidence, gate evidence, monitoring-trigger key, restart-window notes.
- [ ] AC-5: zero worktree-constraint violations during the whole phase (docs-only effort; execution happens in the implementation lane after this plan is dispatched).

---

## 6. Coupling Map (WP × WP)

| | WP1 | WP2 | WP3 | WP4 | WP5 | WP6 | WP7 | WP8 |
|---|---|---|---|---|---|---|---|---|
| **WP1** | — | tight (agent id + registry contract) | tight (soul criteria → findings rows) | independent | independent | loose (procedure references designer conventions) | loose | loose |
| **WP2** | tight | — | tight (facade hosts bridge + returns schema) | independent | independent | independent | loose (registration for capture tool) | tight (proves facade) |
| **WP3** | tight | tight | — | independent | independent | independent | tight (AC-3 consumes captures) | tight |
| **WP4** | independent | independent | independent | — | tight (inspect needs install) | loose | loose | tight (OD pairs) |
| **WP5** | independent | independent | independent | tight | — | tight (verdict gate) | tight (verdict gate) | tight |
| **WP6** | loose | independent | independent | loose | tight | — | mutually exclusive | loose |
| **WP7** | loose | loose | tight | loose | tight | mutually exclusive | — | loose |
| **WP8** | loose | tight | tight | tight | tight | loose | loose | — |

**Cross-phase coupling:** P1→WP1/WP3 (A1/A2/A5/A6 — gates, re-verified before work); WP4→P3 (L1 install template); WP5→P3 (L2 verdict artifact); WP7→P3 (L3 capture tool as-is).

---

## 7. Risks Carried from Arch §8 (phase-2 filter)

| # | Arch §8 risk | Severity | P2 exposure | Mitigation (WP-pinned) |
|---|--------------|----------|-------------|------------------------|
| R1 | `allowed_models` silent fallback (D2) — overrides resolve to default silently if `vision` missing | 🔴 | WP1, WP2, WP3 (model resolution) | Verify-before-work gate on P1 A1/A2 (WP1 AC-3); facade init fails LOUD (WP3 AC-5, test-pinned) |
| R2 | Invoke-semaphore/concurrency drain — 4 shared slots + LLM 10 | 🟡 | WP2 (blocking calls), WP8 (e2e runs) | Monitoring triggers T1–T3 with A→C escape valve (WP2 AC-5); serial compares day-1 (WP8); documented latency expectation |
| R3 | Capture-tool build gated on verification | 🟡 | Entire Track B | WP5 IS the gate — verdict before any build code (WP7 executes only on BUILD verdict) |
| R4 | Vision-model judgment variability | 🟡 | WP1, WP3, WP8 | Criteria pinned in soul.md (WP1 AC-2); evidence-line mandate in findings schema (WP3 AC-1); criteria-anchored e2e review (WP8) |
| R5 | Boot-time discovery + restart coupling (designer dir, vision entry, OD registration, one restart window) | 🟡 | WP1, WP4 | Restart-window notes consolidated in WP8 AC-4; image-comparator dir lands before restart (WP1 AC-4, `registry.py:536-571`) |
| R6 | `POST /agents` permissive-default trap (API-created agents carry no tools fields) | 🔴 | WP1 | Hand-author mandate (WP1 AC-1) |
| R7 | Clipboard channel delivers descriptions, not pixels (`messages.py:269`) | 🟡 | WP3 (inputs), WP8 (pair sourcing) | Facade accepts PATHS/data-URIs only (WP3 AC-4); real pairs sourced from substrate/user files, not clipboard (WP8 AC-1) |
| R8 | MCP config stores env RAW (P3's defect, but OD install may touch credentials) | 🔴(adjacent) | WP4 | Prefer zero-credential day-1 (D5); any credential recorded verbatim for P3 marker migration (WP4 AC-4); no casual raw rows |

**Not carried into P2** (owned elsewhere): KMS fail-soft `CredentialManager` (P3 build requirement); designer write-boundary (resolved by D1); stale-spec silent approval (killed by D6); no-cron audit cadence (ops/leader concern, out of P2).

---

## 8. Cross-Cutting — D6 Spec Front-Matter Phase-2 Slice

**The ONE hard rule (D6, arch §4.4): `pinned_spec_sha`.** Phase-2 wiring:

| Surface | D6 application |
|---------|----------------|
| Comparator findings schema (WP3) | `pinned_spec_sha` field; set when judging against an approved spec; every conformance-flavored compare output references the immutable spec version |
| WP5 verdict artifact | carries decision/spec SHAs where applicable (arch base SHA mandatory; spec SHAs if any spec governs) |
| WP8 e2e | proves the rule end-to-end: spec-play run with non-null SHA (AC-2) |
| All other front-matter | **advisory / lint-warning-only** — no P2 artifact hard-fails on naming, schema, or format front-matter (D6's explicit scope) |

---

## 9. §10 Open-Question Triage (this phase's ownership)

| §10 OQ | Triage | Rationale |
|--------|--------|-----------|
| Does self-hosted OD's `agent-browser` skill actually deliver app screenshot capture once installed? | **P2-WP5 OWNS producing the answer** (gate WP) | Dispatcher-assigned; the installed instance + defined operational criteria (C1–C5) turn the open question into a decided artifact; feeds GAP-1's resolution |
| Comparator A→C consolidation triggers (semaphore saturation >10/day; image-reader `comparison_mode`; native proxy multi-image) | **P2-WP2 records them as monitoring triggers; explicitly NOT day-1 work** | Dispatcher-assigned; triggers + escape valve recorded in tool module + `design.comparator.monitor` KV key; execution deferred until a trigger fires |
| Path→data-URI bridge design (store facts) | P1 owns design (A6); **P2-WP3 implements in the facade** | Phase map assignment; P2 does not re-design |
| `send_message` `images` param (GAP-3) | Out of P2 scope | Substrate paths + pixels-at-dispatch suffice for all P2 deliverables; stays open post-P2 |
| KMS-Lite root-key custody | P3 | KMS-Lite is P3's build |
| Raw-secrets one-time migration design | P3 | MCP-env seam + markers are P3; WP4 only records inputs |
| `job_continue` resume envelope convention | P3 | Bootstrap-flow mechanism (P3 lane) |
| Designer-initiated audit cadence (no daemon cron) | Out of P2 | Ops/leader-triggered per arch §8 🟡; no P2 WP depends on a cron |

---

## 10. Exit Criteria (phase-level)

1. `compare_images` facade live: real-screenshot compare returns the full structured findings schema (WP8 AC-1), never an image, never raising (WP2/WP3 ACs).
2. D6 proven: `pinned_spec_sha` non-null on the spec-play e2e findings (WP8 AC-2).
3. Gate decided: verdict artifact at `verdicts/capture-adopt-or-build.md` with evidence per criterion, branch named (WP5 ACs).
4. Branch landed or explicitly pending: WP6 (adopt, zero daemon code) or WP7 (build) executed per verdict — or a dispatcher-flagged deferral recorded in the verdict artifact.
5. Monitoring triggers on record: tool module + `design.comparator.monitor` KV key (WP2 AC-5).
6. Leaves-behind contract (§3.2 L1–L5) satisfied — P3's assumptions are true on the tree.
7. Zero violations of the docs-only constraints during planning (§11).

---

## 11. Hard-Constraint Compliance (this planning effort)

- [x] DOCS-ONLY markdown — this file plans; nothing builds.
- [x] NO git operations (no add/commit/push/merge; branch stays existence-only).
- [x] NO daemon/test boot, NO venv/uv, NO DB/network access from this effort.
- [x] NO actual OpenDesign install from the worktree — WP4 is a PLANNED ops-lane task (ambient POSTGRES_* live-probe trap respected; 3 prior incidents noted in WP4).
- [x] Read-only inspection only (arch doc + cited files; spot-checks in §4 were grep/sed reads).
- [x] D1–D6 never contradicted — every WP cites its ratifying decision.

## 12. Open Items for the Dispatcher

| # | Item | Needed by |
|---|------|-----------|
| O-1 | Confirm the verdict-artifact path choice: `implementation-plan/verdicts/capture-adopt-or-build.md` (this plan's canonical; §3.2 L5 establishes the `verdicts/` convention) | before P2 dispatch |
| O-2 | Confirm registry category key: plan specifies `"design": ["image-comparator"]` per arch pattern; implementer may rename if a narrower key fits, provided the `_auth.py` MUST-match rule holds (WP2 AC-1) | before P2-WP2 |
| O-3 | Ops-lane availability + host access for WP4 (manual OD install) — the only WP outside the daemon/agents lane | before Track B start |

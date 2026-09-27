# Phase 1 — Foundations (designer-agent implementation)

- **Date:** 2026-09-26
- **Author:** planner[v2] via plan-creation worker (phase-1 dispatch)
- **Source of truth:** `.agents/shared/planning/designer-agent/architecture-recommendation.md` (ratified 2026-09-26; D1–D6 LOCKED — never re-opened by this plan)
- **Status:** Draft — implements arch-doc §2 (D1/D2 decisions), §3 (anatomy + model wiring incl. §3.4 silent-fallback flag), §3.5 (constraints), §4.2–4.5 (artifacts/workflows/handoffs), §5.1 (tmp_images substrate), §8 (risks), §10 (OQ-1 bridge design + OQ-6 audit cadence)
- **Companions:** `phase2-parallel-builds.md` (P2), `phase3-bootstrap-kms-lite.md` (P3, written), `plan-overview.md` + `decisions.md` (separate workers)
- **Hard constraints (worktree-scoped):** docs-only markdown; NO `git add/commit/push/merge`; NO daemon/test boot, NO venv/uv, NO DB/network access from worktree (ambient `POSTGRES_*` live-probe trap — 3 prior incidents); read-only inspection allowed; D1–D6 never contradicted.

---

## 0. Phase Boundary (machine-checkable)

| | P1 owns | P1 does NOT own |
|---|---|---|
| Cluster A — Model wiring | `vision` entry in daemon-global `allowed_models` + restart protocol; `model_vision` deployment-config step in the same restart window; `caller_model_overrides` generalization into the spawn chain (D2); precedence-chain documentation + spawn-seam enforcement; silent-fallback verdict (resolve-or-defer) | Per-worker allowlists (do not exist — `allowed_models` is daemon-GLOBAL, arch-doc §3.4); comparator build (P2); `model_vision` unset fail-fast behavior change (existing, untouched) |
| Cluster B — Anatomy + wiring | `agents/designer/` hand-authored (meta.json verbatim §3.3 + soul/rule/workflow/tools_note); leader `team_members` 14→15; three leader `workflow.md` edit sites; two-channel clipboard-relay mitigation text; trigger-based audit cadence (no-cron reality documented) | Charter/tool-facade wiring template B (v2, deferred per arch-doc §3.1); installer skill + bootstrap flow (P3); comparator agent anatomy (P2); leader prompt-file semantic redesign beyond the three cited sites |
| Cluster C — Image substrate | Provenance sidecar `{feature, page, version, source_agent}`; agent-facing `image_save`/`image_list`/`image_get`; protected retention class; **path→data-URI bridge DESIGN doc** (design only) | Bridge implementation if P2 claims it (P2's comparator is the first consumer — build-home decided in plan-overview); capture tool / GAP-1 (P2, gated on OD `agent-browser` verification); OpenDesign-as-MCP render (P2) |
| Cross-cutting | Spec front-matter lint phase-1 slice: `design-spec.md` front-matter skeleton + `design-review.md` pinned-SHA citation rule; exactly ONE hard lint check (D6) + advisory warnings | Full lint tooling beyond the one hard check; conformance-loop mechanics (leader/designer workflow behavior, not P1 files) |

### P1 MAY ASSUME (fixed by dispatcher / verified ground truth)

- Base worktree `feature/designer-agent-design` @ `e67e5cd8` (branch of `latest`); D1–D6 ratified; no prior `agents/designer/` exists (verified: 39 agent dirs, no `designer`).
- `config.yaml:82` = `allowed_models: ${OPENAI_SELECTABLE_MODELS:-agentic,coding,coding2}` — exact-match, daemon-global (verified).
- Spawn chain model resolution lives at `daemon/services/instance_lifecycle.py:1780-1835` ("resolve the final model and its source ONCE" block) with `resolved_source` tracking; caller-override validation at `:1407-1436`; caller-facing fallback notice at `:1438-1481` (verified).
- tmp_images store facts (verified in `daemon/services/tmp_image_store.py`): `<data_dir>/tmp_images/` (`:115-117`), 1 GiB cap (`:117`), extensionless blob + `<id>.json` sidecar (`:74-82`), GET 404s on missing sidecar (`:20-21`), atomic blob (`O_CREAT|O_EXCL`) + sidecar (tmp-file + `os.replace`) writes (`:17-21`), sweep with orphan-sidecar view (`:215-221`).
- `explain_image` local reads are project-workdir-confined (`daemon/tools/image_tools.py:429-444`, verified) — the data-dir/workdir deployment caveat is real.

### P1 LEAVES BEHIND (inter-phase contract — P2/P3 may assume exactly this)

1. **`vision` live in daemon-global `allowed_models`** + a documented restart protocol that also covers `model_vision` (deployment config) and the one-shot boot-discovery ordering (`daemon/registry.py` `discover()`, `:537-575` — dir must land before restart; hot-add impossible).
2. **`caller_model_overrides` generalized into the spawn service chain** (`daemon/services/instance_lifecycle.py` spawn seam) — ANY sub-team lead can override child models; precedence chain **documented and enforced at the spawn seam**: `model_tier` > spawn `model=` > parent-map (`caller_model_overrides`) > `llm_models` pool > `llm_model` > global default.
3. **`agents/designer/` anatomy live** (meta.json per arch-doc §3.3 verbatim; soul/rule/workflow/tools_note per §3.2) + leader team/workflow entry wiring (three sites) — designer registered at boot discovery, craft-class hybrid, C+ BROAD writes, no deny key, `llm_model: "vision"`, `team_members: ["worker"]`.
4. **tmp_images substrate upgrades:** provenance sidecar, agent-facing `image_save`/`image_list`/`image_get` (path-addressable, provenance-queryable), protected retention class exempting design baselines from the 30-day sweep — **and the path→data-URI bridge DESIGN doc** (P2's comparator consumes this design; §5 below is its requirements contract).
5. **Phase-1 spec-lint slice:** `design-spec.md` front-matter skeleton + `design-review.md` must-cite-`pinned_spec_sha` templates with exactly one HARD check (D6) — P2/P3 lint work extends, never weakens, this rule.
6. **Spawn-time model observability:** spawn log carries resolved `model` + `source` (P1-WP3) — P2/P3 verification (comparator model, installer worker model) reads this signal instead of re-deriving it.

---

## 1. Phase Objective (1 sentence, testable)

After one planned restart, the daemon has a registered `designer` agent resolving to the `vision` model with non-silent resolution evidence, any sub-team lead can override child models through the generalized spawn chain, and the tmp_images substrate exposes provenance-tagged, retention-protected images to agents through `image_save`/`image_list`/`image_get` — with a bridge design ready for P2's comparator.

---

## 2. Ground-Truth Spot-Checks (verified read-only — these refine the arch doc, never contradict D1–D6)

| # | Arch-doc claim | Verified reality | Plan consequence |
|---|---|---|---|
| GT-1 | "Leader Implementation workflow routes UI/UX to designer (`agents/leader/workflow.md:242-252`); trivial cosmetic skip (`:258-262`); Debug Phase 1.5 UI/UX classification (`:459-466`)" | The three anchors are **INSERTION/EDIT points, not existing routes**: `:242-252` currently holds Architecture-Decision-Routing prose; `:258-262` holds the code-complexity ladder (cosmetic branch at `:259`); `:459-466` holds Phase 1.5 domain classification with **no UI/UX class**. Verified via grep: no `UI/UX`/`designer` routing exists anywhere in `agents/leader/workflow.md` today. | P1-WP5 **authors new routing blocks** at/near those anchors (do not treat as edits of existing designer routes). drift flag for `decisions.md`: arch-doc phrasing reads as if routes exist. |
| GT-2 | "🔴 spawn `model=`/overrides silently resolve to default when target ∉ allowed_models" | Precisely: `_resolve_model_override` (`instance_lifecycle.py:1407-1436`) never raises — no-match returns `None` with a **DEBUG-level** log only; companion `_format_model_fallback_notice` (`:1438-1481`) returns a caller-facing `[NOTE] Model '<X>' is not in allowed_models; spawned with the default model instead.` | Silent **in resolution** (no exception), but not fully silent at the tool layer. The fail-loud vs defer verdict (P1-WP3) must weigh this existing notice. |
| GT-3 | "resolution priority documented; enforce at spawn seam" | The `:1780-1835` block tracks `resolved_source` (`override`/`llm_models`/`llm_model`/`default`) but **logs nothing for the final resolution** except `llm_load_balance_selected` (llm_models path) and `instance_model_persisted` (`:1989`, llm_models path only); persist to `instance_metadata["model_override"]` happens for `override`/`llm_models` only (`:1984-1988`). The meta `llm_model` branch (Priority 3) is taken **without a visible allowed_models filter** in that block. | (i) Designer resolves via Priority 3 (`llm_model: "vision"`) → **no existing log/persist signal** proves what resolved → P1-WP3 adds the observability line (one log statement; also the phase's non-silent-evidence AC). (ii) The meta-path no-filter nuance is flagged for `decisions.md`; D2's operative change (allowlist entry) remains the hard prerequisite either way. |
| GT-4 | "designer meta.json draft (§3.3)" | All draft field names exist in the codebase — `coder/meta.json` (craft-class precedent) carries `no_force_explore`, `recursion_limit_multiplier: 7`, `context_injection.heuristic_match_shared_md_files`, `tools.allow` incl. `instance`/`service`/`midflight`, `team_members: ["worker"]`, and no `deny` key. | Draft is implementable verbatim; no schema surprises. |

---

## 3. Work-Package Index (P1-WPn IDs)

| WP ID | Title | Cluster | Touchpoints (file:line per arch doc, spot-verified where marked ✓) | Deps | Risks carried (§8) |
|---|---|---|---|---|---|
| **P1-WP1** | `vision` in daemon-global `allowed_models` + restart protocol | A | `config.yaml:82` ✓ (`${OPENAI_SELECTABLE_MODELS:-agentic,coding,coding2}`); `daemon/config.py:429-465` (env resolution + deprecation shim); `daemon/registry.py:537-575` ✓ (one-shot `discover()`) | none (gate for WP4-registration, WP12) | 🔴 silent fallback (§8 R1) — primary prerequisite; 🟡 boot-time restart coupling (§8) |
| **P1-WP2** | Generalize `caller_model_overrides` into the spawn chain (D2) | A | move from `daemon/tools/knowledge_tools.py:723-790` ✓ into `daemon/services/instance_lifecycle.py:1780-1835` ✓ spawn seam; inherit `_resolve_model_override` (`:1407-1436`), `_format_model_fallback_notice` (`:1438-1481`), persist (`:1984-1988`), restore re-read | WP1 (targets must be allowlisted to resolve) | 🟡 concurrency (no new slots); regression risk on explorer path (mitigated by AC-2b) |
| **P1-WP3** | Silent-fallback verdict + spawn-model observability (plan-time decision) | A | `instance_lifecycle.py:1436-1441` (DEBUG → WARNING upgrade); `:2006` ✓ (spawn log line — extend with `model=` + `source=`); verdict row → `decisions.md` | WP2 (same seam) | 🔴 R1 — closes the observability half; behavior change explicitly deferred |
| **P1-WP4** | `agents/designer/` anatomy — hand-authored | B | new `agents/designer/{meta.json,soul.md,rule.md,workflow.md,tools_note.md}`; meta.json **verbatim** arch-doc §3.3 (`:61-80`); soul rule arch-doc §3.2 (`:45-56`); 🔴 never `POST /api/agents` (§8 permissive-default trap) | WP1 (restart window ordering) | 🔴 POST /agents trap; 🟡 restart coupling |
| **P1-WP5** | Leader wiring: team 14→15 + three workflow edit sites | B | `agents/leader/meta.json` `team_members` (14→15, verified 14 ✓); `agents/leader/workflow.md` :242-252 (UI/UX route), :258-262 (trivial-cosmetic skip), :459-466 (Phase 1.5 UI/UX class) — all insertion points per GT-1; clipboard two-channel mitigation text cites `daemon/routers/messages.py:269` ✓ + `daemon/services/tmp_image_converter.py:128` ✓ | WP4 (designer must exist to route to) | 🟡 clipboard channel delivers descriptions not pixels (§8) |
| **P1-WP6** | Designer-initiated audit cadence WITHOUT daemon cron (§10 OQ-6) | B | arch-doc §4.3c (`:155-158`); encode in `agents/designer/workflow.md` (WP4 artifact) + leader trigger sites from WP5; no-cron reality accepted+documented | WP4, WP5 | 🟡 no daemon cron for audits (§8) — residual explicitly out-of-scope |
| **P1-WP7** | Provenance sidecar `{feature, page, version, source_agent}` | C | `daemon/services/tmp_image_store.py` sidecar record (`:74-82` schema point; `:17-21` atomicity); sidecar extension is additive | none | torn-sidecar window exists (`:20-21`) — provenance reads must stay 404-tolerant |
| **P1-WP8** | Agent-facing `image_save` / `image_list` / `image_get` | C | new tools in `daemon/tools/` (image category, alongside `daemon/tools/image_tools.py:429-444` ✓ confinement precedent); store methods via `tmp_image_store.py`; registry per `daemon/tools/_tool_registry.py` conventions | WP7 (provenance tags queryable) | 🟡 workdir confinement caveat — tools are daemon-side (data_dir-reachable), NOT workdir-confined reads |
| **P1-WP9** | Protected retention class (design baselines never swept mid-project) | C | `tmp_image_store.py` sweep (`:215-221` walk + orphan view; 30-day logic); retention class field rides WP7 sidecar | WP7 | sweep eviction must remain all-or-nothing per class (AC-9b) |
| **P1-WP10** | Path→data-URI bridge DESIGN (§10 OQ-1 — design doc, not implementation) | C | design doc at `planning/designer-agent/implementation-plan/bridge-design.md`; designs against verified store facts (§0 MAY ASSUME + §4 constraints); consumer: P2 comparator facade (`compare_images` paths-in, arch-doc §6 `:226-230`) | WP7 (sidecar = MIME source of truth) | 🟡 comparator vision variability (P2's); data-dir/workdir caveat is the reason the bridge exists |
| **P1-WP11** | Spec front-matter lint slice (D6: ONE hard rule) | X | templates: `planning/designer-agent/implementation-plan/templates/design-spec.md` (front-matter skeleton arch-doc §4.4 `:166-183`), `templates/design-review.md` (must cite `pinned_spec_sha`); lint tooling spec with exactly one HARD check + advisory warnings | none | advisory-lint creep risk (D6 violation by accretion) — guarded by AC-11c |
| **P1-WP12** | Phase rollout verification (end-to-end) | ALL | post-restart: registration + vision resolution evidence (WP3 log line); store round-trip w/ provenance; protected-retention sweep simulation; leader wiring presence | WP1–WP11 | aggregate |

---

## 4. Detailed Work Packages

### P1-WP1 — `vision` in daemon-global `allowed_models` + restart protocol

**Objective.** `vision` resolves as a model name daemon-wide, with a restart protocol that makes the one-shot boot-discovery ordering explicit and bundles every restart-coupled change into one window.

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | Add `vision` to the `allowed_models` default in `config.yaml:82` → `${OPENAI_SELECTABLE_MODELS:-agentic,coding,coding2,vision}` (or set `OPENAI_SELECTABLE_MODELS` in deployment env — document both, exact-match semantics) | `config.yaml:82` (or env doc) lists `vision`; grep-verifiable |
| 2 | Document in the restart protocol: `allowed_models` is daemon-GLOBAL (not per-worker; user intent "worker allowlist", mechanism exact — arch-doc §3.4) | protocol section states GLOBAL scope + exact-match |
| 3 | Same-window deployment config: `model_vision` (`config.yaml:23`, default **empty**) set to a vision-capable model — per-turn vision routing (`graph.py:7589` pattern) fail-fasts otherwise; comparator (P2) requires it (arch-doc §6: "Requires `model_vision` configured + `vision` in global `allowed_models`") | restart protocol includes `model_vision` step with the fail-fast consequence stated |
| 4 | Restart-protocol ordering: ALL `agents/` dir changes (WP4) land BEFORE restart (boot `discover()` is one-shot, `registry.py:537-575`; hot-add impossible — arch-doc §3.5) | protocol has an explicit "dir-first, restart-once" ordering rule |
| 5 | Name the single restart window: designer dir (WP4) + `vision` entry + `model_vision` + daemon-code changes (WP2/WP3/WP7-9 as implemented) land together | protocol lists the window contents as a checklist |

**Risks.** 🔴 R1 silent fallback — this WP is its primary prerequisite (mitigation: AC-1b, AC-12b). 🟡 restart coupling (§8) — mitigation: one window, checklist above.

**Dependencies.** None. Gates WP4 registration, WP12 verification; P2 comparator assumes both `vision` + `model_vision`.

---

### P1-WP2 — Generalize `caller_model_overrides` into the spawn chain (D2)

**Objective.** Any sub-team lead can override a child's model by declaring `caller_model_overrides` in its own meta.json — lookup moves from the knowledge-tool path to the spawn seam so it applies to every spawn, not one tool's path.

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | Move the map lookup from `knowledge_tools.py:723-790` (today: explorer-scoped) into `instance_lifecycle.py` spawn seam (`:1780-1835` block neighborhood): at spawn, read `caller_model_overrides` from the **calling (parent) agent's meta.json**, resolve the child's entry, feed through the existing precedence slot (between spawn `model=` and `llm_models`) | spawn-time resolution honors parent meta map for ANY parent (test: designer meta declaring `caller_model_overrides: {worker: vision}` → worker resolves `vision`) |
| 2 | Inherit the existing machinery unchanged: validation `_resolve_model_override` (`:1407-1436`), caller notice `_format_model_fallback_notice` (`:1438-1481`), persistence `instance_metadata["model_override"]` (`:1984-1988`), restore re-read skip | unit-level: override rejected on non-allowlisted target → notice surfaces, default used, no exception |
| 3 | Document the precedence chain and enforce at the seam: `model_tier` > spawn `model=` > parent-map > `llm_models` > `llm_model` > global default (tier validates first — `:1166-1251` `_resolve_model_tier` precedes the legacy chain) | doc block in spawn seam + chain asserted by one test per adjacent pair |
| 4 | Verify the explorer path: `knowledge_tools` either delegates to the chain or is retired if its spawn already flows through `instance_lifecycle` — no behavior regression for explorer | explorer override behavior preserved (spot-verify at implement; capture in WP12 evidence) |

**Risks.** Regression on the explorer path (only existing consumer) — mitigation: task 4 + AC-2d. No new concurrency exposure (lookup is map-read, no LLM/IO).

**Dependencies.** WP1 (targets must be allowlisted to resolve — the whole point of D2's operative change). Consumers: designer→worker `vision` shards (P2 comparator fan-out, P3 installer workers).

---

### P1-WP3 — Silent-fallback verdict + spawn-model observability (plan-time decision PD-1)

**Objective.** Resolve the 🔴 §3.4/§8 flag **for the designer phase** without changing daemon-wide spawn semantics, and make model resolution provable.

**Verdict: DEFER the behavior change; ADD observability.** (Plan-time decision → `decisions.md` as PD-1.)

- **Why not fail-loud:** the silent-fallback contract is deliberate (docstring: "do NOT error", `:1412-1415`); a raising guard changes spawn semantics for every agent (37+ roster), exceeding P1's scope and re-opening ratified ground (D2's operative change already prevents the designer exposure: with `vision` allowlisted, nothing falls back).
- **Why not pure status-quo:** GT-3 shows the fallback is DEBUG-silent and the meta `llm_model` path logs nothing — "model actually resolved to vision, not default" is unprovable today. Non-silent *evidence* is the requirement; non-silent *failure* is not.
- **Revisit trigger (record in PD-1):** any agent meta declaring a model absent from `allowed_models` at boot (new registry lint candidate), the first missed-entry incident, or P2 comparator resolution ambiguity → open a fail-loud/fail-notice commission (e.g. WARNING + spawn-result flag before any raise).

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | Upgrade the fallback log `instance_lifecycle.py:1436-1441` DEBUG → WARNING (text unchanged: names the model, the allowlist, and the fallback) | log line at WARNING level on rejection; no behavior change |
| 2 | Extend spawn log `:2006` with resolved model + source: `(agent=…, model={resolved_model}, source={resolved_source}, …)` — one line, all four resolution paths | every spawn logs what resolved and from where (AC-12b consumes this) |
| 3 | Write PD-1 row (context/options/decision/consequence/revisit trigger) into the plan-time decision register (§8 of this file) for the `decisions.md` worker | register row present; no contradiction with D2 |

**Risks.** 🔴 R1 — observability half closed here; prerequisite half closed by WP1. Log-volume: +1 field on an existing line, +0 lines on the happy path.

**Dependencies.** WP2 (same seam, land together). Consumed by WP12 (AC-12b).

---

### P1-WP4 — `agents/designer/` anatomy (hand-authored)

**Objective.** Designer exists as a registered, craft-class-hybrid agent with the ratified identity — authored by hand, never via `POST /api/agents`.

**🔴 Permissive-default trap (§8):** API-created agents carry no tools/deny fields. **Hand-author the directory.** This is a hard rule of this WP.

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | `agents/designer/meta.json` — **verbatim arch-doc §3.3 (`:61-80`)**: craft-class hybrid, C+ BROAD writes per D1, **no `deny` key**, `llm_model: "vision"`, `innate_skills: ["dynamic-skill","todo"]`, `skill_injection: true`, `no_force_explore: true`, `recursion_limit_multiplier: 7`, `tools.allow` incl. `image`/`mcp`/`instance`/`dynamic-skill`, `context_injection.heuristic_match_shared_md_files: true`, `team_members: ["worker"]` (all field names GT-4-verified against `coder/meta.json`) | file parses as JSON; keys match the §3.3 draft 1:1; `jq '.deny'` → null |
| 2 | `soul.md` ~80 lines — identity (expert designer + sub-team lead) + **THE judgment rule**: "I design and review; implementation belongs to coder. I may read/annotate any project file; I do not land app-code changes." — soul-level convention per D1, **NOT** a `tools.deny` entry | rule present verbatim-ish at soul level; no deny semantics anywhere in the dir |
| 3 | `rule.md` ~40 lines — validate spec vs brief acceptance criteria; NEEDS MORE INFO on thin briefs (charter `rule.md:12` precedent); never claim pixel fidelity for text mockups; sharding discipline (what partitions go to workers) | four rule families present |
| 4 | `workflow.md` ~100 lines — brief → enumerate ACs → (gap? NEEDS MORE INFO) → work directly or shard → spec sections (IA → components → tokens → a11y → wireframe → tradeoffs) → self-review → return; embeds WP6 audit-cadence triggers; in-flight state exposure per arch-doc §4.5 (`shared_meta_kv` keys `design.<task-id>.*`, ≤15 min heartbeat) | workflow covers the full loop; KV keys match §4.5 schema |
| 5 | `tools_note.md` ~60 lines — tool semantics; tmp_images substrate conventions (provenance tags, image_save/list/get, protected class); two-channel image reality (pixels only via base64 dispatch; substrate paths via bridge/tools — not clipboard); sub-team dispatch discipline (job_continue resume lane for PAUSED workers — `instance.py:2998-3004`) | conventions match WP7-10 surface; no workdir-confined read instructions for store paths |

**Risks.** 🔴 POST /api/agents trap (mitigation: hand-author, AC-4a; plus registration check AC-12a). 🟡 restart coupling (dir lands in the WP1 window). Constraint caps respected by design: child cap 50 (`config.py:546`), LLM concurrency 10 (`config.py:549`), invoke semaphore 4 (`utils.py:591-603`) — designer's sub-team fits (arch-doc §3.5).

**Dependencies.** WP1 (registration ordering). Consumed by WP5 (routing target), WP12, P3 (designer as sub-team lead).

---

### P1-WP5 — Leader wiring: team 14→15 + three workflow edit sites

**Objective.** Leader can discover, route to, and brief designer — with the UI/UX classification and the clipboard-relay mitigation encoded where leader actually decides.

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | `agents/leader/meta.json`: append `"designer"` to `team_members` (14→15; current 14 verified) | JSON parses; count = 15; contains `designer` |
| 2 | `agents/leader/workflow.md` **Implementation** section at the `:242-252` anchor (GT-1: insertion): "primary artifact is UI/UX → route to designer BEFORE coder" with the §4.5 leader→designer brief contract (task_id, phase, files, notes, plan_ref, conventions; pinned_spec_sha on re-conformance; escalation_path) | routing block present; brief contract fields match arch-doc §4.5 |
| 3 | `:258-262` anchor (GT-1: cosmetic branch exists at `:259` — extend it): trivial-cosmetic edits **skip designer** (straight to developer) | skip rule adjacent to the existing TINY/cosmetic ladder |
| 4 | `:459-466` anchor — Debug **Phase 1.5**: add UI/UX classification → designer (investigator), preserving the existing domain classes and the 3-instance concurrency note | UI/UX class present; no existing class removed |
| 5 | Two-channel clipboard mitigation (§4.3b, §8 🟡): leader relays the tmpimg://→text conversion **inline** + the ref/path; designer re-digests via substrate path/`explain_image` — because clipboard refs convert to text descriptions on the chat path (pixels cleared: `messages.py:269` pre_dispatch hook; `tmp_image_converter.py:128` pattern) and only direct base64 `images=[data_uri]` reaches vision routing | mitigation text present at the routing site; cites the mechanism (descriptions-not-pixels) |

**Risks.** 🟡 clipboard channel (§8) — mitigated by task 5. 🟡 leader prompt bloat — three scoped blocks, no rewrite of adjacent sections (GT-1 anchors are surgical).

**Dependencies.** WP4 (designer must be registered). Consumed by P2/P3 workflows that enter through leader.

---

### P1-WP6 — Designer-initiated audit cadence WITHOUT daemon cron (§10 OQ-6)

**Objective.** Design-system upkeep is trigger-driven and documented; the no-cron reality is accepted, not papered over.

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | Encode trigger set in `agents/designer/workflow.md` (WP4 artifact): (a) tester visual-drift failure → leader conformance loop; (b) phase boundaries (designer self-audits when a planned phase closes); (c) on request (leader/user); (d) pre-release sweep before merge to `latest` | four triggers present with entry conditions |
| 2 | Leader-side echo: the WP5 routing blocks reference the phase-boundary + pre-release triggers so leader knows to summon designer | leader blocks mention the triggers |
| 3 | Explicit residual: daemon-cron/self-scheduling audits are **out of scope** — revisit trigger: a real drift incident that no trigger caught, or a scheduler subsystem that gains agent-facing cron | residual + revisit trigger documented in §7 Deferred |

**Risks.** 🟡 no daemon cron (§8) — accepted by ratification posture (arch-doc §4.3c "noted gap"); mitigation is the trigger web above, not new infra.

**Dependencies.** WP4, WP5.

---

### P1-WP7 — Provenance sidecar `{feature, page, version, source_agent}`

**Objective.** Every substrate image carries structured provenance, queryable later.

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | Extend the sidecar record (`tmp_image_store.py:74-82` schema point) with optional `provenance: {feature, page, version, source_agent}` — additive, default null (clipboard-path images stay tag-free) | old sidecars read unchanged; new field present when provided |
| 2 | Preserve atomicity: provenance rides the existing sidecar write (tmp-file + `os.replace`, `:17-21`) — no second file, no new torn-write class | no new partial-state mode beyond the documented torn sidecar (`:20-21`) |
| 3 | 404-tolerance: provenance reads through the same missing-sidecar 404 gate as the blob (`:20-21`) | provenance query on torn/missing sidecar → clean 404-style miss, never a hang |

**Risks.** Torn-sidecar window pre-exists (`:20-21`) — task 3 keeps provenance inside the same contract; no new retention/sweep semantics in this WP (WP9 owns classes).

**Dependencies.** None. Consumed by WP8 (queryable), WP9 (class field rides the same sidecar — implement together), WP10 (bridge reads sidecar for MIME).

---

### P1-WP8 — Agent-facing `image_save` / `image_list` / `image_get`

**Objective.** Agents address the substrate directly: save with provenance, list by provenance, fetch by path — path-addressable, provenance-queryable.

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | `image_save(bytes|path, feature, page?, version?, source_agent=auto)` → returns substrate path/id; auto-stamps calling agent id as `source_agent` | save round-trips; `source_agent` auto-stamped |
| 2 | `image_list(feature=?, page=?, source_agent=?)` → path/id + provenance + ts, 404-gated listing semantics (torn sidecars excluded — the sweep's orphan view precedent, `:215-221`) | filter combinations return exact subsets; torn entries never listed |
| 3 | `image_get(path|id)` → bytes + MIME (sidecar is the MIME source of truth — blobs are extensionless, `:74-75`) + provenance | get returns sidecar MIME, not extension-guessed |
| 4 | Tool registration in the `image` category (alongside `explain_image`); tools run daemon-side with data_dir access — they are **not** subject to the `explain_image` workdir confinement (`image_tools.py:429-444`), and `tools_note.md` (WP4) says so explicitly | agents on the `image` category can save/list/get; docs state the confinement distinction |

**Risks.** 🟡 workdir confinement caveat (§8-adjacent, arch-doc §5.1) — the raison d'être of WP10; task 4 prevents agents being told the wrong access model. Abuse guard: save respects the 1 GiB store cap (`:109-117`, `TmpImageStoreFull` surfaces as a clean tool error).

**Dependencies.** WP7. Consumed by P2 (capture/comparator substrate flow), designer workflows (conformance captures, baselines).

---

### P1-WP9 — Protected retention class (design baselines never swept mid-project)

**Objective.** A retention class distinct from the 30-day clipboard sweep: `protected` images survive sweeps until explicitly released or project-closed.

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | Add `retention_class: normal|protected` to the sidecar (rides WP7's additive extension); default `normal` (clipboard behavior byte-identical) | default path unchanged; sweep math untouched for `normal` |
| 2 | Sweep (`:215-221` walk) skips `protected` entries regardless of age; store-cap pressure (`:109-117`) still counts them (cap exhaustion → `TmpImageStoreFull`, never silent protected-eviction) | protected entry at age > 30 d survives sweep simulation; cap accounting includes it |
| 3 | Release path: explicit `image_save`-class downgrade or project-close sweep note — protected ≠ immortal; document both | release semantics documented; no orphan-forever state |

**Risks.** Protected-class eviction ambiguity under cap pressure — mitigation: task 2 (fail loud via `TmpImageStoreFull`, consistent with the store's existing all-or-nothing posture).

**Dependencies.** WP7 (sidecar). Consumed by designer baselines (conformance loops compare against unswept baselines — arch-doc §5.1).

---

### P1-WP10 — Path→data-URI bridge DESIGN (§10 OQ-1 home; design doc only)

**Objective.** A design doc P2's comparator can implement against: how a substrate path becomes a `data:` URI for pixels-at-dispatch, designed against the three verified store facts.

**Output.** `planning/designer-agent/implementation-plan/bridge-design.md` — requirements contract below is binding; solution shape is P2-implementer freedom.

**Design constraints (each traces to a verified fact).**
| Store fact (verified) | Design requirement |
|---|---|
| **Data-dir/workdir deployment caveat** — store lives `<data_dir>/tmp_images/` (`:115-117`); `explain_image` local reads are workdir-confined (`image_tools.py:429-444`); deployed installs have data_dir ∉ any project workdir | Bridge executes **daemon-side** (data_dir-reachable seam — store method or tool-layer helper, not an agent-side file read); agents never circumvent confinement by hand |
| **Extensionless blobs + JSON sidecar** (`:74-82`) | MIME resolved from the sidecar record (never extension guessing); data-URI prefix built from sidecar MIME; magic-byte sniff only as a sidecar-missing fallback (arch-doc §5.1 "extensionless-tolerant magic-byte reads") |
| **404-gated listing/GET semantics** (torn sidecar ⇒ 404, `:20-21`) | Bridge treats 404 as authoritative unavailability — no optimistic caching, no retry-storm; comparator receives a structured unavailable-path error, not a truncated image |
| **Consumer shape** — comparator passes `images=[a, b]` in one vision call (multi-image tested: `instance_messaging.py:113-128`, `test_vision_routing.py::TestMultipleImages`; arch-doc §6 `:226-230`) | Output contract: ordered `data:` URIs (base64) ready for the `images=[...]` dispatch param; per-image size guard before base64 expansion (state the cap; store total is 1 GiB, `:109-117`) |
| **Provenance available** (WP7) | Bridge optionally tags its output with provenance so comparator findings can cite `{feature, page, version}` (feeds §4.5 designer→tester handoff) |

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | Write `bridge-design.md` with: seam-placement options (store method vs tool-layer) + recommendation; MIME resolution order; 404 contract; size guard; output schema; provenance tagging; two worked examples (comparator pair; designer explain_image-adjacent flow) | every §5 constraint row addressed; recommendation justified against the facts table |
| 2 | Mark implementation home as P2's decision (plan-overview worker arbitrates P1-design vs P2-build split) | doc states the handoff explicitly |

**Risks.** 🟡 comparator vision variability (§8) — out of P1 scope but the design's criteria-passthrough note (`criteria?` param) keeps the comparator soul in charge. Design-vs-build drift — mitigation: constraints table is the contract; P2 review compares implementation to it.

**Dependencies.** WP7 (sidecar MIME/provenance). Consumed by P2 (comparator facade `compare_images`).

---

### P1-WP11 — Spec front-matter lint slice (D6: exactly ONE hard rule)

**Objective.** The designer artifact contract exists from day 1 with `pinned_spec_sha` as the single hard rule; everything else advisory.

**Tasks.**
| # | Task | Acceptance |
|---|---|---|
| 1 | `templates/design-spec.md` — front-matter skeleton verbatim arch-doc §4.4 (`:166-183`): `spec_id`, `status` enum, `pinned_spec_sha` (set at `status: approved`), advisory `owners`; body: component-by-component guidance + pack-mapped ACs (`Validation:` convention) + token refs + traceability table | template matches §4.4 field-for-field; hard rule commented as THE rule |
| 2 | `templates/design-review.md` — findings per component, `conformance_iter` tagged, **must cite `pinned_spec_sha`** (D6 hard rule, arch-doc §4.2 table `:136`) | template's verdict block has a mandatory `pinned_spec_sha:` field |
| 3 | Lint tooling spec (tooling itself may be P2+; the SPEC is P1): exactly ONE hard check — *conformance verdict missing/unmatched `pinned_spec_sha` ⇒ FAIL*; ALL else (component schema, criteria format, naming, owners) ⇒ WARNING-only | spec lists 1 hard check by name; advisory list enumerated; zero other FAIL rules |
| 4 | Anti-creep note: adding any new hard lint check requires a `decisions.md` ADR (D6 discipline) | note present |

**Risks.** Advisory-lint creep silently re-creates the unfunded-enforcement gap D6 killed — mitigation: tasks 3-4.

**Dependencies.** None. Consumed by designer workflows (WP4 references templates), P2/P3 lint work.

---

### P1-WP12 — Phase rollout verification (end-to-end)

**Objective.** Prove the phase with commands/queries — no narrative claims.

| # | Verification | Provable by |
|---|---|---|
| 12a | Designer registered post-restart | boot log / agent listing shows `designer`; dir existed pre-restart (WP1 protocol ordering) |
| 12b | **Non-silent vision resolution** | spawn designer → P1-WP3 log line shows `model=vision source=llm_model` (or `override` if leader spawned with `model=vision`); absence of the WARNING fallback line; `vision` present in effective `allowed_models` (config/env echo) |
| 12c | Override chain live | designer (or any lead) spawns worker with meta `caller_model_overrides: {worker: vision}` → worker spawn log `model=vision source=override`; non-allowlisted target → WARNING + `source=default` + caller notice |
| 12d | Store round-trip with provenance | `image_save(feature=f1)` → `image_list(feature=f1)` returns it with `{feature,page,version,source_agent}` → `image_get` returns bytes + sidecar MIME |
| 12e | Protected retention | protected-class image with mtime aged past 30 d survives a sweep run (or sweep-eligibility query excludes it); `normal` control is swept; cap accounting includes protected bytes |
| 12f | Leader wiring | leader meta count = 15 incl. `designer`; three workflow blocks present at their anchors; clipboard-relay text present |
| 12g | Templates + lint spec | both templates exist; lint spec names exactly one hard check |
| 12h | Bridge design | `bridge-design.md` addresses all five constraint rows |

---

## 5. Coupling Map

| | P1 | P2 | P3 |
|---|---|---|---|
| **P1** | — | **tight** (bridge design → comparator; `vision`+`model_vision` → comparator vision calls; substrate tools → capture flow) | **tight** (designer lead + overrides → installer dispatch; spawn observability → P3 verification) |
| **P2** | tight | — | **loose** (P3 consumes P2's early manual OD install + builtin class) |
| **P3** | tight | loose | — |

Within P1: WP1 → {WP4 registration, WP12}; WP4 → {WP5, WP6, WP12}; WP7 → {WP8, WP9, WP10}; WP2 → WP3 (same seam); WP11 independent. Critical path: **WP1 ∥ WP7 → WP4 → WP5 → restart window → WP12**.

---

## 6. Phase Exit Criterion (machine-checkable)

ALL of: 12a–12h pass on a single post-restart daemon; `decisions.md` register (§8 below) handed off with PD-1..PD-4 resolved-or-deferred rows; `bridge-design.md` + both templates on disk under `implementation-plan/`. Any red ⇒ phase not done; no partial credit on 12b (non-silent resolution is THE phase gate).

---

## 7. Deferred — explicitly NOT planned here

- Fail-loud spawn guard (raise on non-allowlisted model) — deferred by PD-1 with revisit trigger (P1-WP3).
- Daemon cron / self-scheduled audits — residual of §10 OQ-6; trigger web is the day-1 answer (P1-WP6 task 3).
- Bridge **implementation** — design lands in P1; build home arbitrated at plan-overview (P2 comparator is first consumer).
- Charter/tool-facade wiring template B (arch-doc §3.1 v2) — designed, deferred; v1 team-phase wiring only.
- GAP-1 capture tool / OD `agent-browser` verification; OpenDesign-as-MCP; comparator agent + `compare_images` (all P2).
- `send_message images=` param (GAP-3 §10) — substrate paths + pixels-at-dispatch suffice for P1; not P1's OQ.
- Bootstrap flow, KMS-Lite, installer skills (P3).

---

## 8. Plan-time Decision Register (hand-off rows for `decisions.md`)

| ID | Decision | Verdict | Rationale (condensed) | Revisit trigger |
|---|---|---|---|---|
| **PD-1** | 🔴 silent-fallback: resolve or defer (arch-doc §3.4/§8) | **DEFER behavior change; ADD observability** (WP3: WARNING upgrade + spawn-log model/source) | Raising guard changes spawn semantics daemon-wide (37+ agents) beyond designer scope; D2's allowlist entry already removes the designer exposure; fallback already emits a caller `[NOTE]` (`:1467-1481`) so it is not fully silent | Missed-entry incident; boot-time meta-model lint candidate; P2 comparator resolution ambiguity |
| **PD-2** | `model_vision` deployment step inside the P1 restart window | **In scope** (WP1 task 3) | Vision routing (`graph.py:7589`) fail-fasts if unset; P2 comparator hard-requires it; same window = one restart | If deployment env cannot carry it: comparator verification (P2) blocks, revisit |
| **PD-3** | Spawn-log extension (`model=`/`source=` at `:2006`) | **Adopt** (WP3 task 2) | Only existing proof of resolution is the llm_models persist log (`:1989`); designer resolves via `llm_model` (Priority 3) which logs nothing (GT-3) | Log-volume complaint (none expected: +2 fields, +0 lines) |
| **PD-4** | Leader `workflow.md` anchors are insertion points (GT-1) | **Author new blocks** at :242-252 / :258-262 / :459-466 | No UI/UX routing exists today; arch-doc phrasing reads as pre-existing routes — recorded to prevent implementer confusion | Anchor drift on `latest` before implementation → re-locate by section, not line |

---

## 9. Open Questions YOU OWN — Triage (per dispatcher spec)

| # | OQ (arch-doc §10) | Triage | Rationale (1 line) |
|---|---|---|---|
| 1 | Path→data-URI bridge design against verified store facts | **P1-WP10** (design doc; build home arbitrated at plan-overview) | P2's comparator consumes the bridge day-1; the three store facts are P1-verified ground; design-now/build-next keeps D4's zero-code-dependency parallelism honest |
| 2 | Designer-initiated audit cadence without daemon cron | **P1-WP6** (trigger web) with residual **explicitly out-of-scope** (§7) | Triggers (tester drift, phase boundaries, on-request, pre-release) cover every ratification-relevant entry point; self-scheduling needs infra that does not exist and D-decisions did not fund |

## 10. Open Questions YOU DO NOT OWN (flagged for awareness)

- OD `agent-browser` capture verification (GAP-1) — P2, gates capture build.
- `send_message images=` param (GAP-3) — dispatcher-level; P1's substrate+bridge posture works without it.
- Comparator A→C consolidation triggers — P2, on record.
- KMS raw-row migration, root-key custody, `[resume]` envelope ratification — P3 (P3 file already carries its own triage).
- Meta `llm_model` path lacking a visible allowed_models filter (GT-3 nuance) — flagged to `decisions.md` via PD-1/PD-3; not a P1 behavior change.

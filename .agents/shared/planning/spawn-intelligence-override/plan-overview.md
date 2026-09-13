# Plan Overview: Spawn-Time Intelligence Override (Feature #1)

Date: 2026-09-14
Author: planner[v2] via plan-creation worker
Status: Approved — architecture pass complete (A1–A10), owner-ratified (D12/D13)
Verified at SHA: a904374e56d386048d29f7e56a5f4b5014926757 (branch `plan/spawn-intelligence-override`)

Companion to: **Feature #2** (long-tool-call-nudge, merged). Feature #1 populates the `# FUTURE` extensibility seam in `_build_long_tool_notice` (Feature #2 settled zone).

---

## Objective

Parents can spawn a child with opt-in higher intelligence via a new `spawn_instance(model_tier="high")` parameter. The parameter resolves to a daemon-side configured high-tier model (default `"agentic"`) and persists the model choice for the child's lifetime. Default spawn behavior is unchanged: omitting `model_tier` continues to use today's weighted-pool load balancing.

---

## Background

### Incident bd4b36ef — the bug class Feature #1 closes

On 2026-09-13, instance `bd4b36ef` (a `coder` child on model `coding`, parent `Dev[V2]` `84563a03`) ran for **5h48m** doing real but slow work — 5 bash calls spanning 12.5–30+ minutes each, the daemon's 7200s cap as the sole backstop, child-nudge sitting 19min mid-tool before the parent noticed. Zero LLM errors. The signature is **busy-slow weak-model**: the model isn't broken, the task just out-runs the model's headroom.

The smart loop we want:

1. **Nudge** — Feature #2 already fires (long-tool-call > per-child threshold).
2. **Parent decides** — rec 4 in the nudge notice (this plan, D4) tells the parent the option exists.
3. **Terminate** — rec 3 in the existing notice already covers this.
4. **Re-spawn with high intelligence** — new: `spawn_instance(agent_id=<role>, model_tier="high")`.
5. **Continuation brief** — the parent passes the original task + the previous child's output context.

Today the parent has `model="agentic"` (working but undiscovered — see Wanderer verdict below). Feature #1 makes the high-intent **discoverable**, **validatable**, and **advisable** in the nudge.

### Wanderer verdict (KB, HIGH confidence)

> ~90% of Feature #1 already exists. `spawn_instance(model='agentic')` works today; priority-1 override skips pool; frozen in `instance_metadata.model_override`; restore-revalidates; allowlist contains `agentic` (default tuple `_ALLOWED_MODELS_DEFAULT = ("agentic", "coding")` at `daemon/config.py:2307`).

Missing: **(a)** explicit opt-in surface parents will discover, **(b)** loud-vs-silent validation choice, **(c)** nudge-text recommendation wiring, **(d)** no daemon alias table. This plan ships all four.

---

## Verified Evidence Map

All file:line references verified by direct read at SHA `a904374e`.

| Concept | File:line | Verified |
|---------|-----------|----------|
| `SpawnInstanceInput.model` (param-surface precedent) | `daemon/tools/instance.py:1745-1755` | ✓ |
| `spawn_instance` signature + docstring (current) | `daemon/tools/instance.py:1806-1825` | ✓ |
| `spawn_councilor` strict validation (copy-worthy) | `daemon/tools/instance.py:2046-2068` | ✓ |
| `_resolve_model_override` (silent fallback today) | `daemon/services/instance_lifecycle.py:1241-1280` | ✓ |
| `_format_model_fallback_notice` (existing notice path) | `daemon/services/instance_lifecycle.py:1282-1315` | ✓ |
| Resolution priority chain | `daemon/services/instance_lifecycle.py:1619-1668` | ✓ |
| `model_override` persist (override + llm_models sources) | `daemon/services/instance_lifecycle.py:1820-1828` | ✓ |
| Restore + revalidate stored model_override | `daemon/services/instance_lifecycle.py:3894-3929` | ✓ |
| `_select_weighted_model` (pool seam, fires per-spawn) | `daemon/services/llm_load_balancer.py:21` | ✓ |
| `append_allowed_models` (discoverability seam) | `daemon/services/instance_lifecycle.py:877-944` | ✓ |
| `_ALLOWED_MODELS_DEFAULT` (canonical home for allowed models) | `daemon/config.py:2307` (corrected from spec's `2214+`) | ✓ |
| `allowed_models` field + `OPENAI_SELECTABLE_MODELS` env | `daemon/config.py:385-413` | ✓ |
| `_build_long_tool_notice` (5-section locked) | `daemon/services/long_tool_nudge.py:796-855` | ✓ |
| `# FUTURE` extensibility seam | `daemon/services/long_tool_nudge.py:843-849` | ✓ |
| Notice structure test pin (5 sections + pause/resume absence) | `tests/unit/test_long_tool_nudge.py:537-551` | ✓ |
| `register_tool_category("instance")` site | `daemon/tools/instance.py:1804` | ✓ |
| `wrapped_tools_node` use site (Feature #2 settled) | `daemon/graph.py:8492-8506` | ✓ |
| `caller_model_overrides` (per-agent null-semantics precedent) | `agents/explorer/meta.json:18-20` | ✓ |

**Drift correction:** the spec's task brief cited `daemon/config.py:2214+` for `_ALLOWED_MODELS_DEFAULT`; the actual line is `2307`. Everything else in the brief's code map matched exactly.

---

## Design Summary (one-line per decision)

See `decisions.md` for full rationale; one-line summary here:

| ID | Decision | Summary |
|----|----------|---------|
| D1 | Param surface | (a) New `model_tier: Literal["high"]` on `spawn_instance`; global tier→model map |
| D2 | Validation semantics | LOUD `ValueError` (mirror `spawn_councilor`); legacy `model=` silent path UNTOUCHED |
| D3 | Which parents | `spawn_instance` ONLY (parent-spawns-child path); `spawn_councilor` out of scope |
| D4 | Nudge text | Replace `# FUTURE` placeholder with real rec 4 referencing `model_tier='high'` |
| D5 | Hint injection | docstring + `append_allowed_models` enrichment + nudge text; no system-prompt edits |
| D6 | Default-unchanged pin | Regression test proving `_select_weighted_model` still fires when `model_tier` absent |
| D7 | Facade-Forwarding | NOT triggered — no new InstanceManager kwarg (resolver returns resolved model) |
| D8 | Activation | Daemon restart required (`SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` is process-lifetime) |
| D9 | Test discipline | `uv run python -m pytest` from worktree root exclusively |
| D10 | Non-goals | No pool changes; no per-agent defaults; no auto-upgrades; no system-prompt edits; no provider alias changes |
| D11 | Deferred items | Per-agent tier overrides; multiple tiers beyond `"high"`; `spawn_councilor` tier param; auto-recovery |

---

## Phases

| Phase | Name | Objective | Primary files | Independent test gate | Status |
|-------|------|-----------|----------------|------------------------|--------|
| 1 | Config surface + resolver | Add `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` env (default `"agentic"`) and `_resolve_intelligence_tier` free function in lifecycle | `daemon/config.py` (new `spawn_intelligence` section), `daemon/services/instance_lifecycle.py` (new resolver) | Unit test: `_resolve_intelligence_tier("high") → ("agentic", None)` with default env; `("agentic", WARN)` when not in allowed_models; `(None, None)` when tier is `None`; `(None, ERROR)` for unknown literals | pending |
| 2 | Tool surface + validation | Wire `SpawnInstanceInput.model_tier` and the loud-validation path; thread resolved model through `manager.spawn_instance(model=...)` | `daemon/tools/instance.py:1728-1806` (input model + signature), `daemon/tools/instance.py:2046-2068` (validation pattern) | Real-dispatch integration test: `spawn_instance(agent_id="coder", model_tier="high")` returns `(instance_id, "agentic")` when in allowed_models; raises `ValueError` listing valid models when NOT in allowed_models. Legacy `model=` silent path regression test unchanged. | pending |
| 3 | Discoverability surfaces | Update `spawn_instance` docstring + extend `append_allowed_models` block with a `# Spawn Intelligence` tail | `daemon/tools/instance.py:1807-1825` (docstring), `daemon/services/instance_lifecycle.py:907-924` (allowed-models tail) | Unit test: docstring mentions `model_tier` and the high-tier default. Unit test: `append_allowed_models` with `inject_allowed_models=True` includes the new tail; with `inject_allowed_models=False` does NOT. Merge constraint (A7): lands same-commit-as or after Phase 2 — never before. | pending |
| 4 | Nudge text integration | Replace `# FUTURE` placeholder with real rec 4 referencing `model_tier='high'`; update notice test pin | `daemon/services/long_tool_nudge.py:843-849` (lines to replace), `tests/unit/test_long_tool_nudge.py:546` (pin update) | Test: `TestU11NoticeStructure` updated — `"# FUTURE"` replaced with `"model_tier"` + `"high"` + `"Re-spawn with high intelligence"` once. All other `TestU11*`/`U12*`/`U17*` UNCHANGED green. Length test (`test_length_within_1_5x_wedge_notice`) threshold comfortable. | pending |
| 5 | Default-unchanged regression + activation | Prove `_select_weighted_model` still fires when `model_tier` absent; document activation/restart notes | New: `tests/unit/services/test_spawn_intelligence_tier.py`; new: `tests/integration/test_spawn_default_unchanged.py`; rollout doc section in plan | Unit + integration test: `manager.spawn_instance(agent_id="coder", model_tier=None, model=None)` proceeds through weighted pool; persisted `instance_metadata.model_override` matches pool-selected model; `_resolve_intelligence_tier` is NOT called. | pending |

### Phase dependency graph

```
Phase 1 ──► Phase 2 ──► Phase 3
   │            │
   │            └──► Phase 5 (depends on 2)
   │
   └─► Phase 4 (independent of 1; depends on Phase 2 only for `model_tier` name confirmation)
```

Phase 4 is technically independent of Phase 1 (the nudge text only references the param NAME; the resolver doesn't need to exist for the text to be valid). Phase 5 depends on Phase 2 because the test must observe a real `spawn_instance` call. Phases 3 and 4 are independent of each other.

A combined "implementation worktree" can land all 5 phases in one PR; a "review worktree" can test each gate independently.

**Merge shape (A7, architect-confirmed 2026-09-14):** one PR, **≥3 ordered commits: P1 / P2+P3 / P4+P5**, with the P2→P3 constraint explicit — P3's docstring/`Field`-description renders the tool-schema description for the P2 field, so P3 must land **same-commit-as or AFTER P2, never before** (a P3-first landing describes a nonexistent param).

---

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---------|---------|---------|---------|---------|
| Phase 1 | — | tight (resolver feeds validation) | independent | loose (param name only) | independent |
| Phase 2 | tight | — | tight (docstring + tool) | loose (param name) | tight (real dispatch) |
| Phase 3 | independent | tight | — | independent | independent |
| Phase 4 | loose | loose | independent | — | independent |
| Phase 5 | independent | tight | independent | independent | — |

**Tight couplings to flag for the phase-plan worker:**
- Phases 1 ↔ 2: the resolver's return shape `(model: str | None, error: str | None)` is the contract Phase 2 consumes. Lock this in Phase 1 before Phase 2 starts.
- Phase 2 ↔ Phase 3: the docstring text in Phase 3 must match the field name in Phase 2 (`model_tier`, literal `"high"`). Land in same commit or order 3 after 2. **A7 hard constraint (2026-09-14): same-commit-as or AFTER P2 — never before**; the docstring/`Field`-description references the P2 field, and landing first renders a description for a nonexistent param.
- Phase 2 ↔ Phase 5: the integration test in Phase 5 needs Phase 2's `spawn_instance` signature to expose `model_tier`.

**Independent pairs:** Phase 3 (discoverability) and Phase 4 (nudge) touch different surfaces; either can land first.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Naming lock-in (`model_tier`, `"high"`) | Medium | Low | `Literal["high"]` in v1; widen later with coordinated change |
| 2 | L1 global tier map limits flexibility | Medium | Medium | Per-agent overrides explicitly deferred (D11); v2 if operator feedback |
| 3 | L2 loud validation may confuse parents accustomed to silent `model=` | Medium | Medium | Docstring explicitly calls out asymmetry; `ValueError` lists valid models |
| 4 | L4 nudge-text settled-zone regression | High | Low | Phase 4 single-commit acceptance criterion (prod + test pin update land together) |
| 5 | L4 rec 4 ignored by parents (soft wording) | Low | Medium | Soft wording matches existing recs 1-3; discoverability does the heavy lifting |
| 6 | Activator forgets to restart (process-lifetime env) | High | Medium | Phase 5 activation checklist; rollout doc |
| 7 | Vocabulary drift across three discoverability surfaces | Medium | Low | `grep -rn "model_tier"` across `daemon/` + `agents/` catches drift |
| 8 | Weighted pool non-determinism in regression test | Low | Medium | Membership assertion in integration test; monkeypatch in unit test |
| 9 | Existing Feature #2 settled zones regress | High | Low | Phase 4 acceptance test: all `TestU11*`/`TestU12*`-`TestU17*` UNCHANGED green |
| 10 | Facade-Forwarding discipline violation | Medium | Low | D7: confirmed not triggered (no new InstanceManager kwarg) |
| 11 | Existing kill-switch `ENSEMBLE_LONG_TOOL_NUDGE_*` accidentally affected | High | Low | D4 changes only `_build_long_tool_notice` body; no env vars, no kill-switch paths |
| 12 | Existing `LongToolNudgeScanner` accidentally modified | Medium | Low | D4 is string-template only; Phase 4 acceptance excludes scanner file |
| 13 | `wrapped_tools_node` use site accidentally touched | High | Low | Phase 4 acceptance excludes `daemon/graph.py:8492-8506` |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | `spawn_instance(model_tier="high")` returns a child with `agentic` as the resolved model | Real-dispatch integration test | Pass: returned `instance_id` + `model="agentic"` |
| 2 | `spawn_instance(model_tier="high")` raises `ValueError` when `agentic` is NOT in `allowed_models` | Real-dispatch integration test | Pass: `ValueError` raised with valid-models list in message |
| 3 | `spawn_instance(model_tier="high")` persists `model_override="agentic"` in `instance_metadata` | DB read-back in integration test | Pass: `instance_metadata["model_override"] == "agentic"` |
| 4 | Legacy `spawn_instance(model="gpt-4")` STILL silently falls back when not in `allowed_models` | Regression test (unchanged behavior) | Pass: spawn succeeds; notice string returned to parent |
| 5 | `spawn_instance()` (no `model_tier`, no `model`) continues to use weighted pool | Default-unchanged regression test | Pass: persisted `model_override` is one of `["agentic", "coding", "coding2"]`; `_resolve_intelligence_tier` NOT called |
| 6 | Nudge notice still has 5 sections (a-e); section (d) mentions `model_tier='high'` | `TestU11NoticeStructure` updated assertions | Pass: all 5 sections present; `assert "model_tier" in notice` passes; `assert "# FUTURE" not in notice` passes |
| 7 | All Feature #2 settled zones green | `tests/unit/test_long_tool_nudge.py` + scanner tests | Pass: 0 regressions |
| 8 | Discoverability: parent reading `spawn_instance` docstring learns about `model_tier` | Docstring unit test | Pass: docstring contains `model_tier` and `"high"` |
| 9 | Discoverability: agent with `inject_allowed_models=True` sees the new `# Spawn Intelligence` tail | `append_allowed_models` unit test | Pass: tail block present when flag=True; absent when flag=False |
| 10 | Daemon restart activates new env var | Manual smoke test (not in CI) | Pass: `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL=gpt-5` after restart produces children with `model="gpt-5"` |
| 11 | Facade-Forwarding Discipline unviolated | grep + integration test | Pass: `grep -n "model_tier" daemon/manager.py` returns zero results; integration test asserts no `TypeError` |
| 12 | All tests run via `uv run python -m pytest` from worktree root exclusively | Per-Phase acceptance | Pass: each phase's test gate invokes pytest via uv |

---

## Activation / Restart Notes

**Process-lifetime config.** Per the spec: *"meta.json per-agent config is a process-lifetime snapshot (restart needed)."* The new env var `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` follows the same pattern. Phase 5 includes a rollout checklist:

1. Stop the daemon (graceful — see Repo & Dev Environment Conventions blueprint).
2. Optionally set `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` (default `"agentic"` if unset).
3. Start the daemon.
4. Verify boot log shows the expected config load (existing boot-line pattern; no new boot line required for Phase 1).
5. Smoke test: spawn a child via REST API with `model_tier="high"`, verify `instance_metadata.model_override` is the configured high-tier model.

**No daemon-internal config flip required.** The Feature #2 kill-switch family (`ENSEMBLE_LONG_TOOL_NUDGE_*`) is unaffected — Phase 4 is string-template only.

**No meta.json edits required.** The feature is operator-side env var only; no agent's `meta.json` changes.

**No new kill-switch.** Feature #1 is additive and parent-initiated; a kill-switch would defeat the discoverability goal. (A3, 2026-09-14: setting `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL=""` is **NOT** a kill-switch — empty/whitespace env = UNSET = default `"agentic"`, per the `_clean_env_value` shell-style `:-` house pattern at `daemon/config.py:2248-2253`, unanimous across every `_resolve_*` helper. Soft-disable instead = set the env var to a model name NOT in `allowed_models`: boot-time WARNING at load_config + every `model_tier="high"` call raises loud with the configured value named (R-A6). That soft-disable path is a v2 concern.)

---

## Non-Goals (explicit)

The following are **out of scope** for v1:

- **No pool changes** — weighted-pool weights and membership at `daemon/services/llm_load_balancer.py` and `daemon/config.py` are untouched.
- **No per-agent-type tier defaults** — no `meta.json` field for "this agent type prefers tier X". Global-only (D1).
- **No automatic/spontaneous upgrades** — the parent must write `model_tier="high"` explicitly. No auto-promotion of slow children.
- **No system-prompt edits** — discoverability is via docstring + `append_allowed_models` enrichment + nudge text (D5). No new ambient context blocks.
- **No provider-side alias changes** — `coding` / `coding2` / `agentic` remain opaque provider strings. The daemon tier map is the only mapping layer.
- **No `spawn_councilor` tier param** — council semantics differ (D3).
- **No multiple tier literals** — only `"high"` in v1 (D11).

---

## Deferred Items

| Item | Why deferred | Trigger to revisit |
|------|--------------|---------------------|
| Per-agent tier overrides (mirror `caller_model_overrides`) | v1 ships global-only; per-agent is configuration churn across 15 agents | Operator feedback that a specific agent needs a different tier |
| Multiple tier literals (`"low"`, `"medium"`, `"auto"`) | YAGNI; v1 ships `"high"` only | Use case surfaces for "I want a cheap child" |
| `spawn_councilor` tier param | Council = diverse models by design | Use case surfaces for "high-tier council" |
| Auto-recovery (no parent prompt required) | Parent decides per spec | Long-term automation arc |
| Tier→model map hot-reload (no restart) | Env vars are process-lifetime; matches existing pattern | Operational request for no-downtime config flips |
| ~~`SPAWN_INTELLIGENCE_TIER_HIGH_MODEL=""` kill-switch~~ | **ANSWERED/REMOVED (A3+A10, 2026-09-14):** empty/whitespace env = UNSET = default `"agentic"` (`_clean_env_value`, `daemon/config.py:2248-2253`); soft-disable = a model name NOT in `allowed_models` (boot WARN + per-spawn loud raise — see D13). Row kept for traceability. | n/a — answered by architect review + owner ratification |

---

## Open Questions

See `decisions.md` §Open Questions for the full list. Top three for caller attention:

1. **Tier literal scope** — `"high"` only, or include `"default"` for symmetry? **Draft position: ship `"high"` only.**
2. **Error message wording** — should the `ValueError` include the operator-overridable default ("default: 'agentic'")? **Draft position: include.**
3. **Nudge rec-4 wording final** — architect review on the proposed text in `decisions.md` §D4 is the natural gate before Phase 4 lands.

---

## Companion Plan Files (phaseN-plan.md — to be authored by phase-plan worker)

The phase-plan worker will detail each of the 5 phases into:

- `phase1-plan.md` — Config surface + resolver
- `phase2-plan.md` — Tool surface + validation
- `phase3-plan.md` — Discoverability surfaces
- `phase4-plan.md` — Nudge text integration
- `phase5-plan.md` — Default-unchanged regression + activation

Each phaseN-plan.md follows the standard template (objective, tasks with depends-on + acceptance, coupling, risks, exit criterion). Tight couplings to flag (Phase 1↔2 contract, Phase 2↔3 same-commit, Phase 2↔5 test dependency) are noted in the Coupling Map above.

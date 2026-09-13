# Decisions: Spawn-Time Intelligence Override (Feature #1 of agents-ensemble daemon)

Date: 2026-09-14
Author: planner[v2] via plan-creation worker
Status: Approved — architecture pass complete (A1–A10), owner-ratified (D12/D13)
Feature: Opt-in higher-intelligence model for child spawns (companion to long-tool-call-nudge / Feature #2)
Verified at SHA: a904374e56d386048d29f7e56a5f4b5014926757 (branch `plan/spawn-intelligence-override`)

---

## Summary of recommendations (one-line each)

| ID | Question | Recommendation |
|----|----------|----------------|
| D1 | Param surface | (a) New `model_tier: Literal["high"]` on `spawn_instance`; global tier→model map; extensible |
| D2 | Validation semantics | LOUD `ValueError` (mirror `spawn_councilor` 2046-2068); legacy `model=` silent path UNTOUCHED |
| D3 | Which parents | `spawn_instance` ONLY (parent-spawns-child path); `spawn_councilor` out of scope |
| D4 | Nudge text | Replace `# FUTURE` placeholder with real rec 4 referencing `model_tier="high"`; update test pin |
| D5 | Hint injection | docstring + `append_allowed_models` enrichment + nudge text (D4); no system-prompt edits |
| D6 | Default-unchanged pin | Regression test proving `_select_weighted_model` still fires when `model_tier` absent |

---

## D1 — Param Surface

### Options considered

**(a) New `model_tier="high"` param with daemon-side tier→model resolution.** Adds a small, opinionated vocabulary layer in front of the existing `model=` string. `spawn_instance(agent_id="coder", model_tier="high")` is the new opt-in surface.

**(b) New param that is a model-name passthrough.** A second param like `high_intelligence_model="agentic"` — but this duplicates the existing `model="agentic"` semantics and adds an alias surface for no behavioral gain.

**(c) No new param — discoverability only.** Point parents at the existing `model="agentic"` via docstring + nudge text. No code change to `SpawnInstanceInput`.

### Recommendation: (a) with global tier→model map

Owner intent verbatim: *"a NEW OPTION to spawn a child with higher intelligence — resolved to the agentic/default model."* This is a "new option" — (c) violates the literal spec. (b) duplicates `model=`.

The tier name (`"high"`) is the **discoverability handle** the spec is paying for: a parent writing `model_tier="high"` is self-documenting in the LLM tool call; the same parent writing `model="agentic"` requires the parent to already know which model name is "high". The semantic mapping is daemon-owned, which decouples parent prompts from operator-side model renames (an operator can re-point "high" from `agentic` to `gpt-5` without touching agent code).

### Where does "high" map, per-agent or global?

**Recommendation: GLOBAL**, with one canonical resolver and one env var.

- **Per-agent tier overrides** would mirror `agents/explorer/meta.json:18-20` `caller_model_overrides` (three-way null semantics: absent → no override, null → config default, string → explicit model). That precedent is for CALLER-side pinning in a council flow — a different shape than spawn-time override. Replicating it for tier overrides would: (1) require schema work in 15 agent `meta.json` files; (2) complicate activation (per-agent config is process-lifetime snapshot — restart needed); (3) defer the simpler 80%-case (everyone agrees "high" = agentic) behind per-agent config.
- **GLOBAL** keeps the alias table at one canonical home: `daemon/config.py` next to `_ALLOWED_MODELS_DEFAULT: tuple[str, ...] = ("agentic", "coding")` at `daemon/config.py:2307`. Operator flips `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` (or `OPENAI_INTELLIGENCE_TIER_HIGH_MODEL` — see Naming below) and restarts; no agent prompt or meta.json churn.

**Per-agent overrides are DEFERRED** (see Deferred Items in plan-overview).

### Naming

- **Param name:** `model_tier` (matches the spec's "tier" vocabulary; not `intelligence` to avoid clashing with future "intelligence_score"-style metrics).
- **Tier literal:** `Literal["high"]` (open for future extension to `"low"`, `"medium"`, but v1 ships ONE tier).
- **Env var:** `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` (default `"agentic"`). Matches the existing `SPAWN_*`/`OPENAI_*` env family at `daemon/config.py:385-413`. Fall back to the `_ALLOWED_MODELS_DEFAULT` tuple's first element (`"agentic"` at `daemon/config.py:2307`) if env unset — keeps behavior identical to "spec says resolve to the agentic/default model".

### Justification (file:line evidence)

- Existing param-surface precedent: `SpawnInstanceInput.model` at `daemon/tools/instance.py:1745-1755`. Pattern: `Annotated[str | None, Field(...)] = None` with rich description. `model_tier` follows the same shape but as `Literal["high"] | None`.
- `model=` already exists and works (`daemon/tools/instance.py:1806` signature). The new param is additive; existing callers unchanged.
- `_ALLOWED_MODELS_DEFAULT = ("agentic", "coding")` at `daemon/config.py:2307` is the canonical home for "what models does this deployment know about"; the tier→model map lives in the same module.
- Resolution chain precedent (`daemon/services/instance_lifecycle.py:1619-1668`): priority 1 = validated override, priority 2 = `metadata.llm_models` weighted pool, priority 3 = `metadata.llm_model`, priority 4 = config default. The new tier path slots in at priority 1 — same precedence as today's caller-supplied `model=`.

### Rejected alternatives — rationale

- **(b) passthrough alias** — duplicates `model=`; no discoverability gain; pure rename.
- **(c) discoverability-only** — explicitly violates spec ("a NEW OPTION").
- **Per-agent tier overrides in v1** — deferring simplifies activation (one env var, no `meta.json` edits across 15 agents) and avoids prematurely locking a per-agent surface that may not be wanted.

### Risk of recommendation

- **Naming lock-in.** `model_tier="high"` is a vocabulary commitment. If a future tier ("low", "medium", "auto") lands, the Literal grows. Mitigation: keep tier values as `Literal["high"]` in v1 (closes the door silently); if a second tier ships, file a follow-up that widens the Literal in coordinated fashion (Phase 1 will re-validate the resolver for any new tier string).
- **Global tier map hardcodes an opinion.** A deployment that wants different tiers for different agents cannot get them in v1. Mitigation: per-agent override is documented in plan-overview.md Deferred Items; operator feedback can drive the v2 surface.

### Test implications

- Unit test: `_resolve_intelligence_tier("high")` returns `("agentic", None)` when env unset and `"agentic"` is in allowed_models.
- Unit test: `_resolve_intelligence_tier("high")` returns `("agentic", "WARN: ...") ` when env unset and `"agentic"` is NOT in allowed_models — the warning is the loud-validation contract (D2).
- Unit test: `_resolve_intelligence_tier(None)` returns `(None, None)` — no override.
- Unit test: `_resolve_intelligence_tier("bogus")` returns `(None, "ERROR: ...") — unknown tier literal; mirrors `spawn_councilor`'s RUNTIME validation at `daemon/tools/instance.py:2046-2068`. **A1 re-anchor (architect, 2026-09-14):** the previously cited `model_validator` rejection at `daemon/tools/instance.py:1757-1762` checks `agent_id` ONLY — `SpawnInstanceInput` has NO Pydantic-level model validation, which is precisely why the tool-body resolver block must do the validating.

---

## D2 — Validation Semantics

### Options considered

**Loud (`ValueError`).** Mirror `spawn_councilor` at `daemon/tools/instance.py:2046-2068` — when the validated model resolves to `None` (not in allowed_models), raise a `ValueError` with the valid-models list. No silent fallback. The calling agent MUST see the failure.

**Silent fallback (current `spawn_instance` `model=` behavior).** `_resolve_model_override` at `daemon/services/instance_lifecycle.py:1241-1280` returns `None` when not in allowed_models; the spawn continues with the default model. The TOOL layer surfaces a soft notice via `_format_model_fallback_notice` at `daemon/services/instance_lifecycle.py:1282-1315` — parent sees `[NOTE] Model 'X' is not in allowed_models; spawned with the default model instead.`

### Recommendation: LOUD for the new `model_tier` path; LEGACY `model=` UNAFFECTED

The spec is explicit: *"Owner context favors LOUD — a parent explicitly requesting high intelligence must KNOW if it silently fell back to a quick model."* This is the right semantics: if a parent writes `model_tier="high"` expecting `agentic` and the deployment's `allowed_models` does NOT include `agentic`, the parent must abort (or correct the call) — not silently run on a fast/cheap model that fails the high-complexity task.

The asymmetry with `spawn_instance` legacy `model=` is **deliberate** and matches the KB note *"Today's silent-vs-strict asymmetry is DELIBERATE."* The two params encode different intents:
- `model="agentic"` — parent is expressing a *specific* model preference, and the fallback to default is a graceful degradation (parent gets a working spawn, just on a different model).
- `model_tier="high"` — parent is expressing a *capability* requirement, and the fallback defeats the purpose (parent asked for "high" because the task is hard; a default-model spawn is exactly what they were trying to avoid).

The KB explicitly validates this asymmetry: spawn_councilor is documented as deliberate for council model diversity; extending the strict-raise pattern to `model_tier` follows the same rationale.

### Justification (file:line evidence)

- `spawn_councilor` strict-validation pattern at `daemon/tools/instance.py:2046-2068` — copy-worthy template (canonical normalization at line 2070-2078, W7).
- `_resolve_model_override` silent path at `daemon/services/instance_lifecycle.py:1241-1280` — UNCHANGED; legacy `model=` continues to fall back.
- `_format_model_fallback_notice` at `daemon/services/instance_lifecycle.py:1282-1315` — UNUSED for the `model_tier` path; the loud raise happens before the spawn proceeds.
- W7 canonical normalization at `daemon/tools/instance.py:2070-2078` (case-insensitive match against allowed_models) — apply to the tier-resolved model name before validation, so `model_tier="high"` resolving to `"agentic"` matches even if `allowed_models` lists `"Agentic"`.

### Rejected alternatives — rationale

- **Loud for legacy `model=` too** — breaks the silent-fallback contract for existing callers. Out of scope; would be a breaking change. Spec is explicit: D2 is about the NEW path.
- **Soft notice + loud log** (hybrid) — the parent LLM still has to parse a string to detect failure. `ValueError` is the explicit LLM-tool contract for "this call did not do what you asked"; LLM frameworks route tool errors back to the model as a failed tool call, which is the right signal.
- **Loud via return string (like spawn_instance auth gate)** — the spawn_instance auth gate returns a `"ERROR: ..."` string at `daemon/tools/instance.py:1833-1846`; consistent with that. But spawn_councilor uses `ValueError` for validation failures at `daemon/tools/instance.py:2021-2068`. The council pattern is more canonical for "this validation rejected your parameter"; mirror it.

### Risk of recommendation

- **Behavioral asymmetry between `model=` and `model_tier` may confuse parents.** A parent that learns "the fallback notice is OK" for `model=` may try `model_tier="high"` and get a ValueError. Mitigation: docstring explicitly calls out the asymmetry (`model=` silent fallback for graceful degradation; `model_tier` loud raise for capability requirements); docstring links to the relevant lines.
- **Loud validation blocks the spawn entirely.** If `agentic` is not in allowed_models, the parent gets nothing — no spawned child. Mitigation: this is the correct behavior (silent fallback would defeat the parent's intent); the `ValueError` message lists valid models so the parent can retry with `model=` if appropriate.

### Test implications

- Real-dispatch integration test: `spawn_instance(agent_id="coder", model_tier="high")` against a manager where `agentic` is NOT in `allowed_models` raises `ValueError` with the valid-models list.
- Real-dispatch integration test: `spawn_instance(agent_id="coder", model_tier="high")` against a manager where `agentic` IS in `allowed_models` returns `(instance_id, "agentic")` tuple.
- Default-unchanged regression test (D6): `spawn_instance(agent_id="coder")` with NO `model_tier` continues to use the weighted pool.
- Unit test: legacy `spawn_instance(agent_id="coder", model="gpt-4")` against `allowed_models=["agentic","coding"]` STILL silently falls back (notice string returned) — confirms we did not regress the silent path.

---

## D3 — Which Parents

### Options considered

**(a) Expose on `spawn_instance` ONLY.** Single tool surface change at `daemon/tools/instance.py:1804-1806`. Parents that need it (developer, tester, any agent that spawns children) already have access via the `instance` tool category.

**(b) Expose on `spawn_instance` AND `spawn_councilor`.** Adds the same field to `spawn_councilor` at `daemon/tools/instance.py:1981`. Governor flow gets the same capability.

**(c) Expose on every instance-category tool.** All 7 `register_tool_category("instance")` sites: `:1804` (spawn_instance), `:2530`, `:3274`, `:3816`, `:4114`, `:4135`, `:4151`.

### Recommendation: (a) — `spawn_instance` ONLY

The Feature #2 use-case is specifically **parent → re-spawn stuck child** (`daemon/services/long_tool_nudge.py:840-841` recommendation 3: `terminate_instance + re-spawn a replacement`). The follow-on rec 4 (D4) tells the parent to write `spawn_instance(...)`. That is the primary and only documented call site.

- **`spawn_councilor` is for governor diversity** — a governor spawning a council for voting/review. Different flow. The "high intelligence" tier doesn't fit council semantics (councilors are typically diverse-model by design; pinning all to `agentic` defeats the council purpose).
- **All instance-category tools** is scope creep — only `spawn_instance` creates new instances; the others (terminate, send_message, etc.) operate on existing ones.

This matches the Feature #2 precedent (AM-3) of pinning the change to the spawn surface, not the broader tool category.

### Justification (file:line evidence)

- `register_tool_category("instance")` sites at `daemon/tools/instance.py:1804, 2530, 3274, 3816, 4114, 4135, 4151` (7 sites) — `spawn_instance` is the only SPAWN-creating site in the instance category.
- `_check_team_membership` authorization gate at `daemon/tools/instance.py:1844` — existing; the new param flows through this gate unchanged.
- `caller_agent_id` closure capture at `daemon/tools/instance.py:1796` — the new param is bound to the same authorization check as `agent_id`.

### Rejected alternatives — rationale

- **(b) spawn_councilor too** — semantic mismatch (council = diverse models by design). A "high intelligence" council override could be added later if a use-case surfaces, but YAGNI for v1.
- **(c) every instance-category tool** — `terminate_instance`, `send_message`, `subtree_messages`, etc. do not take a model override. Adding `model_tier` to non-spawn tools has no meaning.

### Risk of recommendation

- **A future parent needs the override on a different spawn-like tool.** Mitigation: the resolver (`_resolve_intelligence_tier`) is a free function in `daemon/services/instance_lifecycle.py`; adding it to a second spawn site is a 5-line change once the resolver exists.

### Test implications

- Tool-surface test: `SpawnInstanceInput` schema includes `model_tier: Literal["high"] | None = None` field.
- Tool-surface test: `spawn_instance` runtime signature accepts `model_tier` keyword.
- Negative test: `spawn_councilor` signature does NOT accept `model_tier` (out of scope for v1).
- Negative test: `terminate_instance` signature does NOT accept `model_tier` (out of scope).

---

## D4 — Nudge Text

### Current state (verified)

`daemon/services/long_tool_nudge.py:796-855` `_build_long_tool_notice` has a **5-section locked structure**:
- **(a) header** — line 820-825: child/tool/call-id/elapsed/threshold
- **(b) why-it-matters** — line 826-830: busy-slow weak-model signature, loop-breaker evasion
- **(c) three recommendations** — lines 831-842: #1 subtree_messages, #2 send_message, #3 terminate_instance
- **(d) `# FUTURE` extensibility seam** — lines 843-849: greppable placeholder, NOT implemented today
- **(e) advisory-only footer** — lines 850-853: episode id

The test pin `TestU11NoticeStructure.test_five_sections_and_no_pause_resume_advice` at `tests/unit/test_long_tool_nudge.py:538-551` asserts:
- `"[system:long-tool-nudge]" in notice`  (a)
- `"busy-slow / weak-model signature" in notice`  (b)
- `"subtree_messages" in notice`, `"send_message" in notice`, `"terminate_instance" in notice`  (c)
- `"# FUTURE" in notice`  **(d)**
- `"advisory only" in notice`  (e)
- `"pause_instance" not in notice`, `"resume_instance" not in notice`

### Recommendation: REPLACE the `# FUTURE` placeholder with a real rec 4 referencing `model_tier="high"`; UPDATE the test pin

The Feature #2 plan (per critical notes: *"Feature #2 integration: populate the # FUTURE seam in _build_long_tool_notice (daemon/services/long_tool_nudge.py — 5 locked sections) with the smarter-re-spawn recommendation"*) explicitly contemplates this change. The 5-section structure is preserved; only section (d) content changes from placeholder to real recommendation.

### Exact wording (proposed)

Replace lines 843-849 with:

```
(
    "4. Re-spawn with high intelligence: spawn_instance(agent_id=<role>, "
    "model_tier='high') — picks the configured high-tier model (default 'agentic'). "
    "Use after recommendation 3 if the child was busy-slow on a low-tier model."
),
```

Notes on the wording:
- **Numbered as "4."** to extend the existing 1-2-3 numbered list; keeps the parents' mental model "do these in order".
- **Named param `model_tier='high'`** — discoverable; the parent writes the explicit param name and gets the new opt-in surface (D1).
- **`agent_id=<role>`** — generic; the parent fills in its own child role (e.g., `coder`, `developer`).
- **`'high'`** — single quotes match the typical LLM tool-call style; unambiguous inside a JSON-ish prompt context.
- **`picks the configured high-tier model (default 'agentic')`** — tells the parent what they'll actually get, including the operator-overridable default.
- **`Use after recommendation 3`** — chains to the existing "terminate + re-spawn" rec so the parent's mental model is: terminate first, then re-spawn with high tier.

**Architect ruling (A9, 2026-09-14):** the rec-4 wording above ships VERBATIM as planned — single-path (names only `model_tier='high'`; no second param in the notice). The dual-path insight (mentioning the legacy `model=` fallback in the notice) is RELOCATED to the loud `ValueError` message (architecture-recommendation.md §2.1, adopted as phase2-plan.md task 4b.i by A2), where it has strictly better context: the notice fires BEFORE any failure and teaches the one canonical path; the error fires exactly when the parent needs the fallback, with the actual allowed-list interpolated. This keeps the Feature #2 settled-zone delta minimal and the `test_length_within_1_5x_wedge_notice` margin untouched.

### Test pin update (coordinated)

The test pin `tests/unit/test_long_tool_nudge.py:546` (`assert "# FUTURE" in notice`) MUST be updated. Two coordinated assertions replace it:

```
assert "model_tier" in notice  # (d) rec 4 mentions the new opt-in param
assert "high" in notice        # (d) rec 4 names the tier literal
```

And one new assertion to ensure we don't double-implement:

```
assert notice.count("Re-spawn with high intelligence") == 1  # exactly one rec 4
```

The other 5-section pins (a/b/c/e) are UNCHANGED. The "pause_instance not in notice" / "resume_instance not in notice" guards are UNCHANGED. The `test_length_within_1_5x_wedge_notice` test at `tests/unit/test_long_tool_nudge.py:553-556` may need its threshold relaxed (adding one line grows notice by ~250 chars; wedge notice is ~600-800 chars; 1.5x is comfortably wide — verify in Phase 4).

### Settled-zone discipline

Per the spec: *"Feature #2 settled zones must not regress (notice structure pins, kill-switch ENSEMBLE_LONG_TOOL_NUDGE... family, scanner)."* The settled zones are:
- 5-section structure (a-e) — preserved; only (d) content changes
- Notice pins (the 11 assertions in `TestU11NoticeStructure`) — preserved except for the one explicit `# FUTURE` pin
- Kill-switch family `ENSEMBLE_LONG_TOOL_NUDGE_*` — UNTOUCHED
- `LongToolNudgeScanner` — UNTOUCHED (D4 is a string-template change only)
- `wrapped_tools_node` site at `daemon/graph.py:8492-8506` — UNTOUCHED

The change is **string-template only**: zero new state, zero new code paths, zero new env vars. The Feature #2 incident-bd4b36ef-driven structure is preserved; the 4th rec is a one-liner that the test file is the only other place to touch.

### Rejected alternatives — rationale

- **Add a 4th rec that is a "5" and KEEP the # FUTURE as a 6th placeholder** — overcounts recs; the parent's mental model breaks (recs 1-3 are concrete; rec 4-5 become long). Stick to "4 recs, no placeholder".
- **Make the rec advisory with strong wording ("MUST re-spawn with high tier")** — violates the spec's "RECOMMENDATION (advisory; parent decides)". The current recs use soft wording ("Consider", "if stuck past 2x threshold"); rec 4 mirrors that style.
- **Embed the tier name in env-var form** (`$SPAWN_INTELLIGENCE_TIER_HIGH_MODEL`) — over-engineered; the parent's LLM will not interpolate env vars. Hardcode "default 'agentic'" in the prose.

### Risk of recommendation

- **Test pin update is a coordinated change.** If Phase 4 ships the prod code change without the test update, the test suite breaks. Mitigation: Phase 4 has a single acceptance criterion — both `_build_long_tool_notice` change AND the test pin update land in the same commit.
- **Rec 4 may be ignored by parents.** Soft wording is intentional; parents that need high-tier today already use `model="agentic"` (today's path). Rec 4 lowers the cognitive load (one phrase to recognize, one tool-call shape to copy) without forcing the parent.
- **Rec 4 says "agent_id=<role>" but the parent may have a different name in mind.** Mitigation: the parent knows its own child role; `<role>` is a placeholder for the parent's context.

### Test implications

- `TestU11NoticeStructure.test_five_sections_and_no_pause_resume_advice` updated per above.
- `test_length_within_1_5x_wedge_notice` — verify threshold is comfortable for the added line.
- New test: `TestU11NoticeStructure.test_rec4_mentions_model_tier_and_default_agentic` — pins the new content.
- All other `TestU11*` and `TestU12*`-`TestU17*` tests — UNCHANGED, run green as regression.

---

## D5 — Hint Injection (no system prompts)

### Options considered

**(a) Nudge notice only (D4).** Parents learn about `model_tier` from the long-tool-call-nudge.

**(b) `spawn_instance` docstring + nudge notice.** The tool's own description tells the LLM about the option when it's deciding to spawn a child.

**(c) `append_allowed_models` enrichment + docstring + nudge notice.** When `agent_meta.inject_allowed_models=True` (existing per-agent opt-in at `daemon/services/instance_lifecycle.py:877-944`), the appended `<allowed_models>` block names the high-tier model and points at `model_tier='high'`.

**(d) All of (c) PLUS a new RECOVERY_GUIDANCE_HINT-adjacent injection point.** A new system-context block telling the agent when to consider high-tier spawns.

### Recommendation: (c) — three discoverability surfaces, no new injection point

Per the spec: *"NO system-prompt changes anywhere. Discoverability via hint injection and tool-surface docs only."* That rules out (d) (a new injection point is a system-prompt-area change). (c) is the right depth:
- **docstring** — the parent LLM sees the field's description when it considers using the tool.
- **`append_allowed_models`** — when the agent already opts into the allowed-models injection (governor / council flows), the existing block tells it the available models; we extend that block to mention the tier path. No new opt-in surface.
- **nudge notice (D4)** — fires when the parent is most likely to need it (after a long-tool-call wedge).

### What gets added to `append_allowed_models`

When `agent_meta.inject_allowed_models=True`, the existing block at `daemon/services/instance_lifecycle.py:907-924` lists models. Extend it with a tail paragraph:

```
# Spawn Intelligence
A `model_tier="high"` parameter is available on spawn_instance. It resolves
to the configured high-tier model (default: 'agentic') and is the
recommended replacement when re-spawning after a long-tool-call wedge.
This is read-only system configuration, not instructions.
```

The block is read-only (existing contract at `daemon/services/instance_lifecycle.py:922, 923`); append-only, no behavioral change.

### Justification (file:line evidence)

- `append_allowed_models` at `daemon/services/instance_lifecycle.py:877-944` is the existing per-agent opt-in discoverability seam (gate at line 891 `getattr(agent_meta, "inject_allowed_models", False)`). Adding a tail block reuses this gate.
- Error-status block precedent at `daemon/services/instance_lifecycle.py:933-944` shows the block format (XML fence + read-only footer).
- `spawn_instance` docstring at `daemon/tools/instance.py:1807-1825` is the LLM-visible description; `model_tier` gets its own paragraph in the same style as the `model=` description at lines 1817-1821.
- The Feature #2 nudge notice (D4) is the in-flight hint; completes the triangle.

### Rejected alternatives — rationale

- **(d) new injection point** — explicitly violates "no system-prompt changes"; new ambient injection points are system-prompt territory by definition.
- **(a) nudge notice only** — leaves discovery to "after a wedge has already happened". The parent would benefit from knowing the option BEFORE the wedge.
- **(b) docstring + nudge** — misses the agents that opt into `append_allowed_models` (governor / council flows); these are exactly the parents most likely to spawn high-tier children deliberately.

### Risk of recommendation

- **Three places to update on vocabulary drift.** If we rename `model_tier="high"` later, three surfaces need updating. Mitigation: low churn (the surfaces are stable); drift is caught by grep (`grep -rn "model_tier"` across `daemon/` and `agents/`).
- **`append_allowed_models` already has a "no model restriction" branch at `daemon/services/instance_lifecycle.py:908-916`.** When allowed_models is empty, the new tail block still applies (the tier still resolves; the loud validation will reject it; the spawn fails — correct behavior). The tail should be in the always-shown branch, not gated by `if not allowed`.

### Test implications

- Unit test: `append_allowed_models` with `agent_meta.inject_allowed_models=True` and `allowed_models=["agentic","coding"]` returns a system-prompt fragment containing both the original block AND the new `# Spawn Intelligence` paragraph.
- Unit test: `append_allowed_models` with `agent_meta.inject_allowed_models=False` (default) does NOT include the new block (existing fail-open behavior preserved).
- Unit test: docstring of `spawn_instance` mentions `model_tier` and its semantic ("high intelligence model override").

---

## D6 — Default-Unchanged Pin

### Options considered

**(a) Unit test on `_select_weighted_model` invocation.** Pin that when `spawn_instance` is called without `model_tier` AND without `model`, `_select_weighted_model` fires and the returned model is from `metadata.llm_models`.

**(b) Integration test on `InstanceManager.spawn_instance`.** Real-dispatch path; verify the persisted `instance_metadata.model_override` is the weighted-pool selection (NOT a tier-resolved value).

**(c) End-to-end test (spawn + first LLM call).** Boot a manager, spawn without `model_tier`, capture the LLM model's first invocation, verify it matches the weighted-pool selection.

### Recommendation: (a) + (b) — unit + integration

(c) is overkill for a regression pin (the LLM model is already pinned by the resolution-chain test at `daemon/services/instance_lifecycle.py:1619-1668`; what we want is to prove the new param doesn't disturb the chain when absent).

(a) is the minimum: pin the absence-of-param path through the resolver. (b) is the broader integration pin: the real `manager.spawn_instance(...)` with no `model_tier` argument returns a tuple where the second element is from the weighted pool (NOT from `_resolve_intelligence_tier`).

### Test shape (specifics)

**Unit test (`tests/unit/services/test_instance_lifecycle.py` or new `test_spawn_intelligence_tier.py`):**
- `_resolve_intelligence_tier(None)` returns `(None, None)` — no tier requested, no resolution.
- `manager.spawn_instance(agent_id="coder", model_tier=None)` proceeds without consulting `_resolve_intelligence_tier`.
- The persisted `instance_metadata["model_override"]` is set ONLY if the resolution source is `"override"` OR `"llm_models"` (existing behavior at `daemon/services/instance_lifecycle.py:1820-1828`); the test asserts `"override"` is from `model=` (legacy), `"llm_models"` is from the weighted pool.

**Integration test (`tests/integration/test_spawn_default_unchanged.py`):**
- Real `InstanceManager` + real DB (SQLite or PG per Repo conventions).
- `manager.spawn_instance(agent_id="coder", instance_id=None, parent_id=<seed>, project_id=<seed>, instance_name=None, model=None, version_tag=None)` — no `model_tier`.
- Assert: returned model is one of `["agentic", "coding", "coding2"]` (matches weighted pool).
- Assert: `instance_metadata.model_override` equals the returned model (proves the pool-selection path persisted).
- Assert: NO call to `_resolve_intelligence_tier` was made (monkeypatch the function and assert not-called; or use a counter fixture).

### Justification (file:line evidence)

- Resolution chain at `daemon/services/instance_lifecycle.py:1619-1668` defines the priority; the new param slots at priority 1 ONLY when present.
- `model_override` persist at `daemon/services/instance_lifecycle.py:1820-1828` — when source is `"llm_models"` (weighted pool), it persists the chosen model. This is the existing behavior we want to preserve.
- `_select_weighted_model` at `daemon/services/llm_load_balancer.py:21` — fires per-spawn (line 1640 call site) and is the seam we want to prove still fires.

### Rejected alternatives — rationale

- **(c) end-to-end** — over-tests; the resolution chain is already pinned at `daemon/services/instance_lifecycle.py:1619-1668`. The new pin only needs to prove the absence-of-param path is undisturbed.
- **Single (a) unit test only** — misses the Facade-Forwarding Discipline guard: if someone adds a `model_tier` kwarg to `InstanceManager.spawn_instance` and forgets to thread it, a unit-only test would still pass.

### Risk of recommendation

- **Test flakiness if weighted pool is non-deterministic.** RNG-driven pool selection means the returned model varies; the integration test asserts membership in the pool, not a specific value. Mitigation: monkeypatch `_select_weighted_model` to return a fixed value for the integration test (deterministic, but loses the "real pool" property). Compromise: integration test uses real pool with membership assertion; unit test uses monkeypatch with a fixed return.

### Test implications

- New file: `tests/unit/services/test_spawn_intelligence_tier.py` — unit tests for the resolver + tool param.
- New file: `tests/integration/test_spawn_default_unchanged.py` — integration test for the no-`model_tier` path.
- All existing tests in `tests/unit/services/test_instance_lifecycle.py` (model override / pool / restore paths) — UNCHANGED, run green as regression.

---

## Cross-Cutting Decisions (additional)

### D7 — Facade-Forwarding Discipline

**Recommendation:** Apply the Facade-Forwarding Discipline guard from the Repo & Dev Environment Conventions blueprint: any new kwarg added to `InstanceManager.spawn_instance` must grep cleanly in `daemon/manager.py`, AND a real-dispatch integration test must assert the intended exception type.

In this feature's case:
- **No new InstanceManager kwarg** — `model_tier` resolves to `model` BEFORE the manager call (D1's resolver returns the resolved model name). The existing `model: str | None` kwarg at `daemon/services/instance_lifecycle.py:1325` carries the resolved value; the manager signature is UNCHANGED.
- Therefore the Facade-Forwarding guard is **NOT triggered** by this feature. Confirmed: the only thing that crosses the facade is the already-validated model string.

### D8 — Activation / Restart Notes

**Recommendation:** Document that the new env var `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` is **process-lifetime** — requires daemon restart to take effect.

Per the spec: *"meta.json per-agent config is a process-lifetime snapshot (restart needed) — activation notes must say so."* Env vars follow the same pattern (read at process start; no SIGHUP reload). Activation plan in plan-overview.md Phase 5 lists this explicitly.

### D9 — Test Execution Discipline

**Recommendation:** All tests run EXCLUSIVELY via `uv run python -m pytest` from worktree root (per Repo & Dev Environment Conventions blueprint).

This applies to the new test files (`test_spawn_intelligence_tier.py`, `test_spawn_default_unchanged.py`) and the updated test pin in `test_long_tool_nudge.py`. No deviation.

### D10 — Non-Goals (explicit)

The plan-overview.md Non-Goals section is the canonical list. Decisions-level summary:
- No pool changes (weights, membership) — pure additive surface.
- No per-agent-type tier defaults in v1 — global only.
- No automatic/spontaneous upgrades — parent-initiated only (D4 advisory rec).
- No system-prompt edits — discoverability via docstring + append_allowed_models + nudge (D5).
- No provider-side alias changes — coding/coding2/agentic remain opaque provider strings.

### D11 — Deferred Items (explicit)

- **Per-agent tier overrides** (mirror `caller_model_overrides`): defer until operator feedback indicates a need.
- **Multiple tier literals** (`"low"`, `"medium"`, `"auto"`): defer; v1 ships `"high"` only.
- **`spawn_councilor` tier param**: defer; council semantics differ.
- **Auto-recovery (no parent prompt required)**: explicitly NOT in scope; the parent decides.

*(A10, architect review 2026-09-14: two previously-deferred items removed from this list as ANSWERED — (1) empty-string kill-switch semantics: answered "empty/whitespace env = UNSET = default `"agentic"`" per the `_clean_env_value` house pattern; soft-disable = a model name NOT in `allowed_models` (boot WARN + per-spawn loud raise, see D13). (2) Tier→model map hot-reload: answered "boot-snapshot only" (D8/A6) — single `load_config` env read, restart-only activation, no runtime reload in v1.)*

---

## Risk Roll-Up (decisions-level)

| # | Decision | Risk | Mitigation |
|---|----------|------|------------|
| R1 | D1(a) new param | Naming lock-in for `model_tier`/`"high"` | `Literal["high"]` in v1; widen later with coordinated change |
| R2 | D1 global tier map | Per-agent flexibility deferred | Documented in Deferred Items; v2 if needed |
| R3 | D2 loud validation | Asymmetry with legacy `model=` may confuse parents | Docstring explicitly calls out asymmetry |
| R4 | D2 loud validation | Spawn blocks entirely on validation failure | ValueError message lists valid models |
| R5 | D3 spawn_instance only | Future parent needs it elsewhere | Resolver is free function; 5-line extension |
| R6 | D4 nudge text | Test pin update must coordinate | Same-commit acceptance criterion in Phase 4 |
| R7 | D4 nudge text | Rec 4 may be ignored | Soft wording matches existing recs 1-3 |
| R8 | D5 three-surface discovery | Vocabulary drift across three places | `grep -rn "model_tier"` catches drift |
| R9 | D6 regression test | Weighted pool non-determinism | Membership assertion in integration test |
| R10 | D8 restart required | Operator forgets to restart | Phase 5 activation checklist; rollout doc |

---

## Open Questions (for caller / architect)

1. **Should `model_tier="high"` be the ONLY tier in v1, or should the Literal include `"default"` for symmetry?** Spec says v1 ships one tier ("high"); symmetry with the negative path is not needed. **Recommendation: ship `"high"` only.**
2. **Should the loud `ValueError` message include the operator-overridable default ("default: 'agentic'")?** Spec implies yes (parents should know what they'd have gotten). **Recommendation: include in the message.**
3. **Should `append_allowed_models` show the high-tier block when `inject_allowed_models=False`?** Spec says "no system-prompt changes"; a new always-on block is a system-prompt change. **Recommendation: gate on `inject_allowed_models=True` (existing opt-in).**
4. **Is the rec-4 wording final, or should the architect review?** Wording is proposed, not committed; Phase 4 has architect review on the test-pin update as a natural gate.

---

## D12 — Both-Params Precedence (`model_tier` AND `model` passed together)

**Owner-ratified 2026-09-14, supersedes architecture-recommendation.md §9** (pending-decision item 1; the strict-loudness ValueError-on-conflict alternative is REJECTED).

**Decision:** when BOTH `model_tier` AND `model=` are passed to `spawn_instance`, **`model_tier` WINS** — `model=` is superseded — and the tool result carries a **visible `[NOTE]` line documenting the supersede**. **NOT a strict-ValueError.**

- Supersede notice format: `[NOTE] model='<model>' superseded by model_tier='high' (using <tier-mapped model>)`.
- Rationale (architecture-recommendation.md §4c): house precedent `caller_model_overrides` (`daemon/tools/knowledge_tools.py:722-788`) — explicit override wins, never silently, never rejected; "the newer, more specific intent wins" is least surprising for an LLM caller that just read the rec-4 nudge; a ValueError-on-conflict wastes a turn on two legitimate intents, while silent-ignore would violate the feature's own loudness philosophy — the notice is the correct middle.
- Implementation: NEW phase-2 task (phase2-plan.md task 4d branch + task 7g / Pin X) with test pins: (1) both-params → tier resolution wins (spawn proceeds on the tier-resolved model); (2) `[NOTE]` supersede line present in the tool result; (3) persisted `instance_metadata.model_override` = tier-mapped model.

## D13 — Boot WARNING + Per-Spawn Raise (tier-mapped model ∉ allowed_models)

**Owner-ratified 2026-09-14, supersedes architecture-recommendation.md §9** (pending-decision item 2).

**Decision:** when the tier-mapped model is NOT in `allowed_models`:

- **Boot time:** emit a boot-time **WARNING (NOT boot-fail)** — one WARNING line at `load_config` naming the env var, the resolved value, and the allowed list. Boot-fail is rejected: the mismatch is semantic (well-formed string, wrong list), not malformed; the fail-loud-at-boot house pattern (`_parse_bool_switch`, `daemon/config.py:2444-2463`) is for malformed switch values only (architecture-recommendation.md §4a). Implemented as phase1-plan.md task 2d.
- **Per spawn:** the loud `ValueError` stays as designed (D2) — the parent-facing contract; verbatim message adopted as phase2-plan.md task 4b.i (A2 / §2.1).

Kill-switch semantics follow: setting a NON-ALLOWED model name (NOT an empty string) = soft-disable — boot WARN + every `model_tier="high"` call raises loud (empty/whitespace env = UNSET = default `"agentic"`; see A3/A4 and the A10 note under D11).

## D14 — Tier-Path Model-Visibility Marker in the Tool Success Return

**Owner-ratified 2026-09-14** (council review follow-up; recorded here because decisions.md is the ratification ledger).

**Decision:** the tier-path SUCCESS return of the `spawn_instance` tool gains a one-line model-visibility marker — `model='agentic' (model_tier='high')` — so parents must SEE what model the child actually got (mirrors the existing fallback-notice pattern).

- Composite return spec (implementation detail in phase2-plan.md task 4e; composition site `daemon/tools/instance.py:1924-1928`, relative to `fallback_notice` at `:1908-1910`): (1) UUID prefix preserved — `Successfully spawned instance: <uuid>` byte-identical incl. `child_count_line`; (2) `[NOTE]` supersede line only on the both-params path (D12); (3) visibility line `model='<resolved>' (model_tier='high')` on EVERY tier-path success; (4) legacy `fallback_notice` suppressed on the tier path (guarded on `model_tier is None`).
- Pins: Pin X asserts startswith-id + `[NOTE]` substring + visibility line + persisted override = tier-mapped model (phase2-plan.md task 7g).

# Phase 3 — Observability (loud skip) + prefix-sweep ownership guard

**Lane:** `plane-pm-issue-tools` · base `941cb6321` · **Tier: Tier-1 blast-radius (≤30 min HARD, fail-loud); the aggregate lane diff also triggers Tier-2 (`daemon/manager.py` is a core seam)**
**Depends on:** Phase 0 (G2) · **Phase 2 (HARD)** · Phase 1 gate (i) · **Blocks:** Phase 5 (the refusal must be *visible*, else it mimics the original bug), Phase 6

> **> (G2 fold, approver iter-003, 2026-11-02 — non-blocking item 11: the header omitted the HARD Phase-2 edge — the 4th place the G2 dependency reconciliation had missed.)** Phase 3 **hard-depends on Phase 2** (`phase3-plan.md` §3): this phase's pins assert **Phase-2-produced strings** — the resolver's **typed reason** (P3-C, verbatim in the skip WARNING), the **policy-refused bucket** (P3-E), and the `manager.py`-side **P2-G / P2-H / P2-I** (the stale-row deactivation, the heal-conditionality gate, the slug-equality heal trigger) — all of which live on the Phase-2 resolver. **Phase 3 cannot be implemented, pinned, or validated without Phase 2's resolver.** The commit order follows: Phase 2's `plane.py` commit lands **first** (`plan-overview.md` §5 hot spots).

---

## 1. Objective

Two defects that made the original failure invisible and the agent's toolset non-deterministic:

1. **The silent skip.** `daemon/manager.py:3075-3087` logs at **INFO**: *"Builtin '{name}' skipped — missing environment configuration (see server definition docs)"* — naming **neither** the agents that expected the server **nor** the env vars that were missing. That is why a PM with `mcp_full_access: ["plane"]` and zero Plane tools produced no error anywhere.
2. **The prefix-sweep category collision.** `DYNAMIC_TOOL_PREFIXES = frozenset({"plane_"})` (`daemon/tools/_tool_registry.py:279-281`); the expansion loop at `daemon/tools/instance.py:499-511` populates `tool_categories["plane"]` from every runtime name carrying the prefix, with **no ownership check**. `plane_sync_project` is registered under category `"plane_sync"` (`daemon/tools/plane_sync.py:210-212`) but its **name** starts with `plane_` ⇒ when category `"plane"` is empty/absent (the MCP server did not register) the sweep sweeps `plane_sync_project` into `"plane"` ⇒ PM's `tools.allow: ["plane"]` grants it. **The same `meta.json` yields two different toolsets depending on server-registration state** — a determinism defect, not merely a permission defect.

---

## 2. File-level scope

### 2.1 MODIFIED — `daemon/mcp/builtin_servers/base.py` (layer 1 — "say why")

Add to the builtin base a classmethod pair (house precedent: `daemon/services/plane_sync_service.py:374-381` `unavailable_reason()` — a **typed reason string**, not a log phrase):

| New API | Contract |
|---------|----------|
| `unavailable_reason() -> str \| None` | `None` when available; otherwise a typed reason. **Base default: `None`** (no behaviour change for the other builtins). |
| `missing_env_vars() -> list[str]` | the **names** of the missing env vars. **Never values.** |

`daemon/mcp/builtin_servers/plane.py` overrides both: the kill-switch case (already has a one-line notice at `plane.py:301-323`) is reported as **"deliberately disabled"**, distinct from **"unexpectedly unavailable"**; the resolver refusal is reported with its typed reason from Phase 2.

### 2.2 MODIFIED — `daemon/manager.py` (layers 2 + 3)

| Site | Change |
|------|--------|
| `:3075-3087` (the skip branch) | Raise the skip log from **INFO** to **WARNING** and include: the server name, the **agents** whose `mcp_full_access` references it, and the **missing env var names**. The agent lookup is a registry query over `daemon/registry.py:411` (field), `:710`/`:771` (parse), `:1419-1457` (validation against the builtin registry). |
| `_bootstrap_builtin_servers` (`:3025`+) — after the loop | **Layer 3 — the registry-vs-row cross-check.** For every agent's `mcp_full_access` entry, if **no registered** `mcp_servers` row of that name exists, emit a WARNING naming the agent + the server. This single check covers **both** failure shapes (skipped server **and** absent row) and is exactly where the original incident was invisible. It is the natural home for a future "declared capability vs delivered capability" report. |
| the skip branch (`:3075-3087`) — **P2-G (G1 fold B2: implemented HERE)** | On a **workspace refusal**, **deactivate any pre-existing ACTIVE plane row** (mirror the `MCP_DISABLE_BUILT_IN_*` path, `daemon/manager.py:3057-3066`), so the layer-3 cross-check never reads a refused row as *registered*. Boundary: the deactivation path must **never log/echo the row's `config.headers`** (the AD2 exposure stays out of scope). Design contract: `phase2-plan.md` §2.2 (P2-G). |
| the heal sites `daemon/manager.py:312-353` / `:3115-3132` — **P2-H + P2-I (G1 fold B2: implemented HERE)** | The heal is **conditional on availability** (P2-H — a refusal defers it; a refusal boot leaves the pre-existing row's `config_schema_version` unchanged) and gains the **plane-scoped slug-equality trigger** (P2-I — persisted `headers['x-workspace-slug']` ≠ the resolved slug ⇒ heal; plane-scoped only, webfetch/context7 untouched). Phase-5's allowed-boot-first sequencing is unchanged. Design contracts: `phase2-plan.md` §2.2 (P2-H/P2-I). |

**Layer 3 caveat (must be handled, not assumed).** The cross-check must distinguish:
- *deliberately off* (`PLANE_MCP_ENABLED=false`, `MCP_DISABLE_BUILT_IN_<NAME>`) ⇒ a **debug/info** line, not a WARNING — otherwise every environment with the integration intentionally off gets a boot WARNING (the wave-1 analysis flags this risk explicitly);
- *unexpectedly unavailable* ⇒ **WARNING** with the agent + server + missing env vars.

> **Amendment P3-A (architect G0, 2026-11-02 — folded append-only): the log-ownership contract (R-1…R-5).** The architect found that the path has **five** emitters while the pre-fold plan named **two**. Ownership is now assigned explicitly, so no two emitters interpolate the same reason and no emitter invents one:
>
> | Rule | Owner | Contract |
> |------|-------|----------|
> | **R-1** | the **resolver** (`daemon/plane_workspace.py`) | **NEVER logs.** It returns typed reasons and nothing else. A resolver that logs would make the refusal reason observable at an arbitrary call site (and `is_available()` is called from 5 sites). |
> | **R-2** | `PlaneServerDefinition.is_available()` | Emits **at most one process-wide envelope notice** — label + source + effective slug, **never the reason**. One-shot pattern (no per-call logging). |
> | **R-3** | the **manager skip WARNING** (`manager.py:3075-3087`) | The **ONLY** reason interpolator, with **three distinct causes**: (i) *deliberately disabled*; (ii) *unexpectedly unavailable*; (iii) **workspace refused (policy)**. **A refusal is NOT a missing-env-var case** — it must never be rendered as one. |
> | **R-4** | the **registry-vs-row cross-check** | Emits **agent + server + pointer** (to the R-3 WARNING) — **no re-interpolation**. A **policy-refused** server stays at **WARNING**: it still costs the agent its tools, so it is not an informational event. |
> | **R-5** | the pre-existing `mcp_service.py:652-657` / `:1221-1237` **INFOs** | **Stay as they are** — they are a **different fact** (schema-warm / preload-gate detail), not a duplicate of the skip WARNING. They are **documented, not removed**. |

> **Amendment P3-B (architect G0, 2026-11-02 — folded append-only): the full emitter census — five, not two.** The pre-fold plan named only the manager skip log and the registry cross-check. The complete set on this path (all five are now assigned an owner above):
>
> | # | Emitter | Site | Level today | Owner rule |
> |---|---------|------|-------------|------------|
> | 1 | the manager skip log | `daemon/manager.py:3075-3087` | INFO | **R-3** — the only reason interpolator |
> | 2 | the registry-vs-row cross-check | `daemon/manager.py` post-bootstrap | (new) | **R-4** — pointer only |
> | 3 | the plane kill-switch one-liner | `plane.py:301-323` | (one-shot notice) | **R-2** — the envelope notice |
> | 4 | the MCP schema-warm INFO | `daemon/services/mcp_service.py:652-657` | INFO | **R-5** — stays, documented |
> | 5 | the MCP preload-gate INFO | `daemon/services/mcp_service.py:1221-1237` | INFO | **R-5** — stays, documented |
>
> **The architect's finding that underlies this:** the **bootstrap WARNING is the surface** — there is **no boot-summary construct** in the daemon, so the lane must own the log surface it creates rather than assume a summary will surface it.

> **Amendment P3-C (architect G0, 2026-11-02 — folded append-only): the typed reason goes in the skip WARNING verbatim, with three causes.** The R-3 WARNING must carry the resolver's **typed reason string verbatim** (not a paraphrase, not a re-derived phrase), and must distinguish the three causes explicitly in the message. The **P5 pin** (below) asserts: (i) the WARNING level; (ii) the server name; (iii) the **agents** whose `mcp_full_access` references it; (iv) the missing env var **names**; (v) the **typed reason verbatim** for the refusal cause; and (vi) that the *deliberately disabled* cause does **not** produce a WARNING.

> **Amendment P3-D (architect G0, 2026-11-02 — folded append-only): `POST /api/mcp-servers/test-connection` must consult the resolver.** It currently bypasses the MCP kill-switch and reports `success=true, 177 tools` while the switch is OFF (the pre-existing **D7 / AD7** defect) — the operator's natural "is Plane up?" probe therefore **lies**. This amendment folds D7 into the R-15 observability contract: the endpoint must **report the refusal instead of lying** — i.e. it must consult the resolver and surface the **typed refusal reason** (and the kill-switch state) rather than a stale tool count. The endpoint moves from "a pre-existing follow-up" to "a Phase-3 item".
> **Scope boundary:** the endpoint is `daemon/routers/mcp_servers.py:337`. This amendment changes **only** its plane-relevant reporting path (resolver consult + reason surfacing); it does **not** redesign the endpoint, and it does **not** touch `redact_secrets` (**AD2 stays out of scope** — the escalation remains on record).

> **FC4 / R15 state-(iii) disposition (G1 fold B6, 2026-11-02 — declaration route, per the architect's FC4 amendment "…or declare the measurement source + owner; record UNOBSERVABLE-while-R1").** **No session-failure counter ships in this lane.** R15 state (iii) (*dead credentials: row present + connect 401*) is **UNOBSERVABLE-as-a-counter while R1 is open** — the live/demo credential is dead pending the user's re-mint (ADJ-4), so a counter would measure a known-dead token, not the condition FC4 guards. **The observable mechanism that DOES ship:** the P3-D honest `test-connection` report (ends the D7 lie), the P5c three-cause WARNING taxonomy (cause (ii) *unexpectedly unavailable* covers connect failure), and the R-2 envelope notice. **Measurement source when a counter is later wanted:** the connect-failure evidence surfaced through the P3-D report + the resilience layer's existing failure handling. **Owner:** the R1 re-mint follow-up is user-owned (ADJ-4); counter work routes to the **API/security lane** (ADJ-3 routing — naming a concrete owner awaits leader ratification, G1 open question 3). This block discharges the §10 FC4 "declare the measurement source + owner" branch and the R15 pointer from `plan-overview.md` §6.

> **Amendment P3-E (architect G0, 2026-11-02 — folded append-only): the cross-check is pointer-only and gains a policy-refused bucket.** The registry-vs-row cross-check emits **agent + server + pointer** to the R-3 WARNING — it does **not** re-interpolate the reason (that would create a second, divergent rendering of the same fact). It must also recognise the **policy-refused** bucket: a server that is *defined* but whose row was **deactivated on a workspace refusal** (**P2-G**, implemented in this phase — G1 fold B2) is **policy-refused**, not *never registered* — and a policy-refused server **stays at WARNING**, because it still costs the agent its tools.

### 2.3 MODIFIED — `daemon/tools/instance.py` (the ownership guard)

Mirror the `mcp_` lane's existing pattern (`:466-477`) onto the dynamic-prefix loop (`:499-511`):

```python
for prefix in sorted(DYNAMIC_TOOL_PREFIXES):
    ...
    category_key = prefix.removesuffix("_")
    if tool_categories.get(category_key):
        continue
    owned_elsewhere = {name for cat, names in tool_categories.items() if cat != category_key for name in names}
    tool_categories[category_key] = sorted(
        name for name in all_tool_names
        if name.startswith(prefix) and name not in owned_elsewhere
    )
```

**The exclusion MUST be computed per `category_key`** — the `mcp_` lane excludes only `"mcp"`; the dynamic loop must exclude *the target category itself*, or every legitimately-discovered `plane_*` tool would be dropped. The category map **is** the ownership map: it is populated by `scan_tools_for_full_docs` **before** `_apply_tool_filter` runs (the production order in `create_instance_tools`).

### 2.4 MODIFIED — `daemon/registry.py` (support)

Expose a small helper to enumerate the agents referencing a given server name via `mcp_full_access` (the field at `:411`; parse at `:710`/`:771`; validation at `:1419-1457`). **Read-only**; no change to the validation semantics (NR5).

### 2.5 MODIFIED / NEW — test packs

| File | Action | Content |
|------|--------|---------|
| `tests/unit/tools/test_dynamic_toolset_expansion.py` (`:55-108`) | **AMEND** | add the collision cases (P1 a–d). The existing `_production_categories()` seam (`:84-102`) and the synthetic fixture are reused. **Also fix the fixture's drifted names**: the current `PLANE_TOOLS = {"plane_list_issues", "plane_create_issue", "plane_delete_issue"}` (`:105`) names verbs that do not exist upstream — rename to the real `*_work_item` verbs so the fixture does not teach the wrong vocabulary (W-9). |
| `tests/unit/test_plane_loud_skip_observability.py` | **NEW** | P5 (the WARNING's content + level) and P6 (the registry-vs-row cross-check). |
| `tests/unit/tools/test_plane_prefix_sweep_ownership.py` | **NEW** (or as extra cases in the file above) | P1's four cases, kept in a dedicated pack so the pin is independently runnable. |
| `tests/unit/test_plane_test_connection_report.py` | **NEW** | **P5d** — `POST /api/mcp-servers/test-connection` consults the resolver and surfaces the typed refusal + kill-switch state instead of the stale `success=true, 177 tools` report (dedicated pack, independently runnable — G1 fold W-C; the pin previously had no named pack home). |
| `tests/unit/tools/test_plane_agent_surface_census.py` | **NEW** *(G2 fold, approver iter-002 — non-blocking item 1: FC7 gets a NAMED pack.)* | **FC7's detector pack** — the per-agent resolved-toolset census, run **with the plane server REGISTERED**. See the written-out expectation below. |

> **> (G2 fold, approver iter-002, 2026-11-02 — non-blocking item 1: FC7's predicate expectation is now WRITTEN OUT, not a code location.)** The pre-fold pin pointed at `instance.py:513-520` (the empty-allow universe branch) — a **code location**, not an **expectation**. The pack's expectation, enumerated:
>
> | # | Expectation (the census asserts each) | Pass predicate |
> |---|---|---|
> | **E1** | The census runs with the **plane server REGISTERED** | the resolved universe contains the `plane_*` names for PM ⇒ **if the census resolves zero `plane_*` tools for everyone, the census is VACUOUS ⇒ FAIL** |
> | **E2** | **WRITE surface** | **no agent other than PM** resolves any `plane_*` **write** tool. The write set is the classifier's 104 verbs (the corrected census: `create_`26 + `update_`27 + `delete_`26 + `remove_`1 + `set_`2 + `manage_`14 + `transfer_`/`complete_`/`link_`/`import_`/`attach_`/`detach_`/`upload_`/`resolve_` ×1 each) — see `phase2-plan.md` §2.2.1 |
> | **E3** | **READ exposure disclosure** | the census **records** which non-PM agents resolve `plane_*` **read** tools via the empty-allow universe branch (`instance.py:513-520` — the architect §5 accepted caveat: the guard governs *where*, not *who*), and **REDs on any GROWTH beyond the disclosed set** (the disclosed set is captured at the census's first run and frozen in the pack) |
> | **E4** | **PM is the only `mcp_full_access` holder** | the census confirms `mcp_full_access: ["plane"]` appears in exactly one `agents/*/meta.json` |
> | **E5** | **consistency with the P2-K widening** | the read set the census sees for a non-full-access agent is **73** verbs and matches the corrected classifier's read set exactly (no verb appears in the census that the classifier does not classify as read) — the same arithmetic `phase2-plan.md` §2.2.1 pins |
>
> **RED legs:** grant `plane` (or `mcp_full_access: ["plane"]`) to any other agent ⇒ **E2** goes RED; widen the read-holder set beyond the disclosed form ⇒ **E3** goes RED; run the census unregistered ⇒ **E1** goes RED; desynchronise the census read set from the classifier ⇒ **E5** goes RED.

---

## 3. Dependencies

| From | Why |
|------|-----|
| Phase 0 G2 | the observability design (W-7) and the guard design (W-8) are frozen |
| **Phase 2 (HARD — upgraded by G2 fold, approver iter-001, 2026-11-02)** | **> The pre-fold row said "Phase 2 (soft) … the phases may land in either order". That was WRONG and is superseded: Phase 3's pins assert Phase-2-produced strings** — the resolver's **typed reason** is what the plane override reports as `unavailable_reason()`, the **P3-C** pin asserts that reason **verbatim** in the skip WARNING, the **P3-E** pin asserts the policy-refused bucket, and **P2-G/P2-I** (both landed in Phase 3's `manager.py`) depend on the Phase-2 resolver existing. **Phase 3 therefore HARD-depends on Phase 2.** The dependency direction is Phase 2 → Phase 3, which is also the **commit order** (`plan-overview.md` §5 hot spots: land Phase 2's `plane.py` commit first). |
| Phase 1 gate (i) | the fixture's verb names must match the re-captured inventory |
| Phase 1 gate — the **FC9 baseline** *(G2 fold B3)* | the gate also acquires `baseline_tokens` (the plane category absent); Phase 3 does not consume it, but the same artifact family carries it — recorded here for the ordering record only |

**Coupling:** `daemon/mcp/builtin_servers/plane.py` is touched by Phase 2 (E3/E4) and by Phase 3 (the `unavailable_reason` override) — **land Phase 2's `plane.py` commit first** (`plan-overview.md` §5).

---

## 4. Tier gating (per `.agents/tester/rules/ensure.md:11-28`)

**Tier-1 (DEFAULT) — blast-radius leg** over the declared blast radius: `daemon/mcp/builtin_servers/base.py`, `daemon/mcp/builtin_servers/plane.py`, `daemon/manager.py`, `daemon/tools/instance.py`, `daemon/registry.py`, **`daemon/routers/mcp_servers.py` (the P3-D target — G1 fold W-C)**, `tests/unit/tools/test_dynamic_toolset_expansion.py`, the new packs above. Budget **≤ 30 min HARD, fail-loud** (abort + report + ONE auto re-scope). Recipe body read from `.agents/tester/skills/blast-radius-leg/`.

**Tier-2 (MANDATORY, aggregate).** `daemon/manager.py` is on the mechanical core-seam list ⇒ the lane's aggregate diff triggers the whole-tree dual-side leg. Recipes: `.agents/tester/skills/whole-tree-base-proven-diff/` or `.agents/tester/skills/regression-ab-partition-run/`.

**Tier escalation is MECHANICAL, never a judgement call** (ensure.md §Gate Tiers). The promote-blocking release gate inherits the Release Gate's pre-existing judgement trigger.

**Fences:** identical to Phase 2 (§4 of `phase2-plan.md`) — dual-layer timeout, **per-variable** `POSTGRES_*` scrubs (the glob form is a silent NO-OP), `-p no:randomly`, worktree-local `.venv/bin/python`, pack-mapped execution, `--collect-only` slicing proof, FAILED-digest comparison, pre-exclusion registry.

---

## 5. Pin / test obligations

*(G1 fold B2: pins **P2-G**, **P2-H** and **P2-I** — defined in `phase2-plan.md` §5 — are **implemented and validated in THIS phase** (manager.py-site work per §2.2); their pin rows there carry the moved-home marker. This resolves the pre-fold contradiction that Phase 2 declared `daemon/manager.py` untouched while X2.11/X2.12 claimed its edits.)*

| # | Pin | Cases | Mutation |
|---|-----|-------|----------|
| **P1** | **prefix-sweep ownership guard** | (a) server **absent** ⇒ category `"plane"` must NOT contain `plane_sync_project`, and resolving PM's `allow=["plane"]` must not yield it; (b) server **registered** ⇒ `"plane"` contains every `plane_*` name and NOT `plane_sync_project`; (c) **positive control** — the worker still gets `plane_sync_project` through its `"plane_sync"` allow entry (`agents/worker/meta.json:29`); (d) the `mcp_` lane's `owned_elsewhere` behaviour is unchanged. **> (G2 fold, approver iter-002 — non-blocking item 4: the RED leg needs a fixture that is ACTUALLY populated; the pre-fold leg was vacuous.)** The pre-fold case (a) used the production `_production_categories()` seam, which **never populates `"plane_sync"`** (the category is registered by the decorator, but its tool list is empty in that seam) ⇒ the "another category owns a `plane_*` tool" precondition was **absent** ⇒ case (a) would pass **vacuously** (there was nothing to leak). **The fixture construction (mandatory):** the pack **builds its own category map** in which `"plane_sync"` holds a **synthetic `plane_*`-named tool owned by another category** — e.g. a synthetic `plane_sync_project` registered under `"plane_sync"` — alongside a `"plane"` category that is **empty** (the server-absent shape). Then: with the ownership guard **absent**, the sweep pulls the synthetic `plane_sync_project` into `"plane"` ⇒ case (a) **REDs**; with the guard present ⇒ `"plane"` stays empty and PM's `allow=["plane"]` resolves **nothing** ⇒ GREEN. **Case (b)** uses the complementary shape (`"plane"` populated with the runtime `plane_*` names + `"plane_sync"` holding the synthetic tool) and asserts `"plane"` contains every `plane_*` name and **not** the synthetic owner. **Case (c)** is the positive control on the synthetic owner (the worker still receives it through its `"plane_sync"` allow entry). | remove the ownership exclusion ⇒ (a) and (b) go RED; (c)/(d) stay GREEN. **With the synthetic fixture absent, (a) cannot fire ⇒ the leg is vacuous ⇒ the pin is INVALID** |
| **P5** | **loud-skip WARNING** | the WARNING names the server, the **agents** whose `mcp_full_access` references it, and the **missing env var names**; and it is at **WARNING** level, not INFO | demote to INFO or drop the agent/env names ⇒ the log-capture assertion goes RED |
| **P5c** *(P3-C)* | **typed reason verbatim + three causes** | the R-3 WARNING carries the resolver's **typed reason string verbatim**; the three causes (*deliberately disabled* / *unexpectedly unavailable* / *workspace refused*) are distinguishable in the message; a **refusal is never rendered as a missing-env-var case**; the *deliberately disabled* cause produces **no** WARNING | paraphrase the reason, or render a refusal as a missing-var ⇒ RED |
| **P5d** *(P3-D)* | **test-connection consults the resolver** | `POST /api/mcp-servers/test-connection` surfaces the resolver's **typed refusal reason** (and the kill-switch state) instead of reporting a stale `success=true, 177 tools` while the switch is OFF (the **D7/AD7** lie) | restore the unconditional tool-count report ⇒ RED |
| **P5e** *(P3-A/R-2)* | **one-shot envelope notice** | `is_available()` emits **at most one** process-wide envelope notice (label + source + effective slug, **never the reason**), and does **not** log per call (it has 5 call sites) | log per call, or interpolate the reason there ⇒ RED |
| **P6** | **registry-vs-row cross-check** | an agent declaring `mcp_full_access: ["plane"]` with **no registered row** ⇒ a WARNING naming the agent + the server; and an intentionally-disabled server does **not** produce a WARNING | remove the cross-check ⇒ RED; and treat "deliberately disabled" as "unexpected" ⇒ the negative control goes RED |
| **P6b** *(P3-E)* | **pointer-only + policy-refused bucket** | the cross-check emits **agent + server + pointer** and does **not** re-interpolate the reason; a server **deactivated on a workspace refusal** (P2-G) is classified *policy-refused* — not *never registered* — and stays at **WARNING** | re-interpolate the reason, or classify a policy-refused row as absent/INFO ⇒ RED |
| **P9** | **PM `deny: ["mcp"]` bypass preserved** | with the server registered, PM receives `plane_*` write verbs despite `deny: ["mcp"]` — the prefix override must keep bypassing it (the *designed* path, `plane.py:143-152`) | remove the prefix override ⇒ PM loses the plane tools ⇒ RED |
| **FC7** | **plane WRITE surface does not leak to a non-PM agent** *(G1 fold B1 — re-scoped; G2 fold NB1 — pack named + expectation enumerated)* | **The census runs with the plane server REGISTERED** (an unregistered census is vacuous — it resolves zero `plane_*` tools for everyone; the unregistered state is covered by P6/P6b). (i) **no agent other than PM resolves any `plane_*` WRITE tool** (`mcp_full_access` = PM, the only holder in the tree); (ii) the **read-exposure disclosure assertion**: the census records the non-PM *read* holders reaching the plane category via the empty-allow universe branch (`instance.py:513-520` — the architect §5 accepted caveat: the guard governs *where*, not *who*) and REDs on any **growth beyond the disclosed set**. **Load-bearing in the same release as P2-K**, which widens the read subset. **> (G2 fold NB1: the expectations are ENUMERATED in the named pack `tests/unit/tools/test_plane_agent_surface_census.py` — E1 registered-census, E2 write-surface, E3 read-disclosure, E4 sole-`mcp_full_access`-holder, E5 classifier-consistency — instead of pointing at a code location.)** | grant `plane` (or `mcp_full_access: ["plane"]`) to any other agent ⇒ the write-census leg goes RED; widen the read-holder set beyond the disclosed form ⇒ the disclosure leg goes RED; run the census unregistered ⇒ the vacuity tripwire goes RED |

**No-regression obligations verified in this phase:** NR3 (the worker keeps `plane_sync_project`), NR4 (PM's `deny: ["mcp"]` semantics unchanged — the deny strips only `mcp_`-shaped names), NR5 (`mcp_full_access` semantics unchanged), NR6 (the `mcp_` lane's guard unchanged), NR8 (no privileged-category change).

**Explicitly NOT asserted:** that PM *does* hold `plane_sync_project` — that was the **false** claim in `agents/project-manager/tools_note.md:24`, corrected in Phase 4.

---

## 6. Exit criteria

| # | Criterion |
|---|-----------|
| **X3.1** | A skipped builtin produces a **WARNING** naming the server, the referencing agents, and the missing env var **names**; an intentionally-disabled builtin does **not** (P5, P6). |
| **X3.2** | `plane_sync_project` is absent from PM's toolset in **both** server states and present in the worker's (P1 a–c). |
| **X3.3** | The `mcp_` lane's ownership guard is unchanged (P1 d) and PM still receives `plane_*` write verbs despite `deny: ["mcp"]` (P9). |
| **X3.4** | The test fixture no longer teaches the drifted `*_issue` vocabulary. |
| **X3.5** | Tier-1 leg PASS within the 30-min hard budget, fail-loud, slicing proof recorded, net-new reds = 0. |
| **X3.6** | Every pack is registered in `.agents/tester/PACKS.md`. |
| **X3.7** *(P3-A/P3-B)* | The **five** emitters are enumerated with an owner each (R-1…R-5); the resolver never logs; `is_available()` emits at most one envelope notice and never the reason; the two pre-existing `mcp_service` INFOs stay and are documented as a different fact. |
| **X3.8** *(P3-C)* | The skip WARNING carries the typed reason **verbatim** and distinguishes the **three** causes; a refusal is never rendered as a missing-env-var case; *deliberately disabled* produces no WARNING. |
| **X3.9** *(P3-D)* | `test-connection` consults the resolver and reports the refusal instead of the stale `success=true, 177 tools` (the D7/AD7 lie). The `redact_secrets` surface is **untouched** (AD2 stays out of scope). |
| **X3.10** *(P3-E)* | The cross-check is pointer-only and recognises the **policy-refused** bucket (P2-G's deactivated row) — still WARNING, not INFO. |
| **X3.11** *(FC7, G1 fold B1 — re-scoped)* | The per-agent resolved-toolset census is green **with the plane server REGISTERED**: (i) **no agent other than PM resolves any `plane_*` WRITE tool**; (ii) the **read-exposure disclosure assertion** passes — the non-PM read holders (empty-allow universe branch, `instance.py:513-520`, the §5 accepted caveat) are recorded and any growth beyond the disclosed set REDs. Armed **in the same release** as P2-K's widened read subset. |
| **X3.12** *(P2-G, G1 fold B2)* | The stale-row deactivation on a workspace refusal is implemented in this phase's manager.py scope and pinned (P2-G): the layer-3 cross-check never reads a refused row as *registered*; the deactivation path never surfaces `config.headers` (AD2 boundary). |
| **X3.13** *(P2-H/P2-I, G1 fold B2)* | The heal is conditional on availability (a refusal boot leaves the pre-existing row's `config_schema_version` **unchanged**) and the plane-scoped slug-equality trigger heals a permitted-slug change — both implemented here, both pinned. |

---

## 7. Fold register — architect G0 amendments mapped to Phase 3

**Fold date:** 2026-11-02 · **Source:** `architecture-recommendation.md` §7 (Phase 3 block) + §3 (log ownership) + §4 (FC7) + §6 (R15) · **Mode:** append-only.

| Amendment | Substance | Where folded in this file |
|-----------|-----------|---------------------------|
| **P3-A** | the log-ownership rules **R-1…R-5** (resolver never logs; `is_available` = one envelope notice; the skip WARNING is the ONLY reason interpolator; the cross-check is pointer-only; the pre-existing INFOs stay) | §2.2 (the P3-A block) + §5 pin **P5e** + §6 X3.7 |
| **P3-B** | the full **five**-emitter census (the pre-fold plan named only two) | §2.2 (the P3-B block — the emitter table) + §6 X3.7 |
| **P3-C** | the typed reason goes into the skip WARNING **verbatim**, with **three** causes (deliberately disabled / unexpectedly unavailable / workspace refused); a refusal is **not** a missing-env-var case | §2.2 (the P3-C block) + §5 pin **P5c** + §6 X3.8 |
| **P3-D** | `POST /api/mcp-servers/test-connection` consults the resolver and **reports the refusal instead of lying** (folds the pre-existing D7/AD7 defect into the R-15 contract) | §2.2 (the P3-D block, incl. the AD2 non-widening boundary) + §5 pin **P5d** + §6 X3.9 |
| **P3-E** | the cross-check is **pointer-only** and gains a **policy-refused bucket** (a P2-G-deactivated row is policy-refused, not never-registered; it stays WARNING) | §2.2 (the P3-E block) + §5 pin **P6b** + §6 X3.10 |
| **FC7** *(§4, new)* | the plane surface must not leak to a non-PM agent — detector: per-agent resolved-toolset census + the pin "no agent other than PM resolves any `plane_*` MCP tool" | §5 pin **FC7** + §6 X3.11 — **load-bearing in the same release as P2-K** (which widens the read subset) |
| **G1 (reviewer council 2026-11-02)** | **B1**: FC7 re-scoped — write-tools-only ban + read-exposure disclosure assertion + registered-census requirement (the as-worded all-tools pin was RED-at-HEAD forever via the `instance.py:513-520` empty-allow branch and had a vacuity trap). **B2**: P2-G + P2-H/P2-I implementation home = THIS phase (§2.2 rows; X3.12/X3.13; pins validated here). **B6**: FC4/R15 state-(iii) disposition block — declaration route, no counter ships (§2.2). **W-C**: `daemon/routers/mcp_servers.py` added to the §4 blast radius; the P5d pack named (`tests/unit/test_plane_test_connection_report.py`, §2.5) | §2.2 / §2.5 / §4 / §5 / §6 |

**Phase 3 carries 5 of the architect's §7 amendment IDs** (P3-A…P3-E) **plus FC7**, a newly adopted flip condition that the architect made load-bearing against Phase 2's P2-K.

---

## G2 fold (approver iter-001, 2026-11-02) — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 001 **REJECTED** (3 blockers + 9 non-blocking). **Architecture NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB2** | **FOLDED** — the pre-fold "Phase 2 (soft)" row is **upgraded to a HARD dependency** (the pins assert Phase-2-produced strings: the resolver's typed reason, the policy-refused bucket, and `manager.py`'s P2-G/P2-I). The Phase-4 → Phase-3 row in `phase4-plan.md` is **withdrawn** (there is no such dependency). One truth, reconciled across all three tables. | §3 (the G2 fold NB2 block) + `plan-overview.md` §4/§5 |

---

## G2 fold (approver iter-002, 2026-11-02) — FINAL FOLD — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 002 **REJECTED** (4 blockers + 10/13 non-blocking). **Architecture / D-1 / ADJ-1 / phase structure NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB1** | **FOLDED** — FC7 gets a **named pack** (`tests/unit/tools/test_plane_agent_surface_census.py`) and a **written-out expectation** (E1 registered-census / E2 write-surface / E3 read-disclosure / E4 sole-`mcp_full_access`-holder / E5 classifier-consistency) instead of a code location. | §2.5 (the pack + the E1–E5 block) + §5 (the **FC7** row) |
| **NB4** | **FOLDED** — **P1's RED leg** now specifies a **mandatory synthetic fixture** (a `plane_sync`-owned `plane_*` tool alongside an empty `"plane"` category), because the production `_production_categories()` seam never populates `"plane_sync"` ⇒ the pre-fold case (a) could not fire. | §5 (the **P1** row) |

---

## G2 fold (approver iter-003, 2026-11-02) — FINAL FOLD — item disposition

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 003 **REJECTED** (2 blockers + 17 mechanical notes); **approver cap reached ⇒ the leader proceeds to implementation; there is NO further approver round.** **Architecture / D-1 / ADJ-1 / the phase structure / the 7-phase decomposition are NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered or repurposed.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB11** | **FOLDED** — the header now states the **HARD Phase-2 edge** (Phase 3's pins assert Phase-2-produced strings: the typed reason, the policy-refused bucket, and the manager.py-side P2-G/P2-H/P2-I) — the 4th place the G2 dependency reconciliation had missed. | the header (the "Depends on" line + the note) |

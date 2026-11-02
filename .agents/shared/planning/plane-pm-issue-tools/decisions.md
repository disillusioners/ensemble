# decisions.md — `plane-pm-issue-tools` — decision log (bound leans + wave-1 recommendations + open adjudications)

**Lane:** `plane-pm-issue-tools` · branch `feature/plane-pm-issue-tools` · base `941cb6321` (= `origin/latest` at lane start)
**Status:** PLAN DELIVERED — awaiting the **architect adjudication gate (Phase 0 / G0)**. Nothing here is implemented.

This log has three parts:

- **§1 — Leader leans (BOUND, verbatim).** Reproduced from `investigation-digest.md` §3. These are the dispatcher's own rulings. The architect may overrule with evidence (lean #1 says so explicitly); everything else is binding on this lane.
- **§2 — Wave-1 recommendations ADOPTED.** The technical-analysis worker's conclusions that this plan freezes.
- **§3 — OPEN ADJUDICATIONS (ADJ-1…ADJ-5).** Each carries a **default** (what happens if nobody adjudicates), the alternatives, and **what flips it**. The defaults are the plan's working assumptions; an adjudication may change them without re-planning the lane.

**Numbering discipline.** §1 leans keep the digest's `1…8` numbering. §2 uses `W-1…W-n`. §3 uses `ADJ-1…ADJ-5`. All are referenced by ID from `plan-overview.md` and the phase files.

---

## §1 — Leader leans (BOUND — reproduced VERBATIM from `investigation-digest.md` §3)

> **1.** Path A (register the remote server via per-env config) is the PRIMARY lean — designed path (builtin descriptor + prefix override + mcp_full_access exist precisely for it), zero new key material on live, smaller shipped surface. Architect MUST adjudicate A vs B vs hybrid with the 5-axis trade-off matrix and a write-verb reality check against the actual tools/list inventory. If the remote surface turns out write-poor for work items (e.g. no create/update work-item verbs), Path B becomes primary.
>
> **2.** Task 2 enforcement: fail-closed typed REFUSAL on non-live when slug is wrong OR unset (no auto-override). Sandbox counts as non-live => e2e-only too. No kill-switch for the guard; if architect insists on one, default-ON and it must not disable resolution, only the refusal.
>
> **3.** Loud-skip observability IN SCOPE: when a builtin server referenced by any agent's mcp_full_access is unavailable/skipped at boot, log a prominent WARNING naming the agent(s) and the missing env vars. Also fold the registry validation gap into the same observability fix if cheap.
>
> **4.** Prefix-sweep ownership guard IN SCOPE (instance.py:498-511, mirror the mcp_ lane's owned_elsewhere pattern at :466-477) + regression pin covering the plane_sync_project/plane collision in BOTH server-registered and server-absent states.
>
> **5.** Charter reconciliation IN SCOPE: rewrite the false/drifted prose (tools_note.md:24 plane_sync claim; verb vocabulary vs actual tools/list inventory — verb-agnostic phrasing with the live inventory noted is acceptable; exact names must match reality). Any agents/project-manager/*.md edit => prompt-artifact re-stamp 161/161 + SEALED_SHA_BASELINES re-pin for workflow.md IN THE SAME COMMIT.
>
> **6.** No privileged-category change expected — unless architect argues otherwise, in which case the full same-commit pin ceremony applies.
>
> **7.** Durable repo-doc persistence of the Task 2 directive (extend the canonical runbook/doc surface the lane finds; .env.example already carries doctrine prose) + project-level metadata/critical-note persistence is ALREADY DONE leader-side.
>
> **8.** Task 3 verification must be a plan phase: after implementation, on an env where verification is possible (dev with e2e + MCP armed), dispatch PM to create the preserved DRAFT issue in e2e, and prove the enforcement refuses/blocks the nea direction on non-live. Include the exact repro/verification procedure as a gate.

### Lean → plan mapping (traceability)

| Lean | Satisfied by | Phase |
|------|--------------|-------|
| 1 | Path A frozen as the **recommended** path; the 5-axis matrix + write-verb reality check + **FC1–FC6** are embedded in the plan; the architect adjudication is **G0, the first gate after plan delivery** | 0 |
| 2 | E1–E4 fail-closed typed refusal on non-live (wrong **or** unset); sandbox ⇒ `e2e`-only; **no kill-switch** for the guard | 2 |
| 3 | Loud-skip WARNING naming agents + missing env vars, **plus** the registry-vs-row cross-check ("if cheap" ⇒ it is cheap: one registry query) | 3 |
| 4 | Ownership guard on the dynamic-prefix sweep + the 4-case regression pin (registered / absent / positive control / `mcp_` non-regression) | 3 |
| 5 | Charter reconciliation incl. the daemon's own docstring; prompt-artifact re-stamp + SEALED re-pin **same commit** | 4 |
| 6 | No privileged-category change; the ceremony is listed as a conditional sub-task if the architect forces one | 2 (R11) |
| 7 | New canonical runbook `docs/runbooks/plane-workspace-scoping.md` + `.env.example:185-190` doctrine extension; leader-side metadata persistence noted as already done | 4 |
| 8 | Phase 5 is a **full plan phase** with the exact repro/verification procedure | 5 |

---

## §2 — Wave-1 recommendations ADOPTED (frozen into the plan)

| ID | Recommendation | Rationale (anchor) | Rejected alternatives |
|----|----------------|--------------------|------------------------|
| **W-1** | **Path A** is the implementation path: register the remote Plane MCP server per environment; **zero shipped code for the tool surface**. | 5-axis matrix — A wins Complexity / Risk / Cost outright; its two losing axes (Scalability, Maintainability) are *tunable* (Option A′; charter phrasing + re-capture gate). Path B must first *invent* a Plane REST contract for work items / cycles / comments with **zero in-repo precedent** (`PlaneHttpClient` has exactly 4 project methods: `:563/:606/:652/:677`) **and** mint a second credential class (`PLANE_API_KEY`, absent on live **and** demo). `technical-analysis.md` §Trade-offs. | Path B (first-party REST tools); Hybrid (A+B — worst on both axes: A's prompt surface **plus** B's daemon-side REST traffic). |
| **W-2** | The **leader's Path-B trigger is DISCHARGED** — the remote surface is **not** write-poor: work-item CRUD (8 verbs), comments (5), cycles (10), and `create_work_item(is_draft=…)`; **116 of 177** tools are write-capable. | `tools-list-inventory.md` §3/§3e; the 2026-09-20 capture was simply not representative (`investigation-digest.md` §8). | — |
| **W-2a** *(G2 fold, approver iter-001, 2026-11-02 — non-blocking item 7: the census figure inside W-2 is WITHDRAWN.)* | **The "116 of 177 write-capable" figure inside W-2 is withdrawn** and replaced by the **three-number disclosure** — **104/73** by the inventory's stated method, and **48 read / 82 write / 47 unclassified** by the daemon's actual classifier (`plane.py:242-259` + `resilience.py:638-687`). **The Path-B DISCHARGE IS UNAFFECTED** — it rests on the **verb coverage** (work-item CRUD + 5 comment + 10 cycle + `create_work_item(is_draft=…)`), which is confirmed, **not** on the write-percentage. **W-2 is otherwise unchanged** (it is a frozen wave-1 entry; this row records the supersession rather than editing it in place). | `architecture-recommendation.md` §1.1 (the census is wrong in three incompatible ways) + §11 **W-13**; the amendment itself is **P1-B** (`phase1-plan.md` §3) | — || **W-3** | **D-1 — the lane-local live determination.** Prod access requires **explicit** `ENSEMBLE_SELF_ENV=live` **OR** (no explicit marker **AND** the daemon's **own** `POSTGRES_DB == ensemble_prod`). An **auto-derived** `live` is **not** trusted; `None` (unclassified) ⇒ non-live ⇒ fail-closed. | The shared oracle is **fail-OPEN in the guard's dangerous direction**: `_auto_derive_env` reads the **sibling** live install's `.env` and returns `"live"` for the dev checkout **before** the dev-shape signal runs (`daemon/tools/upgrade_tools.py:505-555` vs `:556-558`) — I re-verified this code structure; the runtime result is **UNVERIFIED-BY-YOU** (wave-1 executed it). | (a) Fix the shared oracle in-lane (ADJ-2 / R-B); (b) trust the shared ladder (REJECTED — it would grant dev the prod workspace and would never fire the "unset ⇒ refuse" rule on dev). |
| **W-4** | The resolver is a **dedicated module** (`daemon/plane_workspace.py`), **not** a raising config validator. Borrow the *shape* of `LiveViewsRootConfig` (`daemon/config.py:3178`, validator `:3262-3271`) — **not** its failure mode. | Plane is an **optional** integration; a raising `BaseSettings` validator would abort the **whole daemon boot** because a third-party workspace is unset — strictly worse than skipping Plane. `technical-analysis.md` §On the LiveViewsRootConfig precedent. | A `PlaneWorkspaceConfig` section that raises at boot (REJECTED); a raising validator on an existing section (REJECTED). |
| **W-5** | **E1–E4 adopted; E5 (tool layer) rejected.** E1 = one resolver; E2 = REST chokepoints (`_rest_base_url` / `is_available` / client construction / `_headers`); E3 = MCP chokepoints (`is_available` gains a resolution gate ⇒ **no row ⇒ no tools**; `get_base_config` emits the **resolved** slug); E4 = `schema_version` `"2"`→`"3"` so the heal re-materialises `headers` from the current env. | Closes P1 (non-prod REST + `nea`), P2 (MCP + slug **unset** ⇒ account-default prod), P3 (non-prod MCP + `nea`), P4 (stale persisted row). E4's mechanism was **re-verified** — `_heal_config_preserving_env` (`daemon/manager.py:312-353`) rebuilds the template config and merges **only** the `env` axis, so `headers` are template-derived (`base.py:229-250` → `get_base_config` → current env). `investigation-digest.md` §6. | Connect-time header validation (`daemon/mcp/config.py:245-277` / `connection_manager.py:173`) — **optional belt-and-braces**, not required; E5 (tool layer) — the workspace is never a tool parameter, so a tool-layer check duplicates E1–E3 and will drift. |
| **W-6** | **No kill-switch for the guard.** If the architect insists on one, it must be **default-ON** and gate **only the refusal**, never the resolution — the effective slug is always computed and always logged, so the diagnostic surface survives the switch. | Leader lean #2 verbatim. | A resolution-disabling switch (REJECTED — it re-opens the pollution class the user closed). |
| **W-7** | **Loud-skip = three layers.** (1) `unavailable_reason()` / `missing_env_vars()` on the builtin base + a plane override (mirrors `plane_sync_service.unavailable_reason()` `:374-381`); (2) the skip log raised to **WARNING** naming the server, the **agents** whose `mcp_full_access` references it, and the missing env vars; (3) a post-bootstrap **registry-vs-row** cross-check (declared capability vs delivered capability). | `manager.py:3075-3087` is INFO and names neither agents nor env vars — that is why the original incident was invisible. `mcp_full_access` is validated against the builtin **registry** (a definition exists) but **never against a row** (`daemon/registry.py:1419-1457`) ⇒ a defined-but-unavailable server passes validation. | Layer 3 skipped ("if cheap" ⇒ it is one registry query — cheap); a connect-failure WARNING as the *only* mechanism (REJECTED — it does not catch the "never registered" case). |
| **W-8** | **Prefix-sweep ownership guard**: compute `owned_elsewhere` **per `category_key`** (the union of all categories **except** the one being populated) and exclude those names from the dynamic-prefix sweep. | The `mcp_` lane already carries this pattern (`daemon/tools/instance.py:466-477`); the dynamic loop (`:499-511`) does not. `plane_sync_project` is registered under `"plane_sync"` (`daemon/tools/plane_sync.py:210-212`) but its **name** starts with `plane_` ⇒ when category `"plane"` is empty/absent it is swept into `"plane"` ⇒ PM's `allow: ["plane"]` grants it. **Same `meta.json`, two different toolsets** — a determinism defect, not just a permission defect. | Excluding the target category itself (REJECTED — it would drop every legitimately-discovered `plane_*` tool); a per-prefix allow-list (REJECTED — heavier, and the category map already *is* the ownership map). |
| **W-9** | **Charter reconciliation scope = the agent's four prompt files AND the daemon's own docstrings.** Verb-agnostic English prose is acceptable; **exact tool names must match reality**; the live inventory is noted. | `tools-list-inventory.md` §2: **zero `*_issue` verbs exist**; the entity is `work_item`; upstream's own `serverInfo.instructions` say "an epic is a work item whose type is named 'Epic'". The daemon repeats the drift at `daemon/mcp/builtin_servers/plane.py:143-152` and `daemon/tools/plane_tools.py:14`. | Rewriting only `agents/project-manager/*.md` (REJECTED — the daemon docstring is the doc a future implementer reads); inventing a `plane_issue_*` alias layer (REJECTED — a new shipped surface, out of scope under Path A). |
| **W-10** | **Path A's two losing axes are handled by measurement, not by forking.** Ship the full `"plane"` category and **measure** PM's tool-selection accuracy / task success rate; the remedy, if needed, is **A′** — narrow PM's grant to an explicit allow-list of the verb names the charter uses (a `meta.json`-only change; the filter matches literal names inside a populated dynamic category). | `technical-analysis.md` §Option A′. The upstream catalog is server-level and moves (server `v3.2.0` today); the catalog is **workspace-agnostic** (`tools/list` returns byte-identical 177-name lists for `e2e` and `nea` — `tools-list-inventory.md` §6). | Pre-narrowing by default (ADJ-5 — it forecloses an option before the measurement exists); forking the surface (REJECTED — a one-way door). |
| **W-11** | **Flip conditions FC1–FC6 are embedded** in the plan as the re-open criteria for Path B / hybrid. | `technical-analysis.md` §Flip conditions. FC1 is now a **mechanical detector** (the gate-(i) re-capture), not a judgement call. | Treating the decision as irreversible (REJECTED — Path A's reversibility is very high: unsetting the env vars restores today's state). |
| **W-12** | **Dev is the only environment where Task 3 can execute.** | The live and demo `PLANE_MCP_API_KEY` are the **same** token and both **dead** (401 `invalid_token`); the dev token differs and works; dev is the only env pointing at `e2e` **and** the only one carrying a REST key. `tools-list-inventory.md` §5/§7 (UNVERIFIED-BY-YOU). | Running Task 3 on demo or live (IMPOSSIBLE — dead keys); running it against a mock (REJECTED — it would not prove the wiring). |
| **W-13 (G0 ruling):** | "Path A CONFIRMED; Path-B discharge stands for the surface with two riders (discovery-verb params captured at re-capture; charter reconciliation a hard prerequisite for Task 3); the 116/177 census replaced by the three-number disclosure (104/73 stated-method, 82/48/47 daemon classifier); A′ = third option (full at ship + numeric FC9 ceiling + A′ default-on-breach, keep-set pre-derived ~28); FC7–FC11 adopted; R18–R21 added. Source: architect G0 2026-11-02, architecture-recommendation.md §1/§4/§6." | Architect G0 adjudication (source: `architecture-recommendation.md` §11 fold entry, appended VERBATIM per instruction). | — (a G0 ruling, not a planner choice) |
| **W-14 (G0 ruling):** | "D-1 CONFIRMED lane-local with predicate hardening (opt-out short-circuit; garbage ⇒ non-live; empty=absent; DB-name input POSTGRES_DB → own ensemble.json pg.db; hard bans on upgrade_tools import and non-own file reads); own-DB arm KEPT (self-consistent semantics; ambient-poison residual disclosed, root fix owned by the env-poison commission). Source: architecture-recommendation.md §2." | Architect G0 adjudication (source: `architecture-recommendation.md` §11 fold entry, appended VERBATIM per instruction). | — (a G0 ruling, not a planner choice) |

---

## §3 — OPEN ADJUDICATIONS

Each ADJ carries: the **question**, the **default** (what this plan assumes if nobody adjudicates before implementation), the **alternatives**, and **what flips it** (the specific evidence or ruling that changes the default). The defaults are safe to implement against — each one is either byte-identical to today's behaviour or the more conservative of the two options.

---

### ADJ-1 — Strict live (`nea`-only) vs **live-exempt**

**Question.** On a live environment, is the permitted slug set `{nea}` (strict — `e2e` is refused) or "whatever is configured, with `nea` allowed" (live-exempt)?

> **✅ ADJUDICATED — CLOSED (G0, 2026-11-02).** Outcome: **LIVE-EXEMPT CONFIRMED** (leader-ruled; the architect found no safety contradiction and verified live byte-identity). The strict variant is **not** adopted. The ADJ-1×E4 divergence is closed a different way — by the **plane-scoped slug-equality heal trigger** (amendment **P2-I**), not by tightening the policy. **Pointer:** `architecture-recommendation.md` §9 (binding rulings reconfirmed) + §11 fold entry; the §11 entry is reproduced VERBATIM in **§5** below. **The "Default" paragraph below is retained unchanged as the historical record of what this plan assumed before adjudication** — the adjudicated outcome coincides with that default.

**Default: LIVE-EXEMPT.** Leader lean #2 says "live exempt ⇒ byte-identical"; the live install carries `ENSEMBLE_SELF_ENV=live` + `PLANE_MCP_WORKSPACE_SLUG=nea` + no `PLANE_API_KEY`, so live-exempt is **byte-identical to today** (SC3 / NR1). The strict variant is a **one-line policy flip** in the resolver (add `e2e` to the live-refusal set).

**Alternatives.**

- **Strict (recommended by the wave-1 analysis, Open Question 1).** Byte-identical to today's live behaviour too (the live slug *is* `nea`), and it closes the **mirror-image hazard** — prod data written into the *test* workspace — which the user's directive does not cover.
- **Live-exempt (the default).** Zero behavioural delta on live; permits `e2e` on live if an operator sets it.

**What flips it.** (a) The leader/directive owner says the directive should be **bidirectional** (prod must not touch `e2e`); or (b) the architect's review finds a live-scoped write path where an `e2e` slug on live would corrupt test-workspace expectations; or (c) the user prefers a strictly-typed policy with no live exemption at all.

**Cost of the flip.** One line in `daemon/plane_workspace.py` + one added case in the gate-(iv) pack. **No shipped-surface change elsewhere.**

> **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 5: the live PERMITTED SET is now stated once, consistently.)** The approver found three places disagreeing about what live permits (`decisions.md` ADJ-1, `phase2-plan.md` §2.1 policy table, `phase4-plan.md` P4-A). **The adjudicated rule, stated once here and mirrored in all three:**
>
> | Environment | Permitted slug | Refusal |
> |---|---|---|
> | **live** (explicit `live`, or own db `ensemble_prod` with no marker) | **ANY configured slug** — `nea`, `e2e`, or a third (the rule is about the **environment**, not a slug allow-list); **unset ⇒ account-default** (byte-identical to today) | **none** — the strict-variant string is **never emitted** |
> | **non-live** (dev / demo / sandbox / unclassified) | **`e2e` only** | every other slug — **including `your-workspace-slug`** — is refused with the typed reason |
>
> **Consequences folded:** (i) `phase4-plan.md` P4-A's claim that `your-workspace-slug` is refused "**everywhere**" is corrected (it is refused on **non-live**); (ii) the `.env.example` sample must be **COMMENTED** (an active `e2e` default would silently target the test workspace on a fresh install, **including live**); (iii) the **missing pin** — *live + a third slug ⇒ ALLOWED* — is added to **P2** and **P3** (`phase2-plan.md` §5).

**Constraint in force either way.** The *default* policy table (from `technical-analysis.md`) already lists the live refusal reason `"workspace 'e2e' is not permitted in the production environment"` — that string exists because the analysis drafted the strict variant. **Under the live-exempt default, that reason must NOT be emitted for the live branch** — the implementer must reconcile the two artifacts (the analysis's policy table row for live vs the ADJ-1 default) before the gate-(iv) pack is written. **This is a named, tracked reconciliation item, not a silent choice.**

> **G1 fold B3 (2026-11-02) — the reconciliation is now PINNED, not just tracked.** The policy table's live row (`phase2-plan.md` §2.1) carries the strict string marked **HISTORICAL (never emitted)**; the live+`e2e` ⇒ **ALLOWED** case + the strict-variant-string-never-emitted assertion were added to pins **P2** and **P3** (`phase2-plan.md` §5, `plan-overview.md` §11) and to the Phase-6 boot pin **P24** (`phase6-plan.md` §7). A strict-variant re-introduction now goes RED at three layers.

---

### ADJ-2 — D-1 lane-local (R-A) vs fixing the shared env oracle (R-B)

**Question.** Is the Plane guard's environment determination **lane-local** (`daemon/plane_workspace.py` implements its own live predicate), or does the lane fix the shared resolver `_auto_derive_env` (`daemon/tools/upgrade_tools.py:440-561`) and call it?

> **✅ ADJUDICATED — CLOSED (G0, 2026-11-02).** Outcome: **LANE-LOCAL D-1 CONFIRMED**, with **predicate hardening** folded in as amendments **P2-A** (opt-out short-circuit; garbage ⇒ non-live + WARNING; empty = absent; exact non-normalizing `VALID_ENVS` match) and **P2-B** (canonical DB-name input `POSTGRES_DB` → the daemon's **own** `ensemble.json` `pg.db`; hard bans on importing `daemon.tools.upgrade_tools` and on every filesystem read beyond that one own-config file — enforced by a grep pin + a zero-file-access test). The **own-DB arm is KEPT** (its semantics are self-consistent: if the daemon's own main DB resolves to `ensemble_prod`, the process genuinely serves prod). **One residual is disclosed:** the bare-source-mode ambient-poison case — the root fix belongs to the env-poison commission, not to this guard. The **AD5 shared-oracle follow-up stands** (plan scope O3). **Pointer:** `architecture-recommendation.md` §2 + §9 + §11; the §11 entry is reproduced VERBATIM in **§5** below. **The "Default" paragraph below is retained unchanged as the historical record of what this plan assumed before adjudication** — the adjudicated outcome coincides with that default.

**Default: LANE-LOCAL (R-A).** The shared resolver is **security-relevant for the 3-factor promote ceremony** and is consumed far outside this lane; changing its blast radius is a different order of magnitude. D-1 costs a small duplicate predicate and **zero** risk to the promote gate.

**Alternatives.**

- **R-B (single root fix).** Fix `_auto_derive_env` so a repo checkout derives `dev`, then have the Plane resolver call the shared ladder. Cleaner long-term; **larger blast radius** and its own gates; and the Plane guard's fail-open semantics would then depend on a resolver shared with unrelated security paths.
- **Hybrid (R-C).** Lane-local D-1 **plus** a narrow, separately-gated fix to the shared oracle in the same release. *Rejected as the default:* it couples this lane's promote ceremony to a shared-resolver change.

**What flips it.** (a) The architect rules the single-root fix is correct and this lane should carry it (then the analysis must be **re-cut** — the blast radius and the gate plan change materially); or (b) the shared resolver is fixed **first** by another lane, in which case D-1 may be **retired** in favour of calling the corrected ladder (a Phase-2 simplification, decided at implement time); or (c) the promote-gate owner objects to a duplicated predicate.

**Recorded regardless.** AD5 (`technical-analysis.md` §Adjacent defects) — the shared oracle **remains wrong** after this lane, for every consumer other than Plane. It stays an **open follow-up** (plan scope O3), regardless of which ADJ-2 branch is taken.

---

### ADJ-3 — AD2 (header redaction) in-lane or follow-up

**Question.** `GET /api/mcp-servers` returns `mcp_servers.config.headers` **unredacted** — `redact_secrets` scrubs only the `env` sub-dict and the `url` userinfo (`daemon/routers/mcp_servers.py:185-253`, whose docstring at `:196-215` enumerates exactly those two surfaces). The Plane bearer therefore rides in a GET response.

> **✅ ADJUDICATED — CLOSED (G0, 2026-11-02).** Outcome: **OUT-OF-SCOPE STANDS** (routed follow-up), **with the escalation on record**. Both architect analysts independently re-flagged the exposure as **pre-existing** — the lane neither creates nor fixes it. The one-file redaction follow-up stays **high-priority**. **Pointer:** `architecture-recommendation.md` §6 (R12/R13 row) + §9 + §11; the §11 entry is reproduced VERBATIM in **§5** below. **The "Default" paragraph below is retained unchanged as the historical record of what this plan assumed before adjudication** — the adjudicated outcome coincides with that default. **Note the interaction with the Phase-3 work:** the stale-row deactivation on a workspace refusal (**P2-G**) touches this same row — it must **not** widen the exposure (the deactivation path must not log or echo the header).

**Default: OUT OF SCOPE — follow-up.** Per the leader's default and the lane's budget (the enforcement work consumes the fix-cycle budget). Recorded as plan scope **O1**. Owner *(G1 fold (OQ3, leader-ruled), 2026-11-02)*: **leader-tracked follow-up queue via project critical note 18bcc502 (risk/high, created 2026-11-02T05:38Z); target lane: MCP-gateway/security hardening commission** — supersedes the interim "API/security lane" naming.

**Severity escalation — recorded verbatim as a plan obligation.** The wave-1 analysis states it plainly: *"Recommend: do NOT wait — this is a live credential in a GET response."* The plan therefore records: **a live Plane MCP credential is currently readable through `GET /api/mcp-servers`.** Two consequences the dispatcher must accept explicitly: (i) the exposure exists **today**, before this lane ships anything; (ii) this lane's Phase 2/3 work does **not** create it and does **not** fix it. Any decision to keep it out of scope is a decision to leave a **live credential** readable through a public read surface, and it must be made with that fact in view — not by default silence.

**Alternatives.**

- **In-lane, one-file fix** (the wave-1 analysis's recommendation): extend `redact_secrets` to scrub `headers` (bearer-shaped values) — small, but it touches a **public read surface**, so it needs its own review and a redacted-shape pin.
- **Follow-up lane** (the default).

**What flips it.** (a) The leader or the security owner rules it in-lane; (b) the architect's review treats a live credential in a GET response as a **promote blocker** (then it becomes a Phase-6 blocker, not a Phase-2 task); (c) the KMS/headers path (AD3) is scheduled first, making the redaction moot.

**Interim mitigation that IS in scope.** Phase 3's observability work must **not** log the bearer while naming the missing env vars (the WARNING names env var **names**, never values), and the Phase-4 runbook must state that the operator probe (`POST /api/mcp-servers/test-connection`) is unreliable while the kill-switch is OFF (AD7/D7).

---

### ADJ-4 — Credential re-mint ownership + whether **one** credential may serve both lanes

**Question.** (i) Who re-mints the expired live/demo `PLANE_MCP_API_KEY`? (ii) May a **single** credential serve both the `nea` and the `e2e` workspaces?

> **⏳ ADJUDICATED-PARTIAL — REMAINS OPEN / USER-OWNED (G0, 2026-11-02).** Outcome: **ADJ-4 remains open and user-owned** — the architect recorded it as *"the disclosed live-efficacy precondition"* and did **not** close it. Per §9: *"ADJ-4 user-owned, carried as the disclosed live-efficacy precondition."* **Leader ruling incorporated and NOT re-opened:** the re-mint is **user-owned and non-blocking** for this lane (the staged artifact is correct without it; only *live functional efficacy* waits on it). **Gate (ii) `list_projects` authorization PROCEEDS** per the leader — so the "one credential, both lanes" question (ii) is now answerable at Phase 1 via the authorised read-only probe, and the answer is recorded in the Phase-1 gate note (ADJ-4(ii) ⇒ closed *empirically* or recorded as still-open). **Pointer:** `architecture-recommendation.md` §9 + §11; the §11 entry is reproduced VERBATIM in **§5** below. **The "Default" paragraphs below are retained unchanged as the historical record of what this plan assumed before adjudication.**

**Default: (i) USER-OWNED.** There is no automated rotation path for a Plane MCP token; the re-mint is an operator action in the Plane UI. Recorded as plan scope **O7** and as the **promote-efficacy precondition** (plan §12 step 10). It is **not** a lane blocker: the staged artifact is correct without it; only *live functional efficacy* waits on it.

> **> (G2 fold, approver iter-003, 2026-11-02 — non-blocking item 17: ADJ-4(ii) is CONFIRMED still OPEN and USER-OWNED; it is NOT closed by any of the folds.)** The iteration-002 fold **dropped** the `nea`-side read from the dev probe (dev→nea traffic is forbidden by the lane's fence), so the "may one credential serve both lanes?" question can **no longer be answered empirically from dev** — and it never could: the live/demo token is dead (401), and whether the *dev* credential reaches `nea`'s **data** is precisely what is unknown. **Disposition: ADJ-4(ii) remains OPEN, USER-OWNED, and disclosed** — it is answered **on the live host by the user, after the credential re-mint** (ADJ-4(i)). **The gate-(ii) criterion that referenced it was re-pointed** (`phase1-plan.md` §4 criterion (b) + exit criterion X1.9) so that no artifact in the set claims it is closed. **This is the third consecutive fold in which ADJ-4 has been carried as a disclosed precondition — it is the lane's standing live-efficacy precondition, not a fold residue.** **Default: (ii) PROVISIONALLY YES, UNVERIFIED.** The dev context proves it in one environment: the dev `PLANE_MCP_API_KEY` is identical to the dev `PLANE_API_KEY` and serves **both** the REST and the MCP lane. But the wave-1 evidence also records that `tools/list` is **workspace-agnostic** — it returns a byte-identical 177-name list for `e2e` and `nea` — so **a successful listing proves nothing about a workspace's data reachability**. The question "does the same credential reach `nea`'s **data**?" is therefore **unanswered**, and answering it requires an explicitly authorised read-only MCP verb (`list_projects`).

**Alternatives.**

- **One credential per workspace** (strict isolation) — requires two minted tokens for live once the live lane is armed.
- **One credential for both** (as the dev context demonstrates) — fewer secrets to manage, but it widens the blast radius of a single token to both workspaces.
- **Separate MCP tokens per workspace** (Plane-side scoping) — the strongest isolation, but it needs Plane-side scoping support that the daemon's header model (`Authorization: Bearer` + `x-workspace-slug`) may not expose.

**What flips it.** Gate (ii) — the **e2e MCP data-reachability probe** — plus the operator authorisation to run one read-only MCP verb. Specifically: if `list_projects` on `e2e` returns the two e2e projects (HTTP 200), the non-live ⇒ `e2e` pin is satisfiable **and** the "one credential, both lanes" answer is informed by the `nea` leg of the same probe. **Gate (ii) is a HARD prerequisite for shipping the non-live ⇒ `e2e` pin** (`technical-analysis.md` §Gate (ii) failure action).

**Explicit operator authorisation required.** The commission's hard rule forbade any MCP verb other than `tools/list`. The `list_projects` probe is therefore **not** authorised today and must be requested before Phase 1 proceeds past G4.

---

### ADJ-5 — Full 177-tool category vs **A′** pre-narrowed grant

**Question.** Does PM receive the **whole** `plane` category (177 runtime-discovered tools), or a **pre-narrowed** explicit allow-list of the verb names PM's charter uses (Option A′)?

> **✅ ADJUDICATED — CLOSED, AMENDED to the THIRD OPTION (G0, 2026-11-02).** Outcome: the architect **rejected ADJ-5's binary** ("full-now vs narrowed-now") and ruled a **third option**: **full category at ship + mandatory numeric baselines + a ceiling at which A′ becomes the DEFAULT outcome of Phase 5, not a judgement call.** Concretely: Phase 5 records per-turn prompt tokens for PM, the tool-call error rate over a stated N of real turns, and the mis-selected-verb count (amendment **P5-C**); **FC9** defines the ceiling (absolute tokens/turn + % growth vs the pre-Plane baseline) and makes **A′ the default outcome on breach**; the **A′ keep-set is pre-derived now** (~28 of 177 — the charter's reconciled verb vocabulary: work-item CRUD + comments + cycles + assignee/manage + the discovery/typing reads; pruning the customer / release / project-admin / module / milestone / initiative / page / property families and the out-of-scope deletes) so an A′ flip needs **no new analysis**. A′ mechanics are **CONFIRMED** (literal names inside a populated dynamic category; empirically executed) and A′ **incidentally removes the R7 leak** — but A′ converts upstream renames into **silent capability loss**, hence **FC8 is mandatory iff A′ is adopted** (minimum-toolset assertion + the gate-(i) drift detector). Ceremony note: `meta.json` is **not** in the 161-file prompt-artifact baseline (verified) — but an A′ flip forces a charter edit, so the re-stamp + SEALED re-pin rides the A′ commit (R8 unchanged). **Pointer:** `architecture-recommendation.md` §1.5 + §4 + §9 + §11; the §11 entry is reproduced VERBATIM in **§5** below. **The "Default" paragraph below is retained unchanged as the historical record of what this plan assumed before adjudication** — the adjudicated outcome **supersedes** it on the "who decides" question: the decision is now made **by measurement against FC9**, not by judgement.

**Default: FULL CATEGORY at ship time; A′ decided at implementation with the architect, triggered by measurement.** The technical analysis recommends shipping the full category and **measuring** PM's tool-selection accuracy / task success rate, because A′ forecloses an option before the measurement exists.

**Alternatives.**

- **Full category (default).** Maximum capability immediately; the prompt/tool-choice cost is real but unmeasured.
- **A′ (narrowed allow-list).** A **`meta.json`-only** change — the filter matches literal tool names inside a populated dynamic category, so no code ships. Smaller prompt, better tool-selection accuracy; but it must be **maintained** as the upstream catalog moves, and it forecloses capability PM might legitimately need (e.g. `plane_create_work_item_type`, `plane_manage_cycle_work_items`).
- **Hybrid by task** — an A′ grant plus a documented escalation path. *Rejected as the default:* it adds a policy surface with no owner.

**What flips it.** **FC3** — *"Measured PM tool-selection accuracy or task success rate degrades materially with the 177-tool surface **and** A′ does not recover it"* — plus FC2 (upstream renames a PM-critical verb twice in one release cycle with no alias) and FC1 (a charter-critical verb absent at the gate-(i) re-capture). FC1 and FC2 re-open **Path B**, not merely A′.

**A′ must not be pre-applied.** If the architect rules A′ at G0, the narrowing list must be **derived from the gate-(i) re-capture** (Phase 1), not from the current inventory — otherwise the grant is written against a catalog snapshot that may already be stale. Sequencing: **G0 (adjudication) → G3 (re-capture) → A′ decision**.

**Measurement obligation (ships with the full-category default).** Phase 5 must record PM's tool-selection behaviour against the 177-tool surface — tool-call error rate, mis-selected-verb count, and whether the charter's named verbs were actually invoked — so FC3 has a **baseline** to be evaluated against later. A′ without a baseline is unfalsifiable.

---

## §4 — Decisions that are NOT open (bound by evidence, not by preference)

| # | Decision | Why it is closed |
|---|----------|-----------------|
| C1 | **Path B is not the primary path.** | The leader's own trigger is discharged by the captured inventory (W-2). Re-opening requires FC1–FC6, not a preference. |
| C2 | **The tool surface ships zero new daemon code.** | Path A (W-1). The shipped surface for the *tool surface* is config; the shipped code in this lane is the **enforcement**, the **observability**, the **sweep guard**, and the **charter** — all common to A, B, and hybrid. |
| C3 | **The guard gets no kill-switch.** | Leader lean #2 (W-6). A refusal-only, default-ON switch is the *only* admissible variant, and none is currently proposed. |
| C4 | **The workspace is never a tool parameter.** | E5 rejected (W-5). The workspace is a **structural env fact**, shared by the REST and MCP lanes. |
| C5 | **No DB schema change.** | No new table, no new `.sql`, no `SQLModel` change, no `config.yaml` change ⇒ the migration delta is **EMPTY** ⇒ the stage-time rollback adjudication is the delta-empty-legitimate case of the STANDING RULE. |
| C6 | **The shell scrubs are not removed.** | NR2 — defence in depth; `boot_subprocess_sweep.py:150-152` precedent (do not trust `ENSEMBLE_SELF_ENV` alone). |
| C7 | **The lane never promotes.** | Plan §12 step 9 — the user's 3-factor nonce ceremony owns the flip. |
| C8 | **The three wave-1 artifacts are not modified by this plan.** | Commission constraint. They are the evidence base; the plan **links** them and records its own re-anchoring separately. |

---

## §5 — Architect adjudication G0, 2026-11-02 — APPROVED-WITH-AMENDMENTS (source: architecture-recommendation.md §11)

> **Provenance:** Architect adjudication G0, 2026-11-02 — APPROVED-WITH-AMENDMENTS (source: architecture-recommendation.md §11).
> **Fold discipline:** the three entries below are reproduced **VERBATIM** from `architecture-recommendation.md` §11 (lines 205–207), including their original `§2 append` / `§3 close` routing instructions. **Nothing here is renumbered, reworded, or summarised.** Their *disposition* is recorded in §6 below (which entries were folded where) so a reviewer can trace §11 → the phase plans without gaps.
> **Reading order for the reviewer council + fresh approver:** `decisions.md` (this file) → `architecture-recommendation.md` (the G0 record; **read-only, never modified**) → `plan-overview.md` → `phase0-plan.md` … `phase6-plan.md`.

### §11 fold entries (VERBATIM)

- §2 append **W-13 (G0 ruling):** "Path A CONFIRMED; Path-B discharge stands for the surface with two riders (discovery-verb params captured at re-capture; charter reconciliation a hard prerequisite for Task 3); the 116/177 census replaced by the three-number disclosure (104/73 stated-method, 82/48/47 daemon classifier); A′ = third option (full at ship + numeric FC9 ceiling + A′ default-on-breach, keep-set pre-derived ~28); FC7–FC11 adopted; R18–R21 added. Source: architect G0 2026-11-02, architecture-recommendation.md §1/§4/§6."
- §2 append **W-14 (G0 ruling):** "D-1 CONFIRMED lane-local with predicate hardening (opt-out short-circuit; garbage ⇒ non-live; empty=absent; DB-name input POSTGRES_DB → own ensemble.json pg.db; hard bans on upgrade_tools import and non-own file reads); own-DB arm KEPT (self-consistent semantics; ambient-poison residual disclosed, root fix owned by the env-poison commission). Source: architecture-recommendation.md §2."
- §3 close **ADJ-1** (live-exempt CONFIRMED — leader-ruled, no contradiction found; E4 divergence closed by the slug-equality heal trigger instead), **ADJ-2** (lane-local), **ADJ-3** (out-of-scope stands, escalation on record), **ADJ-5** (amended to the third option). **ADJ-4** remains open/user-owned (disclosed precondition).

### §6 — Disposition of the §11 entries (fold trace)

| §11 entry | Routed by §11 to | Folded into | Status |
|-----------|-----------------|-------------|--------|
| **W-13** | §2 append | **`decisions.md` §2** (appended after W-12; the W- sequence continues unbroken) + **plan-overview.md** §3/§6/§10/§11 | ✅ folded |
| **W-14** | §2 append | **`decisions.md` §2** (appended after W-13) + **`phase2-plan.md`** §2.1 (the D-1 predicate hardening) + **plan-overview.md** §6/§11 | ✅ folded |
| **ADJ-1 close** | §3 close | **`decisions.md` §3 / ADJ-1** — marked ADJUDICATED (outcome + pointer). The "Default" text is **retained unchanged** as the historical record | ✅ folded (not deleted) |
| **ADJ-2 close** | §3 close | **`decisions.md` §3 / ADJ-2** — marked ADJUDICATED (outcome + pointer; P2-A/P2-B hardening recorded) | ✅ folded (not deleted) |
| **ADJ-3 close** | §3 close | **`decisions.md` §3 / ADJ-3** — marked ADJUDICATED (outcome + pointer; escalation retained) | ✅ folded (not deleted) |
| **ADJ-4** | §3 — *remains open / user-owned* | **`decisions.md` §3 / ADJ-4** — marked ADJUDICATED-PARTIAL, still OPEN; leader ruling (user-owned, non-blocking) + gate-(ii) authorisation recorded | ⏳ **OPEN by design** |
| **ADJ-5 close** | §3 close | **`decisions.md` §3 / ADJ-5** — marked ADJUDICATED, AMENDED to the third option; the numeric-ceiling mechanism is carried in **`phase5-plan.md`** (P5-C) and **`plan-overview.md`** §10 (FC9) | ✅ folded |

### §7 — Amendment-count reconciliation (architect §7 → this fold)

The architect's §7 header and §0 row 7 state **"30 consolidated amendments"**. A mechanical enumeration of the amendment IDs **present in the architect's own §7 text** yields **27**:

| Phase block in §7 | IDs | Count |
|---|---|---|
| Phase 1 | `P1-A`, `P1-B` | 2 |
| Phase 2 | `P2-A` … `P2-M` (A,B,C,D,E,F,G,H,I,J,K,L,M) | 13 |
| Phase 3 | `P3-A` … `P3-E` (A,B,C,D,E) | 5 |
| Phase 4 | `P4-A`, `P4-B` | 2 |
| Phase 5 | `P5-A`, `P5-B`, `P5-C` | 3 |
| Phase 6 | `P6-A` | 1 |
| Blast radius | `BR-A` | 1 |
| **Total distinct amendment IDs in §7** | | **27** |

**All 27 are folded** (see the per-phase fold tables in each `phaseN-plan.md`). The 3-item gap against the stated "30" is a **count discrepancy in the architect's summary line**, not 3 unfolded amendments: a mechanical grep of `architecture-recommendation.md` for the amendment-ID pattern yields exactly the 27 IDs above, and **no 28th/29th/30th amendment ID appears anywhere in the document** (verified by regex sweep of the whole file). The residue is therefore **reported, not silently absorbed** — see the fold report.

**Sequences NOT counted in the 27** (they are §4 flip conditions and §6 risks, not §7 amendments, but they are folded too): **FC7, FC8, FC9, FC10, FC11** (5 new flip conditions, §4) and **R18, R19, R20, R21** (4 new risks, §6). All are carried into `plan-overview.md` §10 and §6 respectively, and FC11 is implemented as the **P2-K coverage pin** inside `phase2-plan.md`.

---

## §8 — G1 reviewer-council fold register (2026-11-02)

**Gate:** G1 (plan council) · **Body:** reviewer-convened governor council `plane-pm-g1-council` (instance `87bbdef3-a046-4af2-9db9-218d542d89b5`) · **Councilors:** 2 (councilor-planreview-agentic `150617db`, councilor-planreview-coding `32d71af7`), skill `plan-review`, both read-only in this worktree · **Council verdict:** APPROVE-WITH-ADVISORIES (2/2, normal confidence) · **Reviewer verdict after fold:** APPROVE-WITH-ADVISORIES (all blocking-grade findings folded; one item escalated, below).

**Adjudicated open questions (reviewer mandate):**
1. **P2-G/P2-I implementation home → Phase 3** (governor recommendation adopted): one manager.py-touching phase; resolves the RED-leg sequencing hazard; A-h dissolves. P2-H's manager-side gate moves with them for the same reason.
2. **FC4/R15 state-(iii) → declaration route** (no counter ships): disposition block + honest R1(b)/FC4/R15 wording; measurement source + owner recorded.
3. **FC6/ADJ-3 owner naming → ESCALATED to the leader — RESOLVED (G1 fold (OQ3, leader-ruled), 2026-11-02):** owner = "leader-tracked follow-up queue via project critical note 18bcc502 (risk/high, created 2026-11-02T05:38Z); target lane: MCP-gateway/security hardening commission".

**Folded (B = blocking-grade, W = should-fold, A = advisory):**

| ID | Finding (anchor at fold time) | Disposition |
|----|-------------------------------|-------------|
| B1 | FC7 pin structurally unsatisfiable + vacuity trap (`phase3-plan.md:139`, `plan-overview.md:301`) | **Folded** — re-scoped: registered-census requirement + WRITE-tools-only ban + read-exposure disclosure assertion (phase3 §5/X3.11; overview §10/§11) |
| B2 | P2-G/P2-I (and P2-H's manager leg) had no implementation home matching the declared phase scope | **Folded** — Phase 3 home: phase3 §2.2 rows, X3.12/X3.13, §5 note; phase2 §2.2/§3/§5/§6 annotations; overview hot-spot row + R21 |
| B3 | ADJ-1 live+`e2e` ⇒ allowed case unpinned; retained strict string live-looking | **Folded** — P2/P3/P24 pin legs + strict-string-never-emitted; policy-table row marked HISTORICAL; ADJ-1 note here (§3) |
| B4 | P10 vacuous (no test reads the shell-scrub content; `tests/test_deploy.sh` carries zero PLANE/e2e assertions — grep-verified by council AND reviewer) | **Folded** — static-content assertion pack `tests/packs/plane_shell_scrub_content.sh` specified (overview §11 P10); NR2 proof reference fixed |
| B5 | Pin-ID collision (phase5 P19 = FC9 vs phase6 P19) + G13 under-enumeration | **Folded** — phase6 staged pins renumbered P24–P27 (P23 unchanged); G13 enumeration completed + runner-class split; master index added (overview §11) |
| B6 | FC4/R15 state-(iii) disposition unlanded; R1(b) claim unbacked | **Folded** — declaration-route disposition block (phase3 §2.2); FC4/R1/R15 rows amended (overview) |
| B7 | BR-A missed a 7th breaking file — `tests/unit/test_plane_identifier.py:365-370` constructs `PlaneHttpClient(..., workspace_slug="nea", ...)` (verified on disk by council AND reviewer) | **Folded** — BR-A item 7 + blast radius + X2.15 + register row (phase2) |
| W-A | X0.5 prematurely closed (G0/G1 conflation) | **Folded** — X0.5 re-marked: closed on THIS G1 fold (phase0 §7) |
| W-B | X5.4/P15 superseded-but-live wording; X5.7 "six" vs seven artifacts | **Folded** — P15 marked SUPERSEDED by P15a; X5.4 restated; X5.7 "seven" (phase5) |
| W-C | `mcp_servers.py:337` absent from Phase-3 Tier-1 blast radius; P5d pack homeless | **Folded** — file added to §4; `tests/unit/test_plane_test_connection_report.py` named (phase3 §2.5) |
| A-a | 177-name catalog fixture must be checked-in, sha-tied to FC10 | **Folded** (phase2 §2.2.1) |
| A-b | P2-H had no mutation leg | **Folded** — new P2-H pin row (phase2 §5) |
| A-c | Phase-2 Tier-2 justification mis-attributed | **Folded** (phase2 :3) |
| A-d | Root-cause sentence: `PLANE_MCP_ENABLED` defaults ON (optional kill-switch, not co-required) | **Folded** (overview §1) |
| A-e | phase1 duplicate §8 + phantom §9 refs | **Folded** — fold register renumbered §9; refs corrected; cross-refs updated (phase0/overview) |
| A-f | SC2 conflated refusal with missing-env | **Folded** (overview §7 SC2) |
| A-g | G13 mixed behavioral vs evidence pins | **Folded** — runner-class split (phase6 G13) |
| A-h | P2-G RED-leg sequencing hazard | **Dissolved** by B2 (Phase 3 home) — no edit needed |
| A-i | P2-K/P3-D exceed literal commission (G0-adjudicated, disclosed) | **No action** — councilors decline to reject; existing escalation line stands (BR-A remediation needing production change = finding/escalate) |

**Untouched by design:** `architecture-recommendation.md` (the G0 record — read-only per §5) and the three wave-1 artifacts (C8). All G1 edits carry explicit `G1 fold` markers at their sites.

**Escalated (not folded):**
1. **OQ3 / FC6 — name the ADJ-3 owner — RESOLVED (G1 fold (OQ3, leader-ruled), 2026-11-02):** owner named in the ADJ-3 close = "leader-tracked follow-up queue via project critical note 18bcc502 (risk/high, created 2026-11-02T05:38Z); target lane: MCP-gateway/security hardening commission". No G2 qualifier — G2 proceeds on the plan as folded.
2. **G2 approver visibility — ADJUDICATED leader-side as DECLINED per approver-independence (G1 fold (OQ3, leader-ruled), 2026-11-02):** G2 proceeds on the plan as folded; no approver-visibility instruction carries forward. (Historical record of the reviewer's hint: the fresh approver should verify this fold landed coherently — the B2 phase-home move and the B5 renumber were named as the two structural changes a reviewer could trip over.)

**Residual disclosures (recorded, non-blocking):** the fold-count discipline follows the lane's own rule (count against source IDs); the architect's "30 vs 27" discrepancy remains reported-not-absorbed (§7 above). B1's re-scope records the read-exposure caveat as a **disclosure assertion** — the *who*-narrowing remains a future lane per the architect's §5 caveat, unchanged by this fold.

---

## G2 fold (approver iter-003, 2026-11-02) — FINAL FOLD — item disposition

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 003 **REJECTED** (2 blockers + 17 mechanical notes); **approver cap reached ⇒ the leader proceeds to implementation; there is NO further approver round.** **Architecture / D-1 / ADJ-1 / the phase structure / the 7-phase decomposition are NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered or repurposed.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB17** | **FOLDED** — **ADJ-4(ii) is CONFIRMED still OPEN and USER-OWNED** (it cannot be answered empirically from dev since the `nea`-side read was dropped); the gate-(ii) criterion that referenced it was re-pointed so that **no artifact claims it is closed**. | §3 (the ADJ-4 status block) + `phase1-plan.md` §4 (criterion (b)) + §8 (**X1.9**) |

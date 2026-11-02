# plan-overview.md — `plane-pm-issue-tools` — Plane issue-tool wiring + env-scoped workspace enforcement

**Lane:** `plane-pm-issue-tools` · branch `feature/plane-pm-issue-tools` · worktree `/home/nea/ensemble-src-wt-plane-pm`
**Base:** `941cb63215f0fe0948f4d48f93821aec4c169527` (= `origin/latest` at lane start; re-verify at implement time)
**Status:** PLAN DELIVERED → **ARCHITECT G0 ADJUDICATED (2026-11-02): APPROVED-WITH-AMENDMENTS** → **G1 REVIEWER FOLDED** → **G2 APPROVER ITER-001 REJECTED (folded)** → **G2 APPROVER ITER-002 REJECTED (folded — FINAL FOLD)**. The phase structure was **CONFIRMED and stands**; 27 architect amendments + 3 + 4 blockers + 23 non-blocking notes folded append-only. Awaiting **approver iteration 003**.
**Posture:** this lane **stages** a release; it **never promotes**. The promote is the user's 3-factor nonce ceremony.

> ## ⚠️ WHAT TASK 1 ACTUALLY DELIVERS — READ THIS FIRST
>
> **> (G2 fold, approver iter-002, 2026-11-02 — non-blocking item 13: the commissioner's most load-bearing expectation, disclosed where a reader cannot miss it.)**
>
> **Task 1 (PM receives Plane issue tools) ships CONFIG + OBSERVABILITY — NOT SHIPPED CODE for the tool surface.**
>
> | | |
> |---|---|
> | **What ships for Task 1** | the **per-environment configuration** (`PLANE_MCP_URL` + `PLANE_MCP_API_KEY` + `PLANE_MCP_WORKSPACE_SLUG` per env) that makes the builtin Plane MCP row register and the ~177 runtime-discovered `plane_*` tools reach PM — **zero new daemon code for the tool surface** (Path A) — **plus** the observability that makes the state visible (loud-skip WARNING, registry-vs-row cross-check, the typed refusal reasons) |
> | **What ships for Task 2** | the **daemon-side structural enforcement** (the resolver + the REST/MCP chokepoints + the stale-row deactivation + the slug-equality heal trigger) — this **is** shipped code, and it is the durable part of the lane |
> | **Live/demo efficacy** | **DEPENDS ENTIRELY ON A USER-OWNED CREDENTIAL RE-MINT.** The live and demo `PLANE_MCP_API_KEY` are the **same expired token** (HTTP 401 `invalid_token`) and the gate is **presence-only** ⇒ after the promote, live would carry a `plane` row whose **sessions all fail 401**. The lane's contribution is to make that failure **visible** (Phase 3) instead of silent. **The re-mint is the user's action** (ADJ-4 / O7 / R1 / assumption A2) — it is **not** a lane task and **not** a lane blocker for staging. |
> | **What the lane CAN arm** | **only `dev`.** The main checkout `/home/nea/ensemble-src` is the only environment with a working Plane MCP credential **and** the `e2e` slug **and** a REST key. The **fenced worktree** (the Phase-5 execution env) carries **zero** `PLANE_*` keys and must be **seeded** from the main checkout first (`phase5-plan.md` §4 Step 0). |
> | **Consequence for the commissioning user's expectation** | after the promote, **dev has working Plane tools**; **live/demo have a configured-but-unusable Plane lane until the user re-mints**. This is stated here, in the header, because it is the single most load-bearing expectation in the whole commission. |

---

## 1. Objective

Deliver three coupled outcomes:

1. **Wiring** — make the Plane issue/work-item CRUD surface actually reach the `project-manager` (PM) agent. Today PM declares `mcp_full_access: ["plane"]` (`agents/project-manager/meta.json:11-13`) and `tools.allow` contains `"plane"` (`:50`), yet **zero** `plane_*` tools exist at runtime. Root cause: the builtin Plane MCP server is a *remote* descriptor whose availability gate (`daemon/mcp/builtin_servers/plane.py:263-298`) requires `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` (the `PLANE_MCP_ENABLED` kill-switch **defaults ON** — `plane.py:269-276`, `.env.example:445` — it is an optional disable, not a co-required gate; G1 fold A-d); when the gate fails, `_bootstrap_builtin_servers` **skips creating the row** at INFO level (`daemon/manager.py:3075-3087`), so the preload never sees a server named `plane`.
2. **Enforcement (USER DIRECTIVE, Discord 2026-11-02)** — make the Plane workspace **structurally** env-scoped: workspace `nea` is production/live ONLY; dev + demo MUST target the `e2e` test workspace for ALL Plane operations (REST *and* MCP). Enforcement lives in daemon code, not convention.
3. **Verification** — prove (2) end-to-end: PM creates the preserved DRAFT "Guidelines system" work item into the env-correct workspace on **dev/e2e**, and the enforcement is proven to **refuse** the `nea` direction on non-live.

**Recommended implementation path (subject to Phase-0 adjudication): Path A** — register the remote Plane MCP server per environment; config-only for the tool surface. Rationale, matrix, and flip conditions: [`technical-analysis.md`](technical-analysis.md) §Trade-offs; ADJ-5 below.

---

## 2. Scope

### In scope

| # | Item | Anchor / source |
|---|------|-----------------|
| S1 | **Path A registration** — per-env `PLANE_MCP_URL` / `PLANE_MCP_API_KEY` / `PLANE_MCP_WORKSPACE_SLUG` so the builtin row materialises and the ~177 runtime-discovered `plane_*` tools reach PM | `plane.py:263-298`, `manager.py:3089-3132` |
| S2 | **Env-scoped workspace resolver + refusal** (E1) with the **D-1 tightening** (prod access requires an explicit `live` marker OR the daemon's own `POSTGRES_DB=ensemble_prod`) | `technical-analysis.md` §Workspace enforcement design |
| S3 | **REST chokepoints** (E2) — `_rest_base_url`, `is_available`, client construction, `_headers` | `daemon/clients/plane_http_client.py:296-308`, `:326-362`, `:364-374`, `:388-398` |
| S4 | **MCP chokepoints** (E3) — `is_available` gains a resolution gate (refusal ⇒ no row ⇒ no tools); `get_base_config` emits the **resolved** slug | `plane.py:263-298`, `:325-354` |
| S5 | **Stale-row heal** (E4) — bump plane `schema_version` `"2"` → `"3"`; the heal rebuilds `headers` from the current env | `plane.py:128-140`; `manager.py:312-353`; `base.py:229-250` |
| S6 | **Loud-skip observability** — WARNING naming the server, the **agents** whose `mcp_full_access` references it, and the missing env vars; plus a registry-vs-row cross-check | `manager.py:3075-3087`; `registry.py:411/:710/:771/:1419-1457` |
| S7 | **Prefix-sweep ownership guard** — `plane_sync_project` must never leak into category `"plane"` | `daemon/tools/instance.py:499-511` vs the `mcp_` guard at `:466-477`; `plane_sync.py:210-212` |
| S8 | **Charter reconciliation** — PM's four prompt files + the daemon's own docstrings name verbs that **do not exist** (entity is `work_item`, zero `*_issue` verbs) | `tools-list-inventory.md` §2; `agents/project-manager/{tools_note,rule,workflow,soul}.md`; `plane.py:143-152`; `daemon/tools/plane_tools.py:14` |
| S9 | **Durable repo-doc persistence** of the Task-2 directive (new canonical runbook + `.env.example` doctrine extension) | `.env.example:185-190`; house precedent `docs/runbooks/project-skill-source.md` |
| S10 | **Task-3 verification** — dev/e2e PM draft-work-item creation + a proof that non-live refuses `nea` | Phase 5 |
| S11 | **Test packs** + PACKS.md registration + prompt-artifact re-stamp (161/161) + `SEALED_SHA_BASELINES` re-pin **in the same commit** | `agents/.prompt-artifacts.sha256`; `tests/test_chart_image_delivery_e2e.py:1490` |

### Out of scope (recorded, routed — NOT fixed here)

| # | Item | Anchor | Route to |
|---|------|--------|-----------|
| O1 | **AD2 — plaintext bearer readable via `GET /api/mcp-servers`** (`redact_secrets` scrubs `env` + `url` userinfo only, **not** `headers`) | `daemon/routers/mcp_servers.py:185-253` (docstring `:196-215`) | API/security lane — **severity escalated in ADJ-3** |
| O2 | **AD1/AD3 — no KMS binding path for header-borne secrets** | `daemon/manager.py:3089-3097`; `daemon/tools/infra.py:1012/:1022/:1527+` | secrets / kms lane |
| O3 | **AD5 — shared env auto-derive mislabels a repo checkout as `live`** | `daemon/tools/upgrade_tools.py:440-561` (sibling read at `:505-556`) | own commission (D-1 contains it for Plane) |
| O4 | **AD6 (D6) — custom non-UUID `instance_id` ⇒ `mcp_tool_names=[]`** | 2026-09-20 report §D6 | follow-up — Task 3 MUST use the default UUID id path |
| O5 | **AD7 (D7) — `POST /api/mcp-servers/test-connection` bypasses the kill-switch** | `daemon/routers/mcp_servers.py:337` | follow-up — degrades R1's operator remediation |
| O6 | **AD4 — `daemon/tools/mcp_tools.py:17` documents a nonexistent `call_mcp_tool`** | `daemon/tools/mcp_tools.py:17` | folded into the Phase-4 docs pass (doc-only, zero risk) |
| O7 | **Live/demo Plane credential re-mint** | probe evidence in `tools-list-inventory.md` §5 | **USER-OWNED** — precondition for live efficacy, not a lane blocker (ADJ-4) |
| O8 | **Path B** (first-party REST issue tools) | `technical-analysis.md` §Alternatives | only via a **flip condition** (FC1–FC6) — ADJ-5 |

---

## 3. Commission requirements → coverage map

| Commission requirement | Covered by | Phase |
|---|---|---|
| 1. PM actually receives Plane issue CRUD tools | S1 (+ S6/S7 so the state is *observable* and *deterministic*) | 1, 3 |
| **> (G2 fold, approver iter-003 — non-blocking item 7: the coverage map mis-attributed the CONFIG DELIVERY to Phases 1+3. Corrected.)** **Per-environment config delivery is Phase 5 Step 0** (the arming/seeding procedure — `phase5-plan.md` §4 Step 0), **not** Phases 1+3: Phase 1 is the **probe** (read-only), Phase 3 is the **observability** (the state becomes visible), and **neither delivers a config**. **The live/demo config is OPERATOR-OWNED and PRE-EXISTING** (the live `.env` already carries `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` + `nea`; the demo carries the same two plus `e2e` — the live token being expired is R1/ADJ-4) ⇒ **the lane configures nothing on live or demo**; the only config the lane itself performs is the **dev worktree seeding** in Phase 5 Step 0. | **Phase 5 Step 0** (dev seeding) + operator (live/demo) | 5 |
| 2. Structural, env-scoped workspace enforcement (`nea` = live only; dev/demo ⇒ `e2e`) | S2, S3, S4, S5 (D-1) | 2 |
| 3. PM creates the preserved DRAFT "Guidelines system" issue into the env-correct workspace; enforcement proven to refuse `nea` on non-live | S10 | 5 |
| Phase gating per `ensure.md` Gate Tiers | §10 gate map | 0–6 |
| Mutation-verified pins (prefix-sweep guard ×2 states; E1–E4 ×4 + D-1 case; loud-skip WARNING; charter/baseline coupling) | §11 | 2, 3, 4 |
| No-regression obligations | §8 | 2, 3, 6 |
| Config/env contract per environment | §9 | 1, 2 |
| Verification gates for unverified items sequenced before enforcement ships | §10 + Phase 1 | 1 |
| Staged-release path (W9 re-derivation, rollback adjudication, prompt-baseline + SEALED re-pin, PACKS.md, NO promote) | §12 + Phase 6 | 6 |
| Durable repo-doc persistence (lean #7) | S9 | 4 |
| Charter reconciliation (lean #5) incl. `plane.py:143-152` | S8 | 4 |
| Leader lean #1 — architect adjudication is the FIRST gate after plan delivery | §10 gate G0 | 0 |
| **> G0: classifier reconciliation (P2-K)** — the daemon's read/write classifier is written against a non-existent vocabulary; 47 verbs are neither read nor write | §2.2.1 of `phase2-plan.md` | 2 |
| **> G0: the gate chain is 4 links, all keyed on `is_available()`** | P2-F | 2 |
| **> G0: the log-ownership contract R-1…R-5 over 5 emitters** | P3-A/P3-B | 3 |
| **> G0: `test-connection` must consult the resolver** (folds D7/AD7) | P3-D | 3 |
| **> G0: the behavioural-rollback disclosure** | P6-A | 6 |
| **> G0: the A′/FC9 numeric-ceiling ruling** (full at ship + baselines + A′ default-on-breach) | P5-C, FC8/FC9 | 5 |

---

## 4. Phase map

| Phase | Name | Objective | Depends on | Gate tier |
|-------|------|-----------|------------|-----------|
| **0** | [Adjudication + plan gates](phase0-plan.md) | Architect rules A vs B vs hybrid on the 5-axis matrix + write-verb reality check; reviewer council; fresh approver. Plan frozen before any code. | — | plan gates (no code) |
| **1** | [Verification gates (i)/(ii)/(iii)](phase1-plan.md) | Re-capture `tools/list`; prove MCP credential reachability of `e2e` data; fenced read of the live `mcp_servers` row + scratch heal drill. | 0 | read-only probes |
| **2** | [Enforcement: E1–E4 + D-1 resolver](phase2-plan.md) | New `daemon/plane_workspace.py`; REST + MCP chokepoints; schema bump; env-classification proof pack (gate iv). | 0, 1 | Tier-1 + Tier-2 (core seam) |
| **3** | [Observability + prefix-sweep guard](phase3-plan.md) | Loud-skip WARNING naming agents + env vars; registry-vs-row cross-check; ownership guard for the dynamic prefix sweep + 4 regression cases. | 0, **2** *(G2 fold: Phase 2 upgraded from soft to HARD — the pins assert Phase-2-produced strings)* | Tier-1 |
| **4** | [Charter reconciliation + doc persistence](phase4-plan.md) | PM's 4 prompt files + 2 daemon docstrings reconciled to the real `*_work_item` verbs; `.env.example` extension; new `docs/runbooks/plane-workspace-scoping.md`; prompt-artifact re-stamp + SEALED re-pin **same commit**. | 0, 1 (vocabulary depends on the re-capture) | Tier-0/Tier-1 (docs + prompt artifacts) |
| **5** | [Task-3 end-to-end verification](phase5-plan.md) | On dev/e2e with MCP armed: PM creates the DRAFT "Guidelines system" work item into `e2e`; enforcement refuses the `nea` direction on non-live; evidence artifacts archived. | 2, 3, 4 | functional (Tier-2 legs) |
| **6** | [Staged release + ceremony](phase6-plan.md) | Code council → tidier → functional gate → staged verification → leader sweep → giter stage (W9 re-derivation, rollback adjudication) → **user ceremony; NO live promote by this lane**. | 2, 3, 4, 5 | release gate (Tier-2, promote-blocking) |

**Sequencing note.** Phase 1 **precedes** Phase 2 by design: shipping the non-live ⇒ `e2e` pin against an unreachable workspace converts a "wrong workspace" failure into a "no Plane at all" failure (`technical-analysis.md` §Verification-gate procedures).

> **> Architect G0 (2026-11-02) — phase structure CONFIRMED, 0–6 STANDS.** The architect adjudicated the plan at G0: **APPROVED-WITH-AMENDMENTS**, *"phase structure stands"* (`architecture-recommendation.md` §0 row 7, §9). The phase table above is therefore **unchanged** — no phase was added, removed, merged, or renumbered. The **27** amendment IDs the architect's §7 enumerates are folded **into** the phases it mapped them to (Phase 1: 2 · Phase 2: 13 + **BR-A** · Phase 3: 5 · Phase 4: 2 · Phase 5: 3 · Phase 6: 1). **P2-K** (the read/write classifier reconciliation) is a **new daemon-code item inside the Phase-2 blast radius** — it does **not** create a phase. Per-phase fold registers: `phase1-plan.md` §9, `phase2-plan.md` §7, `phase3-plan.md` §7, `phase4-plan.md` §7, `phase5-plan.md` §10, `phase6-plan.md` §9. Count reconciliation: `decisions.md` §7. **G1 reviewer-council folds (2026-11-02): `decisions.md` §8.**

> **> Leader rulings incorporated, NOT re-opened (architect G0 §9).** ADJ-1 **live-exempt**; gate-(ii) `list_projects` **authorisation granted**; ADJ-4 **user-owned and non-blocking** (the disclosed live-efficacy precondition); **fail-closed typed refusal, no auto-override**; **AD2** (the plaintext-bearer GET defect) **out of scope** (routed follow-up, escalation on record).

---

## 5. Coupling map

| Phase ↓ / → | 0 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|---|
| **0** | — | strong (adjudication gates phase 1's probes) | strong | strong | strong | — | — |
| **1** | — | — | **strong** (gate (ii) must pass before E1 ships) | medium (gate (i) fixes the vocabulary) | **strong** (gate (i) fixes the charter verbs) | medium | — |
| **2** | — | — | — | medium (both touch `manager.py`/`plane.py`) | — | strong | medium (`plane.py` schema bump) |
| **3** | — | — | **strong** *(G2 fold: was "—" — Phase 3 HARD-depends on Phase 2)* | — | — | strong | medium |
| **4** | — | — | — | — | — | strong | **strong** (prompt-artifact + SEALED re-pin ride the stage) |
| **5** | — | — | — | — | — | — | strong (release evidence) |

**Shared-file hot spots** (implementation must serialise edits to these, or land them in separate commits):

> **> G2 fold (approver iter-001, 2026-11-02) — non-blocking item 2: the three dependency tables are reconciled to ONE truth.**
>
> | Dependency | The **truth** | Where it was wrong | Reconciliation |
> |---|---|---|---|
> | **Phase 3 → Phase 2** | **HARD** | `phase3-plan.md` §3 said "Phase 2 (**soft**) … the phases may land in either order" | **upgraded to HARD** in `phase3-plan.md` §3 + `plan-overview.md` §4 (phase table row 3) + §5 (coupling matrix row 3, col 2 = **strong**). **Reason:** Phase 3's pins assert **Phase-2-produced strings** — the resolver's typed reason (P3-C verbatim), the policy-refused bucket (P3-E), and `manager.py`'s P2-G/P2-I. |
> | **Phase 4 → Phase 3** | **NONE** | `phase4-plan.md` §3 listed "Phase 3" | **row withdrawn** (text retained as the historical record) — the charter depends on the **gate-(i) re-capture** for its vocabulary, not on Phase 3's sweep guard. The charter states the ownership **rule**; Phase 3's guard is what makes the runtime match it. |
> | **Phase 4 → Phase 1** | **HARD** (the re-capture for the vocabulary) | — | consistent everywhere (`plan-overview.md` §4 row 4, `phase4-plan.md` §3, `phase1-plan.md`) |
> | **Phase 5 → 2/3/4** | **HARD** | — | consistent (`plan-overview.md` §4 row 5, `phase5-plan.md` §6) |
> | **Phase 6 → 2/3/4/5** | **HARD** | — | consistent (`plan-overview.md` §4 row 6, `phase6-plan.md`) |
> | **Phase 1 → 0** | **HARD** | — | consistent |- `daemon/mcp/builtin_servers/plane.py` — Phase 2 (E3/E4) + Phase 3 (loud-skip reason) + Phase 4 (docstring) → **three phases touch one file**; land E3/E4 and the docstring in **one** commit per phase boundary or rebase cleanly.
- `daemon/manager.py` — Phase 2 (no direct edit) + Phase 3 (skip WARNING + cross-check **+ P2-G stale-row deactivation + P2-I slug-equality heal trigger + P2-H heal-conditionality gate — G1 fold B2: Phase 3 is the single manager.py-touching phase**) → Phase 3 owns it.
- `daemon/clients/plane_http_client.py` — Phase 2 only.
- `daemon/tools/instance.py` — Phase 3 only.
- `agents/project-manager/*.md` + `agents/.prompt-artifacts.sha256` + `tests/test_chart_image_delivery_e2e.py` — **Phase 4 only, ONE commit** (the baseline coupling is atomic).

---

## 6. Risks

Seeded from `technical-analysis.md` §Risk register (R1–R14). Severity column carries the **wave-1** severity; the "plan mitigation" column is this lane's commitment.

| ID | Risk | Sev | Plan mitigation | Owner |
|----|------|-----|-----------------|-------|
| **R1** | Live **and** demo `PLANE_MCP_API_KEY` are the **same expired token** (HTTP 401 `invalid_token`). The gate is presence-only ⇒ a dead key still yields a row ⇒ every session fails at connect, with no typed refusal. | High | (a) USER-OWNED re-mint (ADJ-4, precondition for live efficacy); (b) the lane ships the *observability* half — **via the P3-D honest `test-connection` report and the P5c three-cause WARNING taxonomy; no dedicated connect-failure counter ships** (G1 fold B6 declaration — see the disposition block in `phase3-plan.md` §2.2); (c) **Task 3 runs on dev only** (the sole armed env). | User + lane |
| **R2** | Upstream verb drift — charter names `plane_create_issue`; **zero `*_issue` verbs exist**; entity is `work_item`. The daemon's own docstring repeats it. | High | Phase 4 charter reconciliation (scope **includes** `plane.py:143-152` and `daemon/tools/plane_tools.py:14`) + gate (i) re-capture as the drift detector. | Lane |
| **R3** | Silent skip (INFO, no agent names, no env var names). | High | Phase 3 layers 1–3: typed `unavailable_reason()` + WARNING naming agents + missing vars + registry-vs-row cross-check. | Lane |
| **R4** | **Env misclassification is fail-OPEN**: `_auto_derive_env` reads the **sibling** live install's `.env` and returns `"live"` for the dev checkout, before the dev-shape signal runs. | High | **D-1** (Phase 2): prod access requires an explicit `live` marker OR the daemon's **own** `POSTGRES_DB=ensemble_prod`. Auto-derived `live` is **not** trusted. Gate (iv) pins the case. | Lane (+ follow-up O3) |
| **R5** | Stale `mcp_servers` row keeps the prod slug after the env is corrected (P4). | Med | E4 schema bump `"2"`→`"3"` re-materialises `headers` from the current env; gate (iii) proves it on a scratch install. | Lane |
| **R6** | 177-tool prompt surface ⇒ token cost, latency, tool-choice error rate. | Med | Ship the full `"plane"` category and **measure**; A′ (a `meta.json`-only narrowed allow-list) is the remedy if the measurement says so (ADJ-5). | Lane |
| **R7** | Prefix-sweep category collision ⇒ PM's toolset depends on server-registration state. | Med | Phase 3 ownership guard + 4 regression cases (absent / registered / positive control / `mcp_` non-regression). | Lane |
| **R8** | Charter/baseline coupling — any `agents/project-manager/*.md` edit ⇒ prompt-artifact re-stamp (161/161) **and** `SEALED_SHA_BASELINES` re-pin for `workflow.md`, **same commit**. | Med | Phase 4 enforces the atomicity; the seal tripwire is the enforcement mechanism (a stale row goes red). | Lane |
| **R9** | D6 (pre-existing): custom non-UUID `instance_id` ⇒ `mcp_tool_names=[]`. | Med | Task 3 MUST use the **default UUID id path**. | Task-3 executor |
| **R10** | D7 (pre-existing): `test-connection` bypasses the kill-switch ⇒ the operator's own "is Plane up?" probe lies. | Med | Recorded (O5); Phase 4 runbook tells operators the probe is unreliable while the switch is OFF. | Follow-up |
| **R11** | Privileged-category change would require the full same-commit pin ceremony. | Low | **No privileged-category change expected** (lean #6). If the architect forces one, the ceremony (16 assertion sites / 10 files + count pins + the ari trailing-entry tuple + a registration pin) becomes a Phase-2 sub-task. | Lane |
| **R12** | `mcp_servers` row stores the bearer in plaintext; `GET /api/mcp-servers` does not redact `headers`. | Med | **Out of scope** (O1) — but **ADJ-3 escalates the severity**: a live credential is readable through a GET. | API/security lane |
| **R13** | Path B would need a **second** credential (`X-Api-Key`) that neither install has. | Low | N/A under Path A; recorded for the flip analysis. | — |
| **R14** | **Dev is the only armed environment** ⇒ Task 3 cannot run on demo or live. | Med | Task 3 pinned to the dev context (`~/ensemble-src`: working MCP key + `e2e` + a REST key). | Task-3 executor |

**Lane-added risks (this plan):**

| ID | Risk | Sev | Mitigation |
|----|------|-----|------------|
| **R15** | A non-live env refuses ⇒ **no `mcp_servers` row** ⇒ PM has *no* Plane tools at all — which looks identical to the original bug. | High | Phase 3 makes the refusal **loud and typed** (reason names the workspace and the expected value). Phase 5 asserts the refusal *and* the reason string. *(State (iii) disposition — G1 fold B6: no counter ships; declaration recorded in `phase3-plan.md` §2.2.)* |
| **R16** | The dev checkout is misclassified as `live` ⇒ the `nea` workspace becomes reachable from dev — the exact pollution class the directive exists to prevent. | High | **D-1** + gate (iv) case "no marker + own db `ensemble_dev` ⇒ **non-live**" + a Phase-5 negative test that a dev checkout *cannot* obtain a `nea`-bearing Plane row. |
| **R17** | E4's schema bump re-materialises the row's `headers` from the current env — which **re-persists the bearer into the JSONB row** on every boot with a changed schema version. | Low | No change in exposure vs today (the row already stores it); O1/O2 remain the real fix. Disclosed, not mitigated here. **> (Amendment P2-J, architect G0.)** Extended clause: the bearer is **env-derived and re-derived (overwritten) on every heal** — **no new copy**, redaction posture unchanged. |

**> Architect G0 §6 — four risks ADDED (R18–R21).** These are the architect's new risks; the pre-fold rows R1–R17 remain as written above except where an amendment note appears.

| ID | Risk | Sev | Plan mitigation (per architect) |
|----|------|-----|-------------------------------|
| **R18** | **read-only-subset trap** — the daemon's own classifier partitions the 177 verbs as **48 read / 82 write / 47 unclassified**, and `is_read_tool` returns `False` for the unclassified ⇒ they are **dropped from every non-full-access agent's schema list**. Any future non-full-access grant silently loses **every `retrieve_*`**. The classifier is written against a **non-existent vocabulary** (`add_`/`edit_`/`assign_` match zero catalog verbs; its docstrings name `plane_create_issue`/`plane_assign_issue`). | **Med-High** | **Closed by amendment P2-K** (the classifier reconciliation — a **new daemon-code item, in Phase-2 scope**, with its own coverage pin) + **FC11** (the coverage pin: every catalog verb ∈ read ∪ write ∪ documented-unclassified, with the documented-unclassified set asserted empty). Implemented in `phase2-plan.md` §2.2.1. **> (G2 fold, approver iter-002 — blocker B4: the corrected census.)** With the **`read_` addition** (the one verb the ruled table missed — `read_work_item_attachment`), the corrected classifier is **73 read / 104 write / 0 unclassified** ⇒ **FC11 is satisfiable**. The R18 closure is now **proven by set arithmetic**: the non-full-access kept set grows **48 → 73** (**+25** = `retrieve_`23 + `count_`1 + `read_`1) and the set difference `today − corrected` is **EMPTY** ⇒ **no verb that was kept is dropped anywhere**. The arithmetic (recomputed by me against the 177-verb catalog) is in `phase2-plan.md` §2.2.1 and `phase1-plan.md` §3. |
| **R19** | **A′ rename-loss** — a narrowed grant converts upstream renames into **silent capability loss**. | Med (**iff** A′) | **FC8** — the minimum-toolset assertion (resolved set ⊇ keep-set) + the gate-(i) drift detector (FC10's sorted-name-set sha256), **mandatory iff A′ is adopted**. A′ is the **default outcome on an FC9 breach** (not a judgement call). |
| **R20** | **behavioural rollback** — the old binary re-heals the row with the **RAW env slug**; a **non-live** install re-opens **P1/P2/P3** until the next promote. | Med | **Disclose-and-accept** (amendment **P6-A**) — recorded in the release notes, the ceremony note and the runbook; the observability contract (Phase 3) is the mitigation for the window's invisibility. The **schema** adjudication is unaffected. See `phase6-plan.md` §4. |
| **R21** | **ADJ-1×E4 divergence** — under live-exempt, a permitted-slug change on live leaves a **stale persisted header** (the heal is one-shot, keyed on schema version). | Low-Med | **Closed by the slug-equality heal trigger** (amendment **P2-I**): a row whose persisted slug ≠ the resolved slug ⇒ heal (plane-scoped). See `phase2-plan.md` §2.2 *(implemented in Phase 3 — G1 fold B2; `phase3-plan.md` X3.13)*. |
| **R22** *(G2 fold, approver iter-003 — non-blocking item 8: the OPERATOR ESCAPE HATCH was absent from the risk register.)* | **The operator escape hatch.** A **hand-created** MCP server (not the builtin) pointed at `nea` is **operator authority** and is **not name-matched** to the builtin — the name-keyed preload gate returns **"no opinion"**, so the workspace policy does not speak to it. The REST-create endpoint is an operator-authority path as well. **Consequence:** the env-scoped workspace guarantee holds for the **builtin** Plane lane (and for the daemon-created rows), **not** for operator-created MCP servers. | Med | **Disclose, do not close** (it is an operator capability, not a defect): (a) the **runbook's operator-caveats section states it explicitly** — *the workspace guard governs the builtin Plane lane; a hand-created MCP server pointed at `nea` is operator authority and is outside the guard* (Phase 4's runbook is the home for that sentence); (b) the Phase-3 registry-vs-row cross-check **does** name an operator-created row in its census (it is a **registered** server) — so it is *visible*, just not *refused*. **Closing it is out of this lane's scope.** | Operator / docs |

---

## 7. Success criteria (measurable)

| # | Criterion | How verified |
|---|-----------|---------------|
| **SC1** | On dev with `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` + `PLANE_MCP_WORKSPACE_SLUG=e2e` set, a `plane` row exists in `mcp_servers` and PM's resolved toolset contains ≥ 100 `plane_*` tools including `plane_create_work_item`. | Phase 5 (live dev daemon) + Phase 1 gate (i) |
| **SC2** | On dev/demo/sandbox, a **non-`e2e`** slug ⇒ `PlaneServerDefinition.is_available()` is `False` ⇒ **no row**, **no `plane_*` tool**, and a **WARNING naming the agent + the typed policy reason** (R-3 cause (iii) *workspace refused* — a refusal is **never** rendered as a missing-env-var case; P3-C — G1 fold A-f). | Phase 2 pack + Phase 3 pack + Phase 5 |
| **SC3** | **> (G2 fold, approver iter-003, 2026-11-02 — non-blocking item 3: "byte-identical" was OVER-BROAD and is now stated precisely.)** On live (`ENSEMBLE_SELF_ENV=live`): the **resolution** is byte-identical to today (`nea` permitted; any configured slug permitted; unset ⇒ account-default; the `mcp_servers` row still materialises — and it **already** materialises **pre-promote**, because the live `.env` carries `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` and `is_available()` is **presence-only**). **The ONE intended difference is the Phase-3 prefix-sweep ownership guard, which INTENTIONALLY removes `plane_sync_project` from PM's live toolset** (the pre-fix behaviour — PM receiving `plane_sync_project` — is the **defect** the guard fixes; the guard is server-state-independent). **Nothing else about live changes.** | Phase 5/6 staged-boot comparison (the L4 leg) + Phase 6 staged-verification |
| **SC4** | A dev checkout with **no** explicit marker and `POSTGRES_DB=ensemble_dev` resolves to **non-live** and is refused the `nea` workspace. | Gate (iv) pack (Phase 2) + Phase 5 negative leg |
| **SC5** | `plane_sync_project` is **absent** from PM's toolset in **both** server states (registered and absent) and **present** in the worker's toolset via its `plane_sync` allow entry. | Phase 3 pack (4 cases) |
| **SC6** | The "Guidelines system" DRAFT work item exists in the **`e2e`** workspace with `is_draft=true` and the preserved content; **and no write of any kind reached the `nea` workspace** — proved **structurally** (the resolver never permits `nea` in a non-live resolution **and** the explicit-constructor bypass is closed by P2-D), **not observationally** (the optional observational `nea` read was **dropped** — dev→nea traffic is forbidden by the lane's fence; the structural guarantee is recorded in N-5/P15b). | Phase 5 evidence artifacts (the e2e read-back) + the mechanism-level assertion (P15b) |
| **SC7** | The boot WARNING names the server, the referencing agent(s), and the missing env var(s). | Phase 3 pack (log-capture assertion) |
| **SC8** | `agents/.prompt-artifacts.sha256` is **161/161 clean** and the `SEALED_SHA_BASELINES` row for `agents/project-manager/workflow.md` matches the new content sha — **in the same commit** as the charter edits. | `scripts/protection/generate_prompt_artifact_baseline.py --check` + the seal tripwire |
| **SC9** | Every mutation leg in §11 is **RED at the planted defect and GREEN at the fix**, proven by digest. | Phase 6 functional gate |
| **SC10** | The lane ships a **staged** release with `rollback_safe=true` + a recorded semantic adjudication, and performs **no** live promote. | Phase 6 |

---

## 8. No-regression obligations (binding)

| # | Obligation | Proof |
|---|------------|-------|
| NR1 | **Live behaviour is byte-identical** — *precisely* *(G2 fold, approver iter-003 — non-blocking item 3: the pre-fold wording was OVER-BROAD and is restated)*: the live **resolution** and the live **row materialisation** are unchanged (`nea` permitted; **any** configured slug permitted; unset ⇒ account-default; the row materialises exactly as today — **pre-promote included**, because the live `.env` carries `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` and `is_available()` is **presence-only**). The REST lane stays inert (no `PLANE_API_KEY` on live). **The ONE intended difference is the Phase-3 prefix-sweep ownership guard, which INTENTIONALLY removes `plane_sync_project` from PM's live toolset** — the pre-fix behaviour was the defect the guard fixes. | Phase 2 pack (the live legs) + Phase 6 staged boot (L4) |
| NR2 | **The shell scrubs stay**: `scripts/deploy.sh:255-276` (demo `e2e` pin + grep assert) and `scripts/run_tests_scrubbed.sh:52`/`:78` (unset + leak gate, exit 78) are **not** removed or weakened. Defence in depth: the daemon guard does not replace the shell layer (`boot_subprocess_sweep.py:150-152` precedent — do not trust `ENSEMBLE_SELF_ENV` alone). | Phase 6 diff review (shell files untouched) **+ the new static-content assertion pack for P10** (G1 fold B4 — the previously-cited "existing pack" does **not** read the shell files' content; grep-verified: `tests/test_deploy.sh` carries zero `PLANE_MCP_WORKSPACE_SLUG`/`e2e` assertions and no pack reads `run_tests_scrubbed.sh` — P10's RED leg was unsatisfiable without the new pack) |
| NR3 | **`plane_sync_project` visibility for the worker is unchanged** (`agents/worker/meta.json:29` holds `plane_sync`). | Phase 3 case 3 (positive control) |
| NR4 | **PM's `deny: ["mcp"]` semantics are unchanged** — it strips only `mcp_`-shaped names, and the `plane_` prefix override must keep bypassing it (that is the *designed* path; `plane.py:143-152`). | Phase 3 pack: with the server registered, PM still receives `plane_*` write verbs despite `deny: ["mcp"]` |
| NR5 | **`mcp_full_access` semantics are unchanged** — the schema-level strip (`daemon/services/mcp_service.py:1243-1313`, builtin-registry consult at `:1283-1292`) still exempts the plane server for PM only. | Phase 3 pack + existing `plane_domain_access` pack |
| NR6 | **The `mcp_` lane's `owned_elsewhere` guard is unchanged** (Phase 3's mirror must not regress it). | Phase 3 case 4 |
| NR7 | **No DB schema change** — zero new `.sql` migrations, zero `SQLModel` tables, zero `config.yaml` changes. | Phase 6 migration census (delta MUST be EMPTY) |
| NR8 | **No privileged-category change** (lean #6) ⇒ the 16-member privileged set is untouched. | Phase 2/3 diff review |
| NR9 | **`plane_sync_service`'s REST behaviour is unchanged** on live/demo (REST key absent ⇒ inert). | Phase 2 pack |

---

## 9. Config / env contract per environment

**House rules for this table:** the daemon reads `PLANE_MCP_WORKSPACE_SLUG` for **both** lanes (REST URL path at `plane_http_client.py:303` + MCP header at `plane.py:348-352`). The workspace slug is **never** a tool parameter. Values below reflect wave-1's read-only census — **UNVERIFIED-BY-YOU**, **except** the two dev rows, which were re-censused by me in the G2 fold (**key NAMES only, never values**).

> **> G2 fold (approver iter-001, 2026-11-02) — blocker B1(e): the "dev" row is SPLIT.** The pre-fold table named **one** dev row covering **two** environments (`~/ensemble-src` and `~/ensemble-src-wt-*`) and asserted that Plane credentials were "present" in both. **That assertion was FALSE for the worktree**, which carries **zero** `PLANE_*` keys. Two rows now, each naming its actual state.

| Env | `ENSEMBLE_SELF_ENV` | `POSTGRES_DB` | Classification under D-1 | `PLANE_MCP_URL` | `PLANE_MCP_API_KEY` | `PLANE_MCP_WORKSPACE_SLUG` | `PLANE_BASE_URL` + `PLANE_API_KEY` | Who sets it |
|-----|--------------------|--------------|--------------------------|------------------|--------------------|------------------------------|----------------------------------|----------|
| **live** (`~/agents-ensemble`) | `live` (explicit) | `ensemble_prod` | **live** ⇒ **any configured slug permitted** (ADJ-1 live-exempt: `nea` ✔, `e2e` ✔, a third slug ✔; unset ⇒ account-default) | present | present but **EXPIRED** (401) | `nea` | absent ⇒ REST inert | live install `.env` (operator) |
| **demo** (`~/agents-ensemble-demo`) | `demo` (explicit) | `ensemble_demo` | non-live ⇒ **`e2e` only** | present | present but **EXPIRED** (same token as live) | `e2e` | absent ⇒ REST inert | `scripts/deploy.sh:255-276` sed-pins `e2e` + grep-asserts before `mv` |
| **dev — MAIN CHECKOUT** (`~/ensemble-src`) | **absent** (⇒ auto-derive ⇒ the shared oracle wrongly says `live`; D-1 does **not** trust it) | `ensemble_dev` | **non-live** ⇒ `e2e` only | **present** | **present, works** | `e2e` | **present** + **present** (one credential serves both) | manual `.env` — **mode 0600** |
| **dev — FENCED WORKTREE** (`~/ensemble-src-wt-plane-pm`) — **the ONE Task-3 environment** | **`dev`** (explicit — the giter fence) | `ensemble_dev` | **non-live** ⇒ `e2e` only | **ABSENT ⇒ must be seeded** | **ABSENT ⇒ must be seeded** | **ABSENT ⇒ must be seeded as `e2e`** | **ABSENT ⇒ must be seeded** | giter fence + **Phase-5 Step-0 seeding** from the main checkout (mode 0600) |
| **sandbox** (staged binary) | `sandbox` (explicit) or derived | — | non-live ⇒ **`e2e` only** | set per drill | set per drill | **`e2e`** | scrubbed | drill harness |
| **worktree / test children** | inherited | scrubbed per-variable | non-live ⇒ `e2e` only | inherited | inherited | **scrubbed** (`run_tests_scrubbed.sh:52`) | **scrubbed** (`:52`) | the scrub script |

**G2 fold census (verified by me — key NAMES only, never values):**

| File | mode | `PLANE_*` key names present | `PLANE_*` count |
|------|------|-----------------------------|-----------------|
| `/home/nea/ensemble-src-wt-plane-pm/.env` (the Task-3 worktree) | `0600` | **none** | **0** |
| `/home/nea/ensemble-src/.env` (the main checkout — **seeding source only**) | `0600` | `PLANE_BASE_URL`, `PLANE_MCP_URL`, `PLANE_MCP_API_KEY`, `PLANE_MCP_WORKSPACE_SLUG`, `PLANE_API_KEY`, `PLANE_SYNC_ENABLED` | **6** (five are needed by Task 3; `PLANE_SYNC_ENABLED` is not seeded) |

**Rules that follow from the table:**

1. **Dev/demo/sandbox MUST set `PLANE_MCP_WORKSPACE_SLUG=e2e` explicitly.** Unset ⇒ the resolver refuses (fail-closed) ⇒ no row, no tools. It never silently falls back to the account-default workspace.
2. **Live may leave the slug unset** — that preserves today's account-default behaviour exactly (byte-identical; SC3/NR1). Under the adjudicated **ADJ-1 live-exempt** rule, live permits **any configured slug** (`nea`, `e2e`, or a third) — the *strict* `nea`-only variant is **NOT shipped** and its refusal string is **never emitted** (`phase2-plan.md` §2.1 policy table). The E4 interaction with a permitted-slug change is closed by the **slug-equality heal trigger (P2-I)**, not by tightening the policy.
3. **The shell layer stays** (NR2). The daemon layer is *additional*, never a replacement.
4. **The dev context is the only environment family where Task 3 can execute** (R14) — and, since the G2 fold, the **only** armed environment family is the **main checkout**; the **fenced worktree is the ONE Task-3 environment** and must be **seeded** from the main checkout first (`phase5-plan.md` §4 Step 0).
5. **Key liveness is not a config property** — the daemon's gate is presence-only; liveness is surfaced by observability (Phase 3), fixed by the user's re-mint (O7/ADJ-4).
6. **> (G2 fold, approver iter-001 — non-blocking item 5: the live permitted-set is stated ONCE, consistently.)** The three places that previously disagreed (`decisions.md` ADJ-1, `phase2-plan.md` policy table, `phase4-plan.md` P4-A) now agree on **exactly this**: **live ⇒ any configured slug is permitted** (unset ⇒ account-default, byte-identical); **non-live ⇒ `e2e` only**, and every other slug — **including `your-workspace-slug`** — is **refused** with the typed reason. The P4-A `.env.example` text was corrected accordingly (it had claimed `your-workspace-slug` is refused "everywhere", which is **false on live** under live-exempt). **The missing pin is now added:** *live + a third slug ⇒ ALLOWED* (pinned in `phase2-plan.md` §5 and `phase3-plan.md` §5).

---

## 10. Gate map (house commission pattern)

```
G0  ARCHITECT ADJUDICATION        Phase 0   A vs B vs hybrid, on the 5-axis matrix + write-verb reality check
G1  REVIEWER / PLAN COUNCIL       Phase 0   plan review (fold findings into decisions.md)
G2  FRESH APPROVER                 Phase 0   plan approval (iterations 001..N; each blocker folded)
---                              code begins only after G2 ---
G3  GATE (i) tools/list re-capture Phase 1    freshness + vocabulary source of truth
G4  GATE (ii) e2e MCP reachability Phase 1   MUST PASS before the non-live=>e2e pin ships
G5  GATE (iii) live row read + heal drill Phase 1 (fenced, SELECT-only / scratch)
G6  TIER-1 blast-radius leg        Phase 2   recipe: .agents/tester/skills/blast-radius-leg/
G7  TIER-2 whole-tree dual-side    Phase 2   recipe: .../whole-tree-base-proven-diff/ (core seam: daemon/manager.py touched)
G8  TIER-1 blast-radius leg        Phase 3
G9  TIER-1 / docs leg              Phase 4   prompt-artifact + SEALED coupling
G10 TIER-2 whole-tree dual-side    Phase 4/5 (Phase 4 touches daemon/plane_tools.py + agents/)
G11 CODE COUNCIL                   Phase 6   0 critical, advisories folded
G12 TIDIER                         Phase 6   findings folded; zero behaviour change
G13 FUNCTIONAL GATE                Phase 6   all mutation legs RED->GREEN by digest
G14 STAGED VERIFICATION            Phase 6   artifact integrity + in-binary content + isolated boot smoke + live fence
G15 LEADER SWEEP                   Phase 6   evidence merge to origin/latest
G16 USER 3-FACTOR CEREMONY         Phase 6   promote AS-IS — **the user's action; the lane never promotes**
```

> **> Architect G0 §4 — the flip conditions (FC1–FC6 AMENDED + FC7–FC11 ADOPTED).** The gate chain above is unchanged; the **flip conditions** attached to it are amended and extended. They are the conditions under which the Path-A decision **re-opens** (Path B / hybrid) or under which a **sub-decision** flips (A′).

| FC | Status | Amendment / condition | Where it lives |
|----|--------|----------------------|----------------|
| **FC1** | **AMENDED** | Name the **owner** (leader). Make the charter keep-set **machine-readable** so FC1 becomes a **set-difference** ("no verb in PM's reconciled keep-set is absent"), not a judgement. | `phase1-plan.md` §3 criterion 4 (**P1-B**) |
| **FC2** | **AMENDED** | Unobservable as written ⇒ trigger a re-capture on an observable **`serverInfo.version` change** (`3.2.0` recorded), or downgrade to named-owner judgement. | `phase1-plan.md` §3 (**P1-A** captures the version + name-set sha256) |
| **FC3** | **AMENDED** | Had no metric/threshold/baseline ⇒ fixed by **FC9's numerics** + the **P5-C baselines**; **R6**'s owner carried onto FC3. | `phase5-plan.md` Step 3 (**P5-C**) + §9 X5.11 |
| **FC4** | **AMENDED** | No counter ships ⇒ the condition was unevaluable on live while **R1** is open. Ship a **session-failure counter**, or declare the measurement source + owner; record **UNOBSERVABLE-while-R1**. **(G1 fold B6: the DECLARATION branch is taken — no counter ships; measurement source + owner recorded in the disposition block at `phase3-plan.md` §2.2; state (iii) is UNOBSERVABLE-as-a-counter while R1 is open.)** | `phase3-plan.md` (observability surface) + `phase6-plan.md` §5 PC1 |
| **FC5** | **AMENDED** | Conflated with R1 ⇒ **split the 401 taxonomy**: `invalid_token` ⇒ **R1**; other 401/403 or per-tool auth ⇒ **FC5 candidate**. | `phase3-plan.md` (P3-D reporting path) |
| **FC6** | **AMENDED** | Name the **security owner**. | `decisions.md` §3 (ADJ-3 close) |
| **FC7** | **ADOPTED (new; detector re-scoped G1 fold B1)** | The plane **write** surface **leaks to a non-PM agent**. Detector: a **per-agent resolved-toolset census run with the plane server REGISTERED** (an unregistered census is vacuous) + the pins: (i) *no agent other than PM resolves any `plane_*` WRITE tool*; (ii) *read-exposure disclosure assertion* — the census REDs on any read-holder growth beyond the §5-disclosed empty-allow set (`instance.py:513-520`). **Load-bearing in the same release as P2-K**, which widens the read subset. | `phase3-plan.md` §5 pin **FC7** + §6 X3.11 |
| **FC8** | **ADOPTED (new)** | A′ **silent-zero / rename-loss**. **Mandatory IFF A′ is adopted**: a minimum-toolset assertion (resolved ⊇ keep-set, ≥ keep-set size) + the gate-(i) drift detector. | `phase5-plan.md` Step 3 (**P5-C** block) |
| **FC9** | **ADOPTED (new)** | **Prompt-budget breach** — absolute tokens/turn + % growth vs the pre-Plane baseline. **FC3's numeric anchor**, and the **ceiling at which A′ becomes the DEFAULT outcome of Phase 5** (not a judgement call). | `phase5-plan.md` Step 3 (**P5-C** baselines) + §9 X5.11 |
| **FC10** | **ADOPTED (new)** | **Catalog drift** — record the **count AND the sha256 of the sorted name set** at every re-capture. | `phase1-plan.md` §3 (**P1-A**) |
| **FC11** | **ADOPTED (new)** | **Classifier-coverage gap** — assert every catalog verb ∈ read ∪ write ∪ documented-unclassified (the documented-unclassified set asserted **empty** for the current catalog). **> (G2 fold, approver iter-002 — blocker B4: the expectation is now SATISFIABLE.)** As ruled, the pattern table left **exactly one** captured verb — **`read_work_item_attachment`** — unclassified ⇒ the "empty" assertion would have been **RED at ship**. The fix is to add **`read_`** as a READ prefix (consistent with the plan's own stated method at `phase1-plan.md` §3 and the architect's `read_*`×1 at `architecture-recommendation.md:30`), which yields **73 read / 104 write / 0 unclassified**. The pin therefore asserts: **documented-unclassified = ∅** **AND** the **R18 set-arithmetic** (kept 73 / dropped 104; kept ⊃ today's 48 by exactly 25; today's − corrected = ∅). | `phase2-plan.md` §2.2.1 + §5 pin **P2-K/FC11** |

> **> Architect G0 §1.5 — the A′ / FC9 ruling (ADJ-5 amended to the THIRD OPTION).** ADJ-5's binary (full-now vs narrowed-now) is **rejected**. The ruling: **full category at ship + mandatory numeric baselines + a ceiling at which A′ becomes the DEFAULT outcome of Phase 5.** The **A′ keep-set is pre-derived now** (~28 of 177 — the charter's reconciled verb vocabulary: work-item CRUD + comments + cycles + assignee/manage + the discovery/typing reads; pruning the customer / release / project-admin / module / milestone / initiative / page / property families and the out-of-scope deletes), so an A′ flip needs **no new analysis**. A′ mechanics are **CONFIRMED** (literal names inside a populated dynamic category; empirically executed) and A′ **incidentally removes the R7 leak** — but it converts upstream renames into **silent capability loss**, hence **FC8**. Ceremony note: `meta.json` is **not** in the 161-file prompt-artifact baseline (verified) — but an A′ flip forces a charter edit, so the re-stamp + SEALED re-pin rides the A′ commit (**R8 unchanged**).

**Tier doctrine citation** (`.agents/tester/rules/ensure.md:11-28`): every regression leg picks its tier **before** running; Tier-1 = scoped dual-side FAILED-list diff over the declared blast radius, **≤30 min HARD, fail-loud** (abort + report + ONE auto re-scope); Tier-2 = whole-tree dual-side, triggered when the diff touches a **core seam** (`daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`, `daemon/services/task_processor.py`, `daemon/services/job_queue_service.py`) **or** the gate is promote-blocking; ≤90 min advisory. **This lane touches `daemon/manager.py` ⇒ Tier-2 is MANDATORY, not optional.** Every leg runs as a **pack** (never a bare `pytest`), with the recipe body **read from `.agents/tester/skills/`** before execution — never via `skill_search` discovery.

**Fence rules that apply to every pack invocation in this lane** (from the house corpus):- `timeout 300` outer + `--timeout=120` inner (dual layer).
- **Explicit per-variable `env -u POSTGRES_*` scrubs** — `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names).
- `-p no:randomly` for determinism.
- Worktree-local `.venv/bin/python`; worktrees carry no `.venv`.
- Never resolve live-shaped (HOME-derived) install paths in a test script.
- `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly** on any frozen-binary boot smoke.

---

## 11. Mutation-verified pin obligations

Each pin MUST be proven **RED at the planted defect → GREEN at the fix**, by digest, at the functional gate. The **positive control** (the un-mutated sibling leg) is required so a vacuous green is impossible.

| # | Pin | Site | Cases | Mutation |
|---|-----|------|-------|----------|
| **P1** | **Prefix-sweep ownership guard** | `daemon/tools/instance.py:499-511` (mirror of the `mcp_` guard at `:466-477`) + `tests/unit/tools/test_dynamic_toolset_expansion.py:60-108` | (a) server **absent** ⇒ category `"plane"` must NOT contain `plane_sync_project`, and resolving PM's `allow=["plane"]` must not yield it; (b) server **registered** ⇒ `"plane"` contains every `plane_*` name and NOT `plane_sync_project`; (c) **positive control** — the worker still gets `plane_sync_project` through its `"plane_sync"` allow entry (`agents/worker/meta.json:29`); (d) the `mcp_` lane's `owned_elsewhere` behaviour is unchanged | remove the ownership exclusion → (a) and (b) go RED, (c)/(d) stay GREEN |
| **P2** | **E1 resolver — policy matrix** | `daemon/plane_workspace.py::resolve_plane_workspace()` | explicit `live` ⇒ live; explicit `demo` ⇒ demo; explicit `sandbox` ⇒ non-live; **no marker + own db `ensemble_dev` ⇒ NON-LIVE** (the D-1 case); no marker + own db `ensemble_prod` ⇒ live; no marker + unknown db ⇒ non-live/unclassified; explicit opt-out ⇒ non-live; **explicit `live` + slug `e2e` ⇒ ALLOWED (ADJ-1 live-exempt) AND the strict-variant reason string is NOT emitted (G1 fold B3)**. Each asserts the permitted slug **and** the typed reason string | flip the D-1 predicate to trust the auto-derive → the `ensemble_dev` case goes RED; **re-introduce the strict-variant refusal on live+`e2e` ⇒ the never-emitted assertion goes RED** |
| **P3** | **E2/E3 refusal at the chokepoints** | `plane_http_client.py:296-308/:364-374` + `plane.py:263-298` | non-live + `nea` ⇒ **refuse** (REST client absent; `is_available()` False); non-live + slug **unset** ⇒ **refuse**; live + `nea` ⇒ **allowed**; live + unset ⇒ **allowed** (byte-identical to today); **live + `e2e` ⇒ allowed (ADJ-1 — no refusal rendered, strict string absent — G1 fold B3)** | drop the resolution gate from `is_available()` ⇒ the two refusal cases go RED; render the strict-variant refusal on live+`e2e` ⇒ RED |
| **P4** | **D-1 fail-open case** | `daemon/plane_workspace.py` (the live-determination predicate) | a dev checkout misclassified as `live` by the shared oracle **must NOT** obtain a `nea`-bearing resolution — the lane resolver ignores the auto-derive entirely | replace the lane-local predicate with a call to `upgrade_tools._self_env_marker()` ⇒ the dev case goes RED |
| **P5** | **Loud-skip WARNING** | `daemon/manager.py:3075-3087` + `base.py` (`unavailable_reason`) | the WARNING must name the server, the **agents** whose `mcp_full_access` references it, and the **missing env var names**; and it must be at WARNING level (not INFO) | demote to INFO or drop the agent/env names ⇒ the log-capture assertion goes RED |
| **P6** | **Registry-vs-row cross-check** | `daemon/registry.py` + `manager.py` post-bootstrap | an agent declaring `mcp_full_access: ["plane"]` with **no registered row** ⇒ a WARNING naming the agent + server | remove the cross-check ⇒ the assertion goes RED |
| **P7** | **Charter/baseline coupling** | `agents/.prompt-artifacts.sha256` (161 rows) + `tests/test_chart_image_delivery_e2e.py:1490` | the seal tripwire MUST go RED when a `agents/project-manager/*.md` file is edited **without** the same-commit re-pin, and GREEN after the re-pin | edit a PM `.md` file without re-pinning ⇒ the seal tripwire goes RED (the existing tripwire is the mutation) |
| **P8** | **E4 heal re-points the slug** | `plane.py:128-140` (`schema_version` "2"→"3") + `manager.py:312-353` | after a schema-version bump the persisted `config.headers['x-workspace-slug']` equals the **resolved** slug, and an operator-set `config.env` sentinel **survives** the heal | revert the schema bump ⇒ the row keeps the stale slug ⇒ RED |
| **P9** | **PM `deny: ["mcp"]` bypass preserved** | `plane.py:143-152` + `daemon/services/mcp_service.py:1243-1313` | with the server registered, PM receives `plane_*` write verbs despite `deny: ["mcp"]` | remove the prefix override ⇒ PM loses the plane tools ⇒ RED |
| **P10** | **Shell scrubs intact** *(G1 fold B4 — backed by a NEW static-content pack; **G2 fold B1 — the pack now HAS a home: Phase 4 CREATES it**)* | `scripts/deploy.sh:255-276`, `scripts/run_tests_scrubbed.sh:52` (the unset) + **`:101`** (the leak-gate `exit 78` — **the G2 fold corrects the pre-fold `:52/:78` anchor**) + the NEW pack `tests/packs/plane_shell_scrub_content.sh` (**created + registered in PACKS.md by Phase 4** — `phase4-plan.md` §2.5/§4/§5/§6) | a **static-content assertion pack** (registered in PACKS.md): (a) asserts `scripts/deploy.sh` still contains the `PLANE_MCP_WORKSPACE_SLUG=e2e` sed-pin + grep-assert lines; (b) asserts `scripts/run_tests_scrubbed.sh` still contains the per-variable unset + the leak-gate exit-78 lines; (c) positive control on the intact files ⇒ GREEN. *(The prior form was structurally vacuous: grep-verified that NO existing test asserts this content — `tests/test_deploy.sh` carries zero `PLANE_MCP_WORKSPACE_SLUG`/`e2e` assertions — so the RED leg could not fire.)* | delete/weaken the asserted lines in a scratch copy (or plant the weakened variant) ⇒ the content assertions go RED; restore ⇒ GREEN |
| **P30** *(G2 fold B3 — the no-active-default check)* | `.env.example` carries **NO active** `PLANE_MCP_WORKSPACE_SLUG=` assignment; the sample is **COMMENTED OUT** (`#PLANE_MCP_WORKSPACE_SLUG=e2e`) with the "live requires `nea`; non-live requires `e2e` when armed" comment. **The RED baseline leg is on disk today:** `.env.example:209` carries an **ACTIVE** `PLANE_MCP_WORKSPACE_SLUG=your-workspace-slug` (verified by me). **Why prod-safety:** live-exempt would **accept** an active `e2e` default ⇒ a fresh install would silently point at the **test** workspace. | restore the active assignment ⇒ the static check goes RED; comment it out ⇒ GREEN |

> **> Architect G0 — the pins ADDED by the fold** (the pre-fold rows P1–P10 above are unchanged; these are additional, each owned by the phase plan that implements it):

| # | Pin | Site | Cases | Mutation |
|---|-----|------|-------|----------|
| **P2-A** | **predicate edge hardening** | `daemon/plane_workspace.py` | opt-out ⇒ non-live **even with** own-db=prod; garbage marker ⇒ non-live + WARNING; empty marker ⇒ absent; exact non-normalizing `VALID_ENVS` match | restore the as-written predicate ⇒ the opt-out+prod-db case goes RED |
| **P2-B** | **canonical DB-name input + hard bans** | `daemon/plane_workspace.py` | `POSTGRES_DB` → own `ensemble.json` `pg.db`; an env-var-less prod install ⇒ **live**; **no** `upgrade_tools` import; **no** file opened when the env var is supplied | read only the env var ⇒ the env-var-less prod case goes RED; import `upgrade_tools` ⇒ the grep pin goes RED |
| **P2-C** | **one canonical slug normalization** | REST + MCP lanes | the same env value yields the same slug in both lanes (no split-brain) | restore the per-lane normalizations ⇒ RED |
| **P2-D** | **constructor bypass closed** | `plane_http_client.py:326-361` | an explicit `workspace_slug`/`base_url` may never introduce a slug the resolver did not return; a refused resolution ⇒ **inert client** | allow the explicit param through ⇒ RED |
| **P2-G** | **stale-row deactivation on refusal** | `daemon/manager.py` *(implemented Phase 3 — G1 fold B2; `phase3-plan.md` X3.12)* | a pre-existing ACTIVE plane row is **deactivated** on a workspace refusal (mirror `MCP_DISABLE_BUILT_IN_*`) | drop the deactivation ⇒ the cross-check reads it as registered ⇒ RED |
| **P2-I** | **slug-equality heal trigger** | `plane.py` + `manager.py:3115-3132` *(implemented Phase 3 — G1 fold B2; `phase3-plan.md` X3.13)* | a persisted slug ≠ the resolved slug ⇒ the row is healed (not only a schema-version bump) | remove the trigger ⇒ RED |
| **P2-K / FC11** | **classifier coverage** | `plane.py:242-259` + `resilience.py:638-687` | every catalog verb ∈ read ∪ write ∪ documented-unclassified, with documented-unclassified **empty**; the ruled pattern disposition holds | revert any pattern ⇒ RED (a `retrieve_*` revert silently drops it from the read set ⇒ **R18** returns) |
| **P2-L** | **sync refusal reason** | `plane_sync_service.py:374-381` | `unavailable_reason()` carries the resolver's **typed refusal reason** (not "API key not set") | restore the old reason ⇒ RED |
| **P3-C / P5c** | **typed reason verbatim + three causes** | `manager.py:3075-3087` | the skip WARNING carries the resolver's typed reason **verbatim**; three distinguishable causes; a refusal is never rendered as a missing-env-var case; *deliberately disabled* ⇒ no WARNING | paraphrase the reason / render a refusal as a missing-var ⇒ RED |
| **P3-D / P5d** | **test-connection consults the resolver** | `mcp_servers.py:337` | the endpoint reports the refusal instead of the stale `success=true, 177 tools` (the D7/AD7 lie) | restore the unconditional tool-count report ⇒ RED |
| **P3-A / P5e** | **one-shot envelope notice** | `plane.py` `is_available()` | at most **one** process-wide envelope notice (label + source + effective slug, **never the reason**); no per-call logging (5 call sites) | log per call / interpolate the reason ⇒ RED |
| **P3-E / P6b** | **pointer-only + policy-refused bucket** | `manager.py` cross-check | the cross-check emits agent + server + **pointer**, no re-interpolation; a P2-G-deactivated row is *policy-refused*, not *never-registered*, and stays at **WARNING** | re-interpolate / reclassify ⇒ RED |
| **FC7** | **no plane WRITE leak to a non-PM agent** *(G1 fold B1 — re-scoped)* | per-agent resolved-toolset census, **run with the plane server REGISTERED** | (i) no agent other than PM resolves any `plane_*` **WRITE** tool (`mcp_full_access` = PM only); (ii) the read-exposure set matches the §5 disclosure — **growth beyond the disclosed empty-allow set (`instance.py:513-520`) ⇒ RED** (**load-bearing with P2-K**) | grant `plane`/`mcp_full_access` elsewhere ⇒ the write leg REDs; run the census unregistered ⇒ the vacuity tripwire REDs |
| **P5-C / P19** | **FC9 baselines** | `phase5-plan.md` Step 3 | per-turn prompt tokens for PM · tool-call error rate over a stated N · mis-selected-verb count (+ the pre-Plane base) | omit a baseline ⇒ FC9 is unevaluable ⇒ RED |
| **P5-A / P15a** | **the RESTATED N-4** | `phase5-plan.md` Step 6 | no **NEW** row; pre-existing row's `config_schema_version` **unchanged**; PM's **resolved** toolset free of `plane_*`; WARNING logged; `mcp_tool_names` stored-fallback free of stale `plane_*` | weaken any of the four parts ⇒ RED |
| **P6-A / P23** | **behavioural rollback disclosure** | release notes + ceremony note + runbook | the rollback re-heals the row with the **raw env slug**, re-opening P1/P2/P3 on non-live until the next promote (live unaffected) — disclose-and-accept | omit the disclosure ⇒ RED |
| **P20** *(G2 fold B1)* | **the arming precondition** | `phase5-plan.md` Step 0 | the key-**NAME** census shows all **five** required `PLANE_*` names present in the worktree `.env`; the worktree `.env` mode is `0600`; the **main-checkout `.env` is unchanged**; **no value** in any log/note/artifact | seed a missing name ⇒ RED |
| **P21** *(G2 fold B1)* | **the STOP branch** | `phase5-plan.md` Step 0.3 + §7 | an unsatisfiable census ⇒ the run **stops before Steps 3–4** and reports an **environment defect**; cause (d) is triaged **before** any code-regression hypothesis | remove the STOP branch ⇒ RED |
| **P22** *(G2 fold NB4)* | **SC1's numeric assertion** | `phase5-plan.md` Step 3 | `pm_toolset_census.json` records `plane_tool_count` ≥ **100** and `sc1_satisfied: true`, with `plane_create_work_item` present | drop the numeric assertion ⇒ RED |
| **P28** *(G2 fold B3)* | **the FC9 derivation** | `phase5-plan.md` Step 3 | `fc9_baselines.json` records `baseline_tokens`, `present_tokens`, `growth_tokens`, `growth_pct`, **both** ceiling booleans and the verdict `PASS`/`BREACH`/`UNEVALUABLE`; a missing `baseline_tokens` ⇒ `UNEVALUABLE`, never a substituted guess | substitute a guess for a missing baseline ⇒ RED |
| **P29** *(G2 fold B2)* | **the A′ branch** | `phase5-plan.md` §5.6 | on `BREACH`: the narrowed grant + the charter edit + the **161/161** re-stamp + the **SEALED** re-pin in **ONE commit**, + the FC8 minimum-toolset assertion + the drift detector; on `PASS`: **no** A′ commit exists and the measurement is recorded | ship a breach without the A′ commit ⇒ RED; ship an A′ commit on a PASS verdict ⇒ RED |

> **Pin-ID master index (G1 fold B5 — single-homing rule; CORRECTED by G2 fold, approver iter-001, 2026-11-02 — non-blocking item 8).** The pre-fold index claimed the fold-added rows were "**P2-A…P2-L**" in the §11 second table — an **overstatement**: a regex sweep of the second table shows **P2-E, P2-F, P2-H and P2-J are NOT there**. The accurate index is:
>
> | ID class | IDs | Home |
> |---|---|---|
> | **Pre-fold mutation pins** | **P1–P12** | `plan-overview.md` §11 **first** table (P1–P10) + `phase3-plan.md` §5 (P5/P5c/P5d/P5e/P6/P6b/P9/FC7) + `phase4-plan.md` §5 (P7/P10/P11/P12) |
> | **Fold-added mutation pins (G0)** | **P2-A, P2-B, P2-C, P2-D, P2-G, P2-I, P2-K/FC11, P2-L, P3-C/P5c, P3-D/P5d, P3-A/P5e, P3-E/P6b, FC7, P5-C/P19, P5-A/P15a, P6-A/P23** | `plan-overview.md` §11 **second** table, each with its implementation phase |
> | **Fold-added mutation pins (G2 iter-001)** | **P20, P21, P22, P28, P29** | §11 second table (G2 rows) — `phase5-plan.md` §8 |
> | **Fold-added mutation pins (G2 iter-002)** | **P2-H** (reclassified as a mutation pin — see below), **P30** (the no-active-default check) | **P2-H** → `phase2-plan.md` §5 (implementation home Phase 3, validated at Phase-3 exit X3.13); **P30** → `phase4-plan.md` §5 |
> | **Phase-5 pins** | **P13–P19** (+ **P15a/P15b**; P15 superseded) | `phase5-plan.md` §8 |
> | **Phase-6 staged pins** | **P23–P27** (P19–P22 → **P24–P27** by G1 fold B5 to clear the Phase-5 P19 collision; P23 unchanged) | `phase6-plan.md` §7 |
> | **Phase-1 gate pins** | **P-G1, P-G1a, P-G1b, P-G2, P-G3, P-G4** *(G2 fold, approver iter-003 — non-blocking item 4: **P-G2/P-G3 were MISSING from this index**, and **P-G4** is new (the keep-set artifact's own integrity))* | `phase1-plan.md` §7 |
> | **AMENDMENT IDs that are NOT mutation pins** | **P2-E** (anchor drift — a citation correction), **P2-F** (the 4-link gate-chain statement — a structural assertion), **P2-J** (the R17 extended clause — a **disclosure**, covered by the disclosure-presence pin **P23**). **> (G2 fold, approver iter-002 — non-blocking item 2: P2-H is RECLASSIFIED as a MUTATION PIN.)** The pre-fold index listed **P2-H** here; that was wrong — **P2-H IS a mutation pin** with a meaningful RED leg (remove the heal-conditionality gate ⇒ the heal runs on a refusal boot ⇒ the schema-version-unchanged assertion goes RED). It is now in the **mutation-pin** row above, with its **implementation home = Phase 3** (the `manager.py` heal site) and its **validation point = Phase-3 exit** (X3.13). The **single** statement of that classification is the pin row in `phase2-plan.md` §5; this index and the Phase-6 G13 enumeration both defer to it. |
>
> **> G2 fold (approver iter-001) — the amendment-status sub-table (the four IDs the over-stated index implied were pins):**
>
> | Amendment ID | What it is | How it is verified (NOT a mutation pin) | Home |
> |---|---|---|---|
> | **P2-E** | the anchor drift correction (`:353-356`, `:326-361`) | a **citation** check at implementation time (the anchors are re-derived and cited by quoted phrase) — no RED leg is meaningful | `phase2-plan.md` §2.3 + the P2-E block |
> | **P2-F** | the four-link gate chain (bootstrap skip / warmup / schema warm / preload gate) | a **structural** assertion (all four sites consult the same classmethod) — asserted by the gate-(iv) + enforcement packs, not by a planted-defect mutation | `phase2-plan.md` §2.2 + the P2-F block |
> | **P2-H** | **> (G2 fold, approver iter-002 — non-blocking item 2: RECLASSIFIED — this IS a mutation pin.)** The heal is **conditional on availability** | **a mutation pin with a RED leg** (remove the conditionality gate ⇒ the heal runs on a refusal boot ⇒ the "schema version unchanged" assertion goes RED). Its **implementation home is Phase 3** (`manager.py:312-353` — the G1 fold B2 split) ⇒ **validated at Phase-3 exit (X3.13)**; the Phase-2 heal pack keeps its **P8** leg green at Phase-2 exit. The **single** statement of this classification is the pin row in `phase2-plan.md` §5 (the master index and the Phase-6 G13 enumeration both defer to it) | `phase2-plan.md` §5 (the **P2-H** pin row) + `phase3-plan.md` §6 (X3.13) |
> | **P2-J** | the R17 extended clause (the bearer is env-derived and re-derived on every heal; no new copy; redaction posture unchanged) | a **disclosure-presence** check, same runner class as **P23** (static pack; the RED leg is a planted-absence) | `phase2-plan.md` §2.2 (the P2-J block) + `phase6-plan.md` §7 (**P23**) |
>
> **G13's enumeration** (`phase6-plan.md` §3) is reconciled to this index by the G2 fold — it now covers **every** pin, including the five G2-fold pins. **The G1 enumeration was already correct in excluding P2-E/P2-F/P2-J** (they are not pins); it needed only the five G2 additions.

---

## 12. Staged release path

| Step | Owner | Content |
|------|-------|---------|
| 1 | Implementation lane | code + pins + packs on `feature/plane-pm-issue-tools`; prompt-artifact re-stamp + SEALED re-pin in the **same** commit as the charter edits |
| 2 | Code council | 0 critical; advisories folded |
| 3 | Tidier | findings folded; zero behaviour change |
| 4 | Tester | functional gate: all packs green, every mutation leg RED→GREEN by digest, net-new reds = 0 against the declared blast radius (Tier-2 for the `daemon/manager.py` seam) |
| 5 | Leader sweep | evidence merged to `origin/latest` |
| 6 | **giter stage** | `VERSION=v0.28.6` (or the next free slot — **re-derive at stage time**, W9) + `scripts/upgrade/stage.sh`; manifest + both provenance sidecars; `rollback_safe` adjudication recorded **per the STANDING RULE** (explicit semantic reasoning, not a reflex): this lane ships **zero** `.sql` files ⇒ delta-EMPTY ⇒ `rollback_safe=true source=explicit`; reasoning: no schema mutation, the old binary stays compatible, auto-rollback may fire safely on a failed gate. **> (Amendment P6-A — the BEHAVIOURAL clause, disclose-and-accept.)** A rollback restores the old binary, which **re-heals the row to schema `"2"` with the RAW env slug**; on a **non-live** install that re-opens **P1/P2/P3** until the next promote (live is unaffected). The schema adjudication is **not** downgraded — the behavioural window is **disclosed** alongside it in the release notes, the ceremony note and the runbook, with the Phase-3 observability contract as the mitigation (an operator who rolls back sees the plane lane go quiet — that is the rollback window, not a credential problem). |
| 7 | Prompt-artifact + SEALED baseline at stage time | `scripts/protection/generate_prompt_artifact_baseline.py --check` must be clean (161/161) at the staged tree; the seal row must match the staged `agents/` payload |
| 8 | Staged verification | 4-way sha; in-binary content proof (the new resolver symbols present in the staged binary, absent in the live one); isolated boot smoke on a scratch root with `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned and per-variable env scrubs; **live fence held** (the live `current` symlink and `state.json` byte-identical pre/mid/post) |
| 9 | **User ceremony** | `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.6 bash scripts/upgrade/promote.sh live --f2-verified-closed` — **the user's 3-factor nonce gate owns the flip. This lane does NOT promote.** |
| 10 | Post-promote efficacy (user-owned) | the live/demo **credential re-mint** (ADJ-4/O7) — until it lands, live has a row + failing sessions, which the Phase-3 observability now makes **visible** instead of silent |

**Promotion precondition (unmet until the user acts):** the live Plane MCP credential is expired (R1). Path A on live therefore yields a row and failing sessions until the re-mint lands. **This does not block staging** — the staged artifact is correct — but it does block *live efficacy*, and the runbook must say so.

---

## 13. Assumptions & open questions

**Assumptions (each one, if false, re-opens a decision):**

- **A1** — The upstream server keeps exposing work-item / comment / cycle write verbs (re-captured at gate (i)). *False ⇒ FC1 fires.*
- **A2** — The user re-mints a working Plane MCP credential for live and demo (user-owned; without it, live has no functional Plane).
- **A3** — The 177-tool prompt surface is acceptable for PM, or A′ is applied (measurable).
- **A4** — The daemon can tolerate an upstream-owned verb catalog (drift absorbed by charter phrasing + the re-capture gate).
- **A5** — The dev context stays armed with a working MCP key + `e2e` + a REST key through Phase 5 (the only environment where Task 3 can run).
- **A6** — No privileged-category change is needed (lean #6).

**Open questions for the dispatcher (carried into `decisions.md` as ADJ-1…ADJ-5):**

1. **ADJ-1** — strict live (`nea`-only) vs **live-exempt** (default). *Default: live-exempt, byte-identical per lean #2 + live-continuity; strict is a one-line flip.*
2. **ADJ-2** — D-1 lane-local (R-A, default) vs fixing the shared oracle (R-B).
3. **ADJ-3** — AD2 header redaction: in-lane or follow-up. *Leader default: OUT OF SCOPE; the plan records the severity escalation (a live credential readable via GET).*
4. **ADJ-4** — key re-mint ownership (user) + whether **one** credential may serve both `nea` and `e2e` (needs authorisation to test).
5. **ADJ-5** — full 177-tool category vs **A′** pre-narrowed grant (default: decide at implementation with the architect; A′ is the Scalability-axis remedy).

---

## 14. Evidence base (linked, not re-derived)

| File | What it carries |
|------|-----------------|
| [`investigation-digest.md`](investigation-digest.md) | §1 Digest A (wiring gap) · §2 Digest B (workspace/env) · §3 **Leader Leans (verbatim — bound)** · §4 planner verification stamps · §5 wave-1 re-verification table (V1–V32) · §6 the E4 correction · §7 probe evidence · §8 the 2026-09-20 cross-check · §9 residual unverified list |
| [`technical-analysis.md`](technical-analysis.md) | the binding technical basis: 5-axis matrix + **Path A recommendation + FC1–FC6**; the E1–E5 enforcement design + **D-1**; loud-skip observability; the prefix-sweep guard; the **R1–R14** risk register; the **gate (i)–(iv)** procedures; the adjacent-defect record (AD1–AD7); open questions |
| [`tools-list-inventory.md`](tools-list-inventory.md) | the captured **177-tool** inventory; the write-verb reality check (**the three-number census, not the withdrawn 116/177 figure** — 104/73 by the inventory's stated method; **48 read / 82 write / 47 unclassified** by the daemon's actual classifier, `plane.py:242-259` + `resilience.py:638-687`; 8 work-item CRUD verbs, 5 comment verbs, 10 cycle verbs; `create_work_item` carries `is_draft`); credential health; the e2e REST reachability result |
| `.agents/tester/rules/ensure.md` | the **Gate Tiers** doctrine (Tier-0/1/2, 30-min hard Tier-1) and the pack/fence rules cited throughout |
| `.agents/tester/PACKS.md` | the pack-registration convention (invocation + scope + last-run + status columns; the node-count convention) |
| `.agents/tester/skills/{blast-radius-leg,whole-tree-base-proven-diff,regression-ab-partition-run,failed-digest-equality-gate,ab-base-leg-provisioning-artifacts}/` | the **recipe bodies** to load before executing a gate leg — deterministic path, never `skill_search` discovery |

**Anchor provenance in this plan.** Every `file:line` in this plan was **re-read by me** in `/home/nea/ensemble-src-wt-plane-pm` @ `941cb6321` unless the line carries an explicit **UNVERIFIED-BY-YOU** mark. Re-verified by me in this plan-assembly pass: `plane.py:128-140/:143-152/:263-298/:325-354`; `manager.py:312-353/:3075-3087/:3089-3132`; `instance.py:466-477/:499-511`; `_tool_registry.py:279-281/:932-936`; `plane_sync.py:210-212`; `plane_http_client.py:296-308/:326-362/:364-374/:388-398/:563/:606/:652/:677`; `registry.py:411/:710/:771/:1419-1457`; `upgrade_tools.py:187/:260-294/:395-418/:440-561/:563-608`; `config.py:3178/:3262-3271`; `mcp_servers.py:185-253/:337`; `mcp_service.py:1243-1313/:1283-1292`; `tool_adapter.py:487-511`; `base.py:229-250`; `plane_sync_service.py:157-162/:374-381`; `mcp/config.py:245-277`; `connection_manager.py:173`; `agents/project-manager/meta.json` (all fields); `agents/project-manager/{tools_note,rule,workflow,soul}.md` drift lines; `agents/.prompt-artifacts.sha256` (161 rows, PM rows at :120-123); `tests/test_chart_image_delivery_e2e.py:1490`; `tests/unit/tools/test_dynamic_toolset_expansion.py:55-108`; `.env.example:185-190/:195-209/:444-445`; `scripts/deploy.sh:253-278`; `scripts/run_tests_scrubbed.sh:45-80`; `daemon/tools/plane_tools.py` (whole file).

**UNVERIFIED-BY-YOU (wave-1 claims I did not re-verify — they are the lane's premises, not my findings):** the HTTP status codes of the credential probes (401 live / 200 dev); the e2e REST 200 + 2 projects; the 177-tool inventory contents (read from the captured artifact, not re-probed); the runtime classification table (I re-verified the **code structure** of `_auto_derive_env` — the sibling `.env` read at `:505-555` precedes the dev-shape signal at `:556-558` — but did **not** execute the resolver); the live/demo/dev `.env` contents; the live `mcp_servers` row state.

> **> Architect G0 fold (2026-11-02) — additional re-verification performed during the fold.** The fold required re-anchoring every new anchor the architect introduced, so the following were **read by me** in this fold (not inherited):
>
> | Anchor | What I verified |
> |---|---|
> | `daemon/mcp/builtin_servers/plane.py:242-259` | the resilience config's `read_tool_patterns = ("list_","get_","search_")` (:242-245) and `write_tool_patterns = ("create_","update_","delete_","add_","remove_","set_","edit_","assign_")` (:250-259), plus the stale comment at :248-250 naming `plane_set_issue_priority` / "assignee-update tools" — **confirmed**. |
> | `daemon/mcp/resilience.py:638-687` | `is_read_tool` — write-first precedence, the first-segment strip, and the fallthrough semantics that make an unclassified verb return `False` (not read) — **confirmed**. |
> | `daemon/services/mcp_service.py:977-998` | the CR-3 strip keeps only `is_read_tool == True` schemas for non-full-access agents; PM's opt-out at `:1310-1312` — **confirmed**. |
> | the 6 BR-A test files | **all six exist** (2,206 / 1,081 / 979 / 259 / 476 / 1,766 lines) — verified before being written into the blast radius. |
> | the inventory facts behind P2-K | `add_`/`edit_`/`assign_` match **zero** catalog verbs (architect correct); **`set_` matches 2** (`set_work_item_property_value`, `set_customer_property_values`) and **`remove_` matches 1** (`remove_work_item_relation`) ⇒ both are **kept**; `search_` is **already** a read pattern ⇒ the read delta is `retrieve_` + `count_` — verified by grep over `tools-list-inventory.md`. |
> | `scripts/deploy.sh:255-276`, `scripts/run_tests_scrubbed.sh:52`/`:78` | the demo `e2e` pin + grep assert and the scrub + leak gate exist as described (P4-B) — **confirmed**. |
> | `daemon/routers/settings.py:703-716` | the FE `PLANE_BASE_URL` echo gate (P4-B) — **confirmed**. |
> | the amendment-ID census | a regex sweep of `architecture-recommendation.md` yields exactly **27** distinct amendment IDs (P1-A…P6-A + BR-A) — the architect's stated "30" is a summary-count discrepancy, reconciled in `decisions.md` §7. |

---

## 15. Fold register — G2 approver iteration 001 (2026-11-02)

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` (main checkout) — iteration 001 **REJECTED** (3 blockers + 9 non-blocking notes). **Architecture NOT re-opened** — both approver workers re-verified the enforcement design holds and commission tasks 1+2 are fully covered. **Mode:** append-only; **no phase, gate, pin or exit-criterion ID was renumbered**; historical text retained where it is the record, edited only where it is live routing.

| Item | Substance | Where folded | Disposition |
|------|-----------|--------------|-------------|
| **B1** | Task-3 environment ambiguous + **unarmed worktree** (zero `PLANE_*` keys ⇒ Steps 3–4 unexecutable ⇒ the `:166` triage would mis-route it as a code regression) | §9 (the **dev row is split** into *main checkout (armed)* and *fenced worktree (unarmed, seeded)* + the verified census); `phase5-plan.md` §4 Step 0 (census / seeding / STOP branch) + §7 (the triage rewrite); `phase1-plan.md` §3 (the probe env named) | **FOLDED** |
| **B2** | the FC9-breach ⇒ A′ branch had **no executable phase home** | `phase5-plan.md` §2 (the conditional file-scope exception) + §5.6 (the A′ sub-step) + §8 **P29** + §9 **X5.12** | **FOLDED** |
| **B3** | FC9 had **no threshold and no comparison base** anywhere in the set | `phase5-plan.md` §5 Step 3 (the pre-committed ceiling rule + justification + comparison procedure) + §8 **P28** + §9 X5.11; `phase1-plan.md` §3 (the baseline acquisition at the gate) | **FOLDED** |
| **NB1** | the staged-boot negative legs are vacuous without credentials | `phase6-plan.md` S3 (the four enumerated legs, all credentialed + the vacuity tripwire) + §7 **P24** | **FOLDED** |
| **NB2** | three dependency tables disagree | §4 (phase table row 3) + §5 (coupling matrix row 3 col 2 + the reconciliation table) + `phase3-plan.md` §3 (soft ⇒ **HARD**) + `phase4-plan.md` §3 (the Phase-3 row **withdrawn**) | **FOLDED** |
| **NB3** | the P2-D injection seam is unstated | `phase2-plan.md` §2.3 (the P2-D block — both seams named: the direct constructor `plane_http_client.py:326-332` and the sync-service injection parameter `plane_sync_service.py:334/:349/:389-390`; the resolver-injection boundary per P2-M) | **FOLDED** |
| **NB4** | SC1's "≥ 100" is not asserted in the Phase-5 census | `phase5-plan.md` §5 Step 3 + §8 **P22** + §9 **X5.14** | **FOLDED** |
| **NB5** | the live permitted set is inconsistent across three files | §9 rule 6 (the single statement) + `decisions.md` ADJ-1 (the G2 note) + `phase2-plan.md` §5 (**P2**/**P3** gain the live+third-slug leg) + `phase4-plan.md` P4-A (corrected) | **FOLDED** |
| **NB6** | an **active** `PLANE_MCP_WORKSPACE_SLUG=e2e` sample would silently target the test workspace on fresh installs (incl. live) | `phase4-plan.md` P4-A (the sample must be **COMMENTED** + the static check) | **FOLDED** |
| **NB7** | the withdrawn 116/177 figure is still advertised | §14 (the tools-list-inventory row → the three-number disclosure) + `phase0-plan.md` §3 (annotated as historical record) + `decisions.md` **W-2a** (the supersession row) | **FOLDED** |
| **NB8** | the pin-ID master index is overstated | §11 (the index **corrected** into six ID classes + the **amendment-status sub-table** for P2-E/P2-F/P2-H/P2-J) + the five G2-fold pins added to the second table + `phase6-plan.md` §3 (the G13 enumeration reconciled) | **FOLDED** |
| **NB9** | the optional observational `nea` read contradicts the no-nea-contact fence | `phase5-plan.md` §5 Step 6 (the N-5 clause **DROPPED**, the coverage loss stated) + §8 **P17** | **FOLDED (DROPPED)** |

**G2-fold pin IDs added:** **P20, P21, P22, P28, P29** (Phase 5) — all new, none renumbering an existing ID. **G2-fold exit criteria added:** **X5.12, X5.13, X5.14** (Phase 5) — all new.

**Anchor provenance for the G2 fold (verified by me, read-only):** the worktree + main-checkout `.env` **key NAMES only, never values** (the §9 census); `dev.sh:79-112`; `daemon/graph.py:10531`/`:10549` + `daemon/loader.py:704-706` (the FC9 measurement point); `daemon/clients/plane_http_client.py:326-332`; `daemon/services/plane_sync_service.py:334/:341/:349/:389-390`; `.env.example:185-190/:209/:444-445`; `daemon/mcp/builtin_servers/plane.py:242-259`; `daemon/mcp/resilience.py:638-687`.

---

## 16. Fold register — G2 approver iteration 002 (2026-11-02) — FINAL FOLD

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 002 **REJECTED** (4 blocking + 10 non-blocking notes; the dispatch lists **13** non-blocking after merging). **Architecture / D-1 / ADJ-1 / phase structure NOT re-opened.** **Mode:** append-only; no phase, gate, pin or exit-criterion ID renumbered; historical text retained where it is the record, edited where it is live routing.

| Item | Substance | Where folded | Disposition |
|------|-----------|--------------|-------------|
| **B1** | the **P10** static-content pack has **no implementation home** (the file does not exist; only 2 mentions of its name existed) | **Phase 4 owns it**: `phase4-plan.md` §2.5 (NEW — the pack file + the PACKS.md row), §4 (blast radius + the "why Phase 4" rationale + the index correction), §5 (**P10** pin row), §6 (**X4.12**); `plan-overview.md` §11 (the **P10** pin row + the master-index home corrected) | **FOLDED** |
| **B2** | the **FC9 baseline daemon boot** is undesignated, unfenced, and contradicts its own section ("touches no daemon") | `phase1-plan.md` §3 — the **activity/environment reconciliation table** (the two probes touch no daemon; the baseline + gate (iii) boots are **scratch**), the **designated baseline-boot environment** with **all** house fences bound (scratch root only; `ENSEMBLE_SELF_ENV=sandbox` pinned; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned, **never** `env -u`; per-variable PG scrub; `mktemp` FAKE_HOME + escape guard; scratch PG DB + throwaway role; a port **neither 9797 nor 7979** (both live); SIGTERM-only teardown; the live-fence proof), the **no-`.env`-mutation** rule (the plane-absent condition is a **per-process env override**), and the **6 ordered baseline assertions**; §8 **X1.8** | **FOLDED** |
| **B3** | **X4.9 contradicts NB6** (an active `e2e` assignment satisfied the old wording) | `phase4-plan.md` §6 — **X4.9 reworded** (the sample must be **COMMENTED**; **no** active assignment anywhere), **X4.11** (the static check's exit criterion), **X4.12** (the P10 pack's exit criterion); §5 — the new **P30** pin with the **RED baseline leg = the active `.env.example:209` on disk today**; `plan-overview.md` §11 (the **P30** row) | **FOLDED** |
| **B4** | the ruled P2-K leaves **one** captured verb unclassified ⇒ **FC11 RED at ship as ruled** | `phase2-plan.md` §2.2.1 (the pattern table gains **`read_`**; the **recomputed census** 73/104/0 + the full arithmetic) + §2.2 (the `read_tool_patterns` row) + §2.4 (the pack) + §5 (the **P2-K/FC11** pin gains the **R18 set-arithmetic leg**) + §3 (the gate-(i) hard dependency); `phase1-plan.md` §3 (the census disclosure gains **row 4** + the arithmetic); `plan-overview.md` §6 (**R18**) + §10 (**FC11** restated as satisfiable) | **FOLDED** |
| **NB1** | **FC7** needs a named pack + a written-out predicate expectation | `phase3-plan.md` §2.5 (the pack `tests/unit/tools/test_plane_agent_surface_census.py`) + §5 (**E1–E5** enumerated) | **FOLDED** |
| **NB2** | **P2-H classified two ways** | `phase2-plan.md` §5 (the **single** statement: it **IS** a mutation pin, implementation home Phase 3, validated at Phase-3 exit); `plan-overview.md` §11 (the index + the amendment-status row corrected); `phase6-plan.md` §3 (the G13 enumeration corrected) | **FOLDED** |
| **NB3** | Phase 5 §2 omits `agents/project-manager/meta.json` though A′.1 writes it | `phase5-plan.md` §2 (the file row now names `meta.json` + the "not in the baseline, but rides the atomic commit" note) | **FOLDED** |
| **NB4** | **P1's RED leg** needs a fixture with a `plane_*` tool owned by another category (today vacuous) | `phase3-plan.md` §5 (**P1** — the mandatory synthetic-fixture construction) | **FOLDED** |
| **NB5** | no **restore step** after the Phase-5 negative legs | `phase5-plan.md` §5 (the new **Step 6-R — RESTORE**, 5 assertions, mandatory even if a leg fails) | **FOLDED** |
| **NB6** | Phase-6 S3 credential provenance: a dummy key suffices | `phase6-plan.md` §3 S3 (the **dummy-key** rule: no real bearer in any staged smoke env/log/artifact; the payload assertion) | **FOLDED** |
| **NB7** | the gate-(ii) `nea`-side read is unreconciled with NB9 | `phase1-plan.md` §4 (the `nea`-side read **DROPPED** from the dev probe; **ADJ-4(ii) stays open + user-owned**, answered on the live host after the re-mint) + §8 **X1.9** | **FOLDED (DROPPED)** |
| **NB8** | "live + unset slug ⇒ account-default" unpinned in **both** lanes | `phase2-plan.md` §5 (**P3** — the MCP account-default leg **and** the REST-absent leg, asserted separately because they differ) | **FOLDED** |
| **NB9** | Phase-6 **L4** boots `ENSEMBLE_SELF_ENV=live` on a scratch root ⇒ a live-resolving root | `phase6-plan.md` §3 S3 (the **safe form**: the binary **copied into the scratch root** ⇒ the path-derived root **IS** the scratch; the live *classification* pinned explicitly; full scrub; dummy credential; the fence; and the explicit statement that L4 proves the *live-branch behaviour*, **not** a live boot) | **FOLDED** |
| **NB10** | the Phase-5 §4 census table is **abridged** | `phase5-plan.md` §4 (the **full 7** worktree key names + the note that `PLANE_*=0` was correct; and that the ambient `POSTGRES_*` set makes the per-variable scrub load-bearing) | **FOLDED** |
| **NB11** | Phase 2's local dependency row understates the gate-(i) dependency | `phase2-plan.md` §3 (gate (i) ⇒ **HARD**: it is the FC11 catalog fixture) | **FOLDED** |
| **NB12** | anchor drift: the leak gate is at `:101`; `test_plane_identifier.py` is 404 lines | `plan-overview.md` §11 (the **P10** row's anchors) + `phase2-plan.md` §2.4 (BR-A item 7: 404 lines) | **FOLDED** |
| **NB13** | Task 1 ships **config + observability, NOT code**; live/demo efficacy depends on the **user-owned** re-mint; only **dev** is armable | **The overview header** (a dedicated ⚠️ block, above the fold) + this register | **FOLDED** |

**G2-iter-002 pins added:** **P30** (Phase 4). **G2-iter-002 exit criteria added:** **X1.8**, **X1.9** (Phase 1) · **X4.11**, **X4.12** (Phase 4). **No existing pin/exit/phase/gate ID was renumbered or repurposed.**

**Anchor provenance for the G2 iter-002 fold (verified by me, read-only):** the 177-verb catalog re-extracted and the classifier re-implemented from `daemon/mcp/resilience.py:638-687` (the on-disk recompute reproduces the architect's **48/82/47** exactly); `daemon/mcp/builtin_servers/plane.py:243-258`; `.env.example:207-209` (**active** assignments today); `scripts/run_tests_scrubbed.sh:52` (the unset) + **`:101`** (the leak gate `exit 78`); `tests/unit/test_plane_identifier.py` (**404** lines); `tests/packs/plane_shell_scrub_content.sh` (**absent** from the repo); the worktree `.env` (**7** key names, `PLANE_* = 0`) and the main-checkout `.env` (**6** `PLANE_*` names), **key NAMES only, never values**; the live ports **:9797** and **:7979** (both **LISTENING**); `dev.sh:79-112`.

---

## G2 fold (approver iter-003, 2026-11-02) — FINAL FOLD — item disposition

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 003 **REJECTED** (2 blockers + 17 mechanical notes); **approver cap reached ⇒ the leader proceeds to implementation; there is NO further approver round.** **Architecture / D-1 / ADJ-1 / the phase structure / the 7-phase decomposition are NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered or repurposed.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB2** | **FOLDED** — SC6 restated: the `nea` guarantee is **structural** (the resolver + P2-D), **not observational** (the optional `nea` read was dropped). | §7 (**SC6**) |
| **NB3** | **FOLDED** — "zero `plane_*` tools" and "live byte-identical" are stated **precisely**: the live row **already materialises pre-promote** (the live `.env` carries the URL + key; `is_available()` is presence-only), and the **one** intended live difference is the Phase-3 ownership guard, which **intentionally** removes `plane_sync_project` from PM's live toolset. | §7 (**SC3**) + §8 (**NR1**) |
| **NB4** | **FOLDED** — the master index now includes **P-G2/P-G3** (and the new **P-G4**); the G13 over-scope is corrected in `phase6-plan.md` §3. | §11 (the master index) + `phase6-plan.md` §3 |
| **NB7** | **FOLDED** — the coverage map no longer attributes **config delivery** to Phases 1+3: config delivery is **Phase 5 Step 0** (the dev seeding), and the **live/demo config is operator-owned and pre-existing**. | §3 (the coverage map) |
| **NB8** | **FOLDED** — the **operator escape hatch** is now a **risk (R22)** and a required **runbook caveat**. | §6 (**R22**) + `phase4-plan.md` §2.3 (caveat 7) |

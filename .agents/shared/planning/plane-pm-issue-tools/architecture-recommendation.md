# architecture-recommendation.md — `plane-pm-issue-tools` — Phase-0 (G0) architect adjudication

**Date:** 2026-11-02 · **Adjudicator:** architect (controller) · **Base:** `941cb6321` · **Worktree:** `/home/nea/ensemble-src-wt-plane-pm`
**Analysts:** architect-worker-pathA-tradeoff (`93c6529e`), architect-worker-d1-security (`1b71d0d9`), architect-worker-e1e4-dataflow (`81203ded`)
**Status: APPROVED-WITH-AMENDMENTS.** Path A confirmed; D-1 confirmed with predicate hardening; E1–E4 confirmed with one bypass closure; phase structure stands; 30 consolidated amendments below fold into the existing phases (append-only). Binding leader rulings (ADJ-1 live-exempt, gate (ii) authorization, fail-closed refusal, AD2 out-of-scope) are incorporated and were NOT re-opened.

---

## §0 Verdict summary

| # | Commission question | Verdict |
|---|---------------------|---------|
| 1 | Path A vs B vs Hybrid | **PATH A CONFIRMED** — matrix re-scored; discharge stands for the surface; census number corrected (three-number disclosure); charter reconciliation is a hard prerequisite for Task 3 |
| 2 | D-1 resolver | **CONFIRMED with hardening** — the ladder defect is real (exact lines proven); the own-DB arm is KEPT (commission-specified, self-consistent) but its edges are hardened (opt-out short-circuit, garbage ⇒ non-live, canonical DB-name input, hard import/file-read bans); one residual disclosed |
| 3 | E1–E4 shape | **CONFIRMED with amendments** — one real bypass closed (explicit-constructor split-brain); gate chain is 4 links (stronger than plan claims); heal is conditional-on-availability; log-ownership rule adopted; ADJ-1×E4 divergence closed by a slug-equality heal trigger |
| 4 | FC1–FC6 | **ALL AMENDED** (detectability/owners/thresholds) + **FC7–FC11 adopted** |
| 5 | Privileged category | **CONFIRMED no change** — non-privileged external-resource category; caveat recorded (read surface universe-wide on live) |
| 6 | Risk register | R1–R17 amended/prioritized + **R18–R21 added** |
| 7 | Amendments | 30 consolidated items in §7; phase structure stands |

---

## §1 Path A vs Path B vs Hybrid (commission §1)

### 1.1 Inventory claim validation

- **177 total / 8 work-item CRUD / 5 comment / 10 cycle / `is_draft` present / zero `*_issue` — all CONFIRMED** (re-counted from disk by the analyst).
- **The "116 of 177 write-capable" census is WRONG in three incompatible ways** and must not be frozen as a gate criterion:
  1. The inventory's *stated* method (read = list/retrieve/get/count/search/read) yields **104 write / 73 read**.
  2. The stated 61/116 pair silently excludes 12 plainly read-only verbs (`get_*`×11, `read_*`×1) from the read set.
  3. The daemon's *actual* classifier (`daemon/mcp/builtin_servers/plane.py:242-258` + `daemon/mcp/resilience.py:638-687 is_read_tool`) partitions 177 as **48 read / 82 write / 47 UNCLASSIFIED** — and `is_read_tool` returns False for the unclassified, so they are **dropped** from every non-full-access agent's schema list.
- The classifier itself is written against a **non-existent vocabulary**: `add_`/`edit_`/`assign_` write patterns match zero catalog verbs, and its docstrings name `plane_create_issue`/`plane_assign_issue`. → Amendment **P2-K** (in-scope reconciliation) + new risk **R18**.

### 1.2 Path-B-trigger discharge — STANDS (surface), with two riders

- `create_work_item` requires **only `name` + `project_id`**; `is_draft` optional — the DRAFT use case is directly expressible.
- Discovery chain is present: `list_projects`, `list_states`/`retrieve_state`, `list_work_item_types`/`retrieve_work_item_type`/`resolve_work_item_type`, plus members/labels/search. **Rider 1:** the inventory records required-params only for work-item/comment/cycle verbs — discovery-verb params are an *inference*; gate (i) re-capture must record them (Amendment **P1-A**).
- **Rider 2:** PM's charter names SIX verbs that do not exist (`plane_list_issues` ×4; `plane_create/update/delete_issue`, `plane_add_comment`, `plane_remove_comment`, `plane_assign_issue` — there is no `add_`/`remove_`/`assign_` verb). Charter reconciliation (Phase 4) is a **hard prerequisite** for the Task-3 turn; the discharge statement should say so explicitly.
- **Empirical confirmation of R7:** PM's `allow:["plane"]` resolves **178** names with the server registered (177 MCP + `plane_sync_project` leaked via the unguarded dynamic-prefix sweep) vs **1** with it absent — same `meta.json`, two toolsets. Evidence class raised from code-read to **executed**. Also: `tools_note.md:24` ("PM does not hold plane_sync_project") is false today.

### 1.3 R6 prompt-surface mechanics — answered precisely

**No — only PM's prompt carries plane schemas.** Code trace: schema-level strip (`mcp_service.py:1243-1313`; PM opts out via `mcp_full_access` at `:1310-1312`) → per-instance tool list (`instance.py:7065-7084`) → name filter (`:7104`, `:7218-7233`) → `graph.py:10531/:10549` `llm.bind_tools(tools)` per turn; tool *names* additionally enter the system prompt via `loader.py:704-706`. Agent census: PM is the only `mcp_full_access` holder; the only other plane mention is worker's `plane_sync` *category*.

| Class | Schemas bound per turn | Names in system prompt |
|---|---|---|
| PM (full_access + allow) | **177** (write included) | **178** (incl. the R7 leak) |
| `allow:"plane"` without `mcp_full_access` | **48** — and the 47 unclassified (incl. **every** `retrieve_*`) are silently dropped | 48 |
| Neither | 0 | 0 |

The planner's "10–18 k tokens" is plausible but **unverified** (the capture recorded property names only, never schema bodies). Token counts must be captured at re-capture (P1-A). Note the read-strip-everyone semantics the commission took for granted are only coherent **after** P2-K fixes the classifier.

### 1.4 5-axis matrix (re-scored) — Path A wins

| Axis | A: remote MCP | B: first-party REST | Hybrid | Winner |
|---|---|---|---|---|
| Complexity | ≈0 marginal (enforcement common to all paths) | new module + factory + registry rows + **an invented REST contract** (PlaneHttpClient has exactly 4 project methods, re-verified) | A+B | **A** |
| Scalability | 177 schemas/turn **PM-only**; names in system prompt; upstream does the work | ~6–10 small schemas but daemon-side REST round-trips on the loop (the v0.27.14 class) | both costs | **B** (prompt) / **A** (infra) — tunable via A′ |
| Maintainability | upstream-owned catalog; **drift cost larger than stated** — the daemon classifier itself is drifted (P2-K pulls this in-scope, narrowing the gap) | daemon-owned stable contract; must track Plane REST drift itself | two contracts | **B**, gap narrowed by P2-K |
| Risk | operational (dead live key — already realised; user-owned re-mint) + the 47-verb read hole (R18) + unmeasured prompt budget (FC9) | structural (contract authored from zero; second credential class; new shipped code) | union | **A** |
| Cost | zero code for the surface; zero new keys under A | new code + second credential per env + maintenance | highest | **A** |

**Discharge stands; Path B is not opened.** Corrections are to magnitude, measurement, and scope — not the verdict.

### 1.5 A′ ruling (ADJ-5) — the third option

ADJ-5's binary (full-now vs narrowed-now) is rejected. **Ruling: full category at ship + mandatory numeric baselines + a ceiling at which A′ becomes the DEFAULT outcome of Phase 5, not a judgement call.**

- Phase 5 records: per-turn prompt tokens for PM, tool-call error rate over a stated N of real turns, mis-selected-verb count (Amendment **P5-C**).
- FC9 defines the ceiling (absolute tokens/turn and % growth vs pre-Plane baseline).
- The A′ keep-set is **pre-derived now** (~28 of 177; the charter's reconciled verb vocabulary: work-item CRUD + comments + cycles + assignee/manage + the discovery/typing reads; prune the customer/release/project-admin/module/milestone/initiative/page/property families and out-of-scope deletes) and is recorded so an A′ flip needs no new analysis.
- A′ mechanics CONFIRMED (literal names inside a populated dynamic category; empirically executed). A′ **incidentally removes the R7 leak**. But A′ converts upstream renames into **silent capability loss** → **FC8 mandatory iff A′ adopted** (minimum-toolset assertion + gate-(i) drift detector).
- Ceremony note: `meta.json` is NOT in the 161-file prompt-artifact baseline (verified) — but an A′ flip forces a charter edit, so the re-stamp + SEALED re-pin rides the A′ commit (R8 unchanged).

---

## §2 D-1 resolver (commission §2)

### 2.1 The ladder defect — CONFIRMED at exact lines

`_auto_derive_env` (`daemon/tools/upgrade_tools.py:440-560`): the own-signal-first rule is gated on `home.name in _LIVE_INSTALL_DIR_NAMES` (`:535-544`) and therefore **does not apply** in the operator-HOME shape (`/home/nea`); the lanes loop (`:546-554`) reads the **sibling** live install's `.env` (`_install_root_candidates` `:395-418` → `/home/nea/agents-ensemble`, `POSTGRES_DB=ensemble_prod`) and returns `"live"` at `:554` **before** the dev-shape signal at `:556-558` can run. The docstring (`:467-479`) asserts the opposite intent and is **false** for this topology. Corroborated in-repo by `boot_subprocess_sweep.py:150-158`. The planner's W-3/D-1 premise is correct.

### 2.2 Predicate ruling — KEEP the own-DB arm, harden its edges

The leader's commission specified the two-arm predicate ("explicit live marker **or** own ensemble_prod DB"). Worker B recommends dropping the arm (marker-only). **Ruling: KEEP the arm** — its semantics are self-consistent: if the daemon's own main DB resolves to `ensemble_prod`, the process genuinely serves prod, so `live` is the correct label for workspace targeting; the arm's only behavioral delta on this host is the ambient-poisoned bare-source-mode case, which the env-poison root-fix commission (AD5 family, standing critical note) owns — not this guard. But the predicate text as written has genuine construction fail-opens that MUST be closed (Amendments **P2-A/P2-B**):

- **Opt-out short-circuits:** `ENSEMBLE_SELF_ENV ∈ {0,false,no,off}` is an explicit *non-live* attestation ("no auto-detection") — it must resolve non-live **before** the own-DB arm. As written, opt-out + `POSTGRES_DB=ensemble_prod` ⇒ LIVE = fail-open.
- **Garbage markers ⇒ non-live/unclassified + WARNING, never the own-DB arm** (`"Live"`, `"prod"`, typos; exact non-normalizing match against `VALID_ENVS = ("dev","demo","live","sandbox")`; whitespace stripped, case NOT folded).
- **Empty marker = absent** (house `_read_marker_value` semantics) — empty marker + own-db=prod ⇒ live.
- **Own-DB input mirrors daemon precedence:** `POSTGRES_DB` → `ensemble.json` `pg.db` (`daemon/repositories/factory.py:345-350`, `daemon/ensemble_config.py:126`). A resolver reading only the env var mis-classifies an env-var-less prod install as non-live — a live-continuity break. The resolver may read the daemon's **own** `ensemble.json` for this one input; **all other filesystem reads are banned**, as is importing `daemon.tools.upgrade_tools` (grep pin + zero-file-access test — the spec as written permits both accidents).
- **One canonical slug normalization** (trim + strip one outer matching quote pair) consumed by BOTH lanes — today REST (`plane_http_client.py:125-143`) and MCP (`daemon/mcp/config.py:64-85`) differ, so one env value can target two workspaces. Re-point the quote-sanitization pin at the resolver.
- **Resolve once per operation, thread the resolution** (env TOCTOU between `is_available()` and `get_base_config()`); injectable signature `resolve_plane_workspace(env=None, *, postgres_db=None)` for the gate-(iv) matrix.

### 2.3 Host-shape census (final)

| Shape | Class | Correct |
|---|---|---|
| live install (explicit `live` + own prod db) | live ⇒ exempt | ✅ byte-identical (verified against live `.env` key census) |
| demo (explicit `demo`) | non-live ⇒ e2e | ✅ |
| dev checkout (no marker, `ensemble_dev`) | non-live ⇒ e2e | ✅ |
| dev + ambient `POSTGRES_DB=ensemble_prod`, launcher-started (.env loads) | non-live | ✅ |
| dev + ambient prod, **bare source-mode** (no .env load) | live via own-DB arm | ⚠️ self-consistent (daemon genuinely serves prod) — **disclosed residual**; root fix = env-poison commission |
| worktree (fenced .env, `ensemble_dev`) | non-live ⇒ refuse-unset-slug | ✅ |
| frozen staged boot (stage.sh stages marker) | sandbox ⇒ e2e-only | ✅ |
| explicit opt-out / garbage / unknown | non-live, fail-closed | ✅ after P2-A |
| `ENSEMBLE_SELF_ENV=live` on non-prod host (operator error) | live | accepted — the marker is the operator's attestation (house precedence) |

Exactly **one** fail-open construction survives (opt-out+prod-db) and P2-A closes it; exactly **zero** live-continuity breaks remain after P2-B. The `boot_subprocess_sweep` precedent is **consistent** (auto-derived `live` never grants prod authority; `sys.frozen` is not a drop-in — a staged sandbox binary is frozen too).

**Live continuity: CONFIRMED byte-identical** (arm 1 fires on live; unset-slug account-default preserved under live-exempt; REST lane inert — `PLANE_API_KEY` absent on live). **ADJ-1 live-exempt: CONFIRMED, no safety contradiction found.**

---

## §3 Enforcement shape E1–E4 (commission §3)

- **E2 chokepoints:** the four production sites confirmed; **anchor drift fixed** (raw fallback is `:353-356`, `__init__` `:326-361`). No cached/module-level client (verified module docstring + sole construction site `plane_sync_service.py:391`). **One real bypass closed (P2-D):** `PlaneHttpClient.__init__` accepts explicit `base_url`/`workspace_slug` (`:329-330`) which bypass the resolver — worst case a split-brain URL-says-e2e/header-says-nea. Ruling: gate `__init__`/`create()` — a refused resolution ⇒ **inert client** (existing guards raise `PlaneAPIError`), and explicit params may never introduce a slug the resolver did not return. Phase-5 N-5's mechanism-level assertion is then true as written. `daemon/routers/settings.py:703-716` (FE `PLANE_BASE_URL` echo) is not a slug leak — documented in the runbook (P4-B), no FE change this lane.
- **E3 gate composition:** ordering confirmed (`super().is_available()` quarantine consult first, `plane.py:291-292`). The chain is **four links, all keyed on the same classmethod** (`manager.py:3073` skip; `manager.py:3328` warmup; `mcp_service.py:647` schema warm; `mcp_service.py:1200` preload gate) — the real guarantee is "**no tools even with a stale row**", stronger than the plan claims (P2-F). `get_base_config` emitting the resolved slug regresses no sanitizer provided the resolver applies the canonical normalization (P2-C). `is_available()` has 5 call sites, never per-request — a pure resolution is free; it must not log per call (one-shot pattern).
- **Stale-row disposition (P2-G):** the skip branch leaves a pre-existing ACTIVE row carrying the old prod slug, visible unredacted via `GET /api/mcp-servers` (AD2 gap), and the layer-3 registry-vs-row cross-check would read it as *registered* — it would not fire on exactly the case the lane exists for. **Ruling: deactivate the pre-existing plane row on a workspace refusal** (mirror the `MCP_DISABLE_BUILT_IN_*` path, `manager.py:3057-3066`).
- **E4 heal:** headers are template-derived and rebuilt from the current env; only the `env` axis is operator-preserved — **CONFIRMED**, and the bump is **per-row scoped** (webfetch/context7 untouched). **Correction (P2-H):** the heal runs only when `is_available()` is True — a refusal **defers** the heal; Phase-5 Step 3 must sequence allowed-boot-first. **ADJ-1×E4 divergence closed (P2-I):** the heal is one-shot keyed on schema version; under live-exempt a permitted-slug change leaves the persisted header stale. Add a plane-scoped slug-equality trigger (row header slug ≠ resolved slug ⇒ heal). **R17 disclosure accurate** with one added clause (P2-J): the row's bearer is env-derived and **re-derived (overwritten) on every heal**; no new copy, no redaction-posture change.
- **Log ownership (P3-A/P3-B):** the path has **5 emitters**, the plan names 2. Adopted rule: **R-1** resolver never logs (returns typed reasons); **R-2** `plane.is_available()` emits at most one process-wide envelope notice (label + source + effective slug, never the reason); **R-3** the manager skip WARNING is the ONLY reason interpolator, with **three distinct causes** — *deliberately disabled* / *unexpectedly unavailable* / **workspace refused (policy)** (a refusal is not a missing-env-var case); **R-4** the registry-vs-row cross-check emits agent + server + pointer, no re-interpolation (policy-refused stays WARNING — it still costs the agent its tools); **R-5** the pre-existing `mcp_service.py:652-657`/`:1221-1237` INFOs stay, documented as different facts.
- **Placement:** dedicated module `daemon/plane_workspace.py` CONFIRMED (no import cycle, no heavy deps from either consumer; marker read is a pure env read; `upgrade_tools` correctly excluded — it pulls `langchain_core`). W-4 holds: borrow `LiveViewsRootConfig`'s shape, never its raising failure mode.
- **`plane_sync_service.unavailable_reason` (P2-L):** must carry the resolver's refusal reason — otherwise the REST 503 and the agent tool report "API key not set" for a workspace refusal. This resolves phase2 §2.5's self-contradiction (the file is already in Phase 2's blast radius).
- **No-DDL (C5): CONFIRMED** — only the existing `McpServer` row mutates; no `.sql`, no model, no `config.yaml`; migration delta EMPTY ⇒ delta-empty-legitimate rollback adjudication. **With the behavioral clause added (P6-A):** a rollback restores the OLD binary which has no resolver and **re-heals the row to schema "2" with the RAW env slug** — on a non-live install a rollback re-opens P1/P2/P3 until the next promote. Disclose-and-accept.

---

## §4 Flip conditions (commission §4)

| FC | Verdict | Amendment |
|----|---------|-----------|
| FC1 | AMENDED | name the owner (leader); make the charter keep-set machine-readable so FC1 is a set-difference |
| FC2 | AMENDED | unobservable as written — trigger a re-capture on observable `serverInfo.version` change (3.2.0 recorded), or downgrade to named-owner judgement |
| FC3 | AMENDED | no metric/threshold/baseline — fixed by FC9 numerics + P5-C baselines; R6 owner carried onto FC3 |
| FC4 | AMENDED | no counter ships; condition unevaluable on live while R1 open — ship a session-failure counter or declare the measurement source + owner; record UNOBSERVABLE-while-R1 |
| FC5 | AMENDED | conflated with R1 — split the 401 taxonomy (`invalid_token` ⇒ R1; other 401/403 or per-tool auth ⇒ FC5 candidate) |
| FC6 | AMENDED | name the security owner |

**Adopted new:** **FC7** plane surface leaking to a non-PM agent (detector: per-agent resolved-toolset census + pin "no agent other than PM resolves any `plane_*` MCP tool"); **FC8** A′ silent-zero/rename-loss (mandatory iff A′; minimum-toolset assertion ≥ keep-set size + gate-(i) drift detector); **FC9** prompt-budget breach (absolute tokens/turn + % vs pre-Plane baseline — FC3's numeric anchor); **FC10** catalog drift (record count AND sha256 of the sorted name set at every re-capture); **FC11** classifier-coverage gap (assert every catalog verb ∈ read ∪ write ∪ documented-unclassified).

---

## §5 Privileged category (commission §5) — CONFIRMED no change

`"plane"` stays a **non-privileged external-resource category**. The privileged tier is calibrated to **daemon-owned** blast radius (mutates daemon state, audited in daemon tables); Plane's blast radius is **upstream-owned** — a daemon-side ledger would be a second, weaker source of truth beside Plane's authoritative activity log. Irreversibility of `delete_work_item` argues for narrowing *who may write* — which the lane already achieves (PM-exclusive `mcp_full_access`, the only one in the tree; read-only default for everyone else; env-scoped targeting) — not for the privileged tier, which would not reduce an authorized PM write's blast radius at all. No pin ceremony; the privileged 16-set is untouched. **Recorded caveat (non-blocking):** via the empty-allow universe branch (`instance.py:513-520`), **every** agent with no `tools.allow` can *read* the plane workspace on live today — the guard governs *where*, not *who*; a future lane may narrow the read audience. Also recorded: a user-created MCP server literally named "plane" is gated by the same name-keyed preload gate (blocked on non-live) — the REST create endpoint remains an operator-authority escape hatch, alongside the explicit marker.

**Kill-switch: CONFIRMED none.** The emergency live→e2e case is already permitted under live-exempt; a broken-resolver case recovers by setting `ENSEMBLE_SELF_ENV=live` (one auditable line, arm 1). "Always compute + always log the effective slug and classification" is kept as a **design constraint**, not a switch. If one is ever forced: default-ON, refusal-only, and it may never make a non-live environment prod-reachable (it may relax slug policy inside an already-live env only).

---

## §6 Risk register (commission §6) — amended + prioritized

| # | Risk | Sev | Amendment |
|---|------|-----|-----------|
| R1 | live/demo Plane MCP credentials dead (401) — presence-only gate yields a row with failing sessions | High | unchanged (user-owned re-mint; observability is the lane's part); FC4/FC5 amended so the condition is observable later |
| R2 | upstream verb drift; daemon docstrings repeat it | High | scope now ALSO includes the classifier patterns (P2-K), not only docstrings |
| R3 | silent INFO skip | High | superseded by the R15 contract (§3 log ownership) |
| R4 | env misclassification fail-open | High | D-1 CONFIRMED; residual = bare-source-mode ambient poison (disclosed; env-poison commission owns the root fix) |
| R5 | stale row keeps prod slug | Med | E4 confirmed; conditional-heal correction (P2-H) + slug-equality trigger (P2-I) + stale-row deactivation (P2-G) |
| R6 | 177-tool prompt surface | Med | **PM-only** (not every agent); token estimate unverified → measure (P1-A/P5-C); A′ default-on-breach (§1.5) |
| R7 | prefix-sweep collision | Med | **CONFIRMED BY EXECUTION** (178 vs 1 by server state); Phase-3 guard stands; A′ incidentally removes it |
| R8 | charter/baseline coupling | Med | unchanged; A′ flips also ride the ceremony (charter edit forces re-stamp) |
| R9/R10 | D6/D7 pre-existing | Med | unchanged (D7 folded into the R15 contract: test-connection must consult the resolver, P3-D) |
| R11 | privileged change | Low | CONFIRMED none (§5) |
| R12/R13 | bearer in GET / second credential | Med/Low | AD2 out-of-scope stands with escalation on record; both workers re-flag it as **pre-existing** (the lane neither creates nor fixes it) |
| R14 | dev = only armed env | Med | unchanged; Task 3 runs on dev |
| **R15** | refusal visually identical to the original silent-absence bug | **High** | **Observability contract adopted:** three distinguishable states — (i) *refused*: envelope notice + skip WARNING with the **typed policy reason verbatim** (pinned) + **no active row** (stale row deactivated); (ii) *never registered*: WARNING with missing env-var names, no row; (iii) *dead credentials*: row present + connect 401 + schema-lookup WARNING; test-connection reports the refusal instead of lying (P3-D). The bootstrap WARNING is the surface — no boot-summary construct exists (own it) |
| **R16** | fail-open class | Med | census closed: one construction fail-open (opt-out+prod-db) closed by P2-A; garbage/empty/normalization edges pinned; hard bans (no upgrade_tools import, no filesystem reads beyond own ensemble.json) with tests |
| **R17** | schema-bump re-persists bearer | Low | disclosure accurate; extended clause: bearer is env-derived and re-derived on every heal (only `env` is preserved) |
| **R18** | **read-only-subset trap** — 47 verbs unclassified ⇒ any future non-full-access grant silently loses every `retrieve_*` | Med-High | closed by P2-K (classifier reconciliation) + FC11 pin |
| **R19** | **A′ rename-loss** — narrowed grant converts upstream renames into silent capability loss | Med (iff A′) | FC8 minimum-toolset assertion |
| **R20** | **behavioral rollback** — old binary re-heals the row with the RAW env slug; non-live re-opens P1–P3 until next promote | Med | disclose-and-accept (P6-A); delta-empty schema adjudication unaffected |
| **R21** | **ADJ-1×E4 divergence** — permitted-slug change on live leaves a stale persisted header (heal is one-shot) | Low-Med | closed by the slug-equality heal trigger (P2-I) |

---

## §7 Consolidated amendments (fold into phases; append-only)

**Phase 1:** P1-A gate-(i) re-capture records discovery-verb required-params, per-tool schema token counts, sorted-name-set sha256; P1-B census replaced by the three-number disclosure (104/73 stated-method; 82/48/47 daemon classifier) and gate-(i) criterion 4 re-worded to **set-membership** ("no verb in PM's reconciled keep-set is absent").
**Phase 2:** P2-A predicate edge hardening (opt-out short-circuit; garbage ⇒ non-live+WARNING; empty=absent; exact VALID_ENVS); P2-B canonical DB-name input (POSTGRES_DB → own ensemble.json pg.db) + hard bans (no `upgrade_tools` import; no other file reads; grep pin + zero-file-access test); P2-C canonical slug normalization in both lanes + pin re-point; P2-D constructor bypass closed (refused ⇒ inert client; explicit params never introduce an un-resolved slug); P2-E anchor drift (`:353-356`, `:326-361`); P2-F state the 4-link gate chain; P2-G deactivate stale ACTIVE row on workspace refusal; P2-H conditional-heal correction + Step-3 resequencing; P2-I slug-equality heal trigger; P2-J R17 extended clause; P2-K classifier reconciliation (read += `retrieve_`, `count_`, `search_`; write += `manage_`,`transfer_`,`complete_`,`link_`,`import_`,`attach_`,`detach_`,`upload_`,`resolve_`; drop `add_`/`edit_`/`assign_`; coverage pin) — **new daemon-code item, now in scope**; P2-L `plane_sync_service.unavailable_reason` carries the refusal reason (resolves §2.5 contradiction); P2-M injectable resolver signature + resolve-once threading.
**Phase 3:** P3-A log-ownership rules R-1…R-5; P3-B add the 3 unnamed emitters to the log table; P3-C typed reason verbatim in the skip WARNING with three causes + P5 pin; P3-D test-connection consults the resolver; P3-E cross-check pointer-only + policy-refused bucket.
**Phase 4:** P4-A fix `.env.example` (`:186-190` "silently targets account-default" becomes false post-Phase-2; `:209` sample `your-workspace-slug` becomes a hard refusal everywhere ⇒ ship `e2e` + "live requires nea" comment); P4-B runbook: deploy pin is defence-in-depth (covers only deploy-generated .env); FE Plane indicator reflects env presence, not resolution.
**Phase 5:** P5-A restate N-4/P15 ("no NEW row + pre-existing row's `config_schema_version` unchanged + no `plane_*` in PM's **resolved** toolset + WARNING logged"; the dev daemon already has a row) + assert `mcp_tool_names` stored-fallback carries no stale `plane_*`; P5-B N-5 holds via P2-D (no weakening needed); P5-C numeric baselines (prompt tokens/turn, tool-call error rate, mis-selected-verb count) + FC9 ceiling making A′ the default outcome on breach.
**Phase 6:** P6-A behavioral-rollback disclosure clause.
**Blast radius:** BR-A add the 6 breaking test files to Phase 2's Tier-1 blast radius with pre-agreed remediation: `tests/unit/test_plane_sync.py`, `test_plane_sync_phase3.py`, `test_plane_sync_phase4.py`, `tests/integration/test_plane_sync_endpoint.py`, `test_mcp_quote_sanitization.py`, `test_mcp_service.py` (unclassified test envs become non-live ⇒ refused).

---

## §8 Open questions — ruled

1. **A′ default-or-not** → third option (§1.5): full at ship, numeric ceiling, A′ default-on-breach; keep-set pre-derived.
2. **Classifier reconciliation in scope?** → YES, Phase 2 (P2-K). Without it the leader's "read strip applies to everyone" premise is incoherent — 47 verbs are neither read nor write today.
3. **Deactivate stale row on refusal + close constructor bypass?** → YES both (P2-G, P2-D). Neither changes live behaviour.
4. **May the resolver read `ensemble.json`?** → YES, the daemon's OWN config file, for exactly one input (pg.db), mirroring `factory.py` precedence; every other filesystem read banned (P2-B).

## §9 What does NOT change (binding rulings reconfirmed)

ADJ-1 live-exempt CONFIRMED (byte-identical live verified; E4 interaction closed by P2-I instead). ADJ-2 lane-local D-1 CONFIRMED (AD5 shared-oracle follow-up stands). ADJ-3 AD2 out-of-scope CONFIRMED with the escalation on record — both analysts independently re-flag the pre-existing live bearer; the one-file redaction follow-up stays high-priority. ADJ-4 user-owned, carried as the disclosed live-efficacy precondition. Fail-closed typed refusal on non-live wrong-or-unset; sandbox ⇒ e2e-only; no kill-switch. Gate (ii) `list_projects` authorization proceeds per the leader. The lane stages, never promotes. C5 no-DDL CONFIRMED (delta-empty-legitimate rollback adjudication + P6-A behavioral clause). Phase structure 0–6 STANDS.

## §10 Evidence class + degradation notes

All three analysts worked read-only in the worktree; one executed the real `resolve_tool_filter` against synthetic name sets (empirical). **DEGRADED — skill bank miss:** the bank has no skill named `security-design` (worker B: NO SKILL LOADED) and none named `data-flow-design` (worker C self-served a substitute, `c267873a` "Plan Missed-Items Data-Flow Sweep", via `skill_search`). Both reports were adjudicated complete on evidence (file:line anchors throughout; no re-dispatch needed). Same family as the known `structural-design` gap (critical note 257488dc) — the design-skill bank needs the execution-skill names added or remapped (follow-up for the skill-bank lane). Outside-the-worktree reads were limited to `.env` key names + 4 non-secret values for the host-shape census; no secrets were read or printed; no Plane network calls were made.

## §11 decisions.md fold entries (for the planner — append verbatim, never renumber)

- §2 append **W-13 (G0 ruling):** "Path A CONFIRMED; Path-B discharge stands for the surface with two riders (discovery-verb params captured at re-capture; charter reconciliation a hard prerequisite for Task 3); the 116/177 census replaced by the three-number disclosure (104/73 stated-method, 82/48/47 daemon classifier); A′ = third option (full at ship + numeric FC9 ceiling + A′ default-on-breach, keep-set pre-derived ~28); FC7–FC11 adopted; R18–R21 added. Source: architect G0 2026-11-02, architecture-recommendation.md §1/§4/§6."
- §2 append **W-14 (G0 ruling):** "D-1 CONFIRMED lane-local with predicate hardening (opt-out short-circuit; garbage ⇒ non-live; empty=absent; DB-name input POSTGRES_DB → own ensemble.json pg.db; hard bans on upgrade_tools import and non-own file reads); own-DB arm KEPT (self-consistent semantics; ambient-poison residual disclosed, root fix owned by the env-poison commission). Source: architecture-recommendation.md §2."
- §3 close **ADJ-1** (live-exempt CONFIRMED — leader-ruled, no contradiction found; E4 divergence closed by the slug-equality heal trigger instead), **ADJ-2** (lane-local), **ADJ-3** (out-of-scope stands, escalation on record), **ADJ-5** (amended to the third option). **ADJ-4** remains open/user-owned (disclosed precondition).

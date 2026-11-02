# Phase 1 — Verification gates (i) / (ii) / (iii)

**Lane:** `plane-pm-issue-tools` · base `941cb6321` · **Tier:** read-only probes + a scratch-install drill (no repo diff ⇒ no regression leg; the drill runs on a scratch, not the repo)
**Depends on:** Phase 0 (G2) · **Blocks:** Phase 2 (gate (ii) hard-blocks the non-live ⇒ `e2e` pin), Phase 4 (gate (i) fixes the charter vocabulary)

---

## 1. Objective

Close the three gates the wave-1 analysis left open, **before** any enforcement code ships, so the lane never converts a "wrong workspace" failure into a "no Plane at all" failure.

| Gate | Status at plan time | What it decides |
|------|--------------------|-----------------|
| **(i)** full remote `tools/list` inventory | CAPTURED 2026-11-02 (wave 1); **must be re-captured at implement time** | the vocabulary source of truth for the Phase-4 charter rewrite; the FC1 mechanical detector |
| **(ii)** `e2e` workspace existence + **MCP credential reachability** | REST leg PASSED (wave 1); **MCP leg UNVERIFIED** | whether the non-live ⇒ `e2e` pin is satisfiable at all |
| **(iii)** live `mcp_servers` row state + heal behaviour | **NOT RUN** | whether the live row already carries a stale slug (P4) and whether E4's heal re-points it |

---

## 2. File-level scope

**This phase writes NO repo source.** Its artifacts are **evidence notes** in the planning dir + one probe script under the planning dir (never under `daemon/`).

| File | Action | Detail |
|------|--------|--------|
| `.agents/shared/planning/plane-pm-issue-tools/gate-i-tools-list-recapture.md` | **NEW** | the re-captured inventory (tool count, full name list, the input-schema property names for the charter-relevant verbs). **No secrets.** |
| `.agents/shared/planning/plane-pm-issue-tools/gate-i-keep-set.json` | **NEW** | **> (G2 fold, approver iter-003 — blocker B1: the keep-set becomes a GATE-(i) ARTIFACT, produced HERE.)** The **machine-readable keep-set** — the **28** verbs in **both** forms (raw verb + `plane_`-prefixed daemon name), the pruned count, and the sorted-name sha256. **The gate produces what it consumes**: criterion 4 is evaluated against THIS artifact, **Phase 4 §2.1 consumes it as the charter's vocabulary input**, and the A′ branch consumes it as the **FC8 minimum-toolset** input. **The enumeration itself is in §3.** **No secrets.** |
| `tests/fixtures/plane_catalog.json` | **NEW** *(G2 fold, approver iter-003 — non-blocking item 12: the catalog FIXTURE needs a producer, or the Phase-2 coverage pack would consume an artifact nobody makes.)* | the **committed** copy of the gate-(i) re-capture (the sorted name list + the per-tool schema property map), written **verbatim** from `gate-i-tools-list-recapture.md` / the re-capture output. **Why it is committed rather than read from the planning dir:** the Phase-2 coverage pack (`tests/unit/test_plane_tool_classifier_coverage.py` + the loader `tests/helpers/plane_catalog_fixture.py`) must be **hermetic** — it must not depend on a planning-dir file that is not shipped. **The gate produces both forms**; the planning-dir artifacts are the evidence, the fixture is the test input. **No secrets.** |
| `.agents/shared/planning/plane-pm-issue-tools/gate-ii-e2e-mcp-reachability.md` | **NEW** | the HTTP status + the project identifiers. **No keys.** |
| `.agents/shared/planning/plane-pm-issue-tools/gate-iii-row-heal.md` | **NEW** | before/after row snapshots (**bearer redacted**) + the captured WARNING line |
| `.agents/shared/planning/plane-pm-issue-tools/probe_plane_mcp.py` | **NEW** (planning dir) | a **read-only** probe helper (`initialize` → `notifications/initialized` → `tools/list`, plus the single authorised read verb for gate (ii)). Lives in the planning dir so it is **never shipped**. |
| `daemon/**`, `agents/**`, `scripts/**` | **NO CHANGE** | this phase ships no daemon/agent/script code |
| `tests/**` | **AMEND (fixture only)** | the single exception: `tests/fixtures/plane_catalog.json` (the committed catalog fixture above) — a **data fixture**, not code, and not a test. |

---

## 3. Gate (i) — full remote `tools/list` re-capture

**Procedure.** From an environment with a working `PLANE_MCP_API_KEY`, run MCP `initialize` → `notifications/initialized` → `tools/list`. Record: tool count, the full name list, and the input-schema property names per tool. **No other verb may be called. Never echo the key.**

> **> G2 fold (approver iter-001, 2026-11-02) — blocker B1(f): the probe environment is now NAMED, and it is NOT the worktree.**
>
> | Gate | The environment used | Why |
> |---|---|---|
> | **Gate (i) — this re-capture** | **`/home/nea/ensemble-src` (the main checkout, dev-context)** — the **armed** env (it carries the six `PLANE_*` keys; the **fenced worktree carries none** and is **not** booted for this probe) | it is the only env with a working MCP key; the probe is read-only (`tools/list` only) and touches no daemon |
> | **Gate (ii) — the `e2e` reachability probe** | **the same main checkout** | same reason; the probe is a single authorised read-only MCP verb (`list_projects`) |
> | **Phase 5 — the Task-3 run** | **`/home/nea/ensemble-src-wt-plane-pm` (the fenced worktree)**, **seeded first** per `phase5-plan.md` §4 Step 0 | Task 3 needs the **branch's code** running as the daemon; the worktree is the fenced dev context |
>
> **The main checkout is the PROBE + SEEDING source; the fenced worktree is the EXECUTION env.** They are not interchangeable, and the pre-fold text ("from an environment with a working `PLANE_MCP_API_KEY` (dev context)") was ambiguous between them — the ambiguity is closed here. **Key NAMES only, never values, in every artifact this gate produces.**

> **> G2 fold (approver iter-001, 2026-11-02) — blocker B3: this gate also ACQUIRES the FC9 baseline.** The `baseline_tokens` measurement (PM's per-turn prompt token count with the **plane category ABSENT**) is captured **here**, at the gate, because this is the only point at which the category can be absent *by construction*. **Procedure:** boot the daemon with **`PLANE_MCP_URL` unset** ⇒ the builtin is unavailable ⇒ **the row never registers** ⇒ zero `plane_*` tools ⇒ measure PM's per-turn prompt token count on that boot (the tokeniser of the LLM lane; the prompt assembly point is `llm.bind_tools(tools)` — `daemon/graph.py:10531`/`:10549` — plus the tool **names** in the system prompt via `loader.py:704-706`). **Record `baseline_tokens` in the gate artifact** ⇒ it becomes the `fc9_baselines.json` input consumed by the Phase-5 FC9 evaluation. **If this measurement is not captured, FC9 is UNEVALUABLE at Phase 5** (the executor must **not** substitute a guess). **The measurement point is a Phase-1-gate architect-ratification item; the FC9 ceiling RULE itself is already folded** (`phase5-plan.md` §5 Step 3) and is **not** deferred to that ratification.

> **> (G2 fold, approver iter-002, 2026-11-02 — BLOCKER B2: the baseline boot is now DESIGNATED, FENCED, and no longer contradicts this section.)**
>
> **The contradiction the approver found (and the reconciliation).** This gate's own table above says the gate-(i) probe *"touches no daemon"* — **correct for the probe** (it is a `tools/list` JSON-RPC call, no daemon, no boot). But the **FC9 baseline acquisition** (the block immediately above) **BOOTS the daemon**. The two sentences described one gate and were mutually exclusive. **The reconciliation, binding from here:**
>
> | Activity | Touches a daemon? | Environment |
> |---|---|---|
> | **Gate (i) — the `tools/list` re-capture** | **NO** — a direct JSON-RPC call; no daemon, no boot, no port | the **main checkout** (read-only use of its `.env` key names/values for the probe) |
> | **Gate (ii) — the `list_projects` reachability probe** | **NO** — same shape | the **main checkout** |
> | **Gate (iii) — the live row read + heal drill** | **YES** — a scratch boot | the **scratch root** (fenced below) |
> | **FC9 baseline acquisition** | **YES** — a scratch boot | the **scratch root** (fenced below) |
>
> **The phrase "touches no daemon" is retained, and now scoped explicitly to the two probes** (it is true of them); the baseline acquisition is a **separate activity with its own fenced environment**.
>
> **THE BASELINE BOOT ENVIRONMENT (designated).** The FC9 baseline boot runs in a **dedicated scratch sandbox**, created fresh for the gate and destroyed at its end:
>
> | Item | Value / rule | Anchor |
> |---|---|---|
> | **Root** | a **fresh `mktemp -d` scratch root** — **NEVER** the live install path (`~/agents-ensemble`), **NEVER** `~/ensemble-src` | the frozen-binary path-derivation hazard: a staged/frozen binary derives its root from `sys.executable.parent.parent.parent`, which resolves to the **LIVE install** unless the binary is copied into the scratch root (see the Phase-6 L4 form, `phase6-plan.md` §3 S3) |
> | **`ENSEMBLE_SELF_ENV`** | **`sandbox`** — pinned **EXPLICITLY** | the house lesson: a frozen-binary boot must not resolve to the live install; `sandbox` is the explicit non-live attestation (and D-1 ⇒ non-live ⇒ `e2e`-only) |
> | **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`** | **`=0`** — pinned **EXPLICITLY** | **never `env -u` on it**: the `env -u` idiom strips the ambient guard, the sweep then SIGTERM+SIGKILLs **LIVE** launcher/daemon children (the 2026-10-31 staged-smoke incident). `env -u POSTGRES_*` is likewise a **silent NO-OP** (bash does not glob env names) |
> | **PG env scrub** | **per-variable, one by one**: `env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u DATABASE_URL` | the worktree `.env` carries `POSTGRES_HOST/PORT/DB/USER/PASSWORD` — the glob form would leak them straight into the scratch boot |
> | **HOME** | a **`mktemp` FAKE_HOME** + an **escape guard** (the fixture must not resolve a live-shaped install path; abort if the resolved path is under `$HOME/agents-ensemble*`) | two live-install deletions (2026-10-12) came from unfenced HOME-derived fixtures |
> | **PG database** | a **scratch DB on a throwaway role** (`createdb -O <throwaway-role>`), **DROPped** with the role at cleanup | the live `ensemble_prod` is never addressed |
> | **Port** | a **non-conflicting port** — explicitly **NOT 9797 and NOT 7979** (both are **LIVE and listening now**: prod `:9797`, demo `:7979` — verified by `ss` on 2026-11-02) | the port must be recorded in the gate artifact |
> | **Teardown** | **SIGTERM-only** (no SIGKILL escalation), then assert **no orphans** | house boot-smoke rule |
> | **Live fence proof** | the live `state.json` **sha256 PRE == MID == POST**, the live daemon's **pid + uptime unchanged**, `ensemble_prod` **never addressed** | the fence assertion that proves the scratch boot touched nothing live |
> | **The `.env` of the baseline boot** | **NOTHING IS MUTATED.** The "plane category absent" condition is produced by an **explicit per-process env override in the scratch boot** (`PLANE_MCP_URL` **unset for that process only**), **never** by editing any tracked or installed `.env` | the **main checkout's `.env` is the Phase-5 SEEDING SOURCE** — mutating it would break **X5.13**; the worktree `.env` is the Phase-5 execution env — mutating it would break the run too. **The baseline boot mutates no `.env` at all.** |
>
> **The baseline boot's assertions (in order).** (1) The scratch daemon boots (`/livez` + `/readyz` 200) with `ENSEMBLE_SELF_ENV=sandbox`; (2) **no** `plane` `mcp_servers` row exists (the category is absent **by construction**, not by skip accident); (3) PM's resolved toolset contains **zero** `plane_*` tools; (4) `baseline_tokens` is measured at the prompt-assembly point; (5) the live fence holds (PRE == MID == POST; pid/uptime unchanged); (6) teardown is SIGTERM-only, the scratch DB + role are dropped, the scratch root is removed. **If any of (1)–(5) fails, `baseline_tokens` is NOT recorded** ⇒ FC9 is **UNEVALUABLE** downstream (never a substituted guess).

> **Amendment P1-A (architect G0, 2026-11-02 — folded append-only).** The re-capture must additionally record:
> - **the required-params of the DISCOVERY verbs** — the wave-1 inventory recorded required-params only for the work-item/comment/cycle verbs, so the discovery-verb params (`list_projects`, `list_states`/`retrieve_state`, `list_work_item_types`/`retrieve_work_item_type`/`resolve_work_item_type`, plus members/labels/search) are an **inference**, not a capture. The re-capture closes that inference.
> - **per-tool schema token counts** — the wave-1 capture recorded **property names only**, never schema bodies, so the "10–18 k tokens" figure was plausible but **unverified**. Token counts must be captured here (they feed **FC9**).
> - **the sha256 of the sorted name set** — so FC2/FC10 (catalog drift) become a **diff**, not a judgement ("the count AND the sha256 of the sorted name set at every re-capture").

**Pass criteria.**

1. `tools/list` succeeds (HTTP 200) ⇒ the credential is live.
2. The list contains `create_work_item` (with an `is_draft` property), `update_work_item`, `delete_work_item`, `list_work_items`, `retrieve_work_item`, **all 5** comment verbs (`create/list/update/delete/retrieve_work_item_comment`), and **all 10** cycle verbs.
3. **No** `*_issue` verb appears ⇒ the charter must name `*_work_item` verbs.
4. **> (Amendment P1-B — criterion re-worded to SET-MEMBERSHIP.)** The prior wording — "the write-capable count (116 at wave-1 capture) has not dropped below the set PM's charter names" — is **withdrawn**: the 116/177 census is **wrong in three incompatible ways** (see the three-number disclosure below) and must not be frozen as a gate criterion. The criterion is now: **"no verb in PM's reconciled keep-set is absent"** — a set-membership check against the charter's reconciled verb vocabulary, machine-readable so **FC1 becomes a set-difference** rather than a judgement.

> **> (G2 fold, approver iter-003, 2026-11-02 — BLOCKER B1: the keep-set becomes a GATE-(i) ARTIFACT. This closes a producer/consumer cycle that would have aborted the lane at its FIRST gate.)**
>
> **The defect the approver found (exact failure mode).** Criterion 4 consumed "PM's reconciled keep-set" — but the reconciled keep-set is a **Phase-4** artifact, and **Phase 4 DEPENDS ON gate (i)** ⇒ circular. And at gate-(i) time the only charter vocabulary in existence is the **DRIFTED** one (the seven non-existent `*_issue`/`plane_add_comment`/`plane_remove_comment`/`plane_assign_issue` names), **none** of which is in the 177-verb catalog ⇒ criterion 4 would fail ⇒ **FC1 fires** ⇒ X1.1 re-opens Path B ⇒ **the lane aborts at its first gate on a defect that does not exist.**
>
> **The remedy (one artifact + one sentence), now folded:**
>
> | # | Element | The folded rule |
> |---|---------|----------------|
> | 1 | **THE ARTIFACT** | **`gate-i-keep-set.json`** — the **machine-readable keep-set**, produced **BY THE GATE-(i) EXECUTOR**, as part of the gate-(i) artifact set. It enumerates, from the **re-captured catalog**, the keep-set in both forms: the **raw verb** (`create_work_item`) and the **daemon-exposed name** (`plane_create_work_item`) — because the A′ allow-list in `meta.json` matches **literal prefixed names** inside the populated dynamic category, while the catalog is raw |
> | 2 | **PRODUCER** | the gate-(i) executor, **in the same run** that produces the re-capture. **The gate produces what it consumes** ⇒ the cycle is broken at the source, not by a phase re-ordering |
> | 3 | **CRITERION 4's INPUT** | criterion 4 is evaluated against **`gate-i-keep-set.json`** — **NOT** against the pre-Phase-4 (drifted) charter. **Phase 4 then reconciles the charter TO that artifact** |
> | 4 | **PHASE 4's INPUT** | `gate-i-keep-set.json` is **Phase 4 §2.1's input** (the vocabulary the charter is rewritten to). This is the second half of the circularity break: Phase 4 consumes the artifact the gate produced, not a self-derived notion |
> | 5 | **A′ / FC8** | the same artifact is the **FC8 minimum-toolset input** for the A′ branch (`phase5-plan.md` §5.6) ⇒ "an A′ flip needs no new analysis" becomes literally true |
> | 6 | **FAILURE ACTION** | the failure action is re-pointed: FC1 fires **only** if a verb **in `gate-i-keep-set.json`** is absent from the re-captured catalog. A charter drift at gate-(i) time is **not** a failure — the charter is reconciled in Phase 4 |
>
> **The keep-set, LITERALLY ENUMERATED (28 names — the architect's "~28", now exact).** This is the content the gate-(i) executor writes into `gate-i-keep-set.json`, derived from the re-captured catalog using the architect's family description (A′: *"the charter's reconciled verb vocabulary: work-item CRUD + comments + cycles + assignee/manage + the discovery/typing reads; prune the customer/release/project-admin/module/milestone/initiative/page/property families and the out-of-scope deletes"*):
>
> | Family | Raw verbs (the catalog form) | Daemon-exposed names (the `meta.json` allow-list form) | Count |
> |---|---|---|---|
> | **Work-item CRUD** | `create_work_item`, `update_work_item`, `delete_work_item`, `retrieve_work_item`, `retrieve_work_item_by_identifier`, `list_work_items`, `search_work_items`, `count_work_items` | `plane_create_work_item` … `plane_count_work_items` | **8** |
> | **Comments** | `create_work_item_comment`, `list_work_item_comments`, `update_work_item_comment`, `delete_work_item_comment`, `retrieve_work_item_comment` | `plane_create_work_item_comment` … `plane_retrieve_work_item_comment` | **5** |
> | **Cycles** | `create_cycle`, `update_cycle`, `delete_cycle`, `list_cycles`, `retrieve_cycle`, `complete_cycle`, `manage_cycle_work_items`, `transfer_cycle_work_items`, `list_cycle_work_items`, `manage_cycle_archive` | `plane_create_cycle` … `plane_manage_cycle_archive` | **10** |
> | **Assignee** | `manage_work_item_assignee` | `plane_manage_work_item_assignee` | **1** |
> | **Discovery / typing reads** | `list_projects`, `list_work_item_types`, `resolve_work_item_type`, `list_states` | `plane_list_projects`, `plane_list_work_item_types`, `plane_resolve_work_item_type`, `plane_list_states` | **4** |
> | | | **TOTAL** | **28** |
>
> **Pruning note (the architect's families, for the executor's audit trail — the executor must NOT add names from these):** the **customer**, **release**, **project-admin**, **module**, **milestone**, **initiative**, **page** and **property** families, and the out-of-scope deletes, are **pruned** from the keep-set. The artifact records the pruned set as a **count + the sorted-name sha256** (so FC2/FC10 remain diffable) but **not** as a second name list — the 28 above is the **whole** keep-set.
>
> **Where the artifact lives:** `.agents/shared/planning/plane-pm-issue-tools/gate-i-keep-set.json` (the planning dir — **evidence**, not shipped code), alongside the other gate-(i) artifacts.

> **Amendment P1-B (architect G0, 2026-11-02 — folded append-only): the three-number census disclosure.** The wave-1 "116 of 177 write-capable (65 %)" figure is **not usable**. The disclosure:
>
> | # | Method | Read | Write | Unclassified |
> |---|--------|------|-------|--------------|
> | 1 | The inventory's **stated** method (read = list/retrieve/get/count/search/read) | **73** | **104** | — |
> | 2 | The same stated method, but with the **12 plainly read-only verbs** the stated 61/116 pair silently excluded (`get_*`×11, `read_*`×1) restored to the read set | (the pair is the 61/116 artefact) | (idem) | — |
> | 3 | The daemon's **actual** classifier (`daemon/mcp/builtin_servers/plane.py:242-259` patterns + `daemon/mcp/resilience.py:638-687 is_read_tool`) | **48** | **82** | **47** |
> | **4** | **> (G2 fold, approver iter-002, 2026-11-02 — BLOCKER B4: the CORRECTED classifier, after P2-K + the `read_` amendment.)** The daemon's classifier **as amended by this lane**: read = `list_` + `get_` + `search_` + `retrieve_` + `count_` + **`read_`**; write = `create_` + `update_` + `delete_` + `remove_` + `set_` + `manage_` + `transfer_` + `complete_` + `link_` + `import_` + `attach_` + `detach_` + `upload_` + `resolve_` (the dead `add_`/`edit_`/`assign_` patterns are **dropped**) | **73** | **104** | **0** |
>
> **B4 — the arithmetic, verified by me against the 177-verb catalog** (`tools-list-inventory.md` §2; the extraction was checked to yield exactly 177 unique names, and the recompute reproduces the architect's on-disk row 3 **48/82/47** exactly — the acid test that the classifier semantics were modelled correctly):
>
> | Step | Arithmetic | Result |
> |---|---|---|
> | **Row 3 — on-disk today** | read = `list_` **36** + `get_` **11** + `search_` **1** = **48**; write = `create_` **26** + `update_` **27** + `delete_` **26** + `add_` **0** + `remove_` **1** + `set_` **2** + `edit_` **0** + `assign_` **0** = **82**; unclassified = 177 − 48 − 82 | **48 / 82 / 47** |
> | **Row "ruled P2-K"** (before the `read_` amendment) | read = 48 + `retrieve_` **23** + `count_` **1** = **72**; write = 82 + `manage_` **14** + `transfer_` **1** + `complete_` **1** + `link_` **1** + `import_` **1** + `attach_` **1** + `detach_` **1** + `upload_` **1** + `resolve_` **1** = **104**; unclassified = **1** — **`read_work_item_attachment`** | **72 / 104 / 1** ⇒ **FC11's "documented-unclassified must be EMPTY" is RED as ruled** — the approver's finding, confirmed |
> | **Row 4 — the B4 amendment** (add **`read_`** as a READ prefix) | read = 72 + `read_` **1** = **73**; write = **104** (unchanged); unclassified = 177 − 73 − 104 = **0** | **73 / 104 / 0** ⇒ **FC11 becomes satisfiable** |
>
> **Two reconciliations the arithmetic yields, both worth recording:**
> 1. **Row 4 == row 1.** The inventory's **stated** method (read = list/retrieve/get/count/search/read) gives **73/104** — which is **exactly** the corrected classifier. **The drift was in the daemon's classifier, not in the inventory.** After P2-K + `read_`, the daemon and the inventory agree exactly, and the architect's "three-number disclosure" collapses to **two** numbers (73 read / 104 write) with **zero** unclassified.
> 2. **The drifted sub-counts** (the approver's corrections, confirmed by my recompute): **`manage_` = 14** (not 13 — the pre-fold plan said 13) and **`retrieve_` = 23** (the pre-fold plan said "~20"). `add_` = `edit_` = `assign_` = **0** ⇒ dropping those three patterns is **safe** (no verb loses its classification).
>
> **R18 re-verification (the trap P2-K exists to close) — MECHANICALLY PROVEN.** For a **non-full-access** agent, the CR-3 strip keeps exactly the schemas where `is_read_tool` is `True`:
>
> | Classifier | Verbs kept for a non-full-access agent | Verbs silently dropped |
> |---|---|---|
> | **on-disk today** | **48** | **129** (82 write + 47 unclassified) — including **every** `retrieve_*` (23) |
> | **corrected (P2-K + `read_`)** | **73** | **104** (all explicitly write) |
> | **Set difference** | **+25 restored** = `retrieve_` **23** + `count_` **1** + `read_` **1** | **0 removed** — the corrected read set is a **STRICT SUPERSET** of today's |
>
> **⇒ No verb that was kept is dropped anywhere.** The widened read subset **restores** 25 silently-dropped verbs and **removes** none. (This is the R18 closure, now proven by set arithmetic rather than by assertion.)
>
> The **47 unclassified** verbs are neither read nor write today; `is_read_tool` returns `False` for them, so they are **dropped from every non-full-access agent's schema list** (this is risk **R18**, closed by amendment **P2-K** in Phase 2). **The re-capture must report all three numbers**, and the charter rewrite must not depend on any of them as a threshold.
>
> **Consequence for Phase 4:** the charter's verb vocabulary is derived from the **re-captured catalog + the reconciled keep-set**, not from the 116/177 figure.

**Failure action.** **> (G2 fold, approver iter-003, 2026-11-02 — BLOCKER B1: the failure action is RE-POINTED at the keep-set ARTIFACT.)** If a verb **listed in `gate-i-keep-set.json`** is **absent** from the re-captured catalog ⇒ **FC1 fires** ⇒ re-open Path B ⇒ the leader commissions a re-plan (Phase 0 §7). If the credential is dead ⇒ **R1** ⇒ stop; a user re-mint is required before any further Plane work. **A charter drift observed at gate-(i) time is NOT a failure** — the drifted charter is reconciled **to** `gate-i-keep-set.json` in **Phase 4**; the pre-fold wording (which would have failed the gate on the drifted charter) is withdrawn.

**Bound compliance.** Listing only; no write verb; no data read; env var **names** and HTTP status codes only — never values.

> **Rider binding (architect G0 §1.2):** the Path-B discharge **stands for the surface**, with two riders — (1) discovery-verb params captured at re-capture (**P1-A**); (2) **charter reconciliation is a HARD PREREQUISITE for the Task-3 turn** (Phase 4 must land before Phase 5's PM turn, because PM's charter names **seven distinct non-existent verbs** (across **ten occurrences**) — `plane_list_issues` ×4, `plane_create_issue`, `plane_update_issue`, `plane_delete_issue`, `plane_add_comment`, `plane_remove_comment`, `plane_assign_issue`; there is **no** `add_`/`remove_`/`assign_` verb in the catalog). **> (G2 fold, approver iter-003 — non-blocking item 5: the pre-fold label "SIX verbs" was a miscount — the enumeration is 7 distinct names / 10 occurrences.)**. PM's charter must name real verbs before the Task-3 agent turn, or the turn selects a non-existent tool.

---

## 4. Gate (ii) — `e2e` workspace existence + MCP credential reachability

**Status.** REST leg PASSED in wave 1: `GET {PLANE_BASE_URL}/api/v1/workspaces/e2e/projects/` with `X-Api-Key` ⇒ HTTP 200, 2 projects (`E2E`/`e2e`, `SYSTEMDEFAUL`/`__system_default__`). **UNVERIFIED-BY-YOU** (the commission forbids touching `.env` files; I did not re-probe).

**The gap.** `PLANE_MCP_API_KEY` is a **different credential** from `PLANE_API_KEY`; the REST GET therefore does **not** prove MCP reachability. And `tools/list` is **workspace-agnostic** — wave 1 got byte-identical 177-name lists for `e2e` and `nea` (`tools-list-inventory.md` §6) — so a successful listing proves **nothing** about a workspace's data reachability.

**Procedure (to close).**

> **Leader ruling (G0, 2026-11-02 — AUTHORISATION GRANTED, incorporate and do NOT re-open):** the gate-(ii) `list_projects` authorisation **PROCEEDS per the leader** (`architecture-recommendation.md` §9). The "authorisation-not-granted" branch below is therefore **not** the default path — it survives only as a contingency. ADJ-4's question (ii) ("may one credential serve both lanes?") is now **answerable empirically** by this probe.

1. ~~**Request explicit operator authorisation** for one read-only MCP verb.~~ — **AUTHORISED (leader, 2026-11-02).** The commission's original hard rule forbade any MCP verb other than `tools/list`; **that restriction is lifted for this single read-only verb** (`list_projects`) by the leader's ruling. It remains **read-only, no write, no delete** — the authorisation does **not** extend to any mutating verb.
2. With authorisation: call `list_projects` (a read, no write) on the `e2e` slug with the dev MCP credential. Expect HTTP 200 + the two e2e projects.
3. ~~With the same authorisation: repeat on the `nea` slug, to determine whether the **same** credential reaches production.~~ — **> (G2 fold, approver iter-002, 2026-11-02 — non-blocking item 7: this `nea`-side read is REMOVED.)** The pre-fold step 3 read the **`nea`** slug from the **dev** main checkout. That is **dev→nea traffic** — forbidden by the lane's own fence (the same rationale as NB9's dropped optional `nea` read in Phase 5) — and it is **unexecutable in any case**: the live/demo token is dead (401), and whether the *dev* credential reaches `nea`'s **data** is precisely the open question. **Disposition: DROPPED (not relocated to dev).** The **`e2e`-side read (step 2) is retained** — it alone decides whether the non-live ⇒ `e2e` pin is satisfiable. **ADJ-4(ii) ("may one credential serve both lanes?") therefore stays OPEN and USER-OWNED**: it is answered **on the live host, by the user, after the credential re-mint** (ADJ-4(i)) — **not** from dev. **This does not block the gate.**
4. Record: HTTP status + project identifiers. **No keys, no secret values.**

**Pass criteria.**

- (a) `e2e` resolves under the MCP credential ⇒ the non-live ⇒ `e2e` pin is satisfiable.
- (b) **> (G2 fold, approver iter-003 — non-blocking item 14: this criterion asked for the DROPPED `nea` read; it is replaced.)** ~~The `nea` answer is recorded~~ ⇒ **replaced by:** *"the `e2e`-side answer is recorded, and **ADJ-4(ii) is recorded as still OPEN and USER-OWNED** (answered on the live host by the user after the credential re-mint — the `nea`-side read was **dropped** from the dev probe as dev→nea traffic, which the lane's fence forbids)."* **ADJ-4(ii) is NOT closed by this gate and must not be reported as closed.**

**Failure action.** If `e2e` does **not** resolve under the MCP credential ⇒ **do NOT ship** the non-live ⇒ `e2e` pin. Instead: create/point the workspace first, **or** narrow the policy to "refuse anything that is not explicitly `e2e`" without requiring `e2e` to exist. **This branch is a Phase-2 scope change ⇒ back to the architect (Phase 0 §3) — it is not an implementer's call.**

**Authorisation-not-granted branch (contingency only — NOT the default path).** The leader's G0 ruling grants the authorisation, so this branch is retained only as a contingency. If the authorisation were withheld anyway, the non-live ⇒ `e2e` pin **does not ship** in this release. The lane may still ship: the resolver + the refusal for `nea` on non-live (a refusal is safe to ship without the pin's satisfiability proof — it can only *remove* capability), the observability work, the sweep guard, the classifier reconciliation, and the charter. The `e2e`-required policy for non-live is deferred. **If this branch is taken, it must be recorded, not silently assumed.**

---

## 5. Gate (iii) — live `mcp_servers` row state + heal drill

**Status.** NOT RUN (needs a fenced live touch — outside the lane's write bounds).

**Procedure.**

1. **Fenced, SELECT-only read** of the live `mcp_servers` row for `name='plane'`: capture `config_schema_version`, `config->>'headers'->>'x-workspace-slug'`, and the `url` — **bearer redacted**. **No writes.** The live daemon is not restarted; the live DB is not modified.
2. On a **scratch** install (a copy, never the live tree): change the env (slug `nea` → `e2e`) and bump plane's `schema_version` `"2"` → `"3"`.
3. Boot the scratch install and re-read the row: assert the slug now equals the **resolved** slug and the schema version advanced.
4. Assert the **refusal** path on the scratch install: with a non-live env and slug `nea`, the row is **not** created and the daemon logs the WARNING naming the agent + the missing env vars. (This leg depends on Phase 2/3 code ⇒ it is re-run there; gate (iii) covers steps 1–3.)

**Pass criteria.**

- (i) After the bump, `config.headers['x-workspace-slug']` == the resolved slug ⇒ the heal re-materialises `headers` from the current env (`daemon/manager.py:312-353` — only the `env` axis is preserved).
- (ii) An operator-set `config.env` **sentinel survives** the heal.
- (iii) With a refused resolution: **no row**, **no tools**, a **WARNING** naming the agent(s).
- (iv) The live daemon is untouched by the drill (the live read is SELECT-only; the drill runs on a scratch install).

**Failure action.** If (i) fails ⇒ the heal does **not** re-point the slug ⇒ E4 needs the connect-time header validation as well (`daemon/mcp/config.py:245-277` / `connection_manager.py:173`) ⇒ **escalate to the architect before shipping E4.**

**Scratch-install hygiene (the house fences — ENUMERATED, mandatory here; they apply to the gate-(iii) drill exactly as they do to the FC9 baseline boot).**

> **> (G2 fold, approver iter-003, 2026-11-02 — non-blocking item 15: the fences were REFERENCED but not ENUMERATED. They are enumerated now, and the gate-(iii) drill is bound by the same list as the FC9 baseline boot (`phase1-plan.md` §3's G2-fold B2 block) — the two boots are the same kind of activity and carry the same obligations.)**
>
> | # | Fence | Value / rule | Why (house incident class) |
> |---|-------|--------------|---------------------------|
> | F1 | **Scratch root only** | a fresh `mktemp -d` scratch root; **never** the live install path (`~/agents-ensemble`), **never** `~/ensemble-src` | a frozen binary derives its root from its own path ⇒ a "scratch" boot that runs the live binary resolves the LIVE install |
> | F2 | **The binary is copied into the scratch root** | the staged/frozen binary is **copied** (never booted in place) ⇒ the path-derived root **IS** the scratch | the 2026-10-21 staged-smoke hazard (a staged boot can bind the live promote journal and prune a live ceremony's nonce) |
> | F3 | **`ENSEMBLE_SELF_ENV=sandbox`** pinned **explicitly** | never inherited, never derived | a boot must never resolve to the live environment by accident |
> | F4 | **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`** pinned **explicitly** | **never `env -u` on it** | `env -u` strips the ambient guard ⇒ the sweep SIGTERM+SIGKILLs **LIVE** launcher/daemon children (2026-10-31) |
> | F5 | **Per-variable PG scrub, one by one** | `env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u DATABASE_URL` | `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names); the worktree `.env` carries all of them |
> | F6 | **`mktemp` FAKE_HOME + escape guard** | the fixture must not resolve a live-shaped install path; **abort** if the resolved path is under `$HOME/agents-ensemble*` | two live-install deletions (2026-10-12) came from unfenced HOME-derived fixtures |
> | F7 | **Scratch PG DB on a throwaway role** | `createdb -O <throwaway-role>`; **DROP** the DB + role at cleanup | the live `ensemble_prod` is never addressed |
> | F8 | **Non-conflicting port** | **neither 9797 nor 7979** (both **LISTENING** live: prod + demo) — verified by `ss` on 2026-11-02 | a port collision hits the live daemon |
> | F9 | **SIGTERM-only teardown** | no SIGKILL escalation; then assert **no orphans** | house boot-smoke rule |
> | F10 | **Live fence proof** | the live `state.json` sha256 **PRE == MID == POST**; the live daemon's **pid + uptime unchanged**; the live `current` symlink unchanged; `ensemble_prod` never addressed | the proof that the drill touched nothing live |
> | F11 | **No `.env` mutation** | the drill's env changes are **per-process env overrides** only — never an edit of any tracked or installed `.env` | the main checkout's `.env` is the Phase-5 seeding source (X5.13 depends on it); the worktree's is the Phase-5 execution env |
>
> **The gate-(iii) drill inherits F1–F11 verbatim.** The FC9 baseline boot (the block in §3) carries the same list; if the two ever diverge, **this list is canonical**.

---

## 6. Tier gating

No repo diff ⇒ **no regression leg**. The scratch drill is a **functional** drill, not a test-pack run.

However, the drill's *readiness* is gated on the Phase-2/3 code existing for legs (iii) and (iv). The plan therefore splits gate (iii): **rows 1–2 above are Phase-1 work** (the live read + the scratch setup), **rows 3–4 are re-run in Phase 2/3 as part of the pin packs** (P8 covers the heal; P3/P5 cover the refusal + WARNING).

---

## 7. Pin / test obligations

| # | Obligation | Where |
|---|------------|-------|
| **P-G1** | The re-capture artifact records the **count + names + property names**, and its `create_work_item` schema shows `is_draft` (the Task-3 draft capability) | Gate note (this phase) |
| **P-G1a** *(P1-A)* | The re-capture artifact additionally records: the **discovery verbs' required-params** (not an inference), the **per-tool schema token counts** (feeds FC9), and the **sha256 of the sorted name set** (feeds FC2/FC10) | Gate note (this phase) |
| **P-G1b** *(P1-B)* | The artifact reports the **three-number census** (104/73 stated-method; 82/48/47 daemon classifier) and the gate's criterion 4 is evaluated as **set-membership** against PM's reconciled keep-set — the 116/177 figure is **not** used as a threshold. **> (G2 fold, approver iter-003 — BLOCKER B1: the keep-set is a NAMED GATE-(i) ARTIFACT.)** Criterion 4 is evaluated against **`gate-i-keep-set.json`** — **produced by this gate**, **not** by a later phase and **not** against the drifted pre-Phase-4 charter. The artifact enumerates the **28** keep-set verbs in **both** forms (raw verb + `plane_`-prefixed daemon name) and records the pruned count + the sorted-name sha256. **FC1 fires only if a verb IN that artifact is absent from the re-captured catalog.** The same artifact is **Phase 4 §2.1's input** and the **FC8 minimum-toolset input** for the A′ branch. | Gate note (this phase) — artifact `.agents/shared/planning/plane-pm-issue-tools/gate-i-keep-set.json` |
| **P-G4** *(G2 fold, approver iter-003 — new: the keep-set artifact's own integrity)* | **`gate-i-keep-set.json` exists, is machine-readable, contains exactly the 28 enumerated names in both forms, every raw verb is a member of the re-captured catalog, and the pruned count + sorted-name sha256 are recorded.** Its RED leg: delete a keep-set entry, or add a name that is not in the catalog ⇒ the artifact-integrity assertion goes RED | Gate note (this phase) |
| **P-G2** | The probe helper is **read-only by construction** — it refuses any verb outside an explicit allow-list (`tools/list`, `list_projects` when authorised) and never prints a credential value | `probe_plane_mcp.py` |
| **P-G3** | The live read is SELECT-only and the evidence note states the redacted bearer handling | Gate note (this phase) |

---

## 8. Exit criteria

| # | Criterion |
|---|-----------|
| **X1.1** | Gate (i) re-captured and passing; the artifact names the charter-relevant verbs with `is_draft` confirmed. **If FC1 fires, exit here and re-open Path B.** |
| **X1.2** | Gate (ii) closed **with** authorisation ⇒ `e2e` reachable under the MCP credential; **or** the "authorisation not granted" branch is formally recorded and the `e2e`-required policy is marked deferred. |
| **X1.3** | Gate (iii) rows 1–2 done: the live row state captured (SELECT-only, bearer redacted); the scratch drill environment prepared with the house fences. |
| **X1.4** | ADJ-4's "one credential, both lanes" question has a recorded answer (or is explicitly deferred to the user's re-mint). |
| **X1.5** | The Phase-4 charter rewrite's verb list is sourced from the **gate-(i) re-capture**, not the wave-1 snapshot. |
| **X1.6** *(§1.2 rider)* | The charter-reconciliation prerequisite is recorded: Phase 4 (charter) **lands before** the Phase-5 Task-3 agent turn, because PM's charter names **seven distinct non-existent verbs** (across **ten occurrences**) — `plane_list_issues` ×4, `plane_create_issue`, `plane_update_issue`, `plane_delete_issue`, `plane_add_comment`, `plane_remove_comment`, `plane_assign_issue`. *(G2 fold iter-003 — the pre-fold "SIX" label was a miscount.)* |
| **X1.7** *(P1-A/P1-B)* | The re-capture artifact carries the discovery-verb required-params, the per-tool schema token counts, the sorted-name-set sha256, and the three-number census — and gate-(i) criterion 4 was evaluated as a **set-membership** check, not against the withdrawn 116/177 figure. |
| **X1.8** *(G2 fold, approver iter-002 — blocker B2: the FC9 baseline capture is an exit criterion.)* | The **FC9 baseline boot** ran in the designated **scratch sandbox** with **every** house fence bound and asserted: `ENSEMBLE_SELF_ENV=sandbox` pinned explicitly; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly (**never** `env -u`); per-variable `POSTGRES_*` scrub; `mktemp` FAKE_HOME + escape guard; scratch PG DB on a throwaway role; a port that is **neither 9797 nor 7979** (both live); **no** `.env` mutated (the plane-absent condition is a **per-process env override**); SIGTERM-only teardown with no orphans; and the **live fence** proven (live `state.json` sha **PRE == MID == POST**, live pid + uptime unchanged, `ensemble_prod` never addressed). **`baseline_tokens` is recorded** in the gate artifact ⇒ FC9 is evaluable at Phase 5. **If the boot fails any assertion, `baseline_tokens` is NOT recorded** and FC9 is **UNEVALUABLE** downstream — never a substituted guess. |
| **X1.9** *(G2 fold, approver iter-002 — non-blocking item 7: the `nea`-side probe leg is explicitly out.)* | The gate-(ii) record states that the **`nea`-side read was dropped** (dev→nea traffic is forbidden by the lane fence) and that **ADJ-4(ii) remains OPEN and USER-OWNED** — answered on the live host by the user after the re-mint, **not** from dev. The `e2e`-side read either passed or the "authorisation-not-granted" branch is recorded (X1.2). |
| **X1.10** *(G2 fold, approver iter-003 — blocker B1: the keep-set artifact is an exit criterion.)* | **`gate-i-keep-set.json` EXISTS**, is machine-readable, and enumerates **exactly the 28** keep-set verbs in **both** forms (raw verb + `plane_`-prefixed daemon name); **every** raw verb is a member of the re-captured catalog; the pruned count + the sorted-name sha256 are recorded. **Criterion 4 was evaluated against THIS artifact** (not against the drifted pre-Phase-4 charter), and the artifact is **named as Phase 4 §2.1's input** and as the **FC8 minimum-toolset input** for the A′ branch. The gate's failure action fired **only** if a verb **in the artifact** was absent from the catalog. **Its RED leg is demonstrated** (delete a keep-set entry, or add a name absent from the catalog ⇒ the artifact-integrity assertion goes RED). |

---

## 9. Fold register — architect G0 amendments mapped to Phase 1

*(G1 fold A-e, 2026-11-02: this section was a duplicate "§8" — the file already has §8 Exit criteria — and its rows pointed at a phantom "§9 exit criteria". Renumbered to §9; the exit-criteria references below corrected to §8. Cross-references updated in `plan-overview.md` §4 note and `phase0-plan.md` §8.)*

**Fold date:** 2026-11-02 · **Source:** `architecture-recommendation.md` §7 (Phase 1 block) + §1.2 (riders) + §11 · **Mode:** append-only; no existing text renumbered or removed.

| Amendment | Substance | Where folded in this file |
|-----------|-----------|---------------------------|
| **P1-A** | gate-(i) re-capture records discovery-verb required-params, per-tool schema token counts, sorted-name-set sha256 | §3 **Procedure** (the P1-A block) + §7 pin **P-G1** (below) + §8 exit criteria |
| **P1-B** | the 116/177 census is replaced by the three-number disclosure (104/73 stated-method; 82/48/47 daemon classifier); gate-(i) criterion 4 re-worded to **set-membership** | §3 **Pass criteria** criterion 4 (re-worded) + the three-number disclosure table + the "Rider binding" note |
| **Rider (§1.2)** | Path-B discharge stands for the surface with two riders: discovery-verb params captured at re-capture; charter reconciliation is a hard prerequisite for Task 3 | §3 **Rider binding** block + §8 exit criteria **X1.6** |
| **§11 / W-13** | Path A confirmed; census replaced; FC7–FC11 adopted; R18–R21 added | `decisions.md` §2 (W-13) + `plan-overview.md` §6/§10/§11 — **not** a Phase-1 file change |

**Amendment-count note:** Phase 1 carries **2** of the amendment IDs the architect's §7 enumerates (P1-A, P1-B). The architect's §7 header states "30 consolidated amendments"; the document's own IDs number **27** — reconciled in `decisions.md` §7 (fold report) rather than absorbed silently.

---

## G2 fold (approver iter-001, 2026-11-02) — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 001 **REJECTED** (3 blockers + 9 non-blocking). **Architecture NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **B1(f)** | **FOLDED** — the probe environment is now **named**: the **main checkout** (`/home/nea/ensemble-src`, armed) is the probe + seeding source; the **fenced worktree** is the Phase-5 execution env. The pre-fold "from an environment with a working `PLANE_MCP_API_KEY` (dev context)" ambiguity is closed. | §3 (the G2 fold B1(f) block) |
| **B3** (the baseline acquisition) | **FOLDED** — this gate also acquires `baseline_tokens` (the plane category **absent**: boot with `PLANE_MCP_URL` unset ⇒ the row never registers). A missing measurement ⇒ FC9 is **UNEVALUABLE** at Phase 5 (no substituted guess). The measurement **point** is a Phase-1 ratification item; the FC9 **rule** is already folded in `phase5-plan.md`. | §3 (the G2 fold B3 block) |

---

## G2 fold (approver iter-002, 2026-11-02) — FINAL FOLD — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 002 **REJECTED** (4 blockers + 10/13 non-blocking). **Architecture / D-1 / ADJ-1 / phase structure NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **B2** | **FOLDED** — the FC9 baseline boot is **designated** (a dedicated scratch sandbox), **fully fenced** (scratch root only; `ENSEMBLE_SELF_ENV=sandbox` pinned; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned — never `env -u`; per-variable PG scrub; `mktemp` FAKE_HOME + escape guard; scratch PG DB + throwaway role; a port neither 9797 nor 7979; SIGTERM-only teardown; the live-fence proof), **mutates no `.env`** (the plane-absent condition is a per-process env override), and the section's "touches no daemon" wording is **reconciled** (it applies to the two probes; the baseline boot is a separate, fenced activity). Exit criterion **X1.8** added. | §3 (the G2-fold B2 blocks) + §8 **X1.8** |
| **B4** (the census disclosure) | **FOLDED** — the three-number disclosure gains **row 4** (the corrected classifier: **73 / 104 / 0**) with the **full arithmetic** (the on-disk row reproduced exactly as the acid test), the drifted sub-counts corrected (**`manage_` = 14**, **`retrieve_` = 23**), and the **R18 set-arithmetic** (kept 48 → 73, **+25** restored, **0** removed). | §3 (the P1-B disclosure table + the B4 arithmetic block) |
| **NB7** | **FOLDED (DROPPED)** — the gate-(ii) **`nea`-side read** is removed from the dev probe (dev→nea traffic is forbidden by the lane fence, and the live token is dead); the `e2e`-side leg is retained; **ADJ-4(ii) stays open + user-owned** (answered on the live host after the re-mint). Exit criterion **X1.9** added. | §4 (the gate-(ii) step 3) + §8 **X1.9** |

---

## G2 fold (approver iter-003, 2026-11-02) — FINAL FOLD — item disposition

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 003 **REJECTED** (2 blockers + 17 mechanical notes); **approver cap reached ⇒ the leader proceeds to implementation; there is NO further approver round.** **Architecture / D-1 / ADJ-1 / the phase structure / the 7-phase decomposition are NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered or repurposed.

| Item | Disposition | Where |
|------|-------------|-------|
| **B1** | **FOLDED** — the keep-set becomes a **GATE-(i) ARTIFACT**: **`gate-i-keep-set.json`**, produced by the gate executor (the gate produces what it consumes), enumerating the **28** keep-set verbs in both forms (raw verb + `plane_`-prefixed name) with the pruned count + the sorted-name sha256. Criterion 4 is evaluated against **that artifact** (not the drifted pre-Phase-4 charter); the failure action fires only if a verb **in the artifact** is absent from the catalog; the artifact is **Phase 4 §2.1's input** and the **FC8** minimum-toolset input for the A′ branch. New pin **P-G4**, new exit criterion **X1.10**. | §3 (the B1 block + the 28-name enumeration) + §3 (criterion 4 + the failure action) + §7 (**P-G1b** re-pointed, **P-G4**) + §8 **X1.10** |
| **NB14** | **FOLDED** — the gate-(ii) criterion (b) no longer asks for the dropped `nea` read; it now requires the `e2e`-side answer plus the explicit statement that **ADJ-4(ii) is still OPEN and USER-OWNED**. | §4 (criterion (b)) |
| **NB15** | **FOLDED** — the gate-(iii) drill's house fences are **ENUMERATED as F1–F11** (scratch root only; the binary copied into the scratch; `sandbox` pinned; the sweep guard pinned explicitly; per-variable PG scrub; FAKE_HOME + escape guard; scratch PG DB + throwaway role; a non-conflicting port; SIGTERM-only teardown; the live-fence proof; no `.env` mutation) and declared **canonical** for both the drill and the FC9 baseline boot. | §5 (the F1–F11 table) |

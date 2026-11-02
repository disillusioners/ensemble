# Phase 5 — Task-3 end-to-end verification (dev / `e2e`)

**Lane:** `plane-pm-issue-tools` · base `941cb6321` · **Tier: functional (live dev daemon, real LLM, real Plane MCP) — Tier-2 legs where a regression claim is made**
**Depends on:** Phase 2 (the enforcement must exist — otherwise nothing refuses `nea`), Phase 3 (the refusal must be *visible*), Phase 4 (PM's charter must name the real verbs), Phase 1 gate (ii) (the `e2e` workspace must be MCP-reachable) · **Blocks:** Phase 6 (the functional gate's evidence)

---

## 1. Objective

Close the commission's **third** task with a real, reproducible procedure:

1. **PM creates the preserved DRAFT "Guidelines system" work item into the env-correct workspace** — on **dev**, that is the **`e2e`** test workspace (never `nea`).
2. **The enforcement is proven to refuse the `nea` direction on non-live.**
3. Evidence artifacts are captured.

**Environment constraint (R14).** The dev context is the **only** armed environment on this host: the live and demo `PLANE_MCP_API_KEY` are the **same expired token** (HTTP 401 `invalid_token`) and only dev points at `e2e` **and** carries a REST key. **Task 3 therefore runs on dev, and only on dev.** (UNVERIFIED-BY-YOU — wave-1 probe evidence.)

---

## 2. File-level scope

**No repo source file is written by this phase**, with **ONE conditional exception** (G2 fold, approver iter-001, 2026-11-02 — blocker **B2**): if the **FC9 breach** verdict fires, the **A′ sub-step** (§5.6) **does** write repo source — the `agents/project-manager/**` charter, the `agents/.prompt-artifacts.sha256` re-stamp, and the `SEALED_SHA_BASELINES` re-pin — in **one atomic commit**, exactly per the Phase-4 idiom (`phase4-plan.md` §2.5). Outside that conditional branch, the phase writes **only** evidence notes + a run record in the planning dir, plus the **Plane workspace state change** (a draft work item in `e2e` — the intended, authorised side effect).

| File | Action | Detail |
|------|--------|--------|
| `.agents/shared/planning/plane-pm-issue-tools/task3-verification-run.md` | **NEW** | the run record: environment fence, the resolved classification, the tool-surface census, the created work item's identifier + workspace, the refusal evidence, the negatives. **No secrets.** |
| `.agents/shared/planning/plane-pm-issue-tools/task3-evidence/` | **NEW** (dir) | the captured artifacts: the resolved `plane_workspace` resolution dump (slug + reason), the `mcp_servers` row snapshot (**bearer redacted**), the PM tool-list census, the created-work-item read-back, the refusal log line |
| `agents/project-manager/*.md` (4 files) · **`agents/project-manager/meta.json`** · `agents/.prompt-artifacts.sha256` · `tests/test_chart_image_delivery_e2e.py` | **CONDITIONAL — A′ branch ONLY** | written **only** when the FC9 verdict is **BREACH**; **one atomic commit** with the 161/161 re-stamp + the `SEALED_SHA_BASELINES` re-pin for `agents/project-manager/workflow.md` (`phase4-plan.md` §2.5 idiom). **Not touched in the no-breach branch.** **> (G2 fold, approver iter-002 — non-blocking item 3: `meta.json` was MISSING from the pre-fold scope table, although A′.1 writes it.)** `agents/project-manager/meta.json` is the file A′.1 narrows (`tools.allow`: the `"plane"` category entry → the explicit keep-set literal names). It is now named explicitly here. **Note:** `meta.json` is **not** in the 161-file prompt-artifact baseline (architect §1.5, verified) ⇒ it needs **no** re-stamp row — but its edit **does** ride the same atomic commit as the charter edit + the re-stamp + the SEALED re-pin, because the charter prose and the grant must land together. |
| `daemon/**`, `tests/**` | **NO CHANGE** | — |

**The one authorised external write:** creating a **draft** work item in the **`e2e`** workspace. It is a draft (`is_draft=true`), so it does not notify a project board. **No write to `nea`** under any circumstance — see the negative legs.

---

## 3. The preserved draft — where it comes from

The draft text lives in the daemon's **project history**, event **`8ffd3082-a1bb-4273-a5ea-e9ac87f5ceca`** (recorded by the 2026-11-02 test commission). It is **not** in the repo and **not** in the RAG knowledge base (verified: a content grep for "Guidelines system" across `/home/nea/ensemble-src` returns **zero** matches).

**Retrieval path (re-verified by me):** the daemon exposes `GET /api/projects/{project_id}/history/search?q=...` (`daemon/routers/projects.py:780-800`, router prefix `/projects` at `:24`) and `GET /api/projects/{project_id}/history` (`:654-660`). **PM itself holds `project_history_search` and `project_history_list`** (`agents/project-manager/meta.json` allow list) — so the Task-3 run can have **PM read the draft from history itself**, which is the cleanest form of the verification (the agent uses only its own declared tools).

**Procedure step 1 therefore has two acceptable forms:** (a) PM calls `project_history_search` with `q="Guidelines system"` and uses the returned `details`/`summary` verbatim; or (b) the operator/verifier fetches the history entry read-only and hands the verbatim text to PM in the dispatch. **Form (a) is preferred** — it exercises the agent's real tool surface end-to-end. **The verbatim text MUST be used; no paraphrase, no re-drafting.**

---

## 4. Environment fence (mandatory — the same class of fence that caused six env-poison incidents)

> **G2 fold (approver iter-001, 2026-11-02) — BLOCKER B1: the Task-3 environment is now ONE NAMED ENV, and the pre-fold table's "credentials present" claim was FALSE for it.**
>
> **What the approver found (and I re-verified independently, key NAMES only, never values):** the fenced worktree `.env` carries **ZERO** `PLANE_*` keys. Under this lane's own fail-closed resolver, a non-live env with the slug unset ⇒ **refused** ⇒ **no row** ⇒ **zero `plane_*` tools** ⇒ **Phase-5 Steps 3–4 are unexecutable** — and the pre-fold triage line (`:166`) would have mis-routed that failure as a Phase-2/3 **code regression**, i.e. **indistinguishable from the original bug this lane exists to fix**.
>
> **Verified census (G2 fold — read by me in this fold, `grep -oE '^[A-Za-z_][A-Za-z0-9_]*='` — NAMES ONLY, NO VALUES):**
>
> | File | mode | keys present | `PLANE_*` count |
> |------|------|--------------|-----------------|
> | `/home/nea/ensemble-src-wt-plane-pm/.env` (**the fenced worktree — the Task-3 env**) | `0600` | `ENSEMBLE_SELF_ENV`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DAEMON_PORT` — **7 key names** | **0** |
> | `/home/nea/ensemble-src/.env` (**the main checkout — the SEEDING SOURCE ONLY**) | `0600` | `PORT`, `POSTGRES_DB`, `PLANE_BASE_URL`, `PLANE_MCP_URL`, `PLANE_MCP_API_KEY`, `PLANE_MCP_WORKSPACE_SLUG`, `PLANE_API_KEY`, `PLANE_SYNC_ENABLED` | **6** |
>
> **> (G2 fold, approver iter-002, 2026-11-02 — non-blocking item 10: the census table was ABRIDGED; it is now the FULL key-name list.)** The G2 iter-001 fold listed only **three** key names for the worktree (`ENSEMBLE_SELF_ENV`, `POSTGRES_DB`, `DAEMON_PORT`) because the census command filtered on a narrower pattern. The **full** list is the 7 above — the missing four are **`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`**. **The `PLANE_* = 0` claim is CONFIRMED correct** (it was the count, not the list, that was wrong). **Why the full list matters:** the worktree `.env` carries **ambient PG credentials** ⇒ the per-variable scrub (`env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD`) is **load-bearing** here, and `env -u POSTGRES_*` (the glob form) is a **silent NO-OP** — bash does not glob env names. (The worktree also uses **`DAEMON_PORT`** where the main checkout uses **`PORT`** — the port var name differs per install.)
>
> (The approver recorded "five `PLANE_*` keys" in the seeding source; the precise census is **six** `PLANE_*` keys, of which **five** are needed by the Task-3 procedure — `PLANE_MCP_URL`, `PLANE_MCP_API_KEY`, `PLANE_MCP_WORKSPACE_SLUG`, `PLANE_BASE_URL`, `PLANE_API_KEY`. `PLANE_SYNC_ENABLED` is the sixth and is **not** seeded — the sync lane is out of scope for Task 3.)
>
> **The rule now in force:**
> - **THE ONE NAMED ENVIRONMENT is the fenced worktree `/home/nea/ensemble-src-wt-plane-pm`.** The main checkout `~/ensemble-src` is **the seeding source only** — it is **not** a Task-3 execution environment and is **never** booted for Task 3.
> - **The worktree must be ARMED before Steps 3–4** (Step 0 below). If it cannot be armed, the run **STOPS** and reports an **environment defect** — **not** a code regression.

| Item | Value / rule | Anchor |
|------|--------------|--------|
| **Task-3 env (ONE)** | **`/home/nea/ensemble-src-wt-plane-pm`** (the fenced worktree) | G2 fold B1(a) |
| Seeding source (ONLY) | `/home/nea/ensemble-src/.env` (main checkout, dev-context) — **copied from, never booted** | G2 fold B1(c) |
| DB | `POSTGRES_DB=ensemble_dev` | the lane's fenced env (`shared_meta_kv` `wt.active.feature/plane-pm-issue-tools.fenced_env` = `dev/ensemble_dev/8079`) |
| Port | `8079` (`DAEMON_PORT` in the worktree fence) | the dev daemon — **> (G2 fold, approver iter-003, 2026-11-02 — non-blocking item 9: the 8079 contention is RESOLVED by naming the authority.)** **Both the fenced worktree (`DAEMON_PORT=8079`) and the main checkout (`PORT=8079`) name the same port** — they are **two different installs**, and only one may bind it at a time. **The rule (binding):** the **Phase-5 daemon is the MAIN CHECKOUT's** (it is the already-running dev daemon on `:8079`, carrying the armed Plane config — the seeding source), and the **fenced worktree is the CODE source**, not the runtime. **If the developer needs the branch's code running**, the worktree daemon must be started on a **different, non-conflicting port** (the house rule: neither **9797** nor **7979** — both live) and `DAEMON_PORT` set accordingly in the worktree fence; the worktree daemon must then be **stopped** before the main-checkout daemon is restarted. **Only one daemon binds :8079 at a time; the run note records which one owned it.** |
| `.env` mode | `0600` — **verified for both files** (G2 fold census) | the lane's own fence |
| `ENSEMBLE_SELF_ENV` | **`dev` (explicit)** | `dev.sh:79-112` — a linked worktree **refuses to boot** without an explicit `ENSEMBLE_SELF_ENV` assignment in its own `.env`; giter writes `ENSEMBLE_SELF_ENV=dev` into every worktree fence. **This means the Task-3 happy path takes D-1's explicit-marker branch ⇒ non-live ⇒ `e2e` permitted.** |
| `PLANE_MCP_URL` / `PLANE_MCP_API_KEY` | **must be present in the worktree `.env`** — currently **ABSENT** ⇒ **Step 0 seeding is mandatory** | G2 fold B1(b)/(c) — census above |
| `PLANE_MCP_WORKSPACE_SLUG` | **`e2e`** (seeded) — the env-correct workspace | G2 fold B1(c) |
| `PLANE_BASE_URL` + `PLANE_API_KEY` | **must be present in the worktree `.env`** (seeded) — the REST lane is used for the **read-back** verification (Step 5) | G2 fold B1(c) |
| Live contact | **FORBIDDEN** — no `nea` write, no live DB, no live daemon, no `releases/` | commission constraint |
| Boot sweep guard | `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly** | the `env -u` idiom strips the ambient guard and the sweep kills LIVE processes |
| Env scrubs | **per-variable** `env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL` | `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) |

### Step 0 — ARMING PRECONDITION (G2 fold B1 — the run does not start unarmed)

**0.1 — Key-NAME census (never values).** Before anything else, census the **worktree** `.env` for the presence of the **five** required key **NAMES**: `PLANE_MCP_URL`, `PLANE_MCP_API_KEY`, `PLANE_MCP_WORKSPACE_SLUG`, `PLANE_BASE_URL`, `PLANE_API_KEY`. **Record the NAMES and their PRESENCE/ABSENCE only — never a value, never a prefix, never a length.** The census command is name-only by construction (e.g. `grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' .env | sed 's/=$//' | grep -E '^PLANE_'`). **Any value that reaches a log, the plan, or an artifact is a defect.**

**0.2 — Seeding (only if the census shows absences).** Copy the **five** keys from the armed main checkout `/home/nea/ensemble-src/.env` into the worktree `/home/nea/ensemble-src-wt-plane-pm/.env`:

| Key | Seeded value | Why Task 3 needs it |
|-----|--------------|--------------------|
| `PLANE_MCP_URL` | verbatim from the main checkout | the MCP endpoint |
| `PLANE_MCP_API_KEY` | verbatim from the main checkout | the MCP bearer — the **only** working one on this host (live+demo are expired: 401) |
| `PLANE_MCP_WORKSPACE_SLUG` | **`e2e`** — asserted, **not** copied blindly | the env-correct workspace |
| `PLANE_BASE_URL` | verbatim from the main checkout | the REST read-back (Step 5) |
| `PLANE_API_KEY` | verbatim from the main checkout | the REST read-back (Step 5) |

**Fencing rules for the seeding step (all mandatory).**

- The worktree `.env` is **written under the same fence the lane already enforces**: `umask 077`, write via a `mktemp` temp file in the **same directory**, then `mv -f` into place, then `chmod 600` — **never** an in-place append to a file whose mode could widen, **never** a world-readable temp file.
- **Do NOT touch the main checkout `.env`** — it is read-only in this step. Its mode (`0600`) must be **unchanged** after the seeding.
- `PLANE_SYNC_ENABLED` is **not** seeded (the sync lane is out of scope for Task 3).
- The seeding step is **idempotent**: re-running it with the keys already present is a no-op.
- **Never echo a seeded value** into a log, the run note, an artifact, or the plan.

**0.3 — STOP branch (the unarmed case).** If the census **cannot** be satisfied (the seeding source is missing/unreadable, the key names are absent there too, the write fails, or the mode assertion fails):

> **STOP. Do NOT proceed to Steps 3–4. Report the state as an ENVIRONMENT DEFECT — explicitly NOT a code regression.** The report must state: the Task-3 env is unarmed (key **names** absent), the census result, and the fact that Steps 3–4 are unexecutable *by design of the fail-closed resolver* (non-live + slug unset ⇒ refused ⇒ no row ⇒ no tools).
>
> **The pre-fold triage line is hereby superseded** (G2 fold B1(d)): the old `:166` text offered three candidate causes for a failed Step 4 — (a) charter/verb mismatch, (b) tool surface absent (Phase 2/3 regression), (c) model non-selection. **Cause (b) is now split**: a tool surface absent because **the env is unarmed** is **(d) an environment defect**, and must be triaged **FIRST**, before any code-regression hypothesis. Only after (d) is excluded may (b) be considered. **The reworked triage line is in §7.**

**0.4 — Re-census after seeding.** Re-run the name-only census and assert all **five** names are present, and assert the worktree `.env` mode is still `0600` and the main checkout `.env` mode is still `0600`. Only then proceed.

**D-1 no-marker leg — a structural constraint the plan must state.** The D-1 fail-open case (a checkout with **no** explicit marker + `POSTGRES_DB=ensemble_dev`) **cannot be exercised through `dev.sh` in a linked worktree** — `dev.sh:107-112` refuses to boot without the explicit marker. Therefore:

- The **deterministic** proof of that case is the **gate-(iv) pack** (Phase 2, case P2/P4) — it drives the resolver directly with an injected env.
- A **live** no-marker leg, if wanted, must run from a **non-worktree scratch copy** (or a scratch install), never by deleting the marker from the giter-fenced worktree `.env` (that would break the fence `dev.sh` exists to enforce).

---

## 5. Procedure (the gate)

### Step 1 — Preconditions

1. The dev daemon is running **the branch's code** (the enforcement from Phase 2 + the observability from Phase 3 + the charter from Phase 4). If the daemon is running an older tree, **restart it on the branch** — otherwise the negative legs are vacuous.
2. `GET /livez` and `GET /readyz` return 200 (readiness **all-true**, `reasons=[]`).
3. The environment fence above is satisfied and **recorded** in the run note.

### Step 2 — Resolution proof (positive leg)

Assert, and record:

- the lane resolver's classification: **non-live**, source = **explicit marker** (`dev`);
- the **effective slug** = `e2e`;
- `allowed = True`.

### Step 3 — Registration + tool-surface census

- Assert a `mcp_servers` row named `plane` **exists** and its `config.headers['x-workspace-slug']` == **`e2e`** (**bearer redacted** in the evidence).
- Assert the row's `config_schema_version` == the bumped value (Phase 2's E4) ⇒ the heal re-materialised the headers from the current env.
- **> (Amendment P2-H — sequence allowed-boot-first.)** The heal runs **only when the resolution is permitted** (`is_available()` is True). A refusal **defers** it. Therefore this Step must be executed on a boot where the resolution is **permitted** (dev + `e2e` + armed MCP) — **the allowed boot comes first**, and the refusal boots (Step 6) come **after**. Asserting the healed slug on a refusal boot would be a vacuous assertion.
- Census PM's **resolved** toolset: it contains `plane_create_work_item` (and the other charter verbs) and **does not** contain `plane_sync_project` (post-Phase-3 ownership). Record the **count** — this is the **FC9 baseline** for the 177-tool surface (ADJ-5 / A3).
- **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 4: SC1's number is now ASSERTED, not implied.)** The census must record **and assert** the **numeric** threshold from `plan-overview.md` §7 **SC1**: the resolved PM toolset contains **≥ 100** `plane_*` tools, **including** `plane_create_work_item`. The assertion is a **pass/fail line** in `pm_toolset_census.json` (`plane_tool_count` + `sc1_threshold: 100` + `sc1_satisfied: true|false`), and it is recorded **before** the FC9 measurement of the same Step — so a threshold failure and a budget breach are distinguishable in the evidence. A count **below** 100 ⇒ the census is a **FAIL** (report it as such; do not proceed to Step 4 with a sub-threshold surface).
- Measure **`present_tokens`** (the plane category PRESENT) per the G2-fold B3 measurement recipe, and evaluate the **FC9 ceiling** ⇒ record the verdict (`PASS` / `BREACH` / `UNEVALUABLE`) per §5 Step 3's B3 block. **The verdict routes the A′ sub-step (§5.6).**
- **Do NOT use a custom `instance_id`** — the default UUID path only (R9 / AD6: a custom id ⇒ `mcp_tool_names=[]`).

> **Amendment P5-C (architect G0, 2026-11-02 — folded append-only): the numeric baselines + the FC9 ceiling.** The pre-fold plan recorded only a tool-name census. The architect ruled that the **decision** to adopt A′ must be made by **measurement against a numeric ceiling**, not by judgement. Phase 5 therefore records **three baselines**, and they are the **inputs to FC9**:
>
> | Baseline | How measured | Feeds |
> |----------|--------------|-------|
> | **per-turn prompt tokens for PM** | token count of the PM system prompt + tool schemas on a representative turn (the schema bodies are now available from the gate-(i) re-capture's per-tool token counts — **P1-A**) | **FC9** (absolute ceiling) |
> | **tool-call error rate** | over a **stated N** of real PM turns, the fraction of tool calls that errored (unknown tool, schema mismatch, wrong arguments) | **FC9** (% growth vs the pre-Plane baseline) + **FC3** |
> | **mis-selected-verb count** | the number of times PM selected a verb that was not the intended one (e.g. `create_work_item` instead of `create_intake_work_item`) | **FC9** + **FC3** |
>
> **FC9 (adopted flip condition, architect §4):** the prompt-budget breach ceiling — **absolute tokens/turn** and **% growth vs the pre-Plane baseline**. **On an FC9 breach, A′ becomes the DEFAULT OUTCOME of Phase 5, not a judgement call** (W-13 / ADJ-5's third option). The **A′ keep-set is pre-derived** (~28 of 177: the charter's reconciled verb vocabulary — work-item CRUD + comments + cycles + assignee/manage + the discovery/typing reads; pruning the customer / release / project-admin / module / milestone / initiative / page / property families and the out-of-scope deletes), so an A′ flip needs **no new analysis**.
>
> **FC8 (mandatory IFF A′ is adopted):** a narrowed grant converts upstream renames into **silent capability loss** ⇒ an A′ flip must ship (a) a **minimum-toolset assertion** (the resolved set ⊇ the keep-set, at least keep-set size) and (b) the **gate-(i) drift detector** (FC10's sorted-name-set sha256). Both ride the A′ commit, which is also a **charter edit** ⇒ the prompt-artifact re-stamp + SEALED re-pin ride it too (R8 unchanged).
>
> **Ceremony note (architect §1.5):** `meta.json` is **not** in the 161-file prompt-artifact baseline (verified) — but an A′ flip forces a charter edit, so the re-stamp + SEALED re-pin rides the A′ commit.

> **G2 fold (approver iter-001, 2026-11-02) — BLOCKER B3: FC9 now has a PRE-COMMITTED derivation rule, a baseline recipe, and a comparison procedure.** The approver's finding: FC9 was declared "mechanical" and "not a judgement call", but the set contained **no threshold and no comparison base anywhere** — an executor would have had to **invent** both. **The rule below is pre-committed NOW; the executor may not invent it.**
>
> **The measurement (what is counted).** PM's **per-turn prompt token count** = the tokens of the PM system prompt **plus** the bound tool schemas **for one representative PM turn**, measured at the point where the daemon binds the tools (`llm.bind_tools(tools)` — `daemon/graph.py:10531`/`:10549`; the tool **names** additionally enter the system prompt via `loader.py:704-706`). The count is taken with the **same** tokeniser the LLM lane uses, and it is recorded **twice**:
>
> | Quantity | When measured | How | Recorded in |
> |----------|---------------|-----|--------------|
> | **`baseline_tokens`** (the plane category **ABSENT**) | **at the Phase-1 gate** | boot the daemon with **`PLANE_MCP_URL` unset** ⇒ the builtin is unavailable ⇒ **the row never registers** ⇒ zero `plane_*` tools ⇒ PM's prompt is measured with the plane surface absent | `phase1-plan.md` §3 artifact → `fc9_baselines.json` |
> | **`present_tokens`** (the plane category **PRESENT**) | **at Phase-5 Step 3** | the same measurement on the armed Task-3 env (plane row registered, 177 tools bound) | `fc9_baselines.json` |
> | `tool_call_error_rate` | Phase-5 Step 3 (over a stated N of real PM turns) | fraction of tool calls that errored | `fc9_baselines.json` |
> | `mis_selected_verb_count` | Phase-5 Step 3 | count of tool calls selecting a verb other than the intended one | `fc9_baselines.json` |
>
> **The ceiling (pre-committed rule — NOT to be chosen at execution time).**
>
> ```
> growth_tokens = present_tokens - baseline_tokens
> growth_pct    = growth_tokens / baseline_tokens
>
> FC9 CEILING (breach if EITHER condition fails):
>   (a) growth_tokens <= max(10_000, 0.25 * baseline_tokens)
>   (b) growth_pct    <= 0.25
> ```
>
> **Why this rule and not another (the justification the approver required):**
> - The **absolute floor (`10_000` tokens)** acknowledges the irreducible cost of a full 177-tool surface. Without it, a small `baseline_tokens` would produce a ceiling that is *unachievable by construction* (the plane schemas alone exceed it), turning FC9 into a guaranteed-breach alarm and devaluing the gate.
> - The **relative cap (`25 %`)** is the growth bound: it prevents a **large** baseline from licensing unbounded schema growth, and it is the number the "% growth vs the pre-Plane baseline" half of FC9 was already asking for (architect §4).
> - **Both** conditions must hold. Condition (a) alone would let a large baseline pass everything; condition (b) alone would fail a small baseline for a growth that is materially harmless. Requiring both means neither a tiny nor a huge baseline can mask a pathological surface.
> - The rule is **environment-independent** (it is a ratio + an absolute floor, not a host-specific constant) ⇒ it is comparable across dev/demo and across releases.
>
> **The comparison procedure (Phase-5 Step 3, in order).** (1) Read `baseline_tokens` from the Phase-1 gate artifact; if it is **absent**, FC9 is **UNEVALUABLE** ⇒ **STOP** and report (the architect's "obtain, don't invent" rule) — **do not** substitute a guess. (2) Measure `present_tokens`. (3) Compute `growth_tokens` and `growth_pct`. (4) Evaluate (a) and (b). (5) Record the verdict (`FC9: PASS` / `FC9: BREACH` / `FC9: UNEVALUABLE`) **and both booleans** in `fc9_baselines.json`. (6) The verdict **routes the A′ sub-step** (§5.6): `PASS` ⇒ no A′; `BREACH` ⇒ A′ **executes** (§5.6); `UNEVALUABLE` ⇒ **no A′ by default**, but the phase **reports FC9 as unevaluable** and **routes back to the architect** — it is **not** an implementer's call to declare a breach or waive one.
>
> **> Phase-1-gate ratification item (recorded, NOT deferred).** The **acquisition** of `baseline_tokens` is a Phase-1-gate item — the Phase-1 artifact must carry it, and the architect should ratify the **measurement point** (`llm.bind_tools` prompt assembly, `graph.py:10531`/`:10549` + `loader.py:704-706`) when it next rules on the plan set. **But the RULE itself (the ceiling above) is folded NOW** and is not deferred to that ratification: the executor implements the rule above as written, and the ratification item only confirms the measurement point, not the threshold.

### Step 4 — THE TASK: PM creates the DRAFT "Guidelines system" work item into `e2e`

1. Read the preserved draft from history (see §3; prefer PM's own `project_history_search`).
2. Dispatch PM (a real PM instance, real LLM) with the verbatim draft text and the instruction to **create a Plane work item in the current (env-resolved) workspace as a DRAFT**, and to cite the resulting identifier.
3. Assert PM's tool call is `plane_create_work_item` with `is_draft=true` — **not** a create-then-delete, and **not** a write to `nea`.
4. Capture the created work item's **identifier** and the **workspace** it landed in.

### Step 5 — Read-back verification (e2e)

- Read the item back **through the Plane REST lane against `e2e`** (`GET {PLANE_BASE_URL}/api/v1/workspaces/e2e/...` with `X-Api-Key`) — an independent channel from the MCP write, which is what makes it a verification rather than a replay.
- Assert: it exists, `is_draft` is true, and the **content matches the preserved draft verbatim** (or the dispatch's verbatim text).

### Step 6 — The negative legs (the enforcement proof)

| Leg | Setup | Assertion |
|-----|-------|-----------|
| **N-1 — non-live + `nea`** | Set `PLANE_MCP_WORKSPACE_SLUG=nea` in the dev context and **re-resolve** (no daemon restart needed for the resolver-level check; a restart is needed for the row-level check) | the resolver returns `allowed=False` with the typed reason naming `nea` as production-only and the environment ⇒ **refused** |
| **N-2 — non-live + slug UNSET** | Unset `PLANE_MCP_WORKSPACE_SLUG` | `allowed=False` with the reason `"PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'"` ⇒ **refused** (this is the P2/P3 case that closes pollution path **P2** — today it silently targets the account-default prod workspace) |
| **N-3 — non-live + other slug** | `PLANE_MCP_WORKSPACE_SLUG=other` | refused with the typed reason |
| **N-4 — no NEW row, no plane tools** | With N-1 in force, re-boot the dev daemon | **> (Amendment P5-A — the assertion is RESTATED, because the pre-fold wording was wrong.)** The dev daemon **already has** a `plane` row (the dev context is armed) ⇒ "no row is created" was a **vacuous** assertion on a pre-existing row. The correct, four-part assertion is: **(i) NO *NEW* row is created** by the refused boot; **(ii) the pre-existing row's `config_schema_version` is UNCHANGED** (the heal is deferred — **P2-H**); **(iii) PM's *resolved* toolset contains NO `plane_*` tool** (the gate is 4-link strong — **P2-F** — so a stale row still yields no tools); **(iv) the WARNING is logged** (the agent + server + typed reason). **Plus the stored-fallback assertion:** `mcp_tool_names` in the spawn response carries **no stale `plane_*`** entry — the stored fallback must not resurrect the old names. |
| **N-5 — no `nea` pollution** | After the whole run | **> (Amendment P5-B — NO weakening is needed; the assertion holds as originally written, because P2-D closed the bypass it depended on.)** **No** write of any kind reached the `nea` workspace. Assert at the mechanism level: the `nea` slug was never permitted in any non-live resolution ⇒ the MCP header could never carry `nea`; and the REST URL path could never carry `nea` **because `PlaneHttpClient.__init__` may no longer accept an explicit `workspace_slug`/`base_url` that the resolver did not return** (**P2-D** — the split-brain vector is closed, so the mechanism-level assertion is **true as written**). **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 9: the OPTIONAL `nea` READ is DROPPED.)** The pre-fold clause *"If an authorised read against `nea` is available, additionally confirm no item named 'Guidelines system' exists there"* is **REMOVED**: a dev-host `nea`-directed read is **dev→nea traffic**, which the lane's own no-nea-contact fence forbids — the optional clause contradicted the very fence it was meant to serve, and it would require an out-of-band prod credential from the dev host (the exact reachability the directive forecloses). **Disposition: DROPPED (not relocated).** The negative coverage is **preserved by the mechanism-level assertion** (P15b): the proof that no `nea` write was possible does not require touching `nea`. **Coverage loss, stated explicitly:** there is no *observational* confirmation that `nea` lacks the item — the guarantee is **structural** (the resolver + P2-D), not observational. If a future lane wants the observational check, it must run **on the live host** with an operator-held prod credential — **not** from dev. |

> **> (G2 fold, approver iter-002, 2026-11-02 — non-blocking item 5: the RESTORE step after the negative legs is now explicit and mandatory.)**
>
> N-1 … N-4 **mutate the worktree `.env`** (the slug is set to `nea`, then to `other`, then unset). Leaving it that way would (a) poison the next run (the Phase-5 env would come up **unarmed** — the Step-0 census would fail ⇒ the run would mis-triage as an environment defect), and (b) break **X5.13** (the arming precondition is asserted at exit). **Step 6-R — RESTORE (mandatory, and it is an exit criterion):**
>
> **> (G2 fold, approver iter-003, 2026-11-02 — non-blocking item 1: the step-ID collision is resolved.)** The RESTORE was folded as "Step 6.5" and the A′ branch was **also** "Step 6.5" — two different activities sharing one ID (and `X5.12` cited "§5.6" while `P29` cited "Step 6.5"). **Resolution (no renumbering of any existing ID):** the A′ sub-step **keeps** `Step 6.5` (it is referenced by **X5.12**, **P29**, `phase5-plan.md` §2 and Step 3, and `plan-overview.md` §11); the **RESTORE is renamed `Step 6-R`** — it is the **tail of Step 6**, not a sibling of 6.5, and the name makes that structural. The two references in the fold registers that said "Step 6.5 — RESTORE" are corrected to "Step 6-R — RESTORE".
>
> | # | Action | Assertion |
> |---|--------|-----------|
> | 1 | Restore the worktree `.env` to its **post-arming** state: `PLANE_MCP_WORKSPACE_SLUG=e2e`, the other four seeded keys unchanged | the key-**NAME** census re-run shows all **five** names present; the slug value is `e2e` (**assert the value**, this is the one value the plan asserts) |
> | 2 | Assert the worktree `.env` **mode is still `0600`** | the restore must not widen the mode |
> | 3 | Assert the **main-checkout `.env` is unchanged** (mode `0600`, content untouched) | the seeding source must be pristine |
> | 4 | Re-boot the dev daemon on the restored env and confirm the `plane` row **is** present with slug `e2e` | proves the restore actually restored the armed state (not merely the file text) |
> | 5 | Record the restore in the run note | the run note shows the pre-run and post-run env states **key-name-wise** (+ the slug value) |
>
> **The restore runs even if a negative leg fails** (it is a `finally`-style obligation, not a success-path step).

**N-1..N-3 must be evaluated by the SAME resolver instance the daemon uses** (not a re-implementation) — otherwise the test proves the test, not the daemon.

### Step 6.5 — THE A′ SUB-STEP (CONDITIONAL — executes only on an FC9 BREACH verdict)

> **G2 fold (approver iter-001, 2026-11-02) — BLOCKER B2: the A′ branch now has an EXECUTABLE PHASE HOME.** The approver's finding was exact: *"A′ is architect-mandated as the DEFAULT outcome on breach, yet it requires charter edits + atomic re-stamp + SEALED re-pin while Phase 5 declares 'no repo source written', Phase 4 has landed, Phase 6 freezes code"* ⇒ the branch was **materially live but structurally homeless**. The approver's first option (leader lean) is adopted: **a conditional A′ sub-step INSIDE Phase 5**, with Phase 5's no-repo-source declaration amended by the explicit conditional exception in §2.

**Precondition.** The Step-3 FC9 verdict is **`BREACH`** — i.e. `growth_tokens > max(10_000, 0.25 × baseline_tokens)` **OR** `growth_pct > 0.25` (the pre-committed rule, §5 Step 3's B3 block). A `PASS` verdict ⇒ **this sub-step does not execute** (the no-breach branch is covered by X5.12a). An `UNEVALUABLE` verdict ⇒ this sub-step does **not** execute and the verdict is **routed back to the architect** (X5.12c).

**What the sub-step does — the A′ keep-set grant.** Narrow PM's grant from the whole `"plane"` category to an **explicit allow-list of the verb names** in the pre-derived **keep-set (~28 of 177)** — the charter's reconciled verb vocabulary: work-item CRUD + comments + cycles + assignee/manage + the discovery/typing reads; pruning the customer / release / project-admin / module / milestone / initiative / page / property families and the out-of-scope deletes. **A′ mechanics are CONFIRMED** (literal tool names inside a populated dynamic category — the filter matches literal names; architect §1.5).

| # | Action | File | Detail |
|---|--------|------|--------|
| **A′.1** | narrow the grant | `agents/project-manager/meta.json` | replace the `"plane"` entry in `tools.allow` with the explicit keep-set literal names (the `"plane"` **category** entry is replaced, not supplemented). **> (G2 fold, approver iter-003, 2026-11-02 — non-blocking item 6: the keep-set is now LITERALLY enumerated, so "an A′ flip needs no new analysis" is literally true.)** The 28 names are enumerated in `phase1-plan.md` §3 (the gate-(i) keep-set block) and are produced **by the gate** as **`gate-i-keep-set.json`** — **that artifact is A′'s input** (the **FC8 minimum-toolset** baseline). The `meta.json` entries use the **daemon-exposed (prefixed) form** (`plane_create_work_item`, …) because the filter matches literal names inside the populated dynamic category. `meta.json` is **not** in the 161-file prompt-artifact baseline (verified) — but see A′.3 |
| **A′.2** | update the charter prose | `agents/project-manager/{tools_note,rule,workflow,soul}.md` | the charter must name the keep-set verbs (the drift reconciliation from Phase 4 is **re-applied** to the narrowed vocabulary); the `plane_sync_project` ownership statement from Phase 4 stays |
| **A′.3** | **atomic re-stamp** | `agents/.prompt-artifacts.sha256` | regenerate to **161/161** via `scripts/protection/generate_prompt_artifact_baseline.py --check` — the house authority |
| **A′.4** | **SEALED re-pin** | `tests/test_chart_image_delivery_e2e.py:1490` | re-pin the `SEALED_SHA_BASELINES` row for `agents/project-manager/workflow.md` to its new content sha |
| **A′.5** | **ONE commit** | — | A′.1 + A′.2 + A′.3 + A′.4 land in **one atomic commit** — the exact Phase-4 idiom (`phase4-plan.md` §2.5). A split commit makes the seal tripwire red at the intermediate commit. **No exceptions.** |
| **A′.6** | **FC8 obligations** | the A′ commit | ship (a) the **minimum-toolset assertion** (resolved set ⊇ keep-set, at least keep-set size) and (b) the **gate-(i) drift detector** (FC10's sorted-name-set sha256) — **mandatory iff A′ is adopted** |
| **A′.7** | re-measure | `fc9_baselines.json` | re-measure `present_tokens` with the narrowed grant and record it **alongside** the pre-narrowing value (the comparison is the value of the branch) |

**Ordering constraint.** The A′ sub-step runs **after** Step 3's measurement and **after** Steps 4–5 (the draft creation + read-back) — the Task-3 verification is **not** conditional on the budget verdict; only the **shipped grant** is. The A′ commit therefore lands **after** the Task-3 evidence is captured, and Phase 6 stages the **post-A′** tree.

**R7 interaction (disclosed).** A′ **incidentally removes the `plane_sync_project` leak** (the leak is the category-sweep artefact; a literal allow-list cannot contain it) — the Phase-3 ownership guard remains the primary fix and must **not** be reverted on the assumption that A′ covers it.

### Step 7 — Evidence capture

All artifacts land in `.agents/shared/planning/plane-pm-issue-tools/task3-evidence/`:

| Artifact | Content | Secret policy |
|----------|---------|---------------|
| `resolution.json` | classification, source, effective slug, allowed, reason | no secrets |
| `mcp_servers_row.json` | name, `config_schema_version`, `url`, `headers['x-workspace-slug']` | **bearer redacted** |
| `pm_toolset_census.json` | the resolved tool names + counts | no secrets |
| `workitem_readback.json` | the e2e read-back (identifier, draft flag, content) | no secrets |
| `refusal_log.txt` | the captured WARNING line(s) from N-4 | env var **names** only |
| `fc9_baselines.json` | the three **FC9** baselines: per-turn prompt tokens for PM, tool-call error rate over a stated N, mis-selected-verb count (+ the pre-Plane comparison base) | no secrets |
| `run-note.md` | the human-readable run record | no secrets |

---

## 6. Dependencies

| From | Why |
|------|-----|
| Phase 1 gate (ii) | the `e2e` workspace must be MCP-reachable, or Step 4 cannot run |
| Phase 2 | the enforcement must exist for the negative legs to be non-vacuous |
| Phase 3 | the refusal must be **visible** (N-4) — otherwise the run cannot distinguish "refused" from "broken" |
| Phase 4 | PM's charter must name `plane_create_work_item` — the agent selects tools from its own instructions |
| The user's **live/demo credential re-mint** | **NOT required** — dev is armed. Live efficacy waits on it (ADJ-4 / O7) |

---

## 7. Tier gating

This is a **functional** gate, not a regression leg. But two of its legs make **regression** claims (the toolset census and the no-row assertion), so those run under the house pack discipline:

- Pack-mapped execution; dual-layer timeout (`timeout 300` + `--timeout=120`).
- The resolver-level negative legs are **deterministic unit legs** ⇒ they belong to the Phase-2 packs (P2/P3/P4) and are **re-run here** to prove the shipped configuration, not just the test fixture.
- `--collect-only` slicing proof for any pack invoked.
- FAILED-digest comparison only if a regression leg is run against a base.

**Real-LLM note.** Step 4 is a **real** PM turn (real LLM, real Plane MCP). It is therefore **non-deterministic** — the plan accepts one canonical path plus a **retry** if the agent selects a different (still valid) creation route, and records the actual tool call used.

> **> (G2 fold, approver iter-001, 2026-11-02 — blocker B1(d): the Step-4 triage line is REWRITTEN so an unarmed environment can never be mis-triaged as a code regression.)**
>
> **A failed Step 4 must be triaged, not retried blindly.** The candidate causes are now **four**, and they are checked **in this order**:
>
> | Order | Cause | How it is distinguished | Disposition |
> |-------|-------|------------------------|-------------|
> | **(d) FIRST** | **THE ENVIRONMENT IS UNARMED** — the worktree `.env` lacks the five `PLANE_*` key **names**, so the fail-closed resolver refuses ⇒ no row ⇒ no `plane_*` tools | the **key-NAME census** (Step 0.1/0.4) shows a name absent, **or** the Step-0 seeding step could not complete | **NOT a code regression.** Report an **environment defect**; **do not** open a Phase-2/3 defect; **do not** attempt to "fix" the resolver |
> | (a) | the charter/verb mismatch (Phase 4 not effective) | the charter names a verb that is absent from the gate-(i) catalog | Phase-4 defect |
> | (b) | the tool surface absent **despite an armed env** | the census shows all five names present, the row exists, yet PM resolves no `plane_*` tool | **Phase-2/3 regression** — the only cause that is a code regression |
> | (c) | genuine model non-selection | the surface is present (count ≥ SC1's 100) and the tools are bound, but the model picked differently | record as **FC3/FC9 evidence**, not a defect |

---

## 8. Pin / test obligations

| # | Pin | Assertion | Where |
|---|-----|-----------|-------|
| **P13** | the D-1 happy path | a dev context with an explicit `dev` marker + slug `e2e` resolves to **non-live**, effective slug `e2e`, `allowed=True` | Step 2 |
| **P14** | the three refusal legs | N-1, N-2, N-3 produce `allowed=False` with the **typed** reason string matching the policy table | Step 6 |
| **P15** | no-row / no-tools — **SUPERSEDED by P15a (G1 fold W-B)**: the pre-fold wording ("no row is created") was **unachievable on dev** — the dev daemon already has a `plane` row; retained for the record only | with `nea` in force on dev, a re-boot creates **no** `plane` row and PM's toolset has **no** `plane_*` tool | Step 6 (N-4) — **the binding form is P15a** |
| **P15a** *(P5-A)* | **the RESTATED N-4** — (i) no **NEW** row; (ii) the pre-existing row's `config_schema_version` **unchanged**; (iii) PM's **resolved** toolset has no `plane_*`; (iv) the WARNING is logged; and `mcp_tool_names` (stored fallback) carries **no stale `plane_*`** | Step 6 (N-4) |
| **P15b** *(P5-B)* | **no `nea` write is possible** — the resolver never permitted `nea` in a non-live resolution **and** the explicit-constructor bypass is closed (**P2-D**), so neither the MCP header nor the REST URL path can carry `nea` | Step 6 (N-5) |
| **P19** *(P5-C/FC9)* — **unique to Phase 5** *(G1 fold B5: the Phase-6 staged pins were renumbered P24–P27 to clear the ID collision; see the master index in `plan-overview.md` §11)* | the three numeric baselines are recorded: **per-turn prompt tokens for PM**, **tool-call error rate over a stated N**, **mis-selected-verb count** — with the **pre-Plane baseline** for the % comparison, so **FC9 is evaluable** and an A′ flip is a **measurement**, not a judgement | Step 3 + Step 7 |
| **P16** | the draft landed in `e2e` | the read-back shows `is_draft=true` and the verbatim content | Steps 4–5 |
| **P17** | no `nea` pollution | no `nea` write occurred (mechanism-level assertion, **P15b**) — *(G2 fold NB9: the optional observational `nea` read is **DROPPED**; the guarantee is structural, not observational)* | Step 6 (N-5) |
| **P18** | the FC3 baseline | the 177-tool surface census is recorded (count + presence of the charter verbs + absence of `plane_sync_project`) so FC3 is later falsifiable | Step 3 |
| **P20** *(G2 fold B1)* | **the arming precondition** | the key-**NAME** census shows all **five** required names present in the worktree `.env`; the worktree `.env` mode is `0600`; the main-checkout `.env` mode is **unchanged** at `0600`; **no value** appears in any log, note or artifact | Step 0 |
| **P21** *(G2 fold B1)* | **the STOP branch** | when the census cannot be satisfied, the run **stops before Steps 3–4** and reports an **environment defect** — and the triage table's cause (d) is checked **before** any code-regression hypothesis | Step 0.3 + §7 |
| **P22** *(G2 fold NB4)* | **SC1's numeric assertion** | `pm_toolset_census.json` records `plane_tool_count` ≥ **100** and `sc1_satisfied: true`, with `plane_create_work_item` present | Step 3 |
| **P28** *(G2 fold B3)* | **the FC9 derivation** | `fc9_baselines.json` records `baseline_tokens` (from the Phase-1 gate artifact), `present_tokens`, `growth_tokens`, `growth_pct`, **both** ceiling booleans, and the verdict `PASS`/`BREACH`/`UNEVALUABLE`; a **missing** `baseline_tokens` ⇒ `UNEVALUABLE` (never a substituted guess) | Step 3 |
| **P29** *(G2 fold B2)* | **the A′ branch** | on `BREACH`, the A′ commit exists with the narrowed grant + the charter edit + the **161/161** re-stamp + the **SEALED** re-pin **in one commit**, plus the FC8 minimum-toolset assertion and the drift detector; on `PASS`, **no** A′ commit exists and the measurement is recorded | Step 6.5 |

---

## 9. Exit criteria

| # | Criterion |
|---|-----------|
| **X5.1** | The "Guidelines system" **draft** work item exists in the **`e2e`** workspace, `is_draft=true`, content matching the preserved history draft verbatim; its identifier is recorded. |
| **X5.2** | PM's tool call was a **create-work-item** call with the draft flag — recorded verbatim (tool name + the arguments, minus any secret). |
| **X5.3** | N-1, N-2, N-3 all **refuse** with the typed reasons (P14). |
| **X5.4** | N-4 asserted in its **P15a four-part form** (see X5.9): no **NEW** row; pre-existing row's `config_schema_version` **unchanged**; PM's **resolved** toolset free of `plane_*`; the **WARNING** naming the agent + typed reason — the refusal is **visible**, not silent (R15). *(G1 fold W-B: the original "no row / no plane_* / WARNING" wording was unachievable on dev — the dev daemon already has a row; superseded by X5.9, retained for the record.)* |
| **X5.5** | N-5: no `nea` pollution (P17). |
| **X5.6** | The FC3 baseline is recorded (P18). |
| **X5.7** | All **seven** evidence artifacts are archived in the planning dir, with the bearer redacted. *(G1 fold W-B: seven, not six — Step 7 lists `resolution.json`, `mcp_servers_row.json`, `pm_toolset_census.json`, `workitem_readback.json`, `refusal_log.txt`, `fc9_baselines.json`, `run-note.md`; the fold note in §10 already says "a seventh evidence artifact".)* |
| **X5.8** | The run note states the **live/demo credential precondition** explicitly — this phase proves the dev lane only; **live efficacy waits on the user's re-mint** (ADJ-4). |
| **X5.9** *(P5-A)* | N-4 is asserted in its **restated** four-part form: no **NEW** row; the pre-existing row's `config_schema_version` **unchanged** (the heal deferred — P2-H); PM's **resolved** toolset free of `plane_*`; the WARNING logged; and `mcp_tool_names` (stored fallback) free of stale `plane_*`. |
| **X5.10** *(P5-B)* | N-5 holds as written — no `nea` write was possible (the resolver never permitted `nea` in a non-live resolution **and** the explicit-constructor bypass is closed by P2-D). |
| **X5.11** *(P5-C/FC9)* | The three **FC9** baselines are recorded (per-turn prompt tokens, tool-call error rate over a stated N, mis-selected-verb count) with the pre-Plane comparison base, so **FC9 is evaluable** and an A′ flip would be **measurement-driven**. The A′ keep-set (~28) is recorded alongside, so an A′ flip needs no new analysis. **> (G2 fold B3: extended.)** The **pre-committed ceiling** is evaluated and recorded: `growth_tokens ≤ max(10_000, 0.25 × baseline_tokens)` **AND** `growth_pct ≤ 0.25`, with both booleans and the verdict `PASS`/`BREACH`/`UNEVALUABLE` in `fc9_baselines.json`; a **missing** `baseline_tokens` ⇒ `UNEVALUABLE` ⇒ **no** substituted guess. |
| **X5.12** *(G2 fold B2 — the A′ branch is exit-criteria-covered in BOTH directions.)* | The A′ sub-step (§5.6) is **conditionally executed and covered in both directions**: **(a) NO-BREACH** ⇒ the verdict is `PASS`, **no A′ commit exists**, and the measurement is recorded in `fc9_baselines.json`; **(b) BREACH** ⇒ the A′ sub-step **executes** — the narrowed grant + the charter edit + the **161/161** prompt-artifact re-stamp + the `SEALED_SHA_BASELINES` re-pin land in **ONE atomic commit**, together with the **FC8** minimum-toolset assertion and the gate-(i) drift detector, and the post-narrowing `present_tokens` is recorded; **(c) UNEVALUABLE** ⇒ no A′ by default, FC9 is reported as unevaluable, and the case is **routed back to the architect** (it is not an implementer's call to declare or waive a breach). |
| **X5.13** *(G2 fold B1 — the arming precondition is an exit criterion.)* | The key-**NAME** census is recorded (names + presence only, **no values**); the worktree `.env` carries all **five** required names; the worktree `.env` mode is `0600`; the **main-checkout `.env` is unchanged** (mode `0600`, content untouched); and the run note names the **one** Task-3 environment (the fenced worktree). **If the census could not be satisfied**, the phase instead exits with the **environment-defect** report (Step 0.3) and Steps 3–4 are **not** executed — that path is a **valid exit**, not a failure of the phase. |
| **X5.14** *(G2 fold NB4)* | The SC1 numeric assertion is recorded: `plane_tool_count ≥ 100` with `plane_create_work_item` present, in `pm_toolset_census.json`. |
| **X5.15** *(G2 fold, approver iter-003 — non-blocking item 1: the RESTORE's assertions 4–5 get their own exit criterion.)* | **Step 6-R — RESTORE** completed: (4) the dev daemon was **re-booted on the restored env** and the `plane` row **is** present with slug `e2e` (proving the restore restored the *armed state*, not merely the file text); (5) the run note records the pre-run and post-run env states **key-name-wise** (+ the slug value). **The restore ran even if a negative leg failed** — it is a `finally`-style obligation, and a run that ended without it is a **failed** Phase 5, not a partial one. |

---

## 10. Fold register — architect G0 amendments mapped to Phase 5

**Fold date:** 2026-11-02 · **Source:** `architecture-recommendation.md` §7 (Phase 5 block) + §1.5 (A′ third option) + §4 (FC8/FC9/FC10) · **Mode:** append-only.

| Amendment | Substance | Where folded in this file |
|-----------|-----------|---------------------------|
| **P5-A** | **restate N-4 / P15** — "no NEW row + pre-existing row's `config_schema_version` unchanged + no `plane_*` in PM's **resolved** toolset + WARNING logged" (the dev daemon already has a row, so the old wording was vacuous); plus the `mcp_tool_names` stored-fallback assertion | §5 Step 6 (N-4 row) + §8 pin **P15a** + §9 X5.9 |
| **P5-B** | N-5 holds via **P2-D** — **no weakening needed** (the constructor bypass was the only vector) | §5 Step 6 (N-5 row) + §8 pin **P15b** + §9 X5.10 |
| **P5-C** | the numeric baselines (prompt tokens/turn, tool-call error rate, mis-selected-verb count) + the **FC9 ceiling**, making **A′ the default outcome on breach** | §5 Step 3 (the P5-C block) + §7 evidence table (`fc9_baselines.json`) + §8 pin **P19** + §9 X5.11 |
| **P2-H** *(cross-phase rider)* | the heal is **conditional on availability** ⇒ a refusal **defers** it ⇒ **Step 3 must sequence allowed-boot-first** | §5 Step 3 (the P2-H note) + §9 X5.9 (ii) |
| **FC8/FC9/FC10** *(§4, new)* | FC9 = the prompt-budget ceiling (absolute + % vs baseline); FC8 = mandatory **iff** A′ (minimum-toolset assertion + the gate-(i) drift detector); FC10 = record the count **and** the sorted-name-set sha256 at every re-capture | §5 Step 3 (the P5-C block) + §8 pin P19; FC10's capture lives in `phase1-plan.md` (P1-A) |

**Phase 5 carries 3 of the architect's §7 amendment IDs** (P5-A, P5-B, P5-C) **plus the P2-H cross-phase rider** and the **FC8/FC9/FC10** flip conditions.

**Amendment-driven change to the phase's own procedure.** Step 3 now asserts the heal on an **allowed** boot **before** the refusal boots; Step 6's N-4 is asserted in the four-part restated form; and Step 7 emits a seventh evidence artifact (`fc9_baselines.json`). The phase's **ordering** (allowed-first) is a substantive amendment, not a cosmetic one.

---

## 11. Fold register — G2 approver iteration 001 (2026-11-02)

**Fold date:** 2026-11-02 · **Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` (main checkout) — iteration 001 **REJECTED**: 3 blockers + 9 non-blocking notes. **Architecture NOT re-opened** (both approver workers re-verified the enforcement design holds; commission tasks 1+2 fully covered). **Mode:** append-only; phase structure unchanged; no pin/phase/gate renumbered.

| Item | Substance | Where folded in this file | Disposition |
|------|-----------|---------------------------|-------------|
| **B1** | Task-3 environment ambiguous + **unarmed worktree** — two envs named; the worktree `.env` has **zero** `PLANE_*` keys ⇒ Steps 3–4 unexecutable under the fail-closed resolver; the `:166` triage would mis-route that as a Phase-2/3 regression | **(a)** §4 — **ONE** named env (the fenced worktree); the main checkout is the **seeding source only**; **(b)** §4 Step 0.1 — the key-**NAME** census (names only, never values); **(c)** §4 Step 0.2 — the explicit seeding step (5 keys + `umask 077` + `mktemp`-in-place + `mv` + `chmod 600`, main checkout read-only); **(d)** §4 Step 0.3 — the **STOP branch** (environment defect, NOT a code regression) + the §7 triage rewrite; **(e)** `plan-overview.md` §9 dev-row split; **(f)** `phase1-plan.md` §3 probe env named | **FOLDED** |
| **B2** | the FC9-breach ⇒ A′ default-outcome branch had **no executable phase home** (needs charter edit + atomic re-stamp + SEALED re-pin while Phase 5 declared "no repo source written" and Phase 6 freezes code) | §2 — the file-scope statement gains the **explicit conditional exception** + the conditional file row; **§5.6 — the new A′ sub-step** (7 actions A′.1–A′.7, ordering constraint, R7 interaction); §8 pin **P29**; §9 **X5.12** (both directions + the unevaluable branch) | **FOLDED** (approver's first option — leader lean) |
| **B3** | FC9 declared "mechanical" but had **no threshold and no comparison base** anywhere in the set — the executor would have had to invent it | §5 Step 3's **G2 fold B3 block** — the pre-committed **ceiling rule** (`growth_tokens ≤ max(10_000, 0.25 × baseline_tokens)` **AND** `growth_pct ≤ 0.25`), the **justification**, the **baseline recipe** (`PLANE_MCP_URL` unset ⇒ the row never registers ⇒ measured at the **Phase-1 gate**), the **comparison procedure** (6 ordered steps, `UNEVALUABLE` ≠ a guess), and the **Phase-1 ratification item** (the measurement point only — the RULE is folded now); §8 pin **P28**; §9 X5.11 extended | **FOLDED** |
| **NB4** | SC1's "≥ 100 `plane_*` tools" was asserted only as a presence, not the number | §5 Step 3 — the **numeric assertion** + the `pm_toolset_census.json` fields; §8 pin **P22**; §9 **X5.14** | **FOLDED** |
| **NB9** | the optional observational `nea` read in N-5 requires out-of-band prod credentials from dev — the exact reachability the directive forecloses | §5 Step 6 (N-5 row) — the clause is **DROPPED** (not relocated); the mechanism-level assertion (P15b) preserves the coverage; the **coverage loss is stated explicitly**; §8 pin **P17** updated | **FOLDED (DROPPED)** |
| **G1 carry** | the G1-fold pins P15a/P15b/P19 and exit criteria X5.9/X5.10/X5.11 remain valid | untouched; the G2 fold adds X5.12–X5.14 alongside them | **preserved** |

**G2-fold pins added in this file:** **P20** (arming precondition) · **P21** (the STOP branch) · **P22** (SC1 numeric) · **P28** (the FC9 derivation) · **P29** (the A′ branch). **No existing pin ID was renumbered or repurposed.**

**G2-fold exit criteria added:** **X5.12** (the A′ branch, both directions + unevaluable) · **X5.13** (the arming precondition, incl. the valid environment-defect exit) · **X5.14** (the SC1 numeric assertion). **No existing exit criterion was renumbered.**

**Anchor provenance for this fold.** Verified by me in this fold, read-only: the worktree `.env` and the main-checkout `.env` **key NAMES only** (never values) — the census in §4; `dev.sh:79-112` (the linked-worktree fence); `daemon/graph.py:10531`/`:10549` (`llm.bind_tools`) and `daemon/loader.py:704-706` (tool names in the system prompt) — the FC9 measurement point.

---

## G2 fold (approver iter-002, 2026-11-02) — FINAL FOLD — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 002 **REJECTED** (4 blockers + 10/13 non-blocking). **Architecture / D-1 / ADJ-1 / phase structure NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB3** | **FOLDED** — the §2 scope table now names **`agents/project-manager/meta.json`** (the file A′.1 narrows), with the note that it is **not** in the 161-row baseline (no re-stamp row needed) but **does** ride the same atomic commit as the charter edit + re-stamp + SEALED re-pin. | §2 (the conditional file row) |
| **NB5** | **FOLDED** — a mandatory **Step 6-R — RESTORE** (5 assertions: the env restored to the post-arming state with the slug `e2e`; the mode still `0600`; the main-checkout `.env` unchanged; a re-boot proving the row is back; the run note recording it) — **runs even if a negative leg fails**. | §5 (the **Step 6-R** block) |
| **NB10** | **FOLDED** — the §4 census table now carries the **full 7** worktree key names (`PLANE_* = 0` confirmed correct) + the note that the ambient `POSTGRES_*` set makes the **per-variable** scrub load-bearing (the glob form is a silent NO-OP). | §4 (the census table) |

---

## G2 fold (approver iter-003, 2026-11-02) — FINAL FOLD — item disposition

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 003 **REJECTED** (2 blockers + 17 mechanical notes); **approver cap reached ⇒ the leader proceeds to implementation; there is NO further approver round.** **Architecture / D-1 / ADJ-1 / the phase structure / the 7-phase decomposition are NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered or repurposed.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB1** | **FOLDED** — the step-ID collision is resolved **without renumbering**: the A′ sub-step **keeps `Step 6.5`** (referenced by X5.12, P29, §2 and Step 3) and the **RESTORE is renamed `Step 6-R`** (it is the tail of Step 6); the two fold-register references are corrected; the RESTORE's assertions **4–5** get their own exit criterion **X5.15**. | §5 (the Step 6-R block) + §9 **X5.15** + the register row |
| **NB6** | **FOLDED** — the A′ keep-set is now **literally enumerated** (28 names, both forms) in the gate-(i) block, and A′.1 points at that artifact as its input ⇒ "an A′ flip needs no new analysis" is literally true. | §5 (A′.1) + `phase1-plan.md` §3 (the enumeration) |
| **NB9** | **FOLDED** — the **:8079 contention** (worktree `DAEMON_PORT=8079` vs main-checkout `PORT=8079`) is resolved by naming the authority: the **Phase-5 daemon is the main checkout's** (it carries the armed Plane config), the worktree is the **code source**; if the branch's code must run, the worktree daemon takes a **different non-conflicting port** and the run note records which daemon owned :8079. | §4 (the Port row) |

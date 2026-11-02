# Phase 4 — Charter reconciliation + durable doc persistence

**Lane:** `plane-pm-issue-tools` · base `941cb6321` · **Tier: Tier-1 scoped leg (prompt artifacts + daemon docstring); the agents/ payload change is a release-manifest change**
**Depends on:** Phase 0 (G2), Phase 1 gate (i) (the verb vocabulary source of truth) · **Blocks:** Phase 5 (PM's charter is what the Task-3 run executes against), Phase 6

---

## 1. Objective

Reconcile every **false** or **drifted** statement about the Plane tool surface — in the agent's prompts **and** in the daemon's own docstrings — and persist the Task-2 workspace directive **durably in the repo**.

**The drift is at the ENTITY level, not the prefix level.** There are **zero** `*_issue` verbs in the captured 177-tool inventory; the entity is `work_item`, and upstream's own `serverInfo.instructions` say so ("an epic is a work item whose type is named 'Epic'"). The charter names `plane_create_issue`, `plane_list_issues`, `plane_add_comment`, `plane_assign_issue` — **none of which exist**.

**The re-stamp coupling is atomic.** Any `agents/project-manager/*.md` edit ⇒ the prompt-artifact manifest re-stamp (**161/161**) **and** the `SEALED_SHA_BASELINES` re-pin for `agents/project-manager/workflow.md` — **in the SAME commit**. The seal tripwire is the enforcement mechanism: a stale row goes red.

---

## 2. File-level scope

### 2.1 MODIFIED — the PM prompt files (4 files)

> **> (G2 fold, approver iter-003, 2026-11-02 — blocker B1: Phase 4's VOCABULARY INPUT is now the named gate-(i) artifact — this is what breaks the producer/consumer cycle.)**
>
> | | |
> |---|---|
> | **Phase 4's vocabulary input** | **`.agents/shared/planning/plane-pm-issue-tools/gate-i-keep-set.json`** — the **machine-readable keep-set**, produced by the **gate-(i) executor** (the gate produces what it consumes). It enumerates the **28** keep-set verbs in **both** forms (raw verb + `plane_`-prefixed daemon name) and is also the **FC8 minimum-toolset input** for the A′ branch |
> | **What Phase 4 does with it** | the charter is reconciled **TO** that artifact — the rows below are the drift sites; the **replacement vocabulary** comes from the artifact (or a verb-agnostic summary + the inventory note, per lean #5) |
> | **Why this is the cycle-breaker** | the pre-fold text asked Phase 4 to derive the vocabulary while Phase 4 *depends on* gate (i) ⇒ circular. Now: gate (i) produces the artifact; Phase 4 consumes it; the gate's criterion 4 evaluates against it too ⇒ **no producer/consumer cycle remains** |
>
> **Numeric label correction (G2 fold, approver iter-003 — non-blocking item 5):** the row for `tools_note.md:37` below said *"all 8 names are non-existent"* — that is **wrong**: **6 of the 8** are non-existent (`plane_create_issue`, `plane_update_issue`, `plane_delete_issue`, `plane_add_comment`, `plane_remove_comment`, `plane_assign_issue`); **`plane_create_cycle` and `plane_update_cycle` DO exist** in the catalog (they are real cycle verbs, correctly prefixed). The required change is unchanged. Likewise, the phrase *"PM's charter names SIX verbs that do not exist"* (used in `phase1-plan.md`, `phase4-plan.md` and `phase0-plan.md`) enumerates **7 distinct names** across **10 occurrences** — corrected to "**seven** distinct names (ten occurrences)" wherever it appears.

Re-verified drift sites (I read every line in this pass):

| File:line | Current text | Problem | Required change |
|-----------|--------------|---------|-----------------|
| `agents/project-manager/tools_note.md:23` | the `plane_*` (read + write) row — "create/update/delete issues, cycles, comments, assignments" | names a non-existent entity vocabulary; also implies `plane_assign_issue` | rewrite the row to the real `*_work_item` / comment / cycle verbs |
| `agents/project-manager/tools_note.md:24` | "`plane_sync_project` — **NOT held by PM** — the spawned `worker` holds it." | **FALSE at runtime today** — with the server unregistered the sweep grants PM `plane_sync_project` (category collision). After Phase 3 it becomes true again, but the charter must state the *post-fix* truth and the mechanism | keep the claim (it is true post-Phase-3) and add the ownership rule: `plane_sync_project` is owned by the `plane_sync` category, held by the **worker** via `agents/worker/meta.json:29` |
| `agents/project-manager/tools_note.md:37` | the explicit verb list: `plane_create_issue`, `plane_update_issue`, `plane_delete_issue`, `plane_add_comment`, `plane_remove_comment`, `plane_create_cycle`, `plane_update_cycle`, `plane_assign_issue` | **6 of the 8 are non-existent** *(G2 fold iter-003 — the pre-fold "all 8" label was wrong: `plane_create_cycle` and `plane_update_cycle` **DO exist** — real cycle verbs)* | replace with the real verbs from **`gate-i-keep-set.json`** (the gate-(i) keep-set artifact — the B1 cycle-breaker): `plane_create_work_item`, `plane_update_work_item`, `plane_delete_work_item`, `plane_retrieve_work_item`, `plane_list_work_items`, `plane_search_work_items`, `plane_count_work_items`, the 5 comment verbs, the 10 cycle verbs (or a verb-agnostic summary + the inventory note) |
| `agents/project-manager/rule.md:5` | "create/update/delete issues, cycles, comments, assignments — enabled by `mcp_full_access: [\"plane\"]`" | entity vocabulary | verb-agnostic phrasing is acceptable (lean #5) — but the *entity* must be right: "work items (Plane's term for issues), cycles, comments" |
| `agents/project-manager/rule.md:39` | "update an issue, add a comment, close a cycle, assign an issue" | verb-agnostic English — acceptable | keep the phrasing; correct "issue" → "work item" |
| `agents/project-manager/workflow.md:14` | "create/update a project, update an issue, close a cycle, add a comment" | verb-agnostic | same treatment |
| `agents/project-manager/workflow.md:42` | "`plane_list_issues` for issues in relevant cycles" | **non-existent verb** | `plane_list_work_items` (+ the cycle-scoped verbs from the re-capture) |
| `agents/project-manager/workflow.md:53` | "`plane_list_cycles`, `plane_list_issues`" | non-existent verb | `plane_list_cycles`, `plane_list_work_items` |
| `agents/project-manager/workflow.md:142` | "`plane_list_cycles` for cycles … `plane_list_issues` for issues tagged or in matching cycle" | non-existent verb | as above, with the cycle-membership verbs (`plane_list_cycle_work_items`, `plane_manage_cycle_work_items`) where they are the accurate tool |
| `agents/project-manager/workflow.md:182` | "`plane_list_issues` filtered by cycle/feature" | non-existent verb | `plane_list_work_items` (filtered via `pql`) |
| `agents/project-manager/soul.md:11` | "Plane project work" | verb-agnostic | acceptable as-is; **no change required** — record this so the re-stamp's changed-file set is minimal and deliberate |

**Rules for the rewrite (lean #5 + W-9).**

- **Verb-agnostic English is acceptable**; **exact tool names must match reality**.
- The live inventory is **noted** (e.g. "Plane's entity is the *work item* — upstream exposes no `*_issue` verbs; the 177-verb catalog is captured in `<planning dir>/gate-i-tools-list-recapture.md`").
- Where the charter names a capability that does **not** exist as a single verb (e.g. "assign an issue"), either name the closest real verbs (`plane_update_work_item` with the assignment property) or phrase it capability-wise. **Do not invent a verb name.**
- The **write-verb discipline text** (Cardinal #1 — "surgical record operations, never bulk") stays; only the vocabulary changes.

### 2.2 MODIFIED — the daemon's own docstrings (2 files)

| File:line | Problem | Change |
|-----------|---------|--------|
| `daemon/mcp/builtin_servers/plane.py:143-152` | the `tool_name_prefix` docstring says: *"Plane's tools are exposed as `plane_list_issues`, `plane_create_issue`, etc."* — **the same drifted names** | rewrite to the real verbs. This is **daemon source**, so it needs **no** prompt-artifact re-stamp and **no** SEALED re-pin — but it IS inside the charter-reconciliation scope (AD4b). Keep the *behavioural* explanation (the `is_mcp_tool()` check does not match ⇒ they bypass `tools.deny: ["mcp"]` ⇒ they feel native) — that is load-bearing (P9) |
| `daemon/tools/plane_tools.py:14` | `CATEGORY_DOC` — "Tool names are prefixed with `plane_` (e.g., `plane_list_issues`)" | correct the example verb; also state the workspace requirement precisely ("Requires `PLANE_MCP_URL` + `PLANE_MCP_API_KEY`; the workspace slug is resolved per environment — non-production environments resolve to `e2e`") |

**Also in this phase (doc-only, zero risk, O6):** `daemon/tools/mcp_tools.py:17` documents a `call_mcp_tool` that does not exist — fix or remove the stale sentence.

### 2.3 NEW — the canonical runbook (Task-2 durable persistence, lean #7)

`docs/runbooks/plane-workspace-scoping.md` (NEW). **House precedent:** `docs/runbooks/project-skill-source.md` (the project-skill-loader lane's per-feature runbook) — the runbook dir is the canonical operator-facing surface. The runbook must carry:

1. The **directive** verbatim (user, Discord 2026-11-02): `nea` = production/live ONLY; dev + demo MUST target `e2e` for ALL Plane operations; enforcement is **structural**, never convention.
2. The **per-environment contract table** (= `plan-overview.md` §9) with who sets each variable.
3. The **resolver's behaviour**: fail-closed typed refusal on non-live when the slug is wrong **or** unset; the refusal reason vocabulary; **no kill-switch** for the guard.
4. The **D-1 note** — why the lane-local live determination exists (the shared auto-derive mislabels a repo checkout as `live` on a multi-install host), and that the shared oracle remains wrong for other consumers (O3).
5. The **operator caveats**: the live/demo credential is expired (R1) — a row + failing sessions is the expected state until the re-mint; `POST /api/mcp-servers/test-connection` **bypasses the kill-switch** (AD7/D7) ⇒ it is not a reliable "is Plane up?" probe; `GET /api/mcp-servers` returns the bearer in `config.headers` unredacted (AD2/O1) — treat the response as secret-bearing.
6. The **shell-layer defence-in-depth** reminder: `scripts/deploy.sh` pins `e2e` for demo and `scripts/run_tests_scrubbed.sh` scrubs + leak-gates the slug; the daemon guard is **additional**, never a replacement (NR2).
7. **> (G2 fold, approver iter-003 — non-blocking item 8: the OPERATOR ESCAPE HATCH is now a runbook caveat, and it is a required sentence — the risk register entry R22 points here.)** **The workspace guard governs the BUILTIN Plane lane only.** A **hand-created** MCP server (not the builtin) pointed at `nea` is **operator authority**: it is **not name-matched** to the builtin, so the name-keyed preload gate returns **"no opinion"** and the workspace policy does not speak to it. The REST-create endpoint is an operator-authority path as well. **The env-scoped guarantee (nea = production-only) therefore holds for the builtin lane and for the daemon-created rows — NOT for operator-created MCP servers.** The registry-vs-row cross-check (Phase 3) **does** name an operator-created row in its census (it is a *registered* server) ⇒ it is **visible**, not *refused*. **An operator who creates such a server has, deliberately, left the guard's scope.**
8. **The live/demo credential state** — the live/demo Plane MCP bearer is **expired** (R1/ADJ-4): a `plane` row exists and **every session fails 401**. A user re-mint is required for live efficacy; until then, the row's presence is **not** evidence that Plane works. (The lane's contribution: the failure is now **visible** — the WARNING taxonomy + the honest `test-connection` report — rather than silent.)

> **Amendment P4-B (architect G0, 2026-11-02 — folded append-only): two runbook corrections.**
> 1. **The deploy pin is DEFENCE-IN-DEPTH and covers only a deploy-generated `.env`.** `scripts/deploy.sh:255-276` sed-pins `PLANE_MCP_WORKSPACE_SLUG=e2e` and grep-asserts it — but only for the `.env` **that deploy.sh itself generates**. A **hand-written** `.env` (the dev checkout, an operator edit, a copied install) bypasses the pin entirely. The runbook must therefore say plainly: *the shell pin covers deploy-generated files only; the daemon guard is the structural enforcement and covers every `.env`.* This is the same "do not trust one layer" discipline as the `boot_subprocess_sweep.py:150-158` precedent.
> 2. **The frontend Plane indicator reflects ENV PRESENCE, not RESOLUTION.** The FE Plane surface is driven by `PLANE_BASE_URL` (`daemon/routers/settings.py:703-716` — the FE iframe gate; the architect confirmed this is **not** a slug leak). Post-Phase-2 the FE can show a Plane indicator while the daemon **refuses** the workspace ⇒ the indicator is **not** evidence that Plane is usable. The runbook must say so, and the FE indicator's meaning must be documented as *"configured"* rather than *"available"*. **No FE code change in this lane** — documentation only.
>
> **Re-verified by me in this fold:** `scripts/deploy.sh:255-276` (sed pin + grep assert) and `scripts/run_tests_scrubbed.sh:52`/`:78` (scrub + leak gate) exist as described; `daemon/routers/settings.py:703-716` is the FE `PLANE_BASE_URL` echo gate.

### 2.4 MODIFIED — `.env.example`

Extend the doctrine prose at `:185-190` (currently 6 lines) and the Plane variable block at `:195-209`:

- State the **daemon-layer** enforcement (it currently describes only the shell behaviour: "REST fails closed when unset, MCP silently targets the account-default (prod) workspace" — that sentence is now **stale**: after Phase 2 the MCP lane also fails closed on non-live).
- Add the per-environment guidance next to `PLANE_MCP_WORKSPACE_SLUG` (`:209`): `nea` = live only; `e2e` = dev/demo/sandbox; unset on non-live ⇒ refused.
- Keep the kill-switch comments at `:444-445` intact and note that `PLANE_MCP_ENABLED=false` is a *deliberate* off (the observability layer distinguishes it from an unexpected skip).

> **Amendment P4-A (architect G0, 2026-11-02 — folded append-only): two concrete `.env.example` corrections.**
> 1. **`:186-190` — the "silently targets the account-default (prod) workspace" sentence becomes FALSE post-Phase-2** and must be rewritten. After Phase 2 the MCP lane **fails closed** on a non-live env with an unset slug (that is pollution path **P2**, closed by E3). The new text must say: *unset on non-live ⇒ **refused** (typed reason, no row, no tools)* — not "silently targets". **The prose is a live hazard**: an operator reading the old sentence would believe an unset slug is harmless on dev, when before Phase 2 it was exactly the prod-pollution path.
> 2. **`:209` — the sample value `PLANE_MCP_WORKSPACE_SLUG=your-workspace-slug` becomes a hard refusal on NON-LIVE.** Post-Phase-2 a literal `your-workspace-slug` is refused on **every non-live environment** (live-exempt permits only a *configured* slug; `your-workspace-slug` is neither `nea` nor `e2e`). **Fix (revised by the G2 fold — see the note below):** ship a **COMMENTED** sample plus the doctrine prose.
>
> **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 6: the sample must be COMMENTED, not an active default.)** The G0 wording above said *"ship `PLANE_MCP_WORKSPACE_SLUG=e2e` as the sample value"* — **that is wrong and is superseded**: an **active** `PLANE_MCP_WORKSPACE_SLUG=e2e` line in `.env.example` would be copied verbatim by any new-install path and would **silently target the TEST workspace on every fresh install, including live** (live-exempt permits `e2e`, so the daemon would accept it — the guard governs *where*, not *who*).
> **The corrected requirement (binding):**
> - The `PLANE_MCP_WORKSPACE_SLUG` sample line is **COMMENTED OUT**: `#PLANE_MCP_WORKSPACE_SLUG=e2e` — with the adjacent comment stating **"live requires `nea`; non-live (dev/demo/sandbox) requires `e2e` when armed"**.
> - The **doctrine prose** at `:185-190` stays and is extended per item 1 above.
> - The sample must **not** be an active default anywhere in the file.
> **Verification:** a static check that the `.env.example` contains **no active** (uncommented) `PLANE_MCP_WORKSPACE_SLUG=` assignment — the same runner class as the P10 static-content pin.
>
> **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 5: the live-consistency correction inside P4-A.)** The pre-fold P4-A text claimed `your-workspace-slug` is refused "**everywhere**" — that is **FALSE on live** under the adjudicated **ADJ-1 live-exempt** rule (live permits **any configured slug**). The three places that disagreed (`decisions.md` ADJ-1, `phase2-plan.md` §2.1 policy table, this P4-A) now agree on exactly this: **live ⇒ any configured slug permitted** (unset ⇒ account-default); **non-live ⇒ `e2e` only**, and every other slug — **including `your-workspace-slug`** — is refused with the typed reason. **The missing pin is added:** *live + a third slug ⇒ ALLOWED* (`phase2-plan.md` §5 + `phase3-plan.md` §5).
>
> **Re-verified by me in this fold:** doctrine prose `.env.example:185-190`; sample slug line `.env.example:209`; Plane variable block `:195-209`; kill-switch comments `:444-445`.
>
> **> (G2 fold, approver iter-003, 2026-11-02 — non-blocking item 16: `.env.example:207-208` carry ACTIVE placeholder Plane keys — a FRESH-INSTALL hazard that is adjacent to P30's work on the same file. Remediation folded in-lane, because the file is already being edited and the hazard is prod-safety.)**
>
> | Line | Current | Hazard |
> |---|---|---|
> | `.env.example:207` | **`PLANE_MCP_URL=https://mcp.ensem.dev/plane/http/api-key/mcp`** — **ACTIVE** | a fresh install that copies `.env.example` verbatim registers a `plane` server against the **real production MCP endpoint** |
> | `.env.example:208` | **`PLANE_MCP_API_KEY=plane_api_your_api_key_here`** — **ACTIVE** | the **presence-only** gate (`plane.py:296-298`) sees a non-empty key ⇒ the row **materialises** ⇒ every session fails auth against the real endpoint. **The placeholder is indistinguishable from a configured credential to the daemon** |
> | `.env.example:209` | **`PLANE_MCP_WORKSPACE_SLUG=your-workspace-slug`** — **ACTIVE** | already covered by **P30** (must become **commented**) |
>
> **The remediation (folded into this phase, same file, same commit as the P30 change):** **all three Plane-plane sample lines become COMMENTED OUT** — `#PLANE_MCP_URL=…`, `#PLANE_MCP_API_KEY=…`, `#PLANE_MCP_WORKSPACE_SLUG=e2e` — with the doctrine prose (`:185-190`) carrying the per-environment guidance (**live requires `nea`; non-live requires `e2e` when armed**). **P30's static check is extended to assert there is NO active `PLANE_*` assignment in the Plane block** (not just the slug), so a fresh install cannot register a Plane row against a placeholder. **Pre-existing, disclosed:** the active lines are **pre-existing** (not introduced by this lane); the lane fixes them because it is already editing that exact file for P30 and the hazard is prod-safety (a fresh install pointed at the **production** endpoint with a placeholder bearer).

### 2.5 NEW — the P10 static-content pack (created by this phase)

> **> (G2 fold, approver iter-002, 2026-11-02 — blocker B1: this phase now OWNS a NEW repo artifact — the P10 static-content pack.)**
>
> | File | Action | Detail |
> |------|--------|--------|
> | `tests/packs/plane_shell_scrub_content.sh` | **NEW** | the **P10** static-content assertion pack: asserts (a) `scripts/deploy.sh` still contains the `PLANE_MCP_WORKSPACE_SLUG=e2e` **sed-pin** + its **grep-assert**; (b) `scripts/run_tests_scrubbed.sh` still contains the **per-variable unset** of `PLANE_MCP_WORKSPACE_SLUG`/`PLANE_BASE_URL` and the **leak-gate `exit 78`** (**`:101`** — the G2 fold corrects the pre-fold `:52/:78` anchor; the unset is at `:52`); (c) a **positive control** on the intact files ⇒ GREEN. **The pack READS the shell files; it never modifies them** (NR2 holds). Its RED leg is a **planted-absence** mutation in a scratch copy. |
> | `.agents/tester/PACKS.md` | **AMEND** | the **pack registration** row for `plane_shell_scrub_content` (invocation + scope + last-run + status columns, per the house convention) |
> | `tests/unit/test_plane_prompt_verb_check.py` | **NEW** *(G2 fold, approver iter-003 — non-blocking item 12: P11's verb check needed a NAMED host file.)* | the **P11** static check: every `plane_*` **MCP verb** in the four PM prompt files + the two daemon docstrings is a member of the gate-(i) catalog (`gate-i-keep-set.json` + the re-capture artifact), **minus the explicit three-name exclusion set** (`plane_sync_project`, `plane_sync`, `plane_sync_state`); no `*_issue` verb remains. RED leg: introduce an invented MCP verb name ⇒ RED; widen the exclusion set ⇒ RED. |
> | `tests/unit/test_plane_env_example_defaults.py` | **NEW** *(G2 fold, approver iter-003 — non-blocking item 12: P30's check needed a NAMED host file.)* | the **P30** static check: `.env.example`'s Plane block carries **no active** `PLANE_*` assignment (all three sample lines commented) and the doctrine prose states the per-environment rule. RED leg: restore any active line ⇒ RED. |
> | `.env.example` | **AMEND** | the **P30** no-active-default change (the sample commented out; no active `PLANE_MCP_WORKSPACE_SLUG=` assignment) — see §2.4 |
>
> **Why this phase owns the pack (and not another):** P10 asserts **shell-layer doctrine** content — the same doctrine surface as the `.env.example` prose (**P30**) and the runbook, both Phase-4 artifacts. **Phase 6 cannot own it**: Phase 6 declares the code **frozen** (`daemon/**, agents/**, scripts/** = NO CHANGE`) and creating a new pack file there would break the freeze. **Phase 3 owns the *runtime* guards; Phase 4 owns the *static* content pins** (P7/P10/P11/P12/P30) — the one place where a new pack file may be created without touching frozen code.

### 2.6 The atomic re-stamp (same commit)
| Artifact | Action |
|----------|--------|
| `agents/.prompt-artifacts.sha256` | regenerate to **161/161** (the manifest has 161 rows today; the PM rows are at `:120-123`). House authority: `scripts/protection/generate_prompt_artifact_baseline.py --check` (the script exists at `scripts/protection/generate_prompt_artifact_baseline.py`) |
| `tests/test_chart_image_delivery_e2e.py:1490` | re-pin the `SEALED_SHA_BASELINES` row for `agents/project-manager/workflow.md` to the new content sha (`bde47909…` today). **Same commit as the charter edits.** The seal tripwire goes RED if the row is stale — that is the enforcement, not an obstacle |

**Commit discipline (one commit):** the four prompt-file edits + the regenerated manifest + the SEALED row + the daemon docstrings + `.env.example` + the new runbook. **One commit. No exceptions.** (A split commit makes the seal tripwire red at the intermediate commit.)

---

## 3. Dependencies

| From | Why |
|------|-----|
| Phase 0 G2 | the reconciliation scope is frozen (W-9) |
| Phase 1 gate (i) | the verb list MUST come from the re-capture, not the wave-1 snapshot — upstream's catalog moves |
| ~~Phase 3~~ | **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 2: this pre-fold row was WRONG and is withdrawn.)** The pre-fold table listed a dependency on **Phase 3** ("the `plane_sync_project` ownership statement in `tools_note.md:24` must describe the post-fix truth"). **There is no such dependency.** Phase 4's charter reconciliation depends on the **gate-(i) re-capture** (the vocabulary), **not** on Phase 3's sweep guard — the charter states the ownership **rule** (`plane_sync_project` is owned by the `plane_sync` category and is held by the **worker**), and Phase 3's guard is what makes the runtime match the charter. **The charter is written to the correct post-fix truth either way**; if Phase 3's guard were absent, the charter would describe the intended ownership and the runtime would be wrong — which is a Phase-3 defect, not a Phase-4 dependency. **Row withdrawn (the text above is retained as the historical record); the dependency direction is Phase 3 → Phase 4 only in the sense that the *runtime* must satisfy the charter.** |

---

## 4. Tier gating (per `.agents/tester/rules/ensure.md:11-28`)

**Tier-1 — scoped dual-side leg** over the declared blast radius: `agents/project-manager/**`, `agents/.prompt-artifacts.sha256`, `tests/test_chart_image_delivery_e2e.py`, `daemon/mcp/builtin_servers/plane.py`, `daemon/tools/plane_tools.py`, `daemon/tools/mcp_tools.py`, `.env.example`, `docs/runbooks/plane-workspace-scoping.md`. Budget ≤ 30 min HARD, fail-loud. Recipe: `.agents/tester/skills/blast-radius-leg/`.

> **> (G2 fold, approver iter-002, 2026-11-02 — blocker B1: the Phase-4 blast radius now carries the P10 pack this phase CREATES.)** The lane **does not modify** the shell scripts (NR2) — but the P10 pack is a **NEW repo artifact this lane creates**, so it belongs in this phase's scope and blast radius:
>
> | Item | Detail |
> |---|---|
> | **NEW pack file** | `tests/packs/plane_shell_scrub_content.sh` — the static-content assertion pack for **P10** (see §5). It **reads** `scripts/deploy.sh` and `scripts/run_tests_scrubbed.sh`; it **never modifies** them |
> | **Pack registration** | `.agents/tester/PACKS.md` — a row for `plane_shell_scrub_content` with its invocation (pack-mapped, dual-layer timeout), its scope, its last-run and its status columns, per the house convention |
> | **Blast radius** | `tests/packs/plane_shell_scrub_content.sh` (new) + `.agents/tester/PACKS.md` (the registration row) — **added** to the Tier-1 list above |
> | **The pack's runner class** | *static/evidence* — its RED leg is a **planted-absence** mutation (delete/weaken the asserted content in a scratch copy), the same class as **P7** (the seal tripwire), **P11** (the static verb check) and **P23** (disclosure-presence) |
> | **Why Phase 4 owns it** | P10 asserts **shell-layer doctrine** content (the deploy `e2e` pin + the scrub + the leak gate) — the same doctrine surface as the `.env.example` prose (P30) and the runbook, both of which are Phase-4 artifacts. **Phase 6 cannot own it**: Phase 6 declares the code **frozen** (`daemon/**, agents/**, scripts/** = NO CHANGE`) and creating a new pack file there would break the freeze |
>
> **The master-index home claim is corrected:** the pre-fold index said P10's home was "phase4-plan.md §5 (P7/P10/P11/P12)" — but phase4 §5 carried **no P10 row** (the pin was homeless). **This fold adds the row** ⇒ the index claim becomes true, and `plan-overview.md` §11's master index is corrected in the same fold.

**Static checks (fast, no pytest)** — the house "Core" section is pack-mapped, and two items are pure static checks:
- the prompt-artifact baseline check (`generate_prompt_artifact_baseline.py --check` ⇒ 161/161 clean);
- the seal tripwire (the `SEALED_SHA_BASELINES` pack) ⇒ green **only** with the re-pin.

**The `agents/` payload tree hash changes** ⇒ the release manifest's `agents_tree_sha256` changes ⇒ the stage-time manifest delta must record it (Phase 6).

**Fences:** as Phase 2/3 (dual-layer timeout, per-variable scrubs, `-p no:randomly`, worktree-local venv, pack-mapped, slicing proof).

---

## 5. Pin / test obligations

| # | Pin | Cases | Mutation |
|---|-----|-------|----------|
| **P7** | **charter/baseline coupling** | the seal tripwire MUST go RED when a `agents/project-manager/*.md` file is edited **without** the same-commit re-pin, and GREEN after the re-pin | edit a PM `.md` file without re-pinning ⇒ the seal tripwire goes RED (the existing tripwire *is* the mutation) |
| **P11** | **no invented verbs** | **> (G2 fold, approver iter-003, 2026-11-02 — BLOCKER B2: P11 is scoped to the MCP VERB surface with an EXPLICIT exclusion set, so it is satisfiable at Phase 4's own mandated end-state.)** Every **`plane_*` MCP verb** that appears in `agents/project-manager/*.md` **or** in the two daemon docstrings must be a member of the **gate-(i) re-captured catalog** — **evaluated against `gate-i-keep-set.json` and the re-capture artifact** (the B1 artifact). **THE EXPLICIT EXCLUSION SET (three names, verbatim — these are NOT MCP catalog verbs and are NOT checked against the catalog):** (1) **`plane_sync_project`** — the **daemon-native** tool (`daemon/tools/plane_sync.py:212`, category `plane_sync`), which §2.1 row 2 **mandates** `tools_note.md:24` keep (with an added ownership rule naming it again); (2) **`plane_sync`** — a **non-tool identifier** (the `plane_sync` *category* name, `agents/worker/meta.json:29`); (3) **`plane_sync_state`** — a **non-tool identifier** (the project-metadata key `plane_sync_state`). **No other identifier is exempt** — any other `plane_*` name in the prompt/docstring surface must be a catalog member, and the check is **not** widened by judgement: the exclusion set is exactly these three names. **Host file for the check: `tests/unit/test_plane_prompt_verb_check.py` (NEW — created by this phase; see §2.5)** | introduce one invented MCP verb name into a prompt file ⇒ the static check goes RED. **The three excluded names must NOT trip the check** — adding `plane_sync_project` to the exclusion set ⇒ the check goes RED (the exclusion set is an exact 3-name list) |
| **P12** | **the false `plane_sync_project` claim stays corrected** | the charter asserts PM does **not** hold `plane_sync_project`, and a runtime check confirms PM's toolset excludes it in both server states (the Phase-3 P1 legs) | restore the pre-Phase-3 sweep behaviour ⇒ the runtime leg goes RED |
| **P10** *(G2 fold, approver iter-002 — blocker B1: the pin is now HOMED here, with its own pack.)* | **Shell scrubs intact** — the lane **does not modify** `scripts/deploy.sh` or `scripts/run_tests_scrubbed.sh` (NR2), so the *proof* that they are intact is a **static-content assertion pack** that the lane **creates**: `tests/packs/plane_shell_scrub_content.sh`. The pack asserts: (a) `scripts/deploy.sh` still contains the `PLANE_MCP_WORKSPACE_SLUG=e2e` **sed-pin** and its **grep-assert**; (b) `scripts/run_tests_scrubbed.sh` still contains the **per-variable unset** of `PLANE_MCP_WORKSPACE_SLUG`/`PLANE_BASE_URL` and the **leak-gate `exit 78`** (the gate's `exit 78` is at **`scripts/run_tests_scrubbed.sh:101`** — the G2 fold corrects the pre-fold `:52/:78` anchor); (c) a **positive control** on the intact files ⇒ GREEN. **The RED leg is real because the file did not exist** — the pre-fold state had **no** implementation home for this pin (grep-verified: `tests/packs/plane_shell_scrub_content.sh` does not exist in the repo; only 2 mentions of the name existed, both in planning files). | **delete/weaken** the asserted lines in a scratch copy (or plant the weakened variant) ⇒ the content assertions go RED; restore ⇒ GREEN |
| **P30** *(G2 fold, approver iter-002 — blocker B3: the no-active-default static check gets its own pin; **G2 fold, approver iter-003 — non-blocking items 12 + 16: the check is EXTENDED to the whole Plane block and its HOST FILE is named**)* | **`.env.example` carries NO active Plane assignment** — the `PLANE_MCP_URL` (`:207`), `PLANE_MCP_API_KEY` (`:208`) and `PLANE_MCP_WORKSPACE_SLUG` (`:209`) sample lines are **all COMMENTED OUT** (`#PLANE_MCP_URL=…`, `#PLANE_MCP_API_KEY=…`, `#PLANE_MCP_WORKSPACE_SLUG=e2e`) and there is **no active** (uncommented) `PLANE_*` assignment in the Plane block; the doctrine prose at `:185-190` states that **live requires `nea`** and **non-live requires `e2e` when armed**. **The RED baseline leg is real and on disk today:** `.env.example:207-209` carry **active** lines (the URL, the placeholder bearer, and the slug) — **verified by me in this fold**. **Why this is prod-safety, not cosmetics:** under the adjudicated **ADJ-1 live-exempt** rule live permits **any configured slug**, so an active `e2e` default would be **accepted on live** ⇒ a fresh install would silently point at the **test** workspace; and the **presence-only** gate (`plane.py:296-298`) would materialise a `plane` row against the **real production MCP endpoint** with a **placeholder bearer**. **HOST FILE for the check: `tests/unit/test_plane_env_example_defaults.py` (NEW — created by this phase; see §2.5).** | restore any active `PLANE_*` assignment in the block (or add one) ⇒ the static check goes RED; comment all three out ⇒ GREEN |

**No-regression obligations:** NR2 (the shell files are byte-identical to the base in this phase's diff); NR4 (the `plane.py:143-152` docstring rewrite must preserve the **behavioural** explanation — the `is_mcp_tool()` bypass — because P9 depends on the code path the docstring documents).

---

## 6. Exit criteria

| # | Criterion |
|---|-----------|
| **X4.1** | **> (G2 fold, approver iter-003 — blocker B2: restated with the EXPLICIT exclusion set, so it is satisfiable at Phase 4's mandated end-state.)** Every **`plane_*` MCP verb** in the PM prompts and the two daemon docstrings is a member of the **gate-(i) re-capture** (P11) — **evaluated against `gate-i-keep-set.json` + the re-capture artifact** — and **no** `*_issue` verb remains. **THE EXPLICIT EXCLUSION SET (three names, verbatim): `plane_sync_project`** (the daemon-native tool at `daemon/tools/plane_sync.py:212`, whose mention §2.1 row 2 **mandates**), **`plane_sync`** (the non-tool category identifier), **`plane_sync_state`** (the non-tool metadata key). **These three are not catalog verbs and are not checked against the catalog; no other identifier is exempt.** |
| **X4.2** | `agents/project-manager/soul.md` reviewed — changed only if required (default: unchanged, deliberately). |
| **X4.3** | The prompt-artifact manifest is **161/161 clean** at the commit (`generate_prompt_artifact_baseline.py --check` ⇒ OK). |
| **X4.4** | The `SEALED_SHA_BASELINES` row for `agents/project-manager/workflow.md` matches the new content sha — **in the same commit** as the charter edits (P7). |
| **X4.5** | `docs/runbooks/plane-workspace-scoping.md` exists and carries all six required sections (§2.3). |
| **X4.6** | `.env.example` carries the updated doctrine (including the now-stale "MCP silently targets the account-default" sentence corrected) and the per-environment guidance. |
| **X4.7** | Tier-1 scoped leg PASS; static checks PASS; the seal tripwire green. |
| **X4.8** | The changed-file list is exactly the intended set — no incidental `agents/` edits. |
| **X4.9** *(P4-A)* **> (G2 fold, approver iter-002 — blocker B3: REWORDED. The pre-fold wording "`:209` ships `e2e`" was satisfiable by an **ACTIVE** assignment, which the binding NB6 requirement forbids on prod-safety grounds — live-exempt would ACCEPT `e2e` on live, so an active default silently points fresh installs at the TEST workspace.)** | The `.env.example` change satisfies **both** halves: (i) `:186-190` no longer claims the MCP lane "silently targets the account-default (prod) workspace" (false post-Phase-2); and (ii) **the `PLANE_MCP_WORKSPACE_SLUG` sample is COMMENTED OUT** — `#PLANE_MCP_WORKSPACE_SLUG=e2e` — with the adjacent comment **"live requires `nea`; non-live (dev/demo/sandbox) requires `e2e` when armed"**, and **NO active `PLANE_MCP_WORKSPACE_SLUG=` assignment anywhere in the file**. The stale `your-workspace-slug` value is **removed** (it is neither `nea` nor `e2e` ⇒ refused on every non-live env). **The static check that proves (ii) is pin P30**, whose **RED baseline leg is the active `:209` assignment on disk today**. |
| **X4.11** *(G2 fold, approver iter-002 — blocker B3: the static check needs its own exit criterion, and the pack needs its own exit criterion.)* | The **P30** static check **exists and runs** (no active `PLANE_MCP_WORKSPACE_SLUG=` assignment in `.env.example`; the sample is commented; the "live requires `nea`" comment is present), and its **RED leg is demonstrated** against the on-disk active `:209` assignment (a scratch copy with the line restored ⇒ the check goes RED). |
| **X4.12** *(G2 fold, approver iter-002 — blocker B1: the P10 pack needs its own exit criterion.)* | The **P10** pack `tests/packs/plane_shell_scrub_content.sh` **exists**, is **registered in `.agents/tester/PACKS.md`** (invocation + scope + last-run + status), **runs green** against the intact shell files (the deploy `e2e` sed-pin + grep-assert; the per-variable unset; the leak-gate `exit 78` at `scripts/run_tests_scrubbed.sh:101`), and its **RED leg is demonstrated** (a scratch copy with the asserted lines deleted/weakened ⇒ the content assertions go RED; restore ⇒ GREEN). **The shell files themselves are byte-identical to the base** (NR2). |
| **X4.10** *(P4-B)* | The runbook states that the deploy pin covers **deploy-generated `.env` files only** and that the daemon guard is the structural enforcement; and that the FE Plane indicator reflects **env presence, not resolution** (no FE code change in this lane). |

---

## 7. Fold register — architect G0 amendments mapped to Phase 4

**Fold date:** 2026-11-02 · **Source:** `architecture-recommendation.md` §7 (Phase 4 block) · **Mode:** append-only.

| Amendment | Substance | Where folded in this file |
|-----------|-----------|---------------------------|
| **P4-A** | fix `.env.example`: (1) `:186-190` "silently targets account-default" becomes false post-Phase-2 ⇒ rewritten as "fails closed"; (2) `:209` sample `your-workspace-slug` becomes a hard refusal everywhere ⇒ ship `e2e` + a "live requires `nea`" comment | §2.4 (the P4-A block) + §6 X4.9 |
| **P4-B** | runbook: the deploy pin is **defence-in-depth** (covers only deploy-generated `.env`); the FE Plane indicator reflects **env presence, not resolution** | §2.3 (the P4-B block) + §6 X4.10 |

**Phase 4 carries 2 of the architect's §7 amendment IDs** (P4-A, P4-B).

**Additional binding rider carried into this phase (architect §1.2):** the charter reconciliation is a **hard prerequisite for the Task-3 turn** — PM's charter names **seven distinct non-existent verbs** today (across **ten occurrences** — *G2 fold iter-003: the pre-fold "SIX" label was a miscount*), so Phase 4 **must land before** Phase 5's agent turn. The dependency is already recorded in `phase1-plan.md` §3 (Rider binding) and `phase5-plan.md` §6; this phase carries the **obligation** to make the charter name only real verbs.

---

## G2 fold (approver iter-001, 2026-11-02) — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 001 **REJECTED** (3 blockers + 9 non-blocking). **Architecture NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB2** | **FOLDED** — the pre-fold dependency row on **Phase 3** is **withdrawn** (the text is retained as the historical record): the charter depends on the **gate-(i) re-capture** for its vocabulary, not on Phase 3's sweep guard. | §3 (the G2 fold NB2 row) |
| **NB5** | **FOLDED** — P4-A's claim that `your-workspace-slug` is refused "**everywhere**" is corrected: it is refused on **non-live**; live permits **any configured slug** (ADJ-1 live-exempt). The missing pin (*live + a third slug ⇒ ALLOWED*) is added in `phase2-plan.md` §5. | §2.4 (the G2 fold NB5 block) |
| **NB6** | **FOLDED** — the `PLANE_MCP_WORKSPACE_SLUG` sample must be **COMMENTED OUT** (`#PLANE_MCP_WORKSPACE_SLUG=e2e` + "live requires `nea`; non-live requires `e2e` when armed"). An **active** `e2e` default would be copied by any fresh-install path and would **silently target the TEST workspace — including on live** (live-exempt permits `e2e`). A static check asserts there is **no active** `PLANE_MCP_WORKSPACE_SLUG=` assignment in `.env.example`. | §2.4 (the G2 fold NB6 block) |

---

## G2 fold (approver iter-002, 2026-11-02) — FINAL FOLD — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 002 **REJECTED** (4 blockers + 10/13 non-blocking). **Architecture / D-1 / ADJ-1 / phase structure NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **B1** | **FOLDED** — the **P10** static-content pack `tests/packs/plane_shell_scrub_content.sh` is **CREATED by this phase**: file scope (§2.5), Tier-1 blast radius + the "why Phase 4" rationale (§4), the **PACKS.md** registration, the pin row (§5 **P10**), and the exit criterion (**X4.12**). The master-index home claim (which was false) is corrected in `plan-overview.md` §11. | §2.5 + §4 + §5 + §6 |
| **B3** | **FOLDED** — **X4.9 is reworded** to require the **COMMENTED** sample + **no active** `PLANE_MCP_WORKSPACE_SLUG=` assignment anywhere (the pre-fold wording was satisfiable by an active assignment, which live-exempt would **accept on live** ⇒ a fresh install would silently point at the TEST workspace); the check gets its own **pin (P30)** and its own **exit criterion (X4.11)**; the **RED baseline leg is the active `.env.example:209` on disk today** (verified by me). | §5 (**P30**) + §6 (**X4.9** reworded, **X4.11**) + `plan-overview.md` §11 (**P30** row) |

---

## G2 fold (approver iter-003, 2026-11-02) — FINAL FOLD — item disposition

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 003 **REJECTED** (2 blockers + 17 mechanical notes); **approver cap reached ⇒ the leader proceeds to implementation; there is NO further approver round.** **Architecture / D-1 / ADJ-1 / the phase structure / the 7-phase decomposition are NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered or repurposed.

| Item | Disposition | Where |
|------|-------------|-------|
| **B1** | **FOLDED** — **Phase 4's vocabulary input is now the named gate-(i) artifact** `gate-i-keep-set.json` (the circularity break: the gate produces it, Phase 4 consumes it). | §2.1 (the input block) + §2.1 (the drift-site table) |
| **B2** | **FOLDED** — **P11/X4.1 are scoped to the MCP-verb surface with an EXPLICIT three-name exclusion set** (`plane_sync_project`, `plane_sync`, `plane_sync_state` — verbatim in both), so they are satisfiable at Phase 4's own mandated end-state; the expected set is **not** widened by judgement. | §5 (**P11**) + §6 (**X4.1**) |
| **NB5** | **FOLDED** — the numeric labels are corrected: **6 of the 8** names in `tools_note.md:37` are non-existent (`plane_create_cycle`/`plane_update_cycle` **do** exist), and the "SIX verbs" label becomes **seven distinct names / ten occurrences** wherever it appeared. | §2.1 (the numeric-label note + the `tools_note.md:37` row) + the set-wide label corrections |
| **NB12** | **FOLDED** — the two static checks now have **named host files**: **`tests/unit/test_plane_prompt_verb_check.py`** (P11) and **`tests/unit/test_plane_env_example_defaults.py`** (P30). | §2.5 (the file-scope rows) + §5 (**P11**, **P30**) |
| **NB16** | **FOLDED** — the **active placeholder** Plane keys at `.env.example:207-208` are remediated **in-lane** (same file, same commit as P30): all three Plane sample lines become **commented**, and P30's check is extended to assert **no active `PLANE_*` assignment in the Plane block** — closing the fresh-install hazard (a row registered against the **real production endpoint** with a placeholder bearer). Disclosed as **pre-existing**. | §2.4 (the NB16 block) + §5 (**P30**) |
| **NB8** | **FOLDED** — the **operator escape hatch** is now a required runbook caveat (the guard governs the **builtin** lane only; a hand-created MCP server at `nea` is operator authority and is *visible* but not *refused*). | §2.3 (runbook caveat 7) + `plan-overview.md` §6 (**R22**) |

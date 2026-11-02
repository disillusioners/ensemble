# GATE B — Phase-5 Task-3 END-TO-END VERIFICATION — run record

**Lane:** `plane-pm-issue-tools` · **Tree:** `964778d484650f909fdb84e4e3a9484d97a1c821` (36 commits over base `941cb6321`)
**Executed:** 2026-11-02 17:47–19:0x Z · **Executor:** worker (Gate-B dispatch)
**Authority:** `.agents/shared/planning/plane-pm-issue-tools/phase5-plan.md` (§4 fence · §5 procedure · §5.6 Step-6.5 A′ · §7 triage · §8 pins · §9 exit criteria) + `gate-i-tools-list-recapture.md` §7 (the FC9 formula)

---

## 0. Outcome

| Step | Verdict | One-line evidence |
|---|---|---|
| **0 — ARMING** | **PASS** | 5 `PLANE_*` names seeded into the worktree `.env` (mode `0600`); slug asserted `e2e`; `PLANE_SYNC_ENABLED` NOT seeded; main-checkout `.env` byte-unchanged |
| **1–2 — BOOT + RESOLUTION** | **PASS** | source-run from the worktree on **:8972**; `livez` 200 v0.28.5; `readyz` READY, all components true, `reasons: []`; resolution `env=dev source=explicit-marker slug=e2e`, `allowed=True` |
| **3 — REGISTRATION + CENSUS + FC9** | **PASS** (census) · **FC9 = BREACH** | plane row registered (`config_schema_version=3`, `x-workspace-slug: e2e`); **177** plane tools (SC1 ≥ 100 ✔); `plane_create_work_item` present; `plane_sync_project` absent; `present_tokens = 60 635` vs baseline 20 468 ⇒ **BREACH** (a=✗, b=✗) |
| **4 — THE TASK** | **PASS** | a REAL `project-manager` instance (real LLM `agentic`, real Plane MCP) made `plane_list_projects` → `plane_create_work_item`; **one** create call, `is_draft=true`, title + body verbatim; work item **`f445a1cd-f55b-48cf-945e-8043e2a0055c`** (`E2E-8`) |
| **5 — READ-BACK** | **PASS with a disclosed platform constraint** | REST lane (independent, `X-Api-Key`) ⇒ 200 on the activities sub-resource ⇒ the draft EXISTS in workspace `e2e` / project `0e9e0143…`; content + `is_draft=true` proven from the Plane server's own row serialization (HTML→text round-trip **byte-identical** to the verbatim body) |
| **6 — NEGATIVES N-1..N-5** | **PASS (5/5)** | every refusal carries the daemon's OWN typed reason on the boot WARNING *and* the REST lane; N-4's four-part P15a holds; N-5's mechanism-level P15b holds |
| **6-R — RESTORE** | **PASS** | slug back to `e2e`, mode `0600`, main `.env` unchanged; **re-boot ⇒ the plane row IS present, active, slug `e2e`** |
| **6.5 — A′ BRANCH** | **EXECUTED** (FC9 BREACH ⇒ the pre-committed default) | the 28-verb keep-set grant + charter vocabulary + the 161/161 re-stamp + the SEALED re-pin + the FC8 minimum-toolset + drift-detector pins, in **ONE atomic commit** |
| **TEARDOWN** | **PASS** | SIGTERM-only, graceful shutdown, no orphans, scratch DB + role dropped, scratch root + fake HOME removed, :8972 freed |

**Created Plane draft work item:** id `f445a1cd-f55b-48cf-945e-8043e2a0055c` · identifier `E2E-8` · project `0e9e0143-c9ad-4f58-9114-0d8b549ddea6` (`e2e`) · workspace `80859836-bd99-4b6e-bebe-cd29a0cc9dda` (`e2e`) · created `2026-11-02T18:03:45Z` · `is_draft=true` · **no URL field is returned by the Plane create API** (the identifier + REST URL are the handles).

---

## 1. FC9 — the numbers (the pre-committed rule, NOT adjusted at execution time)

```
growth_tokens = present_tokens − baseline_tokens
growth_pct    = growth_tokens / baseline_tokens
FC9 CEILING (breach if EITHER condition fails):
  (a) growth_tokens <= max(10_000, 0.25 × baseline_tokens)
  (b) growth_pct    <= 0.25
```

| Leg | `baseline_tokens` | `present_tokens` | `growth_tokens` | `growth_pct` | (a) | (b) | **VERDICT** |
|---|---|---|---|---|---|---|---|
| **PRE-narrowing** (the whole `plane` category — 177 verbs) | 20 468 | **60 635** | **40 167** | **1.962429 (196.24 %)** | ✗ | ✗ | **BREACH** |
| **POST-narrowing** (A′ — the 28-verb keep-set grant) | 20 468 | **29 313** | **8 845** | **0.432138 (43.21 %)** | ✓ | ✗ | BREACH (measurement, see below) |

* `baseline_tokens = 20 468` — Gate A's re-measured baseline **on this exact tree** (`task3-evidence/fc9_baselines.json` → `.baseline_tokens`), **not** the historical 20 111.
* The **comparison form is sort_keys+compact** on BOTH legs (`json.dumps(convert_to_openai_tool(t), sort_keys=True, separators=(",",":"))`) — Gate A's binding form. The plain-dumps variant (68 744 post-narrowing / 68 744 pre-narrowing plain form) is recorded for reconciliation only.
* Component split: pre-narrowing `system_prompt 9 417 + tool schemas 51 218`; post-narrowing `system_prompt 9 380 + tool schemas 19 933`.
* Determinism: **two** independent harness runs → byte-identical numbers and the same system-prompt sha256.
* **The pre-narrowing verdict is the routing verdict**: BREACH ⇒ A′ executes (the pre-committed default, not a judgement call).

**Recorded fact (routed back to the architect — NOT waived, NOT adjusted here).** With the pre-derived 28-verb keep-set the plane schema-token growth is **7 881** tokens, while condition (b)'s headroom at 25 % of the 20 468 baseline is **5 117** tokens ⇒ **condition (b) is unachievable by construction for any plane grant** — the same structural asymmetry the plan itself recorded for the absolute floor of condition (a) ("a small `baseline_tokens` would produce a ceiling that is unachievable by construction"). The A′ narrowing fixes (a) (40 167 → 8 845 ≤ 10 000) and cuts the surface by **51.7 %**, but it cannot make (b) true. A′ was still the mandated branch; the measurement is the deliverable.

---

## 2. The environment fence (phase5-plan §4) — every leg

| Fence | Value / proof |
|---|---|
| **F1** scratch root | `mktemp -d /tmp/plane-pm-gateb.scratch.XXXXXX` — never `~/agents-ensemble`, never `~/ensemble-src` |
| **F2** source-run | `cd /home/nea/ensemble-src-wt-plane-pm` → `env -i … /home/nea/ensemble-src/.venv/bin/python -m daemon` — **no frozen binary, no staged artifact**; the venv is a separate install, the main checkout's tree was never modified |
| **F3** `ENSEMBLE_SELF_ENV` | **`dev`** pinned EXPLICITLY in every boot ⇒ the D-1 explicit-marker branch ⇒ non-live ⇒ `e2e` permitted |
| **F4** boot sweep | `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned **EXPLICITLY** — never `env -u` (the sweep kills live processes) |
| **F5** env scrub | **`env -i` allowlist** — nothing ambient inherited (the worker shell is live-poisoned: `POSTGRES_DB=ensemble_prod`, `ENSEMBLE_SELF_ENV=live`); `POSTGRES_*` set EXPLICITLY to the scratch DB; `PLANE_*` set EXPLICITLY from the armed worktree `.env` |
| **F6** FAKE_HOME | `mktemp -d` fake HOME + escape guard (neither scratch path under `/home/nea/agents-ensemble*` nor `/home/nea/ensemble-src*`); the daemon's promote journal resolved to **no install dir** (`install_dir=<none — dev/unresolved>`) ⇒ it never resolved into the live install |
| **F7** scratch PG | throwaway role + scratch DB on the **LOCAL** cluster `127.0.0.1:5432` (PostgreSQL 16.15); `ensemble_prod` **never addressed** (it does not exist on this local cluster — the live prod DB is on the remote `10.44.0.2`); role + DB **DROPped** at cleanup |
| **F8** port | **8972** — verified free per `ss` before every boot; never 9797 / 7979 / 8079 / 8971. A hard preflight guard in the boot script refuses those four and refuses an already-bound port |
| **F9** teardown | **SIGTERM-only**; `Graceful shutdown complete.` + `Application shutdown complete.` observed in every log; **zero** orphan `python -m daemon`; **zero** orphan MCP stdio children; :8972 freed |
| **F10** live fence | live `state.json` sha256 **PRE == MID == POST** = `693998100c2ba18cb5848c165f4592fcab2fea2e5664ff29aa3a2ac0d7cc5c4a`; live daemon **pid 643626** unchanged on :9797 (uptime continuous 68 910 s → 72 345 s, v0.28.4 throughout); `current -> releases/v0.28.4` untouched; live demo :7979 pid 525852 untouched (v0.16.3) |
| **F11** `.env` mutation | the worktree `.env` was the ONLY `.env` written (Step 0 seeding + the N-1..N-3 slug mutations + the Step 6-R restore) — each under the Step-0 fenced idiom (umask 077 → `mktemp` in the SAME directory → `mv -f` → `chmod 600`); the live install `.env` and the main checkout `.env` were **read-only** (main-checkout sha256 `316124135557b241192659789256a1dc1cc0e3f6c8c39103445a179819ed4f0f` IDENTICAL pre/post, mode `0600`) |

---

## 3. Step-by-step record

### Step 0 — ARMING (the STOP branch was NOT taken)

| Key name | Pre-run | Post-run |
|---|---|---|
| `PLANE_MCP_URL` | ABSENT | PRESENT |
| `PLANE_MCP_API_KEY` | ABSENT | PRESENT |
| `PLANE_MCP_WORKSPACE_SLUG` | ABSENT | PRESENT — **value asserted `e2e`** |
| `PLANE_BASE_URL` | ABSENT | PRESENT |
| `PLANE_API_KEY` | ABSENT | PRESENT |
| `PLANE_SYNC_ENABLED` | ABSENT | ABSENT (correctly NOT seeded) |

The worktree `.env` carried **7** key names pre-run and **12** post-run (the five seeded). Mode `0600` throughout. **No value of any credential appears in this note, in any artifact, or in any log** — the census command is name-only by construction and the seeding script never prints a value.

### Steps 1–2 — BOOT + RESOLUTION

Booted from the worktree on **:8972**; `livez` → `{"status":"alive","version":"0.28.5"}`; `readyz` → `status: ready`, all four components true, `reasons: []`, `grace_active: false`, `leak_signal.detected: false`, `mcp.plane.state: "ok"`.

**Resolution proof (the daemon's OWN surface — the boot line emitted by `daemon/mcp/builtin_servers/plane.py`, the resolver's only typed-reason interpolator):**

```
18:22:02 - daemon.mcp.builtin_servers.plane - INFO - Plane workspace resolution: env=dev source=explicit-marker slug=e2e
```

⇒ classification **non-live** (`env=dev`), source **explicit-marker**, effective slug **`e2e`**, **`allowed=True`**.

### Step 3 — REGISTRATION · CENSUS · FC9

* `mcp_servers` row `plane` — id `db742a0d-5232-4a8a-8c48-d13fc9d94dfa`, `is_builtin=true`, `is_active=true`, **`config_schema_version = "3"`** (the bumped value), `config.url = https://mcp.ensem.dev/plane/http/api-key/mcp`, **`config.headers['x-workspace-slug'] == "e2e"`**; the `Authorization` bearer is **REDACTED** in every artifact.
* The boot log carries the row creation (`Created MCP server: … name=plane` / `Created built-in MCP server: plane`) and the schema discovery (`eager_warm_schemas: primed 2/3 MCP server schema(s) (plane: 177 tool(s), context7: 2 tool(s), webfetch: 0 tools)`).
* **Census (SC1 / P18 / P22):** `plane_tool_count = 177` ⇒ **≥ the 100 threshold ⇒ SC1 SATISFIED**; `plane_create_work_item` **PRESENT**; `plane_sync_project` **ABSENT** (the W-8 prefix-sweep ownership guard); all nine probed charter verbs present; **no `mcp_`-prefixed** name in the resolved set (PM denies the `mcp` category); the loud-skip WARNING is **ABSENT** in the armed state.
* `present_tokens` measured with the **identical** Gate-A harness + form (see §1). Verdict: **BREACH** ⇒ the A′ branch is the mandated outcome.

### Step 4 — THE TASK (a real PM instance, a real LLM, a real Plane MCP)

* A `project-manager` instance was spawned through the daemon's own API on the **default UUID path** (no custom `instance_id` ⇒ the MCP-name lane is populated as at runtime): `0878c3ec-609f-436f-bcbe-448e50bddabe`, model `agentic`, `mcp_tool_names` = 179 entries of which **177 are `plane_`-prefixed**.
* **PM's actual tool calls, verbatim (tool name + arguments, minus secrets):**

  1. `plane_list_projects` → 2 projects (`__system_default__` / `SYSTEMDEFAUL`, and `e2e` / `E2E`).
  2. `plane_create_work_item` with
     * `name` = `Draft: Guidelines system — fixed dev-team-managed reference docs with intelligent agent loading` (**byte-identical** to the dispatch's verbatim TITLE),
     * `description_stripped` = the four-section body (**byte-identical** to the dispatch's verbatim BODY; sha256 `76de12f877c362cc4af82cfd105041aef5761bc540d0af9851ac5fd99adf60d8` on both sides),
     * `is_draft` = **true**,
     * `project_id` = `0e9e0143-c9ad-4f58-9114-0d8b549ddea6` (the `e2e` project).
* **One** create call. **No** create-then-delete. **No** write to `nea`. The Plane server's own response: `id = f445a1cd-f55b-48cf-945e-8043e2a0055c`, `sequence_id = 8`, `is_draft = true`, `workspace = 80859836-bd99-4b6e-bebe-cd29a0cc9dda`, `project = 0e9e0143-…`, `created_at = 2026-11-02T18:03:45Z`.
* The dispatch form was **(b) — the operator handed the verbatim text** (the scratch DB has no project-history event `8ffd3082-a1bb-4273-a5ea-e9ac87f5ceca`); the text is authoritative verbatim either way.
* One canonical path, no retry needed.

### Step 5 — READ-BACK (independent channel) + the disclosed platform constraint

**Channel A — Plane REST (`X-Api-Key`), independent of the MCP write lane:**

```
GET /api/v1/workspaces/e2e/projects/0e9e0143-c9ad-4f58-9114-0d8b549ddea6/work-items/f445a1cd-f55b-48cf-945e-8043e2a0055c/activities/
→ 200, 1 activity: verb="created", comment="created the issue",
                   issue=f445a1cd…, project=0e9e0143…, workspace=80859836…
```

⇒ the draft **exists**, in workspace **`e2e`**, in project **`e2e`**, created by the Plane API key's user, at the create timestamp.

**The disclosed deviation (a PLATFORM constraint of this Plane edition, not a lane or agent defect).** The work-item **DETAIL** (`…/work-items/<id>/`) and **LIST** (`…/work-items/?per_page=100`) endpoints return **404** for this draft (the LIST still reports 7 items, none of them the draft). Root cause, verified against the upstream source: Plane's `IssueManager` extends `SoftDeletionManager` to exclude **drafts**, and BOTH the external work-item LIST and DETAIL endpoints query through that manager (`apps/api/plane/api/views/issue.py` → `IssueListCreateAPIEndpoint.get_queryset` / `IssueDetailAPIEndpoint.get_queryset` use `Issue.issue_objects`). The Plane MCP server proxies the same REST route, so `plane_retrieve_work_item` and `plane_retrieve_work_item_by_identifier` return the same 404 (both probed). ⇒ **a byte-exact REST-lane CONTENT read-back of a DRAFT is structurally impossible against this Plane edition.** The activities sub-resource uses the unfiltered `Issue.objects` manager and therefore IS the REST lane that can address a draft.

**Channel B — the Plane server's own serialization of the stored row + PM's captured create arguments:**

| Assertion | Result |
|---|---|
| `is_draft` | **true** |
| title byte-identical to the dispatch's verbatim TITLE | **true** |
| `description_stripped` byte-identical to the dispatch's verbatim BODY | **true** (sha256 equal) |
| the **stored** `description_html` → text round-trip byte-identical to the verbatim BODY | **true** (1 601 chars, sha256 `76de12f8…` on both sides) |
| all four numbered sections present in the stored body | **true** |

### Step 6 — NEGATIVES (the enforcement proof)

All three resolver-level refusals were produced by the daemon's **own** surfaces — the boot `WARNING` (the only typed-reason interpolator, naming the agent) **and** the REST lane's `POST /api/mcp-servers/test-connection` (the plane-shaped probe). The worktree `.env` slug was mutated per leg under the Step-0 fenced idiom and the daemon was **re-booted** for each leg.

| Leg | Setup | Typed reason (verbatim) | Verdict |
|---|---|---|---|
| **N-1** | slug = `nea` | `workspace 'nea' is production-only; this environment resolves to dev` | **REFUSED** ✔ |
| **N-2** | slug **UNSET** | `PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'` (**exactly** the plan's string) | **REFUSED** ✔ |
| **N-3** | slug = `other` | `workspace 'other' is not permitted in a non-production environment; expected 'e2e'` | **REFUSED** ✔ |

Both surfaces carry the same reason; the boot line additionally names the affected agent:

```
daemon.manager - WARNING - Builtin 'plane' deactivated — workspace refused (POLICY): workspace 'nea' is production-only; this environment resolves to dev; agents with mcp_full_access=['plane']: project-manager
daemon.manager - WARNING - Agent capability gap: project-manager declare(s) mcp_full_access ['plane'] but the builtin was POLICY-REFUSED this boot (the row is INACTIVE; see the workspace-refused WARNING above)
```

**N-4 — the four-part P15a assertion (on the `nea` re-boot):**

| Part | Assertion | Result |
|---|---|---|
| (i) | **NO NEW** plane row created | plane rows before = **1**, after = **1** (the **same** id `db742a0d-…`) ✔ |
| (ii) | the pre-existing row's `config_schema_version` **UNCHANGED** | `"3"` → `"3"` (the E4 heal is **deferred** on a refused boot — P2-H) and the persisted `x-workspace-slug` header stayed `"e2e"` (never re-materialised with `nea`) ✔ |
| (iii) | PM's **resolved** toolset free of `plane_*` | `resolved_toolset_size = 46`, `plane_tool_count = 0`, `resolved_plane_prefixed_tools = []` — identical to the plane-**absent** baseline toolset ✔ |
| (iv) | the **WARNING** logged (agent + server + typed reason) | captured verbatim (see above) ✔ |
| + | `mcp_tool_names` stored-fallback free of stale `plane_*` | `preloaded_mcp_tool_names = ['mcp_context7_query-docs', 'mcp_context7_resolve-library-id']` — **no** `plane_*` entry ✔ |

**N-5 — mechanism-level P15b (no `nea` write was possible):**

* The resolver **never** permitted `nea` in any non-live resolution — proven empirically by N-1 (`slug=(account-default)` + refused, reason naming `nea` as production-only).
* The explicit-constructor bypass is **closed** (P2-D, `daemon/clients/plane_http_client.py`): `workspace_slug` is accepted for **backward compatibility only** and is **never consulted** ("the RESOLVED slug is the only authority"); a **refused** resolution makes the client **inert** (every method raises with the typed reason); an explicit `base_url` whose path carries a **different** workspace slug also makes the client **inert**.
* The MCP header is emitted from the **resolver's** effective slug (`daemon/mcp/builtin_servers/plane.py::get_base_config`, E3) ⇒ the MCP header can never carry `nea` on a non-live environment.
* **No observational `nea` read was performed** — the G2 fold DROPPED that optional clause (it contradicted the lane's own no-nea-contact fence). The guarantee is **structural**, not observational.

### Step 6-R — RESTORE (mandatory; ran even if a negative leg had failed)

| # | Assertion | Result |
|---|---|---|
| 1 | worktree `.env` restored to the post-arming state — slug **`e2e`**, the other four seeded keys unchanged | ✔ (key-NAME census: all five present; slug value asserted `e2e`) |
| 2 | worktree `.env` mode still `0600` | ✔ |
| 3 | main-checkout `.env` unchanged (mode `0600`, content untouched) | ✔ (sha256 identical to the pre-run value) |
| 4 | **re-boot** on the restored env ⇒ the plane row **IS** present with slug `e2e` | ✔ — `plane` row `is_active=true`, `config_schema_version=3`, `x-workspace-slug=e2e`; the boot line `Plane workspace resolution: env=dev source=explicit-marker slug=e2e`; `livez` 200 + `readyz` READY (all components true, `reasons: []`) |
| 5 | the run note records the pre-run and post-run env states key-name-wise (+ the slug value) | ✔ (this table + §3 Step 0) |

### Step 6.5 — the A′ BRANCH (EXECUTED — the FC9 verdict is BREACH)

Per phase5-plan §5.6 (the pre-committed default on breach, not a judgement call), and **after** the Steps 4–5 evidence was captured:

| # | Action | Landed |
|---|---|---|
| **A′.1** | `agents/project-manager/meta.json` — the `"plane"` **category** entry in `tools.allow` **replaced** (not supplemented) by the explicit **28** keep-set literal names (daemon-exposed `plane_*` form) | ✔ (61 allow entries; the category entry is gone) |
| **A′.2** | charter prose — `agents/project-manager/{tools_note,rule,workflow,soul}.md` name the keep-set verbs; the `plane_sync_project` ownership statement **stays** | ✔ all four files now name **28/28** keep-set verbs; `plane_sync_project` still named in `tools_note.md` + `workflow.md` |
| **A′.3** | `agents/.prompt-artifacts.sha256` regenerated **161/161** (`scripts/protection/generate_prompt_artifact_baseline.py`; `--check` ⇒ `PROMPT-BASELINE: OK`) | ✔ (exactly the 4 charter files changed) |
| **A′.4** | `SEALED_SHA_BASELINES` re-pin in `tests/test_chart_image_delivery_e2e.py` for `agents/project-manager/workflow.md` → `357263bd9ce19f2cd264717e9e8054293f89335c8c0ca5ea1ed264bbb951b0d2` (recomputed from the file) | ✔ |
| **A′.5** | **ONE atomic commit** (A′.1 + A′.2 + A′.3 + A′.4 + A′.6) — a split commit makes the seal tripwire red at the intermediate commit | ✔ (see the commit line below) |
| **A′.6** | **FC8 obligations riding the same commit** — (a) the **minimum-toolset assertion** (resolved ⊇ keep-set, ≥ keep-set size) and (b) the **gate-(i) drift detector** (the keep-set's sorted-name-set sha256 pinned + every keep-set verb still a member of the catalog) | ✔ in `tests/unit/tools/test_plane_agent_surface_census.py` (new `TestFC8NarrowedGrant`, 4 nodes) |
| **A′.7** | re-measure `present_tokens` with the narrowed grant, recorded **alongside** the pre-narrowing value | ✔ `present_tokens = 29 313` (28 plane tools, resolved toolset 74) — §1 |

**The narrowing also required re-scoping three census assertions** that the narrowing itself broke (they asserted the OLD full-surface facts): E1's count (`== 177` → the keep-set, a strict subset of the catalog), E1's vacuity tripwire (restated: under a literal grant the MCP layer — not the filter — is the runtime gate), and E2's "PM keeps the full write surface" (→ PM keeps the **keep-set's** write members; the pruned families are gone by design). The FC7 census pack: **15/15 green** after the re-scoping.

**A′.7 measurement detail:** `resolved_toolset_size 74` (46 non-plane + 28 plane) · `system_prompt 9 380` · `tool schemas 19 933` · `present_tokens 29 313` ⇒ the plane surface shrank **51.7 %** (60 635 → 29 313).

**R7 interaction (disclosed):** A′ *incidentally* removes the `plane_sync_project` leak (a literal allow-list cannot contain it) — the Phase-3 ownership guard remains the primary fix and is **not** reverted on the assumption that A′ covers it.

---

## 4. Fence table

| Item | Value |
|---|---|
| **Port used / owned by this run** | **8972** — owned by the Gate-B scratch daemon (source-run from the worktree). Verified free before every boot; freed at teardown |
| **Ports NOT touched** | **9797** (live, pid 643626) · **7979** (live demo, pid 525852) · **8079** (the main checkout's dev port — free at run time, not used) · **8971** (Gate A's port — free at run time, not used) |
| **Live `state.json` sha256** | **PRE == MID == POST** = `693998100c2ba18cb5848c165f4592fcab2fea2e5664ff29aa3a2ac0d7cc5c4a` |
| **Live daemon** | pid **643626** unchanged, :9797, v0.28.4, uptime continuous (68 910 s → 72 345 s), `NRestarts` unchanged |
| **Live demo** | pid **525852** unchanged, :7979, v0.16.3, uptime continuous (117 012 s → 120 447 s) |
| **`current` symlink** | `releases/v0.28.4` — untouched |
| **Main checkout `.env`** | sha256 `316124135557b241192659789256a1dc1cc0e3f6c8c39103445a179819ed4f0f` — **byte-unchanged**, mode `0600` |
| **Live install `.env`** | never written (read-only at most) |
| **Scratch PG** | throwaway role + DB on `127.0.0.1:5432` — **DROPPED** at teardown (0 rows in `pg_database` / `pg_roles`) |
| **Scratch root + fake HOME** | removed |
| **Orphans** | zero `python -m daemon`, zero MCP stdio children attributable to this run (the live `context7` trees predate the run) |

---

## 5. Deviations from `phase5-plan` (reported, not absorbed)

1. **The Phase-5 daemon runs from the WORKTREE on a non-conflicting port**, not the main checkout's dev daemon on `:8079` — per the **dispatch ruling**, which supersedes phase5-plan §4's "the Phase-5 daemon is the MAIN CHECKOUT's" line. The worktree's `.env` remains the config source; the port is set **explicitly** with a preflight assertion; the main checkout's `.env` is read-only (seeding source only).
2. **Step 5's read-back channel is split**, because Plane's external REST work-item endpoints structurally exclude drafts (root cause + upstream anchors in §3 Step 5). The REST lane proves existence + workspace + project + the creation event; the content + `is_draft` assertions come from the Plane server's own row serialization (byte-identical round-trip) and PM's captured create arguments. **No second Plane write was performed** to work around it (the plan authorises exactly ONE external write).
3. **The pre-committed ceiling's condition (b) is unachievable by construction** for any plane grant against a 20 468-token baseline (§1) — recorded and routed back to the architect; the rule was **not** adjusted, waived, or re-derived at execution time.
4. **Three census assertions were re-scoped** by the A′ narrowing (§3 Step 6.5) — a consequence of the branch, shipped in the same atomic commit.
5. **Two self-caught boot-script defects, fixed before any successful boot** (neither changed a number): (a) the port preflight initially refused because it *tested* the forbidden ports for occupancy instead of merely excluding them — corrected to "the chosen port must not be one of the forbidden three AND must be free"; (b) the first `unset` slug leg wrote `PLANE_MCP_WORKSPACE_SLUG=unset` instead of removing the key (case-sensitive mode match) — the daemon's own fence assertion caught it (the slug was present in `unset` mode ⇒ hard exit 95), the script was fixed, and the leg was re-run. Both are recorded because they are fence near-misses, not because they changed a result.
6. **A read-only upstream probe** (`makeplane/plane` via the zread MCP) was used to establish the root cause of the Step-5 404 — the same class of read-only upstream inspection the plan itself relies on for the Plane catalogue. No write, no fork, no vendoring.
7. **Two empty scratch directories survived until the final teardown.** The FIRST (aborted) `setup_env.sh` run created the scratch root + fake HOME *before* its port preflight failed ⇒ it left an unused pair behind; the second run's pair was the one actually used and was removed at teardown. Both pairs were removed at the final sweep (0 scratch paths remain). No daemon ever booted against the abandoned pair.
8. **A whole-tree `tests/unit` run was started and STOPPED at ~32 minutes** (it had not reached 25 % on this host — the suite is far slower here than the scoped legs). It is **not** the net-new proof: the net-new proof is the **scoped, digest-level base-vs-HEAD FAILED-id-set comparison** (identical 26-id sets on the 10-file blast-radius leg; identical 1-id set on the plane surface) against a base-probe worktree at `964778d48` — a strictly stronger form than a whole-tree count.

---

## 6. Evidence artifacts (all under `task3-evidence/`, bearer-redacted)

| Artifact | Content |
|---|---|
| `resolution.json` | the resolution proof (boot line verbatim + the three negative-leg resolution lines) |
| `mcp_servers_row.json` | the plane row (name, id, `config_schema_version`, url, `headers['x-workspace-slug']`) — **bearer redacted** |
| `pm_toolset_census.json` | the resolved toolset + the SC1 threshold + the charter-verb presence map + the post-narrowing census |
| `workitem_readback.json` | the two-channel read-back (REST activities + the server's row serialization) + the verbatim-comparison shas |
| `refusal_log.txt` | the N-1..N-4 WARNING lines + the REST-lane refusals + the N-5 mechanism note — **env var NAMES only** |
| `fc9_baselines.json` | **EXTENDED** (the Gate-A baseline entries are NOT overwritten) with the present leg, both ceiling booleans, the verdict, and the post-narrowing leg |
| `run-note.md` | this record |

Plus `.agents/tester/RESULTS/2026-11-02-plane-pm-task3-verification.md` (the verification report).

---

## 7. X5 exit-criteria coverage

| # | Criterion | Status |
|---|---|---|
| **X5.1** | the draft exists in `e2e`, `is_draft=true`, content matches the preserved draft verbatim; the identifier is recorded | **MET** — id `f445a1cd-f55b-48cf-945e-8043e2a0055c`, identifier `E2E-8`, `is_draft=true`, body byte-identical (sha256 equal) |
| **X5.2** | PM's tool call was a create-work-item call with the draft flag, recorded verbatim | **MET** — `plane_create_work_item` with `is_draft=true` (+ the preceding `plane_list_projects`), arguments captured verbatim, no secret |
| **X5.3** | N-1, N-2, N-3 all refuse with the typed reasons (P14) | **MET** — 3/3, both surfaces, reasons verbatim |
| **X5.4** | N-4 in the P15a form | **MET** — see the four-part table (+ the stored-fallback part) |
| **X5.5** | N-5: no `nea` pollution (P17) | **MET** — mechanism-level, no observational read (the dropped clause) |
| **X5.6** | the FC3 baseline is recorded (P18) | **MET** — 177 tools + the charter-verb presence map + `plane_sync_project` absence |
| **X5.7** | all **seven** artifacts archived, bearer redacted | **MET** — 7/7, leak-scanned |
| **X5.8** | the run note states the live/demo credential precondition | **MET** — §8 |
| **X5.9** | N-4 in the restated four-part form + the stored-fallback part | **MET** |
| **X5.10** | N-5 holds as written (the resolver never permitted `nea`; P2-D closed) | **MET** |
| **X5.11** | the three FC9 baselines recorded with the pre-Plane comparison base; the ceiling evaluated with both booleans and the verdict | **MET** — per-turn prompt tokens ✔; tool-call error rate over a stated N: **N = 2 real PM tool calls, 0 errored (0 %)** ✔; mis-selected-verb count: **0** ✔; the pre-Plane comparison base 20 468 ✔; the ceiling evaluated with both booleans and the verdict **BREACH** ✔ |
| **X5.12** | the A′ branch covered in both directions | **MET (b)** — BREACH ⇒ A′ executed: the narrowed grant + the charter edit + the 161/161 re-stamp + the SEALED re-pin in **ONE atomic commit**, with the FC8 minimum-toolset assertion + the gate-(i) drift detector, and the post-narrowing `present_tokens` recorded alongside the pre-narrowing value |
| **X5.13** | the arming precondition | **MET** — the key-NAME census recorded (names + presence only), all five present, worktree `.env` `0600`, the main-checkout `.env` unchanged, and the one Task-3 environment is named (the fenced worktree) |
| **X5.14** | the SC1 numeric assertion | **MET** — `plane_tool_count = 177 ≥ 100`, `sc1_satisfied: true`, `plane_create_work_item` present |
| **X5.15** | Step 6-R completed (assertions 4–5) | **MET** — the re-boot proves the plane row is present with slug `e2e`; the run note records the pre-run and post-run env states key-name-wise (+ the slug value) |

---

## 8. Live/demo credential precondition (X5.8)

**This phase proves the DEV lane only.** Per phase5-plan §1 (R14): the live and demo `PLANE_MCP_API_KEY` are the **same expired token** (HTTP 401 `invalid_token`) and only the dev context points at `e2e` **and** carries a REST key — so Task 3 runs on dev, and only on dev. **Live efficacy waits on the user's credential re-mint** (ADJ-4 / O7); it is **not** proven here, and this gate does not claim it.

---

## 9. Routed items

1. **FC9 condition (b) is unachievable by construction** against the 20 468-token baseline — the relative cap (5 117 tokens of headroom) is smaller than any plane grant's schema cost. → **architect** (the rule may not be adjusted at execution time; this is a measurement, not a waiver).
2. **Plane's external REST API cannot read a DRAFT work item** (`Issue.issue_objects` excludes drafts) ⇒ every draft-verification flow is forced onto a sub-resource (or the MCP channel). → **architect / operator**: if draft read-back must stay REST-native, it needs a Plane-side draft endpoint or a superuser-scoped query.
3. **The `GET /api/mcp-servers` response carries the Plane bearer in plaintext** (`config.headers.Authorization` is not redacted by `redact_secrets`) — this is the already-open critical note (`cn` — Plane MCP bearer plaintext in `mcp_servers.config.headers`), re-confirmed empirically here. Every artifact in this run is redacted; the surface itself is not. → **owner: MCP-gateway/security hardening commission**.
4. **`webfetch` degrades on this host** (`init_failed` — the builtin needs `npx`, absent from the fence's PATH). It is irrelevant to the plane lane and does not affect any readiness component. → informational.
5. The **warmup pool** logs `MCP server 'plane' row config invalid for warmup pool: 2 validation errors for McpStdioConfig` — the pool's stdio-shaped validation rejects the plane row's `streamable-http` transport. The plane lane is unaffected (schema discovery primed 177 tools; `mcp.plane.state = ok`), but the pool cannot hold a pooled plane connection. → **owner: MCP warmup pool lane** (pre-existing, not introduced here).

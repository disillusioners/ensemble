# Plane PM issue-tools — GATE B: Task-3 END-TO-END VERIFICATION (dev / `e2e`)

**Lane:** `plane-pm-issue-tools` · **Base:** `941cb6321` · **Gate-B tree:** `964778d484650f909fdb84e4e3a9484d97a1c821`
**Executed:** 2026-11-02 17:47–19:0x Z · **Executor:** worker (Gate-B dispatch)
**Binding authority:** `.agents/shared/planning/plane-pm-issue-tools/phase5-plan.md` (§4 fence · §5 procedure · §5.6 Step-6.5 A′ · §7 triage · §8 pins · §9 exit criteria) + `gate-i-tools-list-recapture.md` §7 (the FC9 formula)
**Run record:** `.agents/shared/planning/plane-pm-issue-tools/task3-evidence/run-note.md`

---

## 1. Verdict

**PASS — with one disclosed platform constraint (Step 5's read-back channel) and one routed measurement finding (FC9 condition (b)).**

| Step | Verdict |
|---|---|
| 0 — ARMING | **PASS** |
| 1–2 — BOOT + RESOLUTION | **PASS** |
| 3 — REGISTRATION + CENSUS | **PASS** (SC1 satisfied: 177 ≥ 100) |
| 3 — **FC9 VERDICT** | **BREACH** (a = ✗, b = ✗) ⇒ the A′ branch is the mandated default |
| 4 — THE TASK (real PM, real LLM, real Plane MCP) | **PASS** |
| 5 — READ-BACK | **PASS** (REST existence leg + server-row content leg; the REST *content* read-back of a draft is structurally impossible on this Plane edition) |
| 6 — N-1 … N-5 | **PASS (5/5)** |
| 6-R — RESTORE | **PASS** |
| 6.5 — A′ BRANCH | **EXECUTED** (one atomic commit `262dff12c`) |
| TEARDOWN + LIVE FENCE | **PASS** |

---

## 2. The created Plane draft work item

| | |
|---|---|
| **Work item id** | **`f445a1cd-f55b-48cf-945e-8043e2a0055c`** |
| **Identifier** | **`E2E-8`** (project `E2E`) |
| **Workspace** | `e2e` (`80859836-bd99-4b6e-bebe-cd29a0cc9dda`) |
| **Project** | `0e9e0143-c9ad-4f58-9114-0d8b549ddea6` (`e2e`) |
| **`is_draft`** | **true** |
| **Created** | `2026-11-02T18:03:45Z` |
| **URL** | the Plane create API returns **no URL field**; the handle is the identifier + `GET {PLANE_BASE_URL}/api/v1/workspaces/e2e/projects/0e9e0143…/work-items/f445a1cd…/activities/` |
| **Title** | `Draft: Guidelines system — fixed dev-team-managed reference docs with intelligent agent loading` — **byte-identical** to the dispatch's verbatim text |
| **Body** | the four-section body — **byte-identical** (sha256 `76de12f877c362cc4af82cfd105041aef5761bc540d0af9851ac5fd99adf60d8` on the argument **and** on the server's stored `description_html` → text round-trip) |

**PM's actual tool calls (verbatim, minus secrets):** `plane_list_projects` → then `plane_create_work_item{name, description_stripped, is_draft=true, project_id}`. **One** create call; **no** create-then-delete; **no** write to `nea`.

---

## 3. FC9

```
baseline_tokens = 20 468   (Gate A's re-measurement ON THIS TREE — the comparison base)
present_tokens  = 60 635   (plane category PRESENT, sort_keys+compact form)
growth_tokens   = 40 167
growth_pct      = 1.962429  (196.24 %)
(a) growth_tokens <= max(10 000, 0.25 × 20 468 = 5 117) = 10 000   → FALSE
(b) growth_pct    <= 0.25                                         → FALSE
VERDICT = BREACH   ⇒ A′ EXECUTES (the pre-committed default, not a judgement call)
```

**Post-narrowing (A′.7), same form:** `present_tokens = 29 313` (28 plane tools, resolved toolset 74) — the plane surface shrinks **51.7 %**. Recorded alongside the pre-narrowing value. *Recorded fact, routed back to the architect:* with the pre-derived 28-verb keep-set the plane schema-token growth is 7 881 while condition (b)'s headroom at 25 % of the 20 468 baseline is 5 117 ⇒ **condition (b) is unachievable by construction for any plane grant** (the same structural asymmetry the plan itself recorded for condition (a)'s absolute floor). The rule was **not** adjusted, waived, or re-derived at execution time.

---

## 4. Negatives — the typed reasons (verbatim)

| Leg | Typed reason |
|---|---|
| N-1 (slug `nea`) | `workspace 'nea' is production-only; this environment resolves to dev` |
| N-2 (slug UNSET) | `PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'` |
| N-3 (slug `other`) | `workspace 'other' is not permitted in a non-production environment; expected 'e2e'` |

Each was produced by **the daemon's own surfaces** — the boot `WARNING` (naming the agent) *and* the REST lane's `POST /api/mcp-servers/test-connection`.

**N-4 (P15a, four parts + the stored-fallback part):** (i) **no NEW** plane row (1 before → 1 after, the **same** id); (ii) the pre-existing row's `config_schema_version` **unchanged** (`"3"`) and its persisted slug header stayed `"e2e"` (the E4 heal is deferred on a refused boot — P2-H); (iii) PM's resolved toolset has **zero** `plane_*` tools (46 = the plane-**absent** baseline); (iv) the WARNING **is** logged; plus `mcp_tool_names` carries **no** stale `plane_*` entry.

**N-5 (P15b):** the resolver never permitted `nea` in any non-live resolution, and the explicit-constructor bypass is closed (P2-D — `workspace_slug` is never consulted; a refused resolution makes the client inert; a mismatched `base_url` slug makes it inert). No observational `nea` read was performed (the lane's own no-nea-contact fence; the optional clause was dropped by the G2 fold).

---

## 5. The A′ branch

**Commit `262dff12c`** — ONE atomic commit carrying A′.1 (the 28-literal grant replacing the `plane` category entry in `agents/project-manager/meta.json`), A′.2 (the charter vocabulary in all four PM prompt files — 28/28 verbs named; the `plane_sync_project` ownership statement stays), A′.3 (the **161/161** prompt-artifact re-stamp — `PROMPT-BASELINE: OK`), A′.4 (the `SEALED_SHA_BASELINES` re-pin for `agents/project-manager/workflow.md` → `357263bd…`), and A′.6 (the FC8 minimum-toolset assertion + the gate-(i) drift detector). A′.7's measurement is recorded in `fc9_baselines.json`.

---

## 6. Verification evidence

| Leg | Result |
|---|---|
| FC7 census pack (`tests/unit/tools/test_plane_agent_surface_census.py`) | **15/15 green** (4 new FC8 nodes) |
| The plane test surface (census + prefix-sweep ownership + prompt-verb check + catalog integrity + the seal file) | **72 passed / 1 failed** — the single failure is the **pre-existing** seal tripwire (4 rows red at BASE). A base-probe worktree at `964778d48` reproduces the **identical** failure ⇒ **NET-NEW = 0** |
| Blast-radius leg (10 PM/tool-filter/registry files) | **1859 passed**; the FAILED-id sets at base and on the A′ tree are **byte-identical** (26 ids, all pre-existing: the `MagicMock` `allowed_models` fixture failure in `tests/test_spawn_team_members.py` and 5 charter/developer prompt-integrity reds) ⇒ **NET-NEW = 0** |
| Prompt-artifact baseline at the commit | `PROMPT-BASELINE: OK — 161 versioned artifact(s)` |
| The seal tripwire's `agents/project-manager/workflow.md` row | **GREEN** at the commit (the re-pin landed) |
| Determinism | two independent harness runs → byte-identical numbers and the same system-prompt sha256 |

---

## 7. Fence table

| Item | Value |
|---|---|
| Port used / owned | **8972** (this run's scratch daemon, source-run from the worktree) — freed at teardown |
| Ports NOT touched | 9797 (live, pid 643626) · 7979 (live demo, pid 525852) · 8079 · 8971 |
| Live `state.json` sha256 | **PRE == MID == POST** = `693998100c2ba18cb5848c165f4592fcab2fea2e5664ff29aa3a2ac0d7cc5c4a` |
| Live / demo | pid + uptime continuous (live 68 910 s → 72 763 s v0.28.4; demo 117 012 s → 120 865 s v0.16.3) |
| `current` symlink | `releases/v0.28.4` — untouched |
| Main checkout `.env` | sha256 `316124135557b241192659789256a1dc1cc0e3f6c8c39103445a179819ed4f0f` — **byte-unchanged**, mode `0600` |
| Worktree `.env` | restored to the post-arming state (`PLANE_MCP_WORKSPACE_SLUG=e2e`, all five `PLANE_*` names present), mode `0600` |
| Scratch PG | throwaway role + DB on `127.0.0.1:5432` — **DROPPED** (0 rows) |
| Scratch root + fake HOME | removed |
| Orphans | **zero** `python -m daemon`; zero MCP stdio children attributable to this run (the two live `context7` trees belong to the live :9797 daemon — pid 1675935's parent is **643626** — and the demo :7979 daemon) |
| Secret hygiene | the plane bearer appears in **no** artifact, log, or report (leak-scanned); the *live* `GET /api/mcp-servers` response is **not** redacted by the daemon (the already-open critical note, re-confirmed) |

---

## 8. Deviations from `phase5-plan` (reported, not absorbed)

1. **The Phase-5 daemon ran from the WORKTREE on :8972**, not the main checkout's dev daemon on :8079 — per the **dispatch ruling**, which supersedes §4's port line.
2. **Step 5's read-back is split across two channels** because Plane's external REST work-item endpoints exclude drafts (`Issue.issue_objects` excludes drafts; both the LIST and DETAIL endpoints query through it, and the MCP server proxies the same route — all probed and recorded). The REST lane proves existence + workspace + project + the creation event; the content + `is_draft` come from the Plane server's own row serialization and PM's captured create arguments. **No second Plane write** was used to work around it (the plan authorises exactly one external write).
3. **FC9 condition (b) is unachievable by construction** against the 20 468-token baseline — recorded and routed back to the architect; the rule was not adjusted.
4. **Three FC7 census assertions were re-scoped** by the narrowing itself (they asserted the old full-surface facts) — shipped in the same atomic commit.
5. **Two self-caught boot-script defects**, fixed before any successful boot (neither changed a number): an inverted port preflight, and a case-sensitive `unset` slug match — the daemon's own fence assertion caught the second one. Recorded as fence near-misses.
6. **A read-only upstream probe** (`makeplane/plane`) established the Step-5 404 root cause — read-only inspection, no write/fork/vendoring.

---

## 9. Routed items

1. **FC9 condition (b) is unachievable by construction** for any plane grant against this baseline → **architect** (the rule may not be adjusted at execution time).
2. **Plane's REST API cannot read a DRAFT work item** → **architect / operator**: a draft-native read-back needs a Plane-side draft endpoint or a superuser-scoped query.
3. **`GET /api/mcp-servers` returns the Plane bearer in plaintext** (`redact_secrets` misses `config.headers`) — the already-open critical note, re-confirmed empirically → **MCP-gateway/security hardening commission**.
4. **The MCP warmup pool rejects the plane row's `streamable-http` transport** (`2 validation errors for McpStdioConfig`) — the plane lane is unaffected (177 tools primed; `mcp.plane.state = ok`) but the pool cannot hold a pooled plane connection → **MCP warmup pool lane** (pre-existing).
5. **`webfetch` degrades on this host** (`init_failed` — the builtin needs `npx`, absent from the fence's PATH) — irrelevant to the plane lane; informational.

---

## 10. No promote / no merge / no push

The evidence is committed **branch-side only** on `feature/plane-pm-issue-tools`. **No merge, no push, no stage, no promote.** `.agents/tester/rules/` was not touched. The A′ commit and the evidence commit are the only two commits this gate produced.

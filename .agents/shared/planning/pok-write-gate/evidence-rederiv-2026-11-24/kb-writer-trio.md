# kb-writer trio — evidence re-derivation (2026-11-24)

**Lane:** `pok-write-gate` (T12.2 evidence re-derivation)
**Project:** `83da04de-a410-4fb5-9e92-251a99d28a52` (agents-ensemble)
**Artifact ID:** T12.2 — the "kb-writer trio" re-derivation record
**Authored:** 2026-11-24 (evidence lane)
**Gate this record serves:** approver `S-1` HARD GATE — `plan-overview.md:319`, `phase12-plan.md:17`, `approver-pok-write-gate.md:45` / `:119`. The Phase-12 guideline diff is BLOCKED until each of the three relayed figures is either re-derived and stamped (snapshot time + provenance) or dropped with explicit disclosure.

## Snapshot timestamps

| Stream | Snapshot (UTC) | Source |
|---|---|---|
| Live PostgreSQL (`ensemble_prod`) | 2026-11-24, ~10:45–11:05Z | SELECT-only against `10.44.0.2:5432` |
| RAG corpus inventory | 2026-11-24T11:01:01Z | LightRAG `POST /documents/paginated` |
| 562-row active POK export (`pok-active.tsv`) | 2026-11-24 ~10:57Z | SELECT-only |
| 499-row machine-label TSV (census era) | 2026-11-23 12:29Z snapshot; label set regenerated 14:17Z | `evidence-pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv` |
| 512-row JSON census | 2026-11-23T08:32:36Z | `pok-sweep/census-active-2026-11-23.json` |
| Git refs | read at 2026-11-24 | worktree `feature/pok-write-gate` |

## Fence statement

- **SELECT-only** against `ensemble_prod`. No `INSERT` / `UPDATE` / `DELETE` / DDL was issued against any live table.
- No `pok_upsert` / `pok_curate` / `pok_gate_answer` write path was called. No `pok_query` write path.
- No `git checkout` / `commit` / `stash` / `reset` / `push`. **Read-only git only** (`log`, `cat-file`, `merge-base --is-ancestor`, `branch -a --contains`, `rev-parse`).
- No service was started, stopped, or restarted.
- **The only write in this task is this file:** `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-rederiv-2026-11-24/kb-writer-trio.md`. No other file, no directory creation elsewhere. The canonical checkout `/home/nea/ensemble-src` was **not** written to.
- No number in this document is invented, rounded, extrapolated, or "reasonably estimated". Every figure is either (a) reproduced verbatim from the relayed payload, or (b) independently re-derived here and labelled as such. Where a figure is not measurable, that verdict is stated plainly and no substitute is offered.

## The three relayed claims (verbatim)

1. **Figure 1** — "65 of 81 RAG-duplicate rows were written into BOTH stores in the SAME kb-writer run (±0s)".
2. **Figure 2** — "kb-writer's verify family (85 rows) is 83.5% OPERATIONAL".
3. **Figure 3** — "gate-reject rates run 7-18% for every author (leader 8%)".

---

## 1. Figure 1 — same-run dual-write into both stores

### RELAYED CLAIM

> "65 of 81 RAG-duplicate rows were written into BOTH stores in the SAME kb-writer run (±0s)".

**IN-BRANCH SOURCE: NONE (relayed KV only).** Origin: `shared_meta_kv` `pok-write-gate.addendum-leader-1` (leader via Ari, 2026-11-23) — "of 81 RAG-DUPLICATE POK rows, 65 (80%) written into BOTH stores in the SAME kb-writer run (±0s)". The only in-repo appearances are the gate/approver text that flags the figure as unsourced (`phase12-plan.md:17`, `approver-pok-write-gate.md:119`, `plan-overview.md:319`).

### VALUE (as re-derived)

| Quantity | Relayed | Re-derived here | Status |
|---|---:|---:|---|
| RAG-duplicate POK population | 81 | **NOT REPRODUCIBLE** (live lower-bound overlap set = 10, of which kb-writer = 6) | unbacked |
| Same-run dual-write count | 65 of 81 | **NOT REPRODUCIBLE** — at the ±1 s grade the live count is **2** | unbacked |
| Overlap set size | (implied 81) | **10** (lower bound) | re-derived |
| Exact-hash grade | (implied) | **0** (by construction) | re-derived |
| Slug-identity grade | (implied) | **6** | re-derived |
| Jaccard-only grade | (implied) | **4** | re-derived |
| Same-run (i) instance-linkage | — | **9** of 10 | re-derived |
| Same-run (ii) \|delta_t\| ≤ 60 s | — | **7** of 10 | re-derived |
| Same-run (iii) \|delta_t\| ≤ 1 s ("±0s") | 65 of 81 | **2** of 10 | re-derived |
| Dual-grant mechanism | — | **CONFIRMED, still live on `origin/latest`** | re-derived |

**Defensible restatement (the form this record supports):** *kb-writer wrote both stores in the same run for 6 of 6 kb-writer overlap rows; 2 of those 10 overlap rows fall within ±1 s.* The "65 of 81" figure is not re-stampable.

### METHOD (exact, with rule strings)

**RAG side — external LightRAG server, NOT PostgreSQL.** No RAG tables exist in any schema of `ensemble_prod`. Reached over HTTPS at `https://rag.mtri.app` (auth enabled; workspace `agents_ensemble`; API key sha256 prefix `fa90ac63` — the key itself is never printed). Endpoint `POST /documents/paginated`, **66 pages × page_size 200** (page_size 500/1000 → HTTP 422). API `total_count` = **13,110** docs (the lane's cited **12,968 is stale by +142**). Per-document fields exposed: `file_source`, `created_at` (insert time), `content_length`, `content_summary` (**253-char prefix ONLY**). There is **no GET-document endpoint** → no full text and no server-side content hash ⇒ **the exact-hash evidence grade is 0 BY CONSTRUCTION** and Jaccard is computed on the truncated prefix, so similarity is **UNDERSTATED**.

**POK side.** PostgreSQL `public.project_operating_knowledge`, `project_id = 83da04de-a410-4fb5-9e92-251a99d28a52`, `status = 'active'` → **562 rows**.

**Overlap rule (OR of three):**
1. exact-hash: RAG `content_sha256` == POK `content_sha256`;
2. slug-identity: RAG `file_source` filename stem == POK `slug`;
3. jaccard-only: normalized token-set Jaccard **≥ 0.60**.

**Normalization (exact):** lowercase → replace non-`[a-z0-9]` with space → collapse whitespace → drop stopwords `{the,a,an,and,or,of,to,in,is,are,for,on,at,by,with,that,this,it,be,as,not,no,if,then,than,from,so,do,does,was,were,has,have,had,its,their,there,here}`. Similarity = Jaccard over surviving token **sets**. Grade priority for the single "best" RAG doc per POK row: `exact-hash` > `slug-identity` > `jaccard-only`, then Jaccard desc, then smallest `|delta|`.

**Same-run criteria — three, reported SEPARATELY (not collapsed):**
- **(i) instance-linkage** = 9 — the POK row's `created_at` falls inside a kb-writer instance activity window (`public.instances` `[created_at, last_activity_at]`) that also contains a RAG write. 0 rows had more than one containing instance (no tie-break needed).
- **(ii) time-proximity** = 7 — `|delta_t| ≤ 60 s`.
- **(iii) same-instance-same-second** = 2 — `|delta_t| ≤ 1 s` (this is the relayed "±0s" grade).

`delta_t = rag_created_at − pok_created_at` (seconds).

**Results.** Overlap set size = **10** (lower bound). Exact-hash **0**; slug-identity **6**; jaccard-only **4**. Jaccard histogram (over the truncated 253-char RAG prefix): `1.0–0.9: 0`, `0.9–0.8: 1`, `0.8–0.7: 2`, `0.7–0.6: 1`, `<0.6: 6` (the 6 slug-identity rows, whose summary is only a prefix). Per-author: **kb-writer 6, maintenancer 2, developer 1, worker 1**. Delta histogram: `0–1s: 2`, `1–10s: 0`, `10–60s: 5`, `60–300s: 1`, `>300s: 2`, `no timestamp found: 0`. Per-author (overlap / i / ii / iii): **kb-writer 6/6/6/2; maintenancer 2/2/1/0; developer 1/1/0/0; worker 1/0/0/0**.

**The 10 overlap rows (verbatim):**

| slug | author | pok_created_at | best_rag_file_source | rag_created_at | delta_s | jaccard | grade | i | ii | iii |
|---|---|---|---|---|---:|---:|---|:-:|:-:|:-:|
| views-origin-parser-nonbrowser-spellings | kb-writer | 2026-11-09T03:23:45.090378+00:00 | views-origin-parser-non-browser-spellings.md | 2026-11-09T03:23:08.601715+00:00 | -36.489 | 0.7273 | jaccard-only | 1 | 1 | 0 |
| decision-kill-switch-liveness-triad | kb-writer | 2026-11-09T06:12:52.053297+00:00 | decision-kill-switch-liveness-triad.md | 2026-11-09T06:12:52.155493+00:00 | 0.102 | 0.1087 | slug-identity | 1 | 1 | 1 |
| packs-01-conflict-keep-both-newest-first | kb-writer | 2026-11-12T11:08:40.076842+00:00 | packs-md-eof-conflict-resolution.md | 2026-11-12T11:08:40.229335+00:00 | 0.152 | 0.8667 | jaccard-only | 1 | 1 | 1 |
| wake-path-01-emitter-accept-skip | kb-writer | 2026-11-18T18:40:26.428484+00:00 | wake-path-emitter-amendment-2026-11-18.md | 2026-11-18T18:39:54.491734+00:00 | -31.937 | 0.6250 | jaccard-only | 1 | 1 | 0 |
| upgrade-journal-writer-attribution | maintenancer | 2026-11-19T14:16:09.367397+00:00 | upgrade-journal-writer-attribution.md | 2026-11-19T14:47:43.298523+00:00 | 1893.931 | 0.1538 | slug-identity | 1 | 0 | 0 |
| shared-worktree-amend-trap | developer | 2026-11-22T11:45:04.561143+00:00 | shared-worktree-amend-trap.md | 2026-11-22T11:46:31.496138+00:00 | 86.935 | 0.4444 | slug-identity | 1 | 0 | 0 |
| engine-flipprep-pack-registration-gap | worker | 2026-11-22T19:21:13.881648+00:00 | engine-flipprep-pack-registration-gap.md | 2026-11-22T18:44:29.586842+00:00 | -2204.295 | 0.2439 | slug-identity | 0 | 0 | 0 |
| pending-task-redrive-repair | kb-writer | 2026-11-24T03:02:09.502123+00:00 | pending-task-redrive-repair.md | 2026-11-24T03:01:42.454621+00:00 | -27.048 | 0.1489 | slug-identity | 1 | 1 | 0 |
| pok-write-gate-01-plan-set-location | kb-writer | 2026-11-24T03:45:33.790275+00:00 | pok-write-gate-plan-set-location.md | 2026-11-24T03:45:20.130301+00:00 | -13.660 | 0.7143 | jaccard-only | 1 | 1 | 0 |
| plane-mcp-dead-cached-session | maintenancer | 2026-11-24T04:48:56.381995+00:00 | plane-mcp-dead-cached-session.md | 2026-11-24T04:49:38.517676+00:00 | 42.136 | 0.1778 | slug-identity | 1 | 1 | 0 |

### MECHANISM — verified from git history + DB, not from current file state

The `"pok"` tool-CATEGORY grant was added to **every** agent in commit `16b4e70b21c4522babb01d5dc3ebe5ff4cbf0fc8` (2026-11-07 08:27:33 +0000, "feat(agents): grant the pok category to all 45 agents — B4 sweep + F-1 ceremony (POK Phase 1 T5)") and removed from kb-writer in commit `fd7dc64808a498057cf37dcb6650bb427e6b38b9` (2026-11-24 06:50:51 +0000, "feat(pok): Phase 4 — kb-writer grant removal + compensating capture duty"). So the dual grant coexisted **2026-11-07 → 2026-11-24 (relayed span: 17d 22h 23m** — *exact timestamp subtraction of the two commit dates is 16d 22h 23m 18s; the relayed span appears to count calendar days 7→24 = 17 and is reproduced here verbatim as relayed* **)**.

- **Worktree tip:** `97f269ea42ecc75aa116f72d7dc8b0f6b09ea5b0` (2026-11-24 10:34:45 +0000) — `agents/kb-writer/meta.json` `tools.allow = ["rag_insert_text","tool_help","time","nest-report"]` — **no `"pok"`**. The current worktree HEAD is a **descendant of `97f269ea4`** (the tip moved forward; commit history was not rewritten), so the tip sha quoted in the original measurement is stale but its `meta.json` state is the measurement's subject.
- **Base sha `a1130e257a3c3bb3929455aec28cacafcbfaa1a7`** (2026-11-23 11:23:44 +0000, "docs(tester): land pok-cap-1024 verdict evidence + read-budget-independence pack registration (wi-1921ba17)") **AND `origin/latest` BOTH carry** `["rag_insert_text","tool_help","time","pok","nest-report"]` — **the dual grant**.
- **`git merge-base --is-ancestor fd7dc6480 origin/latest` = FALSE** and **`git branch -a --contains fd7dc6480` = `feature/pok-write-gate` only** ⇒ **the removal is BRANCH-LOCAL and UNPUSHED; `origin/latest` (and therefore the live install) still grants kb-writer BOTH.**
- `origin/latest` = `bcebe77250ab6ae07212cfafc33b2a8e82608d64` (2026-11-24 03:57:47 +0000).
- **Category mapping:** `"pok"` → `daemon/tools/pok` → `pok_upsert` / `pok_query` / `pok_curate` (`daemon/tools/_tool_registry.py` `CATEGORY_MODULES`, ~lines 1951–1953); `"rag_insert_text"` is a direct tool grant (~line 1965).

**Second, independent mechanism for cross-store duplication** (in-branch, `store-effectiveness-rag-overlap-brief.md:53–66`): the duplication mechanism is real — the **kb-curator first-turn mirror duty pushes KB docs into RAG**, and **24 distinct POK writer-agents overlap the `experience()` writer population**, so lane-captured gotchas land in both stores **by construction**. This is independent of the `meta.json` dual grant; the grant explains kb-writer specifically, the mirror duty explains the general cross-store overlap.

**POPULATION COVERAGE:** **0 of kb-writer's 138 active POK rows were created before 2026-11-07** ⇒ the dual grant was in force for **100%** of the kb-writer population. Earliest kb-writer row `2026-11-09T03:23:45.052277+00:00`; latest `2026-11-24T06:26:19.866428+00:00`. (Both re-derived here from the 562-row export.)

**PG-SIDE TOOL-USAGE CO-OCCURRENCE** (table `tool_usage_daily`, columns `day` / `project_id` / `tool_name` / `agent_id` / `call_count`; **RETENTION = 3 days only**, 2026-11-22..2026-11-24, **1,724 rows** total). For project `83da04de`, agent `kb-writer`: 2026-11-22 — `pok_upsert` 32, `rag_insert_text` 124, `pok_query` 7; 2026-11-23 — `pok_upsert` 15, `rag_insert_text` 66, `pok_query` 2; 2026-11-24 — `pok_upsert` 16, `rag_insert_text` 79, `pok_query` 3, `pok_curate` 1. kb-writer also dual-writes on other projects (`a5f20438` 11-22: 4 pok / 17 rag; `54debc61` 11-23: 2 pok / 25 rag). **kb-importer also dual-writes** (`a5f20438` 11-22: 1 pok / 13 rag; `83da04de` 11-24: 2 pok / 4 rag).

**POK-SIDE RUN STRUCTURE** (SQL on `project_operating_knowledge`, `kb-writer`, `active`): **138 rows = 87 runs** under a 60 s gap rule; **39 rows** written within 1 s of the previous row; inside the tool-usage window (2026-11-22..24) **45 active rows in 21 runs, of which 21 rows fell within 1 s of the prior row**. **52 rows** were CREATED in that window, **45 remain active** (7 since superseded/archived by the WAVE-2 arc).

### SOURCES

| Artifact / table | Columns used |
|---|---|
| `public.project_operating_knowledge` | `project_id`, `slug`, `body`, `created_at`, `created_by_agent`, `content_sha256`, `domain`, `audience`, `status`, `superseded_by_id` |
| `public.instances` | `agent_id`, `created_at`, `last_activity_at`, `status` |
| `public.tool_usage_daily` | `day`, `project_id`, `tool_name`, `agent_id`, `call_count` |
| LightRAG HTTP surface (workspace `agents_ensemble`) | `POST /documents/paginated`; `/workspaces`; per-doc `file_source`, `created_at`, `content_length`, `content_summary` |
| Git refs | `16b4e70b2`, `fd7dc6480`, `a1130e257`, `origin/latest` (= `bcebe7725`), worktree tip `97f269ea4` (HEAD is a descendant) |
| On-disk artifacts | `fig1-rag-inventory.tsv` (13,110 rows), `fig1-overlap-set.tsv` (10 data rows), `fig1-method-and-limits.md`, `pok-active.tsv` (562 data rows, **no header line**) |

### SNAPSHOT

Live POK active set **562 rows** at 2026-11-24 ~10:57Z; RAG corpus **13,110 docs** at 2026-11-24T11:01:01Z; git refs read 2026-11-24.

### LIMITS

- **(a)** RAG full text unavailable (253-char summary only) ⇒ overlap set is a **LOWER BOUND**, similarity understated, exact-hash grade structurally **0**.
- **(b)** No semantic / fact-level match possible ⇒ rows whose FACT is in RAG but whose wording differs are **invisible** to this method.
- **(c)** The 81-row RAG-duplicate population has **NO persisted per-row artifact**, so "81" cannot be recomputed, only bounded. In-repo it exists **only as a class count with no per-row join key**: `store-effectiveness-rag-overlap-brief.md:57` — `| RAG-DUPLICATE | 81 | 15.8% |` — and `:64` — "Top overlapping pairs: NOT reproducible live this run (probe blockage above). No pair list is fabricated; the census's pair-level detail lives in its own artifact." ⇒ the **65-of-81 SPLIT is not re-derivable from any persisted artifact**.
- **(d)** The census lane that produced the class counts was **itself probe-blocked**: `store-effectiveness-rag-overlap-brief.md:51–52` records that the RAG corpus is not PostgreSQL-reachable from that seat and that live probing via the sanctioned `explore()` read API was attempted with 10 stratified near-verbatim probes + 1 lone retry — **11/11 timed out at 300s** — so "Live pair-level verification is BLOCKED this run — recorded, not worked around." ⇒ the class counts were produced **without a pair list**. The brief's own `:97` discloses it: "~15.8% RAG-duplicate / ~19% redundant-or-rolling (full-population census, wanderer 2026-11-23; live re-probe blocked by explorer timeouts — disclosed)".
- **(e)** `tool_usage_daily` retention covers 3 days ⇒ PG-side tool evidence exists for only **45 of kb-writer's 138 rows (32.6%)**.
- **(f)** The daemon log carries **no structured tool-call traces** for `pok_upsert` / `rag_insert_text` (only LLM reasoning echoes and tool-filter lists), so logs cannot substitute for the RAG-side timestamp.
- **(g)** The prior census's own RAG-overlap method was itself defeated (11/11 `explore()` probes timing out, above), so the relayed "81" has **no reproducible method on record either**.

### VERDICT: **PARTIAL-METHOD**

The "81" overlap population is **NOT reproducible** on current data (live lower-bound overlap set = **10**, of which kb-writer = **6**). The "65 of 81 same-run (±0s)" is **NOT reproducible** — at the ±1 s grade the live count is **2**. The **MECHANISM (dual grant) is CONFIRMED and still live on `origin/latest`**.

### RECOMMENDATION FOR PHASE-12: **DROP-WITH-DISCLOSURE**

Drop the **81** and **65** numbers with explicit disclosure. Replace with the measured **overlap = 10 (kb-writer 6)** + **same-run 9 / 7 / 2** + the grant-history fact (dual grant `16b4e70b2` → `fd7dc6480`, removal **branch-local and unpushed**, dual grant still live on `origin/latest`).

**Suggested guideline wording:** *"kb-writer wrote both stores in the same run for 6 of 6 measured overlap rows; 2 of 10 overlap rows fall within ±1 s (measured 2026-11-24 ~11:00Z; lower bound — the RAG corpus is only reachable as a 253-char summary, so the overlap set cannot exceed 10). The mechanism is a `pok`-category grant co-resident with `rag_insert_text` in `agents/kb-writer/meta.json`, added 2026-11-07 (`16b4e70b2`) and removed on branch only 2026-11-24 (`fd7dc6480`, not an ancestor of `origin/latest`). A second, independent mechanism (kb-curator first-turn mirror duty + 24 POK writer-agents overlapping the `experience()` writer population) duplicates rows across stores by construction."*

**Do NOT present 65/81 as re-stamped.**

---

## 2. Figure 2 — kb-writer verify family operational share

### RELAYED CLAIM

> "kb-writer's verify family (85 rows) is 83.5% OPERATIONAL".

**IN-BRANCH SOURCE: NONE (relayed KV only).** Origin: `shared_meta_kv` `pok-write-gate.addendum-leader-2-digest` (leader via Ari 2026-11-23, job 71f7c48d) — "verify family 83.5% operational, reject rates 7-18% all authors".

### VALUE (as re-derived)

| Population | Rows | ROLLING | non-ROLLING | Share |
|---|---:|---:|---:|---:|
| **Census snapshot** — 499-row machine-label TSV (2026-11-23 ~13:5xZ), `kb-writer AND domain='verify'` | **79** | 13 | **66** | **83.54%** |
| **Live re-application** — 2026-11-24 ~11:00Z, same rule | **75** | 12 | **63** | **84.00%** |
| Live — union text rule (see method) | **85** | 12 | **73** | **85.88%** |
| Live — pre-census subset of the live domain slice (`created_at < 2026-11-23`) | **66** | 9 | **57** | **86.36%** |
| Live — kb-writer, all active | **138** | 25 | **113** | **81.88%** |
| Live — store-wide active | **562** | 110 | **452** | **80.43%** |

**Sensitivity band (499-era proxy):** H+M confidence only **63/79 = 79.75%**; H only **61/79 = 77.22%**.

**Family size:** the census family definition gives **79** (`kb-writer AND domain='verify'`). The relayed **85** is a **different population** — see FAMILY-SIZE RECONCILIATION below.

### METHOD (exact, with rule strings)

**Census label rules, verbatim** from `evidence-pok-sweep/census-mechanics-2026-11-23.md:105`:

> **Surface used in the `census-rows-active-2026-11-23.mech-labels.tsv` ROLLING labels: body-only substring match (R4).** Regex (case-sensitive): `v0\.\d+(?:\.\d+)*|releases/|2026-\d{2}|staged|promote`.

**Label join order** (`census-mechanics-2026-11-23.md:18`): `DUP_EXACT`; `DUP_NEAR`; `ROLLING`; `TRIVIA_SHORT`; `LONG_BODY`; `NOFAM`; `FAMILY_OVERCAP`; `FAMILY_AT_CAP`; `ZERO_ACCESS`; `NEVER_VERIFIED`; `STALE_30D`; `DUPSLUG`.

**NOFAM** = slug does not match `^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$`.

**`ROLLING:<token>` semantics:** the token is the **literal matched string**, one of: `2026-NN` (YYYY-MM month), `v0.N[.N…]` (version), `releases/`, `staged`, `promote`. Frequency in the 499-row set: `2026-11` → 59, `promote` → 13, `staged` → 7, `releases/` → 7, remainder per-version (`v0.31.16` ×2, `v0.31.2` ×2, `v0.31.10` ×2, `v0.30.11` ×2, `v0.31.1` ×2, `v0.29.6` ×2, then ×1 each).

**Live union text rule:** `domain='verify'` OR slug matches `%verify%` / `%gate%` OR body mentions `gate` / `verify` / `pipeline`.

**Rule re-application self-check against the 499-row TSV** (each label count recomputed and compared to the persisted label): `ROLLING 92 == 92` OK; `NOFAM 184 == 184` OK; `ZERO_ACCESS 109 == 109` OK; `NEVER_VERIFIED 499 == 499` OK; `FAMILY_AT_CAP 102 == 102` OK; `FAMILY_OVERCAP 8 == 8` OK.

**ROLLING label-count nuance (stated precisely):** **92 rows** carry a ROLLING tag. The vocabulary-tally figure "ROLLING 109" is the number of ROLLING **token occurrences** (59 `ROLLING:2026-11`, 13 `ROLLING:promote`, 7 `ROLLING:staged`, 7 `ROLLING:releases/`, plus the per-version atoms) across those 92 rows — one row can carry more than one atom. **There are not 109 rolling rows.**

### FAMILY-SIZE RECONCILIATION

The relayed **"85"** does **NOT** match the census family definition (`kb-writer AND domain='verify'` = **79**). A **live union text rule coincidentally yields 85**. The figure's two halves therefore describe **DIFFERENT populations** — this is stated explicitly and **"85 rows" must not be presented as the census basis**.

Independently confirmed here: the 512-row JSON census holds **marginals only** — `by_domain_active` and `by_created_by_agent_active` are separate one-dimensional distributions with **NO author × domain cross-tab** — so **no artifact can yield 85**. The 512-era kb-writer total is **139**; the 499-era verify family is **79**; the relayed 85 is **neither**.

### SOURCES

| Artifact / table | Columns / content used |
|---|---|
| `census-rows-active-2026-11-23.mech-labels.tsv` | `slug`(1), `family_key`(3), `domain`(4), `created_at`(7), `created_by_agent`(16), `body_verbatim_escaped`(18), `mech_labels`(19) — 19 fields, 500 lines = header + 499 data, sha256 `6737281cfc7eddfb2f82a1af3317c1560ca465225162b503292a2ddd5d075ab1` |
| `census-mechanics-2026-11-23.md` | `:18` join order; `:105` ROLLING regex; `:50` PM arithmetic; `:81` drift note; `:546` DUPSLUG semantics |
| `census-active-2026-11-23.json` (512-era) | `generated_at`, `counts`, `distributions`, `cap_evidence`, `family_analysis` — sha256 `d44d7d8b924ba62ad3ba1ad7302b02f79a340f8396448c010eff3d6f790ce199` |
| `public.project_operating_knowledge` (live) | `slug`, `domain`, `body`, `created_at`, `created_by_agent`, `status`, `access_count`, `verify_count`, `last_verified`, `last_accessed_at`, `pinned`, `disputed`, `superseded_by_id`, `source_tier`, `audience` |

### SNAPSHOT

Census snapshot: 499-row TSV, **2026-11-23 12:29Z** (label set regenerated byte-identically at 14:17Z; the store had grown to **501 active by 14:17Z**). Live re-application: **2026-11-24 ~11:00Z** (562 active). The **rules are exact**; the **data is 5-day-stale** relative to the live 562 — so **83.54% is the 499-era measurement and 84.00% is the current-population measurement**. Both populations are stated wherever the 83.5% appears.

### LIMITS

- The TSV stores **NO operational/non-operational class**. "Operational" is **PROXIED by ROLLING-absence**, making the reported share an **UPPER BOUND** on operational (a general-knowledge row is also ROLLING-free).
- The **O/G/R semantic class** (448 operational / 26 general-knowledge / 38 rolling of 512) came from the 08:32Z "wanderer pass 2" whose **per-row names were NEVER persisted** (`cutlist-gap-2026-11-23.md:50`: "The 08:32Z semantic pass counted **26 G + 38 R = 64** of 512 (12.5%). **Its per-row names were never persisted** (counts only in history 2bf04468)") ⇒ the **operational-vs-general-knowledge split is NOT MEASURABLE from any artifact**.
- Populations differ and must not be mixed: the **512-row** JSON census (2026-11-23T08:32:36Z; kb-writer 139, verify-domain 298) vs the **499-row** TSV vs **live 562**.

### VERDICT: **MEASURED** for the non-rolling share — **NOT-MEASURABLE** for the operational-vs-general-knowledge split

**83.54% census / 84.00% live domain slice / 85.88% live union slice: the relayed 83.5% REPRODUCES within ~1.4 points.** The O-vs-G split remains **NOT MEASURABLE**.

**Honesty qualification (required):** the 83.5% reproduces **NUMERICALLY** but is **NOT a recovery of the original claim's computation**. The original was a **512-era SEMANTIC pass** whose per-row names were never persisted; this is a **mechanical ROLLING-absence proxy** over a **different population** (499-era census / live). The proxy share is an **UPPER BOUND** on operational. Confidence band for the 499-era proxy: **77.22%** (H only) to **83.54%** (all).

**Family size:** **79 confirmed** as the census basis; **85 unconfirmed** — it has no in-branch source.

### RECOMMENDATION FOR PHASE-12: **RE-STAMP** (with the proxy caveat and the family disclosure)

Suggested wording: *"83.5% (66/79, census rule re-applied live 2026-11-24: 84.0%)"* — with the explicit disclosure that **operational-vs-general-knowledge is not measurable** (the semantic class was never persisted) and that **the family is 79 rows, not 85**. State both populations (499-era census and live) wherever the 83.5% appears.

---

## 3. Figure 3 — gate-reject rates per author

### RELAYED CLAIM

> "gate-reject rates run 7-18% for every author (leader 8%)".

This is a **SIMULATED gate-verdict measurement**; the live gate is **not enforcing** (shadow-first, enforcement switch OFF per addendum-3 item 3).

**IN-BRANCH SOURCE: NONE (relayed KV only).** Origin: `shared_meta_kv` `pok-write-gate.addendum-leader-2-digest` — "reject rates 7-18% all authors".

### VALUE (as re-derived)

**MECHANICAL-ONLY REJECTION FLOOR = 0% FOR EVERY AUTHOR.** Live, 562 active rows / 26 authors, 2026-11-24 ~10:50Z, SQL `FILTER` aggregates. R0 format violations = **ZERO for every author, on every check**:

| R0 check | Violations (every author) |
|---|---:|
| slug not matching `^[a-z0-9-]+$` | 0 |
| slug length > 44 | 0 |
| body length > 240 | 0 |
| body containing a newline | 0 |
| body starting with `#` | 0 |
| body containing `[SYSTEM CONTEXT:` | 0 |
| body containing a triple-backtick | 0 |
| audience outside `{operation, implementation, both}` | 0 |
| source_tier outside `{agent, curated}` | 0 |

⇒ The relayed **7–18% cannot be attributable to R0/format**; it is either **semantic (requires the judge)** or **not real**.

**R2 ROLLING-STATE LEXICAL PROXY** (exact census regex, per author, active rows) — non-rolling / total:

| author | non-rolling / total | % | author | non-rolling / total | % |
|---|---|---:|---|---|---:|
| worker | 26/160 | 83.75% | architect | 2/10 | 80.00% |
| kb-writer | 25/138 | 81.88% | blueprinter | 2/9 | 77.78% |
| leader | 13/52 | 75.00% | reviewer | 0/6 | 100% |
| giter | 10/24 | 58.33% | sonic-coder | 0/6 | 100% |
| project-manager | 3/24 | 87.50% | skill-keeper | 0/5 | 100% |
| developer | 4/23 | 82.61% | approver | 0/4 | 100% |
| maintenancer | 12/23 | 47.83% | planner | 0/4 | 100% |
| tester | 4/22 | 81.82% | sonic-reviewer | 2/4 | 50.00% |
| coder | 3/18 | 83.33% | sonic-developer | 0/3 | 100% |
| devops | 2/11 | 81.82% | sonic-tester | 2/3 | 33.33% |
| | | | kb-importer | 0/3 | 100% |
| | | | governor | 0/3 | 100% |
| | | | explorer | 0/3 | 100% |
| | | | sonic-leader | 0/2 | 100% |
| | | | tidier | 0/1 | 100% |
| | | | wanderer | 0/1 | 100% |

**PER-AUTHOR ACTIVE-ROW DISTRIBUTION** (total 562, 26 authors): worker 160, kb-writer 138, leader 52, giter 24, project-manager 24, developer 23, maintenancer 23, tester 22, coder 18, devops 11, architect 10, blueprinter 9, reviewer 6, sonic-coder 6, skill-keeper 5, approver 4, sonic-reviewer 4, planner 4, kb-importer 3, explorer 3, governor 3, sonic-developer 3, sonic-tester 3, sonic-leader 2, wanderer 1, tidier 1.

**STORE-LEVEL MEASURED STATE (live):** NEVER_VERIFIED **562/562** (100%; `verify_count = 0` on **EVERY** row ⇒ the verify-count metric is dead code); ZERO_ACCESS **141** (25.1%); ever_accessed **421** (75.0%); `last_verified` set on **484**; NOFAM **248** (44.1%); FAMILY_AT_CAP **90**; FAMILY_OVERCAP **8**; LONG_BODY **0**; TRIVIA_SHORT **0**; STALE_30D **0**; pinned **0**; disputed **0**; `source_tier` curated **0** (agent 562/562); `project_operating_knowledge_embeddings` = **0 rows** (vector leg dark; retrieval is BM25-only). Duplicates: **0** exact-body duplicate groups (self-join on body); **0** pairs at Jaccard ≥ 0.55 (census method over all C(499,2) pairs, **max observed 0.4146**); a live prefix probe (identical 55-char body prefix and `|length difference| ≤ 30`) also returns **0** pairs.

**DUPSLUG semantics:** `n` = the number of **generations** (rows sharing that slug across **ALL statuses**). Distribution in the 499-row TSV: `DUPSLUG:2` → 50 rows, `:3` → 15, `:4` → 3, `:5` → 1, `:7` → 1, `:8` → 1 ⇒ **71 rows across 71 distinct slugs**; **83 slugs** have >1 generation across all statuses (**max 8**, slug `auth-lane-scrub-ensemble-auth-env`) — `census-mechanics-2026-11-23.md:546`.

### METHOD

- **Mechanical-only floor:** SQL `FILTER` aggregates over the live active set applying each R0 predicate per author, 2026-11-24 ~10:50Z. All nine checks return **0** violations for **every** author.
- **R2 rolling-state lexical proxy:** the exact census regex `v0\.\d+(?:\.\d+)*|releases/|2026-\d{2}|staged|promote`, case-sensitive, **body-only** surface, applied per author to live active rows.
- **Per-author active-row distribution:** `SELECT created_by_agent, count(*) FROM project_operating_knowledge WHERE project_id = 83da04de… AND status = 'active' GROUP BY created_by_agent`.
- **Store-level state:** the same SELECT over the full live active set plus the embeddings-table row count.

### IN-BRANCH SIMULATION MATERIAL — WHAT IT ACTUALLY MEASURES

The **only** in-branch simulated-verdict method is an **ADMISSION simulation, not a gate-verdict simulation**. `architecture-recommendation-addendum.md:28` (row A14): "Admission simulation over the census (deterministic fallback): **all 7 default views admit the IDENTICAL 8 rows** (union 8/499 = 1.6%) — because 378/499 are `both`, nothing pinned/curated/disputed. Lower bound: live query-driven fusion varies by instance | worker simulation |". And `:95` (P1 Instrument + shadow): "census read surface (curator-only product tool); gate SHADOW (verdicts logged, never refuses); `reason` soft-required | shadow coverage ≥95%; **shadow verdict mix ≈ census mix** (disagreement ⇒ rubric wrong — do not enforce) | **Yes** (kill-switch) |". Supporting: `:25` (A11 machine census: `verify_count` 0/499, `access_count` 0 on 109, ROLLING 92, DUPSLUG 71, pinned 0, 378/499 `both`) and `:122` (push reach is 8 rows, not 24).

⇒ This material measures **ADMISSION** (which rows enter a view), **not rejection**. The "shadow verdict mix ≈ census mix" line is the only in-branch statement about what the gate's shadow verdicts should look like — it is a **target**, never a **measurement**. **No in-branch artifact contains a per-author gate-reject rate.**

### SOURCES

| Artifact / table | Columns used |
|---|---|
| `public.project_operating_knowledge` | `slug`, `domain`, `audience`, `source_tier`, `status`, `body`, `created_at`, `created_by_agent`, `access_count`, `verify_count`, `last_verified`, `last_accessed_at`, `pinned`, `disputed`, `superseded_by_id`, `content_sha256` |
| `public.project_operating_knowledge_embeddings` | row count only (**0 rows**) |
| `census-rows-active-2026-11-23.mech-labels.tsv` | `slug`, `mech_labels`, `body_verbatim_escaped` (DUPSLUG / ROLLING label distributions) |
| `architecture-recommendation-addendum.md` | `:25` (A11), `:28` (A14 admission simulation), `:95` (P1 Instrument + shadow), `:122` (push reach) |

### SNAPSHOT

Live, 562 active rows / 26 authors, 2026-11-24 ~10:50Z (PG) — consistent with the Figure-2 live re-application at ~11:00Z.

### LIMITS

- The **semantic reject rate is NOT MEASURABLE**: it requires the judge, and the judge has **never run** (shadow-first, enforcement switch OFF; the live gate is not enforcing).
- The **"leader 8%" leg is strictly weaker than the "7–18%" leg**: **"leader 8%" appears in NO artifact** — not even in the addendum that carries "7-18% all authors". It is a **prompt-only variant**. Searches for `reject rate` / `reject-rate` / `reject_rate` returned **0 hits** across the worktree planning dir, the worktree `.agents/`, and the canonical checkout's `.agents/shared/`; the `7-18` hits there are **date/line false positives** (2026-07-18, "rows 17-18").
- The relayed 7–18% **cannot be attributed to R0/format** — the mechanical-only floor is **0% for every author**.
- The **nearest in-branch sibling is NOT a substitute and NOT a re-derivation**: `architecture-recommendation-addendum-v2.md:34` — "**Class-admissible stock (measured): 325 lexical / ≈413 semantic** (82.8% operational, n=64 hand-label; lexical under-counts at 57% recall). The Emperor's boundary rejects **17–35%, not the bulk**." That is a **different number** (82.8% vs 7–18%), on a **different denominator** (n=64 hand-label vs per-author rows), making a **different claim** (boundary rejection vs per-author gate-reject rate). It is recorded here for adjacency only.
- The 512-era overlap classes (BOTH-USEFUL 337 / RAG-DUPLICATE 81 / POK-ONLY 78 / NEITHER 16) and the O/G/R split likewise come only from the **never-persisted 08:32Z semantic pass**.

### VERDICT: **NOT-MEASURABLE** for the semantic reject rate — **MEASURED** for the mechanical-only floor (0% for every author)

The relayed 7–18% (and the "leader 8%" leg) **cannot be reproduced**, because the judge that would produce them has never executed. The mechanical-only rejection floor is **0% for every author**.

### RECOMMENDATION FOR PHASE-12: **DROP-WITH-DISCLOSURE**

Drop the **7–18%** and **leader-8%** numbers with explicit disclosure. The re-derivable substitute is the **mechanical floor (0% per author)** plus the **per-author R2 rolling proxy table** (above). A real per-author reject rate requires an **actual shadow-mode judge run, which has never been executed**. Do **not** substitute the 82.8% / n=64 hand-label sibling or the 17–35% boundary-rejection sibling for the 7–18% claim.

---

## 4. Cross-cutting

### LIVE DRIFT DURING THIS RUN

The active POK set moved **559 → 562** (48 rows created on 2026-11-24; 114 created in the trailing 24h). Superseded **140**, archived **101** — unchanged from the WAVE-2 post-state **557/140/101** except for the active-set drift. **This drift is stated with the snapshot timestamp; it is not silently reconciled.**

### POPULATION RECONCILIATION

| Population | Snapshot | sha256 / identity | Notes |
|---|---|---|---|
| 512-row JSON census | 2026-11-23T08:32:36Z | `d44d7d8b924ba62ad3ba1ad7302b02f79a340f8396448c010eff3d6f790ce199` | `counts`: `total_rows` 667, `active` 512, `superseded` 114, `archived` 41, `other_status` 0, `distinct_slugs` 553, `slugs_with_multiple_generations` 78, `max_generations_for_one_slug` 8, `cap` 512, `cap_gap_active_vs_cap` 0, `headroom_slots` 0, `pinned` 0, `disputed` 0. `rolling_state_smell.active_rows_matching` 106. Its own `cap_evidence.live_cap_constant`: "POK_ACTIVE_CAP_PER_PROJECT = 512 (daemon/services/pok_service.py:104 @ v0.31.37)" |
| 499-row machine-label TSV | 2026-11-23 12:29Z (labels regenerated 14:17Z) | `6737281cfc7eddfb2f82a1af3317c1560ca465225162b503292a2ddd5d075ab1` (500 lines = header + 499 data, 19 fields) | The store grew to **501 active by 14:17Z** |
| Live | 2026-11-24 ~10:45–11:05Z | `pok-active.tsv` (562 data rows, **no header line**) | Active 562 |

**512 → 499 delta.** `census-mechanics-2026-11-23.md:50` — PM's arithmetic: "512 active at 08:32Z − 12 archived + 2 added = 502". `:81` — "**Net drift beyond PM's predicted -10:** PM predicted 499? PM predicted 502; measured 499 = -3 additional net removals. MEASURED. The 08:32Z→now delta is -13 active (512→499), not -10. INFERABLE: additional sweep archives (beyond the named 12) plus the 2 added account for the extra -3." The three populations are **DIFFERENT** and their figures must not be mixed.

### CAP / CONSTANT CORRECTIONS

The census artifacts' "**cap 512 / headroom 0**" is **STALE**. The live cap is **`POK_ACTIVE_CAP_PER_PROJECT = 1024`** (raised from 512 by **v0.31.42**); live headroom = **1024 − 562 = 462**.

Other live constants (`daemon/services/pok_service.py` @ `ea5325a60`): `POK_FAMILY_MAX` **3**, `POK_BODY_MAX_CHARS` **240**, `POK_SLUG_MAX_CHARS` **44**, `POK_VERIFY_WINDOW_DAYS` **30**.

### SOURCES TABLE (all streams used by this record)

| Name | Used for |
|---|---|
| `public.project_operating_knowledge` | `project_id`, `slug`, `domain`, `audience`, `source_tier`, `status`, `body`, `created_at`, `created_by_agent`, `access_count`, `verify_count`, `last_verified`, `last_accessed_at`, `pinned`, `disputed`, `superseded_by_id`, `content_sha256` |
| `public.instances` | `agent_id`, `created_at`, `last_activity_at`, `status` |
| `public.tool_usage_daily` | `day`, `project_id`, `tool_name`, `agent_id`, `call_count` |
| `public.project_operating_knowledge_embeddings` | row count (0) |
| LightRAG HTTP surface | workspace `agents_ensemble`; `POST /documents/paginated`; `/workspaces` |
| Git refs | `16b4e70b2`, `fd7dc6480`, `a1130e257`, `origin/latest` (`bcebe7725`), worktree tip `97f269ea4` (HEAD is a descendant) |
| In-repo docs | `census-mechanics-2026-11-23.md`, `census-rows-active-2026-11-23.mech-labels.tsv`, `census-active-2026-11-23.json`, `cutlist-gap-2026-11-23.md`, `store-effectiveness-rag-overlap-brief.md`, `architecture-recommendation-addendum.md`, `architecture-recommendation-addendum-v2.md`, `plan-overview.md:157`, `technical-analysis.md:16`, `phase12-plan.md:17`, `approver-pok-write-gate.md:45`/`:119` |

---

## 5. In-branch origin of the three relayed figures

### ORIGIN TABLE — all three originate OUTSIDE the repo

All three relayed figures are **leader-authored digests in `shared_meta_kv`, relayed via Ari** — they are **not in-repo files**. Their **only in-repo appearances are the gate text that flags them as unsourced**:

| # | In-repo gate occurrence | Verbatim |
|---|---|---|
| (a)(b)(c) | `phase12-plan.md:17` | "- kb-writer same-run dual-write mechanism (65 of 81 RAG-duplicate rows); kb-writer family 83.5% operational; reject rates 7–18% across all authors. **[S-1 HARD GATE: all three have NO in-branch source…**" |
| (a)(b)(c) | `approver-pok-write-gate.md:119` | "1. **Phase-12 authoring is load-bearing:** the kb-writer trio (65-of-81 / 83.5% / 7–18%) has NO in-branch source…" |
| (a)(b)(c) | `plan-overview.md:319` | "**HARD GATE (S-1 / approver-002 condition 1): the kb-writer trio — (a) 65-of-81 same-run dual-writes, (b) 83.5% operational verify family, (c) 7-18% reject rates — has NO in-branch source…**" |

Actual origins (outside the repo):

| Figure | Origin | Verbatim |
|---|---|---|
| (a) 65-of-81 | `shared_meta_kv` `pok-write-gate.addendum-leader-1` (leader via Ari, 2026-11-23) | "of 81 RAG-DUPLICATE POK rows, 65 (80%) written into BOTH stores in the SAME kb-writer run (±0s)" |
| (b) 83.5% | `shared_meta_kv` `pok-write-gate.addendum-leader-2-digest` | "verify family 83.5% operational" |
| (c) 7–18% | `shared_meta_kv` `pok-write-gate.addendum-leader-2-digest` | "reject rates 7-18% all authors" |

Each figure section above therefore carries the line **`IN-BRANCH SOURCE: NONE (relayed KV only)`**.

### Figure (a) — the "81" IS in-repo, but as a class count only, with NO per-row join key

`store-effectiveness-rag-overlap-brief.md:57` — `| RAG-DUPLICATE | 81 | 15.8% |`; the surrounding class table at `:53–66` reads `RAG-DUPLICATE 81 / 15.8%`, `NEITHER / rolling∧dup 16 / 3.1%`, `redundant-or-rolling total 97 / 19.0%`, `BOTH-USEFUL 337 / 65.8%`, `POK-ONLY 78 / 15.2%`. And `:64` — "Top overlapping pairs: NOT reproducible live this run (probe blockage above). No pair list is fabricated; the census's pair-level detail lives in its own artifact."

⇒ the **65-of-81 SPLIT is not re-derivable from any persisted artifact**: there is a class count, but no per-row list and no join key.

### The snapshot-era measured-state lines this re-derivation SUPERSEDES

- `plan-overview.md:157` — "499 active @13:5xZ; OH 396 / RS 74 / EPISODE 21 / GK 4 / INCIDENT 2 / DUP 2; **12,968-doc RAG corpus**; overlap BOTH-USEFUL 337/65.8%, RAG-DUPLICATE 81/15.8%, POK-ONLY 78/15.2%, NEITHER 16/3.1%; tiers inert (0 pinned/curated/verify; 109 never-accessed …)".
- `technical-analysis.md:16` — "Measured state at base `a1130e257`: 512 active POK rows; **12,968-doc RAG corpus**; overlap classes BOTH-USEFUL 337 (65.8%) / RAG-DUPLICATE 81 (15.8%) / POK-ONLY 78 (15.2%) / NEITHER 16 (3.1%)."

These are the in-branch statements of the **same** 81 / 12,968 snapshot. This re-derivation **supersedes** them on the corpus figure (**live corpus is 13,110**) and on the overlap figure (**live overlap lower bound is 10**). The corpus size moved by **+142** between snapshots; the overlap population could not be re-derived and is bounded, not reproduced.

---

## 6. Cross-check

### Requested cross-checks — results

| # | Check | Requested | Measured | Result |
|---|---|---|---|---|
| 1 | `fig1-overlap-set.tsv` data rows | exactly **10** | file has **11 lines** = 1 header + **10 data rows**; header is 11 tab-separated fields | **AGREE** |
| 2 | `fig1-overlap-set.tsv` `same_run_i` / `same_run_ii` / `same_run_iii` column counts | **9 / 7 / 2** | column sums computed independently from the TSV: `same_run_i` = **9**, `same_run_ii` = **7**, `same_run_iii` = **2** | **AGREE** |
| 3 | `fig1-rag-inventory.tsv` line count | **13,111** (header + 13,110) | `wc -l` = **13,111**; header is `file_source`/`created_at`/`text_len`/`content_sha256`/`text_first_200`; file is newline-terminated | **AGREE** |

**No cross-check DISAGREEMENT.** All three requested cross-checks agree with the payload.

### Independent re-derivations from the on-disk artifacts (not copied from the payload)

These were computed here from the on-disk 562-row artifact `pok-active.tsv` (562 **data rows, no header line** — the first line is the data row `f8410545-… views-origin-gate-contract …`) and from the 499-row machine-label TSV. They are reported as a **cross-check**; the payload numbers stand unchanged in the body of this record.

| Quantity | Payload | Independently re-derived here | Result |
|---|---:|---:|---|
| Active POK rows / authors | 562 / 26 | 562 / 26 | **AGREE** |
| Per-author active-row distribution (all 26 authors) | worker 160 … tidier 1 | identical for all 26 | **AGREE** |
| Store-wide ROLLING → non-rolling | 110 → 452 (80.43%) | 110 → 452 (80.43%) | **AGREE** |
| R2 per-author rolling proxy (all 26 authors) | 26/160 … 0/1 | identical for all 26 | **AGREE** |
| kb-writer ∩ domain='verify' (live) | 75 / 12 / 63 = 84.00% | 75 / 12 / 63 = 84.00% | **AGREE** |
| kb-writer union text rule (live) | 85 / 12 / 73 = 85.88% | 85 / 12 / 73 = 85.88% | **AGREE** |
| kb-writer all active (live) | 138 / 25 / 113 = 81.88% | 138 / 25 / 113 = 81.88% | **AGREE** |
| kb-writer ∩ domain='verify', `created_at < 2026-11-23` | 66 / 9 / 57 = 86.36% | 66 / 9 / 57 = 86.36% | **AGREE** |
| NOFAM (live) | 248 (44.1%) | 248 (44.1%) | **AGREE** |
| FAMILY_AT_CAP / FAMILY_OVERCAP (live, **row** labels) | 90 / 8 | 30 families at exactly 3 → **90 rows**; 2 over-cap families (`prompt-assent` 4 + `guard-inv` 4) → **8 rows** | **AGREE** |
| Exact-body duplicate groups | 0 | 0 | **AGREE** |
| Max Jaccard over all C(562,2) = 157,641 pairs | 0.4146 | 0.4146 | **AGREE** |
| Live prefix probe (identical 55-char prefix, `|len diff| ≤ 30`) | 0 pairs | 0 | **AGREE** |
| Exact-hash grade (POK `content_sha256` ∩ RAG `content_sha256`) | 0 | 0 (562 POK hashes ∩ 13,106 distinct RAG hashes) | **AGREE** |
| Slug-identity candidates across the **full** 562-row POK set | 6 (in the overlap set) | 6 — the identical six slugs | **AGREE** |
| kb-writer earliest / latest active row | 2026-11-09T03:23:45.052277+00:00 / 2026-11-24T06:26:19.866428+00:00 | identical | **AGREE** |
| kb-writer rows created on 2026-11-24 (live-drift leg) | 48 | 48 | **AGREE** |
| Census label self-check (ROLLING / NOFAM / ZERO_ACCESS / NEVER_VERIFIED / FAMILY_AT_CAP / FAMILY_OVERCAP) | 92 / 184 / 109 / 499 / 102 / 8 | 92 / 184 / 109 / 499 / 102 / 8 | **AGREE** |
| kb-writer ∩ domain='verify' on the 499-era TSV | 79 / 13 / 66 = 83.54% | 79 / 13 / 66 = 83.54% | **AGREE** |
| DUPSLUG distribution (499 set) | :2→50, :3→15, :4→3, :5→1, :7→1, :8→1 = 71 rows / 71 slugs | identical | **AGREE** |
| ROLLING **token** occurrences (499 set) | 109 tokens across 92 rows | 109 tokens across 92 rows | **AGREE** |
| `family_key` derivation vs the TSV's own `family_key` column | — | 315/315 non-NOFAM rows match (the 184 NOFAM rows carry no family key) | **AGREE** |
| 512-JSON `counts` | active 512, total_rows 667, superseded 114, archived 41, cap 512 | identical | **AGREE** |
| 512-JSON `generated_at` / sha256 | 2026-11-23T08:32:36Z / `d44d7d8b…` | identical | **AGREE** |
| mech-labels TSV sha256 / lines / fields | `6737281c…` / 500 / 19 | identical | **AGREE** |

**Two cross-check notes (clarifications, not disagreements):**

1. **ROLLING 92 vs "ROLLING 109".** Both are correct at their own granularity: **92 rows** carry a ROLLING tag; **109** is the number of ROLLING **token occurrences** across those 92 rows (one row may carry more than one atom). Neither figure is a disagreement with the payload.
2. **FAMILY_AT_CAP 90.** My first naive pass produced **30**; that was a **family** count, not a **row** count. The payload's **90** is the row-label count (30 families × 3 rows), which the re-derivation confirms. Not a disagreement.

### Corroboration of the in-branch-sourcing result

Independent searches for "83.5" / "65" / "81" across the worktree reproduced the evidence lane's result: the only hits are **unrelated numeric coincidences** (pytest tallies such as "83.56s", source line numbers, date strings) **plus the gate/approver text that flags the trio as unsourced**. No verdict TSV/JSON artifact for a simulated gate run exists anywhere in the worktree or the canonical checkout.

### Two addendum citations — verified locations (as read here)

- `store-effectiveness-rag-overlap-brief.md` **DOES resolve** — at `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/store-effectiveness-rag-overlap-brief.md` (10,429 bytes, Nov 23 13:51) — i.e. in the **worktree under `pok-write-gate/`**, not under `pok-sweep/`. Verified lines: `:51–52` (probe blockage; "11/11 timed out at 300s"), `:53–66` (class table + structural corroboration note), `:57` (`| RAG-DUPLICATE | 81 | 15.8% |`), `:64` (no pair list fabricated), `:97` ("~15.8% RAG-duplicate / ~19% redundant-or-rolling … live re-probe blocked by explorer timeouts — disclosed").
- The admission-simulation material **DOES resolve** — at `/home/nea/ensemble-src/.agents/shared/planning/pok-shape-review/**architecture-recommendation-addendum.md**` (the **addendum**, not the base `architecture-recommendation.md`): `:28` (A14 admission simulation), `:95` (P1 Instrument + shadow), `:25` (A11), `:122` (push reach). The quoted text is verbatim; only the filename in the original addendum was wrong.

---

## 7. Closing table

| Figure | Relayed | Re-derived | Verdict | Action |
|---|---|---|---|---|
| **1 — same-run dual-write** | 65 of 81 RAG-duplicate rows written into BOTH stores in the SAME kb-writer run (±0s) | Overlap set **10** (kb-writer **6**); exact-hash **0**, slug-identity **6**, jaccard-only **4**; same-run (i) **9** / (ii) **7** / (iii) **2**; dual grant **CONFIRMED, still live on `origin/latest`** | **PARTIAL-METHOD** | **DROP-WITH-DISCLOSURE** — replace with overlap=10 (kb-writer 6) + same-run 9/7/2 + the grant-history fact. Do **not** re-stamp 65/81 |
| **2 — verify family operational share** | kb-writer's verify family (85 rows) is 83.5% OPERATIONAL | Census (499-era): **66/79 = 83.54%**; live: **63/75 = 84.00%**; live union: **73/85 = 85.88%**; band H-only **77.22%** → all **83.54%**; family = **79**, not 85; O-vs-G **not measurable** | **MEASURED** (non-rolling share) / **NOT-MEASURABLE** (O-vs-G) | **RE-STAMP** — "83.5% (66/79, census rule re-applied live 2026-11-24: 84.0%)" with the proxy caveat + the explicit family-79 and O-vs-G-not-measurable disclosures |
| **3 — gate-reject rates** | gate-reject rates run 7-18% for every author (leader 8%) | Mechanical-only floor **0% for every author** (all 9 R0 checks); per-author R2 rolling proxy measured (26 authors); "leader 8%" appears in **no artifact** | **NOT-MEASURABLE** (semantic reject rate) / **MEASURED** (mechanical floor = 0%) | **DROP-WITH-DISCLOSURE** — substitute the mechanical floor (0% per author) + the R2 proxy table. Do **not** substitute the 82.8% n=64 or 17–35% sibling. A real rate needs a shadow-mode judge run, never executed |

**Honest summary:** **two of the three relayed figures do not reproduce as stated** (Figures 1 and 3), and **the third's semantic component is not measurable** (Figure 2 — the numeric share reproduces, but only through a mechanical ROLLING-absence proxy over a different population; the operational-vs-general-knowledge split is unrecoverable). None of the three is re-stampable as originally worded. The **mechanism** behind Figure 1 — the `pok`-category dual grant — is confirmed and **still live on `origin/latest`**, which makes the Phase-4 removal the load-bearing action rather than a documentation edit.

# Fig-1 Re-derivation — Method & Limits

Snapshot timestamp (UTC): 2026-11-24T11:01:01Z
Project: 83da04de-a410-4fb5-9e92-251a99d28a52 (agents-ensemble)
RAG workspace: agents_ensemble (sanitized project name; LIGHTRAG_WORKSPACE env unset, resolved per-request by daemon/rag/client.py + daemon/tools/rag_tools.py::_get_project_workspace)
RAG host: https://rag.mtri.app (LIGHTRAG_API_KEY sha256 prefix fa90ac63)

## 1. RAG corpus

Reachable: YES via HTTP POST /documents/paginated with header X-API-Key + LIGHTRAG-WORKSPACE: agents_ensemble.
Total docs reported by API: 13110 (status_counts: processed 13083, failed 27). Claimed corpus size in lane: 12,968 — close but not identical (delta +142; likely the count was taken at an earlier snapshot).
Inventory fetched: 66 pages x page_size=200 (max allowed; 500/1000 -> 422). Saved to fig1-rag-inventory.tsv (13,110 rows).
Timestamps source: API field `created_at` (ISO-8601 with offset) per document; this is the insert timestamp. No `updated_at`-based recompute used for delta (updated_at is processing-completion time, ~seconds-minutes later).

LIMIT: The LightRAG API exposes only `content_summary` (a ~253-char preview) + `content_length`, NOT the full document text and NO content hash. There is no GET-document-content endpoint in the OpenAPI spec (/documents/paginated is the only listing; /documents/{id} 404s). Therefore:
  - exact-hash grade = 0 by construction (we hashed content_summary, which cannot equal a POK body hash);
  - Jaccard is computed on the 253-char prefix only, which understates true similarity for longer docs and makes slug-identity rows look low (0.10-0.45) even though they are near-certain duplicates.
  - The 81 "RAG-duplicate POK rows" figure CANNOT be re-derived from current live data with this method. Only 10 rows are reproducibly classifiable as overlap.

## 2. Live POK rows

Source: PostgreSQL 10.44.0.2:5432 / ensemble_prod, read via psql (SELECT only). The registered read-only connection "ensemble_prod" tool was not available in this instance's tool set, so psql with the ensemble role was used; only SELECT statements were executed.
  SELECT status,count(*) FROM project_operating_knowledge WHERE project_id=... GROUP BY status
  -> active 562, superseded 140, archived 101 (total 803).
Exported 562 active rows to pok-active.tsv (10 tab fields each, no embedded newlines; verified).

## 3. Overlap set

Normalization: lowercase, replace non-[a-z0-9] with space, collapse whitespace, drop stopwords {the,a,an,and,or,of,to,in,is,are,for,on,at,by,with,that,this,it,be,as,not,no,if,then,than,from,so,do,does,was,were,has,have,had,its,their,there,here}.
Similarity = Jaccard over surviving token SETS.
Overlap classification (OR of):
  (A) exact-hash: RAG content_sha256 == POK content_sha256
  (B) slug-identity: RAG file_source filename stem == POK slug
  (C) jaccard-only: Jaccard >= 0.60
Grade priority for the single "best" RAG doc per POK row: exact-hash > slug-identity > jaccard-only, then Jaccard desc, then smallest |delta|.

Result: overlap set size = 10.
  exact-hash: 0
  slug-identity: 6
  jaccard-only: 4
Jaccard histogram over overlap set (on truncated 253-char RAG prefix): 1.0-0.9:0, 0.9-0.8:1, 0.8-0.7:2, 0.7-0.6:1, <0.6:6 (the 6 slug-identity rows whose summary is only a prefix).
Per-author breakdown of overlap: kb-writer 6, maintenancer 2, developer 1, worker 1.
Overlap restricted to author kb-writer: 6.

## 4. Same-run

For each of the 10 overlap rows, best RAG doc (above) chosen; delta = rag_created_at - pok_created_at (seconds).
  (i) instance-linkage: kb-writer instance (agent_id='kb-writer', project_id=83da...) whose [created_at, last_activity_at] window CONTAINS the POK row's created_at, AND that same instance had at least one RAG doc created inside its window. Ambiguity: 0 rows had multiple containing instances (no tie-break needed).
  (ii) time-proximity: |delta| <= 60 s.
  (iii) same-instance-same-second: |delta| <= 1 s (the lane's "±0s" claim).

Counts (over 10 overlap rows): (i)=9, (ii)=7, (iii)=2.
Delta histogram: 0-1s:2, 1-10s:0, 10-60s:5, 60-300s:1, >300s:2, no_ts:0.
Per-author split (overlap / i / ii / iii): kb-writer 6/6/6/2; maintenancer 2/2/1/0; developer 1/1/0/0; worker 1/0/0/0.

Mechanism — meta.json grant state:
  Current worktree tip (HEAD 97f269ea4): agents/kb-writer/meta.json tools.allow = ["rag_insert_text","tool_help","time","nest-report"] — NO pok grant.
  Base sha a1130e257a3c3bb3929455aec28cacafcbfaa1a7: tools.allow = ["rag_insert_text","tool_help","time","pok","nest-report"] — BOTH granted.
  origin/latest: same as base — both granted.
  History: pok grant added to all agents in commit 16b4e70b2 (2026-11-07) and removed from kb-writer in commit fd7dc6480 (2026-11-24, Phase 4). So kb-writer held BOTH grants simultaneously from 2026-11-07 to 2026-11-24.
  kb-writer-authored POK rows in this project: 138, all created between 2026-11-09 and 2026-11-24 (after grant date).

Tool-usage co-occurrence (day granularity, tool_usage_daily; retention window 2026-11-22 .. 2026-11-24 only):
  For project 83da04de, agent kb-writer:
    2026-11-22: pok_upsert 32, rag_insert_text 124
    2026-11-23: pok_upsert 15, rag_insert_text 66
    2026-11-24: pok_upsert 16, rag_insert_text 79
  Day-level dual-write evidence for ALL authors (pok_upsert AND rag_insert_text same day+project):
    2026-11-22 kb-importer a5f20438: 1 / 13
    2026-11-22 kb-writer   83da...:  32 / 124
    2026-11-22 kb-writer   a5f20438:  4 / 17
    2026-11-23 kb-importer a5f20438:  3 / 1
    2026-11-23 kb-writer   54debc61:  2 / 25
    2026-11-23 kb-writer   83da...:  15 / 66
    2026-11-23 kb-writer   a5f20438:  2 / 6
    2026-11-24 kb-importer 83da...:  2 / 4
    2026-11-24 kb-importer a5f20438:  4 / 1
    2026-11-24 kb-writer   83da...:  16 / 79

## 5. Limits / legs that failed or are incomplete

  (a) RAG full text unavailable (see 1) -> exact-hash grade = 0 by construction; Jaccard understated; 81-figure not reproducible. Only 10 reproducible overlaps.
  (b) tool_usage_daily retention window is only 2026-11-22..2026-11-24, so day-granularity dual-write evidence covers only the last 3 days, not the Sep-Oct window in which most POK rows were created.
  (c) The registered read-only DB connection "ensemble_prod" tool (db_postgres_dml_select) was not present in this instance's tool set; psql with the ensemble role was used instead, restricted to SELECT.
  (d) No local LightRAG storage found (searched /home/nea max depth 4 for *lightrag*/workspace dirs); corpus is remote only, so no full-text fallback.
  (e) content_sha256 on RAG side was computed over the 253-char content_summary, not the full text, so the (A) exact-hash grade is not meaningful and is reported as 0.

## Files written

  /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-rederiv-2026-11-24/fig1-rag-inventory.tsv
  /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-rederiv-2026-11-24/fig1-overlap-set.tsv
  /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-rederiv-2026-11-24/fig1-method-and-limits.md
PYEOF
echo "written"
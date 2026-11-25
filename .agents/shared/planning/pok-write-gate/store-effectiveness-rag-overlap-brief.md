# POK Store Effectiveness & RAG-Overlap Brief (pok-write-gate commission)

- Lane: READ-ONLY measurement, commissioned 2026-11-23. Measurement seat: SELECT-only (`ens_db_postgres_select`) against `ensemble_prod`.
- Fence honored: zero writes to live tables — no `pok_upsert`, no `pok_curate`, no `ens_db_repair_execute`, no RAG `experience()` writes. The ONLY artifact produced by this lane is this file. (The Maintenancer first-turn RAG mirror duty was skipped for the same reason.)
- Columns used (exact names, table `project_operating_knowledge`): `status`, `slug`, `domain`, `audience`, `audience_agents`, `source_tier`, `pinned`, `disputed`, `access_count`, `last_accessed_at`, `verify_count`, `last_verified`, `created_by_agent`, `created_at`, `body`. Supporting tables: `project_operating_knowledge_embeddings` (id, entry_id, embedding, created_at), `tool_usage_daily` (day, project_id, tool_name, agent_id, call_count).
- Snapshot time: 2026-11-23 ~11:55–12:10 UTC. Project: 83da04de-a410-4fb5-9e92-251a99d28a52 (agents-ensemble).

## 1. Effectiveness of the POK store

### Store shape
| measure | value |
|---|---|
| rows for project (all statuses) | 679 = 499 active + 121 superseded + 59 archived |
| store age | 14 days (oldest active `created_at` 2026-11-09) |
| active cap pressure | 499/512 (live cap proven by refusal text; docs say 256) |

### Engagement (active rows; `access_count`, `last_accessed_at`)
| band | rows | % of 499 |
|---|---|---|
| never accessed (`access_count=0`) | 109 | 21.8% |
| 1–2 accesses | 178 | 35.7% |
| 3–9 accesses | 162 | 32.5% |
| ≥10 accesses | 50 | 10.0% |

- Total access events: 1,970. Average 3.95. Max 100 (`plan-body-ledger-agreement`).
- `last_accessed_at` stamped on 390/499; **all 390 stamps fall within the last 23 days** (0 older) — consistent with the 14-day store age; the store is too young for a long-tail rot signal.
- Dead weight is UNIFORM across domains, not concentrated: verify 68/287 (23.7%), environment 13/60 (21.7%), other 11/53 (20.8%), artifact 11/61 (18.0%), deploy 6/37 (16.2%), catalog 0/1.
- Access-semantics caveat (honest): whether `access_count` bumps on `pok_query` reads, on context injection, or both is NOT distinguishable from SQL alone. Telemetry cross-check: `tool_usage_daily` carries pok rows only since 2026-11-22 (telemetry is new); on 2026-11-23 for this project: `pok_query` — project-manager 59, leader 4, sonic-leader 2, maintenancer 3 (a different maintenancer instance, 11:08Z, pre-lane), coder 1, explorer 1, giter 1, sonic-tester 1; `pok_curate` — leader 12, project-manager 9, sonic-tester 1 (today's curation sweeps).

### Governance-feature usage (active rows)
| feature | usage |
|---|---|
| `pinned` | **0 rows** — the always-inject tier (cap 2) is completely unused |
| `disputed` | 0 rows |
| `source_tier` | agent 499/499 — **zero curated rows**; the curated tier has never been used |
| `audience` | both 378 (75.8%), operation 74 (14.8%), implementation 47 (9.4%) |
| `audience_agents` narrowing | 0 rows |
| `verify_count` | sum = 0 — never incremented; yet `last_verified` is stamped on 428/499 (85.8%) → verification happens via curation paths without counting verify acts |
| writers | 24 distinct `created_by_agent` values; top: worker 148 (29.7%), kb-writer 131 (26.3%), leader 49, giter 27, project-manager 24, tester 22, developer 20, coder 19; long tail of 17 more agents |

### Retrieval / injection health
- **`project_operating_knowledge_embeddings` contains 0 rows** (table exists, empty). The vector leg of POK retrieval has been DARK for the entire store life; `pok_query` confirms `bm25_only: true` with `embedding_lag` on returned entries. Embeddings are stored as jsonb (no pgvector in cluster).
- Injection surface observed: session context injection carries ~7 rows + an "(N more entries not shown)" pointer; `pok_query('')` ranked floor returns 2 rows → per-turn delivery ≈ 2–7 of 499 rows (0.4–1.4% utilization). The one `pok_query('')` call made by this lane (post-SQL-capture) may have stamped ≤2 rows (`bash-spawn-reaper-silent-stall`, `carrier-fix-01-bus-api-rename-mocks`) — disclosed.

### Effectiveness verdict (honest)
~78% of the store is read at least once in 2 weeks and the top decile (50 rows, ≥10 accesses) is genuinely load-bearing lane-verification how-to. But: 21.8% is provably dead weight, the two governance features that would concentrate signal (pins, curated tier) are entirely unused, verification counting is broken-by-non-use (`verify_count`=0 everywhere), and vector retrieval is dark store-wide — relevance ranking is BM25-only.

## 2. RAG overlap

### Method
- The RAG corpus is NOT PostgreSQL-reachable from this seat: no documents/entities/relationships tables in `ensemble_prod` (public or any non-public schema), none in the registered `ensemble_dev` connection (holds only POK tables + probe schemas); `pg18_rancher_local` (postgres@localhost:5433) connection refused (known-down listener, matches 2026-10-05 note). The corpus lives outside PG (file/service backend).
- Live probing via the sanctioned `explore()` read API was attempted with 10 stratified near-verbatim probes (top-accessed + never-accessed rows across domains) + 1 lone retry: **11/11 timed out at 300s**; the ephemeral explorer instances were not harvestable (instance-not-found). Live pair-level verification is BLOCKED this run — recorded, not worked around.
- PRIMARY NUMBER therefore cites the full-population census measured independently ~1h earlier (provenance: wanderer, project history milestone 2026-11-23, "12,968-doc live RAG corpus" vs 512 dump-active rows):

| class | rows | % |
|---|---|---|
| RAG-DUPLICATE | 81 | 15.8% |
| NEITHER / rolling∧dup | 16 | 3.1% |
| redundant-or-rolling total | 97 | 19.0% |
| BOTH-USEFUL | 337 | 65.8% |
| POK-ONLY | 78 | 15.2% |

- Structural corroboration (this lane): the duplication mechanism is real — the kb-curator first-turn mirror duty pushes KB docs into RAG, and 24 distinct POK writer-agents overlap the `experience()` writer population, so lane-captured gotchas land in both stores by construction.
- Top overlapping pairs: NOT reproducible live this run (probe blockage above). No pair list is fabricated; the census's pair-level detail lives in its own artifact.

### Overlap verdict
Roughly one active row in six (15.8%) duplicates RAG content and ~19% is redundant-or-rolling — under a 512-row cap with 13 slots of headroom, that is material wasted capacity, and it is the strongest single argument for a write gate that checks RAG-first.

## 3. Boundary split (Emperor test 2026-11-23: POK = operational how-to, DevOps kind, for THIS project)

Method: rule-based classification of ALL 499 active slugs (bodies read for ~30 sampled/ambiguous rows) against the Emperor boundary + the store's own doctrine row (`pok-scope-01-rolling-state-out`: rolling state belongs in KV / critical notes / board / release tree). Rough by design; clear cases classified, ambiguous band marked.

| domain | PASS | ELSEWHERE | AMBIGUOUS | total |
|---|---|---|---|---|
| verify | ~262 | ~10 | ~15 | 287 |
| artifact | ~48 | ~8 | ~5 | 61 |
| environment | ~54 | ~3 | ~3 | 60 |
| deploy | ~30 | ~4 | ~3 | 37 |
| other | ~38 | ~9 | ~6 | 53 |
| catalog | 0 | 1 | 0 | 1 |
| **total** | **~432 (86.6%)** | **~35 (7.0%)** | **~32 (6.4%)** | 499 |

ELSEWHERE exemplars (clear cases):
- Lane/event state → history/board: `tool-usage-metrics-p1-approver-001/002/003`, `tool-usage-p1-plan-delivered`, `tool-usage-p1-architect-pass`, `tool-usage-p1-gate-verdict` (6 rows of P1-phase state), `services-unit-pre-existing-reds-2026-11-11`, `group-chat-01-design-landed`, `maintenance-p2-01-phase2-landed-state`, `evidence-commit-ab-fdc2bb4f-restored`.
- Rolling/version-pinned → KV/release tree: `scripts-pin-01-floor-70a0960e5`, `re-gate-rollback-target-count-34-1`, `rollback-target-c7-anchor-promote-1266`, promote-forensics trio.
- Pure knowledge → RAG: `user-tz-asia-ho-chi-minh-confirmed` (user preference; also dead), `discord-01-channel-identity-already-present`, `tag-read-01-lsremote-yields-tagobj`.

AMBIGUOUS band: system/tool behavior facts that dictate operating procedure but are equally at home in RAG (`sql-01-instances-updated-at-varchar`, `kv-setkv-replaces-whole-value`, `w3-f18-store-not-callable-typeerror`, uv/bash quirk rows) and dated-incident rows whose body carries a durable fix recipe (`teigate-01-auth-cluster-identical-both-sides`).

Qualitative cross-tab: ELSEWHERE-class rows skew dead — `user-tz…`, `tag-read-01…`, half the `tool-usage-p1-*` set, 2 of 3 promote-forensics rows all show `access_count=0`. Misfiled rows are disproportionately never-read: the boundary test and the dead-weight signal point the same direction.

## 4. Headlines

1. 499 active rows; **21.8% never accessed** in the store's 14-day life; top decile (50 rows ≥10 accesses) is the real payload.
2. **Pins unused (0), curated tier unused (0), `verify_count` never incremented** — every concentration/authority feature is dark; the store runs as a flat agent-written BM25 pile.
3. **Vector retrieval dark store-wide** (embeddings table empty since birth) — relevance ranking is BM25-only.
4. **~15.8% RAG-duplicate / ~19% redundant-or-rolling** (full-population census, wanderer 2026-11-23; live re-probe blocked by explorer timeouts — disclosed).
5. **~87% of active rows PASS the Emperor boundary test**; ~7% clearly belongs elsewhere (event records, rolling state, pure knowledge) and skews never-read; ~6% ambiguous.

Implication for the write gate: the gate's highest-value checks are (a) RAG-first duplicate check, (b) boundary test with the rolling-state rule, (c) a generalizability bar for one-commission trap rows — together they target the ~22% dead weight and the ~19% redundancy without touching the load-bearing 65%.

## Remaining / caveats
- RAG pair-level overlap not re-verified live (explorer 300s timeouts ×11); census aggregate cited with provenance. The RAG read-path outage itself is a finding for the operator (surfaced in the lane report; NOT anomaly-reported from this lane — the read-only fence forbids the anomaly-row insert).
- `access_count` bump-site (query vs injection) not distinguishable from SQL alone.
- Boundary split is rule-based on slugs + sampled bodies; the ~6% ambiguous band is where a gate agent's judgment would actually earn its keep.

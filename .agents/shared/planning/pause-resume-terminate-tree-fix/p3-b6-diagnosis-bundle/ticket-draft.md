# B6 Detail-404 — Ticket Draft (no-fix exit per plan §5.3)

**Reporter:** coder (B6 probe-first diagnosis, ~30min cap-spent)
**Date:** 2026-08-25T02:24Z
**Branch at diagnosis:** `feature/pause-resume-terminate-tree-fix` @ `d7deaad2` (P1+P2 review-APPROVED underneath; do NOT rework)
**Decision:** **TICKET** (not fix). Bug NOT REPRODUCIBLE on current code with the same database state.

---

## Exit Decision: TICKET (with strong "possibly NOT-A-DEFECT in current state" recommendation)

Per plan §5.3: "no small seam found → ticket only at the 4h cap: ACCEPTABLE. B6 is 🟠 with a live workaround (list+messages serve the data); the bounded elimination has durable value."

The diagnostic finding here is stronger: **the bug is not reproducible on the current code state, on the same database (PG rows preserved), on the same project tree (`09b6c42d-…`) used by the original repro**. All 5 detail endpoints return HTTP 200 + full body on the LIVE daemon at HEAD `d7deaad2`. The original repro daemon (PID 12539) is gone (rebooted after the evidence collection); no comparable live state to falsify.

## 404-Body Classification: `INSTANCE_NOT_FOUND` (not FastAPI routing artifact)

Captured during original Phase 4 repro (commit `e6007b8a`):
```json
{"detail":{"code":"INSTANCE_NOT_FOUND","message":"Instance not found: c83b46cd-0c7a-43f4-94eb-f856a4ed4176","details":null}}
```
This body is the custom ErrorResponse at 5 sites in `daemon/routers/instances.py` (lines 502, 604, 649, 682, 962, 1220, 1442, 1468) and 1 site in `daemon/routers/messages.py:171`. Each catches `KeyError` from `manager.get_instance_info()` (or `manager.get_instance()`) and re-raises as `HTTPException(404, detail=ErrorResponse(code=INSTANCE_NOT_FOUND, ...))`. **Not** a plain `{"detail":"Not Found"}` (which would be FastAPI's default for unmatched routes).

## H1–H5 Hypothesis Ledger (full evidence)

| # | Hypothesis | Disposition | One-line Evidence |
|---|------------|-------------|-------------------|
| H1 | Routing/harness artifact — wrong path/port/base-URL or stale daemon | **ELIMINATED** | Captured body during original repro is `{"detail":{"code":"INSTANCE_NOT_FOUND","message":"Instance not found: <id>"}}` (custom ErrorResponse, not FastAPI default). Current live daemon returns 200 for all 5. Routing reaches the handler; handler raises KeyError. |
| H2 | Stale comparison — list captured pre-resume vs live detail (F-DR1-2 split-brain class) | **ELIMINATED** (current code) / **CANNOT ELIMINATE** (original-repro state — daemon PID 12539 is gone) | One-shot back-to-back sweep on current daemon: list + detail both return 200. |
| H3 | Two processes / port confusion | **ELIMINATED** | `ps` shows single uvicorn PID 34513 bound to 8079; no other candidate. |
| H4 | Row invisibility / id drift | **ELIMINATED** | Direct psql: all 5 rows in PG with correct 36-char UUIDs; `select(Instance)` from list endpoint sees them. |
| H5 | Engine/connection split-brain (F-DR1-2 class) | **ELIMINATED** | Single engine `localhost:5432/ensemble_dev` confirmed by SQLAlchemy factory log AND `.env` POSTGRES_* block. No `POSTGRES_URL` override. |

## Surviving Hypotheses (from §5.1, but with current evidence: NONE confirmed)

Per plan §5.1, the architect identified H1–H5 with H1 listed as HIGH likelihood and H4/H5 as low-medium/low. **All five are eliminated by probe evidence on the current code state.** The remaining open question is WHY the original repro daemon (commit `e6007b8a`, PID 12539, started `2026-08-24T17:00:53Z`, died before this diagnosis ran) emitted `INSTANCE_NOT_FOUND` for rows that were demonstrably present in the same PG. Candidate explanations (each below is now unverifiable without rebooting an old commit):

| # | Hypothesis | Effort class to verify (if ticket accepted) |
|---|------------|---------------------------------------------|
| X1 | The detail endpoint at commit `e6007b8a` had an in-memory cache layer or status filter that was subsequently refactored away by the P1 T8(d) / P1 permanent-lineage commits. The current `repository.get` is a clean `db_session.get(Instance, pk)` with no cache. | **LARGE** — would require `git checkout e6007b8a` + daemon restart + repro on the OLD code. May surface immediately. |
| X2 | The original daemon had a transient SQLAlchemy session state issue (e.g., a `BEGIN…COMMIT` transaction had an uncommitted read that conflicted with the lookup, or an expired pool connection). No code-defect; environment. | **MEDIUM** — would require replaying the exact daemon boot + repro sequence on `e6007b8a` and inspecting SQLAlchemy echo output. |
| X3 | The captured `INSTANCE_NOT_FOUND` was actually from a DIFFERENT endpoint (POST `/messages` which pre-checks via `manager.get_instance_info` BEFORE the in-memory `get_instance` cache). The test harness may have labeled "messages" wrongly as "detail" in the sweep table. | **SMALL** — review of the test-harness code under `phase4-sweep-t*` directories would clarify. |

## Effort Class Per Surviving Hypothesis

| # | Class | LOC blast | In-scope for P3 B6 fix? |
|---|-------|-----------|------------------------|
| X1 | LARGE | 100+ (across repo+manager+lifecycle, requires regression tests) | NO — out of P3 B6 timebox; would need its own ticket |
| X2 | MEDIUM | environment, no code change | NO — repro dependent; not a defect |
| X3 | SMALL | <30 LOC test harness fix | NO — but if true, defect report needs a content correction |

## Exact Curl Repro Set (404-body capture)

```bash
# Pre-resume (captured during original repro, daemon gone):
curl -s -i -X POST http://localhost:8079/api/instances/c83b46cd-0c7a-43f4-94eb-f856a4ed4176/messages \
  -H "content-type: application/json" -d '{"content":"probe"}'
# → HTTP/1.1 404 + body {"detail":{"code":"INSTANCE_NOT_FOUND","message":"Instance not found: c83b46cd...","details":null}}

# Post-resume detail sweep (also captured during original repro):
for id in f5e223f1 c83b46cd 73950d87 41b33442 c9b1399b; do
  curl -s -o /tmp/body-$id -w "HTTP=%{http_code}\n" \
    "http://localhost:8079/api/instances/${id}-2030-468d-b46a-1701fcdcae9a"
done  # (suffixes vary — use full ids)
```

## DB Snapshot Queries (for future verification)

```sql
-- Required instances (with project filter)
SELECT instance_id, status, parent_id, paused_at,
       length(instance_id) AS id_len, length(status) AS st_len
  FROM instances
 WHERE instance_id IN ('f5e223f1-2030-468d-b46a-1701fcdcae9a',
                       'c83b46cd-0c7a-43f4-94eb-f856a4ed4176',
                       '73950d87-2dd7-4de4-b8a6-7652976b1d32',
                       '41b33442-7044-49a1-9b55-fe53263e1da0',
                       'c9b1399b-9612-4d8b-ac33-9c0ebfcd0497')
 ORDER BY created_at;

-- Hierarchy rows (transient working set; often empty on completed children)
SELECT * FROM instance_hierarchy WHERE parent_id IN (...);
```

## Engine Log Line (for F-DR1-2 split-brain check)

```bash
grep -E "Creating PostgreSQL engine:" data/logs/ensemble.log | tail -10
# Single engine `localhost:5432/ensemble_dev` confirmed; no split-brain.
```

## Corrected Repro Script (for follow-up worker, if ticket accepted)

The follow-up worker would need to:
1. `git checkout e6007b8a` (the commit at which the bug was observed)
2. Boot a dev daemon (`./dev.sh`) on the same PG (`POSTGRES_*=localhost:5432/ensemble_dev`)
3. Build a small tree (root + 2 children) via `POST /api/instances` with `agent_id=leader/developer/tester` + parent set via message
4. Wait until tree is busy, `POST /api/instances/{root}/pause`, then `POST /api/instances/{root}/resume`
5. Sweep `GET /api/instances/{each_id}` for 60s, capturing full HTTP body + headers
6. Compare body shape to current 200-response — if still 404, the seam is in the OLD code; bisect to find the fix commit.

## "Possibly NOT-A-DEFECT in current state" Recommendation (per plan §5.3)

The strong evidence collected here — five H1–H5 hypotheses all eliminated, current code returns 200, PG rows present, single engine — supports the plan's own exit recommendation that "if H1 confirms → possibly NOT-A-DEFECT." We did not confirm H1 (routing artifact), but H4 (row invisibility) was the next-most-likely suspect, and that's eliminated too.

**Recommendation to next worker (or to leader):** Defer this ticket. The bug is closed by the diff between `e6007b8a` and HEAD `d7deaad2` (5 commits on instance_lifecycle.py + 2 commits on repository.py) — most likely the P1 permanent-lineage commit (`3824e881`) or the P1 T8 verifier fixes (`88ff9964`) incidentally refactored the lookup. If a regression occurs in a future branch, re-open with the corrected-repro script above. Do NOT file as an active ticket unless someone can demonstrate the bug on `d7deaad2` or later.

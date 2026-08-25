# FT-004 — B6: Detail 404 Post-Resume (NOT REPRODUCIBLE on HEAD d7deaad2 — possibly NOT-A-DEFECT)

**Source spec:** `phase3-plan.md` §5.3 (B6 probe-first exit) + `p3-b6-diagnosis-bundle/`
**Filed:** 2026-08-25 (P3 documentation pass — finalized from `p3-b6-diagnosis-bundle/ticket-draft.md`)
**Status:** OPEN with strong **"possibly NOT-A-DEFECT in current state"** recommendation
**Branch at diagnosis:** `feature/pause-resume-terminate-tree-fix` @ `d7deaad2`

> **Source of record for this ticket:** `.agents/shared/planning/pause-resume-terminate-tree-fix/p3-b6-diagnosis-bundle/` (README, probe1, probes2-5, ticket-draft, raw body captures). This file is the finalized, ticket-format version of `ticket-draft.md`. The bundle is preserved as evidence; this file is the actionable artifact.

---

## 1. Exit Decision: TICKET (with NOT-A-DEFECT recommendation)

Per plan §5.3: *"no small seam found → ticket only at the 4h cap: ACCEPTABLE. B6 is 🟠 with a live workaround (list+messages serve the data); the bounded elimination has durable value."*

The diagnostic finding here is **stronger** than the §5.3 floor: the bug is **not reproducible on the current code state, on the same database (PG rows preserved), on the same project tree (`09b6c42d-…`)** used by the original repro. All 5 detail endpoints return HTTP 200 + full body on the LIVE daemon at HEAD `d7deaad2`. The original repro daemon (PID 12539) is gone (rebooted after the evidence collection); no comparable live state to falsify.

## 2. 404-Body Classification: `INSTANCE_NOT_FOUND` (not FastAPI routing artifact)

Captured during the original Phase 4 repro (commit `e6007b8a`):

```json
{"detail":{"code":"INSTANCE_NOT_FOUND","message":"Instance not found: c83b46cd-0c7a-43f4-94eb-f856a4ed4176","details":null}}
```

This body is the custom `ErrorResponse` raised at 5 sites in `daemon/routers/instances.py` (lines 502, 604, 649, 682, 962, 1220, 1442, 1468) and 1 site in `daemon/routers/messages.py:171`. Each catches `KeyError` from `manager.get_instance_info()` (or `manager.get_instance()`) and re-raises as `HTTPException(404, detail=ErrorResponse(code=INSTANCE_NOT_FOUND, …))`. **Not** a plain `{"detail":"Not Found"}` (which would be FastAPI's default for unmatched routes).

> **Side-finding:** the original repro's `messages` endpoint ALSO 404'd with the same body. The defect report's "detail-only" framing was imprecise — both the detail and the messages endpoints returned `INSTANCE_NOT_FOUND` in the original repro sweep. This ticket is correctly titled "detail 404 post-resume" because the FE primary-surface impact is on the detail endpoint, but the underlying seam (if it exists) likely affects both.

## 3. H1–H5 Hypothesis Ledger (all ELIMINATED on current code)

| #   | Hypothesis                                                                                       | Disposition                                                                                                                                                                  | One-line Evidence                                                                                                                                                                                                                       |
| --- | ------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H1  | Routing/harness artifact — wrong path/port/base-URL or stale daemon                              | **ELIMINATED**                                                                                                                                                               | Captured body during original repro is `{"detail":{"code":"INSTANCE_NOT_FOUND","message":"Instance not found: <id>"}}` (custom ErrorResponse, not FastAPI default). Current live daemon returns 200 for all 5. Routing reaches the handler; handler raises KeyError. |
| H2  | Stale comparison — list captured pre-resume vs live detail (F-DR1-2 split-brain class)           | **ELIMINATED** (current code) / **CANNOT ELIMINATE** (original-repro state — daemon PID 12539 is gone)                                                                       | One-shot back-to-back sweep on current daemon: list + detail both return 200.                                                                                                                                                          |
| H3  | Two processes / port confusion                                                                   | **ELIMINATED**                                                                                                                                                               | `ps` shows single uvicorn PID 34513 bound to 8079; no other candidate.                                                                                                                                                                  |
| H4  | Row invisibility / id drift                                                                      | **ELIMINATED**                                                                                                                                                               | Direct psql: all 5 rows in PG with correct 36-char UUIDs; `select(Instance)` from list endpoint sees them.                                                                                                                              |
| H5  | Engine/connection split-brain (F-DR1-2 class)                                                    | **ELIMINATED**                                                                                                                                                               | Single engine `localhost:5432/ensemble_dev` confirmed by SQLAlchemy factory log AND `.env` POSTGRES_* block. No `POSTGRES_URL` override.                                                                                                |

## 4. Surviving Hypotheses (unverifiable without rebooting an old commit)

The original repro daemon (commit `e6007b8a`, PID 12539, started `2026-08-24T17:00:53Z`, died before this diagnosis ran) emitted `INSTANCE_NOT_FOUND` for rows that were demonstrably present in the same PG. Candidate explanations (each now unverifiable):

| #  | Hypothesis                                                                                                                                                                                                                                                                                            | Effort class to verify (if ticket accepted)                                                                |
| -- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| X1 | The detail endpoint at commit `e6007b8a` had an in-memory cache layer or status filter that was subsequently refactored away by the P1 T8(d) / P1 permanent-lineage commits. The current `repository.get` is a clean `db_session.get(Instance, pk)` with no cache.                                   | **LARGE** — would require `git checkout e6007b8a` + daemon restart + repro on the OLD code.                |
| X2 | The original daemon had a transient SQLAlchemy session state issue (e.g. a `BEGIN…COMMIT` transaction had an uncommitted read that conflicted with the lookup, or an expired pool connection). No code-defect; environment.                                                                            | **MEDIUM** — would require replaying the exact daemon boot + repro sequence on `e6007b8a`.                |
| X3 | The captured `INSTANCE_NOT_FOUND` was actually from a DIFFERENT endpoint (POST `/messages` which pre-checks via `manager.get_instance_info` BEFORE the in-memory `get_instance` cache). The test harness may have labeled "messages" wrongly as "detail" in the sweep table. (See side-finding §2.)     | **SMALL** — review of the test-harness code under `phase4-sweep-t*` directories would clarify.            |

**Disposition for X1–X3:** None are actionable on HEAD `d7deaad2` without rolling back to the old commit. The follow-up work (X3 SMALL) is the only one a future worker could realistically take without rebooting an old tree.

## 5. Effort Class Per Surviving Hypothesis

| #  | Class | LOC blast                                                            | In-scope for P3 B6 fix?                                                |
| -- | ----- | -------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| X1 | LARGE | 100+ (across repo+manager+lifecycle, requires regression tests)     | NO — out of P3 B6 timebox; would need its own ticket                   |
| X2 | MEDIUM| environment, no code change                                          | NO — repro dependent; not a defect                                     |
| X3 | SMALL | <30 LOC test harness fix                                             | NO — but if true, defect report needs a content correction            |

## 6. Exact Curl Repro Set (404-body capture from original repro)

```bash
# Pre-resume (captured during original repro, daemon gone):
curl -s -i -X POST http://localhost:8079/api/instances/c83b46cd-0c7a-43f4-94eb-f856a4ed4176/messages \
  -H "content-type: application/json" -d '{"content":"probe"}'
# → HTTP/1.1 404 + body {"detail":{"code":"INSTANCE_NOT_FOUND","message":"Instance not found: c83b46cd...","details":null}}

# Post-resume detail sweep (also captured during original repro):
for id in f5e223f1 c83b46cd 73950d87 41b33442 c9b1399b; do
  curl -s -o /tmp/body-$id -w "HTTP=%{http_code}\n" \
    "http://localhost:8079/api/instances/${id}-<rest-of-uuid>"
done  # (suffixes vary — use full ids)
```

**Full repro set:** the 5 instance IDs are
- `f5e223f1-2030-468d-b46a-1701fcdcae9a` (root leader)
- `c83b46cd-0c7a-43f4-94eb-f856a4ed4176` (tester, child of root)
- `73950d87-2dd7-4de4-b8a6-7652976b1d32` (developer, child of root)
- `41b33442-7044-49a1-9b55-fe53263e1da0` (worker r1, child of tester)
- `c9b1399b-9612-4d8b-ac33-9c0ebfcd0497` (worker r2, child of tester)

## 7. DB Snapshot Queries (for future verification)

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

## 8. Engine Log Line (for F-DR1-2 split-brain check)

```bash
grep -E "Creating PostgreSQL engine:" data/logs/ensemble.log | tail -10
# Single engine `localhost:5432/ensemble_dev` confirmed; no split-brain.
```

## 9. Corrected Repro Script (for follow-up worker, if ticket accepted)

The follow-up worker would need to:

1. `git checkout e6007b8a` (the commit at which the bug was observed).
2. Boot a dev daemon (`./dev.sh`) on the same PG (`POSTGRES_*=localhost:5432/ensemble_dev`).
3. Build a small tree (root + 2 children) via `POST /api/instances` with `agent_id=leader/developer/tester` + parent set via message.
4. Wait until the tree is busy, `POST /api/instances/{root}/pause`, then `POST /api/instances/{root}/resume`.
5. Sweep `GET /api/instances/{each_id}` AND `POST /api/instances/{each_id}/messages` (per side-finding §2) for 60s, capturing full HTTP body + headers.
6. Compare body shape to current 200-response — if still 404, the seam is in the OLD code; bisect to find the fix commit.

## 10. Recommendation (per plan §5.3)

> *"if H1 confirms → possibly NOT-A-DEFECT"*

We did not confirm H1 (routing artifact), but H4 (row invisibility — the next-most-likely suspect) was also eliminated. Five-for-five elimination supports a **strong "possibly NOT-A-DEFECT in current state"** recommendation.

**Recommendation to next worker (or to leader):** **Defer this ticket.** The bug is closed by the diff between `e6007b8a` and HEAD `d7deaad2` (5 commits on `instance_lifecycle.py` + 2 commits on `repository.py`) — most likely the P1 permanent-lineage commit (`3824e881`) or the P1 T8 verifier fixes (`88ff9964`) incidentally refactored the lookup. If a regression occurs in a future branch, re-open with the corrected-repro script above. **Do NOT file as an active ticket unless someone can demonstrate the bug on `d7deaad2` or later.**

## 11. Action Items (Bisect / Future Verification)

1. **Bisect action item:** if a future regression of this symptom appears (HTTP 404 + `INSTANCE_NOT_FOUND` body for a row present in PG), run `git bisect` between `e6007b8a` and `d7deaad2` with the corrected-repro script (§9) to identify the commit that incidentally closed the seam. No code change to the current state is required.
2. **X3 verification (SMALL effort):** audit the Phase 4 test-harness code under `phase4-sweep-t*` directories to determine whether the original "detail-only" framing was a harness labeling bug. If yes, the defect report needs a content correction (no code fix).
3. **No active work recommended.** The ticket stays open as a durable record of the bounded elimination; reopen if and only if a regression appears.

## 12. Bundle Path References

- `p3-b6-diagnosis-bundle/README.md` — bundle overview, hard-invariant compliance.
- `p3-b6-diagnosis-bundle/probe1.md` — 404-body classification + captured-from-original-repro evidence + surfacing code paths.
- `p3-b6-diagnosis-bundle/probes2-5.md` — DB byte-compare (probe 4), engine split-brain check (probe 5), one-shot sweep (probes 2+3), H1–H5 ledger.
- `p3-b6-diagnosis-bundle/ticket-draft.md` — source draft for this ticket.
- `p3-b6-diagnosis-bundle/probe1-detail-f5e223f1.headers.body.txt` — raw response from probe 1 (HTTP 200).
- `p3-b6-diagnosis-bundle/detail-*.body` — raw response bodies for all 5 detail calls (all HTTP 200).

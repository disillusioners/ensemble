=== Probe 4: DB byte-compare — SELECT instance_id, status, parent_id, paused_at FROM instances WHERE instance_id IN (5 ids) ===
Time: 2026-08-25T02:22:46Z
DB: localhost:5432/ensemble_dev

             instance_id              |   status   |              parent_id               | paused_at | id_len | st_len 
--------------------------------------+------------+--------------------------------------+-----------+--------+--------
 f5e223f1-2030-468d-b46a-1701fcdcae9a | terminated |                                      |           |     36 |     10
 c83b46cd-0c7a-43f4-94eb-f856a4ed4176 | terminated | f5e223f1-2030-468d-b46a-1701fcdcae9a |           |     36 |     10
 73950d87-2dd7-4de4-b8a6-7652976b1d32 | completed  | f5e223f1-2030-468d-b46a-1701fcdcae9a |           |     36 |      9
 41b33442-7044-49a1-9b55-fe53263e1da0 | completed  | c83b46cd-0c7a-43f4-94eb-f856a4ed4176 |           |     36 |      9
 c9b1399b-9612-4d8b-ac33-9c0ebfcd0497 | completed  | c83b46cd-0c7a-43f4-94eb-f856a4ed4176 |           |     36 |      9
(5 rows)


Byte-length: all 5 ids = 36 chars (matches UUID format); instances are PERMANENT in PG.

=== Probe 5: SQLAlchemy engine log + POSTGRES_URL split-brain check ===
---Live daemon engine lines (data/logs/ensemble.log)---
66636:09:22:01 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: localhost:5432/ensemble_dev
66905:09:22:10 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: localhost:5432/ensemble_dev
67158:09:22:15 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: localhost:5432/ensemble_dev
67411:09:22:20 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: localhost:5432/ensemble_dev
67680:09:22:42 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: localhost:5432/ensemble_dev

---Live daemon .env POSTGRES_* (single engine) ---
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=ensemble_dev
POSTGRES_USER=ensemble
POSTGRES_PASSWORD=

---POSTGRES_URL presence? ---
(none — single-engine config; no split-brain)

---

## Probe 2+3 — One-shot back-to-back sweep (≤15+20 min — combined since single-script falsifies H2 and H3)

**Time:** 2026-08-25T02:16:22Z
**Method:** Same shell loop (`for id in ${IDS[@]}; do curl ... ; done`), same client (`curl`), same base URL (`http://localhost:8079`), back-to-back, no daemon restart between calls.

**One-shot sweep on all 5 ids:**

| Instance ID | HTTP | Size (B) | Time (s) |
|-------------|------|----------|----------|
| `f5e223f1-…-1701fcdcae9a` (leader) | 200 | 1768 | 0.009 |
| `c83b46cd-…-f856a4ed4176` (tester) | 200 | 1468 | 0.008 |
| `73950d87-…-7652976b1d32` (developer) | 200 | 1106 | 0.013 |
| `41b33442-…-fe53263e1da0` (worker r1) | 200 | 1667 | 0.010 |
| `c9b1399b-…-9c0ebfcd0497` (worker r2) | 200 | 1692 | 0.007 |

**List endpoint:** HTTP 200, 11122-byte payload, 6 instances in JSON.

**Single daemon on port 8079** (`ps -o pid,ppid,etime,command` confirmed PID 34513, started 4:14AM today, no other uvicorn processes bound to 8079).

**→ H2 (stale comparison — list captured pre-resume vs live detail) ELIMINATED at the current code state.**
  Evidence: same client, same base URL, same loop → list AND detail BOTH return 200.

**→ H3 (two-processes / port confusion) ELIMINATED.**
  Evidence: `ps` confirms exactly one uvicorn bound to 8079; single `daemon.process` lineage.

## H1–H5 Hypothesis Ledger (final)

| # | Hypothesis | Disposition | One-line Evidence |
|---|------------|-------------|-------------------|
| H1 | Routing/harness artifact — wrong path/port/base-URL (FastAPI 404 `{"detail":"Not Found"}`) or stale daemon | **ELIMINATED** | Captured body during original repro is `{"detail":{"code":"INSTANCE_NOT_FOUND","message":"Instance not found: <id>"}}` (custom ErrorResponse, not FastAPI default). Current live daemon returns 200 for all 5. Routing reaches the handler; handler raises KeyError. |
| H2 | Stale comparison — list captured pre-resume vs live detail (F-DR1-2 split-brain class) | **ELIMINATED** (current code) / **CANNOT ELIMINATE** (original-repro state, daemon is gone) | One-shot back-to-back sweep on current daemon: list + detail both return 200. The original-repro daemon (PID 12539) is gone; no comparable live sweep possible. |
| H3 | Two processes / port confusion | **ELIMINATED** | `ps -o pid,ppid,etime,command` shows single uvicorn PID 34513 bound to 8079; no other candidate. |
| H4 | Row invisibility / id drift | **ELIMINATED** | Direct psql SELECT shows all 5 rows in PG with correct 36-char UUIDs and matching bytes; `select(Instance)` from list endpoint sees them. |
| H5 | Engine/connection split-brain (F-DR1-2 class) | **ELIMINATED** | Single engine `localhost:5432/ensemble_dev` confirmed by SQLAlchemy factory log AND by `.env` POSTGRES_* block. No `POSTGRES_URL` override. |


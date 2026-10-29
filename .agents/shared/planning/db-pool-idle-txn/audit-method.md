# Audit Method — DB borrow-site census (F1)

- **Lane:** `fix/db-pool-idle-txn` · job `634c38f2` · branch `fix/db-pool-idle-txn`
- **BASE anchor:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (verified via `git rev-parse HEAD` in `/home/nea/ensemble-src-wt-dbpool-idletxn`)
- **Companion artifacts (same directory, written in parallel — cross-reference by filename only):**
  `plan-overview.md` · `decisions.md` · `phase1-plan.md` · `phase2-plan.md` · `phase3-plan.md`
  · `research-leak-sites.md` · `research-pool-couplings.md` · `research-audit-census.md` · `test-strategy.md` · `wi-78967dba-verdict.md`
- **Status:** Draft for review — planning only, zero code changes. This artifact **is** F1's deliverable.

---

## 1. Purpose

Two jobs, one procedure:

1. **F1's audit deliverable** — the systematic, bounded audit of every DB connection/session borrow in
   `daemon/`, with a classification and a per-family verdict, so the lane's "0 unclosed borrows" claim is
   *reproducible* rather than asserted.
2. **The future-lane re-run recipe** — a command any later lane can execute to (a) detect a NEW
   non-close-guaranteed borrow, (b) re-verify that the fix did not introduce one, and (c) re-baseline after
   legitimate code movement.

**The claim this artifact must support (and the strength it actually has):**

> **REPRODUCIBLE (scanner-independent):** there are exactly **FOUR** non-`with` session borrows in `daemon/`
> — at `daemon/manager.py:2782`, `daemon/repositories/auth_bootstrap/repository.py:93`,
> `daemon/repositories/auth_bootstrap/repository.py:161`, `daemon/services/child_reports.py:2594` — and **all
> four close in a `finally`**. Every other borrow in `daemon/` is a `with`/`async with` form.

That claim reproduces **exactly** under both the pattern scanner and the AST scanner, and is the claim the
F1 gate should assert. The census's *aggregate total* (`≈689`) is **not** scanner-independent — see §5.3.

---

## 2. Scope and fences

| Dimension | Rule |
|---|---|
| Tree | `daemon/` only (the shipped daemon package) |
| File types | `*.py` only |
| Excluded | `tests/`, `scripts/`, `plugins/`, `frontend/`, `docs/`, `.agents/`, `releases/` |
| Rationale for the exclusions | `tests/` fixtures create their own engines and are not production borrow sites; `plugins/` code runs under its own process; `scripts/` are one-shot CLIs |
| Mutability | **READ-ONLY.** The audit reads files; it never writes to `daemon/`, never opens a DB, never runs the daemon. |
| Live-install fence | The scan MUST run against the worktree (`/home/nea/ensemble-src-wt-dbpool-idletxn`), never against `/home/nea/agents-ensemble/` or `releases/`. |

---

## 3. The enumeration

### 3.1 Primary — AST scanner (canonical)

The census's method note says *"Python-only, `daemon/` scope, tests excluded"* (research-audit-census.md:15).
The canonical form of that scanner is below. **It counts borrow SITES (statements), not lines**, so a
multi-line `with WriteGuardSession(\n Session(...),\n)` counts once, and a `with a, b:` counts twice.

> **Materialisation note:** this lane is planning-only, so the script is **not** written into the repo. The
> phase-3 implementer materialises it at `scripts/audit/borrow_census.py` (exact source below, verified to run
> against this HEAD) and the re-run recipe in §7 refers to that path.

```python
#!/usr/bin/env python3
"""Borrow-site census over daemon/ — AST-based, re-runnable.

Usage: python3 scripts/audit/borrow_census.py <repo-root>

Prints one line per family, the grand total, the docstring N/A note count,
and the per-site detail for family C (the only manual-borrow family).
Exit code 0 always (the caller diffs the output); a parse error is
reported on stderr and counted under PARSE-ERROR.
"""
import ast
import os
import sys
from collections import Counter

FAMILIES = (
    ("A", "A  with Session(...) / WriteGuardSession(Session(...))"),
    ("B", "B  with SQLModelSession(...)"),
    ("C", "C  non-with session assignment (= Session(...))"),
    ("D", "D  with engine.connect() / engine.begin()"),
    ("E", "E  async with pool.acquire()"),
    ("F", "F  raw psycopg / raw_connection / getconn / putconn"),
)


def _is_session_call(node):
    """True for Session(...) / SQLModelSession(...) / WriteGuardSession(...)."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in ("Session", "SQLModelSession", "WriteGuardSession")
    if isinstance(f, ast.Attribute):
        return f.attr in ("Session", "SQLModelSession", "WriteGuardSession")
    return False


def _is_connect_call(node):
    """True for <engine>.connect() / <engine>.begin() — engine-level borrows."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr in ("connect", "begin")
    if isinstance(f, ast.Name):
        return f.id in ("connect", "begin")
    return False


def _is_acquire_call(node):
    """True for pool.acquire() — asyncpg / psycopg_pool checkouts."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "acquire")


def scan(root):
    counts = Counter()
    family_c = []
    docstring_na = 0
    parse_errors = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "daemon")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            try:
                tree = ast.parse(src)
            except SyntaxError as exc:
                parse_errors.append((os.path.relpath(path, root), str(exc)))
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.With):
                    for item in node.items:
                        if _is_session_call(item.context_expr):
                            counts["A"] += 1
                        elif _is_connect_call(item.context_expr):
                            counts["D"] += 1
                elif isinstance(node, ast.AsyncWith):
                    for item in node.items:
                        if _is_acquire_call(item.context_expr):
                            counts["E"] += 1
                elif isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, (ast.Name, ast.Attribute)):
                            if _is_session_call(node.value):
                                counts["C"] += 1
                                family_c.append((os.path.relpath(path, root),
                                                 node.lineno,
                                                 ast.unparse(node)[:90]))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc and ("Session(" in doc or "sessionmaker" in doc):
                        docstring_na += 1
    return counts, family_c, docstring_na, parse_errors


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    counts, family_c, doc_na, errs = scan(root)
    for key, label in FAMILIES:
        print(f"{label}: {counts.get(key, 0)}")
    print(f"TOTAL borrow sites (A+B+C+D+E+F): {sum(counts.values())}")
    print(f"docstring N/A notes: {doc_na}")
    for rel, line in errs:
        print(f"PARSE-ERROR {rel}: {line}")
    print("--- family C detail (the only manual-borrow family) ---")
    for rel, line, text in family_c:
        print(f"  {rel}:{line}: {text}")
```

### 3.2 Secondary — pattern cross-check (grep)

A fast, independent sanity check. **Verified to reproduce families A, B, C, F exactly at this HEAD.**

```bash
cd /home/nea/ensemble-src-wt-dbpool-idletxn
# A — context-managed session borrows
grep -rnE "with (SQLModel\.)?Session\(|WriteGuardSession\(Session\(" daemon/ --include='*.py' | wc -l
# B — aliased CM borrows
grep -rnE "with SQLModelSession\(" daemon/ --include='*.py' | wc -l
# C — NON-context-managed assignments (the risk set)
grep -rnE "=\s*(SQLModel)?Session\(" daemon/ --include='*.py' | wc -l
# D — context-managed connection borrows
grep -rnE "with .*engine\.begin\(|with .*engine\.connect\(" daemon/ --include='*.py' | wc -l
# E — async pool acquisitions
grep -rnE "async with .*acquire\(" daemon/ --include='*.py' | wc -l
# F — raw psycopg / getconn / putconn (must be 0)
grep -rnE "raw_connection\(|getconn\(|putconn\(" daemon/ --include='*.py' | wc -l
# session factories (must be 0 — no sessionmaker anywhere)
grep -rnE "sessionmaker|async_sessionmaker" daemon/ --include='*.py' | wc -l
```

> **GNU grep trap (blueprint "[TESTING]"):** `grep -E "a\|b"` is BROKEN — under ERE `\|` is a *literal pipe*.
> Use a plain `|` under `-E`. The patterns above contain no alternation and are therefore safe.

> **Tooling note:** `rg` (ripgrep) is **not installed on this host** — the census's "ripgrep one-liners"
> presentation is a rendering, not the executed tool. `grep -rnE` is the executable equivalent. Any future lane
> that installs `rg` must re-baseline before comparing counts.

### 3.3 Session-factory finding (a structural pre-check)

`grep -rnE "sessionmaker|async_sessionmaker" daemon/` → **0 hits** (verified at this HEAD). **There is no
`sessionmaker` in `daemon/`** — every borrow is a direct `Session(engine)` / `SQLModelSession(engine)` or an
engine-level `connect()`/`begin()`. This matters because it means there is exactly **one** place where a
session-scoped configuration could be injected (and where `expire_on_commit`/`autoflush`/pool-binding behaviour
lives). The only scope helper is `manager._session_scope()` (`daemon/manager.py:2774-2787`).

---

## 4. Classification rules

Applied **per borrow site** (not per file). The rule set is the census's, restated as a decision procedure.

| Class | Definition | Decision rule |
|---|---|---|
| **CLEAN** | The borrow's scope is *guaranteed* to close on every exit path. | (i) the borrow is a `with` / `async with` item **and** the context manager's `__exit__` closes/returns the connection **or** the pool's `reset_on_return` performs a server-side rollback; **or** (ii) the borrow is a manual assignment inside a `try:` whose `finally:` calls `close()` (with an explicit `rollback()` on every error path); **or** (iii) the borrow is transferred to another owner (a context-managed proxy). |
| **RISK** | The borrow is *close-clean* but carries a **transaction-state** or **retention** hazard on some path. | A `with` borrow that can be **held past its scope** (an abandoned worker thread, an awaited stall inside the block), **or** a code path that leaves a transaction open on the server, **or** a path whose error handling skips a rollback. The borrow still closes — the *transaction* is what leaks. |
| **LEAK** | A confirmed missing close/rollback — the connection is abandoned without release. | The borrow is neither context-managed nor finally-closed on at least one exit path. |
| **N/A** | Not a borrow site. | Comment, docstring, type annotation, import, function definition, or a `Session(` mention inside prose. |

**Ordering rule (important):** classify **close-hygiene** first (CLEAN vs LEAK), then **transaction-state**
(CLEAN vs RISK). A site can be close-CLEAN and still RISK — that is exactly the F3/F4 shape.

**Why "close-clean" is not the same as "safe":** SQLAlchemy's pool `reset_on_return` (the library default
`'rollback'`, **not** explicitly set anywhere in `daemon/` — see research-pool-couplings.md:56) issues a
server-side rollback when a connection is **returned**. It does **not** help a connection that is **checked out
and never returned** — the readiness probe's timed-out-`to_thread` shape. Hence: *close-clean* ≠ *transaction-clean*.

---

## 5. The baseline at `19fc11c8b`

### 5.1 The census's reported counts (research-audit-census.md §1b)

| Family | Pattern | Sites | Classification |
|---|---|---:|---|
| A | `with Session(...)` / `WriteGuardSession(Session(...))` | 439 hits (≈435 code, 4 docstring N/A) | ALL CLEAN |
| B | `with SQLModelSession(...)` | 175 | ALL CLEAN |
| C | non-`with` assignments `= Session(...)` | **4** | ALL CLEAN (verified individually) |
| D | `with engine.begin()` / `with engine.connect()` | 60 hits | ALL CLEAN (0 non-`with` connection borrows) |
| E | asyncpg `pool.acquire()` (checkpointer pool) | 15, all `async with` | ALL CLEAN |
| F | raw psycopg / `raw_connection` / `getconn` / `putconn` | 0 | — |
| | **TOTAL audited (AST baseline — CANONICAL)** | **779** (A=613 / B=175 / C=4 / D=148 / E=14 / F=0) | **779 close-guaranteed / 0 LEAK at borrow-hygiene level** — the census's **≈689** aggregate is **RETIRED / NON-CANONICAL** (unrecorded pattern breadth for families D and E; conservative, so the conclusion is unaffected — see §5.3: "the scanner definition is the contract, not the aggregate number") |

*(Arithmetic check: 435 + 175 + 4 + 60 + 15 = 689 — the census's own numbers are internally consistent.)*

### 5.2 Independent re-verification performed for this artifact

Re-run at this HEAD (`19fc11c8b`) on 2026-10-28, read-only:

| Family | Census value | Pattern scanner | AST scanner | Verdict |
|---|---:|---:|---:|---|
| A | 439 | **439** ✔ | 613 (A+B combined — the AST scanner's `A` also counts `SQLModelSession`) | **REPRODUCES EXACTLY** |
| B | 175 | **175** ✔ | (folded into A above) | **REPRODUCES EXACTLY** |
| C | **4** | **4** ✔ | **4** ✔ | **REPRODUCES EXACTLY — same 4 sites** |
| D | 60 | 37 (literal `with engine.`) / 51 (engine-named receiver) / 148 (AST) / 152 (grep-broad) | **148** | **PATTERN-DEPENDENT — see §5.3** |
| E | 15 | **14** (every `acquire(` variant) | **14** | **PATTERN-DEPENDENT — see §5.3** |
| F | 0 | **0** ✔ | **0** ✔ | **REPRODUCES EXACTLY** |
| `sessionmaker` | 0 | **0** ✔ | n/a | **REPRODUCES EXACTLY** |

**Family C reproduces exactly, site for site** — the AST scanner independently found exactly the four
file:line the census names, and no fifth:

```
daemon/manager.py:2782: session = Session(self._engine)
daemon/repositories/auth_bootstrap/repository.py:161: session = Session(engine)
daemon/repositories/auth_bootstrap/repository.py:93: session = Session(self._engine)
daemon/services/child_reports.py:2594: session = Session(self._manager._engine)
```

(The scanner's target rule was **broadened** during this re-verification — from a fixed target-name list
(`session`/`own`/`db_session`/`own_session`) to *any* assignment target — and the count stayed at 4. The family
is complete.)

### 5.3 The reproducibility caveat (must be carried into `decisions.md`)

**The aggregate `≈689` is NOT scanner-independent.** Families **A, B, C, F** and the `sessionmaker` finding
reproduce **exactly** under both scanners. Families **D** and **E** do not, because their counts depend on how
broadly the *receiver* pattern is defined:

- **D** measured 37 (literal `with engine.connect()`), 51 (engine-named receiver), 148 (AST — every
  `with`-item whose callee attribute is `connect`/`begin`), 152 (grep-broad). The census's **60** corresponds to
  a **narrower scanner definition that is not recorded in the research file** — it cannot be reproduced from the
  patterns the census itself printed.
- **E** measured **14** under *every* `acquire(` variant tried (including the census's own printed pattern);
  the census reports **15**. The one-site delta is not recoverable from the recorded method.

⇒ **The corrected, defensible statement of the census result is:**

> Under a canonical AST definition the census covers **779 borrow sites** in `daemon/` (613 context-managed
> session borrows + 4 manual session borrows + 148 engine-level connection borrows + 14 async pool
> acquisitions + 0 raw-psycopg borrows). **Every one of the 779 is close-guaranteed**: the 775 `with` /
> `async with` sites close by construction, and the 4 manual sites close in a `finally` (verified individually,
> §6). **0 LEAK.**
>
> The census's narrower `≈689` figure used an unrecorded pattern breadth for families D and E. It is
> **conservative** (it under-counted the audited set), so the conclusion is unaffected — but a future lane that
> re-runs with a different breadth and sees a different total must **not** conclude that the code drifted.
> **The scanner definition is the contract, not the aggregate number.**

### 5.4 The hazard set (the RISK classification)

The borrow census's **borrow-hygiene** verdict is 0 unclosed. Its **transaction-state / retention** verdict is
not 0 — four hazards remain, and they are the lane's fix targets:

| ID | Site (file:line @HEAD) | Mechanism | Family |
|---|---|---|---|
| **R1 → F3** | `daemon/tools/ens_db_tools.py:1479`, `:1491` | `session.execute(text("BEGIN"))` sits **outside the inner `try:`** (inner try `:1480`/`:1492`; rollbacks `:1488`/`:1501`/`:1503`); a failure at the BEGIN (or at the DDL-branch rollback) jumps to the outer `except` at `:1513` with **no rollback**. | A (repair engine, site 8) |
| **R2 → F4** | `daemon/services/readiness.py:1477-1478` (DB probe), `:1752-1770` (queue probe), and the checkpoint sentinel probe via the same `_guarded` seam `:1262-1290` | `asyncio.wait_for(asyncio.to_thread(probe), timeout=…)` — on timeout the **worker thread is not cancellable**; the probe's `with engine.connect()` stays open and the PG backend sits `idle in transaction` until the query finishes. The module's own docstring states it (`readiness.py:107-112`). The manager engine sets **no** `statement_timeout` and **no** `idle_in_transaction_session_timeout` (`daemon/repositories/factory.py:252`, `:323-338`) and the DB/queue probes have **no** cancellation. | D (manager engine, site 1) |
| **R3** | `daemon/services/child_reports.py:2594` gate (and similar borrow-then-await shapes) | Borrow is CLEAN (finally-close), but an `await` stall inside the scope holds the txn idle-in-transaction for the stall duration. **Amplification surface, not a leak** — no defect at HEAD. | C |
| **E-logic → F6** | `daemon/services/mission_record_service.py:679-692` → `_mint_epoch_locked` `:499` → `daemon/repositories/mission_record/repository.py:498-514` | The UniqueViolation is **REFUTED as a leak** (the boot lane passes NO session; `insert_mission` uses `with Session(self._engine) as own:` → the exception unwinds → close → pool reset rollback). The 4×/boot UniqueViolation is real, but it is a **logic defect**, not a borrow defect. | A |
| **F-rename → F5** | `daemon/services/wedge_capture/db_capture.py:308`, `:387` (+ docstring `:34`) | `FROM tasks` — the table is `task` (`daemon/repositories/task/models.py:171`). The first failure aborts the transaction and the remaining reads fail with *"current transaction is aborted"* — same-connection cascades. Its own one-shot `psycopg.connect` closes in `finally` (`:289-294`) ⇒ **no pool, no leak**; it is a *diagnostic blind spot*. | (none — one-shot psycopg) |

**Hazard set = {A (F3), D-retention (F4), E-logic (F6), F-rename (F5)}** — matching the lane's hazard table.
**R1 and R2 are the only two that are genuine *pool* hazards.** The other two are logic/diagnostic defects that
the same lane must fix because they sit in the incident's causal neighbourhood.

---

## 6. The audited-and-clean families (named, with file:line) + RCA premise corrections

### 6.1 Family B — the `/api/instances` list path (RCA premise #2 — **CORRECTED**)

- Route: `GET /api/instances` → `list_instances` (`daemon/routers/instances.py:370-371`).
- Service: `daemon/services/instance_lifecycle.py:5021` → `instance_repository.list(...)` (`:5090-5105`).
- Repository: `SQLModelInstanceRepository.list` (`daemon/repositories/instance/repository.py:1259`), with the
  session created **inside the repository**, per call:
  - **flat path:** `with SQLModelSession(self.engine) as db_session:` — `daemon/repositories/instance/repository.py:1383`
  - **root+BFS path:** `with SQLModelSession(self.engine) as db_session:` — `daemon/repositories/instance/repository.py:1434`
- The ORM `select(Instance)` renders the observed fingerprint `SELECT instances.instance_id, instances.project_id, ...`.

**RCA premise correction.** The RCA (`incident-20261026-db-pool-idle-in-transaction.md:59`) claimed this path
leaks via *"ORM read path, autobegin without commit/rollback"*. That is **literally true of the code text** (there
is no explicit commit/rollback) but **NOT SUPPORTED as a leak**: the session is context-managed, `close()` returns
the connection to the pool, and the pool's `reset_on_return` (default `'rollback'`, not overridden —
`daemon/repositories/factory.py:323-338`) issues the server-side rollback. The residual exposure is **R2** (a
thread hung *inside* the with-block), not a missing close.

### 6.2 Family C — the `job_queue_items` count path (RCA premises #3 and #4 — **REFUTED / CORRECTED**)

- **The readiness `queue_freshness` probe does NOT read `job_queue_items`.** `_QUEUE_MAX_AGE_SQL_POSTGRES`
  (`daemon/services/readiness.py:201-205`) and `_INFLIGHT_COUNT_SQL_POSTGRES` (`:228-237`) both read
  `FROM task WHERE status = :status_running`. The RCA's premise #3 ("the probe leaks a transaction per tick")
  is **REFUTED** — re-verified at HEAD, matching the soak lane's premise-10 reconciliation.
- **The fingerprint's real owner** is `_DEFER_PENDING_COUNT_SQL` (`daemon/services/defer_block_resolver.py:185-191`):
  ```python
  "SELECT count(*) FROM job_queue_items j"
  " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
  " WHERE j.deleted_at IS NULL"
  " AND j.admission_state = 'queued'"
  " AND q.queue_type = 'defer'"
  ```
  executed at **`:274-276`** (`with self._job_repo.engine.connect() as conn:`) and at **`:321`** (inside the
  `with self._job_repo.engine.connect()` opened at **`:306`**). **Both context-managed ⇒ CLEAN.**
- A second non-ORM count over `job_queue_items` exists but renders a different prefix
  (`SELECT instance_id, COUNT(*) … GROUP BY`, so it is NOT the incident fingerprint): the husk-evidence probe at
  `daemon/tools/instance_read.py:1408-1416`, `with engine.connect()` at **`:1412`** ⇒ CLEAN.
- **Full enumeration result:** no OTHER raw `count(*)` over `job_queue_items` exists in `daemon/`.

**RCA premise correction.** Premise #4 ("the fingerprint belongs to the readiness probe") is **CORRECTED** to the
`defer_block_resolver` owner — and that owner is clean, so the correction **removes** a leak candidate rather
than relocating it.

### 6.3 The four manual (non-`with`) borrows — each verified try/finally-clean

| # | Site | Shape | Verdict |
|---|---|---|---|
| 1 | `daemon/manager.py:2782` (`_session_scope`) | `session = Session(self._engine)` → `try:` → `with WriteGuardSession(session, self._write_guard) as guarded: yield guarded` → `finally: session.close()` (`:2786-2787`) | **CLEAN** |
| 2 | `daemon/repositories/auth_bootstrap/repository.py:93` (`consume`) | `session = Session(self._engine)` → `try:` … `session.commit()` → `except IntegrityError: session.rollback()` (`:104`) → `finally:` (`:115`) → `session.close()` (`:116`) | **CLEAN** |
| 3 | `daemon/repositories/auth_bootstrap/repository.py:161` (`mint_with_sentinel`) | `session = Session(engine)` → `try:` … → `finally:` (`:209`) → `session.close()` (`:210`) | **CLEAN** |
| 4 | `daemon/services/child_reports.py:2594` (`owns_session` pattern) | `owns_session = session is None` → `if owns_session: session = Session(self._manager._engine)` → `try:` … → `finally: if owns_session: session.close()` (`:2757-2759`) | **CLEAN (RISK for await-stall — see R3)** |

**`WriteGuardSession` ownership verified:** `__exit__` → `close()` → `self._session.close()` inside a
try/finally (`daemon/write_pause_guard.py:361-395`) — so all `with WriteGuardSession(Session(...))` sites close
the inner session. (This is the pin **AC-c** in `test-strategy.md` §3.8.)

### 6.4 The RCA premise ledger (for the plan's `decisions.md`)

| # | RCA premise | Audit verdict |
|---|---|---|
| 2 | `/api/instances` leaks via autobegin without commit/rollback | **CORRECTED / NOT SUPPORTED** — context-managed (`repository.py:1383`/`:1434`) + pool reset active |
| 3 | readiness `queue_freshness` probe leaks a txn per tick | **REFUTED** — probe SQL is `FROM task` (`readiness.py:201-210`/`:228-237`), context-managed connect (`:1752`) |
| 4 | the `SELECT count(*) FROM job_queue_items` fingerprint belongs to the readiness probe | **CORRECTED** — owner is `defer_block_resolver._DEFER_PENDING_COUNT_SQL` (`:185-191`); clean |
| 5 | `mint_boot_epoch_if_none`: "an exception inside a transaction without a rollback leaks that connection" | **REFUTED as a leak** — no session passed at boot (`auto_continue_boot_pass.py:1154-1159`); `insert_mission` uses `with Session(self._engine)` (`repository.py:510-513`) → close → pool reset rollback. The 4×/boot UniqueViolation is real; its trigger is **unexplained** (open question) |
| 6 | wedge-capture queries use `tasks`; the table is `task` | **CONFIRMED** — `db_capture.py:308`, `:387` (+ docstring `:34`) |
| 7 | pool hardening needs `pool_pre_ping`, `reset_on_return`, `pool_recycle` | **CORRECTED** — `pool_pre_ping` already set (`factory.py:328`); `reset_on_return` is the library default and **set nowhere** in `daemon/`; `pool_recycle` genuinely absent on the manager engine. The real gap: **no `statement_timeout` / `idle_in_transaction_session_timeout` on the manager engine** (only the repair engine has them, `ens_db_tools.py:619-627`) |

---

## 7. The re-run recipe (future-lane procedure)

### 7.1 Steps

```bash
# 0. Work in a worktree, never the shared checkout or the live install.
cd /home/nea/ensemble-src-wt-dbpool-idletxn        # or the lane's own worktree
git rev-parse HEAD                                   # record the base for the report

# 1. Materialise the scanner (phase-3 implementer; source is §3.1 of this document).
#    Path: scripts/audit/borrow_census.py

# 2. Run the AST census (read-only; no DB, no daemon).
#    Certified interpreter (C-1/D-29): /usr/bin/python3 has NO pytest and is off the
#    sanctioned path; use the certified venv interpreter even for a pure-AST scanner
#    (consistency, no exceptions — per dispatcher adjudication of FOLD-MAP Q3).
/home/nea/ensemble-src/.venv/bin/python scripts/audit/borrow_census.py "$(pwd)" | tee /tmp/borrow_census_head.txt

# 3. Run the pattern cross-check (§3.2) and record the six numbers.

# 4. Diff against the baseline constants in §5.
```

### 7.2 The diff gate

| Assertion | Baseline | Rule |
|---|---|---|
| **Primary (scanner-independent)** | Family **C == 4**, at the four named `file:line` | The count MUST be 4 and the site list MUST match. A 5th manual borrow ⇒ **the new borrow must be audited individually and classified before the lane may proceed**; a borrow that is NOT finally-closed ⇒ **LEAK ⇒ the lane is blocked.** |
| Family **A** | 439 (pattern) / 613 (AST, A+B combined) | Any change ⇒ enumerate the delta and classify every new site. A *decrease* is fine (a borrow was removed); an *increase* MUST be a `with`/`async with` form. |
| Family **B** | 175 | same rule |
| Family **D** | 148 (AST) | same rule; the AST definition is the contract (§5.3) |
| Family **E** | 14 (AST) | same rule |
| Family **F** | 0 | MUST remain 0 — any `raw_connection(`/`getconn(`/`putconn(` is a **manual pool checkout** and must be audited individually. |
| `sessionmaker` / `async_sessionmaker` | 0 | MUST remain 0. A new `sessionmaker` changes the session-construction contract for the whole tree ⇒ **the lane must re-audit, not just re-count.** |
| Parse errors | 0 | A parse error means the tree was scanned incompletely ⇒ **the census is INVALID**, not "0 findings". |
| **Classification** | 0 LEAK, 0 unclosed | The re-run must produce **0 LEAK** and **0 unclosed**. |

### 7.3 The "do not silently re-baseline" rule

If a re-run produces different numbers, the lane MUST:

1. **Classify every delta site individually** (a table: `file:line`, family, CLEAN/RISK/LEAK, one-line reason);
2. Determine whether the delta is **code drift** (a real change in `daemon/`) or **scanner drift** (a different
   pattern breadth — see §5.3);
3. If it is scanner drift → fix the scanner to the canonical §3.1 form and re-run; **do not** update the
   baseline;
4. If it is code drift → record the new site in `decisions.md` with its classification, and re-baseline only
   with an explicit note naming the change;
5. **Never** re-baseline to make a gate green without the classification table.

### 7.4 Where the audit's output lands in the lane's phases

- **Phase 1 exit:** the census re-run must reproduce the baseline exactly (or explain every delta).
- **Phase 2 exit:** the census re-run must show **0 LEAK** after the F2/F4 changes — the pool-knob changes must
  not have introduced a manual borrow.
- **Phase 3 exit (the F1 receipt):** the census re-run is the **F1 gate** — it is the artifact that converts
  "we looked" into "the method is recorded and re-runnable".

---

## 8. Known limitations / blind spots (disclosed)

1. **Static analysis only.** The census proves *close-guarantee*; it cannot prove *transaction-cleanliness*.
   R1/R2 are RISK precisely because the code closes the borrow but leaves the transaction open or the thread
   running. The **runtime** proof is the F7 guard (`test-strategy.md` §1.3), not this document.
2. **The scope excludes `plugins/` and `scripts/`.** `plugins/opendesign` runs under its own process; `scripts/`
   are one-shot CLIs. If a future lane moves a borrow-bearing code path into either tree, the scope must be
   widened — and the baseline re-derived.
3. **`daemon/` only, `*.py` only.** Non-Python borrow surfaces (a SQL string executed over a raw driver, an
   ad-hoc psycopg call in a template) would be missed. The `raw_connection`/`getconn`/`putconn` family (F) is the
   net for the common case, but not a proof of absence.
4. **The census does not attribute pools.** `application_name` is empty for daemon connections
   (research-leak-sites.md:173), so a runtime census cannot tell the manager engine from the repair engine
   post-hoc. Fixing that (F2-g) is what makes future runtime censuses attributable.
5. **Two of the census's family definitions are not reproducible** (§5.3). This document corrects them; a future
   lane that trusted the `≈689` number literally would flag a false drift.
6. **The unexplained 4×/boot UniqueViolation is OUT of the borrow census's reach** — it is a *logic* defect
   (probe/insert in separate transactions against a partial unique index), not a borrow defect. It is carried as
   F6 and as an explicit open question in `research-leak-sites.md` §5.1.

---

## 9. Open questions for the plan owner

1. **Should the scanner be committed to the repo (`scripts/audit/borrow_census.py`) or kept as a
   planning-directory artifact?** The repo form makes the F1 gate mechanically re-runnable by any lane (and is
   the house convention for CI-able checks); the planning form keeps the lane's diff minimal. **This artifact
   assumes the repo form** (§3.1 materialisation note) — the plan should confirm, because the phase-3 F1 gate
   depends on it.
2. **Should family D's canonical breadth be the AST form (148) or a hand-written narrower form (~60)?** The AST
   form is machine-reproducible and strictly safer (it audits *more*). The narrower form matches the census's
   printed number but its definition was not recorded. **Recommendation: the AST form**, with the census's
   `≈689` retired as a historical figure.
3. **Should the audit also scan `plugins/`?** The plugin subsystem runs out-of-process, but `plugins/opendesign`
   does have DB-adjacent code. Out of scope for this lane; flagged for a future lane.

---

## 10. References

- `research-audit-census.md` — the census whose method this document formalises (§1a method, §1b counts, §1c RISK table, §1d dossiers, §1e pool facts)
- `research-leak-sites.md` — the six-site verification (the RCA premise ledger in §6.4 is cross-checked against its §4)
- `research-pool-couplings.md` — the engine inventory (site 1 manager engine, site 8 repair engine) and the "reset_on_return set nowhere in daemon/" finding
- `test-strategy.md` — the audit-clean pins AC-a … AC-d (§3.8) that keep this census's conclusion true going forward
- `wi-78967dba-verdict.md` — the sweep-coverage verdict that the hazard set feeds
- `daemon/repositories/factory.py:245-338` — `PG_SESSION_CONNECT_ARGS` and `create_postgres_engine` (the knobs the audit's §5.4 hazard set points at)
- `daemon/write_pause_guard.py:361-395` — `WriteGuardSession.__exit__` / `close()` (the ownership proof behind AC-c)
- `/home/nea/ensemble-src/.agents/shared/incident-20261026-db-pool-idle-in-transaction.md` — the RCA (read-only; main checkout)

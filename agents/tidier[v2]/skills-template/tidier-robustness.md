---
version: 1.0.0
category: execution
auto_load: false
---

# Robustness

Review for error-handling patterns. This skill covers **1 of the 6 v1
categories**: Error Handling.

You are the reviewer. You analyze code directly. You are a **READ-ONLY
reviewer** — DO NOT modify files, run mutating commands, or make commits.
Report findings only. The Tidier dispatcher aggregates your report into the
final severity-grouped Tidier review.

> **Aggregation of worker findings is a dispatcher responsibility** (see
> `workflow.md` step 6 and `tidier-strategy.md` Aggregation Strategy). This
> skill does NOT do aggregation — you report findings only.

---

## Read-Only Enforcement

You are a reviewer. Report findings — do not act on them. The Tidier
dispatcher decides what to fix.

**Prohibited actions:**
- `edit_file` / `write_file` / `apply_patch` — no source modifications
- `git commit` / `git push` / `git merge` / `git rebase` — no version-control mutations
- `db_conn_add` / `db_conn_delete` — no DB writes
- Skill updates that mutate the skill bank — analysis only
- Running build / install / deploy commands that change project state

**Allowed actions:**
- `read_file` / `glob` / `grep` — quick filesystem reads
- `bash` for read-only inspection (`ls`, `cat`, `wc`, `head`, `tail`, `git log`, `git diff`)
- `knowledge` / `explore` — project-state queries
- Tool calls that produce analysis output (no side effects)

If you discover a critical issue that MUST be fixed immediately, report it as
a 🔴 finding — do not attempt to fix it yourself.

---

## Pre-Execution Self-Check (Run Before Reviewing)

Before starting the review, verify ALL of the following. If any check fails,
clarify scope with the dispatcher before proceeding.

- [ ] **Target files identified** — exact paths or globs from the dispatch message
- [ ] **Scope locked** — review ONLY the files in the diff; do not expand scope
- [ ] **Category scope noted** — this skill = Error Handling ONLY (defer others)
- [ ] **Resource-leak surfaces identified** — file handles, DB connections,
      network sockets, locks, transactions
- [ ] **Language identified** — Python / JS-TS / SQL / General (apply matching
      error-handling conventions)
- [ ] **Project rules checked** — `.agents/tidier/rules/` (may override
      error-handling patterns)
- [ ] **Severity scale noted** — 🔴 High > 🟡 Medium > 🟢 Low

---

## Review Execution Contract

```
Task: Robustness (Error Handling) Review
Target: [files / globs from dispatch message]
Focus areas: [Error Handling ONLY — bare except, swallowed exceptions,
               None returns, inconsistent propagation, missing input
               validation, resource leaks]

CONSTRAINTS (do NOT violate):
- READ-ONLY: report findings only. Do NOT modify files, run mutating
  commands, or commit.
- SCOPE LOCKED: review ONLY the targets above. Do NOT expand scope.
- CATEGORY SCOPE: this skill covers ONLY Error Handling. If you spot
  a style / smells / readability / hygiene / types issue, report it as
  "consider deferring to <other skill>" — do NOT file it as your own
  finding.
- CITE FILE:LINE for every finding.
- RESOURCE LEAKS are part of this skill (sub-item of v1 Common Pitfalls).
  They are NOT Reviewer's domain — file them here as Error Handling.
- MARK UNCERTAIN findings as 🟢 Low with "consider" framing.
- DO NOT aggregate prior worker reports — the dispatcher does that.

REQUIREMENTS:
- Read all target files end-to-end.
- For each try/except block, verify: catch specificity, error context,
  resource cleanup, re-raise or return behavior.
- For each function that opens a resource (file, connection, transaction),
  verify: cleanup path (with / finally / context manager / explicit close).
- For each public API entry point, verify: input validation at boundaries.
- Produce the mandatory Finding Report (template below).
Deliver your full report as your FINAL message — the complete, detailed version. End your turn; do not add a follow-up summary, condensed re-report, todo update, or narration afterward.

RETURN:
- The Finding Report (template below).
```

---

## Focus Areas

Robustness review covers **error handling** and **resource management**. Each
has sub-checks; any sub-check failing is a candidate finding (severity depends
on the impact).

### Error Handling — Catch Specificity

- [ ] **Bare `except:` clauses** — catches EVERYTHING including `KeyboardInterrupt`
      and `SystemExit`. Always catch a specific exception type.
- [ ] **`except Exception:` too broad** — catches almost everything; consider
      the specific exceptions you expect.
- [ ] **Catching and re-raising as different type without context** —
      `except KeyError: raise ValueError("...")` — the original traceback is
      lost. Use `raise ValueError("...") from e` (Python) to chain.
- [ ] **Catching `BaseException`** — same risk as bare `except:`; usually wrong.
- [ ] **Empty catch in JS** — `try { ... } catch (e) {}` — silently swallows
      all errors. At minimum, log it.

### Error Handling — Swallowed Exceptions

- [ ] **`except: pass`** — silently drops the exception. Use logger or
      re-raise with context.
- [ ] **`except: return None`** — converts exception to `None`; callers can't
      distinguish "no result" from "failed".
- [ ] **Bare `except: return None`** — same problem; swallowed + nil-coalesced.
- [ ] **Async errors without await** — `async def f(): some_async_call()`
      without `await` — error is unhandled.

### Error Handling — None vs Raise

- [ ] **Returning `None` instead of raising** — when the function should
      signal failure, raise an exception. `None` should mean "no value", not
      "failed".
- [ ] **Returning `None` for unexpected input** — invalid argument should
      raise, not return `None`.
- [ ] **Optional/Optional[T] abuse** — using `Optional[T]` to represent
      errors instead of `Result[T, E]` or exceptions.

### Error Handling — Propagation Consistency

- [ ] **Inconsistent propagation** — some functions raise, some return `None`,
      some return default — pick a style and stick to it.
- [ ] **Wrapping low-level errors in domain-specific exceptions** — good,
      but verify the chain (`raise ... from e`).
- [ ] **Catching in the wrong layer** — swallowing at the leaf instead of
      letting it bubble to the layer that knows how to handle it.

### Error Handling — Input Validation

- [ ] **Missing input validation at boundaries** — public APIs, CLI args,
      file paths, env vars. Validate at the entry point.
- [ ] **Validation too deep** — re-validating in every function instead of
      trusting validated input.
- [ ] **Weak validation** — `if not value` for a value that could be `0`,
      `""`, `[]` legitimately.
- [ ] **Pydantic / dataclass validation unused** — for Python, prefer
      Pydantic or dataclasses with `__post_init__` over manual validation.

### Error Handling — Null / None Checks

- [ ] **Missing null-checks on external inputs** — external data (API responses, parsed JSON, user input) dereferenced without a null/None guard. Validate before accessing.
- [ ] **Missing null-checks on optional return values** — functions that may return `None`/`null` whose result is used immediately without a guard (e.g., `result.field` where `result` could be `None`).
- [ ] **Missing null-checks on nullable fields** — optional model/database fields, optional config values, or nullable dict keys (`dict.get()` result) accessed without checking for absence.
- [ ] **`None` dereference chains** — `obj.child.field` where any link could be `None`; prefer explicit guards or optional-chaining (`?.` in JS/TS).

### Resource Management — File Handles

- [ ] **Unclosed files** — `open(path)` without `with` block; or `with` block
      missing in a path that may raise.
- [ ] **Explicit `close()` without `finally`** — exception skips the close.
- [ ] **Mixing `with` and manual `close()`** — pick one (prefer `with`).
- [ ] **File handle in long-lived object** — opened on `__init__`, closed
      on `__del__` (unreliable; prefer explicit `close()` or context manager).
- [ ] **`open()` inside a loop** — opening without closing per iteration;
      use a single `with` block.

### Resource Management — Database Connections

- [ ] **Unclosed DB connections** — connection not released back to the pool.
- [ ] **Unclosed cursors** — cursor leaks; especially problematic with
      streaming results.
- [ ] **Missing transaction boundaries** — multi-statement writes without
      `BEGIN`/`COMMIT` or context manager.
- [ ] **No rollback on error** — `try: ... except: pass` inside a
      transaction leaves it open.
- [ ] **Connection pool exhaustion** — connections not returned to pool on
      error paths.

### Resource Management — Network / Sockets

- [ ] **Unclosed sockets** — `socket.socket()` without `with` block.
- [ ] **Unclosed HTTP connections** — `requests.Session()` not closed.
- [ ] **Connection leak in retry loop** — opening a new connection per retry
      without closing the previous one.

### Resource Management — Transactions

- [ ] **Uncommitted transactions** — `BEGIN` without matching `COMMIT`/
      `ROLLBACK`.
- [ ] **No savepoint for partial rollback** — single large transaction with
      a partial failure rolls back everything; consider savepoints.
- [ ] **Long-running transactions** — hold locks for too long; split into
      smaller transactions.

### Resource Management — Locks / Threads / Async

- [ ] **Locks not released** — `lock.acquire()` without matching `release()`
      in a `finally` or context manager.
- [ ] **Async resources not awaited for cleanup** — `async with` missing for
      async resources (aiohttp sessions, async DB connections).
- [ ] **`asyncio.Task` not awaited / cancelled** — fire-and-forget tasks that
      leak.

### Resource Management — Other

- [ ] **Tempfile not cleaned** — `tempfile.NamedTemporaryFile(delete=False)`
      without explicit cleanup.
- [ ] **Subprocess not waited** — `subprocess.Popen` without `wait()` or
      context manager; zombie processes.
- [ ] **Memory views / mmap not closed** — `mmap.mmap()` without matching
      `close()`.

---

## Mandatory Finding Report Format

Output the report in this exact shape:

```
## Finding Report: [Target]

### Findings
| # | Category | File:Line | Severity | Issue | Fix Suggestion |
|---|----------|-----------|----------|-------|----------------|
| 1 | Error Handling | path/to/file.py:42 | 🔴 High | [bare except / resource leak] | [concrete fix] |
| 2 | Error Handling | path/to/file.py:78 | 🟡 Medium | [swallowed exception / missing input validation] | [concrete fix] |

### Resource Leak Check
- [ ] All file handles use `with` blocks (or explicit close + finally)
- [ ] All DB connections / cursors are released on error paths
- [ ] All transactions have commit / rollback boundaries
- [ ] All locks / async resources use context managers
- [ ] All subprocesses / tempfiles are cleaned up

### Positive Observations
- [What's done well — credit good patterns explicitly]

### Out-of-Scope Observations (for dispatcher)
- [Style / smells / readability / hygiene / types issues spotted — let dispatcher route]

### Severity Summary
- 🔴 High: N
- 🟡 Medium: N
- 🟢 Low: N
```

### Severity Calibration (Robustness)

| Issue Type | Typical Severity |
|---|---|
| Resource leak (file handle, DB connection) | 🔴 High |
| Bare `except:` / `except Exception:` in critical path | 🔴 High |
| Swallowed exception in error-handling infrastructure | 🔴 High |
| Missing input validation at public API boundary | 🟡 Medium |
| Inconsistent error propagation style | 🟡 Medium |
| Returning `None` instead of raising for failure | 🟡 Medium |
| `except: pass` in non-critical path | 🟡 Medium |
| `cast()` / `# type: ignore` to silence type errors | 🟡 Medium |
| Style preference on `raise from` vs `raise` | 🟢 Low |
| Defensive validation deeper than necessary | 🟢 Low |

(See `tidier-strategy.md` for the full severity guidelines.)

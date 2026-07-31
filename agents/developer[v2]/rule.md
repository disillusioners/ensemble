# Rules

## Dispatch Conduct

1. **ALWAYS dispatch coding work. NEVER write code directly.**
2. **Select correct tier** — coder for complex/multi-file, worker for quick/skill-based.
3. **One skill per worker dispatch** — clean attribution. Each worker loads exactly ONE skill via `load_skill`. Skill evolution data depends on this.
4. **End turn after dispatching** — instances report back **asynchronously** as new messages. Do NOT poll, sleep, or `bash` while waiting. Holding the turn open blocks report delivery.
5. **Aggregate before reporting** — combine all instance results into one structured Dev Report. Never stream partial reports.

---

## Tier Selection

6. **Use coder when** — multi-file changes, architectural change, new feature, complex bug, >2h estimated work.
7. **Use worker + skill when** — single-file fix, refactor, commit, review, <2h estimated work, and a matching skill exists.
8. **Use worker WITHOUT skill when** — no matching skill exists, or task is general/unknown (provide detailed request).
9. **Do NOT mix tiers within one logical task** — pick the right tier up front. If a quick worker task expands mid-flight, do not "promote" it to coder; instead, spawn a fresh coder for the expanded scope.
10. **If scope grows during execution, escalate** — spawn a coder to take over the expanded work; do not stretch a worker beyond its tier.

---

## Verification Discipline

11. **Do NOT fully trust coder/worker output.**
12. **For complex changes (coder)**, spawn a SEPARATE coder or worker to verify. Independent verification catches regressions the original instance missed.
13. **For quick changes (worker)**, verify by checking `git diff` or spawning a review worker with `code-review` skill (`code-review` is owned by the reviewer agent and loaded globally from the project skill bank — no local template in developer[v2]'s skill-set.yaml required).
14. **Report verification results explicitly in the Dev Report** — what was checked, what was found, who did the check.
15. **If verification finds issues, spawn another instance to fix** — iterate until clean. Do not declare "done" on unverified work.

---

## Parallelism

16. **Parallelize independent tasks** — up to **3 concurrent instances** per dispatch cycle (WorkerPool alignment).
17. **Partition by module/file** — for independent changes (different modules, different files), dispatch in parallel.
18. **Do NOT parallelize dependent changes** — same file, same module, or chained logic → sequential. Race conditions on overlapping writes produce broken code.
19. **Use `todo_graph` for fan-in tracking** when dispatching 2+ instances. Create nodes before dispatch; mark `done` as reports arrive; aggregate only when all nodes are done.
20. **Deduplicate if multiple instances would touch overlapping areas** — split the scope so each instance owns disjoint files. If overlap is unavoidable, dispatch sequentially.

---

## Direct Tool Discipline

21. **Developer may use filesystem/bash for QUICK LOOKUPS only** — confirm a file exists, check project type, read plan files, check `git status`. These are read-only inspections.
22. **Do NOT write code, edit files, or run builds directly** — always dispatch to coder or worker. The moment a "quick tweak" feels tempting, dispatch instead.
23. **Git operations via bash for status checks only** — `git status`, `git log`, `git diff`, `git branch`. **Commits go through worker with `git-commit` skill** — never commit directly as Developer.

---

## Never

24. **Never write or modify project source code directly.**
25. **Never run builds/tests/linters directly** — dispatch to coder or worker.
26. **Never skip verification for complex changes.**
27. **Never blindly trust coder/worker output without checking.**
# Subset-extrapolation trap + httpx shared-process pollution class — 2026-08-28

## 1. A clean SUBSET run does not certify a FULL sweep (baseline extrapolation trap)

At the injection-marker gate I proved the `_RealLangGraph` pollution fixed via a 7-file discrimination run (integration file + the 6 httpx-affected files, one process → clean) and recorded "108 httpx errors → 0 post-fix". One gate later, the FULL 328-file subdirs sweep showed the httpx errors again (108). Worktree A/B at base proved the class PRE-EXISTING (opencode ×43+5 and vscode ×8×2 reproduced EXACTLY at base): the 7-file subset was clean because the polluter is NOT in that subset — full-sweep ordering pulls in a different, older polluter.

**Rule: never record a "noise class eliminated" baseline from a subset run.** Subset runs prove pairwise causation only. Full-partition claims need full-partition A/B at the comparison commit. Record subset evidence as "pair X↔Y clean", not "class absent".

## 2. httpx `TypeError: object.__new__()` = shared-process state, NOT an httpx version problem

Recurring signature across gates: `async with httpx.AsyncClient(...)` → `TypeError: object.__new__() takes exactly one argument`. Every affected file passes 48/48, 37/37, 13/13, 9/9, 8/8, 4/4 in isolation at every commit tested. Error-vs-failure-vs-pass counts CHURN between full-sweep runs (79E/67F at base vs 108E/48F at branch) — classic order/state-dependent pollution, not deterministic breakage.

**Triage recipe (when a sweep shows these):** (1) isolation-run one affected file — pass ⇒ shared-process class; (2) do NOT touch the venv or pin httpx; (3) worktree A/B the full partition to attribute base-vs-branch; (4) pair-bisect the polluter (candidate order: files running before the first erroring file; opencode/test_client is a stable victim for bisection anchoring); (5) QUARANTINE as env-family so gates classify on sight.

## 3. job_continue un-quarantine protocol executed end-to-end (reference)

Quarantined 2026-08-20 (KeyError 'instance_id', pre-existing on 39f76dc7). Dev's has_instance_busy fixture fix landed later; green runs accumulated across gates (quick-wins run 1, injection-marker sweep run 2, subtree-status standalone + pack runs 3-4). Un-quarantine = (a) tester moves QUARANTINE rows → Resolved with run history; (b) worker removes the 4 `--deselect` lines from the pack script, verifies 80/80 twice, commits (d663ec9a). Pack baseline shifts 76P/4-des → 80P/0-des — future gates must use the new baseline.

## 4. Meta: id-typo dispatch failures leave silent idle workers

Two `send_message` calls failed on instance-id typos mid-batch ("not found — did you mean …") and were easy to miss among 15+ queued sends; the workers sat idle for a full wave. When batching many dispatches, grep the send results for `ERROR: instance` and re-send immediately — an idle worker manifests as a permanently-pending todo node, not an error report.

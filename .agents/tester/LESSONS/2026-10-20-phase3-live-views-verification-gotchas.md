# Phase-3 live-views verification gotchas (2026-10-20)

Lanes: L0 (static forensics) · L1 (`view_write` real e2e) · L2 (board real e2e) · L3 (suites/pins/baseline).
Full evidence: `RESULTS/2026-10-20-ari-tools-phase3-live-views-verification.md`.

1. **The exact-set pin set is SEVEN files, not five.** P1 added `tests/unit/test_board_registration.py`,
   P2 added `tests/unit/test_self_alarm_registration.py`. The tree's SAME-PR comment was corrected in
   the same commit; only older `.agents/tester/` prose says "five". Derive the set with
   `grep -rn "PRIVILEGED_TOOL_CATEGORIES ==" tests/` — do not trust prose.
2. **The addopts silent-deselection trap reproduced BOTH ways.** Default addopts on the 7 pin files:
   `128 passed, 9 deselected`, exit 0 — the whole integration-marked file vanishes while the summary
   stays green. The `deselected` token appears ONLY when non-zero; an absent token is NOT "nothing
   was deselected". Every "all pins green" claim must name its invocation.
3. **`import daemon.api` is impossible in an in-proc harness** — module-level `app = create_app()`
   reads ambient env. To exercise `inject_reports_live_view_root` byte-true, AST-extract the
   function from source and `exec` it — never re-implement it.
4. **Base-leg runs MUST assert import resolution.** The worktree venv carries
   `_editable_impl_ensemble.pth` → the worktree; a naive base leg imports HEAD code under a
   base-looking cwd. Assert `daemon.__file__` (or the module's path) resolves into the base tree
   before trusting any base result. (`git archive <base> | tar -x -C $(mktemp -d)` + `PYTHONPATH`
   avoids all repo mutation.)
5. **The literal frozen-seam command lies.** `git diff -G 'def list_items|def get_item'
   99a28e4f3..a2c33c20` is NON-empty because the phase-2 docs quote that exact command. Always
   scope it: `-- daemon/` (or the three board files). The scoped diff is the proof.
6. **`test_self_alarm_tools.py` is a wall-clock time-bomb (pre-existing).** It hard-codes
   `daily 23:50` (Asia/Ho_Chi_Minh); the 30-min floor on the absolute first-fire instant refuses
   anything requested between ~23:20 and 23:50 local ⇒ 2 deterministic reds in that window. Phase-2's
   "PASS 146/146" was recorded at a favorable hour. Fix = test-debt commission (relative request or
   far-off time-of-day). Symptom signature: `IndexError: list index out of range` +
   `AssertionError: assert isinstance('ERROR: self_alarm requires a minimum horizon of 30 minutes
   — the next daily 23:50 fire is Xm away…', dict)`.
7. **The board passcode row `(board, index.html)` is a GLOBAL singleton.** Parallel harnesses must
   each use their own Fernet key and rotate via `board_link(refresh=True)` — or they fight over one
   row's generation/lockout state.
8. **`is_well_formed_rel_path` validates the POST-DECODE form by design** (its own comment says
   non-decoding callers MUST apply the check to the encoded form). A tool entry point that applies
   it un-decoded accepts `%2e%2e%2f…` — contained, but unservable (the URL layer decodes; the serve
   validator rejects) ⇒ orphan row + lying envelope. This is defect D1. Any future path-taking tool
   must reject `%` or decode-then-revalidate.
9. **Measure board bytes, not rows.** 50 CJK-heavy rows ⇒ ~1.06 MiB; ~180 KB bodies ⇒ the 2 MiB
   render ceiling fires a VISIBLE truncation notice ("Output truncated at the 2 MiB render ceiling")
   and 31 of 50 rows render. The ceiling is real and user-visible — assert on actual response bytes.
10. **Name the scope when citing SQL budgets.** Full authenticated board GET = 6 statements
    (1 passcode gate + 3 board fetch + 2 filter-options). The ≤3 bar is the ADAPTER fetch; the
    in-repo pins are ≤4 (adapter) and ≤8 (public render). Citing "≤4" without the scope invites a
    false FAIL.
11. **(Carried) vacuous-counter discipline held:** every zero-side-effect claim in this commission
    carried a gate-neutralized positive control (the counters were proven able to go red). Keep doing
    this — it caught a false PASS in phase 2 and prevents the same class here.
12. **(Environment) the worktree `.env` dev-fence PG password is stale** (rejected by local PG16);
    unix-socket peer auth works and is strictly more restrictive. Reconcile the `.env` separately.

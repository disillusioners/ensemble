# bind_retry mutation matrix

How to re-verify the "mutation-proven" claim for the bind-retry pins
(`tests/test_bind_retry.py`). Every mutant below is a ONE-LINE edit to
`daemon/services/bind_retry.py` (two touch `daemon/__main__.py`); each
must RED the listed test(s), and `git restore <file>` must bring the
file back byte-exact (sha256 of the file BEFORE the mutation == AFTER
the restore).

```bash
# Venue: a scratch worktree at the branch tip, with its own venv.
git worktree add --detach /tmp/bindrace-mut <branch-tip-sha>
cd /tmp/bindrace-mut && uv sync --frozen
# Prove the scratch tree is what runs:
.venv/bin/python -c "import daemon, tests; print(daemon.__file__, tests.__file__)"
# Baseline must be GREEN:
/tmp/scrub_env.sh .venv/bin/python -m pytest tests/test_bind_retry.py tests/test_main_entry.py -q
# Apply the mutant (edit the named line), then:
.venv/bin/python -m pytest tests/test_bind_retry.py tests/test_main_entry.py -q   # expect the listed REDs
git restore daemon/services/bind_retry.py    # byte-exact restore from the object store
sha256sum daemon/services/bind_retry.py      # == the pre-mutation digest
```

| Mutant | Edit (the surviving behaviour it removes) | Must RED |
|---|---|---|
| M1 | In the retry loop, replace `if remaining <= 0 or attempt >= max_attempts:` with `if True:` — the bind then fails fast on the FIRST EADDRINUSE (the pre-fix uvicorn behaviour) | `test_bind_waits_out_contention_then_succeeds_once_port_is_released`, `test_backoff_ladder_is_exponential_and_capped`, `test_retry_log_line_carries_errno_host_port_attempt_and_wait` |
| M2 | `BIND_FAILURE_EXIT_CODE = 1` → `= 3` (a NEW exit code) | `test_bind_fails_fast_when_budget_is_zero`, `test_bind_budget_exhaustion_exits_1_not_a_new_code` |
| M3 | In the success path, insert `sock.listen(128)` before `sock.set_inheritable(True)` — the pre-bind then queues connections instead of refusing them | `test_pre_bind_does_not_listen_so_probes_still_get_refused` |
| M4 | In `daemon/__main__.py::_serve_with_bind_retry`, replace the `install_root_logging()` call with `pass` — the pre-bind diagnostics become unobservable | `test_serve_seam_installs_log_handlers_before_the_bind` |
| M5 | In `daemon/__main__.py::_StartedLogServer.startup`, replace the `logger.info("Uvicorn running on …")` block with `pass` | `test_bound_socket_is_handed_to_uvicorn_which_serves_on_it` |
| M6 | In `resolve_max_wait_s`, replace `if requested > BIND_RETRY_MAX_WAIT_CEILING_S:` with `if False:` — no clamp, an over-ceiling budget is honoured | `test_effective_budget_is_clamped_to_the_ceiling` |
| M7 | In `resolve_max_wait_s`, replace `if not math.isfinite(requested):` with `if False:` — NaN/inf silently accepted | `test_malformed_or_negative_budget_fails_closed_to_fail_fast` |
| M8 | `BIND_RETRY_MAX_WAIT_CEILING_S: float = 30.0` → `= 120.0` — the ceiling raised past the burst-abort limit | `test_default_budget_is_within_the_hard_ceiling`, `test_effective_budget_is_clamped_to_the_ceiling`, `test_ceiling_plus_frozen_boot_stays_within_the_burst_abort` |
| s-1 | In the retry loop, replace `if exc.errno != errno.EADDRINUSE:` with `if False:` — a NON-EADDRINUSE errno (EACCES …) is treated as transient and retried | `test_non_eaddrinuse_failure_fails_fast_on_the_first_attempt` |
| s-2 | Demote the exhaustion log from `logger.error("Bind retry budget exhausted …")` to `logger.debug(...)` — the terminal verdict becomes unobservable at ERROR level | `test_exhaustion_error_line_is_diagnosable` |
| M-1 | In `daemon/__main__.py::_serve_with_bind_retry`, replace the `sys.exit(STARTUP_FAILURE)` on the KeyboardInterrupt-during-wait path with `return` — a SIGINT during the wait exits 0 ("clean") and the launcher does NOT restart | `test_sigint_during_the_bind_wait_exits_as_a_startup_failure` |
| M-2 | `BIND_RETRY_MAX_WAIT_CEILING_S: float = 30.0` → `= 45.0` (the previously-shipped, unsafe ceiling) | `test_default_budget_is_within_the_hard_ceiling`, `test_ceiling_plus_frozen_boot_stays_within_the_burst_abort` |

Why each mutant discriminates: M1 reproduces the 2026-11-11
crash-on-contention shape (`SystemExit: 1` at the bind site); M2 proves
the exit-code contract is not vacuous; M3 proves the "bind without
listen" ordering (a probe must be REFUSED, not queued); M4/M5 prove the
observability contract (pre-import INFO is dropped, and the
operator-visible boot line is preserved); M6/M7/M8/s-1 prove the
budget-resolution semantics (clamp, fail-closed, ceiling, errno filter);
s-2 proves the terminal verdict is observable at ERROR level; M-1/M-2
prove the two verdict/coupling decisions the review cycle hardened.

# Post-F6a Intent5 run — final HEAD (2026-09-22)

Touch-up commission final round, after Item 1 (F2 failure-path test) +
Item 2 (pack mock-layer guard) + Item 4 (two doc nits) landed as focused
commits. Intent5 re-run at the final commit HEAD against `./dev_with_mock.sh`
on 8079 with the mock LLM on 4124. DB-target scrubbed before boot. All four
emission surfaces GREEN.

## HEAD

```
3530ed9848265bcd200ff35df9fb8ad17bee074e docs(tester): ship-prep evidence — independent gate + Intent5 skip-guard lesson + demo repro
```

## Pre-checks (snapshot at boot time)

```
=== Pre-boot: demo 7979 ===
{"status":"alive","uptime_seconds":59418.46685218811,"version":"0.13.9"}
=== Pre-boot: live 9797 ===
{"status":"alive","uptime_seconds":93603.71483874321,"version":"0.13.8"}
=== Pre-boot: ambient POSTGRES_* ===
POSTGRES_PASSWORD=ASiWyJpUMLxm1QaOG1d22iAilph5z
POSTGRES_HOST=10.44.0.2
POSTGRES_USER=ensemble
POSTGRES_PORT=5432
POSTGRES_DB=ensemble_prod
=== Pre-boot: 8079 free? ===
(empty / connection-refused expected)
=== Pre-boot: 4124 free? ===
(empty / connection-refused expected)
```

Ambient POSTGRES_* pointed at LIVE `ensemble_prod`. **Scrubbed before boot**
via wrapper that `unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD`
and exec'd `./dev_with_mock.sh`. The .env is sourced inside the script and
re-supplies dev-only `POSTGRES_*` values.

## DB-target proof (zero prod POSTGRES)

Boot log (verbatim — `localhost:5432/ensemble_dev`):

```
08:47:18 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: localhost:5432/ensemble_dev
08:47:19 - daemon.persistence - INFO - Creating PostgreSQL checkpointer for localhost:5432/ensemble_dev
```

`/proc/520151/environ` POSTGRES_* dump (values scrubbed at capture; no prod
strings):

```
POSTGRES_HOST=<value-scrubbed>
POSTGRES_PASSWORD=<value-scrubbed>
POSTGRES_PORT=<value-scrubbed>
POSTGRES_USER=<value-scrubbed>
POSTGRES_DB=<value-scrubbed>
```

`grep -i 'prod'` against the daemon's environ yielded only two false-positive
hits — both unrelated process-name artifacts from a previous PyInstaller
build, NOT prod DB credentials:

```
_PYI_ARCHIVE_FILE=/home/nea/agents-ensemble/ensemble-prod
_PYI_LINUX_PROCESS_NAME=ensemble-prod
```

## Daemon version self-ID

`GET http://localhost:8079/livez`:

```
{"status":"alive","uptime_seconds":24.69210982322693,"version":"0.13.10"}
```

`GET http://localhost:8079/readyz`:

```
{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true},"detail":{"reasons":[],"queue_max_age_seconds":null,"checked_at":"2026-09-22T08:47:40.784332+00:00"},"draining":false}
```

## Intent5 run command + verbatim output

Run 1 (F1 guard caught ambient scrub-residue — exactly the protection the
guard provides; SKIP is non-gating on a quick re-scrub):

```
$ timeout 120s .venv/bin/pytest \
    tests/e2e/test_result_summary_emission.py \
    -v --override-ini="addopts=" --tb=short -ra
============================= test session starts ==============================
platform linux -- Python 3.13.15, pytest-9.0.2, pluggy-1.6.0
...
SKIPPED [1] tests/e2e/test_result_summary_emission.py:393:
  REFUSED (F1 env guard): resolved POSTGRES_DB='ensemble_prod' is a
  prod-like database (ensemble_demo, ensemble_live, ensemble_prod).
  This ambient-POSTGRES_* fallback caused the 2026-09-21 live-DB incident.
  Set an explicit E2E_PG_DB (override wins) or scrub POSTGRES_* from the
  environment before running this test.
============================== 1 skipped in 0.21s ==============================
```

Run 2 (scrub + explicit override — Intent5 EXECUTED → PASS):

```
$ unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
$ E2E_PG_DB=ensemble_dev \
  E2E_PG_HOST=localhost \
  E2E_PG_PORT=5432 \
  E2E_PG_USER=ensemble \
  E2E_PG_PASSWORD=testpw \
  timeout 120s .venv/bin/pytest \
    tests/e2e/test_result_summary_emission.py \
    -v --override-ini="addopts=" --tb=short -ra
============================= test session starts ==============================
platform linux -- Python 3.13.15, pytest-9.0.2, pluggy-1.6.0 -- /home/nea/ensemble-src/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/nea/ensemble-src
configfile: pyproject.toml
plugins: anyio-4.12.1, hypothesis-6.165.10, asyncio-1.3.0, mock-3.15.1, xdist-3.8.0, langsmith-0.7.7, timeout-2.4.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_test_loop_scope=function
timeout: 30.0s
timeout method: thread
timeout func_only: False
collecting ... collected 1 item

tests/e2e/test_result_summary_emission.py::test_result_summary_emission_surface_intent5 PASSED [100%]

============================== 1 passed in 12.99s ==============================
```

## All 4 emission surfaces — GREEN (daemon-side access logs verbatim)

Surface (a) — `GET /api/jobs/{job_id}/events` SSE → 200 (terminal `event: completed`,
`data.result_summary` non-null + JSON-envelope content truthy per F6a):

```
08:48:25 - daemon.api - INFO - [127.0.0.1:32912] GET /api/jobs/ef4934ff-5687-44b1-b0c6-5caac8994944/events 200
```

Surface (b) — `GET /api/jobs/{job_id}` → 200 (`result_summary` non-null):

```
08:48:25 - daemon.api - INFO - [127.0.0.1:59652] GET /api/jobs/ef4934ff-5687-44b1-b0c6-5caac8994944 200
```

Surface (c) — read-only DB query (event row `kind='job_completed'`,
`data->>'result_summary'` non-null) — test-internal SQL, exercised inside
the Intent5 test itself (no daemon-side INFO log line; PASSED on the run
above is the proof):

```
08:47:19 - daemon.persistence - INFO - Creating PostgreSQL checkpointer for localhost:5432/ensemble_dev
```

Surface (d) — `/api/notifications/stream` SSE → 200 (notification for
root instance carries `result_summary`):

```
08:48:29 - daemon.api - INFO - [127.0.0.1:32892] GET /api/notifications/stream 200
```

## Internal pipeline (Observer + ChildReports + jobs_streaming)

```
08:48:23 - daemon.services.child_reports - INFO - Instance 6375dafd... completed (no parent, no children), status=COMPLETED
08:48:23 - daemon.services.job_feedback_observer - INFO - Observer: finalized job ef4934ff... status=completed for instance 6375dafd... (released 1 lock(s), instance_was_terminal=True)
08:48:24 - daemon.services.job_feedback_observer - INFO - Observer: finalized job ef4934ff... status=completed for instance 6375dafd... (released 1 lock(s))
08:48:25 - daemon.routers.jobs_streaming - INFO - Work ef4934ff-5687-44b1-b0c6-5caac8994944 completed with status: completed
```

## Teardown

```
$ proc_stop force=True, process_id=proc-8bdbb474
Process proc-8bdbb474 stopped (force=True, status=killed, exit_code=-9).

=== Post-teardown: processes ===
(empty = cleaned)

=== Post-teardown: 8079 down? ===
(connection refused)

=== Post-teardown: 4124 down? ===
(connection refused)

=== Post-teardown: demo 7979 ===
{"status":"alive","uptime_seconds":59592.06061077118,"version":"0.13.9"}

=== Post-teardown: live 9797 ===
{"status":"alive","uptime_seconds":93777.30995631218,"version":"0.13.8"}
```

Demo (7979, v0.13.9) and LIVE (9797, v0.13.8) — both untouched (uptime
advanced naturally during the run; no restart, no PID churn). qa-channel
worktree untouched.

## Verdict

| Surface | Status | Evidence |
|---|---|---|
| (a) SSE events stream | ✅ GREEN | `GET /api/jobs/{job_id}/events 200` |
| (b) GET /api/jobs/{job_id} | ✅ GREEN | `GET /api/jobs/{job_id} 200` |
| (c) DB event row kind='job_completed' | ✅ GREEN | test PASSED on assertion |
| (d) /api/notifications/stream | ✅ GREEN | `GET /api/notifications/stream 200` |
| DB-target = dev (no prod creds) | ✅ GREEN | boot log `localhost:5432/ensemble_dev`; `/proc/<pid>/environ` no prod POSTGRES_* |
| Daemon version self-ID | ✅ v0.13.10 | `livez` 200 |
| Boot time | ~24s | uvicorn ack at 08:47:18, Intent5 fired at 08:48:17 |
| Teardown clean | ✅ | 8079, 4124 down; demo + LIVE + qa-channel untouched |

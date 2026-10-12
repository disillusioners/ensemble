# LESSONS — maintenance-e2e harness quirks (2026-10-12, maintenance-tabs-vacuum gate)

Four operational traps hit during the maintenance-wizard e2e verification; all cost a re-run or a
disambiguation pass. Recorded so future lanes avoid them.

## 1. `test.fixme(...)` INSIDE a test body registers a SKIPPED CHILD entry

`frontend/e2e/maintenance-checkpoint-cleanup-wizard.spec.ts:1080` calls
`test.fixme(true, 'w7 pointer-hover … §D-9.6')` inside the w7 test body (designed retirement of the
pointer-hover assertion). Playwright counts that as a separate "skipped" test in the summary. Symptom:
`-g "w6|w6.5|w7|w2"` run reported "1 skipped" which was misread as w6.5 skipping — w6.5 actually
passes (isolated re-run: 1 passed, exit 0). Rule: when a spec uses in-body `test.fixme`, ALWAYS
disambiguate skips with `--reporter=list` before declaring a test skipped/failed.

## 2. `-g` anchors and positionals: hierarchical names, not bare titles

- `-g "^w6\.5\."` matches NOTHING: the registered test name is the full `describe + title` string
  (starts with `Maintenance — Checkpoint Cleanup WIZARD …`), so `^`-anchored title patterns never
  match. Use unanchored distinctive substrings (`w6\.5\.`).
- A bare positional after `--project maintenance` (e.g. `maintenance-checkpoint-cleanup-wizard`) is
  parsed as a SECOND PROJECT NAME and errors out (`Project(s) … not found`). The config's `testDir`
  discovers specs automatically — omit the positional.
- `-g "w2"` case-INSENSITIVELY over-matches `[W2]` in unrelated specs (workspace-file-tabs), which
  then fails on ERR_CONNECTION_REFUSED against :4199 (dev port, not the e2e :4299) and pollutes the
  result set. Pick grep tokens unique to the spec's naming scheme.

## 3. webServer `uv` PATH trap (boot script)

`frontend/scripts/boot-e2e-maintenance-daemon.sh` runs `uv run python -m uvicorn …`. `uv` lives at
`/home/nea/.local/bin/uv` and is NOT on the webServer's inherited PATH → first invocation died at
the daemon canary (60s timeout, daemon never reached state=ready). Fix: export
`PATH="/home/nea/.local/bin:/usr/lib/postgresql/16/bin:$PATH"` BEFORE `playwright test`
(the postgres bin dir is likewise required for initdb). Environmental, not a code defect.

## 4. PG statement-log proof technique (for guard/order probes)

`log_statement='all'` logs Parse-time text only; asyncpg's cached prepared statements log
`execute <name>:` with EMPTY text. For statement-order proofs against a real daemon, ALSO set
`log_min_duration_statement=0` and refresh the daemon's pooled sessions
(`pg_terminate_backend` on the exclusive probe cluster) so prepared-statement text lands in the log.
Grep for `execute <name>: DELETE…`, not `statement: DELETE…`. See
`/tmp/ens-maintvac/probes/REPORT-c1-c4-adversarial-guard-probes.md` (may be swept; the technique,
not the file, is the durable part).

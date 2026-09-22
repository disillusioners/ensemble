# LESSONS — Midflight-QA-Channel Acceptance Gate (2026-09-21)

Branch `feature/midflight-qa-channel` @ `1222d0d7` (base `246b7325`). Full report: `RESULTS/2026-09-21-midflight-qa-channel-acceptance-gate.md`. Verdict PASS_WITH_EXCEPTIONS, zero new failures vs base.

## 1. AsyncMock kwarg-capture illusion (delegated-coverage adjudication)
Design §9.1(b) delegated the "real PAUSED→RUNNING resume chain" to `test_answer_gate_resume_chain.py` + a "tester-phase integration check". Adjudication found the unit file mocks the ENTIRE manager (`MagicMock()` + `AsyncMock(resume_processing_job/resume_instance_cascade)`); answer-content "delivery" assertions were **captures of AsyncMock kwargs** — they look like content-delivery proofs but prove nothing reaches the graph. Pattern that closed it: classify each seam real-vs-mocked (Q1/Q2/Q3 table), then author a real-chain test mocking ONLY the outermost graph/LLM boundary and asserting on the captured boundary `message` arg (echo header + answer text + question echo + handle work_id). Reusable template for any "delegated coverage" note in future designs — the delegation claim must be verified, not trusted.

## 2. Live-PG hazard is REAL on this host — scrub set confirmed again
Worker shells on this machine carry ambient `POSTGRES_HOST=10.44.0.2`, `POSTGRES_DB=ensemble_prod` (+user/port/password). Every subprocess needs the 7-var scrub: `env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL`. Three independent workers re-confirmed the hazard this gate. Never relax this even for "throwaway" runs.

## 3. jq full-dir A/B fits the 5-min cap — with ~50s headroom
tests/job_queue/ full serial: 243–247s per leg at 1867–1872 tests (SQLite fixtures; pytest startup ~4s dominates small packs). Headroom to 300s is ~50s; if the dir grows ~15% more, split by file-prefix BEFORE it breaches. Delta arithmetic (base 1776P + 44 new green = feature 1820P) is a cheap consistency check that catches silent collection differences (e.g., an untracked mid-flight-authored test file landing in the dir — ours added +2, accounted).

## 4. Untracked in-flight test files contaminate A/B collection
A sibling-authored untracked test (`test_answer_resume_real_chain.py`) landed in tests/job_queue/ while the A/B legs ran. Do NOT inject `--ignore` (breaks byte-identical invocation); instead identify its node IDs and exclude them in the aggregation DIFF with an explicit note. Authoritative result for such files comes from their own worker's run, not the directory sweep.

## 5. Census/meta tests fail by content-drift, not regression
`test_terminal_write_census` red at base AND feature (node-state pre-existing) but the feature signature names a possibly-new unlisted site (`manager.py:5047`). Adjudicate on node-state (fails@base + fails@feature = pre-existing), report signature drift as test-debt follow-up — do not convert content drift inside an already-red census into a branch regression claim without a base-leg content comparison.

## 6. Quarantine families: match by signature, not line anchors
`injection_api` MagicMock-await family: QUARANTINE row cites `messages.py:258`, live failure at `:325` after refactors — same root chain. Rows explicitly anticipate drift; membership = class + exception text.

## 7. Watchover 47-node quarantine family appears HEALED at 1222d0d7
All 328 watchover tests green this gate. Candidate for un-quarantine: run the family 3× clean (flaky-test-management workflow) then resolve the row.

## 8. Boot-gate adjudication pattern (reusable)
When a pre-existing PG-only migration blocks SQLite boot: prove (i) migration file blob-identical at base, (ii) `git diff base..HEAD -- daemon/migrations/` empty, (iii) no diff-touched module in the boot traceback, then adjudicate via boot_probes pack + full import sweep of diff-touched modules. Also: `dev.sh` reloader parent lingers after app-startup failure — `timeout 90` reaps it (exit 124 is the wrapper, not the app).

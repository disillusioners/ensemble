# LLM Request-Gzip Merge Gate — Mock Fidelity & Pack Lessons (2026-08-30)

Gate: `feature/llm-request-gzip` @ `459557b2` (branch tip = latest; feature uncommitted in working tree).
Verdict record: see `RESULTS/2026-08-30-llm-request-gzip-merge-gate.md`.

## L1 — MockTransport placement determines gzip-test validity (the general rule)

For httpx **transport-wrapper** features (gzip wraps the real transport), mock fidelity has exactly three placements:

1. **Mock INSIDE the wrapper** (`GzipRequestTransport(httpx.MockTransport(handler))`) — VALID: wrapper mutates the request before the mock sees it; header/body assertions reflect post-compression state.
2. **Mock OUTSIDE the wrapper** (replacing the whole transport stack) — **VACUOUS**: compression layer never executes; test passes while wire is broken.
3. **Real transport inside wrapper against a real loopback socket** — GOLD: server-received bytes are ground truth.

The feature suite (26 tests) used (1) + (3) with 8 real-socket tests; zero vacuous tests found by audit.
Rule of thumb for future transport-layer features: every ON-path and OFF-path seam needs at least one (3)-class test per construction seam.

## L2 — The `_content` vs `stream` split bug class + the red-green contract pattern

`httpx.Request.content` reads `_content`; MockTransport handlers read the same — so a wrapper that updates `request._content` but forgets `request.stream = ByteStream(compressed)` PASSES all MockTransport tests and breaks the real wire (`h11._util.LocalProtocolError: Too much data for declared Content-Length`).

The feature author embedded a **red-green contract** in the test-file docstring (test_llm_request_gzip.py:932-937): disabling only the `request.stream = ...` line makes ALL real-socket tests fail. This is a cheap, high-value pattern — require it for any test suite guarding serialization invariants MockTransport cannot see.

## L3 — Inventory path reports must be `ls`-verified before pack creation

The inventory worker reported skill-service suites as `tests/unit/test_skill_*.py`; the files actually live in `tests/services/`. First pack run died on pytest exit 4 (file-or-directory-not-found) — a pack-construction FAIL, not a test verdict. Fix: any worker task that names exact file paths for a pack script must run `ls`/existence checks before writing the script (the re-dispatch worker did this correctly). Also note: this repo has DUPLICATE-named test trees (`tests/unit/` vs `tests/services/` vs `tests/manager/`) — canonical location is not predictable from the module name.

## L4 — Strict-bash `set -e` RESULT-echo flaw (confirmed in practice)

`set -euo pipefail` + `EXIT_CODE=$?` after pytest never captures: `set -e` aborts on the failing pytest BEFORE the assignment. Correct idiom (now in `skill_services_gzip_regression_unit_test.sh`):

```bash
EXIT_CODE=0
timeout 120 .venv/bin/pytest ... 2>&1 || EXIT_CODE=$?
```

The `||` list-context exempts the capture from `set -e`. A `skill_fix` was filed against `test-pack-execution` to document this pitfall. (This was already a known follow-up from the reconciler gate — now hit for real.)

## L5 — Ephemeral loopback ports in wire tests (convention deviation, accepted)

The gzip wire tests bind `127.0.0.1:0` (OS-ephemeral) instead of the 10000-19999 mock range. Accepted here because: loopback-only, sub-second lifetime, `HTTPServer.shutdown()+server_close()+join()` cleanup in `finally`, and the pattern was set by the feature file's own `TestWireLevelRealSocketServer` harness. For LONG-LIVED mock services the 10000-19999 rule still applies unchanged.

## L6 — Baseline arithmetic for this gate (for future gates on this lineage)

- `buffer_response_header_family_unit_test`: 161P exact (fix `0eaf21be` IS ancestor — the 53-family is RESOLVED; leader-side notes citing it as active quarantine are stale).
- `compaction_unit_test`: 206P (2026-08-15) → 257P (this gate) = +51 organic growth, all green.
- `concurrency_atomic_unit_test`: 98P/74S ensure-Critical exact.
- `llm_failover v1`: 64P; `v1 adversarial`: 36P (secondary-site zero-drift incl. the 3 gzip-modified skill services — key evidence the raw-SDK plumbing changed no wire behavior).
- `llm_streaming_activation`: 21P; `llm_streaming_wire_verify` (tier-4): 17P.
- New: `test_llm_request_gzip.py` 26P; `test_llm_request_gzip_edge_cases.py` 16P (commit b1fdfb9f); `skill_services_gzip_regression_unit_test` 142P/4F (all 4 non-gzip, see RESULTS for attribution).

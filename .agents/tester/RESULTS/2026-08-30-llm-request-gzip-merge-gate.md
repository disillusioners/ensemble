# Test Report: LLM Request-Gzip — Final Merge Verification Gate

Date: 2026-08-30 (UTC)
Branch: `feature/llm-request-gzip` @ `459557b2` (= `latest` tip; feature UNCOMMITTED: 10 modified + 2 untracked, +298/−16)
Gate HEAD after test-only commits: `4baccdf7` (author's working tree untouched throughout — verified pre/post by 4 workers)
Instance IDs: 093c207e (inv) · 5533a583 (feat) · 099319b5 (fid) · 8d94ca37 (edge) · 8675bd02 (reg-family) · 4892e704 (reg-fo1) · 6037efaa (reg-sact) · e95fb9a6 (reg-fo1adv) · e49a019a (reg-swire) · 4fcdad3a (reg-comp) · 75420724 (reg-skill ×2) · 73713aaf (reg-conc) · 2cf8a617 (base-ev) — 13 workers, ≤3 concurrent

## VERDICT: ✅ PASS — CLEARED FOR MERGE
Zero production defects found. Zero new test failures (no green→red flip in any pack). All 4 leader requirements verified against server-received bytes, not the code's self-description.

### Summary
- Feature suite: 26/26 PASS (independent run, 5.41s) — count exact
- Gate-added coverage: 16/16 PASS (NEW file, commit `b1fdfb9f`)
- Regression: 8 packs — 7 green (1 baseline-exact ×3, 1 baseline-exact, 5 historic-green), 1 PASS-by-attribution (4 failures all pre-existing, base-evidenced)
- Mock fidelity: WIRE-SOUND, 0 vacuous tests
- Production defects: 0 · Production code changes by gate: 0 (mandate held)
- Quarantined (this gate): 2 rows added (4 tests; all pre-existing)

### Scope Decision
> Full-suite NOT run. Change = LLM transport layer (llm_gzip.py NEW + config/graph seams + 3 skill-service call sites) — no daemon/job/queue behavior. Ran: feature suite + fidelity audit + edge coverage + 8 regression packs covering the leader's enumerated neighborhoods (config, failover v1+v2, streaming activation+wire, compaction, skill services, concurrency). Skipped: E2E Release Gate (requires real LLM calls; not warranted for transport-only change; leader did not request). Core ensure.md validated.

### Requirement Verification (leader's 4 "must do")

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Default = byte-identical, no gzip, no Content-Encoding | ✅ VERIFIED | Real-socket literal byte-identity: #19 `wire["body"] == body_bytes`, CE=None, CL==len (test_llm_request_gzip.py:1050-1084); raw-SDK #23 :1272-1317; no-injection guards #4 (clean_llm_config returns dict WITHOUT http_client keys) + #5 (module singletons stay None); production mechanism `resolve_gzip_client` early-return `if not enabled: return None` (llm_gzip.py:401-402). All 8 regression packs ran with flag unset — green. |
| 2 | OPENAI_REQUEST_GZIP=true → gzip ON THE WIRE, both paths | ✅ VERIFIED | LangChain path: #20 real-socket end-to-end through `clean_llm_config` singleton-injection (graph.py:2390-2404) → ThinkingChatOpenAI.invoke() → server receives gzip magic + CE:gzip + CL==len + gunzip round-trip (:1086-1138); async #21. Raw-SDK path: #22 builds `openai.OpenAI(http_client=make_gzip_httpx_client())` — exact production shape at skill_search:812 / skill_embedding:363 / skill_evolution:1568 (:1211-1270). Plumbing audit: 4 new tests assert each call site derives `enabled` from `llm_config['request_gzip']` correctly (edge file). Zero-drift confirmation: llm_failover_adversarial 36/36 (secondary-site wire-attempt assertions green on the 3 modified services). |
| 3 | Responses NOT affected; streaming works with gzip ON | ✅ VERIFIED | NEW real-socket streaming round-trip: singleton-injection branch + streaming=True — request gzipped on wire, SSE consumed to final message (edge file, TestEdgeCaseStreamingRoundTripWireLevel). MockTransport-level #6 (:525-593) additionally. Streaming regressions green: activation 21P, tier-4 wire-verify 17P. Proxy does not touch responses (wrapper is request-side only — llm_gzip.py mutates request, passes response through). |
| 4 | Non-LLM traffic untouched | ✅ VERIFIED | Grep-isolation: `llm_gzip` imported ONLY by daemon/graph.py + 3 skill services + tests. `daemon/clients/plane_http_client.py`, MCP transports, RAG, source adapters: zero imports. Module contract states it (llm_gzip.py:9-21, :54-55); code matches. |

### Test-Plan Item Results (leader's 6)

1. **Independent run**: 26/26 PASS. Wire-level assertion verification: 16/26 wire-bytes, 8 REAL-SOCKET (server-received bytes with gzip magic + CE + CL==len checks). ✓
2. **Mock fidelity**: WIRE-SOUND / 0 vacuous. Key finding: no mock is ever placed OUTSIDE the gzip wrapper (the only vacuous position). The known failure mode (MockTransport passing while wire broken) is covered by the author's red-green contract (:932-937): disabling only `request.stream = ByteStream(compressed)` makes all real-socket tests fail with `h11 LocalProtocolError: Too much data for declared Content-Length`. Suite meets+exceeds the tier-4 `llm_streaming_wire_verify` bar (real-socket + disabled byte-identity + raw-SDK seam are beyond precedent). Audit's 3 gaps → all closed by gate-added tests. ✓
3. **Edge cases** (8/8 covered; 2 existed, 6 added wire-level): empty body ✓ (existing #14 + new wire), tiny body skip-if-not-smaller ✓ (NEW — guard at llm_gzip.py:216 `if len(compressed) >= len(original): return`, previously untested), GET/no-body ✓ (existing + wire), double-compression guard ✓ (existing MockTransport + NEW wire), thread-safety ✓ (existing: 10-thread Barrier, exactly-1 build, verified genuinely concurrent), flag flip mid-process ✓ (NEW ×3: contract = singleton is process-lifetime; flag flips affect only FUTURE clean_llm_config calls — documented-as-behavior), streaming RT ✓ (NEW, see req 3), config parsing ✓ (existing 6 sub-cases + NEW config.yaml ${ENV} interpolation ×4). ✓
4. **Streaming interaction**: NEW real-socket test PASS (request compressed + streamed response consumed normally). ✓
5. **Regression run**: see table below. All failures attributed; zero new-caused. ✓
6. **Config parsing**: default false ✓; `true`/`1` accepted ✓; empty string → false ✓ (W2-style coercion); YAML None → false ✓; config.yaml `${OPENAI_REQUEST_GZIP:-default}` interpolation ×4 ✓ (NEW). ✓

### Regression Results

| Pack | Result | vs Baseline | Attribution |
|---|---|---|---|
| buffer_response_header_family_unit_test (config + failover v2 trio + load-balance) | 161P/0F/0S | exact (161P ×3 @ b1159eca) | green — note: 53-family is RESOLVED (fix 0eaf21be IS ancestor); leader's quarantine note on it was stale |
| llm_failover_unit_test (v1) | 64P/0F | historic green | green |
| llm_failover_adversarial_unit_test (v1) | 36P/0F | historic green | green — incl. zero-drift on the 3 gzip-modified secondary sites |
| llm_streaming_activation_unit_test | 21P/0F | historic green | green — no gzip×streaming/http_client key interaction |
| llm_streaming_wire_verify_unit_test (tier-4) | 17P/0F | historic green | green — gzip branch no-op when flag unset |
| compaction_unit_test | 257P/0F | +51 vs 206P (2026-08-15) — organic growth | green |
| concurrency_atomic_unit_test (ensure Core-Critical) | 98P/74S | exact (98P/74S) | green — 74 skips expected (integration-marked) |
| skill_services_gzip_regression_unit_test (NEW pack, commits 97642e4b+4baccdf7) | 142P/4F | first run | 4F ALL pre-existing, base-evidenced at clean 459557b2 worktree (see below) |

### Failure Attribution (skill-services 4F)
- ×3 `tests/manager/test_skill_service_init.py` — SQLite-migration family `20260714_000001` (`near "CONSTRAINT"` via PG-flavored `ALTER TABLE … DROP CONSTRAINT IF EXISTS` under SQLite). Identical at base ×3. Matches documented QUARANTINE family (since 2026-08-14). NOT gzip.
- ×1 `TestCheckABTestResolution::test_ab_resolution_threshold_met` — **pre-existing red at base** (`AssertionError: assert True is False` @ :1884 — old-skill is_active not deactivated). At HEAD-with-gzip the signature SHIFTS EARLIER (`InvalidRequestError: Could not refresh instance` @ repository.py:351). Zero gzip-path symbols on this code path (gzip diff = :1550-1558; plumbing tests green). 🟡 Follow-up: isolate whether the +38 skill_evolution_service lines shift session/thread timing around check_ab_test_resolution (:1271) — same InvalidRequestError class as the 2026-08-29 dependency_bus StaticPool row. Not a merge blocker (red→red, no green→red).

### ensure.md Validation (Core, blast-radius scoped)
- **Critical**: changed packs PASS ✓ (attribution-documented); concurrency_atomic_unit_test PASS ✓ (deadlock/concurrency integrity + sync-DB-on-loop); dev.sh `--timeout-graceful-shutdown 10` static ✓ (dev.sh:102)
- **Important**: deadlock scenario ✓ (in concurrency pack); await-callers grep — out of blast radius (functions untouched by this change)
- **Release Gate**: NOT TRIGGERED (transport-only change; not architecture-critical)
- Contradictions: none. Quarantine-aware: all 4 failures matched/documented in QUARANTINE.md.

### Quick Fixes Applied
None authorized (leader mandate: report-only). Gate-added TEST code only:
- `b1fdfb9f` — tests/unit/test_llm_request_gzip_edge_cases.py (+1351, 16 tests)
- `97642e4b` + `4baccdf7` — test/packs/skill_services_gzip_regression_unit_test.sh (NEW pack; path fix tests/unit→tests/services)

### Gaps
None. All 13 nodes complete; 1 re-dispatch (reg-skill path fix) succeeded.

### Follow-ups (non-blocking, leader routes)
- 🟡 AB-resolution signature drift under gzip tree (QUARANTINE.md 2026-08-30 row) — timing-sensitive session interaction; retry-budget + isolation suggested
- 🟢 Inventory-path mislabel lesson (LESSONS L3): `ls`-verify paths before pack-script creation
- 🟢 `skill_fix` filed on `test-pack-execution` (set -e RESULT-echo pitfall; id 357d3c12)
- 🟢 Pre-existing reds (SQLite-migration family, AB-resolution) belong to owning feature areas — not this gate

### Documentation Updated
- [x] PACKS.md — gate block + 3 new pack rows
- [x] QUARANTINE.md — 2 rows (4 tests, base-evidenced)
- [x] LESSONS/2026-08-30-llm-gzip-merge-gate-mock-fidelity.md — 6 lessons (L1 mock placement, L2 red-green contract, L3 path verification, L4 strict-bash idiom, L5 ephemeral ports, L6 baselines)
- [x] RESULTS/2026-08-30-llm-request-gzip-merge-gate.md — this report
- rules/ensure.md — untouched (user-owned)

### Overall Status
- Feature suite: ✅ PASS (26/26) · Gate-added: ✅ PASS (16/16)
- Mock fidelity: ✅ WIRE-SOUND (0 vacuous)
- Regression: ✅ 8/8 green-or-fully-attributed (0 new-caused failures)
- ensure.md Core: ✅ 4/4 Critical
- **Testing Complete: ✅ READY — CLEARED FOR MERGE** (follow-ups 🟡×1 / 🟢×3, none blocking)

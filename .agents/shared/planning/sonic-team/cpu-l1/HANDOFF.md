# HANDOFF — cpu-l1 cheap-overhead package (DISCUSSION.md §5 lane L1)

Commission (sonic-leader, Emperor green-light Discord 2026-11-21 + TWO binding amendments):
L1 = #1 ram-guard tick + #2 orjson + #2b response gzip + #5 logging guard. NO live promote — the
Emperor's 3-factor ceremony owns the flip; live fence held throughout (zero live-listener /
ensemble_prod / install-dir contact by any module or by the integrator).
Base: origin/latest 0b5624dc9 (= tag v0.31.22) + cpu-arc2-integration docs merged early (362fa4d11).
Integrated tip: c6124ed4b (module merges m1 672823657 -> m4 07c3eca05 -> m2 2bbe09356 -> m3 5eb0d031e
-> m2-rev c6124ed4b). Complexity verdict: MEDIUM (assessed post-plan, pre-dispatch; no override).

EMPEROR RULINGS (verbatim, binding; also in cpu-throughput-arc2/DECISIONS-l1.md:10,:12):
1. "but scan every second" — ram-guard poll interval 200ms -> 1000ms (primary fix for the
   17%-of-a-core finding).
2. "Cpu still used when agent run task like build code, etc. we still try to use less cpu as
   possible." — standing philosophy: minimize daemon-owned CPU wherever the house controls it;
   agent-task CPU is the actual work and stays.

TRADEOFF DISCLOSURE (Emperor-accepted; CHANGELOG + DECISIONS-l1.md): breach-detection latency rises
from <=200ms to <=1s. At historical balloon rates of 1-3 GiB/s that is up to ~1-3 GB more growth
before kill on the pathological path. Confirm deadline (250ms) and TERM->KILL grace (250ms) are
UNCHANGED; a missed sample remains fail-closed. Secondary consequence: liveness sweep cadence
stretches 5s -> 25s (sweep_every_ticks constant unchanged x 5x longer tick).

## Files touched across modules
- M1 cpu-l1-m1-ramguard @ e924e3e1d: daemon/config.py; daemon/services/ram_guard/guard.py;
  tests/unit/test_ram_guard_config.py; CHANGELOG.md; .agents/shared/planning/cpu-throughput-arc2/DECISIONS-l1.md (new)
- M4 cpu-l1-m4-logging @ 5f2d9a2a6: daemon/graph.py; tests/unit/test_graph_debug_log_guards.py (new)
- M2 cpu-l1-m2-orjson @ 9bcd5d1c9 + dd6212fa1 (seam revision): daemon/routers/instances.py;
  daemon/routers/metrics.py; pyproject.toml; uv.lock; tests/unit/test_orjson_parity.py (new)
- M3 cpu-l1-m3-gzip @ c844ec858 + ee7a84384 (bounce fix): daemon/api.py;
  tests/unit/test_gzip_middleware.py (new); tests/integration/test_auth_off_state.py
- sonic-developer: .agents/tester/RESULTS/sonic-cpu-throughput-arc2-results.md (preserved from the
  analysis worktree, committed pre-dispatch); this HANDOFF.md; the five merge commits.

## What changed
1. RAM-GUARD TICK (#1): RamGuardConfig.poll_interval_ms default 200 -> 1000 at every default site
   (validator empty-string map, Field default, OFF-path model_construct, RamGuard ctor kwarg).
   Env RAM_GUARD_POLL_INTERVAL_MS mechanism + bounds (50,2000) unchanged. api.py:4660 already passes
   the config value. MEASURED: per-tick scan_proc ~11.3 ms on a 265-pid fake /proc; tick rate 5/s ->
   1/s => steady tick CPU ~17.9% -> ~3.6% of one core BY FREQUENCY ARITHMETIC (baseline being
   removed: 16.9/15.7/18.4% live-measured M1 Q3; 17.87% tester re-derive). /proc-scan scoping
   (watch-only-guarded-pgids) deliberately SKIPPED: behavior-changing observation set on the
   kill-decision surface, no measurement-first evidence.
2. LOGGING GUARD (#5): ALL 21 eager f-string logger.debug sites in daemon/graph.py guarded behind
   if logger.isEnabledFor(logging.DEBUG) — NOT the brief's 8: AST re-grep found 9 additional
   multi-line implicit-concat sites invisible to single-line grep. Guard-only diff multiset-verified
   (every removed line re-added verbatim +4 indent). MEASURED: eager build 531 ns/op -> guarded
   106-145 ns/op (INFO level, 200k iters); ~390-550 ns saved per LLM invocation on the hot site
   alone, which fires on EVERY invocation.
3. ORJSON (#2): the two fat render sites swap stdlib -> orjson 3.11.7 (promoted transitive ->
   declared dep; uv.lock delta 2 lines; already bundled in the shipped PYZ). (a) GET /api/instances
   list body via _encode_list_body + _stdlib_wire_render fallback oracle: 157,368 B / 250 rows —
   1.28 ms -> 0.27 ms (4.7x), bytes IDENTICAL. (b) metrics door: render_body() is now THE single
   renderer for served body AND byte-ceiling probe (lockstep); query routes build+render on
   db_offload; /series 180,546 B — 1.97 ms -> 0.88 ms (2.25x); over-ceiling probe+trim 1.69-5.07 MB
   — 11.2-11.3x; trim result length identical (248 series). daemon/tools/metrics_read.py kept on
   stdlib BY DESIGN: its ensure_ascii escape spelling makes orjson bytes differ; ~375 KB
   metrics_history body therefore UNCHANGED (no gain claimed).
4. RESPONSE GZIP (#2b): house ResponseGzipMiddleware in daemon/api.py, registered after CORS
   (egress route -> CORS -> gzip). Stock starlette 0.52.1 GZipMiddleware verified and REJECTED
   (only-exclusion-is-SSE allowlist too narrow, compresslevel 9 = 98.4 ms vs 6.2 ms at L1 on
   2.6 MB, streaming GzipFile buffering hazard). House design: allowlist application/json +
   application/*+json + text/* MINUS text/event-stream; minimum_size 8192; compresslevel 1;
   single-shot; skips HEAD / existing Content-Encoding / ETag-bearing (validator safety); RFC
   Accept-Encoding incl. gzip;q=0 and *. MEASURED: 1.81 MB instances-shaped JSON 1,809,238 ->
   60,407 B (30.0x), real-socket wall -0.6 ms; real /api/instances 600 rows 49,824 -> 1,726 B
   (28.9x); SSE identity + per-event flush proven at ASGI-message level AND over a real uvicorn
   socket (no Content-Encoding); CORS headers survive; /openapi.json compresses; /livez identity.

## What is UNTESTED
- M1: live re-measure of ram-guard-tick CPU share (post-promote; the 3.6% figure is frequency
  arithmetic on a measured per-tick cost); confirm-window growth envelope is arithmetic, not measured.
- M4: hot site (:9567) not driven end-to-end — structurally fence-pinned + canonical message-bytes
  pinned instead; full suite not run (tester owns execution).
- M2: no frozen-binary boot smoke (orjson already bundled in PYZ and imported today via
  langgraph/fastapi); no FE runtime check (FE consumes JSON bodies, shape unchanged); metrics_read
  ~375 KB body untouched (no gain there).
- M3: no live/browser observation (fence); FE decompression verified by source inspection only
  (frontend/src has ZERO content-length/byte-size/responseType assumptions — plain HttpClient
  JSON); no frozen-binary boot smoke; no multi-poller load test.
- Integration: ETag-bearing responses (live-view HTML, FileResponse/SPA) intentionally never
  compressed; no staged-artifact boot smoke at lane time beyond stage.sh's own gates.

## Known risks
- The Emperor-accepted breach-latency tradeoff (above) — the headline risk of this release.
- Liveness sweep cadence 5s -> 25s (tick x constant arithmetic; constant unchanged).
- gzip x future routes: any future fat JSON route auto-compresses (allowlist-gated) — clients that
  assume identity bytes must send Accept-Encoding: identity; M2's parity pins do (and assert
  content-encoding absent, so threshold drift is a loud RED, not silent).
- metrics door routes now return pre-rendered Response — a future route forgetting the
  render_body/Response pairing would serve unencoded dicts; mitigated by the
  exactly-one-renderer pin (3 Response sites pinned).
- orjson deltas documented + pinned as UNREACHABLE on swapped payloads (NaN -> null, non-str keys
  via OPT_NON_STR_KEYS, None-key spelling, subnormal floats); instances route has NO float field
  (pinned), metrics door's prior renderer was pydantic-core which spells floats like orjson (pinned).
- Pre-existing reds (identical at base, NOT branch-caused): 2 in tests/unit/metrics/ (stale census
  + journal drift); test_api_router_extraction api-size QUARANTINE row (api.py grew ~271 lines this
  lane — the documented threshold exception, follow-up belongs to its own PR);
  test_instance_ui_prefs_api / test_agents_api / routers event-loop flake family (98F/1135P
  byte-identical base vs HEAD per M3's A/B).

## How to verify
On the merged tree (integration worktree /home/nea/ensemble-src-wt-cpu-l1 @ c6124ed4b or the tag):
1. .venv/bin/python -m pytest -q tests/unit/test_ram_guard_config.py tests/unit/services/test_ram_guard_guard.py
2. .venv/bin/python -m pytest -q tests/unit/test_graph_debug_log_guards.py && grep -c "isEnabledFor(logging.DEBUG)" daemon/graph.py  # 21
3. .venv/bin/python -m pytest -q tests/unit/test_orjson_parity.py tests/api/test_instances_list_response_bytes.py
4. env -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_DISCOVERY_URL -u ENSEMBLE_AUTH_CLIENT_ID -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE .venv/bin/python -m pytest -q tests/unit/test_gzip_middleware.py tests/integration/test_auth_off_state.py
5. Structure greps: poll default 1000 in daemon/config.py (:1264 validator map, Field default, :9352
   OFF-path) + daemon/services/ram_guard/guard.py:180; "orjson>=3.11" pyproject.toml;
   ResponseGzipMiddleware registration after CORS in daemon/api.py.
Combined lane quick-check: 117 passed / 0 failed (all four module surfaces + the closed seam node).

## Bash-raises used
none — every command ran under the default <=600 s bound; the release stage runs under proc
(background) per lane discipline.

## Complexity self-assessment
medium — assessed after explore+plan, BEFORE dispatch (workflow 2.5); no override raised, no
handover. Four S/XS disjoint modules; what keeps it medium rather than low: the ram-guard knob sits
on a kill-decision safety surface (pinned via the existing test surface), the orjson swap required
third-party-oracle parity (the metrics door's prior renderer was pydantic-core, not stdlib json),
and the gzip item turned into a house middleware after the stock one failed verification at the
shipped starlette version.

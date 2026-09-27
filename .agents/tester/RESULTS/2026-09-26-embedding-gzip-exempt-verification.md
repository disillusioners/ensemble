# Test Report: embedding-path gzip exemption — close-out verification

**Date:** 2026-09-26
**Commission:** TEST & VERIFY `fix/embedding-gzip-exemption` — Debug-workflow close-out (original symptom must be proven dead, not just suites green)
**Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-embed-gzip` (branch `fix/embedding-gzip-exemption`)
**Fix under test:** `ce644d4a` ("fix: exempt embedding path from gzip request transport"), base v0.15.2 @ `139ba352`, 1-commit-clean lineage (verified: merge-base, tag `v0.15.2` points at base, no side-commits)
**Worker instances:** W1 `ca3064db` (verify+census, infra) · W2 `688b9d0c` (pack creation, infra) · W3 `f53006e5` (consumers pack, test-pack-execution) · W4 `6727f981` (gzip pack, test-pack-execution) · W5 `44a65efa` (wire repro + quick-fix, mock-test/quick-fix) · W6 final commit (infra)

---

## Overall verdict

# ✅ PASS — ORIGINAL SYMPTOM DEAD (wire-level two-sides proof) · READY

| Checkpoint | Result |
|---|---|
| Task 1 — Independent suite re-run | ✅ **317/317 PASS** (48 gzip trio + 269 consumers), 0 fail, 0 deselect, 0 quarantined triggers |
| Task 2 — Original-scenario repro (red→green) | ✅ RED faithfully reproduced @ base; GREEN clean @ fix → **closure proven** |
| Task 3 — Regression guard (chat gzip + consumers) | ✅ Chat gzip wire-verified ACTIVE; 269 consumers green (reviewer's ~101 exceeded 3×) |
| ensure.md (blast-radius scoped) | ✅ In-scope Core gates PASS; no contradictions found |
| Web-UI / FE | **Explicitly not tested — zero frontend involvement** (daemon transport fix only) |

---

## Scope Decision

Scope honored as SMALL. Changed set = 3 files (`daemon/services/skill_embedding_service.py`, new `tests/unit/test_embedding_gzip_exempt.py`, re-contracted `tests/unit/test_llm_request_gzip_edge_cases.py`; the other two named suites are inherited unchanged from v0.15.2 — verified via lineage `--stat`). Packs run: the 3 commission packs below. Deliberately excluded periphery (second-order mocked-embed consumers, out of a transport-fix blast radius): llm_failover_v2 family (81), blueprint ×2 (42), critical-notes ×3 (17), skill_evolution_config (12), cross-phase-flow family. Coverage delivered 317 > reviewer's ~101.

*Census correction:* pre-run census estimated the consumers pack at 125 tests; authoritative pytest count = **269** (the census undercounted `tests/services/` per-file `def test_` hits; pytest output is ground truth).

---

## Task 1 — Independent suite verification (re-run, not trusting prior greens)

| Pack | Files | Result | Runtime |
|---|---|---|---|
| `embed_gzip_unit_test` (W4) | `test_embedding_gzip_exempt.py` (6) + `test_llm_request_gzip.py` (26) + `test_llm_request_gzip_edge_cases.py` (16) | ✅ **48 passed** — `48 passed in 8.94s`, exit 0 | 10.6s wall (caps: inner 150s / outer 300s) |
| `embed_consumers_unit_test` (W3) | `test_snapshot_embedding_service` + `test_snapshot_search_service` + `tests/services/test_skill_embedding_service` + `test_skill_search_service` + `test_skill_store_service` + `test_skill_evolution_service` + `test_skill_phase2_integration` | ✅ **269 passed** — `269 passed in 5.19s`, **0 deselected** (default addopts marker filter touched nothing) | 7.0s wall (caps: inner 240s / outer 300s) |

Both runs: drift-pin green (branch `fix/embedding-gzip-exemption`; `139ba352` verified ancestor of HEAD), `uv run python -m pytest --tb=short -q` from worktree root (bare-pytest trap honored), env-scrubbed `POSTGRES_*`, no `-x`. Zero failures → zero quarantine cross-checks needed.

---

## Task 2 — Original-scenario repro, red→green (closure proof)

**Method.** Local strict embeddings server (ports 18771 green / 18772 red, mock range 10000-19999) that emulates direct OpenAI `/embeddings` exactly: any gzip body (`Content-Encoding: gzip` or gzip magic `1f 8b`) → HTTP 400 `{"error":{"message":"We could not parse the JSON body of your request...","type":"invalid_request_error",...}}`; valid JSON → 200 embeddings-shaped response. The REAL code path (`SkillEmbeddingService.embed_text` → `_do_embed_call` → `openai.OpenAI(...)`) is driven with `request_gzip=True` and base_url pointed at the server; no HTTP-layer monkeypatching (construction discipline preserved the wire proof). RED side runs in an isolated temp linked worktree (`git worktree add /tmp/embed-gzip-red-139ba352 139ba352`) — the fix worktree's git state was NEVER mutated; temp worktree removed + pruned after.

### RED @ base `139ba352` (pre-fix) — original 400 symptom REPRODUCED
```
REQ 0 POST /v1/embeddings Content-Encoding=gzip body_head=1f8b080062dcb76a02ffedd6314e0431 json_parse=gzip -> 400
RED embed observation: path=/v1/embeddings Content-Encoding='gzip' body_head=1f8b080062dcb76a02ffedd6314e0431 json_parse=gzip -> 400 client_returned=NoneType client_exc=RuntimeError
RED assertions: observed_gzip=True observed_400=True client_side_failure=True (client_exc=RuntimeError: Embedding API call failed: Error code: 400 - {'error': {'message': 'We could not parse the JSON body of your request. (Simulated strict OpenAI /embeddings behavior)', 'type': 'invalid_request_error', 'param': None, 'code': None}})
RED RESULT: PASS — bug faithfully reproduced (gzip on wire → strict server 400 → client-side failure surfaced)
```
→ Base code wires the gzip client into the embed path; payload above the gzip-shrink threshold gets compressed on the wire; strict server rejects with **the exact original error message**; failure surfaces client-side. This is the production failure mode, byte-for-byte (modulo the mock's self-documenting suffix).

### GREEN @ fix `ce644d4a` (via permanent pack `embed_gzip_wire_mock_test`) — symptom DEAD
```
REQ 0 POST /v1/embeddings Content-Encoding=absent body_head=7b22696e707574223a22536b696c6c20 json_parse=ok -> 200
GREEN embed observation: path=/v1/embeddings Content-Encoding=None body_head=7b22696e707574223a22536b696c6c20 json_parse=ok -> 200
REQ 1 POST /v1/chat/completions Content-Encoding=gzip body_head=1f8b080053dcb76a02ffedce410a0231 json_parse=ok -> 200
GREEN chat observation: chat_captures=1, chat_status=200
GREEN chat-path OK — server saw Content-Encoding='gzip' (gzip transport preserved)
GREEN RESULT: PASS — embed path is ungzipped, chat path stays gzipped
```
→ Same oversized payload, same `request_gzip=True`: embed request goes out as **plain JSON** (`{"input":"Skill …`), no `Content-Encoding` header, strict server accepts 200, vector returned. `RESULT: PASS`, exit 0, driver runtime 1.12s.

### Restore verification (after RED dance)
- Fix worktree HEAD `7375503e` (quick-fix driver commit; parent `ce644d4a`), branch `fix/embedding-gzip-exemption`
- `git status --porcelain` = commission-known entries only (docs + packs + LESSONS); temp worktree fully removed; port 8088 never contacted

---

## Task 3 — Regression guard

- **Chat-path gzip still ACTIVE under the same flag:** pinned twice — (a) suite-level: `test_llm_request_gzip.py` 26/26 inside the 48-green trio (mandatory httpx-pin discipline intact); (b) wire-level: GREEN REQ 1 shows a `resolve_gzip_client(True)`-built client request carrying `Content-Encoding=gzip` with genuine gzip magic body (`1f8b0800…`) — transport preserved end-to-end.
- **No other embedding consumer regressed:** 269/269 across the 7 direct-consumer suites (0 deselected).

---

## ensure.md Validation (blast-radius scoped)

| Requirement | Class | Status |
|---|---|---|
| No regressions in changed packs — all blast-radius packs PASS | Critical | ✅ 3/3 commission packs PASS |
| Deadlock/concurrency integrity (`concurrency_atomic_unit_test`) | Critical | ➖ Out of blast radius (embedding transport change; no concurrency/DB-loop code touched) — per ensure.md's own scoping rule |
| No sync DB calls on event loop | Critical | ➖ Same pack, out of blast radius |
| `dev.sh` carries `--timeout-graceful-shutdown 10` | Critical | ✅ Static check PASS (dev.sh:102 active flag; :99 comment) |
| Async-await caller checks / parent→child→complete scenario | Important | ➖ Out of blast radius |
| No dead code from the fix | Nice-to-have | ✅ Informational: fix removed the embed-path `resolve_gzip_client` call + import; suites green (no dangling references); chat-path usage elsewhere intact |

No contradictions between ensure.md methods and pack/timeout rules → **no Improvement Notices**.

---

## Quick Fixes Applied

1. **Wire-repro driver repair** — commit `7375503ead9ef7fcf68c67493e6f4565a408cb70` ("test: fix wire repro driver (gzip-shrink threshold probe + gzip_magic scope)"), parent `ce644d4a`:
   - Root cause 1: strict-server handler assigned `gzip_magic_present` only in the strict-rule branch; chat-path branch referenced it unbound → server crash on chat probe. Fix: hoist assignment before branch.
   - Root cause 2: probe bodies (57 B embed / 60 B chat) sat below the gzip-shrink threshold — `GzipRequestTransport._compress_request_body` (`llm_gzip.py:216-217`) no-ops when `len(compressed) >= len(original)`, so RED could never manifest. Fix: deterministic `PROBE_TEXT` (~2160 chars) used by both sides. See `LESSONS/2026-09-26-gzip-wire-repro-body-threshold.md`.
   - Verification: full re-run of GREEN pack (PASS) + RED scratch (PASS). Disclosed: ~28-line single-file diff (slightly over the 20-line ideal — same defect class, one file, trivial edits); single amend pre-push for one conceptual fix.

**First RED attempt honestly failed** (tiny probe → no compression → 200 at base). The two-sides rule held: no closure claimed until the repro captured the real bug. This is the proof's strength, not a gap.

---

## Gaps

None. All planned nodes completed. Housekeeping note: `.agents/tester/PACKS.md.new` is a pre-existing 0-byte stub (Sep 26 21:00, git-excluded, harmless) — left in place, flagged to the file's owner.

## NO Web-UI / FE testing

Explicitly out of scope per commission: daemon transport fix, zero frontend involvement. (FE Jest baseline state unchanged — see QUARANTINE.md 2026-09-26 row for the unrelated pre-existing 4F.)

## Documentation Updated

- [x] PACKS.md — commission section + 3 pack results
- [x] MOCK_TESTS.md — wire-mock spec + Last Run
- [x] LESSONS/2026-09-26-gzip-wire-repro-body-threshold.md — threshold trap + handler-scope rule
- [x] RESULTS/2026-09-26-embedding-gzip-exempt-verification.md — this file
- [x] rules/ensure.md — unchanged (user-owned, read-only)

## Code Changes Summary (all TEST-side; production `daemon/` untouched — verified `git diff HEAD~1 -- daemon/` empty)

- `tests/mocks/embed_gzip_wire_mock.py` — NEW, committed `7375503e`
- `test/packs/embed_gzip_unit_test.sh`, `embed_consumers_unit_test.sh`, `embed_gzip_wire_mock_test.sh` — NEW, committed in close-out commit (below)
- `.agents/tester/{PACKS.md, MOCK_TESTS.md, LESSONS/…, RESULTS/…}` — updated/added, committed in close-out commit

## Overall Status

- Unit suites: ✅ PASS (317/317)
- Wire repro red→green: ✅ PASS (symptom DEAD)
- Regression guard (chat gzip + consumers): ✅ PASS
- ensure.md (scoped): ✅ PASS
- **Testing Complete: ✅ READY — original symptom proven dead at the wire level**

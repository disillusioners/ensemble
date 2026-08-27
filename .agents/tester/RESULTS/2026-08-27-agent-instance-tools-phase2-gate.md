# Independent Verification Gate — agent-instance-tools Phase 2 (`subtree_messages` tool)

- **Branch**: `feature/agent-instance-tools` @ `e15be0e2` (range `c2fde8f5..e15be0e2`, 3 commits: 52d82cfd → 89261b6f → e15be0e2)
- **Date**: 2026-08-27
- **Role**: Final pre-merge gate (no prior run trusted; independent re-execution)
- **Diff verified**: 6 files, all in plan-allowed set — daemon/tools/instance.py, daemon/manager.py (exactly one additive facade method), daemon/tools/_tool_registry.py, agents/leader/meta.json, tests/unit/tools/test_instance_tools.py, tests/unit/test_tree_traversal.py. Forbidden-touch check (services/routers/graph/repositories): EMPTY. ✅
- **Dispatches**: 19 workers (1 P0, 2 targeted, 1 mock-fid, 1 probe, 2 follow-ups, 4 committed packs, 7 sweeps, 1 polluter deep-dive), 0 direct executions.

## VERDICT: ✅ PASS (as of fix commit `1ae0acc0`, 2026-08-27) — Phase 2 gate CLOSED; feature CLEARED FOR MERGE to `latest`
*(Initial verdict @ e15be0e2: ❌ FAIL — one 1-line test-hygiene blocker. Fixed in `1ae0acc0` and independently re-verified — see §5.)*

Feature functionally validated end-to-end at the highest rigor so far: real-checkpoint round-trip, real ContextCompactor smoke, 220-case adversarial fuzz, TOCTOU pinning — all pass. The sole blocker is deterministic cross-test pollution between a NEW Phase-2 test and a P2.2 test, fix-verified at 1 line.

---

## 1. 🔴 BLOCKER (1-line fix): order-dependent test pollution in tools_suite

- **Failing**: `tests/unit/tools/test_upgrade_registration.py:339` `TestDocsDefaultDeny::test_empty_allow_agent_no_docs_leak` — `TypeError: '<' not supported between 'MagicMock' and 'str'` at `daemon/loader.py:171`. Passes isolated; fails when run after `tests/unit/tools/test_instance_tools.py`. tools_suite: 982 collected / 976P / 1F (Phase-1 gate: 924P/0F).
- **Polluter** (12-step bisection, uniquely localized): `tests/unit/tools/test_instance_tools.py::TestRegistration::test_tool_help_returns_subtree_messages_doc` (:3170, NEW in `52d82cfd`). It builds `tools` inside `_patch_heavy_helpers()` scope (3 MagicMock factory outputs), then — after patch teardown — passes the contaminated list to the REAL `create_help_tool` (:3209) → `scan_tools_for_full_docs` (`_tool_registry.py:388-422`) writes MagicMock-keyed entries into the module-level `_tool_metadata` singleton, never restored. Probe evidence: singleton grows 1→78 entries, 3 non-str keys.
- **NEW-vs-pre-existing**: victim pre-exists (P2.2); polluter is Phase-2. Phase-1's 924P/0F predates it.
- **Severity**: TEST-HYGIENE only — no production risk (singleton rebuilt at registry boot; no test-style pollution cycle in prod).
- **Fix (worker fix-verified "leak sealed", NOT applied — gate is report-only)**: at `test_instance_tools.py:3209`, before the real `create_help_tool` call: `tools = [t for t in tools if not isinstance(t, MagicMock)]`.
- **Narrow re-verification after fix**: re-run `test/packs/tools_suite_unit_test.sh` (expect 977P/0F) + the polluter/victim pair. Nothing else.

## 2. Verification results (all GREEN)

### P0 statics (16/16)
Facade exactly 1 def (`manager.py:9342`, single-line delegation, one +32-line hunk, no other facade additions); D14 clean (ZERO `_instance_repository` reach-ins in the subtree path); **rollback guard: `aget_state` 0 hits**; W3 caps 64/200 (`_SUBTREE_TOOL_NAME_MAX_CHARS`, content/tools-joined 200); W4 clamps 100/500 + `limit=0` documented at 6 code sites + `_full_doc_`:3123 + test `test_limit_zero_emits_headers_zero_rows`; S1 composite sort key `(x != caller, x)` at :2770 + cap-boundary tests; W1 prefix const :926 + use :1029 + 5 tests; meta.json: ONLY leader, in-place `"subtree_messages"` entry (no category wholesale); registry: `_tool_registry.py:663` KNOWN_TOOL_NAMES.
**Labeling notes (non-blocking)**: "134" is EXACT (P0 worker's 97 was a collect-only artifact — full run collected 134); `test_tree_traversal.py` is MODIFIED-not-NEW (predates branch at `56b76e7e`, +142 lines this phase).

### Targeted — 172/172
`test_instance_tools.py` **134/134** (82 Phase-1 baseline intact + 52 Phase-2 across 9 new classes: ScopingAccept 3, ScopingReject 4, FilterBehavior 9, PaginationAndCaps 7, TokenSafety 9, Registration 9, D12 7, PerformanceFixture 3, CompactionSmoke 1); `test_tree_traversal.py` **38/38** (W5 depth-cap: 256-cap truncation+warn, parent_id walk incl. revive, cycle guard, caller-root edges; file-backed SQLite).

### Behavioral probe (REAL tool fn, 16/16)
**ORIGINAL GOAL CONFIRMED: parent reads child state on demand** (own-subtree default returns all 6 blocks, child state readable). Cross-subtree reject (sibling+unrelated) with ZERO get_messages; targeted child; root-caller self-only; summary mode; W4 limit=0 zero-rows+headers; clamps 100/500 exact; S1 caller survives cap slice even sorting last lexicographically; W1 transformations exact (descendant drop / caller keep / mid-text keep); W2 redaction both modes; W3 caps; status filter N× get_instance_info; Semaphore(5) is concurrency-only (9/9 fetched); **rollback guard: 0 aget_state + exactly 1 get_messages per instance across ALL scenarios**; per-instance error isolation with warning.
Non-blocking observations: (1) limit=0 output carries the sentinel implicitly (header + `messages=0 of N`) — no explicit guidance sentence; docstring documents it. (2) S1 "caller-first" = fetch/cap-slice order; block render order is lexicographic.

### Mock fidelity — CLEAN (0 critical/high)
34 MOCK-OK (get_messages dict shape byte-matches serialize_message; get_tree_ids_permanent list + S1 re-sort makes mock-order irrelevant; get_instance_info dict; synthetic shapes match persistence.py:436-487; 17 patch targets correct; no StaticPool; call-count assertions 3/5/20/100 + aget_state source-grep). 2 INFO divergences: `_tool_msg` fixture emits a standalone-tool-dict shape real serialize never produces (W2 redaction branch is dead code for real data — no runtime divergence); joined-names fixture omits id/output keys. 3 INFO gaps: get_instance_info-KeyError partial-failure branch untested (asymmetric with get_messages path); aget_state runtime regression would only be caught by the static grep (MagicMock auto-attr); tree-traversal file is orthogonal.

### Follow-up (a)/(d)/(e) — real-object probes
**(a) Round-trip PASS, 0 shape mismatches**: real file-backed AsyncSqliteSaver → real get_instance_messages → real filter/render. W1 descendant/caller/mid-text semantics hold on REAL persisted data; ToolMessage correctly absent (retriever drops at persistence.py:360) with output correlated into parent AIMessage.tool_calls[i].output; RemoveMessage sentinels: 0 leaked. **(d) Token delta**: plan's ~80% claim **HELD under budget framing** (raw→summary 73-90% across 6 cells, tiktoken cl100k); but summary-vs-full is **negative** (−12% to −41%) — per-line `(timestamp) tools=` metadata (~6 tokens) exceeds the 200→80-char content saving on long content. Doc nuance for agents reasoning about mode costs; not a defect. **(e) Compaction smoke PASS vs REAL ContextCompactor** (only ThinkingChatOpenAI.invoke stubbed): threshold trigger, summarization replacement shape (20 RemoveMessage + 1 summary + 4 preserved), 1452→356 tokens, post-compact read renders clean.

### Follow-up (b)/(c)/(f) — stress probes
**(b) D12 fuzz 220/220, 0 leakage, 0 crashes** (adversarial markers: mid-text/quoted/fenced/whitespace/full-width/lowercase/doubled — all preserved; char-0 + synthetic ids/keys — all dropped). **(c) Rejection stress**: depth 255 accepted / 257+ rejected (visited-set truth per repository.py:443-449); 200-child fan-out — auth decoupled from pagination (all 6 probe targets accepted); 60 cross-subtree near-miss targets ALL rejected, 0 get_messages; **TOCTOU pinned: facade exactly 1 call per invocation, snapshot semantics**. **(f)**: status filter 5-working-set correct; mixed-failure isolation; no-filter 12/12+caller fetched.
**LOW findings (non-blocking)**: SD-1 — Semaphore(5) at instance.py:2777 wraps SYNC get_instance_info (max in-flight observed 1; sync-in-event-loop blocks all coroutines — decorative); SD-2 — semaphore sits on status fetch, not get_messages fan-out (doc the design choice).

### Regression packs
| Pack | Result | vs baseline |
|---|---|---|
| api_unit_test.sh | 213P/8S/0F | exact |
| concurrency_atomic (ensure Critical) | 98P/74S/0F | exact |
| registry_validation (FIRST-EVER run) | **140/140** (registry 102 + tools 38) | new baseline; leader opt-in accepted, zero unknown-tool warnings |
| tools_suite | 976P/**1F**/5-deselect | **the blocker** |

### Full-tree sweeps ×7 — ZERO UNKNOWN failures
unit-subdirs 1029P/8F (proxy_phase1 family, exact); unit-[a-h] 1202P/15F+4E (all 19 known); unit-[i-r] 2510P/8F (all known; mcp_cold_load_race auto-skip verified); unit-[s-z] 959P/50F+2E (watchover 47 + webfetch 2E + wanderer 2 + validate 1 — all base-evidenced; +2P/−2S drift benign); top-[a-h] 867P/3F (exact); top-[i-r] 1505P/74F (all known; migration-family composition 67→70 within identical total — grouped-count noise, no new IDs); top-[s-z] 1374P/12F (all known; 2 fewer than baseline). **No base-verification wave needed — every failure maps to documented QUARANTINE.md families.**

## 3. Required actions before merge

1. **[BLOCKER]** Apply the 1-line filter at `tests/unit/tools/test_instance_tools.py:3209` (filter MagicMocks from `tools` before the real `create_help_tool` call). Re-run `test/packs/tools_suite_unit_test.sh` (expect 977P/0F) + polluter/victim pair. That closes the gate.
2. **[Recommended, non-blocking]** SD-1/SD-2 semaphore doc/semantics note; consider moving Semaphore or documenting sync-status-fetch design.
3. **[Recommended]** Token-delta doc nuance: plan's "~80%" = raw-budget framing; add one line to `_full_doc_` clarifying summary-vs-full can be negative on long content.
4. **[Optional]** mock-fid INFO gaps: get_instance_info-KeyError branch test (~5 lines); W1 INTERIM fragility depends on serialize_message not stripping the prefix (deferred FULL FIX already documented at instance.py:961-979).

## 4. Worker instances
0d242d20 (P0), 7f0d18e6 (instance 134), 22b72ee7 (tree 38), 22f12e75 (mockfid), 70f92a14 (probe), b6382741 (fu-real), 7f049929 (fu-stress), 30f5d5c8 (tools_suite), b97aa3b9 (api), ad2f10a6 (concurrency), 3f465065 (registry), bfeb8a0b/9cf1454d/794b2642/54154f82/f2ce06e5/a1be03be/cf8838c0 (sweeps ×7), 04dc0ba8 (polluter deep-dive).

Re-verification @ 1ae0acc0 (2026-08-27): 02346bef (fix re-verify).

---

## 5. Re-verification @ 1ae0acc0 — blocker CLOSED, verdict flipped to PASS

Fix commit `1ae0acc0` (parent `e15be0e2`): exactly ONE file `tests/unit/tools/test_instance_tools.py` **+7/−0** — 6-line mechanism comment + the MagicMock filter at `:3215`, immediately before the real `create_help_tool` call (`:3216`); `MagicMock` import already present at `:54`; no other hunks, no production files.

| Check | Expected | Result |
|---|---|---|
| Diff audit | one file +7/−0, filter+comment only | ✅ exact (inserted lines quoted verbatim in worker report) |
| tools_suite pack | 982 collected / 977P / 0F / 5-deselect | ✅ **exact match**, exit 0, 23.64s |
| Polluter/victim pair (one process, registration-first) | 2 passed | ✅ 2 passed in 0.94s — singleton leak sealed |

Everything from §2-§3 of this gate (targeted 172/172, probe 16/16, follow-ups a-f, mock-fid CLEAN, packs, zero-UNKNOWN sweeps) was verified at `e15be0e2` and is untouched by `1ae0acc0` (test-only, one file).

**Phase 2 overall: ✅ PASS — feature CLEARED FOR MERGE to `latest`.** Branch chain for merge: `c2fde8f5 → 52d82cfd → 89261b6f → e15be0e2 → 1ae0acc0`.

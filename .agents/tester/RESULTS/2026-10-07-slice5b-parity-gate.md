# Slice ⑤b Parity Gate — Tester RESULTS (decisive RETIRE-⑦ gate)

- **Date**: 2026-10-07 03:55 UTC · **Tester lane**: worker 9e774851 (`slice05b-preflight`, 3 sequential phases) · **Code changes by tester**: NONE (one sanctioned guarded-restart env override; zero tracked-file modifications; one untracked evidence dir from od.save's canonical lane)
- **Main evidence artifact**: `.agents/shared/planning/plugin-subsystem/2026-10-07-slice-5b-parity-evidence.md` (commission-specified path — full tables + drift + constraint statements)
- Raw: `/tmp/slice05b/runs/` (29 + criterion files), `/tmp/slice05b/prompts/`

## Verdict: 🟢 PASS — (a) 6/6 · (b) zero-bypass · (c) full chain · (d) 3/3. RETIRE ⑦ unblocked from the test side.

| Phase | Verdict | Key evidence |
|---|---|---|
| P0 pre-flight | PASS | HEAD `64d267a6d` exact; port 8079 free; guarded boot (dev attribution + `MCP_POOL_TOOL_CALL_TIMEOUT=600` in environ); livez v0.17.2; registry boot-scan 1 plugin/1 skill; designer `d338e927` with BOTH lanes (4 native + 10 MCP od_*); protected pids identical throughout |
| P1 matrix | (a) PASS | 12 cells: native equal-or-better **6/6** (4 better, 2 equal); budgets 200000 both lanes; identical inputs echo-verified |
| P1 gates | (b) PASS | 6/6 native stop/false/null + lint pass(0); real failures all LOUD (`upstream_http_error`, `upstream_stream_closed`, `missing_artifact_marker` each reproduced live) |
| P2 chain | (c) PASS | compose→generate→lint→save, zero mcp_*, artifact persisted 18,928B with sha-identical integrity chain |
| P2 fallback | (d) PASS | d1 fully live (lane unaffected by skill absence); d2/d3 live behavioral with zero-tool-call verification + exact 5-token/SPEC-INCOMPLETE sentinels; runtime-unbind constraint labeled (spawn API lacks overrides; plugins root hardcoded); unit families cited for binding-level proof |

## MCP-lane retirement evidence (documented, not faked)
3/6 cells delivered SILENT mid-stream truncations (mid-CSS/mid-sentence/mid-tag; no error marker, no isError; designer replied RUN-DONE on cut text). The incumbent cannot signal its own failure; the native lane makes the identical class loud. Plus drift: `data-od-id` attributes 4/6 native vs 0/6 MCP (v0.23.0 vs dist@0.16.1, offset-446 family).

## Watchdog note
One hang-notice during Phase 1 (1h29m) — inspected via subtree_messages: false positive (long blocking LLM calls; runs 01b-06 actively completing). No revive consumed.

## Boundary compliance
8079 guarded dev boot = ONLY daemon touch (2 boots: initial + sanctioned model-fix restart, identity battery both) · 9797/7979/8088/7456 never touched (pid table re-verified at every phase wrap) · no key changes · no tracked-file modifications · worktree-06 untouched · daemon LEFT RUNNING for the ⑦ ceremony/teardown (leader's call).

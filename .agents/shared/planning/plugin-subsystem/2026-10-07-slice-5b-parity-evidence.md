# Slice ⑤b Parity Gate — Evidence Artifact (fail-closed validation for RETIRE ⑦)

- **Date**: 2026-10-07, completed 03:55 UTC
- **Commission**: ⑤b parity gate — the decisive validation. Builds ①–⑤ merged; `latest = 64d267a6dfe30388c6727b0e0e2362ff316c7ffd`. RETIRE ⑦ proceeds ONLY on this gate's PASS.
- **Environment**: leader-decided — GUARDED DEV DAEMON from the merged main checkout (dev.sh, :8079, ensemble_dev DB). Executed by the commission tester (slice05b lane, worker 9e774851) under report-only + sanctioned parity LLM traffic.
- **OVERALL VERDICT: 🟢 PASS — criteria (a) 6/6 · (b) zero-bypass · (c) full native chain · (d) 3/3 fallback legs. ⑦ retirement gate SATISFIED.**
- Raw evidence: `/tmp/slice05b/runs/` (29 Phase-1 files + Phase-2 criterion files) + `/tmp/slice05b/prompts/`.

---

## Phase 0 — Pre-flight (PASS)

| Step | Evidence |
|---|---|
| Main checkout | HEAD = `64d267a6dfe30388c6727b0e0e2362ff316c7ffd` (exact). Dirt = whitelisted `.agents/*` + preserved `x/` scratch + ONE pre-commission stale file (`agents/charter/skills-template/install-mermaid-cli.md`, 2026-10-05, charter-agent template, inert to all gate lanes — adjudicated non-blocking, leader's cleanup call) |
| Port 8079 | FREE (no stale daemon; no stop needed) |
| Guarded boot | dev.sh from main checkout; READY ~15s; worker pid 1462409 (later 1463943 after the sanctioned model-fix restart), cwd=/home/nea/ensemble-src; DB attribution verbatim `Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_dev` (dev, zero prod/demo markers) |
| Timeout knob | `MCP_POOL_TOOL_CALL_TIMEOUT=600` present in daemon environ (supplied at boot — `.env` lacks it; belt-and-suspenders code-level 600s per-server override independently verified at this HEAD: `daemon/mcp/builtin_servers/opendesign.py:134-147`) |
| livez | `{"status":"alive","version":"0.17.2"}` |
| Plugin registry | Boot-scan log verbatim: `Plugin registry boot-scan: 1 plugin(s) (opendesign), 1 plugin-skill(s)` |
| Designer instance | `d338e927-b33b-4173-9822-6ecacb832210` spawned, BOTH lanes live: MCP 12 tools (10× `mcp_opendesign_od_*` + 2× context7) + native 4/4 (`od.generate/compose_brief/save/lint`) bound, zero refusals |
| Protected ports | 9797 LIVE pid 1292283 · 7979 DEMO pid 3457886 · 7456 OD pid 3288819 — identical pids before/during/after, untouched; 8088 absent (baseline) |

## Phase 1 — Parity matrix, criteria (a)+(b) (PASS / PASS)

**System picks** (from the 154 catalog): `minimal` (floor test — leanest spec, 3.1KB), `stripe` (classic, richest spec 20.5KB+tokens), `tetris` (distinctive game-styled).
**Budgets**: `max_tokens=maxTokens=200000` EVERY run, both lanes identically (OQ5 surface). 64000 proved unworkable: the proxy's `vision` model (MiniMax-M3 hybrid thinker, ~55-120 tok/s aggregate, 4-12K thinking tokens/run) + the native adapter's internal window `max(120, budget/370)` → 173s < real need → guaranteed timeout. At 200000: native window 540s vs MCP 600s (lane properties at equal budget).
**Mechanics**: driven via designer-instance messages with explicit tool+param instructions; identical prompt bytes both lanes (echo-verified per run); outputs extracted verbatim from instance history; criterion-(b) lint = `OdLint.lint_dict` in-process (the daemon's exact implementation).

### Matrix (16 attempts → 12 cells; † = superseded attempt, checkpointed)

| cell | lane | artifact | gates / lint | wall | verdict |
|---|---|---|---|---|---|
| minimal-mockup | native | **32417B complete** | stop/false/null · lint pass(0) | 219s | — |
| minimal-mockup | MCP | 48936B complete | lint pass(0) | 158s | EQUAL |
| minimal-deck | native | **19657B, 15 slides** | stop/false/null · pass(0) | 204s | — |
| minimal-deck | MCP | 28528B, 22 slides | pass(0) | 179s | EQUAL |
| stripe-mockup | native | **90040B complete** | stop/false/null · pass(0) | 266s | — |
| stripe-mockup | MCP | 21191B **truncated mid-CSS, SILENT** | **lint fail-1 (EOF)** | 252s | **NATIVE BETTER** |
| stripe-deck | native | **32599B, 18 slides, clean EOF** | stop/false/null · pass(0) | 575s | — |
| stripe-deck | MCP | 30934B doc, **unclean EOF (mid-sentence cut), SILENT** | pass(0) on embedded doc | 247s | **NATIVE BETTER** |
| tetris-mockup | native | **31872B complete** | stop/false/null · pass(0) | ~780s | — |
| tetris-mockup | MCP | 19887B **truncated mid-tag, SILENT** | **lint fail-2 (R3/R7/EOF)** | 232s | **NATIVE BETTER** |
| tetris-deck | native | **25387B, 30 slides** | stop/false/null · pass(0) | ~1090s | — |
| tetris-deck | MCP | 25673B, 23 slides | pass(0) | 244s | EQUAL |

**Criterion (a): PASS — native structurally equal-or-better 6/6 cells (4 better, 2 equal, 0 worse).**

### Criterion (b): PASS — zero bypasses, all failures loud
- Final native runs: **6/6 finish=stop, truncated=false, error=null, lint pass(0), EOF-clean** — every run carries a complete gate record.
- Failed attempts surfaced LOUD typed refusals (gates demonstrably work on real failures): run-01 `upstream_http_error` (timeout); run-01′ `upstream_stream_closed` (envelope collapse — the ⑤ triage family reproduced live); run-05 `missing_artifact_marker` ("refused to return a partial artifact as success").

### Drift documentation (legitimate, per the NOT-byte-parity rule)
- `data-od-id` inspection attributes: **4/6 native artifacts (28–40 occurrences), 0/6 MCP** — consistent with vendored v0.23.0 prompt additions absent from dist@0.16.1 (first divergence offset 446). Not penalized.
- Native artifacts carry `<think>`+fenced envelopes (raw model pass-through); MCP artifact-tagged when complete. Envelope conventions per run in `analysis.json`.

### MCP-lane health (retirement-relevant)
**3/6 complete (50%); 3/6 mid-stream truncations delivered SILENTLY** — no error marker, no `isError`, no trailing comment; the designer received cut-off text and replied RUN-DONE (runs 06/08/10; raws end mid-CSS/mid-sentence/mid-tag). The incumbent's documented live-failure family, reproduced at scale: **it cannot be trusted to deliver complete artifacts and does not announce failure.** The native lane makes the identical failure class loud.

## Phase 2 — Criteria (c)+(d) (PASS / PASS)

### (c) Native-only end-to-end chain: PASS
Task: Acme Analytics dashboard-landing mockup. Chain from history: `od.compose_brief` (413B composed) → `od.generate` (52459B, kind=prototype, 200000 budget) → `od.lint` (`{"verdict":"pass","fail_count":0}`) → `od.save` (canonical lane). **Zero mcp_* invocations** (negative check from history). Persisted artifact verified on disk: `.agents/shared/planning/acme-analytics/design/mockups/dashboard-landing.html`, 18,928B, `sha256=73d428496767bdf9738e741cfe6a63d97f87411c2fad28103699c2a00a656b78` — **byte-identical across lint-input/od.save-report/on-disk** (full integrity chain). Untracked-only git impact.

### (d) Fallback ×3: PASS
Sentinel tokens matched literally from workflow.md Step-2: `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>` + "SPEC INCOMPLETE".
- **d1** (skill absent, native available — fully live): lane did NOT degrade — 2 od.* calls, finish=stop, lint pass. Skill absence breaks nothing.
- **d2** (native unusable — live, mechanism-ii): 0 tool calls verified from history; exact `tool-not-bound` token + `mockup_lane: text` + SPEC INCOMPLETE sentinel; usable 5464B HTML-in-markdown mockup — clean degradation, no crash/silence.
- **d3** (both — live, mechanism-ii): same sentinel semantics + skill-absence noted; usable 8235B mockup.
- **Constraint statement (explicit)**: spawn API carries no tool-override surface; `DEFAULT_PLUGINS_ROOT` is hardcoded (not env-overridable) — a genuine runtime unbind was not achievable without tracked-file edits (forbidden). d2/d3 verify the BEHAVIORAL fallback contract with zero-tool-invocation verification from history; binding-level proof remains available to the reviewer via the unit families (`test_designer_rewire` fallback, `test_tier1_wiring` degradation). Not faked.
- Bonus: a starved d1 attempt (max_tokens=2000 → finish=length → empty) fired **loud lint fail-1** — the gates work at the fallback boundary too (both attempts checkpointed).

## Anomalies (all non-blocking, documented)
1. One sanctioned guarded daemon restart mid-Phase-1: `.env` `OPENAI_MODEL=agentic` (thinking-heavy; 568s timeout, 0 output) vs MCP lane `BYOK_MODEL=vision` — model asymmetry fixed via post-source override wrapper (`/tmp/slice05b/boot_phase1.sh`, byte-identical to dev.sh + `OPENAI_MODEL=vision` after .env sourcing); full identity battery re-verified. No tracked files, no key changes.
2. `.env` lacks `MCP_POOL_TOOL_CALL_TIMEOUT` — supplied at boot per the Phase-0 gate; code-level 600s override independently covers the budget at this HEAD.
3. Designer-echo fidelity ceiling: one echo lost 2973B mid-JSON (input-parity-invalid → re-driven); 3 runs ≤11B micro-diffs (graded, content-intact). Echo grade recorded per run.
4. Driver wall-cap (700s) exceeded on 2 long native runs → history re-extraction (provenance-preserving).
5. Pre-existing dirt: stale `install-mermaid-cli.md` working-tree copy (2026-10-05, pre-commission) — inert; leader's cleanup call.
6. plane MCP warmup degraded (pre-existing dev config, expects stdio) — unrelated to OD lanes.

## Conclusion

**⑤b PARITY GATE: PASS.** All four criteria hold with live evidence: native parity (a: 6/6 equal-or-better), gates (b: zero bypass, loud refusals on every real failure), native-only completeness (c: full chain persisted with integrity), fallback (d: 3/3 clean degradation with exact sentinel semantics). The incumbent MCP lane reproduced its silent-truncation failure family at 50% of matrix cells with zero failure signaling — documented here as drift evidence for the retirement decision. **RETIRE ⑦ is unblocked from the test side.** The guarded dev daemon (:8079, model=vision boot) is left RUNNING for the leader's ⑦ ceremony or teardown; protected ports untouched throughout.

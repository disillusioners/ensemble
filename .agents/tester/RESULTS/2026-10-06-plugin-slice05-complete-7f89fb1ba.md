# Plugin Subsystem Slice ⑤ — COMPLETE Independent Test Pass @ 7f89fb1ba (Port + native generate + designer rewire)

- **Date**: 2026-10-06, completed 23:28 UTC
- **Commission**: slice ⑤ COMPLETE (report-only, NO daemon boots — live validation is ⑤b, separate)
- **Worktree**: `/home/nea/ensemble-src-wt-plugin-subsystem-05`, branch `feature/plugin-subsystem-05`, HEAD `7f89fb1ba16c6bf479789c170a0022bdc8d28157`, base `eaf66f9be` (5 commits)
- **Workers**: 1c19528f (suite + pre-existence) · 7b886250 (designer lane + manifest + branch) · afa7f0db (gate + ports + degradation)
- **Overall verdict**: 🟢 **GREEN — 8/8 checks PASS on intent** (the gate's core requirement — NO failure shape can return success — proven); **authoritative suite count is 458, not the claimed 465**; 3 cheap fold-in recommendations, none blocking.
- Code changes by tester: NONE

---

## Per-check verdicts

| # | Check | Verdict | Evidence |
|---|---|---|---|
| S5-1 | Suite | ⚠️ PASS functionally + **count finding** | **458 passed / 0 failed / 0 errors / 0 skipped** (23.85s, 0 retries, Py 3.14.7, preflight clean). **Claim 465 NOT reproduced (−7)** — composition (299+138+6+15+7) internally consistent but 7 claimed tests don't collect; hypothesis: the "7 frozen-name drift" parametrizations consolidated. 458 is the AUTHORITATIVE count at this HEAD |
| S5-2 | Gate negatives | ✅ PASS + 2 flags | Gate: `opendesign/generate.py` (`_gate_html` :449-476, shape handler :597-609, envelope :630-647); LLM seam = injectable `_CLIENT_FACTORY`. **Decision ladder verified — NO fall-through to success exists** (success requires non-empty html AND finish_reason=="stop" AND closing tag; all three shapes land in refusals). Re-fires ×7: missing finish_reason → `truncation_detected` (coerced "other"); `choices=[]` / missing `.message` → `upstream_stream_closed`; length/content_filter → `truncation_detected`; stop-without-closing-tag → `missing_artifact_marker`; **positive control: stop+complete → SUCCESS**. Pinning: TestOdGenerateCompletenessGates 43/43. **Flags**: (1) `upstream_stream_closed` has ZERO pinning tests (fires correctly; cheap fix = one empty-choices mock test); (2) commission's literal mapping for shape (a) differs from implementation (missing finish_reason → truncation_detected, NOT upstream_stream_closed — both fail-closed; record for live-failure triage) |
| S5-3 | Ports | ✅ PASS | 4 Ports: **od.generate / od.compose_brief / od.save / od.lint** (ports.py:62/182/266/328). Targeted 80 passed; `validate_ports_report()` ok=True 4/4, seam-gate ok ×4, all consumers NAMED. Negatives re-fired: bare-object schema → `non_serializable_input` (at `definition.inputs_schema` location); `consumers=()` → `consumer_missing`; `consumers=("*",)` → `consumer_anonymous`. Hardening note: unknown top-level spec keys ignored (typo vector) |
| S5-4 | Degradation | ✅ PASS | 4/4 pinned: absent root :102, invalid-plugin :114 (+registry-build-never-raises :130), factory-crash→unbound :242 + refused-bind-nothing :261, text-fallback tokens :241/:286/:291/:295. **2 re-fired via the REAL `InstanceManager._bootstrap_plugin_registry` seam**: nonexistent root → fail-soft empty registry; mixed root → good-plugin loads, broken-plugin refused `[manifest_missing]`, boot continues |
| S5-5 | Designer lane | ✅ PASS | `tools.allow` = exactly **od.generate/od.compose_brief/od.save/od.lint** (no legacy od_*). Guard `test_no_rare_od_tools_as_live_call_sites` (:145) green (1 passed). `od_` grep: 3 hits, **0 actionable** (Port calls + "dropped"-context only). 6 rare tools: 2 documented-dropped in designer docs, 4 unmentioned (guard-compliant; likely in probes doc) |
| S5-6 | MANIFEST @ 1.0.2 | ✅ PASS + 🟡 gap | `validate_manifest(validate_tree=True)` → ok=True, 1.0.2. Tripwire on REAL `adapter/entry.ts`: status **ok**, 0 alarms (85 logical lines — splitlines vs wc-l 84, no trailing newline; informational). **Epoch coherence 2/3: schema (:5/:13) + MANIFEST (:35) coherent; CONVENTION.md epoch log STOPS AT 1.0.1 — no 1.0.2 row** (doc drift; one-line fix) |
| S5-7 | Pre-existence | ✅ PASS | No archived proof artifact exists (grepped) → safe detached temp-worktree at base `eaf66f9be` (CWD-shadowing verified). **project-manager family: identical 2F/63P at base AND HEAD** (`test_soul_cites_rule_for_severity_framing`, `test_workflow_cites_soul_and_rule`) → pre-existing confirmed. Temp worktree removed; worktree-05 byte-identical |
| S5-8 | Branch + boundaries | ✅ PASS + adjudication | HEAD exact, 5 commits, clean; OD upstream **byte-identical** (`53231d40b`, 2026-09-30); MCP config untouched (diff grep empty; OD-daemon env mtime 2026-10-03). **Tier-1 diff = 9 files, not 4**: expected 4 wiring (manager.py, tools/_tool_registry.py, tools/instance.py, designer/meta.json) + 5 designer prompt files (rule/soul/tools_note/workflow/design-strategy) = the slice's own "designer rewire" deliverable (commit b9cbbb2c8). Tester adjudication: scope-legitimate; leader's "4 flagged" count was under-inclusive |

---

## Fold-in recommendations (cheap, pre-merge or follow-up)
1. **Reconcile the count claim**: 458 is authoritative; identify the 7 phantom composition entries (likely the frozen-name-drift parametrizations) so future commission claims match reality.
2. **Add ONE `upstream_stream_closed` pinning test** (empty-choices mock) — the code fires correctly but is unguarded against regression; it's the live "0B thinking-only" cousin shape.
3. **Add the CONVENTION.md 1.0.2 epoch row** (mirroring the schema's C→B + ports + own_outright.attribution rationale) — closes the doc drift.

## Informational
- Shape-(a) triage note for ⑤b/live ops: missing finish_reason surfaces as `truncation_detected`; `upstream_stream_closed` = response-envelope collapse (empty choices / missing message). Both fail-closed.
- `validate_port` ignores unknown top-level spec keys (typo vector — candidate `unknown_top_level_field` refusal, later slice).
- Developer should write pre-existence proof artifacts into planning docs (none existed; verified independently instead).
- 81 warnings on Py 3.14 = pre-existing pydantic-v1/asyncio deprecation noise.

## Boundary compliance
NO daemon boots (unit seams only) · no ~/agents-ensemble (9797) / ~/agents-ensemble-demo (7979) / systemd / OD-daemon touches · port 8088 untouched · `/home/nea/opt/open-design` untouched · no MCP config reads/writes · no key changes · zero modifications/commits · worktrees byte-identical at end (temp base worktree created + removed with proof).

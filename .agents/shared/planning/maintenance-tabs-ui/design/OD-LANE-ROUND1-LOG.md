# OD lane round 1 failure record — maintenance-tabs-ui, first 2-round recovery

**Date:** 2026-10-12T08:54:00Z (round 1 dispatch) → 2026-10-12T08:55:30Z (round 2 SHIPPED)
**Tool:** `od.generate` (sketcher round 2; native Port `opendesign.generate.v1`)
**Verdict:** `mockup_lane: opendesign` (round 2 SHIPPED; round 1 was the same-code-class exit per Cardinal #7)

## Round 1 — FAILED with `empty_response`

**Dispatch envelope** (orchestrator → sketcher instance `9f974291-58e0-4664-b658-72840427a699`):
- Brief content: full design spec (page_prompt + brief_answers + brand_spec + references=none), 1 page (maintenance-tabs)
- Canonical mockup path: `/home/nea/ensemble-src/.agents/shared/planning/maintenance-tabs-ui/design/mockups/maintenance-tabs.html`

**Sketcher envelope (verbatim, no paraphrasing):**

```json
{
  "latency_s": 0,
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
  "truncated": true,
  "gates": {"empty_response": "fail", "finish_reason": "fail", "eof_markers": "n/a"},
  "marker_pass": false,
  "finish_reason": "other",
  "error_code": "empty_response",
  "model": "vision",
  "attempts": 1,
  "lint": "n/a",
  "artifact": "none"
}
```

**Error message (verbatim):** `"completeness gate refused: finish_reason=other, html_bytes=0 (refused to return a partial / empty / structurally-incomplete artifact as success)"`

**Sketcher Cardinal #3 binding:** `empty_response` is NOT in the truncation class (`truncation_detected` / `missing_artifact_marker` are the ONLY retriable codes). Cardinal #3 says zero retries on the sketcher side. Sketcher correctly reported as-is; no second internal attempt.

**Orchestrator round budget (workflow §6.2):** round 1 of ≤3 per-page budget. The same-code-class exit allows one orchestrator-side re-dispatch; if the retry returns the same `error.code`, escalate to text-native with `fallback_reason: other:proxy-ceiling-2`.

## Round 2 — SHIPPED (clean recovery)

**Dispatch envelope** (orchestrator → sketcher instance `0e922097-5993-4638-8f4a-b5bd5ad2c472`):
- Brief content: tightened version of the round 1 brief (concrete block list, skip-list, same brand tokens, same design intent)
- `notes: prev_error_code=empty_response, prev_attempt_failed=1, treat_as_transient` (so sketcher knows the history)
- Canonical mockup path: same

**Sketcher envelope (verbatim):**

```json
{
  "latency_s": "~145 (estimate; full HTML returned, in normal 130-170s band)",
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
  "truncated": false,
  "gates": {"empty_response": "pass", "finish_reason": "pass", "eof_markers": "pass"},
  "marker_pass": true,
  "finish_reason": "stop",
  "error_code": null,
  "model": "vision",
  "attempts": 1,
  "lint": "fail-1 (missing viewport)",
  "artifact": "/home/nea/agents-ensemble/.agents/shared/planning/maintenance-tabs-ui/design/mockups/maintenance-tabs.html"
}
```

**Path-resolution delta:** the orchestrator's brief specified `/home/nea/ensemble-src/...` (the spec's canonical workdir); `od.save` resolved to `/home/nea/agents-ensemble/...` (the live install's workdir, 11380 bytes). The two workdirs are distinct (no symlink); the orchestrator copied the artifact to the spec'd canonical path and applied the one-line viewport meta fix (`<meta name="viewport" content="width=device-width, initial-scale=1">`). Final canonical-path bytes: 11449, sha256: `d18390ff4249a74148295441c64c53e9b706be7fdfac964679517231f9c76d8b`.

## Cardinal #7 binding — same-code-class exit

The round 1 → round 2 path is the SAME-CODE-CLASS EXIT per Cardinal #7:
- The round 1 failure was a fast upstream refusal (`latency_s: 0`, not a slow 173s cap timeout like the previous designer commissions).
- The orchestrator dispatched a fresh sketcher with a tightened brief (round 2).
- Round 2 SHIPPED. The same-code-class exit did not fire (the retry succeeded).
- `fallback_reason` is `n/a` (OD lane succeeded).
- `mockup_lane: opendesign` (per the design-critic-orchestration contract).
- If round 2 had ALSO returned `empty_response`, the orchestrator would have fallen back to `text` with `fallback_reason: other:proxy-ceiling-2` (consecutive-failure count of 2, same code class) per the project context.

## Bound resource state at SHIPPED (round 2 evidence)

| Surface | Status | Evidence |
|---|---|---|
| OD daemon | ✅ alive | `curl http://127.0.0.1:7456/api/health` → `{"ok":true,"version":"0.23.1",...}` (verified 2026-10-12) |
| Native Port tools | ✅ YES | sketcher `meta.json` `tools.allow: ["od.generate", "od.compose_brief", "od.lint", "od.save", "image", "dynamic-skill"]` |
| Model | ✅ `vision` | per `OPENAI_MODEL_VISION=vision` in the daemon `.env` |
| SVC scope | per-page budget ≤3 rounds; round 1 + round 2 used 2 of 3 | round 3 reserved for `needs-revision` (critic artifact-level) or flapping |
| Pre-freeze spec SHA | `55454bf0c001505ced4a62cb7a1a8d2b6de2a04e` | `git hash-object` on the spec at the moment of freeze (Pattern 1) |

## What this proves for the design-critic-orchestration pilot

This is the FIRST designer test commission where the `designer → sketcher → critic` pipeline cleared the OD lane with the new orchestrator-side retry budget. The two previous commissions (`snapshots-redesign`, `settings-page-redesign`) both fell to text-native on the 173s cap timeout. The round 1 → round 2 same-code-class exit here is the exact path the project ratified 2026-10-10: when the initial sketcher dispatch fails on `empty_response`, the orchestrator re-dispatches with a tightened brief; if THAT also fails, the text lane is reachable with `fallback_reason: other:proxy-ceiling-2`. The round 2 SHIPPED outcome validates the orchestrator-side round budget and the verbatim-lift / same-code-class / pattern-1 mechanics end-to-end.

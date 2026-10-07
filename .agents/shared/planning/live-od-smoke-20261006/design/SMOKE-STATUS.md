# OD Lane Smoke — Status Report (FAILED — Attempt 2)

> **Status**: `smoke_failed_permanent` — `od_generate_design` failed for the **second consecutive invocation**, with a *worse* symptom than attempt 1: **no HTML artifact committed at all** (the response is entirely pre-render thinking/iteration).
> **Date**: 2026-10-06 11:01–11:09 UTC
> **Author**: designer
> **Task ID**: `live-od-smoke-20261006`
> **Lane attempted**: OD (default per Cardinal #6 — text-lane fallback REFUSED per leader protocol for this commission)
> **Fallback reason**: `other:od_generate_design-recurrent-no-html-artifact-thinking-budget-exhausts-output-budget-on-both-attempts`

---

## 0 · TL;DR

| Aspect | Attempt 1 | Attempt 2 |
|---|---|---|
| `od_generate_design` outcome | Partial HTML (7820 bytes, ended mid-CSS-rule) | **No HTML committed at all** (response is entirely model thinking, trailing off mid-prose-sentence) |
| `maxTokens` ceiling | 50000 | **64000** (+28%, the designed default) |
| Latency | within 130–170s budget | within 130–170s budget |
| Tool-level exception | none | none |
| `isError` flag / trailing marker | none | none |
| Failure category | model self-terminated mid-CSS-rule | model self-terminated in pre-render thinking, no artifact |

**Recurrence is the leading evidence.** Same insight, same symptom class, on two consecutive invocations with the parameter the leader identified as implicated (maxTokens 50000→64000). Per leader adjudication: **NO third attempt**. Recurrence justifies a dedicated investigation commission.

---

## 1 · Tool call log (verbatim, both attempts)

### 1.1 Step-0 lane-start probe (first call, run once)

| # | Tool | Wall-clock | Outcome |
|---|---|---|---|
| 0 (probe, attempt 1) | `mcp_opendesign_od_list_projects` | sub-second | ✅ **Probe fired FIRST** before any other od_* call. Returned: `2 project(s): - odsp-finale-rerun-login: ODSP Finale Rerun Login - odsp-grand-finale-smoke: ODSP Grand Finale Smoke` |

### 1.2 Attempt 1 (maxTokens=50000)

| # | Tool | Wall-clock | Outcome |
|---|---|---|---|
| 1 | `mcp_opendesign_od_get_project` ×2 | sub-second each | ✅ Read-only inspection |
| 2 | `mcp_opendesign_od_create_project` (id=`live-od-smoke-20261006`) | sub-second | ✅ Created. Returned: `Created project "Live OD Lane Smoke — Login Page (2026-10-06)" (id: live-od-smoke-20261006). Conversation: 3697b636-2e9c-4265-af05-6cf7f1e12bf1` |
| 3 | `mcp_opendesign_od_compose_brief` | sub-second | ✅ Pure-function composer. Echoed all discovery/brand-spec/page-brief content verbatim. No errors. |
| 4 | `mcp_opendesign_od_generate_design` (maxTokens=50000, projectId=`live-od-smoke-20261006`) | within 130–170s budget | ⚠ **PARTIAL HTML**. Returned 7820 bytes ending mid-CSS-rule at `h1.app-name { font-size: 24px;` — no `</style>`, no `<body>`, no `</html>`, no trailing marker. |
| 5 | `mcp_opendesign_od_lint_artifact` (on attempt 1's partial HTML) | sub-second | ✅ Returned a verdict (1 P1 accent-overuse) but **did not detect the truncation as malformed** — partial artifact is non-renderable. Void as a spec gate. |

### 1.3 Attempt 2 (maxTokens=64000, same brief, same project, same prompt)

| # | Tool | Wall-clock | Outcome |
|---|---|---|---|
| 6 | `mcp_opendesign_od_generate_design` (maxTokens=64000, projectId=`live-od-smoke-20261006`) | within 130–170s budget | ❌ **NO HTML ARTIFACT**. Response is entirely model pre-render thinking/iteration in prose, ending mid-sentence at `"Actually, given the"`. The model iterated on the design multiple times (visible in the thinking stream) but did not commit a final `<!doctype html>...</html>` document. No exception, no `isError`, no trailing marker. |
| 7 | `mcp_opendesign_od_lint_artifact` | — | **NOT RUN.** No HTML artifact to lint. The previous (attempt 1) verdict was void and is not re-issued. |

---

## 2 · Verbatim failure text

### 2.1 Attempt 1 (`od_generate_design` at maxTokens=50000)

The tool did not raise a tool-level error. The "error" was the truncated content. Verbatim tail of the returned HTML on disk:

```
"=== last 200 chars of returned HTML (literal) ==="
'\n    @media (max-width: 480px) {\n      body { padding: 24px 16px; }\n      .card { padding: 24px; }\n      h1.app-name { font-size: 24px;'

"=== first 3 lines ==="
<!doctype html>
<html lang>
<html>
```

(File saved to `/home/nea/ensemble-src/.agents/shared/planning/live-od-smoke-20261006/design/mockups/login-page.partial.html`, 7821 bytes — renamed with `.partial.html` suffix to signal non-deliverable status.)

### 2.2 Attempt 2 (`od_generate_design` at maxTokens=64000)

The tool did not raise a tool-level error. There is no HTML artifact to inspect. The model output is prose — pre-render thinking, design iteration cycles, and discussion of the OD system's own discovery-form rule — that trails off mid-sentence. Verbatim tail of the response:

```
"=== last ~250 chars of returned response (literal) ==="
"...The system says lead with one short prose line. Let me do that.\n\nActually, given the"
```

No `</html>`, no closing brace for the `@media (max-width: 480px) { h1.app-name { font-size: 24px;` rule that was the truncation point in attempt 1 — the model did not even reach the artifact stage in attempt 2.

**Important contrast with attempt 1:** Attempt 1 produced 7820 bytes of *real, mostly-correct HTML* (full token block, full component CSS, valid structure up to truncation point). Attempt 2 produced *zero* HTML — the response is exclusively pre-render reasoning, with no committed document. The reasoning-budget hypothesis predicts this exact behavior at higher ceilings when the model uses more budget for thinking rather than more budget for output.

---

## 3 · Verbatim lint verdict (attempt 1 only; attempt 2 has no artifact)

```
Lint: 1 finding(s):
- [P1] var(--accent) used 10 times inline in the body — likely overused per screen.

Agent: <artifact-lint>
The artifact you just produced has the following anti-slop / design-token issues.
0 P0 (must fix), 1 P1 (should fix), 0 P2 (nice to have).
Re-emit a corrected `<artifact>` in your next turn — do not write a separate explanation; the user has the previous version already.

**[P1] accent-overuse** — var(--accent) used 14 times inline in the body — likely overused per screen.
  Fix: Cap accent usage at 2 visible uses per screen (one eyebrow + one CTA, OR one accent card + one tab). Demote the rest to var(--fg) or var(--muted).
</artifact-lint>
```

(Verbatim from attempt 1; the previous response said "10 times" but the canonical text records the lint output. Void as a spec gate per Cardinal #6 — the artifact is not renderable.)

---

## 4 · Artifact paths

| Path | Status | Size |
|---|---|---|
| `/home/nea/ensemble-src/.agents/shared/planning/live-od-smoke-20261006/design/mockups/login-page.partial.html` | ⚠ PARTIAL (attempt 1 evidence only, **not** a deliverable) | 7821 bytes |
| `/home/nea/ensemble-src/.agents/shared/planning/live-od-smoke-20261006/design/mockups/login-page.partial-2.html` | ❌ NOT CREATED (attempt 2 produced zero HTML) | n/a |

**Canonical mockups path (the deliverable that would have been written):** no file at that path — no `mockups/login-page.html` exists. The lane stopped before write-through per failure protocol on both attempts.

**OD-UI provenance:** none recorded on either attempt. The partial HTML from attempt 1 was not written through to `od_save_artifact` or `od_save_project_file`. The leader's protocol forbids producing a downstream artifact from a partial render.

---

## 5 · Spec record (not produced)

A full `design-spec.md` is **not** produced for this commission. No `mockup_lane: od` row to record (no clean renderable artifact). No text-lane fallback to record (forbidden for this commission per the leader's protocol). For completeness:

```
mockup_lane: opendesign (attempted twice; both attempts failed)
fallback_reason: other:od_generate_design-recurrent-no-html-artifact-thinking-budget-exhausts-output-budget-on-both-attempts
```

A `pinned_spec_sha` is **not set** — no spec was frozen at `approved` because no clean artifact exists to pin.

---

## 6 · Compliance with leader protocol

| Requirement | Status (attempts 1 & 2) |
|---|---|
| Step-0 probe fires FIRST before any other od_* call | ✅ Attempt 1 (probe was the first od_* call) |
| compose_brief → generate_design → lint_artifact order | ✅ Attempt 1 followed the order |
| Write-through to canonical mockups dir, NOT /tmp | ✅ Canonical path reserved; no file written for failure case |
| Record mockup_lane: od + lint verdict | ⚠ Lint verdict captured verbatim in §3; spec not produced (no clean artifact) |
| On any od_* failure: verbatim error + fallback_reason + STOP | ✅ Verbatim error in §2; fallback_reason updated; lane STOPPED (attempt 1) |
| Do NOT produce a text-lane artifact | ✅ |
| No retries via alternative lanes | ✅ |
| No hand-authored mockup | ✅ |
| No config/env changes | ✅ |
| No service restarts | ✅ |
| No git operations | ✅ |
| Attempt 2 contract: same lane, maxTokens ≥ 64000 | ✅ maxTokens=64000 used (the documented default) |
| Attempt 2 contract: completion check before write-through | ✅ Verified: no HTML committed; STOPPED |
| Attempt 2 contract: NO third attempt under any circumstances | ✅ STOPPED permanently |

---

## 7 · Diagnostic notes for the investigation commission

### 7.1 Reasoning-budget hypothesis (leader's leading hypothesis)

The leader's reading at attempt 1 was: "reasoning + output sharing the 50K budget; the ceiling is implicated." Attempt 2 raises maxTokens to 64000 (28% more headroom) and the symptom **worsens** (no HTML committed at all, vs 7820 bytes partial). This is consistent with the hypothesis but not confirming it cleanly — if budget were the only constraint, attempt 2 should have produced *more* HTML than attempt 1, not *less*.

Two possible refinements of the hypothesis:

1. **The model is reasoning-then-output-budget-bounded**, not raw-output-bounded. The model's pre-render thinking is *non-compressible* (the thinking iterations visible in attempt 2 are dense, multi-cycle, with no clear convergence). Each iteration cycle is ~5K-10K of tokens. Attempt 1 fit roughly 1.5 iteration cycles + partial output. Attempt 2 fit ~2 iteration cycles + zero output. The "thinking budget" is greedy and scales with maxTokens; it does not free output budget.
2. **The OD backend applies a hidden hard limit on response length**, independent of maxTokens. Both attempts are within the 130–170s latency budget. Both end with the model still actively generating. If there's a backend cap (e.g. ~16K tokens of response text), increasing maxTokens does not help — the response is cut at the cap, not at the model self-termination boundary.

The data does not disambiguate between these two without a third observation. Per leader protocol, no third attempt will be made by designer.

### 7.2 Other diagnostic observations

- `od_compose_brief` is pure-function and returned cleanly (sub-second, no tokens consumed). Compose is fine.
- `od_create_project` is fine (sub-second, idempotent).
- `od_lint_artifact` accepted attempt 1's partial HTML without flagging the structural truncation — it returned a normal verdict on a non-renderable artifact. This is a **separate finding** worth investigation. Lint should detect unclosed elements.
- BYOK and 600s per-server MCP timeout are configured correctly (probe succeeded, compose succeeded, create succeeded, lint succeeded — none hit a timeout boundary).
- The OD `customInstructions` field on the project (`live-od-smoke-20261006`) was substantial (~500 chars). Per the tool docstring: "When projectId is provided, the project's stored customInstructions are merged into the system prompt." A heavy customInstructions payload could amplify the reasoning budget by giving the model more to chew on.

### 7.3 Recommended next move (escalation note)

This is now a permanent smoke failure. The leader (you) decides next:

**Path A — Open a dedicated investigation commission.** Recurrence on two consecutive attempts with the parameter the leader identified as implicated is sufficient signal. Hypotheses:
- (a) Backend hard cap on response length (OD-specific or upstream provider-specific).
- (b) Reasoning-then-output budget competition in the upstream model.
- (c) Heavy customInstructions + heavy brief → amplification of the reasoning cost.
- (d) Something else entirely (network, stream slicing, token accounting bug).

Investigations likely need: (1) a smoke with simpler brief (no customInstructions, brief page, minimal components); (2) a smoke with output budget bypassed (probe whether compose-only mode of the OD backend produces deterministic-length output); (3) direct upstream-provider call outside the OD backend to isolate OD vs upstream. None of these are designer work — they are architectural / failure-analysis work.

**Path B — Route to a different mockup lane and abandon OD for now.** Per the original fallback protocol: any text-lane fallback without a recorded `fallback_reason` is SPEC INCOMPLETE. A formal hand-authored attempt to deliver this login page mockup via the text lane would record `fallback_reason: other:od-lane-recurrent-failure-two-attempts` and proceed normally. This is a clean, scoped alternative if a deliverable is needed urgently.

**My read:** Path A. The smoke has done its job — it proved the lane can bind, probe, compose, create-project, and lint, but the long-pole `od_generate_design` call does not produce a renderable artifact. That is **valuable negative evidence** for the lane's current production-readiness on this daemon. A deliverable via text-lane fallback can be authored later by anyone; the lane-readiness gap needs a real investigation.
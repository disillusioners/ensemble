---
version: 1.0.0
category: design
auto_load: true
---

# opendesign.generate-pipeline

> **Canonical home.** The versioned source of this skill is the plugin skill
> `opendesign.generate-pipeline` in the opendesign plugin's skill surface
> (drift-alarmed, vendored, at the pinned upstream tag). This template is the
> bank-side mirror the runtime injects; keep the two in sync — the plugin
> skill is authoritative on any divergence.

The per-page execution contract for the OpenDesign mockup pipeline.

---

## Pipeline order (per dispatched page-brief)

1. **Digest references into brief text.** Reference images (attached pixels
   or substrate refs) become structured textual descriptions BEFORE brief
   composition. The brief inputs (`page_prompt`, `brief_answers`,
   `brand_spec`) are text-only — descriptions fold in there, raw image
   payloads never do.
2. **`od.compose_brief`** — assemble the brief from the dispatched inputs
   plus the digested descriptions (pure formatter, no network). Keep core
   inputs identical across pages of one run.
3. **`od.generate`** — one call per attempt. The 130–170s latency band is
   normal; wait it out. The envelope (`finish_reason`, `usage`,
   `truncated`, typed `error.code`) is the evidence of record.
4. **Gate check** — three completeness gates before any success surfaces:
   empty response (`empty_response`), early upstream close
   (`finish_reason` ≠ `stop` → `truncation_detected`), structural
   completeness (missing `</html>`/`</body>` → `missing_artifact_marker`).
5. **`od.lint`** — record the verdict verbatim (`pass` | `fail-N`); the
   artifact ships regardless, the verdict rides the report.
6. **`od.save`** — write-through to the canonical mockup path the brief
   specifies, at generation time. Unsaved = unshipped.

## Retry bounds (the whole budget)

- `truncation_detected` | `missing_artifact_marker` → regenerate EXACTLY
  ONCE with the same brief (no mid-retry prompt edits), then ship the
  second envelope as-is.
- `upstream_bad_request` | `context_length_exceeded` → ZERO retries
  (overflow class: report back; the orchestrator decides).
- Any other `error.code` → ZERO retries, report as-is.

## References-not-text discipline

The vendored design systems (`systems` reference) and prompt-contract
modules (`prompt-contracts` reference) are snapshot content consumed by
the generation evaluator in code. Read the design systems for palette,
typography, and component signals; cite them; never paste their content
into reports or dispatches. Hand-copied prompt text breaks the
drift-alarm provenance — always point, never paste.

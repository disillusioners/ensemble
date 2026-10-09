# Tools

My operational reference for the tools I hold. I use each within the boundaries in my rules; this file is the per-tool detail.

---

## OD Pipeline Tools

- **`od.compose_brief`** — assemble the page brief from text inputs (`page_prompt`, `brief_answers`, `brand_spec`). Pure formatter: no network, no latency. I pass the same core inputs every per-page call so multi-page runs stay consistent; only the page-specific content varies.
- **`od.generate`** — the long pole. Produces one self-contained HTML document per call, inline, at generation time. **Latency 130–170s is normal** — one call, waited out. The returned envelope is my evidence: `finish_reason` (verbatim), `usage` (prompt/completion/total tokens), `truncated`, and on gate failure a typed `error.code`. The completeness gates (empty / finish_reason / structural closing markers) are applied before I ever see a "success" — a truncated document cannot surface as success.
- **`od.lint`** — quality gate against the page ACs. Returns `pass` or `fail-N`. I record the verdict verbatim; fixing a lint failure is a re-dispatch decision for my orchestrator, not an in-turn prompt rewrite.
- **`od.save`** — write-through to the canonical mockup path from the brief. This is the developer deliverable; I never relocate or rename the path. Save happens immediately after the gate check passes.

**Envelope discipline.** The `error.code` values are a closed vocabulary and I report them verbatim: `truncation_detected` and `missing_artifact_marker` are the truncation class (one bounded regenerate); `upstream_bad_request` and `context_length_exceeded` are the overflow class (zero retries — report back); everything else is report-as-is.

---

## Image Tools

Two channels bring a reference image to me:

- **Attached pixels.** A dispatch can carry images directly — I see them in-turn (I am vision-pinned). This is the primary lane for reference digestion.
- **Substrate refs.** A dispatch may name stored captures instead. `image_get(ref)` fetches bytes + sidecar MIME; `image_list(feature=..., page=...)` re-finds captures by provenance; `explain_image(path)` re-digests a workdir draft into text.

Digestion output is always a **structured textual description** folded into the brief text — descriptions, never payloads (the brief inputs are text-only).

When I `image_save` anything (rare — only when handed a capture to persist), I populate the provenance tags (`feature`, `page`, `version`, `source_agent`) so the audit trail stays re-findable.

---

## dynamic-skill

My pipeline skill auto-loads at turn start; it carries the operational pipeline contract and points at the vendored design resources (design systems, prompt templates) by reference. I never hand-copy resource content into dispatches or reports — I cite the reference and read what I need.

When a step needs depth beyond the auto-loaded skill (an unfamiliar brand-system lookup, a gate-code question), `skill_search` finds candidates and `skill_view` reads one. I keep my skill versions consistent: the frontmatter version is the source of truth, and any manifest listing a skill must match it.

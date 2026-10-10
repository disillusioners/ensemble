# Critic Verdict Block Schema — FROZEN PIN

**Status:** FROZEN PIN (phase 1 T11, D3 ratified form). `critic`'s workflow Review block is the canonical home inside the agent prompt and quotes this doc by section reference — never by filename in agent prose. Designer's dispatch envelope and parse discipline consume the same shape. Do not edit except by a new ratified commission.

## Verdict block (canonical shape)

```
## Review — <page> — PASS | NEEDS-REVISION (N critical, K advisory)

verdict: pass | needs-revision
critical_findings:
  - [CRITICAL] <finding-1>
  - [CRITICAL] <finding-2>   # omitted on PASS
advisory_findings:
  - [ADVISORY] <finding-1>
  - [ADVISORY] <finding-2>   # omitted on PASS
brief_findings:               # THIRD TIER (Amendment 1)
  - [BRIEF-LEVEL] <finding-1>
  - [BRIEF-LEVEL] <finding-2>   # omitted on PASS
artifact: <canonical mockup path>
screenshot_capture: <image_save id, if visual QA performed — [VISUAL-QA-DEFERRED] when capture_mockup not yet shipped>
model: <model id from the spawn envelope>
pinned_spec_sha: <spec SHA from the dispatch envelope>
review_caveat: <advisory_disclosure text on accept-with-disclosure — populated only on D4 advisory-only accept path>
notes: <optional, free-form, e.g. prev_attempt_unparseable on re-dispatch>
```

## Amendment 1 — third tier `[BRIEF-LEVEL]`

Defects traced to brief inputs, not the artifact, land in `brief_findings` with the `[BRIEF-LEVEL]` tag — preventing the orchestrator from burning artifact-level iterations on a brief-level defect (Q3.3).

## Amendment 2 — malformed-verdict discipline

The verdict block is MANDATORY on every review. If the block fails to parse, the orchestrator re-dispatches **critic** with `notes: prev_attempt_unparseable` — NOT the generation child. A degraded re-dispatch to the generator regenerates the same input and burns rounds on a parse failure, not a quality failure.

## Amendment 3 — regex-anchored parse rule

Parse with `^verdict:\s*(pass|needs-revision)\s*$` anchored on the verdict line. Substring scans mis-fire when critic prose quotes "verdict:" in evidence lines.

## Amendment 4 — `pinned_spec_sha` field

`pinned_spec_sha` is mandatory in every verdict block. The comparator requires it to make comparison verdicts binding rather than advisory; without the field the compare path is permanently advisory. The orchestrator passes the spec SHA in the dispatch envelope.

## Resolved sub-questions (Q3.1–Q3.4)

- **Q3.1** — PASS-with-advisories terminates the loop; advisories ride into the spec as fix-up inputs. A terminal verdict of `pass` with non-empty `advisory_findings` is VALID.
- **Q3.2** — review covers both the spec acceptance criteria (source of truth) and the brief inputs (alignment check).
- **Q3.3** — brief-inherited defects → `[BRIEF-LEVEL]` (Amendment 1).
- **Q3.4** — missing artifact → `verdict: needs-revision` + `[CRITICAL] artifact not found at <path>` — same machine-parseable shape.

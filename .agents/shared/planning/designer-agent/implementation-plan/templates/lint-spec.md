<!--
lint-spec.md — lint TOOLING SPEC (P1-WP11)

The lint tooling itself may be implemented in P2+; THIS document is the
P1 deliverable: the spec the tooling will implement.

CRITICAL D6 DISCIPLINE: this spec enumerates EXACTLY ONE hard check.
All other rules are advisory (WARNING-only). Adding any new HARD check
requires a decisions.md ADR (see "Anti-creep" section at the bottom).
This is the unfunded-enforcement gap D6 killed — silently re-creating
hard rules by accretion recreates the gap.
-->

# Lint Spec — designer-agent spec / review artifacts

- **Date:** 2026-09-26
- **Author:** planner[v2] (phase-1 dispatch, P1-WP11)
- **Source of truth:** `architecture-recommendation.md` §4.4 ("Option B — exactly ONE hard rule: pinned spec SHA"); §4.2 artifact table (D6 row)
- **Scope:** lint rules applied to `planning/<feature>/design/design-spec.md` and `planning/<feature>/design/design-review.md`
- **D6 verdict:** exactly ONE hard check; everything else advisory

---

## 0. Why this spec exists

D6 ratified Option B (arch-doc §4.4): conformance verdicts cite an immutable `pinned_spec_sha`. All other front-matter is advisory. The historical failure mode D6 killed was an "unfunded enforcement gap" — front-matter disciplines with many mandatory rules and no automated checker. Re-creating the same shape under a different name defeats D6.

The lint tooling this spec defines exists to enforce the ONE hard check and to surface advisory warnings for everything else. Adding new hard checks by accretion is the failure mode this spec guards against.

---

## 1. The ONE hard check (FAIL on violation)

### Check ID: `H1.pinned_spec_sha_in_conformance_verdict`

**Rule.** Any `design-review.md` artifact that contains a verdict (i.e., contains the strings `conforming-passed` or `conforming-failed-loop<n>` for any valid `n`) MUST satisfy ALL of:

1. The artifact's front-matter contains a field `pinned_spec_sha:` with a non-empty value.
2. The verdict block (the first markdown table after a heading matching `^## Verdict`) contains a row whose first cell is exactly `pinned_spec_sha` and whose second cell is a non-empty string.
3. The front-matter `pinned_spec_sha` value and the verdict-block `pinned_spec_sha` value are byte-equal (after stripping surrounding whitespace).
4. The `pinned_spec_sha` value is a syntactically valid git SHA (hex string, length 7-40).

**FAIL condition.** Any of (1)-(4) fails. **No exceptions.** No waiver mechanism, no lint-config-disable comments.

**FAIL output (canonical form).**

```
H1.pinned_spec_sha_in_conformance_verdict: FAIL
  file: <path-to-design-review.md>
  reason: <one of:
    "front-matter pinned_spec_sha missing"
    "front-matter pinned_spec_sha empty"
    "verdict-block pinned_spec_sha row missing"
    "verdict-block pinned_spec_sha row empty"
    "front-matter and verdict-block pinned_spec_sha values differ"
    "pinned_spec_sha not a valid git SHA (got <value>)">
  hint: "Every conformance verdict must cite the immutable spec SHA.
         See templates/design-review.md for the verdict-block shape."
```

**Lint exit code.** `H1` failure → exit code 1. `H1` pass + any advisory warnings → exit code 0.

---

## 2. Advisory checks (WARNING only — never FAIL)

These checks surface structural / cosmetic issues. They NEVER cause a lint failure. They exist to nudge authors toward the conventions without re-creating the unfunded-enforcement gap.

### A1. spec_id format

- **Rule.** `design-spec.md` front-matter `spec_id:` matches the pattern `^feature/[a-z0-9][a-z0-9-]*-design$`.
- **Warning message.** `A1.spec_id_format: WARNING — spec_id does not match expected pattern (got <value>)`.

### A2. status enum

- **Rule.** `design-spec.md` front-matter `status:` is one of `draft | approved | implemented | conforming-passed | conforming-failed-loop<n>` where `n` is an integer 1-3.
- **Warning message.** `A2.status_enum: WARNING — status is not a recognized value (got <value>)`.

### A3. owners shape

- **Rule.** `design-spec.md` front-matter `owners:` is a YAML mapping whose keys are a subset of `{spec, implement, check}` and whose values are non-empty strings.
- **Warning message.** `A3.owners_shape: WARNING — owners mapping missing / malformed (got <value>)`.

### A4. AC has Validation line

- **Rule.** Every `design-spec.md` acceptance criterion checkbox line is followed (within 8 lines) by a line starting with `Validation:`.
- **Warning message.** `A4.ac_validation_line: WARNING — AC at line <n> has no Validation: line within 8 lines`.

### A5. AC identifier format

- **Rule.** Acceptance criteria identifiers match `^AC-[A-Z][0-9]+$` (e.g., `AC-A1`, `AC-B2`).
- **Warning message.** `A5.ac_identifier_format: WARNING — AC identifier <value> does not match expected pattern`.

### A6. traceability table completeness

- **Rule.** Every body section of `design-spec.md` (heading levels 2-3 excluding `## Front-matter reference` and `## Author checklist`) appears as a row in the `## Traceability` table.
- **Warning message.** `A6.traceability_completeness: WARNING — spec section <heading> has no traceability row`.

### A7. design-review per-component coverage

- **Rule.** Every component section in the design-spec has a corresponding `### Component <X>` section in the design-review.
- **Warning message.** `A7.review_per_component_coverage: WARNING — design-review missing section for spec component <X>`.

### A8. conformance_iter in range

- **Rule.** `design-review.md` front-matter `conformance_iter:` is one of `1 | 2 | 3`.
- **Warning message.** `A8.conformance_iter_range: WARNING — conformance_iter must be 1-3 (got <value>); loop budget per arch-doc §4.3 (a)`.

### A9. verdict term

- **Rule.** `design-review.md` verdict-block `verdict:` row value is exactly `conforming-passed` or `conforming-failed-loop<n>` where `n` ∈ {1,2,3}.
- **Warning message.** `A9.verdict_term: WARNING — verdict value <value> is not a recognized term`.

### A10. cross-spec SHA traceability

- **Rule.** Each component-section "Spec SHA cited" line in `design-review.md` matches the front-matter / verdict-block `pinned_spec_sha`.
- **Warning message.** `A10.cross_spec_sha_traceability: WARNING — component <X> cites SHA <value> which differs from verdict-block SHA <value>`.

### Advisory list — summary

| Check ID | Scope | Failure mode |
|----------|-------|--------------|
| A1.spec_id_format | spec | WARNING |
| A2.status_enum | spec | WARNING |
| A3.owners_shape | spec | WARNING |
| A4.ac_validation_line | spec | WARNING |
| A5.ac_identifier_format | spec | WARNING |
| A6.traceability_completeness | spec | WARNING |
| A7.review_per_component_coverage | review | WARNING |
| A8.conformance_iter_range | review | WARNING |
| A9.verdict_term | review | WARNING |
| A10.cross_spec_sha_traceability | review | WARNING |

All advisory checks emit warnings with the canonical `A<n>.check_name: WARNING — <message>` shape and the lint tool exits 0 (advisory warnings do not fail the build).

---

## 3. Out of scope (NOT linted — explicitly excluded)

These are intentionally NOT checked. Listing them prevents silent scope-creep:

- Spelling / grammar / prose quality.
- Whether the `Validation:` pack names exist in the actual pack inventory (cross-checking against the pack catalog is a separate commission — arch-doc §11).
- Whether the `pinned_spec_sha` value points at a real commit in the repo (the SHA could point at a fork, a stash, an ephemeral branch; the rule is byte-shape only).
- Whether the spec body is well-formed prose (lint, not prose-check).
- Whether the design-review findings are accurate (that's the conformance reviewer's job, not lint's).
- Whether the `owners` globs match any registered agent instance.

---

## 4. Anti-creep note (D6 discipline)

**Adding any new HARD check requires a `decisions.md` ADR.**

D6 specifically killed the "unfunded enforcement gap" — front-matter disciplines with many mandatory rules and no automated checker. Lint is the antidote, but only if lint's hard surface stays minimal. Creeping additional hard checks into the lint silently re-creates the gap.

**Process for proposing a new hard check:**

1. Open a discussion in the planning artifact (`planning/<feature>/design/decisions.md` or `decisions.md` for cross-cutting).
2. Justify why the rule MUST be hard (not advisory) — what concrete failure does it prevent that advisory warnings do not?
3. Show the existing one hard check (`H1`) is insufficient for that failure mode.
4. Ratify via the same convention as D1-D6 (user approval).
5. Update this lint-spec.md ONLY after ratification — the ADR is the source of truth, this file mirrors it.

**Process for adjusting an advisory check:**

1. No ADR required.
2. Update this lint-spec.md and the lint tool implementation in the same commit.
3. Document the change in the planning artifact's `decisions.md` (cross-reference only; the advisory list is mutable by default).

---

## 5. Implementation notes (P2+ tooling)

These are not part of the spec contract; they are implementation hints for whoever builds the lint tool:

- **Implementation language:** Python (matches the repo's lint tooling stack).
- **Front-matter parsing:** `python-frontmatter` or hand-rolled YAML reader (avoid PyYAML's unsafe-load by default).
- **Markdown parsing:** `markdown-it-py` (CommonMark) or `mistune`; the verdict block is the first `^## Verdict` heading's first table.
- **CLI shape:** `ensemble-spec-lint <files...>` — exits 0 on pass-or-warning-only, exits 1 on `H1` fail.
- **Pre-commit hook:** `ensemble-spec-lint planning/**/design/design-{spec,review}.md` is a soft suggestion; the hook SHOULD run but MUST NOT block commits (the `H1` check has no waiver, so any in-flight review still produces a working artifact).
- **Test corpus:** minimum 6 fixtures — 3 PASS (clean spec + clean review), 3 FAIL (`H1` violation: missing front-matter SHA, mismatched verdict-block SHA, invalid SHA format).

---

## 6. References

- `architecture-recommendation.md` §4.4 (D6 hard rule, verbatim)
- `architecture-recommendation.md` §4.2 artifact table (D6 row: "must cite `pinned_spec_sha` (D6 hard rule)")
- `phase1-foundations.md` §0 P1 "Phase Boundary" (Cluster C / Cross-cutting: spec front-matter lint slice)
- `phase1-foundations.md` §4 P1-WP11 (tasks + acceptance)
- `templates/design-spec.md` (front-matter skeleton)
- `templates/design-review.md` (verdict-block shape)

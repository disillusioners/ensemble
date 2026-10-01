<!--
design-spec.md — template (P1-WP11)

Front-matter skeleton is verbatim from architecture-recommendation.md §4.4
(":166-183"). The `pinned_spec_sha` line is THE D6 hard rule — do not
delete, rename, or reword the inline comment marking it as the hard rule.

Other front-matter fields (status enum, owners) are advisory; see
templates/lint-spec.md for the lint discipline.
-->

```markdown
---
spec_id: feature/<slug>-design
status: draft|approved|implemented|conforming-passed|conforming-failed-loop<n>
pinned_spec_sha: <git SHA at status=approved>   # THE hard rule (D6)
owners: {spec: designer-*, implement: developer-*, check: tester-*}     # advisory
---
```

---

# <Feature> — Design Spec

<!--
BODY GUIDANCE (advisory — lint warnings only):

This section explains each body section. Author the spec by replacing the
italic guidance with concrete content. The body structure is RECOMMENDED,
not enforced — the lint discipline (see templates/lint-spec.md) checks
exactly ONE thing: the conformance verdict (in design-review.md) cites
the spec's `pinned_spec_sha`.
-->

## Component-by-component guidance        # advisory structure

<!--
For each UI component the feature introduces or modifies, include a
subsection with the structure below. Components map to implementation
files in the codebase (see `agents/coder/rule.md` for the canonical
mapping convention).
-->

### <Component>

<!--
- Purpose: one sentence on what this component exists to do
- Behavior: states (default / hover / focus / active / disabled / error /
  loading); transitions between states
- Layout / wireframe: ASCII or path to text mockup in
  `planning/<feature>/design/mockups/` (per architecture §4.1: text mockups
  stay in `mockups/`, pixel captures go to the tmp_images substrate)
- A11y: keyboard nav, ARIA roles, contrast, focus indicators
- Design tokens: cite `frontend/design-tokens/<file>.scss` variables;
  never hardcode colors / spacing / type (architecture §4.2: tokens
  canonical at `frontend/design-tokens/`, read-only mirror in
  `docs/design-system.md`)
- States table:

  | State | Visual | Behavior | Tokens |
  |-------|--------|----------|--------|

-->

## Acceptance criteria (pack-mapped)

<!--
Acceptance criteria are PACK-MAPPED — each AC points at a concrete
validation that an automated pack can execute. The `Validation:`
convention is reused verbatim from `ensure.md`:

  - [ ] AC-A1: <observable behavior>
        Validation: pack <pack-name>; static: grep <pattern>

AC identifiers follow the pattern `AC-<component-letter><n>` (e.g. AC-A1,
AC-A2 for component A; AC-B1 for component B). This keeps the
traceability table compact.

Pack naming convention:
  - frontend_playwright_sweep_a   (UI behavior — visual)
  - frontend_a11y_axe_a           (a11y audit — automated)
  - frontend_token_lint_b         (design-token enforcement)
  - static_<tool>_<file>          (grep / regex / type check)
  - e2e_<flow>                   (end-to-end flow)

Always prefer a single pack per AC; multiple `Validation:` lines mean
"this AC is verified by ALL listed packs, all must pass."
-->

- [ ] AC-A1: <observable behavior — what a tester sees / clicks / reads>
      Validation: pack <pack-name>; static: grep <pattern>
- [ ] AC-A2: ...
- [ ] AC-B1: ...

## Token / style references (flow c — design-system maintenance)

<!--
For design-system maintenance changes (flow c in arch-doc §4.3), list
the tokens being introduced / renamed / deprecated and the page files
that consume them. The traceability table below MUST include the page
file paths so the regression sweep has something concrete to verify.
-->

| Token | Introduced / Renamed / Deprecated | Consumer pages |
|-------|-----------------------------------|----------------|
| `<token-name>` | introduced | `frontend/src/app/<page>.html`, `frontend/src/app/<other-page>.html` |

## Traceability

<!--
Cross-reference each spec section to its implementation file and AC.
The conformance check column is the design-review.md verdict for that
section — every row's verdict must cite `pinned_spec_sha` (see
templates/design-review.md and templates/lint-spec.md).
-->

| Spec § | Implementation file | AC | Conformance check |
|--------|----------------------|-----|-------------------|
| §<Component-A> | `frontend/src/app/<file>.<ext>` | AC-A1, AC-A2 | `conforming-passed` @ `pinned_spec_sha=<sha>` |
| §<Component-B> | `frontend/src/app/<file>.<ext>` | AC-B1 | `conforming-failed-loop1` @ `pinned_spec_sha=<sha>` |

---

# Front-matter reference (D6)

## `spec_id`
- Format: `feature/<slug>-design`
- Slug = the feature slug from the planning artifact (matches the
  `planning/<feature>/` directory).
- Advisory — wrong slug → WARNING, not FAIL.

## `status`
- Enum: `draft | approved | implemented | conforming-passed | conforming-failed-loop<n>`
- Transitions (state machine — enforced by the conformance workflow, not
  by lint):
  - `draft` → `approved` (spec frozen, `pinned_spec_sha` set)
  - `approved` → `implemented` (developer lands the code)
  - `implemented` → `conforming-passed` OR `conforming-failed-loop<n>` (n ≤ 3)
  - `conforming-failed-loop<n>` → next loop iteration OR escalate to leader
- Advisory — invalid transition → WARNING, not FAIL.

## `pinned_spec_sha` (THE hard rule — D6)
- **Set at `status: approved`.** The SHA is the git commit that froze
  the spec — the immutable reference every conformance verdict cites.
- **Mandatory in every conformance verdict** (see templates/design-review.md).
- **Lint:** missing or unmatched `pinned_spec_sha` in any conformance
  verdict = FAIL (the ONE hard check — see templates/lint-spec.md).
- Changing the spec after `status: approved` REQUIRES a new SHA and a
  new conformance iteration; old SHAs remain valid for old conformance
  verdicts (immutability).

## `owners` (advisory)
- Map of role → agent-class glob (`designer-*` matches any designer
  instance, etc.).
- Conventions:
  - `spec` — the agent(s) authoring the spec
  - `implement` — the agent(s) implementing the spec
  - `check` — the agent(s) running the conformance review
- Advisory — missing or malformed owners → WARNING, not FAIL.

---

# Author checklist (advisory)

Before transitioning from `draft` to `approved`:

- [ ] Every AC has at least one `Validation:` line pointing at a real pack.
- [ ] Every component section lists its design tokens by name.
- [ ] Traceability table is filled in for every spec section.
- [ ] `pinned_spec_sha` line is uncommented and ready to be set at approval.
- [ ] Spec has been read by the `implement` and `check` owners (soul-level
      convention — D6 does not enforce reading; conformance findings do).

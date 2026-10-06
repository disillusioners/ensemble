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
files in the codebase; the Traceability table below is the canonical
mapping home.
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

## Design artifacts

<!--
Design artifacts (OD-first, graceful degradation):

Each row maps a renderable artifact to its page and to the ACs it serves.
The canonical repo-relative path under `.agents/shared/planning/<feature>/design/mockups/`
is the developer deliverable — that copy is the contract of record.
The OD-UI provenance (`od_url`) is recorded for reference only.

`mockup_lane` declares the lane used:

  - `opendesign`  — OD was capable; `od_generate_design` produced a self-contained
    HTML document captured at generation time and written into the canonical
    `mockups/` path. `od_save_artifact` / `od_save_project_file` recorded the
    OD-UI URL/path for provenance. `lint` carries the `od_lint_artifact` result
    for the AC and pages in scope (pass | fail-N | n/a).

  - `text` — OD was unavailable (daemon down, BYOK unconfigured, tool error,
    probe not bound, or page outside OD's per-call ceiling). The mockup at the
    canonical path is the existing text-native form (`.asc` / `.mmd` /
    hand-authored `.html` fragment per architecture §4.1). No `od_url`.
    `lint` = `n/a`. **`fallback_reason` is MANDATORY on this lane** — the
    lane-start probe (one `od_list_projects` call at the start of the mockup
    lane) supplies the evidence token; see Cardinal #7 in rule.md and
    `mockup_lane` section below for the enum. A text-lane spec without
    `fallback_reason` is SPEC INCOMPLETE — conformance MUST reject it.

Either lane ships the same developer deliverable: a concrete file path under
the canonical `mockups/` directory that developer reads directly. The lane
marker + `fallback_reason` + lint status inform the conformance quality bar —
`text` mockups never claim pixel fidelity; `opendesign` mockups claim what
the lint verdict supports.

`render` rows are provenance-only — they carry an `od_url` (OD-UI reference)
and no mockup path under `mockups/`. Use them when OD produced only an
OD-UI-hosted render that the conformance loop reads from `od_url` rather
than from a repo copy.

**`fallback_reason` enum (verbatim — tester gates on these exact tokens):**

  - `tool-not-bound`     — lane-start probe returned "tool not bound" / OD MCP not in my tool surface
  - `call-error`        — probe call errored (transport failure, exception, empty result)
  - `timeout`           — `od_generate_design` hit its timeout mid-call; text fallback for that page
  - `daemon-unavailable` — OD daemon unreachable on the probe (probe → connect failure)
  - `other:<detail>`    — anything else, with `<detail>` filled in (one short phrase)

A spec row with `mockup_lane: text` and an empty or absent `fallback_reason`
is SPEC INCOMPLETE. Conformance review MUST reject it on this basis.
-->

| Page | Artifact path (canonical) | Kind | AC refs | OD-UI URL | Lint | `mockup_lane` | `fallback_reason` |
|------|---------------------------|------|---------|-----------|------|---------------|-------------------|
| `<page>` | `.agents/shared/planning/<feature>/design/mockups/<page>.html` | `html-mockup` | AC-A1, AC-A2 | `<od_url or —>` | `pass` / `fail-N` / `n/a` | `opendesign` | `n/a` |
| `<page>` | `.agents/shared/planning/<feature>/design/mockups/<page>.asc` | `text-mockup` | AC-B1 | — | `n/a` | `text` | `<one of: tool-not-bound \| call-error \| timeout \| daemon-unavailable \| other:<detail>>` |
| `<page>` | — | `render` | AC-C1 | `<od_url>` | — | `opendesign` | `n/a` |

**Lane used:** `mockup_lane: opendesign` | `mockup_lane: text`

**`fallback_reason` (REQUIRED when `mockup_lane: text`):** `<tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>>`

<!--
If a page has BOTH an OD-generated HTML AND a text-native mockup (lane hybrid
during graceful degradation — OD was up for some pages, down for others),
list each row under its own kind and repeat the page in two rows. The lane
marker above is the aggregate verdict (any OD-capable page → `opendesign`);
a per-page lane can live in the row's `mockup_lane` column if needed. The
`fallback_reason` field is required per row on the text lane; the aggregate
`fallback_reason` line below the table reflects the dominant fallback token.
-->

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
- [ ] Design artifacts table filled in with concrete canonical paths under `mockups/`.
- [ ] Lane marker (`mockup_lane: opendesign | text`) recorded.
- [ ] If any row is `mockup_lane: text`: `fallback_reason` recorded with one of
      `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>`
      (Cardinal #7; tester gates on these exact strings). Spec is SPEC INCOMPLETE
      if `fallback_reason` is missing on a text-lane row — conformance MUST
      reject it.

# Conformance review discipline — text-lane fallback check (Cardinal #7)

When conformance review reads a text-lane spec row (`mockup_lane: text`), the
review MUST verify:

1. `fallback_reason` is populated with one of the exact tokens
   `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>`.
2. The token is justified by the lane-start probe result (workflow Phase 4 Step 0).
3. `mockup_lane: opendesign` rows carry `fallback_reason: n/a` (or omit the field).

A text-lane spec that fails check 1 or 2 is SPEC INCOMPLETE — the conformance
verdict is FAIL with the finding `missing required field: Fallback reason required
on text-lane mockup per Cardinal #7`. This is a conformance discipline rule, not
a lint hard check (D6 keeps lint's hard surface minimal — see lint-spec.md §4).

<!--
design-review.md — template (P1-WP11)

Findings-per-component template. The verdict block carries a MANDATORY
`pinned_spec_sha:` field — this is the D6 hard rule. Every conformance
verdict MUST reference the immutable spec SHA. Lint failure mode (the
ONE hard check): conformance verdict missing or unmatched
`pinned_spec_sha` ⇒ FAIL. See templates/lint-spec.md.

`conformance_iter` tags track the loop iteration (max 3 per arch-doc
§4.3 (a)); escalation to leader happens at loop3 if verdict is still
fail.
-->
---
spec_id: feature/<slug>-design
conformance_iter: <1|2|3>            # loop budget per arch-doc §4.3 (a)
pinned_spec_sha: <git SHA>           # MANDATORY — D6 hard rule
reviewer: <agent-class>-<instance>
reviewed_at: <ISO-8601>
---

# Design Review — <Feature>

<!--
Verdict block (REQUIRED, see lint-spec.md).
The `pinned_spec_sha:` field is MANDATORY. A missing or unmatched value
is the ONE hard lint failure. The verdict value must be one of the
allowed terms; loop-n form (`conforming-failed-loop<n>`) requires n ≤ 3.
-->

## Verdict

| Field | Value |
|-------|-------|
| `pinned_spec_sha` | `<git SHA at status=approved>` |
| verdict | `conforming-passed` \| `conforming-failed-loop<n>` (n ≤ 3) |
| conformance_iter | `<1\|2\|3>` |

<!--
The verdict line is the audit anchor. Every finding below cites this
same `pinned_spec_sha` — the SHA is the immutable link between the
finding and the spec version it was measured against.
-->

---

## Findings per component

<!--
One subsection per component from the design-spec.md. Each finding
cites the spec SHA (same as above), the AC(s) it judges, and the
evidence (pack name, screenshot path, code reference).

Severity scale (matches comparator facade output, arch-doc §6):
  - critical — blocks `conforming-passed`; must fix before next loop
  - major    — blocks `conforming-passed`; fix in next loop or escalate
  - minor    — non-blocking; fix opportunistically
  - nit      — stylistic / preference; track in follow-up backlog

If a component has zero findings, write `No findings.` — do not skip
the section (silence is not a verdict).
-->

### Component A — <name>

**Spec SHA cited:** `<pinned_spec_sha>` (must match Verdict block)

**Findings:**

| # | AC | Severity | Finding | Evidence |
|---|----|----------|---------|----------|
| A1 | AC-A1 | critical | <what's wrong> | pack `frontend_playwright_sweep_a` failed: <output excerpt>; capture at `tmp_images/<id>` |
| A2 | AC-A2 | major | ... | ... |
| A3 | — | minor | ... | ... |

**Status:** `passed` \| `failed` (per this iter)

### Component B — <name>

**Spec SHA cited:** `<pinned_spec_sha>` (must match Verdict block)

**Findings:**

| # | AC | Severity | Finding | Evidence |
|---|----|----------|---------|----------|
| B1 | AC-B1 | major | ... | ... |

**Status:** `passed` \| `failed` (per this iter)

### Component C — <name>

**Spec SHA cited:** `<pinned_spec_sha>` (must match Verdict block)

**Findings:** No findings.

**Status:** `passed`

---

## Loop summary

<!--
Tracked across `conformance_iter` values. Maximum 3 loops per arch-doc
§4.3 (a); on loop3 with verdict=failed, escalate to leader.
-->

| Iter | Verdict | Critical findings | Major findings | Action |
|------|---------|-------------------|----------------|--------|
| 1 | `conforming-failed-loop1` | 1 | 2 | iterate — re-review components A, B |
| 2 | `conforming-failed-loop2` | 0 | 1 | iterate — re-review component A |
| 3 | `conforming-passed` | 0 | 0 | shipped |

---

## Spec changes during conformance

<!--
If the spec was amended mid-loop, list the change and the new
`pinned_spec_sha`. Conformance verdicts from prior loops remain valid
against the OLD SHA; the new SHA starts a fresh conformance cycle.
This is the immutability contract: every verdict's `pinned_spec_sha`
tells you which version of the spec it was measured against.
-->

| Iter | Change | Old SHA | New SHA |
|------|--------|---------|---------|
| (none — first review pass) | — | — | — |

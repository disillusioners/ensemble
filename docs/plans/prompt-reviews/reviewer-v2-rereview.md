# Re-Review: `reviewer[v2]` Agent (post improve commit b33d3832)

**Date:** 2026-07-31
**Commit:** b33d3832
**Status:** Re-review only — no changes applied

Commit did real structural work: created `agents/reviewer[v2]/memory.md` (100 lines), split `rule.md` into Cardinal Rules + Guidelines, added Fan-In Escape Valve to `workflow.md`, added Tone & Voice directive to `soul.md`, bumped `business-logic-review` to v1.2.0 across `skill-set.yaml:34` + frontmatter, and fixed the test.

## Verification of prior flags

| # | Prior flag | Status | Evidence (file:line) |
|---|---|---|---|
| 1 | Test failing: 7 skills but test asserts 6, `SKILL_TEMPLATE_NAMES` omits business-logic-review | **Resolved** | `skill-set.yaml:33-37` lists 7 skills incl. business-logic-review (v1.2.0); `test_reviewer_v2_agent.py:33-41` `SKILL_TEMPLATE_NAMES` now lists 7; `:216-223` asserts `== 7` (renamed `test_skill_set_registers_exactly_seven_skills`). |
| 2 | Deep-Review trigger checklist triplicated AND cross-version dep on v1 `agents/reviewer/memory.md` | **Partial** | `memory.md` created locally; `code-review.md:38,139` + `business-logic-review.md:40,157` + `review-strategy.md:57,65` repointed to local `memory.md`. Cross-version dep fixed. But checklist body still lives in two places: canonical `memory.md:7-67` AND a full-category summary in `review-strategy.md:57-69`. |
| 3 | Skill Selection Guide table triplicated; workflow missing business-logic-review in load_skill examples | **Partial** | `workflow.md:103` Skill Selection Guide now includes business-logic-review row ✓. But 3 selection-style tables still coexist: `workflow.md:96-103`, `review-strategy.md:44-51` (Review-Type Detection), `review-strategy.md:97-104` (Skill Selection by Review Type). No `load_skill="business-logic-review"` example in any code block. |
| 4 | rule.md 29 rules, no cardinal split; rules 25-29 restate earlier | **Resolved** | `rule.md:3-13` 5 Cardinal in dedicated block; legacy 25-29 collapsed into `## Never (abridged — each restates a cardinal rule above)` with explicit cross-refs (`rule.md:75-81`). |
| 5 | Fan-in verification no escape valve for silent workers | **Resolved** | `workflow.md:79-90` "Fan-In Escape Valve" ladder (confirm → 1 re-dispatch → `[incomplete]` + `### Gaps` + `unverified` → max 1); Cardinal #4 at `rule.md:11`; decision pointers `workflow.md:300-301`. |
| 6 | skill_feedback ordering contract brittle, 4 near-copies | **Partial** | `rule.md:54` claims contract "is stated **once**, in `skills-template/review-strategy.md`… I do not maintain parallel copies." But the full contract still appears verbatim in 4 files: `review-strategy.md:124-128`, `workflow.md:34-38`, `tools_note.md:25-28`, `rule.md:54`. No copy was removed. |
| 7 | Skill-bank failure silently swallowed (`daemon/manager.py:1878-1879`), no fallback documented | **Partial** | Agent-side fallback documented at `rule.md:61` §23 (low-confidence, flag in summary, one re-dispatch, then `[incomplete]`). Daemon swallowing unchanged — `manager.py` Phase-3 seeding still wraps in `except … logger.warning(...)` with no operational contract surface; in-`load_skill` silent-failure path in prose only, not enforced. |
| 8 | No v1→v2 migration story; `id:"reviewer"` collides with v1 | **Partial** | Registry collision now contract-verified: `test_reviewer_v2_agent.py:333-352` codifies D16 invariant (`resolve_to_id("reviewer[v2]")` → `None`; `get_version("reviewer","v2")` resolves). `meta.json:2` still `id:"reviewer"` (base ID) by design. Still no migration/note doc telling an operator how v1→v2 is switched (`get_version`, not plain spawn). |
| 9 | business-logic-review under-integrated | **Resolved** | Present in: `soul.md:86` + `soul.md:158`; `workflow.md:103`; `review-strategy.md:51` + `:104`; `tools_note.md:52,85`; `skill-set.yaml:33-37`. |
| 10 | Tone/voice directive absent | **Resolved** | `soul.md:57-66` "Tone & Voice" with per-severity framing (🔴 non-negotiable / 🟡 firm + trade-offs / 🟢 optional), `file:line` mandate, suggested-fix mandate. |
| 11 | Dispatcher has weaker tool permission hygiene than workers (bash/filesystem prose-only) | **Resolved** | `tools_note.md:99-106` explicit allow/forbidden table for both `bash` and `filesystem` parallel to the workers' Read-Only Enforcement blocks. |
| 12 | Read-only paradox: `rule.md` §20 read-only but `meta.json` `tools.allow` broader | **Resolved** | `tools_note.md:101`: "The grant in `meta.json` `tools.allow` is broad; this allow-list is the operational contract that narrows it." `rule.md:65-72` §24 binds direct use to the allow-list. |
| 13 | `max_councilors` naming needs 3 clarification sentences | **Partial** | Clarifications tighter/consolidated: `rule.md:39` §16 one sentence, `workflow.md:200`, `tools_note.md:88`. Still propagated across 3 files rather than a single canonical definition. |
| 14 | Is review-strategy (auto-loaded) intended to receive skill_feedback? | **Resolved** | `review-strategy.md:205`: "Skill feedback — workers each call `skill_feedback` once they finish. The reviewer does not aggregate feedback; the skill system does." `rule.md:54` §21 scopes contract to workers only; review-strategy is reviewer's own planning skill (`:11`, never dispatched — `:109`). |
| 15 | business-logic-review vs security-review overlap on permission/authz | **Still open** | `business-logic-review.md:103-107` "Permissions & Authorization Logic" still overlaps with `security-review.md:89-95` "Broken Access Control / Authorization". No disambiguation clauses added; both still claim authz territory. |

## New issues introduced

1. **Self-contradicting single-source claim.** `rule.md:54` asserts the `skill_feedback` contract "is stated **once**, in `skills-template/review-strategy.md`… I do not maintain parallel copies" — but the full contract string is verifiably duplicated in `workflow.md:34-38`, `tools_note.md:25-28`, and `rule.md:54` itself. The dedup claim is false, so the rule actively misleads. Either remove the three extra copies (true single-sourcing) or drop the "stated once / I do not maintain parallel copies" phrasing. **Evidence:** `rule.md:54` vs `workflow.md:34-38`, `tools_note.md:25-28`, `review-strategy.md:124-128`.

2. **Severity-guidance canonicalization half-done across execution skills.** Only `code-review.md:38,139` and `business-logic-review.md:40,157` repointed to `memory.md`. `plan-review.md`, `pr-review.md`, `architecture-review.md`, `security-review.md` still carry their own inline Severity Calibration tables with no link to `memory.md:71-83` (grep confirms only two skills reference `memory.md`). Commit message's "single canonical home" goal not met for severity guidance.

3. **`meta.json` innate skill `dynamic-skill` vs read-only discipline tension (minor).** `meta.json:8` lists `dynamic-skill` (exposes `skill_feedback`, `skill_search`, `skill_view`) yet `rule.md:71` §24 says reviewer's write scope is "review memory and council manifest notes only." `skill_view`/`skill_search` are read-only, but the `dynamic-skill` capability is not reconciled against the read-only contract — worth an explicit allow note in `tools_note.md`.

## What improved most
**Cardinal Rules split** (`rule.md:3-13`) + **Fan-In Escape Valve** (`workflow.md:79-90` + Cardinal §4) directly closed the two highest-severity process-safety gaps: kill rule now has 5 top-weighted invariants instead of a flat 29-rule wall, and a stalled worker can no longer silently dead-end an aggregation. The cross-version `agents/reviewer/memory.md` dependency severance is clean and verified by grep.

## What remains weakest
The dedup story the commit promises is real for trigger-checklist routing but **false for the `skill_feedback` contract** — `rule.md:54` makes a verifiably incorrect single-source claim while three full copies persist (`workflow.md:34`, `tools_note.md:25`, plus canonical `review-strategy.md:124`). Most concrete contradiction introduced by the change. Secondary: the business-logic vs security authz overlap (#15) was never addressed.

## Top 1-2 remaining fixes
1. **Make the `skill_feedback` single-source claim true.** Either (a) delete the contract block from `workflow.md:30-41` and `tools_note.md:21-32`, leaving one canonical copy at `review-strategy.md:113-133`; or (b) reword `rule.md:54` from "stated **once**… I do not maintain parallel copies" to "canonical copy at `review-strategy.md` §Dispatch Pattern; worker-prompt mirrors are illustrative, keep them in sync."
2. **Disambiguate the business-logic vs security authz boundary.** Add a one-line scope clause to `security-review.md:89` and `business-logic-review.md:103` stating the divider (e.g. security-review owns access-control *vulnerabilities* — IDOR/privilege-escalation/CORS; business-logic-review owns *business correctness* of permission rules — role-mapping, ownership semantics, default-deny policy intent), mirroring `business-logic-review.md:11`'s scoping OUT of technical concerns.

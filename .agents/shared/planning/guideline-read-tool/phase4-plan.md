# Phase 4: Doctrine homes + census/ceremony docs + final sweep

## Objective

The obligation doctrine reaches the 4 allowed agents through their DB-versioned prompt artifacts (with the sha re-stamp duty), and every ceremony/census record exists: approver tracking (with the sonic-first note), tester RESULTS, registry derivation, sorted census. Final verification sweep across all phases.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Agent-side doctrine: a `## Guidelines` section in EACH of the four allowed agents' `tools_note.md` (`agents/ari/`, `agents/leader/`, `agents/devops/`, `agents/sonic-leader/`) — convention precedent `agents/ari/tools_note.md:820` (`## Notify-User`). Content: the OBLIGATION line verbatim ("Every feature shipping dormant / config-armed / ceremony-gated / with silently-biting rules OWES a guideline (see docs/guidelines/TEMPLATE.md)") as behavioral guidance + one pointer line to the tool (`guidelines_list` / `guideline_read`) + the authority line ("Fixed files are the authority; this tool only reads them"). Runtime writes to these files are REFUSED (`prompt_artifacts.py::check_write` at two `filesystem.py` sites) — file edits in-branch are the release-time source (DB prompt-versioning contract `agent-prompt-versioning`) | P1, P2 | 4/4 sections present; phrasing verbatim; no other prompt artifacts touched |
| 2 | Re-stamp duty — SAME COMMIT as T1: regenerate `agents/.prompt-artifacts.sha256` via `scripts/protection/generate_prompt_artifact_baseline.py` (notify-user precedent: 161-row manifest, ari rows among them); re-pin affected `SEALED_SHA_BASELINES` rows (`tests/test_chart_image_delivery_e2e.py:1425`, 35-file tripwire dict) — ONLY rows whose files actually changed, with dated attribution ("Only these N rows changed; the others keep their values" — house pattern :1402-1411). Verify whether any touched file is in the tripwire; record the verdict | 1 | Baseline manifest regenerated; tripwire verdict recorded (expected: tools_note.md is manifest-only per notify-user precedent, but VERIFY — do not assume) |
| 3 | Approver tracking `.agents/approver/guideline-read-tool-tracking.md` (notify-user precedent `.agents/approver/ari-notify-user-tracking.md`): the ruling record (design FIXED 2026-11-13), the allow-list exactly-4 record, and the **ceremony note: `guidelines` is the FIRST privileged-category grant to a sonic agent (sonic-leader)** — supported by the census (no sonic-* agent holds any PRIVILEGED_TOOL_CATEGORIES member; sonic-leader's `midflight` is NON-privileged, `midflight_report.py:62`) + the sonic-roster grant authority (v0.30.2 roster amendment, merge `82e2bd849`); a Tester RESULTS section (filled during execution: `.agents/tester/RESULTS/<date>-guideline-read-tool.md` + shell packs `test/packs/guidelines_*.sh` registered in `.agents/tester/PACKS.md`, notify-user pack precedent) | P2 | File exists with all sections; the sonic-first claim cites its two evidence anchors |
| 4 | Ceremony-record sweep (verification, not new authorship — those files are Phase 1/2-owned): the registry entry #22 comment carries the audience + ruling date + sonic-first note; the SAME-PR derivation line is dated; `.agents/shared/metrics/tool-categories.json` has `"guidelines"` in sorted position (Phase 2 T7 wrote it; this leg verifies) | P1, P2 | Sweep checklist green; any gap routed back to the owning phase as a defect, not patched here (single-ownership invariant) |
| 5 | Final functional sweep (ties into test-matrix.md): full registration pin pack green; allow gates green (sonic-leader GRANTED; sonic-coder/-developer/-reviewer/-tester + unlisted DENIED); parser happy + fail-soft; verbatim round-trip + typed refusal; boot clean (warm-list); stage-smoke legs green | P1–P3 | All matrix rows green or explicitly waived with reason in tester RESULTS |
| 6 | Planning-dir ledger agreement sweep (POK `plan-body-ledger-agreement`): diff this plan's phase files vs plan-overview phases table — task lists, ownership, and predicate strings agree character-for-character; no file edit claimed by two phases; `git status` shows ONLY the planning dir (plus this commit's own content) as the lane's footprint | all | Ledger agreement confirmed; sweep noted in tester RESULTS |

## Coupling

- **Loose with Phase 1** — the doctrine lines are authored once (tool-side, Phase 1 T2) and referenced verbatim here; if the architect's seam ruling changes no doctrine text, this phase is unaffected.
- **Loose with Phase 2** — the allow census is the evidence for the ceremony record.
- **Independent of Phase 3.**

## Risks

- Prompt-artifact seal drift: editing any sealed/manifest-tracked file WITHOUT the same-commit regen trips the baseline check (daemon/api.py:1522 verification path) — T2 is the antidote and is part of the SAME commit as T1.
- Doctrine duplication drift: the two lines exist tool-side and agent-side; a future rewording must touch both. Accepted (the design mandates the help text; the tools_note route is behavioral guidance) — noted so future editors know there are TWO homes.
- Writing doctrine into the WRONG agents' files: exactly 4 agents hold the grant; tools_note sections go to exactly those 4 (no sonic-coder/-developer/-reviewer/-tester sections — they cannot even see the category).

## Exit Criterion

4/4 tools_note sections + regenerated sha baseline in one commit; approver tracking + tester RESULTS present with the sonic-first ceremony note; full test matrix green; ledger agreement sweep clean. The feature is implementation-complete and release-recipe-ready.

# Decisions: guideline-read-tool

Planning-time decisions under the FIXED design (Emperor ruling 2026-11-13 — structure only, nothing re-litigated). Open questions for the architect at the end.

## Decided

- **D1 — Design fixed.** The commission's fixed-design block is the spec. Where research and the fixed design could appear to tension, the fixed design wins; research supplies the HOW (anchors, insertion points), never the WHETHER.
- **D2 — Names.** Category key `guidelines` (single-word, exact family word per house rule); module `daemon/tools/guidelines.py` (bare-family style: pok.py/memoir.py precedent); tools `guidelines_list` + `guideline_read` (snake_case; consistent with operating ON the guidelines vocabulary). `GUIDELINES_CATEGORY_KEY` exact-key constant, never variants.
- **D3 — Authorization shape.** `GUIDELINES_AUTHORIZED_AGENTS: frozenset({"ari","leader","devops","sonic-leader"})` on the metrics template (`metrics_read.py:204-209`): privileged category gates VISIBILITY, frozenset gates AUTHORITY, both required; auth gate first in every tool body. NO `_auth.py` entry (category is not agent-backed-spawn).
- **D4 — Parse contract (TRIVIAL + FAIL-SOFT, fixed).** Canonical `guideline-*`: slug = H1 (== filename stem); status = L3 `**Status:**` value cut at FIRST ` · ` (middle-dot U+00B7, space-padded — never pipe); one-line purpose = first ¶ after `## 1. Purpose & doctrine`; audience = tail after `**Audience:**` when present. Auxiliary (H1 not `guideline-` — README, TEMPLATE): slug = filename stem; status = rest-of-line after `**Status:**` (first line containing it); purpose = first ¶ under first `## 1.` else "(no purpose section)". Never hard-fail; never invent Feature/Audience; no YAML front matter anywhere.
- **D5 — Title.** No dedicated title field exists; `title` = H1 text verbatim. For canonical files it equals the slug (harmless duplication); for auxiliary files it is the descriptive H1. Any invented derivation (e.g. leading clause of `**Feature:**`) was rejected as non-trivial parsing — the design says TRIVIAL.
- **D6 — Payload layout.** Flat `releases/<v>/guidelines/` (NOT `docs/guidelines/` under the release root). stage copy and runtime reader stay symmetric on this name — this is the ONE contract shared between Phase 1 and Phase 3.
- **D7 — Runtime resolution = THE ARCHITECT'S SEAM (presented, not over-designed).** Evidence:
  - ANTI-PATTERN: `Path(__file__).resolve().parents[2]/"plugins"` (`plugin_registry.py:63-64`) — under PyInstaller `__file__` lands inside the bundle; `releases/<v>/plugins/` is invisible (v0.18.0 regression class).
  - POSITIVE PATTERN: `sys.frozen → Path(sys.executable).parent` (`loader.py:446-448`, `manager.py:4815-4820`, `registry.py:1581-1583`, `graph.py:11045`, `api.py:169-171`) — frozen `sys.executable` IS `releases/<v>/ensemble-prod`, so `<exe-dir>/guidelines` resolves the staged payload.
  - MOST ROBUST ANCHOR: `resolved_dir.parent.parent` from the registry-resolved agents dir (`prompt_refresh.py:660/:745`) — anchored to the tree the daemon actually reads, immune to CWD and bundle-vs-binary confusion.
  - DEV: a frozen fork lands at `<repo-root>/guidelines` (nonexistent) — a dev fallback to `<repo>/docs/guidelines` (loader-fork derived, sibling precedent: repo-path fallback, NO env var) or an explicit override is required (overlaps Q3).
  - Plan shape: reader behind an injectable `base_dir` param; default = frozen-fork (a) or registry-anchor (b) — architect picks; everything else in the plan is seam-independent.
- **D8 — Kill-switch: presented, not decided.** Input for the architect: pure read surface, no write path, no ledger writes, auth-first gate, fail-soft everywhere — the notify-user knob protects a SEND surface; there is arguably nothing here to kill. DR-3-spirit switch only if the architect deems one warranted; the decision is RECORDED either way (Q4).
- **D9 — Floor advancement ordering.** Advance BOTH constants (`upgrade_tools.py:4145` + `lib.sh:95`) to the MERGE commit, in the release-bump commit, post-merge pre-stage (research §E; D6-8 cadence stage.sh:633-641). Gates pass mechanically without it — the advance is the obligation; disclose either way.
- **D10 — rollback_safe.** `ENSEMBLE_ROLLBACK_SAFE=1` explicit (unset would exit 78 on the always-matching full-history DDL grep, stage.sh:382-388) — legitimate because the tag-to-tag delta is DDL-empty; delta disclosed; `contains_contract_phase=true` expected; verify via manifest (receipts stdout-only).
- **D11 — Sonic-first ceremony note.** `guidelines` (#22) is the FIRST privileged-category grant to a sonic agent (sonic-leader). SUPPORTED by census: no sonic-* allow array contains any of the 21 privileged members; sonic-leader's `midflight` is NON-privileged (`midflight_report.py:62` — "Opt-in per agent … NOT auto-granted"; held by leader, devops, sonic-leader as a cross-team QA channel). The claim holds precisely BECAUSE the registration is privileged — recorded in: decisions + plan-overview (here), registry entry #22 comment, SAME-PR derivation note, tool-categories.json commit, approver tracking, tester RESULTS. Grant authority: sonic roster v0.30.2 amendment (merge `82e2bd849`).
- **D12 — lib.sh NOT edited.** Integrity coverage for the guidelines tree comes from the stage-local re-hash leg (mirroring plugins `stage.sh:747-753`), NOT from extending `lib.sh integrity_verify`'s loop (`agents frontend`, :4102 — its non-coverage of plugins/scripts is a pre-existing open escalation item this feature does not expand). `executor_family.sh` untouched; guidelines is NOT a family member — adding it to `executor_family_paths` would trip the coverage gate without a stamp re-pin.
- **D13 — Doctrine split.** BOTH fixed lines live verbatim in the tool help (module docstring + CATEGORY_DOC + `_full_doc_` — the tool-side house). The OBLIGATION line additionally lands in the 4 allowed agents' `tools_note.md` as behavioral guidance (notify-user precedent `agents/ari/tools_note.md:820`); the AUTHORITY line is a contract statement that belongs in the tool help. Two homes is accepted and documented (phase4 risk) — a future reword touches both.
- **D14 — Census is grep-derived, not prose-copied.** The pin inventories in phase2-plan were grep-verified at `5316e0cd3`; implementation re-derives by the registry's own three greps (`_tool_registry.py:441-443`). The memoir derivation self-pin (`test_memoir_registration.py:377-384`) needs all THREE faces updated (sought literal, stale-flavor negative, file census).

## Open questions FOR THE ARCHITECT (decision-ordered)

1. **Q1 — THE SEAM (blocks Phase 1 T2 only):** final reader base resolution — (a) `sys.frozen → Path(sys.executable).parent / "guidelines"` (matches the agents-payload fork), or (b) registry-anchored `resolved_dir.parent.parent` (rides the tree the daemon actually reads). Either: injectable `base_dir` param retained for tests; dev fallback per Q3. If the architect answers late, Phases 2–4 proceed (seam-independent); only T2(e) waits.
2. **Q2 — Floor interaction (confirm D9):** architect confirms the advance ordering + the NEXT-ceremony consequence for the pinned scripts worktree (`stale-scripts` refusal vs `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` warn bypass).
3. **Q3 — Dev-checkout fallback shape:** repo-path fallback (`<repo>/docs/guidelines` via the loader fork — sibling precedent, no env var) vs explicit constructor override only. Must keep the tool testable in dev WITHOUT reading repo paths at runtime in live (fixed-design constraint: "no repo-path reads at runtime in live" — a dev-only fallback does not violate it, but the architect owns the call).
4. **Q4 — Kill-switch y/n:** likely none (D8 input above). Architect's call; recorded either way in the ceremony record.

## Single-ownership map (REJECT-grade invariant)

| File | Sole owner |
|------|-----------|
| `daemon/tools/guidelines.py` (NEW) | P1 T1+T2 |
| `daemon/tools/_tool_registry.py` | P1 T3 |
| `daemon/tools/instance.py` | P1 T4 |
| `daemon/loader.py` | P1 T5 |
| `agents/{ari,leader,devops,sonic-leader}/meta.json` | P2 T1 |
| 6 EXPECTED_PRIVILEGED_SET files + 5 inline-pin files + count-pin files + memoir derivation | P2 T2–T4 |
| `test_board_registration.py` order pin, `test_anomaly_report_grant_pins.py`, aegis census | P2 T5 |
| `tests/unit/tools/test_guidelines_registration.py` (NEW) | P2 T6 |
| `tool-categories.json`, `test/packs/metrics_read_mutation_matrix_test.py` | P2 T7 |
| `scripts/upgrade/stage.sh` | P3 T1–T6 |
| `tests/test_guidelines_payload.sh` (NEW) | P3 T7 |
| `agents/<a>/tools_note.md` ×4 + `.prompt-artifacts.sha256` + SEALED_SHA_BASELINES | P4 T1+T2 |
| `.agents/approver/guideline-read-tool-tracking.md`, tester RESULTS/PACKS | P4 T3 |

Explicit non-edits: `lib.sh`, `executor_family.sh`, `promote.sh`, `rollback.sh`, `status.sh`, all daemon manifest consumers, `docs/guidelines/*`, every other `agents/*/meta.json`, `_auth.py`.

# RESULTS: guideline-read-tool — DEVELOPER-LANE evidence (2026-11-13)

> **STATUS: DEVELOPER-LANE EVIDENCE — NOT the independent tester gate.** Every run below was
> executed by the developer lane itself (worktree `/home/nea/ensemble-src-wt-guideline-read-tool`,
> branch `feature/guideline-read-tool`, venv `.venv` uv/CPython) as its final functional sweep
> (phase4-plan T5). The independent tester owns further gates (red-set accounting, counter-mutation
> re-drive, base-vs-head diffing) and may supersede or annotate these numbers. No mutation legs were
> re-driven here (the counter-mutations live in the phase-1/2 pin file and the phase-3 payload
> suite's own negative legs, which DID run green below).

## Lane commits

| Commit | Content |
|---|---|
| `681de8081` | Phases 1+2 — category #22 (`guidelines`) tools + registration + 4-agent grants + 21→22 pin census |
| `ccb7962d5` | Phase 3 — stage payload seam (flat `guidelines/` + `guidelines_tree_sha256` + `guidelines_manifest` + stage-local re-hash leg) + stage-smoke |
| phase-4 commit | Doctrine homes (4/4 `tools_note.md` `## Guidelines` sections) + prompt-artifact sha re-stamp (exactly 4 manifest rows) + approver/tester/ceremony records |

## Per-suite results (honest, phase4-plan T5 + test-matrix §E)

| Suite / check | Command | Result |
|---|---|---|
| Registration pins + allow gates + parser + verbatim (matrix A–C, E4 tool-side) | `.venv/bin/pytest tests/unit/tools/test_guidelines_registration.py -q` | **39 passed** in 1.94s |
| Stage-smoke legs (matrix D1–D7: flat payload, tree sha, per-file manifest, re-hash green + negative, precondition 78, fixture discipline) | `bash tests/test_guidelines_payload.sh` | **rc 0 — 49 passed, 0 failed** |
| SEALED_SHA_BASELINES tripwire (the binding 0-stale gate) | `.venv/bin/pytest "tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts" -q` | **1 passed** in 0.82s |
| Prompt-artifact baseline mechanics (drift core + generator CLI + sha256sum -c compat) | `.venv/bin/pytest tests/test_prompt_artifact_baseline.py -q` | **19 passed** in 2.43s |
| Committed-baseline verification (regen must be the only delta) | `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree <wt>` | **OK — 201 versioned artifacts match** |
| Registered pack (thin wrapper, both lanes) | `bash test/packs/guidelines_read_tool_unit_test.sh` | **RESULT: PASS** (leg 1: 39 passed; leg 2: 49 passed, 0 failed) |

Failures: none. Waivers: none required.

## T1/T2 verification (phase-4 duties)

- **T1 doctrine homes:** 4/4 granted agents' `tools_note.md` carry an `## Guidelines` section (ari, leader, devops, sonic-leader; appended at file end, `---`-separated, notify-user precedent `agents/ari/tools_note.md:820` shape). Both doctrine lines present BYTE-VERBATIM on single physical lines in each file: `Every feature shipping dormant / config-armed / ceremony-gated / with silently-biting rules OWES a guideline (see docs/guidelines/TEMPLATE.md).` and `Fixed files are the authority; this tool only reads them.` (each greps exactly once per file). NO other prompt artifact touched (git status: only the 4 tools_note files + the manifest). No section for sonic-coder/-developer/-reviewer/-tester (they cannot see the category).
- **T2 re-stamp:** manifest regenerated SAME-COMMIT via `scripts/protection/generate_prompt_artifact_baseline.py`; pre/post diff = EXACTLY the 4 expected rows (`agents/ari/tools_note.md`, `agents/devops/tools_note.md`, `agents/leader/tools_note.md`, `agents/sonic-leader/tools_note.md`) — zero unrelated drift (the STOP condition did not fire).
- **T2 tripwire verdict (VERIFIED, not assumed):** `SEALED_SHA_BASELINES` (`tests/test_chart_image_delivery_e2e.py:1425`, 28 rows) contains NONE of the 4 touched files — its only `tools_note` rows are `agents/developer[v2]/tools_note.md` and `agents/planner[v2]/tools_note.md`, both untouched. **Zero rows re-pinned; the test file is NOT edited this commit.** Consistent with the notify-user precedent (tools_note files are manifest-tracked, not sealed-tracked, except those two v2 rows).

## Census/ceremony sweep (T4 — VERIFY-only; single-ownership invariant honored)

| Check | Verdict |
|---|---|
| Registry entry #22 comment carries audience + ruling date + sonic-first note | GREEN — `daemon/tools/_tool_registry.py` KNOWN_TOOL_NAMES block (~:334: audience EXACTLY ari+leader+devops+sonic-leader, ruling 2026-11-13, first-privileged-grant-to-sonic) AND the `PRIVILEGED_TOOL_CATEGORIES` entry (~:843: SONIC-FIRST CEREMONY NOTE with census + midflight-non-privileged + grant authority merge `82e2bd849` + Q4 no-kill-switch) |
| SAME-PR derivation line dated | GREEN — "the numbers below are the 2026-11-13 guidelines Phase-1/2 derivation" (twice in the SAME-PR RULE block, `_tool_registry.py`) |
| `.agents/shared/metrics/tool-categories.json` has `"guidelines"` in sorted position | GREEN — idx 16 of 64, full list sorted (`cats == sorted(cats)`), JSON valid |
| Tool help carries BOTH doctrine lines VERBATIM | GREEN — module docstring (:8-9), `_full_doc_` Doctrine block (:97-98), CATEGORY_DOC build (:470-472); the registration suite's verbatim pins pass (39/39) |
| sonic-first census evidence | GREEN — privileged set = 22 members; the ONLY sonic-* privileged hit across all `agents/*/meta.json` is sonic-leader→`guidelines`; sonic-leader's `midflight` is NON-privileged (`_tool_registry.py:466` set lacks it; `midflight_report.py:62` opt-in docstring) |

## T6 ledger agreement (POK `plan-body-ledger-agreement`)

- `phase4-plan.md` task table (T1–T6) vs `plan-overview.md` phases table row 4 ("Doctrine + ceremony docs", 6 tasks, loose-1 / loose-2 / independent-3): task lists and ownership agree; coupling map agrees.
- No file edit is claimed by two phases: daemon//scripts edits are phases 1–3-owned (this commit makes NONE — verified `git status`); phase 4 owns the 4 tools_note files, the manifest regen, the approver tracking, tester RESULTS, PACKS.md registration, and the thin pack.
- Lane footprint beyond this commit's content: the planning dir (`.agents/shared/planning/guideline-read-tool/`, committed in `064875675`) — nothing else.

## Scope discipline

No code changes in `daemon/` or `scripts/` this commit; no DB/migrations; no env knobs; no POK entries; no critical notes; `docs/guidelines/` untouched; no new test logic (the pack is a thin wrapper).

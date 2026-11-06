# ari-plane-tracking — Functional Gate (right-sized, prompt-only lane)

**Commission:** tester gate for ari-plane-tracking @ 5ab2009c0 (spec 1ce41535f; base 17413bca5; branch feature/ari-plane-tracking; worktree /home/nea/ensemble-src-wt-ari-plane).
**Gate shape:** right-sized per commission — G1–G5 only; NO Tier-2 whole-tree; NO councils. Reviewer verdict upstream: APPROVE-WITH-ADVISORIES 0C/1W/4A; the W-1 action (exact pre-existing-red enumeration) is closed by G3 below.
**Execution:** 2026-11-06; tester dispatcher + 3 parallel workers + evidence worker. Interpreter /home/nea/ensemble-src/.venv/bin/python (worktree .venv absent), cwd=worktree; per-variable env scrub on every run (ambient PORT/ENSEMBLE_SELF_ENV/POSTGRES_* poison found in the agent shell — scrubbed; fence held, zero live contact).

## VERDICT: GO for staging
All legs PASS. Net-new failures = ∅. 10 pre-existing reds (exact ids below). No quick fixes; zero code changes by the gate; sole commit = this evidence rider.

## G1 — Baseline authority: PASS
- `scripts/protection/generate_prompt_artifact_baseline.py --check` → exit 0; verbatim: `PROMPT-BASELINE: OK — 177 versioned artifact(s) match .prompt-artifacts.sha256`
- agents/.prompt-artifacts.sha256: 177 lines; ordered by path field (`LC_ALL=C sort -c -k2,2` rc=0 at HEAD and at base). Whole-line `sort -c` rc=1 is a pre-existing artifact of the hash-prefixed/path-ordered format — byte-identical behavior at base 17413bca5 (same disorder line); not a lane defect.
- Row delta vs base — exactly 2 rows changed (both agents/ari/, del+add "moved" shape), verbatim:
```diff
-4b45329547dcad4e1bc4506b0f53266dda87f57ff7eaf6f7816971f38ed8fa8a  agents/ari/rule.md
+d1df71e1c3471d5938c6aa10ad2e39f31f0d8d64f791ff7e04b91dd2d5500bde  agents/ari/rule.md
 0cfb050555cd8c70124095ad49c19b99ef5710a60fcfa08efdc5d134f4cf96c1  agents/ari/soul.md
 cbd49c0a4d0f7edbc65bb8b2390c2a546316e7db3676da25ee0a52e39a039e38  agents/ari/tools_note.md
-303c78e325b123587303461ba6c2d556569a8171abdc66303e7a9f32ede5967d  agents/ari/workflow.md
+281de549be046b01dd2455f56678dc2206c42a74d6700f665fcfa163c6e8cd15  agents/ari/workflow.md
```
- Blob↔row cross-check (full 64-hex): `git show 5ab2009c0:agents/ari/rule.md` → d1df71e1…0bde == rule.md row; `git show 5ab2009c0:agents/ari/workflow.md` → 281de549…cd15 == workflow.md row.
- `git rev-parse HEAD` = 5ab2009c0e94b7dd32818caa2d3e5b9a7974b932.

## G2 — Guard packs at TIP: PASS (93 nodes green; 6/6 intended modules)
Seal recompute (STEP 0) — recomputed == pinned, both entries:
```
git show 5ab2009c0:agents/ari/rule.md     | sha256sum → d1df71e1c3471d5938c6aa10ad2e39f31f0d8d64f791ff7e04b91dd2d5500bde
git show 5ab2009c0:agents/ari/workflow.md | sha256sum → 281de549be046b01dd2455f56678dc2206c42a74d6700f665fcfa163c6e8cd15
```
Pinned entries observed at tests/test_chart_image_delivery_e2e.py line 1500 (rule.md) / line 1449 (workflow.md) [commission cited :1451/:1504 — adjacent annotation anchors; entries verified by content].

| # | Invocation | Result | Nodes |
|---|---|---|---|
| 1 | tests/test_chart_image_delivery_e2e.py -k test_phase_d_does_not_modify_sealed_artifacts | PASS (0.56s) | 1 passed, 27 deselected |
| 2 | tests/unit/test_anomaly_report_duty_prompts.py | PASS (0.46s) | 19 passed |
| 3 | tests/unit/test_instance_ops_prompt_layer.py | PASS (0.31s) | 3 passed |
| 4 | tests/unit/tools/test_mission_watch_prompts.py | PASS (0.45s) | 15 passed |
| 5 | tests/unit/test_plane_prompt_verb_check.py | PASS (0.48s) | 9 passed |
| 6 | tests/test_chart_image_delivery_audit.py | PASS (0.38s) | 46 passed |

**Path-typo disclosure:** the commission listed invocation 4 as `tests/unit/test_mission_watch_prompts.py` and 5 as `tests/unit/tools/test_plane_prompt_verb_check.py` — the two directory components are swapped relative to the repo. Corrected paths (as run) are the git-tracked modules at tip; both PASS. Adjudicated a commission-spec path typo, not a lane defect; no test files changed.

## G3 — Scoped net-new census: PASS — net-new = ∅ (reviewer W-1 CLOSED)
- Suite source: committed spec at `.agents/shared/planning/ari-plane-tracking/plan.md` §3.3 (verbatim command + §3.3 table / reviewer W-1 additions: tests/test_charter_render_capture.py, tests/unit/tools/test_prompt_section_reference_integrity.py). Command list archived: EVIDENCE/ari-plane-tracking-gate/G3-commands.txt.
- Method: BASE = `git archive 17413bca5` → /tmp scratch (lane worktree untouched); TIP = worktree; identical commands, same interpreter (/home/nea/ensemble-src/.venv/bin/python), per-var env scrub, `timeout 300` + `--timeout=120`, `-p no:cacheprovider -p no:randomly`. Import-shadow proof: BASE leg resolved daemon=/tmp/ari-plane-gate/base/daemon/__init__.py; TIP leg resolved daemon=<worktree>/daemon/__init__.py — each leg exercises its own tree, no shadow.
- BASE failed ids (10) and TIP failed ids (10): **byte-identical**; `diff` of sorted lists exit 0 ⇒ **net-new = ∅**. No ERROR lines either leg. No differing ids ⇒ no 3× isolation needed. Runtimes: BASE 2.23s/9.87s; TIP 1.70s/12.06s.
- Untouched byte-identity: `git diff --name-only 17413bca5..5ab2009c0 -- agents/project-manager/ agents/charter/` → EMPTY; tree hashes identical both refs: project-manager `b8c4e391e7c4a4fa0ce74ddbb8ad4b0c970a744f`, charter `a97c6d89c1d74d4285121e93e80fb618297f9a46`.
- **Count correction (verified, supersedes reviewer's estimate):** the plan-named suite carries 5 pre-existing reds (2 PM TestPromptComposition + 3 test_charter_render_capture); tests/unit/tools/test_prompt_section_reference_integrity.py carries **5** (not ~10): 3× test_no_bare_md_filename_tokens_in_prompts (install-mermaid-cli.md, workflow.md12, workflow.md19) + 2× test_odd_fence_count_fails (rule.md4, workflow.md4). Total = 10.

### Exact pre-existing FAILED ids (both legs identical)
```
tests/test_charter_render_capture.py::test_install_skill_has_prewarm_invocation_section
tests/test_charter_render_capture.py::test_install_skill_has_render_contract_section
tests/test_charter_render_capture.py::test_install_skill_render_contract_encodes_all_six_fixes
tests/unit/test_project_manager_agent.py::TestPromptComposition::test_soul_cites_rule_for_severity_framing
tests/unit/test_project_manager_agent.py::TestPromptComposition::test_workflow_cites_soul_and_rule
tests/unit/tools/test_prompt_section_reference_integrity.py::test_no_bare_md_filename_tokens_in_prompts[install-mermaid-cli.md]
tests/unit/tools/test_prompt_section_reference_integrity.py::test_no_bare_md_filename_tokens_in_prompts[workflow.md12]
tests/unit/tools/test_prompt_section_reference_integrity.py::test_no_bare_md_filename_tokens_in_prompts[workflow.md19]
tests/unit/tools/test_prompt_section_reference_integrity.py::test_odd_fence_count_fails[rule.md4]
tests/unit/tools/test_prompt_section_reference_integrity.py::test_odd_fence_count_fails[workflow.md4]
```

## G4 — Minimality + hygiene: PASS
- Change commit 5ab2009c0 touches EXACTLY 4 files: agents/.prompt-artifacts.sha256 (2/2 — the moved rows), agents/ari/rule.md (21/0), agents/ari/workflow.md (27/0), tests/test_chart_image_delivery_e2e.py (14/18 — the 2 seal re-pins). Pure insertions on both prompt files.
- `git diff --name-only 5ab2009c0^ 5ab2009c0 -- daemon/ scripts/ frontend/ plugins/` → EMPTY.
- Porcelain: static check 0 lines (gate workers left no residue); pre-evidence-commit porcelain = (empty — 0 lines); post-commit porcelain: verified 0 lines (checked after commit).

## G5 — Adjudication: NO separate directional content pin (AGREE with commission)
1. The exact post-review bytes of both touched prompt files are pinned twice: (a) SEALED_SHA_BASELINES entries in tests/test_chart_image_delivery_e2e.py — G2 recomputed blob shas == pinned values, enforced by test_phase_d_does_not_modify_sealed_artifacts; (b) the 177-row baseline agents/.prompt-artifacts.sha256 enforced by generate_prompt_artifact_baseline.py --check (G1). Any single-byte change to the new prose reddens both.
2. A directional content pin (asserting presence of specific policy phrases) is strictly weaker: every mutant it could catch necessarily changes the file's sha and is already caught by seal+baseline; it cannot catch anything the seal misses. It would enlarge the sealed-set maintenance surface with zero additional discrimination.
3. Mutation-meaningfulness: any planted policy-text mutant flips the recomputed sha ⇒ tripwire RED; G2's recomputed==pinned equality is the live proof the pin binds to the committed blobs.
Therefore no new pin is built; this section is the recorded adjudication.

## Evidence artifacts
- .agents/tester/EVIDENCE/ari-plane-tracking-gate/ (durable copies: G3 commands + both failed-id lists + G3 leg logs + G1 check log + G2/G4 summaries)
- /tmp/ari-plane-gate/ (session originals)
- Evidence rider: this commit (see branch log; not pushed).

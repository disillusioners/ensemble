# Release Recipe: v0.30.3 (guideline-read-tool vehicle)

Executor: **giter** (later phase). NO PROMOTE — promote is the Emperor's ceremony. This recipe sequences the phases into the release vehicle; the floor-constant step is the only non-obvious ordering constraint.

## Prerequisites

- Phases 1–4 complete; Phases 1+2 landed as ONE commit (SAME-PR rule); test matrix green.
- Feature branch `feature/guideline-read-tool` pushed; planning dir committed (this plan).

## Steps (ordered)

1. **Merge to latest** — `git merge --no-ff feature/guideline-read-tool` into `latest`; record the MERGE COMMIT SHA (needed by step 2). Push `latest` — MECHANICALLY REQUIRED before stage, not an audit-trail nicety: the L1 tip-identity gate (`stage.sh:195-206` → `lib.sh` `_verify_tip_identity` :4015-4045) refuses `non-tip-tree` unless the staging-tree HEAD equals the integration tip (`origin/latest`, local `latest` fallback) — only the journaled `--allow-stale-stage` override (`stage.sh:149-156`/`lib.sh:4026-4031`) bypasses it.
2. **Release-bump commit** (post-merge, ONE commit):
   - Release-bump file set (SANCTIONED deviation, disclose at ceremony): the 4 version files **+ CHANGELOG + the 2 floor-constant files** (third bullet below) — sanctioned by the cadence text itself (`stage.sh:633-641`: "the release bump … writes the constant"). The 4 version files: `pyproject.toml` (version → 0.30.3), `daemon/__init__.py` (`__version__`), `tests/test_api.py` (version pin), `uv.lock` (regenerated).
   - CHANGELOG: consume the `[Unreleased]` heading into `## [0.30.3] — <date>`. NEVER touch historical dated Unreleased headings (house rule).
   - **Floor constants advance in the SAME commit** (D6-8 cadence, stage.sh:633-641): `daemon/tools/upgrade_tools.py:4145` `PIPELINE_SCRIPTS_FLOOR_COMMIT` AND `scripts/upgrade/lib.sh:95` `PIPELINE_SCRIPTS_REV` BOTH → the step-1 MERGE COMMIT SHA. They travel together, one value, one commit (lib.sh:93-95 comment; research §E). Gates would pass mechanically without this (existence-only coverage) — the advance is the OBLIGATION, and the disclosure either way lands in the ceremony notes.
3. **Tag BEFORE stage** — annotated tag `v0.30.3` at the bump-commit HEAD. The stage exact-match gate refuses (exit 78) if the tag is missing or not at HEAD.
4. **Stage** — from a CLEAN stage worktree (`git_dirty:false` provenance in the sidecar; canonical checkout is DETACHED+DIRTY — never stage from there). Frontend dist built per house recipe (this feature touches no FE code; build only satisfies the payload requirement). Argv shape (operated precedent v0.29.15 + POK `stage-v0300` note — legacy-tree derive-path is unusable, pass explicitly):
   ```
   ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.30.3 bash scripts/upgrade/stage.sh live
   ```
5. **rollback_safe adjudication** — `ENSEMBLE_ROLLBACK_SAFE=1` is legitimate ONLY because the tag-to-tag migration DELTA is DDL-empty (docs+tool-only feature; zero new `daemon/migrations/versions/*.sql`; `KNOWN_SCHEMA_GEN` unchanged). Disclose the tag-to-tag delta explicitly in the ceremony record. EXPECT `contains_contract_phase=true` (full-history DDL grep always fires, stage.sh:356-363) — normal, disclosed, non-blocking. Do NOT rely on stage stdout for verdicts (receipts are STDOUT-only, POK `stageverify-01`).
6. **Post-stage verification** (via the release manifest, not stdout):
   - `releases/v0.30.3/guidelines/` present with the 5 files;
   - `manifest.json`: `guidelines_tree_sha256` (a guidelines tree sha alongside agents/frontend/plugins/scripts) + `guidelines_manifest` (5 entries, tree-relative keys) present and matching an independent lib.sh-ordered recompute;
   - `rollback_safe=true source=explicit`, `contains_contract_phase=true`, `known_schema_gen` unchanged, `staged_at` fresh, `git_dirty:false`;
   - `releases/state.json` promote fields untouched (stage journals nothing — stages leave no trace in state.json).
7. **Arm-time consequence (note for the NEXT ceremony)** — with the floor advanced, the operator's pinned scripts worktree (`ensemble-src-wt-scripts-pin` / `ENSEMBLE_UPGRADE_SCRIPTS_DIR`) must move to ≥ the new merge commit before the next arm, or arm refuses `stale-scripts` (`upgrade_tools.py:4360-4367`; escape `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` warn-only). Record this in the ceremony notes.
8. **STOP — NO PROMOTE.** The vehicle is staged and ceremony-ready; promote belongs to the Emperor.

## Ceremony-record contents (for the Emperor's promote)

- v0.30.3 = the FIRST release carrying the guidelines runtime payload (`releases/<v>/guidelines/`) — the guidelines' first shippable runtime artifact; the tool cannot serve any guideline newer than the running release (INTENDED).
- sonic-first note: `guidelines` = first privileged-category grant to a sonic agent (sonic-leader; census-backed).
- Floor-advance disclosure: old floor `087f782a…` → merge commit; both constants; one bump commit.
- rollback_safe=1 explicit adjudication + DDL-empty delta disclosure; contains_contract_phase=true expected.
- Kill-switch decision RECORDED: **NO** — no kill-switch, no env knob (architect Q4, BINDING 2026-11-13).

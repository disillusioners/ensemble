# HANDOFF — ari-instance-stop-grant (sonic lane) — REFRESHED for re-dispatch #1 / v0.30.9

Commission: Emperor directive 2026-11-14 ("check if devops have it, if it exist add it, or you can craft new one") + clarification ("maintenancer agent have it not devops"). Reuse-first locked; craft-new not used.

**Release vehicle: v0.30.9** (supersedes v0.30.8 — see `## Supersede / disposal` below). NO PROMOTE — the Emperor's 3-factor nonce ceremony owns the flip.
Feature branch: `feature/ari-instance-stop-grant` (base `3a3d1fb3d`). Re-dispatch fix branches: `feature/ari-stop-fix-comments` @ `85fe8209e`, `feature/ari-stop-fix-rel` @ `4e0295c58` (both merged, pushed).

## Supersede / disposal

v0.30.8 was STAGED-NOT-PROMOTED. Re-dispatch #1 (REVIEW: NEEDS-REVISION — release-record corrections only; behavior verified clean by reviewer + tester) produced commits post-dating the v0.30.8 stage, so the stage was re-cut as **v0.30.9** to keep provenance honest (stage sidecar head == actual HEAD; `git describe --exact-match` holds). House supersede precedent (v0.29.0 / disposed v0.28.17):
- v0.30.8 staged release dir: **DISPOSED** (sidecars preserved out-of-tree under `/tmp/v0308-sidecars-preserved-<ts>/`; the dir is removed from `releases/`).
- v0.30.8 LOCAL tag: **DISPOSED**. REMOTE tag `v0.30.8`: **RETAINED as audit trail** (never moved — a pushed tag is immutable).
- Disposal does not alter the v0.30.9 tagged tree; it is filesystem/tag housekeeping executed in this ceremony after the v0.30.9 stage succeeds.

## Files touched across modules

- **Module A — feature/ari-stop-agent @ f0fa135e7**: `agents/ari/meta.json`, `agents/ari/tools_note.md`, `agents/ari/rule.md`, `agents/.prompt-artifacts.sha256`, `tests/test_chart_image_delivery_e2e.py`
- **Module B — feature/ari-stop-daemon @ 713c54f5d** (86edab8ea + 713c54f5d): `daemon/tools/instance_ops.py`, `daemon/tools/instance_read.py`, `daemon/tools/_tool_registry.py`, `tests/unit/test_instance_ops_registration.py`, `tests/unit/test_instance_ops_behaviour.py`, `.agents/shared/metrics/tool-categories.json`
- **Module C — feature/ari-stop-pins @ a51eb4e49** (3 commits): the 7 ari-tail pin files (board / anomaly / memoir / pok / prompt_reload / prompt_assent / guidelines)
- **Module D — feature/ari-stop-bump @ 1c9cece7e**: `CHANGELOG.md`, `daemon/__init__.py`, `pyproject.toml`, `tests/test_api.py`, `uv.lock`
- **Re-dispatch #1 — feature/ari-stop-fix-comments @ 85fe8209e**: `daemon/tools/_tool_registry.py`, `daemon/tools/instance.py` (comment-only)
- **Re-dispatch #1 — feature/ari-stop-fix-rel @ 4e0295c58**: `CHANGELOG.md` (corrections + 0.30.9 section), version files → 0.30.9
- **Integration (this file)** refreshed on latest pre-tag.

## What changed

- **Grant (layer 2).** Ari's allow gains `instance_read` + `instance_ops` WHOLE (per-tool allow for nest-ops stays REJECTED per D-13). Holders now `{ari, maintenancer}`.
- **Layer-3 body gates widened (the load-bearing finding).** `INSTANCE_{OPS,READ}_AUTHORIZED_AGENTS` `{maintenancer}` → `{"ari","maintenancer"}` (D-13 single-owner population amended BY RULING 2026-11-14). Safety machinery UNCHANGED and binding all holders: two-tier `instance_ops_audit` ledger, protected-target two-tier policy (`PROTECTED_TERMINATE_BUDGET_24H=1`), provenance gates (`no-user-provenance`), shared terminate budget (`TERMINATE_BUDGET_24H=3`, cascade = 1 act).
- **Capability surface ari holds after promote:** `instance_list` / `instance_get` / `instance_health` / `fleet_capacity_summary` / `instance_fleet_summary`; `instance_terminate` (the STOP path), `instance_reclaim`, `instance_nudge`, `instance_mark`, `instance_answer`, `instance_dismiss`.
- **Prompt ceremony.** ari `tools_note.md` fleet-surfaces section; `rule.md` fleet-surgery exception; prompt baseline re-stamped (217 rows); `agents/ari/rule.md` seal row re-pinned (`0a261337…`).
- **Pins re-pointed same-branch (never weakened).** ari relative-tail family across 7 files (+ the 4 later-lane faces found by the catch-all re-grep); instance_ops registration allow-list faces flipped to two holders; ari-positive behaviour leg ADDED (leader negative legs untouched); pok artifact-count pin 64→65.
- **Artifact.** `tool-categories.json` REGENERATED with the house generator (never hand-edited; generator byte-identity proven; count 64→65 with `project_future` ADDED and `doc_maintenance` RETAINED — it is genuinely registered). Completes the pre-existing F-1 pair. `VOCABULARY_VERSION` stays 10.
- **Re-dispatch #1 corrections (comment/record ONLY, zero behavior).** CHANGELOG 0.30.8 section: artifact bullet now states REGENERATED 64→65 (not "UNCHANGED"); Disclosures (1) corrected — `doc_maintenance` legitimately registered and retained, no "stale" claim, count 64→65. Two remaining D-13-amendment citations added at the drift sites (`_tool_registry.py:1665`, `instance.py:6830`, plus two same-class residuals in the same registry file disclosed by the owner). New `[0.30.9]` section records the supersede/disposal + standing-matrix statement.
- **Activation.** INERT until promote; v0.30.9 ships the grant, the widened frozensets and the docs.

## What is UNTESTED

- No live-daemon E2E of ari invoking any granted tool (activates only post-promote).
- Protected-target + provenance refusal paths for CALLER=ari not exercised end-to-end (authorization-only-verified; machinery policy-shared and proven for maintenancer).
- Cascade terminate of a real DB-state zombie by ari — not run; first live exercise post-promote.
- Integration-marked suites (`test_maintenancer_spawn_resolves_tools.py`, `test_service_tool_flag_off_byte_identical.py`) not re-run (no category added; their privileged-set faces unchanged).
- Full-tree pytest sweep not run (targeted packs BASE vs HEAD; tester ran the same 14-file matrix independently at the v0.30.8 tree — PASS).
- Re-dispatch #1 verification: comment-only faces are not covered by any source-literal pin (verified by pack green + drift grep, not by a behavior test); the v0.30.9 re-stage executes AFTER this file was committed (final stage facts in the leader report).

## Known risks

- **D-13 amendment** is a ruled population change (two holders), not drift — comments cite the rule.
- **PRE-EXISTING reds (verified red at base `3a3d1fb3d`; NOT ours; leader sweep — do not absorb silently):** `tests/unit/test_anomaly_report_grant_pins.py` → `test_the_grant_is_the_last_element`, `test_the_three_phase_3_touched_agents_carry_both_entries`, `test_the_seal_map_is_zero_stale` (stale `agents/leader/rule.md` seal row `6a225b9f…` vs actual `9ee75e1f…`); **NEWLY DISCLOSED (re-dispatch #1):** `tests/unit/tools/test_nest_report_read_registration.py:284` — devops tail vs `project_future` (same project-future-tail class, verified red at base).
- **Shared budget.** ari's terminate acts consume the fleet-wide 3/24h budget.
- **`instance_get` is not side-effect-free** (lazy MCP schema loads) — documented in ari's tools_note.
- **Artifact count pair** moved 64→65 without a `VOCABULARY_VERSION` bump (not required by the artifact's own gate; vocab stays 10).
- **Merge-order note.** Grant (A) and pins (C) join in one feature branch; intermediate module branches red by design; latest never saw an intermediate red.

## How to verify

Merged tree (worktree `.venv`; `export PATH="$HOME/.local/bin:$PATH"`; scrub env PER-VARIABLE — `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u ENSEMBLE_AUTH_ENABLED`):

- `pytest tests/unit/test_instance_ops_registration.py tests/unit/test_instance_ops_behaviour.py tests/unit/test_board_registration.py tests/unit/test_memoir_registration.py tests/unit/test_pok_grant_pins.py tests/unit/tools/test_prompt_reload_pins.py tests/unit/tools/test_prompt_assent_registration.py tests/unit/tools/test_guidelines_registration.py tests/unit/tools/test_tool_category_artifact.py -q` → **252 passed** (re-dispatch #1 merged gate)
- `pytest tests/test_api.py::test_health_check -q` → 1 passed (version 0.30.9)
- `pytest tests/unit/test_anomaly_report_grant_pins.py -q` → exactly the 3 pre-existing reds
- `pytest tests/unit/tools/test_nest_report_read_registration.py -q` → 1 pre-existing red at :284 (disclosed)
- `ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=/tmp/x.json pytest tests/unit/tools/test_tool_category_artifact.py -q && diff /tmp/x.json .agents/shared/metrics/tool-categories.json` → EMPTY diff
- `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree .` → OK, 217
- `pytest tests/unit/agents/test_aegis_security_family.py -q` → 556 passed
- `grep -rn 'maintenancer-only\|single-owner (maintenancer)' daemon/tools/_tool_registry.py daemon/tools/instance.py` → empty (drift clean)
- `jq -r '.tools.allow[-3:] | join(",")' agents/ari/meta.json` → `guidelines,instance_read,instance_ops`
- POST-PROMOTE live: ari → `instance_list`, then `instance_terminate` on the wedged instance (`4e261d34-…`); expect `instance_ops_audit` rows; protected targets demand provenance (policy).

Net-new record (base `3a3d1fb3d` → HEAD): combined targeted pack `5F/340P` → `3F/343P`; aegis 556/556 both; artifact pack 2F/7P → 9P. **Net-new deterministic reds: 0**; 2 pre-existing reds fixed. Re-dispatch merged gate: 252 passed / 0 failed (comment/CHANGELOG-only delta, standing matrix carried over).

## Bash-raises used

- FE build (`scripts/upgrade/_build_frontend.sh`) + `stage.sh live` under explicit raises (>600 s each, hard cap 1800 s) per `[sonic-bash-rule-v1]`, authorized by the commission. v0.30.8 stage consumed: FE build, stage.sh ×2 (live-guard refusal, then real run with `ENSEMBLE_UPGRADE_LIVE=1`). v0.30.9 re-stage consumes the same raise budget: FE rebuild (sidecar head must == v0.30.9 HEAD) + stage.sh ×1 with `ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1` (explicit rollback choice grounded in the proven-empty migration delta v0.30.8..v0.30.9). Test-pack legs stayed within the default 600 s bound. Final stage facts (tag, binary sha256, disposal evidence) ride the sonic-developer's leader report.

## Complexity self-assessment

**medium** — as assessed at plan-open (grant ceremony: 2 frozenset literals + 1 allow append + prompt docs + mechanical pin re-points + release stage; zero new categories/tools/migrations; multi-holder privileged categories precedented). Re-dispatch #1 was record/comment-only: no behavior change, no complexity reassessment triggered. Two pre-existing defects handled honestly (artifact pair completed; anomaly ×3 + nest_report_read devops-tail disclosed, untouched).

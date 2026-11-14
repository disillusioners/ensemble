# HANDOFF — ari-instance-stop-grant (sonic lane)

Commission: Emperor directive 2026-11-14 (Discord, verbatim: "check if devops have it, if it exist add it, or you can craft new one") + clarification ("maintenancer agent have it not devops"). Subject: Ari needs the capability to STOP/TERMINATE instances directly. Reuse-first path locked; craft-new not used.

Feature branch: `feature/ari-instance-stop-grant` (base `3a3d1fb3d` = origin/latest tip at plan time). Staged release: v0.30.8 (NO PROMOTE — the Emperor's 3-factor nonce ceremony owns the flip).

## Files touched across modules

- **Module A — feature/ari-stop-agent @ f0fa135e7**: `agents/ari/meta.json`, `agents/ari/tools_note.md`, `agents/ari/rule.md`, `agents/.prompt-artifacts.sha256`, `tests/test_chart_image_delivery_e2e.py`
- **Module B — feature/ari-stop-daemon @ 713c54f5d** (commits 86edab8ea + 713c54f5d): `daemon/tools/instance_ops.py`, `daemon/tools/instance_read.py`, `daemon/tools/_tool_registry.py`, `tests/unit/test_instance_ops_registration.py`, `tests/unit/test_instance_ops_behaviour.py`, `.agents/shared/metrics/tool-categories.json`
- **Module C — feature/ari-stop-pins @ a51eb4e49** (3 commits): `tests/unit/test_board_registration.py`, `tests/unit/test_anomaly_report_grant_pins.py`, `tests/unit/test_memoir_registration.py`, `tests/unit/test_pok_grant_pins.py`, `tests/unit/tools/test_prompt_reload_pins.py`, `tests/unit/tools/test_prompt_assent_registration.py`, `tests/unit/tools/test_guidelines_registration.py`
- **Module D — feature/ari-stop-bump @ 1c9cece7e** (amended r2): `CHANGELOG.md`, `daemon/__init__.py`, `pyproject.toml`, `tests/test_api.py`, `uv.lock`
- **Integration (this file)**: `.agents/shared/planning/sonic-team/ari-instance-stop-grant/HANDOFF.md`

## What changed

- **Grant (layer 2).** Ari's allow-array gains the TWO nest-ops fleet categories WHOLE, appended last: `instance_read`, `instance_ops`. Per-tool allow for these categories stays REJECTED (D-13: a literal tool name bypasses the category gate). Holder population is now `{ari, maintenancer}`.
- **Layer-3 body gates widened (the load-bearing integration finding).** `INSTANCE_OPS_AUTHORIZED_AGENTS` and `INSTANCE_READ_AUTHORIZED_AGENTS` were `frozenset({"maintenancer"})` in the tool bodies — a meta.json-only grant would have shipped tools that refuse every ari call. Both widened to `{"ari", "maintenancer"}` under the Emperor directive; **D-13's single-owner population is amended (by ruling), the SAFETY machinery is not**: two-tier `instance_ops_audit` ledger, protected-target two-tier policy, provenance gates (`no-user-provenance`), and the shared fleet terminate budget (3 acts/24h; a cascade counts as ONE act) are unchanged and bind every holder.
- **Capability surface ari now holds:** `instance_list`, `instance_get`, `instance_health`, `fleet_capacity_summary`, `instance_fleet_summary` (reads); `instance_terminate` (the STOP path — whole cascade tree), `instance_reclaim`, `instance_nudge`, `instance_mark`, `instance_answer`, `instance_dismiss` (audited acts).
- **Prompt ceremony.** `agents/ari/tools_note.md` gains a fleet-surfaces section (usage + guardrails, agent-observable language, zero system-internal tokens); `agents/ari/rule.md` gains the fleet-surgery exception (the "Never Use instance Tools" section no longer reads as a blanket ban on `instance_*` names); prompt-artifact baseline re-stamped (217 rows, unchanged count); the `agents/ari/rule.md` seal row re-pinned (0a261337…).
- **Pins re-pointed same-branch (never silently weakened).** Ari relative-tail family across 7 files — board trailing list, anomaly `ALLOW_ARRAY_CONSTANTS` + source literal, memoir offsets + source literal, pok tail, prompt_reload offsets+length, prompt_assent offsets, guidelines relative offset (including the FOUR later-lane faces the first census pass missed; the pins' own catch-all re-grep found them). Negative legs preserved: leader stays unauthorized asserted positively; the leader "not permitted" behaviour leg is untouched and a new ari-positive leg was added.
- **Artifact (pre-existing defect completed).** `.agents/shared/metrics/tool-categories.json` regenerated with the HOUSE GENERATOR (never hand-edited; generator output byte-identical proof recorded): count 64→65 (`project_future` added, `doc_maintenance` retained — the artifact derives from the decorator-registered set, not `CATEGORY_MODULES`); `VOCABULARY_VERSION` stays 10 (the artifact pack's own gate requires vocab == checkpoint_metrics.VOCABULARY_VERSION == 10). This completes an F-1 pair the project-future lane left stale (their lane added the category without regenerating), and the pok artifact-count pin moves 64→65 with it.
- **Release.** Version bump 0.30.7→0.30.8 (5-file house convention) + CHANGELOG `[0.30.8]` entry with **Disclosures**.
- **Activation semantics.** The grant is INERT until promote: the allow entry and the frozenset widen ship in the release payload; the live tree keeps its old agents/tools until the Emperor's ceremony flips `current`. The motivating zombie (`4e261d34-…`, liveness=processing since 2026-11-12) is terminable by ari only POST-promote.

## What is UNTESTED

- **No live-daemon E2E of ari invoking any granted tool.** Unit/behaviour legs use fake fleets / fixture rows; no ari instance has been booted with the new payload (it activates post-promote).
- **Protected-target + provenance refusal paths for CALLER=ari** were not exercised end-to-end — the refusal machinery is policy-shared and proven for maintenancer; ari-specific call-path is authorization-only-verified.
- **Cascade terminate of a real DB-state zombie by ari** — not run (incident not reproducible on demand). First live exercise is post-promote.
- Integration-marked suites (`tests/integration/test_maintenancer_spawn_resolves_tools.py`, `tests/integration/test_service_tool_flag_off_byte_identical.py`) were NOT re-run in this lane: no category was added, their privileged-set faces are unchanged, and the registration/behaviour packs cover every changed face.
- Full-tree pytest sweep not run — the net-new protocol ran the targeted pack list BASE vs HEAD (see below).
- FE build + `stage.sh` execute AFTER this file was committed (see `## Bash-raises used`; final stage facts in the sonic-developer's leader report).

## Known risks

- **D-13 amendment.** The "one owner for both categories" invariant is now a two-holder population BY EMPEROR DIRECTIVE. Source comments cite the amendment (commission id + date) so a future reader sees it was ruled, not drifted. Any revert needs the same-ceremony reverse.
- **PRE-EXISTING reds (verified red at BASE; NOT introduced here; untouched):** `tests/unit/test_anomaly_report_grant_pins.py` → `test_the_grant_is_the_last_element`, `test_the_three_phase_3_touched_agents_carry_both_entries` (project-future tail vs `GRANT_POST_TAIL`), `test_the_seal_map_is_zero_stale` (stale `agents/leader/rule.md` seal row: sealed `6a225b9f…` vs actual `9ee75e1f…`, left by the v0.30.5/0.30.6 sonic-routing lane). Routed to the leader's sweep — do NOT re-point these silently in another lane.
- **Shared budget.** `instance_terminate` consumes the SAME fleet-wide 3-acts/24h budget as maintenancer; ari's acts can exhaust it for the fleet repair lane.
- **`instance_get` is not side-effect-free** (lazy-loads the target's MCP schemas — external discovery traffic). Documented in ari's tools_note.
- **Artifact count pair.** Count moved 64→65 without a `VOCABULARY_VERSION` bump (not required by the artifact's own gate: vocab stays 10). Future lanes touching the count must keep the pair in step.
- **Merge-order note.** The grant (A) and its pins (C) live on separate module branches joined in ONE feature branch; intermediate module branches are red by design. The integrated tree is the unit of correctness and `latest` never sees an intermediate red.

## How to verify

On the merged tree (worktree `.venv`; `export PATH="$HOME/.local/bin:$PATH"`; scrub env PER-VARIABLE — `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u ENSEMBLE_AUTH_ENABLED`):

- `pytest tests/unit/test_instance_ops_registration.py tests/unit/test_instance_ops_behaviour.py -q` → all green
- `pytest tests/unit/test_board_registration.py tests/unit/test_memoir_registration.py tests/unit/test_pok_grant_pins.py tests/unit/tools/test_prompt_reload_pins.py tests/unit/tools/test_prompt_assent_registration.py tests/unit/tools/test_guidelines_registration.py tests/unit/test_ari_agent.py tests/unit/test_ari_user_ruling_allow_sets.py tests/unit/test_ari_pm_terminology_pins.py tests/test_prompt_artifact_baseline.py -q` → all green
- `pytest tests/unit/test_anomaly_report_grant_pins.py -q` → exactly the 3 pre-existing reds (leak-check node ids)
- `pytest tests/unit/tools/test_tool_category_artifact.py -q` → 9 passed
- `ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=/tmp/x.json pytest tests/unit/tools/test_tool_category_artifact.py -q && diff /tmp/x.json .agents/shared/metrics/tool-categories.json` → EMPTY diff (generator-owned)
- `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree .` → OK, 217 artifacts
- `pytest tests/unit/agents/test_aegis_security_family.py -q` → 556 passed
- `jq -r '.tools.allow[-3:] | join(",")' agents/ari/meta.json` → `guidelines,instance_read,instance_ops`
- POST-PROMOTE live: ari → `instance_list` (fleet census) and, on the wedged instance, `instance_terminate` (expect audit rows in `instance_ops_audit`; protected targets will demand provenance — policy working, not an error).

Net-new record: BASE `3a3d1fb3d` → `5 failed, 340 passed` (3 anomaly + 2 artifact pre-existing); HEAD (feature tip) → `3 failed, 343 passed`. **Net-new deterministic reds: 0**; 2 pre-existing reds FIXED (artifact pair). Aegis pack 556/556 both legs.

## Bash-raises used

- FE build (`scripts/upgrade/_build_frontend.sh`) and `stage.sh` ran under explicit raises (>600 s each, hard cap 1800 s) per `[sonic-bash-rule-v1]`, authorized by the commission ("anything longer needs your explicit raise and must ride the handoff"). Test-pack legs stayed within the default 600 s bound. This file was committed BEFORE the stage; exact stage evidence (tag, binary sha256) rides the sonic-developer's leader report.

## Complexity self-assessment

**medium** — assessed after explore+plan, before dispatch; no handover raised, no override in effect. Basis: a pure grant ceremony (2 frozenset literals + 1 allow-array append + prompt docs + mechanical pin re-points across 6 pin families + standard release stage), zero new categories/tools/migrations, and multi-holder privileged categories already precedented (wedge-capture ari+leader+maintenancer; metrics-read maintenancer+devops+wanderer). Complexity driver was pin-surface breadth, not behavioral depth. Two pre-existing defects were discovered and handled honestly (artifact pair completed; anomaly-pack reds routed, untouched).

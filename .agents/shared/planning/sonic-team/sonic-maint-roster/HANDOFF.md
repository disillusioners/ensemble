HANDOFF_PATH = .agents/shared/planning/sonic-team/sonic-maint-roster/HANDOFF.md
# (repo-root-relative; ABSOLUTE: /home/nea/ensemble-src-wt-sonic-maint-roster/.agents/shared/planning/sonic-team/sonic-maint-roster/HANDOFF.md)
Commission: "add maintenancer agent to the sonic team" (user directive via Discord 2026-11-20)
Base = origin/latest @ fb5afe9f23de9d0daff26a043a5493a27179fe11 | Merge tip = 545c2998675e4a6b05d1ad529214ebe434dc7a96 (feature/sonic-maint-roster)
Tag = v0.31.19 (annotated, LOCAL-ONLY — no push per sonic grant) | Staged release = /home/nea/agents-ensemble/releases/v0.31.19 (NO flip; current -> releases/v0.31.10 untouched)
Binary sha (4-way identical: release == stage-wt dist == manifest.binary_sha256 == sidecar) = f8555d8a5d39799de93f1a470651de1c2de3ef58e86e2bd22f3347a63101b319
DEVIATION DISCLOSURE (commission asked): NO sonic-maintenancer duplicate was created — the EXISTING agents/maintenancer (v1.0.0) joined via sonic-leader meta.json team_members, exactly the giter/devops escape-hatch-tier precedent (base agent_id, spawn gate canonicalizes via registry.resolve_pure_id). No structural requirement for a duplicate was found.

## Files touched across modules
- m1 roster-core (branch feature/sonic-maint-m1-roster @ 38624ecf6): agents/sonic-leader/meta.json; tests/unit/test_sonic_leader_agent.py (NEW, 21 nodes); test/packs/sonic_agent_defs_checks.py; .agents/tester/PACKS.md (newest-first re-registration row; prior rows untouched)
- m2 prompts+seal (branch feature/sonic-maint-m2-prompts @ 5534c9925): agents/sonic-leader/rule.md; agents/sonic-leader/workflow.md; agents/sonic-leader/tools_note.md; agents/.prompt-artifacts.sha256 (3 rows re-pinned; soul.md UNTOUCHED, no enumeration)
- m3 release-prep (branch feature/sonic-maint-m3-release @ 92dc44a82): pyproject.toml; daemon/__init__.py; tests/test_api.py; uv.lock; CHANGELOG.md (single 5-file house-shape commit)
- integration (this layer): 3 x merge --no-ff (slots 1->2->3, ZERO conflicts); annotated tag v0.31.19; staged release v0.31.19 (no repo files); this HANDOFF

## What changed
sonic-leader's spawn roster grows 6 -> 7: the existing maintenancer agent (nest-maintenance/repair lane: log forensics, controlled DB repair, gated live-system ops) is now a team member + escape-hatch-tier peer of giter/devops. (1) meta.json team_members += "maintenancer" (after devops), meta version 1.1.1 -> 1.1.2 (house precedent 35d638662). (2) Prompt artifacts enumerate the seat at all 3 roster sites (rule 7 escape-hatch tier; workflow escape-hatch roster sentence; tools_note "six"->"seven" + list + escape-hatch clause) — seal regenerated, 3 rows only, count stable at 217, checker rc=0. (3) NEW census pin file (exact-set roster, real-registry discovery, _check_team_membership behavioral legs incl. negative control) — COUNTER-MUTATION PROVEN by m1 (drop maintenancer -> 6 nodes RED; byte-identical restore -> 21/21 GREEN). (4) Registered pack sonic_agent_defs lockstep: FROZEN_TEAMS + EXPECTED_EFFECTIVE + A2/A3 descriptions + 2 new counter-mutation legs (A2/A3-leader-drop-maintenancer) + real-layer negative "sonic-developer must NOT spawn maintenancer". (5) Version 0.31.18 -> 0.31.19 (script-authored bump + CHANGELOG [0.31.19] — 2026-11-20 section, empty [Unreleased] preserved at top, heading order verified). (6) STAGE-ONLY release v0.31.19 (rollback_safe=true source=explicit, additive-only delta, ZERO migrations — schema_gen unchanged 20261119_000001); staged payload carries the 7-member meta.json v1.1.2 and the re-pinned seal (baseline leg clean IN-PAYLOAD: "PROMPT-BASELINE: OK — 217").

## What is UNTESTED
- No LIVE-daemon spawn of maintenancer BY sonic-leader — authorization proven at gate level (_check_team_membership unit + real-registry legs); the roster takes effect at daemon restart i.e. the promote ceremony (Emperor-owned).
- Pack Layer B (tests/unit/test_ari_pm_terminology_pins.py + wrapper --real leg) never ran VIA the wrapper — the wrapper aborts at the Layer-A static base-reds; m1 ran the --real layer directly (PASS: A2-real leader spawns its 7 incl. maintenancer; A3-real negatives green).
- The staged binary was NOT boot-smoked against a real PostgreSQL — only the sanctioned NON-BOOT SQLite smoke: banner "Starting Ensemble v0.31.19" + rc=3 death at the first PG-only migration (expected outcome per blueprint), evidence /tmp/v0319-smoke2.h0oG5c/out.txt.
- Pre-existing base-reds left unfixed (all independently re-measured at true base fb5afe9f2, NOT caused by this commission): (a) 24 nodes in tests/test_spawn_team_members.py + tests/unit/test_anomaly_report_grant_pins.py — node-ID-identical at base under the same fence/interpreter (py3.14.7); (b) 9 nodes in tests/test_api.py — identical at base (incl. 5 QUARANTINE.md-listed rows); (c) pack static A3 (sonic-leader allow gained 'room' at a32bdb395, never re-pinned into FROZEN_TOOLS) + A9 (pack fence base 3f45cd6dd predates current base) — anomaly 731ea63e (m1).
- No whole-tree pytest sweep (scoped shards only); no reviewer/tester verification wave (sonic-leader's one-wave gate owns that).

## Known risks
- STAGE OVERRIDE: --allow-stale-stage was REQUIRED (L1 non-tip-tree: HEAD 545c29986 vs tip fb5afe9f2) because the no-push constraint keeps the tip ahead of origin/latest. Provenance becomes retroactively tip-consistent when the leader pushes 545c29986 as origin/latest + tag v0.31.19. The override's durable journal append FAILED best-effort ("audit trail is incomplete", reason=non-tip-tree) — anomaly a41a78e5; the stage-log WARN is the only receipt. A clean re-stage post-push remains the leader's option.
- SCRIPTS PIN ADVANCED to the unpushed tip: /home/nea/ensemble-src-wt-scripts-pin now at 545c29986 (T_old=fb5afe9f2). Arm-time floor guard still passes (floor e8695f1 is an ancestor; stamp==floor validated) but the pin sits on a local-only commit until push.
- m2's prompt prose asserts SEVEN team_members — true only with m1 merged (it is, on this branch). Cherry-picking m2 without m1 would desync prompt vs gate.
- Pack static base-reds (A3/A9) mean a tester pack re-run on this tree reports FAIL(static) until a follow-up lane re-pins them — do not attribute to this commission; dual-side base proof in m1's report.
- Pre-existing maintenancer boot warning surfaced by the smoke: "deny entry 'git_commit' is neither a known category nor a known tool" (its own meta, v1.0.0) — cosmetic, out of scope.
- The 21-node wholesale base-red of tests/test_spawn_team_members.py (py3.14/mock class) is a standing defect of that file at base — needs its own lane eventually.

## How to verify
On the MERGED tree (/home/nea/ensemble-src-wt-sonic-maint-roster @ 545c29986, venv .venv):
1. Roster: python3 -c "import json;print(json.load(open('agents/sonic-leader/meta.json'))['team_members'])" -> 7 members ending 'maintenancer'; meta version 1.1.2.
2. Seal: .venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check --tree . -> rc=0, "217".
3. Gates (env: ENSEMBLE_SELF_ENV=dev + 5 explicit POSTGRES_*; unset ENSEMBLE_AUTH_* individually — host ambient auth redirects 41 test_api tests): .venv/bin/python -m pytest -q tests/unit/test_sonic_leader_agent.py tests/test_spawn_team_members.py tests/unit/test_anomaly_report_grant_pins.py tests/unit/agents/test_aegis_security_family.py tests/integration/test_maintenancer_spawn_resolves_tools.py -> expect 625 passed / 24 failed, ALL 24 node-ID-identical at base (re-measure at fb5afe9f2 to classify; logs /tmp/gate-pytest.txt, /tmp/base-measure-24.txt).
4. Pack: bash test/packs/sonic_agent_defs_unit_test.sh -> SELF-TEST: PASS, RESULT: FAIL(static) on exactly A3+A9 (pre-existing; /tmp/gate-pack.txt).
5. Version: .venv/bin/python -m pytest -q tests/test_api.py::test_health_check -> PASS (pins 0.31.19).
6. Stage evidence: manifest fields (version v0.31.19, rollback_safe true, binary_sha256 f8555d8a...); readlink /home/nea/agents-ensemble/current -> releases/v0.31.10 (no flip); payload roster + seal parity (grep sonic-leader rows, diff vs worktree = identical); banner smoke rc=3 (/tmp/v0319-smoke2.h0oG5c/out.txt).

## Bash-raises used
none — every bash tool call <= 580 s (default cap); the PyInstaller build, FE build and stage.sh ran under background proc (sanctioned home for long operations), none exceeded the 1800 s hard cap; no sonic-leader raise was requested or consumed.

## Complexity self-assessment
medium — assessed at plan time (after explore + module split, BEFORE dispatch): three small disjoint modules, no daemon-code edits, zero migrations, every step under a house precedent. No handover was raised; no override occurred; verdict stands unchanged.

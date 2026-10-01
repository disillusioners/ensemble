# v0.16.9 Promote Ceremony Pack (lane-convergence vehicle)

> Documentation artifact only. No system mutations performed by this pack.
> Staged 2026-10-01T15:56:54Z (DevOps stage run, live lane, exit 0). Promote = separate user-gated ceremony.

## Release facts (verified at stage, 2026-10-01T15:56:54Z)

- Staged: `~/agents-ensemble/releases/v0.16.9` — manifest `binary_sha256` `9515c9bfa961900b27c84542f99ca3484fdfce35feb6354307f48de70bd1ebe8` (3-way match: manifest == on-disk == build output), `launcher_sha256` `4c114457b806f64025a1dde8ed02ce1033646f71c9e2ff8f7404bdc32ac28e30` (manifest == on-disk), `rollback_safe=true source=explicit=true` (migration delta v0.16.8→v0.16.9 EMPTY — independently verified via `git diff v0.16.8 v0.16.9 -- daemon/migrations/` = zero output; all destructive-DDL hits pre-existing ≤ v0.16.8), `known_schema_gen` unchanged `20260928_000001_widen_maintenance_runs_expected_bytes.sql`, config/agents/frontend tree hashes byte-identical to v0.16.8 (release delta = binary + launcher.sh only).
- Tag `v0.16.9` = `a91918ff` → commit `d8269b9b` (latest merge commit; ADR-009 D3 exact-match gate satisfied — tag AT staged HEAD; tree `9aef30f5`).
- Repo-lane pin: `/home/nea/ensemble-src` @ `latest` `d8269b9b`, clean — the promote executor resolves scripts via `ENSEMBLE_UPGRADE_SCRIPTS_DIR` to THIS checkout; all fix commits are ancestors. **PRE-ARM CHECK (mandatory):**
  1. `git -C /home/nea/ensemble-src branch --show-current` == `latest` AND `git merge-base --is-ancestor d8269b9b HEAD` (repo-lane checkout at ≥ all fix commits — ancestor form, not exact-sha)
  2. `git -C /home/nea/ensemble-src status --porcelain` empty
- Bundle: 24 non-merge commits (25 incl. the release merge `d8269b9b`) — 4 commissioned fixes (hand-back pin rung, verify-budget headroom, deny-loop bounds b1+b3, sweep hardening/orphaned-sleep pipe-stall) + review/test evidence. Tester verdict READY @ `7957332e` (zero feature-caused reds; convergence drill 4/4). Commission history 47728708.

## MINT (ari lane — arm-owner pattern per v0.16.8 ceremony)

1. Spawn/select FRESH arm-owner instance in the ari lane (holds `system_upgrade` tools).
2. Arm ceremony, `dry_run=true`, target=live, version=v0.16.9, kind=promote → mints nonce (CONFIRM-XXXX format) into `releases/state.json` `pending_actions`, instance-bound + action-bound. Record expiry window.
3. Echo pack to user (Discord): nonce token verbatim + target v0.16.9 + env live + kind promote + expiry + arm-owner identity.
4. On user echo: relay VERBATIM to the arm-owner via the proven api-relay lane; arm-owner verifies nonce match then arms + executes.

## EXECUTION PARAMETERS (speed-first per upgrade_workflow_preference, effective this promote)

- SKIP pg_dump preflight (user directive 2026-10-01). Health gates (livez/readyz/soak) REMAIN.
- Recovery posture: remote-VM replicas + auto-rollback target v0.16.8 (`rollback_safe=true`).
- Expected convergence outcome (the point of this promote): hand-back declares the `.env` pin — `declared=ensemble-main.service verified=UNIT_MANAGED unit=ensemble-main.service` (pin rung `lib.sh` hand-back-scoped; stop path stays cgroup-leaf by design); verify budget `LIVEZ_BUDGET_S=180`.

## POST-PROMOTE VERIFICATION PROTOCOL (closure = all of these)

1. livez `version=0.16.9`; readyz green.
2. `current → releases/v0.16.9`; journal `previous=v0.16.8`.
3. CONVERGENCE PROOF: `cat /proc/<new-main-pid>/cgroup` MUST read `system.slice/ensemble-main.service`.
4. Closure records: supersede project critical notes (pin-rung defect; lane-divergence open item; sweep-hang defect; unbounded-deny-loop defect → all fixed-in-v0.16.9); project history entry.
5. FLAG FOR USER/DEVOPS (manual, NEVER auto): remove/mask the retired `ensemble-live.service` unit file — post-convergence ops step.

## CONTINGENCIES

- Nonce expiry before echo → re-mint fresh nonce, new pack; never reuse.
- Any halt = fail-loud by design (Amendment #1) → report immediately, no improvisation, no ambient nohup fallback.
- Journal-sweep hang recovery: follow the standing runbook (3dd8dc1 family), never improvise.
- Ceremony turns are now protected by the shipped gate bounds (P1 epoch budget 8 + b3 run-deny cap 100, node-loud-terminal-first) — the 6f961c43 recursion death cannot recur in this shape.

## Standing ops notes (carried from stage session)

- `dist/ensemble-prod` holds the fresh v0.16.9 binary — the stage.sh staleness trap is RE-ARMED for the NEXT release; pre-stage `rm -f dist/ensemble-prod` mandatory every stage.
- stage.sh does not append journal history events; the stage record is `releases/<ver>/manifest.json`. Journal gains entries only at promote/rollback/halt/sweep/nonce paths.
- Rollback of the stage itself (if promote is abandoned): `rm -rf ~/agents-ensemble/releases/v0.16.9` (additive-only, nothing points at it; current stays v0.16.8).

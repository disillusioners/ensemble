# Phase 2 — Launcher Seam (verify + precedence-contract pin)

## 1. Objective

Prove the strip causes ZERO functional change at the launcher seam — `launcher.sh` (REPO ROOT, copied to the install dir by `deploy.sh:303`) remains the single env source — and pin the 4-rung env-precedence contract in the docs, with the unit rung now EMPTY BY DESIGN. No functional launcher edits.

## 2. Decisions

| ID | Decision | Rationale (verified @9fc95176b8) | Rejected alternatives |
|----|----------|----------------------------------|------------------------|
| D2a (ref D3) | No launcher code changes — verification + doc pin only | `load_env_file` (:146-189) called env-first at `main()` :1649, before binary resolution, per-key export :186, LAST-wins → `.env` beats everything earlier (unit env, set-environment); precedence is already structural per the in-code comment :1646-1647 (W1-corrected) | Any launcher reorder (would be a functional change with no benefit) |
| D2b (ref R10) | KEEP `launcher.sh:144-145` prose ("prod may rely on plist-passed env") verbatim | TRUE for launchd: both plists are already zero-env (`ensemble-prod.plist:70-71` env deliberately absent; watchdog plist zero Environment mentions) — the sentence describes the plist lane, not systemd | "Fixing" it away (would introduce a false claim about launchd) |

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (verified anchors) | Depends on | Risks carried | AC |
|----|------|--------------------|--------------------------------|------------|----------------|----|
| T2.1 | VERIFY (read-only) the single-source claim: `load_env_file` :146-189 is the only env-ingest path; call site `load_env_file "$INSTALL_DIR/.env"` at :1649 in `main()`, env-first before `resolve_binary`; per-key `export "$key=$value"` :186; comment :1646-1647 (W1-corrected; block :1645-1647) ("Exports win over the binary's own .env load (run_app.py only sets unset vars) — precedence is structural"). Record the verification in the dev notes with the A10 precision: `run_app.py`'s only-unset `.env` load is **frozen-binary-only** (`sys.frozen` gate :9) and **quote-unaware** (:21-28) — the structural precedence guarantee lives solely in the launcher path (`:1649` + `:186`); no edits | Verification recorded (incl. A10 precision + the zero-consumer census for the emptied unit rung, arch §4.1); zero launcher diffs attributable to P2 | launcher.sh:146-189, :186, :1645-1647, :1649; run_app.py:9, :21-31 | P1 | R1 | AC2.1 |
| T2.2 | VERIFY the MALLOC_ARENA_MAX triangle still holds post-strip: template static directive `Environment=MALLOC_ARENA_MAX=2` (:110) seeds the unit env; launcher default-only `: "${MALLOC_ARENA_MAX:=2}"` + export (:1658-1659) never overrides a staged value; `.env` staged value wins via :1649. With the unit rung now carrying ONLY this static directive (no secret-bearing lines), the effective chain is unchanged for every key the daemon reads | Precedence chain documented and confirmed unchanged | template:110; launcher.sh:1658-1659 | T2.1 | R1 | AC2.1 |
| T2.3 | Pin the precedence contract TEXT (authored here, placed during P3's `systemd-adoption.md` pass — single edit, no double ownership): `.env (launcher load_env_file, last-wins) > unit Environment= (now EMPTY by design — zero-env contract; only the static MALLOC_ARENA_MAX directive remains) > systemctl set-environment (inherited) > code defaults (launcher `:=` :1658-1659; config.yaml defaults)`. One sentence + one 4-item list max; include the A10 runbook precision — `load_env_file` is called **ONCE per launcher process at `:1649`, OUTSIDE `run_loop`**: child restarts re-inherit exports without re-reading `.env` (comment :1657); note the `set-environment` rung is vacuous in-repo (zero producers/consumers, arch §4.1) | Contract paragraph exists in the runbook and matches this text | docs/runbooks/systemd-adoption.md (env-sourcing area :135-137 — edited in P3/T3.2) | P1, P3 | R4, R10 | AC2.2 |
| T2.4 | Comment sweep for unit-env references at the seam: template [Service] comment handled in P1/T1.4; launcher.sh needs NO edit (:144-145 stays per D2b). Confirm no launcher comment claims the unit supplies env | `grep -n 'unit' launcher.sh` shows no stale unit-env claims | launcher.sh:144-145 | P1 | R10 | AC2.3 |
| T2.5 | Verification greps (read-only): zero `EnvironmentFile` in scripts/*.sh + launcher.sh + tests (baseline D9: only oauth2-auth.md docs + layer1-arm.md:31, both outside this seam); plists untouched (`git diff --stat scripts/ensemble-prod.plist scripts/ensemble-watchdog-watcher.plist` → empty) | Greps green; plists byte-untouched | repo-wide | P1 | R3 | AC2.4 |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC2.1 | Verification record (dev notes / PR description) confirms: single env source, call-site order, LAST-wins semantics, MALLOC triangle, the A10 frozen-binary-only/quote-unaware `run_app.py` precision, the once-per-process/OUTSIDE-run_loop call shape, and the zero-consumer census for the emptied unit rung (no daemon/supervision/journal-sweep/proc_tools/stop-ensemble/promote-rollback/plist consumer depends on unit-env PORT/POSTGRES_* — arch §4.1) — each with the anchor cited; zero launcher.sh diffs in the branch |
| AC2.2 | `docs/runbooks/systemd-adoption.md` contains the 4-rung precedence contract with the "unit rung empty BY DESIGN (zero-env contract)" clause, textually identical to T2.3's spec |
| AC2.3 | launcher.sh :144-145 byte-untouched; no stale unit-env claims anywhere in launcher.sh |
| AC2.4 | `grep -rn 'EnvironmentFile' scripts/ launcher.sh tests/` → zero hits (docs hits only per D9 baseline, minus the layer1-arm line P3 rewrites); both plists byte-identical to parent |

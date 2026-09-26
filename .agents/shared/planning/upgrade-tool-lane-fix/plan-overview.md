# Plan Overview: v0.15.3 Upgrade Tool-Lane Live Promote Fix

**Date:** 2026-09-26 · **Status:** COMPLETE — ready for implementation dispatch
**Branch:** `feature/upgrade-tool-lane-fix` (base `latest @ 139ba352` = v0.15.2; master DEAD, never touched)
**Mission:** Tool-lane live promote structurally functional (arm → executor → gate → commit) with loud failures, cut as v0.15.3.

## Artifact Map

| File | Lines | Role |
|------|-------|------|
| `plan.md` | 358 | **Unified implementation plan** — phases, change map, decision table, test matrix, risks, out-of-scope, resolutions, release-cut |
| `phase1-plan.md` | 543 | P1 Python subsystem detail (items 1-5 + 7; ~20 sibling tests; sequencing 7→1→3→5→4→2) |
| `phase2-plan.md` | 490 | P2 shell rider detail (item 6 + ADR-035 paste-ready draft + tests 9a-9g + residual ops gates) |
| `research-findings-python.md` | 323 | Branch-verified Python anchors + test-pin inventory + drift report |
| `research-findings-shell-e2e.md` | 272 | Shell/BSD anchors + ensure.md lane rule + house patterns + ADR site survey |
| `plan-overview.md` | this | Top-level synthesis (planner-authored per aggregator boundary) |

## Completion Test

*A 3-factor-verified live arm, with no F2 forge, completes arm → spawn → gate → commit → TERMINAL, with the in_flight journal stamped `f2_verified_closed: true` + `f2_verified_note: <source>:<run_id>`; an unverified arm exits 78 at `require_live_guard` and the failure is journaled.*

## Phase Architecture — P1 ∥ P2 (zero file overlap, parallel implementer instances)

| | P1 — Python subsystem | P2 — Shell rider + ADR |
|---|---|---|
| Items | 1 enabler (argv flag + env passthrough from verified-arm pending_op fields), 2 loud executor-exit journaling (sweep-service-owned reaper queue+worker), 3 arm preflight pre-nonce, 4 boot+periodic reconcile sweep + pending_actions GC (owns the Item-2 reaper queue+worker), 5 `_terminal_outcome` terminal-class filter | 6 stage.sh rollback-safety rider (silent-false path DEAD), ADR-035 mint, tests 9a-9g |
| Sequencing | 7→1→3→5→4→2 (predicate-coupled first; sweep service lands before the reaper it owns) | T1 rider → T2 log → T3 ADR → T4/T5 tests → T6-T8 fences |
| Tests | ~20 sibling tests (ZERO existing pins flip — `:3388-3391`/`:3439-3444`/`:902` stay green as the unverified side) + sole-caller & truth-table pins + interlock pack extension | 9a-9g incl. 9g regression pin (silent-false DEAD); 9f implemented |

## Key Decisions

1. **Item 7 RATIFIED (user, 2026-09-26):** the 3-factor-verified arm (user_confirmed + genuine-user-turn via registered source + nonce match) IS the F2-equivalent attestation for tool-lane live promote. No additional auth layer. Decision table retained with `[COLLAPSED — superseded]` alternative for audit. Rationale: "feature-first, revisit if threat model changes."
2. **Env passthrough mechanism:** rides existing `extra_env` extras merge (`executor_env :1003-1004`) — `EXECUTOR_ENV_ALLOWLIST` never widens; ambient stays fenced for every unverified path. `F2_VERIFIED_NOTE=<source>:<run_id>` is documentation only (lib.sh `:584-589` already consumes it); the gates remain `require_live_guard` (env) + `--f2-verified-closed` (argv). Zero promote.sh edits. Predicate is **5-conjunct** (adds `op.env == "live"` — M-11: safe-by-coincidence → safe-by-construction), consumed via a **mandatory shared helper `_verified_arm_extras`** (3 sites, name-freeze + truth-table pin incl. env=demo rows); unverified call sites stay byte-identical behind `if is_verified_arm(op):`.
3. **ADR-035 ownership:** P2 mints (paste-ready draft `phase2-plan.md:194-232`); P1's exit criterion cites it. Insertion: `self-restart-upgrade-phase2/decisions.md` after ADR-032 (`:213`), Decision Index row appended. Both halves mandatory: verifier verdict evidence (Q1 NO — 4db90d74 doesn't close F2; Q2 YES — user-executed-only) + user supersession with revisit anchor.
4. **53ca row-type discrepancy:** implementation-time verification (jq on live `state.json` once v0.15.3 runs; nobody touches live this mission). Both mechanisms (pending_op reconcile + pending_actions GC) land in Item 4 — clearance covered either way. **Operator instruction:** reboot the live daemon onto the v0.15.3 build after staging — the row clears on the first reconcile sweep tick.

5. **Reaper constants:** 660s default timeout + **benign-detach** — at timeout the sweep-owned reaper worker journals `executor_still_running` and detaches (never kills). Floor math: livez 60 + readyz 120 + soak 300 + overhead ≈ 490s minimum → 660s headroom.

## Test Strategy

- **Two-sided contract, sibling-tests shape (ZERO existing pins flip):** verified arm gains flag + `ENSEMBLE_UPGRADE_LIVE` + `F2_VERIFIED_NOTE` via NEW standalone sibling tests; existing pins — argv `:3388-3391`, allowed-set `:3439-3444`, extras `:902`, poison `:935`/`:962`/`:3423-3434`, `test_release_journal.sh:188/:200` — stay green UNTOUCHED as the unverified side. Added: sole-`spawn_executor`-caller pin + `is_verified_arm` truth table.
- **e2e:** ZERO execution-lane intersection by ensure.md rule (all 6 changes classified N/A — audit in `plan.md` §5f). Item 4 boot sweep gets a `lane_gate_boot_probe`-style shaped validation (~2min scrubbed dev boot, must-not-crash, sweep + GC ticks observable).
- **Spawn-seam testing rule:** patch `daemon.tools.upgrade_journal.spawn_executor`, NEVER `subprocess.Popen`.

## Top Risks (full roll-up: `plan.md` §6, 14 deduped rows)

1. Verified-arm predicate drift across items 1/2/3 → single shared helper, unit-pinned on all four literal fields.
2. Boot sweep racing a fresh arm → owner-pid liveness via `os.kill(pid, 0)` + time-bound predicate before clearing; instance-match check is defense-in-depth only (ids go stale across reboots).
3. CI breaking on unset `ENSEMBLE_ROLLBACK_SAFE` + DROP-detected → refuse exit 78 BEFORE release-dir write, actionable WARNING with both override forms, CHANGELOG names the breaking change.

## Out of Scope (fences) + Residual Ops Gates

**Code fences:** P2.1 GNU debt (`lib.sh:84-89`, `:706-712`, `:1238-1295`); F2 loopback-forge closure (separate commission — `jobs_crud.py:275-278`, `messages.py:391`); any live touch; FE work; manifest schema changes; allowlist widening; promote.sh argv case edits.

**Residual ops gates (NOT code scope — operator obligations for live eligibility):** scripts-bundling validation FL-23 (runbook §8.4(i) `:482`); 3 fresh ari cycles on the bundled release (§8.4(ii)(3) `:484` — tester dev/demo e2e covers PARTIALLY only); `ledger_check.py:277-296` does NOT verify bundling (runbook/evidence obligations). Full table: `phase2-plan.md:234-264`. Tool-lane live promote remains non-armable from the agent until FL-23 bundling validation + 3 fresh ari cycles on the bundled release + the user-executed-only attestation are independently satisfied.

## Open Questions (defaults apply unless caller overrides before implementation dispatch)

1. Preflight refusal token → **default: mint new `arm-preflight-refused:<sub-token>` family** (3 sub-tokens, each pinned).
2. `nonce_consumed` label → **default: rename to "awaiting executor (pending)" + grep-verify + lockstep consumer update**.
3. Reaper timeout → **default: 660s + benign-detach — at timeout the reaper journals `executor_still_running` and detaches (never kills); floor math livez 60 + readyz 120 + soak 300 + overhead ≈ 490s minimum. Knob: `ServicesConfig.upgrade_journal_reaper_timeout_seconds`**.
4. Sweep interval → **default: 90s, own knob `upgrade_journal_sweep_interval_seconds`**.
5. Boot sweep placement → **default: AFTER `_boot_db_preflight` + app.state, BEFORE first request**.

## Release-Cut Sequence (giter executes at mission end)

10a) Pre-commit the 13 pre-existing `.agents/` evidence notes as ONE dedicated commit (2 modified + 11 untracked; zero code) — BEFORE `bump_version.py` because `:121` runs `git add -A`. → 10b) Bump v0.15.3; verify bump commit carries ONLY v0.15.3 files. → 10c) Merge to `latest`, tag `v0.15.3`, stage with `rm -f dist/ensemble-prod` (stale-dist trap re-arms every release). Stage command sets `ENSEMBLE_ROLLBACK_SAFE=1` explicitly — legitimate override: the v0.15.2→v0.15.3 migration delta is EMPTY (mission adds zero migrations; the full-history DROP grep is irrelevant to this delta; ADR-035 cites it).

## Next

Plan routes to architect for design review per caller's flow. Implementation dispatch: one Python implementer instance (P1) ∥ one shell/docs implementer instance (P2); integrator merges into the v0.15.3 commit batch.

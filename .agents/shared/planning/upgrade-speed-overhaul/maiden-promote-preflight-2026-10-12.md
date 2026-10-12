# Maiden-Promote Preflight — v0.20.1 (upgrade-system overhaul vehicle)

- **Version:** 1.0 @ 2026-10-12
- **Mission branch:** `feature/upgrade-speed-overhaul` @ `60eeaec39` (post-version-bump)
- **Canonical path:** `.agents/shared/planning/upgrade-speed-overhaul/maiden-promote-preflight-2026-10-12.md` (this file)
- **Scope:** The operator preflight checklist for the v0.20.1 maiden promote. **NO live promote is performed in P4** — this is the gate decision doc.
- **Related docs:**
  - `.agents/shared/planning/upgrade-speed-overhaul/plan-overview.md` §6 (maiden-promote mode + D-P4.5 resolution)
  - `.agents/shared/planning/upgrade-speed-overhaul/decisions-resolution-2026-10-12.md` (P-Dx matrix — 14 cells RESOLVED)
  - `.agents/shared/planning/upgrade-speed-overhaul/risk-register.md` §4 (residual-risk sign-off — RE-BASELINED to FAST)
  - `docs/runbooks/upgrade-overhaul-watch.md` (operator-facing watch runbook; the four C-11 controls)

---

## §0. Decision (the single binding rule)

**Maiden-promote mode is FAST** (default adaptive 60–120 s soak @ 15 s cadence, 3 consecutive greens, 900 s wall-clock ceiling). This is RE-BASELINED to FAST per the leader ruling on Q6b (closes the conservative-vs-fast decision for v0.20.1).

**The single sanctioned escape hatch**: `ENSEMBLE_SOAK_CONSERVATIVE=1` (or `ENSEMBLE_PROMOTE_SOAK_S=300`). No other budget knob is part of the escape. The 3-factor nonce ceremony is **untouched and remains user-gated** (per the standing project metadata `upgrade_policy`).

---

## §1. Preflight checklist (gate conditions, in order)

### 1.1 Stage verify

- [ ] `git rev-parse HEAD` == `60eeaec3937536d2cdd085144117cd9847b3cf5a` (v0.20.1 version-bump commit)
- [ ] `git describe --tags --exact-match HEAD` == `v0.20.1`
- [ ] `daemon/__init__.py __version__` == `0.20.1`
- [ ] `pyproject.toml version` == `0.20.1`
- [ ] `tests/test_api.py` assertion line == `0.20.1`
- [ ] `uv.lock` `name = "ensemble"` `version` == `0.20.1`
- [ ] No committed files outside the 4-file version-bump commit (no `.agents/approver/` files in any commit — verified)

### 1.2 Quarantine clear

- [ ] `releases/state.json` `quarantined[]` is empty (no pre-existing quarantine from prior stage failures)
- [ ] `releases/state.json` `current` and `previous` reflect the v0.20.0 → v0.20.1 promotion arc (operator-supplied; the maiden promote is the one that creates v0.20.1's `current`)

### 1.3 Version match (T-PIN-failclosed)

- [ ] `stage.sh:353-360` version-verify pass against `daemon/__init__.py __version__` (fails-closed exit-78 on mismatch)
- [ ] `manifest.json.binary_version == 0.20.1` (per `stage.sh:361` derivation; explicit `ENSEMBLE_BINARY_VERSION=0.20.1` if any non-`v`-prefixed version is used)

### 1.4 FAST mode parameters (the default — no env-override required)

- [ ] `ADAPTIVE_SOAK_FLOOR_S=60` (wall-clock floor)
- [ ] `ADAPTIVE_SOAK_CAP_S=120` (cap; shrinks against `ceremony_remaining_s - 60` if caller threads it)
- [ ] `ADAPTIVE_SOAK_CONSECUTIVE=3` (consecutive-green threshold; cap-reach with `< 3` → exit 1 → auto-rollback per C-1)
- [ ] `ADAPTIVE_SOAK_CADENCE_S=15` (probe cadence — 15 s, NOT 30 s; per Q6a final)
- [ ] `PROMOTE_TOTAL_DEADLINE_S=900` (top-level wall-clock ceiling)
- [ ] `PROMOTE_TOTAL_DEADLINE_S=900` ≤ `EXECUTOR_STALENESS_WINDOW_S=900` (conservative-mode pin; under default mode the assertion holds against the 900 s ceiling as ≤)
- [ ] `BOOT_GRACE_S=60` (post-restart grace; transient classifications silently ignored during grace)
- [ ] `BUSY_INFLIGHT_BOUND=5` (busy-amnesty bound; `inflight_turns ≤ 5` reads as busy-but-alive)

### 1.5 Sandbox / fixture verify (C-12 — vehicle reconcile)

- [ ] `git merge-base --is-ancestor c8b3b09f2 c8a3fbf6` exits 0 (commit c8b3b09f2 = "fix(checkpoint): pool-backed saver + retry proxy + readiness sentinel probe" is on the v0.20.1 ancestry — verified by P4)
- [ ] `daemon/persistence.py` exists (pool-backed saver)
- [ ] `daemon/checkpoint_adapter.py` exists (retry wrapper)
- [ ] `daemon/services/readiness.py` probe is in the livez/readyz critical path (verified by `tests/test_adaptive_soak.sh` Section T3.8d pin)

### 1.6 P-Dx matrix resolved (R-13)

- [ ] `.agents/shared/planning/upgrade-speed-overhaul/decisions-resolution-2026-10-12.md` exists
- [ ] All 14 P-Dx cells have concrete `Decision · Cite · Rejected alternative · Evidence` (verified by P4)
- [ ] No fenced-out cells (no decision deferred past the maiden-promote gate)

### 1.7 Test pack green (R-13 — test over-fit)

- [ ] `bash tests/test_upgrade_overhaul_all.sh` (default fast subset) reports PASS for all 11 packs (excluding the known pre-existing `test_launcher.sh` BSD-date-debt — see §1.8 below)
- [ ] `bash tests/test_upgrade_overhaul_all.sh --full` reports the release_journal + atomic_flip packs as **pre-existing** fixture debt (34 failures in `test_release_journal.sh`, 0 in `test_atomic_flip.sh`); NOT regressions from the overhaul
- [ ] Pytest packs green via `uv run --frozen --no-sync pytest --noconftest -x -q tests/unit/tools/test_upgrade_journal_sweep_liveness.py tests/unit/tools/test_upgrade_splice_helper.py tests/unit/tools/test_audit_after_soak_split.py` (49 / 49 passes expected)

### 1.8 Adjudication carry-forward (D-P4.2)

The `tests/test_release_journal.sh` pack has **34 pre-existing failures** (BSD-only `date -ju -v` in the sweep fixture — pre-P1 baseline was 286 failures; the delta of 252 was earned by our work; the remaining 34 are GNU-host fixture debt per the `risk-register.md §3` GNU-date-debt carve-out). **No genuine regression from the overhaul** — verified by:
- [ ] `git show a2997357b~1:tests/test_release_journal.sh | bash` shows the same fixture-debt failure family on the pre-change HEAD
- [ ] The +5 failures from pre-P2 baseline (29×) to post-P3 baseline (34×) are the splice helper's stricter invalid-JSON exit-78 refusal on fixtures containing literal `LASTHB` garbage from the BSD date debt — this is the **intended torn-journal defense**, not a regression

The `tests/test_launcher.sh` pack has **3 pre-existing failures** (8c journal byte-identical, 8c current symlink untouched, 8g stale window rollover → all from `date -ju -v-700S` BSD-only calls in the fixture). **No genuine regression** — verified by:
- [ ] The failures are pinned to fixture subtests using `date -ju -v...` syntax (lib.sh:368 pattern)
- [ ] Pre-P1 baseline has the same failures

### 1.9 Vehicle staging (D-P4.4 — sandbox only, NO flip)

- [ ] `INSTALL_DIR=$(mktemp -d)` (NOT `~/agents-ensemble`)
- [ ] `bash scripts/upgrade/stage.sh sandbox --version v0.20.1 --allow-stale-stage --skip-build <bin>` (sandbox install dir; sandbox port)
- [ ] `ENSEMBLE_BINARY_VERSION=0.20.1` if the version is non-`v`-prefixed (not the case here — `v0.20.1` resolves cleanly)
- [ ] `manifest.json` fields verified: `rollback_safe=true`, `binary_version=0.20.1`, `version=v0.20.1`, `known_schema_gen=<head schema>`
- [ ] `rollback_safe=true` source either `default=true` (no destructive DDL in migration delta) or `explicit=true` (operator confirmed destructive DDL is acceptable; required for v0.20.1 since previous release had destructive DDL)

### 1.10 Bypass checks (operator must verify — the hard constraints)

- [ ] **NO** `systemctl list-units --type=service` shows `ensemble-main.service` running under the operator's intent (live daemon is the `~/agents-ensemble` install — unaffected by the sandbox stage)
- [ ] **NO** port 9797 / 9798 / 7979 occupied by the sandbox (the sandbox installs use a different port, e.g. 9877)
- [ ] **NO** `.env` inside the staged release dir (the `index.html.build-provenance.json` sidecar and any staging-time `.env` are not in the release payload per ADR-014/m6)
- [ ] **NO** manual journal state.json edits (the journal is the load-bearing audit trail; quarantine clears ONLY via re-stage)

---

## §2. Conservative-mode escape (when to set `ENSEMBLE_SOAK_CONSERVATIVE=1`)

Per risk-register §2 change-rollback plan #1, the FIRST response to any "too fast, want the old ceremony back" report is `ENSEMBLE_SOAK_CONSERVATIVE=1`. Use it when:

- 2+ consecutive promote runs show the adaptive-soak early-exit firing at the 60 s floor (3 greens at 15 s cadence) — the soak may be racing through and missing slow-burn regressions
- The June-style "agents-ensemble got stuck in transient storms" pattern re-emerges (the C-1 cap-reach-without-3-greens → exit 1 auto-rollback should catch it, but if the operator wants the v0.18-era flat-wait, conservative mode is the answer)
- A full abort-and-revert is requested by the user (conservative mode is the lightest-touch revert)

**Conservation mode is bit-identical to v0.18-era behavior** (300 s flat-wait, no early-exit, 30 s cadence — per decision (c) D-P3.2). No code revert required; the next promote's gate_soak reads the env var (`lib.sh:4926-4928`).

---

## §3. First-recovery-step (per phase breach — C-10b)

When `_enforce_deadline` halts with `daemon-state=stopped` (post-stop breach):

1. **VERIFY current is still null** in `state.json` (no flip recorded — the promote halted BEFORE the atomic-flip block)
2. **RESTART current**: either `bash launcher.sh restart_current` OR `systemctl --user restart ensemble-main.service` (whichever the install topology expects)
3. **RE-INVOKE** the daemon's `boot_sweep_commit_and_continue` path (launcher.sh:1020-1037) — if the prior `in_flight.displaced_version` was stamped per C-4, this completes the commit on next boot
4. **DO NOT re-promote** — the next promote would attempt an atomic flip on a possibly-committed state, re-introducing the N-2 walk bug

---

## §4. Abort criteria (the absolute stops)

The maiden promote MUST abort if any of:

- **Pre-ceremony emergency** — the `git rev-parse HEAD` is NOT `60eeaec39` (working tree has uncommitted changes — the version-bump commit hasn't been finalized or the working tree has drift)
- **Manifest version mismatch** — `stage.sh:353-360` version-verify refuses (exit-78); this is a fail-closed integrity gate
- **Real-unit contamination** — any test in the preflight touches `~/agents-ensemble`, the live systemd unit, or any port the live daemon binds (9797/7979/9798) — abort immediately
- **Splice-helper absent** — `python3` not on PATH OR below 3.7 (the C-7 preflight probe refuses; promote refuses; abort and install python3 ≥ 3.7 before re-attempt)
- **Nonce ceremony broken** — the 3-factor nonce chain (user_confirmed + genuine-user-turn + nonce match) cannot be satisfied (the user is the only one who can echo the nonce — operator aborts and waits)

---

## §5. Post-promote gate (the four C-11 controls)

The four C-11 controls are detailed in `docs/runbooks/upgrade-overhaul-watch.md` §C:

1. **Post-promote real-message smoke** (mandatory): one real instance + one real message; verify the job lands DONE
2. **First destructive blob-prune drain watch** (24 h cadence): verify ≥1 destructive_prune event within 24 h
3. **No-key-rotation window**: key rotation deferred (the standing `deferred_key_rotation` critical note)
4. **Transient indicator list**: distinguish transient (retry-green) from hard failure (halt) via the journal `daemon-state` field + `deadline-breach-<phase>` reason tokens

All four are operator-facing watch items for the first 24-72 h post-promote. The auto-rollback machinery (`promote.sh:459-496` + `daemon/services/upgrade_journal_sweep.py`) provides the standing safety net; the four C-11 controls are the operator-visible extension.

---

## §6. Sign-off

This preflight is signed off when:

- [ ] All §1 checks pass
- [ ] All §4 abort criteria have been considered and none apply
- [ ] The user has echoed the 3-factor nonce (per `upgrade_policy`; this is NOT the operator's authority)
- [ ] The stage manifest fields are recorded (sandbox path, manifest version, rollback_safe source)

The maiden-promote is now go-decision-ready. **No live promote is performed by this doc** — the operator (with user nonce echo) runs the actual `bash scripts/upgrade/promote.sh live --version v0.20.1 --f2-verified-closed` after satisfying the gate.

---

**End of maiden-promote preflight.**
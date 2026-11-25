#!/bin/bash
# ============================================================================
# scripts/upgrade/lib.sh — shared library for the staged release/upgrade
# pipeline (Self-Restart/Self-Upgrade Phase 2, P2.1 — plan D1..D6, ADR-004/
# 005/009/012 as reconciled with the Phase-2 architect rulings D-FA4.x/D-FA5.x)
# ============================================================================
# SOURCED (never executed directly) by stage.sh / promote.sh / rollback.sh /
# status.sh. Provides:
#
#   resolve_env <target>      3-env topology table + fail-closed sandbox rules
#   require_live_guard        TARGET=live needs ENSEMBLE_UPGRADE_LIVE=1 (exit 78;
#                             mirrors deploy.sh:139-148 with the upgrade guard var)
#   journal_*                 atomic read/write of releases/state.json (D4:
#                             temp-file + mv; .torn rejects torn writes)
#   lock_acquire/_release     rollback.lock.d mkdir-lock (D5/D-FA5.1: mkdir IS
#                             the acquire; owner/run_id/heartbeat; stale-break
#                             >300s via mv to rollback.lock.stale.<pid>)
#   integrity_verify          per-file sha256 tree verification of a release
#                             against its manifest.json (D-FA4.4) + no-.env
#                             invariant + current-symlink resolution
#   _probe                    health-gate probe (2s sleep, curl max-time 5s —
#                             the same budget as deploy.sh phase 5)
#
# Size rationale: ~6720 lines (refreshed 2026-10-30 — the previous note said
# ~5560, i.e. ~1,080 lines stale; +854 of the growth is the
# promote-preflight-hang work: the O(N) journal-read wrapper family
# `_journal_read_helper_path`/`_journal_read_probe`/`_journal_read_legacy`/
# `_journal_read_fallback`/`journal_read` + `_json_sub_legacy`, the per-run
# logging sink `_log*`/`_log_write_failed`, `_run_bounded_step`, the
# `_pipeline_scripts_stamp_verdict` stage gate, the numeric-knob resolver,
# the TERM-forwarding tree walk and `_run_log_retention`). The file crossed
# >3000 during the supervision-detection + 3-layer adoption-protocol growth,
# then ~2400 more across the W5 journal-splice shell helpers, C4 live-waiver
# refusal, F2 gate plumbing and the fast-by-default promote lane; it is still
# the single shared substrate for the five entry scripts (stage/promote/
# rollback/restart/status) plus the SOURCED consumers stop-ensemble.sh /
# adopt-unit.sh; splitting would duplicate cross-cutting contracts
# (journal layout, lock semantics, ENV allowlist, BSD/GNU dispatch). The
# file is the contract inventory. The recent growth is the supervision-
# detection section (classify / outcome map / dualfight / unit hand-back)
# — one twin lives here, the python twin in daemon/tools/upgrade_journal.py.
# BACKLOG (fix-back cycle 2 + fix cycle 2 — candidates ONLY, no split
# executed; any split must keep every consumer's sourcing line
# byte-identical and re-run the full journal/lock/supervision suites):
#   (a) supervision_* family (~:1879-2900: hand-back, restart_via_launcher,
#       owned_pids, classify, outcome map, dualfight) → supervision_lib.sh;
#   (b) pipeline_settled + adoption_marker_* (~:1212-1527, Layer i/ii) →
#       settle_lib.sh;
#   (c) journal_* family (~:481-1014, atomic state.json IO) → journal_lib.sh;
#   (d) journal-READ family (~:1324-1500: `_journal_read_helper_path` →
#       `journal_read`) + `_json_sub_legacy` → journal_read_lib.sh (all five
#       consumers source lib.sh, so the sourcing line stays byte-identical;
#       the parity pack's `git show <rev>:scripts/upgrade/lib.sh` oracle
#       extraction needs a one-line path update);
#   (e) per-run logging family (`_log*` / `_log_write_failed`, ~:296-486)
#       + `_run_log_retention` → log_lib.sh.
#
# ENV DISCIPLINE (D-FA4.6 + test-strategy §5):
#   - the resolved triple (INSTALL_DIR / PORT / POSTGRES_DB) is asserted and
#     echoed by every action script before doing anything;
#   - NO literal live-port number exists anywhere under scripts/upgrade/ (the
#     D-FA4.6 rule; the live port is resolved from the live install dir's
#     staged .env — ADR-014: PORT is staged env state, not a script constant —
#     and resolve_env fails CLOSED when it is absent);
#   - sandbox has NO defaults that could resolve to live: INSTALL_DIR + PORT
#     are explicit requirements.
#
# NO NETWORK FETCH anywhere in this pipeline (ADR-009 D3): builds come from
# the LOCAL checkout; no VCS pull/fetch/clone is ever issued.
#
# Bash 3.2 / BSD tools only (macOS). No flock(1).

# NOTE on patterns: agent dir names contain glob metacharacters (e.g.
# tidier[v2]) — every ${var#pattern} / ${var%pattern} string op that
# interpolates such names MUST quote the interpolated part
# (${var#*"${key}"}) so it matches literally. Do NOT add `set -f` here:
# pathname globbing IS used (retention_evict release scan); the quoted-
# pattern form is the load-bearing fix.
# ============================================================================

# Upgrace namespace prefix for every log line (callers set LOG_TAG before
# sourcing if they want their own name; default "upgrade").
LOG_TAG="${LOG_TAG:-upgrade}"

# ── PIPELINE SCRIPTS IDENTITY STAMP (promote-preflight-hang phase 6, T6.4/D6-4/D6-8) ──
# The daemon-side arm-time guard parses THIS line (a bounded first-64KiB
# read of THIS FILE — B3: the file at <scripts_dir>/lib.sh is the guard's
# input, never the executor echo) before the ceremony nonce burns. A
# pinned scripts dir whose lib.sh predates this stamp is refused as stale.
# The release process stamps the merge commit that introduced the current
# ceremony-relevant scripts/upgrade semantics here (cadence D6-8: bumps
# per release that touches ceremony-relevant scripts/upgrade semantics —
# the SAME cadence as the daemon's PIPELINE_SCRIPTS_FLOOR_COMMIT; the two
# travel together, T6.15/AC15).
PIPELINE_SCRIPTS_REV=46d55b002d35092c29c391fc76e6b897c0819e11

# ── _numeric_knob <NAME> <raw-value> <default> — A5 (code council, 2026-10-30)
# The budget knobs below are consumed in ARITHMETIC (`$(( now + BUDGET ))`)
# and in `sleep`. A non-numeric value used to die CRYPTICALLY (`abc:
# unbound variable` inside `$(( ))`) or, worse, silently INVERT a bound
# (`sleep abc` fails instantly => the watcher fires at elapsed=0 and the
# first wrapped step "breaches" in zero seconds). Resolve every numeric
# knob through here: a non-numeric value is a LOUD, named validation error
# on stderr and the documented default takes over — never a cryptic death
# and never a silently-dead watchdog. Empty => default (the documented
# "unset" spelling). Non-fatal by design: this runs at SOURCE time, and
# status.sh / restart.sh / rollback.sh (R1 recovery class) must never be
# fail-fast halted by an operator typo in an unrelated knob.
_numeric_knob() {
    local __name="$1" __raw="${2:-}" __def="$3"
    if [ -z "$__raw" ]; then
        printf '%s' "$__def"
        return 0
    fi
    case "$__raw" in
        *[!0-9]*)
            printf 'ERROR: %s="%s" is not a decimal integer — using the documented default %s (a garbage budget silently disables or inverts the bound it guards; A5/code council 2026-10-30)\n' \
                "$__name" "$__raw" "$__def" >&2
            printf '%s' "$__def"
            ;;
        *) printf '%s' "$__raw" ;;
    esac
}

# Health-gate budgets (seconds) — same numbers as deploy.sh phase 5.
# LIVEZ_BUDGET_S = 180s (v0.16.9, was 60s): attempt-2 halt showed the
# post-flip boot alone eats ~51s for the journal sweep on the first live
# restart; a 60s verify budget is too tight and halts the promote on
# otherwise-healthy boot-and-livez delays. 180s gives ~3x the base 60s
# headroom for cold-boot + verify span. Worst-case cost = extra wait
# before halt on a genuinely-dead boot (still halts); too-short budget
# = false halt on a healthy boot (the worse outcome — every verify-
# budget halt is a LIVE promote halt).
#
# P3 / decision (c) (forward-looking note, P1 polish — comment-only
# refresh; values land unchanged in P3): the per-phase budgets above
# (livez 180, readyz 120) are preserved through the adaptive-soak
# trio. The default soak is adaptive: 60s floor, 120s cap, 3
# consecutive greens at 15s cadence, fail-fast on red. Override
# with `ENSEMBLE_PROMOTE_SOAK_S` or revert to conservative via
# `ENSEMBLE_SOAK_CONSERVATIVE=1`. The total ceremony budget is 900s
# (15 min) wall-clock — breach routes to halt, not commit.
# See decisions.md (c)/(d), phase3-plan.md D-P3.1/D-P3.2, plan-overview
# §4. C-5: this comment refresh rides the P1 PR as a discoverability
# win for operators BEFORE P3 lands the behavior.
LIVEZ_BUDGET_S="$(_numeric_knob LIVEZ_BUDGET_S "${LIVEZ_BUDGET_S:-}" 180)"
READYZ_BUDGET_S="$(_numeric_knob READYZ_BUDGET_S "${READYZ_BUDGET_S:-}" 120)"
# P3 / decision (d) — total ceremony wall-clock ceiling. The
# promote.sh top-level wraps every phase boundary in
# `_enforce_deadline promote_deadline <phase-name>`. Breach → journal
# `halt` event with `daemon-state` field (C-10b) + lock_release + exit
# 78. The breach routes to the existing halt machinery at
# promote.sh:459-496 — it does NOT trigger auto-rollback (current
# behavior is fine — we timed out on ceremony procedures only). The
# 900s ceiling is the absolute hard cap; all-budgets-max (stop worst
# 755s + every budget max + adaptive soak squeeze) = 1400s → halt.
# C-10a BOUND TABLE (corrected 2026-11-02, promote-handback-halt
# Phase 1 / D-P1-8 — the previous "handback 90 … = 870s" line was
# fiction: it mixed measured values with budget ceilings and never
# described the code): the 900s ceiling is a STOP-LOSS, not a
# budgets-fit guarantee. Worst-case budget knobs in force:
#   preflight 300 (PREFLIGHT_PHASE_BUDGET_S) / stop ~225 (derived) /
#   launcher swap ~0 / atomic flip ~0 /
#   hand-back verify 300 + one 60s progress-gated grace
#     (HANDBACK_VERIFY_BUDGET_S — ceremony-clamped:
#     clamp(remaining−HANDBACK_TAIL_RESERVE_S, FLOOR=180, 300); a
#     SUB-BUDGET of the ceremony, not an addition to it) /
#   livez 180 / readyz 120 / version ~5 / soak ≤120 (adaptive,
#   ceremony-coupled) / commit ~30 ⇒ all-worst ≈1340s > 900s (the
#   1400s all-budgets-max line above stands). Typical healthy run
#   ≈330s. Do NOT re-derive a "fits inside 900s" schedule from this
#   table — it is a bound table; a combined-slow ceremony halts at a
#   downstream boundary with the daemon up (disclosed, OQ-P1-1).
PROMOTE_TOTAL_DEADLINE_S="$(_numeric_knob PROMOTE_TOTAL_DEADLINE_S "${PROMOTE_TOTAL_DEADLINE_S:-}" 900)"

# Subprocess-wait budgets (2.2b, upgrade-resilience 2026-10-07). Rationale
# (cite: §9 manual-push reproduction + incident ④ 2026-10-07 forensics):
# the ceremony has multiple sites where a hung subprocess (systemctl/dbus
# call against a dead or wedged unit) could stall recovery forever — a
# pre-flip freeze at the unbounded `systemctl stop` (stop-ensemble.sh:530,
# incident ④) and a post-flip freeze at the unbounded `systemctl start`
# in the unit hand-back (lib.sh:2751, §9 reproduction). Bounding these
# waits means the ceremony PROGRESSES through the bounded timeout —
# journal event + SIGTERM→SIGKILL the child + fall through to the next
# recovery site (re-verify loop, nohup fallback, caller halt) — instead
# of hanging in pipe_read for ~10+ min and wedging the parent. Generous
# defaults so the happy path NEVER trips them; env-overridable for
# sandbox drills and tests. One event kind `subprocess_wait_timeout`
# journals every site for forensic correlation.
STOP_SCRIPT_BUDGET_S="$(_numeric_knob STOP_SCRIPT_BUDGET_S "${STOP_SCRIPT_BUDGET_S:-}" 120)"     # placeholder; effective bound derived at call site (see _derive_parent_stop_budget)
HANDBACK_START_BUDGET_S="$(_numeric_knob HANDBACK_START_BUDGET_S "${HANDBACK_START_BUDGET_S:-}" 60)"  # unit hand-back start (the §9 culprit)
# D-P1-1..D-P1-2 (promote-handback-halt Phase 1, 2026-11-02) — the
# hand-back VERIFY budget family. The verifier `_supervision_unit_serving`
# previously BORROWED LIVEZ_BUDGET_S (180) — an unrelated knob governing
# the /livez health GATE (gate_livez, lib.sh:5986) — while the flipped
# unit takes ~3 min from unit-active to serving (MCP warmup +
# auto-continue resume; incident r-20261101-212542-8279 missed by
# seconds at 180s; the v0.28.3 ceremony confirmed at 175/180s). The
# quartet replaces that borrow:
#   HANDBACK_VERIFY_BUDGET_S  300  CEILING — the most the verify may wait
#                                     (~1.6x the measured ~185s worst
#                                     boot — same headroom shape the
#                                     house uses for LIVEZ_BUDGET_S)
#   HANDBACK_VERIFY_FLOOR_S   180  FLOOR — the least it may ever be
#                                     given (today's effective budget;
#                                     makes the change monotone-safe —
#                                     never LESS time than before)
#   HANDBACK_VERIFY_GRACE_S    60  ONE progress-gated extension at the
#                                     first expiry (new MainPID ∧ bound
#                                     port ⇒ the daemon is demonstrably
#                                     coming up; no progress ⇒ no
#                                     extension — D-P1-6)
#   HANDBACK_TAIL_RESERVE_S  120  Downstream room (livez+readyz+version+
#                                     soak+commit) reserved when the
#                                     ceremony clock is known.
# Effective budget: CEILING when no ceremony clock is known (restart.sh /
# rollback.sh); else clamp(remaining − TAIL_RESERVE, FLOOR, CEILING) —
# the same deadline-coupling shape as the adaptive soak (D-P3.5). Grace:
# min(GRACE, max(0, remaining − effective)) when the clock is known
# (AM-P1-5); the floor-wins bands carry the DISTINCT "ceremony time
# exhausted" verdict. All four env-overridable via _numeric_knob
# (garbage ⇒ documented default, loud stderr). Phase-2 coupling
# (R-P1-15): UPGRADE_CLOSER_BOOT_GRACE_S (daemon-side) must stay ≥
# HANDBACK_VERIFY_BUDGET_S — both windows cover the same measured boot.
HANDBACK_VERIFY_BUDGET_S="$(_numeric_knob HANDBACK_VERIFY_BUDGET_S "${HANDBACK_VERIFY_BUDGET_S:-}" 300)"
# F4 (fix-cycle, code-council W2): EXPORT the knob. The daemon-side closer
# floors its boot-grace at max(UPGRADE_CLOSER_BOOT_GRACE_S, this value) and
# reads it from its environment; the transport is (a) here — every child of
# a lib.sh-sourcing context inherits the RESOLVED value — and (b) the
# launcher's .env export seam (launcher.sh exports every .env key and the
# daemon is the launcher's child). Exporting does not change the shell's own
# reads (the variable is read by name; export only marks it for children).
export HANDBACK_VERIFY_BUDGET_S
HANDBACK_VERIFY_FLOOR_S="$(_numeric_knob HANDBACK_VERIFY_FLOOR_S "${HANDBACK_VERIFY_FLOOR_S:-}" 180)"
HANDBACK_VERIFY_GRACE_S="$(_numeric_knob HANDBACK_VERIFY_GRACE_S "${HANDBACK_VERIFY_GRACE_S:-}" 60)"
HANDBACK_TAIL_RESERVE_S="$(_numeric_knob HANDBACK_TAIL_RESERVE_S "${HANDBACK_TAIL_RESERVE_S:-}" 120)"
# D6-v (self-service-promote Wave 2, 2026-11-19) — MEASURED-CEILING cap
# for the verify budget's journal-sample mechanism. MECHANISM ONLY: the
# DEFAULT ceiling stays HANDBACK_VERIFY_BUDGET_S=300; when the journal
# history carries a serving_green_commit/commit row with a
# verify_elapsed_s sample, the ceiling becomes
# min(clamp(sample, FLOOR, MEASURED_CEILING), 780) — the inflation
# itself waits for the wedge commission (design §7: "no budget
# inflation"; §1.2: no in-ceremony budget covers a 9.5–11 min boot).
HANDBACK_VERIFY_MEASURED_CEILING_S="$(_numeric_knob HANDBACK_VERIFY_MEASURED_CEILING_S "${HANDBACK_VERIFY_MEASURED_CEILING_S:-}" 600)"
SYSTEMCTL_PROBE_BUDGET_S="$(_numeric_knob SYSTEMCTL_PROBE_BUDGET_S "${SYSTEMCTL_PROBE_BUDGET_S:-}" 5)"  # reset-failed / is-active probes
SCOPE_START_BUDGET_S="$(_numeric_knob SCOPE_START_BUDGET_S "${SCOPE_START_BUDGET_S:-}" 10)"      # scope-arm/opt-in launcher start
# D1 (promote-preflight-hang) — per-tree cap for the stage manifest checksum
# walks. DERIVED, not invented (wave-2 re-measurement — the wave-1 numbers
# were lost with its /tmp sandbox; this host, read-only over the worktree,
# ambient load ~2 cores, `preflight_shasum_batch_parity_test.sh --full`):
# the BATCHED walk is 2.0 s for the 4,930-file plugins tree (303.8 s before
# batching), 0.28 s agents (338 files), 0.33 s frontend (567 files); output
# byte-parity pinned by the same pack. Budget = 60 s = ~30x the worst
# measured batched walk — headroom for a cold cache or a slower host —
# while staying ~150x BELOW the 304 s per-file pass, so a batching REGRESSION
# (fallback to per-file hashing) trips this LOUDLY instead of reproducing
# the 2026-10-28 09:51/09:54 stage-SIGTERM shape (unbounded, unlogged CPU in
# an early phase). Env-overridable. Re-measure with:
#   nohup bash tests/packs/preflight_shasum_batch_parity_test.sh --full &
STAGE_TREE_BUDGET_S="$(_numeric_knob STAGE_TREE_BUDGET_S "${STAGE_TREE_BUDGET_S:-}" 60)"
# D2 (promote-preflight-hang, phase 2) — the preflight watchdog budgets.
# Both are env-overridable; the value in force is NAMED in the breach row
# when a budget trips (T2.1/AC1 — `_budget_in_force` is the resolver).
#
# PREFLIGHT_STEP_BUDGET_S=120 — DERIVED (D2-15/architect RULING 6b): ≈ 2.3x
# the longest single measured preflight step. `integrity_verify` ≈ 26 s/call
# on the release payload shape (agents+frontend, ~431 files; the pre-D1
# per-file walk — evidence/CODE-PATHS.md §12: 52-60 s of the 68 s healthy
# preflight across 2 calls). Post-D1 both calls ride the batched walk
# (~0.3 s/tree), so 120 s is generous belt headroom; it exists to catch the
# NEXT unknown slow site (the 2026-10-28 hang was ONE step — a checkpoint
# before/after the site still hung 37 minutes; only a per-step bound fires
# inside a step).
#
# PREFLIGHT_PHASE_BUDGET_S=300 — ≈ 4.4x the measured healthy preflight
# (68 s wall / 22 s CPU, sandbox replay at 25 KB journal), and deliberately
# BELOW the daemon's 660 s benign-detach observation
# (upgrade_journal_sweep.py DEFAULT_REAPER_TIMEOUT_SECONDS): the shell's
# loud failure beats the daemon's silent observation.
#
# ARMING SCOPE (D2-11/T2.19): these budgets are armed around the promote
# PREFLIGHT window ONLY (promote.sh:225-373 region) and the stage tree
# walks (STAGE_TREE_BUDGET_S). They are NEVER armed around gate_soak
# (lib.sh gate_soak; reachable only from promote.sh's gate section, outside
# the wrapped window) — a watchdog that covers the soak would kill a
# legitimate 300 s conservative soak (R1). The rollback/restart/status
# scripts hold no pipeline lock on their journal reads and get the
# UNLOCKED breach behaviour instead (D2-6/SC4b: warn + per-run log, no
# journal row; status.sh exits 1).
PREFLIGHT_STEP_BUDGET_S="$(_numeric_knob PREFLIGHT_STEP_BUDGET_S "${PREFLIGHT_STEP_BUDGET_S:-}" 120)"
PREFLIGHT_PHASE_BUDGET_S="$(_numeric_knob PREFLIGHT_PHASE_BUDGET_S "${PREFLIGHT_PHASE_BUDGET_S:-}" 300)"
# Post-flip soak (ADR-005 gate: 300s). Overridable for sandbox drills only
# (ENSEMBLE_PROMOTE_SOAK_S); production default stays 300.
SOAK_S_DEFAULT=300

# P3 / decision (c) — adaptive soak (D-P3.1). The default-mode soak is
# adaptive (60s floor, 120s cap, 3 consecutive greens at 15s cadence,
# fail-fast on red). Operator can revert to the v0.18-era flat 300s
# behavior via ENSEMBLE_SOAK_CONSERVATIVE=1 (D-P3.2). The conservative
# path stays bit-identical to the pre-P3 behavior (soak_s=${SOAK_S}
# or 300, sleep 30, no early exit). The total ceremony budget is
# governed by PROMOTE_TOTAL_DEADLINE_S=900 at the top of promote.sh
# (decision (d)); the soak phase gets its remaining budget from the
# per-phase decomposition. C-1 corrected semantics: cap-reach with
# consecutive_green < 3 → exit 1 → auto-rollback (the never-green
# commit bug under sustained DB churn). Floor 60s is wall-clock
# (3 greens at 15s cadence ≈ 45s, so the floor holds the window
# long enough to clear one transient blip + one curl timeout).
ADAPTIVE_SOAK_FLOOR_S="${ADAPTIVE_SOAK_FLOOR_S:-60}"
ADAPTIVE_SOAK_CAP_S="${ADAPTIVE_SOAK_CAP_S:-120}"
ADAPTIVE_SOAK_CONSECUTIVE="${ADAPTIVE_SOAK_CONSECUTIVE:-3}"
ADAPTIVE_SOAK_CADENCE_S="${ADAPTIVE_SOAK_CADENCE_S:-15}"

# ── A1/A2 grace semantics (soak-gate-mcp-warmup) ────────────────────────
# The class-gated readiness grace (A1) and the soak's three observation
# kinds (A2). The NORMATIVE predicate definitions live in
# .agents/shared/planning/soak-gate-mcp-warmup/requirements.md §4.1-§4.3
# (the single normative home); these are the SHELL-side constants only.
#
# ENV-OVERRIDABLE BY DESIGN (4-tier config ladder); requirements.md §4.6 = DEFAULTS.
# GRACE_HORIZON_S — the daemon-side grace horizon (requirements §4.3 P3):
# ONE number, OBSERVATION-RELATIVE (the daemon owns the clock; the
# shell never keeps a second one). DOCUMENTATION-ONLY MIRROR: NO shell
# code consumes it — the soak's window-extension bound is built from
# ADAPTIVE_SOAK_CAP_S + GRACE_EXTENSION_CAP_S_INT (see _soak_effective_cap
# and gate_soak). The constant is kept so a reader sees the daemon-side
# figure the gate's bounds are designed against.
GRACE_HORIZON_S="${GRACE_HORIZON_S:-120.0}"
# GRACE_EXTENSION_CAP_S — the BOUNDED window extension the soak may
# spend when a grace-eligible HOLD was observed (requirements §4.3 P3).
# It is SPENT, NOT REFUNDED: a re-arm does not hand back extension
# budget. The soak's ABSOLUTE bound is
#     soak_entry + ADAPTIVE_SOAK_CAP_S (120) + GRACE_EXTENSION_CAP_S (180)
#   = soak_entry + 300s.
GRACE_EXTENSION_CAP_S="${GRACE_EXTENSION_CAP_S:-180.0}"
# The INTEGER projection of GRACE_EXTENSION_CAP_S — the shell's
# arithmetic is integer-only ($(( ))) and the normative value (180 s) is
# a whole number of seconds. "%%.*" strips a fractional part when there
# is one and is a no-op when there is not.
GRACE_EXTENSION_CAP_S_INT="${GRACE_EXTENSION_CAP_S%%.*}"
# MAX_HOLDS_PER_GREEN — the bound on consecutive HOLD observations
# (requirements §4.2 P2). A green counts only behind AT MOST this many
# consecutive holds; a green behind more is REJECTED (the consecutive
# streak resets) — a never-green daemon still ends at cap-reach with
# consecutive_green < 3 and is rolled back.
MAX_HOLDS_PER_GREEN="${MAX_HOLDS_PER_GREEN:-2}"

# P3 / decision (e) — boot-window grace (D-P3.7). Grace period starts
# on restart_via_launcher returning 0; ends on earliest of (a) 60s
# elapsed OR (b) 3 consecutive non-transient greens on livez. During
# grace: transient probe classifications are silently ignored; green
# is counted; red is counted as red (NOT blanket-suppressed — only
# suppress transients). Operator override: PROMOTE_BOOT_GRACE_S.
BOOT_GRACE_S="${BOOT_GRACE_S:-60}"
PROMOTE_BOOT_GRACE_S="${PROMOTE_BOOT_GRACE_S:-${BOOT_GRACE_S}}"

# P3 / decision (e) — busy-amnesty (D-P3.8, C-8b). Extend readyz's
# inflight_turns > 0 semantic to livez + soak via a SHELL-SIDE READER
# ONLY (NO new /run/ensemble/inflight_turns file; NO new daemon-side
# counter writer). The reader pulls the EXISTING
# readiness.py:198/222 detail.inflight_turns field via a readyz curl.
# Bound: inflight_turns ≤ BUSY_INFLIGHT_BOUND. 0 or >bound → no
# amnesty. The bound is BOUNDED — a wedged daemon (inflight_turns=0
# but pool saturated) does NOT get amnesty.
BUSY_INFLIGHT_BOUND="${BUSY_INFLIGHT_BOUND:-5}"

# Rollback window / anti-flapping (ADR-005 D2, APPROVED).
ROLLBACK_CAP_24H=3            # max rollbacks per 24h window (entry-side)
COOLDOWN_S=600                # 10 min cooldown after an auto-rollback
SWEEP_STALE_S=600             # journal in_flight older than this = orphaned

# Lock protocol (D5/D-FA5.1).
LOCK_HEARTBEAT_S=30           # live owner rewrites heartbeat this often
LOCK_STALE_S=300              # heartbeat older than this = stale-breakable
LOCK_WAIT_S_DEFAULT=15        # bounded wait for a busy lock, never forever

# Fix #6 (2026-11-14 incident §5.5): every stale-break LEAVES its sibling
# behind by protocol (mv to rollback.lock.d.stale.<pid>, never rmdir —
# racy) and nothing ever removed them — `.stale.1099` / `.stale.92146`
# accumulated on the live install. Age-bounded prune knob: a sibling
# older than this is reaped at acquire time. 7d conservative default (a
# promote/rollback runtime is bounded far below it, so nothing reaped can
# belong to a live pipeline); env-overridable for tests/rescue. Garbage
# values fall back to the default via _numeric_knob (a garbage bound that
# read as 0 would reap FRESH siblings — the inverse failure).
ROLLBACK_LOCK_STALE_PRUNE_S="$(_numeric_knob ROLLBACK_LOCK_STALE_PRUNE_S "${ROLLBACK_LOCK_STALE_PRUNE_S:-}" 604800)"

# HEARTBEAT_STALE_S mirrors LOCK_STALE_S; the two are refreshed
# together at every lock_heartbeat call site but kept as separate
# constants so the lock vs journal paths age independently.
#
# D2a / pass-A follow-up (WS9a, 2026-11-11): HEARTBEAT_STALE_S is ALSO the
# journal-txn liveness threshold consumed by the comp3 reclaim predicates
# (`_txn_heartbeat_stale` + the launcher's `_journal_sweep`) and it MUST equal
# the PYTHON side's `daemon.tools.upgrade_journal.TXN_HEARTBEAT_STALE_S` (the
# PREDICATE-DIVERGENCE NOTE (tidier round 3; deliberate — do NOT "fix"):
# the LAUNCHER'S BOOT RECLAIM (launcher.sh:2889 owner-dead ALONE, :3345 the
# bounded comp3-owner-dead confirm) reclaims on DEAD-ALONE; the TOOL/SWEEP
# mechanism below requires STALE AND DEAD. At boot a dead owner is
# unambiguous (no process can refresh a dead pid's heartbeat; the boot won
# the singleton race); in the tool/sweep lane a DEAD-ALONE rule would reopen
# the double-promote window (an in-flight executor whose pid lookup glitched
# would become reclaim-able mid-promote). One mechanism PER LANE — not
# byte-identical predicates.
# single mechanism `txn_is_reclaimable` owns). The two literals are pinned to
# agree by tests/unit/tools/test_upgrade_journal.py::test_shell_heartbeat_
# stale_matches_python — do not change one without the other.
HEARTBEAT_STALE_S=300

# Retention (ADR-004): keep 3 releases; previous pinned.
RETENTION_KEEP=3

# D3 (promote-preflight-hang phase 3) -- LOGGING THAT SURVIVES ANYTHING.
#
# When ENSEMBLE_UPGRADE_LOG_FILE is set (the daemon threads the per-run
# NARRATIVE path into the executor env; D3-12's two-file sink), the
# shell's own lines are appended DIRECTLY to that file -- timestamped --
# and stdout/stderr become a best-effort mirror that is SKIPPED on a
# successful file write (no duplication: systemd's `append:` capture
# feeds the separate PAYLOAD file, not this one). When the variable is
# UNSET (manual/legacy runs), the behaviour is BYTE-IDENTICAL to the
# pre-D3 shape (AC1).
#
# Write failures are REPORTED (D3-5/O5): the append goes through
# _log_tsl (the single writer -- D3-14/AC15); on failure the line goes to
# stderr WITHOUT the blanket 2>/dev/null and ONE best-effort journal row
# (marker logwrite_failed) is attempted per run, so a full disk / a
# read-only log surfaces instead of swallowing the narrative.
_LOG_WRITE_WARNED=0
_log_write_failed() {
    # stderr, UNREDIRECTED (a broken transport must be visible)
    printf '%s\n' "$1" >&2
    if [ "${_LOG_WRITE_WARNED:-0}" = "0" ]; then
        _LOG_WRITE_WARNED=1
        # journald visibility (AC2/AC13): the unwritable-file fallback is
        # recorded with a distinct marker; best-effort, never fatal (the
        # journal append itself routes through _warn on its own failures --
        # the flag above stops that recursion).
        journal_history_append warn "logwrite_failed: per-run narrative log write FAILED (path=${ENSEMBLE_UPGRADE_LOG_FILE:-?}) -- lines fall back to stderr only" >/dev/null 2>&1 || true
    fi
}
_log()  {
    if [ -n "${ENSEMBLE_UPGRADE_LOG_FILE:-}" ]; then
        if _log_tsl "$ENSEMBLE_UPGRADE_LOG_FILE" "${LOG_TAG}[${UP_TARGET:-lib}]: $*"; then
            return 0
        fi
        _log_write_failed "${LOG_TAG}[${UP_TARGET:-lib}]: $*"
        return 0
    fi
    printf '%s[%s]: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "$*" 2>/dev/null
}
# _logv -- verbatim body writer (JSON payloads etc.). D3-11 (phase-3 fold
# A21): the _logv call sites (promote.sh livez/readyz bodies, rollback,
# restart) are UNBOUNDED today (1,258 B measured; a degraded readyz grows)
# -- so with the narrative sink set they ride the SAME writer (timestamp,
# 4 KiB cap, embedded-newline sanitization); without it the legacy
# byte-identical stdout write stands.
_logv() {
    if [ -n "${ENSEMBLE_UPGRADE_LOG_FILE:-}" ]; then
        if _log_tsl "$ENSEMBLE_UPGRADE_LOG_FILE" "$*"; then
            return 0
        fi
        _log_write_failed "$*"
        return 0
    fi
    printf '%s\n' "$*" 2>/dev/null
}                # verbatim (JSON etc.)
_warn() {
    if [ -n "${ENSEMBLE_UPGRADE_LOG_FILE:-}" ]; then
        if _log_tsl "$ENSEMBLE_UPGRADE_LOG_FILE" "${LOG_TAG}[${UP_TARGET:-lib}]: WARN: $*"; then
            return 0
        fi
        _log_write_failed "${LOG_TAG}[${UP_TARGET:-lib}]: WARN: $*"
        return 0
    fi
    printf '%s[%s]: WARN: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "$*" >&2 2>/dev/null
}

# _now_epoch — seconds since epoch (BSD-safe).
_now_epoch() { date +%s; }

# _now_iso — UTC ISO-8601 timestamp (journal fields are ISO).
_now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# _cooldown_until_iso — the `cooldown_until` stamp: NOW + COOLDOWN_S seconds,
# in the journal's ISO-8601 Z form ("%Y-%m-%dT%H:%M:%SZ").
#
# PORTABLE (the same class as the launcher's `_ts_off` fix — its D4
# defect: BSD `date` REJECTS an UNSIGNED positive adjustment (`-v300S`:
# "Cannot apply date adjustment") and accepts the SIGNED form (`-v+300S`);
# a negative offset already carries its own `-`). The two date families
# are NOT interchangeable:
#   BSD/macOS:  ``date -ju -v+${COOLDOWN_S}S -f '%Y-%m-%dT%H:%M:%SZ' <now>``
#   GNU/Linux:  ``date -u -d "+${COOLDOWN_S} seconds"``
# The GNU form FAILS on BSD (`-d` is not a BSD date flag) and the BSD form
# FAILS on GNU (`-j` rejected, `-v` unknown, `-f` takes a different format
# ordering) ⇒ the family MUST be SELECTED, never chained (the `_iso_to_epoch`
# dispatch above is the same precedent). Chaining them with `||` is what
# turns "the wrong family ran" into "the stamp silently became NOW" — a
# DISARMED anti-flapping window (the incident shape).
#
# SELECTION IS OBSERVABLE (test-only): `_COOLDOWN_DATE_UNAME` overrides the
# `uname -s` dispatch, so a stubbed `date` can exercise the GNU arm on a
# BSD host and vice versa. Production behaviour is unchanged: the default
# is `uname -s`.
#
# FAILURE: a date error on EITHER family (or an unrecognized platform)
# falls back to "now" — the historical behaviour — and says so on stderr.
# A silently disarmed anti-flapping window is the shape this fallback used
# to hide; the stamp itself is unchanged by this refactor.
_cooldown_until_iso() {
    local until=""
    case "${_COOLDOWN_DATE_UNAME:-$(uname -s)}" in
        Darwin|*BSD*|*bsd*)
            if [ "${COOLDOWN_S#-}" != "$COOLDOWN_S" ]; then
                # a NEGATIVE offset keeps its own sign (BSD accepts
                # `-v-300S`; the SIGNED positive form is `-v+300S`).
                until="$(date -ju -v${COOLDOWN_S}S -f '%Y-%m-%dT%H:%M:%SZ' "$(_now_iso)" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" || until=""
            else
                until="$(date -ju -v+${COOLDOWN_S}S -f '%Y-%m-%dT%H:%M:%SZ' "$(_now_iso)" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" || until=""
            fi
            ;;
        Linux|GNU*|*GNU*)
            until="$(date -u -d "+${COOLDOWN_S} seconds" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" || until=""
            ;;
        *)
            echo "_cooldown_until_iso: unrecognized platform '$(uname -s)' — refusing (fail-closed; BSD or Linux required)" >&2
            until=""
            ;;
    esac
    if [ -z "$until" ]; then
        echo "_cooldown_until_iso: the cooldown stamp is UNAVAILABLE on this host — falling back to NOW (the anti-flapping window is DISARMED)" >&2
        until="$(_now_iso)"
    fi
    printf '%s' "$until"
}

# _enforce_deadline <deadline_epoch> <phase_name>
# P3 / decision (d) — top-level wall-clock ceiling enforcement at
# every phase boundary. On breach:
#   - journal_history_append halt "<deadline-breach-<phase>> ... daemon-state=<live|stopped|handed-back>" (C-10c + C-10b)
#   - lock_release (caller-held lock released)
#   - exit 78 (the standard refusal code; reuses the existing halt
#     machinery at promote.sh:459-496)
# The breach does NOT trigger auto-rollback (the existing
# auto-rollback is for gate-fail halts, not for ceremony timing
# halts). A post-stop breach leaves the daemon DOWN — the runbook
# first-recovery-step is to verify/restart current BEFORE re-promote.
#
# Returns 0 if not breached (caller continues). Exits 78 on breach
# (caller NEVER sees the return path; the function terminates
# the script with the standard halt shape).
#
# daemon-state derivation (C-10b):
#   - "stopped" if past the stop-ensemble phase
#   - "handed-back" if past the restart_via_launcher phase
#   - "live" otherwise (pre-stop, pre-flip, or between flip and restart)
#   The phase name is a coarse hint — the actual state at halt time
# is the consumer's job to interpret. The journal field is the
# single source of truth (runbook step 1 + post-mortem correlation).
_enforce_deadline() {
    local deadline="$1" phase="$2" budget_extra="${3:-}"
    local now elapsed daemon_state="live"
    now="$(_now_epoch)"
    if [ "$now" -le "$deadline" ]; then
        return 0
    fi
    elapsed=$(( now - deadline ))
    # C-10b: derive daemon-state from the phase name. The mapping
    # is conservative — the journal event is the audit truth, the
    # runbook first-recovery-step reads it.
    # Order matters: check "post-restart" phases FIRST so "restart"
    # itself classifies as "handed-back" (the daemon was handed
    # back to the unit/no-hup just before this phase; if the
    # breach fires here the daemon is alive but we timed out on
    # the gate). "stop"/"launcher"/"flip" come AFTER and are
    # "stopped" (the daemon is DOWN).
    case "$phase" in
        livez|readyz|version|soak|commit|rollback|restart) daemon_state="handed-back" ;;
        *stop*|*launcher*|*flip*) daemon_state="stopped" ;;
        *) daemon_state="live" ;;
    esac
    # W3 (phase-2 fold): the optional third argument carries the budget
    # identity (e.g. "budget=PREFLIGHT_PHASE_BUDGET_S value=300") so BOTH
    # breach producers — this L1 checkpoint and the L2a `_budget_breach`
    # below — emit daemon-state= in one readable row shape. Callers that
    # pass two arguments (every pre-existing site) produce BYTE-IDENTICAL
    # rows to the pre-D2 shape.
    if [ -n "$budget_extra" ]; then
        journal_history_append halt "deadline-breach-${phase} (elapsed=${elapsed}s past ${deadline}) ${budget_extra} daemon-state=${daemon_state}" \
                              || true   # history is advisory; the breach is already decided
    else
        journal_history_append halt "deadline-breach-${phase} (elapsed=${elapsed}s past ${deadline}) daemon-state=${daemon_state}" \
                              || true   # history is advisory; the breach is already decided
    fi
    _warn "CEREMONY DEADLINE BREACH at phase=${phase}: elapsed=${elapsed}s past deadline=${deadline} (daemon-state=${daemon_state}) — halt-for-human (C-10b/C-10c)"
    lock_release 2>/dev/null || true
    exit 78
}

# _budget_in_force <NAME> — single resolver for the D2 watchdog budgets
# (T2.1/AC1). Prints the value in force (env override or documented
# default) so the breach row can NAME it; refusing loudly on an unknown
# name keeps the resolver honest (a typo'd budget in a breach row would
# silently misreport the value).
#
# A5 (code council, 2026-10-30): the values printed here are the RESOLVED
# globals — every numeric knob already passed through `_numeric_knob` at
# source time, so a garbage override can neither reach a breach row nor
# reach `$(( ))`/`sleep`. The defaults are NOT re-declared here (one
# source of truth per budget).
_budget_in_force() {
    case "$1" in
        PREFLIGHT_STEP_BUDGET_S)   printf '%s' "$PREFLIGHT_STEP_BUDGET_S" ;;
        PREFLIGHT_PHASE_BUDGET_S)  printf '%s' "$PREFLIGHT_PHASE_BUDGET_S" ;;
        STAGE_TREE_BUDGET_S)       printf '%s' "$STAGE_TREE_BUDGET_S" ;;
        SYSTEMCTL_PROBE_BUDGET_S)  printf '%s' "$SYSTEMCTL_PROBE_BUDGET_S" ;;
        JOURNAL_LEGACY_READ_BUDGET_S) printf '%s' "$JOURNAL_LEGACY_READ_BUDGET_S" ;;
        # D-P1-10 (promote-handback-halt Phase 1) — the hand-back verify
        # budget family, so any future breach row can NAME the value in
        # force (unknown names still fail loudly below).
        HANDBACK_VERIFY_BUDGET_S)  printf '%s' "$HANDBACK_VERIFY_BUDGET_S" ;;
        HANDBACK_VERIFY_FLOOR_S)   printf '%s' "$HANDBACK_VERIFY_FLOOR_S" ;;
        HANDBACK_VERIFY_GRACE_S)   printf '%s' "$HANDBACK_VERIFY_GRACE_S" ;;
        HANDBACK_TAIL_RESERVE_S)   printf '%s' "$HANDBACK_TAIL_RESERVE_S" ;;
        *) return 1 ;;
    esac
}

# _budget_breach <budget_name> <step> <phase> <elapsed_s> — the L2a breach
# handler (T2.2/D2-5/D2-12/W3). ORDER IS LOAD-BEARING and pinned
# (tests/packs/preflight_watchdog_test.sh): journal row FIRST, then the
# stderr _warn, then lock_release, then exit 78. Release-first would lose
# the row on a live install: journal_splice.py's live-install fence
# (`_pipeline_lock_held`, journal_splice.py:553) refuses history appends
# while the pipeline lock is NOT held by a live owner — releasing the lock
# before journalling makes the append fail exactly when it matters most.
# The row keeps the house vocabulary: kind `halt`, detail prefixed
# `deadline-breach-<step>` (same family as _enforce_deadline's
# `deadline-breach-<phase>`), carrying ALL FIVE budget fields (T2.9/AC9:
# budget name, value in force, step, elapsed, phase) PLUS daemon-state=
# (W3 — one row shape for both producers; the runbook's first-recovery-step
# reads the field from either). Two breach classes stay distinct (D2-12):
# (i) this shell-watchdog breach = exit 78 + halt row WITH the
# `deadline-breach-` prefix; (ii) an L3 systemd RuntimeMaxSec kill =
# exit 143 + halt row WITHOUT the prefix (the TERM trap's
# "killed by SIGTERM" row). Terminal classification is by row kind, never
# by exit code alone.
#
# LOCKED-PATH ONLY: callers hold the pipeline lock (the promote preflight
# and the supervision probes all run under it). The unlocked scripts
# (status.sh / the unlocked journal reads in rollback.sh / restart.sh) use
# their own failure handling — they must NOT call this function (their
# breach rows would be fence-refused on a live install; D2-6/SC4b).
_budget_breach() {
    local budget="$1" step="$2" phase="$3" elapsed="$4"
    local value daemon_state="live"
    value="$(_budget_in_force "$budget" 2>/dev/null)" || value="unknown"
    # The steps this handler serves are preflight/pre-stop ⇒ daemon live
    # (same conservative mapping as _enforce_deadline's default arm).
    journal_history_append halt "deadline-breach-${step} (budget=${budget} value=${value} elapsed=${elapsed}s phase=${phase}) daemon-state=${daemon_state}" \
                          || true   # a torn journal must not block the halt; the exit below is the contract
    _warn "PREFLIGHT STEP BUDGET BREACH step=${step}: exceeded budget=${budget} value=${value}s elapsed=${elapsed}s phase=${phase} — halting (deadline-breach-${step}, exit 78; lock released)"
    lock_release 2>/dev/null || true
    exit 78
}

# _run_bounded_step <step-name> <command...> — the L2a per-step wrapper
# (T2.3/D2-14). Runs the step under the house `_run_bounded` primitive
# (BYTE-UNCHANGED; the 7 pre-existing consumers are untouched) with
# PREFLIGHT_STEP_BUDGET_S; on rc 124 (the 137/143→124 collapse inside
# _run_bounded) the breach handler fires LOUDLY (row + warn + lock release
# + exit 78). Any other rc propagates unchanged — healthy steps are
# unaffected.
#
# WRAPPER CONTRACT (D2-14, corrected by W1/code council 2026-10-30): the
# wrapped step MUST be read-only OR death-idempotent BY ITS OWN DESIGN (a
# kill mid-step must leave either no state change or a state the next
# sweep/re-run repairs). Read-only sites (journal reads, entry checks,
# staleness predicate, integrity walks) are the majority; the ONE
# non-read-only site is `adopt_stale_txn` (promote.sh step 1c), whose
# recovery is flip-first precisely so a death mid-sequence is re-run
# idempotently by the launcher sweep on the same stale txn (its own comment
# block says so). The side-effecting ceremonies that are NOT death-
# idempotent (stop script, launcher swap, the post-txn flip) are
# deliberately NOT wrapped — a kill mid-`retention_evict`/stop would leave
# half-mutated state (the MUST-NOT-WRAP rows in phase2-plan §3.3b).
# NOTE: the step runs in a forked subshell (the house primitive backgrounds
# it) — never wrap a step that must set globals in this shell
# (supervision_preflight is covered INSIDE its dualfight probe sites
# instead, via _df_systemctl below).
#
# RC CONTRACT: a step that REFUSES by `exit 78` inside the subshell surfaces
# to the caller as the wrapper's rc 78 — the caller MUST propagate it
# (`exit 78`) and MUST NOT append a row of its own (that row would be
# mislabelled and would double-record one refusal). See promote.sh's
# adopt-txn arm (W1) and the BUDGET_BREACH_RC consumers below.
_run_bounded_step() {
    local step="$1"; shift
    local t0 rc
    t0="$(_now_epoch)"
    _run_bounded "$PREFLIGHT_STEP_BUDGET_S" "$@"
    rc=$?
    if [ "$rc" -eq 124 ]; then
        _budget_breach PREFLIGHT_STEP_BUDGET_S "$step" "${_PIPELINE_PHASE:-preflight}" "$(( $(_now_epoch) - t0 ))"
    fi
    return "$rc"
}

# BUDGET_BREACH_RC — the rc `_budget_breach` exits with, exported to the
# CALLERS that invoke `_run_bounded_step` inside a COMMAND SUBSTITUTION
# (A-4, verifier 2026-10-30). `exit` inside `$( )` ends only the
# substitution's subshell, so such a caller never observes the wrapper's
# 124: it sees THIS rc, while the breach handler has ALREADY written its
# `deadline-breach-<step>` halt row, emitted its WARN and released the lock.
# The caller's contract is therefore: on this rc, propagate the halt
# (`exit "$BUDGET_BREACH_RC"`) and write NO row of its own — a caller-side
# row would be mislabelled (it names the step's own failure vocabulary, e.g.
# the journal size guard) and would double-record a single breach event.
# Keep in lockstep with `_budget_breach` (which pins the literal 78: its
# ORDER/shape is asserted by tests/packs/preflight_watchdog_test.sh).
BUDGET_BREACH_RC=78

# _pipeline_scripts_stamp_verdict <stamp> <floor> <repo_root> — W-2 (verifier
# 2026-10-30): the RELEASE-TIME validity check for the lib.sh
# `PIPELINE_SCRIPTS_REV` stamp, the second half of the phase-6 provenance gate
# (the daemon-side floor is the first half; stage.sh validates both).
#
# WHY IT EXISTS: on a NON-GIT pinned scripts dir the stamp IS the provenance
# (Q2/D6-11), and the daemon's acquisition regex (`_STAMP_RE`) accepts any
# `[0-9A-Za-z._/-]+` — so the pre-release placeholder
# (`unreleased-<lane>`) would arm a ceremony with a meaningless value and no
# git ancestry able to contradict it. Three mechanical conditions:
#   1. a 40-hex commit sha (a placeholder is NOT one);
#   2. that sha RESOLVES to a commit in this tree (same rule as the floor: a
#      release may only name commits it contains);
#   3. when the floor is set, the stamp IS the floor commit — D6-8's "the two
#      travel together" (one merge commit names both the floor the daemon
#      enforces and the revision the scripts tree declares).
# CONTRACT: prints "" / returns 0 when valid; prints the human REFUSAL REASON
# (one line, no trailing newline) / returns 1 when not. It writes nothing and
# exits nothing, so a caller owns the wording prefix and the halt. Extracted
# from stage.sh's inline block so the gate is EXECUTABLE in a pack (the real
# stage.sh cannot be run without a full PyInstaller build) — the pack's
# refusal legs and the mutation matrix drive THIS function.
_pipeline_scripts_stamp_verdict() {
    local stamp="${1:-}" floor="${2:-}" repo="${3:-.}"
    if ! printf '%s' "$stamp" | grep -Eq '^[0-9a-f]{40}$'; then
        printf 'PIPELINE_SCRIPTS_REV in scripts/upgrade/lib.sh is "%s" — not a 40-hex commit sha (W-2/T6.4). The pre-release placeholder MUST be replaced by the release bump with the merge commit introducing the preflight-hang fix (the SAME commit as PIPELINE_SCRIPTS_FLOOR_COMMIT, cadence D6-8). A non-git pinned scripts dir has NO other provenance than this stamp, so a placeholder would arm silently.' "${stamp:-<absent>}"
        return 1
    fi
    if ! git -C "$repo" cat-file -e "${stamp}^{commit}" >/dev/null 2>&1; then
        printf 'PIPELINE_SCRIPTS_REV=%s does not resolve to a commit in this tree (W-2 — the stamp must name a commit this release contains, the same rule as the floor). Fix scripts/upgrade/lib.sh and re-stage.' "$stamp"
        return 1
    fi
    if [ -n "$floor" ] && [ "$stamp" != "$floor" ]; then
        printf 'PIPELINE_SCRIPTS_REV=%s does not equal PIPELINE_SCRIPTS_FLOOR_COMMIT=%s — D6-8 requires the two to travel together (the same merge commit names both the floor the daemon enforces and the revision the scripts tree declares). Align both and re-stage.' "$stamp" "$floor"
        return 1
    fi
    return 0
}

# _pipeline_scripts_floor_verdict <floor> <repo_root> — W-2's DAEMON-side
# twin (T6.15/AC15): the RELEASE-TIME validity check for the
# `PIPELINE_SCRIPTS_FLOOR_COMMIT` constant in daemon/tools/upgrade_tools.py.
# Two mechanical conditions, the same ancestry rule the stamp uses:
#   1. the value is set and looks like a commit sha (the stage-time extract
#      accepts 7..40 lowercase hex chars — an abbreviated sha is a legal
#      `git merge-base` argument, so the check MUST NOT be stricter than
#      the extract or the release would refuse a tree it used to accept);
#   2. the value RESOLVES and IS AN ANCESTOR of the built tree — the floor
#      names a commit the release itself contains, so a daemon built from
#      this tree can never demand a fix commit its own history lacks.
# CONTRACT: prints "" / returns 0 when valid; prints the human REFUSAL
# REASON (one line, no trailing newline) / returns 1 when not. It writes
# nothing and exits nothing, so the caller owns the wording prefix and the
# halt. Extracted from stage.sh's inline block so the gate is EXECUTABLE in
# a pack (the real stage.sh cannot run without a full PyInstaller build) —
# the same discipline as the stamp verdict above (phase3-plan.md P3-D5 /
# P3-PIN-14).
_pipeline_scripts_floor_verdict() {
    local floor="${1:-}" repo="${2:-.}"
    if [ -z "$floor" ]; then
        printf 'PIPELINE_SCRIPTS_FLOOR_COMMIT is unset (or not a commit sha) in daemon/tools/upgrade_tools.py — the daemon-side arm-time stale-scripts guard (phase 6) would refuse every explicitly-pinned ceremony. The release bump MUST set it to the merge commit introducing the ceremony-relevant scripts/upgrade semantics (T6.15/AC15, cadence D6-8). No release ships without a provably-valid floor.'
        return 1
    fi
    if ! printf '%s' "$floor" | grep -Eq '^[0-9a-f]{7,40}$'; then
        printf 'PIPELINE_SCRIPTS_FLOOR_COMMIT="%s" is not a commit sha (T6.15/AC15 — the floor must name a commit this release itself contains).' "$floor"
        return 1
    fi
    if ! git -C "$repo" merge-base --is-ancestor "$floor" HEAD >/dev/null 2>&1; then
        printf 'PIPELINE_SCRIPTS_FLOOR_COMMIT=%s is unresolvable or NOT an ancestor of the built tree %s (T6.15/AC15 — the floor must name a commit this release itself contains; git merge-base --is-ancestor failed). Fix the constant to the merge commit introducing the ceremony-relevant scripts/upgrade semantics and re-stage.' "$floor" "$(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo '?')"
        return 1
    fi
    return 0
}

# _log_phase_marker <step> -- the D3-8/T3.3 mid-phase marker: ONE
# timestamped line naming the phase, the step and the elapsed seconds
# since the preflight armed (_PREFLIGHT_T0). Written through _log, so it
# lands in the per-run NARRATIVE file when ENSEMBLE_UPGRADE_LOG_FILE is
# set (SC7) and on stdout otherwise (manual runs). The markers sit at the
# SAME boundaries as the D2 checkpoints -- one coherent narrative for the
# post-mortem (a watchdog trip and the last marker name the same step).
_log_phase_marker() {
    local step="$1" elapsed=0
    if [ -n "${_PREFLIGHT_T0:-}" ]; then
        elapsed=$(( $(_now_epoch) - _PREFLIGHT_T0 ))
    fi
    _log "phase-marker: phase=${_PIPELINE_PHASE:-?} step=${step} elapsed=${elapsed}s"
}

# _effective_ceremony_remaining_s <deadline_epoch> — returns the
# remaining seconds on the ceremony wall-clock ceiling, clamped at
# zero. Used by gate_soak (D-P3.5) to shrink the adaptive cap
# against the remaining budget. Returns "0" when the deadline has
# passed (never a negative number; callers can compare against
# ADAPTIVE_SOAK_FLOOR_S without underflow).
_effective_ceremony_remaining_s() {
    local deadline="$1" now remaining
    now="$(_now_epoch)"
    remaining=$(( deadline - now ))
    if [ "$remaining" -lt 0 ]; then
        remaining=0
    fi
    printf '%s\n' "$remaining"
}

# _splice_python_floor_probe — bash preflight probe (P2 / C-7 / T2.0a).
# Verifies that `python3` on PATH satisfies the 3.7 floor that
# `journal_splice.py` leans on (`pathlib.samefile` + Python 3.7+ dict
# ordering + json round-trip). Stdlib only — no venv, no `pip install`,
# no `PLUGIN_STALENESS_PYTHON` chain (that chain exists for PyYAML and
# is intentionally NOT used here). Refuses (returns 1) when:
#   - `python3` not on PATH at all
#   - `python3` below 3.7 (assertion inside the helper)
#   - the helper cannot exec (broken interpreter path, signal)
# The helper itself does NOT touch the journal — that's the WRAPPER's
# job after `journal_init`. This function ONLY prints a single stderr
# line on refusal and returns 1; on success returns 0 silently.
_splice_python_floor_probe() {
    local py
    if ! py="$(command -v python3 2>/dev/null)" || [ -z "$py" ]; then
        printf 'splice: REFUSED: python3-floor: python3 not on PATH\n' >&2
        return 1
    fi
    if ! "$py" -c "import sys, json, os, pathlib; assert sys.version_info >= (3, 7)" 2>/dev/null; then
        printf 'splice: REFUSED: python3-floor: %s below 3.7 or stdlib modules unavailable\n' "$py" >&2
        return 1
    fi
    return 0
}

# _log_ts — UTC ISO-8601 timestamp prefix for log lines (comp6, r-f82e).
# BSD/GNU-portable: ``date -u +format`` is IDENTICAL on both — the
# format-string operand uses the SAME syntax in BSD and GNU date, and
# no -j/-f/-d/-j flags appear here. Verified on macOS date(1) and
# GNU coreutils 9.x. Used as the timestamp source for upgrade.log
# lines that previously had no anchor (the incident file
# `/home/nea/ensemble-prod/data/upgrade.log` had 19 lines, none
# timestamped; post-mortems had no time correlation).
_log_ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }

# _log_tsl <file> <message...> — append a TIMESTAMPED line to the
# upgrade.log (or any log file). Test-exercised only — NOT yet wired
# into promote/restart/rollback/stage. Sites that DO need a
# timestamped log line can opt in by calling
# ``_log_tsl "$UPGRADE_LOG" "<message>"`` in place of the
# ``printf … >> "$UPGRADE_LOG"`` pattern that produced unanchored
# output. (See the comp6 paragraph below for the minimal-disruption
# adoption plan.)
#
# BYTE-IDENTICAL FALLBACK: if _log_ts is unavailable (no date binary,
# or PATH stripped in a sandbox), the timestamp degrades to "-" rather
# than aborting the write. The log line still goes through; a
# reviewer who sees "-" can correlate with the journal's ISO
# started_at timestamps (always present, comp4-portable).
# D3-11 ATOMICITY CONTRACT (phase-3 fold A21): ONE log line = one write(2)
# to an O_APPEND fd; lines < 8192 B are atomic against a concurrent
# writer, >= 8192 B corrupt (the break is stdio BUFSIZ). Therefore a hard
# 4 KiB line cap (2x margin): longer payloads emit a header plus
# 'payload elided, N bytes'; embedded newlines are sanitized so one call
# yields ONE physical line. Per-line fsync is REJECTED (page-cache
# durability covers parent/process death -- the stated objective; a host
# crash loses everything else too).
_LOG_LINE_MAX=4000
_log_tsl() {
    local file="$1"; shift
    local ts line orig_n
    ts="$(_log_ts 2>/dev/null)" || ts="-"
    line="$*"
    # one physical line: CR/LF -> spaces
    line="$(printf '%s' "$line" | tr '\n\r' '  ')"
    orig_n=${#line}
    if [ "$orig_n" -gt "$_LOG_LINE_MAX" ]; then
        line="${line:0:_LOG_LINE_MAX} payload elided, ${orig_n} bytes"
    fi
    printf '%s %s\n' "$ts" "$line" >> "$file" 2>/dev/null || return 1
}

# comp6 (r-f82e): the existing _log helper writes to STDOUT (and WARN —
# i.e. _warn — writes to STDERR; the pre-D3 text here had the two streams
# INVERTED, corrected under phase-3 fold R-H) and is fine for the
# operator's console.
# It is NOT a timestamped upgrade.log writer; the upgrade.log
# history that the forensic report needed was the executor's own
# stdout/stderr captured by spawn_executor (data/upgrade.log, append).
# Promotion/restart/rollback scripts append their phase markers to
# that same file via bare echo/printf redirects — those writes are
# the ones this helper replaces.
#
# The MINIMAL-DISRUPTION pattern: add the timestamp wrapper, keep all
# existing printf/echo writes (they still produce lines; only the
# unanchored lines change). Sites that opt into the timestamped
# wrapper call ``_log_tsl "$UPGRADE_LOG" "<message>"`` instead of
# ``printf … >> "$UPGRADE_LOG"``. Sites that don't change → lines
# stay unanchored (preserved BYTE-IDENTICALLY, by design — the
# incident's 19 unanchored lines had THIS shape; only new lines get
# anchored).
# Variable export so per-script helpers know where to append:
# UPGRADE_LOG="$INSTALL_DIR/data/upgrade.log" (set by caller; falls
# back to data/upgrade.log in $INSTALL_DIR if unset).

# _iso_to_epoch <iso-ts> — parse journal ISO timestamps; 0 on garbage
# (callers treat 0 as "unknown age" and fail CLOSED on the decision that
# matters: a sweep never fires on an unparseable fresh-looking txn).
#
# Portability (fix/portable-iso-parse-linux, 2026-09-28, r-f82e §3.9 #1):
# the parser MUST work identically on BSD/macOS and GNU/Linux. Plain
# BSD ``date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s`` is WRONG on Linux —
# the GNU date(1) rejects ``-j`` (BSD date only), ``-f`` requires a
# DIFFERENT format ordering than BSD, and ``-u`` is silently accepted
# but does not parse ISO with the BSD operand order. Symptom: a
# ``lib.sh:86`` line unparseable on this host means the launcher's
# self-heal sweep silently FAILS CLOSED on every boot — an unfixable
# orphan txn wedge.
#   BSD/macOS:  ``date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s``
#   GNU/Linux:  ``date -d "$ts" +%s``   (GNU's -d parses ISO-8601
#                                          directly; -u is implicit for
#                                          the Z suffix and -j/-f are
#                                          not understood)
# We dispatch on ``uname -s`` rather than chaining the two forms with
# ``||``: on BSD a REAL -j failure would otherwise hit the GNU form
# and surface a wrong-platform error. Unrecognized platforms refuse
# (fail-closed; we never guess). Verified on macOS date(1) and GNU
# coreutils 9.x (Linux). MIRRORS the atomic_flip precedent
# (fix/portable-atomic-flip-linux, 47630be0).
_iso_to_epoch() {
    local ts="$1" epoch
    case "$(uname -s)" in
        Darwin|*BSD*|*bsd*)
            epoch="$(date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s 2>/dev/null)" || return 1
            ;;
        Linux|GNU*|*GNU*)
            epoch="$(date -d "$ts" +%s 2>/dev/null)" || return 1
            ;;
        *)
            echo "_iso_to_epoch: unrecognized platform '$(uname -s)' — refusing (fail-closed; BSD or Linux required)" >&2
            return 1
            ;;
    esac
    [ -n "$epoch" ] || return 1
    printf '%s' "$epoch"
}

# _sha256 <file> — BSD shasum.
_sha256() { shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'; }

# _canon_dir <dir> — PHYSICAL path of an existing dir (cd + pwd -P: resolves
# symlinks AND normalizes trailing slashes / dot components). Fallback: the
# raw string when the dir cannot be entered (an unresolvable dir cannot be
# an alias of a live dir that exists; the caller's existence check rejects
# it right after). Bash 3.2/BSD-safe (no readlink -f on stock macOS).
_canon_dir() { (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "$1"; }

# 2026-10-12: install dir deleted twice by agent-authored test code; immediate
# cause (test-script defect) fixed same day by user; this change is part of
# the Layer 1 ownership-separation backstop.
#
# _install_dir_candidates <name> — newline-separated candidate roots for a
# canonical install name (agents-ensemble | agents-ensemble-demo) under the
# CURRENT $HOME, most-preferred first. Layer-1 tolerant — two HOME shapes:
#   legacy       HOME=/home/<user>           → $HOME/<name>
#   post-Layer-1 HOME=<install dir> (dedicated service user whose systemd
#                unit carries Environment=HOME=<install dir>) → $HOME itself,
#                plus the sibling <name> next to it (cross-env resolution,
#                e.g. the flipped live daemon resolving the demo install).
# Under post-Layer-1 the naive $HOME/<name> would WRONGLY NEST — that
# resolution class is exactly what this helper exists to fix. Bash 3.2-safe
# (basename/dirname only, no readlink -f).
_install_dir_candidates() {
    local name="$1" home base
    home="${HOME%/}"
    case "$home" in
        */"$name") printf '%s\n' "$home" ;;
    esac
    printf '%s\n' "$home/$name"
    base="$(basename "$home")"
    case "$base" in
        agents-ensemble|agents-ensemble-demo)
            [ "$base" = "$name" ] || printf '%s\n' "$(dirname "$home")/$name"
            ;;
    esac
}

# _resolve_install_root <name> — pick the first _install_dir_candidates entry
# that actually looks like an install root (.env file or releases/ dir);
# fall back to the legacy $HOME/<name> when none verifies (preserves the
# pre-Layer-1 resolution verbatim when nothing exists).
_resolve_install_root() {
    local name="$1" cand
    while IFS= read -r cand; do
        [ -n "$cand" ] || continue
        if [ -f "$cand/.env" ] || [ -d "$cand/releases" ]; then
            printf '%s' "$cand"
            return 0
        fi
    done < <(_install_dir_candidates "$name")
    printf '%s' "$HOME/$name"
}

# _is_canonical_install_dir <canon-path> — true when the (already-canonical)
# path equals the canonical form of ANY current-shape install candidate for
# EITHER install name. Used by the sandbox refusal guard so a sandbox
# INSTALL_DIR pointing at the real live/demo tree is refused under BOTH
# HOME shapes.
_is_canonical_install_dir() {
    local target="$1" name cand
    for name in agents-ensemble agents-ensemble-demo; do
        while IFS= read -r cand; do
            [ -n "$cand" ] || continue
            [ "$target" = "$(_canon_dir "$cand")" ] && return 0
        done < <(_install_dir_candidates "$name")
    done
    return 1
}

# _json_escape <string> — minimal JSON string escaper (quotes + backslash +
# ALL control chars < 0x20 plus DEL 0x7F as standard \u00XX escapes). Bash
# 3.2-safe, builtin-only (printf -v — no per-char subshell forks; reasons
# are short and stage.sh maps stay fast).
# N4 (P2.2 fix pass 2026-08-23): the old version only handled \n \t \r —
# any other raw control char passed through verbatim; the first N4 cut
# then escaped by `-lt 32` alone, but bash 3.2 printf '%d' yields SIGNED
# bytes, so chars >= 0x80 (é, curly quotes, CJK — routine in LLM-authored
# --reason) went negative, passed the guard, and rendered \uffffff… (>4
# hex digits). Both failures share the nastiest property: lenient readers
# (python/jq, this file's own extractor) ACCEPT the mangled form — the
# damage is SILENT TEXT CORRUPTION, not a parse failure, so nothing fails
# loudly. Now: 0 <= code < 0x20, plus DEL (0x7F — NIT-D, P2.3 B3.5: the
# one control char >= 0x20 the tidy ledger wanted escaped for parity with
# the < 0x20 family), escapes as \u00XX; negative (high UTF-8 byte)
# passes through raw — valid UTF-8 JSON. NUL cannot occur in bash
# strings/argv.
_json_escape() {
    local s="$1" out="" ch i code
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    s="${s//$'\r'/\\r}"
    for ((i = 0; i < ${#s}; i++)); do
        ch="${s:i:1}"
        printf -v code '%d' "'$ch"
        if [ "$code" -ge 0 ] && { [ "$code" -lt 32 ] || [ "$code" -eq 127 ]; }; then
            printf -v ch '\\u%04x' "$code"
        fi
        out+="$ch"
    done
    printf '%s' "$out"
}

# _json_field <json> <key> — crude top-level "key": "value" / number / bool
# extractor for FLAT json objects written by this pipeline. Handles nested
# objects only via _json_sub (below). Not a general parser: the journal is
# ours, and its shape is the D4 contract.
_json_field() {
    local json="$1" key="$2" rest
    rest="${json#*"${key}"}"
    [ "$rest" = "$json" ] && return 1
    rest="${rest#*:}"
    # trim leading whitespace, then dispatch on type
    rest="${rest#"${rest%%[![:space:]]*}"}"
    if [ "${rest:0:1}" = "\"" ]; then
        rest="${rest:1}"                 # opening quote
        rest="${rest%%\"*}"              # up to the closing quote
    else
        rest="${rest%%\,*}"              # number / bool / null
        rest="${rest%%\}*}"
    fi
    # trim whitespace
    rest="${rest#"${rest%%[![:space:]]*}"}"
    rest="${rest%"${rest##*[![:space:]]}"}"
    printf '%s' "$rest"
}

# _json_field_quoted <json> <qkey> — like _json_field, but anchored on the
# FULL JSON key form `"key":` (with both quotes AND the colon) rather
# than the bare key substring. This defeats the textual first-occurrence
# hazard that bites _json_field when a field name appears inside a
# history detail string BEFORE the top-level field (the live journal had
# "pending_op" at position 875 inside a sweep detail string, ahead of
# the top-level `"pending_op":null` at file end — _json_field returned
# "no in_flight" and pipeline_settled refused every stop/adopt).
#
# Callers MUST pass the quoted form: _json_field_quoted "$json" '"pending_op":'.
# Pure bash, no jq dependency (matches the existing _json_field/_json_sub
# portability invariant).
_json_field_quoted() {
    local json="$1" qkey="$2" rest
    rest="${json#*"$qkey"}"
    [ "$rest" = "$json" ] && return 1
    rest="${rest#"${rest%%[![:space:]]*}"}"
    if [ "${rest:0:1}" = "\"" ]; then
        rest="${rest:1}"                 # opening quote
        rest="${rest%%\"*}"              # up to the closing quote
    else
        rest="${rest%%\,*}"              # number / bool / null
        rest="${rest%%\}*}"
    fi
    rest="${rest#"${rest%%[![:space:]]*}"}"
    rest="${rest%"${rest##*[![:space:]]}"}"
    printf '%s' "$rest"
}

# _json_has <json> <key> — key present at top level?
_json_has() {
    case "$1" in
        *"\"$2\""*) return 0 ;;
        *) return 1 ;;
    esac
}

# _json_sub <json> <key> — extract a nested object/array value by bracket
# counting from `"key":`. Prints the raw {…} / […] text.
_json_sub() {
    local json="$1" key="$2" out rc
    # Fast path (D1): the O(N) extractor. String-NAIVE by design -- a brace or
    # bracket inside a string value still moves the depth, exactly as the
    # legacy counter below does (plan D1-12: the three verbs are three
    # algorithms; the string-aware machine is NOT shared with this one).
    if _journal_read_probe; then
        out="$(printf '%s' "$json" | "$_JR_PY" "$_JR_HELPER" sub "$key")"
        rc=$?
        case "$rc" in
            0) printf '%s' "$out"; return 0 ;;
            1) return 1 ;;
            *) _warn "_json_sub: read helper UNUSABLE (exit $rc) — falling back to the legacy bracket counter" ;;
        esac
    else
        # T1.5: the same fallback discipline as journal_read — the slow path
        # is ALWAYS visible, never a silent legacy walk.
        _warn "_json_sub: O(N) read helper unavailable — using the legacy bracket counter (correct but slow; install python3 >= 3.7 for the fast path)"
    fi
    _json_sub_legacy "$json" "$key"
}

# _json_sub_legacy <json> <key> — the pre-fix bracket counter, kept verbatim as
# the no-interpreter fallback. Quadratic TWICE (`out="$out$char"` and
# `rest="${rest:1}"`); both mechanisms are removed from the fast path.
_json_sub_legacy() {
    local json="$1" key="$2" rest char depth start out=""
    rest="${json#*"${key}"}"
    [ "$rest" = "$json" ] && return 1
    rest="${rest#*:}"
    rest="${rest#"${rest%%[![:space:]]*}"}"
    case "$rest" in
        '{'*|'['*) ;;
        *) return 1 ;;
    esac
    depth=0
    while [ -n "$rest" ]; do
        char="${rest:0:1}"
        case "$char" in
            '{'|'[') depth=$((depth + 1)) ;;
            '}'|']')
                depth=$((depth - 1))
                out="$out$char"
                if [ "$depth" -eq 0 ]; then
                    printf '%s' "$out"
                    return 0
                fi
                rest="${rest:1}"
                continue
                ;;
        esac
        out="$out$char"
        rest="${rest:1}"
    done
    return 1
}

# ── A1/A2 readiness-class parser (soak-gate-mcp-warmup) ─────────────────
#
# The daemon computes the failure CLASS of every readiness component and
# publishes it in the /readyz body (detail.component_classes), together
# with the grace state (detail.grace) and the leak signal
# (detail.leak_signal). The gate CONSUMES that structured signal — it
# never re-derives a class from a free-text reason string (the legacy
# reason string is stale; decisions §2 row 23).
#
# Parsing follows the EXISTING _json_sub / _json_field precedent
# (_read_inflight_turns_bounded) — pure bash, no jq dependency.
#
# FAIL-CLOSED BY CONSTRUCTION: an absent / unreadable / unexpected
# payload ⇒ "not grace-eligible". An OLDER daemon that publishes no
# detail.grace block is therefore read as the "unknown" class ⇒ NO
# GRACE ⇒ the pre-change soak behaviour is preserved verbatim
# (R-MASK-8).

# The GRACE-ELIGIBLE failure classes (requirements.md §4.2 P2).
GRACE_ELIGIBLE_CLASSES_SH="probe_timeout pool_busy queue_stale read_timeout"

# _readyz_detail <body> — the readyz "detail" object (empty on failure).
_readyz_detail() {
    local body="$1"
    [ -n "$body" ] || return 1
    _json_sub "$body" detail 2>/dev/null
}

# _readyz_grace_active <body> — 0 (true) when the daemon reports an
# ACTIVE class-gated grace; 1 (false) otherwise (including an absent
# grace block ⇒ fail-closed ⇒ no grace).
_readyz_grace_active() {
    local detail grace active
    detail="$(_readyz_detail "$1")" || return 1
    [ -n "$detail" ] || return 1
    grace="$(_json_sub "$detail" grace 2>/dev/null)" || return 1
    [ -n "$grace" ] || return 1
    active="$(_json_field "$grace" grace_active 2>/dev/null)" || return 1
    [ "$active" = "true" ] && return 0
    return 1
}

# _readyz_leak_detected <body> — 0 (true) when the daemon reports a
# pool-leak signal (the grace must REFUSE / DISENGAGE); 1 (false)
# otherwise. An absent leak_signal block reads as "no leak" (the daemon
# publishes it whenever the predicate could be evaluated).
_readyz_leak_detected() {
    local detail leak detected
    detail="$(_readyz_detail "$1")" || return 1
    [ -n "$detail" ] || return 1
    leak="$(_json_sub "$detail" leak_signal 2>/dev/null)" || return 1
    [ -n "$leak" ] || return 1
    detected="$(_json_field "$leak" detected 2>/dev/null)" || return 1
    [ "$detected" = "true" ] && return 0
    return 1
}

# _json_bool_field <json> <key> — the boolean value of a FLAT key
# ("key": true / false). Returns 1 when the key is absent.
_json_bool_field() {
    local json="$1" key="$2" rest
    rest="${json#*"\"${key}\""}"
    [ "$rest" = "$json" ] && return 1
    rest="${rest#*:}"
    rest="${rest#"${rest%%[![:space:]]*}"}"
    case "$rest" in
        true*) printf 'true' ;;
        false*) printf 'false' ;;
        *) return 1 ;;
    esac
}

# _readyz_grace_eligible_transient <body> — 0 (true) when EVERY DEGRADED
# component carries a GRACE-ELIGIBLE failure class (requirements §4.2
# P2). Any never-eligible class, any unknown class, a missing class map
# or a missing components map ⇒ 1 (NOT eligible — fail-closed).
#
# The aggregation is a CONJUNCTION, never a vote: one hard-red component
# among three timeouts kills the grace for that tick.
_readyz_grace_eligible_transient() {
    local body="$1" detail classes comps name value cls
    detail="$(_readyz_detail "$body")" || return 1
    [ -n "$detail" ] || return 1
    classes="$(_json_sub "$detail" component_classes 2>/dev/null)" || return 1
    [ -n "$classes" ] || return 1
    comps="$(_json_sub "$body" components 2>/dev/null)" || return 1
    [ -n "$comps" ] || return 1
    local saw_degraded=0
    for name in database queue_freshness services checkpoint_saver; do
        value="$(_json_bool_field "$comps" "$name")" || return 1
        if [ "$value" = "false" ]; then
            saw_degraded=1
            cls="$(_json_field "$classes" "$name" 2>/dev/null)" || return 1
            [ -n "$cls" ] || return 1
            case " $GRACE_ELIGIBLE_CLASSES_SH " in
                *" $cls "*) : ;;
                *) return 1 ;;
            esac
        fi
    done
    # A readyz that is not degraded at all is not a "grace-eligible
    # transient" (the HELD kind requires a degraded readyz).
    [ "$saw_degraded" = "1" ] || return 1
    return 0
}

# _soak_effective_cap <granted> <ceremony_remaining_s> <soak_s> — the
# effective soak cap (requirements.md §4.3 P3):
#     min(ADAPTIVE_SOAK_CAP_S + granted,
#         ceremony_remaining_s - 60,
#         soak_s,
#         ADAPTIVE_SOAK_CAP_S + GRACE_EXTENSION_CAP_S)
# The 4th term is the ABSOLUTE ceiling; the 2nd is the ceremony budget
# (skipped when the caller passed no budget); the 3rd is the operator's
# own ceiling. With granted=0 the result is EXACTLY the pre-change
# min(ADAPTIVE_SOAK_CAP_S, ceremony_remaining_s - 60, soak_s).
_soak_effective_cap() {
    local granted="$1" remaining="$2" soak_s="$3" cap ceiling c
    cap=$(( ADAPTIVE_SOAK_CAP_S + granted ))
    if [ -n "$remaining" ] && [ "$remaining" -gt 60 ]; then
        c=$(( remaining - 60 ))
        [ "$c" -lt "$cap" ] && cap="$c"
    fi
    if [ "$soak_s" -lt "$cap" ]; then
        cap="$soak_s"
    fi
    ceiling=$(( ADAPTIVE_SOAK_CAP_S + GRACE_EXTENSION_CAP_S_INT ))
    [ "$ceiling" -lt "$cap" ] && cap="$ceiling"
    printf '%s' "$cap"
}

# ── Target resolution (D1 topology table) ────────────────────────────────────
#
# Sets: UP_TARGET, INSTALL_DIR, PORT, POSTGRES_DB, SELF_ENV_MARKER.
# Exits 78 on any unresolved element (fail-closed; D-FA4.6).
resolve_env() {
    local target="${1:-}"
    UP_TARGET="$target"
    SELF_ENV_MARKER="$target"
    case "$target" in
        demo)
            INSTALL_DIR="$(_resolve_install_root agents-ensemble-demo)"
            PORT=7979
            POSTGRES_DB=ensemble_demo
            ;;
        live)
            # Live topology: install dir + DB from the table; PORT from the
            # staged live .env (ADR-014 — no literal live port in this tree,
            # test-strategy §5.2). Execution is USER-GATED (require_live_guard).
            INSTALL_DIR="$(_resolve_install_root agents-ensemble)"
            POSTGRES_DB=ensemble_prod
            if [ -f "$INSTALL_DIR/.env" ]; then
                PORT="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}PORT[[:space:]]*=[[:space:]]*//p' "$INSTALL_DIR/.env" | head -1 | tr -d '\r')"
                PORT="${PORT%\"}"; PORT="${PORT#\"}"
                PORT="${PORT%\'}"; PORT="${PORT#\'}"
            else
                PORT=""
            fi
            if ! printf '%s' "$PORT" | grep -Eq '^[0-9]+$'; then
                _warn "live target: no resolvable PORT in $INSTALL_DIR/.env — refusing (fail-closed)"
                exit 78
            fi
            ;;
        sandbox)
            # Sandbox: EXPLICIT overrides only. No default that could ever
            # resolve to live (by construction there is no default at all
            # for dir/port). Ambient shells on this host may export the
            # LIVE daemon's env (PORT/POSTGRES_DB of the prod install) —
            # a sandbox NEVER inherits those values:
            #   - PORT must be explicitly numeric AND must not be the live
            #     install's staged port (resolved from ~/agents-ensemble/.env,
            #     no literal), the demo port, or the dev (repo) port;
            #   - POSTGRES_DB equal to another env's DB name is ignored
            #     (warned) and replaced by the sandbox default;
            #   - INSTALL_DIR must not be the demo/live install dir.
            INSTALL_DIR="${INSTALL_DIR:-}"
            PORT="${PORT:-}"
            POSTGRES_DB="${POSTGRES_DB:-}"
            # M1 (council rework Batch C): compare PHYSICAL paths, not raw
            # strings — a symlink alias or a trailing-slash spelling of the
            # live/demo dir passed the old string match. Both sides go
            # through _canon_dir (cd + pwd -P), so aliases normalize to the
            # real install dir and are refused; an unresolvable INSTALL_DIR
            # falls back to its raw string and is rejected by the existence
            # check below anyway. The canonical side considers BOTH Layer-1
            # HOME shapes (_is_canonical_install_dir) — under the
            # post-Layer-1 service user the naive $HOME/agents-ensemble
            # would wrongly nest and the refusal would MISS the real tree.
            local canon_sbx
            canon_sbx="$(_canon_dir "$INSTALL_DIR")"
            if _is_canonical_install_dir "$canon_sbx"; then
                _warn "sandbox INSTALL_DIR must be a throwaway dir — refusing to operate on $INSTALL_DIR"
                exit 78
            fi
            if [ -z "$INSTALL_DIR" ] || [ ! -d "$INSTALL_DIR" ]; then
                _warn "sandbox target requires an existing INSTALL_DIR (got '${INSTALL_DIR:-<empty>}') — e.g. TARGET=sandbox INSTALL_DIR=/tmp/ens-sbx PORT=8377"
                exit 78
            fi
            if ! printf '%s' "$PORT" | grep -Eq '^[0-9]+$'; then
                _warn "sandbox target requires an explicit numeric PORT (got '${PORT:-<empty>}')"
                exit 78
            fi
            case "$PORT" in
                7979)
                    _warn "sandbox PORT 7979 is the DEMO port — refusing (own-port discipline, test-strategy §5)"
                    exit 78
                    ;;
                8079)
                    # the repo dev daemon's port (dev.sh) — a sandbox must
                    # never collide with it either (review m5: the sandbox
                    # guard refuses demo + live-staged ports; dev completes
                    # the triple)
                    _warn "sandbox PORT 8079 is the DEV (repo) port — refusing (own-port discipline, test-strategy §5)"
                    exit 78
                    ;;
            esac
            # Live-port cross-check (own-port discipline, test-strategy §5).
            # FAIL CLOSED (M2): a live .env that EXISTS but cannot be read
            # must not silently disable the guard — that is exactly how a
            # sandbox ends up on the live port. Unreadable/unsourcedable →
            # refuse the sandbox outright. An ABSENT .env means no live
            # install is staged on this host — nothing to collide with.
            # Layer-1 HOME shapes (post-flip: HOME/.env, not the nested
            # HOME/agents-ensemble/.env).
            _live_env="$(_resolve_install_root agents-ensemble)/.env"
            if [ -e "$_live_env" ] || [ -L "$_live_env" ]; then
                if [ -d "$_live_env" ] || [ ! -r "$_live_env" ] \
                   || [ ! -f "$_live_env" ]; then
                    _warn "sandbox guard: live install .env at $_live_env exists but is NOT a readable file — cannot verify PORT collision — refusing (fail-closed)"
                    exit 78
                fi
                _live_port_resolve="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}PORT[[:space:]]*=[[:space:]]*//p' "$_live_env" 2>/dev/null | head -1 | tr -d '\r\"'"'"'')"
                if [ -n "$_live_port_resolve" ] && [ "$PORT" = "$_live_port_resolve" ]; then
                    _warn "sandbox PORT equals the LIVE install's staged port — refusing (live-isolation by construction)"
                    exit 78
                fi
            fi
            case "$POSTGRES_DB" in
                ensemble_prod|ensemble_demo|ensemble_dev|"")
                    [ -n "$POSTGRES_DB" ] && _warn "sandbox POSTGRES_DB '$POSTGRES_DB' belongs to another env (or ambient leak) — using the sandbox default"
                    POSTGRES_DB=ensemble_sandbox
                    ;;
            esac
            ;;
        *)
            _warn "unknown target '${target:-<empty>}' (demo|live|sandbox)"
            exit 78
            ;;
    esac
    export UP_TARGET INSTALL_DIR PORT POSTGRES_DB
}

# echo_env_triple — every action echoes the resolved triple (D-FA4.6).
echo_env_triple() {
    _log "resolved env: target=$UP_TARGET dir=$INSTALL_DIR port=$PORT db=$POSTGRES_DB"
}

# require_live_guard <target> — live needs ENSEMBLE_UPGRADE_LIVE=1 else exit
# 78 (deploy.sh:139-148 pattern with the upgrade-flavored guard variable).
require_live_guard() {
    if [ "$1" = "live" ] && [ "${ENSEMBLE_UPGRADE_LIVE:-0}" != "1" ]; then
        cat >&2 <<EOF
${LOG_TAG}: REFUSING to operate on live.
  The upgrade pipeline targets DEMO (\$HOME/agents-ensemble-demo, :7979,
  ensemble_demo) and SANDBOXES (own dir + port + throwaway DB). Live is
  the running orchestrator of Ari and all live agents — out of bounds
  for this initiative by user directive; execution belongs to the user
  after the P2.3 promotion ladder.
  To operate on live anyway:   ENSEMBLE_UPGRADE_LIVE=1 <script> live
EOF
        exit 78
    fi
}

# require_demo_guard <target> [make-target] — the DEMO lane needs the same
# explicit operator ack as the LIVE lane (2026-11-09: the demo install IS a
# running demo orchestrator — a stray `make upgrade-stage` rewrites the whole
# demo install and restarts its daemon; the DEFAULT target of every mutating
# upgrade wrapper is demo). The confirm var is the Makefile guard's per-path
# DEMO var (ENSEMBLE_INSTALL_DEMO) — one var per PATH, not one per lane, so
# the demo confirm means the same thing in every lane. The SANDBOX lane is
# EXEMPT (a throwaway tree, never a running install). Mirrors
# require_live_guard's shape and exit code (78).
require_demo_guard() {
    if [ "$1" = "demo" ] && [ "${ENSEMBLE_INSTALL_DEMO:-0}" != "1" ]; then
        cat >&2 <<EOF
${LOG_TAG}: REFUSING to operate on demo.
  The upgrade pipeline's DEFAULT target is demo (\$HOME/agents-ensemble-demo,
  :7979, ensemble_demo) — a stray invocation rewrites the whole demo install
  and restarts the demo daemon, the same incident class the live guard
  exists for (2026-11-09).
  To operate on demo anyway:   ENSEMBLE_INSTALL_DEMO=1 make ${2:-upgrade-stage}
EOF
        exit 78
    fi
}

# require_live_guard_restart <target> — agent-restart (§3.4 C-1/F-3/F-4):
# the RESTART lane's sibling live guard. Accepts ONLY
# ENSEMBLE_RESTART_LIVE=1 — minted exclusively by the post-turn drain
# IFF the arm stamped spec["provenance_ok"]=True post-gate (marker ⇒
# gate passed; absent ⇒ exit 78 fail-closed). Deliberately DISJOINT from
# ENSEMBLE_UPGRADE_LIVE (§3.4 — the two vars stay disjoint forever; the
# restart lane never sets/reads the upgrade var — T0c). On trip it
# journals a best-effort LOCK-FREE ``live-restart-refused`` event
# (pre-adoption; the notification rides ``executor_exit`` which the
# reaper observes — the event itself has NO terminal-set membership).
require_live_guard_restart() {
    if [ "$1" = "live" ] && [ "${ENSEMBLE_RESTART_LIVE:-0}" != "1" ]; then
        journal_history_append live-restart-refused "restart executor refused: ENSEMBLE_RESTART_LIVE absent (the arming turn's provenance gate did not pass / marker lost) — restart NOT executed; pending_op left for the boot sweep" 2>/dev/null || true
        cat >&2 <<EOF
${LOG_TAG}: REFUSING to restart live.
  The restart executor requires ENSEMBLE_RESTART_LIVE=1 — minted ONLY by
  the post-turn drain when the arming system_restart call passed the
  genuine-user provenance gate. An arm that never passed the gate (or a
  manual invocation) never executes against live.
  This refusal is journaled (live-restart-refused); the armed op, if any,
  is left for the boot sweep to close as abandoned.
EOF
        exit 78
    fi
}

# ── Journal (D4: releases/state.json — single durable state) ────────────────
#
# Schema (interface contract — the launcher sweep [T7, separate owner] and
# the P2.2 Python journal module implement against EXACTLY these fields;
# do not rename):
#   {
#     "current": "<ver>|null", "previous": "<ver>|null",
#     "in_flight": null | { "kind": "promote|rollback|sweep_rollback",
#                           "target": "<ver>", "started_at": "<iso>",
#                           "flipped": false, "owner_pid": <int> },
#                           [live-lane promote only, MINOR-4b, additive:]
#                           "f2_verified_closed": true,
#                           "f2_verified_at": "<iso>",
#                           "f2_verified_note": "<opt operator note>",
#     "rollback_window_count": { "24h": <int>, "window_start": "<iso>" },
#     "cooldown_until": "<iso>|null",
#     "quarantined": ["<ver>", ...],
#     "history": [ { "ts": "<iso>", "event": "commit|rollback|quarantine|sweep|halt", "detail": "..." } ]
#   }

journal_path() { printf '%s/releases/state.json' "$INSTALL_DIR"; }

# journal_init — create the empty journal if absent (idempotent).
journal_init() {
    local jp
    jp="$(journal_path)"
    [ -f "$jp" ] && return 0
    mkdir -p "$(dirname "$jp")"
    journal_write '{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}'
}

# ── Journal read path: O(N) helper + legacy fallback + size guard ───────────
#
# D1 (promote-preflight-hang, 2026-10-29). The 2026-10-28 preflight called
# `journal_read` on a 455,783-byte journal and burned 37 min of 100 % CPU
# without reaching integrity: the balance scan below is O(N^2) because every
# character access is an offset substring (`${json:i:1}`, lib.sh:~910).
#
# The read path now delegates to `scripts/upgrade/journal_read.py` (single
# pass, O(N), byte-level port of the SAME algorithm — exact verdict and
# stdout parity is pinned by tests/packs/preflight_journal_parity_test.sh).
# The bash scanner stays as the FALLBACK so a host without an interpreter is
# slow, never broken.
#
# READ PATH POLICY vs WRITE PATH POLICY (plan D1-13 / RULING 2): the helper is
# its own file with its own policy. It is deliberately NOT routed through
# `_splice_journal`, because that wrapper remaps rc 78 -> 1 and merges stderr
# into stdout (both break the read contract), and because
# `journal_splice.py` refuses a path under the live install unless the
# pipeline lock is held — which `status.sh` (no lock at all) and
# `rollback.sh`/`restart.sh` (before lock acquisition) never hold.
#
# SIZE GUARD (D1-17, leader directive W1): a LEGACY-CLASS REGRESSION BACKSTOP,
# not a size police. It trips only when BOTH conjuncts hold — (1) the read is
# about to run the per-character bash walk (helper absent/unusable) AND
# (2) the journal is >= JOURNAL_SIZE_GUARD_BYTES. On the O(N) path the guard
# is NOT consulted, so the next ceremony reading a 455 KB+ live journal is
# never blocked. Path scope (leader ruling Q3): REFUSE on the promote path;
# WARN + CONTINUE on rollback/restart/status — the emergency recovery path is
# never blocked by a promote-hygiene guard.
#
#   * the number: the measured legacy-walk curve (11 KB -> 2.27 s,
#     55 KB -> 51.6 s, 111 KB -> 199 s, evidence/CODE-PATHS.md §4.4) crosses
#     the 120 s preflight step budget at ~87 KB => 98304 B (96 KiB) is a round
#     number just above the crossover.
#   * JOURNAL_READ_STRICT is set by promote.sh PER INVOCATION as a plain
#     (non-exported) shell variable, immediately before its first journal
#     read: never persisted, never in .env, and never inherited by the child
#     scripts promote.sh launches (so `stop-ensemble.sh` keeps the
#     warn-and-continue recovery semantics).
JOURNAL_SIZE_GUARD_BYTES="${JOURNAL_SIZE_GUARD_BYTES:-98304}"
JOURNAL_LEGACY_READ_BUDGET_S="$(_numeric_knob JOURNAL_LEGACY_READ_BUDGET_S "${JOURNAL_LEGACY_READ_BUDGET_S:-}" 120)"
JOURNAL_READ_STRICT="${JOURNAL_READ_STRICT:-0}"
# D-P1-3 / D-P1-9 (promote-handback-halt Phase 1) — the hand-back
# verifier's two promote-threaded inputs, on the JOURNAL_READ_STRICT
# channel: set by promote.sh PER INVOCATION as PLAIN (non-exported)
# shell variables, never persisted, never in .env, never inherited by
# the child scripts. Empty = unknown:
#   PROMOTE_CEREMONY_DEADLINE_EPOCH — the ceremony wall-clock deadline
#     (epoch seconds). Empty (restart.sh / rollback.sh contexts) ⇒ the
#     verifier uses its CEILING budget, no squeeze, full grace.
#   PROMOTE_EXPECTED_VERSION — the manifest binary_version the flipped
#     release must report on /livez. Empty ⇒ the serving check degrades
#     to "2xx, no version comparison" (still proves SERVING).
PROMOTE_CEREMONY_DEADLINE_EPOCH="${PROMOTE_CEREMONY_DEADLINE_EPOCH:-}"
PROMOTE_EXPECTED_VERSION="${PROMOTE_EXPECTED_VERSION:-}"

# _journal_read_helper_path — absolute path to the read helper. Mirrors
# `_splice_journal_path`: BASH_SOURCE-anchored so the helper is found even
# when the caller changes PWD, with a $REPO_ROOT fallback. JOURNAL_READ_HELPER
# is an explicit override (tests pin the "helper absent" class with it).
_journal_read_helper_path() {
    if [ -n "${JOURNAL_READ_HELPER:-}" ]; then
        if [ -f "$JOURNAL_READ_HELPER" ]; then
            printf '%s\n' "$JOURNAL_READ_HELPER"
            return 0
        fi
        return 1
    fi
    local self_dir
    self_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || self_dir=""
    if [ -n "$self_dir" ] && [ -f "$self_dir/journal_read.py" ]; then
        printf '%s\n' "$self_dir/journal_read.py"
        return 0
    fi
    if [ -n "${REPO_ROOT:-}" ] && [ -f "$REPO_ROOT/scripts/upgrade/journal_read.py" ]; then
        printf '%s\n' "$REPO_ROOT/scripts/upgrade/journal_read.py"
        return 0
    fi
    return 1
}

# _journal_read_probe — 0 when the O(N) read path is usable. Sets the pair
# _JR_PY / _JR_HELPER. The probe is the interpreter floor check: the helper is
# stdlib-only and runs on 3.7+ (the same floor `_splice_python_floor_probe`
# enforces for the write path). Called directly (NOT in a command
# substitution) so the globals land in the caller's shell.
_journal_read_probe() {
    local h p
    _JR_PY=""
    _JR_HELPER=""
    if ! h="$(_journal_read_helper_path)"; then
        return 1
    fi
    if ! p="$(_splice_python_path)"; then
        return 1
    fi
    if "$p" "$h" --probe >/dev/null 2>&1; then
        _JR_PY="$p"
        _JR_HELPER="$h"
        return 0
    fi
    return 1
}

# _journal_read_legacy <journal_path> — the pre-fix scanner, kept verbatim as
# the no-interpreter fallback. Correct, but O(N^2): it is the SLOW path and it
# always announces itself (a silent fallback would recreate the incident as a
# "mystery slow host").
_journal_read_legacy() {
    local jp
    jp="$1"
    if [ ! -f "$jp" ]; then
        return 1
    fi
    local json
    json="$(cat "$jp" 2>/dev/null)" || return 1
    if [ -z "$json" ]; then
        _warn "journal at $jp is EMPTY (torn write?) — refusing to trust it"
        return 1
    fi
    # brace/bracket balance sanity — rejects truncated JSON.
    local ob=0 cb=0 os=0 cs=0 i c in_str=0 esc=0
    for ((i = 0; i < ${#json}; i++)); do
        c="${json:i:1}"
        if [ "$esc" = "1" ]; then esc=0; continue; fi
        if [ "$c" = "\\" ]; then
            if [ "$in_str" = "1" ]; then esc=1; fi
            continue
        fi
        if [ "$c" = "\"" ]; then
            in_str=$((1 - in_str))
            continue
        fi
        [ "$in_str" = "1" ] && continue
        case "$c" in
            '{') ob=$((ob + 1)) ;;
            '}') cb=$((cb + 1)) ;;
            '[') os=$((os + 1)) ;;
            ']') cs=$((cs + 1)) ;;
        esac
    done
    if [ "$ob" != "$cb" ] || [ "$os" != "$cs" ] || [ "$in_str" = "1" ]; then
        _warn "journal at $jp is MALFORMED (torn write: unbalanced json) — refusing to trust it"
        return 1
    fi
    printf '%s' "$json"
}

# _journal_read_fallback <journal_path> — the legacy walk, guarded.
_journal_read_fallback() {
    local jp="$1" size rc
    size="$(wc -c < "$jp" 2>/dev/null | tr -d ' ')" || size=0
    case "$size" in ''|*[!0-9]*) size=0 ;; esac
    if [ "$size" -ge "$JOURNAL_SIZE_GUARD_BYTES" ]; then
        if [ "$JOURNAL_READ_STRICT" = "1" ]; then
            _warn "journal at $jp is ${size} bytes and the O(N) read helper is UNAVAILABLE — REFUSING (promote path; JOURNAL_SIZE_GUARD_BYTES=${JOURNAL_SIZE_GUARD_BYTES}): the legacy per-character bash walk needs ${size}B worth of quadratic scanning (minutes: this is the 2026-10-28 preflight hang). Remedy: put python3 >= 3.7 on PATH (or set SPLICE_PYTHON), or trim the journal below ${JOURNAL_SIZE_GUARD_BYTES} bytes."
            return 1
        fi
        _warn "journal at $jp is ${size} bytes and the O(N) read helper is UNAVAILABLE — proceeding on the LEGACY per-character bash walk under a ${JOURNAL_LEGACY_READ_BUDGET_S}s bound (warn-and-continue: a recovery/diagnostic read is never blocked by the size guard; put python3 >= 3.7 on PATH, or trim the journal to speed this up)"
        _run_bounded "$JOURNAL_LEGACY_READ_BUDGET_S" _journal_read_legacy "$jp"
        rc=$?
        case "$rc" in
            0) return 0 ;;
            124)
                _warn "legacy journal read at $jp exceeded ${JOURNAL_LEGACY_READ_BUDGET_S}s — treating the journal as UNREADABLE (hung read; install python3 >= 3.7 for the O(N) path)"
                return 1 ;;
            *) return 1 ;;
        esac
    fi
    _warn "journal at $jp: O(N) read helper unavailable — falling back to the legacy per-character bash walk (correct but slow; install python3 >= 3.7 for the fast path)"
    _journal_read_legacy "$jp"
}

# journal_read — print journal JSON; exit 1 when absent/unreadable/torn.
# Torn-write detection is CONTENT-based (Batch C doc correction — earlier
# comments described a ".torn marker sibling" mechanism that never existed
# in code; there is NO .torn file, and none is needed): our writers never
# touch the target in place (temp-file + mv is atomic), so a torn state.json
# can only come from an OUT-of-discipline writer — and it is caught by (a)
# the empty-file check and (b) the brace/bracket-balance scan below, which
# rejects truncated JSON. Nothing under releases/ named *.torn is produced
# or consumed by this pipeline.
journal_read() {
    local jp
    jp="$(journal_path)"
    if [ ! -f "$jp" ]; then
        return 1
    fi
    # Fast path (D1): the O(N) helper does ONE read and one pass, and prints
    # the bash-processed bytes (trailing newlines stripped, NULs dropped) --
    # the SAME string the legacy scan below consumes. Verdict and stdout are
    # byte-identical to the walk (pinned: tests/packs/preflight_journal_parity_test.sh).
    if _journal_read_probe; then
        "$_JR_PY" "$_JR_HELPER" verify "$jp"
        case $? in
            0) return 0 ;;                 # stdout already carries the journal
            1) return 1 ;;                 # authoritative refusal (wording from the helper)
            *) _warn "journal read helper ($_JR_HELPER) was UNUSABLE — falling back to the legacy per-character bash walk" ;;
        esac
    fi
    _journal_read_fallback "$jp"
}

# journal_write <json> — ATOMIC: temp file in the same dir + mv -f (D4).
journal_write() {
    local json="$1" jp tmp
    jp="$(journal_path)"
    mkdir -p "$(dirname "$jp")"
    tmp="$jp.tmp.$$"
    if ! printf '%s\n' "$json" > "$tmp" 2>/dev/null; then
        rm -f "$tmp" 2>/dev/null
        _warn "journal write FAILED (cannot create temp in $(dirname "$jp"))"
        return 1
    fi
    if ! mv -f "$tmp" "$jp"; then
        rm -f "$tmp" 2>/dev/null
        _warn "journal write FAILED (mv into place)"
        return 1
    fi
    return 0
}

# journal_update <field> <raw-json-or-scalar> — read-modify-write ONE top-level
# field atomically. <raw> is spliced verbatim (objects/arrays/strings must
# arrive pre-quoted; numbers/bools/null raw). Simple + sufficient for the D4
# schema; avoids a jq dependency (none in the repo's shell toolchain).
journal_update() {
    local field="$1" raw="$2" json out rc
    json="$(journal_read)" || return 1
    # Fast path (D1): the O(N) value splice. ONE read (the journal_read above)
    # -- a second read could straddle the writer's atomic replace (D1-16).
    if _journal_read_probe; then
        out="$(printf '%s' "$json" | "$_JR_PY" "$_JR_HELPER" update "$field" "$raw")"
        rc=$?
        case "$rc" in
            0)
                # The helper terminates its output with a 0x1f sentinel so a
                # raw value that ends in newlines survives `$( )` (bash strips
                # trailing newlines; the sentinel is never the last newline).
                out="${out%$'\x1f'}"
                journal_write "$out"
                return $?
                ;;
            3) _warn "journal_update: field '$field' not found (schema drift?)"; return 1 ;;
            4) _warn "journal_update: splice failed for '$field'"; return 1 ;;
            *) _warn "journal_update: read helper UNUSABLE (exit $rc) — falling back to the legacy value scanner" ;;
        esac
    else
        # T1.5: the same fallback discipline as journal_read — the slow path
        # is ALWAYS visible, never a silent legacy walk.
        _warn "journal_update: O(N) read helper unavailable — using the legacy value scanner (correct but slow; install python3 >= 3.7 for the fast path)"
    fi
    _journal_update_legacy_from "$field" "$raw" "$json"
}

# _journal_update_legacy_from <field> <raw> <json> — the pre-fix RMW splice,
# kept verbatim as the no-interpreter fallback. Its duplicate-key asymmetry
# (head at the LAST occurrence, value scan at the FIRST) is BASE behaviour and
# is preserved by the fast path too (BASE-BUG-PRESERVING; see the parity pack).
_journal_update_legacy_from() {
    local field="$1" raw="$2" json rest head tail out="" c depth
    json="$3"
    if ! _json_has "$json" "$field"; then
        _warn "journal_update: field '$field' not found (schema drift?)"
        return 1
    fi
    # Walk the object; replace the value that follows "field":.
    rest="$json"
    while [ -n "$rest" ]; do
        case "$rest" in
            *"\"$field\""*)
                # head = prefix up to (and including) the last "field"
                # occurrence (% removes the shortest suffix containing it);
                # rest = what follows the occurrence. NOTE: escaped quotes in
                # the pattern — raw "..." here would be swallowed by the
                # outer double-quote context (verified).
                head="${rest%*\"${field}\"*}\"${field}\""
                rest="${rest#*\"${field}\"}"
                rest="${rest#*:}"
                # drop leading whitespace of the old value
                rest="${rest#"${rest%%[![:space:]]*}"}"
                # consume the old value by bracket/quote scanning
                depth=0
                if [ "${rest:0:1}" = "\"" ]; then
                    # string value: skip to closing quote (escape-aware)
                    rest="${rest:1}"
                    while [ -n "$rest" ]; do
                        c="${rest:0:1}"
                        if [ "$c" = "\\" ]; then rest="${rest:2}"; continue; fi
                        [ "$c" = "\"" ] && { rest="${rest:1}"; break; }
                        rest="${rest:1}"
                    done
                else
                    while [ -n "$rest" ]; do
                        c="${rest:0:1}"
                        case "$c" in
                            '{'|'[') depth=$((depth + 1)) ;;
                            '}'|']')
                                if [ "$depth" -eq 0 ]; then break; fi
                                depth=$((depth - 1)) ;;
                            ',')
                                [ "$depth" -eq 0 ] && break ;;
                        esac
                        rest="${rest:1}"
                    done
                fi
                out="$head:$raw$rest"
                break
                ;;
            *)
                break
                ;;
        esac
    done
    [ -n "$out" ] || { _warn "journal_update: splice failed for '$field'"; return 1; }
    journal_write "$out"
}

# journal_in_flight — print the in_flight object verbatim, or nothing.
journal_in_flight() {
    local json
    json="$(journal_read)" || return 1
    _json_sub "$json" "in_flight"
}

# journal_history_append <event> <detail> — append to history (newest last).
# P2 CALL-SITE MIGRATION (this commission): the body is now a single
# `_splice_journal append-history` call — the O(1) python splice
# (journal_splice.py) replaces the journal_read + bash brace-walk +
# journal_update composition that cost ~280 s/event at the 126 KB /
# 500-entry fixture. Caller contract UNCHANGED: same argv (event,
# detail), 0 on success, nonzero on failure (helper refusals — exit 78,
# e.g. torn journal / missing journal — are forwarded as 1 by
# _splice_journal with the token-pinned `splice: REFUSED:` stderr line,
# mirroring the old journal_read refusal; atomicity errors propagate
# as-is). The ~35 promote.sh / rollback.sh / restart.sh call sites are
# covered by this body swap — NO call-site edits. Byte-shape note: the
# helper serializes compact (separators=(",", ":")) with 3.7+ dict-
# order preservation, so non-target fields round-trip byte-identical
# (T2.9 pin, test_journal_splice.sh §7); entries carry the same
# {ts,event,detail} shape (helper builds them structurally).
journal_history_append() {
    _splice_journal append-history "$1" "$2"
}

# _splice_python_path — resolve the python3 interpreter the splice helper
# will run under. Explicit override ($SPLICE_PYTHON) wins (lets tests pin
# a captured interpreter for `T-NEG-python3-missing` / `T-NEG-python3-version-low`),
# else bare `command -v python3`. The preflight probe
# (`_splice_python_floor_probe`, called once at promote.sh entry per C-7)
# is the gate that rejects absent / below-floor interpreters; this
# resolver is for any tool-side re-check.
_splice_python_path() {
    if [ -n "${SPLICE_PYTHON:-}" ]; then
        printf '%s\n' "$SPLICE_PYTHON"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return 0
    fi
    return 1
}

# _splice_journal_path — resolve the absolute path to the helper
# script. Uses lib.sh's own location (BASH_SOURCE) so the helper is
# found even when the caller changes PWD; falls back to a path relative
# to $REPO_ROOT (which promote/stage/rollback all set, and which the
# install paths also know).
_splice_journal_path() {
    local self_dir
    self_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || self_dir=""
    if [ -n "$self_dir" ] && [ -f "$self_dir/journal_splice.py" ]; then
        printf '%s\n' "$self_dir/journal_splice.py"
        return 0
    fi
    if [ -n "${REPO_ROOT:-}" ] && [ -f "$REPO_ROOT/scripts/upgrade/journal_splice.py" ]; then
        printf '%s\n' "$REPO_ROOT/scripts/upgrade/journal_splice.py"
        return 0
    fi
    # LOUD on resolution failure: both anchors missed (no sibling
    # journal_splice.py next to a sourced lib.sh COPY, and no REPO_ROOT).
    # _splice_journal also warns, but naming the two probed paths here is
    # what makes the failure attributable instead of a silent vacuous run.
    _warn "journal splice helper UNRESOLVED (self_dir='${self_dir:-}' REPO_ROOT='${REPO_ROOT:-}') — every journal append will FAIL; a lib.sh sourced from a copy without its sibling journal_splice.py is the classic cause"
    return 1
}

# _splice_journal <op> [args…] — STDIO-FAITHFUL wrapper around the
# python splice helper (decisions (a) / D-P2.4 / T2.2). Same ergonomics
# as `journal_history_append` so the 19 promote.sh call-sites change
# by replacing the function name only. Resolution:
#   1) journal path  → journal_path (one fork via subshell)
#   2) helper path   → _splice_journal_path (BASH_SOURCE-anchored)
#   3) interpreter   → _splice_python_path (SPLICE_PYTHON override or
#                      bare `python3`)
#
# Failure path: anything the helper refuses (exit 78) is forwarded as
# exit 1 from the wrapper with a `_warn` line carrying the op + first
# arg — operators see the same token-pinned stderr as a direct helper
# invocation. Atomicity errors (exit 1) propagate as-is.
_splice_journal() {
    local op="$1"; shift || { _warn "_splice_journal: missing op"; return 1; }
    local jp helper py
    jp="$(journal_path)" || { _warn "_splice_journal: cannot resolve journal_path"; return 1; }
    helper="$(_splice_journal_path)" || { _warn "_splice_journal: cannot locate helper (scripts/upgrade/journal_splice.py)"; return 1; }
    py="$(_splice_python_path)" || { _warn "_splice_journal: python3 not on PATH and SPLICE_PYTHON unset"; return 1; }
    # NOTE: do NOT word-split "$@" through eval — pass it as a literal
    # argv slice; the helper parses explicit args.
    "$py" "$helper" "$jp" "$op" "$@" 2>&1
    local rc=$?
    case "$rc" in
        0) return 0 ;;
        78) _warn "_splice_journal: $op refused (exit 78 — see stderr above for splice: REFUSED: line)"; return 1 ;;
        *)  _warn "_splice_journal: $op exited $rc"; return "$rc" ;;
    esac
}

# journal_open_txn <kind> <target> — set in_flight {kind,target,started_at,
# flipped:false,owner_pid,last_heartbeat,displaced_version}. Refuses
# (returns 1) if a txn is already open. ADDITIVE field: last_heartbeat
# is the epoch the txn was opened — readers that don't know about it
# (legacy launchers, Python twin pre-comp3) ignore the extra key
# (json.loads last-wins on duplicates; lib.sh's textual splice at the
# LAST occurrence picks up the embedded `last_heartbeat` naturally
# because it appears ONCE per txn object). Refreshed by lock_heartbeat
# (see below) so a live owner keeps its heartbeat < HEARTBEAT_STALE_S;
# a dead owner's heartbeat ages to stale, and the new owner-liveness
# fast path in adopt_stale_txn + _journal_sweep can reclaim/observe it
# before the 600s age boundary.
#
# ADDITIVE field (P1, decision (g) / C-4): `displaced_version` is the
# version THIS txn is about to displace (i.e. journal.current at the
# moment the txn is opened), preserved as a version STRING (e.g.
# "v0.20.1" or "null" / empty for a fresh install). Type-distinct from
# `last_heartbeat` (epoch int). The boot_sweep_commit_and_continue path
# (launcher.sh:1020-1037) reads `in_flight.displaced_version` and
# writes `previous = displaced_version` before closing the txn — this
# is the ONLY path in launcher.sh that may update `previous` from
# boot-sweep land (FORWARD semantic: `previous` = the version displaced
# by the most recent terminal event). Old-format in_flight (no
# `displaced_version` field) → halt-for-human in the boot_sweep path
# (ambiguous state, consistent with the "halt-for-human is BY DESIGN"
# recovery invariant). The bash whole-object writer pattern below is
# preserved (no helper dependency, no python in boot path).
journal_open_txn() {
    local kind="$1" target="$2" json existing displaced
    json="$(journal_read)" || return 1
    existing="$(_json_sub "$json" "in_flight")"
    if [ -n "$existing" ] && [ "$existing" != "null" ]; then
        _warn "journal_open_txn: an in_flight txn already exists — refusing (pipeline-busy)"
        return 1
    fi
    # C-4: capture journal.current BEFORE the new txn is opened on top
    # of it. Anchored on the QUOTED-key form (`"current":`) via
    # _json_field_quoted — same defense-in-depth as the lib.sh:1355-1364
    # migration, defeats the bare-substring first-occurrence hazard
    # (a future key like `"current_previous_candidate"` substring would
    # otherwise match ahead of the top-level `"current":`).
    displaced="$(_json_field_quoted "$json" '"current":' 2>/dev/null)" || displaced=""
    case "$displaced" in
        null|"") displaced_raw="null" ;;
        *)       displaced_raw="\"$displaced\"" ;;
    esac
    journal_update "in_flight" \
        "{\"kind\":\"$kind\",\"target\":\"$target\",\"started_at\":\"$(_now_iso)\",\"flipped\":false,\"owner_pid\":$$,\"last_heartbeat\":$(_now_epoch),\"displaced_version\":$displaced_raw}"
}

# journal_mark_flipped — in_flight.flipped = true (raw splice inside the
# in_flight object).
journal_mark_flipped() {
    local json inf new_inf
    json="$(journal_read)" || return 1
    inf="$(_json_sub "$json" "in_flight")"
    [ -n "$inf" ] || { _warn "journal_mark_flipped: no in_flight txn"; return 1; }
    new_inf="${inf//\"flipped\": false/\"flipped\": true}"
    [ "$new_inf" != "$inf" ] || new_inf="${inf//\"flipped\":false/\"flipped\":true}"
    [ "$new_inf" != "$inf" ] || { _warn "journal_mark_flipped: flipped flag not found"; return 1; }
    journal_update "in_flight" "$new_inf"
}

# journal_mark_f2_verified — MINOR-4b (P2.3 review cycle 1): stamp the
# operator's --f2-verified-closed attestation INTO the open in_flight txn
# (live lane only; promote.sh calls it immediately after journal_open_txn,
# under the held lock). Adds f2_verified_closed:true + f2_verified_at
# (ISO ts) + optional f2_verified_note (from F2_VERIFIED_NOTE env, the
# operator's audit trail). ADDITIVE fields only — existing readers (the
# launcher sweep's _js_json_field, the Python twin) parse named fields
# and ignore extras, so the D4 schema stays splice-compatible.
journal_mark_f2_verified() {
    local json inf new_inf note
    json="$(journal_read)" || return 1
    inf="$(_json_sub "$json" "in_flight")"
    # M2 (tidier, P2.3 final batch): _json_sub returns ANY balanced
    # container — an array-shaped (or otherwise non-object) in_flight
    # extraction passed the old non-empty check, and the blind %\}} strip
    # + append below then wrote MALFORMED JSON into the journal (torn on
    # next read). Refuse on null/absent/empty AND any non-'{' shape; the
    # only stampable form is a real object. No journal write on refusal.
    case "$inf" in
        '{'*) ;;
        ''|null)
            _warn "journal_mark_f2_verified: no in_flight txn (in_flight null/absent) — refusing to stamp (journal untouched)"
            return 1
            ;;
        *)
            _warn "journal_mark_f2_verified: in_flight read is null/malformed (not a JSON object: '${inf:0:40}') — refusing to stamp (journal untouched)"
            return 1
            ;;
    esac
    new_inf="${inf%\}}"
    note="${F2_VERIFIED_NOTE:-}"
    if [ -n "$note" ]; then
        new_inf="${new_inf},\"f2_verified_closed\":true,\"f2_verified_at\":\"$(_now_iso)\",\"f2_verified_note\":\"$(_json_escape "$note")\"}"
    else
        new_inf="${new_inf},\"f2_verified_closed\":true,\"f2_verified_at\":\"$(_now_iso)\"}"
    fi
    journal_update "in_flight" "$new_inf"
}

# journal_mark_captured_previous <ver|''> — D5 (self-service-promote
# Wave 3): stamp the FLIP-PREP FILESYSTEM CAPTURE into the open in_flight
# txn as the additive field `captured_previous` — the transactional source
# of truth for the rollback-target chain (LD-5: R0). '' (no current
# symlink — fresh install) persists JSON null = the documented ABSENT
# capture (old-format-equivalent fall-through downstream). CHECKED write:
# a failure returns nonzero and the CALLER must halt with NO flip (the
# capture is the hard precondition — 8b without it resolves from stale
# journal fields, the C-4 wrong-rollback class).
# ADDITIVE field only (D4 splice discipline — same shape as
# journal_mark_f2_verified); existing readers parse named fields and
# ignore extras. The field rides the txn's whole life (journal_mark_flipped
# and journal_heartbeat splice in place, never rewrite the object).
journal_mark_captured_previous() {
    local ver="${1:-}"
    local json inf new_inf raw
    json="$(journal_read)" || return 1
    inf="$(_json_sub "$json" "in_flight")"
    case "$inf" in
        '{'*) ;;
        *)
            _warn "journal_mark_captured_previous: no in_flight txn object (in_flight null/absent/malformed) — refusing to stamp (journal untouched)"
            return 1
            ;;
    esac
    case "$ver" in
        ''|null) raw="null" ;;
        # WRITER-SIDE TYPE GUARD (leader ruling 2026-11-20, option (b) —
        # Finding 5): a non-string captured_previous is treated ABSENT by
        # ALL THREE resolvers (the typing-seam limit); this writer must
        # never LAUNDER one into a JSON string. The resume path
        # (promote.sh re-reads `_json_field … captured_previous` from a
        # prior txn) is the only in-tree path that could: a number /
        # boolean / object fragment comes back raw, and re-stamping it
        # quoted would mint a STRING the R0 predicate then treats as a
        # real (present) capture. Reject — non-string never written; the
        # CALLER halts (checked write — the capture is the hard
        # precondition, no stop, no flip).
        #
        # CYCLE-2 MAJOR-2 (2026-11-20) — the accept arm is VERSION-SHAPED
        # ONLY: the old `*[!0-9.-]*` accept arm took ANY token containing
        # a character outside [0-9.-], which LAUNDERED the exponent forms
        # (1e5 / 1.5e-3 / 2E+10 — JSON NUMBER fragments) into JSON
        # STRINGS (measured rc=0, "captured_previous":"1e5"). The only
        # accept class is now a v-prefixed digits-and-dots core (at
        # least one dot) plus an OPTIONAL separator-suffix that starts
        # with a LETTER (the shape promote.sh's own capture produces:
        # readlink current → releases/vX.Y.Z → vX.Y.Z; prerelease
        # suffixes -rc1 / -beta.1 / .alpha shape) — no exponent markers
        # glued to digits (v1.2.3e10 REFUSES), no empty dot-runs
        # (v1.2.3..4 REFUSES), no bare numbers (those keep the
        # number-shaped rejection), no v-less words. Refusals are
        # ADDITIVE: every token the old arm rejected is still rejected;
        # previously-accepted non-version tokens now refuse loudly
        # (journal untouched).
        true|false|*'{'*|*'['*|*'"'*)
            _warn "journal_mark_captured_previous: non-string captured_previous value rejected (writer-side type guard — leader ruling 2026-11-20 option (b)): '$ver' — refusing to stamp (journal untouched)"
            return 1
            ;;
        # CYCLE-3 A1 (2026-11-20) — the ruling's literal shape
        # `^v[0-9]+([.][0-9]+)+([.-][0-9A-Za-z][0-9A-Za-z.-]*)?$` is
        # BYTE-IDENTICAL to what shipped at the polish commit, yet it
        # still ACCEPTS v1.2.3e10 and v1.2.3..4: the regex engine
        # BACKTRACKS the numeric core to `v1.2` and hands `.3e10` /
        # `.3..4` to the suffix group (separator `.` + alnum + a star
        # class that re-admits bare letters and empty dot-runs). The
        # ruling's INTENT (both forms refuse) is delivered by the
        # minimal strengthening below — same structure, two changes:
        # the suffix's FIRST char must be a LETTER (numeric
        # continuations belong to the core's dot-groups), and dots
        # inside the suffix must be followed by a NON-EMPTY run (no
        # `..`, no trailing dot). Every previously-accepted good shape
        # (v1.2 / v1.2.3 / v1.2.3.4 / v1.2.3-rc1 / v0.32.0-beta.1 /
        # v1.2.3.alpha) still stamps; refusals remain ADDITIVE.
        v[0-9]*.[0-9]*)
            if printf '%s' "$ver" | grep -Eq '^v[0-9]+([.][0-9]+)+([.-][A-Za-z][0-9A-Za-z-]*([.][0-9A-Za-z-]+)*)?$'; then
                raw="\"$(_json_escape "$ver")\""
            else
                _warn "journal_mark_captured_previous: captured_previous value is NOT version-shaped (v + digits.dots, at least one dot, no exponent markers — cycle-2 MAJOR-2): '$ver' — refusing to stamp (journal untouched)"
                return 1
            fi
            ;;
        *[!0-9.-]*)
            # has characters outside the numeric charset but is NOT
            # version-shaped — the exponent forms (1e5 / 1.5e-3 / 2E+10),
            # bare words, anything v-less: MAJOR-2 refuses what the old
            # arm silently accepted-and-quoted.
            _warn "journal_mark_captured_previous: captured_previous value is NOT version-shaped (v + digits.dots, at least one dot, no exponent markers — cycle-2 MAJOR-2): '$ver' — refusing to stamp (journal untouched)"
            return 1
            ;;
        *)
            # only digits/dots/dash — a JSON NUMBER fragment
            _warn "journal_mark_captured_previous: number-shaped captured_previous value rejected (writer-side type guard — leader ruling 2026-11-20 option (b)): '$ver' — refusing to stamp (journal untouched)"
            return 1
            ;;
    esac
    new_inf="${inf%\}}"
    new_inf="${new_inf},\"captured_previous\":$raw}"
    journal_update "in_flight" "$new_inf"
}

# ── D5 (self-service-promote Wave 3): the rollback-target chain ─────────────
# The 5-rung resolution R0 > R1 > R2 > R3 > R4 (§2.5 of
# .agents/shared/planning/self-service-promote/architecture-recommendation.md
# — the BINDING table). THREE implementations exist by necessity (lib.sh
# for the ceremony scripts, launcher.sh's _js_ twin because the launcher
# is staged standalone and never sources lib.sh, and the Python resolver
# daemon/services/rollback_target_chain.py for the daemon closer) —
# AC-D5-12's parity pack pins all three resolve IDENTICALLY
# (tests/fixtures/rollback_target_chain_cases.tsv).
#
# VALID (a G0 candidate: capture / displaced):
#   non-empty ∧ ≠ txn target ∧ ≠ live symlink target ∧ releases/<v> exists
# VALID_to (the R1 `to=` field — F5): non-empty ∧ ≠ target ∧ releases/<v>
#   exists (the live-link conjunct is a G0-candidate rule; the request's
#   to= is gated exactly as the spec's F5 words it).
# R0 capture: ABSENT (field null/empty — the flip-prep persist writes null
#   when there is no symlink) ⇒ fall through; present ∧ VALID ⇒ candidate
#   `captured`; present ∧ INVALID ⇒ HALT capture_invalid — NEVER fall
#   through (F3: a present-but-bogus capture is evidence of a corrupted
#   txn, not of an absent field).
# R1 to=: absent/none ⇒ fall; VALID-fail ⇒ treated ABSENT (never a
#   candidate — F5); VALID ∧ R0-candidate exists ∧ ≠ R0 ⇒ HALT
#   rollback_target_ambiguous; VALID ∧ no R0 ⇒ candidate `requested`.
# R2 journal.current: null ⇒ fall; == target ⇒ INVALID ⇒ fall; VALID ⇒
#   candidate `current`.
# R3 displaced: null ⇒ fall; present ∧ ≠ R2-value while R2 is a VALID
#   candidate ⇒ HALT rollback_target_ambiguous; else VALID ⇒ candidate
#   `displaced` (when R2 == R3 the R2 candidate stands, source=current).
# R4 journal.previous (G1): null ⇒ HALT no_previous_release (the caller
#   maps it to the EXISTING "journal has no previous release" halt,
#   VERBATIM); == target ⇒ same halt; VALID ⇒ candidate `previous`;
#   present-but-otherwise-invalid ⇒ the same halt (never resolve past the
#   last rung onto a bogus value).
# Resolution SHORT-CIRCUITS at the highest VALID rung (clarification (i)):
#   a VALID R0 resolves OVER any stale-field disagreement between
#   R2/R3/R4 — those rungs are never evaluated once R0 resolves.
_rollback_target_valid() {
    # <releases_dir> <ver> <target> <live_link>
    local rel="$1" ver="$2" target="$3" live="$4"
    [ -n "$ver" ] || return 1
    [ "$ver" != "$target" ] || return 1
    [ "$ver" != "$live" ] || return 1
    [ -d "$rel/$ver" ] || return 1
    return 0
}

# CYCLE-2 MAJOR-1 (2026-11-20): the shell seam's mirror of the Python
# resolver's _clean fold. The JSON field extraction (_json_field / the
# launcher's _js_json_field twin) strips quotes, so a JSON NUMBER 123 and
# a JSON string "123" arrive here as the SAME token — the seam cannot
# distinguish, and parity (documented in rollback_target_chain.py's
# _clean) treats such tokens as ABSENT. This predicate folds EXACTLY the
# set _clean folds: the empty string, the JSON-null text forms
# (null/none — none is ALSO the R1 to= sentinel the daemon closer
# writes), the JSON booleans, and every token matching the strict
# JSON-number grammar (int/float/exponent forms; a multi-dot version
# like 0.31.10 is NOT a number and stays a PRESENT candidate). Applied
# to every rung input at resolve() entry — one vocabulary, folded once,
# matching the Python resolver's own entry fold.
_rollback_rung_absent() {
    # rc 0 = the token folds to ABSENT; rc 1 = a present candidate token
    case "$1" in
        ''|null|none|true|false) return 0 ;;
    esac
    printf '%s' "$1" | grep -Eq '^-?(0|[1-9][0-9]*)([.][0-9]+)?([eE][+-]?[0-9]+)?$'
}

_rollback_target_resolve() {
    # <releases_dir> <target> <live_link> <capture> <r1_to> <r2_current>
    # <r3_displaced> <r4_previous>
    # stdout: "resolve <ver> <source>" (rc 0) | "halt <reason>" (rc 1)
    local rel="$1" target="$2" live="$3" cap="$4" r1to="$5"
    local r2="$6" r3="$7" r4="$8"
    local cand="" src=""
    # MAJOR-1: entry fold — mirrors resolve()'s _clean on every rung
    # input (a numeric capture like 123 folds ABSENT here exactly as the
    # non-string fold does daemon-side; a present-but-INVALID string
    # capture still halts capture_invalid below, F3 unchanged).
    _rollback_rung_absent "$live" && live=""
    _rollback_rung_absent "$cap" && cap=""
    _rollback_rung_absent "$r1to" && r1to=""
    _rollback_rung_absent "$r2" && r2=""
    _rollback_rung_absent "$r3" && r3=""
    _rollback_rung_absent "$r4" && r4=""
    # ── R0: the filesystem capture ──
    case "$cap" in
        ''|null)
            ;; # ABSENT (old-format txn / fresh-install null persist) — fall
        *)
            if _rollback_target_valid "$rel" "$cap" "$target" "$live"; then
                cand="$cap"; src="captured"
            else
                printf 'halt capture_invalid'
                return 1
            fi
            ;;
    esac
    # ── R1: the rollback_requested row's to= ──
    case "$r1to" in
        ''|null|none)
            ;; # absent/none — a disposition request only; fall
        *)
            if [ -n "$r1to" ] && [ "$r1to" != "$target" ] && [ -d "$rel/$r1to" ]; then
                if [ -n "$cand" ] && [ "$r1to" != "$cand" ]; then
                    printf 'halt rollback_target_ambiguous'
                    return 1
                fi
                [ -n "$cand" ] || { cand="$r1to"; src="requested"; }
            fi
            # a to= failing VALID (== target / missing dir) is treated as
            # ABSENT — never a candidate, never a halt (F5)
            ;;
    esac
    # SHORT-CIRCUIT (clarification (i)): resolution stops at the highest
    # VALID rung — once R0 or R1 resolved, any R2/R3/R4 stale-field
    # disagreement is RESOLVED-OVER, never an ambiguity halt (those fire
    # only when the conflict is reached with no higher rung resolved).
    if [ -n "$cand" ]; then
        printf 'resolve %s %s' "$cand" "$src"
        return 0
    fi
    # ── R2: journal.current at resolution time ──
    local r2_cand=""
    case "$r2" in
        ''|null)
            ;;
        *)
            if [ "$r2" != "$target" ] && [ "$r2" != "$live" ] && [ -d "$rel/$r2" ]; then
                r2_cand="$r2"
                [ -n "$cand" ] || { cand="$r2"; src="current"; }
            fi
            # == target ⇒ INVALID ⇒ fall; missing dir ⇒ not a candidate ⇒ fall
            ;;
    esac
    # ── R3: in_flight.displaced_version (legacy G0 stamp) ──
    case "$r3" in
        ''|null)
            ;;
        *)
            if [ -n "$r2_cand" ] && [ "$r3" != "$r2_cand" ]; then
                # present ∧ ≠ a VALID R2 ⇒ ambiguity — never a wrong rollback
                printf 'halt rollback_target_ambiguous'
                return 1
            fi
            if [ -z "$cand" ] && _rollback_target_valid "$rel" "$r3" "$target" "$live"; then
                cand="$r3"; src="displaced"
            fi
            ;;
    esac
    # ── R4: journal.previous (G1 — the last rung) ──
    if [ -z "$cand" ]; then
        case "$r4" in
            ''|null)
                printf 'halt no_previous_release'
                return 1
                ;;
        esac
        if [ "$r4" != "$target" ] && [ "$r4" != "$live" ] && [ -d "$rel/$r4" ]; then
            cand="$r4"; src="previous"
        else
            printf 'halt no_previous_release'
            return 1
        fi
    fi
    printf 'resolve %s %s' "$cand" "$src"
    return 0
}

# _rollback_request_to <target> <run_id> — the R1 reader: extract the
# `to <DEST>` token from the rollback_requested history row for this
# (target, run_id). Delimiter-anchored on the PAIR `to <v> to_source <s>`
# (the Wave-3 writer always emits both; a pre-Wave-3 row carries no to=
# ⇒ absent). rc 0 + the value on stdout (may be "none"); rc 1 = no row
# for this (target, run_id) / journal unreadable (the caller treats both
# as R1-absent — an unreadable request row can never nominate a
# destination, and the chain's own halt rules still guard the resolution).
_rollback_request_to() {
    local target="$1" run_id="$2"
    local json hist entry to_tok
    [ -n "$target" ] || return 1
    [ -n "$run_id" ] || return 1
    json="$(journal_read 2>/dev/null)" || return 1
    hist="$(_json_sub "$json" "history" 2>/dev/null)" || return 1
    case "$hist" in ''|null|'[]') return 1 ;; esac
    while IFS= read -r entry; do
        [ -n "$entry" ] || continue
        case "$entry" in
            *"\"event\":\"rollback_requested\""*|*"\"event\": \"rollback_requested\""*) ;;
            *) continue ;;
        esac
        case "$entry" in
            *"target $target "*|*"target $target\"") ;;
            *) continue ;;
        esac
        case "$entry" in
            *"run_id $run_id "*|*"run_id $run_id\"") ;;
            *) continue ;;
        esac
        to_tok="$(printf '%s' "$entry" | grep -oE ' to [^ ]+ to_source' | head -1)"
        case "$to_tok" in
            ' to none to_source') printf 'none'; return 0 ;;
            ' to '*)
                to_tok="${to_tok# to }"
                to_tok="${to_tok% to_source}"
                printf '%s' "$to_tok"; return 0 ;;
            *) return 0 ;;   # the row exists but predates the to= payload ⇒ absent (empty)
        esac
    done < <(_journal_history_entries "$hist")
    return 1
}

# journal_close_txn — in_flight = null.
journal_close_txn() {
    journal_update "in_flight" "null"
}

# journal_heartbeat — refresh the journal's in_flight.last_heartbeat
# to NOW (epoch seconds). The lock's heartbeat file (lock_heartbeat)
# and this field are refreshed TOGETHER — the helper is invoked from
# every lock_heartbeat site (promote/restart/rollback/stage) so a live
# owner keeps BOTH fresh. Mirrors the D5 lock discipline with the
# in_flight journal field added by comp3.
#
# Best-effort: a torn/unwritable journal is WARN-only and never raises
# — lock_heartbeat returns 1 if the dir is gone, 0 otherwise; the
# journal refresh failure is logged but does not flip the return code
# (the lock heartbeat is what callers depend on for liveness; the
# journal one is the fast-path sweeper's signal).
#
# No-op when in_flight is null/absent (no txn to heartbeat — race-safe).
#
# BASH-PATTERN HAZARD: bash parameter-substitution globs ([0-9]*) are
# GREEDY and can eat past the digits — this must stay a sed anchored to
# the field name ("last_heartbeat":[0-9]+); regression pin in
# test_promote_cgroup_survivorship.sh 3b.
journal_heartbeat() {
    # P2 (decision (b) / D-P2.6 / C-3): REPLACES the 139 s/call bash
    # brace-walk that used to live here (this comment + the sed+journal_update
    # body that followed). The splice path uses the
    # `set-in-flight-field last_heartbeat` op (C-2 — the NESTED write that
    # the sweep's `in_flight.last_heartbeat` accessor at
    # daemon/services/upgrade_journal_sweep.py:1590 actually reads; a
    # top-level `set-field last_heartbeat` would be invisible). Caller
    # contract unchanged: ownership-guarded (the helper does NOT check
    # the lock or the owner_pid; lock_heartbeat already enforced both),
    # best-effort, never raises.
    #
    # OUTSIDE-A-TXN SAFEGUARD (preserves the legacy "no-op when no
    # in_flight" semantic that the bash version enforced via
    # `case "$inf" in ''|null) return 0 ;; esac`): the helper would
    # CREATE an empty `in_flight={}` if absent (per the C-2 protocol),
    # which would silently inject a half-formed txn record into the
    # journal.
    #
    # CHEAP GUARD (P2 call-site migration, this commission): the
    # interim guard called journal_in_flight → journal_read — a FULL
    # bash brace-walk of the whole journal per 30 s heartbeat, which
    # at the 126 KB / 500-entry fixture re-introduced the very
    # ~139 s/call cost the splice removed (the heartbeat would have
    # dominated the promote path again at scale). Replaced with a raw
    # `grep -q` on the serialized byte shape. SAFE because every
    # writer in the system emits this field COMPACT:
    #   - bash journal_init       → "in_flight":null
    #   - bash journal_open_txn   → "in_flight":{"kind":…  (field splice)
    #   - python twin (upgrade_journal.py json.dumps
    #     separators=(",", ":"))  → "in_flight":{…|null
    #   - splice helper (_atomic_write, same separators) → ditto
    # so `grep -q '"in_flight":{'` matches EXACTLY the open-txn case;
    # in_flight null / file missing → no match → return 0 — the same
    # no-op semantic. The splice call itself is UNCHANGED (ownership-
    # guarded, best-effort, never raises). Known narrow divergence: a
    # TORN journal that happens to contain the literal pattern now
    # reaches the splice call, which refuses exit-78 on the torn parse
    # (warn line, swallowed by `|| true`) — one stderr line of extra
    # visibility vs the old silent no-op; harmless, because a torn
    # journal halts the pipeline at the next journal_read anyway.
    grep -q '"in_flight":{' "$(journal_path)" 2>/dev/null || return 0
    _splice_journal set-in-flight-field last_heartbeat "$(_now_epoch)" || true
}

# journal_set_current <ver> — set current (string) + sanity echo.
journal_set_current() {
    journal_update "current" "\"$1\""
}

# journal_set_previous <ver> — set previous (string or null).
# P2 CALL-SITE MIGRATION (this commission): single `_splice_journal
# set-previous` call. The helper's set-previous op owns the
# null-vs-version distinction itself — literal argv "null" → JSON null,
# anything else → string (outer double-quotes stripped if present) —
# VERIFIED in journal_splice.py::_op_set_previous — so the bash
# branch collapses into a pass-through. `previous` is in the helper's
# ALLOWED_TOP_LEVEL_WRITES (field-ownership matrix, decisions (a));
# `current` stays FENCED (journal_set_current remains bash — flip-
# moment writes are safety-critical and infrequent). Caller contract
# unchanged: 0 on success, nonzero on failure (refusals forwarded
# as 1 with the `splice: REFUSED:` stderr line).
journal_set_previous() {
    _splice_journal set-previous "$1"
}

# journal_quarantine <ver> — append to quarantined[] (idempotent).
journal_quarantine() {
    local ver="$1" json q new
    json="$(journal_read)" || return 1
    q="$(_json_sub "$json" "quarantined")"
    case "$q" in
        *"\"$ver\""*) return 0 ;;
    esac
    if [ -z "$q" ] || [ "$q" = "[]" ]; then
        new="[\"$ver\"]"
    else
        new="${q%]}, \"$ver\"]"
    fi
    journal_update "quarantined" "$new"
}

# journal_quarantine_clear <ver> — remove from quarantined[]. Called by
# stage.sh when a version is RE-STAGED: the operator explicitly rebuilt the
# artifact, so the prior quarantine verdict no longer describes it.
journal_quarantine_clear() {
    local ver="$1" json q new
    json="$(journal_read)" || return 1
    q="$(_json_sub "$json" "quarantined")"
    case "$q" in
        *"\"$ver\""*) ;;
        *) return 0 ;;
    esac
    # rebuild the list without the entry
    new="$(printf '%s' "$q" | tr -d '[]' | tr ',' '\n' | sed 's/^ *"//;s/" *$//' | grep -v "^$ver\$" | sed 's/^/"/;s/$/"/' | paste -sd, - | sed 's/^/[/;s/$/]/')"
    [ "$new" = "[]" ] || [ "$new" = "[ ]" ] && new="[]"
    journal_update "quarantined" "$new"
}

# ── Phase 2 (promote-handback-halt): the PER-ENTRY history readers ──────────
#
# THE ONE reader contract (phase2-plan.md §D.3's contract box + AM-P2-14):
# a history row matches an (event, target[, run_id]) triple ONLY when ALL
# of its parts appear INSIDE ONE history entry. The entry-object boundary
# is `{"ts":` — the reader splits the history array on it and requires the
# event kind, the delimiter-anchored `target <VER>` and (when supplied)
# the `run_id <ID>` to appear in the SAME entry.
#
# TWO hazards, BOTH closed by that shape:
#   (1) PREFIX COLLISION — `target v0.28.5` occurs inside `target
#       v0.28.55`; the delimiter anchor (a space, or the end of the detail
#       string) closes it.
#   (2) CROSS-ENTRY FALSE POSITIVE — a whole-array conjunction matches
#       "event kind in entry A" AND "target string in entry B". On the
#       CLEARANCE reader a false positive would UN-QUARANTINE a version
#       that was never cleared; on the AUTHORIZATION reader it would make
#       an unauthenticated flip look authorized.
#
# The version literal is UNQUOTED in the house detail convention (the
# promote.sh intent_flip emit is `preflight: intentional promote target
# $VERSION — …`) — a quote-anchored match would NEVER fire (the
# §D.3 contract box records that hazard).

# _journal_history_entries <history_array> — split the raw history array
# into its entries (one per line). The boundary is the entry-object
# marker `{"ts":`.
_journal_history_entries() {
    local rest="$1" entry
    while :; do
        case "$rest" in
            *'{"ts":'*) rest="${rest#*'{"ts":'}" ;;
            *) break ;;
        esac
        case "$rest" in
            *'{"ts":'*) entry="${rest%%'{"ts":'*}" ;;
            *) entry="$rest" ;;
        esac
        printf '%s\n' "$entry"
    done
}

# _journal_history_has_event <event> <target> [run_id] — the ONE per-entry,
# delimiter-anchored reader (the shell twin of the daemon's
# `uj.journal_history_has_event` and of the launcher's
# `_js_history_has_event_for_target`). Reads the journal itself.
_journal_history_has_event() {
    local event="$1" target="$2" run_id="${3:-}"
    local json hist entry
    [ -n "$event" ] || return 1
    [ -n "$target" ] || return 1
    json="$(journal_read)" || return 1
    hist="$(_json_sub "$json" "history")" || return 1
    case "$hist" in
        ''|null|'[]') return 1 ;;
    esac
    while IFS= read -r entry; do
        [ -n "$entry" ] || continue
        # the event-kind literal — the house writers emit the COMPACT form
        # ("event":"x"); the spaced form ("event": "x") is tolerated too
        # (a hand-edited journal must not silently change a verdict).
        case "$entry" in
            *"\"event\":\"$event\""*|*"\"event\": \"$event\""*) ;;
            *) continue ;;
        esac
        case "$entry" in
            *"target $target "*|*"target $target\"") ;;
            *) continue ;;
        esac
        if [ -n "$run_id" ]; then
            case "$entry" in
                *"run_id $run_id "*|*"run_id $run_id\"") ;;
                *) continue ;;
            esac
        fi
        return 0
    done < <(_journal_history_entries "$hist")
    return 1
}

# _version_strip_v <ver> — strip ONE leading "v" from a version literal
# (idempotent; digit-guarded so a non-version string is never mangled).
# The release dir/target is "v0.28.5"; the daemon reports "0.28.5". A
# naive compare is ALWAYS false ⇒ every healthy target reads unhealthy ⇒
# the wrongful rollback returns (R-1). ONE helper per side (the launcher's
# twin is `_js_serving_version_strip`; the daemon's is
# `sweep_serving_green_closer`'s inline strip).
_version_strip_v() {
    local v="$1"
    case "$v" in
        v[0-9]*) printf '%s' "${v#v}" ;;
        *) printf '%s' "$v" ;;
    esac
}

# _journal_txn_identity <in_flight_json> <target> — the txn identity used by
# the write-once key, the request detail and the readers. The RESTART lane
# stamps `run_id` into in_flight; the PROMOTE lane does NOT (the promote
# run_id lives in the pipeline LOCK, gone by the time a stale txn is swept)
# ⇒ the identity falls back to "<target>@<started_at>". THE LAUNCHER AND THE
# DAEMON MUST DERIVE THE SAME STRING (launcher.sh's
# `_js_serving_txn_identity`, upgrade_journal_sweep.py's
# `_closer_txn_identity`) — the request reader matches on it.
_journal_txn_identity() {
    local inf="$1" target="$2" run_id started
    run_id="$(_json_field "$inf" "run_id" 2>/dev/null)" || run_id=""
    case "$run_id" in
        ''|null) run_id="" ;;
    esac
    if [ -n "$run_id" ]; then
        printf '%s' "$run_id"
        return 0
    fi
    started="$(_json_field "$inf" "started_at" 2>/dev/null)" || started=""
    case "$started" in
        ''|null) started="" ;;
    esac
    if [ -n "$started" ]; then
        printf '%s@%s' "$target" "$started"
    else
        printf '%s' "$target"
    fi
}

# journal_history_has_intent_flip <ver> — the AUTHORIZATION reader (the
# T11 conjunct: a serving-green commit-close fires only when BOTH a
# positive serving verdict AND an `intent_flip` event naming that same
# target exist). lib.sh had ZERO intent_flip readers before this lane
# (it has history WRITERS and the quarantined[] array reader only) ⇒
# this is NEW work, not a copy of an existing reader.
journal_history_has_intent_flip() {
    _journal_history_has_event "intent_flip" "$1"
}

# journal_quarantine_cleared_serving_green <ver> [<run_id>] — the
# CLEARANCE reader: the shell gate that refuses a quarantined target
# accepts it when this event names the version. The frozen rule is
# EXTENDED ("cleared by re-staging OR by an explicit serving-green
# clearance event"), never bypassed.
journal_quarantine_cleared_serving_green() {
    _journal_history_has_event "quarantine_cleared_serving_green" "$1" "${2:-}"
}

# journal_is_quarantined <ver>.
journal_is_quarantined() {
    local json
    json="$(journal_read)" || return 1
    case "$(_json_sub "$json" "quarantined")" in
        *"\"$1\""*) return 0 ;;
        *) return 1 ;;
    esac
}

# journal_fail_loud <what> [exit_rc] — a post-flip journal mutation FAILED
# (M5, council rework Batch C). Pre-fix, promote's commit path only WARNED
# and exited 0 with the flipped symlink and the journal diverged — and a
# journal_set_current/close_txn failure left the txn OPEN + flipped:true,
# so the NEXT launcher start would sweep-ROLLBACK a HEALTHY promote.
# Here: best-effort close the txn (if ANY write still lands, closing kills
# the sweep-rollback risk), best-effort halt event, then exit NON-ZERO with
# an unmissable divergence message. Never exit 0 past a failed journal
# write. Operator remediation: repair the journal dir/file to agree with
# the `current` symlink before the next launcher start.
journal_fail_loud() {
    local what="$1" rc="${2:-1}"
    journal_close_txn 2>/dev/null || true
    journal_history_append halt "journal write FAILED ($what) — journal diverged from the flipped current symlink; best-effort txn close attempted; repair the journal before the next launcher start" 2>/dev/null || true
    printf '%s[%s]: JOURNAL DIVERGENCE: journal write FAILED at %s — best-effort txn close attempted. The env may be healthy but the journal does not agree with the current symlink; repair %s BEFORE the next launcher start (a surviving open flipped txn makes the sweep roll back a healthy promote)\n' \
        "$LOG_TAG" "${UP_TARGET:-lib}" "$what" "$(journal_path)" >&2
    exit "$rc"
}

# ── Heartbeat liveness fast-path (comp3; r-20260928-005506-f82e) ─────────────
#
# WHY TWO GATES NOW EXIST (deliberate-design note, R1.3 + comp3):
#
#   Primary  — SWEEP_STALE_S=600 (age). A txn's AGE alone decides
#              adoption / clearing. The 600s window is the primary race
#              guard against a live owner whose heartbeat file has
#              aged: a LIVE owner in the middle of a long op (the
#              stop-script span clamps to 600s > LOCK_STALE_S) writes
#              to the lock file but NOT to the journal's heartbeat
#              field. Pre-comp3 this was the SOLE gate. Preserved
#              BYTE-IDENTICALLY.
#
#   Added    — HEARTBEAT_STALE_S=300 AND owner dead (liveness). A
#              txn's journal heartbeat AND the owner pid BOTH signal
#              death → reclaim NOW, before the 600s age boundary.
#              Closes the r-f82e gap: the executor was killed ~2s in
#              (no heartbeat ever written) but the reaper was also
#              dead and the journal was orphaned for the full 600s
#              before the launcher's next boot sweep could reclaim it.
#
#   Both are checked by the consumers below. The age gate is the
#   FIRST check (preserves ordering — a fresh txn stays fresh
#   regardless of liveness); the liveness gate is the SECOND check
#   (a stale-and-dead txn reclaims earlier than the age gate alone
#   would allow).
#
# _txn_heartbeat_stale <inf> <now_epoch> — print 1 if the in_flight
# txn's journal heartbeat is stale (>HEARTBEAT_STALE_S old), 0 if
# fresh. Missing/garbage heartbeat → stale (1) — a fresh txn is
# always heartbeat-written by journal_open_txn, so an absent value
# means a hand-edited or legacy journal; fail-open to stale so the
# liveness dimension owns the decision.
_txn_heartbeat_stale() {
    local inf="$1" now="$2" hb age
    hb="$(_json_field "$inf" "last_heartbeat" 2>/dev/null)" || hb=""
    if ! printf '%s' "$hb" | grep -Eq '^[0-9]+$'; then
        printf '1'; return 0
    fi
    age=$(( now - hb ))
    if [ "$age" -gt "$HEARTBEAT_STALE_S" ]; then
        printf '1'
    else
        printf '0'
    fi
}

# _txn_owner_dead <inf> — print 1 if the in_flight txn's owner_pid
# is missing/garbage OR kill -0 says it's dead. EPERM = ALIVE
# (foreign-user owner; matches _pid_alive above — breaking a live
# foreign owner's txn would trample its work).
_txn_owner_dead() {
    local inf="$1" owner
    owner="$(_json_field "$inf" "owner_pid" 2>/dev/null)" || owner=""
    if [ -z "$owner" ] || ! printf '%s' "$owner" | grep -Eq '^[0-9]+$'; then
        printf '1'; return 0
    fi
    if _pid_alive "$owner"; then
        printf '0'
    else
        printf '1'
    fi
}

# journal_count_rollback — increment rollback_window_count with 24h window
# rollover; arms cooldown_until (now + COOLDOWN_S) when <arm_cooldown> = 1.
# Prints the new count (0-3 form: the count AFTER this rollback).
# WINDOW ANCHOR (Batch C doc): this is a SLIDING window anchored at the
# LAST rollback, not a tumbling 24h calendar window — window_start is
# re-stamped on EVERY counted rollback, so N rollbacks are refused only
# while all N fall inside the 24h span ending at the newest one. Sparse
# rollback pairs ≥24h apart never accumulate toward the cap; a burst is
# capped at 3 regardless of how the stamps drift. Callers needing a fixed
# anchor would require a schema change (out of scope; ADR-005 D2 wording
# "3/24h" is implemented as this sliding-anchor reading).
# Window rule: first rollback opens the window; a rollback whose window_start
# is >24h old resets count to 0 and re-opens the window.
journal_count_rollback() {
    local arm_cooldown="${1:-0}" json counts cnt wstart now_epoch wstart_epoch new_cnt
    json="$(journal_read)" || return 1
    counts="$(_json_sub "$json" "rollback_window_count")"
    cnt="$(_json_field "$counts" "24h")"
    wstart="$(_json_field "$counts" "window_start")"
    [ -n "$cnt" ] || cnt=0
    case "$wstart" in ""|null) wstart="" ;; esac
    now_epoch="$(_now_epoch)"
    if [ -n "$wstart" ] && wstart_epoch="$(_iso_to_epoch "$wstart")" 2>/dev/null \
       && [ "$((now_epoch - wstart_epoch))" -ge 86400 ]; then
        cnt=0   # window rolled over — this rollback re-opens it
    fi
    new_cnt=$((cnt + 1))
    # M5: a failed counter/cooldown write must propagate — callers (promote
    # 8b bookkeeping, rollback.sh) treat rc≠0 as a journal divergence and
    # fail loud instead of exiting 0 with the anti-flapping count lost.
    journal_update "rollback_window_count" \
        "{\"24h\": $new_cnt, \"window_start\": \"$(_now_iso)\"}" || return 1
    if [ "$arm_cooldown" = "1" ]; then
        local until
        # P2.1 debt — opportunistic fix (2.2b, upgrade-resilience 2026-10-07):
        # the BSD-only ``date -ju -v+${COOLDOWN_S}S -f '%Y-%m-%dT%H:%M:%SZ'`` form
        # FAILS on GNU coreutils (``-j`` rejected; ``-v`` unknown; ``-f``
        # expects a DIFFERENT format ordering than BSD). The plain
        # ``|| until=$(_now_iso)`` fallback silently DISARMS the anti-flapping
        # window on Linux — a corrupt/disarmed cooldown on this host. Dispatch
        # on ``uname -s`` (mirror of atomic_flip lib.sh:2602-2611 +
        # _iso_to_epoch lib.sh:188-204 precedent).
        #
        # The dispatch itself now lives in `_cooldown_until_iso` (the ONE
        # implementation — a REPLICA here would drift from it and make a
        # confirmed defect look refuted; POK probe-01). It keeps both
        # literal date forms byte-identical (the C7 pin greps for them) and
        # adds the `_COOLDOWN_DATE_UNAME` selection override, so either
        # family can be exercised on either host.
        #
        # FAILURE (unchanged behaviour, now loud): a date error on either
        # family — or an unrecognized platform — falls back to "now", i.e.
        # the anti-flapping window is DISARMED. The fallback now says so on
        # stderr instead of silently disarming.
        until="$(_cooldown_until_iso)"
        journal_update "cooldown_until" "\"$until\"" || return 1
    fi
    printf '%s' "$new_cnt"
}

# journal_rollback_count_24h — current in-window count (0 when window stale:
# a caller checking ENTRY sees the post-rollover view).
journal_rollback_count_24h() {
    local json counts cnt wstart wstart_epoch
    json="$(journal_read)" || { printf '0'; return 0; }
    counts="$(_json_sub "$json" "rollback_window_count")"
    cnt="$(_json_field "$counts" "24h")"
    wstart="$(_json_field "$counts" "window_start")"
    [ -n "$cnt" ] || cnt=0
    case "$wstart" in ""|null) printf '0'; return 0 ;; esac
    if wstart_epoch="$(_iso_to_epoch "$wstart")" 2>/dev/null \
       && [ "$(($(_now_epoch) - wstart_epoch))" -ge 86400 ]; then
        printf '0'
        return 0
    fi
    printf '%s' "$cnt"
}

# journal_cooldown_active — 0 if within cooldown (refuse entry), 1 if clear.
# FAIL CLOSED (M3): a present-but-unparseable cooldown_until (garbage stamp,
# empty string, absent field) is treated as an ACTIVE cooldown — a corrupt
# stamp must never silently disable the ADR-005 anti-flapping window. Only
# an explicit null is "no cooldown". Remediation: fix the stamp or let the
# next journal_count_rollback overwrite it.
journal_cooldown_active() {
    local json cd_epoch until_epoch
    json="$(journal_read)" || return 1
    cd_epoch="$(_json_field "$json" "cooldown_until")"
    case "$cd_epoch" in null) return 1 ;; esac
    if [ -z "$cd_epoch" ] || ! until_epoch="$(_iso_to_epoch "$cd_epoch")"; then
        _warn "cooldown_until is unparseable ('${cd_epoch:-<absent>}') — treating cooldown as ACTIVE (fail-closed, ADR-005 anti-flapping)"
        return 0
    fi
    [ "$(_now_epoch)" -lt "$until_epoch" ]
}

# ── rollback.lock.d — mkdir-based lock (D5/D-FA5.1) ─────────────────────────
#
# The PROTOCOL is the contract (launcher [T7] and the P2.2 Python journal
# module implement the identical protocol independently):
#   - mkdir "$INSTALL_DIR/releases/rollback.lock.d" is the atomic acquire
#   - contents: owner (pid), run_id, heartbeat (epoch secs, refreshed
#     ~30s by the live owner)
#   - stale: heartbeat older than LOCK_STALE_S AND owner pid dead/
#     unverifiable (W1: a live owner's lock is never broken on heartbeat
#     age alone — the stop span is un-heartbeated up to 600s) → mv the
#     dir to rollback.lock.stale.<pid> (never rmdir — racy) → re-acquire
#   - second invocation: structured "pipeline-busy run_id=…" (exit 75
#     flavor at the caller), NOT a crash
lock_dir_path() { printf '%s/releases/rollback.lock.d' "$INSTALL_DIR"; }

# _lock_mtime_epoch <path> — file mtime as epoch seconds, platform-branched
# (the house date-idiom branch: BSD stat -f %m vs GNU stat -c %Y). Empty on
# ANY failure/unknown platform — the caller treats that as NEVER PRUNE
# (fail-closed conservative: an unmeasurable age is not proof of age).
_lock_mtime_epoch() {
    case "$(uname -s)" in
        Darwin|*BSD*) stat -f %m "$1" 2>/dev/null || true ;;
        Linux|GNU*)   stat -c %Y "$1" 2>/dev/null || true ;;
        *) : ;;
    esac
}

# _lock_prune_stale_siblings — Fix #6 (2026-11-14 incident §5.5):
# age-bounded prune of rollback.lock.d.stale.<pid> siblings. Called from
# lock_acquire AFTER the acquire succeeded — the pruner is the pipeline
# owner, serialized against concurrent acquirers (a loser would be
# pipeline-busy, never mid-prune). Rules:
#   - ONLY siblings matching rollback.lock.d.stale.<digits> (the protocol's
#     own mv target; suffix = the breaker's pid). Any other name is left
#     untouched — an unknown shape may be operator evidence.
#   - reap only when mtime age > ROLLBACK_LOCK_STALE_PRUNE_S (7d default):
#     at/under the bound SURVIVES (a freshly-broken sibling is crash-
#     forensics the operator may still want; it also cannot be confused
#     with a live pipeline — nothing reaped is young enough to be one).
#   - unparseable/missing mtime ⇒ never prune (fail-closed conservative).
#   - the LIVE lock (rollback.lock.d, no suffix) is structurally out of
#     reach: the glob requires the .stale.<digits> suffix.
#   - every reap is logged twice: a _log line + a journal history row
#     (legal here — the pipeline lock is held by a LIVE owner, which is
#     exactly the history-append authority). Best-effort end to end: a
#     prune failure logs a warning and never fails the acquisition it
#     piggybacks on.
_lock_prune_stale_siblings() {
    local releases_dir lock pruning mtime age reaped=0
    lock="$(lock_dir_path)"
    releases_dir="$(dirname "$lock")"
    [ -d "$releases_dir" ] || return 0
    for pruning in "$releases_dir"/"${lock##*/}".stale.*; do
        [ -e "$pruning" ] || continue   # unexpanded glob (bash 3.2: no nullglob)
        case "${pruning##*/}" in
            rollback.lock.d.stale.[0-9]*) ;;
            *) continue ;;
        esac
        # MINOR ④ (review round 2, twins-mirror): the python twin
        # (upgrade_journal._prune_stale_lock_siblings) fullmatches
        # rollback.lock.d.stale.<DIGITS-to-EOL> — the bare glob case above
        # admitted digit-first + trailing junk (stale.7004junk would be
        # reaped here but SURVIVES there). Enforce the same exact shape:
        # everything after the LAST ".stale." must be ALL digits.
        case "${pruning##*/}" in
            *.stale.*) ;;
            *) continue ;;
        esac
        # strip the NAME, then the ".stale." prefix — the name's suffix is
        # preceded by "d." (rollback.lock.d.stale.X), never by a slash, so
        # a path-form ##*/.stale. strip would match nothing.
        _prune_suffix="${pruning##*/}"
        _prune_suffix="${_prune_suffix##*.stale.}"
        case "$_prune_suffix" in
            ''|*[!0-9]*) continue ;;
        esac
        mtime="$(_lock_mtime_epoch "$pruning")"
        case "$mtime" in ''|*[!0-9]*) continue ;; esac
        age=$(( $(_now_epoch) - mtime ))
        [ "$age" -gt "$ROLLBACK_LOCK_STALE_PRUNE_S" ] || continue
        if rm -rf "$pruning" 2>/dev/null; then
            reaped=$((reaped + 1))
            _log "pipeline lock: reaped stale-broken sibling ${pruning##*/} (age ${age}s > bound ${ROLLBACK_LOCK_STALE_PRUNE_S}s) — fix #6, incident 2026-11-14 §5.5"
            journal_history_append lock-stale-prune \
                "reaped ${pruning##*/} age=${age}s bound=${ROLLBACK_LOCK_STALE_PRUNE_S}s" \
                >/dev/null 2>&1 || true
        else
            _warn "pipeline lock: FAILED to reap stale-broken sibling ${pruning##*/} (age ${age}s) — leaving it (never block the acquisition on housekeeping)"
        fi
    done
    [ "$reaped" -eq 0 ] || _log "pipeline lock: stale-sibling prune complete (${reaped} reaped)"
    return 0
}

lock_run_id=""

# _pid_alive <pid> — kill -0 liveness with EPERM=ALIVE semantics
# (kill -0 EPERM, P2.1 ledger → closed P2.3 B3.5): exit 0 → alive;
# ENOENT/ESRCH → dead; EPERM → ALIVE — the process exists but belongs to
# another user (permission denied ≠ dead; breaking a live foreign owner's
# lock would trample its txn). Matches the Python journal twin
# upgrade_journal._pid_alive (PermissionError → True). Stderr-text match
# under LC_ALL=C (NIT-5 — locale-stable: a localized shell must not flip
# the branch): bash builtin kill and /bin/kill both render "Operation not
# permitted".
_pid_alive() {
    local err
    err="$(LC_ALL=C kill -0 "$1" 2>&1)" && return 0
    case "$err" in
        *"not permitted"*) return 0 ;;
        *) return 1 ;;
    esac
}

# lock_acquire [wait_s] — acquire or fail after bounded wait. Sets
# lock_run_id. Returns: 0 acquired (incl. stale-break); 1 busy after wait.
lock_acquire() {
    local wait_s="${1:-$LOCK_WAIT_S_DEFAULT}" lock owner_pid hb age run_id
    # D4(a)/T4.2 (phase 4): reclaim audit state. The release is TRAP-ONLY
    # today, so a trap-bypassing death (SIGKILL class) orphans the lock
    # dir; the dead-owner/stale-heartbeat breaks below reclaim it --
    # previously a stdout-only _log line, invisible in the durable audit
    # trail, which is how the 2026-10-28 operator ended up performing a
    # MANUAL stale-break believing it necessary. Set on the break, written
    # AFTER we hold the lock (the live-install splice fence refuses
    # history appends unless the pipeline lock is held by a LIVE owner --
    # at break time the old owner is dead, so the row must wait until the
    # new acquisition succeeds).
    local _broke_reason="" _broke_owner="" _broke_run="" _broke_age=""
    lock="$(lock_dir_path)"
    mkdir -p "$(dirname "$lock")"
    lock_run_id="run-$(date +%Y%m%d-%H%M%S)-$$"
    local waited=0
    while :; do
        if mkdir "$lock" 2>/dev/null; then
            printf '%s\n' "$$" > "$lock/owner" 2>/dev/null
            printf '%s\n' "$lock_run_id" > "$lock/run_id" 2>/dev/null
            printf '%s\n' "$(_now_epoch)" > "$lock/heartbeat" 2>/dev/null
            if [ -n "$_broke_reason" ]; then
                # ONE row per reclaim, all five fields (AC2): reclaimed
                # owner pid, reclaimed run_id, heartbeat age, reason,
                # reclaiming run_id. Best-effort (a torn/absent journal
                # never blocks the acquisition).
                journal_history_append lock-stale-break \
                    "reclaimed owner pid=${_broke_owner:-?} run_id=${_broke_run:-?} heartbeat_age=${_broke_age:-?}s reason=${_broke_reason} reclaiming_run_id=${lock_run_id}" \
                    >/dev/null 2>&1 || true
            fi
            # Fix #6 (incident 2026-11-14 §5.5): housekeeping rides the
            # acquire — we hold the lock, so the prune is serialized
            # against concurrent pipelines. Best-effort, never blocks.
            _lock_prune_stale_siblings
            return 0
        fi
        # exists — stale? Break only when the heartbeat is > LOCK_STALE_S AND
        # the owner pid is dead/unverifiable (W1 — mirrors _js_lock_acquire
        # in launcher.sh; the PROTOCOL is the contract, D5). A stale
        # heartbeat ALONE must NOT break the lock: promote/rollback hold it
        # un-heartbeated across the stop-script span (stop-ensemble WAIT_S
        # clamps to 600s > the 300s staleness bound), and breaking under a
        # LIVE owner lets a concurrent action trample a txn whose owner is
        # still mutating it. A missing/garbage owner pid is UNVERIFIABLE:
        # nothing to protect, the heartbeat alone breaks it (preserves
        # crash progress). kill -0 via _pid_alive (POSIX) is the liveness
        # test — EPERM counts as ALIVE (foreign-user owner; see _pid_alive
        # above); residual pid-reuse wedge (crashed owner, pid reused by a
        # live process) degrades to pipeline-busy — safer than trampling a
        # live owner.
        hb="$(cat "$lock/heartbeat" 2>/dev/null)"
        owner_pid="$(cat "$lock/owner" 2>/dev/null)"
        run_id="$(cat "$lock/run_id" 2>/dev/null)"
        # comp3 NOTE (r-f82e): the journal-level liveness fast path lives
        # in the journal sweeps (adopt_stale_txn above, _journal_sweep in
        # launcher.sh), not here. The lock's own heartbeat + owner gate
        # below is the contract — the lock and journal heartbeats are
        # refreshed together at lock_heartbeat sites, so they age in
        # lockstep; a separate journal-OWNER gate here would duplicate
        # the same liveness check (and a stale-but-live lock vs dead-but-
        # fresh journal is the kind of paired-mismatch the r-f82e audit
        # specifically warned against). R-SR13 mirrors the lock/journal
        # protocol across both files; the journal-side fast path
        # reclaims txns, not locks.
        if printf '%s' "$hb" | grep -Eq '^[0-9]+$'; then
            age=$(( $(_now_epoch) - hb ))
            if [ "$age" -gt "$LOCK_STALE_S" ]; then
                local owner_live=0
                if [ -n "$owner_pid" ] && printf '%s' "$owner_pid" | grep -Eq '^[0-9]+$'\
                   && _pid_alive "$owner_pid"; then
                    owner_live=1
                fi
                if [ "$owner_live" -eq 0 ]; then
                    _log "pipeline lock stale (heartbeat ${age}s old, owner pid ${owner_pid:-?} dead/unverifiable, run ${run_id:-?}) — breaking"
                    if mv "$lock" "${lock}.stale.$$" 2>/dev/null; then
                        _broke_reason="stale-heartbeat"
                        _broke_owner="$owner_pid"
                        _broke_run="$run_id"
                        _broke_age="$age"
                        continue
                    fi
                    continue
                fi
                # LIVE owner mid un-heartbeated long op (the stop span) —
                # fall through to the bounded wait/busy below; NEVER break.
            fi
        fi
        # owner process dead? (crash left a fresh-heartbeat dir) — break too
        # (_pid_alive: EPERM = alive under another user — never break)
        if [ -n "$owner_pid" ] && printf '%s' "$owner_pid" | grep -Eq '^[0-9]+$'\
           && ! _pid_alive "$owner_pid"; then
            _log "pipeline lock owner pid $owner_pid is dead — breaking lock"
            if mv "$lock" "${lock}.stale.$$" 2>/dev/null; then
                _broke_reason="dead-owner"
                _broke_owner="$owner_pid"
                _broke_run="$run_id"
                _broke_age="${age:-?}"
                continue
            fi
            continue
        fi
        if [ "$waited" -ge "$wait_s" ]; then
            _log "pipeline-busy run_id=${run_id:-?} owner=${owner_pid:-?} (live) — another pipeline action holds the lock"
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
}

# lock_heartbeat — refresh the heartbeat (call ~every 30s from long ops).
# OWNERSHIP-GUARDED (B2b): only the lock's owner (per the owner file) may
# refresh it. An unguarded write from a non-owner would keep ANOTHER
# process's lock alive — masking a crashed owner — or resurrect a lock a
# stale-breaker already moved aside. This guard is what lets _probe refresh
# the heartbeat unconditionally: probes also run from lockless display
# paths (status.sh-style) and must be a no-op there.
#
# comp3 (r-20260928-005506-f82e): also refreshes the journal's
# in_flight.last_heartbeat via journal_heartbeat. The two writes go
# together so a live owner keeps BOTH fresh; a dead owner ages both.
# journal_heartbeat is best-effort and ownership-guarded itself —
# never raises.
#
# P2 (decisions (b) / D-P2.6 / C-2 + C-3): journal_heartbeat's body
# was REPLACED (not augmented — the prior bash brace-walk was ~280 s
# on the 87 KB state.json, leaving the 30 s heartbeat cadence a
# fiction). The new body is a single splice call:
#     _splice_journal set-in-flight-field last_heartbeat "$(_now_epoch)"
# The NESTED op (C-2) is what the sweep's `in_flight.last_heartbeat`
# accessor at daemon/services/upgrade_journal_sweep.py:1590 reads.
# A top-level `set-field last_heartbeat` would be invisible to that
# reader. The 30 s cadence now actually delivers — the sweep's
# three-signal defense (heartbeat fresh + os.kill + cgroup leaf) sees
# a recent stamp within the budget.
lock_heartbeat() {
    local lock owner
    lock="$(lock_dir_path)"
    [ -d "$lock" ] || return 1
    owner="$(cat "$lock/owner" 2>/dev/null)"
    [ "$owner" = "$$" ] || return 1
    printf '%s\n' "$(_now_epoch)" > "$lock/heartbeat" 2>/dev/null
    # companion journal refresh — same owner, same cadence. The
    # callers already invoke this helper in long loops (gate_soak,
    # promote phase 3-8, stage assembly, restart grace). The body
    # of journal_heartbeat itself is a single splice call now (C-3
    # REPLACE — see comment block above).
    journal_heartbeat >/dev/null 2>&1 || true
}

# lock_release — remove the lock dir (only if we still own it).
lock_release() {
    local lock
    lock="$(lock_dir_path)"
    if [ -d "$lock" ]; then
        local owner
        owner="$(cat "$lock/owner" 2>/dev/null)"
        if [ "$owner" = "$$" ]; then
            rm -rf "$lock"
            return 0
        fi
        _warn "lock_release: lock owned by pid ${owner:-?}, not us — leaving it"
        return 1
    fi
    return 0
}

# ── pipeline_settled / adoption marker (commission v0.16.6 component 2) ─────
#
# INCIDENT (r-20260929-170301-0cb2, 2026-09-29): a systemd-adoption mission
# gated on "current == v0.16.5 + healthy" — STRUCTURALLY RACY because the
# `current` symlink flips at promote.sh:258 BEFORE gates+soak complete, so
# the check passes mid-flight and the adoption raced a live flip. ZERO
# mutual exclusion between promote and adoption: stop-ensemble.sh and
# adopt-unit.sh took NO lock and read NO pipeline state.
#
# FIX (v0.16.6 component 2 — 3-layer adoption protocol):
#   Layer (i)  pipeline_settled preflight (this section) — adoption/stop
#              may NEVER gate on "current == X + health". They MUST gate on
#              this settle check (current set, on-disk symlink matches
#              journal current, no in_flight, no pending_op, lock free).
#   Layer (ii) pipeline lock + adoption marker (this section + the calling
#              scripts) — adoption/stop acquire the rollback.lock.d the
#              same way promote/stage do; an adoption-in-progress marker
#              file refuses concurrent promote prefights symmetrically.
#   Layer (iii) runbook (docs/runbooks/systemd-adoption.md) — the operator
#              procedure; explicit "FORBIDDEN: current == X + health check
#              pattern" with the incident citation.
#
# CONTRACT: pipeline_settled prints ONE machine-readable line on stdout
# ("reason=<token>: …") on failure and returns 1; returns 0 silently on
# success. Consumers wrap the call with their own loud warn + exit 78 —
# this helper stays a PURE check, no side effects, no journal writes.
# BSD date flags BEFORE the `-f fmt value` operand; portable grep -E;
# safe-glob (no set -f in this lib). JSON parse is intentionally shallow
# (`_json_field` returns the first comma/close-brace slice — sufficient
# for top-level scalars here, never reaches into nested structures).

# _pipeline_current_target — print the journal `current` value (empty if
# absent/null/garbled). Strips a leading/trailing JSON-string quote pair.
# Anchored on the QUOTED-KEY form (`"current":`) via _json_field_quoted —
# _json_field would also work today (top-level appears first), but a
# future history detail containing the bare word "current" would silently
# shift the anchor to a substring inside a string value. The quoted form
# is robust against any future history detail content.
_pipeline_current_target() {
    local json jp cur
    jp="$(journal_path 2>/dev/null)" || return 1
    [ -f "$jp" ] || return 1
    json="$(journal_read 2>/dev/null)" || return 1
    cur="$(_json_field_quoted "$json" '"current":' 2>/dev/null)" || cur=""
    case "$cur" in
        null|"") return 1 ;;
        \"*\")    cur="${cur#\"}"; cur="${cur%\"}" ;;
    esac
    [ -n "$cur" ] || return 1
    printf '%s' "$cur"
}

# pipeline_settled — Layer (i) gate. Verifies the upgrade pipeline is in a
# quiescent state: a committed `current`, the on-disk symlink agrees with
# the journal, no in_flight txn, no pending_op, the rollback lock is free.
# Prints ONE machine-readable refusal line on stdout and returns 1 on any
# failure. Returns 0 silently on success — the caller's quiet path.
#
# The refusal line format is FIXED so downstream tooling (incident triage,
# operator scripts, future tests) can grep `^reason=<token>:` and act:
#   reason=journal-current-unset — journal has no current (fresh install
#                                  or post-rollback halt)
#   reason=current-symlink-absent — INSTALL_DIR/current symlink missing
#                                  while journal claims one (layout drift)
#   reason=current-symlink-mismatch — symlink points at a DIFFERENT release
#                                  than the journal (an uncommitted flip;
#                                  the symlink MUST be trusted over the
#                                  journal only when both agree)
#   reason=current-symlink-garbled — symlink target not shaped like
#                                  releases/<ver> (operator-tampered?)
#   reason=in-flight-txn        — journal has an open txn (kind/target/
#                                  started_at/flipped printed in detail)
#   reason=pending-op           — journal has a non-null pending_op
#                                  (in-daemon tool-armed promote record)
#   reason=lock-held            — rollback.lock.d present, owned by pid/
#                                  run_id (promote/stage/rollback in flight)
#
# SPECIAL CASE — no-journal: NOT a refusal. No journal = no pipeline state
# to be unsettled (fresh install, never promoted, post-init). Refusing
# stop here would gate every `Makefile stop`, every fresh-install
# lifecycle, and every operator emergency on a category error. We log an
# INFO line (NOT a `reason=<token>:` failure line — the failure-vocabulary
# contract stays intact for the 7 refusal tokens above) and return 0.
# Fix-back commission v0.16.6 c2.
pipeline_settled() {
    local jp cur target reason=""
    jp="$(journal_path 2>/dev/null)" || true
    if [ -z "$jp" ] || [ ! -f "$jp" ]; then
        # TRIVIALLY SETTLED — no journal = no baseline to protect.
        # INFO line, NOT a refusal (no `reason=<token>:` prefix; the
        # failure-vocabulary contract holds for the 7 refusal tokens).
        printf 'INFO: pipeline_settled — no journal at %s (fresh install / never promoted); trivially settled, no baseline to protect\n' "${jp:-<unresolved>}"
        return 0
    fi
    if ! cur="$(_pipeline_current_target)"; then
        printf 'reason=journal-current-unset: journal at %s has no `current` — fresh install or post-rollback halt; refuse stop/adopt that needs a committed baseline\n' "$jp"
        return 1
    fi
    if [ ! -L "$INSTALL_DIR/current" ]; then
        printf 'reason=current-symlink-absent: $INSTALL_DIR/current symlink missing while journal claims %s — layout divergence (D-FA5.3 freezes mutations)\n' "$cur"
        return 1
    fi
    target="$(readlink "$INSTALL_DIR/current" 2>/dev/null)" || target=""
    case "$target" in
        "releases/$cur") ;;   # agreement — OK
        "releases/"*)
            printf 'reason=current-symlink-mismatch: on-disk current→%s but journal says current=%s (symlink was flipped without committing the journal — never trust either side; halt-for-human)\n' "$target" "$cur"
            return 1
            ;;
        *)
            printf 'reason=current-symlink-garbled: on-disk current→%s (not shaped like releases/<ver>); refuse (operator-tampered?)\n' "${target:-<empty>}"
            return 1
            ;;
    esac
    local json inf pop kind targ started flipped
    json="$(journal_read 2>/dev/null)" || json=""
    if [ -n "$json" ]; then
        # in_flight — null/empty/missing is OK; anything else is a settle
        # failure with the txn's identifying fields named.
        # NOTE: shallow top-level extraction (relies on journal schema
        # discipline — no nested in_flight-named field exists today; any
        # future nested field with the same name will be missed here).
        inf="$(_json_sub "$json" in_flight 2>/dev/null)" || inf=""
        case "$inf" in
            ""|null) ;;
            *)
                kind="$(_json_field "$inf" kind 2>/dev/null)"
                targ="$(_json_field "$inf" target 2>/dev/null)"
                started="$(_json_field "$inf" started_at 2>/dev/null)"
                flipped="$(_json_field "$inf" flipped 2>/dev/null)"
                printf 'reason=in-flight-txn: journal has an open txn (kind=%s target=%s started_at=%s flipped=%s) — refuse any stop/adopt that would race the in-flight mutation\n' "${kind:-?}" "${targ:-?}" "${started:-?}" "${flipped:-?}"
                return 1
                ;;
        esac
        # pending_op — ADDITIVE field owned by the Python journal twin
        # (D-FA1.1). The shell pipeline NEVER writes pending_op (it's the
        # in-daemon tool-armed promote record); its presence means a tool
        # arm is in flight, which must not race a stop/adopt.
        # NOTE: anchored on the QUOTED-KEY form `"pending_op":` via
        # _json_field_quoted — _json_field's bare-substring anchor returns
        # a false-positive when the literal "pending_op" appears inside a
        # history detail string BEFORE the top-level field (the live
        # journal's 2026-09-26T10:02:10Z sweep detail at position 875
        # contains `pending_op run_id=... cleared by reconcile: no in_flight,
        # no terminal event, past expires_at ...` — _json_field extracted
        # "no in_flight" and pipeline_settled refused every stop/adopt,
        # blocking the in-progress systemd adoption). The quoted anchor
        # (`"pending_op":`) only matches the real top-level field.
        pop="$(_json_field_quoted "$json" '"pending_op":' 2>/dev/null)" || pop=""
        case "$pop" in
            ""|null) ;;
            *)
                printf 'reason=pending-op: journal has a non-null pending_op (in-daemon tool-armed promote record) — refuse stop/adopt that would race the tool arm\n'
                return 1
                ;;
        esac
    fi
    # lock free? — the lock_dir_path helper takes INSTALL_DIR; the dir's
    # presence is itself the acquire, regardless of owner contents.
    if [ -d "$(lock_dir_path)" ]; then
        local owner run_id
        owner="$(cat "$(lock_dir_path)/owner" 2>/dev/null)" || owner="?"
        run_id="$(cat "$(lock_dir_path)/run_id" 2>/dev/null)" || run_id="?"
        printf 'reason=lock-held: rollback.lock.d present (owner=%s run_id=%s) — promote/stage/rollback in flight; refuse concurrent stop/adopt\n' "${owner:-?}" "${run_id:-?}"
        return 1
    fi
    return 0
}

# ── Adoption-in-progress marker (Layer ii, promote-side symmetric refusal) ──
#
# RATIONALE: adopt-unit.sh mutates the host in two distinct phases — install
# the unit file (.env NOT staged) → daemon-reload → .env staged BUT unit
# NOT enabled. A concurrent promote whose restart/hand-back keys off the
# staged ENSEMBLE_RESTART_UNIT (lib.sh:2029-2031 hb_unit resolution — env
# override then INSTALL_DIR/.env read — and lib.sh:2082 launcher unit-path
# gate, both inside restart_via_launcher) hits the half-staged state and
# silently misroutes to a systemctl hand-back for a unit that doesn't
# exist yet — promoting onto nothing. The marker tells the promote preflight: "an adoption is mid-
# sequence; refuse (78) until the marker is cleared". The marker lives as
# a file (NOT a journal field — schema discipline: additive journal fields
# require migrations and the Python twin must learn them; a marker file is
# schema-gen-safe by construction and is owned by exactly one tool).
#
# FORMAT: plain text, three lines:
#   pid=<pid>
#   run_id=<run-id>
#   started_at=<iso>
# (no JSON, no shell eval — consumed by read-only inspection; the file's
# mere existence is the gate signal.)
#
# LIFECYCLE: adopt-unit.sh writes it BEFORE install and clears it AFTER
# verify success (or after a step-d failure, with a loud warn — the
# recover path is the documented manual procedure). Promote preflight
# refuses on presence; no auto-expiry (the operator must remove a stale
# marker — see the runbook).

adoption_marker_path() { printf '%s/releases/.adoption_in_progress' "$INSTALL_DIR"; }

# adoption_marker_present — 0 if marker present (refuse), 1 if absent.
adoption_marker_present() {
    [ -e "$(adoption_marker_path)" ] && return 0
    return 1
}

# adoption_marker_write — best-effort stamp; named fields, plain text.
adoption_marker_write() {
    local mp pid run_id started
    mp="$(adoption_marker_path)"
    mkdir -p "$(dirname "$mp")" 2>/dev/null || return 1
    pid="$$"
    run_id="run-$(date +%Y%m%d-%H%M%S)-$$"
    started="$(_now_iso)"
    {
        printf 'pid=%s\n' "$pid"
        printf 'run_id=%s\n' "$run_id"
        printf 'started_at=%s\n' "$started"
    } > "$mp.tmp.$$" 2>/dev/null || { rm -f "$mp.tmp.$$" 2>/dev/null; return 1; }
    mv -f "$mp.tmp.$$" "$mp" 2>/dev/null || { rm -f "$mp.tmp.$$" 2>/dev/null; return 1; }
    return 0
}

# adoption_marker_clear — best-effort + idempotent. NOT owner-guarded by
# design: the operator stale-recovery lane is `rm -f` against the marker
# path (runbook §3c), which is intentionally a non-pid-checked clear so
# stale pids / orphaned runs can be cleaned by hand. Idempotent: absent
# marker → 0, never an error. Used by the verify-success closure path
# (where pid-match is implicit — the writer IS the caller) and as the
# helper behind operator `rm -f` documentation.
adoption_marker_clear() {
    local mp
    mp="$(adoption_marker_path)"
    [ -e "$mp" ] || return 0
    rm -f "$mp" 2>/dev/null || {
        _warn "adoption_marker_clear: cannot remove $mp (manual: rm -f $mp) — promote preflight will keep refusing until the file is gone"
        return 1
    }
    return 0
}

# ── Signal-trap discipline (component 2; r-20260928-005506-f82e) ─────────────
#
# INCIDENT GAP (2026-09-28): the pipeline scripts registered
#   trap 'lock_release' EXIT
# as their ONLY safety net. Bash's EXIT trap does NOT fire on untrapped
# TERM/HUP/INT — so a SIGTERM from systemd cgroup teardown
# (KillMode=control-group), a Ctrl-C, or a SIGHUP from the parent shell
# killed the executor WITHOUT releasing the lock and WITHOUT leaving a
# terminal journal event. The reaper also died first (in-process), so
# no second chance observed the orphan.
#
# FIX: install explicit TERM/HUP/INT handlers that (a) journal a halt
# event (death-anchored — survives the executor exit), (b) release the
# lock idempotently, (c) exit with the signal-appropriate code.
# Race-safe with the EXIT trap: a per-script guard
# ``_LOCK_RELEASED=1`` is set BEFORE lock_release runs in the signal
# trap; the EXIT trap (now ``_trap_safe_exit``) consults the guard and
# skips the second release. Idempotent by construction — no double-fire.
#
# BYTE-IDENTICAL FALLBACK: scripts that don't call ``_trap_install_signal_handlers``
# keep their original ``trap 'lock_release' EXIT`` shape (the existing
# promote.sh / restart.sh / rollback.sh / stage.sh sites). Those sites
# are EXACTLY the four updated here — no other scripts depend on the
# EXIT-only trap.

# Trap guard (set by the signal handler BEFORE lock_release). The EXIT
# trap checks it and skips the release when set.
_LOCK_RELEASED=0

# _trap_safe_exit — EXIT trap body that respects _LOCK_RELEASED. When
# the signal handler ran first, the guard is 1 → no-op (the handler
# already released the lock). When the script exits normally (no signal),
# the guard stays 0 → the original lock_release fires.
_trap_safe_exit() {
    # D2 (self-service-promote Wave 4): the WATCHDOG HOLD is released on
    # EVERY exit path — the belt that covers the three named terminal paths
    # (P4 commit / P5 abort-pre-flip / P6 halt) AND the paths nobody
    # enumerated. The release is IDEMPOTENT (the process-local flag gates
    # it; the marker file is the durable record; the restore removes it).
    # It runs BEFORE the lock-release early return: the hold must be
    # released even when the lock was already released by the path itself.
    _watchdog_hold_release_if_armed >/dev/null 2>&1 || true
    # hbk-err inode-churn fix (2026-11-22; wording narrowed at review W5):
    # the channel files are removed on every exit path of the CEREMONY
    # scripts — the six that call _trap_install_signal_handlers, which
    # REPLACES the EXIT trap with this very body (so the hook survives by
    # being part of the replacement, not by leaving the old trap intact).
    # Packs that install their OWN EXIT trap replace this one and do NOT get
    # this cleanup; they are safe by the lazy-mint INVARIANT (no file exists
    # unless it carries a payload), not by a trap. status.sh installs no
    # trap at all and is safe by that same invariant.
    # It runs BEFORE the lock-release early return so it also fires on the
    # already-released path.
    _handback_channels_cleanup >/dev/null 2>&1 || true
    if [ "${_LOCK_RELEASED:-0}" = "1" ]; then
        return 0
    fi
    lock_release
}

# _signal_journal_halt <signum> — best-effort halt event for the death.
# Best-effort ONLY: an absent/torn/unwritable journal must NEVER block
# the exit (the lock release below is what matters for the next action
# to acquire; a missing halt event is no worse than today's behavior).
# Mirrors _refuse's best-effort journal pattern (ADR-034 append).
#
# D2-4 (phase-2 fold A1/R14): RE-ENTRANCY GUARD. A second TERM arriving
# while the handler is mid-journalling re-enters it ⇒ a DUPLICATE halt
# row (the journal is append-only; `_LOCK_RELEASED` protects the lock
# release, NOT the journal write). The guard sets the flag BEFORE
# journalling; a second entry is a NO-OP (first-row-wins). Pinned by the
# double-TERM and post-completion-TERM legs of
# tests/packs/preflight_watchdog_test.sh.
_HALT_JOURNALED=0
_signal_journal_halt() {
    if [ "${_HALT_JOURNALED:-0}" = "1" ]; then
        return 0
    fi
    _HALT_JOURNALED=1
    local sig="$1" phase="${_PIPELINE_PHASE:-unknown}" detail
    detail="killed by SIG$(printf '%s' "$sig" | sed 's/^SIG//') at phase $phase (r-20260928-005506-f82e: death-anchored journaling; signal handler installed 2026-09-28)"
    journal_history_append halt "$detail" >/dev/null 2>&1 || true
}

# _signal_handler — single handler installed for TERM/HUP/INT.
# Argument is the signal NAME (TERM/HUP/INT) per bash convention
# (``trap '...' TERM`` invokes the handler with $1=TERM). We journal
# first, mark the guard, release the lock, then exit.
# NEVER raise from here — a raise inside a signal handler in bash 3.2
# is undefined behavior. Just exit with the signal-appropriate code.
_signal_handler() {
    local sig="$1"
    # 128 + signum per shell convention: TERM=15→143, HUP=1→129, INT=2→130
    case "$sig" in
        TERM|HUP|INT) ;;
        *) sig=TERM ;;  # conservative
    esac
    _signal_journal_halt "$sig"
    _LOCK_RELEASED=1
    # Direct call (NOT through EXIT trap) — the EXIT trap's
    # _trap_safe_exit sees _LOCK_RELEASED=1 and no-ops anyway.
    lock_release >/dev/null 2>&1 || true
    # Exit via plain `exit N`: the trap runs in-process, so the exit
    # code carries the signal attribution directly.
    case "$sig" in
        TERM) exit 143 ;;
        HUP)  exit 129 ;;
        INT)  exit 130 ;;
        *)    exit 143 ;;
    esac
}

# _trap_install_signal_handlers <phase> — install TERM/HUP/INT traps +
# swap the EXIT trap to the safe variant. Idempotent: a second call
# resets all three traps to the same handler. Phase is recorded on the
# halt event so post-mortems can correlate.
_trap_install_signal_handlers() {
    local phase="$1"
    _PIPELINE_PHASE="$phase"
    trap '_signal_handler TERM' TERM
    trap '_signal_handler HUP'  HUP
    trap '_signal_handler INT'  INT
    trap '_trap_safe_exit' EXIT
    # Reset both guards (in case the script was sourced mid-flow). The
    # halt-journal guard resets with the traps: a re-install (e.g. promote
    # preflight → stage:lock phase rename) must be able to journal a fresh
    # halt row for the NEW phase if a signal lands after the rename.
    _LOCK_RELEASED=0
    _HALT_JOURNALED=0
}

# ── Integrity (T3 / D-FA4.4) ─────────────────────────────────────────────────
#
# manifest.json (written by stage.sh) — field groups (ADR-004 M5 + D-FA4.4):
#   identity:     version, binary_version, staged_at, known_schema_gen,
#                 contains_contract_phase, rollback_safe
#   launcher:     launcher_sha256
#   checksums:    binary_sha256, config_sha256,
#                 agents_manifest {path:sha256,…}, agents_tree_sha256,
#                 frontend_manifest {path:sha256,…}, frontend_tree_sha256
# All sha256 values are hex, bare.

manifest_path() { printf '%s/releases/%s/manifest.json' "$INSTALL_DIR" "$1"; }

# manifest_field <ver> <key> — print a manifest top-level string/bool/number.
manifest_field() {
    local mp json
    mp="$(manifest_path "$1")"
    [ -f "$mp" ] || return 1
    json="$(cat "$mp")" || return 1
    _json_field "$json" "$2"
}

# _tree_manifest <dir> <relprefix> — print "sha256  relpath" lines, sorted by
# relpath (deterministic; D-FA4.4 per-file map + sorted-listing hash).
_tree_manifest() {
    local dir="$1" prefix="$2"
    local _tm_sub="" _tm_rc=0
    # A1 (code council, 2026-10-30): `_run_bounded` TERMs the function's
    # subshell, and a signal does NOT propagate to grandchildren — the inner
    # worker survived a budget kill, kept hashing into a file nobody reads,
    # and its `trap ... EXIT` never ran (the mktemp pair leaked). Two levels
    # are needed: this forwarding trap (the process `_run_bounded` signals)
    # TERMs the worker and WAITS for its own cleanup trap to finish before
    # exiting, so a killed walk leaves no process and no temp file behind.
    # Every call site runs this function in its own subshell (`_run_bounded`,
    # a pipeline, `$( )`), so installing the trap here cannot disturb a
    # caller's handlers.
    trap '{ [ -n "$_tm_sub" ] && kill -TERM "$_tm_sub" 2>/dev/null; [ -n "$_tm_sub" ] && wait "$_tm_sub" 2>/dev/null; exit 143; }' TERM INT HUP
    ( cd "$dir" 2>/dev/null || exit 1
      local list sums rel line sha n_list n_sums
      list="$(mktemp "${TMPDIR:-/tmp}/tree-manifest.XXXXXX")" || exit 1
      sums="$list.sum"
      # A1: an EXIT-only trap does NOT run when the parent TERMs this worker,
      # so the batch survived and the mktemp pair leaked. Reap the batch on
      # TERM/INT/HUP too: children FIRST (a TERMed xargs orphans them and the
      # pgrep lookup would find nothing), then xargs, then remove the pair.
      # Bounded by construction — at most ONE shasum child is in flight
      # (chunks are sequential inside xargs).
      _tm_batch=""
      _tm_clean() {
          local _tm_kids=""
          if [ -n "$_tm_batch" ]; then
              if command -v pgrep >/dev/null 2>&1; then
                  _tm_kids="$(pgrep -P "$_tm_batch" 2>/dev/null)"
              fi
              for _tm_c in $_tm_kids; do
                  kill -TERM "$_tm_c" 2>/dev/null || true
              done
              kill -TERM "$_tm_batch" 2>/dev/null || true
              wait "$_tm_batch" 2>/dev/null || true
              _tm_batch=""
          fi
          rm -f "$list" "$sums" 2>/dev/null || true
      }
      trap '_tm_clean' EXIT
      trap '_tm_clean; exit 143' TERM INT HUP
      find . -type f | sed 's|^\./||' | sort > "$list" || exit 1
      [ -s "$list" ] || return 0
      # ONE shasum invocation per chunk instead of one fork PER FILE. The
      # per-file fork is what made the plugins tree walk 329.6 s/pass
      # (measured; stage walks it twice) -- the phase-1 D1-7 batching.
      # Output order == argv order == the sorted list order, so the two files
      # pair up line by line.
      # ONE shasum invocation per CHUNK (500 files) instead of one fork
      # PER FILE: the per-file fork made the plugins walk 329.6 s/pass
      # (measured; stage walks it twice). Chunking additionally emits an
      # IN-OP progress marker per chunk to STDERR (D3-13/T3.14 -- no
      # background writer; the walk itself narrates every 500 files).
      # Output order == argv order == the sorted list order, so the two
      # files pair up line by line; concatenated chunk output is
      # BYTE-IDENTICAL to the whole-list form (parity re-pinned).
      _tn=0
      while IFS= read -r rel; do printf '%s\0' "$rel"; done < "$list" \
        | xargs -0 -n 500 shasum -a 256 2>/dev/null > "$sums" &
      _tx=$!
      _tm_batch="$_tx"
      # progress narration on the same interval (stderr; stdout stays DATA)
      _tsz="$(wc -l < "$list" | tr -d ' ')"
      _tlast=0
      while kill -0 "$_tx" 2>/dev/null; do
          _tn="$(wc -l < "$sums" 2>/dev/null | tr -d ' ')"
          [ -n "$_tn" ] || _tn=0
          if [ "$_tn" -gt "$_tlast" ] && [ $(( _tn / 500 )) -gt $(( _tlast / 500 )) ]; then
              _tlast=$_tn
              printf '%s %s[%s]: tree-walk progress: %s/%s files
' "$(_log_ts 2>/dev/null || printf '%s' '-')" "$LOG_TAG" "${UP_TARGET:-lib}" "$_tn" "$_tsz" >&2
          fi
          sleep 0.2
      done
      wait "$_tx"
      _tm_batch=""
      n_list="$(wc -l < "$list" | tr -d ' ')"
      n_sums="$(wc -l < "$sums" | tr -d ' ')"
      if [ "$n_list" != "$n_sums" ]; then
          # A hashing failure (unreadable file, an unnameable path) breaks the
          # positional pairing. Fall back to the per-file loop -- byte-identical
          # output, slow -- instead of emitting a MISALIGNED manifest.
          _warn "_tree_manifest: batched shasum produced ${n_sums}/${n_list} lines for $dir — falling back to per-file hashing"
          while IFS= read -r rel; do
              [ -n "$rel" ] || continue
              printf '%s  %s\n' "$(_sha256 "$rel")" "$prefix$rel"
          done < "$list"
          return 0
      fi
      exec 3< "$list" 4< "$sums"
      while IFS= read -r rel <&3; do
          IFS= read -r line <&4 || break
          sha="${line%%  *}"
          printf '%s  %s\n' "$sha" "$prefix$rel"
      done
      exec 3<&- 4<&-
    ) &
    _tm_sub=$!
    wait "$_tm_sub"
    _tm_rc=$?
    _tm_sub=""
    return "$_tm_rc"
}

# _stage_tree_walk <label> <dir> — a bounded, heartbeating, LOGGED tree walk.
#
# D1-7 (phase1): stage.sh:436-446 spent an unbounded, unlogged stretch of CPU
# in an early phase with the last `lock_heartbeat` BEFORE the walk and the next
# one ~130 lines later. That is exactly the shape of the 2026-10-28
# 09:51:36Z / 09:54:59Z stage SIGTERMs: nothing to correlate a kill against.
# Three things change, nothing else:
#   (a) the walk runs under `_run_bounded` with STAGE_TREE_BUDGET_S;
#   (b) a heartbeat loop refreshes the lock/journal DURING the walk;
#   (c) a log line before and after each tree, with the entry count.
# The walk itself is unchanged (_tree_manifest), so byte-parity of the
# manifest is a property of _tree_manifest alone.
_stage_tree_walk() {
    local label="$1" dir="$2" out_f rc hb
    # The walk's output is DATA (it feeds the manifest hash and the per-file
    # map). This function is always invoked as `X="$(_stage_tree_walk ...)"`,
    # so EVERYTHING it writes to stdout is captured into that variable --
    # including `_log` markers, which corrupt the manifest (caught by
    # tests/test_release_journal.sh: tree-hash MISMATCH + phantom map rows on
    # the .staging walk). Two rules therefore:
    #   * the walk lands in a temp file and is cat'd as the ONLY stdout;
    #   * the before/after markers go to STDERR (`_log ... >&2`).
    out_f="$(mktemp "${TMPDIR:-/tmp}/stage-walk.XXXXXX")" || { _warn "stage: checksum walk ${label}: mktemp failed"; return 1; }
    _log "stage: checksum walk START ${label} ($(find "$dir" -type f 2>/dev/null | wc -l | tr -d ' ') files)" >&2
    ( while :; do sleep 15; lock_heartbeat >/dev/null 2>&1 || true; done ) &
    hb=$!
    _run_bounded "$STAGE_TREE_BUDGET_S" _tree_manifest "$dir" "" > "$out_f"
    rc=$?
    kill -KILL "$hb" 2>/dev/null || true
    wait "$hb" 2>/dev/null || true
    case "$rc" in
        0) ;;
        124)
            rm -f "$out_f"
            _warn "stage: checksum walk ${label} exceeded STAGE_TREE_BUDGET_S=${STAGE_TREE_BUDGET_S}s — aborting (an unbounded walk in an early phase is the 2026-10-28 stage-SIGTERM shape; re-measure before raising the budget)"
            return 1 ;;
        *)
            rm -f "$out_f"
            _warn "stage: checksum walk ${label} failed (rc=$rc)"
            return 1 ;;
    esac
    _log "stage: checksum walk DONE ${label} ($(wc -l < "$out_f" | tr -d ' ') entries)" >&2
    cat "$out_f"
    rm -f "$out_f"
}

# _tree_hash <dir> — hash over the sorted listing (the tree's identity).
_tree_hash() {
    _tree_manifest "$1" "" | shasum -a 256 | awk '{print $1}'
}

# _tree_hash_of_lines <manifest-lines> — aggregate hash of ALREADY-computed
# _tree_manifest output (avoids a second tree walk).
_tree_hash_of_lines() {
    printf '%s\n' "$1" | shasum -a 256 | awk '{print $1}'
}

# _manifest_map_lines <manifest-json> <tree> — the "<tree>_manifest" flat
# {path:sha,…} map as "path sha" lines. The map is FLAT by construction
# (stage.sh writes it: no nested braces, no commas inside paths), so a
# line-based sed/tr conversion replaces the generic char-walking JSON
# parser — the latter is O(n²) in bash and costs ~30s on the real agents
# tree. Paths containing commas/quotes would break this (none exist in the
# repo; a false mismatch fails CLOSED — flagged for operator inspection).
_manifest_map_lines() {
    printf '%s' "$1" | sed -n "s/.*\"$2_manifest\": *{//p" | sed 's/}[^}]*$//' \
        | tr ',' '\n' \
        | sed -e 's/^ *"//' -e 's/" *: *"*/ /' -e 's/" *$//' \
        | sort
}

# _no_env_in_release <release_dir> — ADR-014/m6 invariant: NO .env of any
# kind inside a release dir. Returns 0 = clean; 1 = violation (names file).
_no_env_in_release() {
    local hit
    hit="$(find "$1" -name '.env' -print -quit 2>/dev/null)"
    if [ -n "$hit" ]; then
        _warn "release dir contains a .env (ADR-014 m6 invariant): $hit"
        return 1
    fi
    return 0
}

# ── Build provenance (cn 0472b31f trap family; 2026-10-04) ─────────────────
#
# The stage.sh staleness trap family has materialized TWICE (2026-09-02
# dist/, 2026-10-02 frontend/dist) and was caught only by luck each time.
# The structural fix: a provenance sidecar MUST be written at BUILD time and
# verified at STAGE time — blind reuse of prebuilt artifacts is rejected
# with a loud, actionable error. Provenance records:
#
#   - git_head       — the commit at build time (40-hex)
#   - git_head_short — abbreviated sha (operator-friendly log line)
#   - git_dirty      — true if the working tree had uncommitted changes
#                      when the build was produced (a dirty build is unsafe
#                      to stage: an idempotent re-stage would re-build with
#                      DIFFERENT outputs, and the staged binary diverges
#                      from any tag on the tree)
#   - build_at       — ISO-8601 Z timestamp
#   - build_tool     — free-form string identifying the build pipeline
#                      (e.g. "pyinstaller:ensemble.spec", "ng build:frontend")
#   - artifact_sha256 — sha256 of the artifact, computed at build time
#   - artifact_path  — repo-relative path (operator log line)
#
# Sidecar filename: "<artifact>.build-provenance.json" (sibling to the
# artifact). This shape avoids collision with any other manifest, keeps
# the sidecar atomic with the artifact (one write, one fsync), and lets
# `rm dist/ensemble-prod*` clean both in a single operator gesture.
#
# Refusal tokens (best-effort journaled on the install dir):
#   provenance-missing       — no .build-provenance.json sidecar
#   stale-provenance         — provenance records a different git HEAD
#   dirty-build              — provenance reports a dirty build tree
#   provenance-hash-mismatch — provenance's artifact_sha256 doesn't match
#   non-tip-tree             — staging tree is not at the integration tip
#                              (origin/latest or local latest; the
#                              v0.16.13 payload case where a tag was placed
#                              on a non-tip commit and the actual fixes
#                              landed later on the tip)
#
# Override: STAGE_FRESHNESS_OVERRIDE=1 (set by --allow-stale-stage in
# stage.sh; mirrors the --f2-verified-closed pattern). The override
# unlocks ALL refusal tokens in one go, AND journals the override event
# on the install dir so the operator's acceptance is auditable.

# _provenance_path <artifact_abs_path> — sidecar path (sibling).
_provenance_path() {
    printf '%s.build-provenance.json' "$1"
}

# _provenance_write <artifact_abs_path> <git_head> <git_dirty_bool> <tool>
# — write the provenance sidecar. Computes artifact_sha256 inline. Refuses
# on missing artifact (caller bug) or an unrecognized dirty value. The
# dirty value is NORMALIZED to canonical JSON true/false before writing
# (accepts true/True/1 and false/False/0 — legacy numeric 0/1 producers
# keep producing correct sidecars). The write is ATOMIC: tmp file in the
# same dir + mv -f (same discipline as journal_write) — a torn sidecar
# can only come from an out-of-discipline writer, and the verifier's
# provenance-malformed gate catches that class.
_provenance_write() {
    local art="$1" head="$2" dirty="$3" tool="$4"
    if [ ! -f "$art" ]; then
        _warn "_provenance_write: artifact $art missing — refusing to write a sidecar for nothing"
        return 1
    fi
    local dirty_canon
    dirty_canon="$(_provenance_dirty_canon "$dirty")" || {
        _warn "_provenance_write: git_dirty value '$dirty' is not a recognized boolean (true/True/1/false/False/0) — refusing to write an ambiguous sidecar"
        return 1
    }
    local sha short built_at rel prov tmp
    if ! sha="$(_sha256 "$art")" || [ -z "$sha" ]; then
        # M3 (review round 2): refuse LOUDLY on an empty hash. The
        # production hashing tool (shasum -a 256) is missing on this
        # host OR is failing — either way, silently writing
        # `"artifact_sha256": ""` would let stage misdirect with
        # provenance-malformed instead of naming the actual cause
        # (missing hashing tooling). Actionable: install shasum (perl
        # core on most systems) or run from a host that has it; the
        # broader sha256sum/openssl capability-detection is a follow-up
        # (out of this round's scope per the council report's G5/G6/G7
        # deferred ledger).
        _warn "_provenance_write: empty sha256 for $art — the 'shasum' tool is missing, not on PATH, or failed for this artifact. Install shasum (perl core on most systems; BSD's native 'sha256' is non-portable) or run from a host that has it, then rebuild. Refusing to write a sidecar with artifact_sha256:\"\" — stage would misdirect with provenance-malformed instead of naming the missing tooling."
        return 1
    fi
    short="${head:0:12}"
    built_at="$(_now_iso)"
    rel="${art#"$REPO_ROOT"/}"
    prov="$(_provenance_path "$art")"
    tmp="$prov.tmp.$$"
    if ! cat > "$tmp" <<EOF
{
  "git_head": "$head",
  "git_head_short": "$short",
  "git_dirty": $dirty_canon,
  "build_at": "$built_at",
  "build_tool": "$(_json_escape "$tool")",
  "artifact_sha256": "$sha",
  "artifact_path": "$(_json_escape "$rel")"
}
EOF
    then
        rm -f "$tmp" 2>/dev/null
        _warn "_provenance_write: FAILED to write sidecar tmp $tmp"
        return 1
    fi
    if ! mv -f "$tmp" "$prov"; then
        rm -f "$tmp" 2>/dev/null
        _warn "_provenance_write: FAILED to move sidecar tmp into place at $prov"
        return 1
    fi
    _log "wrote provenance $prov (head=$short dirty=$dirty_canon tool=$tool)"
}

# _provenance_dirty_canon <raw> — normalize a git_dirty flag to the
# canonical JSON boolean the writer emits and the verifier's dirty branch
# consumes. Dirty side: true/True/1. Clean side: false/False/0. ANY other
# value returns 1 — the caller (writer) refuses rather than writing an
# ambiguous sidecar, and the verifier refuses provenance-malformed on the
# same vocabulary. No value skips a check, ever (cn 0472b31f C1 contract).
_provenance_dirty_canon() {
    case "$1" in
        true|True|1)    printf 'true' ;;
        false|False|0)  printf 'false' ;;
        *) return 1 ;;
    esac
}

# _provenance_read <artifact_abs_path> — print sidecar JSON content;
# returns 1 (and sets _PROVENANCE_READ_STATUS) when the content cannot be
# trusted. The verifier consumes the status to dispatch the refusal token:
#
#   ok          — _PROVENANCE_READ_STATUS=ok; prints the JSON to stdout.
#   absent      — sidecar file does not exist; verifier → provenance-missing.
#   unreadable  — file exists but cat failed; verifier → provenance-missing.
#   malformed   — file present but content fails _provenance_sidecar_ok
#                 (unbalanced quote/brace, or a load-bearing key appears
#                 more than once — the laundering signature: a hand-
#                 crafted sidecar with an unescaped-quote decoy
#                 `"git_dirty": false` or `"git_head": "<sha>"` inside
#                 an earlier field's value, which would otherwise win the
#                 textual first-occurrence race and let a dirty build
#                 pass as clean — see cn 0472b31f fix round 2, M1).
#                 verifier → provenance-malformed.
#
# Single read path: writer and reader share the _provenance_path
# resolution, so a path-shape drift between them is structurally
# impossible.
_provenance_read() {
    local prov
    prov="$(_provenance_path "$1")"
    _PROVENANCE_READ_STATUS="absent"
    [ -f "$prov" ] || return 1
    local json
    if ! json="$(cat "$prov" 2>/dev/null)"; then
        _PROVENANCE_READ_STATUS="unreadable"; return 1
    fi
    _PROVENANCE_READ_STATUS="malformed"
    [ -n "$json" ] || { _PROVENANCE_READ_DETAIL="empty file"; return 1; }
    if ! _provenance_sidecar_ok "$json"; then
        _PROVENANCE_READ_DETAIL="quote/brace imbalance OR a load-bearing key appears more than once (laundering signature)"
        return 1
    fi
    _PROVENANCE_READ_STATUS="ok"
    _PROVENANCE_READ_DETAIL=""
    printf '%s' "$json"
}
_PROVENANCE_READ_STATUS=""
_PROVENANCE_READ_DETAIL=""

# _provenance_sidecar_ok <json> — return 0 if the sidecar content is
# well-formed enough to trust _json_field_quoted's first-occurrence
# extraction; 1 otherwise. TWO gates, both bash-only (no jq, no python
# — same discipline as journal_read lib.sh:519-566; python3 IS
# available on every host that runs the upgrade pipeline but the
# verifier stays portable per ADR-014):
#
#   Gate 1: journal_read quote/brace-balance scanner, ported verbatim
#           (catches torn writes + unclosed strings; reviewer-spec M1).
#   Gate 2: load-bearing-key uniqueness — each of `"git_head":`,
#           `"git_dirty":`, `"artifact_sha256":` must appear EXACTLY ONCE
#           in the content. The first-occurrence extractor would
#           otherwise return the DECOY (in a non-key field's value)
#           and let a dirty build pass as clean. The balance scan
#           alone is insufficient here — the reviewer's M1 spec
#           suggested it, but the laundering shapes (decoy inside a
#           `"note": "leaked "git_dirty": false more text"` value)
#           are quote-balanced yet invalid; the uniqueness check is
#           what catches the class. Absent (count 0) is tolerated by
#           THIS gate (the downstream load-bearing shape gate handles
#           the missing-field case).
#
# The validity-bounded is balanced quotes + balanced braces + unique
# load-bearing keys. Together: enough to defeat the laundering class
# without a real JSON parser.
_provenance_sidecar_ok() {
    local json="$1"
    # Gate 1: balance scan (ported from journal_read lib.sh:540-565).
    local ob=0 cb=0 os=0 cs=0 i c in_str=0 esc=0
    for ((i = 0; i < ${#json}; i++)); do
        c="${json:i:1}"
        if [ "$esc" = "1" ]; then esc=0; continue; fi
        if [ "$c" = "\\" ]; then
            if [ "$in_str" = "1" ]; then esc=1; fi
            continue
        fi
        if [ "$c" = "\"" ]; then
            in_str=$((1 - in_str))
            continue
        fi
        [ "$in_str" = "1" ] && continue
        case "$c" in
            '{') ob=$((ob + 1)) ;;
            '}') cb=$((cb + 1)) ;;
            '[') os=$((os + 1)) ;;
            ']') cs=$((cs + 1)) ;;
        esac
    done
    if [ "$ob" -ne "$cb" ] || [ "$os" -ne "$cs" ] || [ "$in_str" -ne 0 ]; then
        return 1
    fi
    # Gate 2: load-bearing-key uniqueness (laundering signature).
    # The writer's output contains each of these exactly once; a hand-
    # crafted sidecar with a decoy `"git_dirty":` or `"git_head":` in
    # a non-key field's leaked value contains it twice. bash 3.2-safe
    # pattern-stripping count (no grep dependency).
    local key count tmp
    for key in '"git_head":' '"git_dirty":' '"artifact_sha256":'; do
        tmp="$json"; count=0
        while [ "${tmp#*"$key"}" != "$tmp" ]; do
            tmp="${tmp#*"$key"}"
            count=$((count + 1))
        done
        if [ "$count" -gt 1 ]; then
            return 1
        fi
    done
    return 0
}

# _git_dirty_porcelain — print the canonical JSON boolean for the repo's
# working-tree state: true if uncommitted changes exist, false otherwise.
# `git status --porcelain` is O(file count) and BSD-safe. Ignores
# submodules (the scripts dir is self-contained; if it grows submodules,
# add --ignore-submodules=dirty). CANONICAL-BOOLEAN CONTRACT (C1 fix):
# prints true/false — the exact values _provenance_write normalizes and
# the verifier's dirty branch matches — never bare 0/1.
_git_dirty_porcelain() {
    if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]; then
        printf 'true'
    else
        printf 'false'
    fi
}

# _tip_sha — print the integration-tip SHA. Resolution order: the
# remote-tracking ref refs/remotes/origin/latest, then the local BRANCH
# ref refs/heads/latest. Both are FULL refspecs — the bare-`latest` DWIM
# form is deliberately NOT used because it can resolve a TAG named
# `latest` (refs/tags/latest wins in git's DWIM order), which would pin
# the tip to a tag instead of the integration branch. Returns 1 if no
# tip ref resolves. The tip is the highest commit on the integration
# branch — a non-tip staging tree either missed a merge (stale tip
# behind the tag) or is on a feature branch (intentional but explicit
# override required for the v0.16.13 payload case where the tag was
# placed BEFORE the merges that included the actual fixes).
_tip_sha() {
    local tip
    tip="$(git -C "$REPO_ROOT" rev-parse --verify refs/remotes/origin/latest 2>/dev/null)" \
        && [ -n "$tip" ] && printf '%s' "$tip" && return 0
    tip="$(git -C "$REPO_ROOT" rev-parse --verify refs/heads/latest 2>/dev/null)" \
        && [ -n "$tip" ] && printf '%s' "$tip" && return 0
    return 1
}

# _stage_freshness_journal_override <reason_token> <detail> — best-effort
# journal append for a freshness-guard override. Idempotent and unlocked
# (the override event is informational; the lock is for serialization with
# promote/rollback, which doesn't apply to pre-lock stage). The append
# rides plain journal_history_append — additive, no new write mechanics.
# DURABLE LANDING (S1): the journal file is ensured to exist before the
# append (journal_init is idempotent), so a VIRGIN install dir still
# records the override — the audit trail does not depend on a prior
# pipeline op having initialized the journal. The init is gated on the
# install dir ALREADY existing: a refusal/override never bootstraps an
# install path that stage itself has not created yet.
_stage_freshness_journal_override() {
    local reason="$1" detail="$2"
    if [ -d "${INSTALL_DIR:-}" ]; then
        journal_init >/dev/null 2>&1 || true
    fi
    journal_history_append "stage_freshness_override" \
        "$detail (reason=$reason operator_accepted=true)" \
        >/dev/null 2>&1 \
        || _warn "freshness-override journal append FAILED (best-effort) — override is taken but the audit trail is incomplete; reason=$reason"
}

# _freshness_refuse <token> <msg...> — the freshness guard's refusal
# wrapper: ensures the install dir's journal exists (same durable-landing
# gate as _stage_freshness_journal_override) then delegates to the shared
# _refuse (loud WARN with (reason=<token>) + best-effort journal append +
# exit 78). Shared _refuse is NOT modified — promote/rollback refusal
# semantics are untouched; only the freshness guard durably lands its
# events on a virgin install dir.
_freshness_refuse() {
    local token="$1"; shift
    if [ -d "${INSTALL_DIR:-}" ]; then
        journal_init >/dev/null 2>&1 || true
    fi
    _refuse "$token" "$@"
}

# ═══════════════ Config-compat promote entry check (D-2) ═══════════════
#
# _config_compat_check — E2E round 1 🟠 (demo promote 2026-11-15): the
# install's config.yaml does NOT travel with releases (only launcher.sh
# swaps per-release), so a promote whose TARGET release cannot parse the
# install config crash-loops the new daemon with a deterministic pydantic
# error AFTER the flip — the demo auto-rollback then landed on an
# unbootable previous release and halted for 5+ hours of human time. This
# entry check validates the install config's KEY SHAPE against the TARGET
# release's config model contract BEFORE any stop/flip; incompatibility is
# an exit-class refusal (the 78 freshness-refusal family) with a named
# remedy. FAIL-CLOSED by mandate: if the check CANNOT run (checker or
# contract absent, interpreter without PyYAML, config unparseable), it
# REFUSES — it never warns-and-passes.
#
# Contract resolution (explicit order — D-6, E2E round 4 / cycle-4 🔴
# anomaly 7ce14c96: the §7 self-detached promote runs from the INSTALL dir,
# where the pre-D-6 legs could not resolve — no daemon/config.py in the
# install layout, no contract next to lib.sh, REPO_ROOT by-design absent
# (the refusal rendered the unset fallback literally: "?/daemon/...") while
# the 12,873-B contract sat at releases/<ver>/daemon/plugin_subsystem/):
#   1. TARGET-PAYLOAD — $INSTALL_DIR/releases/$VERSION/daemon/plugin_subsystem/
#      config_compat_contract.json. FIRST-AUTHORITATIVE when promoting a
#      staged version: stage.sh minted it (stage.sh:546) from exactly the
#      tree that boots after the flip — the release content, not the
#      current tree state, is the oracle.
#   2. TREE CONTEXT — promote running from a checkout/worktree: the target
#      model is importable at <tree>/daemon/config.py, so the contract is
#      minted FRESH via the payload's own minter
#      (config_compat_contract.py --repo-root <tree>) into a tmp file; a
#      mint failure falls through to the staged-contract rungs (and finally
#      to the refusal). Skipped when leg 1 already resolved.
#   3./4. PAYLOAD CONTEXT legacy rungs — next to lib.sh, then $REPO_ROOT.
#      Legacy payloads staged before this lane carry no contract ⇒ REFUSE
#      with the re-stage remedy.
# The checker + minter are slice members (executor_family_paths — staged
# with every payload; commit-in the tree for the tree context).
#
# Interpreter ladder (same conventions as the staleness predicate's
# PyYAML requirement): CONFIG_COMPAT_PYTHON wins; else the build venv
# (the only interpreter guaranteed to carry pydantic for the tree-context
# mint); else python3.
_config_compat_check() {
    local version="${1:-?}" checker contract py cfg_out rc
    # SCOPE (D-2): the gate guards REAL installs (demo/live) — the sandbox
    # lane is the free exercise lane (require_live_guard precedent), and
    # sandbox fixtures cannot mint a contract (no target model in a
    # fixture tree). A sandbox promote SKIPS the check loudly.
    if [ "${UP_TARGET:-}" = "sandbox" ]; then
        _log "config-compat: sandbox target — entry check skipped (D-2 scope: the gate guards demo/live installs)"
        return 0
    fi
    checker="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/../../daemon/plugin_subsystem/config_compat_check.py"
    [ -f "$checker" ] || checker="${REPO_ROOT:-}/daemon/plugin_subsystem/config_compat_check.py"
    if [ ! -f "$checker" ]; then
        _freshness_refuse config-compat-unrunnable \
            "config-compat checker module not found (looked next to lib.sh and at ${REPO_ROOT:-?}/daemon/plugin_subsystem/config_compat_check.py). " \
            "The gate refuses FAIL-CLOSED when it cannot evaluate (D-2). " \
            "Remedy: run promote from the repo checkout (scripts/upgrade/promote.sh) or re-stage with a stage.sh that mints the config-compat contract."
    fi
    if [ -n "${CONFIG_COMPAT_PYTHON:-}" ]; then
        py="$CONFIG_COMPAT_PYTHON"
    elif [ -x "${REPO_ROOT:-}/.venv/bin/python" ]; then
        py="${REPO_ROOT}/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="python3"
    else
        _freshness_refuse config-compat-unrunnable \
            "config-compat check cannot run: no python interpreter found (CONFIG_COMPAT_PYTHON unset, no .venv/bin/python, no python3). " \
            "The gate refuses FAIL-CLOSED when it cannot evaluate (D-2)."
    fi
    contract=""
    # D-6 leg 1 — TARGET-PAYLOAD (FIRST-AUTHORITATIVE, see the block comment
    # above). Checked before every other rung: when promoting a staged
    # version the payload contract IS the target release's key-shape
    # contract; the detached executor's only resolvable leg.
    local payload_contract
    payload_contract="${INSTALL_DIR:-}/releases/${version}/daemon/plugin_subsystem/config_compat_contract.json"
    if [ -f "$payload_contract" ]; then
        contract="$payload_contract"
    fi
    local tree_root
    tree_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)"
    if [ -z "$contract" ] && [ -f "$tree_root/daemon/config.py" ] && [ -f "$checker" ] && [ -f "$tree_root/daemon/plugin_subsystem/config_compat_contract.py" ]; then
        # TREE CONTEXT: mint fresh from the target model (always current).
        cfg_out="$(mktemp -t cc.XXXXXX 2>/dev/null || printf '/tmp/cc.%s.json' "$$")"
        if "$py" "$tree_root/daemon/plugin_subsystem/config_compat_contract.py" \
            --repo-root "$tree_root" > "$cfg_out" 2>/dev/null && [ -s "$cfg_out" ]; then
            contract="$cfg_out"
        else
            rm -f "$cfg_out" 2>/dev/null
            _warn "config-compat: fresh contract mint FAILED in tree context — falling through to the staged contract rung"
        fi
    fi
    if [ -z "$contract" ]; then
        contract="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/../../daemon/plugin_subsystem/config_compat_contract.json"
        [ -f "$contract" ] || contract="${REPO_ROOT:-}/daemon/plugin_subsystem/config_compat_contract.json"
        if [ ! -f "$contract" ]; then
            _freshness_refuse config-compat-unrunnable \
                "config-compat contract not found (looked next to lib.sh and at ${REPO_ROOT:-?}/daemon/plugin_subsystem/config_compat_contract.json). " \
                "The TARGET release's config key-shape is unknown — the gate refuses FAIL-CLOSED (D-2; a release whose model rejects the install config crash-loops AFTER the flip). " \
                "Remedy: re-stage the release with a contract-minting stage.sh (the contract is minted from the target tree at stage time), or pin a compatible release."
        fi
    fi
    if [ ! -f "${INSTALL_DIR:-}/config.yaml" ]; then
        _freshness_refuse config-compat-unrunnable \
            "install config.yaml not found at ${INSTALL_DIR:-?}/config.yaml — the daemon refuses to boot without it, so the flip would crash-loop. " \
            "Remedy: restore config.yaml (stage/config backups) before promoting."
    fi
    local out
    out="$("$py" "$checker" "$contract" "${INSTALL_DIR}/config.yaml" 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        _log "config-compat: $(printf '%s' "$out" | tail -n 1)"
        return 0
    fi
    local code detail
    code="$(printf '%s\n' "$out" | sed -n 's/.*code=\([a-z0-9-]*\).*/\1/p' | head -n 1)"
    detail="$(printf '%s\n' "$out" | sed -n 's/.*detail=//p' | head -n 1)"
    case "$code" in
        config-incompatible)
            _freshness_refuse config-incompatible \
                "config.yaml incompatible with $VERSION — migrate config or pin a compatible release. " \
                "Refused at ENTRY (before stop/flip; D-2): $detail"
            ;;
        *)
            _freshness_refuse config-compat-unrunnable \
                "config-compat check could not evaluate (rc=$rc): $(printf '%s\n' "$out" | head -n 4 | tr '\n' ' ') " \
                "The gate refuses FAIL-CLOSED when it cannot evaluate (D-2)."
            ;;
    esac
    return 0  # _freshness_refuse exits; never reached
}

# ═══════════════ Plugin-staleness promote predicate (slice ⑥) ═══════════════
#
# promote_plugin_staleness_check — REC §1.2 component 13
# (promote_staleness_check): pin age > N days OR unresolved divergence
# ⇒ refuse; unowned drift alarms escalate to the same refusal after
# escalation days (CON §5: "unowned for escalation days ⇒ block
# promote"). Wired into promote.sh preflight (before integrity, before
# any mutation — the same fail-closed family as the freshness guards).
#
# DATA SOURCE: the plugin tree under $REPO_ROOT/plugins (each dir with
# a MANIFEST.yaml): the predicate consumes the sync-runner's trail
# (sync_trail.jsonl — the sync_result directly, per the ③ probe doc)
# + the manifest's divergence-register statuses. OFFLINE — no git, no
# DB, no daemon. A repo with NO plugins/ dir passes trivially (the
# gate must not spuriously block plugin-less promotes).
#
# TRIAGE OUTCOMES (default changed at allow-stale flip 2026-10-07 —
# stale pins are the steady state, NOT a promote-blocking anomaly):
#   - predicate FRESH ⇒ proceed (rc=0).
#   - predicate STALE ⇒ DEFAULT: journal `plugin_staleness_observed`
#     + PROCEED (rc=0). STRICT (PROMOTE_STRICT_STALENESS=1 env or
#     --block-on-stale argv, opt-in): refuse exit 78 (slice-⑥
#     blocking restored — journaled as a `refusal` event with the
#     predicate's code token). OVERRIDE (PROMOTE_STALENESS_OVERRIDE=1
#     env or --allow-stale-plugins argv, audit-continuity back-compat):
#     journal `plugin_staleness_override` + PROCEED. STRICT wins over
#     OVERRIDE on conflict (strict is the "loud failure" choice).
#   - predicate UNEVALUABLE (no interpreter, no module, predicate
#     crash) ⇒ refuse exit 78 unless overridden (fail-closed — unknown
#     tooling is LOUD; stale pins are the steady state).
#
# OVERRIDE / STRICT (argv-only, mirrors --allow-stale-stage argv-only
# discipline): --allow-stale-plugins sets PROMOTE_STALENESS_OVERRIDE=1;
# --block-on-stale sets PROMOTE_STRICT_STALENESS=1. Every override and
# every observed event is JOURNALED on the install dir (reason token +
# operator_accepted). The `plugin_staleness_observed` event is
# JOURNAL-ONLY — explicitly NOT mapped in
# daemon/tools/upgrade_journal.py:ALERT_KIND_BY_EVENT (stale is no
# longer a refusal/rollback/halt-class event). The only SSE-mapped kinds
# the gate may emit remain halt/refusal/rollback/sweep_rollback/
# quarantine.
#
# IMPLEMENTATION NOTE: the predicate is a stdlib+PyYAML-only Python
# module (daemon/plugin_subsystem/promote_staleness.py) invoked by
# FILE PATH (not ``-m``) so promote-time needs no package import and
# no venv: the first of $PLUGIN_STALENESS_PYTHON / $REPO_ROOT/.venv/
# bin/python / python3 / python that executes wins. A python that
# cannot run the predicate refuses FAIL-CLOSED (plugin-staleness-
# unreadable) unless overridden — an unevaluable gate is a refused
# gate, never a silent pass.
#
# Exit-code contract of THIS function: returns 0 = fresh/observed/
# overridden; every refusal path (strict refuse; unevaluable refuse)
# exits 78 itself (the promote_entry_check caller convention — L11
# tidier).

_plugin_staleness_journal_override() {
    local reason="$1" detail="$2"
    if [ -d "${INSTALL_DIR:-}" ]; then
        journal_init >/dev/null 2>&1 || true
    fi
    journal_history_append "plugin_staleness_override" \
        "$detail (reason=$reason operator_accepted=true)" \
        >/dev/null 2>&1 \
        || _warn "plugin-staleness override journal append FAILED (best-effort) — override is taken but the audit trail is incomplete; reason=$reason"
}

# _plugin_staleness_journal_observed <plugin> <reasons_concat> — best-
# effort journal append for a STALE verdict accepted under the new
# default (allow-stale flip 2026-10-07 — stale pins are the steady
# state, NOT a promote-blocking anomaly; the event is JOURNALED for
# audit but the promote PROCEEDS unless strict mode opts back in).
# Idempotent and unlocked (same durable-landing discipline as
# _plugin_staleness_journal_override: journal_init first, then plain
# journal_history_append). The new event kind
# `plugin_staleness_observed` is journal-ONLY — explicitly NOT mapped
# in daemon/tools/upgrade_journal.py:ALERT_KIND_BY_EVENT (stale is no
# longer a refusal/rollback/halt-class event). STRICT refusals and
# UNEVALUABLE refusals still flow through _freshness_refuse → `refusal`
# (SSE-mapped).
_plugin_staleness_journal_observed() {
    local plugin="$1" reasons="$2"
    if [ -d "${INSTALL_DIR:-}" ]; then
        journal_init >/dev/null 2>&1 || true
    fi
    journal_history_append "plugin_staleness_observed" \
        "plugin=$plugin reasons=$reasons" \
        >/dev/null 2>&1 \
        || _warn "plugin-staleness observed journal append FAILED (best-effort) — observed is taken but the audit trail is incomplete; plugin=$plugin"
}

promote_plugin_staleness_check() {
    local plugins_root="${PLUGIN_STALENESS_PLUGINS_ROOT:-}"
    if [ -z "$plugins_root" ]; then
        local repo_root_guess="${REPO_ROOT:-}"
        if [ -z "$repo_root_guess" ]; then
            repo_root_guess="$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)" || repo_root_guess=""
        fi
        [ -z "$repo_root_guess" ] && repo_root_guess="$PWD"
        plugins_root="$repo_root_guess/plugins"
    fi
    # No plugins tree ⇒ nothing to check; a fresh-state promote on a
    # plugin-less repo must NOT block (the gate is plugin-scoped).
    if [ ! -d "$plugins_root" ]; then
        _log "plugin-staleness: no plugins tree at $plugins_root — gate passes (nothing to check)"
        return 0
    fi

    # Collect plugin dirs carrying a manifest (fail-closed on unreadable
    # manifests is the PREDICATE's job — manifest-unreadable token).
    local plugin_dirs=""
    local d
    for d in "$plugins_root"/*/; do
        [ -d "$d" ] || continue
        if [ -f "${d}MANIFEST.yaml" ]; then
            plugin_dirs="$plugin_dirs ${d%/}"
        fi
    done
    if [ -z "$plugin_dirs" ]; then
        _log "plugin-staleness: no manifest-bearing plugin dirs under $plugins_root — gate passes"
        return 0
    fi

    # Resolve the interpreter (see IMPLEMENTATION NOTE above).
    local py="" cand
    if [ -n "${PLUGIN_STALENESS_PYTHON:-}" ] && [ -x "${PLUGIN_STALENESS_PYTHON}" ]; then
        py="$PLUGIN_STALENESS_PYTHON"
    fi
    if [ -z "$py" ]; then
        for cand in \
            "${REPO_ROOT:-/nonexistent}/.venv/bin/python" \
            "$(command -v python3 2>/dev/null || true)" \
            "$(command -v python 2>/dev/null || true)"
        do
            if [ -n "$cand" ] && [ -x "$cand" ]; then
                if "$cand" -c "import yaml" >/dev/null 2>&1; then
                    py="$cand"
                    break
                fi
            fi
        done
    fi
    if [ -z "$py" ]; then
        # Fail-closed: an unevaluable gate refuses (overridable).
        local detail="no python with PyYAML available (tried PLUGIN_STALENESS_PYTHON, $REPO_ROOT/.venv/bin/python, python3, python)"
        if [ "${PROMOTE_STALENESS_OVERRIDE:-0}" = "1" ]; then
            _warn "PLUGIN-STALENESS OVERRIDE: predicate cannot run — proceeding (operator accepted; journaled)"
            _plugin_staleness_journal_override plugin-staleness-unreadable \
                "override: predicate unevaluable ($detail)"
            return 0
        fi
        _freshness_refuse plugin-staleness-unreadable \
            "plugin-staleness check cannot run: $detail. " \
            "The gate refuses FAIL-CLOSED when it cannot evaluate (a silent pass would make staleness invisible — the exact failure this predicate exists to kill). " \
            "Remedies, in order: " \
            "  1) run promote from a session with python3 + PyYAML on PATH " \
            "  2) set PLUGIN_STALENESS_PYTHON=<interpreter with PyYAML> " \
            "  3) pass --allow-stale-plugins to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi

    local pred="$(
        cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd
    )"
    pred="$pred/../../daemon/plugin_subsystem/promote_staleness.py"
    if [ ! -f "$pred" ]; then
        # SCRIPT_DIR-based resolution failed (sourced from elsewhere);
        # fall back to the REPO_ROOT-relative location.
        pred="${REPO_ROOT:-}/daemon/plugin_subsystem/promote_staleness.py"
    fi
    if [ ! -f "$pred" ]; then
        if [ "${PROMOTE_STALENESS_OVERRIDE:-0}" = "1" ]; then
            _warn "PLUGIN-STALENESS OVERRIDE: predicate module not found — proceeding (operator accepted; journaled)"
            _plugin_staleness_journal_override plugin-staleness-unreadable \
                "override: predicate module not found (looked next to lib.sh and at $REPO_ROOT/daemon/plugin_subsystem/)"
            return 0
        fi
        _freshness_refuse plugin-staleness-unreadable \
            "plugin-staleness predicate module not found (looked next to lib.sh and at ${REPO_ROOT:-?}/daemon/plugin_subsystem/promote_staleness.py). " \
            "The gate refuses FAIL-CLOSED when it cannot evaluate. " \
            "Remedy: run promote from the repo checkout (scripts/upgrade/promote.sh), or pass --allow-stale-plugins to override (JOURNALED)."
    fi

    local pdir out rc code
    for pdir in $plugin_dirs; do
        out="$("$py" "$pred" "$pdir" 2>&1)"
        rc=$?
        if [ "$rc" -eq 0 ]; then
            _log "plugin-staleness: $(printf '%s' "$out" | tail -n 1)"
            continue
        fi
        # First reported token (the predicate prints code=<token> per
        # reason); a crash (rc not in {0,3}) maps to the unreadable
        # token so the journal taxonomy stays greppable.
        code="$(printf '%s\n' "$out" | sed -n 's/.*code=\([a-z0-9-]*\).*/\1/p' | head -n 1)"
        if [ "$rc" -ne 3 ] || [ -z "$code" ]; then
            code="plugin-staleness-unreadable"
        fi
        if [ "${PROMOTE_STRICT_STALENESS:-0}" = "1" ]; then
            # Strict mode (opt-in to slice-⑥ blocking, 2026-10-07 flip
            # flipped the DEFAULT — strict is the explicit "loud failure"
            # choice now). The OVERRIDE flag does NOT bypass strict:
            # strict and override are orthogonal, strict wins on
            # conflict.
            _freshness_refuse "$code" \
                "promote refused (strict staleness mode): plugin $pdir failed the plugin-staleness predicate (rc=$rc): $(printf '%s\n' "$out" | head -n 8 | tr '\n' ' ') " \
                "Strict staleness is an OPT-IN to the slice-⑥ refuse-on-stale behavior (PROMOTE_STRICT_STALENESS=1 env or --block-on-stale argv). " \
                "Remedies, in order: " \
                "  1) fix the named condition (refresh the pin via a real sync; land the divergence disposition; assign alarm_owner) " \
                "  2) drop the strict env/argv to fall back to the default (stale observed + journaled, promote proceeds)"
            # _freshness_refuse exits 78 — never reached.
        elif [ "${PROMOTE_STALENESS_OVERRIDE:-0}" = "1" ]; then
            _warn "PLUGIN-STALENESS OVERRIDE: $pdir is stale ($code) — proceeding (operator accepted; journaled)"
            _plugin_staleness_journal_override "$code" \
                "override: $pdir failed the plugin-staleness predicate (rc=$rc): $(printf '%s\n' "$out" | head -n 8 | tr '\n' ' ')"
            continue
        fi
        # DEFAULT (allow-stale flip 2026-10-07): stale pins are the
        # steady state. The predicate's verdict is JOURNALED for audit
        # via `plugin_staleness_observed` and the promote PROCEEDS
        # (return 0 at the function end). STRICT (above) opts back
        # into the slice-⑥ blocking. OVERRIDE (above) is the explicit
        # audit-continuity back-compat — it journaled the OLD
        # `plugin_staleness_override` event on the install dir.
        _warn "PLUGIN-STALENESS OBSERVED: $pdir is stale ($code) — proceeding (default; journaled for audit)"
        _plugin_staleness_journal_observed "$pdir" \
            "$(printf '%s\n' "$out" | tr '\n' '|')"
    done
    return 0
}


# _verify_artifact_provenance <artifact_abs_path> — verify provenance matches
# the current tree. Refuses (exit 78) with a distinct reason token unless
# STAGE_FRESHNESS_OVERRIDE=1. Token + override journaling in one place so
# the operator can grep `reason=...` on the install dir's state.json to
# audit every override. Tip-identity (non-tip-tree) is checked separately
# at the stage.sh entry point, before this helper runs.
#
# FAIL-CLOSED FIELD CONTRACT (C1/C2 fix, cn 0472b31f): a sidecar that
# EXISTS must carry ALL THREE load-bearing fields — git_head (7-40 hex),
# git_dirty (exactly true/True/1 or false/False/0), artifact_sha256
# (64 hex) — each syntactically valid. ANY missing/empty/unrecognized
# value refuses provenance-malformed. No value skips a check, ever: the
# old `[ -n "$field" ] &&` guards are GONE — an empty field was a silent
# skip, which is exactly the trap family this guard exists to kill.
#
# Refusal token vocabulary (the verify helper emits one of):
#   provenance-missing, provenance-malformed, stale-provenance,
#   dirty-build, provenance-hash-mismatch
_verify_artifact_provenance() {
    local art="$1" prov json git_head git_head_short git_dirty art_sha current_sha
    local dirty_flag="" malformed=""
    prov="$(_provenance_path "$art")"
    if [ ! -f "$prov" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: provenance missing for $art — proceeding (operator accepted; journaled on install dir)"
            _stage_freshness_journal_override provenance-missing \
                "override: $art has no .build-provenance.json sidecar (looked for $prov)"
            return 0
        fi
        _freshness_refuse provenance-missing \
            "stale-artifact stage refused: no build provenance for $art (looked for $prov). " \
            "Blind reuse of prebuilt artifacts is structurally impossible (cn 0472b31f trap family). " \
            "Remedies, in order: " \
            "  1) rebuild: 'rm -rf dist/ frontend/dist/' and re-run stage (build path writes provenance) " \
            "  2) for --skip-build fixtures: the provided artifact must carry a .build-provenance.json sidecar; see _provenance_write in scripts/upgrade/lib.sh " \
            "  3) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi
    # Read via the shared reader (single path resolution for writer and
    # verifier). _PROVENANCE_READ_STATUS is the dispatch: absent/unreadable
    # → provenance-missing; malformed (empty / torn / quote-imbalance /
    # laundering signature — see _provenance_read + _provenance_sidecar_ok)
    # → provenance-malformed; ok → fall through to the shape gate.
    #
    # Call _provenance_read DIRECTLY (no command substitution) and capture
    # stdout via a temp file. The _PROVENANCE_READ_STATUS /
    # _PROVENANCE_READ_DETAIL globals are set by _provenance_read IN the
    # caller's scope this way — a previous `json="$(...)"` subshell
    # silently dropped the mutations and the case dispatch below never
    # fired (review round 3 d-1: the 4-state routing was DEAD CODE; the
    # shape-gate "missing" message was reaching operators for the
    # laundering class). rc 0 means the sidecar passed the sidecar_ok
    # gate (status=ok, content on stdout); rc 1 means one of the three
    # failure states — read the globals to learn which.
    local _pr_tmp _pr_status _pr_detail _pr_json=""
    _pr_tmp="$(mktemp -t ens-prov-read.XXXXXX)"
    if _provenance_read "$art" > "$_pr_tmp" 2>/dev/null; then
        _pr_status="ok"
        _pr_json="$(cat "$_pr_tmp")"
    else
        _pr_status="$_PROVENANCE_READ_STATUS"
        _pr_detail="$_PROVENANCE_READ_DETAIL"
    fi
    rm -f "$_pr_tmp"
    case "$_pr_status" in
        absent)
            _freshness_refuse provenance-missing \
                "stale-artifact stage refused: no build provenance for $art (looked for $prov). " \
                "Blind reuse of prebuilt artifacts is structurally impossible (cn 0472b31f trap family). " \
                "Remedies, in order: " \
                "  1) rebuild: 'rm -rf dist/ frontend/dist/' and re-run stage (build path writes provenance) " \
                "  2) for --skip-build fixtures: the provided artifact must carry a .build-provenance.json sidecar; see _provenance_write in scripts/upgrade/lib.sh " \
                "  3) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
            ;;
        unreadable)
            _freshness_refuse provenance-missing \
                "stale-artifact stage refused: provenance sidecar $prov exists but is unreadable. " \
                "A sidecar that exists must carry all three load-bearing fields (git_head, git_dirty, artifact_sha256). " \
                "Remedies, in order: " \
                "  1) rebuild: 'rm -rf dist/' and re-run stage (the build path rewrites the sidecar atomically) " \
                "  2) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
            ;;
        malformed)
            _freshness_refuse provenance-malformed \
                "stale-artifact stage refused: provenance sidecar for $art is MALFORMED (${_pr_detail}). " \
                "A sidecar that exists must carry all three load-bearing fields, syntactically valid, AND be well-formed enough " \
                "that the field extractor can trust it (no quote/brace imbalance, no laundering signature — a decoy key in a " \
                "non-key field's value would otherwise win the textual first-occurrence race and let a dirty build pass as clean). " \
                "Remedies, in order: " \
                "  1) rebuild: 'rm -rf dist/' and re-run stage (the build path rewrites the sidecar atomically) " \
                "  2) if the sidecar was hand-maintained: fix the named issue (empty / unbalanced / duplicate key) " \
                "  3) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
            ;;
    esac
    # ok → _pr_json holds the validated content; fall through to the shape gate.
    # Field extraction uses the QUOTED-key form (_json_field_quoted): the
    # bare-substring _json_field would match the "git_head" INSIDE
    # "git_head_short" when the real "git_head" key is absent — turning a
    # provenance-malformed case into a bogus stale-provenance (found by
    # scenario viii-d). Anchoring on `"key":` defeats the aliasing.
    git_head="$(_json_field_quoted "$_pr_json" '"git_head":' 2>/dev/null)" || git_head=""
    git_head_short="$(_json_field_quoted "$_pr_json" '"git_head_short":' 2>/dev/null)" || git_head_short=""
    git_dirty="$(_json_field_quoted "$_pr_json" '"git_dirty":' 2>/dev/null)" || git_dirty=""
    art_sha="$(_json_field_quoted "$_pr_json" '"artifact_sha256":' 2>/dev/null)" || art_sha=""
    current_sha="$(_sha256 "$art")"
    # ── Shape gate (C2): every load-bearing field present + syntactically
    #    valid, BEFORE any comparison runs. Empty no longer means skip.
    if [ -z "$git_head" ]; then
        malformed="git_head missing/empty"
    elif [ -z "$git_dirty" ]; then
        malformed="git_dirty missing/empty"
    elif [ -z "$art_sha" ]; then
        malformed="artifact_sha256 missing/empty"
    elif ! printf '%s' "$git_head" | grep -Eq '^[0-9a-fA-F]{7,40}$'; then
        malformed="git_head not a git sha (got '$git_head')"
    elif ! dirty_flag="$(_provenance_dirty_canon "$git_dirty")"; then
        malformed="git_dirty not a recognized boolean true/True/1 or false/False/0 (got '$git_dirty')"
    elif ! printf '%s' "$art_sha" | grep -Eq '^[0-9a-fA-F]{64}$'; then
        malformed="artifact_sha256 not a sha256 digest (got '$art_sha')"
    elif [ -n "$git_head_short" ] && ! printf '%s' "$git_head_short" | grep -Eq '^[0-9a-fA-F]{7,40}$'; then
        # git_head_short is INFORMATIONAL (gates no comparison) but it is
        # operator-facing: garbage free text is a corruption signal, so a
        # PRESENT-and-garbage value refuses (fail-closed symmetry with
        # git_head). Absent/empty is tolerated — it is not load-bearing.
        malformed="git_head_short not a git sha (got '$git_head_short')"
    fi
    if [ -n "$malformed" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: provenance sidecar malformed for $art ($malformed) — proceeding (operator accepted; journaled)"
            _stage_freshness_journal_override provenance-malformed \
                "override: $art sidecar malformed ($malformed)"
            return 0
        fi
        _freshness_refuse provenance-malformed \
            "stale-artifact stage refused: provenance sidecar for $art is MALFORMED ($malformed). " \
            "A sidecar that exists must carry all three load-bearing fields, syntactically valid — " \
            "an unrecognized value must never silently skip its check (cn 0472b31f trap family). " \
            "Remedies, in order: " \
            "  1) rebuild: 'rm -rf dist/' and re-run stage (the build path rewrites the sidecar atomically) " \
            "  2) if the sidecar was hand-maintained: fix the named field to the documented shape (docs/runbooks/upgrade-drills.md §A.2) " \
            "  3) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi
    # ── Comparison checks. Every value here is GUARANTEED non-empty and
    #    shape-valid by the gate above — no `[ -n ]` guards, no skips.
    if [ "$git_head" != "$HEAD_SHA" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: provenance head mismatch for $art (built=$git_head current=$HEAD_SHA) — proceeding (operator accepted; journaled)"
            _stage_freshness_journal_override stale-provenance \
                "override: $art was built at $git_head but staging tree is at $HEAD_SHA"
            return 0
        fi
        _freshness_refuse stale-provenance \
            "stale-artifact stage refused: $art was built at a different commit " \
            "(built at ${git_head:0:12}, current HEAD=$HEAD_SHA). " \
            "Reuse of prebuilt artifacts from a different tree is structurally impossible (cn 0472b31f trap family). " \
            "Remedies, in order: " \
            "  1) rebuild from the current tree: 'rm -rf dist/' and re-run stage (writes provenance) " \
            "  2) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi
    if [ "$dirty_flag" = "true" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: $art was built on a dirty tree — proceeding (operator accepted; journaled)"
            _stage_freshness_journal_override dirty-build \
                "override: $art was built on a dirty tree (HEAD=$HEAD_SHA)"
            return 0
        fi
        _freshness_refuse dirty-build \
            "stale-artifact stage refused: $art was built on a dirty (uncommitted) tree. " \
            "Staging an artifact whose build includes uncommitted changes is unsafe: the staged binary " \
            "diverges from any tag on the tree, and an idempotent re-stage would re-build with different " \
            "outputs. Remedies, in order: " \
            "  1) commit the changes, rebuild, re-run stage " \
            "  2) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi
    if [ "$art_sha" != "$current_sha" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: $art hash mismatch (manifest=$art_sha actual=$current_sha) — proceeding (operator accepted; journaled)"
            _stage_freshness_journal_override provenance-hash-mismatch \
                "override: $art hash mismatch (manifest=$art_sha actual=$current_sha HEAD=$HEAD_SHA)"
            return 0
        fi
        _freshness_refuse provenance-hash-mismatch \
            "stale-artifact stage refused: $art hash doesn't match its build provenance. " \
            "The artifact was modified AFTER its build (manifest=$art_sha, actual=$current_sha). " \
            "Remedies, in order: " \
            "  1) rebuild: 'rm -rf dist/' and re-run stage (writes fresh provenance) " \
            "  2) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi
    return 0
}

# _verify_tip_identity — refuse if the staging tree is not at the
# integration tip. The tip SHA comes from _tip_sha (refs/remotes/origin/
# latest or refs/heads/latest — full refspecs only, never the bare-DWIM
# `latest` which could resolve a TAG named latest). Refuses with reason
# token non-tip-tree; STAGE_FRESHNESS_OVERRIDE unlocks + journals. The
# HEAD_SHA must be set by the caller (stage.sh computes it at entry so
# this helper is pure-readable).
#
# TWO MODES (documented in upgrade-drills.md §A.1):
#   ACTIVE  — a tip ref resolves and HEAD == tip: the gate is engaged and
#             PASSES the tree through to L2. No output.
#   REFUSE  — a tip ref resolves and HEAD != tip: exit 78 (or override).
#   WARN+SKIP — NO tip ref resolves (no origin/latest, no local latest —
#             e.g. a CI checkout or a fixture repo without integration
#             refs): the check is SKIPPED with a loud WARN and L2
#             (provenance) remains the sole gate. The skip is always
#             visible on stderr; it is never silent.
#
# The v0.16.13 payload case: tag v0.16.13 was placed at commit C; the
# actual fix commits landed AFTER C on the integration branch, so the
# tip has moved past C. Staging from C would produce a binary without
# the fixes. This check refuses that path unless the operator explicitly
# overrides (and the override is journaled on the install dir).
_verify_tip_identity() {
    local tip
    tip="$(_tip_sha 2>/dev/null || true)"
    if [ -z "$tip" ] || [ -z "$HEAD_SHA" ]; then
        # WARN+SKIP mode: no tip ref resolves (e.g. CI without origin, or
        # a fixture repo without integration refs). The check is skipped
        # LOUDLY — L2 (provenance) remains the sole gate in this mode.
        _warn "tip-identity check SKIPPED (WARN+SKIP mode): no integration tip ref resolves (HEAD=$HEAD_SHA tip=<unset>; looked for refs/remotes/origin/latest then refs/heads/latest). L2 provenance check remains the sole gate."
        return 0
    fi
    if [ "$tip" != "$HEAD_SHA" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: tip-identity mismatch (HEAD=$HEAD_SHA tip=$tip) — proceeding (operator accepted; journaled)"
            _stage_freshness_journal_override non-tip-tree \
                "override: staging tree at $HEAD_SHA but integration tip is at $tip"
            return 0
        fi
        # DATA-NOT-CODE (refusal-message quoting): every literal fragment of
        # this operator-facing text is SINGLE-QUOTED, so bash performs NO
        # expansion on it — the documented commands below (`git fetch origin`,
        # `git push origin <branch>:latest`) are LITERAL TEXT and can never be
        # parsed as shell code. The previous double-quoted form made bash
        # EXECUTE both commands while assembling the message: the backticked
        # regions were command substitutions, `git fetch origin` actually
        # fetched, and `git push origin <branch>:latest` was re-parsed by the
        # inner shell as `git push origin < branch > :latest` (an input
        # redirection from a file named `branch` plus a stdout redirection
        # that CREATED a file named `:latest` in the operator's CWD) — and the
        # command text VANISHED from the message the operator was shown.
        # Only the SHA fragment stays double-quoted: it interpolates
        # ${HEAD_SHA:0:12} / ${tip:0:12} (git object names). Bash never
        # re-scans an expansion result for substitutions, redirections or
        # globs, so that fragment is data by language guarantee, not by
        # convention. Keep every OTHER fragment single-quoted.
        _freshness_refuse non-tip-tree \
            'stale-artifact stage refused: staging tree is NOT at the integration tip ' \
            "(HEAD=${HEAD_SHA:0:12} tip=${tip:0:12}). " \
            'The tag at HEAD does not include fixes that landed on the tip after the tag was placed. ' \
            'This catches the v0.16.13 payload case: a tag placed on a non-tip commit, with the actual ' \
            'fixes landing later on the tip. Remedies, in order: ' \
            '  1) merge your work into the integration branch and re-tag at the tip, then re-run stage — ' \
            'or, if the local branch is ahead of origin: `git fetch origin` (a stale ' \
            'refs/remotes/origin/latest with fixes still landing there would be refreshed), ' \
            'or `git push origin <branch>:latest` first so the remote-tracking tip catches up, then re-check ' \
            '  2) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)'
    fi
    return 0
}

# integrity_verify <ver> — verify a staged release against its manifest.
# Exit 0 clean; exit 1 with the offending file(s) named on mismatch.
# Checks: manifest exists + readable; no .env inside; every checksummed
# component matches; tree manifests match per-file AND in aggregate.
integrity_verify() {
    local ver="$1" rd mp json rc=0
    rd="$INSTALL_DIR/releases/$ver"
    mp="$rd/manifest.json"
    if [ ! -f "$mp" ]; then
        _warn "integrity: $mp MISSING"
        return 1
    fi
    json="$(cat "$mp" 2>/dev/null)" || { _warn "integrity: $mp unreadable"; return 1; }
    _no_env_in_release "$rd" || rc=1

    # binary
    local want got
    want="$(_json_field "$json" "binary_sha256")"
    if [ -n "$want" ]; then
        got="$(_sha256 "$rd/ensemble-prod")"
        if [ "$want" != "$got" ]; then
            _warn "integrity MISMATCH: $ver/ensemble-prod (want ${want:0:12}… got ${got:0:12}…)"
            rc=1
        fi
    else
        _warn "integrity: manifest missing binary_sha256"
        rc=1
    fi
    # launcher
    want="$(_json_field "$json" "launcher_sha256")"
    if [ -n "$want" ]; then
        got="$(_sha256 "$rd/launcher.sh")"
        if [ "$want" != "$got" ]; then
            _warn "integrity MISMATCH: $ver/launcher.sh (want ${want:0:12}… got ${got:0:12}…)"
            rc=1
        fi
    else
        _warn "integrity: manifest missing launcher_sha256"
        rc=1
    fi
    # config.yaml
    want="$(_json_field "$json" "config_sha256")"
    if [ -n "$want" ]; then
        got="$(_sha256 "$rd/config.yaml")"
        if [ "$want" != "$got" ]; then
            _warn "integrity MISMATCH: $ver/config.yaml (want ${want:0:12}… got ${got:0:12}…)"
            rc=1
        fi
    fi
    # agents/frontend trees — ONE walk per tree: actual manifest lines feed
    # both the aggregate hash AND the per-file pinpoint (diff against the
    # recorded map; names the tampered/missing/extra files). No per-file
    # forks beyond the single walk.
    local sub want_hash got_hash got_lines rec_lines actual_ps key wantf dpath dline
    local tree
    for tree in agents frontend; do
        got_lines="$(_tree_manifest "$rd/$tree" "")"
        want_hash="$(_json_field "$json" "${tree}_tree_sha256")"
        got_hash="$(_tree_hash_of_lines "$got_lines")"
        if [ -z "$want_hash" ] || [ "$want_hash" != "$got_hash" ]; then
            _warn "integrity MISMATCH: $ver/$tree tree (want ${want_hash:0:12}… got ${got_hash:0:12}…)"
            rc=1
        fi
        # per-file pinpoint: recorded map (flat — sed/tr conversion, no
        # per-char JSON walk) vs the actual walk ("sha  path" → "path sha");
        # diff pinpoints every delta (tampered, missing, unrecorded)
        actual_ps="$(printf '%s\n' "$got_lines" | awk -F '  ' '{print $2" "$1}')"
        while IFS= read -r dline; do
            [ -n "$dline" ] || continue
            case "$dline" in
                "< "*) dpath="${dline#< }"; dpath="${dpath%% *}"
                       _warn "integrity MISMATCH: $ver/$tree/$dpath (differs from manifest)"
                       rc=1 ;;
                "> "*) dpath="${dline#> }"; dpath="${dpath%% *}"
                       _warn "integrity MISMATCH: $ver/$tree/$dpath (on disk, not in manifest)"
                       rc=1 ;;
            esac
        done <<EOF
$(_manifest_map_lines "$json" "$tree" | diff - <(printf '%s\n' "$actual_ps" | sort) 2>/dev/null | grep -E '^[<>]' || true)
EOF
    done
    return "$rc"
}

# verify_current_release — resolve the `current` symlink + integrity-verify
# the release it points at. Exit 0 clean; 1 mismatch/unresolvable.
verify_current_release() {
    local cur_link="$INSTALL_DIR/current" target
    if [ ! -L "$cur_link" ]; then
        _warn "current symlink missing at $cur_link (no staged release promoted yet, or layout divergence)"
        return 1
    fi
    target="$(readlink "$cur_link")"
    target="${target##*/}"
    if [ ! -d "$INSTALL_DIR/releases/$target" ]; then
        _warn "current symlink points at missing release: $target"
        return 1
    fi
    integrity_verify "$target"
}

# ── Health gate probe (deploy.sh phase-5 budget) ─────────────────────────────
# _probe <path> <budget_s> <port> — body on stdout, 0 on success; nonzero
# when the budget expires without an HTTP response. 2s sleep between tries,
# curl --max-time 5 (same as deploy.sh).
#
# B2b hardening — both halves:
#   - WALL-CLOCK budget: deadline = start-epoch + budget. The old
#     iteration counter added only the 2s sleeps, never the curl time
#     (up to 5s/try), so a "60s" /livez gate could span ~210s wall and a
#     full gate run ~640s — past LOCK_STALE_S (300) and most of
#     SWEEP_STALE_S (600). The deadline bounds the gate to its documented
#     wall seconds (+ at most one in-flight curl of 5s).
#   - LOCK HEARTBEAT every iteration: gate loops used to be the one long
#     lock-held span with NO heartbeat, so a second launcher start could
#     stale-break a LIVE owner's lock mid-gate and double-rollback
#     underneath it. lock_heartbeat is ownership-guarded — this is a no-op
#     when the caller does not hold the pipeline lock.
# P3 / decision (e) — classified probe (D-P3.6) + bounded retry +
# boot-window grace (D-P3.7) + busy-amnesty extension (D-P3.8, C-8b).
#
# ─────────────────────────────────────────────────────────────────────
# The v0.20.0 boot-storm race (incident 2026-10-10 boot storm) taught
# us that the OLD `_probe` (curl -fsS --max-time 5) could not
# distinguish a transient 503 / connection-refused from a real red —
# the `-f` form suppresses the body on exit 22 (the 4xx/5xx branch),
# so a body-sniffing classifier saw empty-stdout and classified every
# HTTP error as red. The 1.5s transient window between pool-ready and
# gate-fire flipped the gate to halt even when the daemon was alive
# and about to serve a green. C-8a fix: status-code via -w + body to
# tmp; classification on the integer exit + http_code; bounded retry
# with 1s/2s/4s backoff (max 3); transient → "no green" (do not fail).
# ─────────────────────────────────────────────────────────────────────

# _probe_once_classified <path> <port> [body_out] [httpcode_out]
#
# Single fast classified fetch. Writes the response body to <body_out>
# (or discards if empty) and the http_code string to <httpcode_out>
# (or discards if empty). Sets globals on success:
#   _PROBE_STATUS — "green" | "red" | "transient"
#   _PROBE_BODY   — response body (empty on transient/connection errors)
#   _PROBE_CODE   — http_code string (empty on curl-only exit failures)
#
# Classification table (decisions.md (e) Concrete Contract + C-8a):
#   curl exit 0 + http_code 2xx         → green
#   curl exit 0 + http_code 503         → transient
#   curl exit 0 + http_code 5xx (other) → red
#   curl exit 0 + http_code 4xx         → red
#   curl exit 7 (connection refused)     → transient
#   curl exit 28 (timeout)               → transient
#   curl exit anything-else              → red
# Curl exit 22 (HTTP 4xx/5xx with -f) is no longer reachable — the
# invocation is curl -sS -o tmp -w '%{http_code}', so a non-2xx http
# code lands as exit 0 with the body in <tmp-body>. The body is
# preserved (per C-8a) so a future body-sniffing classifier can add
# nuanced red-vs-transient logic without re-architecting the probe.
#
# BOOT-GRACE HOOK: when a boot-grace window is ACTIVE (the
# _boot_grace_active function returns 0), transient results are
# silenced (do not count as red, do not count as green). Red still
# counts. Green still counts. This is the 60s post-restart window
# (D-P3.7) that auto-extends past transient storms but ends on the
# earliest of (a) elapsed OR (b) 3 consecutive non-transient greens.
#
# BUSY-AMNESTY HOOK: when a busy-amnesty applies (the
# _busy_amnesty_granted function returns 0 for a busy-but-alive
# daemon — inflight_turns 1..BUSY_INFLIGHT_BOUND), a transient that
# would otherwise be "no green" advances the consecutive-green
# counter as a SOFT-GREEN (the daemon is up; the gate is just under
# load). This is the asymmetry-closer between readyz (already has
# busy-amnesty) and livez/soak (now extended).
#
# Return: 0 on green, 1 on red, 2 on transient (the bash convention
# for the three-state classification).
_probe_once_classified() {
    local path="$1" port="$2" tmp_body http_code curl_rc
    tmp_body="$(mktemp -t probe.XXXXXX 2>/dev/null || printf '/tmp/probe.%s.body' "$$")"
    # C-8a: the load-bearing change. curl -sS -o tmp -w '%{http_code}'
    # (NOT curl -fsS with body-sniff — exit 22 suppresses body, so a
    # body-sniffing classifier sees empty-stdout and classifies every
    # HTTP error as red, defeating the 503-transient path entirely).
    #
    # C1 (R1 remediation, council 2026-10-12): curl's exit code is
    # captured AT THE ASSIGNMENT (`rc=$?` on the next line — the
    # assignment's exit status IS the command substitution's status).
    # The prior shape read `${PIPESTATUS[0]}` several commands LATER —
    # PIPESTATUS is reset by every intervening command, so the read saw
    # the rc of the last assignment (always 0) and every connection-
    # level failure (refused/timeout — the v0.20.0 boot-storm classes)
    # fell into the red bucket. Empirically proven twice by the council.
    # Also: the old `|| http_code=""` handler DISCARDED curl's "000"
    # no-response marker; the empty/000 shape is now classified from
    # the captured curl_rc instead.
    curl_rc=0
    http_code="$(curl -sS -o "$tmp_body" -w '%{http_code}' --max-time 5 \
        "http://localhost:$port$path" 2>/dev/null)"
    curl_rc=$?
    # capture classification globals
    _PROBE_BODY=""
    _PROBE_CODE="$http_code"
    case "$http_code" in
        ""|000)
            # curl-level failure: no HTTP response landed (or the
            # 000 no-response marker). The captured rc separates the
            # known transient classes from unknown hard failures.
            case "$curl_rc" in
                7) _PROBE_STATUS="transient" ;;   # connection refused
                28) _PROBE_STATUS="transient" ;;  # timeout
                *) _PROBE_STATUS="red" ;;         # unknown curl-level failure: fail-closed
            esac
            ;;
        2*) _PROBE_STATUS="green" ;;
        503) _PROBE_STATUS="transient" ;;
        5*) _PROBE_STATUS="red" ;;
        4*) _PROBE_STATUS="red" ;;
        *) _PROBE_STATUS="red" ;;
    esac
    # Read body for callers that need it (e.g. inflight_turns extraction
    # for the busy-amnesty reader; gate_version for the version field).
    # Skip the read for empty/connection-error results (no body landed).
    #
    # A1 (soak-gate-mcp-warmup): the body is ALSO read on the TRANSIENT
    # path — the soak's class-gated grace is published in the readyz
    # 503 body (detail.grace / detail.leak_signal /
    # detail.component_classes), and the gate can only classify a
    # transient if it can READ the body. The green read is unchanged.
    if [ -s "$tmp_body" ] && { [ "$_PROBE_STATUS" = "green" ] || [ "$_PROBE_STATUS" = "transient" ]; }; then
        _PROBE_BODY="$(cat "$tmp_body" 2>/dev/null)"
    fi
    rm -f "$tmp_body" 2>/dev/null
    # BOOT-GRACE: silence transients during the 60s post-restart window.
    # Red still counts. Green still counts. The 503/path-7/28 storm
    # observed in the v0.20.0 boot storm is the exact class silenced
    # here — pool still warming, the gate gets the quiet window.
    # W3 (council 2026-10-12): a SUPPRESSED transient is "ignored" —
    # neither green nor red. The old shape returned 0 (pass), which
    # callers could mistake for a green; the return is now the
    # dedicated 3 (ignored) and _PROBE_STATUS carries the token so
    # display paths can still distinguish it. Consumers: _probe_with_retry
    # propagates 3; _probe treats 3 as no-green/no-fail (rides on);
    # gate_soak counts it as "no green" (the consecutive counter
    # resets — an ignored probe is not a green). Conservative mode
    # (W1) sets _PROBE_SUPPRESSION_STRICT=1 and NEVER lands here —
    # v0.18-identical strictness.
    if [ "$_PROBE_STATUS" = "transient" ] \
       && [ "${_PROBE_SUPPRESSION_STRICT:-0}" != "1" ] \
       && _boot_grace_active; then
        _PROBE_STATUS="boot-grace-suppressed"
        return 3
    fi
    # BUSY-AMNESTY: a transient that is NOT in boot-grace still
    # advances consecutive-green as a soft-green when the busy
    # predicate grants amnesty. The busy-amnesty is read from the
    # existing readyz detail.inflight_turns field via
    # _read_inflight_turns_bounded below (C-8b: NO new file, NO
    # new daemon-side writer). The asymmetry between readyz (has
    # amnesty today) and livez/soak (extended now) is closed here.
    # W1 (council): conservative mode sets _PROBE_SUPPRESSION_STRICT=1
    # — NO amnesty/suppression, v0.18-identical strictness (503 and
    # connection-level failures are red, not soft-greens).
    if [ "$_PROBE_STATUS" = "transient" ] \
       && [ "${_PROBE_SUPPRESSION_STRICT:-0}" != "1" ] \
       && _busy_amnesty_granted; then
        _PROBE_STATUS="busy-amnesty"
        return 0
    fi
    # C2 (council): the tail must implement the DOCUMENTED three-state
    # contract (header above: "0 on green, 1 on red, 2 on transient").
    # The prior shape returned 1 for transients, which the retry layer
    # reads as hard-red — making the 1s/2s/4s backoff and the phase
    # ride-out DEAD CODE (a single post-grace blip insta-failed the
    # gate). Transients now return 2; the retry loop and _probe's
    # phase-budget ride-out consume that state.
    case "$_PROBE_STATUS" in
        green) return 0 ;;
        red) return 1 ;;
        *) return 2 ;;   # transient (and any future soft class) — retryable
    esac
}

# _probe_with_retry <path> <port> <budget_s> — bounded retry inside
# per-phase budget (D-P3.6). 3 retries max, 1s/2s/4s backoff. After
# 3 retries: last=transient → "no green" (return 2; do not fail the
# phase); last=red → fail the phase (return 1). Heartbeats the lock
# between retries (D5/D-FA5.1) so a long retry never stale-breaks
# the pipeline lock.
#
# Returns: 0 on green, 1 on red, 2 on transient-exhausted. The caller
# (gate_livez / gate_readyz / gate_soak) interprets the three-state
# outcome.
_probe_with_retry() {
    local path="$1" port="$2" budget="$3"
    local deadline backoff=1 attempt=0 rc
    deadline=$(( $(_now_epoch) + budget ))
    while :; do
        lock_heartbeat
        if [ "$(_now_epoch)" -ge "$deadline" ]; then
            return 2
        fi
        _probe_once_classified "$path" "$port"
        rc=$?
        case "$rc" in
            0)
                # green (or a soft-green)
                case "$_PROBE_STATUS" in
                    green)
                        # Advance the boot-grace counter ONLY on real
                        # green (boot-grace-suppressed and busy-amnesty
                        # soft-greens do NOT count as a "non-transient
                        # green" — the D-P3.7 invariant is "3 consecutive
                        # non-transient greens" and a soft-green is
                        # explicitly a transient class). This keeps the
                        # 60s grace window from collapsing on
                        # 503/7/22/28 storms.
                        _boot_grace_record_green
                        return 0
                        ;;
                    busy-amnesty) return 0 ;;
                esac
                ;;
            1)
                # red — hard fail, do not retry
                _boot_grace_record_non_green
                return 1
                ;;
            3)
                # W3: boot-grace-suppressed (ignored) — neither green
                # nor red. Does NOT advance the green counter, does
                # NOT retry (the grace window is ending on its own),
                # does NOT fail. Propagate 3 so _probe keeps riding
                # the phase budget.
                return 3
                ;;
        esac
        # rc=2 — transient → bounded retry with backoff (also resets the
        # boot-grace counter — a transient is not a non-transient
        # green, so the consecutive-counting invariant holds).
        _boot_grace_record_non_green
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 3 ]; then
            return 2
        fi
        sleep "$backoff" 2>/dev/null
        backoff=$(( backoff * 2 ))
    done
    return 2
}

# _read_inflight_turns_bounded — read the EXISTING
# readiness.py:198/222 detail.inflight_turns field via a readyz
# curl (D-P3.8, C-8b). NO new /run/ensemble/inflight_turns file;
# NO new daemon-side counter writer; NO reuse of has_instance_busy
# (a different predicate: PENDING+RUNNING+PAUSED tasks vs readiness
# turns). The bound (≤ BUSY_INFLIGHT_BOUND) keeps amnesties
# genuinely narrow. Returns:
#   0  if inflight_turns in 1..BUSY_INFLIGHT_BOUND  (busy-but-alive)
#   1  if inflight_turns == 0, > bound, or unreadable (no amnesty)
_read_inflight_turns_bounded() {
    local body detail_field inflight
    body="$(curl -sS -o /dev/null -w '' --max-time 2 \
        "http://localhost:$PORT/readyz" 2>/dev/null)" || return 1
    # We need the body for inflight_turns extraction; do a second
    # bounded fetch. The two-fetch shape costs <1s typical and
    # keeps the busy-amnesty asymmetry close to "readyz has it
    # today" without breaking the budgeted loop in gate_livez /
    # gate_soak.
    body="$(curl -sS --max-time 2 \
        "http://localhost:$PORT/readyz" 2>/dev/null)" || return 1
    [ -n "$body" ] || return 1
    detail_field="$(_json_sub "$body" detail 2>/dev/null)" || return 1
    [ -n "$detail_field" ] || return 1
    inflight="$(_json_field "$detail_field" inflight_turns 2>/dev/null)" \
        || return 1
    case "$inflight" in
        ''|null) return 1 ;;
        *[!0-9]*) return 1 ;;
        0) return 1 ;;
    esac
    if [ "$inflight" -gt "$BUSY_INFLIGHT_BOUND" ]; then
        return 1
    fi
    return 0
}

# _busy_amnesty_granted — returns 0 (true) if the busy-amnesty
# predicate grants amnesty, 1 (false) otherwise. Default predicate:
# always 1 (no amnesty) so the gate stays strict by default; the
# gate functions call _read_inflight_turns_bounded and treat a
# non-error as a soft-green.
_busy_amnesty_granted() {
    _read_inflight_turns_bounded
}

# _boot_grace_active — returns 0 (grace is active, suppress
# transients) if the post-restart grace window is still in effect,
# 1 otherwise. The window starts on restart_via_launcher returning
# 0 (D-P3.7) and ends on earliest of (a) grace_s elapsed OR
# (b) 3 consecutive non-transient greens on livez. The window
# stops short of the 900s hard ceiling (the grace is a sub-budget
# within a single phase, not the ceremony).
#
# State is held in two globals set by _boot_grace_start and
# advanced by _boot_grace_record_green. Default: no grace active
# (the gate starts in strict mode and only enters grace after
# restart_via_launcher succeeds).
_BOOT_GRACE_START_EPOCH=""
_BOOT_GRACE_CONSEC_GREEN=0
_boot_grace_start() {
    _BOOT_GRACE_START_EPOCH="$(_now_epoch)"
    _BOOT_GRACE_CONSEC_GREEN=0
}
_boot_grace_record_green() {
    _BOOT_GRACE_CONSEC_GREEN=$(( _BOOT_GRACE_CONSEC_GREEN + 1 ))
}
_boot_grace_record_non_green() {
    _BOOT_GRACE_CONSEC_GREEN=0
}
_boot_grace_active() {
    # Not started → no grace
    [ -n "$_BOOT_GRACE_START_EPOCH" ] || return 1
    local now elapsed
    now="$(_now_epoch)"
    elapsed=$(( now - _BOOT_GRACE_START_EPOCH ))
    # (a) grace_s elapsed → no longer active
    if [ "$elapsed" -ge "$PROMOTE_BOOT_GRACE_S" ]; then
        return 1
    fi
    # (b) 3 consecutive non-transient greens on livez → no longer active
    if [ "$_BOOT_GRACE_CONSEC_GREEN" -ge 3 ]; then
        return 1
    fi
    return 0
}

# _probe <path> <budget_s> <port> — budgeted probe (D2) with
# classified transient handling (D-P3.6). Replaces the v0.18-era
# `curl -fsS` body-sniff approach: status code via -w, body to tmp,
# 3-state classification (green / red / transient), bounded retry
# inside the per-phase budget.
#
# Stdout: response body on green (gate_livez / gate_readyz consumers
# depend on this contract). Return: 0 on green, 1 on red or
# transient-exhausted (callers treat both as gate-fail; the
# difference is the warn message + whether auto-rollback fires).
#
# The boot-grace hook is silent (no INFO line) per the
# busy-but-alive semantic — a silenced transient is the daemon
# still warming, not a gate-fail.
_probe() {
    local path="$1" budget="$2" port="$3" deadline now body="" status rc remaining
    deadline=$(( $(_now_epoch) + budget ))
    lock_heartbeat
    while :; do
        # C2 (council): pass a RELATIVE budget (the remaining phase
        # slice) — the prior shape passed the ABSOLUTE deadline, which
        # _probe_with_retry added to _now_epoch again, producing a
        # retry deadline ~1.7e9 s in the future (retry loop could
        # never time out against its own budget).
        remaining=$(( deadline - $(_now_epoch) ))
        [ "$remaining" -lt 1 ] && remaining=1
        _probe_with_retry "$path" "$port" "$remaining" >/dev/null
        rc=$?
        case "$rc" in
            0)
                # green or soft-green (busy-amnesty)
                if [ "$_PROBE_STATUS" = "green" ]; then
                    # Echo body for the green case so gate_livez /
                    # gate_readyz callers receive the JSON response
                    if [ -n "$_PROBE_BODY" ]; then
                        printf '%s\n' "$_PROBE_BODY"
                    else
                        # Body was discarded (gate_livez uses /livez
                        # which returns the version + status — fetch
                        # the body once for the green case).
                        body="$(curl -sS --max-time 2 \
                            "http://localhost:$port$path" 2>/dev/null)" || body=""
                        [ -n "$body" ] && printf '%s\n' "$body"
                    fi
                fi
                # busy-amnesty: emit the captured body (a readyz
                # busy-but-alive still has a payload).
                if [ "$_PROBE_STATUS" = "busy-amnesty" ]; then
                    if [ -n "$_PROBE_BODY" ]; then
                        printf '%s\n' "$_PROBE_BODY"
                    fi
                fi
                return 0
                ;;
            3)
                # W3: boot-grace-suppressed — ignored: not a green,
                # not a fail. Keep riding the phase budget.
                ;;
            2)
                # C2: transient-exhausted batch — DO NOT fail the
                # phase. The approved design rides transients out
                # inside LIVEZ_BUDGET_S/READYZ_BUDGET_S; the gate
                # fails only when the PHASE budget is exhausted (or
                # on a hard red below).
                _warn "probe ${path}: transient — riding out inside the phase budget (${remaining}s left)"
                ;;
            1)
                _warn "probe ${path}: hard fail (red — non-transient error)"
                return 1
                ;;
        esac
        now="$(_now_epoch)"
        [ "$now" -lt "$deadline" ] || break
        lock_heartbeat
        sleep 2
    done
    # C2: phase budget exhausted without green — the transient
    # ride-out ceiling. Return 2 (transient-exhausted-at-deadline)
    # so callers can distinguish it from a hard red; both fail the
    # gate (fail-closed), but the reason line says WHY.
    _warn "probe ${path}: phase budget ${budget}s exhausted without green — gate fails (transient ride-out ceiling)"
    return 2
}

# _probe_once <path> <port> — single fast fetch (status.sh display; no budget).
# P3 (decision (e)): classified shape (D-P3.6). Body to stdout on green;
# otherwise nothing. Status.sh consumers tolerate empty body (they just
# display "no response" on rc != 0).
_probe_once() {
    _probe_once_classified "$1" "$2"
    case "$_PROBE_STATUS" in
        green|busy-amnesty|boot-grace-suppressed)
            [ -n "$_PROBE_BODY" ] && printf '%s\n' "$_PROBE_BODY"
            return 0
            ;;
        *) return 1 ;;
    esac
}

# _run_bounded <budget_s> [--] <cmd> [args...] — run a foreground command
# with a wall-clock SIGTERM→SIGKILL deadline (2.2b, upgrade-resilience
# 2026-10-07).
#
# Why NOT GNU `timeout(1)`: absent on BSD/macOS stock installs — the
# pipeline is BSD+GNU portable (P2.1 debt family). Background + sleep
# + kill is portable; bash builtin kill is a single syscall (no FS /
# IPC dependency), safe to ignore if the watchdog itself races.
#
# Return codes (modelled on GNU `timeout(1)` for cross-tool consistency):
#   0     → child exited cleanly within budget
#   !=0   → child exited nonzero within budget (the child's own rc)
#   124   → SIGTERM after TERM__1…_TERM_GRACE_S then SIGKILL fired
#           (timed out — caller distinguishes via `case "$rc" in 124)`)
#
# The function NEVER aborts the script on a 124 — caller-side MUST
# capture with `|| rc=$?` or `if _run_bounded ... ; then …` so a
# bounded timeout falls through (these scripts run with `set -e`;
# a bare `_run_bounded …` invocation would propagate the rc and
# terminate the script on timeout, which is the wrong shape — the
# recovery flow IS the fall-through). stdout/stderr is preserved
# via temp-file (no FD inheritance issues across the background
# fork).
#
# Implementation shape (mirror of launcher.sh:518 _js_run_bounded
# precedent — same algorithm, separate copy because the launcher
# is BOOT-path self-contained and does NOT source scripts/upgrade/lib.sh):
#   "$@" &
#   pid=$!
#   (
#       sleep "$timeout_s"
#       kill -TERM "$pid" 2>/dev/null
#       sleep TERM_GRACE_S
#       kill -KILL "$pid" 2>/dev/null
#   ) >/dev/null 2>&1 &
#   watcher=$!
#   if wait "$pid"; then rc=0; else rc=$?; fi
#   case "$rc" in 137|143) rc=124 ;; esac
#   kill -KILL "$watcher" 2>/dev/null || true
#   wait "$watcher" 2>/dev/null || true
#   return "$rc"
#
# Note: the watcher runs `sleep + kill TERM + sleep + kill KILL`. If
# the child finishes FAST, the watcher is still sleeping and gets
# SIGKILLed at the end — bash builtin kill is a single syscall, no
# orphan risk on the SIGKILLed watcher (it cannot have FDs held
# open by the watched child). Verified portable: bash 3.2 (macOS
# stock), bash 5.x (GNU/Linux). NOT dependent on GNU `timeout(1)`.
_run_bounded() {
    local timeout_s="$1"; shift
    [ "${1:-}" = "--" ] && shift
    local pid watcher rc=0 TERM_GRACE_S=5
    "$@" &
    pid=$!
    (
        sleep "$timeout_s" 2>/dev/null
        kill -TERM "$pid" 2>/dev/null
        sleep "$TERM_GRACE_S" 2>/dev/null
        kill -KILL "$pid" 2>/dev/null
    ) >/dev/null 2>&1 &
    watcher=$!
    if wait "$pid" 2>/dev/null; then
        rc=0
    else
        rc=$?
    fi
    # SIGKILL = 137 (128+9), SIGTERM = 143 (128+15) → collapse to 124
    # (GNU `timeout` convention).
    case "$rc" in
        137|143) rc=124 ;;
    esac
    kill -KILL "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    return "$rc"
}

# _bounded_rc_kind <rc> — the HONEST outcome class of a bounded-child rc, as
# the journal ROW token (`kind=`). Pure string function, no I/O, no side
# effects: the three probe call sites keep their own site label / unit /
# budget-knob name (T1, promote-handback-halt Phase 1 §4.3).
#
# WHY the taxonomy lives HERE and not in _run_bounded: _run_bounded is the
# house's portable replacement for GNU timeout(1) and its only legitimate
# remap is 137|143 → 124 (a killed child). Teaching it `systemctl` semantics
# would poison every other caller (stop_via_stop_script, _df_systemctl), so
# _run_bounded stays BYTE-UNCHANGED and the taxonomy is applied where the
# child rc is INTERPRETED (§4.1 of the plan).
#
#   124 → budget        (the watcher SIGTERM→SIGKILL'd the child at the budget)
#   else → nonzero_exit (a GENUINE command failure — including rc 3, which
#                        `systemctl is-active` returns for every inactive unit;
#                        the CALL SITE decides whether that deserves a journal
#                        row at all — §4.3 site 2 grants rc 3 an INFO line only)
_bounded_rc_kind() {
    case "${1:-}" in
        124) printf '%s' "budget" ;;
        *)   printf '%s' "nonzero_exit" ;;
    esac
}

# _bounded_rc_reason <rc> [<state-hint>] [<budget-descriptor>] — the ROW
# `reason=` payload for a bounded-child rc. Pure string function.
#   124 → "timeout(<budget-descriptor>)"   (byte-identical to legacy when the
#         caller passes e.g. SYSTEMCTL_PROBE_BUDGET_S=5)
#   else → "nonzero rc=<rc>" [+ " state=<state-hint>" when a hint is given]
# The caller supplies the `child: ` prefix (it owns the site label/unit), so
# the 124 arms can reproduce the legacy journal text character-for-character.
_bounded_rc_reason() {
    local __rc="${1:-}" __hint="${2:-}" __bdescr="${3:-}"
    case "$__rc" in
        124) printf 'timeout(%s)' "${__bdescr:-budget}" ;;
        *)
            if [ -n "$__hint" ]; then
                printf 'nonzero rc=%s state=%s' "$__rc" "$__hint"
            else
                printf 'nonzero rc=%s' "$__rc"
            fi
            ;;
    esac
}

# _bounded_state_hint <raw> — sanitize a probe's stdout token for journaling
# ([a-z-]{1,20}; empty → empty, the caller applies its own fallback literal).
# Keeps hostile/garbled probe output out of the single-line journal field.
_bounded_state_hint() {
    printf '%s' "${1:-}" | tr -cd 'a-z-' | cut -c1-20
}

# _resolve_wait_s_mirror <env_file> — PARENT-SIDE mirror of the child's
# WAIT_S resolution chain (scripts/stop-ensemble.sh:113 + 270-289 + 292-307).
# SYNC-GUARD: this function MUST stay byte-identical in semantics to the
# child's resolution — see the sync-guard comment at lib.sh:94+STOP_SCRIPT_BUDGET_S
# for the full invariant. The brief calls for the parent to derive
# STOP_SCRIPT_BUDGET_S from the SAME resolved WAIT_S the child uses; if
# the two resolvers drift (e.g. one updates the +10 offset or the
# floor/cap), the parent's derived budget silently under-covers the
# child's legitimate worst-case → premature SIGKILL → flip against a
# still-live daemon (the review finding this fix closes). The
# tests/test_bounded_subprocess_waits.sh sync-guard case D8 runs BOTH
# resolvers over the same fixtures and asserts identical output.
#
# Precedence (mirrors the child at stop-ensemble.sh:113, 292-307 + 270-289):
#   1. explicit $WAIT_S env (digits-only) — wins
#   2. malformed explicit $WAIT_S — fall back to DEFAULT_WAIT_S (NOT
#      to the .env read; this is a SUBTLE child quirk the mirror
#      preserves — a malformed operator config is treated as "no
#      config at all, use the safe default", not "fall through to
#      the .env read" which would silently honor an unrelated
#      DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS the operator may
#      have set for a different purpose).
#   3. no explicit $WAIT_S — call _resolve_wait_s (which reads the
#      env file, applies the +10 offset, clamps [FLOOR..CAP],
#      falls back to DEFAULT_WAIT_S on absent/malformed)
#
# Side note: $WAIT_S is the caller's env value (CLI / exported) — the
# SAME env the child sees (the parent fork-execs the child with
# PIPELINE_LOCK_HELD_BY_CALLER=1, but the rest of env inherits). A
# merely-set-but-empty $WAIT_S="" is NOT treated as explicit (it falls
# through to the .env read), exactly like the child.
DEFAULT_WAIT_S=70
WAIT_S_FLOOR=10
WAIT_S_CAP=600
_resolve_wait_s_mirror() {
    local env_file="$1" raw="" val=""
    # Precedence 1+2: explicit $WAIT_S in the caller's env
    # (digits-only → win; malformed → fall back to DEFAULT_WAIT_S
    # without consulting the .env file — mirrors the child).
    raw="${WAIT_S:-}"
    if [ -n "$raw" ]; then
        if printf '%s' "$raw" | grep -Eq '^[0-9]+$'; then
            printf '%s\n' "$raw"
            return 0
        fi
        # Malformed explicit — fall back to default, do NOT consult .env.
        printf '%s\n' "$DEFAULT_WAIT_S"
        return 0
    fi
    # Precedence 3: no explicit — read the .env file (sed-extract is
    # byte-identical to the child's _resolve_wait_s at
    # stop-ensemble.sh:270-289: same pattern, same handling of
    # optional `export` prefix, same trailing-CARRIAGE-RETURN strip,
    # same quote-strip, same digits-only validation, same +10 offset,
    # same [FLOOR..CAP] clamp, same fallback to DEFAULT_WAIT_S).
    if [ -n "$env_file" ] && [ -f "$env_file" ]; then
        raw="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS[[:space:]]*=[[:space:]]*//p' "$env_file" 2>/dev/null | head -1)"
        raw="${raw%$'\r'}"
        case "$raw" in
            \"*\") raw="${raw#\"}"; raw="${raw%\"}" ;;
            \'*\') raw="${raw#\'}"; raw="${raw%\'}" ;;
        esac
    fi
    if ! printf '%s' "$raw" | grep -Eq '^[0-9]+$'; then
        printf '%s\n' "$DEFAULT_WAIT_S"
        return 0
    fi
    val=$((raw + 10))
    [ "$val" -lt "$WAIT_S_FLOOR" ] && val="$WAIT_S_FLOOR"
    [ "$val" -gt "$WAIT_S_CAP" ] && val="$WAIT_S_CAP"
    printf '%s\n' "$val"
}

# _derive_parent_stop_budget [<env_file>] — DERIVES the parent
# (lib.sh) STOP_SCRIPT_BUDGET_S from the child's RESOLVED WAIT_S (and
# the other timeouts in the child's stop span), instead of a fixed
# 120s. Rationale (cite: integration review 2026-10-07, post-2.2b):
# the parent bounds the `bash "$stop_script" ...` fork-exec; the child
# (stop-ensemble.sh) can legitimately take its own site-2 stop bound +
# resolved WAIT_S + UNIT_KILL_GRACE_S + internal _run_bounded TERM
# grace to complete. A fixed parent budget of 120s under-covers
# operators with WAIT_S>120 → the parent SIGKILLs the child
# mid-graceful-wait → premature rc=0 fall-through → the flip may
# proceed against a still-live daemon. The invariant we establish:
# "parent budget ≥ child's legitimate worst-case runtime, always, by
# construction — no fixed-constant coupling left."
#
# Composition sum (child's legitimate worst-case, IN-ORDER):
#   1. child site-2 stop bound — the child's own STOP_SCRIPT_BUDGET_S
#      (env-shared with the parent; same env inherits across the
#      fork-exec). Default 120s.
#   2. resolved WAIT_S — the child's poll loop after systemctl stop
#      (stop-ensemble.sh:600-609: `while [ $WAITED -lt $WAIT_S ]`). The
#      child can poll for the full WAIT_S window if the unit takes the
#      full graceful timeout to drain. Resolved via
#      _resolve_wait_s_mirror (the parent's mirror of the child's
#      _resolve_wait_s; sync-guarded).
#   3. UNIT_KILL_GRACE_S — the child's re-verify poll after
#      `systemctl kill` SIGKILL escalation (stop-ensemble.sh:615-624).
#      Default 10s.
#   4. internal _run_bounded TERM-grace (5s) — the parent's helper
#      itself takes 5s between TERM and KILL on its OWN bound, but
#      since the parent bounds the WHOLE child fork-exec (not just the
#      child's site-2 stop), the child's internal _run_bounded TERM
#      grace is INSIDE the parent's bound. A hung child's site-2
#      _run_bounded could legitimately occupy 5s of TERM-grace before
#      KILL. Compose, don't double-count.
#   5. margin (+20s) — startup overhead (process spawn, sed-extract,
#      PID discovery), scheduler latency, and any clock skew between
#      the two fork-execs. The child itself uses a +10s margin
#      (stop-ensemble.sh:268: "Budget = graceful timeout + 10s margin");
#      we use +20s for the cross-process bound (a slightly wider
#      margin — the cross-process cost is higher than the
#      intra-process cost).
# Absolute floor: 120s — preserves the prior default at low WAIT_S
# (no regression for the 70s-default case in the happy path; the
# derived default at WAIT_S=70 is 120+70+10+5+20=225, which is MORE
# generous than the prior 120, but still a tight bound — a healthy
# stop completes in <2s).
#
# <env_file> optional override for the .env path (defaults to
# $INSTALL_DIR/.env). The test pack passes an explicit path when
# the caller is a test harness; production callers omit it and let
# the helper pick up $INSTALL_DIR.
#
# Operator override: STOP_SCRIPT_BUDGET_S explicit env → use that value
# verbatim (the call site never sees the derivation). This preserves
# the env-overridable knob for sandbox drills + tests.
#
# SYNC-GUARD: this default and the child's STOP_SCRIPT_BUDGET_S at
# stop-ensemble.sh:362 must move TOGETHER. The child's value is
# 120-default; the parent's derivation picks up the SAME env (child
# inherits the parent's env across the fork-exec). If you change the
# child's default, change the child_site2 default here. Likewise,
# _resolve_wait_s_mirror above MUST stay byte-identical to
# stop-ensemble.sh:270 _resolve_wait_s; the D8 sync-guard test runs
# BOTH resolvers over the same fixtures and asserts identical output.
_derive_parent_stop_budget() {
    local env_file="${1:-${INSTALL_DIR:-}/.env}"
    # child_site2 — only honor an explicit STOP_SCRIPT_BUDGET_S env
    # if it is digits-only. A malformed/non-digits override (e.g. "abc")
    # falls back to the default; the arithmetic below REQUIRES a
    # digits value (bash's (( )) triggers "unbound variable" on
    # non-digits, and the test pack runs with `set -u` — the brief
    # calls for fail-closed semantics, not silent arithmetic
    # corruption). Mirrors the child's "malformed WAIT_S → fall back
    # to default" contract.
    local child_site2
    if [ -n "${STOP_SCRIPT_BUDGET_S:-}" ] && printf '%s' "${STOP_SCRIPT_BUDGET_S}" | grep -Eq '^[0-9]+$'; then
        child_site2="${STOP_SCRIPT_BUDGET_S}"
    else
        child_site2=120
    fi
    local resolved_ws ukg
    resolved_ws="$(_resolve_wait_s_mirror "$env_file" 2>/dev/null || printf '%s' "$DEFAULT_WAIT_S")"
    ukg="${UNIT_KILL_GRACE_S:-10}"
    local term_grace=5
    local margin=20
    local derived
    derived=$(( child_site2 + resolved_ws + ukg + term_grace + margin ))
    if [ "$derived" -lt 120 ]; then derived=120; fi
    printf '%s\n' "$derived"
}

# _effective_stop_script_budget [<env_file>] — the parent's EFFECTIVE
# bound for the `bash "$stop_script" ...` fork-exec at the call site.
# Honors an explicit STOP_SCRIPT_BUDGET_S env (operator override —
# sandbox drills, tests, or a deliberately tighter bound for a
# known-fast daemon); otherwise derives from the child's resolved
# WAIT_S + composition sum. Computed at the call site (NOT at module
# load) because the parent's `INSTALL_DIR` is per-target and may be
# unset at lib.sh source-time. The default at lib.sh:94 is a fallback
# (env-overridable); the DERIVED value here is the one actually used
# at runtime.
#
# Malformed override handling: a STOP_SCRIPT_BUDGET_S that is NOT
# digits-only (e.g. "" empty, "abc", unset) is treated as NO override —
# the derivation runs. This mirrors the child's "malformed WAIT_S →
# fall back to default" semantics: an operator typo cannot produce a
# nonsense bound that breaks the recovery machinery.
#
# <env_file> optional override for the .env path (passed to
# _derive_parent_stop_budget). Production callers omit it (defaults
# to $INSTALL_DIR/.env); the test pack passes an explicit path.
_effective_stop_script_budget() {
    local env_file="${1:-}"
    if [ -n "${STOP_SCRIPT_BUDGET_S:-}" ] && printf '%s' "${STOP_SCRIPT_BUDGET_S}" | grep -Eq '^[0-9]+$'; then
        printf '%s\n' "${STOP_SCRIPT_BUDGET_S}"
        return 0
    fi
    if [ -n "$env_file" ]; then
        _derive_parent_stop_budget "$env_file"
    else
        _derive_parent_stop_budget
    fi
}


# ── Promote/rollback shared mechanics (D6 + D-FA4.1 amendment) ──────────────
# stop_via_stop_script — SIGTERM-bounded, ownership-scoped stop. ALWAYS via
# scripts/stop-ensemble.sh (D6: reused, never duplicated; NEVER a raw kill).
#
# Commission v0.16.6 component 2 (Layer ii): this is called FROM a lock
# holder — promote.sh / rollback.sh / restart.sh hold the rollback.lock.d
# through soak+rollback. Set PIPELINE_LOCK_HELD_BY_CALLER=1 so the child
# stop-ensemble.sh skips its own lock acquire (otherwise it would busy-wait
# 15s on the lock the parent already holds — every promote would deadlock
# 15s on its stop span).
# _supervision_launchd_drained — true when no owned pid remains AND the port
# (when PORT is known and lsof is available) is free.
_supervision_launchd_drained() {
    local pids
    pids="$(_supervision_owned_pids 2>/dev/null)"
    [ -z "$pids" ] || return 1
    if [ -n "${PORT:-}" ] && command -v lsof >/dev/null 2>&1; then
        lsof -ti:"$PORT" >/dev/null 2>&1 && return 1
    fi
    return 0
}

# _supervision_stop_launchd <label> — D2a supervised STOP: bootout (the M1
# barrier) then a TERM-IDEMPOTENT straggler drain. bootout TERMs the job
# (launchd escalates to SIGKILL after the job's ExitTimeOut); the launcher
# forwards that TERM to the daemon (launcher.sh forwards EVERY signal, no
# once-guard) ⇒ after bootout we NEVER send a second TERM (a second TERM
# would force_exit a draining uvicorn = voided checkpoint). Only if an owned
# pid is STILL alive after the bounded drain do we SIGKILL — the launchd-native
# last resort, and NOT a second TERM. Returns 0 drained (or escalated-and-gone),
# 1 on bootout failure or still-alive-after-kill (caller halts fail-safe).
_supervision_stop_launchd() {
    local label="${1:-}" deadline pid
    command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1 || return 1
    # Capture the pre-stop owned pid (for the post-bootstrap pid≠pre verify).
    SUPERVISION_PRESTOP_MAINPID="$(_supervision_owned_pids 2>/dev/null | head -1)"
    [ -n "$SUPERVISION_PRESTOP_MAINPID" ] || SUPERVISION_PRESTOP_MAINPID=""
    if ! _supervision_launchd_bootout "$label"; then
        _warn "launchd stop: bootout ${label:-<domain>} FAILED (still loaded) — failing the stop (caller halts fail-safe)"
        return 1
    fi
    _log "launchd stop: bootout ${label} issued (M1 barrier) — draining (NO second TERM; launcher forwarded launchd's TERM)"
    deadline=$(( $(_now_epoch) + LAUNCHD_DRAIN_BUDGET_S ))
    while [ "$(_now_epoch)" -lt "$deadline" ]; do
        if _supervision_launchd_drained; then
            _log "launchd stop: drained (owned pids gone, port free) — done"
            return 0
        fi
        sleep 1
    done
    # Still alive after the drain budget ⇒ the daemon ignored TERM. Escalate
    # with SIGKILL ONLY (never a second TERM — the checkpoint-void regression).
    # Fix #5 (ENSEMBLE-32): the candidate list runs through the SELF-EXCLUSION
    # filter first — $$ (this shell), $PPID (the promote executor) and every
    # own-process-group pid are NEVER SIGKILL targets (incident 2026-11-14
    # §7 defect #7: the stop escalation must not sweep the executor running
    # the very stop). Defense-in-depth alongside the executor self-detach.
    for pid in $(_pipeline_self_excluded "$(_supervision_owned_pids 2>/dev/null)"); do
        _log "launchd stop: pid $pid still alive after ${LAUNCHD_DRAIN_BUDGET_S}s drain — SIGKILL escalation (no second TERM; own-group/self pids excluded)"
        journal_history_append stop_forced_kill \
            "launchd stop: pid=$pid survived bootout + ${LAUNCHD_DRAIN_BUDGET_S}s drain — SIGKILL escalated (no second TERM; graceful checkpoint write VOIDED)" \
            >/dev/null 2>&1 || true
        kill -9 "$pid" 2>/dev/null || true
    done
    deadline=$(( $(_now_epoch) + 10 ))
    while [ "$(_now_epoch)" -lt "$deadline" ]; do
        _supervision_launchd_drained && return 0
        sleep 1
    done
    _warn "launchd stop: owned pids STILL alive after SIGKILL escalation — stop NOT confirmed; failing (caller halts fail-safe)"
    return 1
}

# _upgrade_stop_script_sibling <calling-script-path> — resolve the sibling
# stop script ONE level up of the calling script's dir (the W4 SINGLE source
# of truth: promote/rollback/restart live in scripts/upgrade/, so the leg is
# <scripts_dir>/../stop-ensemble.sh). Echoes the path (never fails).
#
# H1 (tidier round 3): the THREE preflight probes (promote/rollback/restart)
# were copy-pasted and each claimed "self-contained (no lib.sh dependency)" —
# FALSE (lib.sh is sourced above every one of them, the probe's own _warn is
# lib.sh-only, and the claim "lib.sh itself only tests -f" described no code
# at all). This ONE helper is the single implementation.
_upgrade_stop_script_sibling() {
    local calling="${1:-${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}}"
    cd "$(dirname "$calling")" 2>/dev/null && pwd
} 2>/dev/null || true

# _upgrade_stop_script_sibling_usable <calling-script-path> — 0 when the
# sibling exists AND is executable. Prints the resolved path to stderr-free
# stdout ONLY via the caller's variable; callers refuse rc 78 on nonzero
# (identical behaviour at all three sites: promote §1, rollback preflight,
# restart preflight). The exec-bit requirement is conservative hardening —
# every consumer invokes `bash stop-ensemble.sh`, so the bit is not a runtime
# requirement; lib.sh's own stop_via_stop_script only needs the FILE.
_upgrade_stop_script_sibling_usable() {
    local dir script
    dir="$(_upgrade_stop_script_sibling "${1:-}")" || return 1
    [ -n "$dir" ] || return 1
    script="${dir}/../stop-ensemble.sh"
    _UPGRADE_STOP_SCRIPT_SIBLING="$script"
    [ -f "$script" ] && [ -x "$script" ]
}

stop_via_stop_script() {
    local stop_script
    # W4 (D2a, 2026-11-11): the SINGLE source of truth is the SIBLING one level
    # up of the CALLING script's dir (BASH_SOURCE[1] — promote/rollback/restart
    # live in scripts/upgrade/, so the leg is scripts/upgrade/../stop-ensemble.sh).
    # The old `$(pwd)/scripts/stop-ensemble.sh` fallback was DEAD in the install
    # layout (make install ships only watchdog-watcher.sh into <install>/scripts/
    # and the executor's cwd is the install dir) and was a trap that masked a
    # MISSING sibling with an rc=127 mid-stop — deleted. The shell-side
    # preflight probes (promote/rollback/restart) refuse rc 78 BEFORE any stop
    # via _upgrade_stop_script_sibling_usable (the ONE implementation).
    stop_script="$(_upgrade_stop_script_sibling "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")/../stop-ensemble.sh"
    # P1 §5(b) — the STOP SITE: emit exactly ONE machine-readable
    # ENSEMBLE_SUPERVISION_RESULT line (the pre-flight already emitted its
    # own; enforcement stayed there — the stop site NEVER refuses
    # mid-pipeline, so an unresolved-explicit-unit rc is ignored here).
    supervision_classify || true
    # P2 §3 (2026-09-29) — DUAL_FIGHT refuses BEFORE any stop action: no
    # TERM is sent, no systemctl stop issued. Distinct from the
    # unresolved-explicit-unit rc ignored above: an unresolvable NAME is a
    # benign degradation (the script path still stops owned pids
    # correctly), while a DUAL_FIGHT fault means two masters are live —
    # stopping via EITHER path could kill the wrong thing or
    # false-succeed — so the halt path (journal halt event + exit 78,
    # inside supervision_dualfight_check) is the only safe answer. The
    # pre-flight already checked; this is the stop-site re-check (state
    # may have drifted across the run — e.g. promote's rollback stop runs
    # ~5min after its pre-flight, post flip + restart).
    supervision_dualfight_check
    # D2a (2026-11-11) — LAUNCHD branch BEFORE the UNIT branch: bootout is the
    # M1 barrier; the straggler stop is TERM-idempotent (see
    # _supervision_stop_launchd). Reached only on a Darwin host whose
    # preflight verified exactly one launchd job (LAUNCHD_MANAGED:<label>).
    if [ "${SUPERVISION_STATE:-}" = "LAUNCHD_MANAGED" ] && [ -n "${SUPERVISION_UNIT:-}" ] \
       && _supervision_host_allows_launchd && command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1; then
        _supervision_stop_launchd "$SUPERVISION_UNIT"
        return $?
    fi
    # P2 §1 (b″ fix) — UNIT_MANAGED with a resolvable unit name hands the
    # classification VALUE to the stop script (machine-line grammar, same
    # '<state>[:<unit>]' format as the machine line above) so it stops
    # via `systemctl stop` + a UNIT-STATE poll (a unit-respawned
    # replacement pid is invisible to a pid poll; the unit state is not).
    # Every other shape keeps the byte-identical pid-scoped invocation —
    # no new env reaches the child on the script path.
    if [ "${SUPERVISION_STATE:-}" = "UNIT_MANAGED" ] && [ -n "${SUPERVISION_UNIT:-}" ]; then
        _log "stop: UNIT path — systemctl stop ${SUPERVISION_UNIT} + unit-state poll via $stop_script (b″: respawn-invisible-to-policy-poll fix)"
        # P3: snapshot the unit's PRE-STOP MainPID (behind the same host
        # guard family as the hand-back; the daemon is still live HERE —
        # stop_via_stop_script runs pre-stop by contract) —
        # restart_via_launcher's unit hand-back verifies the post-start
        # MainPID is NEW (≠ this). Unresolvable → empty (the verify then
        # requires only a nonzero NEW pid).
        SUPERVISION_PRESTOP_MAINPID=""
        if _supervision_host_allows_unit && command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1; then
            SUPERVISION_PRESTOP_MAINPID="$("$SYSTEMCTL_BIN" show "$SUPERVISION_UNIT" -p MainPID --value 2>/dev/null || true)"
            [ -n "$SUPERVISION_PRESTOP_MAINPID" ] || SUPERVISION_PRESTOP_MAINPID=""
        fi
        # 2.2b (upgrade-resilience 2026-10-07) — bound the child fork-exec.
        # The old shape invoked `bash "$stop_script" …` directly; a hung
        # child (kill -0 or wait blocked forever) could wedge this caller.
        # _run_bounded waits at most STOP_SCRIPT_BUDGET_S (default 120s),
        # then SIGTERMs the child, gives a 5s grace, and SIGKILLs — falling
        # THROUGH to the caller's stop verification/re-verify machinery
        # below (which already handles a still-running unit / pid). On
        # timeout: loud warn + journal `subprocess_wait_timeout` event,
        # then CONTINUE with rc=0 (recovery proceeds). The 2.2b invariant:
        # bounded waits NEVER exit the script; falling through IS the path
        # forward. On NON-timeout child failure (real nonzero rc), preserve
        # that rc as the function's return so the caller's `if !`
        # correctly takes the B4 leave-txn-open path (rollback.sh:139,
        # promote's stop-failed halt). PIPELINE_LOCK_HELD_BY_CALLER=1 —
        # the parent caller (promote / rollback / restart) already holds
        # the rollback.lock.d through soak+rollback; the child
        # stop-ensemble.sh MUST skip its own acquire (Layer ii — Layer i's
        # settle-check too: the pipeline is manifestly not settled while
        # the parent holds the lock).
        #
        # POST-REVIEW HARDENING (2026-10-07): the bound here is the
        # EFFECTIVE budget — env-overridden or derived from the child's
        # resolved WAIT_S (see _effective_stop_script_budget and
        # _derive_parent_stop_budget). The fixed 120 default at
        # lib.sh:94 was the prior shape; the fixed-constant coupling
        # was the review finding. The derived value at WAIT_S=70 is
        # 225s; at WAIT_S=600 (the cap) it's 755s — both cover the
        # child's legitimate worst-case runtime with the +20s margin
        # (see _derive_parent_stop_budget for the composition).
        local eff_budget stop_rc=0
        eff_budget="$(_effective_stop_script_budget)"
        if PIPELINE_LOCK_HELD_BY_CALLER=1 \
            ENSEMBLE_SUPERVISION_RESULT="${SUPERVISION_STATE}:${SUPERVISION_UNIT}" \
            _run_bounded "$eff_budget" -- bash "$stop_script" "$INSTALL_DIR" "$PORT"; then
            stop_rc=0
        else
            local sbrc=$?
            if [ "$sbrc" = "124" ]; then
                _warn "stop_script fork-exec timed out (budget=${eff_budget}s) — falling through to caller's stop verification (child SIGTERM→SIGKILL'd by _run_bounded); function returns 0 (recovery proceeds)"
                journal_history_append subprocess_wait_timeout \
                    "site=stop_via_stop_script_unit kind=budget reason=$(printf 'child: timeout(eff_budget=%s)' "$eff_budget") path=$stop_script unit=$SUPERVISION_UNIT rc=124 — SIGTERM→SIGKILL, falling through to recovery (rc=0)" \
                    || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
                stop_rc=0   # timeout = recovery continues; do NOT propagate
            else
                _warn "stop_script fork-exec FAILED (rc=$sbrc) — propagating nonzero rc to caller (B4 leave-txn-open path takes over)"
                journal_history_append subprocess_wait_timeout \
                    "site=stop_via_stop_script_unit kind=nonzero_exit reason=$(printf 'child: nonzero rc=%s' "$sbrc") path=$stop_script unit=$SUPERVISION_UNIT rc=$sbrc — propagating to caller" \
                    || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
                stop_rc=$sbrc
            fi
        fi
        return "$stop_rc"
    else
        _log "stop: ownership-scoped SINGLE-TERM via $stop_script"
        # 2.2b — same bound on the pid-scoped fork-exec (mirror of the
        # UNIT path above; same budget + timeout-vs-failure rc policy).
        # PIPELINE_LOCK_HELD_BY_CALLER=1 — same reason: the parent holds
        # the lock; the child MUST NOT acquire (Layer ii). POST-REVIEW
        # HARDENING: the bound is the EFFECTIVE budget (env override or
        # derived) — same derivation as the UNIT path above.
        local eff_budget stop_rc=0
        eff_budget="$(_effective_stop_script_budget)"
        if PIPELINE_LOCK_HELD_BY_CALLER=1 \
            _run_bounded "$eff_budget" -- bash "$stop_script" "$INSTALL_DIR" "$PORT"; then
            stop_rc=0
        else
            local sbrc=$?
            if [ "$sbrc" = "124" ]; then
                _warn "stop_script fork-exec timed out (budget=${eff_budget}s) — falling through to caller's stop verification (child SIGTERM→SIGKILL'd by _run_bounded); function returns 0 (recovery proceeds)"
                journal_history_append subprocess_wait_timeout \
                    "site=stop_via_stop_script_pid kind=budget reason=$(printf 'child: timeout(eff_budget=%s)' "$eff_budget") path=$stop_script rc=124 — SIGTERM→SIGKILL, falling through to recovery (rc=0)" \
                    || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
                stop_rc=0
            else
                _warn "stop_script fork-exec FAILED (rc=$sbrc) — propagating nonzero rc to caller (B4 leave-txn-open path takes over)"
                journal_history_append subprocess_wait_timeout \
                    "site=stop_via_stop_script_pid kind=nonzero_exit reason=$(printf 'child: nonzero rc=%s' "$sbrc") path=$stop_script rc=$sbrc — propagating to caller" \
                    || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
                stop_rc=$sbrc
            fi
        fi
        return "$stop_rc"
    fi
}

# launcher_swap <ver> — swap INSTALL_DIR/launcher.sh from a release's staged
# copy. MUST run in the STOPPED window (launcher + daemon both exited post
# SINGLE-TERM) — D-FA4.1 amendment: the launcher is part of the release
# surface; carrying it in-payload makes launcher/binary skew self-healing.
launcher_swap() {
    local ver="$1"
    local src="$INSTALL_DIR/releases/$ver/launcher.sh"
    if [ ! -f "$src" ]; then
        _warn "launcher_swap: no staged launcher at $src — keeping existing launcher"
        return 1
    fi
    cp "$src" "$INSTALL_DIR/launcher.sh" || { _warn "launcher_swap: copy failed"; return 1; }
    chmod +x "$INSTALL_DIR/launcher.sh"
    _log "launcher swapped from release $ver (stopped window)"
    return 0
}

# atomic_flip <ver> — rename(2)-semantics symlink flip: build current.new.$$
# then mv -f over `current` (the mv is the atomic point). The link lives at
# the INSTALL-DIR ROOT ($INSTALL_DIR/current) — the launcher's shipped
# resolver looks there FIRST (launcher.sh resolve_binary:
# $INSTALL_DIR/current/ensemble-prod, verified foundation) — and its target
# is "releases/<ver>", relative to the install dir.
#
# Portability (fix/portable-atomic-flip-linux, 2026-09-21): the flip
# MUST work identically on BSD/macOS and GNU/Linux. Plain `mv -f` is
# WRONG on either — it would follow current→releases/<old> and move the
# temp link INSIDE the old release dir, silently leaving `current` on
# the OLD release.
#   BSD/macOS: `mv -h -f`  — swap the SYMLINK itself (don't follow
#     DEST when DEST is a symlink-to-dir).
#   GNU/Linux: `mv -T -f`  — treat DEST as a normal file (no-follow
#     DEST semantics).
# We dispatch on `uname -s` rather than chaining `mv -h -f X Y ||
# mv -T -f X Y`: on BSD a REAL -h failure (permission denied, missing
# source, RO filesystem) would otherwise hit an invalid -T and surface
# a wrong-platform error, hiding the real cause. Unrecognized platforms
# refuse (fail-closed; we never guess). Verified on macOS mv(1) and
# GNU coreutils 9.4 (Linux).
atomic_flip() {
    local ver="$1"
    local mv_args=""
    case "$(uname -s)" in
        Darwin|*BSD*|*bsd*)
            mv_args="-h -f"
            ;;
        Linux|GNU*|*GNU*)
            mv_args="-T -f"
            ;;
        *)
            _warn "atomic_flip: unrecognized platform '$(uname -s)' — refusing to flip (fail-closed; BSD or Linux required)"
            return 1
            ;;
    esac
    ln -sfn "releases/$ver" "$INSTALL_DIR/current.new.$$" || return 1
    # mv_args intentionally word-split (a single string of options)
    # shellcheck disable=SC2086
    if ! mv $mv_args "$INSTALL_DIR/current.new.$$" "$INSTALL_DIR/current"; then
        rm -f "$INSTALL_DIR/current.new.$$"
        return 1
    fi
    _log "current -> releases/$ver (atomic flip)"

    # Pointer alias maintenance (relcur-m1): the ceremony OWNS the
    # $INSTALL_DIR/releases/current alias and must never strand it.
    # Mirror the top-level flip onto the alias with the same rename(2)
    # discipline. CREATE it on first flip when absent (mv -T/-h creates
    # dest when missing). Top-level flip is already committed, so an
    # alias failure/mismatch is LOUD but never fails/rolls back.
    local _rel="$INSTALL_DIR/releases"
    local _alias_mirror_ok=0
    mkdir -p "$_rel" 2>/dev/null || true
    if ln -sfn "$ver" "$_rel/current.alias.new.$$" 2>/dev/null; then
        # shellcheck disable=SC2086
        if ! mv $mv_args "$_rel/current.alias.new.$$" "$_rel/current" 2>/dev/null; then
            rm -f "$_rel/current.alias.new.$$" 2>/dev/null
            _warn "atomic_flip: pointer alias releases/current flip FAILED for $ver — top-level current already committed; NOT rolling back"
            journal_history_append warn "pointer-alias: releases/current flip FAILED for $ver (top-level current committed)" >/dev/null 2>&1 || true
        else
            _alias_mirror_ok=1
            _log "releases/current -> $ver (pointer alias flip)"
        fi
    else
        _warn "atomic_flip: pointer alias releases/current ln FAILED for $ver — top-level current already committed; NOT rolling back"
        journal_history_append warn "pointer-alias: releases/current ln FAILED for $ver (top-level current committed)" >/dev/null 2>&1 || true
    fi

    # ASSERT (flip site): the top-level pointer and the releases/current
    # alias MUST name the SAME release, and the alias MUST name the
    # release just flipped. Both hold at flip time (the journal's `current`
    # does NOT — it is written later, at commit — so it is deliberately NOT
    # compared here; see pointer_alias_assert_commit for that check).
    # Loud on mismatch; never fatal (the top flip is already committed).
    local _top_base _alias_base
    _top_base="$(basename "$(readlink "$INSTALL_DIR/current" 2>/dev/null)" 2>/dev/null)"
    _alias_base="$(_pointer_alias_basename)"
    if [ "$_alias_mirror_ok" = "1" ] && [ -z "$_alias_base" ]; then
        _warn "atomic_flip: ASSERT FAILED releases/current alias is ABSENT after a successful mirror (target $ver)"
        journal_history_append warn "pointer-alias: ASSERT alias ABSENT after mirror (target $ver)" >/dev/null 2>&1 || true
    fi
    if [ -n "$_alias_base" ] && [ "$_top_base" != "$_alias_base" ]; then
        _warn "atomic_flip: ASSERT FAILED top-level basename '$_top_base' != alias basename '$_alias_base' (target $ver)"
        journal_history_append warn "pointer-alias: ASSERT top '$_top_base' != alias '$_alias_base'" >/dev/null 2>&1 || true
    fi
    if [ -n "$_alias_base" ] && [ "$_alias_base" != "$ver" ]; then
        _warn "atomic_flip: ASSERT FAILED alias basename '$_alias_base' != flipped version '$ver'"
        journal_history_append warn "pointer-alias: ASSERT alias '$_alias_base' != flipped '$ver'" >/dev/null 2>&1 || true
    fi

    return 0
}

# _pointer_alias_basename — the release name the releases/current alias
# points at (basename of its target), or EMPTY when the alias is absent.
# The alias target is a bare release name (e.g. "v0.31.42"); a relative
# "releases/<ver>" target is also handled (basename).
_pointer_alias_basename() {
    local t=""
    [ -L "$INSTALL_DIR/releases/current" ] || return 0
    t="$(readlink "$INSTALL_DIR/releases/current" 2>/dev/null)" || t=""
    [ -n "$t" ] || return 0
    printf '%s' "${t##*/}"
}

# pointer_alias_assert_commit <ver> <journal_current> — COMMIT-site
# pointer-alias assertion. Called AFTER the journal's `current` has been set
# to the promoted version, where the equality genuinely HOLDS on a healthy
# ceremony: the releases/current alias MUST name the same release as the
# journal's `current`. A mismatch here means the pointer was stranded or
# mis-pointed — LOUD (_warn + a `warn` journal row) but NEVER fatal: the
# ceremony's commit has already succeeded by the time this runs.
#
# <journal_current> — the journal's `current` value, as ALREADY READ by the
# caller (the commit path reads the journal for its immediacy assertion
# anyway). Passing it keeps this helper free of a second O(N) journal read
# — the journal_read call-site census (tests/packs/preflight_census_test.sh)
# counts direct call sites, so the read is NOT duplicated here. An EMPTY
# value means the journal current could not be read: the invariant is then
# UNVERIFIABLE, which is said out loud (an unverified pointer is not a
# verified pointer).
#
# Deliberately NOT called at the flip site: there the journal still holds
# the PREVIOUS current (it is written at commit), so comparing the alias
# against the journal at flip time is guaranteed-spurious.
pointer_alias_assert_commit() {
    local ver="${1:-}"
    local journal_base="${2:-}"
    local alias_base=""
    alias_base="$(_pointer_alias_basename)"
    [ "$journal_base" = "null" ] && journal_base=""
    # No journal current -> the invariant CANNOT be checked. Say so (loud,
    # non-fatal); the ceremony's own journal_fail_loud path owns the hard
    # refusal for an unreadable journal.
    if [ -z "$journal_base" ]; then
        _warn "pointer alias: journal current unreadable — the releases/current vs journal-current invariant could NOT be verified (promoted=${ver:-unknown})"
        return 0
    fi
    if [ -z "$alias_base" ]; then
        _warn "pointer alias: releases/current is ABSENT while journal current='$journal_base' (promoted=${ver:-unknown}) — the release pointer is stranded; repair: scripts/upgrade/pointer-alias-repair.sh"
        journal_history_append warn "pointer-alias: releases/current ABSENT while journal current='$journal_base' (promoted=${ver:-unknown}) — pointer stranded" >/dev/null 2>&1 || true
        return 0
    fi
    if [ "$alias_base" != "$journal_base" ]; then
        _warn "pointer alias: releases/current -> '$alias_base' != journal current '$journal_base' (promoted=${ver:-unknown}) — the release pointer is mis-pointed; repair: scripts/upgrade/pointer-alias-repair.sh"
        journal_history_append warn "pointer-alias: releases/current '$alias_base' != journal current '$journal_base' (promoted=${ver:-unknown}) — pointer mis-pointed" >/dev/null 2>&1 || true
    fi
    return 0
}

# ── Supervision-aware hand-back (P3, ownership-mode commission 2026-09-29) ───
#
# STUB SEAM (P2/P5 — mirrors scripts/stop-ensemble.sh:323): EVERY systemctl
# interaction on the lib.sh side routes through SYSTEMCTL_BIN (default
# 'systemctl' — PATH-resolved, so PATH-injected stubs work exactly like the
# comp7 suite's systemctl stubs). Point SYSTEMCTL_BIN at a script to drive
# scripted sequences: hand-back failure→halt-NO-fallback, is-active
# sequences, MainPID changes (P5 will script all of these).
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"

# SUPERVISION_UNIT_DIR — directory scanned for unit FILES by the
# nohup-survivor heal inside supervision_classify (supervisor war r3,
# 2026-09-30). Default /etc/systemd/system = the adopt-unit.sh DEST
# convention (admin-managed units: adopt-unit generates there, the
# runbook's hand-provisioned ensemble-*.service units live there —
# lib.sh:1990 context). Test seam, SYSTEMCTL_BIN style: point it at a
# fixture dir so heal cells never depend on the host's real /etc/systemd.
SUPERVISION_UNIT_DIR="${SUPERVISION_UNIT_DIR:-/etc/systemd/system}"

# _supervision_host_allows_unit — the shared host guard for every unit
# hand-back path: Linux + a live systemd ONLY. The uname check runs FIRST
# so non-Linux hosts take zero new /proc or /run reads (P1 guard
# discipline — the BSD/macOS arms stay byte-identical).
_supervision_host_allows_unit() {
    [ "$(uname -s)" = "Linux" ] || return 1
    [ -d /run/systemd/system ] || return 1
    return 0
}

# ── launchd seam (D2a, promote-full-auto 2026-11-11) ───────────────────────
# Twin of SYSTEMCTL_BIN (S4/R23): PATH-resolved default so a PATH-injected
# stub drives scripted sequences (the mac-side test seam); env override
# remains for absolute pinning. Every launchctl call rides _run_bounded so a
# wedged launchctl can never wedge the ceremony.
LAUNCHCTL_BIN="${LAUNCHCTL_BIN:-launchctl}"
# Bound on ONE launchctl invocation (the gui-domain dump and each per-job
# print). Generous vs a healthy ~sub-second call; a wedged launchctl → a
# loud refusal, never a boot outage.
LAUNCHD_PROBE_BUDGET_S="${LAUNCHD_PROBE_BUDGET_S:-5}"
# Bound on the post-bootout drain wait (mirrors CHILD_STOP_WAIT_S=70: daemon
# graceful shutdown 60s + 10s margin). bootout TERMs the job; we wait this
# long for the owned tree + port to drain BEFORE any SIGKILL escalation, and
# NEVER send a second TERM in that window.
LAUNCHD_DRAIN_BUDGET_S="${LAUNCHD_DRAIN_BUDGET_S:-70}"
# The watchdog AGENT label is NOT the daemon job — excluded from the
# program-ownership predicate (a watchdog-matching launcher would double-count).
LAUNCHD_WATCHDOG_LABEL="${LAUNCHD_WATCHDOG_LABEL:-com.ensemble.watchdog}"

# _supervision_host_allows_launchd — host guard for every launchd path:
# Darwin ONLY. The uname check runs FIRST so non-Darwin hosts take ZERO
# launchctl forks (mirrors _supervision_host_allows_unit's guard discipline).
_supervision_host_allows_launchd() {
    [ "$(uname -s)" = "Darwin" ]
}

# _supervision_launchd_domain — the operator lane domain (USER LaunchAgent by
# design: never sudo; make plist-install targets gui/$(id -u)).
_supervision_launchd_domain() {
    printf 'gui/%s\n' "$(id -u)"
}

# _supervision_launchd_detect — probe the gui domain and return 0 IFF EXACTLY
# ONE job matches, EXCLUDING the watchdog label. Sets SUPERVISION_UNIT to the
# resolved label. On zero / multiple / unresolvable → returns 1 (the caller
# refuses; NEVER a SCRIPT_NOHUP fallthrough on Darwin).
#
# F9 (review round 1) — MATCHING SEMANTICS, PRECISE (the old wording claimed
# an "argv[1] realpath" predicate; the code is a WHOLE-DUMP SUBSTRING test):
# the dump of `launchctl print gui/<uid>/<label>` is matched with a shell
# case glob — `*"${install_real}/launcher.sh"*` OR `*"${INSTALL_DIR}/
# launcher.sh"*` — ANYWHERE in the dump (a "program arguments" line, a
# "path =" line, a comment — position is irrelevant). Not hardened this
# round BY DESIGN: (a) the property that bounds the blast radius is
# UNIQUE-MATCH-OR-REFUSE — zero or ≥2 matching jobs refuse, so a stray
# path mention can only turn a would-be `arm` into a refusal (fail-closed),
# never arm on the wrong job unless EXACTLY ONE job mentions the path, in
# which case it IS the install's job by inspection; (b) tightening to an
# argv[1] realpath parse would change matching semantics the F4/F5 seam
# cells depend on (they fabricate exactly the documented stub contract
# below). Any future hardening must re-cut those cells in the same change.
#
# Stub contract (tests + comp7 style): point LAUNCHCTL_BIN at a script that
# answers `list` (3-column PID STATUS LABEL rows) and
# `print gui/<uid>/<label>` (a job dump containing the launcher path). Both
# steps ride _run_bounded. ENSEMBLE_LAUNCHD_LABEL pins the ONLY label
# considered (precedence over auto-detection); it still must match exactly.
_supervision_launchd_detect() {
    local want="${ENSEMBLE_LAUNCHD_LABEL:-}" domain labels lbl dump
    local install_real count=0 match=""
    [ -n "${INSTALL_DIR:-}" ] || return 1
    command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1 || return 1
    install_real="$(cd -P "$INSTALL_DIR" 2>/dev/null && pwd || printf '%s' "$INSTALL_DIR")"
    [ -n "$install_real" ] || return 1
    domain="$(_supervision_launchd_domain)"
    # Step 1 — domain dump of labels (launchctl list: PID<TAB>STATUS<TAB>LABEL).
    labels="$(_run_bounded "$LAUNCHD_PROBE_BUDGET_S" -- "$LAUNCHCTL_BIN" list 2>/dev/null)" || return 1
    [ -n "$labels" ] || return 1
    if [ -n "$want" ]; then
        set -- "$want"
    else
        # shellcheck disable=SC2046
        set -- $(printf '%s\n' "$labels" | awk 'NF>=3 {print $3}')
    fi
    # Step 2 — per-job print; predicate on the launcher script path.
    for lbl in "$@"; do
        [ -n "$lbl" ] || continue
        [ "$lbl" = "$LAUNCHD_WATCHDOG_LABEL" ] && continue
        dump="$(_run_bounded "$LAUNCHD_PROBE_BUDGET_S" -- "$LAUNCHCTL_BIN" print "${domain}/${lbl}" 2>/dev/null)" || continue
        [ -n "$dump" ] || continue
        case "$dump" in
            *"${install_real}/launcher.sh"*|*"${INSTALL_DIR}/launcher.sh"*)
                match="$lbl"; count=$((count + 1)) ;;
        esac
    done
    if [ "$count" -eq 1 ]; then
        SUPERVISION_UNIT="$match"
        return 0
    fi
    return 1
}

# _supervision_launchd_bootout — the M1 STOP BARRIER (D2a supervised flip):
# `launchctl bootout gui/<uid>/<label>` (or the bare domain for a whole-job
# teardown). Bounded. "not found" is benign-idempotent (rc 0); any other
# failure returns nonzero so the caller halts fail-safe. The bootstrap side
# is _supervision_launchd_bootstrap.
_supervision_launchd_bootout() {
    local label="${1:-}" domain target
    # M4 (tidier round 3): the EMPTY-LABEL guard lives HERE, not only at the
    # promote disarm call site. An empty label used to fall through to the
    # BARE gui/<uid> DOMAIN target — a whole-domain teardown of EVERY user
    # LaunchAgent. No caller legitimately needs the whole-domain form (both
    # callers pass a resolved label; the stop branch requires -n
    # SUPERVISION_UNIT), so an empty label is refused fail-loud here.
    if [ -z "$label" ]; then
        _warn "launchd bootout REFUSED: empty job label — the bare gui/<uid> domain target (a whole-domain teardown) is never legitimate from this helper; refusing fail-closed"
        return 1
    fi
    command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1 || return 1
    domain="$(_supervision_launchd_domain)"
    target="${domain}/${label}"
    local out rc
    out="$(_run_bounded "$LAUNCHD_PROBE_BUDGET_S" -- "$LAUNCHCTL_BIN" bootout "$target" 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        return 0
    fi
    # M8 (tidier round 3, SAFETY-RELEVANT): the benign-idempotent match is
    # rc-AGNOSTIC + SUBSTRING-based in the old shape — a FAILED bootout whose
    # stderr merely CONTAINED "not found" (e.g. "No such process: …
    # permission denied", or any unrelated diagnostic carrying the phrase)
    # was read as success ⇒ the DISARM reported success while the supervisor
    # stayed LOADED+ARMED. Fail-closed now:
    #   (a) rc 124 (bounded-probe timeout) is ALWAYS a failure;
    #   (b) the benign class is rc 3 ONLY — launchctl's documented
    #       "service not found / No such process" band — AND the output must
    #       match one of the anchored benign phrases;
    #   (c) ANY other rc or output ⇒ NOT disarmed (unknown ⇒ failure).
    [ "$rc" -eq 124 ] && return 1
    if [ "$rc" -eq 3 ]; then
        case "$out" in
            *"Could not find specified service"*|*"No such process"*|*"service not found"*)
                return 0 ;;
        esac
    fi
    # rc-unknown or a non-benign diagnostic ⇒ the failure stands.
    return 1
}

# _supervision_launchd_bootstrap — the M2 SUPERVISOR-OWNED START (D2a):
# `launchctl bootstrap gui/<uid> <verbatim-plist-path>`. RunAtLoad=true ⇒
# bootstrap == start. Bounded. "Already loaded" is benign ONLY after a
# revalidation by the caller; here a nonzero rc is returned for the caller to
# handle (no promote-started launcher ever on the supervised path).
#
# Fix #4 (ENSEMBLE-32 repair lane; incident 2026-11-14 §3.2/§5.2): on a
# hard failure the captured launchctl output is STASHED into the global
# HANDBACK_LAST_ERROR (flattened to one line, bounded) instead of being
# discarded — the v0.30.5/v0.30.9 halts recorded NO launchctl error
# anywhere, so the failure was unreproducible from the artifacts. The
# WARN lines at the hand-back call site and the three halt templates
# (promote/restart/rollback) surface it. Success clears the stash.
#
# MAJOR #1 (review round 2, ENSEMBLE-32): a VARIABLE stash dies in the
# subshell whenever the writer runs inside `$()` — and the RESOLVER
# (_supervision_launchd_plist_path) is consumed via `$()` at the
# hand-back site (restart_via_launcher). On the exact incident path
# (resolver fails → halt) the halt detail degraded to "hand-back error:
# none captured" — the capture purpose UNMET for that branch. Out-of-band
# channel: both stash sites ALSO write the diagnosis to
# HANDBACK_ERROR_FILE (a $$-keyed temp file — filesystem, subshell-proof);
# the halt readers go through _handback_last_error_text, which prefers
# the in-scope variable (bootstrap branch — plain call, variable
# survives) and falls back to the file (resolver branch). Every FAILURE
# rewrites both; every SUCCESS/benign arm clears both, so a stale seed
# (e.g. a pre-stop resolver failure at the SUPERVISION_LAUNCHD_PLIST_PATH
# stash sites) can never surface instead of the diagnosis for the actual
# hand-back failure.
HANDBACK_LAST_ERROR=""
# ── LAZY MINT (hbk-err inode-churn fix, 2026-11-22, anomaly 84be2e4e) ───────
# The three hand-back side-channels are $$-keyed FILES that carry a payload
# BETWEEN a writer that runs INSIDE a $() subshell and a reader that runs in
# the PARENT shell (the MAJOR #1 subshell-proof bridge — a bare variable dies
# at the subshell boundary). Two invariants follow, and the pre-fix code
# broke the second one:
#   1. the PATH must be agreed at SOURCE time (a subshell assignment would
#      die with the subshell — the very defect the channel exists to avoid);
#   2. the FILE must exist ONLY when it carries a payload.
# The pre-fix shape called `mktemp` (which CREATES) at source time, so EVERY
# shell that sourced lib.sh burned three zero-byte inodes — the /tmp inode
# churn (the ensemble-hbk-* classes, ~10.9k files on the host). `mktemp -u`
# agrees a UNIQUE name WITHOUT creating anything; the O_EXCL claim moves to
# the writer (the payload-bearing moment).
# THE READERS ARE UNCHANGED: all three gate on `[ -s "$_f" ]`, so an ABSENT
# file and an EMPTY file read identically ("nothing captured") — the fix is
# invisible to every consumer.
HANDBACK_ERROR_FILE="$(mktemp -u "${TMPDIR:-/tmp}/ensemble-hbk-err.$$.XXXXXX" 2>/dev/null || printf "%s" "${TMPDIR:-/tmp}/ensemble-hbk-err.$$")"
# _handback_channel_write <path> <text> — the ONE payload writer for all
# three channels: the file is created ONLY NOW (the payload-bearing moment)
# and the name is claimed O_EXCL (`set -C` noclobber) so the create is
# atomic. A REPEAT write in the same shell (the reason channel is written up
# to 15 times per verify) truncates-and-overwrites — the pre-fix
# last-writer-wins semantics, preserved exactly. Best-effort throughout: a
# tmp failure must never break the failure path itself.
_handback_channel_write() {
    local f="${1:-}" text="${2:-}"
    [ -n "$f" ] || return 0
    # SYMLINK GUARD (review W2, 2026-11-22): the O_EXCL claim protects the
    # FIRST write, but a REPEAT write in the same shell must still
    # truncate-and-overwrite (last-writer-wins — the reason channel is
    # written up to 15 times per verify). Between those two moments a
    # planted symlink at the agreed path would be FOLLOWED by an
    # unconditional `printf > "$f"`, overwriting its target. Pre-fix the
    # XXXXXX path was mktemp-CREATED (symlink-immune at creation), so this
    # plant window is NEW. Fail closed: write ONLY when the claim succeeded
    # OR the existing path is a REGULAR, NON-SYMLINK file.
    if ( set -C; : > "$f" ) 2>/dev/null; then
        :  # fresh file created by us — the name is ours
    else
        # claim refused: either a repeat write to our own file, or a plant.
        # `-f` alone follows a symlink to a regular file, so `-L` is the
        # decisive rejection. A dangling link, FIFO, device or directory
        # also fails `-f` and is refused.
        [ -f "$f" ] && [ ! -L "$f" ] || return 0
    fi
    printf '%s' "$text" > "$f" 2>/dev/null || true
}
_handback_err_file_write() {
    # $1 = diagnosis text; best-effort — a tmp failure must never break
    # the failure path itself.
    _handback_channel_write "${HANDBACK_ERROR_FILE:-}" "$1"
    # LOGGING SYMMETRY (hbk-err fix, 2026-11-22): the err channel was the
    # ONE hand-back side-channel whose writer had NO normal-logging
    # sibling — its diagnosis reached the operator only at the READER
    # site, two hops later (and on Linux the channel is never written at
    # all, so the halt row's "none captured" there is an UNWRITTEN channel,
    # not a missing capture). Emit it HERE, at the writer, so a hand-back
    # error can never be swallowed. _hb_verify_diag is the DUAL-SINK
    # emitter (the narrative file + unbuffered stderr) — the same seam
    # every reason-channel writer already uses.
    _hb_verify_diag "launchd hand-back: hand-back error captured (err side-channel): $(printf '%s' "${1:-}" | head -c 400)"
}
_handback_err_file_clear() {
    rm -f "${HANDBACK_ERROR_FILE:-}" 2>/dev/null || true
}
# _handback_last_error_text — THE reader for every consumer (WARN lines +
# halt templates). Variable first (survives plain calls), file second
# (survives $() subshells). Empty output = nothing captured.
_handback_last_error_text() {
    if [ -n "${HANDBACK_LAST_ERROR:-}" ]; then
        printf '%s' "$HANDBACK_LAST_ERROR"
        return 0
    fi
    local _f="${HANDBACK_ERROR_FILE:-}"
    if [ -s "$_f" ]; then
        printf '%s' "$(head -c 400 "$_f" 2>/dev/null | tr '\n' ';' | tr -s ' ')"
    fi
    return 0
}

# ── HANDBACK VERIFIER-REASON channel (D6-(iv) / AC-D6-6, 2026-11-19) ───────
# WHY A SEPARATE CHANNEL (and NOT "HB_ERR is broken"):
#   HB_ERR / HANDBACK_LAST_ERROR is the LAUNCHD RESOLVER's diagnosis channel.
#   Every write of it sits inside a launchd block (_supervision_launchd_bootstrap
#   / _supervision_launchd_plist_path — T8, tidier fold 2026-11-20:
#   re-anchored from stale line numbers to function names) and the systemd
#   hand-back touches
#   the channel NOWHERE — so on Linux the halt row's "hand-back error:
#   none captured" is an UNWRITTEN channel, not a missing capture (C-9).
#   Its presence therefore positively indicates a VERIFIER-PATH failure
#   (attribution / version / hard-red), not a resolver failure.
#   ⇒ The fix is NOT "populate HB_ERR on the systemd path" (that would blur
#   two unrelated vocabularies and touch a launchd-only channel); it is to
#   BYPASS it: the verifier-path reason travels on its OWN additive channel
#   and the halt row renders it. The launchd channel is left byte-unchanged.
#
# SUB-SHELL-PROOF (the MAJOR #1 precedent, one screen up): the reason is
# produced INSIDE $() — _supervision_unit_serving is consumed as
# `newpid="$(_supervision_unit_serving ...)"` — so a bare variable would
# die at the subshell boundary. That is exactly the defect that made the
# launchd resolver's diagnosis degrade to "none captured" on the incident
# path. Hence the $$-keyed file (out-of-band), read back by the caller in
# the parent shell. `$$` is the PARENT shell's pid inside a bash subshell,
# so the path is stable across the boundary.
HANDBACK_VERIFY_REASON=""
# m10 (review-fix, 2026-11-20): the side-channel files are MKTEMP-minted
# (O_EXCL-safe, no fixed-name /tmp paths — the $$ suffix rides the TEMPLATE
# so cross-referencing still works). The path must be agreed BEFORE the $()
# subshell (a subshell assignment dies with the subshell) — hence source
# time. Degradation: mktemp failure falls back to the legacy $$-keyed literal
# (a write-time redirect, same as pre-m10).
# LAZY MINT (2026-11-22, anomaly 84be2e4e): `mktemp -u` agrees the name
# WITHOUT creating the file — the O_EXCL claim moved to the writer, so the
# file exists ONLY when it carries a payload. The `-s` reader below treats an
# absent file exactly like an empty one (nothing captured), so no consumer
# changes.
HANDBACK_VERIFY_REASON_FILE="$(mktemp -u "${TMPDIR:-/tmp}/ensemble-hbk-verify.$$.XXXXXX" 2>/dev/null || printf '%s' "${TMPDIR:-/tmp}/ensemble-hbk-verify.$$")"
# _handback_verify_reason_write <text> — best-effort; a tmp failure must
# never break the failure path itself. Bounded to 400 chars (the journal
# detail is a single-line quoted string). The file is created ONLY here (the
# payload-bearing moment — see the LAZY MINT note above); a repeat write
# truncates-and-overwrites (last-writer-wins, as before).
_handback_verify_reason_write() {
    _handback_channel_write "${HANDBACK_VERIFY_REASON_FILE:-}" \
        "$(printf '%s' "${1:-}" | tr '\n' ';' | tr -s ' ' | cut -c1-400)"
}
_handback_verify_reason_clear() {
    rm -f "${HANDBACK_VERIFY_REASON_FILE:-}" 2>/dev/null || true
}
# _handback_verify_reason_text — THE reader for every consumer (the hand-back
# WARN lines + the three halt templates). Variable first (survives a plain
# call), file second (survives the $() subshell). Empty output = nothing
# captured.
_handback_verify_reason_text() {
    if [ -n "${HANDBACK_VERIFY_REASON:-}" ]; then
        printf '%s' "$HANDBACK_VERIFY_REASON"
        return 0
    fi
    local _f="${HANDBACK_VERIFY_REASON_FILE:-}"
    if [ -s "$_f" ]; then
        printf '%s' "$(head -c 400 "$_f" 2>/dev/null | tr '\n' ';' | tr -s ' ')"
    fi
    return 0
}

# ── HANDBACK VERIFY-ELAPSED channel (D6-v mechanism, self-service-promote ──
# Wave 2, 2026-11-19) — the SAME $$-keyed out-of-band shape as the reason
# channel above (the verifier runs inside $() — `newpid="$(…)"` — so a bare
# variable dies at the subshell boundary). On SUCCESS the verifier writes
# its own elapsed boot-to-green latency here; the caller reads it into
# HANDBACK_VERIFY_ELAPSED_S (global, same-shell) and promote.sh's commit
# row stamps `verify_elapsed_s=<n>` — the journal sample the D6-v ceiling
# reader below consumes. Design §2.1 option (v): MECHANISM ONLY — no
# inflation; with no sample the ceiling stays HANDBACK_VERIFY_BUDGET_S=300.
HANDBACK_VERIFY_ELAPSED_S=""
HANDBACK_VERIFY_ELAPSED_FILE="$(mktemp -u "${TMPDIR:-/tmp}/ensemble-hbk-velapsed.$$.XXXXXX" 2>/dev/null || printf "%s" "${TMPDIR:-/tmp}/ensemble-hbk-velapsed.$$")"
_handback_verify_elapsed_write() {
    _handback_channel_write "${HANDBACK_VERIFY_ELAPSED_FILE:-}" "${1:-}"
    # LOGGING SYMMETRY (hbk-err fix, 2026-11-22): the elapsed channel's
    # payload is a MEASUREMENT (the D6-v journal sample), not a diagnosis —
    # but it must still reach the operator's log, and the seam must NEVER be
    # stdout: the verify runs inside a $() whose stdout IS the captured
    # MainPID, so a stdout write here would corrupt the capture. The
    # unbuffered stderr seam is the safe one (same idiom as _hb_verify_diag,
    # minus the WARN tag — a latency sample is not a warning).
    printf '%s[%s]: hand-back verify: boot-to-green elapsed=%ss (the D6-v journal sample)\n' \
        "${LOG_TAG:-lib}" "${UP_TARGET:-lib}" "${1:-}" >&2 2>/dev/null || true
}
_handback_verify_elapsed_clear() {
    rm -f "${HANDBACK_VERIFY_ELAPSED_FILE:-}" 2>/dev/null || true
}
_handback_verify_elapsed_text() {
    local _f="${HANDBACK_VERIFY_ELAPSED_FILE:-}"
    if [ -s "$_f" ]; then head -c 16 "$_f" 2>/dev/null; fi
    return 0
}
# _handback_channels_cleanup — remove ALL THREE hand-back side-channel files.
# The pre-fix cleanup covered only the err + reason channels, and only in the
# ceremony scripts' failure branch — the ELAPSED file was cleaned by NOBODY
# outside lib.sh, so every ceremony that did not reach the hand-back leaked
# it. With the LAZY MINT (2026-11-22) the file exists only when it carries a
# payload, so removing all three here is the complete cleanup. Best-effort:
# a tmp failure must never break the exit path.
_handback_channels_cleanup() {
    rm -f "${HANDBACK_ERROR_FILE:-}" 2>/dev/null || true
    rm -f "${HANDBACK_VERIFY_REASON_FILE:-}" 2>/dev/null || true
    rm -f "${HANDBACK_VERIFY_ELAPSED_FILE:-}" 2>/dev/null || true
    return 0
}

# _handback_unit_state <unit> — the D6-(ii) BOUNDED STATE PROBE: ONE
# `systemctl show <unit> -p ActiveState -p SubState -p MainPID` call
# (assignment output — each field self-names, so one probe yields all
# three). Replaces the verifier's bare unbounded MainPID poll AND
# provides the PENDING/FAILED discriminator the rc alone cannot express
# (an is-active rc=3 is "not active" AND "failed"'s neighbour — the
# STATE STRING is the only honest key; design §2.1: "keyed on the state
# string, never the rc").
# Sets the globals _HB_STATE_ACTIVE / _HB_STATE_SUB / _HB_STATE_MAINPID
# / _HB_STATE_RC. rc 0 = systemctl ANSWERED (a field may still be empty
# — the CALLER classifies); 124 = bounded-wait timeout (the only
# genuine timeout; _run_bounded collapses 137|143 → 124); any other
# nonzero = probe tool failure (classify UNKNOWN — never evidence).
# stdout of the probe itself is consumed here; nothing leaks to the
# caller's captured output.
_handback_unit_state() {
    local unit="$1" out=""
    _HB_STATE_ACTIVE=""; _HB_STATE_SUB=""; _HB_STATE_MAINPID=""; _HB_STATE_RC=""
    out="$(_run_bounded "$SYSTEMCTL_PROBE_BUDGET_S" -- "$SYSTEMCTL_BIN" show "$unit" \
            -p ActiveState -p SubState -p MainPID 2>/dev/null)"
    _HB_STATE_RC=$?
    _HB_STATE_ACTIVE="$(printf '%s\n' "$out" | sed -n 's/^ActiveState=//p' | head -1)"
    _HB_STATE_SUB="$(printf '%s\n' "$out" | sed -n 's/^SubState=//p' | head -1)"
    _HB_STATE_MAINPID="$(printf '%s\n' "$out" | sed -n 's/^MainPID=//p' | head -1)"
    return 0
}

# _handback_measured_verify_ceiling_s — the D6-v ceiling reader
# (MECHANISM ONLY). Scans the journal history[] for the MOST RECENT
# `serving_green_commit` row — or the executor's own `commit` row —
# carrying the additive `verify_elapsed_s=<n>` sample (writers: the
# executor's commit row, promote.sh 8a; future writers may join), takes
# that ONE most-recent sample, and prints
#   min( clamp(sample, HANDBACK_VERIFY_FLOOR_S, MEASURED_CEILING), 780 )
# The 780 cap = EXECUTOR_STALENESS_WINDOW_S (900,
# daemon/services/upgrade_journal_sweep.py EXECUTOR_STALENESS_WINDOW_S —
# T8: name-anchored, the old :117 cite was off by one) − 120: the verify may
# never extend past the sweep's owner-staleness window (design §2.1
# interaction constraint). Returns 1 (no output) when no sample exists
# — the caller then stays at the HANDBACK_VERIFY_BUDGET_S default (NO
# inflation). Deliberately does NOT read anything but the journal: the
# sample is only ever a JOURNAL-recorded observation.
_handback_measured_verify_ceiling_s() {
    local json hist entry sample="" tok v
    json="$(journal_read 2>/dev/null)" || return 1
    hist="$(_json_sub "$json" "history" 2>/dev/null)" || return 1
    case "$hist" in ''|null|'[]') return 1 ;; esac
    while IFS= read -r entry; do
        [ -n "$entry" ] || continue
        case "$entry" in
            *"\"event\":\"serving_green_commit\""*|*"\"event\": \"serving_green_commit\""*|\
            *"\"event\":\"commit\""*|*"\"event\": \"commit\""*) ;;
            *) continue ;;
        esac
        tok="$(printf '%s' "$entry" | grep -o 'verify_elapsed_s=[0-9][0-9]*' | head -1 | cut -d= -f2)"
        [ -n "$tok" ] && sample="$tok"
    done < <(_journal_history_entries "$hist")
    [ -n "$sample" ] || return 1
    case "$sample" in *[!0-9]*) return 1 ;; esac
    v="$sample"
    [ "$v" -lt "$HANDBACK_VERIFY_FLOOR_S" ] && v="$HANDBACK_VERIFY_FLOOR_S"
    [ "$v" -gt "$HANDBACK_VERIFY_MEASURED_CEILING_S" ] && v="$HANDBACK_VERIFY_MEASURED_CEILING_S"
    # 780 = EXECUTOR_STALENESS_WINDOW_S(900) − 120 (defensive: subsumed
    # by the 600 ceiling while it stands, but pins the staleness bound
    # independently of the ceiling knob).
    [ "$v" -gt 780 ] && v=780
    printf '%s' "$v"
    return 0
}
_supervision_launchd_bootstrap() {
    local plist="${1:-}" domain
    [ -n "$plist" ] || return 1
    [ -f "$plist" ] || return 1
    command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1 || return 1
    domain="$(_supervision_launchd_domain)"
    local out rc
    out="$(_run_bounded "$LAUNCHD_PROBE_BUDGET_S" -- "$LAUNCHCTL_BIN" bootstrap "$domain" "$plist" 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        HANDBACK_LAST_ERROR=""
        _handback_err_file_clear
        return 0
    fi
    # Fix #4: stash the launchctl diagnosis (one line, bounded — journal
    # detail is a single-line quoted string). MAJOR #1: also out-of-band
    # (file) — this function is called as a STATEMENT so the variable
    # survives, but the file keeps ONE channel semantics for the readers.
    HANDBACK_LAST_ERROR="$(printf 'launchctl bootstrap %s %s failed (rc=%s): %s' "$domain" "$plist" "$rc" "$(printf '%s' "$out" | tr '\n' ';' | tr -s ' ' | cut -c1-400)")"
    _handback_err_file_write "$HANDBACK_LAST_ERROR"
    case "$out" in
        *"already bootstrapped"*|*"already loaded"*|*"Input/output error"*)
            # "already loaded" is NOT success on its own — return a distinct
            # code (2) so the caller revalidates the LIVE job rather than
            # trusting a no-op rc. BENIGN arm: clear the stash (F3 council
            # fix) — this is attempt N of the caller's retry loop, and a
            # diagnosis captured by an EARLIER failed attempt must not ride
            # a later halt detail in the same run (the success arm at the
            # top clears it; this arm is success-shaped too, modulo the
            # caller's mandatory revalidation).
            HANDBACK_LAST_ERROR=""
            _handback_err_file_clear
            return 2 ;;
    esac
    [ "$rc" -eq 124 ] && return 1
    return 1
}

# _supervision_launchd_plist_path — resolve the launchd job's plist path.
# THREE rungs (2026-11-14 hand-back halt, PC-1 — incident
# docs/incidents/2026-11-14-macos-launchd-handback-halt.md): the promote
# stop phase BOOTOUTS the job BEFORE the hand-back (lib.sh
# stop_via_stop_script → _supervision_stop_launchd), so at hand-back time
# `launchctl print` answers "Could not find service" exactly when the
# resolver is needed most — the v0.30.5/v0.30.9 halts. Resolution:
#   1. LIVE probe — `launchctl print` `path =` (FIRST, backward compat:
#      call sites that run pre-stop, e.g. the disarm warning, still get
#      the launchd-attested verbatim path).
#   2. PRE-STOP STASH — SUPERVISION_LAUNCHD_PLIST_PATH, captured by
#      supervision_classify while the job was still registered (belt).
#   3. DETERMINISTIC FALLBACK — "$HOME/Library/LaunchAgents/<label>.plist"
#      (braces): VALID under the ratified filename==label convention
#      (incident §7b — the repo plists are label-verbatim since the
#      2026-11-15 rename, so the host file is derivable). The old "never
#      label-derive" rule guarded a filename≠label install that the
#      repo's own tooling can no longer produce; every candidate is still
#      verified with -f, so a stale/mis-named file resolves to NOTHING
#      (fail-closed), never to a wrong path.
# Empty output + rc 1 ⇒ unresolvable ⇒ the caller halts fail-safe
# (Amendment #1 — never a nohup fallback on Darwin).
#
# Fix #4 (ENSEMBLE-32 repair lane; incident §5.2): the live probe's
# stderr is CAPTURED, not discarded — on total resolution failure it is
# stashed into HANDBACK_LAST_ERROR so the halt journal carries WHY the
# resolver could not see the job (the v0.30.5 halt's "Could not find
# service" was never recorded).
_supervision_launchd_plist_path() {
    local label="${1:-}" dump p errtmp
    [ -n "$label" ] || return 1
    # Rung 1 — live probe (the job may still be registered).
    errtmp="$(mktemp -t hbk.XXXXXX 2>/dev/null || printf '/tmp/hbk.%s.err' "$$")"
    if command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1; then
        dump="$(_run_bounded "$LAUNCHD_PROBE_BUDGET_S" -- "$LAUNCHCTL_BIN" print "$(_supervision_launchd_domain)/${label}" 2>"$errtmp")" || dump=""
        p="$(printf '%s\n' "$dump" | sed -n 's/^[[:space:]]*path = //p' | head -1)"
        if [ -n "$p" ] && [ -f "$p" ]; then
            rm -f "$errtmp" 2>/dev/null
            _handback_err_file_clear
            printf '%s\n' "$p"
            return 0
        fi
    fi
    # Rung 2 — the pre-stop stash (survives the bootout).
    p="${SUPERVISION_LAUNCHD_PLIST_PATH:-}"
    if [ -n "$p" ] && [ -f "$p" ]; then
        rm -f "$errtmp" 2>/dev/null
        _handback_err_file_clear
        printf '%s\n' "$p"
        return 0
    fi
    # Rung 3 — label-verbatim fallback (ratified convention).
    p="$HOME/Library/LaunchAgents/${label}.plist"
    if [ -f "$p" ]; then
        rm -f "$errtmp" 2>/dev/null
        _handback_err_file_clear
        printf '%s\n' "$p"
        return 0
    fi
    # All rungs unresolvable — stash the live probe's stderr (Fix #4).
    # MAJOR #1 (review round 2): the VARIABLE stash dies in the caller's
    # `$()` consumption subshell (restart_via_launcher) — this function is
    # THE resolver on the incident path, so the diagnosis goes OUT-OF-BAND
    # to HANDBACK_ERROR_FILE (subshell-proof); the halt readers use
    # _handback_last_error_text. The variable write stays for plain-call
    # consumers (and is harmless in the subshell).
    HANDBACK_LAST_ERROR="$(printf "plist resolution failed for job '%s' (launchctl print: %s)" "$label" "$(printf '%s' "$(cat "$errtmp" 2>/dev/null)" | tr '\n' ';' | tr -s ' ' | cut -c1-400)")"
    _handback_err_file_write "$HANDBACK_LAST_ERROR"
    rm -f "$errtmp" 2>/dev/null
    return 1
}

# ═══════════════ Executor SELF-DETACH (Fix #5, ENSEMBLE-32 Family-3) ═══════
# A promote executor that runs INSIDE the daemon's process tree (the
# agent-bash lane: daemon → bash tool → agent shell → promote.sh) dies with
# its own stop sweep: the launchd bootout of com.ensemble.prod (or the pid
# sweep's escalation) tears down the WHOLE launchd job tree INCLUDING the
# executor — incident docs/incidents/2026-11-14-macos-launchd-handback-halt.md
# §7 (00:50:53, the v0.30.9 promote killed mid-stop BEFORE the flip, defect
# #7). The §7b proven pattern, productized: on darwin, an in-tree executor
# re-execs ITSELF inside a transient ONE-SHOT LaunchAgent (label-verbatim
# plist, RunAtLoad, bootout + plist-removal MANDATORY on every path — the
# RunAtLoad-at-login footgun) and propagates the detached exit code. This is
# the darwin mirror of the VM's systemd-run scope guarantee
# (docs/incidents/2026-09-28-promote-cgroup-teardown.md — setsid escapes a
# process GROUP, never a systemd cgroup / a launchd job; only a
# supervisor-owned unit does).
#
# AMENDMENT #1 UNTOUCHED: the no-nohup-fallback rule governs the DAEMON (the
# SERVING process must stay launchd-supervised — 2026-11-11 incident). ONLY
# the EXECUTOR detaches, and it detaches via launchd itself (a supervised,
# bounded, one-shot job with a log file — never a nohup'd orphan).
#
# Test seams (SYSTEMCTL_BIN / LAUNCHCTL_BIN house discipline):
#   - LAUNCHCTL_BIN drives every launchctl call (PATH-injected stub works).
#   - `ps` is PATH-resolved for the ppid/args/comm probes (a PATH-injected
#     stub drives scripted ancestry chains in tests).
#   - PROMOTE_DETACH_WAIT_S / PROMOTE_DETACH_POLL_S bound the wait.

# The marker env var: set (exported) in the DETACHED child's environment so
# the re-executed promote skips the detach block (recursion guard).
ENSEMBLE_PROMOTE_DETACH_MARKER="${ENSEMBLE_PROMOTE_DETACH_MARKER:-ENSEMBLE_PROMOTE_SELF_DETACHED}"
PROMOTE_DETACH_WAIT_S="${PROMOTE_DETACH_WAIT_S:-1800}"
PROMOTE_DETACH_POLL_S="${PROMOTE_DETACH_POLL_S:-2}"

# _detach_last_error — Fix #4 discipline (incident §5.2): a detach failure
# must be RECORDED, not swallowed — the v0.30.5/v0.30.9 halts were
# unreproducible because launchctl output was discarded.
_DETACH_LAST_ERROR=""

# _executor_detach_ps_field <pid> <field> — one ps probe (ppid|args|comm).
# PATH-resolved `ps` so tests script ancestry chains; empty output on any
# probe failure (a dead pid mid-walk just terminates the chain).
_executor_detach_ps_field() {
    ps -o "$2=" -p "$1" 2>/dev/null | head -1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

# _executor_detach_in_tree — 0 IFF this shell sits inside the install's
# launcher/daemon lineage. Walks the ppid chain from $$ (bounded depth 16 —
# paranoia against exotic trees, same bound family as
# _supervision_pid_in_launchd_lineage), and counts as IN-TREE when an
# ancestor either (a) carries THIS install's launcher/daemon shapes in its
# argv (tier-1a anchored-path shapes MIRRORED from _supervision_owned_pids
# — the pinned mirror discipline: stop-ensemble.sh stays a leaf, lib.sh owns
# the lib-side shapes), or (b) is ensemble-SHAPED (comm/args say
# launcher.sh/ensemble-prod) AND its cwd IS this install (the tier-1b
# conjunct). The cwd conjunct is LOAD-BEARING, not decoration: a sandbox or
# fixture promote runs inside the REAL daemon's tree of a DIFFERENT install
# — an unanchored "ancestor named ensemble-prod" would misclassify every
# such run as in-tree and bootstrap a REAL LaunchAgent for a fixture
# promote (observed in the staleness pack's e2e legs, 2026-11-15).
# Requires INSTALL_DIR (resolve_env must have run). Never throws.
_executor_detach_in_tree() {
    [ -n "${INSTALL_DIR:-}" ] || return 1
    local esc esc_phys phys pid args comm cwd depth=0
    esc="$(_supervision_ere_escape "$INSTALL_DIR")"
    phys="$(cd -P "$INSTALL_DIR" 2>/dev/null && pwd || printf '%s' "$INSTALL_DIR")"
    esc_phys="$(_supervision_ere_escape "$phys")"
    pid="$(_executor_detach_ps_field "$$" ppid)"
    while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ] && [ "$depth" -lt 16 ]; do
        args="$(_executor_detach_ps_field "$pid" args)"
        comm="$(_executor_detach_ps_field "$pid" comm)"
        if printf '%s' "$args" 2>/dev/null | grep -Eq \
            "(${esc}|${esc_phys})/(current/)?ensemble-prod( |$)|${esc}/launcher\.sh( |$)"; then
            return 0
        fi
        case "$comm $args" in
            *ensemble-prod*|*launcher.sh*)
                cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
                if [ "$cwd" = "$INSTALL_DIR" ] || [ "$cwd" = "$phys" ]; then
                    return 0
                fi
                ;;
        esac
        pid="$(_executor_detach_ps_field "$pid" ppid)"
        depth=$((depth + 1))
    done
    return 1
}

# _executor_detach_xml_escape <string> — minimal plist XML text escaping.
_executor_detach_xml_escape() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

# _executor_detach_cleanup <domain> <label> <plist> <tmpdir> — the MANDATORY
# bootout + scratch removal (the RunAtLoad-at-login footgun: a spent one-shot
# left registered would RE-FIRE the promote at the next login — incident
# §7b, the operator had to hand-remove the spent com.ensemble.promote-v03009).
# Idempotent by construction (benign "not found" bootout class, rm -f); the
# DETACHED LOG FILE is deliberately kept (forensics — incident §7b's log was
# the only artifact carrying the hand-back error).
_executor_detach_cleanup() {
    local domain="${1:-}" label="${2:-}" plist="${3:-}" tmpdir="${4:-}"
    if [ -n "$label" ]; then
        _supervision_launchd_bootout "$label" >/dev/null 2>&1 || true
    fi
    [ -n "$plist" ] && rm -f "$plist" 2>/dev/null
    [ -n "$tmpdir" ] && [ -d "$tmpdir" ] && rm -rf "$tmpdir" 2>/dev/null
    return 0
}

# _executor_detach_self <promote-script> <argv…> — re-exec the ENTIRE promote
# inside a transient one-shot LaunchAgent, wait for completion, and print
# NOTHING on stdout except the final rc via the function's return code.
# Contract:
#   - bootstrap failure ⇒ belt-cleanup + rc 1 with _DETACH_LAST_ERROR set
#     (the caller refuses fail-closed — an in-tree executor that cannot
#     detach would die with its own stop sweep, the exact defect #7 shape).
#   - wait budget exhausted (PROMOTE_DETACH_WAIT_S) without a done-file ⇒
#     cleanup + rc 124 (the house bounded-wait convention).
#   - job vanished without writing the done-file (spawn failure class) ⇒
#     cleanup + rc 1, _DETACH_LAST_ERROR set.
#   - otherwise rc = the detached promote's exit code (from the done-file).
# The DETACHED CHILD self-cleans too (done-file first, then bootout + rm) —
# so cleanup happens even if THIS in-tree waiter is SIGKILLed mid-wait
# (the incident's own shape).
_executor_detach_self() {
    local script="${1:-}"
    # D-5 (E2E round 3, cycle-3 🔴): $1 IS the promote script — consumed
    # here. WITHOUT this shift the argv-quoting loop below re-includes the
    # script path in esc_args and the detached child runs
    # `promote.sh promote.sh <args>` → promote.sh refuses `unknown flag`
    # rc 78 in ~2s (cycle-3 E2E: the detach machinery worked end-to-end,
    # then the wrapper duplicated the script). Every other positional
    # ($2… — the caller's own argv, promote.sh:182) forwards verbatim;
    # there is no other preamble positional consumption in this function.
    [ "$#" -gt 0 ] && shift
    [ -n "$script" ] && [ -f "$script" ] || {
        _DETACH_LAST_ERROR="executor-detach: promote script not found: ${script:-<empty>}"
        return 1
    }
    [ -n "${INSTALL_DIR:-}" ] || {
        _DETACH_LAST_ERROR="executor-detach: INSTALL_DIR unresolved (resolve_env must run first)"
        return 1
    }
    command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1 || {
        _DETACH_LAST_ERROR="executor-detach: no launchctl on PATH (LAUNCHCTL_BIN=${LAUNCHCTL_BIN}) — cannot detach; the caller refuses (an in-tree executor without detachment dies with its own stop sweep)"
        return 1
    }
    local uid domain label runid tmpdir envfile wrapper plist logfile donefile
    uid="$(id -u)"
    domain="gui/${uid}"
    runid="promote-$(printf '%s' "${VERSION:-run}" | tr -c 'A-Za-z0-9' '-' | sed -e 's/-\{1,\}/-/g' -e 's/^-*//' -e 's/-*$//')-$(_now_epoch)"
    label="com.ensemble.${runid}"
    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/ens-promote-detach.XXXXXX")" || {
        _DETACH_LAST_ERROR="executor-detach: cannot create the detach scratch dir"
        return 1
    }
    envfile="${tmpdir}/env.sh"
    wrapper="${tmpdir}/wrapper.sh"
    plist="${tmpdir}/${label}.plist"
    donefile="${tmpdir}/done.rc"
    logfile="${INSTALL_DIR}/data/${runid}.detach.log"
    mkdir -p "$(dirname "$logfile")" 2>/dev/null || true

    # Env snapshot for the detached child: EVERYTHING exported here, minus
    # the one poison key (ENSEMBLE_LAUNCHER_STATE_FILE must NEVER propagate —
    # the env-parity rule), plus the marker that stops the child from
    # re-detaching. Plain (non-exported) shell variables are NOT carried —
    # the child re-derives them exactly like a fresh invocation would.
    {
        declare -px | grep -v "^declare -x ENSEMBLE_LAUNCHER_STATE_FILE=" || true
        printf 'export %s=1\n' "$ENSEMBLE_PROMOTE_DETACH_MARKER"
    } > "$envfile" 2>/dev/null

    # Wrapper: env → promote → rc → done-file → SELF-CLEAN (bootout BEFORE
    # rm; done-file BEFORE bootout so the waiter reads the rc even if the
    # bootout kills this wrapper mid-clean). Forensics log kept. Script and
    # argv are single-quote-escaped into the wrapper body.
    local esc_args arg_q launchctl_abs script_q
    script_q="'$(printf '%s' "$script" | sed -e "s/'/'\\\\''/g")'"
    esc_args=""
    for arg_q in "$@"; do
        esc_args="$esc_args '$(printf "%s" "$arg_q" | sed -e "s/'/'\\\\''/g")'"
    done
    launchctl_abs="$(command -v "$LAUNCHCTL_BIN" 2>/dev/null || printf 'launchctl')"
    # D-6 (E2E round 4, cycle-4 🔴 anomaly 7ce14c96): thread REPO_ROOT into
    # the plist EnvironmentVariables EXPLICITLY. HOOK POINT: the plist
    # template below (this is the detach machinery's own template — no
    # daemon-side allowlist is widened). Convention: allowlist-of-one,
    # per the EXECUTOR_ENV_ALLOWLIST discipline (upgrade_journal.py:3034) —
    # a NAMED key threaded deliberately, never a blanket env copy; the
    # envfile snapshot (declare -px) already carries EXPORTED vars, but it
    # misses a set-but-NON-exported REPO_ROOT (an operator shell that
    # assigned it plainly), which is exactly the checkout-leg resolution
    # the config-compat gate reads (lib.sh _config_compat_check leg 4).
    # Set-but-empty stays ABSENT (no empty-string env entries — launchd
    # would hand the child a literal empty REPO_ROOT and the gate's
    # "-f" probe would still miss, but the refusal detail would render
    # a confusing empty path instead of "?").
    local detach_repo_root_xml=""
    if [ -n "${REPO_ROOT:-}" ]; then
        detach_repo_root_xml="    <key>EnvironmentVariables</key>
    <dict>
        <key>REPO_ROOT</key><string>$(_executor_detach_xml_escape "$REPO_ROOT")</string>
    </dict>
"
    fi
    cat > "$wrapper" <<WEOF
#!/bin/bash
# Executor self-detach wrapper (ENSEMBLE-32 Fix #5) — generated by lib.sh
# _executor_detach_self; run inside the transient one-shot LaunchAgent
# ${label}. Cleanup below is MANDATORY (RunAtLoad-at-login footgun,
# incident 2026-11-14 §7b).
. '${envfile}'
bash ${script_q}${esc_args}
rc=\$?
printf '%s\n' "\$rc" > '${donefile}'
'${launchctl_abs}' bootout '${domain}/${label}' >/dev/null 2>&1 || true
rm -f '${plist}' '${wrapper}' '${envfile}' >/dev/null 2>&1 || true
exit "\$rc"
WEOF
    chmod +x "$wrapper"

    # Plist: label-verbatim FILENAME == LABEL (Family-1 ratified invariant,
    # incident §7b root cause). RunAtLoad one-shot, KeepAlive false,
    # ExitTimeOut bounded. NO plist ever lands in ~/Library/LaunchAgents —
    # this one lives in the private scratch dir and is bootstrapped by path.
    cat > "$plist" <<PLEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$(_executor_detach_xml_escape "$label")</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$(_executor_detach_xml_escape "$wrapper")</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
    <key>ExitTimeOut</key><integer>30</integer>
    <key>StandardOutPath</key><string>$(_executor_detach_xml_escape "$logfile")</string>
    <key>StandardErrorPath</key><string>$(_executor_detach_xml_escape "$logfile")</string>
${detach_repo_root_xml}</dict>
</plist>
PLEOF

    # Belt cleanup trap for THIS shell: whatever happens to the wait
    # (timeout, TERM/INT, exit), bootout + scratch removal run here too.
    # Idempotent with the wrapper's own self-clean. The trap fires at the
    # caller's exit (the promote.sh detach block exits right after us).
    trap "_executor_detach_cleanup '${domain}' '${label}' '${plist}' '${tmpdir}' >/dev/null 2>&1 || true" EXIT

    _log "executor self-detach: bootstrapping one-shot LaunchAgent ${label} (plist=${plist}, log=${logfile})"
    local out rc
    out="$(_run_bounded "$LAUNCHD_PROBE_BUDGET_S" -- "$LAUNCHCTL_BIN" bootstrap "$domain" "$plist" 2>&1)"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        _DETACH_LAST_ERROR="$(printf 'launchctl bootstrap %s %s failed (rc=%s): %s' "$domain" "$plist" "$rc" "$(printf '%s' "$out" | tr '\n' ';' | tr -s ' ' | cut -c1-400)")"
        _warn "executor self-detach FAILED: ${_DETACH_LAST_ERROR}"
        _executor_detach_cleanup "$domain" "$label" "$plist" "$tmpdir"
        return 1
    fi

    # Bounded wait on the done-file. Fast-fail: the job VANISHED from the
    # domain without writing the done-file (spawn failure class — e.g. the
    # wrapper died before exec; launchctl print answers empty).
    local deadline waited job_dump
    deadline=$(( $(_now_epoch) + PROMOTE_DETACH_WAIT_S ))
    waited=0
    while [ "$(_now_epoch)" -lt "$deadline" ]; do
        if [ -f "$donefile" ]; then
            rc="$(cat "$donefile" 2>/dev/null)"
            case "$rc" in
                ''|*[!0-9]*) _DETACH_LAST_ERROR="executor-detach: done-file unreadable/garbage (${donefile})"; _warn "executor self-detach: ${_DETACH_LAST_ERROR}"; _executor_detach_cleanup "$domain" "$label" "$plist" "$tmpdir"; return 1 ;;
            esac
            _log "executor self-detach: detached promote completed rc=${rc} after ${waited}s (log=${logfile})"
            return "$rc"
        fi
        sleep "$PROMOTE_DETACH_POLL_S"
        waited=$((waited + PROMOTE_DETACH_POLL_S))
        if [ "$waited" -ge 10 ]; then
            job_dump="$(_run_bounded "$LAUNCHD_PROBE_BUDGET_S" -- "$LAUNCHCTL_BIN" print "${domain}/${label}" 2>/dev/null)"
            if [ -z "$job_dump" ] && [ ! -f "$donefile" ]; then
                _DETACH_LAST_ERROR="executor-detach: LaunchAgent ${label} vanished from ${domain} without writing the done-file (spawn failure class; log=${logfile})"
                _warn "executor self-detach: ${_DETACH_LAST_ERROR}"
                _executor_detach_cleanup "$domain" "$label" "$plist" "$tmpdir"
                return 1
            fi
        fi
    done
    _DETACH_LAST_ERROR="$(printf 'executor-detach: the detached promote did not complete within PROMOTE_DETACH_WAIT_S=%ss (label=%s, log=%s)' "$PROMOTE_DETACH_WAIT_S" "$label" "$logfile")"
    _warn "executor self-detach WAIT BUDGET EXHAUSTED: ${_DETACH_LAST_ERROR}"
    _executor_detach_cleanup "$domain" "$label" "$plist" "$tmpdir"
    return 124
}

# _pipeline_self_excluded <pid> — DEFENSE-IN-DEPTH SELF-EXCLUSION for the
# kill sweeps (Fix #5, ENSEMBLE-32 Family-3; incident 2026-11-14 §7 defect
# #7). MAJOR #3 (review round 2): the previous shape ALSO excluded PPID's
# PARENT when the sweep ran in-tree (_pipeline_self_excluded_in_tree
# ancestor walk) — and the walk ANCHORS on the daemon, so in-tree it
# excluded the TARGET itself: the daemon was classified, filtered out of
# the kill sweep, and the stop false-succeeded while the daemon lived (the
# exact class reproduced live in scripts/stop-ensemble.sh _stop_pids; the
# 16d TERM-pin regression is the same family — a blanket own-pgid
# filter drops the daemon because the in-tree sweep shares the launchd
# job group). SHIPPED SHAPE (mirrors _stop_pids):
#   1. executor identity ONLY: _PIPELINE_SELF_PID/$PPID (seam-injectable
#      for tests);
#   2. HARD GUARD (never-exclude-the-TARGET): a pid that is a sweep
#      CANDIDATE is never excludable — the daemon dies even when it is
#      literally $PPID (a daemon-spawned sweep).
# A detached executor lives in a different tree BY CONSTRUCTION and is not
# an ancestor of the sweep at all, so it needs no exclusion here. Prints
# the surviving pids, one per line, ascending, deduped.
_PIPELINE_SELF_PID="${_PIPELINE_SELF_PID:-$$}"
_PIPELINE_SELF_PPID="${_PIPELINE_SELF_PPID:-$PPID}"
_pipeline_self_excluded() {
    local my_pid my_ppid pid my_excluded _c _is_target
    my_pid="$_PIPELINE_SELF_PID"
    my_ppid="$_PIPELINE_SELF_PPID"
    my_excluded="$my_pid $my_ppid"
    local my_excluded_new=""
    for _c in $my_excluded; do
        _is_target=0
        for pid in $(printf '%s\n' "$@" | grep -E '^[0-9]+$' | sort -n | awk '!seen[$0]++'); do
            [ "$pid" = "$_c" ] || continue
            _is_target=1
            break
        done
        if [ "$_is_target" = "1" ]; then
            : "never-exclude-the-target: $_c is a sweep candidate — the target wins (MAJOR #3)"
        else
            my_excluded_new="$my_excluded_new $_c"
        fi
    done
    for pid in $(printf '%s\n' "$@" | grep -E '^[0-9]+$' | sort -n | awk '!seen[$0]++'); do
        case " $my_excluded_new " in *" $pid "*) continue ;; esac
        printf '%s\n' "$pid"
    done
}

# _hb_verify_diag <message> — VERIFY-PHASE DIAGNOSTIC EMITTER (F16 /
# AC-OBS-3, 2026-11-19). DUAL SINK, BY CONSTRUCTION:
#   1. _warn            → the per-run NARRATIVE file (ENSEMBLE_UPGRADE_LOG_FILE)
#                         when that sink is set, else stderr — the
#                         operator-facing line (unchanged shape).
#   2. printf ... >&2  → the executor unit's STDERR, UNBUFFERED, ALWAYS.
#
# WHY THE SECOND SINK (F16 / C-8 — the mechanism, not the symptom):
#   _warn routes to the narrative sink whenever it is set — and the ceremony
#   ALWAYS sets it (spawn_executor threads ENSEMBLE_UPGRADE_LOG_FILE into
#   the transient unit). The executor unit's stdio is owned by systemd
#   (StandardOutput/StandardError=append:<run_id>.payload.log —
#   daemon/tools/upgrade_journal.py build_service_argv), so the narrative
#   file and the PAYLOAD log are DIFFERENT files. The verify-phase
#   diagnostics therefore never reached the payload log: both surviving
#   runs (r-20261119-043446-c8da, r-20261119-132823-3a3c) end at the verify
#   budget line — the ONE verify-phase line emitted with an explicit
#   `printf >&2` (lib.sh's budget banner).
#   `printf >&2` is UNBUFFERED (a direct write(2) on the fd) ⇒ the flush
#   requirement of AC-OBS-3 ("unbuffered stderr + an explicit _warn before
#   EACH fail-fast arm") holds by construction, with no dependence on the
#   shell's stdio buffering.
#
# [7b SUBSTRING TRAP] — the banned systemctl-start literal must never
# appear in a log line; the messages below carry none.
_hb_verify_diag() {
    local msg="$1"
    _warn "$msg"
    printf '%s[%s]: WARN: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "$msg" >&2 2>/dev/null
}

# _handback_serving_probe — the hand-back's SERVING conjunct
# (D-P1-4/D-P1-5, promote-handback-halt Phase 1). Called ONLY when PORT
# is set and curl is available; assumes the port-listener fast condition
# was already established (or lsof is absent — degraded, serving-only).
# Reuses the house classifier `_probe_once_classified` VERBATIM
# (AM-P1-2: taxonomy 0 green / 1 red / 2 transient / 3 suppressed —
# never derive a second one): 503 + curl rc 7/28 are transient, other
# 4xx/5xx are red. /livez ONLY — the hand-back deliberately does NOT
# probe /readyz (D-P1-4 rejected alternative (ii): readyz carries
# readiness components legitimately not green during the ~3 min boot —
# demanding ready here would halt-for-human on every slow-but-healthy
# boot, re-creating the incident class; readiness is the GATE's
# question at the promote.sh readyz boundary).
#
# Return: 0 = serving the expected release (2xx ∧ version==expected —
#           version compared only when PROMOTE_EXPECTED_VERSION is set);
#         1 = fail-fast (probe-level): 2xx with the WRONG version, 2xx
#           with the version field absent/unparseable while the expected
#           version is KNOWN (AM-P1-1: definitively unverifiable as the
#           right release), or a house-classifier RED answer. Probe-level
#           vocabulary ONLY (REV-R-4): "fails fast" says the PROBE stops
#           probing — it prescribes NO disposition (no rollback, no
#           quarantine language); the CALLER owns the halt.
#         2 = transient (keep polling — the daemon is booting).
_handback_serving_probe() {
    local running hspc_rc=0 __attr_rc=0
    _probe_once_classified "/livez" "${PORT:-}" || hspc_rc=$?
    case "$hspc_rc" in
        0)
            # green-class (green or busy-amnesty) — the body is in _PROBE_BODY
            # Fix #2: the green answer must come from the ATTRIBUTED daemon.
            # A mismatch is probe-level FAIL-FAST (the probe's own vocabulary:
            # no amount of polling fixes a foreign answerer — the incident's
            # nohup-serves-while-launchd-crash-loops shape).
            # RESOLVED H1 (branch T7b / D-P1-4 × L 0f133d2b9 D6-(iv), AC-D6-6):
            # explicit rc capture stays; the definitive rc=1 arm keeps L's
            # reason-channel write + dual-sink diagnostic; the transient rc=2
            # arm keeps polling and writes NO halt reason (there is no halt).
            __attr_rc=0
            _gate_serving_attribution "hand-back verify" || __attr_rc=$?
            case "$__attr_rc" in
                1)
                    # D6-(iv) / AC-D6-6: the REFUSAL DETAIL is the reason the
                    # halt must carry — captured on the dedicated verifier-
                    # reason channel (the launchd HB_ERR channel is untouched)
                    # and emitted to BOTH sinks (the payload log included — F16).
                    _handback_verify_reason_write "attribution-refused: ${GATE_ATTRIBUTION_DETAIL:-<no attribution detail captured>}"
                    _hb_verify_diag "hand-back verify: serving-process attribution REFUSED${GATE_ATTRIBUTION_DETAIL:+ — $GATE_ATTRIBUTION_DETAIL} (probe-level fail-fast, Fix #2); NOT success"
                    return 1
                    ;;
                2)
                    # transient: keep polling within the caller's verify budget.
                    # No reason write: rc 2 is NOT a halt, and a stale reason
                    # would poison a later halt in the same run.
                    return 2
                    ;;
                0) : ;;
                *) return 2 ;;   # contract violation — treated as transient (I-B13)
            esac
            if [ -n "${PROMOTE_EXPECTED_VERSION:-}" ]; then
                running="$(_json_field "${_PROBE_BODY:-}" version 2>/dev/null)" || running=""
                if [ -z "$running" ]; then
                    _handback_verify_reason_write "livez-version-absent: observed=<absent/unparseable> expected=${PROMOTE_EXPECTED_VERSION} — the /livez answer carried no parsable version while the expected version is known"
                    _hb_verify_diag "hand-back verify: /livez answered 2xx but the version field is absent/unparseable while the expected version is known (${PROMOTE_EXPECTED_VERSION}) — definitively unverifiable as the right release (probe-level fail-fast); NOT success"
                    return 1
                fi
                if [ "$running" != "$PROMOTE_EXPECTED_VERSION" ]; then
                    _handback_verify_reason_write "livez-version-mismatch: observed=${running} expected=${PROMOTE_EXPECTED_VERSION} — the daemon on :${PORT:-?} is NOT serving the right release"
                    _hb_verify_diag "hand-back verify: /livez reports version=${running} but the flipped release is ${PROMOTE_EXPECTED_VERSION} — the daemon on :${PORT:-?} is NOT serving the right release (probe-level fail-fast: no amount of waiting fixes a wrong version); NOT success"
                    return 1
                fi
            fi
            return 0
            ;;
        1)
            # house-classifier RED (non-2xx outside the 503/7/28 transient
            # classes — the daemon answers HTTP but wrongly). Probe-level
            # fail-fast, mirroring _probe's hard-red handling.
            _handback_verify_reason_write "livez-hard-red: class=${_PROBE_STATUS:-red} code=${_PROBE_CODE:-?} on :${PORT:-?} — non-transient HTTP error (house classifier)"
            _hb_verify_diag "hand-back verify: /livez hard fail (red — non-transient HTTP error, class=${_PROBE_STATUS:-red} code=${_PROBE_CODE:-?}) on :${PORT:-?} (probe-level fail-fast, house classifier); NOT success"
            return 1
            ;;
        *)
            # 2 = transient (connection refused / timeout / 503), 3 =
            # boot-grace-suppressed — both are "still booting": poll on.
            return 2
            ;;
    esac
}

# _handback_repair_dualfight_probe <unit> — the REPAIR-SCOPED dual-fight
# re-check (design §2.3 option (c) / AC-D2-4, step 1 of the bounded repair).
# READ-ONLY and NEVER exits: the repair runs INSIDE the hand-back's
# `$( ... )` subshell, where supervision_dualfight_check's exit 78 would be
# swallowed (its own header forbids the subshell form) — so the repair uses
# this probe instead. Same evidence as Check B: the LISTEN-scoped shared
# reader + the ANCHORED lineage match against the unit's ControlGroup.
# rc 0 = no fault; rc 1 = a fault (the fault token on stdout).
# A probe that cannot see (no ControlGroup, no listener, unprobeable
# census) is NO EVIDENCE — never a fault (the DUAL_FIGHT precedent).
_handback_repair_dualfight_probe() {
    local unit="$1" cg hpid pcg holders rc=0
    [ -n "$unit" ] || return 0
    [ -n "${PORT:-}" ] || return 0
    command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1 || return 0
    cg="$(_run_bounded "$SYSTEMCTL_PROBE_BUDGET_S" -- "$SYSTEMCTL_BIN" show "$unit" -p ControlGroup --value 2>/dev/null)" || cg=""
    [ -n "$cg" ] || return 0
    holders="$(_supervision_port_holders)" || rc=$?
    [ "$rc" -eq 0 ] || return 0
    for hpid in $holders; do
        [ -e "/proc/$hpid" ] || continue
        pcg=""
        if [ -r "/proc/$hpid/cgroup" ]; then
            pcg="$(tail -n 1 "/proc/$hpid/cgroup" 2>/dev/null)"
            pcg="${pcg##*:}"
        fi
        case "$pcg" in
            "$cg"|"$cg"/*) ;;
            *)
                printf 'port-holder-outside-unit-lineage'
                return 1
                ;;
        esac
    done
    return 0
}

# _handback_wrong_version_repair <unit> — the BOUNDED REPAIR (design §2.3
# option (c) / AC-D2-4): a watchdog that slipped in started the daemon on
# the WRONG release inside the ceremony's stop→verified window (the unit
# was already up when the ceremony's own start ran — a start on an ACTIVE
# unit is a no-op, so the OLD release kept serving). The repair:
#   1. the dual-fight re-check (fail-closed — a second master ⇒ NO repair:
#      stopping the unit would be collateral);
#   2. stop the wrong-version daemon (the unit);
#   3. start it again — the unit now boots `current`, the FLIPPED release;
#   4. the caller RE-VERIFIES inside the EXISTING budget (the loop's
#      deadline is untouched: the repair SPENDS the budget, it never
#      extends it).
# rc 0 = the repair ran (the caller keeps polling); rc 1 = no repair (the
# terminal verdict stands).
_handback_wrong_version_repair() {
    local unit="$1" reason fault
    reason="$(_handback_verify_reason_text)"
    case "$reason" in
        livez-version-mismatch*) ;;
        *) return 1 ;;   # only a WRONG-VERSION answer is repairable
    esac
    fault="$(_handback_repair_dualfight_probe "$unit")" || fault="port-holder-outside-unit-lineage"
    if [ -n "$fault" ]; then
        _handback_verify_reason_write "version-repair-refused: the dual-fight re-check faulted ($fault) — the wrong-version daemon is NOT stopped (a second master would be collateral); the wrong-version verdict stands"
        _hb_verify_diag "hand-back verify: the bounded repair is REFUSED (dual-fight re-check faulted: $fault) — the wrong-version daemon is left alone; NOT success"
        return 1
    fi
    _handback_verify_reason_write "version-repair: the daemon on :${PORT:-?} serves the WRONG release (a watchdog that slipped in started it inside the ceremony window) — the bounded repair re-starts the unit (dual-fight re-check clean; the re-verify rides the existing budget)"
    _hb_verify_diag "hand-back verify: wrong-version daemon detected — the bounded repair re-starts the unit (dual-fight re-check clean; the re-verify rides the existing budget)"
    if ! _run_bounded "$HANDBACK_START_BUDGET_S" -- "$SYSTEMCTL_BIN" stop "$unit" >/dev/null 2>&1; then
        _handback_verify_reason_write "version-repair-failed: the unit stop failed — the wrong-version verdict stands"
        _hb_verify_diag "hand-back verify: the bounded repair could NOT stop the unit — the wrong-version verdict stands; NOT success"
        return 1
    fi
    if ! _run_bounded "$HANDBACK_START_BUDGET_S" -- "$SYSTEMCTL_BIN" start "$unit" >/dev/null 2>&1; then
        _handback_verify_reason_write "version-repair-failed: the unit start failed — the wrong-version verdict stands"
        _hb_verify_diag "hand-back verify: the bounded repair could NOT start the unit — the wrong-version verdict stands; NOT success"
        return 1
    fi
    return 0
}

# _supervision_unit_serving <unit> <prestop_pid> — hand-back VERIFIER.
# Polls (deadline-bounded, lock-heartbeat every iteration — the _probe
# precedent) until the unit's MainPID is a NEW nonzero pid — ≠
# <prestop_pid> whenever one was captured — AND the port is serving AND
# (when the tools allow) the daemon ANSWERS /livez with the expected
# version (the serving conjunct, D-P1-4). A MainPID EQUAL to the
# pre-stop pid fails FAST (no retry loop can fix it: the old daemon
# never left — the start no-op'd on an active unit, or the stop
# false-succeeded).
#
# Budget (D-P1-1/D-P1-2, replacing the LIVEZ_BUDGET_S borrow — the
# incident r-20261101-212542-8279 race): effective = CEILING
# (HANDBACK_VERIFY_BUDGET_S=300) when no ceremony clock is known;
# else clamp(remaining − HANDBACK_TAIL_RESERVE_S, FLOOR, CEILING) with
# FLOOR=HANDBACK_VERIFY_FLOOR_S=180 — monotone-safe, never less than
# today's 180s. Grace (D-P1-6 + AM-P1-5, deadline-aware):
#   grace = min(HANDBACK_VERIFY_GRACE_S, max(0, remaining − effective))
# when the clock is known (full GRACE otherwise); ONE progress-gated
# extension at the first expiry, only with a new MainPID ∧ bound port.
# Floor-wins bands (remaining < FLOOR + TAIL_RESERVE at entry) carry
# the DISTINCT "ceremony time exhausted" verdict (T-P1-12: the abort is
# the ceremony's arithmetic, not the release's health — the post-mortem
# must be able to tell them apart).
#
# Degenerate branches preserved (never fault on a missing tool,
# _unit_stopped precedent): PORT unset → MainPID-alone; lsof+curl both
# absent → MainPID-alone; lsof absent + curl present → serving-only
# (the port-listener check is skipped, not failed); lsof present +
# curl absent → MainPID + port-listener (today's verdict, AM-P1-1).
# Prints the new MainPID on success; returns 1 with a loud warn
# otherwise.
#
# [7b SUBSTRING TRAP — no success-path log line below may contain the
# banned systemctl-start literal; every _log/_warn string stays free
# of it.]
_supervision_unit_serving() {
    local unit="$1" prestop="$2"
    local deadline now mp entry_remaining effective grace total
    local clock_known=0 floor_band=0 budget_source extended=0 progress=0
    # D2 containment (c) / AC-D2-4: the bounded wrong-version repair is
    # attempted AT MOST ONCE per hand-back (the repair re-starts the unit;
    # a second wrong-version answer after the repair is the terminal
    # verdict — never a repair loop).
    local repair_tried=0
    local verify_ceiling hb_t0 hb_elapsed=0 hb_last_word="unknown" hb_last_class="UNKNOWN"
    entry_remaining=0
    if [ -n "${PROMOTE_CEREMONY_DEADLINE_EPOCH:-}" ] \
       && case "${PROMOTE_CEREMONY_DEADLINE_EPOCH:-}" in *[!0-9]*) false ;; *) true ;; esac; then
        clock_known=1
        entry_remaining="$(_effective_ceremony_remaining_s "$PROMOTE_CEREMONY_DEADLINE_EPOCH")"
    fi
    # D6-v (self-service-promote Wave 2) — the MEASURED-CEILING mechanism
    # (MECHANISM ONLY, no inflation): with a journal sample the ceiling
    # may follow the last observed boot-to-green latency; with NO sample
    # the ceiling is exactly HANDBACK_VERIFY_BUDGET_S (the default —
    # unchanged). The measured value can only RAISE the ceiling, never
    # shrink it (monotone-safe, the FLOOR's own shape: a fast last boot
    # must not cut the next boot's wait). Clamp [FLOOR,
    # HANDBACK_VERIFY_MEASURED_CEILING_S=600] and hard-cap 780 =
    # EXECUTOR_STALENESS_WINDOW_S(900) − 120 (never extend past the
    # sweep's owner-staleness window — §2.1 interaction constraint).
    # The ceremony clock clamp below still applies ON TOP (the verify
    # never lengthens past PROMOTE_TOTAL_DEADLINE_S).
    verify_ceiling="$HANDBACK_VERIFY_BUDGET_S"
    local _hb_measured=""
    _hb_measured="$(_handback_measured_verify_ceiling_s 2>/dev/null)" || _hb_measured=""
    if [ -n "$_hb_measured" ] && [ "$_hb_measured" -gt "$verify_ceiling" ]; then
        verify_ceiling="$_hb_measured"
    fi
    if [ "$clock_known" = "1" ]; then
        effective=$(( entry_remaining - HANDBACK_TAIL_RESERVE_S ))
        [ "$effective" -lt "$HANDBACK_VERIFY_FLOOR_S" ] && effective="$HANDBACK_VERIFY_FLOOR_S"
        [ "$effective" -gt "$verify_ceiling" ] && effective="$verify_ceiling"
        # grace (AM-P1-5 ruled form): min(GRACE, max(0, remaining − effective))
        grace=$(( entry_remaining - effective ))
        [ "$grace" -lt 0 ] && grace=0
        [ "$grace" -gt "$HANDBACK_VERIFY_GRACE_S" ] && grace="$HANDBACK_VERIFY_GRACE_S"
        if [ "$entry_remaining" -lt "$HANDBACK_VERIFY_FLOOR_S" ]; then
            floor_band=3   # the clock cannot accommodate an honest verify
            budget_source="ceremony-floor(clock-too-short)"
        elif [ "$effective" -eq "$HANDBACK_VERIFY_FLOOR_S" ] \
             && [ "$(( entry_remaining - HANDBACK_TAIL_RESERVE_S ))" -lt "$HANDBACK_VERIFY_FLOOR_S" ]; then
            floor_band=2   # floor wins, tail reserve unavailable
            budget_source="ceremony-floor(tail-reserve-unavailable)"
        else
            budget_source="ceremony-clamped"
        fi
    else
        effective="$verify_ceiling"
        grace="$HANDBACK_VERIFY_GRACE_S"
        budget_source="handback-ceiling(no-ceremony-clock)"
    fi
    # D6-v: surface the measured sample in the budget line's source when
    # it raised the ceiling (the L2/L8a source strings stay byte-identical
    # when NO sample exists — the no-inflation default).
    [ -n "$_hb_measured" ] && [ "$_hb_measured" -gt "$HANDBACK_VERIFY_BUDGET_S" ] \
        && budget_source="${budget_source}+measured(${_hb_measured}s)"
    total=$(( effective + grace ))
    deadline=$(( $(_now_epoch) + effective ))
    # Truthful budget line (T-P1-8): names the EFFECTIVE budget, its
    # source, the grace, and the total — the post-mortem's arithmetic.
    # STDERR, NOT _log: this function's stdout is CAPTURED by the caller
    # (`newpid="$(_supervision_unit_serving ...)"`) — the pid must be the
    # ONLY thing on stdout (_warn's stderr shape, INFO semantics).
    printf '%s[%s]: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "hand-back verify: budget effective=${effective}s source=${budget_source} grace=${grace}s total=${total}s$( [ "$clock_known" = "1" ] && printf ' ceremony-remaining=%ss' "$entry_remaining" ) (knobs: HANDBACK_VERIFY_BUDGET_S=${HANDBACK_VERIFY_BUDGET_S} FLOOR=${HANDBACK_VERIFY_FLOOR_S} TAIL=${HANDBACK_TAIL_RESERVE_S} GRACE=${HANDBACK_VERIFY_GRACE_S})" >&2 2>/dev/null
    # ── D6-(i)/(ii) PENDING state machine (self-service-promote Wave 2,
    # 2026-11-19) ─────────────────────────────────────────────────────────
    # The poll rides the bounded 3-field state probe (_handback_unit_state)
    # — replacing the bare unbounded MainPID poll — and classifies EVERY
    # observation before acting (design §2.1 table; KEYED ON THE STATE
    # STRING, NEVER THE RC):
    #   failed                       → TERMINAL FAILED (halt now; a genuine
    #                                 `failed` is NEVER softened, AC-D6-3)
    #   activating/deactivating/
    #   reloading                    → PENDING (not terminal; keep polling)
    #   active                       → the serving conjunct decides (the
    #                                 pre-existing logic below)
    #   anything else / probe failed → UNKNOWN (never success, never
    #                                 failure; the deadline owns the verdict)
    # The budget line above bounds the whole machine; the terminal
    # budget-expiry verdict carries the DISTINCT `handback-budget-exhausted`
    # token naming the LAST observed state + elapsed (AC-D6-4/-5).
    hb_t0="$(_now_epoch)"
    while :; do
        lock_heartbeat
        _handback_unit_state "$unit"
        case "${_HB_STATE_ACTIVE:-}" in
            failed)
                hb_elapsed=$(( $(_now_epoch) - hb_t0 ))
                _handback_verify_reason_write "unit-state-failed: unit $unit entered ActiveState=failed (SubState=${_HB_STATE_SUB:-unknown} MainPID=${_HB_STATE_MAINPID:-0}) ${hb_elapsed}s into the hand-back verify — systemd's terminal failure state (AC-D6-3: immediate FAILED, never softened, never PENDING)"
                _hb_verify_diag "hand-back verify: unit $unit FAILED (ActiveState=failed, SubState=${_HB_STATE_SUB:-unknown}, MainPID=${_HB_STATE_MAINPID:-0}) at ${hb_elapsed}s into the verify — a terminal state, NOT PENDING and NOT budget exhaustion; halting for the caller's B4 policy"
                return 1
                ;;
            activating|deactivating|reloading)
                hb_last_word="${_HB_STATE_ACTIVE}"
                hb_last_class="PENDING"
                ;;
            active)
                hb_last_word="active"
                ;;
            *)
                # probe unavailable (rc≠0/124), empty field, or an
                # unexpected state word — UNKNOWN by the §2.1 table.
                hb_last_word="${_HB_STATE_ACTIVE:-unprobeable}"
                hb_last_class="UNKNOWN"
                ;;
        esac
        mp="${_HB_STATE_MAINPID:-}"
        progress=0
        case "$mp" in
            ''|0) ;;  # not up yet — keep polling (PENDING — §2.1: MainPID=0/port unbound is a STATE, not a verdict)
            *)
                if [ -n "$prestop" ] && [ "$mp" = "$prestop" ]; then
                    _handback_verify_reason_write "mainpid-unchanged: unit $unit MainPID=$mp EQUALS the pre-stop pid — the old daemon never left (start no-op'd on an active unit, or the stop false-succeeded)"
                    _hb_verify_diag "hand-back verify: unit $unit MainPID=$mp EQUALS the pre-stop pid — the old daemon never left (start no-op'd on an active unit, or the stop false-succeeded); NOT success"
                    return 1
                fi
                # D6: a NEW MainPID with the serving conjunct unsatisfied is
                # the §2.1 ACTIVE state — not terminal; the budget line owns
                # the verdict.
                hb_last_class="ACTIVE"
                if [ -n "${PORT:-}" ] && command -v lsof >/dev/null 2>&1; then
                    if lsof -ti:"$PORT" >/dev/null 2>&1; then
                        # Port bound (necessary fast condition — it proves
                        # the socket). The serving conjunct decides below.
                        if command -v curl >/dev/null 2>&1; then
                            hsp_rc=0
                            _handback_serving_probe || hsp_rc=$?
                            case "$hsp_rc" in
                                0)
                                    _handback_verify_elapsed_write "$(( $(_now_epoch) - hb_t0 ))"
                                    printf '%s\n' "$mp"
                                    return 0
                                    ;;
                                1)
                                    # D2 (c) / AC-D2-4: a WRONG-VERSION answer
                                    # is repairable ONCE — the bounded repair
                                    # re-starts the unit and the loop
                                    # re-verifies inside the SAME budget.
                                    if [ "$repair_tried" = "0" ] \
                                       && _handback_wrong_version_repair "$unit"; then
                                        repair_tried=1
                                        sleep 2
                                        continue
                                    fi
                                    return 1   # probe-level fail-fast
                                    ;;
                                *) ;;             # transient — poll to the deadline
                            esac
                        else
                            # lsof present, curl absent: today's verdict
                            # (MainPID + port-listener), unchanged (AM-P1-1).
                            _handback_verify_elapsed_write "$(( $(_now_epoch) - hb_t0 ))"
                            printf '%s\n' "$mp"
                            return 0
                        fi
                        progress=1   # new MainPID ∧ bound port
                    fi
                    # port not listening yet — transient, fall through
                elif [ -n "${PORT:-}" ] && command -v curl >/dev/null 2>&1; then
                    # lsof absent, curl present — degraded: serving-only
                    # (the port-listener check is skipped, not failed).
                    hsp_rc=0
                    _handback_serving_probe || hsp_rc=$?
                    case "$hsp_rc" in
                        0)
                            _handback_verify_elapsed_write "$(( $(_now_epoch) - hb_t0 ))"
                            printf '%s\n' "$mp"
                            return 0
                            ;;
                        1)
                            # D2 (c) / AC-D2-4 — the degraded (lsof absent)
                            # arm carries the SAME bounded repair.
                            if [ "$repair_tried" = "0" ] \
                               && _handback_wrong_version_repair "$unit"; then
                                repair_tried=1
                                sleep 2
                                continue
                            fi
                            return 1
                            ;;
                        *) ;;
                    esac
                    progress=1   # new MainPID; port uncheckable ⇒ not held against it
                else
                    # PORT unset, or lsof+curl both absent: MainPID alone
                    # decides (degenerate branch, unchanged).
                    _handback_verify_elapsed_write "$(( $(_now_epoch) - hb_t0 ))"
                    printf '%s\n' "$mp"
                    return 0
                fi
                ;;
        esac
        now="$(_now_epoch)"
        if [ "$now" -ge "$deadline" ]; then
            # ONE progress-gated extension (D-P1-6): only while the daemon
            # is demonstrably coming up (new MainPID ∧ bound port / port
            # uncheckable). A dead daemon is never granted extra time.
            if [ "$extended" = "0" ] && [ "$grace" -gt 0 ] && [ "$progress" = "1" ]; then
                extended=1
                deadline=$(( deadline + grace ))
                _warn "hand-back verify: budget ${effective}s expired with the daemon demonstrably coming up (new MainPID ∧ bound port) — ONE progress-gated grace extension of ${grace}s in force (HANDBACK_VERIFY_GRACE_S; total wait cap ${total}s)"
                continue
            fi
            break
        fi
        sleep 2
    done
    # Distinct floor-wins verdicts (T-P1-12 / AM-P1-5): in these bands the
    # ceremony clock itself could not accommodate an honest verify — the
    # abort is the CEREMONY'S arithmetic, not the release's health, and a
    # post-mortem must be able to tell this apart from an unhealthy daemon
    # (the ordinary wording would blame a healthy-but-slow release).
    if [ "$floor_band" = "3" ]; then
        _handback_verify_reason_write "ceremony-time-exhausted: the ceremony clock had ${entry_remaining}s left (< floor ${HANDBACK_VERIFY_FLOOR_S}s) — the hand-back cannot succeed inside the ceremony budget no matter how healthy the daemon is; unit $unit not confirmed within ${effective}s (effective hand-back budget; source ${budget_source})"
        _hb_verify_diag "hand-back verify: ceremony time exhausted — the ceremony clock had ${entry_remaining}s left (< floor ${HANDBACK_VERIFY_FLOOR_S}s): the hand-back cannot succeed inside the ceremony budget no matter how healthy the daemon is; deliberate bounded overshoot $(( HANDBACK_VERIFY_FLOOR_S - entry_remaining ))s (FLOOR − remaining), grace 0; unit $unit not confirmed within ${effective}s (effective hand-back budget; source ${budget_source})"
        return 1
    fi
    if [ "$floor_band" = "2" ]; then
        _handback_verify_reason_write "ceremony-time-exhausted: the ceremony clock had ${entry_remaining}s left (< floor ${HANDBACK_VERIFY_FLOOR_S}s + tail reserve ${HANDBACK_TAIL_RESERVE_S}s) — the tail reserve was unavailable so the floor pinned the budget at ${effective}s; unit $unit not confirmed within ${effective}s (effective hand-back budget; source ${budget_source})"
        _hb_verify_diag "hand-back verify: ceremony time exhausted — the ceremony clock had ${entry_remaining}s left (< floor ${HANDBACK_VERIFY_FLOOR_S}s + tail reserve ${HANDBACK_TAIL_RESERVE_S}s): the tail reserve was unavailable so the floor pinned the budget at ${effective}s and the grace was clamped to ${grace}s (total ${total}s ≤ remaining); unit $unit not confirmed within ${effective}s (effective hand-back budget; source ${budget_source})"
        return 1
    fi
    # D6 (self-service-promote Wave 2): the ORDINARY budget-expiry verdict
    # now carries the DISTINCT `handback-budget-exhausted` token naming the
    # LAST observed state + elapsed (AC-D6-4/-5: BUDGET-EXHAUSTED is
    # terminal — txn left OPEN by the caller's B4 policy — and its reason=
    # is DISTINCT from every FAILED reason: unit-state-failed /
    # livez-version-* / attribution-refused / mainpid-unchanged /
    # unit-start-failed). The two floor-band verdicts above keep their own
    # DISTINCT ceremony-arithmetic wording (T-P1-12, AC-D6-10 unchanged).
    hb_elapsed=$(( $(_now_epoch) - hb_t0 ))
    _handback_verify_reason_write "handback-budget-exhausted: unit $unit not confirmed (new MainPID ≠ ${prestop:-<none>} + port ${PORT:-?} serving + /livez answering) within ${effective}s (effective hand-back budget; source ${budget_source}; grace ${grace}s $( [ "$extended" = "1" ] && printf consumed || printf unused )) — last-state=${hb_last_word:-unknown} (${hb_last_class:-UNKNOWN}) after ${hb_elapsed}s of verify: the BUDGET expired, not a daemon-health verdict"
    _hb_verify_diag "hand-back verify: unit $unit not confirmed (new MainPID ≠ ${prestop:-<none>} + port ${PORT:-?} serving + /livez answering) within ${effective}s (HANDBACK_VERIFY_BUDGET_S effective budget; grace ${grace}s $( [ "$extended" = "1" ] && printf consumed || printf unused )) — handback-budget-exhausted (last-state=${hb_last_word:-unknown}, elapsed=${hb_elapsed}s): the budget expired while ${hb_last_class:-UNKNOWN}; NOT a FAILED verdict"
    return 1
}

# _supervision_handback_unit <unit> <prestop_pid> <scope_heal 0|1> <logfile>
# — the unit hand-back (P3 design items 2+3). Sequence:
#   reset-failed → is-active preflight (truthful journal rows, Wave 1) →
#   D6 PRE-START TAXONOMY (PENDING/PROCEED/FAILED/UNKNOWN, keyed on the
#   state string — self-service-promote Wave 2) → unit start (a budget
#   overrun is NOT terminal: the verify continues — AC-D6-4) → verify
#   NEW MainPID (≠ pre-stop) + port serving on the D6 state machine
#   (activating ⇒ PENDING; failed ⇒ immediate terminal FAILED; budget
#   expiry ⇒ the DISTINCT handback-budget-exhausted token).
# NO NOHUP FALLBACK — ABSOLUTE (Amendment #1): falling back would
# re-create the survivor lineage; service-mode Restart=/journald
# ownership must remain INTACT through the upgrade — systemd brings up
# the flipped release, never a nohup lineage. ANY failure returns 1
# (never a false success); the CALLER journals its halt event and
# applies the B4 leave-txn-open policy.
#
# [7b SUBSTRING TRAP — the byte-identity spine: the banned systemctl-start
# literal must NEVER appear inside a success-path log line
# (the comp7 7b assert_not_contains pin is the authoritative check).
# Command invocations below route through "$SYSTEMCTL_BIN" and every
# _log/_warn string stays free of that literal — guard comment honored
# at each nearby log site.]
_supervision_handback_unit() {
    local unit="$1" prestop="$2" scope_heal="$3" log="$4"
    local isact errfile first newpid outcome
    # D6 hygiene: a stale elapsed sample from an earlier hand-back in this
    # same shell must never reach a later commit row.
    HANDBACK_VERIFY_ELAPSED_S=""
    _handback_verify_elapsed_clear
    # hbk-err inode-churn fix (2026-11-22): the SAME stale-read hole
    # applies to the REASON channel — the reason file is minted ONCE per
    # shell (the $$-keyed path), so a reason written by an EARLIER attempt
    # that failed WITHOUT writing a reason of its own would be read back by
    # a LATER attempt in the same shell and surfaced as that attempt's
    # diagnosis. Clear it at entry: a hand-back starts with no verdict.
    HANDBACK_VERIFY_REASON=""
    _handback_verify_reason_clear
    # 1. reset-failed — clear any stale failed state so neither the
    #    is-active preflight nor the start is poisoned by it
    #    (best-effort: a reset-failed failure is not itself fatal —
    #    the start below surfaces real problems).
    #
    # 2.2b (upgrade-resilience 2026-10-07) — bound the probe: a hung
    # systemctl/dbus call against a wedged unit could stall this site
    # forever (the §9 reproduction cited a similar hang at the
    # unbounded start at step 3 below). Bounded at
    # SYSTEMCTL_PROBE_BUDGET_S (default 5s); on timeout journal the
    # canonical event + loud stderr + CONTINUE (reset-failed is
    # best-effort by its own comment).
    if _run_bounded "$SYSTEMCTL_PROBE_BUDGET_S" -- "$SYSTEMCTL_BIN" reset-failed "$unit" >/dev/null 2>&1; then
        :
    else
        local sbrc=$?
        # D-P1-1 / I-A1..A4 (promote-handback-halt Phase 1 §4.3 site 1): the
        # rc is CLASSIFIED, never assumed. The legacy arm hard-coded `rc=124`
        # and said "exceeded budget" for EVERY nonzero rc — on a healthy
        # hand-back `reset-failed` can exit nonzero for an ordinary reason,
        # and the mislabel is what made the incident's journal read as a
        # timeout. The event kind names the bounded-wait FAMILY; the row's
        # `kind=` names the OUTCOME (§4.2).
        if [ "$(_bounded_rc_kind "$sbrc")" = "budget" ]; then
            _warn "reset-failed probe exceeded budget (${SYSTEMCTL_PROBE_BUDGET_S}s) for $unit — continuing (best-effort; child SIGTERM→SIGKILL'd)"
            journal_history_append subprocess_wait_timeout \
                "site=handback_reset_failed kind=budget reason=child: $(_bounded_rc_reason "$sbrc" "" "SYSTEMCTL_PROBE_BUDGET_S=${SYSTEMCTL_PROBE_BUDGET_S}") unit=$unit rc=$sbrc — SIGTERM→SIGKILL, continuing (best-effort)" \
                || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
        else
            _warn "reset-failed probe FAILED (rc=$sbrc) for $unit — continuing (best-effort)"
            journal_history_append subprocess_wait_timeout \
                "site=handback_reset_failed kind=nonzero_exit reason=child: $(_bounded_rc_reason "$sbrc") unit=$unit rc=$sbrc — nonzero rc, continuing (best-effort; the probe's own failure is never fatal)" \
                || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
        fi
    fi
    # 2. is-active preflight — ALREADY-ACTIVE ≠ SUCCESS: a unit start on
    #    an active unit is a NO-OP that exits 0 (the false-success
    #    shape). The verdict comes from the MainPID/port verification
    #    below, NEVER from the start rc.
    #
    # 2.2b — bound the probe (mirror of reset-failed above). On
    # timeout: treat as NOT-active (continue into the start attempt; the
    # MainPID/port verify below owns the verdict — never fail the
    # hand-back on a probe timeout alone). CAREFUL: do NOT `|| true` the
    # assignment — `|| true` would mask the rc 124 and the IF would
    # always take the THEN branch (skipping the journal event). Capture
    # via a temp var + explicit rc-test.
    #
    # D6-(iii) / AC-D6-1 / AC-D6-2 (2026-11-19): ONLY rc=124 is a genuine
    # bounded-wait timeout — _run_bounded collapses 137|143 → 124 and passes
    # every other rc through UNCHANGED. The old shape journalled a
    # `subprocess_wait_timeout` row for ANY non-zero rc, so systemctl's own
    # answer rc=3 ("not active" — the EXPECTED pre-start value: the unit is
    # deliberately stopped pre-flip) was recorded as a TIMEOUT. That lie
    # misdirected triage for ~10h. The state word is ON STDOUT and was
    # discarded; it is now captured and journaled, so "inactive" /
    # "activating" / "failed" are distinguishable in the journal.
    # VERDICT UNCHANGED: this site never decides — it journals and
    # continues into the start attempt (the MainPID/port verify owns the
    # verdict). The D6-(i)/(ii) PENDING state machine (self-service-
    # promote Wave 2) now consumes the observed state word BELOW (the
    # pre-start taxonomy) and inside the verify loop's bounded 3-field
    # state probe; this journal site stays the rc≠124 truth-teller.
    local isact_rc=0 isact_state=""
    isact="$(_run_bounded "$SYSTEMCTL_PROBE_BUDGET_S" -- "$SYSTEMCTL_BIN" is-active "$unit" 2>/dev/null)"
    isact_rc=$?
    # The observed state word (stdout) — flattened to one line, trimmed.
    isact_state="$(printf '%s' "${isact:-}" | tr '\n' ' ' | tr -s ' ' | sed 's/[[:space:]]*$//')"
    if [ "$isact_rc" -ne 0 ]; then
        # RESOLVED H2 (branch D-P1-1 three-way × L 865ec4ad3 D6-(iii),
        # AC-D6-1/2): the failure-shaped `subprocess_wait_timeout` taxonomy is
        # the branch's (124 budget / other nonzero_exit / rc 3 INFO, no
        # timeout-shaped row); L's ADDITIVE `handback_is_active_state` row is
        # kept for EVERY answered rc≠124 because two L-side tests read it
        # (tests/test_bounded_subprocess_waits.sh:404,
        # tests/packs/promote_handback_halt_gate_legs_test.sh:614).
        __isact_kind="$(_bounded_rc_kind "$isact_rc")"
        if [ "$__isact_kind" = "budget" ]; then
            _warn "is-active probe exceeded budget (${SYSTEMCTL_PROBE_BUDGET_S}s) for $unit — treating as NOT-active; continuing into the start attempt (the MainPID/port verify owns the verdict)"
            journal_history_append subprocess_wait_timeout \
                "site=handback_is_active kind=budget reason=child: $(_bounded_rc_reason "$isact_rc" "" "SYSTEMCTL_PROBE_BUDGET_S=${SYSTEMCTL_PROBE_BUDGET_S}") unit=$unit rc=$isact_rc — treating as not-active, continuing" \
                || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
        elif [ "$isact_rc" = "3" ]; then
            # rc 3 = systemctl's ROUTINE pre-start answer (inactive OR
            # activating): INFO only on the timeout surface, with the site
            # token, state and rc so post-mortem greps still find this site.
            # The state token falls back to `unknown` (never the literal
            # `inactive|activating` form — CP-10).
            __isact_state="$(_bounded_state_hint "${isact}")"
            [ -n "$__isact_state" ] || __isact_state="unknown"
            _log "is-active: site=handback_is_active state=${__isact_state} rc=3 — routine pre-start answer; proceeding into the start attempt"
            journal_history_append handback_is_active_state \
                "site=handback_is_active kind=state reason=state:${__isact_state} unit=$unit rc=$isact_rc state=${isact_state:-<none>} — systemctl answered (not a timeout; rc≠124); treating as not-active, continuing" \
                || _warn "handback_is_active_state journal event append FAILED (best-effort)"
        else
            # Answered rc≠124 other than the routine rc 3 (e.g. rc 4 unknown,
            # rc 1 failed): genuine probe failure — WARN + the branch's
            # true-rc `nonzero_exit` row, carried on L's additive state event
            # so L's "no subprocess_wait_timeout row for an answered rc" pin
            # (tests/test_bounded_subprocess_waits.sh:422) stays true.
            _warn "is-active probe FAILED (rc=$isact_rc state=${isact:-<none>}) for $unit — treating as NOT-active; continuing into the start attempt (the MainPID/port verify owns the verdict)"
            journal_history_append handback_is_active_state \
                "site=handback_is_active kind=nonzero_exit reason=child: $(_bounded_rc_reason "$isact_rc" "$(_bounded_state_hint "${isact}")") unit=$unit rc=$isact_rc state=${isact_state:-<none>} — systemctl answered (not a timeout; rc≠124); treating as not-active, continuing" \
                || _warn "handback_is_active_state journal event append FAILED (best-effort)"
        fi
        isact=""
    fi
    [ "$isact" = "active" ] \
        && _warn "unit $unit ALREADY ACTIVE at hand-back — a unit start on an active unit no-ops (exit 0); the MainPID/port verification decides, not the start rc"
    # ── D6-(i)/(ii) PRE-START TAXONOMY (self-service-promote Wave 2,
    # 2026-11-19) — keyed on the OBSERVED STATE STRING (the is-active
    # stdout word IS ActiveState), never on the rc. This block REPLACES
    # the old "any non-green observation collapses into one verdict"
    # shape: PENDING and PROCEED are now first-class (§2.1), a genuine
    # `failed` is an IMMEDIATE terminal (AC-D6-3 — never softened; the
    # reset-failed above already cleared stale failure state, so a
    # failed reading NOW is real: the stop half of this ceremony ended
    # in systemd's terminal failure state), and everything unclassifiable
    # proceeds — the verify loop owns the verdict.
    case "$isact_state" in
        failed)
            _handback_verify_reason_write "unit-state-failed: unit $unit is ActiveState=failed BEFORE the start (rc=$isact_rc, after reset-failed) — the stop half of the ceremony left systemd's terminal failure state; refusing to start over it (AC-D6-3: immediate FAILED, never softened)"
            _hb_verify_diag "unit hand-back: unit $unit FAILED PRE-START (ActiveState=failed after reset-failed) — a terminal state, NOT PENDING; halting for the caller's B4 policy (NO nohup fallback — Amendment #1)"
            return 1
            ;;
        activating|deactivating|reloading)
            # PENDING — a state, not a verdict (AC-D6-3: activating ⇒
            # PENDING, never FAILED). systemd is mid-transition; the
            # start below joins/queues the transition.
            _hb_verify_diag "unit hand-back: unit $unit is $isact_state PRE-START — PENDING (a state, not a verdict); issuing the start and letting the verify budget own the outcome"
            ;;
        inactive)
            # PROCEED — the expected pre-start value (the unit was
            # deliberately stopped pre-flip). No log line: this is the
            # healthy path and the budget line below already speaks.
            ;;
        active)
            # Already classified above (the ALREADY-ACTIVE no-op warning);
            # the verify below decides. No further action.
            ;;
        *)
            # rc=124 (no state word), probe failure, or an unexpected
            # word — UNKNOWN: never success, never failure (§2.1);
            # proceed into the start; the verify owns the verdict.
            _hb_verify_diag "unit hand-back: pre-start state for $unit unclassifiable (rc=$isact_rc state=${isact_state:-<none>}) — UNKNOWN; proceeding into the start attempt (the verify budget owns the verdict)"
            ;;
    esac
    # 3. the start itself (pure command invocation — see the substring-
    #    trap guard at the top of this function).
    #
    # 2.2b — bound at HANDBACK_START_BUDGET_S (default 60s) — the
    # §9-culprit site: manual-push reproduction froze POST-flip here
    # with the parent wedged in pipe_read 10+ min while the child
    # subshell spun in the unit hand-back. On timeout: journal +
    # SIGTERM→SIGKILL the child (done by _run_bounded).
    #
    # D6 / AC-D6-4 (self-service-promote Wave 2, 2026-11-19): a start
    # exceeding the budget is NOT TERMINAL. _run_bounded kills the
    # systemctl CLIENT, not the start job — systemd keeps processing it
    # server-side, so the unit may still come up. The old shape treated
    # rc=124 as a terminal halt (the "second false-negative generator",
    # design §1.1) — the verify now CONTINUES to its own budget and, on
    # expiry, issues the DISTINCT `handback-budget-exhausted` token.
    # ONLY a real start failure (rc≠0 ∧ ≠124) is a terminal FAILED here
    # (§2.1: "start rc ≠ 0 ∧ ≠ 124").
    errfile="$(mktemp /tmp/.ensemble-hb-sc.XXXXXX)"
    local start_hung=0
    if _run_bounded "$HANDBACK_START_BUDGET_S" -- "$SYSTEMCTL_BIN" start "$unit" 2>"$errfile"; then
        :
    else
        local sbrc=$?
        first="$(head -n1 "$errfile" 2>/dev/null || true)"
        rm -f "$errfile"
        if [ "$sbrc" = "124" ]; then
            start_hung=1
            _warn "unit start exceeded budget — under-budget child (HANDBACK_START_BUDGET_S=${HANDBACK_START_BUDGET_S}s) was SIGTERM→SIGKILL'd; unit=$unit — NOT terminal (D6 AC-D6-4: systemd keeps processing the start job); continuing into the bounded verify"
            # RESOLVED H3 (branch dynamic reason × L 0f133d2b9/865ec4ad3
            # D6 AC-D6-4): _bounded_rc_reason emits the byte-identical
            # `child: timeout(HANDBACK_START_BUDGET_S=<n>)` text for 124,
            # and the true rc variable replaces L's hardcoded rc=124.
            # L's truthful NOT-terminal tail is kept (the start job
            # continues server-side; the bounded verify owns the verdict).
            journal_history_append subprocess_wait_timeout \
                "site=handback_start kind=budget reason=child: $(_bounded_rc_reason "$sbrc" "" "HANDBACK_START_BUDGET_S=${HANDBACK_START_BUDGET_S}") unit=$unit rc=$sbrc — SIGTERM→SIGKILL of the CLIENT; NOT terminal (D6 AC-D6-4): continuing into the bounded verify (the start job continues server-side)" \
                || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
        else
            # Leader ruling D2 (phase1 §4.3 note) — journal SYMMETRY: a genuine
            # non-124 start failure gets its own `kind=nonzero_exit` row so it
            # is machine-visible with the TRUE rc (the WARN text is unchanged).
            journal_history_append subprocess_wait_timeout \
                "site=handback_start kind=nonzero_exit reason=child: $(_bounded_rc_reason "$sbrc") unit=$unit rc=$sbrc — nonzero rc (not a timeout), halting for caller's B4 policy" \
                || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
            _warn "unit hand-back FAILED: unit $unit did not come up (${first:-no stderr}) — NO nohup fallback (Amendment #1: a fallback re-creates the survivor lineage and abandons Restart=/journald ownership) — halting for the caller's B4 policy"
            _handback_verify_reason_write "unit-start-failed: unit $unit did not come up (rc=${sbrc}; ${first:-no stderr})"
            return 1
        fi
    fi
    rm -f "$errfile"
    # 4. verify NEW MainPID (differs from pre-stop) + port serving.
    if ! newpid="$(_supervision_unit_serving "$unit" "$prestop")"; then
        # D6-(iv) / AC-D6-6: the VERIFIER-PATH reason was produced INSIDE
        # the $() subshell (the attribution detail / the observed /livez
        # version / the classifier class) ⇒ read it back out-of-band into
        # THIS shell, where the caller (restart_via_launcher → the ceremony
        # script) composes the halt row. The launchd HB_ERR channel is
        # untouched (it is a different vocabulary, and it is empty on the
        # systemd path by construction — C-9).
        HANDBACK_VERIFY_REASON="$(_handback_verify_reason_text)"
        _handback_verify_reason_clear
        _warn "unit hand-back FAILED verification for $unit — NO nohup fallback (Amendment #1) — halting for the caller's B4 policy${HANDBACK_VERIFY_REASON:+ — verify reason: $HANDBACK_VERIFY_REASON}"
        return 1
    fi
    # Success hygiene (the launchd channel's precedent): a SUCCESSFUL
    # hand-back must never leave a stale verifier reason behind for a
    # LATER halt in the same run.
    HANDBACK_VERIFY_REASON=""
    _handback_verify_reason_clear
    # D6-v: read the boot-to-green elapsed out-of-band into this shell —
    # promote.sh's 8a commit row stamps it as the journal's
    # verify_elapsed_s sample (the ceiling reader's input).
    HANDBACK_VERIFY_ELAPSED_S="$(_handback_verify_elapsed_text)"
    _handback_verify_elapsed_clear
    if [ "$start_hung" = "1" ]; then
        # The start-budget timeout was a FALSE ALARM (AC-D6-4's positive
        # direction): the client died, the start job finished server-side,
        # and the verify confirmed green. Say so — the journal's
        # subprocess_wait_timeout row would otherwise read as a fault.
        _log "unit hand-back: the earlier start-budget timeout was a false alarm — the start job completed server-side and the verify confirmed green in ${HANDBACK_VERIFY_ELAPSED_S:-?}s (new MainPID $newpid)"
    fi
    outcome="$(supervision_map_outcome "${SUPERVISION_MODE:-}" "${SUPERVISION_STATE:-unknown}" "${SUPERVISION_UNIT:-}")"
    outcome="${outcome%%|*}"
    if [ "$scope_heal" = "1" ]; then
        # P3 design item 3: the self-heal of today's live shape, inside
        # the promote — journaled as its own event (D4 splice
        # discipline: additive event kind; A1 outcome name in the
        # payload, additive field naming).
        journal_history_append supervision_handback "scope→unit: scope-survivor lineage handed back to unit $unit inside the promote (self-heal; declared=${SUPERVISION_MODE:-?} verified=${SUPERVISION_STATE:-?} outcome=$outcome; new MainPID $newpid; port ${PORT:-?} serving)" \
            || _warn "supervision_handback scope→unit journal event append FAILED (best-effort — the hand-back itself succeeded)"
    fi
    # (f) mode-aware tail logging: the unit path mentions the unit
    # journal AND the fallback file. [Substring-trap guard: this success
    # line deliberately phrases the start as "serves" — never the banned
    # systemctl-start literal.]
    _log "unit hand-back COMPLETE: $unit serves the flipped release (new MainPID $newpid${prestop:+, pre-stop was $prestop}; port ${PORT:-?} serving; outcome=$outcome) — logs: journalctl -u $unit -f (unit journal) or $log (fallback file)"
    return 0
}

# restart_via_launcher — start the launcher.
#
# comp7 / stretch7 (r-20260928-005506-f82e): on Linux+systemd hosts,
# the nohup launcher inherits the launcher's cgroup — the same
# survivorship model the executor's setsid lived under (and the same
# gap that killed r-f82e). The safer path is to let systemd manage
# the launcher via its unit file (the unit IS the cgroup boundary —
# the executor that follows lives inside it; a unit teardown
# propagates predictably). The systemd-aware branch is OPT-IN
# (``ENSEMBLE_RESTART_UNIT`` env var) because:
#   (a) unit discovery is HOST-MADE (the unit file at
#       /etc/systemd/system/ensemble-live.service is hand-provisioned
#       on ensemble-vm by devops, out-of-repo — see
#       incident doc §2.2 / r-f82e §1.7 #4). A probe here would have
#       to guess unit names that the operator chose;
#   (b) switching the default would break operator hand-runs where
#       the unit doesn't exist (drill / sandbox hosts);
#   (c) the nohup path is the SAME PATH THE PIPELINE USED BEFORE
#       r-f82e — preserving it as the default is a deliberate
#       backstop for hosts that don't run the daemon under systemd.
#
# P3 (ownership-mode commission 2026-09-29) — SUPERVISION-AWARE
# HAND-BACK. The executor's P1 classification — the SUPERVISION_*
# globals set by the LAST supervision_classify in this process (the
# action script's preflight, or fresher: stop_via_stop_script's
# stop-site re-classify — always PRE-STOP, i.e. while the daemon still
# ran) — now SELECTS the hand-back mode. CONSUMED, never re-derived
# here: a fresh classify at hand-back time sees a STOPPED daemon (no
# owned pid) and collapses every shape to SCRIPT_NOHUP, which would
# hand a unit-managed host back via nohup — the exact defect P3
# fixes. Modes:
#   UNIT_MANAGED (+unit)    → unit hand-back (_supervision_handback_unit,
#                             scope_heal=0): reset-failed → is-active
#                             preflight → start → verify NEW MainPID +
#                             port serving. NO NOHUP FALLBACK — ABSOLUTE
#                             (Amendment #1: a fallback re-creates the
#                             survivor lineage; service-mode Restart=/
#                             journald ownership must stay intact through
#                             the upgrade). Failure → rc 1 → the caller
#                             journals a halt event + B4 leave-txn-open
#                             (never false-success).
#   SCOPE_SURVIVOR + unit   → the SAME unit hand-back (scope_heal=1) +
#                             the `supervision_handback scope→unit`
#                             journal event — the self-heal of today's
#                             live shape, inside the promote.
#   SCOPE_SURVIVOR, no unit → today's nohup path BYTE-IDENTICAL + WARN
#                             (the survivor lineage continues; the WARN
#                             names the self-heal configuration).
#   SCRIPT + ENSEMBLE_RESTART_UNIT set → the comp7 opt-in path WITH
#                             the a′ fixes — is-active preflight before
#                             the start, real error propagation (first
#                             stderr line surfaces), no false-success
#                             (already-active verifies port serving
#                             instead of trusting a no-op rc) — while
#                             KEEPING the nohup fallback (comp7 7c pin).
#   EXPLICIT unit + unit-    → M3 (review cycle 1, leader FAIL-CLOSED):
#     incapable host           REFUSES exit 78 (supervision-unit-
#                             incapable-host) — never loud-degrade +
#                             nohup at rc 0. Normally caught at the
#                             preflight; this is the defense-in-depth
#                             arm (SUPERVISION_EXPLICIT_UNIT=1 keys it).
#   everything else         → today's nohup path byte-identical.
#
# (e) SCOPE ESCAPE — deliberately UNCHANGED by P3: the promote
# executor's own scope escape (comp1: systemd-run --scope) places the
# executor in a SIBLING cgroup of the unit, NOT inside it — that is
# what lets the executor SURVIVE a mid-promote unit teardown (the
# r-f82e death mode). P3 moves only the DAEMON's ownership back to
# the unit at hand-back; the executor keeps its sibling scope.
#
# DELIBERATELY DEFERRED (r-f82e follow-up commission candidate):
# auto-discovery of the right unit name. Not implemented here —
# operator-set opt-in only. The full unit-ownership lifecycle
# (which unit, where it's provisioned, how stage.sh promotes the
# unit file) belongs to a separate commission per incident doc
# §1.7 #4 + the deploy-ownership-fix ticket family.
#
# Returns: 0 on every legacy shape (fire-and-forget nohup, comp7 unit
# start, comp7 fallback) AND on a VERIFIED unit hand-back; 1 ONLY on a
# unit-path hand-back failure (Amendment #1: no nohup fallback — the
# caller must halt + B4; never a false success). M3 exception: the
# EXPLICIT-unit-on-unit-incapable-host arm _refuse-EXITS 78 (journaled
# refusal) instead of returning. The caller's gate_* helpers still
# observe the deep result via /livez + /readyz.
restart_via_launcher() {
    mkdir -p "$INSTALL_DIR/data"
    local log="$INSTALL_DIR/data/launcher.log"
    local hb_unit=""
    local hb_mode=""
    local hb_prestop="${SUPERVISION_PRESTOP_MAINPID:-}"
    local _hb_pin=""

    # ── D2a launchd hand-back (THE supervised path; 2026-11-11) ────────────
    # Consume the PRE-STOP classification (never re-derive — the daemon is
    # DOWN here). LAUNCHD_MANAGED ⇒ M2 supervisor-owned start: `launchctl
    # bootstrap gui/<uid> <verbatim-plist>`. F10 (review round 1), wording
    # precision: the JOB is started exactly once BY LAUNCHD (RunAtLoad=true
    # ⇒ a successful bootstrap IS the start); the bootstrap COMMAND itself
    # may be invoked MORE than once — the retry loop below re-issues it
    # (2s→4s backoff) after a FAILED attempt, and a rc-2 "already loaded"
    # answer means the start predates this call (benign ONLY because the
    # forward path booted the job out first; the caller's gate revalidates).
    # The promote starts NOTHING else — no nohup on the supervised path
    # (the 2026-11-11 dual-supervisor incident). bootstrap failure is
    # fail-safe: retry 2x then return 1 (caller halts); a bootstrap refusal
    # NEVER degrades to a competing nohup launcher.
    if [ "${SUPERVISION_STATE:-}" = "LAUNCHD_MANAGED" ] && [ -n "${SUPERVISION_UNIT:-}" ] \
       && _supervision_host_allows_launchd; then
        local lplist lrc lattempt=1
        # PC-1 (2026-11-14 hand-back halt): the resolver is THREE-runged —
        # live `launchctl print` (rarely alive here: the stop bootouted the
        # job) → the pre-stop classify stash → the label-verbatim
        # ~/Library/LaunchAgents/<label>.plist fallback (ratified
        # filename==label convention, incident §7b). The old
        # "never label-derive" refusal was the halt: filename ≠ label was
        # the incident's root cause, and the rename retired that install
        # shape; every candidate is still -f-verified, so unresolvable
        # remains unresolvable (fail-closed, no fabricated path).
        lplist="$(_supervision_launchd_plist_path "$SUPERVISION_UNIT")"
        if [ -z "$lplist" ]; then
            _hb_err="$(_handback_last_error_text)"   # MAJOR #1: subshell-proof reader (resolver wrote the file from inside $())
            _warn "launchd hand-back: cannot resolve the plist path for job '$SUPERVISION_UNIT' — live probe, pre-stop stash, and the label-verbatim ~/Library/LaunchAgents/<label>.plist fallback ALL unresolvable (incident 2026-11-14 §7b: filename must equal the job label) — failing; caller halts fail-safe${_hb_err:+; captured: $_hb_err}"
            return 1
        fi
        lrc=1
        while [ "$lattempt" -le 2 ]; do
            _supervision_launchd_bootstrap "$lplist"
            lrc=$?
            # rc 0 = started; rc 2 = already loaded (benign ONLY because the
            # forward path booted out first — the caller's gate revalidates).
            if [ "$lrc" -eq 0 ] || [ "$lrc" -eq 2 ]; then break; fi
            _hb_err="$(_handback_last_error_text)"
            _warn "launchd hand-back: bootstrap attempt $lattempt FAILED (rc=$lrc) for '$SUPERVISION_UNIT' — retrying${_hb_err:+; captured: $_hb_err}"
            sleep $(( lattempt * 2 ))
            lattempt=$(( lattempt + 1 ))
        done
        if [ "$lrc" -ne 0 ] && [ "$lrc" -ne 2 ]; then
            _hb_err="$(_handback_last_error_text)"
            _warn "launchd hand-back: bootstrap EXHAUSTED after retries (last rc=$lrc) — failing (never a nohup fallback on Darwin); caller halts fail-safe${_hb_err:+; captured: $_hb_err}"
            return 1
        fi
        HANDBACK_LAST_ERROR=""; _handback_err_file_clear   # MAJOR #1 hygiene: success clears both channels
        _log "hand-back: launchd — bootstrapped job '$SUPERVISION_UNIT' from $lplist (RunAtLoad starts the daemon exactly once; no nohup, no promote-started launcher)"
        return 0
    fi

    # ── P3 mode selection (consume the PRE-STOP classification) ───────
    if _supervision_host_allows_unit && command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1; then
        case "${SUPERVISION_STATE:-}" in
            UNIT_MANAGED)
                # ── v0.16.9 hand-back pin rung (lane-convergence fix) ──
                # Pre-convergence the daemon runs under ensemble-live.service
                # (cgroup-derived SUPERVISION_UNIT); promoting to
                # ensemble-main.service requires the hand-back to declare/
                # verify the NEW unit so the promote completes instead of
                # halting on declared≠verified. Precedence: env var > .env
                # pin > cgroup leaf, with r3-style validation
                # (ensemble-*.service conformance + unit-file existence,
                # commit e56f2e85) — missing or non-conforming falls
                # through to the cgroup leaf, BYTE-IDENTICAL to current
                # behavior. HAND-BACK SCOPED ONLY: the stop path keeps
                # the cgroup-leaf selection (pre-convergence stop aimed at
                # the pinned unit would pre-flip-abort; a global override
                # would leak into the stop seam).
                _hb_pin="${ENSEMBLE_RESTART_UNIT:-}"
                if [ -z "$_hb_pin" ]; then
                    _hb_pin="$(_supervision_unit_from_dotenv 2>/dev/null)" || _hb_pin=""
                fi
                case "$_hb_pin" in
                    ensemble-*.service)
                        if [ -f "$SUPERVISION_UNIT_DIR/$_hb_pin" ]; then
                            hb_unit="$_hb_pin"
                        else
                            _warn "hand-back: pin '$_hb_pin' set but unit file absent at $SUPERVISION_UNIT_DIR (manual: create the unit file at $SUPERVISION_UNIT_DIR/$_hb_pin or unset ENSEMBLE_RESTART_UNIT) — falling through to cgroup leaf ${SUPERVISION_UNIT:-<none>}"
                        fi
                        ;;
                    *)
                        [ -n "$_hb_pin" ] && _warn "hand-back: pin '$_hb_pin' not ensemble-*.service (manual: correct the name to ensemble-*.service or unset ENSEMBLE_RESTART_UNIT) — falling through to cgroup leaf ${SUPERVISION_UNIT:-<none>}"
                        ;;
                esac
                if [ -z "$hb_unit" ] && [ -n "${SUPERVISION_UNIT:-}" ]; then
                    hb_unit="$SUPERVISION_UNIT"
                fi
                if [ -n "$hb_unit" ]; then
                    hb_mode="unit"
                fi
                ;;
            SCOPE_SURVIVOR)
                # §3 precedence for a NEEDED name, minus the cgroup rung
                # (a scope leaf yields no unit): env ENSEMBLE_RESTART_UNIT
                # > INSTALL_DIR/.env read directly.
                hb_unit="${ENSEMBLE_RESTART_UNIT:-}"
                if [ -z "$hb_unit" ]; then
                    hb_unit="$(_supervision_unit_from_dotenv)"
                    [ -n "$hb_unit" ] || hb_unit=""
                fi
                if [ -n "$hb_unit" ]; then
                    hb_mode="scope-to-unit"
                fi
                ;;
        esac
    elif [ "${SUPERVISION_STATE:-}" = "UNIT_MANAGED" ] && [ -n "${SUPERVISION_UNIT:-}" ]; then
        # Byte-identical BSD/macOS arms: unit paths live behind the host
        # guard. M3 (review cycle 1, leader-ruled FAIL-CLOSED): an
        # EXPLICIT unit declaration (SUPERVISION_EXPLICIT_UNIT=1, set by
        # the ladder-top 'unit' arm of supervision_classify) on a host
        # that cannot do a unit hand-back REFUSES (exit 78,
        # supervision-unit-incapable-host) — silently degrading an
        # explicit assertion to the nohup lineage at rc 0 is the r-f82e
        # surprise class. Defense-in-depth: the preflight normally
        # refuses this shape BEFORE the pipeline starts; only a
        # capability change mid-run (or a direct call) lands here.
        # NON-explicit shapes (auto-resolved UNIT_MANAGED — e.g. systemd
        # present at classify time but systemctl unresolvable at
        # hand-back) KEEP the legacy degrade-LOUD-to-nohup semantics
        # (stop-ensemble.sh :354 precedent) — M3 is confined to the
        # explicit arm.
        if [ "${SUPERVISION_EXPLICIT_UNIT:-0}" = "1" ]; then
            _refuse supervision-unit-incapable-host "restart refused: ENSEMBLE_SUPERVISION=unit is explicit (classification UNIT_MANAGED:${SUPERVISION_UNIT}) but unit hand-back is unavailable here (non-Linux / no systemd / no resolvable systemctl) — never silent-degrade to the nohup lineage (the r-f82e surprise class)"
        fi
        if _supervision_host_allows_launchd; then
            # D2a: a UNIT_MANAGED classification on Darwin (stale/inconsistent
            # — the classifier now resolves Darwin to launchd or refuses) can
            # NOT honor a unit hand-back; the Darwin guard below refuses rather
            # than degrade to nohup (the 2026-11-11 incident mechanism).
            _warn "classification UNIT_MANAGED:${SUPERVISION_UNIT} on Darwin — unit hand-back is unavailable; Darwin never degrades to the nohup lineage (refusing at the nohup guard, fail-safe)"
        else
            _warn "classification UNIT_MANAGED:${SUPERVISION_UNIT} but unit hand-back is unavailable here (non-Linux / no systemd / no resolvable systemctl) — degrading LOUD to the nohup path"
        fi
    fi

    if [ "$hb_mode" = "unit" ]; then
        _log "hand-back: UNIT_MANAGED — the flipped release returns to unit $hb_unit (service-mode Restart=/journald ownership intact; no nohup lineage)"
        _supervision_handback_unit "$hb_unit" "$hb_prestop" 0 "$log"
        return $?
    fi
    if [ "$hb_mode" = "scope-to-unit" ]; then
        _log "hand-back: SCOPE_SURVIVOR + unit $hb_unit configured — SAME unit hand-back (the self-heal of today's live shape, inside the promote)"
        _supervision_handback_unit "$hb_unit" "$hb_prestop" 1 "$log"
        return $?
    fi
    if [ "${SUPERVISION_STATE:-}" = "SCOPE_SURVIVOR" ]; then
        _warn "supervision: SCOPE_SURVIVOR with NO unit configured (env ENSEMBLE_RESTART_UNIT and INSTALL_DIR/.env both empty) — handing back via the byte-identical nohup path (the survivor lineage continues); configure a unit to self-heal into unit ownership at the next promote"
    fi

    # Linux+systemd + operator-opted-in → unit path for the launcher.
    # The unit, if present, runs the launcher as the service user; the
    # unit's cgroup is the survivorship boundary for the executor that
    # follows. Byte-identical macOS / no-systemd / no-opt-in fallback to
    # nohup.
    if [ "$(uname -s)" = "Linux" ] \
       && [ -d "/run/systemd/system" ] \
       && [ -n "${ENSEMBLE_RESTART_UNIT:-}" ]; then
        # a′-1 PREFLIGHT (P3 design item 4): already-active ≠ success —
        # a unit start on an ACTIVE unit is a NO-OP that exits 0 (the
        # false-success shape). Detect it FIRST; verify port serving
        # instead of ever trusting a no-op rc.
        #
        # [7b FREEZE CONTRACT — M5 (review cycle 1), stated explicitly so
        # future edits don't re-litigate: comp7 7b is SUBSTRING-TRAP-
        # frozen, NOT full-line-frozen. The frozen property is exactly
        # one: NO success-path log line below may contain the banned
        # 'systemctl start' literal (command invocations only). The
        # journalctl-hint tails edited onto the success lines this cycle
        # ('— logs: journalctl -u <unit> -f (unit journal) or <file>
        # (fallback file)') are LEGITIMATE under this contract — they
        # carry no banned substring and the comp7 7b pin asserts
        # assert_not_contains 'systemctl start' on the success output,
        # not line-by-line byte identity. The FAILURE path alone carries
        # the frozen 7c wording (see the guard below).]
        local _sc_isact _sc_skip_start=0 _sc_rc=0
        # 2.2b (upgrade-resilience 2026-10-07) — bound the probe at
        # SYSTEMCTL_PROBE_BUDGET_S (5s); on timeout treat as NOT-active
        # and continue into the start attempt (the MainPID/port verify
        # and comp7 opt-in nohup fallback own the verdict). CAREFUL: do
        # NOT `|| true` the assignment — `|| true` would mask the rc 124
        # and the IF would always take the THEN branch (skipping the
        # journal event). Capture via a temp var + explicit rc-test:
        _sc_isact="$(_run_bounded "$SYSTEMCTL_PROBE_BUDGET_S" -- "$SYSTEMCTL_BIN" is-active "$ENSEMBLE_RESTART_UNIT" 2>/dev/null)"
        _sc_rc=$?
        if [ "$_sc_rc" -ne 0 ]; then
            # D-P1-1 / I-A1..A4 / I-D4 (§4.3 site 3 — the scope arm, identical
            # three-way split to site 2): rc 124 only is a budget class; rc 3
            # is the routine pre-start answer (INFO, NO journal row — the twins'
            # incident shape was exactly this rc being journaled as a timeout);
            # rc 4/other is a genuine probe failure with the TRUE rc.
            if [ "$(_bounded_rc_kind "$_sc_rc")" = "budget" ]; then
                _warn "is-active probe exceeded budget (${SYSTEMCTL_PROBE_BUDGET_S}s) for $ENSEMBLE_RESTART_UNIT — treating as NOT-active; continuing (probe timeout never fails the launcher start on its own)"
                journal_history_append subprocess_wait_timeout \
                    "site=scope_arm_is_active kind=budget reason=child: $(_bounded_rc_reason "$_sc_rc" "" "SYSTEMCTL_PROBE_BUDGET_S=${SYSTEMCTL_PROBE_BUDGET_S}") unit=$ENSEMBLE_RESTART_UNIT rc=$_sc_rc — treating as not-active, continuing" \
                    || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
            elif [ "$_sc_rc" = "3" ]; then
                __sc_state="$(_bounded_state_hint "${_sc_isact}")"
                [ -n "$__sc_state" ] || __sc_state="unknown"
                _log "is-active: site=scope_arm_is_active state=${__sc_state} rc=3 — routine pre-start answer; proceeding into the start attempt"
            else
                _warn "is-active probe FAILED (rc=$_sc_rc state=${_sc_isact:-<none>}) for $ENSEMBLE_RESTART_UNIT — treating as NOT-active; continuing (probe failure never fails the launcher start on its own)"
                journal_history_append subprocess_wait_timeout \
                    "site=scope_arm_is_active kind=nonzero_exit reason=child: $(_bounded_rc_reason "$_sc_rc" "$(_bounded_state_hint "${_sc_isact}")") unit=$ENSEMBLE_RESTART_UNIT rc=$_sc_rc state=$(_bounded_state_hint "${_sc_isact}") — treating as not-active, continuing" \
                    || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
            fi
            _sc_isact=""
        fi
        if [ "$_sc_isact" = "active" ]; then
            if [ -n "${PORT:-}" ] && command -v lsof >/dev/null 2>&1; then
                if lsof -ti:"$PORT" >/dev/null 2>&1; then
                    _log "unit $ENSEMBLE_RESTART_UNIT already active and serving :$PORT — no unit start needed (a no-op's exit 0 is NOT success; verified port serving instead) — logs: journalctl -u $ENSEMBLE_RESTART_UNIT -f (unit journal) or $log (fallback file)"
                    return 0
                fi
                _warn "unit $ENSEMBLE_RESTART_UNIT already active but :$PORT NOT serving — skipping the no-op start; falling back (comp7 opt-in keeps the nohup fallback)"
                _sc_skip_start=1
            else
                _log "unit $ENSEMBLE_RESTART_UNIT already active (port ${PORT:-unknown} / lsof unavailable — not verifiable here; the caller's gate owns the deep checks) — logs: journalctl -u $ENSEMBLE_RESTART_UNIT -f (unit journal) or $log (fallback file)"
                return 0
            fi
        fi
        if [ "$_sc_skip_start" = "0" ]; then
            # 2.2b — bound the scope-arm start at SCOPE_START_BUDGET_S
            # (default 10s). On timeout: journal + fall through to the
            # EXISTING nohup fallback (:3208-3210) — this arm legitimately
            # keeps its fallback (comp7 opt-in KEEPS the nohup fallback —
            # the 7c contract). The frozen 7c wording below stays
            # BYTE-IDENTICAL for non-timeout failures; on timeout the
            # prefix changes to "timed out" (the literal "systemctl
            # start" stays out of the success-path log lines per the 7b
            # substring trap). CAPTURE rc into a var BEFORE the IF (bash
            # `if …; then …; fi` always exits 0 when the body completes
            # — `local _sbs=$?` after `fi` would silently capture 0).
            local _sc_errfile="/tmp/.ensemble-scerr.$$"
            local _sc_rc=0
            _run_bounded "$SCOPE_START_BUDGET_S" -- "$SYSTEMCTL_BIN" start "$ENSEMBLE_RESTART_UNIT" 2>"$_sc_errfile"
            _sc_rc=$?
            if [ "$_sc_rc" = "0" ]; then
                rm -f "$_sc_errfile"
                _log "launcher started via systemd unit $ENSEMBLE_RESTART_UNIT (comp7: cgroup-bound; immune to setsid inheritance) — logs: journalctl -u $ENSEMBLE_RESTART_UNIT -f (unit journal) or $log (fallback file)"
                return 0
            fi
            local _sc_first
            _sc_first="$(head -n1 "$_sc_errfile" 2>/dev/null || true)"
            rm -f "$_sc_errfile"
            # [7b substring-trap guard: this is the FAILURE path — the
            # FROZEN comp7 7c wording (pinned by assert_contains in the
            # comp7 suite) lives ONLY here, never on a success line.
            # On timeout (rc 124) the wording PREFIXES a "timed out"
            # marker; the frozen tail "falling back to nohup launcher
            # (comp7 opt-in path)" stays byte-identical.
            if [ "$_sc_rc" = "124" ]; then
                _warn "$SYSTEMCTL_BIN start $ENSEMBLE_RESTART_UNIT timed out (budget=${SCOPE_START_BUDGET_S}s) — falling back to nohup launcher (comp7 opt-in path)"
                journal_history_append subprocess_wait_timeout \
                    "site=scope_arm_start kind=budget reason=child: $(_bounded_rc_reason "$_sc_rc" "" "SCOPE_START_BUDGET_S=${SCOPE_START_BUDGET_S}") unit=$ENSEMBLE_RESTART_UNIT rc=$_sc_rc — SIGTERM→SIGKILL, falling back to nohup" \
                    || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
            else
                _warn "systemctl start $ENSEMBLE_RESTART_UNIT failed: ${_sc_first:-no stderr} — falling back to nohup launcher (comp7 opt-in path)"
                # Leader ruling D2 (phase1 §4.3 note) — journal SYMMETRY: the
                # genuine non-124 arm gets its own `kind=nonzero_exit` row (the
                # frozen 7c WARN wording above stays BYTE-IDENTICAL).
                journal_history_append subprocess_wait_timeout \
                    "site=scope_arm_start kind=nonzero_exit reason=child: $(_bounded_rc_reason "$_sc_rc") unit=$ENSEMBLE_RESTART_UNIT rc=$_sc_rc — nonzero rc (not a timeout), falling back to nohup" \
                    || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
            fi
        fi
    fi
    # D2a (2026-11-11): NEVER fall through to the nohup lineage on Darwin — a
    # promote-started launcher against a launchd job IS the dual-supervisor
    # incident. A Darwin hand-back that reaches here has no verified launchd
    # job (the preflight would have refused; only a direct call or a hard-set
    # stale state lands here) — fail-safe refusal (exit 78 halt class), never
    # a quiet degrade. (The `unsupervised` acknowledgement that would permit a
    # Darwin nohup lane is D2b.)
    if _supervision_host_allows_launchd; then
        _refuse supervision-launchd-not-verified "restart refused: Darwin hand-back reached the nohup fallback with NO verified launchd job (state=${SUPERVISION_STATE:-?} label=${SUPERVISION_UNIT:-?}) — never start a competing nohup launcher (the 2026-11-11 dual-supervisor incident); install/repair the LaunchAgent (make plist-install) and re-run"
    fi
    ( cd "$INSTALL_DIR" && nohup ./launcher.sh >> data/launcher.log 2>&1 & )
    _log "launcher started (nohup) — logs: $INSTALL_DIR/data/launcher.log"
    # P3 / decision (e) / D-P3.7 — start the 60s boot-window grace.
    # The grace silences transient probe classifications (503/7/22/28)
    # for the post-restart window; ends on earliest of (a) 60s elapsed
    # OR (b) 3 consecutive non-transient greens on livez. Wedged
    # daemons fall back to existing red-fail (a real red is counted
    # even during grace — only transients are suppressed).
    _boot_grace_start
    return 0
}

# ── Supervision classification (ownership-mode commission P1, 2026-09-29) ────
#
# DETECTION + CLASSIFIER ONLY — zero behavior change to existing paths.
# Consumers: stop-path P2 LANDED 2026-09-29 (unit-aware stop + stop-site
# DUAL_FIGHT re-check via stop_via_stop_script + the ENSEMBLE_SUPERVISION_
# RESULT env handoff into scripts/stop-ensemble.sh's unit branch);
# hand-back P3 LANDED 2026-09-29 (restart_via_launcher consumes the
# classification to select the hand-back mode — unit / scope→unit
# self-heal / comp7-a′ / nohup); unit adoption P4 lands later. This
# section exposes the classifier, the preflight refusal seam
# (explicit-unit unresolved + DUAL_FIGHT fault) and the additive txn
# stamp.
#
# §0 MANDATORY (architect 2026-09-29, leader-ratified): the cgroup BASENAME
# of the owning pid's cgroup leaf (/proc/<pid>/cgroup) is the PRIMARY
# signal. INVOCATION_ID is CORROBORATION/DIAGNOSTIC ONLY — it is set for
# transient scopes too (systemd-run --scope mints one for every promote
# executor) and the executor env allowlist strips it, so an
# INVOCATION_ID-first gate would misclassify today's live survivor as
# SCRIPT. On disagreement: trust the cgroup, WARN-once.
#
#   leaf ends '.service'                      → UNIT_MANAGED (yields unit name)
#   leaf matches 'ensemble-upgrade-*.scope'   → SCOPE_SURVIVOR
#   'session-*' / user-slice / init scope     → SCRIPT_NOHUP
#
# TWINS CONTRACT: the ladder is implemented TWICE — this shell twin
# (pipeline-side: preflight + stop site) and _supervision_detect_real in
# daemon/tools/upgrade_journal.py (daemon-side: boot advisory). The ~6-line
# cgroup-leaf parse below is DELIBERATELY duplicated across the twins
# (cross-language seam — twins-pinned); the P5 twins-agree drift-guard test
# enforces agreement. Do not "DRY" them into one side.
#
# Env contract (TWO variables, deliberately NOT merged):
#   ENSEMBLE_SUPERVISION   mode: 'unit' | 'script' | 'auto' (default auto)
#   ENSEMBLE_RESTART_UNIT  unit name / comp7 opt-in — its ALREADY-SHIPPED
#                          semantics in restart_via_launcher are untouched.
#
# Resolution ladder (mirrors the ENSEMBLE_SELF_ENV shape, upgrade_tools
# :199-242): explicit unit/script wins → '0|false|no|off' = silent script
# opt-out → any other garbage → WARN-once → script (fail-toward-script) →
# 'auto'/unset → auto-derive chain: non-Linux → script (ZERO /proc + /run
# reads — the BSD/macOS arms stay byte-identical) → /run/systemd/system
# absent → script → cgroup basename decisive → unreadable cgroup →
# script + WARN-once.
#
# Unit-name resolution (§3 precedence, when a name is needed):
#   env ENSEMBLE_RESTART_UNIT > cgroup-derived (UNIT_MANAGED only) >
#   INSTALL_DIR/.env read directly (precedent: _resolve_wait_s in
#   scripts/stop-ensemble.sh:97-116 — deploy/Makefile invocations do not
#   export the staged env, so the file is the only reliable source).
#   All fail → WARN → script (or exit 78 at PREFLIGHT when mode was
#   explicitly 'unit' — never silent-degrade; the STOP SITE classifies
#   again but never refuses mid-pipeline).
#
#   TWINS DIVERGENCE (M1, review cycle 1 — pinned + documented, NOT
#   implemented in the python twin): the python twin
#   (_supervision_detect_real in daemon/tools/upgrade_journal.py)
#   resolves env > cgroup ONLY — NO .env rung, DELIBERATELY. The daemon
#   is launcher-started, and launcher.sh load_env_file EXPORTS every
#   .env key into the daemon process env, so a .env-sourced
#   ENSEMBLE_RESTART_UNIT already reaches the python env rung
#   transitively; a .env read in the daemon would need the install-dir
#   ladder re-implemented across the twins seam (cyclic import + ambient
#   live-install reads from dev-context tests). The .env rung — and the
#   exit-78 refusals — are PIPELINE-side only.
#
# ── Named deployment topologies + declared×verified outcome map (A1) ────
#
# TWO named topologies (Amendment #1, 2026-09-29):
#   SCRIPT MODE  — the operator runs the script; the launcher lineage
#                  self-respawns (crash backoff, ADR-011). Today's
#                  live/prod shape: direct start, no OS supervisor.
#   SERVICE MODE — an OS service owns monitoring/restart. systemd is THIS
#                  commission's substrate (ENSEMBLE_SUPERVISION=unit +
#                  ENSEMBLE_RESTART_UNIT); service×macOS = launchd =
#                  documented FUTURE scope.
#
# Deployment type is a FIRST-CLASS dimension alongside OS detection: the
# DECLARED mode (ENSEMBLE_SUPERVISION: unit|script|auto) and the
# classifier's VERIFIED state (UNIT_MANAGED / SCOPE_SURVIVOR /
# SCRIPT_NOHUP / DUAL_FIGHT) form one explicit matrix with NAMED outcome
# classes — conforming / degraded / fault (supervision_map_outcome
# below). A1 is BEHAVIOR-PRESERVING: every conforming/degraded/fault PATH
# predates this mapping and is unchanged (exit-78 preflights,
# WARN-degradeds, DUAL_FIGHT halts); A1 adds only naming, documentation
# and the mapping surface.
#
#   declared        verified                 outcome     (existing behavior named)
#   ─────────────── ──────────────────────── ──────────  ──────────────────────
#   script          SCRIPT_NOHUP             conforming  SCRIPT MODE healthy
#   script          UNIT_MANAGED             degraded    decl-mismatch; latent two-masters (UNREACHABLE: explicit script never verifies the cgroup)
#   script          SCOPE_SURVIVOR           degraded    WARN-once + self-heals at next promote with a unit configured
#   unit            UNIT_MANAGED (name set)  conforming  SERVICE MODE healthy
#   unit            UNIT_MANAGED (no name)   fault       exit-78 at preflight — never silent-degrade
#   unit            anything else            fault       never silent-degrade (DUAL_FIGHT arm halts loud)
#   auto            SCRIPT_NOHUP             conforming  auto defers to verification
#   auto            UNIT_MANAGED             conforming  auto defers to verification (P4 adoption signal)
#   auto            SCOPE_SURVIVOR           degraded    WARN-once + self-heals at next promote with a unit configured
#   any             DUAL_FIGHT               fault       halt-loud (two masters must never meet a flip)
#   unknown         anything                 fault       fail-closed
#
# RESOLVED-MODE EQUIVALENCE: supervision_classify resolves 'auto' INTO
# 'unit'|'script' before returning ($SUPERVISION_MODE = the declaration
# POST-ladder), and the resolved-mode cells agree with the declared cells
# above row-for-row — runtime callers pass $SUPERVISION_MODE and the
# twins agree cell-for-cell either way.
#
# OS×deployment matrix — canonical home: docs/runbooks/systemd-adoption.md
# (lands in P4; the forward reference is INTENTIONAL). Rows: script×macOS
# = byte-identical no-systemd arm; script×ubuntu-no-systemd = same arm;
# script×ubuntu-systemd-present-not-adopted = TODAY's live topology;
# service×ubuntu = systemd substrate; service×macOS = FUTURE scope (launchd).
#
# Surfaced outcome name (additive only): the supervision_preflight machine
# line ENSEMBLE_SUPERVISION_OUTCOME=<outcome> (SEPARATE line — the
# ENSEMBLE_SUPERVISION_RESULT=<state>[:<unit>] grammar is FROZEN, P2
# consumes it) + the in_flight journal stamp field "outcome"
# (journal_mark_supervision) + the daemon boot-advisory detail
# (upgrade_journal_sweep.py, python twin).

SUPERVISION_WARN_DONE=0
_supervision_warn_once() {
    [ "$SUPERVISION_WARN_DONE" -eq 0 ] || return 0
    SUPERVISION_WARN_DONE=1
    _warn "supervision: $*"
}

# ERE-escape an install dir for pgrep patterns (stop-ensemble.sh :146-149
# mirror — pinned with the tier mirror below).
_supervision_ere_escape() {
    printf '%s' "$1" | sed -e 's/[][\.*^$()+?{}|\\]/\\&/g'
}

# _supervision_owned_pids — pid discovery MIRRORING the anchored ownership
# tiers of scripts/stop-ensemble.sh (_list_candidates + _classify, :157-226
# — the design cites the tier contract at :11-25). Pinned mirror, not a
# shared helper: stop-ensemble.sh is an executable entry script (sourcing
# it here would run its stop machinery), and D6's "reused, never
# duplicated" rule governs the STOP ACTION, which still goes through
# stop_via_stop_script. Tier 1a: anchored executable paths. Tier 1b:
# ensemble-shaped process whose cwd IS the install dir (logical OR
# physical — lsof reports physical). Deduped, ascending, one per line.
_supervision_owned_pids() {
    local esc phys esc_phys
    esc="$(_supervision_ere_escape "$INSTALL_DIR")"
    phys="$(cd -P "$INSTALL_DIR" 2>/dev/null && pwd || printf '%s' "$INSTALL_DIR")"
    esc_phys="$(_supervision_ere_escape "$phys")"
    {
        pgrep -f "${esc}/launcher\.sh( |$)" 2>/dev/null
        pgrep -f "${esc}/ensemble-prod( |$)" 2>/dev/null
        pgrep -f "${esc}/current/ensemble-prod( |$)" 2>/dev/null
        ps -axo pid=,comm=,args= 2>/dev/null \
            | grep -E '[e]nsemble-prod|[l]auncher\.sh' \
            | awk '{
                pid=$1; comm=$2; $1=""; $2=""; args=$0; sub(/^  */, "", args)
                if (args ~ /(ensemble-prod)( |$)/ || comm ~ /ensemble-prod/ || \
                    args ~ /(^|\/)launcher\.sh( |$)/)
                    print pid}'
    } 2>/dev/null | sort -n | awk '!seen[$0]++' \
      | while read -r pid; do
            [ -n "$pid" ] || continue
            # Tier-1b candidates without an anchored path need the cwd
            # ownership check (foreign installs / dev daemons / editors
            # must never be probed). Anchored-path pids (tier 1a) pass.
            if ! ps -o args= -p "$pid" 2>/dev/null | grep -Eq \
                "(${esc}|${esc_phys})/(current/)?ensemble-prod( |$)|${esc}/launcher\.sh( |$)"; then
                cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
                [ "$cwd" = "$INSTALL_DIR" ] || [ "$cwd" = "$phys" ] || continue
            fi
            printf '%s\n' "$pid"
        done
}

# _supervision_port_holders — the LISTENING pids on $PORT (Fix #2,
# ENSEMBLE-32 repair lane; incident
# docs/incidents/2026-11-14-macos-launchd-handback-halt.md §5.1). The
# census is -sTCP:LISTEN-scoped DELIBERATELY: bare `lsof -ti:$PORT`
# (the stop path's fast condition) also lists every pid holding an
# ESTABLISHED client connection (uptime monitor, browser, kubectl
# sidecar) — reading those as "holders" would refuse every green gate
# on a monitored host. Only the LISTENER is the serving process.
# _flip_verify_port_attribution <post_pid> — D-1 (E2E round 1 🔴, demo
# promote 2026-11-15): the flip-verify's SERVING attribution with a
# BOUNDED LISTEN-GRACE. The defect shape: the launchd verify broke its
# wait-loop on the FIRST owned-pid appearance, then a SINGLE unbuffered
# _supervision_port_holders census issued a crash-shape verdict if
# nothing listened yet — the boot-to-listen span of a migration-heavy
# cold boot (~15-20s on the demo install) exceeded the census instant,
# so a HEALTHY boot was killed mid-flight and the auto-rollback landed
# on an unbootable previous release. The attribution requirement is
# RIGHT; the TIMING was the defect. Shaped fix:
#   - bounded listen-grace: poll the census (FLIP_VERIFY_LISTEN_POLL_S,
#     default 2s — the _probe family cadence) up to
#     FLIP_VERIFY_LISTEN_GRACE_S (default = LIVEZ_BUDGET_S — the SAME
#     operator-tuned "how long a healthy boot may take" budget the
#     later /livez gate uses; a distinct name so it can be shortened
#     independently, same env-overridable-seconds knob conventions);
#   - grace applies ONLY to the waiting-for-an-owned-holder condition.
#     A FOREIGN holder fails IMMEDIATELY at the census that sees it (no
#     grace for wrong-process), and budget exhaustion without a listener
#     still issues the crash-shape verdict (leg (b) preserved);
#   - census-tool failure (rc 2) breaks the poll immediately into the
#     degraded pid-provenance arm — a broken lsof will not heal by
#     polling, and a missing tool is never refusal evidence.
# Sets _FLIP_VERIFY_REASON (global) and returns 0 = attributed serving
# (proceed to the version gate); 1 = refusal (caller sets gate_fail_reason
# from _FLIP_VERIFY_REASON); 2 = degraded-skip (census unprobeable —
# pid provenance only, NOT a pass evidence; the caller logs the skip).
# STATEMENT FORM: call directly, never in $( ) (it sleeps).
# Both knobs route through _numeric_knob (A5 house rule): unset -> the
# documented default (the grace inherits the already-routed LIVEZ_BUDGET_S,
# the operator's tuned boot-patience budget), garbage/float -> a LOUD stderr
# note + the default -- garbage can NEVER kill the promote shell mid-flip-
# verify NOR silently zero the grace (a zero grace would restore the D-1
# crash-shape instant verdict the knob exists to prevent).
FLIP_VERIFY_LISTEN_GRACE_S="$(_numeric_knob FLIP_VERIFY_LISTEN_GRACE_S "${FLIP_VERIFY_LISTEN_GRACE_S:-}" "${LIVEZ_BUDGET_S:-180}")"
FLIP_VERIFY_LISTEN_POLL_S="$(_numeric_knob FLIP_VERIFY_LISTEN_POLL_S "${FLIP_VERIFY_LISTEN_POLL_S:-}" 2)"
_flip_verify_port_attribution() {
    local post_pid="${1:-}" deadline holders hrc owned owned_spaced h foreign
    deadline=$(( $(_now_epoch) + FLIP_VERIFY_LISTEN_GRACE_S ))
    holders="" ; hrc="" ; foreign=""
    while :; do
        holders="$(_supervision_port_holders)"
        hrc=$?
        if [ "$hrc" -eq 2 ]; then
            return 2   # census unprobeable — degraded arm, no polling can heal it
        fi
        if [ -n "$holders" ]; then
            owned="$(_supervision_owned_pids 2>/dev/null)"
            # the owned set is NEWLINE-joined — normalize for token matching
            # (the F1 council fix, mirrored from promote.sh's flip-verify).
            owned_spaced="$(printf '%s' "$owned" | tr '\n' ' ')"
            for h in $holders; do
                [ -n "$h" ] || continue
                case " $owned_spaced " in
                    *" $h "*) ;;
                    *) foreign="$foreign $h" ;;
                esac
            done
            break   # DECIDE NOW: all-owned → success; any foreign → immediate
                    # refusal — the grace covers only "no listener YET", never
                    # a wrong-process holder
        fi
        [ "$(_now_epoch)" -lt "$deadline" ] || break
        sleep "$FLIP_VERIFY_LISTEN_POLL_S"
    done
    if [ -z "$holders" ]; then
        _FLIP_VERIFY_REASON="launchd verify: owned pid $post_pid exists but NOTHING listened on :${PORT} within the ${FLIP_VERIFY_LISTEN_GRACE_S}s listen-grace — the flip produced a process, not a serving daemon (crash-loop shape; D-1 bounded grace exhausted)"
        return 1
    fi
    if [ -n "$foreign" ]; then
        _FLIP_VERIFY_REASON="launchd verify: port :${PORT} holder(s):$foreign NOT in the owned-pid set (owned:$owned_spaced) — the flip is served by a foreign process"
        return 1
    fi
    _FLIP_VERIFY_REASON=""
    return 0
}

_supervision_port_holders() {
    [ -n "${PORT:-}" ] || return 2
    command -v lsof >/dev/null 2>&1 || return 2
    local errtmp out rc
    errtmp="$(mktemp -t attr.XXXXXX 2>/dev/null || printf '/tmp/attr.%s.err' "$$")"
    out="$(lsof -ti:"$PORT" -sTCP:LISTEN 2>"$errtmp")"
    rc=$?
    if [ "$rc" -ne 0 ] && [ -s "$errtmp" ]; then
        rm -f "$errtmp" 2>/dev/null
        return 2   # lsof errored (bad flag / permissions) — not evidence
    fi
    rm -f "$errtmp" 2>/dev/null
    if [ -n "$out" ]; then
        printf '%s\n' "$out"
        return 0
    fi
    return 1
}

# _supervision_launchd_job_pid <label> — the launchd job's CURRENT pid
# (Fix #2 launchd lineage anchor; the `pid = ` line of the job dump —
# the same parse family as the plist `path = ` resolver above). Bounded
# (LAUNCHD_PROBE_BUDGET_S). Prints the pid; returns nonzero when the
# job is not registered. Prints NOTHING for a registered-but-pidless
# job (crash-looping: launchctl answers `pid = 4294967295` (-1
# sentinel) or omits the line) — rc 0, empty output: the CALLER
# distinguishes "probe tool failed" (skip, no evidence) from "the job
# has NO live process while the port serves someone" (the v0.30.3
# dual-supervisor shape — refuse).
_supervision_launchd_job_pid() {
    local label="${1:-}" dump rc
    [ -n "$label" ] || return 1
    command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1 || return 2
    dump="$(_run_bounded "$LAUNCHD_PROBE_BUDGET_S" -- "$LAUNCHCTL_BIN" print "$(_supervision_launchd_domain)/${label}" 2>/dev/null)"
    rc=$?
    [ "$rc" -eq 0 ] || return 2
    printf '%s\n' "$dump" | sed -n 's/^[[:space:]]*pid = //p' | head -1 \
        | while IFS= read -r p; do
            case "$p" in
                ''|0|4294967295) ;;   # no live process behind the job
                *[!0-9]*) ;;
                *) printf '%s\n' "$p" ;;
            esac
        done
    return 0
}

# _supervision_pid_in_launchd_lineage <pid> <root> — is <pid> the root
# itself or a bounded ppid-descendant of it? launchd's job pid is the
# LAUNCHER; the daemon is its foreground child, so one or two hops
# always suffice (depth 12 is paranoia against exotic trees). PPID
# walking is safe here BY SHAPE: the launcher does not double-fork the
# daemon (KeepAlive supervises the tree), which is exactly why the
# Linux cgroup containment is used for systemd instead — see the
# supervision_dualfight_check header.
_supervision_pid_in_launchd_lineage() {
    local cur="${1:-}" root="${2:-}" depth=0
    [ -n "$cur" ] && [ -n "$root" ] || return 1
    while [ -n "$cur" ] && [ "$depth" -lt 12 ]; do
        [ "$cur" = "$root" ] && return 0
        cur="$(ps -o ppid= -p "$cur" 2>/dev/null | tr -d '[:space:]')"
        case "$cur" in
            ''|0|1) return 1 ;;
            *[!0-9]*) return 1 ;;
        esac
        depth=$(( depth + 1 ))
    done
    return 1
}

# _supervision_pid_cgroup_path <pid> — the pid's cgroup LAST LINE with the
# leading `<id>:` prefix stripped (the full cgroup PATH, e.g.
# /system.slice/ensemble-main.service/mcp-child.scope), or nothing.
# TWINS-PINNED parse: the ONE shared read used by both projections —
# _supervision_pid_cgroup_leaf (basename) and the containment ladder's R2
# rung (full path). A third copy of this parse must never be minted
# (promote-handback-halt Phase 1 §5.3, A32). EVERY /proc read is behind
# [ -r ... ] per the P1 contract.
_supervision_pid_cgroup_path() {
    local pid="${1:-}" line path
    [ -n "$pid" ] || return 1
    [ -r "/proc/$pid/cgroup" ] || return 1
    line="$(tail -n 1 "/proc/$pid/cgroup" 2>/dev/null)" || return 1
    [ -n "$line" ] || return 1
    path="${line##*:}"
    printf '%s\n' "$path"
}

# _supervision_pid_cgroup_leaf <pid> — basename of the pid's cgroup LEAF,
# or nothing. TWINS-PINNED parse (~6 lines, duplicated in the python twin
# _supervision_read_cgroup_leaf — cross-language seam; see the section
# comment). EVERY /proc read is behind [ -r ... ] per the P1 contract.
# Projection over _supervision_pid_cgroup_path (ONE parse, two projections).
_supervision_pid_cgroup_leaf() {
    local pid="$1" path leaf
    [ -n "$pid" ] || return 1
    path="$(_supervision_pid_cgroup_path "$pid" 2>/dev/null)" || return 1
    [ -n "$path" ] || return 1
    leaf="${path##*/}"
    [ -n "$leaf" ] || return 1
    printf '%s\n' "$leaf"
}

# _supervision_pid_ancestor_of <pid> <roots-spaced> — is <pid> inside the
# given pid SET (spaced list) by birth, i.e. is it a root itself or a
# bounded ppid-descendant of one? T3 (promote-handback-halt Phase 1 §5.3
# rung R3). Same bounded-walk shape as _supervision_pid_in_launchd_lineage
# (depth 12) but testing membership in the argv/cwd-anchored OWNED SET
# (Layer 1's trust root) rather than a single job pid — portable, because it
# uses POSIX `ps` only (so it also serves Darwin / nohup shapes).
#
# Returns: 0 = reachable (contained); 1 = the walk COMPLETED without
# entering the set (a reparented orphan ends at pid 1 — answered, and pid
# reuse is judged on the CURRENT occupant, so no false accept is reachable,
# architect closure C-5); 2 = cannot answer (`ps` unavailable — a missing
# tool is NEVER refusal evidence, the _gate_attr_unprobeable contract).
_supervision_pid_ancestor_of() {
    local cur="${1:-}" roots="${2:-}" depth=0
    [ -n "$cur" ] || return 2
    # An EMPTY owned set is an ANSWERED negative: nothing can be the
    # ancestor of a process that is not in a set that has no members
    # (phase1 P8 / M3 — "R1 empty, R2 n/a, R3 root set empty => 1").
    [ -n "$roots" ] || return 1
    command -v ps >/dev/null 2>&1 || return 2
    while [ -n "$cur" ] && [ "$depth" -lt 12 ]; do
        case " $roots " in
            *" $cur "*) return 0 ;;
        esac
        cur="$(ps -o ppid= -p "$cur" 2>/dev/null | tr -d '[:space:]')"
        case "$cur" in
            ''|0|1) return 1 ;;
            *[!0-9]*) return 1 ;;
        esac
        depth=$(( depth + 1 ))
    done
    return 1
}

# _supervision_unit_from_dotenv — read ENSEMBLE_RESTART_UNIT directly from
# $INSTALL_DIR/.env (§3 step 3; _resolve_wait_s precedent: sed-extract,
# strip optional quotes, validate non-empty).
_supervision_unit_from_dotenv() {
    local raw env_file="${INSTALL_DIR:-}/.env"
    [ -n "${INSTALL_DIR:-}" ] || return 1
    raw="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}ENSEMBLE_RESTART_UNIT[[:space:]]*=[[:space:]]*//p' "$env_file" 2>/dev/null | head -1)"
    raw="${raw%$'\r'}"
    case "$raw" in
        \"*\") raw="${raw#\"}"; raw="${raw%\"}" ;;
        \'*\') raw="${raw#\'}"; raw="${raw%\'}" ;;
    esac
    [ -n "$raw" ] || return 1
    printf '%s\n' "$raw"
}

# _supervision_derive_unit_name — LAST-resort unit-name derivation for the
# nohup-survivor heal in supervision_classify (supervisor war r3, 2026-09-30):
# PINNED MIRROR of adopt-unit.sh _derive_unit_name (the header naming rule:
# ensemble-<slug>.service, slug = basename(INSTALL_DIR) minus a leading
# "agents-"/"ensemble-", sanitized to [0-9A-Za-z@._-]; empty or bare
# "ensemble" slug → "main" — ~/agents-ensemble → ensemble-main.service,
# ~/agents-ensemble-demo → ensemble-demo.service). Mirrored, not sourced:
# adopt-unit.sh is an entry script (sourcing it runs the adoption
# machinery); same pin discipline as _supervision_owned_pids (the
# stop-ensemble.sh tier mirror). Deliberately NOT the nonexistent
# 'ensemble-${TARGET}.service' template (supervisor war r3 review: on
# ensemble-vm that would adopt the RETIRED ensemble-live.service lane
# while INSTALL_DIR/.env pins ensemble-main.service).
_supervision_derive_unit_name() {
    local base
    base="$(basename "${INSTALL_DIR:-}")"
    case "$base" in agents-*) base="${base#agents-}" ;; esac
    # avoid a double prefix: agents-ensemble-demo → ensemble-demo (not
    # ensemble-ensemble-demo); a BARE ensemble dir falls to "main" below.
    case "$base" in ensemble-*) base="${base#ensemble-}" ;; esac
    base="$(printf '%s' "$base" | sed -e 's/[^0-9A-Za-z@._-]/-/g' -e 's/^[-.]*//' -e 's/[-.]*$//')"
    if [ -z "$base" ] || [ "$base" = "ensemble" ]; then
        base="main"
    fi
    printf 'ensemble-%s.service' "$base"
}

# supervision_classify — THE classifier. Uncached per-run (state may change
# across a run: the daemon stops mid-promote). Sets globals:
#   SUPERVISION_MODE   resolved mode: 'unit' | 'script' | 'launchd'
#   SUPERVISION_STATE  SCRIPT_NOHUP | UNIT_MANAGED | SCOPE_SURVIVOR |
#                      LAUNCHD_MANAGED | LAUNCHD_NOT_VERIFIED
#   SUPERVISION_UNIT   unit name / launchd label ('' when none)
#   SUPERVISION_EXPLICIT_UNIT   1 ONLY for a ladder-top EXPLICIT 'unit'
#                       declaration (M3 discriminator — auto-derived
#                       UNIT_MANAGED also resolves MODE=unit)
#   SUPERVISION_REFUSE_REASON   '' | 'unit-unresolved' |
#                       'unit-incapable-host' (M3) | 'launchd-incapable-host' |
#                       'launchd-not-verified' | 'unsupervised-declaration-required' |
#                       'declaration-invalid' (D2a — the preflight branches its
#                       exit-78 _refuse token on this)
# Prints EXACTLY ONE machine-readable line:
#   ENSEMBLE_SUPERVISION_RESULT=<state>[:<unit>]
# Returns 1 for the EXPLICIT / Darwin fail-closed classes (the PREFLIGHT
# caller refuses with exit 78; the stop site ignores rc — it never refuses
# mid-pipeline). SUPERVISION_REFUSE_REASON discriminates them:
#   "unit-unresolved"     — explicit unit, no name resolvable (env >
#                           cgroup > INSTALL_DIR/.env all empty)
#   "unit-incapable-host" — explicit unit, this host CANNOT honor unit
#                           supervision at all (non-Linux / no systemd /
#                           systemctl unresolvable) — M3 (review cycle 1,
#                           leader-ruled FAIL-CLOSED): silently degrading
#                           an explicit assertion to the nohup lineage at
#                           rc 0 is the r-f82e surprise class; the name
#                           (if one resolved from env/.env) is carried for
#                           the refusal diagnosis, never acted on.
#   D2a (2026-11-11, promote-full-auto) — Darwin is FAIL-CLOSED: a declared
#   `launchd` on a non-Darwin host refuses (launchd-incapable-host); AUTO and
#   declared `launchd` on Darwin PROBE launchd and refuse
#   (launchd-not-verified) on zero/multiple/unresolvable — the AUTO path
#   NEVER falls through to SCRIPT_NOHUP on Darwin (the 2026-11-11 incident
#   mechanism); declared `script`/opt-out on Darwin refuses
#   (unsupervised-declaration-required; the `unsupervised` VALUE is D2b);
#   garbage on Darwin refuses (declaration-invalid). Non-Darwin non-Linux
#   shapes (BSD) keep the byte-identical SCRIPT_NOHUP arm.
# Sets one additional consumed global: SUPERVISION_EXPLICIT_UNIT (1 only
# when the ladder top saw an EXPLICIT 'unit' declaration; 0 otherwise —
# an auto-derived UNIT_MANAGED leaf also resolves MODE=unit, so MODE alone
# cannot discriminate the declared shape). restart_via_launcher's M3
# refusal arm keys on it.
supervision_classify() {
    SUPERVISION_MODE="script"
    SUPERVISION_STATE="SCRIPT_NOHUP"
    SUPERVISION_UNIT=""
    SUPERVISION_EXPLICIT_UNIT=0
    SUPERVISION_REFUSE_REASON=""
    # PC-1 stash reset (2026-11-14 hand-back halt): a re-classify must not
    # inherit a previous classification's plist path — the job may have
    # been re-registered from a different file between the two runs.
    SUPERVISION_LAUNCHD_PLIST_PATH=""
    local raw low leaf pid inv heal_unit heal_on
    heal_on=0
    raw="${ENSEMBLE_SUPERVISION:-}"
    raw="$(printf '%s' "$raw" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    low="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"

    # ── explicit / opt-out / garbage (ladder top) ────────────────────────
    if [ -n "$low" ]; then
        case "$low" in
            0|false|no|off)
                # D2a: an opt-out / `script` declaration on Darwin is NOT a
                # legitimate acknowledgement — it would silent-degrade the
                # launchd host to the nohup lineage (the 2026-11-11 incident
                # mechanism) at rc 0. Refuse; the `unsupervised` VALUE that
                # acknowledges an intentionally unsupervised Darwin lane is D2b.
                if _supervision_host_allows_launchd; then
                    _supervision_warn_once "mode '$low' on Darwin is not a legitimate supervision acknowledgement — Darwin is fail-closed: declare 'launchd' (or 'auto'), or unset ENSEMBLE_SUPERVISION; an unsupervised Darwin lane needs a distinct declaration (not yet supported) — preflight must refuse (78)"
                    SUPERVISION_MODE="script"
                    SUPERVISION_REFUSE_REASON="unsupervised-declaration-required"
                    printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                    return 1
                fi
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                return 0
                ;;
            script)
                if _supervision_host_allows_launchd; then
                    _supervision_warn_once "mode 'script' on Darwin is not a legitimate supervision acknowledgement — Darwin is fail-closed: declare 'launchd' (or 'auto'), or unset ENSEMBLE_SUPERVISION; an unsupervised Darwin lane needs a distinct declaration (not yet supported) — preflight must refuse (78)"
                    SUPERVISION_MODE="script"
                    SUPERVISION_REFUSE_REASON="unsupervised-declaration-required"
                    printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                    return 1
                fi
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                return 0
                ;;
            unit)
                SUPERVISION_MODE="unit"
                SUPERVISION_STATE="UNIT_MANAGED"
                SUPERVISION_EXPLICIT_UNIT=1
                SUPERVISION_UNIT="${ENSEMBLE_RESTART_UNIT:-}"
                if [ -z "$SUPERVISION_UNIT" ] && [ "$(uname -s)" = "Linux" ] \
                   && [ -d /run/systemd/system ]; then
                    # 2>/dev/null (D3-7/H2): the `head -1` early close is a
                    # BENIGN EPIPE the helper would print as "write error: Broken
                    # pipe" -- 64 historic lines of red herring in the aggregate
                    # log. Suppressing the helper's stderr removes the symptom
                    # without hiding a real failure (stdout unaffected).
                    pid="$(_supervision_owned_pids 2>/dev/null | head -1)"
                    if [ -n "$pid" ]; then
                        leaf="$(_supervision_pid_cgroup_leaf "$pid")"
                        case "$leaf" in *.service) SUPERVISION_UNIT="$leaf" ;; esac
                    fi
                fi
                if [ -z "$SUPERVISION_UNIT" ]; then
                    SUPERVISION_UNIT="$(_supervision_unit_from_dotenv)" \
                        || SUPERVISION_UNIT=""
                fi
                # ── M3 host-capability rung (review cycle 1, leader-ruled
                # FAIL-CLOSED): an EXPLICIT unit assertion on a host that
                # cannot honor unit supervision (non-Linux / no systemd /
                # systemctl unresolvable) refuses at preflight (78) — NEVER
                # loud-degrades to the nohup lineage at rc 0 (silently
                # degrading an explicit assertion is the r-f82e surprise
                # class). The resolved name (env/.env), if any, is carried
                # in the machine line + globals for the refusal diagnosis.
                # Non-explicit (auto/script) modes never reach this rung.
                # Zero /proc reads on incapable hosts (the cgroup rung
                # above is already uname-guarded).
                if ! _supervision_host_allows_unit || ! command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1; then
                    _supervision_warn_once "mode 'unit' explicit but this host cannot honor unit supervision (non-Linux / no systemd / no resolvable systemctl${SUPERVISION_UNIT:+; name '$SUPERVISION_UNIT' from env/INSTALL_DIR/.env is unusable here}) — preflight must refuse (78); never silent-degrade to nohup"
                    printf 'ENSEMBLE_SUPERVISION_RESULT=%s%s\n' "$SUPERVISION_STATE" "${SUPERVISION_UNIT:+:$SUPERVISION_UNIT}"
                    SUPERVISION_REFUSE_REASON="unit-incapable-host"
                    return 1
                fi
                if [ -n "$SUPERVISION_UNIT" ]; then
                    printf 'ENSEMBLE_SUPERVISION_RESULT=%s:%s\n' "$SUPERVISION_STATE" "$SUPERVISION_UNIT"
                    return 0
                fi
                # explicit unit, no resolvable name — never silent-degrade;
                # the preflight caller turns rc 1 into exit 78.
                _supervision_warn_once "mode 'unit' explicit but no unit name resolvable (env ENSEMBLE_RESTART_UNIT unset, cgroup not unit-managed, INSTALL_DIR/.env has none) — preflight must refuse (78)"
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                return 1
                ;;
            launchd)
                # D2a: declared launchd (B4). Modelled on the unit arm incl.
                # the host-capability rung. On a non-Darwin host this REFUSES
                # (launchd-incapable-host) exactly like unit-on-incapable; on
                # Darwin it PROBES (gui domain primary) and resolves the label
                # (ENSEMBLE_LAUNCHD_LABEL > auto-detect) — zero/multiple/
                # unresolvable → refuse (launchd-not-verified). NEVER a
                # SCRIPT_NOHUP fallthrough on Darwin.
                SUPERVISION_MODE="launchd"
                SUPERVISION_STATE="LAUNCHD_MANAGED"
                if ! _supervision_host_allows_launchd || ! command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1; then
                    SUPERVISION_STATE="LAUNCHD_NOT_VERIFIED"
                    _supervision_warn_once "mode 'launchd' explicit but this host cannot honor launchd supervision (non-Darwin / no resolvable launchctl) — preflight must refuse (78); never silent-degrade to nohup"
                    printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                    SUPERVISION_REFUSE_REASON="launchd-incapable-host"
                    return 1
                fi
                if _supervision_launchd_detect; then
                    SUPERVISION_STATE="LAUNCHD_MANAGED"
                    # PC-1 stash (belt): capture the plist path NOW, while
                    # the job is still registered and `launchctl print`
                    # answers — the stop phase bootouts the job before the
                    # hand-back, killing the live probe exactly when the
                    # hand-back needs it (2026-11-14 halt, incident §3.2).
                    SUPERVISION_LAUNCHD_PLIST_PATH="$(_supervision_launchd_plist_path "$SUPERVISION_UNIT" 2>/dev/null)" \
                        || SUPERVISION_LAUNCHD_PLIST_PATH=""
                    printf 'ENSEMBLE_SUPERVISION_RESULT=%s:%s\n' "$SUPERVISION_STATE" "$SUPERVISION_UNIT"
                    return 0
                fi
                SUPERVISION_STATE="LAUNCHD_NOT_VERIFIED"
                _supervision_warn_once "mode 'launchd' explicit but no single launchd job could be verified for $INSTALL_DIR/launcher.sh (gui domain — zero or multiple matches, or unreadable) — preflight must refuse (78); never silent-degrade"
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                SUPERVISION_REFUSE_REASON="launchd-not-verified"
                return 1
                ;;
            auto)
                ;;
            *)
                # D2a: garbage on Darwin ⇒ REFUSE (today: warn-once + rc 0 +
                # mode=script was the incident mechanism). NON-Darwin keeps
                # the byte-identical WARN-toward-script behavior.
                if _supervision_host_allows_launchd; then
                    SUPERVISION_MODE="launchd"
                    SUPERVISION_STATE="LAUNCHD_NOT_VERIFIED"
                    _supervision_warn_once "garbage ENSEMBLE_SUPERVISION='$raw' on Darwin — fail-closed: refusing (never silent-degrade to script at rc 0); unset it or declare 'auto'/'launchd' — preflight must refuse (78)"
                    printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                    SUPERVISION_REFUSE_REASON="declaration-invalid"
                    return 1
                fi
                _supervision_warn_once "garbage ENSEMBLE_SUPERVISION='$raw' — WARN-once, failing toward script"
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                return 0
                ;;
        esac
    fi

    # ── auto-derive chain (ZERO /proc + /run reads before the guards) ────
    # D2a: AUTO on Darwin PROBES launchd FIRST and NEVER falls through to
    # SCRIPT_NOHUP (the 2026-11-11 incident mechanism: any non-Linux host
    # returned SCRIPT_NOHUP + rc 0 before any detection). Exactly one job ⇒
    # LAUNCHD_MANAGED:<label>; zero / multiple / unresolvable ⇒ rc 1 refusal.
    if _supervision_host_allows_launchd; then
        SUPERVISION_MODE="launchd"
        SUPERVISION_STATE="LAUNCHD_MANAGED"
        if command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1 && _supervision_launchd_detect; then
            SUPERVISION_STATE="LAUNCHD_MANAGED"
            # PC-1 stash (belt) — same rationale as the explicit-launchd arm:
            # capture while registered; the hand-back consumes it post-bootout.
            SUPERVISION_LAUNCHD_PLIST_PATH="$(_supervision_launchd_plist_path "$SUPERVISION_UNIT" 2>/dev/null)" \
                || SUPERVISION_LAUNCHD_PLIST_PATH=""
            printf 'ENSEMBLE_SUPERVISION_RESULT=%s:%s\n' "$SUPERVISION_STATE" "$SUPERVISION_UNIT"
            return 0
        fi
        SUPERVISION_STATE="LAUNCHD_NOT_VERIFIED"
        _supervision_warn_once "auto on Darwin but no single launchd job could be verified for $INSTALL_DIR/launcher.sh (gui domain — zero or multiple matches, or unreadable) — preflight must refuse (78); never fall through to the nohup lineage"
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
        SUPERVISION_REFUSE_REASON="launchd-not-verified"
        return 1
    fi
    if [ "$(uname -s)" != "Linux" ]; then
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
        return 0
    fi
    if ! [ -d /run/systemd/system ]; then
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
        return 0
    fi
    pid="$(_supervision_owned_pids 2>/dev/null | head -1)"
    if [ -z "$pid" ]; then
        _supervision_warn_once "no owned pid discovered (anchored tiers vs INSTALL_DIR=$INSTALL_DIR) — classifying script"
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
        return 0
    fi
    leaf="$(_supervision_pid_cgroup_leaf "$pid")"
    if [ -z "$leaf" ]; then
        _supervision_warn_once "cgroup unreadable for owned pid $pid — classifying script"
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
        return 0
    fi
    case "$leaf" in
        *.service)
            SUPERVISION_STATE="UNIT_MANAGED"
            SUPERVISION_MODE="unit"
            SUPERVISION_UNIT="$leaf"
            ;;
        ensemble-upgrade-*.scope)
            SUPERVISION_STATE="SCOPE_SURVIVOR"
            ;;
        session-*.scope|init.scope|user.slice|*.user.slice|user-*.slice|*.user-*.slice|machine.slice|*.machine.slice)
            SUPERVISION_STATE="SCRIPT_NOHUP"
            ;;
        *)
            # Unknown leaf shape — fail toward script, no unit name.
            SUPERVISION_STATE="SCRIPT_NOHUP"
            ;;
    esac

    # ── INVOCATION_ID corroboration (diagnostic ONLY — §0) — read the
    # OWNING pid's environ (same-uid readable; [ -r ] guarded like every
    # other /proc read), not our own: the script's INVOCATION_ID describes
    # the script (a scope executor mints one), not the daemon.
    inv=""
    if [ -r "/proc/$pid/environ" ]; then
        inv="$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^INVOCATION_ID=//p' | head -1)"
    fi
    if [ -n "$inv" ] && [ "$SUPERVISION_STATE" != "UNIT_MANAGED" ]; then
        _supervision_warn_once "owned pid $pid has INVOCATION_ID but cgroup leaf '$leaf' classifies $SUPERVISION_STATE — trusting cgroup (§0: transient scopes mint INVOCATION_ID too)"
    elif [ -z "$inv" ] && [ "$SUPERVISION_STATE" = "UNIT_MANAGED" ]; then
        _supervision_warn_once "UNIT_MANAGED (leaf '$leaf') but owned pid $pid has no INVOCATION_ID — trusting cgroup (§0 corroboration only)"
    fi

    # ── nohup-survivor heal (supervisor war r3, 2026-09-30) ─────────────
    # A nohup survivor from an old-version promote (cgroup session-*.scope
    # / a user or machine slice / an unknown leaf) kept EVERY later promote
    # on the nohup spawn path: the v0.16.5 unit hand-back was compiled in
    # but never reached, because THIS classifier trusted the survivor's
    # cgroup (live 139 restarts ~5.5h; demo 2,561 restarts 43h). Heal:
    # when the outcome would be SCRIPT_NOHUP but a systemd unit FILE
    # exists for this install, adopt UNIT_MANAGED so the stop/hand-back
    # paths treat the install as unit-managed — the FIRST promote running
    # this code hands the lineage back to systemd regardless of the
    # current daemon's cgroup. Guards UNCHANGED and still downstream:
    # supervision_dualfight_check still halts two LIVE masters, and the
    # unit-path stop still fails loud when a foreign pid holds the port —
    # the heal NEVER bypasses them (fail-closed everywhere). Name
    # resolution: INSTALL_DIR/.env pin FIRST (_supervision_unit_from_dotenv
    # — the per-install record adopt-unit.sh stages; the pin must conform
    # to the ensemble-*.service shape or it is treated as ABSENT —
    # finding 4, r3 hardening), then the adopt-unit naming-convention
    # derivation; the -f file gate applies to the
    # RESOLVED name — file absent → NO heal (SCRIPT_NOHUP stands).
    # COLD-STATE BYPASS IS DELIBERATE (finding 3, r3 hardening): the
    # auto-derive early returns above (no owned pid / unreadable cgroup)
    # skip the heal BY DESIGN — those arms describe a cold or
    # unobservable install, not a nohup survivor; adopting from them is a
    # separate commission if ever wanted. Host
    # guards: the auto-derive chain above already returned on non-Linux /
    # no-/run/systemd/system shapes (BSD/macOS arms byte-identical;
    # requirement: unchanged SCRIPT_NOHUP), and _supervision_host_allows_
    # unit re-asserts both defensively. EXPLICIT script/opt-out
    # declarations return at the ladder top — the heal can never override
    # an operator opt-out. SUPERVISION_EXPLICIT_UNIT stays 0 (auto-derived
    # heal, not a ladder-top declaration — restart_via_launcher's M3 arm
    # keys on it). SCOPE_SURVIVOR is untouched: it already carries its own
    # hand-back (the comp7 opt-in path).
    if [ "$SUPERVISION_STATE" = "SCRIPT_NOHUP" ] && _supervision_host_allows_unit; then
        heal_unit="$(_supervision_unit_from_dotenv)" || heal_unit=""
        case "$heal_unit" in
            ensemble-*.service) ;;
            # finding 4: a non-conforming .env pin is treated as ABSENT
            # (falls through to the derivation rung) — the heal never
            # adopts a name outside the ensemble-*.service family the
            # adopt-unit polkit pattern governs.
            *) heal_unit="" ;;
        esac
        if [ -z "$heal_unit" ]; then
            heal_unit="$(_supervision_derive_unit_name)"
        fi
        if [ -n "$heal_unit" ] && [ -f "$SUPERVISION_UNIT_DIR/$heal_unit" ]; then
            SUPERVISION_STATE="UNIT_MANAGED"
            SUPERVISION_MODE="unit"
            SUPERVISION_UNIT="$heal_unit"
            heal_on=1
            _log "supervision: unit file exists for $heal_unit at $SUPERVISION_UNIT_DIR — adopting UNIT_MANAGED (healing from nohup-survivor state; cgroup leaf '$leaf' classified SCRIPT_NOHUP)"
        fi
    fi

    if [ "$SUPERVISION_STATE" = "UNIT_MANAGED" ] && [ -n "$SUPERVISION_UNIT" ]; then
        # §3: env-explicit unit name OVERRIDES the cgroup-derived one —
        # NATIVE path only. POST-HEAL (finding 1, r3 hardening): the
        # ambient ENSEMBLE_RESTART_UNIT override is WITHHELD — the healed
        # resolution (.env pin > adopt-unit derivation, both already -f
        # gated + conformance-checked) outranks the operator shell.
        # Rationale: file-existence + ensemble-*.service conformance
        # gating alone cannot reject a PRESENT-FILE stale lane
        # (ensemble-vm's retired ensemble-live.service still carries its
        # unit file), so any post-heal env application re-opens the
        # silent stale-lane adoption the heal exists to close. Divergence
        # logged loud so the operator sees their ambient pin was not
        # honored.
        if [ -n "${ENSEMBLE_RESTART_UNIT:-}" ]; then
            if [ "$heal_on" = "1" ]; then
                if [ "$ENSEMBLE_RESTART_UNIT" != "$SUPERVISION_UNIT" ]; then
                    _log "supervision: keeping healed unit $SUPERVISION_UNIT — ambient ENSEMBLE_RESTART_UNIT='$ENSEMBLE_RESTART_UNIT' NOT applied post-heal (stale-lane guard; the override needs no-heal provenance + ensemble-*.service conformance + a $SUPERVISION_UNIT_DIR file)"
                fi
            else
                SUPERVISION_UNIT="$ENSEMBLE_RESTART_UNIT"
            fi
        fi
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s:%s\n' "$SUPERVISION_STATE" "$SUPERVISION_UNIT"
        return 0
    fi
    printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
    return 0
}

# supervision_map_outcome <declared> <verified> [<unit>] — the NAMED
# declared×verified mapping (Amendment #1 delta A1, 2026-09-29). Echoes
# ONE line '<outcome>|<short reason>' (outcome = conforming | degraded |
# fault; reason = twins-pinned prose, never contains '|'); ALWAYS returns
# 0 — this is a pure NAMING function, not a gate: it never refuses,
# warns, or journals. The runtime paths it names (exit-78 preflight
# refusal, WARN-once degradeds, DUAL_FIGHT halt) predate A1 and are
# untouched.
#
#   $1 declared — unit | script | auto (ENSEMBLE_SUPERVISION vocabulary;
#                 runtime callers pass $SUPERVISION_MODE — RESOLVED-MODE
#                 EQUIVALENCE, section comment)
#   $2 verified — SCRIPT_NOHUP | UNIT_MANAGED | SCOPE_SURVIVOR |
#                 DUAL_FIGHT (classifier / dualfight vocabulary)
#   $3 unit     — optional resolved unit name; ONLY consulted for
#                 declared=unit × verified=UNIT_MANAGED, where EMPTY
#                 names the reachable exit-78 cell (explicit unit,
#                 nothing resolvable) → fault.
#
# Cell-for-cell table: see the section comment above. TWINS-PINNED: the
# python twin supervision_outcome (daemon/tools/upgrade_journal.py)
# implements the IDENTICAL table — outcome names AND reason strings
# byte-identical; the P5 twins-agree drift-guard test pins this. Do not
# "DRY" them into one side.
supervision_map_outcome() {
    local declared="${1:-}" verified="${2:-}" unit="${3:-}" low
    low="$(printf '%s' "$declared" | tr '[:upper:]' '[:lower:]')"

    if [ "$verified" = "DUAL_FIGHT" ]; then
        printf 'fault|two masters live (unit active/armed while owned pids or port-holder sit outside it) — halt-loud\n'
        return 0
    fi
    case "$low" in
        script)
            case "$verified" in
                SCRIPT_NOHUP)
                    printf 'conforming|script topology declared and verified (SCRIPT MODE — launcher lineage self-respawns)\n' ;;
                UNIT_MANAGED)
                    printf 'degraded|declaration mismatch: script declared but unit-managed reality — latent two-masters hazard (unreachable today: explicit script never verifies the cgroup)\n' ;;
                SCOPE_SURVIVOR)
                    printf 'degraded|scope survivor under script declaration: WARN-once + self-heals at next promote with a unit configured\n' ;;
                LAUNCHD_MANAGED)
                    printf 'degraded|declaration mismatch: script declared but launchd reality — latent two-masters hazard (unreachable today: Darwin'"'"'s declared script mode refuses before verification)\n' ;;
                *) printf 'fault|unknown declared mode or verified state — fail-closed\n' ;;
            esac
            ;;
        unit)
            case "$verified" in
                UNIT_MANAGED)
                    if [ -n "$unit" ]; then
                        printf 'conforming|service topology declared and verified, unit name resolved (SERVICE MODE)\n'
                    else
                        printf 'fault|unit declared but no unit name resolvable — preflight refuses (exit 78), never silent-degrade\n'
                    fi
                    ;;
                SCRIPT_NOHUP)
                    printf 'fault|declaration mismatch: unit declared but script reality — never silent-degrade (unreachable today: explicit unit never verifies the cgroup)\n' ;;
                SCOPE_SURVIVOR)
                    printf 'fault|declaration mismatch: unit declared but scope-survivor reality — never silent-degrade (unreachable today)\n' ;;
                LAUNCHD_MANAGED)
                    printf 'fault|declaration mismatch: unit declared but launchd reality — never silent-degrade (unreachable today: Darwin verification is launchd-only)\n' ;;
                *) printf 'fault|unknown declared mode or verified state — fail-closed\n' ;;
            esac
            ;;
        auto)
            case "$verified" in
                SCRIPT_NOHUP)
                    printf 'conforming|auto defers to verification: script topology confirmed (today'"'"'s nohup direct/live-prod shape)\n' ;;
                UNIT_MANAGED)
                    printf 'conforming|auto defers to verification: unit topology confirmed (P4 adoption signal)\n' ;;
                SCOPE_SURVIVOR)
                    printf 'degraded|scope survivor under auto declaration: WARN-once + self-heals at next promote with a unit configured\n' ;;
                LAUNCHD_MANAGED)
                    printf 'conforming|auto defers to verification: launchd topology confirmed (D2a supervised lane)\n' ;;
                *) printf 'fault|unknown declared mode or verified state — fail-closed\n' ;;
            esac
            ;;
        launchd)
            # A2 (review round 1): the Darwin supervised lane resolves
            # SUPERVISION_MODE=launchd — a HEALTHY verified launchd install
            # had NO cell here and fell through to the fail-closed "unknown
            # declared mode" arm ⇒ the boot advisory journalled
            # outcome=fault for a HEALTHY supervisor. These cells report the
            # conforming-equivalent state (advisory-only naming).
            case "$verified" in
                LAUNCHD_MANAGED)
                    if [ -n "$unit" ]; then
                        printf 'conforming|launchd topology declared and verified, job label resolved (LAUNCHD MODE — the supervisor owns the launcher)\n'
                    else
                        printf 'fault|launchd declared but no job label resolvable — refusing the whole-domain bootout, never silent-degrade\n'
                    fi
                    ;;
                *)
                    printf 'fault|declaration mismatch: launchd declared but non-launchd reality — never silent-degrade (unreachable today: Darwin verification is launchd-only)\n' ;;
            esac
            ;;
        *) printf 'fault|unknown declared mode or verified state — fail-closed\n' ;;
    esac
    return 0
}

# supervision_dualfight_check — §6 FAULT detector (call before any journal
# txn / stop / flip mutation): when the resolved unit is active or
# auto-restart-armed AND reality disagrees (owned pids NOT inside the
# unit's cgroup, OR the port-holder outside the unit's MainPID lineage)
# → halt journal event + exit 78. Two masters must never meet a flip.
# Call sites: PREFLIGHT via supervision_preflight (P1), plus the P2
# stop-site re-check in stop_via_stop_script (still pre-stop-mutation —
# the state may drift between preflight and the stop).
# MainPID lineage is tested as cgroup containment within the unit's
# ControlGroup. The unit's KillMode=mixed (scripts/systemd/ensemble-
# daemon.service:85) TERMs only the launcher (the cgroup root) — cgroup
# siblings/nested children outlive a stop until SIGKILL escalation.
# That is precisely why a cgroup-containment check is the canonical
# lineage signal here: PPID-walking breaks on double-forked daemons
# (the launcher's child renames itself to the daemon pid), so we
# consult /proc/<pid>/cgroup instead.
# Containment test (B4, self-service-promote Wave 2, 2026-11-19): the
# match is ANCHORED — the holder's cgroup must BE the unit's cgroup
# (`"$cg"`) or a real DESCENDANT (`"$cg"/*`). The pre-B4 form was the
# UNANCHORED substring `*"$cg"*`, which admitted any cgroup path
# merely CONTAINING the unit's path string (a sibling scope like
# `/system.slice/ensemble-x.service-extra`) — a guard-weakening
# over-include. The anchored form tightens the fault arm ONLY (a
# genuine descendant still passes; a string-cousin now faults).
# Linux+systemd Check A/B unchanged in shape; Fix #2-i (ENSEMBLE-32
# repair lane, incident 2026-11-14 §3.1) adds the MISSING LAUNCHD ARM
# for Darwin launchd-declared hosts (SUPERVISION_MODE=launchd): Check
# B grammar on the launchd pid lineage (_df_launchd_check_b) — the old
# guard returned 0 immediately on Darwin, so a launchd host ran NO
# dual-fight detection at all (the gap the v0.30.3 dual-supervisor
# commit lived through). Other shapes still return 0.
# _df_systemctl <outfile> <args...> — a BOUNDED supervision systemctl probe
# (T2.7/phase-2 D2). The dual-fight check's four `systemctl is-active` /
# `systemctl show` calls were the last unbounded probes on the preflight
# path (unlike the 5 sibling sites that already ride `_run_bounded`).
# A wedged D-Bus must surface as a LOUD failure (journal row + exit 78),
# NEVER as a silent "the unit is not active" — reading a wedged bus as
# "unit down" is exactly the misread the incident family forbids.
#
# STATEMENT-FORM CONTRACT: this helper is called as a statement (never
# inside `$( )`) — on a budget breach `_budget_breach` exits THE SCRIPT.
# Output lands in <outfile> (the caller `reads` it back); rc is the
# probe's own rc (is-active exits 3 when inactive — preserved).
#
# KIND SCOPE (W3, code council 2026-10-30): the breach handler is
# promote-path-only. `supervision_preflight` is reached from promote.sh
# (`promote:*`, holds the lock), rollback.sh (`rollback:*`, holds the lock)
# and restart.sh (`restart:*`, adopts a lock), plus the P2 stop-site
# re-check inside stop_via_stop_script. R1 is binding: "non-promote paths
# (status / rollback / restart / adopt-unit / stop) => WARN + bounded +
# continue. NO fail-fast on recovery paths." A wedged bus during a restart
# or a rollback must not fail-fast a RECOVERY-class script (and on a live
# install its halt row would be fence-refused anyway — fatal AND
# unrecorded, the inverse of this lane's promise). So:
#   * `_PIPELINE_PHASE` unset            => treat as the PROMOTE path
#     (fail-closed: today's behaviour for any un-phased caller, and every
#     shipped recovery script sets its phase BEFORE its supervision call);
#   * `_PIPELINE_PHASE=promote:*`        => `_budget_breach` (row + WARN +
#     lock release + exit 78) — unchanged;
#   * any other explicit phase           => WARN + continue with the probe's
#     own rc, 124 mapped to "unknown / cannot classify" (the dual-fight
#     check then degrades instead of claiming a FAULT it cannot see).
# Both arms are pinned by tests/packs/preflight_watchdog_test.sh (D10/D10b
# promote-breach, D12 recovery-continue).
_df_systemctl() {
    local __out="$1"; shift
    local __t0 __rc __phase
    __t0="$(_now_epoch)"
    _run_bounded "$SYSTEMCTL_PROBE_BUDGET_S" -- "$SYSTEMCTL_BIN" "$@" > "$__out" 2>/dev/null
    __rc=$?
    if [ "$__rc" -eq 124 ]; then
        __phase="${_PIPELINE_PHASE:-}"
        case "$__phase" in
            ''|promote:*)
                rm -f "$__out" 2>/dev/null || true
                : > "$__out" 2>/dev/null || true
                _budget_breach SYSTEMCTL_PROBE_BUDGET_S "systemctl-$1" "supervision-dualfight" "$(( $(_now_epoch) - __t0 ))"
                ;;
            *)
                # R1 recovery class: bounded probe + loud WARN + continue.
                rm -f "$__out" 2>/dev/null || true
                : > "$__out" 2>/dev/null || true
                _warn "supervision probe '$1' exceeded SYSTEMCTL_PROBE_BUDGET_S=${SYSTEMCTL_PROBE_BUDGET_S}s at phase ${__phase} — continuing WITHOUT a classification (R1: no fail-fast on a recovery path; elapsed=$(( $(_now_epoch) - __t0 ))s)"
                return 124
                ;;
        esac
    fi
    return "$__rc"
}

# _df_launchd_check_b — the launchd Check B (Fix #2-i): every LISTEN
# holder of $PORT must sit in the launchd job's pid lineage. Mirrors the
# systemd Check B verdict shape (fault → halt journal event → exit 78)
# with the launchd lineage anchor (_supervision_launchd_job_pid +
# _supervision_pid_in_launchd_lineage). Degraded arms follow the house
# no-missing-tool-fault rule: PORT unset / lsof absent → skip (a
# report-only port lookup cannot fault on a missing tool — systemd Check
# B precedent); job pid unresolvable via a TOOL failure → skip (a wedged
# launchctl is not evidence); a registered job with NO live pid while
# the port IS held → FAULT (the v0.30.3 dual-supervisor shape — the job
# is armed but its tree is crash-looping while something else serves).
# STATEMENT-FORM CONTRACT (unchanged): call directly, never in $( ).
_df_launchd_check_b() {
    local unit="${SUPERVISION_UNIT:-}" hpid lpid fault="" holders
    holders="$(_supervision_port_holders)" || return 0   # rc1 no-listener / rc2 unprobeable — nothing to fight over
    lpid="$(_supervision_launchd_job_pid "$unit")"
    case "$?" in
        2) return 0 ;;   # launchctl tool/budget failure — never evidence
    esac
    if [ -z "$lpid" ]; then
        # Registered job, no live pid, port held → two masters (or a
        # serving orphan). Fault, not skip.
        fault="launchd-job-pidless-while-port-held"
        journal_history_append halt "supervision DUAL_FIGHT ($fault): launchd job $unit is registered with NO live pid while port ${PORT:-?} is held by:${holders} — refusing PRE-TXN (two masters must never meet a flip; halt-for-human, resolve the supervision split before any pipeline mutation)" >/dev/null 2>&1 \
            || _warn "DUAL_FIGHT halt journal append FAILED (best-effort) — proceeding to exit 78"
        exit 78
    fi
    for hpid in $holders; do
        [ -n "$hpid" ] || continue
        if ! _supervision_pid_in_launchd_lineage "$hpid" "$lpid"; then
            _warn "supervision DUAL_FIGHT: port $PORT holder $hpid is OUTSIDE launchd job $unit lineage (job pid $lpid) — two masters"
            fault="port-holder-outside-launchd-lineage"
            break
        fi
    done
    if [ -n "$fault" ]; then
        journal_history_append halt "supervision DUAL_FIGHT ($fault): launchd job $unit is armed while the port-holder sits OUTSIDE its pid lineage — refusing PRE-TXN (two masters must never meet a flip; halt-for-human, resolve the supervision split before any pipeline mutation)" >/dev/null 2>&1 \
            || _warn "DUAL_FIGHT halt journal append FAILED (best-effort) — proceeding to exit 78"
        exit 78
    fi
    return 0
}

supervision_dualfight_check() {
    # ── Fix #2-i (ENSEMBLE-32 repair lane; incident 2026-11-14 §3.1) ──
    # LAUNCHD ARM (was the gap: the Linux guard below returned 0
    # immediately on Darwin, so a launchd-declared host ran NO
    # dual-fight detection at all). Check B grammar on the launchd
    # lineage: the port-holder must be the launchd job's process —
    # a holder outside the job's pid lineage while the job is
    # armed = two masters meeting a flip (the v0.30.3 shape: a nohup
    # daemon serving while the launchd-adopted tree crash-looped).
    if [ "${SUPERVISION_MODE:-}" = "launchd" ] \
       && [ -n "${SUPERVISION_UNIT:-}" ] && _supervision_host_allows_launchd; then
        _df_launchd_check_b
        return $?
    fi
    [ "${SUPERVISION_MODE:-}" = "unit" ] || return 0
    [ -n "${SUPERVISION_UNIT:-}" ] || return 0
    [ "$(uname -s)" = "Linux" ] || return 0
    [ -d /run/systemd/system ] || return 0
    local unit="${SUPERVISION_UNIT:-}" is_active restart main_pid cg pid pcg hpid fault=""
    local df_out
    df_out="$(mktemp /tmp/.ensemble-df.XXXXXX)" || return 0
    _df_systemctl "$df_out" is-active "$unit"
    is_active="$(cat "$df_out" 2>/dev/null)"
    _df_systemctl "$df_out" show "$unit" -p Restart --value
    restart="$(cat "$df_out" 2>/dev/null)"
    if [ "$is_active" != "active" ] \
       && [ "$restart" != "always" ] && [ "$restart" != "on-failure" ]; then
        rm -f "$df_out" 2>/dev/null
        return 0  # unit dormant and unarmed — no fight possible
    fi
    _df_systemctl "$df_out" show "$unit" -p MainPID --value
    main_pid="$(cat "$df_out" 2>/dev/null)"
    _df_systemctl "$df_out" show "$unit" -p ControlGroup --value
    cg="$(cat "$df_out" 2>/dev/null)"
    rm -f "$df_out" 2>/dev/null
    if [ -z "$cg" ] || [ "$cg" = "/" ]; then
        _supervision_warn_once "unit $unit active/armed but ControlGroup unobservable — skipping DUAL_FIGHT pid checks (diagnostic only)"
        return 0
    fi
    # Check A: every owned pid must sit inside the unit's cgroup. The
    # subshell pipe captures the fault marker (the while-loop runs in a
    # subshell; the temp file carries the verdict out).
    local df_tmp
    df_tmp="$(mktemp /tmp/.ensemble-df.XXXXXX)" || return 0
    _supervision_owned_pids | while read -r pid; do
        [ -n "$pid" ] || continue
        pcg=""
        if [ -r "/proc/$pid/cgroup" ]; then
            pcg="$(tail -n 1 "/proc/$pid/cgroup" 2>/dev/null)"
            pcg="${pcg##*:}"
        fi
        case "$pcg" in
            # B4 anchored lineage match (see the header): the owned pid's
            # cgroup must BE the unit's cgroup or a real descendant — the
            # old unanchored `*"$cg"*` admitted any path merely containing
            # the unit's string.
            "$cg"|"$cg"/*) ;;
            *)
                _warn "supervision DUAL_FIGHT: owned pid $pid cgroup '${pcg:-<unreadable>}' is OUTSIDE unit $unit cgroup $cg — two masters"
                echo "__DUALFIGHT_FAULT__"
                ;;
        esac
    done > "$df_tmp" 2>/dev/null
    if grep -q '__DUALFIGHT_FAULT__' "$df_tmp" 2>/dev/null; then
        fault="owned-pids-outside-unit-cgroup"
    fi
    rm -f "$df_tmp" 2>/dev/null
    # Check B: the port-holder must be the unit's MainPID or inside its
    # cgroup. CENSUS SCOPE (B4, self-service-promote Wave 2, D1 root
    # cause (b), 2026-11-19): the LISTEN-scoped canonical shared reader
    # `_supervision_port_holders` — NEVER a bare `lsof -ti:$PORT` (the
    # old inline census also listed every pid holding an ESTABLISHED
    # client connection — an uptime monitor, a transient curl — and a
    # holder that vanished between census and cgroup read then read as
    # a FAULT: the 05:42:58Z false-refusal class; the reader's own
    # header at its definition documents why broad is wrong).
    # rc handling mirrors the launchd arm (_df_launchd_check_b)
    # verbatim: rc 1 = no listener ⇒ nothing to fight over; rc 2 =
    # census unprobeable ⇒ SKIP the check — a failing lsof reads as
    # PROBE-FAILED (rc 2), never as "nothing to fight over" (the old
    # `2>/dev/null` shape discarded the rc into the for word-split and
    # silently equated the two), and never as fault evidence
    # (no-missing-tool-fault rule, stop-ensemble.sh precedent).
    # ⚠ CENSUS-SCOPING RULE (design §2.2 — do not "unify" wrongly): the
    # census stays LISTEN-scoped, NOT inode-scoped — a true second
    # master holds a DIFFERENT inode on the same port and must stay
    # visible to the census (F8); inode identity belongs in an
    # admission predicate, never here.
    if [ -z "$fault" ]; then
        holders="$(_supervision_port_holders)"
        case "$?" in
            0) ;;
            *) holders="" ;;   # rc1 no-listener / rc2 unprobeable — Check B has nothing to say
        esac
        for hpid in $holders; do
            [ -n "$hpid" ] || continue
            [ "$hpid" = "$main_pid" ] && continue
            # Vanished-holder semantics (§1.3(b)): a holder that EXITED
            # between the census and this cgroup read (/proc/<pid> GONE —
            # the 05:42:58Z detail read cgroup='<unreadable>' for exactly
            # this race) is not evidence — the fight is over; skip it. A
            # LIVE holder whose cgroup is still unreadable stays
            # FAIL-CLOSED (the fault arm below) — we cannot prove it
            # inside the unit, and two masters must never meet a flip.
            if [ ! -e "/proc/$hpid" ]; then
                _warn "supervision DUAL_FIGHT: port $PORT holder $hpid vanished between census and cgroup read (exited transient — not evidence); skipping"
                continue
            fi
            pcg=""
            if [ -r "/proc/$hpid/cgroup" ]; then
                pcg="$(tail -n 1 "/proc/$hpid/cgroup" 2>/dev/null)"
                pcg="${pcg##*:}"
            fi
            # B4 anchored lineage match (see the header): the holder's
            # cgroup must BE the unit's cgroup or a real descendant —
            # the old unanchored `*"$cg"*` admitted any path merely
            # containing the unit's string.
            case "$pcg" in
                "$cg"|"$cg"/*) ;;
                *)
                    _warn "supervision DUAL_FIGHT: port $PORT holder $hpid is outside unit $unit lineage (MainPID=${main_pid:-?} cgroup='${pcg:-<unreadable>}') — two masters"
                    fault="port-holder-outside-unit-lineage"
                    ;;
            esac
        done
    fi
    if [ -n "$fault" ]; then
        journal_history_append halt "supervision DUAL_FIGHT ($fault): unit $unit is active/auto-restart while owned pids or the port-holder sit OUTSIDE it — refusing PRE-TXN (two masters must never meet a flip; halt-for-human, resolve the supervision split before any pipeline mutation)" >/dev/null 2>&1 \
            || _warn "DUAL_FIGHT halt journal append FAILED (best-effort) — proceeding to exit 78"
        # Direct invocation only — supervision_dualfight_check must be called
        # directly (statement form), NEVER wrapped in `$( ... )` or any other
        # subshell. The exit 78 below must kill the calling pipeline script;
        # a subshell swallows the exit and the refusal silently degrades to
        # the caller's rc, which is exactly the r-f82e surprise class the
        # DUAL_FIGHT halt exists to prevent.
        exit 78
    fi
    return 0
}

# supervision_preflight — one call per action script's PREFLIGHT: classify
# (emits the machine line), refuse 78 on explicit-unit-unresolved, then the
# §6 DUAL_FIGHT fault check. PRE-TXN by call-site contract (promote/rollback
# call it before journal_open_txn; restart calls it after lock adoption,
# before its stop phase — the restart txn was armed by the tool earlier and
# the executor is its completion, not its opener).
# A1 (2026-09-29): also emits the NAMED declared×verified outcome as ONE
# SEPARATE additive machine line ENSEMBLE_SUPERVISION_OUTCOME=<outcome>
# (conforming|degraded|fault) — a DIFFERENT variable name on its own line;
# the ENSEMBLE_SUPERVISION_RESULT=<state>[:<unit>] grammar is FROZEN (P2
# consumes it — never extend that line). Emitted AFTER the DUAL_FIGHT
# check so a fault run (exit 78 inside the check) never prints a stale
# 'conforming' — DUAL_FIGHT's named surface is the halt event itself.
# The reason half of supervision_map_outcome is human prose and is
# deliberately NOT machine-surfaced.
# ── D3-E4 (self-service-promote Wave 5, §2.4): the executor cgroup
# self-check — PREVENTION for the 2026-11-18 double-outage shape.
#
# THE SHAPE IT CONVERTS TO A NO-OP: an agent (or a mis-spawned executor)
# ran a ceremony script from INSIDE the supervision unit's cgroup —
# `systemctl stop` on the unit killed the CALLER with its own cgroup
# before the start half ran (`Restart=on-failure` skipped after an
# explicit stop) ⇒ down 67 min, then 4h32m. The sanctioned executor runs
# in its OWN transient unit (a system.slice sibling — outside the
# supervision cgroup by construction); this check is the fail-closed
# backstop for every OTHER invocation path.
#
# REFUSE-ONLY (deliberate design choice, §2.4 E4's "or" — the re-exec
# relocation variant is NOT implemented): refusing converts the outage
# shape into a clean exit-78 with zero relocation risk (a re-exec under
# systemd-run with the same lib.sh would need provenance-stamp
# preservation + a recursion guard; the refuse path needs neither and
# loses nothing — the arm was never a legitimate ceremony).
#
# Probe-failure posture (the DUAL_FIGHT ControlGroup-unobservable
# precedent, lib.sh's own diagnostic-only skip): a self-cgroup read
# failure or an unprobeable unit ControlGroup is NOT evidence of being
# inside — warn once and CONTINUE. Only a POSITIVE containment match
# (self path == unit path, or self path under "<unit-path>/") refuses.
supervision_executor_cgroup_selfcheck() {
    # Not applicable without a resolved supervision unit (script mode /
    # launchd hosts) — the check is systemd-specific.
    [ -n "${SUPERVISION_UNIT:-}" ] || return 0
    local self_line self_cg unit_cg
    if [ ! -r /proc/self/cgroup ]; then
        _supervision_warn_once "cgroup self-check: /proc/self/cgroup unreadable — skipping (diagnostic only; not evidence of containment)"
        return 0
    fi
    # Same idiom as _supervision_pid_cgroup_leaf: tail -n 1 (the unified
    # hierarchy's `0::/path` line), strip the controller prefix.
    self_line="$(tail -n 1 /proc/self/cgroup 2>/dev/null)" || self_line=""
    self_cg="${self_line##*:}"
    case "$self_cg" in
        /*) ;;
        *)
            _supervision_warn_once "cgroup self-check: self cgroup unparseable ('$self_line') — skipping (diagnostic only)"
            return 0
            ;;
    esac
    unit_cg="$(_run_bounded "$SYSTEMCTL_PROBE_BUDGET_S" -- "$SYSTEMCTL_BIN" show "$SUPERVISION_UNIT" -p ControlGroup --value 2>/dev/null)" || unit_cg=""
    case "$unit_cg" in
        /*) ;;
        *)
            _supervision_warn_once "cgroup self-check: unit '$SUPERVISION_UNIT' ControlGroup unobservable — skipping the containment match (diagnostic only; the DUAL_FIGHT precedent)"
            return 0
            ;;
    esac
    if [ "$self_cg" = "$unit_cg" ] || case "$self_cg" in "$unit_cg"/*) true ;; *) false ;; esac; then
        _refuse executor-inside-supervision-cgroup "preflight refused: this ceremony process runs INSIDE the supervision unit's cgroup (self=${self_cg} unit=${SUPERVISION_UNIT} ControlGroup=${unit_cg}) — a stop of the unit would kill the caller mid-ceremony (the 2026-11-18 double-outage shape: stop half ran, caller died with its cgroup, start half never ran). Run the ceremony from the sanctioned executor transient unit (systemd-run sibling), never from a process inside the supervised unit's cgroup"
    fi
    _log "cgroup self-check: outside the supervision cgroup (self=${self_cg} unit_cgroup=${unit_cg})"
    return 0
}

supervision_preflight() {
    if ! supervision_classify; then
        case "${SUPERVISION_REFUSE_REASON:-}" in
            unit-incapable-host)
                # M3 (review cycle 1, leader-ruled FAIL-CLOSED): explicit unit
                # on a unit-INCAPABLE host — same exit-78 preflight refusal
                # semantics as supervision-unit-unresolved, its own greppable
                # reason token. NEVER loud-degrade + nohup at rc 0.
                _refuse supervision-unit-incapable-host "preflight refused: ENSEMBLE_SUPERVISION=unit is explicit but this host cannot honor unit supervision (non-Linux / no systemd / systemctl unresolvable) — never silent-degrade to the nohup lineage (the r-f82e surprise class); run under systemd or declare ENSEMBLE_SUPERVISION=script"
                ;;
            launchd-incapable-host)
                # D2a: declared launchd on a non-Darwin host — the unit-arm
                # mirror. Its own greppable token; never loud-degrade.
                _refuse supervision-launchd-incapable-host "preflight refused: ENSEMBLE_SUPERVISION=launchd is explicit but this host cannot honor launchd supervision (non-Darwin / no resolvable launchctl) — never silent-degrade to the nohup lineage; run under launchd or declare the host's real supervisor"
                ;;
            launchd-not-verified)
                # D2a: Darwin AUTO/declared-launchd probe found zero/multiple/
                # unresolvable jobs — refuse (never fall through to nohup).
                _refuse supervision-not-verified "preflight refused: launchd supervision could not be verified on Darwin — no single launchd job whose argv points at $INSTALL_DIR/launcher.sh (zero/multiple matches or unreadable gui domain). Install/repair the LaunchAgent (make plist-install) or fix ENSEMBLE_LAUNCHD_LABEL; never fall through to the nohup lineage (the 2026-11-11 incident mechanism)"
                ;;
            unsupervised-declaration-required)
                # D2a: script/opt-out on Darwin is not an acknowledgement.
                _refuse supervision-unsupervised-declaration-required "preflight refused: ENSEMBLE_SUPERVISION=script (or an opt-out/0) on Darwin is not a legitimate supervision acknowledgement — an unsupervised Darwin lane is not supported here; declare 'launchd' (or 'auto'), or unset ENSEMBLE_SUPERVISION"
                ;;
            declaration-invalid)
                # D2a: garbage declared value on Darwin — fail-closed.
                _refuse supervision-declaration-invalid "preflight refused: ENSEMBLE_SUPERVISION carries an unrecognized value on Darwin — fail-closed (never silent-degrade to script); unset it or declare 'auto'/'launchd'"
                ;;
            *)
                _refuse supervision-unit-unresolved "preflight refused: ENSEMBLE_SUPERVISION=unit is explicit but no unit name is resolvable (env ENSEMBLE_RESTART_UNIT > cgroup-derived > INSTALL_DIR/.env all empty) — never silent-degrade (P1 §2); set ENSEMBLE_RESTART_UNIT or run under the unit"
                ;;
        esac
    fi
    # D3-E4 (Wave 5 §2.4): the cgroup self-check — BEFORE DUAL_FIGHT and
    # before any mutation: a process inside the supervision cgroup must
    # never run the stop half of a ceremony. Refuse-only, fail-closed,
    # positive-match-only (probe failures are diagnostic skips).
    supervision_executor_cgroup_selfcheck
    supervision_dualfight_check
    # D2a: the declared×verified OUTCOME map stays host-blind (its launchd
    # cells are D2b) — skip the OUTCOME line for a launchd host so a verified
    # flip never emits a false `fault` from the unknown-declared default.
    if [ "${SUPERVISION_MODE:-}" != "launchd" ]; then
        SUPERVISION_OUTCOME="$(supervision_map_outcome "${SUPERVISION_MODE:-}" "${SUPERVISION_STATE:-}" "${SUPERVISION_UNIT:-}")"
        printf 'ENSEMBLE_SUPERVISION_OUTCOME=%s\n' "${SUPERVISION_OUTCOME%%|*}"
    fi
}

# journal_mark_supervision — stamp supervision=<state> / unit=<name|null>
# ADDITIVELY on the open in_flight txn (D4 splice discipline: additive-only
# textual splice; existing stamp lines and journal whitespace byte-preserved
# — journal JSON whitespace differs between launcher and lib.sh by design,
# assert semantically, never raw-spacing). Mirrors journal_mark_f2_verified.
# Reads the globals set by the last supervision_classify in this process.
# A1 (2026-09-29): also stamps outcome=<conforming|degraded|fault> — the
# NAMED declared×verified cell from supervision_map_outcome (additive field
# only; existing readers parse named fields and ignore extras). Computed
# from the resolved-mode globals — RESOLVED-MODE EQUIVALENCE (section
# comment) makes that cell-for-cell identical to the declared-mode cell.
journal_mark_supervision() {
    local json inf new_inf unit_json outcome
    json="$(journal_read)" || return 1
    inf="$(_json_sub "$json" in_flight)"
    case "$inf" in
        '{'*) ;;
        ''|null)
            _warn "journal_mark_supervision: no in_flight txn (in_flight null/absent) — refusing to stamp (journal untouched)"
            return 1
            ;;
        *)
            _warn "journal_mark_supervision: in_flight read is null/malformed (not a JSON object: '${inf:0:40}') — refusing to stamp (journal untouched)"
            return 1
            ;;
    esac
    if [ -n "${SUPERVISION_UNIT:-}" ]; then
        unit_json="\"$(_json_escape "$SUPERVISION_UNIT")\""
    else
        unit_json="null"
    fi
    new_inf="${inf%\}}"
    new_inf="${new_inf},\"supervision\":\"$(_json_escape "${SUPERVISION_STATE:-unknown}")\",\"unit\":$unit_json"
    # D2a: the launchd outcome cells are D2b; the map is host-blind so a
    # verified launchd would fall to its unknown-declared `fault` default.
    # Stamp supervision+unit but OMIT outcome for launchd (no false fault).
    if [ "${SUPERVISION_MODE:-}" != "launchd" ]; then
        outcome="$(supervision_map_outcome "${SUPERVISION_MODE:-}" "${SUPERVISION_STATE:-unknown}" "${SUPERVISION_UNIT:-}")"
        outcome="${outcome%%|*}"
        new_inf="${new_inf},\"outcome\":\"$outcome\""
    fi
    new_inf="${new_inf}}"
    journal_update "in_flight" "$new_inf"
}

# _gate_serving_attribution <what> — the Fix #2 ATTRIBUTION CONJUNCT
# (ENSEMBLE-32 repair lane Family 2; incident
# docs/incidents/2026-11-14-macos-launchd-handback-halt.md §3.1/§5.1).
# Rides AFTER a green curl in every gate (gate_livez / gate_readyz /
# gate_version — gate_soak inherits it through its per-tick
# gate_version) and in _handback_serving_probe: the gate answer is
# only trusted when the process HOLDING the port is THIS install's
# supervised daemon. Live fact driving this: on ensemble-vm the daemon
# listens 127.0.0.1:9798 while :9797 is held by `kubectl port-forward`
# + an uptime monitor + a code-server child — a port-answer check
# validated the WRONG process (the v0.30.3 promote "committed" on
# /livez answers from a nohup daemon while the launchd-adopted tree
# crash-looped on EADDRINUSE for 9h).
#
# TWO layers, both fail-closed:
#   1. OWNERSHIP — every LISTEN holder of $PORT must be a member of
#      the supervision-owned pid set (_supervision_owned_pids — the
#      INSTALL_DIR-anchored shape ladder). Catches foreign holders
#      (kubectl/monitor/code-server). An EMPTY owned set against a
#      live holder is the same mismatch (nothing anchored is serving).
#   2. SUPERVISOR LINEAGE — when the pre-stop classification resolved
#      a supervisor (SUPERVISION_MODE=launchd on Darwin, =unit on
#      Linux), every holder must sit in THAT supervisor's lineage
#      (launchd job pid / systemd MainPID ∧ unit cgroup containment —
#      the dualfight Check B grammar). Catches the v0.30.3 shape:
#      the nohup daemon IS owned-shaped (same install) but is NOT the
#      launchd job's process. Script/nohup mode has no supervisor to
#      attribute to — the ownership layer alone governs there.
#
# Refusal vs degraded (no silent fallbacks):
#   * holder present ∧ not owned / outside lineage / no listener
#     behind a green answer  → REFUSE: rc 1 + loud _warn naming the
#     pids (the caller turns the gate red — never warn-and-pass).
#   * unprobeable (PORT unset, lsof absent, or lsof itself errored;
#     supervisor probe TOOL failure — never evidence) → rc 0 with a
#     ONE-TIME loud warn naming the skipped layer (dualfight/
#     _supervision_unit_serving no-missing-tool-fault precedent).
#
# Sets GATE_ATTRIBUTION_DETAIL ("<reason>" on refusal, "" on pass) so
# callers that thread a failure reason (promote 8b, restart GATE_FAIL,
# rollback REGATE_FAIL, status verify) can carry WHY. Called as a
# statement; reads PORT/INSTALL_DIR/SUPERVISION_* globals.
GATE_ATTRIBUTION_DETAIL=""
_GATE_ATTR_WARNED=""
# _gate_attr_unprobeable <what> <why> — the ONE-TIME loud degraded note
# (never silent, never spammy — the budgeted probe loops would repeat it
# every iteration). NOT a pass evidence: it marks the layer as skipped.
_gate_attr_unprobeable() {
    [ -n "$_GATE_ATTR_WARNED" ] && return 0
    _GATE_ATTR_WARNED=1
    _warn "attribution [$1]: skipped (${2}) — the serving-process conjunct cannot be evaluated on this host/shape (Fix #2; gates do NOT refuse on a missing probe tool, but this is NOT a pass evidence)"
}
# ── rc=2 consumer contract — the helper family (T7d-v2, R2 §2.1-2.2) ────────
# Canonical carrier: gate_defer_reason — the SINGLE transient-carrier name
# (the per-surface aliases are STRUCK). Read/written exclusively via
# ${gate_defer_reason:-} forms => set -u-safe by construction. _gate_consume
# is the SOLE WRITER (statement position only — never inside $( )); every
# guard/success/logbook branch reads _gate_outcome (the SOLE READER), never a
# carrier variable — that is what makes an omitted-site read structurally
# impossible on the read side (T7g census, I-B14).
#
# Wording classes are CLOSED vocabularies: definitive labels
# (/livez unreachable >Ns, version verify mismatch, soak failure, ...) go ONLY
# into the surface's failure carrier (byte-preserved); transient labels go
# ONLY into gate_defer_reason + the gate-defer: detail. Never cross-
# contaminated (D-P1-4 / T7c).
gate_defer_reason=""
# _gate_consume <surface> <rc> <label> — consume a one-shot gate call's rc.
#   rc=0: no-op (echo ok).
#   rc=1: set the surface's FAILURE CARRIER with today's byte-exact string;
#         return 1 so the caller's existing branch keeps its shape.
#   rc=2: set gate_defer_reason (transient wording — never a definitive
#         label), failure carrier UNTOUCHED, write ONE gate_defer row per run
#         (fire-once latch = the carrier itself), _warn per tick, return 2.
#   unknown rc: contract violation -> treated as 2 + a warn row.
# Post-defer chain semantics (§5.4a-1, ruling D-P1-14, NORMATIVE precedence
# fail > defer > ok):
#   (1) DEFINITIVE-FAILURE PRECEDENCE — a defer never downgrades a definitive
#       failure: _gate_consume refuses to mint a defer carrier while the
#       surface's failure carrier is already set (the fail wins; the defer
#       tick is recorded on stderr only).
#   (2) LATER-GREEN SUPERSESSION — an earlier defer does not veto a later
#       attributed ^ version-correct green at the cycle's terminal read; the
#       consuming reads below therefore clear the carrier on a definitive ok
#       (consume clears — rule 3).
#   (3) CONSUME CLEARS — the carrier never leaks across stages/cycles: every
#       consumption clears it after the read.
_gate_consume() {
    local __surface="$1" __rc="$2" __label="$3"
    case "$__rc" in
        0)
            # A definitive ok supersedes an earlier defer in this cycle
            # (rule 2) and the carrier is cleared after the read (rule 3).
            gate_defer_reason=""
            printf '%s' "ok"
            return 0
            ;;
        1)
            # Definitive failure: the failure carrier wins (rule 1) and any
            # stale defer token from an earlier gate in this cycle is
            # irrelevant to the fail path — clear it so the halt/rollback
            # branch never renders two verdicts.
            gate_defer_reason=""
            printf '%s' "fail"
            return 1
            ;;
        2)
            if [ -n "${gate_fail_reason:-}${GATE_FAIL:-}${REGATE_FAIL:-}" ]; then
                # rule 1: a definitive failure already recorded in this cycle
                # is NEVER downgraded by a defer.
                _warn "gate-consume [$__surface]: transient at '$__label' IGNORED — a definitive failure is already recorded this cycle (fail > defer)"
                printf '%s' "defer-suppressed"
                return 2
            fi
            if [ -n "${gate_defer_reason:-}" ]; then
                # fire-once: one halt row per run per surface (the latch IS the
                # carrier); still warn per tick.
                _warn "gate-consume [$__surface]: '$__label' attribution deferred again (transient) — $gate_defer_reason"
                printf '%s' "defer"
                return 2
            fi
            gate_defer_reason="$__label: attribution deferred (transient — budget expired)"
            _warn "gate-consume [$__surface]: '$__label' attribution DEFERRED (transient) — $gate_defer_reason"
            journal_history_append gate_defer "gate-defer: $__surface $__label — $gate_defer_reason (transient; NOT a failure verdict; no rollback, no quarantine)"                 || true
            printf '%s' "defer"
            return 2
            ;;
        *)
            # Contract violation (I-B13): an unknown rc is treated as 2.
            _warn "gate-contract violation: $__surface '$__label' returned rc=$__rc (expected 0|1|2) — treating as transient"
            gate_defer_reason="$__label: gate-contract violation (rc=$__rc treated as transient)"
            journal_history_append gate_defer "gate-defer: $__surface $__label — $gate_defer_reason (contract violation; treated as transient)"                 || true
            printf '%s' "defer"
            return 2
            ;;
    esac
}
# _gate_outcome <surface> — THE reader. Echoes ok | fail | defer from the
# surface's carriers; guards branch on THIS token, never on a carrier read.
_gate_outcome() {
    local __surface="$1"
    case "$__surface" in
        promote)
            if [ -n "${gate_fail_reason:-}" ]; then printf '%s' "fail"
            elif [ -n "${gate_defer_reason:-}" ]; then printf '%s' "defer"
            else printf '%s' "ok"; fi
            ;;
        restart)
            if [ -n "${GATE_FAIL:-}" ]; then printf '%s' "fail"
            elif [ -n "${gate_defer_reason:-}" ]; then printf '%s' "defer"
            else printf '%s' "ok"; fi
            ;;
        rollback)
            if [ -n "${REGATE_FAIL:-}" ]; then printf '%s' "fail"
            elif [ -n "${gate_defer_reason:-}" ]; then printf '%s' "defer"
            else printf '%s' "ok"; fi
            ;;
        *)
            _warn "gate-contract violation: _gate_outcome called with unknown surface '$__surface'"
            printf '%s' "fail"
            ;;
    esac
}
# _gate_defer_reset — CONSUME CLEARS (rule 3): stage/cycle boundaries reset
# the carrier so it never leaks.
_gate_defer_reset() { gate_defer_reason=""; }

# ── T7 — the pooled deferral seam (I-B8, leader D1/D3; NOT an exported knob,
# NOT registered — routed through _numeric_knob for its integer parse, then
# CLAMPED to [0,5], and honoured only under the dual-key test marker
# ENSEMBLE_TEST_GATE_DEFER=1; never exported; re-armed per CALL).
# The pool counts SECONDS (decrement by the slept duration — the honest
# sleep bound is <= _GATE_ATTR_DEFER_S + 1s per CALL at 2s granularity
# (5s => 3 sleeps = 6s), not iterations and not a per-shell total).
_GATE_ATTR_DEFER_S="$(_numeric_knob _GATE_ATTR_DEFER_S "${_GATE_ATTR_DEFER_S:-}" 5)"
case "$_GATE_ATTR_DEFER_S" in
    ''|*[!0-9]*) _GATE_ATTR_DEFER_S=5 ;;
esac
if [ "$_GATE_ATTR_DEFER_S" -gt 5 ]; then
    _GATE_ATTR_DEFER_S=5   # clamp: a leaked 300 becomes 5 (D3)
fi
if [ "$_GATE_ATTR_DEFER_S" -lt 0 ]; then
    _GATE_ATTR_DEFER_S=0
fi
if [ "${ENSEMBLE_TEST_GATE_DEFER:-0}" != "1" ]; then
    _GATE_ATTR_DEFER_S=5   # dual-key: a non-default value is honoured ONLY
                           # under the explicit test marker; absent => 5
fi
# Per-shell patience pool, initialised at source time from the clamped seam.
_GATE_ATTR_DEFER_REMAIN="$_GATE_ATTR_DEFER_S"
# Fire-once latch for the exhaustion marker (the _GATE_ATTR_WARNED precedent).
_GATE_ATTR_EXHAUST_LATCH=""

# _gate_attr_containment <holder> [<owned-spaced>] [<what>] — THE containment
# ladder (T5, promote-handback-halt Phase 1 §5.3). Classifies ONE port holder
# against the supervised tree. Three-valued, fail-closed:
#   0 = CONTAINED (an answered accept — evidence-only)
#   1 = positively NOT contained (today's refusal)
#   2 = unattributable-yet (the deciding tooling could not answer) -> deferral
#
# Rungs, in cost order (each rung is only reached if every earlier rung could
# not ANSWER):
#   R1  argv/cwd ownership       — holder in the owned set. Zero extra cost.
#   R2  systemd cgroup containment — unit mode + Linux only. Segment-aware:
#       pcg == cg || pcg PREFIX-matches "$cg"/  (canonical predicate; the raw
#       substring form is the over-accept this lane exists to kill — decisions
#       §2.1/D-P2-3. The legacy Layer 2 substring stays byte-identical and is
#       deliberately NOT this rung).
#   R3  ppid ancestry into the OWNED SET (POSIX ps — also the Darwin path).
#   R4  launchd job lineage (Darwin): a registered-but-pidless job while the
#       port is served is the v0.30.3 dual-supervisor shape -> refuse.
#
# COMPOSITION RULE (architect closure C-1, NORMATIVE): the FIRST rung that
# ANSWERS decides (0 accept / 1 refuse); only a 2 (cannot-answer) falls
# through. An answered 1 is DOWNGRADED to 2 when a STRONGER (earlier) rung was
# unanswerable — an incomplete determination is not positive foreignness, else
# a wedged systemctl would make the ladder STRICTER than today's check.
_gate_attr_containment() {
    local h="${1:-}" owned_space="${2:-}" what="${3:-gate}"
    local pcg cg mainpid rc lpid r4_rc __pre
    [ -n "$h" ] || return 2

    # ── R1 — argv/cwd ownership ──────────────────────────────────────
    # `owned_space` is THREADED EXPLICITLY (T6/A32): the legacy gate has a
    # leaked `owned_spaced` global, and the ladder must not become its second
    # reader. An EMPTY owned set answers NOTHING here (no member can match) —
    # R3 then answers the negative (P8/M3: `owned:<none>` still refuses).
    if [ -n "$owned_space" ]; then
        case " $owned_space " in
            *" $h "*) return 0 ;;
        esac
    fi

    # ── R2 — systemd cgroup containment (unit mode, Linux+systemd only) ──
    local r2_ran=0 r2_unanswered=0
    if [ "${SUPERVISION_MODE:-}" = "unit" ] && [ -n "${SUPERVISION_UNIT:-}" ] \
       && _supervision_host_allows_unit; then
        r2_ran=1
        # ONE ctx resolve per gate invocation (the cache is reset at gate entry
        # — T6); a second holder reuses the same MainPID/ControlGroup.
        if [ "${_GA_CTX_READY:-0}" != "1" ]; then
            if _gate_attr_unit_ctx "$what" >/dev/null 2>&1; then
                _GA_CTX_READY=1
            else
                _GA_CTX_READY=0
                _GA_CTX_REASON="${_GA_UNIT_UNPROBEABLE_WHY:-unknown}"
            fi
        fi
        if [ "$_GA_CTX_READY" != "1" ]; then
            r2_unanswered=1
        else
            mainpid="$_GA_UNIT_MAINPID"
            cg="$_GA_UNIT_CG"
            # the equality arm (unchanged by the amendment)
            if [ "$h" = "$mainpid" ]; then
                return 0
            fi
            # degenerate guard: "" or "/" => the tooling resolved no real
            # cgroup. This is THE false-halt class — abstain, never decide.
            if [ -z "$cg" ] || [ "$cg" = "/" ]; then
                r2_unanswered=1
            else
                pcg="$(_supervision_pid_cgroup_path "$h" 2>/dev/null)"
                if [ -z "$pcg" ]; then
                    r2_unanswered=1        # unreadable /proc read is no evidence
                elif [ "$pcg" = "$cg" ]; then
                    return 0               # (a) exact ControlGroup
                else
                    # (b) SEGMENT-BOUNDARY PREFIX test. The case pattern is
                    # anchored at the LEFT of $pcg: $cg literal, then '/', then
                    # anything. A cgroup name containing glob metacharacters
                    # would be mis-read as a PATTERN, so the prefix form is
                    # pre-checked with a LITERAL string comparison instead of
                    # relying on the glob (approver iter-001 note 9).
                    __pre=0
                    case "$pcg" in "$cg"/*) __pre=1 ;; esac
                    # The case pattern treats a cgroup name's glob
                    # metacharacters as PATTERN syntax, so it can mis-match in
                    # either direction; the quoted parameter expansion below is
                    # the AUTHORITATIVE literal prefix test (approver iter-001
                    # note 9). Accept if EITHER form says prefix.
                    if [ "$__pre" = "1" ] || [ "${pcg#"$cg"/}" != "$pcg" ]; then
                        return 0
                    fi
                    # positively NOT contained. R2 is the strongest rung, so an
                    # answered 1 here is final (the C-1 downgrade applies to the
                    # WEAKER R3 negative when R2 was unanswerable — below).
                    return 1
                fi
            fi
        fi
    fi

    # ── R3 — ppid ancestry into the owned set (portable / Darwin) ─────
    rc=0
    _supervision_pid_ancestor_of "$h" "$owned_space" || rc=$?
    case "$rc" in
        0) return 0 ;;
        1) if [ "$r2_unanswered" = "1" ]; then
               : # DOWNGRADE (C-1): a stronger rung could not answer, so this
                 # negative is not positive foreignness — fall through.
           else
               return 1
           fi
           ;;
        *) : ;;   # ps unavailable — cannot answer, fall through to R4
    esac

    # ── R4 — launchd job lineage (Darwin; the dual-supervisor refusal) ──
    if [ "${SUPERVISION_MODE:-}" = "launchd" ] && [ -n "${SUPERVISION_UNIT:-}" ] \
       && _supervision_host_allows_launchd; then
        lpid="$(_supervision_launchd_job_pid "$SUPERVISION_UNIT")"
        r4_rc=$?
        case "$r4_rc" in
            2) : ;;   # probe failure — no evidence, keep abstaining
            *)
                if [ -z "$lpid" ]; then
                    # registered-but-pidless job while a holder serves: refuse
                    # (the v0.30.3 shape; M7 pin preserved)
                    return 1
                fi
                if _supervision_pid_in_launchd_lineage "$h" "$lpid"; then
                    return 0
                fi
                return 1
                ;;
        esac
    fi

    # ── Exhausted ────────────────────────────────────────────────────
    return 2
}

_gate_serving_attribution() {
    local what="${1:-gate}" holders h owned owned_spaced nonmatch="" lpid lrc hrc
    local ladder_accept ladder_refuse ladder_unattrib l2_holders __lrc __h
    GATE_ATTRIBUTION_DETAIL=""
    # Per-invocation ctx cache: the ladder resolves the unit MainPID /
    # ControlGroup at most ONCE per gate call (T4/T6); resetting here keeps it
    # per-invocation, so a $( ) fork and a statement call never share state.
    _GA_CTX_READY=0
    _GA_CTX_REASON=""
    # owned_spaced is a PROPER LOCAL now (A32): it was a leaked global, and
    # the ladder must not become its second reader — it is threaded into
    # _gate_attr_containment explicitly instead.
    owned_spaced=""
    holders="$(_supervision_port_holders)"
    hrc=$?
    case "$hrc" in
        2) GATE_ATTRIBUTION_DETAIL=""; _gate_attr_unprobeable "$what" "no lsof / PORT unset / lsof errored"; return 0 ;;
    esac
    if [ -z "$holders" ]; then
        # curl JUST answered green on :$PORT — a green answer with no
        # listener is an unattributable answer (the answerer vanished
        # mid-probe, or the census cannot see it). Fail-closed: the
        # incident class is exactly "trusting an unattributable answer".
        GATE_ATTRIBUTION_DETAIL="port :${PORT} answered green but lsof finds NO listener — the answer is unattributable"
        _warn "attribution [$what]: REFUSED — $GATE_ATTRIBUTION_DETAIL (incident 2026-11-14 §3.1: the gate must verify WHO answers)"
        return 1
    fi
    # ── Layer 1: ownership ────────────────────────────────────────────
    owned="$(_supervision_owned_pids 2>/dev/null)"
    # _supervision_owned_pids emits a NEWLINE-joined list (one printf per
    # pid); the membership case below matches SPACE-delimited tokens, so
    # normalize ONCE before the loop. F1 (ENSEMBLE-32 repair lane council):
    # on every supervised install the owned set has >=2 pids (the launcher
    # run_loop holds the daemon as a live child, launcher.sh:3838; the
    # launchd job argv anchors the launcher) — matching raw newlines
    # false-refused exactly that shape (holder=daemon pid, owned=<launcher
    # NL daemon> => REFUSED), the F1 empiric reproduced before this fix.
    owned_spaced="$(printf '%s' "$owned" | tr '\n' ' ')"
    for h in $holders; do
        [ -n "$h" ] || continue
        case " $owned_spaced " in
            *" $h "*) ;;
            *) nonmatch="$nonmatch $h" ;;
        esac
    done
    if [ -n "$nonmatch" ]; then
        # ── Layer 1b: THE CONTAINMENT LADDER (T6, B-primary) ──────────────
        # Layer 1 (argv/cwd ownership) is the cheap accept and stays FIRST, so
        # the common all-owned path never reaches this block and issues ZERO
        # extra probes (I-B2 — byte-identical common case). The ladder is
        # attempted ONLY on shapes that refuse today: a holder that is
        # demonstrably inside the supervised tree (unit cgroup / owned
        # ancestor / launchd lineage) is a HEALTHY boot, not a foreign one —
        # the incident class (inherited-LISTEN-fd MCP children of the
        # supervised daemon) is exactly this shape.
        ladder_accept=""
        ladder_refuse=""
        ladder_unattrib=""
        for h in $nonmatch; do
            [ -n "$h" ] || continue
            __lrc=0
            _gate_attr_containment "$h" "$owned_spaced" "$what" || __lrc=$?
            case "$__lrc" in
                0) ladder_accept="$ladder_accept $h" ;;
                1) ladder_refuse="$ladder_refuse $h" ;;
                *) ladder_unattrib="$ladder_unattrib $h" ;;
            esac
        done
        # CONSUME CLEARS / DEFINITIVE-FAILURE PRECEDENCE (ruling D): a refusal
        # is never downgraded by a deferral classification, and no carrier
        # survives past this block (see _gate_consume in C3's helper family).
        if [ -n "$ladder_refuse" ]; then
            # owned_spaced (NOT $owned) in the detail: GATE_ATTRIBUTION_DETAIL
            # is threaded into promote's gate_fail_reason and from there into
            # the journal event detail — a single-line quoted string by
            # contract. The PREFIX is byte-identical to the pre-fix refusal
            # (the M1/M3/M6/M7 pins assert it); the ladder verdict is appended
            # as a SUFFIX only.
            GATE_ATTRIBUTION_DETAIL="port-holder(s):${nonmatch} NOT in the supervision-owned pid set (owned:${owned_spaced:-<none>}) — the :${PORT} answerer is not this install's daemon; containment ladder:${ladder_refuse}"
            _warn "attribution [$what]: REFUSED —$GATE_ATTRIBUTION_DETAIL (ensemble-vm §5.1: :9797 held by kubectl/monitor while the daemon serves :9798)"
            return 1
        fi
        if [ -n "$ladder_accept" ]; then
            # INFO on STDERR, never _log: every gate call runs inside a $( )
            # capture (LIVEZ_JSON="$(gate_livez)", newpid="$(_supervision_
            # unit_serving …)") where the captured payload must be the ONLY
            # stdout content (I-B2b; the :6210 budget-printf precedent).
            printf '%s[%s]: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "attribution [$what]: ACCEPTED — port-holder(s):${ladder_accept} contained in the supervised tree (cgroup containment / owned ancestry; the argv-anchored owned set does not cover them)" >&2 2>/dev/null
            # Layer 2 (refusal-only, and deliberately still raw-substring)
            # must NOT re-test holders the ladder already accepted — closure
            # C-2: re-testing risks a silent false veto in the mixed case where
            # the unit probes resolve but the holder's /proc/<pid>/cgroup is
            # unreadable.
            l2_holders="$(printf '%s\n' $holders | while IFS= read -r __h; do
                case " $ladder_accept " in *" $__h "*) ;; *) printf '%s\n' "$__h" ;; esac
            done)"
        fi
        # ── The DEFERRAL RUNG (T7, I-B8, leader D1) ────────────────────────
        # The residual "unattributable-yet" class: the deciding tooling could
        # not answer (systemctl wedged, cgroup unreadable, ps absent). The
        # ladder NEVER accepts on missing evidence — it DEFERS, bounded by a
        # per-call patience pool that counts SECONDS (the honest sleep bound
        # is <= _GATE_ATTR_DEFER_S + 1s per CALL at 2s granularity:
        # _GATE_ATTR_DEFER_S=5 => 3 sleeps => 6s; NOT a per-shell total).
        # Exhaustion => the TRANSIENT rc 2 (leader D1): refusal happens ONLY
        # at the caller's budget end through the unchanged floor-wins verdict.
        # A positively-foreign holder (ladder_refuse) never reaches this rung.
        if [ -n "$ladder_unattrib" ]; then
            _GATE_ATTR_DEFER_REMAIN="$_GATE_ATTR_DEFER_S"
            while [ "${_GATE_ATTR_DEFER_REMAIN:-0}" -gt 0 ]; do
                sleep 2
                _GATE_ATTR_DEFER_REMAIN=$(( _GATE_ATTR_DEFER_REMAIN - 2 ))
                [ "$_GATE_ATTR_DEFER_REMAIN" -lt 0 ] && _GATE_ATTR_DEFER_REMAIN=0
                # re-census + re-classify
                holders="$(_supervision_port_holders)"
                hrc=$?
                if [ "$hrc" = "2" ] || [ -z "$holders" ]; then
                    # still unattributable — keep waiting (or the pool ends)
                    continue
                fi
                owned="$(_supervision_owned_pids 2>/dev/null)"
                owned_spaced="$(printf '%s' "$owned" | tr '\n' ' ')"
                nonmatch=""
                for h in $holders; do
                    [ -n "$h" ] || continue
                    case " $owned_spaced " in
                        *" $h "*) ;;
                        *) nonmatch="$nonmatch $h" ;;
                    esac
                done
                if [ -z "$nonmatch" ]; then
                    # every holder is owned now — accept (evidence-only).
                    GATE_ATTRIBUTION_DETAIL=""
                    return 0
                fi
                ladder_accept=""; ladder_refuse=""; ladder_unattrib=""
                for h in $nonmatch; do
                    [ -n "$h" ] || continue
                    __lrc=0
                    _gate_attr_containment "$h" "$owned_spaced" "$what" || __lrc=$?
                    case "$__lrc" in
                        0) ladder_accept="$ladder_accept $h" ;;
                        1) ladder_refuse="$ladder_refuse $h" ;;
                        *) ladder_unattrib="$ladder_unattrib $h" ;;
                    esac
                done
                if [ -n "$ladder_refuse" ]; then
                    # a definitive negative arrived during the deferral window:
                    # refuse NOW (the deferral never downgrades a fail).
                    GATE_ATTRIBUTION_DETAIL="port-holder(s):${nonmatch} NOT in the supervision-owned pid set (owned:${owned_spaced:-<none>}) — the :${PORT} answerer is not this install's daemon; containment ladder:${ladder_refuse}"
                    _warn "attribution [$what]: REFUSED —$GATE_ATTRIBUTION_DETAIL (ensemble-vm §5.1: :9797 held by kubectl/monitor while the daemon serves :9798)"
                    return 1
                fi
                if [ -n "$ladder_accept" ]; then
                    printf '%s[%s]: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "attribution [$what]: ACCEPTED — port-holder(s):${ladder_accept} contained in the supervised tree (cgroup containment / owned ancestry; the argv-anchored owned set does not cover them)" >&2 2>/dev/null
                    l2_holders="$(printf '%s\n' $holders | while IFS= read -r __h; do
                        case " $ladder_accept " in *" $__h "*) ;; *) printf '%s\n' "$__h" ;; esac
                    done)"
                    break
                fi
                # still unattributable — loop until the pool is exhausted
            done
            if [ -n "$ladder_accept" ]; then
                : # accepted inside the deferral window (evidence-only)
            else
                # ── EXHAUSTION (leader D1): TRANSIENT, not a refusal ──────
                # The distinct marker rides STDERR (I-B2b) and journals ONE
                # fire-once row (the latch) with the NEW row token
                # kind=attr_deferred — the EVENT kind stays
                # subprocess_wait_timeout (no new event kind, I-E3).
                if [ -z "$_GATE_ATTR_EXHAUST_LATCH" ]; then
                    _GATE_ATTR_EXHAUST_LATCH=1
                    journal_history_append subprocess_wait_timeout \
                        "site=gate_attr kind=attr_deferred reason=child: containment UNATTRIBUTABLE within ${_GATE_ATTR_DEFER_S}s (unit MainPID/ControlGroup unresolvable, ps unavailable) — deferring to the verify budget" \
                        || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
                fi
                printf '%s[%s]: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "attribution [$what]: containment UNATTRIBUTABLE within ${_GATE_ATTR_DEFER_S}s (unit MainPID/ControlGroup unresolvable, ps unavailable) — deferring to the verify budget" >&2 2>/dev/null
                GATE_ATTRIBUTION_DETAIL="port-holder(s):${nonmatch} NOT in the supervision-owned pid set (owned:${owned_spaced:-<none>}) — containment UNATTRIBUTABLE within ${_GATE_ATTR_DEFER_S}s — deferring to the verify budget (transient)"
                return 2
            fi
        fi
    fi
    # ── Layer 2: supervisor lineage (consume the PRE-STOP classify) ──
    case "${SUPERVISION_MODE:-}" in
        launchd)
            if [ -n "${SUPERVISION_UNIT:-}" ] && _supervision_host_allows_launchd; then
                lpid="$(_supervision_launchd_job_pid "$SUPERVISION_UNIT")"
                lrc=$?
                case "$lrc" in
                    2)
                        GATE_ATTRIBUTION_DETAIL=""
                        _gate_attr_unprobeable "$what" "launchctl probe failed (tool/budget) — launchd lineage unverifiable"
                        return 0
                        ;;
                    *)
                        if [ -z "$lpid" ]; then
                            # The job is registered (print answered) but has NO
                            # live process while the port serves someone — the
                            # v0.30.3 dual-supervisor shape (launchd tree
                            # crash-looping, a nohup daemon serving). Refuse.
                            GATE_ATTRIBUTION_DETAIL="launchd job '$SUPERVISION_UNIT' has NO live pid while :${PORT} is served by:${holders} — the answerer is not the supervised daemon"
                            _warn "attribution [$what]: REFUSED — $GATE_ATTRIBUTION_DETAIL (incident 2026-11-14 §3.1)"
                            return 1
                        fi
                        for h in $holders; do
                            if ! _supervision_pid_in_launchd_lineage "$h" "$lpid"; then
                                GATE_ATTRIBUTION_DETAIL="port-holder $h is OUTSIDE launchd job '$SUPERVISION_UNIT' lineage (job pid $lpid) — a second supervisor is serving"
                                _warn "attribution [$what]: REFUSED — $GATE_ATTRIBUTION_DETAIL"
                                return 1
                            fi
                        done
                        ;;
                esac
            fi
            ;;
        unit)
            if [ -n "${SUPERVISION_UNIT:-}" ]; then
                # $holders is newline-joined — pass UNQUOTED so the unit arm
                # word-splits per holder (F1 council fix: a quoted pass made a
                # multi-holder host probe /proc/"p1 NL p2"/cgroup and
                # false-refuse with garbled detail).
                # closure C-2: skip holders the ladder already accepted
                _gate_attr_unit_lineage "$what" ${l2_holders-$holders} || return 1
            fi
            ;;
    esac
    return 0
}

# _gate_attr_unit_ctx <what> — resolve the unit's MainPID + ControlGroup ONCE
# per gate invocation (T4, promote-handback-halt Phase 1 §5.3). Sets the two
# globals `_GA_UNIT_MAINPID` / `_GA_UNIT_CG` and returns:
#   0 = resolved (both values usable)
#   2 = unprobeable (systemctl tool/budget/unit-state failure, MainPID 0, or a
#       degenerate ControlGroup of ""/"") — the caller ABSTAINS, never decides
#       (a "/"-or-empty ControlGroup means the tooling resolved no real cgroup;
#       reading it as positive foreignness is exactly the false-halt class this
#       lane removes — the code precedent is the twin guard in the legacy
#       lineage arm).
# The single factored home of the two bounded `systemctl show` probes: the
# containment ladder's R2 rung and the legacy Layer 2 arm BOTH read it, so a
# gate invocation that reaches either path pays for at most two probes and the
# probe arithmetic lives in exactly one place.
_GA_UNIT_MAINPID=""
_GA_UNIT_CG=""
_GA_UNIT_UNPROBEABLE_WHY=""
_gate_attr_unit_ctx() {
    local what="${1:-gate}" tmp rc_main main_pid cg
    tmp="$(mktemp -t attr.XXXXXX 2>/dev/null || printf '/tmp/attr.%s.out' "$$")"
    _run_bounded "$SYSTEMCTL_PROBE_BUDGET_S" -- "$SYSTEMCTL_BIN" show "$SUPERVISION_UNIT" -p MainPID --value > "$tmp" 2>/dev/null
    rc_main=$?
    main_pid="$(cat "$tmp" 2>/dev/null)"
    if [ "$rc_main" -ne 0 ] || [ -z "$main_pid" ] || [ "$main_pid" = "0" ]; then
        rm -f "$tmp" 2>/dev/null
        _GA_UNIT_MAINPID=""; _GA_UNIT_CG=""
        _GA_UNIT_UNPROBEABLE_WHY="MainPID"
        return 2
    fi
    _GA_UNIT_MAINPID="$main_pid"
    _run_bounded "$SYSTEMCTL_PROBE_BUDGET_S" -- "$SYSTEMCTL_BIN" show "$SUPERVISION_UNIT" -p ControlGroup --value > "$tmp" 2>/dev/null
    cg="$(cat "$tmp" 2>/dev/null)"
    rm -f "$tmp" 2>/dev/null
    if [ -z "$cg" ] || [ "$cg" = "/" ]; then
        _GA_UNIT_MAINPID="$main_pid"; _GA_UNIT_CG=""
        _GA_UNIT_UNPROBEABLE_WHY="ControlGroup"
        return 2
    fi
    _GA_UNIT_CG="$cg"
    _GA_UNIT_UNPROBEABLE_WHY=""
    return 0
}

# _gate_attr_unit_lineage <what> <holders...> — the systemd lineage arm
# (MainPID ∧ unit-cgroup containment — the supervision_dualfight_check
# Check B grammar, mirrored): holders must BE the unit's MainPID or sit
# inside the unit's ControlGroup. Tool failure (bounded probe rc 124 /
# systemctl absent) = unverifiable → loud skip, never evidence; an
# ANSWERED-but-uncontainable holder is a mismatch → refuse.
_gate_attr_unit_lineage() {
    local what="$1"; shift
    local h main_pid cg pcg
    # T4: behaviour-identical re-implementation on top of the factored ctx
    # resolver (ONE home for the two bounded probes). The messages — including
    # the `MainPID=` / `cgroup=` detail tokens — stay BYTE-IDENTICAL.
    if ! _gate_attr_unit_ctx "$what"; then
        # Distinguish the two unprobeable shapes so the loud note keeps its
        # legacy wording: an unresolvable MainPID vs an unobservable
        # ControlGroup.
        if [ "${_GA_UNIT_UNPROBEABLE_WHY:-}" = "ControlGroup" ]; then
            _gate_attr_unprobeable "$what" "unit '$SUPERVISION_UNIT' ControlGroup unobservable — containment unverifiable"
        else
            _gate_attr_unprobeable "$what" "systemd MainPID unresolvable for '$SUPERVISION_UNIT' (tool/budget/unit state)"
        fi
        return 0
    fi
    main_pid="$_GA_UNIT_MAINPID"
    cg="$_GA_UNIT_CG"
    for h in "$@"; do
        [ -n "$h" ] || continue
        [ "$h" = "$main_pid" ] && continue
        # The legacy raw-substring containment test is DELIBERATELY kept
        # byte-identical (decisions.md §2.2(5)): Layer 2 is refusal-only and
        # the common path must not change. The ladder's R2 rung is the
        # segment-aware (strictly narrower) form; a ladder-accepted holder
        # never reaches this re-test (closure C-2).
        pcg="$(_supervision_pid_cgroup_path "$h" 2>/dev/null)"
        case "$pcg" in
            *"$cg"*) ;;
            *)
                GATE_ATTRIBUTION_DETAIL="port-holder $h is OUTSIDE unit '$SUPERVISION_UNIT' lineage (MainPID=$main_pid cgroup='${pcg:-<unreadable>}') — a second supervisor is serving"
                _warn "attribution [$what]: REFUSED — $GATE_ATTRIBUTION_DETAIL"
                return 1
                ;;
        esac
    done
    return 0
}

# gate_livez / gate_readyz — budgeted probes (D2). Print body; nonzero on fail.
# Fix #2: a green answer is only trusted when the attribution conjunct
# holds (the SUPERVISED daemon answers — see _gate_serving_attribution).
# ROW 2 (phase1 §5.4b, ruling D-P1-15): the probe's budget-exhaustion rc 2
# ("NOBODY ANSWERED" — the daemon is dead/unreachable) is NORMALIZED to rc 1
# at livez/readyz: no answer at all IS a health verdict, so the 8b
# auto-rollback stays armed (the B-dead-daemon leg). The gate's own rc 2
# (green but UNATTRIBUTABLE) is NOT normalized — it stays transient and is
# consumed as a deferral by the one-shot consumers (T7c). This asymmetry is
# deliberate and recorded (phase1 §5.4a).
_gate_livez_rc() {
    local __rc=0 __grc=0
    _probe "/livez" "$LIVEZ_BUDGET_S" "$PORT" || __rc=$?
    case "$__rc" in
        0) ;;
        2) return 1 ;;   # nobody answered => definitive health failure
        *) return "$__rc" ;;
    esac
    _gate_serving_attribution "/livez" || __grc=$?
    case "$__grc" in
        0) return 0 ;;
        2) return 2 ;;   # transient: green but unattributable
        *) return 1 ;;
    esac
}
_gate_readyz_rc() {
    local __rc=0 __grc=0
    _probe "/readyz" "$READYZ_BUDGET_S" "$PORT" || __rc=$?
    case "$__rc" in
        0) ;;
        2) return 1 ;;   # nobody answered => definitive health failure
        *) return "$__rc" ;;
    esac
    _gate_serving_attribution "/readyz" || __grc=$?
    case "$__grc" in
        0) return 0 ;;
        2) return 2 ;;   # transient: green but unattributable
        *) return 1 ;;
    esac
}
gate_livez()  { _gate_livez_rc; }
gate_readyz() { _gate_readyz_rc; }

# gate_version <expected> — one-shot /livez version check (D2/ADR-027: the
# RUNNING daemon must self-report the manifest's binary_version).
# Fix #2: version identity is only trusted from the ATTRIBUTED daemon —
# the conjunct rides the green answer (gate_soak's per-tick version
# check inherits the attribution from this site).
gate_version() {
    local expected="$1" body running __vrc=0
    body="$(_probe_once "/livez" "$PORT")"
    [ -n "$body" ] || { _warn "version verify: /livez not answering on :$PORT"; return 1; }
    running="$(_json_field "$body" version)"
    if [ "$running" != "$expected" ]; then
        _warn "version verify MISMATCH: running=$running expected=$expected (manifest binary_version)"
        return 1
    fi
    # ROW 2: the direct collapse (`|| return 1`) is REMOVED — the gate's rc is
    # captured so a TRANSIENT attribution deferral (rc 2) survives to the
    # consumer instead of being laundered into a definitive "version verify
    # mismatch" (which would arm the 8b auto-rollback on a healthy boot).
    __vrc=0
    _gate_serving_attribution "version verify" || __vrc=$?
    if [ "$__vrc" != "0" ]; then
        return "$__vrc"
    fi
    _log "version verify OK: $running"
    return 0
}

# gate_soak <seconds> <expected_version> — ADR-005 soak with P3 adaptive
# default + conservative opt-in.
#
# P3 (decision (c), D-P3.1) — adaptive default behavior:
#   - Floor ADAPTIVE_SOAK_FLOOR_S=60, cap ADAPTIVE_SOAK_CAP_S=120,
#     consecutive-green threshold ADAPTIVE_SOAK_CONSECUTIVE=3, probe
#     cadence ADAPTIVE_SOAK_CADENCE_S=15 (NOT 30; per leader Q6a ruling).
#   - Each iteration probes livez+readyz+version via the classified
#     _probe_once_classified path (decision (e), D-P3.6): 503/7/22/28 are
#     transient (do not fail the phase), everything else is red (fail
#     fast — do NOT ride out).
#   - Termination (C-1 corrected semantics):
#       * now >= adaptive_cap AND consecutive_green >= 3 → exit 0 SUCCESS
#       * now >= adaptive_cap AND consecutive_green <  3 → exit 1 → auto-rollback
#         (C-1: under sustained DB churn, the gate would otherwise
#         reach cap with zero greens and silently commit; the pre-P3
#         behavior was instant red → auto-rollback, never silent)
#       * any red at any iteration → exit 1 FAILURE immediately
#       * early-exit SUCCESS requires consecutive_green >= 3 (the
#         floor is wall-clock, not green-count — 3 greens at 15s cadence
#         ≈ 45s cannot satisfy before the 60s floor anyway)
#   - Mid-soak version drift preserved (gate_version failure → red,
#     C-10 + lib.sh:4258 anchor).
#   - The 900s hard ceiling at promote.sh:156 unchanged; the gate
#     respects the per-phase budget floor (the soak phase gets its
#     remaining budget from the per-phase decomposition).
#
# P3 (decision (c), D-P3.2) — conservative opt-in:
#   ENSEMBLE_SOAK_CONSERVATIVE=1 → reverts to the v0.18-era flat
#   behavior (no early exit, sleep 30, soak_s=300 unless overridden).
#   Existing ENSEMBLE_PROMOTE_SOAK_S env var is the user-override
#   knob in BOTH modes.
#
# P3 (decision (d), D-P3.5) — adaptive-soak coupling: if the caller
# passes a 3rd arg (ceremony_remaining_s), the effective adaptive cap
# is min(ADAPTIVE_SOAK_CAP_S, ceremony_remaining_s - 60). If
# ceremony_remaining_s < 60, the gate runs with 0s and journals a
# deferral event. The "-60" preserves a 60s audit tail (commit
# bookkeeping + retention_evict + lock_release).
#
# Heartbeats the pipeline lock (D5/D-FA5.1) so a long soak never
# stale-breaks a live owner's lock.
gate_soak() {
    local soak_s="$1" expected="$2" ceremony_remaining_s="${3:-}"
    local waited=0 now deadline rc consecutive_green=0 soak_start
    local adaptive_floor="$ADAPTIVE_SOAK_FLOOR_S"
    local adaptive_cap="$ADAPTIVE_SOAK_CAP_S"
    local adaptive_n="$ADAPTIVE_SOAK_CONSECUTIVE"
    local adaptive_cadence="$ADAPTIVE_SOAK_CADENCE_S"
    local conservative=0
    local log_line
    # C6 (council): record the WALL-CLOCK soak start at gate entry —
    # the early-exit floor is elapsed = now - soak_start, NOT the
    # green count and NOT the sleep-cadence-accumulated $waited
    # (which drifts against real time).
    soak_start="$(_now_epoch)"
    if [ "${ENSEMBLE_SOAK_CONSERVATIVE:-0}" = "1" ]; then
        conservative=1
    fi

    if [ "$soak_s" -le 0 ]; then
        _log "soak skipped (0s — drill knob)"
        return 0
    fi

    # D-P3.5: cap shrinks against the ceremony budget when the caller
    # threads ceremony_remaining_s. Subtract a 60s audit tail
    # (commit bookkeeping + retention_evict + lock_release). If
    # remaining < 60, journal a deferral event and return 0.
    if [ -n "$ceremony_remaining_s" ]; then
        if ! printf '%s' "$ceremony_remaining_s" | grep -Eq '^[0-9]+$'; then
            _warn "soak: ceremony_remaining_s='$ceremony_remaining_s' is non-numeric — falling back to ADAPTIVE_SOAK_CAP_S"
            ceremony_remaining_s=""
        else
            local adj_cap
            adj_cap=$(( ceremony_remaining_s - 60 ))
            if [ "$adj_cap" -lt "$adaptive_cap" ]; then
                if [ "$adj_cap" -lt "$adaptive_floor" ]; then
                    _log "soak: ceremony_remaining_s=${ceremony_remaining_s} — cap=$adj_cap < floor=$adaptive_floor; journaling deferral and running 0s soak"
                    journal_history_append soak_deferred "ceremony budget starvation: ceremony_remaining_s=$ceremony_remaining_s < adaptive_floor+60=${adaptive_floor}; cap shrinks to $adj_cap < floor $adaptive_floor — deferring (gates remain the source of truth)" \
                                                      || true
                    return 0
                fi
                adaptive_cap="$adj_cap"
            fi
        fi
    fi

    # W2 (council): ENSEMBLE_PROMOTE_SOAK_S (soak_s) was claimed by the
    # log line as the adaptive ceiling but never APPLIED — the env was
    # silently ignored in adaptive mode. It is now a REAL cap:
    # effective cap = min(ADAPTIVE_SOAK_CAP_S, ceremony-shrunk cap,
    # soak_s). The floor is clamped to the effective cap so a short
    # user ceiling still terminates (a floor ABOVE the cap would make
    # the soak un-exitable on greens).
    if [ "$soak_s" -lt "$adaptive_cap" ]; then
        _log "soak: ENSEMBLE_PROMOTE_SOAK_S=${soak_s}s is BELOW the adaptive cap ${adaptive_cap}s — capping (W2: the env is a real ceiling now)"
        adaptive_cap="$soak_s"
    fi
    if [ "$adaptive_floor" -gt "$adaptive_cap" ]; then
        _log "soak: floor ${adaptive_floor}s > effective cap ${adaptive_cap}s — clamping floor to the cap"
        adaptive_floor="$adaptive_cap"
    fi

    if [ "$conservative" = "1" ]; then
        # W1 (council): conservative mode is v0.18-identical STRICT —
        # boot-grace suppression and busy-amnesty soft-greens are
        # DISABLED for the conservative loop (the strict flag makes
        # _probe_once_classified classify 503/connection-failures as
        # plain transient→_probe_once non-zero→red, exactly the v0.18
        # curl -fsS behavior). Delta vs v0.18 (documented, acceptable):
        # (a) classification mechanism is -w status-code + captured
        # curl_rc instead of -fsS body-sniff (outcome-identical for
        # 503/5xx/4xx/refused/timeout); (b) probes heartbeats the
        # pipeline lock each iteration (additive safety, no outcome
        # change); (c) gate_version mid-soak re-check present (was
        # absent in v0.18 — strictens, never loosens).
        _PROBE_SUPPRESSION_STRICT=1
        _log "soak ${soak_s}s (CONSERVATIVE mode: ENSEMBLE_SOAK_CONSERVATIVE=1 — re-probe /livez + /readyz every 30s; no early exit; v0.18-identical strict probes)"
        deadline=$(( $(_now_epoch) + soak_s ))
        while :; do
            lock_heartbeat
            if ! _probe_once "/livez" "$PORT" > /dev/null; then
                _warn "soak FAILURE: /livez went red at ${waited}s (conservative mode)"
                return 1
            fi
            if ! _probe_once "/readyz" "$PORT" > /dev/null; then
                _warn "soak FAILURE: /readyz went red at ${waited}s (conservative mode)"
                return 1
            fi
            # T7f / note 8 (decisions §9c): the soak tick routes through the
            # gate family. A per-tick FRESH pool, and a transient tick is
            # INCONCLUSIVE (streak-freeze) — never a definitive failure.
            _GATE_ATTR_DEFER_REMAIN="$_GATE_ATTR_DEFER_S"
            __svrc=0
            gate_version "$expected" > /dev/null || __svrc=$?
            case "$__svrc" in
                0) : ;;
                2) _log "soak: version tick INCONCLUSIVE at ${waited}s (attribution deferred — transient; streak frozen, counter neither advanced nor reset)";;
                *) _warn "soak FAILURE: version drifted at ${waited}s (conservative mode)"; return 1 ;;
            esac
            now="$(_now_epoch)"
            [ "$now" -lt "$deadline" ] || break
            sleep 30
            waited=$((waited + 30))
        done
        _log "soak complete (${soak_s}s green; conservative mode)"
        return 0
    fi

    # Adaptive default mode.
    _log "soak ${adaptive_cap}s (early-exit on ${adaptive_n} consecutive green probes @ ${adaptive_cadence}s cadence, floor ${adaptive_floor}s; user-set soak_s=${soak_s} — the adaptive CAP governs, soak_s is a hard ceiling)"
    _PROBE_SUPPRESSION_STRICT=0   # W1: adaptive default re-enables grace/amnesty
    deadline=$(( $(_now_epoch) + adaptive_cap ))
    # ── A2 (soak-gate-mcp-warmup) — the soak's grace state ──────────
    # consecutive_hold — the consecutive HOLD observations (a HOLD does
    #   NOT advance the green counter; it HOLDS it).
    # granted — the window extension spent so far (0 until a
    #   grace-eligible HOLD is observed; the extension is SPENT, NOT
    #   REFUNDED).
    # soak_absolute_bound — the ABSOLUTE bound of the soak window:
    #   soak_entry + ADAPTIVE_SOAK_CAP_S + GRACE_EXTENSION_CAP_S
    #   (= soak_entry + 300s). The grace-eligible observation window can
    #   NEVER extend past it (requirements §4.3 P3).
    local consecutive_hold=0
    local granted=0
    # ROW 19 (T7f): INCONCLUSIVE-tick counter — the cap-reach discriminator
    # that separates "we could not tell" (rc 2, transient) from "genuinely
    # never green" (rc 1, auto-rollback).
    local __soak_inconclusive=0
    local soak_absolute_bound=$(( soak_start + ADAPTIVE_SOAK_CAP_S + GRACE_EXTENSION_CAP_S_INT ))
    while :; do
        lock_heartbeat
        # Classified probe path (D-P3.6): 503/7/22/28 → transient
        # (counted as "no green" — does not advance counter, does not
        # fail the phase). All other non-2xx → red (fail-fast).
        #
        # ── A2: the per-probe status locals ────────────────────────
        # Each probe's status is captured IMMEDIATELY after its OWN
        # call. The counter below reads these LOCALS, not the probe-level
        # global ``_PROBE_STATUS`` — which the SECOND probe overwrites
        # (the aliasing defect: decisions §3b B5 / R-MASK-6). The
        # global stays the probe-level value everywhere else (~22 call
        # sites) — the fix is AT THE COUNTER SITE ONLY.
        local livez_rc readyz_rc livez_status readyz_status livez_body readyz_body
        _probe_once_classified "/livez" "$PORT"
        livez_rc=$?
        livez_status="$_PROBE_STATUS"
        livez_body="$_PROBE_BODY"
        case "$livez_rc" in
            0) ;;  # green (or boot-grace-suppressed / busy-amnesty)
            1)
                # red — fail-fast, do not ride out
                _warn "soak FAILURE: /livez went RED at ${waited}s (non-transient — fail-fast per D-P3.1)"
                return 1
                ;;
            2)
                # transient-exhausted — counted as "no green"
                _log "soak: /livez transient-exhausted at ${waited}s (counted as no green)"
                ;;
            3)
                # W3: boot-grace-suppressed — ignored: not a green,
                # not a fail. The consecutive counter resets below
                # (an ignored probe is not a green).
                _log "soak: /livez boot-grace-suppressed at ${waited}s (ignored)"
                ;;
        esac
        _probe_once_classified "/readyz" "$PORT"
        readyz_rc=$?
        readyz_status="$_PROBE_STATUS"
        readyz_body="$_PROBE_BODY"
        case "$readyz_rc" in
            0) ;;  # green (or busy-amnesty soft-green)
            1)
                _warn "soak FAILURE: /readyz went RED at ${waited}s (non-transient — fail-fast per D-P3.1)"
                return 1
                ;;
            2)
                _log "soak: /readyz transient-exhausted at ${waited}s (counted as no green)"
                ;;
            3)
                _log "soak: /readyz boot-grace-suppressed at ${waited}s (ignored)"
                ;;
        esac
        # Version drift: same red semantics (lib.sh:4258 anchor —
        # mid-soak REAL version drift fails the gate). C2 (council):
        # the version check runs ONLY on a green tick — during a
        # transient window there is NO version evidence (empty probe
        # body); reading that as "drift" turned every transient storm
        # into an instant soak failure (the C2 violation). A genuine
        # drift still arrives on a 200 tick and fails exactly per the
        # pin.
        # T7f / note 8 (decisions §9c): the soak tick MUST route through the
        # gate family — a per-tick FRESH pool (the pool is reset at the top of
        # each tick's version check), and a transient tick is INCONCLUSIVE
        # (streak-freeze: it neither increments nor resets consecutive_green).
        local __soak_tick_rc=0 __soak_version_rc=0
        local observation_kind="RESET"
        if [ "$livez_rc" = "0" ] && [ "$readyz_rc" = "0" ]; then
            _GATE_ATTR_DEFER_REMAIN="$_GATE_ATTR_DEFER_S"
            gate_version "$expected" > /dev/null || __soak_version_rc=$?
            __soak_tick_rc="$__soak_version_rc"
        fi
        if [ "$__soak_tick_rc" = "2" ]; then
            __soak_inconclusive=$(( ${__soak_inconclusive:-0} + 1 ))
            # INCONCLUSIVE — a third observation kind, distinct from the
            # probe-transient RESET (which is preserved). The streak FREEZES.
            _log "soak: version tick INCONCLUSIVE at ${waited}s (attribution deferred — transient; streak frozen, counter neither advanced nor reset)"
            observation_kind="INCONCLUSIVE"
        elif [ "$__soak_tick_rc" != "0" ] && [ "$__soak_tick_rc" != "" ]; then
            _warn "soak FAILURE: version drifted at ${waited}s (lib.sh:4258 pin)"
            return 1
        fi
        # Advance the consecutive-green counter ONLY on real green
        # results (boot-grace-suppressed and busy-amnesty soft-greens
        # do NOT count — the C-1 invariant is "consecutive_green" is the
        # count of NON-transient green probes; soft-greens are
        # explicitly excluded so the floor requirement stays load-
        # bearing).
        #
        # ── A2 (soak-gate-mcp-warmup): the THREE observation kinds ───
        # The normative rule is requirements.md §4.2 (P2). In short:
        #
        #   GREEN — livez HARD-green (rc=0 AND its per-probe status is
        #           literally "green" — NOT a busy-amnesty soft-green)
        #           AND readyz hard-green (rc=0 AND status "green").
        #           => consecutive_green += 1 AND consecutive_hold = 0.
        #   HELD  — livez HARD-green AND readyz is a grace-eligible
        #           transient AND the daemon reports an ACTIVE grace AND
        #           no leak signal.
        #           => consecutive_green UNCHANGED, consecutive_hold += 1.
        #   RESET — EVERYTHING ELSE, INCLUDING any tick where livez is
        #           NOT a hard green (a livez transient is a RESET, never
        #           a HOLD), any never-eligible class, any unknown
        #           class, any hard red, and the boot-grace-suppressed
        #           class.
        #           => consecutive_green = 0 AND consecutive_hold = 0.
        #
        # A green counts only behind AT MOST MAX_HOLDS_PER_GREEN
        # consecutive holds; a green behind more is REJECTED (the streak
        # breaks and resets) — a stream of holds can never manufacture
        # greens, and a never-green daemon still ends at cap-reach with
        # consecutive_green < 3 => return 1 => auto-rollback.
        # (observation_kind is declared ABOVE, before the tick-rc block, so the
        # INCONCLUSIVE arm can set it — T7f.)
        # One leak parse per tick — the same unchanged body fed BOTH the
        # HELD classification below and the extension guard (the parsers
        # are pure; re-parsing per site would be wasted work).
        local readyz_leak=0
        _readyz_leak_detected "$readyz_body" && readyz_leak=1
        if [ "$livez_rc" = "0" ] && [ "$livez_status" = "green" ] && \
           [ "$readyz_rc" = "0" ] && [ "$readyz_status" = "green" ]; then
            observation_kind="GREEN"
        elif [ "$livez_rc" = "0" ] && [ "$livez_status" = "green" ] && \
             [ "$readyz_status" != "green" ] && \
             _readyz_grace_eligible_transient "$readyz_body" && \
             _readyz_grace_active "$readyz_body" && \
             [ "$readyz_leak" = "0" ]; then
            observation_kind="HELD"
        fi
        case "$observation_kind" in
            INCONCLUSIVE)
                # STREAK-FREEZE (T7f, R2 §2.4): a transient tick neither
                # advances nor resets the streak — incrementing would
                # manufacture greens from unattributable observations, and
                # resetting would let a transient storm reach the cap with
                # zero greens.
                ;;
            GREEN)
                if [ "$consecutive_hold" -gt "$MAX_HOLDS_PER_GREEN" ]; then
                    # The hold budget is SPENT: a green behind more than
                    # MAX_HOLDS_PER_GREEN consecutive holds does NOT
                    # count toward the 3 — the consecutive streak breaks.
                    consecutive_green=0
                else
                    consecutive_green=$((consecutive_green + 1))
                fi
                consecutive_hold=0
                ;;
            HELD)
                consecutive_hold=$((consecutive_hold + 1))
                ;;
            RESET)
                consecutive_green=0
                consecutive_hold=0
                ;;
        esac
        # ── A2: the BOUNDED window extension (requirements §4.3 P3) ───
        # The extension is granted only when a grace-eligible HOLD was
        # observed AND the leak predicate is false. It is SPENT, NOT
        # REFUNDED, and the ABSOLUTE bound (soak_entry + 300s) always
        # dominates: the deadline is re-anchored at each (re)grant but
        # can NEVER exceed the absolute bound.
        if [ "$observation_kind" = "HELD" ] && [ "$readyz_leak" = "0" ]; then
            if [ "$granted" -eq 0 ]; then
                granted="$GRACE_EXTENSION_CAP_S_INT"
                journal_history_append soak_grace_extended "grace-eligible HOLD observed — the soak window is extended by ${GRACE_EXTENSION_CAP_S_INT}s (bounded: absolute bound soak_entry+${ADAPTIVE_SOAK_CAP_S}+${GRACE_EXTENSION_CAP_S_INT}s; the never-green trigger is unchanged and unconditional)" \
                                  || true   # the journal line is advisory; the window extension is already decided
            fi
            local new_cap new_deadline
            new_cap="$(_soak_effective_cap "$granted" "$ceremony_remaining_s" "$soak_s")"
            new_deadline=$(( $(_now_epoch) + new_cap ))
            [ "$new_deadline" -gt "$soak_absolute_bound" ] && new_deadline="$soak_absolute_bound"
            if [ "$new_deadline" -gt "$deadline" ]; then
                deadline="$new_deadline"
                adaptive_cap="$new_cap"
                _log "soak: grace-eligible HOLD — window extended to ${deadline} (cap ${new_cap}s, absolute bound ${soak_absolute_bound})"
            fi
        fi
        now="$(_now_epoch)"
        # C-1 corrected: cap-reach with insufficient greens → exit 1
        # → auto-rollback (the never-green commit bug under sustained
        # DB churn). The pre-P3 behavior was instant red → auto-
        # rollback; the C-1 fix is that the gate MUST reach the cap
        # with consecutive_green >= 3 to commit.
        # C6 (council): early-exit ALSO requires the wall-clock floor —
        # elapsed = now - soak_start (recorded at gate entry), NOT the
        # green count alone. 3 fast greens must NOT end the soak before
        # the approved floor.
        local soak_elapsed=$(( $(_now_epoch) - soak_start ))
        if [ "$consecutive_green" -ge "$adaptive_n" ] && [ "$soak_elapsed" -ge "$adaptive_floor" ]; then
            _log "soak SUCCESS at ${waited}s — ${consecutive_green} consecutive greens (adaptive early-exit; floor ${adaptive_floor}s HELD: elapsed=${soak_elapsed}s)"
            return 0
        fi
        if [ "$now" -ge "$deadline" ]; then
            if [ "$consecutive_green" -ge "$adaptive_n" ]; then
                _log "soak SUCCESS at cap (${waited}s) — ${consecutive_green} consecutive greens"
                return 0
            fi
            # ROW 19 (phase1 §5.4b) — cap-reach disposition. A cap reached
            # with INCONCLUSIVE ticks and no definitive red is NOT a health
            # verdict: it returns the TRANSIENT rc 2 so the promote consumer
            # takes the defer arm (never 8b). A genuine never-green
            # (inconclusive == 0) keeps the C-1 anti-silent-commit rc 1.
            if [ "${__soak_inconclusive:-0}" -gt 0 ]; then
                _warn "soak: cap-reach at ${waited}s with consecutive_green=$consecutive_green < $adaptive_n AND ${__soak_inconclusive} inconclusive (unattributable) tick(s) — INCONCLUSIVE, deferring (transient; NOT an auto-rollback verdict)"
                return 2
            fi
            _warn "soak FAILURE: cap-reach at ${waited}s with consecutive_green=$consecutive_green < $adaptive_n — auto-rollback route (C-1: never-green commit bug under sustained DB churn)"
            return 1
        fi
        sleep "$adaptive_cadence" 2>/dev/null
        waited=$(( waited + adaptive_cadence ))
    done
}

# retention_evict — T8: keep RETENTION_KEEP newest (by manifest staged_at,
# dir-mtime fallback); NEVER evict current or journal previous; previous
# pinned. Explicit check: previous recorded but dir missing → loud WARN
# (the auto-rollback halt path already refuses to roll back onto it).
#
# Journal-aware rewrite (retention-eviction-gap commission): the legacy body
# evicted purely by epoch order, so a STAGED release awaiting its ceremony
# (no promoted descendant yet) was rm -rf'd silently with NO journal row,
# and the on-disk `current` pointer target could be evicted when it diverged
# from journal current. The rewrite is pointer-safe and journaled: every
# eviction / refusal / hold writes a history row BEFORE the mutation; a
# staged-awaiting release is pinned unless SUPERSEDED by a version-greater
# commit; the pin set also covers in_flight.target / pending_op.target.
# Keep-3 COUNTING/ORDER is UNCHANGED (current+previous are pinned members of
# the three; epoch sort stays). RC contract UNCHANGED: 0 normal (including
# refusal-to-evict — never fail the post-commit seat), 1 only on journal_read.
#
# Helpers below answer the three lifecycle questions over a one-entry-per-line
# history view. The splice serializes compact single-line JSON, so grep -n
# over "$json" would report line 1 for every row; splitting history on "},{"
# first makes grep -n line numbers equal chronological array indices (SPEC §2).

# _retention_hist_lines <json> — one history entry per line, chronological.
# String-naive split on "},{" by design (our detail vocabulary never emits
# that byte pair — same discipline as _json_sub's bracket walk). Pure bash so
# no new python surface and no sed-portability (GNU/BSD) drift.
_retention_hist_lines() {
    local hist nl=$'\n' ob='{' cb='}'
    hist="$(_json_sub "$1" history 2>/dev/null)" || return 0
    [ -n "$hist" ] && [ "$hist" != "null" ] || return 0
    printf '%s\n' "${hist//\},\{/$cb$nl$ob}"
}

# _retention_staged_line <hist_lines> <V> — 1-based array index of V's LAST
# "staged" row whose detail begins `release <V> staged at `, else empty.
# LAST wins on re-stage. Anchors (SPEC §2): event token
# `"event":[[:space:]]*"staged"` (spacing-tolerant) + detail prefix
# `release <V> staged at ` (v-prefix dots escaped for ERE).
_retention_staged_line() {
    local lines="$1" v="$2" vre="${2//./\\.}"
    printf '%s\n' "$lines" \
        | grep -nE "\"event\":[[:space:]]*\"staged\".*\"detail\":[[:space:]]*\"release ${vre} staged at " \
        | tail -1 | cut -d: -f1
}

# _retention_commit_line <hist_lines> <V> — 1-based array index of V's LAST
# commit row naming V (`promote V committed`), else empty. LAST wins.
_retention_commit_line() {
    local lines="$1" v="$2" vre="${2//./\\.}"
    printf '%s\n' "$lines" \
        | grep -nE "\"event\":[[:space:]]*\"commit\".*promote ${vre} committed" \
        | tail -1 | cut -d: -f1
}

# _retention_superseded_by <hist_lines> <after_idx> <V> — greatest W whose
# commit row sits AFTER the given row and whose stripped-v version sorts
# GREATER than V's (sort -V). Empty => no superseding promotion.
_retention_superseded_by() {
    local lines="$1" after="$2" v="$3" vv="${3#v}" tail_lines w wv best=""
    tail_lines="$(printf '%s\n' "$lines" | tail -n "+$((after + 1))")"
    while read -r w; do
        [ -n "$w" ] || continue
        wv="${w#v}"
        [ "$wv" = "$vv" ] && continue
        [ "$(printf '%s\n%s\n' "$vv" "$wv" | sort -V | tail -1)" = "$wv" ] || continue
        if [ -z "$best" ] || [ "$(printf '%s\n%s\n' "${best#v}" "$wv" | sort -V | tail -1)" = "$wv" ]; then
            best="$w"
        fi
    done < <(printf '%s\n' "$tail_lines" \
        | grep -E "\"event\":[[:space:]]*\"commit\"" \
        | grep -oE 'promote [^[:space:]]+ committed' | awk '{print $2}')
    printf '%s' "$best"
}

# _retention_liveness_holder <proc_root> <release_dir_abs>
# LIVENESS PROBE (stopgap AMEND-2): a release dir may still be the cwd /
# exe / root of a LIVE process, or appear inside a live process's argv.
# Scanning the directory listing for "oldest first" and deleting it is the
# defect class this closes: the dir must be PROVEN dead first.
#   rc 0 — a holder was found: prints "<pid>:<which-link>"
#   rc 1 — no holder found (safe to evict)
#   rc 2 — /proc unavailable/unreadable (FAIL-CLOSED: never evict)
# Only NUMERIC /proc/<pid> entries are scanned (kernel threads and other
# non-pid entries are skipped). The probe root is overridable for tests via
# ENSEMBLE_RETENTION_PROC_ROOT (default /proc).
_retention_liveness_holder() {
    local proc_root="$1" p="$2" phys="" pid_dir pid link target cmdline
    if [ ! -d "$proc_root" ] || [ ! -r "$proc_root" ]; then
        return 2
    fi
    # Physical form of the release dir: /proc paths are kernel-RESOLVED, so
    # a $HOME reached through a symlink would otherwise never prefix-match.
    phys="$(cd "$p" 2>/dev/null && pwd -P)" || phys=""
    for pid_dir in "$proc_root"/*; do
        pid="${pid_dir##*/}"
        case "$pid" in ''|*[!0-9]*) continue ;; esac
        for link in cwd exe root; do
            target="$(readlink "$pid_dir/$link" 2>/dev/null)" || target=""
            [ -n "$target" ] || continue
            # "under $P" = $P itself OR anything below it. The strict
            # "$P/" prefix covers exe/root (they always point INSIDE the
            # dir); the exact "$P" arm is the cwd case (a process whose cwd
            # IS the release dir — AMEND-4's own "background sleep whose cwd
            # is inside it"). Being a strict SUPERSET of the stated rule,
            # it can only ever hold MORE, never evict more.
            case "$target" in
                "$p"|"$p"/*) printf '%s:%s' "$pid" "$link"; return 0 ;;
            esac
            if [ -n "$phys" ]; then
                case "$target" in
                    "$phys"|"$phys"/*) printf '%s:%s' "$pid" "$link"; return 0 ;;
                esac
            fi
        done
        cmdline="$(tr '\0' '\n' < "$pid_dir/cmdline" 2>/dev/null)" || cmdline=""
        case "$cmdline" in
            *"$p"/*) printf '%s:%s' "$pid" "cmdline"; return 0 ;;
        esac
        if [ -n "$phys" ]; then
            case "$cmdline" in
                *"$phys"/*) printf '%s:%s' "$pid" "cmdline"; return 0 ;;
            esac
        fi
    done
    return 1
}

retention_evict() {
    local rel="$INSTALL_DIR/releases" json cur prev d name entries sorted n
    # LIVENESS PROBE ROOT (AMEND-2): /proc by default; overridable so a
    # scratch harness can simulate both a live holder and an unreadable /proc.
    local PROC_ROOT="${ENSEMBLE_RETENTION_PROC_ROOT:-/proc}"
    json="$(journal_read)" || return 1
    cur="$(_json_field "$json" current)"
    prev="$(_json_field "$json" previous)"
    [ "$cur" = "null" ] && cur=""
    [ "$prev" = "null" ] && prev=""
    if [ -n "$prev" ] && [ ! -d "$rel/$prev" ]; then
        _warn "retention: journal previous '$prev' has NO release dir (manual deletion?) — rollback target missing; auto-rollback would halt-for-human"
    fi

    # SPEC §1 — pin set: current, previous, in_flight.target, pending_op.target.
    # A null/absent sub-object yields an empty target (null-safe). The journal
    # is ours, so _json_sub + _json_field is the established extraction pair.
    local inf="" inf_target="" po="" po_target=""
    inf="$(_json_sub "$json" in_flight 2>/dev/null)" || inf=""
    if [ -n "$inf" ] && [ "$inf" != "null" ]; then
        inf_target="$(_json_field "$inf" target 2>/dev/null)"
        [ "$inf_target" = "null" ] && inf_target=""
    fi
    po="$(_json_sub "$json" pending_op 2>/dev/null)" || po=""
    if [ -n "$po" ] && [ "$po" != "null" ]; then
        po_target="$(_json_field "$po" target 2>/dev/null)"
        [ "$po_target" = "null" ] && po_target=""
    fi

    # SPEC §4 — POINTER GATE, before ANY eviction. The flip symlink lives at
    # $INSTALL_DIR/current -> releases/<ver>; normalize to the bare version
    # (${##*/}) so both the dir check and the candidate comparison use release
    # names (a legacy releases/current symlink is honored as fallback). The
    # gate engages ONLY when a pointer symlink EXISTS: a fresh/pre-fix layout
    # with none has no target to protect (journal `current` is pinned above),
    # and refusing there would strand every no-symlink fixture / first boot.
    # Healthy = target dir present AND target == journal current; anything
    # else journals retention_refuse + WARN + returns 0 with ZERO evictions
    # (post-commit seat must never fail). Pointer WRITES belong to the
    # stage/promote lane — we only read and protect here.
    local ptr_link="$INSTALL_DIR/current" tgt_raw="" tgt="" refuse_reason=""
    local _top_healthy=0
    if [ -L "$ptr_link" ]; then
        tgt_raw="$(readlink "$ptr_link" 2>/dev/null)" || tgt_raw=""
        _top_healthy=1
    elif [ -L "$rel/current" ]; then
        tgt_raw="$(readlink "$rel/current" 2>/dev/null)" || tgt_raw=""
    fi
    tgt="${tgt_raw##*/}"
    if [ -n "$tgt_raw" ]; then
        if [ ! -d "$rel/$tgt" ]; then
            refuse_reason="pointer-dangling"
        elif [ -n "$cur" ] && [ ! -d "$rel/$cur" ]; then
            refuse_reason="journal-current-dir-missing"
        elif [ "$tgt" != "$cur" ]; then
            refuse_reason="pointer-mismatch"
        fi
    fi

    # Alias-divergence seam (relcur-m1): when BOTH pointers exist and the
    # top-level pointer is healthy, the releases/current alias is a SECOND
    # pointer. If it is dangling or disagrees with the journal current, refuse
    # all evictions (fail-closed, same path as primary-pointer divergence) so
    # the alias target is never evicted. When the alias is healthy it equals
    # journal current == already pinned — no extra pin needed.
    if [ -z "$refuse_reason" ] && [ "$_top_healthy" = "1" ] && [ -L "$rel/current" ]; then
        local _alias_raw="" _alias_tgt=""
        _alias_raw="$(readlink "$rel/current" 2>/dev/null)" || _alias_raw=""
        _alias_tgt="${_alias_raw##*/}"
        if [ -n "$_alias_raw" ] && { [ ! -d "$rel/$_alias_tgt" ] || [ "$_alias_tgt" != "$cur" ]; }; then
            refuse_reason="alias-divergence"
            tgt="$_alias_tgt"
        fi
    fi

    if [ -n "$refuse_reason" ]; then
        journal_history_append retention_refuse \
            "reason=$refuse_reason pointer_target=$tgt journal_current=$cur evictions=0" \
            >/dev/null 2>&1 \
            || _warn "retention: journal append FAILED for retention_refuse ($refuse_reason)"
        _warn "retention: REFUSING all evictions — $refuse_reason (pointer_target=$tgt journal_current=$cur); zero evictions; pointer writes belong to the stage/promote lane"
        return 0
    fi

    # Chronological history view (SPEC §2/3) — empty when no journal rows yet.
    local hist_lines
    hist_lines="$(_retention_hist_lines "$json")"

    # Count ALL releases; the keep-3 CEILING is unchanged (ADR-004: "retention
    # 3 releases" — current + previous are pinned members of the three, not
    # additions to it). What CHANGED under the stopgap is ELIGIBILITY: a
    # release dir may be evicted ONLY when the journal AFFIRMATIVELY shows
    # its slot is dead. Deciding eviction from the directory listing (oldest
    # first) IS the defect — there is NO journal-less eviction any more.
    #   EVICTABLE : V has a lifecycle row of its own (a `staged` row OR a
    #               `commit` row) AND a LATER commit row for a version-
    #               greater W  ->  reason=superseded-by-promoted-<W>
    #   HELD      : EVERY other candidate, with a frozen reason token:
    #       no-journal-evidence                             (no journal row
    #                               ever mentions this version)
    #       staged-awaiting-ceremony-no-promoted-descendant  (staged row, no
    #                               later close, no superseding commit)
    #       promoted-not-superseded                         (has a commit row,
    #                               no later version-greater commit)
    # CONSEQUENCE (carry in the comment, do not "fix"): a release with NO
    # journal evidence (e.g. one staged before this fix landed) is now HELD,
    # not evicted — the sweep prefers holding to guessing.
    local total=0 st="" st_raw="" staged_line="" commit_line="" ref_line=""
    local cand_staged="no" cand_reason="" sup_by="" hold_reason=""
    local entries="" held="" held_reasons="" held_count=0
    for d in "$rel"/*/; do
        [ -d "$d" ] || continue
        name="${d%/}"; name="${name##*/}"
        [ "$name" = "current" ] && continue   # the flip symlink, not a release
        # Protocol/working artifacts under releases/ are NOT releases: the
        # D5 lock dir, its stale-break leftovers, and stage temp assemblies
        # (pre-fix the lock dir sorted first and was silently rm -rf'd).
        case "$name" in
            rollback.lock.d|rollback.lock.d.stale.*|.staging.*) continue ;;
        esac
        total=$((total + 1))
        # Hard pins — counted in total, never evicted (SPEC §1 + pointer gate).
        case "$name" in
            "$cur"|"$prev"|"$inf_target"|"$po_target"|"$tgt")
                [ -n "$tgt" ] && [ "$name" = "$tgt" ] \
                    && _log "retention: SKIP $name — on-disk pointer target (stage/promote lane owns pointer writes)"
                continue ;;
        esac
        # normalize the sort key to EPOCH: a manifest staged_at (ISO) is
        # converted; a missing manifest falls back to the dir mtime (already
        # epoch). Sorting ISO strings against epoch digits lexicographically
        # is meaningless — normalizing keeps oldest-first order (review i3).
        st_raw="$(manifest_field "$name" staged_at 2>/dev/null)"
        if [ -n "$st_raw" ]; then
            st="$(_iso_to_epoch "$st_raw" 2>/dev/null)"
        else
            st=""
        fi
        # P2.1 BSD-port closure: `stat -f '%m'` is BSD-only; GNU reads `-f` as
        # FILESYSTEM status and emits MEASURED garbage. Same uname-dispatch
        # shape as _iso_to_epoch / _atomic_flip — families SELECTED, never
        # ||-chained (a chain would launder wrong-family output into the sort
        # key). Degrade unchanged: stat error / unknown platform → 0.
        if [ -z "$st" ]; then
            case "$(uname -s)" in
                Darwin|*BSD*|*bsd*) st="$(stat -f '%m' "$d" 2>/dev/null)" ;;
                Linux|GNU*|*GNU*)   st="$(stat -c '%Y' "$d" 2>/dev/null)" ;;
                *)                  st="" ;;
            esac
        fi
        [ -n "$st" ] || st=0

        # EVIDENCE: V's own lifecycle rows (last one of each kind wins).
        staged_line="$(_retention_staged_line "$hist_lines" "$name")"
        commit_line="$(_retention_commit_line "$hist_lines" "$name")"
        if [ -z "$staged_line" ] && [ -z "$commit_line" ]; then
            hold_reason="no-journal-evidence"
        else
            # The reference point is V's LAST lifecycle row: a superseding
            # commit must come AFTER it (a promotion that happened BEFORE V's
            # own promotion does not supersede V).
            if [ -z "$staged_line" ]; then
                ref_line="$commit_line"
            elif [ -z "$commit_line" ]; then
                ref_line="$staged_line"
            elif [ "$staged_line" -gt "$commit_line" ]; then
                ref_line="$staged_line"
            else
                ref_line="$commit_line"
            fi
            sup_by="$(_retention_superseded_by "$hist_lines" "$ref_line" "$name")"
            if [ -n "$sup_by" ]; then
                cand_staged="no"; [ -n "$staged_line" ] && cand_staged="yes"
                cand_reason="superseded-by-promoted-$sup_by"
                entries="$entries$st $name $cand_staged $cand_reason\n"
                continue
            fi
            # Not superseded -> HELD, one frozen reason token per dir.
            # (the staged_line empty case MUST be tested separately: a bare
            # `[ "$commit_line" -ge "$staged_line" ]` against an empty string
            # is an integer-expression error in bash and would silently
            # mislabel a promoted release as staged-awaiting.)
            if [ -z "$staged_line" ]; then
                hold_reason="promoted-not-superseded"
            elif [ -n "$commit_line" ] && [ "$commit_line" -ge "$staged_line" ]; then
                hold_reason="promoted-not-superseded"
            else
                hold_reason="staged-awaiting-ceremony-no-promoted-descendant"
            fi
        fi
        held="${held:+$held,}$name"
        held_reasons="${held_reasons:+$held_reasons:}$hold_reason"
        held_count=$((held_count + 1))
        _log "retention: HOLD $name — $hold_reason (the journal does not affirm this slot is dead; the sweep prefers holding to guessing)"
    done

    # The pin_hold row names EVERY held dir with its reason (positional to
    # `held=`), plus the counts. Written whenever >=1 dir is held — "nothing
    # evicted" must never be silent.
    if [ "$held_count" -gt 0 ]; then
        journal_history_append retention_pin_hold \
            "held=$held reasons=$held_reasons keep=$RETENTION_KEEP total=$total evictions=0" \
            >/dev/null 2>&1 \
            || _warn "retention: journal append FAILED for retention_pin_hold"
        _warn "retention: PIN HOLD — $held_count dir(s) held (held=[$held] reasons=[$held_reasons]); eviction proceeds ONLY for journal-evidenced superseded releases; over-retention is the safe direction"
    fi

    if [ "$total" -le "$RETENTION_KEEP" ]; then
        _log "retention: $total releases ≤ keep=$RETENTION_KEEP — nothing to evict"
        return 0
    fi
    local evict_count=$((total - RETENTION_KEEP))
    _log "retention: $total releases > keep=$RETENTION_KEEP — evicting up to $evict_count oldest (current=$cur previous=$prev in_flight=$inf_target pending_op=$po_target pointer=$tgt pinned; held=$held)"

    # Eviction loop — pipeline subshell (existing shape): journal appends are
    # atomic; no variable propagation out of it is needed.
    printf '%b' "$entries" | sort | head -n "$evict_count" | while read -r st name cand_staged cand_reason; do
        [ -n "$name" ] || continue
        # Belt: the pointer target is never evicted (hard-pinned above too).
        if [ -n "$tgt" ] && [ "$name" = "$tgt" ]; then
            _log "retention: SKIP $name — pointer target; never evicted"
            continue
        fi
        # LIVENESS PROBE (AMEND-2) — the dir must be PROVEN dead before the
        # rm -rf: a release dir may still be the cwd / exe / root of a live
        # process, or appear inside a live process's argv.
        p="$rel/$name"
        case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
        holder="$(_retention_liveness_holder "$PROC_ROOT" "$p")"
        probe_rc=$?
        if [ "$probe_rc" -eq 2 ]; then
            # FAIL-CLOSED: /proc unreadable -> we cannot prove the slot is
            # dead, so we NEVER evict it (and we say why in the row).
            journal_history_append retention_refuse \
                "reason=live-process-holding-release version=$name path=$p holder=proc-root-unreadable evictions=0" \
                >/dev/null 2>&1 \
                || _warn "retention: journal append FAILED for retention_refuse (liveness probe unavailable, $name)"
            _warn "retention: HOLD $name — liveness probe UNAVAILABLE ($PROC_ROOT unreadable); FAIL-CLOSED, never evict a dir whose liveness cannot be proven"
            continue
        fi
        if [ -n "$holder" ]; then
            journal_history_append retention_refuse \
                "reason=live-process-holding-release version=$name path=$p holder=$holder evictions=0" \
                >/dev/null 2>&1 \
                || _warn "retention: journal append FAILED for retention_refuse (live holder, $name)"
            _warn "retention: HOLD $name — a LIVE process holds it (holder=$holder path=$p); never evict a release dir a live process is running from"
            continue
        fi
        ptarget="no"; [ "$name" = "$tgt" ] && ptarget="yes"
        st_at="$(manifest_field "$name" staged_at 2>/dev/null)"
        [ -n "$st_at" ] || st_at="unknown"
        # FIX-CONTRACT (b) — FAIL-CLOSED on a journal-append failure: a
        # retention_evict row we could NOT write means the eviction is NOT
        # durably recorded, so we do NOT evict. The dir SURVIVES (the
        # semantics module C's pack pins) and the sweep CONTINUES with the
        # other candidates. rc stays 0: this is the post-commit seat, which
        # must never fail; over-retention is the safe direction. This mirrors
        # stage.sh's fail-loud staged append (stage.sh:1192) IN SPIRIT — but
        # WITHOUT exiting, because the stage seat may exit and this one may
        # not. The pre-fix shape ("eviction still performed") was the
        # fail-OPEN window: an unrecorded rm -rf is indistinguishable from a
        # never-happened eviction.
        if ! journal_history_append retention_evict \
            "version=$name staged_at=$st_at reason=$cand_reason staged_row=$cand_staged pointer_target=$ptarget journal_current=$cur journal_previous=$prev" \
            >/dev/null 2>&1; then
            _warn "retention: HOLD $name — held: journal row append failed — refusing to evict without a journal record (fix-contract b)"
            journal_history_append retention_pin_hold \
                "held=$name reasons=journal-append-failed keep=$RETENTION_KEEP total=$total evictions=0" \
                >/dev/null 2>&1 \
                || _warn "retention: journal append FAILED for retention_pin_hold ($name — the journal path may be down entirely)"
            continue
        fi
        _log "retention: evicting $name (staged $st) — $cand_reason (staged_row=$cand_staged)"
        rm -rf "$rel/$name"
    done
    return 0
}

# _run_log_retention <dir> -- D3-10/T3.11 per-run LOG retention (R8 binding).
#
# OWNER = the running shell AT CEREMONY END, UNDER THE PIPELINE LOCK (the
# seats are promote.sh's commit phase and stage.sh's success path). R8:
# the liveness check applies to FOREIGN/STALE lock owners ONLY -- the
# running owner ($$) IS the one deleting, so "<=20 kept" genuinely holds
# (the plan's original fence-5 wording -- "never delete while the lock is
# held" -- would have made retention a self-contradicting NO-OP for its
# own owner; R8 supersedes it).
#
# Rule: keep-newest 20 (by mtime) + age-cap 14 d, over the env-scoped
# per-run file family (<run_id>.log narrative / .payload.log / .client.log,
# plus the PRE-A2 <run_id>.payload.client.log name still on disk).
# FIVE FENCES -- a file survives regardless of age/count when it is:
#   (1) this run's own narrative file ($ENSEMBLE_UPGRADE_LOG_FILE);
#   (2) the journal in_flight run's files (a CONCURRENT txn's narrative);
#   (3) the last pointer's files (data/upgrade.log's newest pointer line);
#   (4) modified < 6 h ago -- the incident-class guard: a STALLED live run
#       holds a stale-mtime file open while systemd still owns it; deleting
#       it would vanish the narrative into an unlinked inode exactly when
#       the post-mortem needs it;
#   (5) nothing at all when the lock is held by a LIVE FOREIGN owner.
# Portable primitives ONLY: `find -mmin` / `find -mtime` / `ls -1t`. NEVER
# `stat -f '%m'` (MEASURED silent garbage on GNU -- lib.sh's retention_evict
# call at the release seat is a pre-existing P2.1-class defect this lane
# must not propagate; grep-pinned absent from THIS function).
_run_log_retention() {
    local dir="${1:-}"
    [ -n "$dir" ] || dir="$(dirname "${ENSEMBLE_UPGRADE_LOG_FILE:-/nonexistent/x}")"
    [ -d "$dir" ] || return 0
    # (5) foreign live owner -> hands off
    local lock owner
    lock="$(lock_dir_path)"
    if [ -d "$lock" ]; then
        owner="$(cat "$lock/owner" 2>/dev/null)"
        if [ -n "$owner" ] && [ "$owner" != "$$" ]            && printf '%s' "$owner" | grep -Eq '^[0-9]+$' && _pid_alive "$owner"; then
            _log "run-log retention: skipped (pipeline lock held by a live foreign owner pid $owner)"
            return 0
        fi
    fi
    local protected="" cur_ptr_run f
    # (1) our own narrative
    [ -n "${ENSEMBLE_UPGRADE_LOG_FILE:-}" ] && protected="$protected $(basename "$ENSEMBLE_UPGRADE_LOG_FILE")"
    # (2) the in_flight run (concurrent txn transcript)
    local _j="" _inf=""
    _j="$(journal_read 2>/dev/null)" || _j=""
    if [ -n "$_j" ]; then
        _inf="$(_json_sub "$_j" in_flight 2>/dev/null)" || _inf=""
    fi
    cur_ptr_run=""
    if [ -n "$_inf" ] && [ "$_inf" != "null" ]; then
        cur_ptr_run="$(_json_field "$_inf" run_id 2>/dev/null)" || cur_ptr_run=""
    fi
    # NOTE: a miss is a safe non-protect only because a concurrent txn also
    # holds the pipeline lock (fence 5 would have blocked the whole pass).
    case "$cur_ptr_run" in ''|null|*[!A-Za-z0-9._-]*) cur_ptr_run="" ;; esac
    # (3) the last pointer line's files
    local ptr_line=""
    [ -f "$INSTALL_DIR/data/upgrade.log" ] && ptr_line="$(tail -1 "$INSTALL_DIR/data/upgrade.log" 2>/dev/null)"
    # (4) anything modified in the last 6 h
    local recent
    recent="$(find "$dir" -maxdepth 1 -type f \( -name '*.log' \) -mmin -360 2>/dev/null | sed 's|.*/||')"
    # newest-first ordering, then keep the first 20 NON-fenced; delete the rest.
    local kept=0 name
    local alln
    alln="$(ls -1t "$dir" 2>/dev/null | grep -E '\.log$')" || alln=""
    [ -n "$alln" ] || return 0
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        case " $protected " in *" $name "*) continue ;; esac
        case "$cur_ptr_run" in
            "") ;;
            # A2 (code council, 2026-10-30): the per-run family is FOUR names
            # while pre-A2 runs exist on disk -- `<run>.log` (narrative),
            # `<run>.payload.log` (unit append: capture), `<run>.client.log`
            # (client stderr, the CURRENT name since A2) and
            # `<run>.payload.client.log` (the PRE-A2 name). Fencing only the
            # first three left the running run's client log protected solely
            # by the <6 h mtime fence. Pinned by preflight_logging_test.sh
            # E10b.
            *) case "$name" in "${cur_ptr_run}.log"|"${cur_ptr_run}.payload.log"|"${cur_ptr_run}.client.log"|"${cur_ptr_run}.payload.client.log") continue ;; esac ;;
        esac
        case "$ptr_line" in *"$name"*) continue ;; esac
        printf '%s\n' "$recent" | grep -Fqx -- "$name" && continue
        kept=$((kept + 1))
        if [ "$kept" -gt 20 ]; then
            rm -f "$dir/$name" 2>/dev/null || true
        fi
    done <<EOF
$alln
EOF
    # age cap: anything older than 14 d that survived the count pass must
    # still be fence-protected, so only UNFENCED over-age files remain --
    # the loop above already deleted everything past the newest 20.
    find "$dir" -maxdepth 1 -type f -name '*.log' -mtime +14 2>/dev/null | while IFS= read -r f; do
        name="$(basename "$f")"
        case " $protected " in *" $name "*) continue ;; esac
        case "$ptr_line" in *"$name"*) continue ;; esac
        printf '%s\n' "$recent" | grep -Fqx -- "$name" && continue
        rm -f "$f" 2>/dev/null || true
    done
    return 0
}

# ── Entry-side refusal checks (D-FA4.2: ENTRY only — never the recovery) ────
# _refuse <reason-token> <message...> — entry-refusal: WARN the message
# UNCHANGED, best-effort-append a `refusal` journal history event carrying
# the D-FA2.2 reason=<token> (activates the dormant upgrade_promote_refusal
# SSE kind, P2.3 B4/T8a), then exit 78. Best-effort ONLY: an absent/torn/
# unwritable journal WARNs once and the refusal exit proceeds — NEVER
# blocks, NEVER alters the exit code, and NEVER acquires the pipeline lock
# (an in-flight txn's lock is sacred; callers hold it already or accept a
# dropped event). ADR-034: the append rides the plain journal_history_append
# splice — additive only, no new write mechanics. The lock-busy refusal
# sites deliberately do NOT journal (unlocked append would race the live
# lock-holder's read-modify-write; pipeline-busy is structured-logged).
_refuse() {
    local reason="$1"; shift
    # L10 (tidier, P2.3 final batch): a token-less or message-less refusal is
    # a caller bug — refuse LOUDLY (warn + exit 78) WITHOUT journaling an
    # empty reason=()/detail record (the taxonomy stays greppable).
    if [ -z "${reason:-}" ] || [ -z "$*" ]; then
        _warn "refusal helper MISUSED: _refuse requires a non-empty reason token AND message (got reason='${reason:-}' msg='$*') — refusing without journaling"
        exit 78
    fi
    # Include the reason token in the WARN line too (not just the journal)
    # so the operator can grep stderr for the taxonomy without a journal
    # read. The journal still gets the full detail for audit; this is a
    # parallel channel, not a replacement.
    _warn "$* (reason=$reason)"
    journal_history_append refusal "$* (reason=$reason)" >/dev/null 2>&1 \
        || _warn "refusal journal append FAILED (best-effort) — proceeding to exit 78"
    exit 78
}

# promote_entry_check <target_ver> — refuses (exit 78) on: halthead (cap
# exhausted in-window), cooldown window, quarantined target, fresh in_flight,
# adoption-in-progress marker (Layer ii, commission v0.16.6 component 2).
# The AUTO-ROLLBACK path and the launcher sweep NEVER call this.
promote_entry_check() {
    local target_ver="$1" json cnt
    # cap / halt state
    cnt="$(journal_rollback_count_24h)"
    if [ "$cnt" -ge "$ROLLBACK_CAP_24H" ]; then
        _refuse cap "HALT-FOR-HUMAN: rollback cap $ROLLBACK_CAP_24H/24h reached (count=$cnt) — promotes refused until the 24h window resets or an operator intervenes (the journal refusal event reason=cap carries the record, D-FA2.2; the halt events that ARMED the cap sit in the rollback windows' history)"
    fi
    # cooldown
    if journal_cooldown_active; then
        local until
        until="$(_json_field "$(journal_read)" cooldown_until)"
        _refuse cooldown "promote refused: rollback cooldown active until $until (ADR-005: 10-min anti-flapping)"
    fi
    # quarantined target — the frozen "cleared only by re-staging" rule is
    # EXTENDED, never bypassed (promote-handback-halt Phase 2, §D.3): the
    # gate now ALSO accepts an explicit `quarantine_cleared_serving_green`
    # history event naming this version. That event is written ONLY by the
    # serving-green closer, after a positive serving-green verdict that is
    # itself gated on intent_flip evidence + txn staleness + owner-deadness
    # + the health probes — so it is evidence-backed, journaled, and
    # explicit. Two surfaces carry the clearance (the event AND the array
    # rewrite) and EITHER satisfies the gate — belt-and-braces against a
    # half-written clearance. The refusal message is UNCHANGED when neither
    # signal is present (the frozen vocabulary).
    if journal_is_quarantined "$target_ver"; then
        if journal_quarantine_cleared_serving_green "$target_ver"; then
            _log "promote entry: version '$target_ver' is in quarantined[] BUT the journal carries a quarantine_cleared_serving_green event for it — accepting (the explicit serving-green clearance; the frozen rule is extended, not bypassed)"
        else
            _refuse quarantine "promote refused: version '$target_ver' is QUARANTINED (prior gate failure) — quarantine is cleared only by re-staging the version"
        fi
    fi
    # adoption-in-progress marker (commission v0.16.6 component 2, Layer ii).
    # Symmetric refusal: a half-staged adoption (.env staged, unit NOT
    # enabled) would silently misroute a concurrent promote's restart
    # through `systemctl restart <unit>` (lib.sh keys off
    # ENSEMBLE_RESTART_UNIT directly) onto a unit that does not exist
    # yet — promoting onto nothing. Marker present → refuse; the marker
    # is removed by adopt-unit.sh on verify success or by the operator
    # on a half-staged recovery (see the runbook).
    if adoption_marker_present; then
        local mp
        mp="$(adoption_marker_path)"
        _refuse adoption-in-progress "promote refused: adoption-in-progress marker present at $mp (adopt-unit.sh mid-sequence or stalled) — a concurrent promote would race the half-staged .env/unit state and silently misroute the restart; wait for the adoption to clear the marker, or remove it manually after verifying the unit is NOT half-staged"
    fi
    return 0
}

# adopt_stale_txn — preflight handling of an unresolved in_flight. MIRRORS
# the launcher sweep decision table (launcher.sh _journal_sweep — D-FA4.3)
# so a promote never tramples an unresolved transaction and never strands
# the env on an orphaned flip:
#   unparseable started_at → FAIL CLOSED (refuse; never adopt a txn we
#       cannot age — the sweep does the same);
#   kind=restart → NEVER adopted (D-FA4.3: the daemon boot sweep owns
#       restart txns; the launcher sweep skips them too) — refuse, leave
#       untouched;
#   comp3 FAST PATH (heartbeat-stale + owner-dead, age ≤ SWEEP_STALE_S):
#       reclaim NOW (skip the 600s wait). Closes the r-f82e gap where
#       the executor died 2s in (no heartbeat) AND the owner pid was
#       gone, but the launcher sweep wouldn't reclaim for 600s.
#       Heartbeat fresh OR owner alive → fall through to the age gate
#       below (do NOT short-circuit; the liveness fast path is
#       ADDITIVE to the primary age gate, not a replacement);
#   fresh (age ≤ SWEEP_STALE_S) → leave alone + refuse (pipeline-busy).
#       Only reached when the fast path didn't fire (heartbeat fresh
#       OR owner alive — the conservative reading);
#   stale + flipped:true → the SAME recovery the sweep would perform:
#       manifest gate on previous FIRST (null / QUARANTINED (M4) / release
#       dir missing / not rollback_safe → halt event, NO repoint, txn LEFT
#       IN PLACE, exit 78), then repoint current→previous (atomic_flip —
#       the portable rename(2) helper — see atomic_flip's portability
#       comment for the BSD/GNU mv dispatch details), quarantine
#       the failed target, count the rollback + arm cooldown (ADR-024),
#       history event 'sweep_rollback' (P2.3's ledger consumes event
#       names), clear txn. W3: every recovery state write (quarantine /
#       set_current / close_txn) is CHECKED — any failure leaves the txn
#       OPEN (the sweep retries this recovery idempotently), WARNs loudly,
#       and refuses the promote (exit 78). The counter write stays
#       warn-only (over-count is the safe drift direction);
#   stale + flipped:false → clear (history event 'sweep'). Close failure
#       → txn LEFT OPEN + loud warn + exit 78 (W3 — same contract).
#
# comp3 (r-f82e): both the liveness fast path (HEARTBEAT_STALE_S ×
# owner dead → reclaim early, before SWEEP_STALE_S) and the
# launcher.sh _journal_sweep mirror this decision. The launcher
# mirror is at BOOT (no concurrent activity to race against) and is
# the safer place; the adopt path also gets it because a manual recovery
# push (run-this-pipeline-now) shouldn't wait 600s for a heartbeat
# gap that's already been confirmed by liveness.
#
# Runs UNDER the caller's lock (promote acquires before calling). After a
# sweep-rollback adoption the enclosing promote continues into its ENTRY
# checks, which then apply the freshly armed cooldown/cap — PER DESIGN
# (D-FA4.2/ADR-024: the NEXT entry is refused inside the 10-min window;
# rollback.sh manual recovery never refuses on cooldown/cap).
adopt_stale_txn() {
    local json inf kind target started flipped owner epoch age now hb_stale owner_dead
    json="$(journal_read)" || return 0
    inf="$(_json_sub "$json" in_flight)"
    [ -z "$inf" ] && return 0
    case "$inf" in *"kind"*) ;; *) return 0 ;; esac
    kind="$(_json_field "$inf" kind)"
    target="$(_json_field "$inf" target)"
    started="$(_json_field "$inf" started_at)"
    flipped="$(_json_field "$inf" flipped)"
    owner="$(_json_field "$inf" owner_pid)"
    now="$(_now_epoch)"
    # comp3: liveness state computed once, consulted below.
    hb_stale="$(_txn_heartbeat_stale "$inf" "$now")"
    owner_dead="$(_txn_owner_dead "$inf")"
    # D-FA4.3 / R-SR13: restart-kind pending-ops are NEVER adopted (the
    # launcher sweep skips them too) — restarts are self-completing and the
    # daemon boot sweep owns them (P2.2). Leave untouched; pipeline-busy.
    if [ "$kind" = "restart" ]; then
        _warn "promote refused: in_flight kind=restart (target=${target:-?}) — D-FA4.3: restart txns are never adopted/swept (daemon boot sweep owns them); leaving untouched, pipeline-busy"
        exit 78
    fi
    # unparseable started_at → fail closed: the sweep leaves such a txn
    # untouched (launcher.sh:570-573); adoption must not fire on a txn it
    # cannot prove stale.
    if ! epoch="$(_iso_to_epoch "$started")"; then
        _warn "promote refused: in_flight $kind txn (target=$target) has unparseable started_at ('$started') — leaving untouched, pipeline-busy (the sweep fails closed on it too)"
        exit 78
    fi
    age=$(( now - epoch ))
    # comp3 FAST PATH (heartbeat-stale + owner-dead → reclaim now).
    # Runs BEFORE the freshness refuse: closes the r-f82e gap where
    # the executor died ~2s in (no heartbeat ever written, owner gone)
    # and the existing freshness gate would have held the txn for
    # the full SWEEP_STALE_S=600s before the next launcher sweep
    # could reclaim it. The fast path SKIPS the freshness refuse and
    # goes straight to the standard stale-adopt body below — so the
    # hold-or-reclaim decision is uniform across the age-gated and
    # fast-paths (same manifest gate, same flip, same quarantine,
    # same counter increment).
    local _FAST_PATH=0
    if [ "$hb_stale" = "1" ] && [ "$owner_dead" = "1" ]; then
        _log "adopt_stale_txn: comp3 fast path triggered (hb_stale=1 owner_dead=1 age=${age}s) — reclaiming via the standard recovery (flipped=${flipped:-—})"
        _FAST_PATH=1
    fi
    # fresh txn (no fast path fired) → refuse. The fast path above
    # only fires when heartbeat is stale AND owner is dead; a fresh
    # heartbeat OR a live owner with a fresh-age txn still hits this
    # refuse (the conservative reading — never trample a possibly-
    # about-to-write owner).
    if [ "$_FAST_PATH" -eq 0 ] && [ "$age" -le "$SWEEP_STALE_S" ]; then
        _warn "promote refused: in_flight $kind txn (target=$target, pid=${owner:-?}, age ${age}s ≤ ${SWEEP_STALE_S}s) is FRESH — left alone (heartbeat-fresh OR owner-alive; comp3 fast path consulted above) — pipeline-busy"
        exit 78
    fi
    # comp3 audit: log the liveness state at adoption so post-mortems
    # can correlate (the launcher sweep logs the same fields).
    if [ "$hb_stale" = "1" ] && [ "$owner_dead" = "1" ]; then
        _log "adopt_stale_txn: comp3 audit — heartbeat-stale + owner-dead (hb_stale=$hb_stale, owner_dead=$owner_dead) reclaimed via the standard recovery"
    fi
    if [ "$flipped" = "true" ]; then
        # ── Phase 2 (T11): the SERVING-GREEN branch ────────────────────────
        # The promote-entry mirror of the same decision table. lib.sh is NOT
        # purity-constrained ⇒ the mature classified probe
        # (`_probe_once_classified`, the house 0/1/2 taxonomy) is REUSED —
        # never a second taxonomy.
        #
        # GREEN (the CANONICAL predicate: /livez 200 ∧ version == the
        # v-stripped target ∧ /readyz 200 whose body reports ready) ∧
        # `intent_flip` evidence ⇒ COMMIT-CLOSE (AM-P2-1's
        # history-before-close order) and the promote CONTINUES — no
        # quarantine, no counter, no cooldown.
        #
        # The GREEN-without-`intent_flip` arm takes the EXISTING chain
        # BELOW (the documented divergence, §D.4 (a-i)): the launcher
        # DEFERS and the daemon takes no action this tick, but this
        # promote-entry path keeps its pre-ruling behaviour — this lane
        # does not rewrite it. The NOT-GREEN arm ALSO takes the existing
        # chain UNCHANGED, and that is correct here: this path engages only
        # a txn already PAST the 600s staleness gate (strictly older than
        # the 210–360s slow-boot class the daemon-side boot grace covers)
        # and the operator is present (§D.4 (a-ii)) — no defer, no grace.
        local _sp_livez_rc _sp_readyz_rc _sp_version _sp_readyz_status _sp_green _sp_displaced
        _sp_livez_rc=0
        _probe_once_classified "/livez" "${PORT:-}" || _sp_livez_rc=$?
        _sp_version=""
        if [ "$_sp_livez_rc" = "0" ] && [ "$_PROBE_CODE" = "200" ]; then
            _sp_version="$(_json_field "$_PROBE_BODY" "version" 2>/dev/null)" || _sp_version=""
        fi
        _sp_readyz_rc=0
        _probe_once_classified "/readyz" "${PORT:-}" || _sp_readyz_rc=$?
        _sp_readyz_status=""
        if [ "$_sp_readyz_rc" = "0" ] && [ "$_PROBE_CODE" = "200" ]; then
            _sp_readyz_status="$(_json_field "$_PROBE_BODY" "status" 2>/dev/null)" || _sp_readyz_status=""
        fi
        _sp_green=0
        if [ "$_sp_livez_rc" = "0" ] && [ "$_sp_readyz_rc" = "0" ] \
           && [ -n "$_sp_version" ] \
           && [ "$_sp_version" = "$(_version_strip_v "$target")" ] \
           && [ "$_sp_readyz_status" = "ready" ]; then
            _sp_green=1
        fi
        if [ "$_sp_green" = "1" ] && journal_history_has_intent_flip "$target"; then
            # ALL conjuncts hold — commit-close (history-before-close) and
            # let the promote continue.
            # D5 (AC-D5-11): `previous` is republished from the CAPTURE
            # (in_flight.captured_previous) when present — displaced_version
            # is the OLD-FORMAT fallback; the halt fires only when BOTH
            # are absent (byte-unchanged for old-format txns).
            _sp_displaced="$(_json_field "$inf" "captured_previous" 2>/dev/null)" || _sp_displaced=""
            case "$_sp_displaced" in
                ''|null)
                    _sp_displaced="$(_json_field "$inf" "displaced_version" 2>/dev/null)" || _sp_displaced=""
                    ;;
            esac
            case "$_sp_displaced" in
                ''|null)
                    journal_history_append halt "adopt: stale flipped $kind txn (target=$target) is SERVING GREEN but has no in_flight.displaced_version (old-format txn) — halt-for-human, txn left in place"
                    _warn "HALT-FOR-HUMAN: serving-green txn (target=$target) has no displaced_version (old-format) — no silent commit; txn left for the sweep/human"
                    exit 78
                    ;;
            esac
            _log "adopt_stale_txn: target=$target is SERVING GREEN (/livez 200 + version match + /readyz 200 ready) with intent_flip evidence — committing (history-before-close); the promote continues"
            journal_set_current "$target" || {
                _warn "adopt: serving-green commit — journal_set_current $target FAILED; txn LEFT OPEN; promote refusing (W3)"
                exit 78
            }
            journal_set_previous "$_sp_displaced" || {
                _warn "adopt: serving-green commit — journal_set_previous $_sp_displaced FAILED; txn LEFT OPEN; promote refusing (W3)"
                exit 78
            }
            if journal_is_quarantined "$target"; then
                journal_history_append quarantine_cleared_serving_green \
                    "target $target run_id $(_journal_txn_identity "$inf" "$target") serving_green_commit prev $_sp_displaced"
                journal_quarantine_clear "$target" \
                    || _warn "adopt: quarantined[] rewrite FAILED (the clearance EVENT is the gate's acceptance signal — the array is hygiene)"
            fi
            journal_history_append serving_green_commit \
                "target $target run_id $(_journal_txn_identity "$inf" "$target") prev $_sp_displaced owner pid ${owner:-?} age ${age}s — serving green (adopt-path classified probe: /livez 200 + version match + /readyz 200 ready); committed at promote entry — NOT rolled back, NOT quarantined, counter NOT incremented, no cooldown (intent_flip evidence present)"
            journal_close_txn || {
                _warn "adopt: serving-green commit — txn close FAILED; txn LEFT OPEN for the sweep to retry; promote refusing (W3)"
                exit 78
            }
            return 0
        fi
        # ── stale flipped → adopt via the sweep-rollback recovery ──────────
        # D5 (self-service-promote Wave 3): the rollback target is resolved
        # by the 5-rung CHAIN (_rollback_target_resolve — R0
        # in_flight.captured_previous > R1 to= > R2 current > R3 displaced >
        # R4 previous), NOT the bare journal.previous read (the C-4
        # stale-field class). Manifest gates on the RESOLVED target follow,
        # order unchanged (null → QUARANTINED → missing dir → rollback_safe).
        local prev prev_safe newcnt _adp_live _adp_cap _adp_r1 _adp_r2 _adp_r3 _adp_res _adp_src
        _adp_live="$(readlink "$INSTALL_DIR/current" 2>/dev/null)" || _adp_live=""
        case "$_adp_live" in releases/*) _adp_live="${_adp_live#releases/}" ;; esac
        _adp_cap="$(_json_field "$inf" "captured_previous" 2>/dev/null)" || _adp_cap=""
        [ "$_adp_cap" = "null" ] && _adp_cap=""
        _adp_r1=""
        if _rollback_request_to "$target" "$(_journal_txn_identity "$inf" "$target")" >/dev/null 2>&1; then
            _adp_r1="$(_rollback_request_to "$target" "$(_journal_txn_identity "$inf" "$target")")"
        fi
        _adp_res="$(_rollback_target_resolve "$INSTALL_DIR/releases" "$target" "$_adp_live" "$_adp_cap" "$_adp_r1" \
            "$(_json_field "$json" current)" "$(_json_field "$inf" displaced_version)" "$(_json_field "$json" previous)")"
        if [ "$?" -ne 0 ]; then
            case "$_adp_res" in
                'halt no_previous_release')
                    journal_history_append halt "adopt: stale flipped $kind txn (target=$target) but journal has no previous release — halt-for-human, NO repoint, txn left in place"
                    _warn "HALT-FOR-HUMAN: stale flipped txn (target=$target) with NO previous release — cannot adopt-rollback; txn left for the sweep/human"
                    exit 78
                    ;;
                'halt capture_invalid')
                    journal_history_append halt "adopt: stale flipped $kind txn (target=$target) capture_invalid (captured_previous='$_adp_cap' fails VALID — F3: never fall through) — halt-for-human, NO repoint, txn left in place"
                    _warn "HALT-FOR-HUMAN: captured_previous='$_adp_cap' is present-but-INVALID (corrupted txn evidence) — refusing to adopt-rollback; txn left for the sweep/human"
                    exit 78
                    ;;
                'halt rollback_target_ambiguous')
                    journal_history_append halt "adopt: stale flipped $kind txn (target=$target) rollback_target_ambiguous (capture='$_adp_cap' to='$_adp_r1' current='$(_json_field "$json" current)' displaced='$(_json_field "$inf" displaced_version)' previous='$(_json_field "$json" previous)') — halt-for-human, NO repoint, txn left in place"
                    _warn "HALT-FOR-HUMAN: CONFLICTING valid rollback-target candidates — never a wrong rollback; txn left for the sweep/human"
                    exit 78
                    ;;
                *)
                    journal_history_append halt "adopt: rollback-target resolution unrecognized ('$_adp_res') — fail-closed, NO repoint, txn left in place"
                    _warn "HALT-FOR-HUMAN: rollback-target resolution unrecognized ('$_adp_res') — fail-closed; txn left for the sweep/human"
                    exit 78
                    ;;
            esac
        fi
        prev="${_adp_res#resolve }"
        _adp_src="${prev##* }"
        prev="${prev% *}"
        _log "adopt: rollback target resolved -> $prev (source=$_adp_src)"
        # M4: a QUARANTINED previous is a known-bad release (it failed a
        # gate before) — never adopt-rollback onto it. Same halt shape as
        # promote's auto-rollback gate and the launcher sweep's: halt event,
        # NO repoint, txn left in place. Guards against a stranded/hand-
        # edited journal (promote's bookkeeping no longer strands
        # previous==quarantined, but drift must fail closed at consumption
        # too). Gate order matches promote 8b and the sweep: null →
        # QUARANTINED → missing dir → rollback_safe.
        if journal_is_quarantined "$prev"; then
            journal_history_append halt "adopt: previous release $prev is QUARANTINED (known-bad) — halt-for-human, NO repoint, txn left in place (M4)"
            _warn "HALT-FOR-HUMAN: previous release $prev is QUARANTINED — refusing to adopt-rollback onto a known-bad release; txn left for the sweep/human (M4)"
            exit 78
        fi
        if [ ! -d "$INSTALL_DIR/releases/$prev" ]; then
            journal_history_append halt "adopt: previous release $prev missing (evicted/manually deleted?) — halt-for-human, NO repoint, txn left in place"
            _warn "HALT-FOR-HUMAN: previous release $prev is MISSING — cannot adopt-rollback; txn left for the sweep/human"
            exit 78
        fi
        prev_safe="$(manifest_field "$prev" rollback_safe 2>/dev/null)"
        if [ "$prev_safe" != "true" ]; then
            journal_history_append halt "adopt: previous release $prev has rollback_safe=${prev_safe:-missing} (D-FA4.5 schema-drift guard) — halt-for-human, NO repoint, txn left in place"
            _warn "HALT-FOR-HUMAN: previous release $prev is NOT rollback_safe (${prev_safe:-missing}) — refusing to adopt-rollback into schema drift; txn left in place"
            exit 78
        fi
        # Repoint FIRST — the same flip-first ordering as the sweep: if we
        # die mid-sequence the next launcher start re-runs the sweep on
        # this same stale txn; every journal step below is idempotent
        # except the counter increment (over-counts only — conservative,
        # anti-flapping direction). Without the repoint the promote would
        # exit stranded: journal.current ≠ symlink target, env left on the
        # ungated orphaned release, cleared txn blocking future sweeps.
        _warn "adopting STALE flipped txn (age ${age}s, target=$target): sweep-rollback recovery — repoint current -> $prev, quarantine $target, count + cooldown (ADR-024)"
        if ! atomic_flip "$prev"; then
            _warn "adopt: repoint current -> $prev FAILED — txn left in place (the sweep retries at the next launcher start); promote refusing"
            exit 78
        fi
        # W3 (M5 completed): every recovery STATE write below is checked —
        # a failed write must leave the txn OPEN (the sweep retries this
        # recovery idempotently at the next launcher start) and refuse the
        # promote (exit 78) with a loud warn. NEVER close over a failed
        # state write: a set_current failure followed by a successful close
        # is permanent silent divergence, and a quarantine failure silently
        # leaves the gate-failed version promotable. The counter write
        # stays warn-only by design (9be59635 — over-count is the only
        # safe drift direction); history appends are advisory.
        journal_quarantine "$target" || {
            _warn "adopt: quarantine write FAILED for $target after the repoint — txn LEFT OPEN (the sweep retries this recovery idempotently at the next launcher start); promote refusing (W3)"
            exit 78
        }
        journal_set_current "$prev" || {
            _warn "adopt: journal_set_current $prev FAILED after the repoint — txn LEFT OPEN, never closed over a failed state write (the sweep retries idempotently); promote refusing (W3)"
            exit 78
        }
        # D5 — normalize `previous` for the new current (leave unchanged iff
        # VALID: non-null ∧ ≠ the new current ∧ the release dir exists; else
        # null + a previous_invalidated row).
        local _adp_prev_now
        _adp_prev_now="$(_json_field "$(journal_read 2>/dev/null)" previous 2>/dev/null)" || _adp_prev_now=""
        [ "$_adp_prev_now" = "null" ] && _adp_prev_now=""
        if [ -n "$_adp_prev_now" ] && [ "$_adp_prev_now" != "$prev" ] \
           && [ -d "$INSTALL_DIR/releases/$_adp_prev_now" ] \
           && ! journal_is_quarantined "$_adp_prev_now"; then
            : # VALID for the new current — left unchanged (the G1 record)
        else
            journal_set_previous "null" || _warn "adopt: previous normalization write FAILED (journal diverged)"
            journal_history_append previous_invalidated "adopt: journal previous='${_adp_prev_now:-none}' was not VALID for the new current=$prev — set null" || true
        fi
        newcnt="$(journal_count_rollback 1)" || newcnt=""
        if [ -z "$newcnt" ]; then
            # M5: the recovery's repoint/quarantine already landed — do not
            # undo them — but the counter/cooldown write FAILED: the
            # anti-flapping count is lost until the journal is repaired.
            # Loud, not silent (the sweep-rollback over-count direction is
            # the only safe drift here).
            _warn "adopt: rollback counter/cooldown write FAILED after the repoint — journal diverged; anti-flapping count may be lost (repair the journal)"
        fi
        journal_history_append sweep_rollback "adopt: orphaned flipped $kind txn (target=$target, owner pid ${owner:-?}, age ${age}s) rolled back to $prev (source=$_adp_src) at promote preflight; counted as auto-rollback (ADR-024)"
        journal_close_txn || {
            _warn "adopt: txn close FAILED after the sweep-rollback recovery — txn LEFT OPEN for the sweep to retry idempotently at the next launcher start; promote refusing (W3)"
            exit 78
        }
        if [ -n "$newcnt" ] && [ "$newcnt" -ge "$ROLLBACK_CAP_24H" ]; then
            journal_history_append halt "adopt sweep-rollback reached cap $ROLLBACK_CAP_24H/24h (count=$newcnt) — promotes refused until the window resets or an operator intervenes"
            _warn "HALT-FOR-HUMAN: cap reached while adopting stale txn — this promote is refused; next entry attempts will refuse too"
            exit 78
        fi
    else
        _warn "adopting STALE pre-flip txn (age ${age}s, target=$target): clearing (never flipped)"
        journal_history_append sweep "orphaned pre-flip $kind txn target=$target cleared at promote preflight"
        # W3: same checked-write contract — a failed close leaves the txn
        # OPEN for the sweep to retry; never report the clear as done.
        journal_close_txn || {
            _warn "adopt: txn close FAILED while clearing the stale pre-flip txn — txn LEFT OPEN (the sweep retries idempotently); promote refusing (W3)"
            exit 78
        }
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════════
# D2 — WATCHDOG CONTAINMENT (self-service-promote Wave 4, 2026-11-19)
#
# THE SUBJECT: the host's keepalive/watchdog TIMER (e.g. ensemble-keepalive.timer)
# can fire INSIDE the ceremony's stop→verified window and bring the daemon up
# on the WRONG release (design §1.4: the keepalive observed `failed` and
# issued a start ~5min after the flip; "no human was involved"). Two
# defences, complementary (design §2.3 option (e) = (a) + (c)):
#   (a) PREVENTION — HOLD the candidate timer(s) for the whole window: mint
#       the restore transient FIRST (P0.5), then `systemctl stop` the timer
#       (P1 — NEVER `disable`: an ENABLED timer is re-armed by systemd at
#       boot, so the reboot path stays covered), and RESTORE on EVERY
#       terminal path (P4 commit / P5 abort-pre-flip / P6 halt).
#   (c) CONTAINMENT — the version fast-fail on EVERY path that lacked it
#       (rollback.sh / restart.sh — the P-5 blind pin) + a bounded repair
#       (dual-fight re-check → stop → start → re-verify INSIDE the existing
#       budget) so a slipped watcher's start can NEVER be a success.
#
# THE CONTRACT (design §2.3 "Host-probe contract — probe, don't assume"):
#   * every read is BOUNDED (_run_bounded) and the whole probe is bounded
#     (WATCHDOG_PROBE_TOTAL_BUDGET_S ≤ 20s, charged to the 300s preflight);
#   * the probe NEVER assumes a unit name — the daemon unit is the
#     CLASSIFIED one (SUPERVISION_UNIT), never a hardcoded 'ensemble-main';
#   * on a SYSTEMD host the probe UNUSABLE ⇒ HALT BEFORE ANY STOP (the new
#     reason `watchdog_probe_unreadable`, inside the existing halt + exit-78
#     class). On a host WITHOUT systemd there is no systemd watchdog to
#     probe ⇒ the probe is NOT-APPLICABLE (never a false halt — the launchd
#     lane keeps working);
#   * a candidate's ExecStart unreadable ⇒ HOLD THAT TIMER ONLY (the
#     unknown-candidate arm — fail-closed, never a silent skip);
#   * the restore transient cannot be armed ⇒ DO NOT HOLD (loud
#     `watchdog_hold_unavailable` — degrade to containment (c); disclosed,
#     never silent).
#
# EVENT KINDS (ADDITIVE, and ALL NON-TERMINAL — they never close a
# pending_op; see daemon/tools/upgrade_journal.py `_TERMINAL_EVENTS`):
#   watchdog_probe · watchdog_hold · watchdog_restore ·
#   watchdog_hold_unavailable · watchdog_hold_deferred
# The HALT reason (`watchdog_probe_unreadable`) rides the EXISTING `halt`
# kind — it is a refusal REASON, not a new event kind.
#
# GUARD SCOPE (design §2.3 adjudication, SETTLED): the EXECUTOR lane is
# sanctioned to stop the keepalive timer — `stop-ensemble.sh` already stops
# an ensemble-family unit every ceremony, ungated by design. The AGENT lane
# stays refused: daemon/tools/_systemctl_guard.py is UNTOUCHED by this work.
# ═══════════════════════════════════════════════════════════════════════════

# ── D2 knobs ─────────────────────────────────────────────────────────────────
# WATCHDOG_HOLD_ENABLED — the kill-switch (default ON). 0 ⇒ the hold is NOT
# performed and the ceremony degrades to containment (c); the degradation is
# JOURNALED (watchdog_hold_unavailable), never silent.
WATCHDOG_HOLD_ENABLED="${WATCHDOG_HOLD_ENABLED:-1}"
# Per-READ bound for the candidate/daemon-unit probes (the census uses
# SYSTEMCTL_PROBE_BUDGET_S — the house probe budget).
WATCHDOG_PROBE_READ_BUDGET_S="$(_numeric_knob WATCHDOG_PROBE_READ_BUDGET_S "${WATCHDOG_PROBE_READ_BUDGET_S:-}" 2)"
# TOTAL probe bound (design §2.3: ≤20s, charged to the 300s preflight).
WATCHDOG_PROBE_TOTAL_BUDGET_S="$(_numeric_knob WATCHDOG_PROBE_TOTAL_BUDGET_S "${WATCHDOG_PROBE_TOTAL_BUDGET_S:-}" 20)"
# How many timers the probe may open an ExecStart read for (the census is
# unbounded in principle; the probe is not — the cap keeps the total bound).
WATCHDOG_PROBE_MAX_CANDIDATES="$(_numeric_knob WATCHDOG_PROBE_MAX_CANDIDATES "${WATCHDOG_PROBE_MAX_CANDIDATES:-}" 4)"
# The restore transient's TTL (default 30min). The AC-D2-3 leg scales it to
# 5s so the RT's re-arm/restore cycle is observable inside a test.
WATCHDOG_HOLD_RT_TTL_S="$(_numeric_knob WATCHDOG_HOLD_RT_TTL_S "${WATCHDOG_HOLD_RT_TTL_S:-}" 1800)"
# The hold's iteration bound (design F13/AC-D2-10: ≤3 — the OLD 20-iteration
# loop IS the named defect: ~10h of darkness on executor death).
WATCHDOG_HOLD_MAX_ITERATIONS="$(_numeric_knob WATCHDOG_HOLD_MAX_ITERATIONS "${WATCHDOG_HOLD_MAX_ITERATIONS:-}" 3)"
# An operator-declared timer name (the "configured timer" predicate arm).
WATCHDOG_TIMER_NAME="${WATCHDOG_TIMER_NAME:-}"
# The systemd-run binary (the restore transient's minter).
SYSTEMD_RUN_BIN="${SYSTEMD_RUN_BIN:-systemd-run}"

# ── D2 state (populated by the probe; consumed by the hold/restore) ─────────
WATCHDOG_PROBE_STATUS=""          # ok | unreadable | not-applicable
WATCHDOG_PROBE_DETAIL=""
WATCHDOG_PROBE_TIMERS=""          # the census (space separated timer names)
WATCHDOG_PROBE_VERDICTS=""        # the per-timer verdicts (name=verdict ...)
WATCHDOG_CANDIDATES=""            # the held set (space separated timer names)
WATCHDOG_DAEMON_STATE=""          # the classified daemon unit's ActiveState
WATCHDOG_DAEMON_SUBSTATE=""
WATCHDOG_DAEMON_FAILED=""         # the is-failed answer (the §2.3 blind pin)
WATCHDOG_DAEMON_UNITFILE_STATE="" # the F14 enable-state discriminator
WATCHDOG_RT_UNIT=""               # the restore transient's unit name
WATCHDOG_HOLD_ARMED=0             # 1 once the hold is applied (the EXIT trap's gate)
WATCHDOG_HOLDED_TIMERS=""         # the applied hold set
WATCHDOG_RESTORE_DETAIL=""

# journal_resume_txn — RE-STAKE an EXISTING open txn (resume-from-state,
# design F2-i / AC-CER-1): the owner_pid and the last_heartbeat are
# rewritten to THIS shell; EVERY other field (kind/target/started_at/
# flipped/captured_previous/displaced_version) is PRESERVED byte-for-byte —
# the capture is load-bearing for the rollback chain (the Wave-3 R0 rung)
# and a resume NEVER drops it.
journal_resume_txn() {
    local json inf new_inf now
    json="$(journal_read)" || return 1
    inf="$(_json_sub "$json" in_flight)"
    case "$inf" in
        '{'*) ;;
        *) _warn "journal_resume_txn: no open in_flight txn (in_flight null/malformed) — refusing to re-stake"; return 1 ;;
    esac
    now="$(_now_epoch)"
    new_inf="$(printf '%s' "$inf" | sed -e 's/"owner_pid": *[0-9][0-9]*/"owner_pid":'"$$"'/' -e 's/"last_heartbeat": *[0-9][0-9]*/"last_heartbeat":'"$now"'/')"
    [ "$new_inf" != "$inf" ] || { _warn "journal_resume_txn: the owner/heartbeat fields were not found in the txn — refusing (never a silent no-op)"; return 1; }
    journal_update "in_flight" "$new_inf"
}

# _promote_resume_decision <version> — the RESUME-FROM-STATE decision
# (design F2-i / AC-CER-1): a re-arm on a partially-flipped system MUST
# resume from the STATE, never re-flip:
#   gates-only : the open txn is flipped=true ⇒ the ceremony runs the
#                GATES/COMMIT ONLY — no stop, no swap, no flip (a second
#                atomic_flip would be the double-flip the design forbids);
#   flip-prep  : the open txn is flipped=false ⇒ the ceremony continues the
#                flip-prep with idempotency guards;
#   busy       : the open txn's owner is ALIVE ⇒ another executor owns the
#                journal ⇒ refuse (pipeline-busy — never trample a live
#                executor);
#   fresh      : no open txn for THIS target ⇒ the normal path.
#
# There is NO restart-from-arm path: the decision vocabulary is exactly
# {gates-only, flip-prep, busy, fresh} — "restart" is not one of them.
_promote_resume_decision() {
    local version="$1" json inf kind target flipped owner
    json="$(journal_read 2>/dev/null)" || json=""
    inf="$(_json_sub "$json" in_flight 2>/dev/null)" || inf=""
    case "$inf" in ''|null) printf 'fresh\n'; return 0 ;; esac
    kind="$(_json_field "$inf" kind 2>/dev/null)" || kind=""
    target="$(_json_field "$inf" target 2>/dev/null)" || target=""
    flipped="$(_json_field "$inf" flipped 2>/dev/null)" || flipped=""
    owner="$(_json_field "$inf" owner_pid 2>/dev/null)" || owner=""
    [ "$kind" = "promote" ] || { printf 'fresh\n'; return 0; }
    [ "$target" = "$version" ] || { printf 'fresh\n'; return 0; }
    if [ -n "$owner" ] && _pid_alive "$owner"; then
        printf 'busy\n'
        return 0
    fi
    case "$flipped" in
        true) printf 'gates-only\n' ;;
        *) printf 'flip-prep\n' ;;
    esac
    return 0
}

# _promote_resume_mutation_ok — 0 when the ceremony may MUTATE (the stop /
# the launcher swap / the atomic flip); 1 when the resume state forbids it
# (the gates-only resume). The ONLY mutation-free mode is gates-only.
_promote_resume_mutation_ok() {
    [ "${PROMOTE_RESUME_MODE:-fresh}" = "gates-only" ] && return 1
    return 0
}

# _watchdog_hold_dir — the marker's directory: <install>/data/upgrade/<env>/
# (the SAME env-scoped home the per-run narrative + payload logs use).
_watchdog_hold_dir() {
    printf '%s/data/upgrade/%s\n' "${INSTALL_DIR:-}" "${UP_TARGET:-demo}"
}

# _watchdog_hold_marker_path — the marker FILE. Root-written by the executor
# (it runs on the system bus as root) and daemon-readable (mode 0644 — the
# RC8 privilege boundary: the daemon side reads the hold state; the RT
# re-arms BY TIMER NAME and never rewrites the marker).
_watchdog_hold_marker_path() {
    printf '%s/watchdog.hold.json\n' "$(_watchdog_hold_dir)"
}

# _watchdog_candidate_verdict <timer> <execstart> <family> <daemon_unit> <configured>
#   → prints ONE of: candidate | unknown-candidate | not
#
# THE PREDICATE (design §2.3, published so no implementer invents a weaker
# one — the arms are evaluated IN THIS ORDER):
#   1. the CONFIGURED timer name (operator-declared) ⇒ candidate;
#   2. an UNREADABLE / unresolvable ExecStart ⇒ unknown-candidate WHEN the
#      timer carries the family token (keepalive|watchdog) — treated as a
#      watchdog (fail-closed) — else not;
#   3. an ExecStart that references the RESOLVED daemon unit WITH a start
#      verb ⇒ candidate;
#   4. an ExecStart whose path carries a keepalive/watchdog token ⇒ candidate;
#   5. anything else ⇒ not (an unrelated timer is never held).
#
# PURE: no I/O, no globals — the AC-D2-9 leg drives it directly.
_watchdog_candidate_verdict() {
    local timer="$1" execstart="$2" family="${3:-0}" daemon_unit="${4:-}" configured="${5:-}"
    local fam=0
    case "$timer" in *keepalive*|*watchdog*|*Keepalive*|*Watchdog*) fam=1 ;; esac
    [ "$family" = "1" ] && fam=1
    # arm 1 — the configured timer name
    if [ -n "$configured" ] && [ "$timer" = "$configured" ]; then
        printf 'candidate\n'
        return 0
    fi
    # arm 2 — an unreadable ExecStart
    if [ -z "$execstart" ]; then
        if [ "$fam" = "1" ]; then printf 'unknown-candidate\n'; else printf 'not\n'; fi
        return 0
    fi
    # arm 3 — the ExecStart references the RESOLVED daemon unit with a start verb
    if [ -n "$daemon_unit" ]; then
        case "$execstart" in
            *start*|*Start*)
                case "$execstart" in
                    *"$daemon_unit"*) printf 'candidate\n'; return 0 ;;
                esac
                ;;
        esac
    fi
    # arm 4 — a keepalive/watchdog path
    case "$execstart" in
        *keepalive*|*watchdog*|*Keepalive*|*Watchdog*) printf 'candidate\n'; return 0 ;;
    esac
    # arm 5 — unrelated
    printf 'not\n'
    return 0
}

# _watchdog_probe_read <budget> -- <systemctl args...> — a BOUNDED read,
# clamped by the probe's TOTAL budget (the deadline is set by _watchdog_probe).
_watchdog_probe_read() {
    local budget="$1"; shift
    [ "${1:-}" = "--" ] && shift
    if [ -n "${_WATCHDOG_PROBE_DEADLINE:-}" ]; then
        local remaining=$(( _WATCHDOG_PROBE_DEADLINE - $(_now_epoch) ))
        [ "$remaining" -lt 1 ] && remaining=1
        [ "$remaining" -lt "$budget" ] && budget="$remaining"
    fi
    _run_bounded "$budget" -- "$SYSTEMCTL_BIN" "$@"
}

# _watchdog_unit_state <unit> — the unit's ActiveState (bounded). Empty when
# the unit is unknown or the probe failed.
_watchdog_unit_state() {
    local unit="${1:-}" out=""
    [ -n "$unit" ] || return 1
    out="$(_watchdog_probe_read "$WATCHDOG_PROBE_READ_BUDGET_S" -- show "$unit" -p ActiveState --value 2>/dev/null)" || return 1
    printf '%s' "$out"
}

# _watchdog_probe — THE HOST PROBE (P0).
# Sets WATCHDOG_PROBE_STATUS / _DETAIL / _TIMERS / _VERDICTS / _CANDIDATES
# and the daemon-unit observations.
# rc 0 = the probe ran (status ok | not-applicable)
# rc 1 = the probe is UNREADABLE on a systemd host ⇒ the caller HALTS
#        BEFORE any stop (fail-closed — the `watchdog_probe_unreadable`
#        reason rides the existing halt + exit-78 class).
_watchdog_probe() {
    local daemon_unit="${SUPERVISION_UNIT:-}"
    local configured="${WATCHDOG_TIMER_NAME:-}"
    local census="" rc=0 line timer service execstart verdict enabled active state
    local probed=0 unprobed=0

    WATCHDOG_PROBE_STATUS="unreadable"
    WATCHDOG_PROBE_DETAIL=""
    WATCHDOG_PROBE_TIMERS=""
    WATCHDOG_PROBE_VERDICTS=""
    WATCHDOG_CANDIDATES=""
    WATCHDOG_DAEMON_STATE=""
    WATCHDOG_DAEMON_SUBSTATE=""
    WATCHDOG_DAEMON_FAILED=""
    WATCHDOG_DAEMON_UNITFILE_STATE=""

    # NOT-APPLICABLE: a host without systemd has no systemd watchdog timer to
    # probe (the launchd lane is a different animal). Never a false halt.
    if ! _supervision_host_allows_unit || ! command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1; then
        WATCHDOG_PROBE_STATUS="not-applicable"
        WATCHDOG_PROBE_DETAIL="this host has no systemd unit supervision — there is no systemd watchdog timer to probe (the launchd lane is unaffected)"
        return 0
    fi

    _WATCHDOG_PROBE_DEADLINE=$(( $(_now_epoch) + WATCHDOG_PROBE_TOTAL_BUDGET_S ))

    # READ 1 — the timer census (the ONLY enumeration; no name is assumed).
    census="$(_watchdog_probe_read "$SYSTEMCTL_PROBE_BUDGET_S" -- list-unit-files --type=timer --no-legend --no-pager 2>/dev/null)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        WATCHDOG_PROBE_DETAIL="the timer census probe failed (rc=$rc) — the watchdog state is UNREADABLE on this systemd host"
        return 1
    fi

    # READS 2-4 — the CLASSIFIED daemon unit (never a hardcoded name): its
    # state, its enable state (the F14 discriminator) and the is-failed
    # answer (design §2.3's 🟡 blind pin — the pipeline never probed
    # is-failed before this wave).
    if [ -n "$daemon_unit" ]; then
        state="$(_watchdog_probe_read "$WATCHDOG_PROBE_READ_BUDGET_S" -- show "$daemon_unit" -p ActiveState -p SubState -p UnitFileState 2>/dev/null)" || state=""
        if [ -n "$state" ]; then
            WATCHDOG_DAEMON_STATE="$(printf '%s\n' "$state" | sed -n 's/^ActiveState=//p' | head -1)"
            WATCHDOG_DAEMON_SUBSTATE="$(printf '%s\n' "$state" | sed -n 's/^SubState=//p' | head -1)"
            WATCHDOG_DAEMON_UNITFILE_STATE="$(printf '%s\n' "$state" | sed -n 's/^UnitFileState=//p' | head -1)"
        fi
        WATCHDOG_DAEMON_FAILED="$(_watchdog_probe_read "$WATCHDOG_PROBE_READ_BUDGET_S" -- is-failed "$daemon_unit" 2>/dev/null || printf 'unknown')"
        [ -n "$WATCHDOG_DAEMON_FAILED" ] || WATCHDOG_DAEMON_FAILED="unknown"
    fi

    # The census → the candidate verdicts. A timer is OPENED (its ExecStart
    # read) only while the probe's budget and its candidate cap allow; an
    # unopened timer is DISCLOSED in the probe row (the containment (c)
    # backstop covers what the prevention missed — never a silent skip).
    for line in $census; do
        timer="${line%% *}"
        [ -n "$timer" ] || continue
        case "$timer" in *.timer) ;; *) continue ;; esac
        WATCHDOG_PROBE_TIMERS="${WATCHDOG_PROBE_TIMERS}${WATCHDOG_PROBE_TIMERS:+ }$timer"
        # the configured timer is a candidate WITHOUT opening it
        if [ -n "$configured" ] && [ "$timer" = "$configured" ]; then
            WATCHDOG_PROBE_VERDICTS="${WATCHDOG_PROBE_VERDICTS}${WATCHDOG_PROBE_VERDICTS:+ }$timer=candidate"
            WATCHDOG_CANDIDATES="${WATCHDOG_CANDIDATES}${WATCHDOG_CANDIDATES:+ }$timer"
            continue
        fi
        if [ "$probed" -ge "$WATCHDOG_PROBE_MAX_CANDIDATES" ]; then
            # m6 (review-fix, 2026-11-20): the candidate cap must not
            # SILENTLY skip the FREE verdict — the name-token arm of
            # _watchdog_candidate_verdict needs NO timer open (no
            # ExecStart read): emit it BEFORE counting the skip, so a
            # family-named timer beyond the cap is still DISCLOSED as
            # unknown-candidate (and held-eligible), never lost. The
            # containment (c) backstop covers what prevention missed —
            # never a silent skip.
            verdict="$(_watchdog_candidate_verdict "$timer" "" 0 "$daemon_unit" "$configured")"
            WATCHDOG_PROBE_VERDICTS="${WATCHDOG_PROBE_VERDICTS}${WATCHDOG_PROBE_VERDICTS:+ }$timer=$verdict"
            case "$verdict" in
                candidate|unknown-candidate)
                    WATCHDOG_CANDIDATES="${WATCHDOG_CANDIDATES}${WATCHDOG_CANDIDATES:+ }$timer"
                    ;;
            esac
            unprobed=$(( unprobed + 1 ))
            continue
        fi
        probed=$(( probed + 1 ))
        # the timer's own enable state + active state (the prior state the
        # marker records; the F14 enable-state discriminator)
        enabled="$(_watchdog_probe_read "$WATCHDOG_PROBE_READ_BUDGET_S" -- show "$timer" -p UnitFileState -p ActiveState 2>/dev/null)" || enabled=""
        # the PAIRED service's ExecStart (the timer → service pairing is the
        # systemd convention: foo.timer ⇒ foo.service)
        service="${timer%.timer}.service"
        execstart="$(_watchdog_probe_read "$WATCHDOG_PROBE_READ_BUDGET_S" -- show "$service" -p ExecStart 2>/dev/null)" || execstart=""
        verdict="$(_watchdog_candidate_verdict "$timer" "$execstart" 0 "$daemon_unit" "$configured")"
        WATCHDOG_PROBE_VERDICTS="${WATCHDOG_PROBE_VERDICTS}${WATCHDOG_PROBE_VERDICTS:+ }$timer=$verdict"
        case "$verdict" in
            candidate|unknown-candidate)
                WATCHDOG_CANDIDATES="${WATCHDOG_CANDIDATES}${WATCHDOG_CANDIDATES:+ }$timer"
                ;;
        esac
    done

    WATCHDOG_PROBE_STATUS="ok"
    WATCHDOG_PROBE_DETAIL="daemon_unit=${daemon_unit:-none} daemon_state=${WATCHDOG_DAEMON_STATE:-unknown} daemon_substate=${WATCHDOG_DAEMON_SUBSTATE:-unknown} daemon_is_failed=${WATCHDOG_DAEMON_FAILED:-unknown} daemon_unit_file_state=${WATCHDOG_DAEMON_UNITFILE_STATE:-unknown} timers=$(printf '%s' "$WATCHDOG_PROBE_TIMERS" | wc -w | tr -d ' ') opened=$probed unopened=$unprobed candidates=${WATCHDOG_CANDIDATES:-none}"
    return 0
}

# _watchdog_marker_field <marker-path> <field> — read ONE flat field from the
# marker (the marker is a FLAT, shell-readable record: the RT parses it with
# the same helpers the journal uses — no nested JSON parsing).
_watchdog_marker_field() {
    local marker="$1" field="$2" json="" out="" key=""
    [ -f "$marker" ] || return 1
    json="$(cat "$marker" 2>/dev/null)" || return 1
    # The QUOTED-key form (the house `_json_field_quoted` contract — the
    # bare-substring form would match a longer key first). The key is
    # BUILT with printf (an embedded escaped-quote literal would unbalance
    # the shell-quote scanners the tree gates on).
    key="$(printf '"%s":' "$field")"
    out="$(_json_field_quoted "$json" "$key" 2>/dev/null)" || out=""
    [ "$out" = "null" ] && out=""
    printf '%s' "$out"
}

# _watchdog_marker_write <run_id> <rt-unit> <timers> <prior-active> <prior-enabled> <daemon-unit>
# — write the marker (root-written; mode 0644 so the daemon can READ it).
# The record is flat on purpose (the RT re-arms BY TIMER NAME and never
# rewrites the marker — the RC8 privilege boundary).
_watchdog_marker_write() {
    local run_id="$1" rt_unit="$2" timers="$3" prior_active="$4" prior_enabled="$5" daemon_unit="$6"
    local dir marker tmp
    dir="$(_watchdog_hold_dir)"
    marker="$(_watchdog_hold_marker_path)"
    mkdir -p "$dir" 2>/dev/null || return 1
    tmp="$dir/.watchdog.hold.json.$$"
    {
        printf '{'
        printf '"run_id":"%s",' "$run_id"
        printf '"env":"%s",' "${UP_TARGET:-demo}"
        printf '"held_at":"%s",' "$(_now_iso)"
        printf '"rt_unit":"%s",' "$rt_unit"
        printf '"daemon_unit":"%s",' "$daemon_unit"
        printf '"timers":"%s",' "$timers"
        printf '"prior_active":"%s",' "$prior_active"
        printf '"prior_unit_file_state":"%s"' "$prior_enabled"
        printf '}\n'
    } > "$tmp" 2>/dev/null || { rm -f "$tmp" 2>/dev/null || true; return 1; }
    chmod 0644 "$tmp" 2>/dev/null || true
    mv -f "$tmp" "$marker" 2>/dev/null || { rm -f "$tmp" 2>/dev/null || true; return 1; }
    return 0
}

# _watchdog_rt_arm <run_id> — P0.5: mint the RESTORE TRANSIENT.
# It MUST be armed BEFORE any hold: a hold without a restore path is the
# "watchdog permanently disabled" failure class (design §2.3 option (a),
# "the restore cannot be armed ⇒ DO NOT HOLD").
# rc 0 = armed (WATCHDOG_RT_UNIT set); rc 1 = NOT armed (the caller must
#        NOT hold — loud `watchdog_hold_unavailable`).
_watchdog_rt_arm() {
    local run_id="$1"
    local unit="ensemble-keepdog-restore-$run_id"
    local libpath ttl out rc=0
    WATCHDOG_RT_UNIT=""
    [ -n "$run_id" ] || return 1
    if ! command -v "$SYSTEMD_RUN_BIN" >/dev/null 2>&1; then
        _warn "watchdog hold: the restore transient cannot be armed ($SYSTEMD_RUN_BIN is not available) — NO watchdog timer will be held (degrading to containment (c))"
        return 1
    fi
    libpath="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)/$(basename "${BASH_SOURCE[0]:-$0}")"
    [ -f "$libpath" ] || libpath="${BASH_SOURCE[0]:-$0}"
    ttl="$WATCHDOG_HOLD_RT_TTL_S"
    out="$(_run_bounded "$WATCHDOG_PROBE_READ_BUDGET_S" -- "$SYSTEMD_RUN_BIN" \
        --on-active="$ttl" \
        --unit="$unit" \
        --description="ensemble watchdog restore transient (run $run_id)" \
        --setenv=_LIB_WATCHDOG_RT_ENTRY=1 \
        --setenv=INSTALL_DIR="${INSTALL_DIR:-}" \
        --setenv=UP_TARGET="${UP_TARGET:-demo}" \
        --setenv=WATCHDOG_RT_RUN_ID="$run_id" \
        --setenv=WATCHDOG_RT_ITERATION=1 \
        --setenv=WATCHDOG_HOLD_RT_TTL_S="$ttl" \
        --setenv=WATCHDOG_HOLD_MAX_ITERATIONS="$WATCHDOG_HOLD_MAX_ITERATIONS" \
        -- bash "$libpath" --watchdog-restore 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        _warn "watchdog hold: the restore transient could NOT be minted (rc=$rc) — NO watchdog timer will be held (degrading to containment (c))"
        return 1
    fi
    WATCHDOG_RT_UNIT="$unit"
    _log "watchdog hold: restore transient armed (unit=$unit ttl=${ttl}s run=$run_id)"
    return 0
}

# _watchdog_hold_apply <run_id> — P1: HOLD the candidate timer(s).
# `systemctl stop` ONLY — NEVER `disable` (an enabled timer is re-armed by
# systemd at boot ⇒ the reboot path stays covered; design §2.3 P8).
# The marker records the held set + the prior state.
_watchdog_hold_apply() {
    local run_id="$1" timer rc=0 held="" prior_active="" prior_enabled="" daemon_unit="${SUPERVISION_UNIT:-}"
    # T-HOLD (tidier fold, 2026-11-20): per-timer prior state. The loop
    # used to overwrite prior_active/prior_enabled per iteration, so a
    # MULTI-timer hold recorded only the LAST candidate's prior state in
    # the marker + journal row (diagnostic fields — the RESTORE path
    # re-arms every held timer unconditionally and never reads them; this
    # fixes the RECORD, not a restore path). The two *_list strings are
    # SPACE-ALIGNED with $held (same order, one token per held timer); in
    # the single-timer case — the normal shape — the marker fields are
    # byte-identical to the pre-T-HOLD output.
    local pa_list="" pe_list=""
    local marker
    marker="$(_watchdog_hold_marker_path)"
    if [ -z "$WATCHDOG_CANDIDATES" ]; then
        _log "watchdog hold: no candidate timer — nothing to hold (the probe found no watchdog-shaped timer)"
        return 0
    fi
    for timer in $WATCHDOG_CANDIDATES; do
        prior_active="$(_watchdog_probe_read "$WATCHDOG_PROBE_READ_BUDGET_S" -- is-active "$timer" 2>/dev/null || printf 'unknown')"
        prior_enabled="$(_watchdog_probe_read "$WATCHDOG_PROBE_READ_BUDGET_S" -- show "$timer" -p UnitFileState --value 2>/dev/null || printf 'unknown')"
        [ -n "$prior_active" ] || prior_active="unknown"
        [ -n "$prior_enabled" ] || prior_enabled="unknown"
        if _run_bounded "$WATCHDOG_PROBE_READ_BUDGET_S" -- "$SYSTEMCTL_BIN" stop "$timer" >/dev/null 2>&1; then
            held="${held}${held:+ }$timer"
            pa_list="${pa_list}${pa_list:+ }$prior_active"
            pe_list="${pe_list}${pe_list:+ }$prior_enabled"
            _log "watchdog hold: timer $timer stopped for the ceremony window (enable state preserved: $prior_enabled — a stopped timer is re-armed by systemd at boot)"
        else
            _warn "watchdog hold: could NOT stop timer $timer — it may still fire inside the window (the version fast-fail + the bounded repair contain it)"
        fi
    done
    if [ -z "$held" ]; then
        _warn "watchdog hold: NO timer could be held — the ceremony continues WITHOUT a watchdog hold (containment (c) is the only defence left)"
        return 1
    fi
    if ! _watchdog_marker_write "$run_id" "${WATCHDOG_RT_UNIT:-}" "$held" "$pa_list" "$pe_list" "$daemon_unit"; then
        _warn "watchdog hold: the hold marker could NOT be written (${marker}) — restoring the held timer(s) immediately (a hold without a record cannot be undone by the RT)"
        _watchdog_hold_restore
        return 1
    fi
    WATCHDOG_HELD_TIMERS="$held"
    WATCHDOG_HOLD_ARMED=1
    journal_history_append watchdog_hold "watchdog hold applied: timers=[$held] prior_active=$pa_list prior_unit_file_state=$pe_list daemon_unit=${daemon_unit:-none} restore_transient=${WATCHDOG_RT_UNIT:-none} run_id=$run_id (stopped, never disabled — an enabled timer is re-armed by systemd at boot)" \
        || _warn "watchdog_hold journal append FAILED (best-effort)"
    return 0
}

# _watchdog_hold_restore — RESTORE the held timer(s) + cancel the restore
# transient. IDEMPOTENT: a missing marker ⇒ already restored (no-op, rc 0).
# Called on EVERY terminal path (P4 commit / P5 abort-pre-flip / P6 halt)
# and by the EXIT trap (the belt that covers the paths nobody enumerated).
_watchdog_hold_restore() {
    local marker timers rt_unit timer rc=0 restored=""
    marker="$(_watchdog_hold_marker_path)"
    WATCHDOG_RESTORE_DETAIL=""
    if [ ! -f "$marker" ]; then
        WATCHDOG_RESTORE_DETAIL="no hold marker — nothing to restore"
        return 0
    fi
    timers="$(_watchdog_marker_field "$marker" timers)" || timers=""
    rt_unit="$(_watchdog_marker_field "$marker" rt_unit)" || rt_unit=""
    for timer in $timers; do
        if _run_bounded "$WATCHDOG_PROBE_READ_BUDGET_S" -- "$SYSTEMCTL_BIN" start "$timer" >/dev/null 2>&1; then
            restored="${restored}${restored:+ }$timer"
        else
            rc=1
            _warn "watchdog restore: could NOT re-arm timer $timer — the watchdog stays down until an operator re-arms it (loud, never silent)"
        fi
    done
    # cancel the restore transient (it has nothing left to restore)
    if [ -n "$rt_unit" ]; then
        _run_bounded "$WATCHDOG_PROBE_READ_BUDGET_S" -- "$SYSTEMCTL_BIN" stop "$rt_unit.timer" >/dev/null 2>&1 || true
    fi
    rm -f "$marker" 2>/dev/null || true
    WATCHDOG_HOLD_ARMED=0
    WATCHDOG_RESTORE_DETAIL="restored=[${restored:-none}] rt_cancelled=${rt_unit:-none}"
    journal_history_append watchdog_restore "watchdog restore: timers=[${restored:-none}] restore_transient_cancelled=${rt_unit:-none} (the held timer(s) are re-armed; the marker is cleared)" \
        || _warn "watchdog_restore journal append FAILED (best-effort)"
    return "$rc"
}

# _watchdog_hold_release_if_armed — the EXIT-trap belt: release the hold on
# ANY exit path (including the ones nobody enumerated). No-op when no hold
# was applied (the flag is process-local — the hold state is the flag, not
# the marker).
_watchdog_hold_release_if_armed() {
    [ "${WATCHDOG_HOLD_ARMED:-0}" = "1" ] || return 0
    WATCHDOG_HOLD_ARMED=0
    _watchdog_hold_restore >/dev/null 2>&1 || true
}

# _watchdog_rt_rearm <run_id> <iteration> — the RT's DEFER path: re-arm
# itself (bounded by WATCHDOG_HOLD_MAX_ITERATIONS — design F13).
_watchdog_rt_rearm() {
    local run_id="$1" iteration="$2"
    local next=$(( iteration + 1 ))
    local unit="ensemble-keepdog-restore-$run_id-$next"
    local libpath ttl
    libpath="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)/$(basename "${BASH_SOURCE[0]:-$0}")"
    [ -f "$libpath" ] || libpath="${BASH_SOURCE[0]:-$0}"
    ttl="$WATCHDOG_HOLD_RT_TTL_S"
    if ! command -v "$SYSTEMD_RUN_BIN" >/dev/null 2>&1; then
        return 1
    fi
    _run_bounded "$WATCHDOG_PROBE_READ_BUDGET_S" -- "$SYSTEMD_RUN_BIN" \
        --on-active="$ttl" \
        --unit="$unit" \
        --description="ensemble watchdog restore transient (run $run_id, iteration $next)" \
        --setenv=_LIB_WATCHDOG_RT_ENTRY=1 \
        --setenv=INSTALL_DIR="${INSTALL_DIR:-}" \
        --setenv=UP_TARGET="${UP_TARGET:-demo}" \
        --setenv=WATCHDOG_RT_RUN_ID="$run_id" \
        --setenv=WATCHDOG_RT_ITERATION="$next" \
        --setenv=WATCHDOG_HOLD_RT_TTL_S="$ttl" \
        --setenv=WATCHDOG_HOLD_MAX_ITERATIONS="$WATCHDOG_HOLD_MAX_ITERATIONS" \
        -- bash "$libpath" --watchdog-restore >/dev/null 2>&1
}

# _watchdog_rt_main — the restore transient's ENTRY POINT (design §2.3 P7):
# the executor died ⇒ the RT fires at its TTL ⇒ re-read the journal:
#   * an OPEN in_flight ⇒ the ceremony may still be running ⇒ re-arm the RT
#     (bounded ≤ WATCHDOG_HOLD_MAX_ITERATIONS) UNLESS the daemon is down
#     and no fresh executor heartbeat proves the executor alive (the F13
#     darkness bound: restore as soon as the unit is inactive|failed ∧ no
#     fresh executor heartbeat);
#   * no open txn ⇒ RESTORE.
_watchdog_rt_main() {
    local iteration="${WATCHDOG_RT_ITERATION:-1}"
    local run_id="${WATCHDOG_RT_RUN_ID:-}"
    local marker timers daemon_unit inf="" open=0 hb_stale unit_state
    marker="$(_watchdog_hold_marker_path)"
    if [ ! -f "$marker" ]; then
        _warn "watchdog restore transient: no hold marker — nothing to restore (the ceremony already restored the timer(s))"
        return 0
    fi
    timers="$(_watchdog_marker_field "$marker" timers)" || timers=""
    daemon_unit="$(_watchdog_marker_field "$marker" daemon_unit)" || daemon_unit=""
    inf="$(_json_sub "$(journal_read 2>/dev/null || printf '{}')" in_flight 2>/dev/null)" || inf=""
    case "$inf" in ''|null) open=0 ;; *) open=1 ;; esac
    if [ "$open" = "1" ]; then
        hb_stale="$(_txn_heartbeat_stale "$inf" "$(_now_epoch)")"
        unit_state=""
        if [ -n "$daemon_unit" ]; then
            unit_state="$(_watchdog_unit_state "$daemon_unit")" || unit_state=""
        fi
        if [ "$iteration" -ge "$WATCHDOG_HOLD_MAX_ITERATIONS" ]; then
            _warn "watchdog restore transient: iteration $iteration reached the bound ($WATCHDOG_HOLD_MAX_ITERATIONS) — restoring the held timer(s) now (the hold is bounded; a longer hold is the F13 darkness defect)"
            _watchdog_hold_restore
            return 0
        fi
        case "$unit_state" in
            inactive|failed)
                if [ "$hb_stale" = "1" ]; then
                    _warn "watchdog restore transient: the daemon unit is $unit_state and no fresh executor heartbeat proves the ceremony alive — restoring the held timer(s) now (the hold is bounded by daemon liveness)"
                    _watchdog_hold_restore
                    return 0
                fi
                ;;
            '')
                if [ "$hb_stale" = "1" ]; then
                    _warn "watchdog restore transient: the daemon unit state is unprobeable and no fresh executor heartbeat proves the ceremony alive — restoring the held timer(s) now (the hold is bounded by daemon liveness)"
                    _watchdog_hold_restore
                    return 0
                fi
                ;;
        esac
        # still open + the daemon is alive (or the executor heartbeat is fresh)
        # ⇒ DEFER: re-arm ourselves, bounded.
        journal_history_append watchdog_hold_deferred "watchdog hold deferred: iteration=$iteration of $WATCHDOG_HOLD_MAX_ITERATIONS; the txn is still open (unit=${daemon_unit:-none} state=${unit_state:-unknown} heartbeat_stale=$hb_stale) — the restore transient re-arms itself (the hold stays bounded by daemon liveness)" \
            || _warn "watchdog_hold_deferred journal append FAILED (best-effort)"
        if ! _watchdog_rt_rearm "$run_id" "$iteration"; then
            _warn "watchdog restore transient: could NOT re-arm itself — restoring the held timer(s) now (never leave the hold unbounded)"
            _watchdog_hold_restore
            return 0
        fi
        return 0
    fi
    # no open txn ⇒ the ceremony is over ⇒ restore
    _watchdog_hold_restore
    return 0
}

# ── D2 RT entry point (the LAST statement of this file) ─────────────────────
# The restore transient runs `bash <lib.sh> --watchdog-restore` with
# _LIB_WATCHDOG_RT_ENTRY=1 — the guard below is the ONLY path that reaches
# the RT's decision logic. Every other consumer SOURCES this file, and the
# guard is inert without the env var.
if [ "${_LIB_WATCHDOG_RT_ENTRY:-0}" = "1" ]; then
    _watchdog_rt_main "$@"
    exit $?
fi

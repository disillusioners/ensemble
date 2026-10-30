# Upgrade-Overhaul Watch Runbook — Maiden Promote (v0.20.1)

- **Version:** 1.0 @ 2026-10-12
- **Mission branch:** `feature/upgrade-speed-overhaul` @ `60eeaec39` (post-version-bump)
- **Canonical path:** `docs/runbooks/upgrade-overhaul-watch.md` — this file (sibling of `upgrade-drills.md`)
- **Scope:** the **§7 watch entry** for the v0.20.1 maiden promote per phase4-plan D-P4.6 + risk-register §2. Operator-facing.
- **Related docs:**
  - `docs/runbooks/upgrade-drills.md` (canonical DRILL runbook; §0–§9 + appendices)
  - `.agents/shared/planning/upgrade-speed-overhaul/plan-overview.md` §6 (vehicle, maiden-promote mode)
  - `.agents/shared/planning/upgrade-speed-overhaul/risk-register.md` §2 (operator runbook entry contract)
  - `.agents/shared/planning/upgrade-speed-overhaul/decisions-resolution-2026-10-12.md` (P-Dx matrix)
  - `.agents/shared/planning/upgrade-speed-overhaul/maiden-promote-preflight-2026-10-12.md` (the preflight checklist)

**Fast mode is the maiden-promote default** (D-P4.5 / Q6b resolved). `ENSEMBLE_SOAK_CONSERVATIVE=1` is the **single sanctioned reversion knob** (per risk-register §2 change-rollback plan #1).

---

## §A. Pre-ceremony

**A.1 Drill evidence link.** Cite the `tests/measurements/aggregator-*.log` from the most recent `tests/test_upgrade_overhaul_all.sh` run (the default fast subset — see T4.2). PASS=11 / FAIL=1 / SKIP=1 in the P4 run; the single FAIL is `test_launcher.sh` — **pre-existing GNU-host fixture debt** (BSD-only `date -ju -v` calls in the sweep fixture), NOT a regression from the overhaul (verified against `a2997357b~1` baseline). The `--full` flag adds `test_release_journal.sh` (34 pre-existing failures, all in BSD-date-debt sweep fixtures; pre-P1 baseline was 286 failures — the delta is from our work).

**A.2 Conservative-vs-fast decision.** Recorded: **FAST** (the default-mode behavior — adaptive 60–120 s soak @ 15 s cadence, 3 consec greens, 900 s ceiling). Conservative mode (`ENSEMBLE_SOAK_CONSERVATIVE=1` or `ENSEMBLE_PROMOTE_SOAK_S=300`) is available as the **env-only escape hatch**. Per the project's standing `upgrade_workflow_preference` policy ("PREFER SPEED OVER MAXIMAL SAFETY", effective from next-upgrade-after-2026-10-01 v0.16.8 promote), the default IS fast.

**A.3 ENSEMBLE_BINARY_VERSION naming check.** Confirmed: `VERSION="v0.20.1"` resolves cleanly via `stage.sh:361` (`ENSEMBLE_BINARY_VERSION:-${VERSION#v}` → `0.20.1`). The stage manifest's `binary_version` matches `daemon/__init__.py __version__ = "0.20.1"` (version-verify fails-closed at promote per `T-PIN-failclosed`). **Re-staging the SAME version requires an explicit `ENSEMBLE_BINARY_VERSION` override** (vehicle-naming interaction per risk-register §2 #2).

**A.4 Vehicle reconcile (C-12).** Verified: `feature/checkpoint-conn-resilience` is on `c8a3fbf6` (the v0.20.1 base). Specifically, commit `c8b3b09f2` ("fix(checkpoint): pool-backed saver + retry proxy + readiness sentinel probe") is an ancestor of `c8a3fbf6` (confirmed via `git merge-base --is-ancestor c8b3b09f2 c8a3fbf6` → exits 0). The pool-backed `daemon/persistence.py` + retry-wrapper `daemon/checkpoint_adapter.py` + readiness sentinel probe ride the v0.20.1 vehicle as-is. **No separate merge commit required.**

**A.5 Sandbox / install-dir stage verify.** Production-stage v0.20.1 in a `mktemp -d` `INSTALL_DIR` with `--allow-stale-stage` + `ENSEMBLE_ROLLBACK_SAFE=1` + `ENSEMBLE_BINARY_VERSION=0.20.1`. Manifest fields: `version=v0.20.1`, `binary_version=0.20.1`, `rollback_safe=true` (source=explicit=true; the migration delta between previous release and v0.20.1 has destructive DDL — `contains_contract_phase=true` — so explicit confirmation was required), `known_schema_gen=20261011_102502_set_checkpoint_trio_autovacuum_reloptions.sql`. Quarantine state: clean (no `quarantined[]` entries in `state.json`). No `current` symlink repoint (stage does not flip — promote.sh owns that).

---

## §B. During the ceremony

**B.1 Journal tail command.** (operator console):

```bash
INSTALL_DIR=~/agents-ensemble bash -c '
    J="$(journal_path)"
    tail -F "$J" | jq -c "select(.event != null) | {ts,event,detail}"
'
```

(or any equivalent — `tail -F` + a JSON line formatter; the journal is line-delimited JSON entries appended by the splice helper).

**B.2 Expected event sequence** (the canonical happy-path arc — all events under `journal_*` history key in `state.json`):

1. `python_floor_probe` (interpreter stamp)
2. `commit_block` writes open-txn (armed_at, kind=promote, target=v0.20.1, owner_pid=$$)
3. `integrity_check` (CURRENT + TARGET manifest verify)
4. `livez` probe greens (HTTP 200)
5. `readyz` probe greens (HTTP 200)
6. `version_check` (gate_version matches `0.20.1`)
7. `soak` greens (adaptive 60–120 s @ 15 s cadence, 3 consec greens required for early-exit)
8. `commit` (committed_at, current=v0.20.1, previous=$OLD_CUR — captured at flip-prep moment; per decision (g))
9. `committed` (no-op tail event for ledger readability)

**B.3 Budget-breach routing.** On any phase boundary overrun, the `_enforce_deadline` helper journals:

```
deadline-breach-<phase> (elapsed=<X>s past <deadline>) daemon-state=<live|stopped|handed-back>
```

…then releases the lock + exits 78. The halt routes to the **existing halt machinery** at `promote.sh:459-496`. The breach does **NOT** trigger auto-rollback (auto-rollback is for gate-fail halts, NOT ceremony timing halts — per decision (d)). Operator: do NOT intervene mid-rollback; budget-breach = terminal-within-budget.

**B.3a TWO breach classes — never read exit-78 alone** (D2-12/T2.20/AC20,
promote-preflight-hang phase 2, folded from the P2 runbook deferral):

| Class | Exit | Journal row | Producer |
|---|---|---|---|
| (i) shell watchdog breach | **78** | kind `halt`, detail carries the **`deadline-breach-<step>` (budget=… value=… elapsed=… phase=…) daemon-state=…** prefix | the L2a `_budget_breach` handler / the L1 `_enforce_deadline` checkpoints |
| (ii) L3 systemd kill | **143** (SIGTERM) | kind `halt`, detail **WITHOUT** the prefix (the TERM trap journals "killed by SIGTERM") | the transient unit's derived `RuntimeMaxSec=` backstop |

**The discriminator is the JOURNAL ROW KIND + the `deadline-breach-` prefix,
NEVER the exit code** — 78 already spans two semantics (`refusal` vs `halt`),
so no operator surface may read exit-78 alone. First step on a suspected
class-(ii) kill:

```bash
systemctl show ensemble-upgrade-<run_id> -p RuntimeUSec -p RuntimeMaxUSec
```

(`RuntimeMaxSec` is derived at arm time — `max(1200, PROMOTE_TOTAL_DEADLINE_S
+ SOAK_S + 120)`; a runtime AT or above it means systemd fired, not the
shell.) Both producers' rows carry `daemon-state=` (W3/D2-23) — one reader,
one runbook, both shapes.

**B.4 C-10b post-stop breach first-recovery-step.** If the halt detail shows **`daemon-state=stopped`** (i.e. the breach fired DURING or AFTER the `stop` phase), the operator's first action is:

```bash
INSTALL_DIR=~/agents-ensemble bash -c '
    # 1. Verify current is null (no flip recorded)
    J="$(journal_read)"
    CUR="$(_json_field_quoted "$J" "\"current\":" 2>/dev/null)" || CUR="null"
    if [ "$CUR" != "null" ]; then
        echo "WARNING: current=$CUR — flip already recorded; investigate before re-promote"
        exit 1
    fi
    # 2. Restart current — DERIVED-scope systemd form (agent-restart rev-5:
    # the launcher takes NO subcommands — 'restart_current' does NOT exist;
    # running bare launcher.sh would enter the foreground supervision loop
    # AND create a second supervisor against the systemd-managed unit. The
    # live host classifies SYSTEM scope: /system.slice/ensemble-main.service)
    sudo systemctl restart ensemble-main.service  # user-scope host: systemctl --user restart <unit>
'
```

**NEVER skip-rollback to a fresh promote** — a post-stop breach leaves the daemon DOWN; the next promote would attempt an atomic flip on a still-stale `previous`, re-introducing the N-2 walk bug the overhaul fixed.

---

## §C. Post-ceremony — the four C-11 controls + §7 watch

### §C.1 — Control 1 (mandatory): Post-promote real-message smoke

The 2026-10-10 outage proved that **`/readyz` returning green can hide a dead checkpoint layer** (the livez/readyz gates pass without exercising the checkpoint saver — a regression in `daemon/persistence.py` or `daemon/checkpoint_adapter.py` would not surface until the first agent instance messages through the API). Per the runbook contract, the operator MUST send **one real instance message** end-to-end after the promote completes and verify the corresponding job lands in `DONE`:

```bash
# Create one real instance (any agent — leader is the canonical default)
curl -X POST http://localhost:9797/api/instances \
    -H "Content-Type: application/json" \
    -d '{"agent_id":"leader", "project_id":"83da04de-a410-4fb5-9e92-251a99d28a52"}' \
    | jq '.instance_id'

# Send one real message (substitute the instance_id from the response)
INSTANCE_ID="<from above>"
curl -X POST "http://localhost:9797/api/instances/${INSTANCE_ID}/messages" \
    -H "Content-Type: application/json" \
    -d '{"content": "smoke: post-v0.20.1-promote checkpoint-path verification", "role": "user"}'

# Poll the job completion (timeout 30s; expect DONE within 10s for a leader smoke)
sleep 10
curl "http://localhost:9797/api/jobs?instance_id=${INSTANCE_ID}&limit=1" \
    | jq '.[] | {job_id, status, result_summary}'
# EXPECT: status=DONE (or COMPLETED); if HALTED / FAILED — investigate the
# checkpoint layer FIRST (per the 2026-10-10 lesson) before declaring success.
```

This smoke exercises the checkpoint-saver path that the gates alone don't cover.

### §C.2 — Control 2: First destructive blob-prune drain watch

v0.20.0 shipped `blob_prune_dry_run=False` (destructive default-on) + the `VacuumMaintenanceJob` + the trio autovacuum reloptions. **The first destructive drain cycle has NOT yet run** (24 h cadence; v0.20.0 was just LIVE-promoted 2026-10-12 07:00Z per the `deferred_feature_adoptions` metadata). v0.20.1 inherits this window — the operator MUST monitor the first 24-hour window post-promote:

```bash
# Watch the vacuum-maintenance job cadence + outcome
journal=/home/nea/agents-ensemble/releases/state.json
# After 24 h: count destructive prune events in the journal history
jq -r '.history[] | select(.event | test("blob_prune|prune_destructive|vacuum_maintenance")) | .ts' "$journal" \
    | head -5
# EXPECT: ≥1 destructive_prune event within 24h of promote.
# If absent: investigate the VacuumMaintenanceJob activation + daemon-side
# readiness probe (per v0.20.0 incident cn class).
```

The watch ownership is documented elsewhere (`docs/runbooks/checkpoint-blob-prune-restore.md` §7) — the v0.20.1 watch ride shares the same observation surface.

### §C.3 — Control 3: No-key-rotation window

Key rotation **stays deferred** (per the standing `deferred_key_rotation` critical note + the 2026-10-03 correction: the OD daemon's `~/.config/opendesign-daemon/env` holds a PHYSICAL `OPENAI_API_KEY` copy that the env-ref bridge does NOT propagate to). The maiden promote does NOT rotate any keys. Window remains open until user-initiated.

**If the operator must rotate mid-window** (emergent security reason, NOT routine):

1. Mint new proxy key; update ensemble `.env` (env-ref bridge propagates to OD lane automatically — the OPENAI bridge).
2. Update `~/.config/opendesign-daemon/env` (the physical key copy) + `systemctl --user restart opendesign-daemon` — the OD daemon does NOT see the env-ref change.
3. Rotate dev `SYSTEM_ENCRYPTION_KEY` (may orphan dev-KMS test data — acceptable; the remote_parity_2026_10_05 coupling requires re-encrypting all `db_connections` Fernet credentials under the new key or the external-conn registry breaks).

Do NOT rotate during the ceremony window (between arm and disarm) — the OD lane would see a key mid-write and could strand the openai proxy lane.

### §C.4 — Control 4: Transient indicator list

The journal halt/handback events now carry `deadline-breach-<phase>` reason tokens (C-10c) + `daemon-state` fields (C-10b). The daemon-side transient indicators (per decision (e) C-8b — busy-amnesty, retry/backoff) emit at the livez/readyz probe site. Operator distinguishes transient (retry-green) from hard failure (halt):

**Transient indicators (retry-green — DO NOT intervene):**

| Indicator | Where | Meaning | Action |
|-----------|-------|---------|--------|
| `transient probe classification` | `/var/log/ensemble/upgrade.log` (or captured `_probe_once_classified` stderr) | curl exit 7/22/28 OR 503 within 1.5 s window — counted as "no green", does not fail the phase | Wait for next probe; 3 retries within budget |
| `inflight_turns ≤ 5` | `/readyz` body `detail.inflight_turns` | busy but alive (BUSY_INFLIGHT_BOUND=5) | Busy-amnesty — gate greens; no operator action |
| `consecutive_green=2/3` (soak) | journal events `soak_progress` (when DEBUG) | mid-soak, 2 greens at 15 s cadence | Wait for the 3rd green; no operator action |

**Hard-failure indicators (halt — operator action required):**

| Indicator | Where | Meaning | Action |
|-----------|-------|---------|--------|
| `halt` event with `deadline-breach-<phase>` | journal `history[]` | ceremony wall-clock ceiling exceeded | See §B.4 above |
| `soak` event with `consecutive_green < 3` at cap | journal `history[]` | cap-reach-without-3-greens (C-1 corrected) — never-green commit bug | Auto-rollback route already firing; verify rollback in §D |
| `deadline-breach-stop` (`daemon-state=stopped`) | journal `history[]` + halt event | post-stop breach — daemon DOWN | First-recovery-step: `verify/restart current` BEFORE re-promote (§B.4) |
| `halt` event with `immediacy breach at commit: journal current=N ≠ $VERSION` | journal `history[]` | commit-time integrity breach — journal drift | **Halt for human** — do NOT auto-recover; investigate journal divergence FIRST |

---

## §D. Incident paths

### §D.1 Halt→re-promote recognition

If a prior promote halted (gate-fail or budget-breach) and a fresh promote starts, the new promote reads `state.json` and applies the existing rollback handling (`promote.sh:459-496`):

- **gate-fail**: auto-rollback to N-1 (per the AC4 + AC5 matrix; the P1 P1 fix lands `$OLD_CUR` capture-at-flip-prep, NOT stale journal `previous` read)
- **budget-breach**: halt-for-human (per §B.3)

The matrix ensures `previous == N-1` always at the commit boundary. **Operators do NOT need to manually roll back** between halt and re-promote — the machinery does it.

### §D.2 boot_sweep commit-and-continue recognition

On daemon restart post-flip, the launcher `boot_sweep_commit_and_continue` path (`launcher.sh:1020-1037`) reads `in_flight.displaced_version` (stamped at `journal_open_txn` time, per C-4 + decision (g)) and writes `previous = displaced_version`. If the prior in_flight was already a `commit_and_continue` candidate (per the AC5 cell 3 pin), the launcher completes the commit on next boot.

**Recognition**: a `boot_sweep_commit_and_continue` event in the journal `history[]`. **Operator**: do NOT re-run promote — the flip is already recorded; re-promoting would attempt another atomic flip on a (committed) mis-match and strand the daemon.

### §D.3 Quarantine re-stage procedure

If staging fails or refuses (any of the explicit refusal text or the ENSEMBLE_RUNTIME=stage-fail exit-78):

1. Inspect the staging journal: `INSTALL_DIR/releases/state.json` `quarantined[]` field — note the version token.
2. The version is quarantined (per `stage.sh:616-618`); **quarantine clears ONLY via re-stage** — there is no manual quarantine edit (fail-closed by design).
3. Re-stage: either bump the version (so the manifest is genuinely new), or set `ENSEMBLE_BINARY_VERSION` explicitly to force a fresh binary identity (the version-naming interaction per risk-register §2 #2).
4. After successful re-stage, `state.json.quarantined[]` is cleared automatically by the stage's `journal_quarantine_clear` call (`stage.sh:618`).

---

## §E. Conservative-mode escape (the single sanctioned reversion)

If the operator wants to revert the maiden promote to the v0.18-era flat-wait behavior (300 s, no early exit, 30 s cadence — bit-identical to pre-overhaul), set:

```bash
# Per-shell:
export ENSEMBLE_SOAK_CONSERVATIVE=1

# Per-promote (one-shot):
ENSEMBLE_SOAK_CONSERVATIVE=1 bash scripts/upgrade/promote.sh demo --version v0.20.1
```

**This is the FIRST response to any "too fast, want the old ceremony back" report.** No code revert, no re-stage, no daemon restart required. The env var is read at gate_soak invocation (`lib.sh:4926-4928`); the next promote's gate_soak reverts to bit-identical-v0.18 behavior.

`ENSEMBLE_PROMOTE_SOAK_S=300` is the alternative escape (sets soak to 300 s explicitly, but the cadence stays at the default 30 s in conservative mode). Either knob reverts to bit-identical behavior.

No other budget knob is part of the escape — every other knob is locked by the C-10a / C-10b identity table (test-strategy.md §1 row T-AC2b).

---

## §F. Ceremony scripts provenance (promote-preflight-hang phase 6)

The upgrade pipeline's SHELL surface (promote.sh/stage.sh/lib.sh/…) is NOT
shipped inside release payloads (payloads have carried no `scripts/` since
v0.27.4) — the daemon resolves a **scripts dir** at ARM time:
`ENSEMBLE_UPGRADE_SCRIPTS_DIR` → the module repo → `<install>/scripts/upgrade`.
**Deployment truth (measured 2026-10-29, architect RULING 9):** the live
`.env` pins `ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src/scripts/upgrade`
and that checkout was **277 commits behind** the lane base — its `lib.sh`
still carried the O(N²) journal walk (the 2026-10-28 preflight hang). Without
a sync, the next ceremony would run the OLD code with the watchdog absent.

### §F.1 The arm-time guard (what the daemon now enforces)

At ARM time, BEFORE the ceremony nonce burns, the daemon reads
`<scripts_dir>/lib.sh` (bounded first-64KiB read) for the
`PIPELINE_SCRIPTS_REV` stamp and judges the dir:

| Pinned-dir state | Verdict |
|---|---|
| git dir, HEAD contains `PIPELINE_SCRIPTS_FLOOR_COMMIT` as an ancestor (`git -C <dir> merge-base --is-ancestor <floor> HEAD`) | **arm** (however new the tree is) |
| git dir, floor NOT an ancestor / floor object missing-unknown / git error | **refuse** — pre-nonce, refusal names the sync command |
| git dir whose lib.sh carries no stamp (tree predates the fix) | **refuse** |
| floor constant unset in the daemon build | **refuse** on an explicitly pinned dir (fail-closed — a mis-made release becomes visible instead of silently skipping the guard); warn on a fallback dir |
| NON-git dir, with stamp | **arm** (stamp + sha256 are the provenance) |
| NON-git dir, no stamp | **warn only — never refused** (Q2) |
| any dir + `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` | allowed, **journaled loudly** |

Every arm journals an `arm-scripts-provenance` row carrying `scripts_dir`,
the `lib.sh` sha256, the git rev and the dirty flag — plus a
`stale-scripts-dir` refusal row when it refuses.

### §F.2 USER-OWNED PRECONDITION (before the next ceremony)

Either **(a) sync the pinned checkout** so its HEAD contains
`PIPELINE_SCRIPTS_FLOOR_COMMIT` as an ANCESTOR — `git -C <dir> fetch origin
&& git -C <dir> checkout <a-floor-descendant>` (git SHAs are UNORDERED: the
predicate is ancestry, never a "≥ rev" comparison) — or **(b) move the
`ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin** to a dir that passes the same predicate.
The lane itself does NOT touch the shared checkout (it carries another lane's
WIP; cross-lane WIP-owner precedent) or the live `.env`. If the precondition
is unmet the ceremony **refuses at arm time and NO ceremony is consumed** —
the loud failure mode, by design.

### §F.3 Checklist items

- [ ] **ceremony scripts provenance verified — the arm row shows the expected
      stamp** (`arm-scripts-provenance` in `releases/state.json` history).
- [ ] exit-code discipline (§B.3a): classify by journal row, never exit-78
      alone; on a suspected L3 kill run the `RuntimeMaxUSec` show step.
- [ ] `ENSEMBLE_ALLOW_STALE_SCRIPTS` is **per-invocation only** — never write
      it to `.env` or any durable store (the arm journals its use loudly).

### §F.4 Staged-verification split (W5)

The **daemon-side** guard surface is verified from the **staged artifact**
(the binary carries the guard); the **shell** surface (stamp, watchdog
token) is verified from the **pinned checkout at the synced rev** — payloads
ship no `scripts/`. The arm row's REQUIRED provenance fields are BOTH the
git rev AND the `lib.sh` sha256; a missing field rejects the row.

## §G. Operator-visible contract changes in the promote-preflight-hang lane

### §G.1 `status.sh` exit codes (A6)

`status.sh` (read-only, no lock) **exits 1 — not 0 — when its journal read is
refused or over-budget** (the size-guard / torn-journal class). This is
deliberate (D2-6/SC4b: a diagnostic tool never files itself as a pipeline
refusal, so 78 is never used, and it never mutates the journal), but it IS a
contract change for anything scripting `status.sh`:

| mode | journal readable | journal refused / over-budget |
|---|---|---|
| plain `status.sh` | rc 0 | **rc 1** |
| `status.sh --verify` | rc 0 (all checks pass) | **rc 1** (the verify result is folded in at the same rc) |

There is no in-repo consumer of `status.sh`'s rc today; external wrappers
should treat 1 as "diagnostics incomplete" and read the WARN line, not as
"the daemon is down".

### §G.2 Per-run log family — FOUR names (A2)

One ceremony run writes, in `$INSTALL_DIR/data/upgrade/<env>/`:

| file | writer | content |
|---|---|---|
| `<run_id>.log` | the shell (`ENSEMBLE_UPGRADE_LOG_FILE`) | the narrative (markers, WARNs, breach lines) |
| `<run_id>.payload.log` | systemd (`StandardOutput=append:`) | the payload's raw stdout |
| `<run_id>.client.log` | the daemon (`spawn_executor`) | the `systemd-run` CLIENT's stderr (signature triage) |
| `<run_id>.payload.client.log` | **pre-A2 daemon builds only** | legacy name of the row above |

Retention keeps 20 newest per directory (+ a 14-day cap) and fences the
current run's **whole family**, all four names, plus the in-flight run's and
the last pointer line's files, plus anything modified within 6 h. Since A2
the writer uses `<run_id>.client.log`; the legacy fourth name stays fenced so
a run started by an older build is not retro-deleted mid-ceremony.

### §G.3 Supervision probe on a wedged bus (W3)

`_df_systemctl`'s budget breach is **kind-scoped**:

- phase `promote:*` (or an un-phased caller) ⇒ halt row + exit 78 (unchanged);
- any other phase (`restart:*`, `rollback:*`, `stop:*`, `stage:*`) ⇒ ONE
  WARN, the probe returns "unknown", the DUAL_FIGHT check degrades to
  "cannot classify" and the RECOVERY script continues (R1).

An operator seeing `supervision probe ... exceeded SYSTEMCTL_PROBE_BUDGET_S`
on a restart/rollback path should read it as "this recovery continued blind",
not as a failure — re-run the probe or `systemctl status` by hand when the
bus recovers.

---

**End of §C/§D/§E/§F watch runbook entry.**

The §0–§B pre/during sections above pair with §7 / §8 / §9 of `docs/runbooks/upgrade-drills.md` (the canonical drill runbook; this file extends the operator-facing surface for the v0.20.1 maiden promote specifically).
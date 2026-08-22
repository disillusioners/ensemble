# Phase 1 (P2.1): Release & Upgrade Pipeline

> **⛔ HARD CONSTRAINT (user directive — VERBATIM, applies throughout this phase):**
> NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.

**ADR basis:** ADR-004 (release layout), ADR-005 (auto-rollback gate + cap), ADR-009 (stage/promote/rollback, SIGTERM-bounded), ADR-012 (launcher journal sweep) — as reconciled with Phase-2 deviations #4/#5 (see `plan-overview.md` §6).

**Scope of environment:** all implementation work targets **demo** (`~/agents-ensemble-demo`, :7979, `ensemble_demo`) and **sandboxes** (own port + throwaway PG). Live target paths exist in the scripts behind guards but their execution is **USER-GATED** and never performed by this initiative.

---

## Objective

Deliver a deterministic, health-gated release/upgrade pipeline for staged installs: build → integrity-check → stage as a release trio → promote with atomic flip and health gate → commit or auto-rollback (manifest-gated, cooldown, cap 3/24h) — plus the ADR-012 launcher journal sweep that makes orphaned promote transactions self-healing. Everything verifiable end-to-end on demo/sandbox with no operator improvisation.

**Exit in one sentence:** on demo, `upgrade promote VERSION=<current-demo-version>` runs a full cycle (stage → stop → flip → start → gate → commit) AND an induced gate-failure rolls back automatically to the previous release with the journal recording quarantine + counters — all proven by journal reads + probe transcripts, zero live contact.

---

## Verified Starting Point (do not re-derive)

- `launcher.sh` binary resolution **already prefers** `$INSTALL_DIR/current/ensemble-prod`, falls back to flat `$INSTALL_DIR/ensemble-prod` (launcher.sh:349-374) — the `releases/` seam is ready; no launcher change needed for resolution.
- `launcher.sh` journal-sweep hook exists as a **stub with the Phase-3 contract written in comments** (launcher.sh:151-174, called at :567-568 before binary resolution) — implement exactly that contract.
- `launcher.sh` env: exports `INSTALL_DIR/.env` before exec; wins over binary (launcher.sh:95-149, 561-565). Exit map 0/75/78/1 + burst budget + backoff already shipped and unit-tested (74/74 pack).
- `scripts/deploy.sh` is the **bootstrap** path: bare PyInstaller build (deploy.sh:195-197), flat staging with unbacked `rm -rf agents/` + `frontend/dist` (deploy.sh:276, 282), health gates `/livez` ≤60s + `/readyz` ≤120s with `_probe` (2s sleep, curl max-time 5s), live guard exit 78 without `ENSEMBLE_DEPLOY_LIVE=1` (deploy.sh:139-148). **No releases/, manifest, journal, rollback, or backup logic exists in the repo.**
- `scripts/stop-ensemble.sh`: ownership-scoped SINGLE-TERM stop, `WAIT_S` precedence env > `DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS`+10 > 70, clamp 10..600 — **reused, never duplicated** (deploy.sh Phase-3 precedent).
- `/livez` returns `version` (daemon/api.py:1719-1733) — a version source with zero new surface. **No `--version` CLI flag exists.**
- Makefile has no stage/promote/rollback; deploy.sh deliberately avoids make targets on feature branches (ensure-latest hazard, deploy.sh:19-22).

---

## Design Decisions (this phase)

**D1 — Pipeline is env-parameterized scripts, not make targets (deviation #4).** New `scripts/upgrade/` suite: `stage.sh`, `promote.sh`, `rollback.sh`, `status.sh` (+ shared `lib.sh`). Every script takes `TARGET=demo|live|sandbox` (env or `$1`), derives INSTALL_DIR/port/DB per the 3-env topology (demo=`~/agents-ensemble-demo`/7979/`ensemble_demo`; live=`~/agents-ensemble`/9797/`ensemble_prod`; sandbox=explicit `INSTALL_DIR` + `PORT` + `POSTGRES_DB` overrides). Live requires the same explicit operator confirmation class as deploy.sh (`ENSEMBLE_UPGRADE_LIVE=1`, exit 78 otherwise — mirroring deploy.sh:139-148). **Out of scope: remote "fetch" — the pipeline builds from a LOCAL checkout only** (ADR-009 D3 rationale: explicit `VERSION`, fail-if-not-at-tag, no auto `git pull`, no network fetch in the release path; scope item 1's "fetch" = local resolution of an explicit version, not remote artifact retrieval). Optional `Makefile` targets become **thin wrappers only** (`upgrade-stage` → `bash scripts/upgrade/stage.sh`); the pipeline NEVER invokes make targets itself (deploy.sh:19-22 rationale: the ensure-latest chain would yank the feature branch).

**D2 — Version check via `/livez` version field (open question Q2 resolved here).** Integrity/version verification reads the `version` field from `/livez` after restart (daemon/api.py:1719-1733) and compares to the staged manifest's `binary_version`. **Justification:** zero new daemon surface (a `--version` CLI flag would need a PyInstaller-aware code path, arg parsing in `__main__.py`, and a second version source to keep in sync); `/livez` version is already exposed and Phase-1-validated; the check runs at the same moment the gate probes run, so no extra process is needed. Trade-off accepted: the version is the daemon's self-report, not an independent measurement — mitigated by the manifest trio checksums (T3) which verify the *payload*, while `/livez` verifies the *running process*.

**D3 — Bootstrap vs staged installs are distinct modes.** `deploy.sh` remains the bootstrap (fresh install dir, flat layout). Once `stage.sh` has run once against an install dir, that dir is "staged-mode" (`releases/` exists) and ALL subsequent upgrades go through stage→promote — the flat-overwrite destructiveness (`rm -rf` unbacked, deploy.sh:276, 282) is retired for staged envs. `stage.sh` refuses to run against the live dir without the live confirmation guard; the live dir's initial migration to staged mode is **USER-GATED** (P2.3 designs it; never executed here).

**D4 — Journal is the single durable state; every mutation is atomic.** `releases/state.json` written via temp-file + `mv` (same discipline as `.launcher-state`, launcher.sh:187-247). Schema (interface sketch, not implementation code):

```jsonc
// releases/state.json — interface sketch
{
  "current": "v0.10.5",            // == target of current symlink
  "previous": "v0.10.4",           // rollback target; pinned against eviction
  "in_flight": null | {            // non-null during promote/rollback
    "kind": "promote|rollback|sweep_rollback",
    "target": "v0.10.6",
    "started_at": "2026-08-22T09:00:00Z",
    "flipped": false,              // true once current symlink moved
    "owner_pid": 12345
  },
  "rollback_window_count": { "24h": 1, "window_start": "..." },  // ADR-005 cap 3/24h
  "cooldown_until": null,          // ISO ts — promotes refused while set (10 min)
  "quarantined": ["v0.10.3"],      // skipped by future promotes
  "history": [ { "ts": "...", "event": "commit|rollback|quarantine|sweep|halt", "detail": "..." } ]
}
```

**D5 — `rollback.lock.d` — mkdir-based lock directory [CONFORMED to D-FA5.1, architecture-recommendation.md:182-184 — supersedes both the original ADR-004 m7 file-lock sketch and the earlier D5 text; reviewer-ruled canonical 2026-08-22].** `$INSTALL_DIR/releases/rollback.lock.d` (per-install-dir = per-env in this topology): **mkdir IS the atomic acquire** — portable, no `flock` (no `flock(1)` CLI on stock macOS; no repo precedent). Contents: `owner` (pid), `run_id`, `heartbeat` (epoch, rewritten ~30s by the live owner). Stale-break: heartbeat >300s → `mv` the dir to `rollback.lock.stale.<pid>` (avoids racy rmdir) → re-acquire. **The protocol — not shared code — is the contract:** implemented identically in `scripts/upgrade/lib.sh`, the Python journal module (P2.2 T4), AND `deploy.sh` gains the same acquire so manual deploys serialize with the pipeline (R-SR03). Second invocation → structured `pipeline-busy run_id=…` result, not an error. Bounded wait, never block forever on a dead owner.

**D6 — Stop is ALWAYS stop-ensemble.sh semantics.** Promote's stop step invokes `scripts/stop-ensemble.sh "$INSTALL_DIR" "$PORT"` (reused, never duplicated — deploy.sh Phase-3 precedent). **NEVER a raw kill** — SIGTERM + bounded wait is the only path; SIGKILL only as documented last resort inside stop-ensemble.sh itself. Restart is ALWAYS via `launcher.sh` (which runs the journal sweep first — T7).

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **T1** | **Create `scripts/upgrade/lib.sh`** — shared env/target resolution (demo/live/sandbox), INSTALL_DIR/port/DB table, live guard (`ENSEMBLE_UPGRADE_LIVE=1` else exit 78, mirroring deploy.sh:139-148), journal read/write helpers (atomic temp+mv), lock helpers, `_probe` reuse (2s sleep, curl max-time 5s — same budget as deploy.sh) | none | Sandbox: `TARGET=sandbox INSTALL_DIR=/tmp/ens-sbx PORT=8377 bash scripts/upgrade/status.sh` prints resolved env + exits 0; `TARGET=live` without guard exits 78 with refusal message; unit pack asserts guard matrix |
| **T2** | **Implement `stage.sh`** — build (bare PyInstaller, deploy.sh:195-197 pattern; `--skip-build` accepts a prebuilt binary path) → assemble `releases/<ver>/` trio: binary + `agents/` + `frontend/dist/` + **`launcher.sh` [ARCHITECT AMENDMENT 2026-08-22 — launcher joins the staged payload; manifest gains a `launcher_sha256`; swap launcher in the stopped window (launcher+daemon both exited post SINGLE-TERM); see architecture-recommendation.md FA4/D-FA4.1]** + `manifest.json` + `config.yaml`; compute + write manifest (ADR-004 fields: `binary_version`, `known_schema_gen`, `contains_contract_phase`, `rollback_safe`, trio checksums — sha256 per component); assert **NO `.env` inside the release dir** (ADR-014/m6 invariant); explicit `VERSION` required, fail if `git describe`/tag mismatch (ADR-009 D3: no auto pull); **stage `ENSEMBLE_SELF_ENV=<dev\|demo\|live\|sandbox>` into `INSTALL_DIR/.env` (NOT inside the release dir — m6 invariant; D-FA2.3 RATIFIED: P2.2's env self-match consumes this marker; absent marker → ALL actor tools fail closed, S-31)**; NO flip | T1 | Sandbox: after `stage.sh --skip-build`, `releases/<ver>/manifest.json` exists with all 5 field groups; `find releases/<ver> -name '.env'` empty; re-stage same version is idempotent (checksums stable); missing tag → exit 78; **`grep ENSEMBLE_SELF_ENV "$INSTALL_DIR/.env"` shows the correct value for the resolved target env (S-31 in `test-strategy.md` covers the absent-marker refusal path)** |
| **T3** | **Implement integrity checks** — verify trio checksums of the *currently pointed* release on every promote preflight + after every stage (detect drift/corruption); verify `current` symlink resolves; version smoke = `/livez` `version` vs manifest `binary_version` (D2) | T2 | Sandbox: tamper one file in a staged release → `status.sh --verify` reports checksum mismatch (exit 1, names the file); untampered → exit 0; promote preflight aborts (exit 78) on drift |
| **T4** | **Implement `promote.sh`** — full sequence: (1) preflight: lock acquire, integrity check, journal open `in_flight`, cooldown/cap check; (2) SIGTERM-bounded stop via stop-ensemble.sh; (3) atomic flip: `ln -sfn` new target to `current.new.$$` + `mv -f` (rename(2) semantics); (4) mark `flipped: true`; (5) restart via `launcher.sh`; (6) health gate: `/livez` ≤60s → `/readyz` ≤120s → version verify (D2) → 300s soak; (7) commit (journal: current/previous update, history append, retention) OR auto-rollback (T5); 10-min outer window | T1, T2, T3 | Demo: promote to same-or-newer version completes: journal shows `commit`, `/livez` version matches manifest, `previous` updated, lock released. Sandbox: kill promote mid-flip (SIGKILL the script after flip marker) → journal shows `in_flight` + `flipped: true` (T7 sweep input) |
| **T5** | **Implement auto-rollback path inside promote** — gate fail → **manifest gate first** (previous release `rollback_safe: true`? else halt-for-human + notify + journal `halt` event) → repoint `current` to previous → restart via launcher → re-gate (short form: /livez + /readyz + version) → notify (stderr/log + journal event) → quarantine failed version → cooldown 10 min (`cooldown_until`) → increment `rollback_window_count`; **cap 3/24h → halt-for-human** (journal `halt`, all promotes refused with explanatory message until window resets) | T4 | Sandbox with induced failure (bad binary or `ENSEMBLE_READINESS_FORCE_DEGRADED=1` in the NEW release's env — drill knob, readiness.py:48-67): journal shows `rollback` + `quarantine` + cooldown set; re-promote of quarantined version refused; 3rd rollback in 24h → `halt` event + subsequent promotes exit 78 with halt message; previous-unsafe (manifest `rollback_safe: false`) → halt-for-human, NO flip |
| **T6** | **Implement `rollback.sh`** (manual) — acquire lock, manifest gate on target, repoint, restart, re-gate, journal `rollback` event (counts toward cap), release lock | T4 | Sandbox: manual rollback to previous succeeds and journal counts it; rollback onto quarantined version requires `--force` + prints warning |
| **T7** | **Implement ADR-012 launcher journal sweep** (fill the stub) — `launcher.sh:_journal_sweep` (launcher.sh:151-174): journal exists + `in_flight` + `now - started_at > 600s` → if `flipped: true` → launcher executes rollback itself (repoint to `previous`, notify, journal `sweep_rollback` event counting toward the ADR-005 cap); else clear `in_flight`. Runs BEFORE binary resolution (:567-568). Bash 3.2/BSD-safe, consistent with existing launcher style; knobs stay script constants | T4 | Sandbox: journal seeded with stale `in_flight` + `flipped: true` → next `launcher.sh` start logs sweep-rollback, `current` repointed to `previous`, journal event recorded, cap incremented; stale pre-flip txn → cleared, boot proceeds; fresh (<10 min) `in_flight` → left alone (owner may still be alive); launcher pack suite extended + green |
| **T8** | **Retention + eviction** — after every commit: keep 3 newest releases, eviction order = oldest that is neither `current` nor journal `previous` (previous pinned, never evicted); previous missing (manual deletion) → auto-rollback path halts-for-human (already in T5) — add explicit check | T4 | Sandbox: stage 5 versions + promote through them → exactly 3 remain, `current` + `previous` always among survivors; eviction never removes `previous` |
| **T9** | **Makefile thin wrappers (optional, last)** — `upgrade-stage` / `upgrade-promote` / `upgrade-rollback` / `upgrade-status` → `bash scripts/upgrade/<x>.sh $(TARGET) ...)` pass-through only; **no pipeline logic in make**; document that release path never auto-pulls | T1-T6 | `make upgrade-status TARGET=sandbox ...` output byte-identical to direct script invocation; grep confirms zero git-pull in scripts/upgrade/ |
| **T10** | **Demo end-to-end validation + evidence** — full clean cycle on demo (stage → promote → commit) AND one induced-failure cycle (rollback) with journal + probe transcripts recorded to `.agents/tester/RESULTS/` (file naming per tester convention); live pids verified untouched at every checkpoint (Phase-1 §5 precedent) | T4-T8 | RESULTS file exists with: promote transcript (timestamps, gate probe outputs, version verify line), rollback transcript, journal final state, 6-checkpoint live-pid assertion; tester-verifiable without rerunning |

**USER-GATED (designed, never executed by this initiative):**
- `TARGET=live` on any pipeline script (guarded by `ENSEMBLE_UPGRADE_LIVE=1` + this initiative's ruling: live is out of bounds — execution belongs to the user after P2.3's ladder).
- Migration of the live install dir from flat/legacy (`.bak`s present) to staged mode — P2.3 documents the exact user-confirmed action sequence.

---

## Interface Sketches (design only — no implementation code)

```bash
# promote.sh — shell pseudocode (structure, not literal)
TARGET=${1:-demo}; resolve_env "$TARGET"          # lib.sh: dir/port/db/guard
require_live_guard "$TARGET"                       # live → ENSEMBLE_UPGRADE_LIVE=1 or exit 78
lock_acquire "releases/rollback.lock" --stale 300  # PID + heartbeat
journal_open_txn promote "$VERSION"                # in_flight {kind,target,started_at,flipped:false}
integrity_verify "$(readlink current)"             # trio checksums (T3)
check_cooldown_and_cap                             # journal: cooldown_until / rollback_window_count
stop_via stop-ensemble.sh "$INSTALL_DIR" "$PORT"   # NEVER raw kill (D6)
atomic_flip "releases/$VERSION"                    # current.new.$$ + mv -f; journal.flipped=true
restart_via launcher.sh                            # sweep runs first (T7)
gate: livez ≤60s; readyz ≤120s; version==manifest.binary_version; soak 300s   # D2
  → pass: journal_commit; retention_evict (T8)
  → fail: auto_rollback (T5)  # manifest gate → repoint → restart → re-gate → quarantine → cooldown → cap
lock_release
```

---

## Coupling

- **Tight with P2.2** — every agent-facing action (`system_restart`, `system_upgrade`) routes exclusively through these scripts' semantics; P2.2 adds NO parallel stop/flip logic. Journal schema (D4) is the contract: P2.2 reads `history`/`in_flight`/cap state for `release_info` and result delivery.
- **Tight with P2.3** — drills exercise exactly these scripts; the N-clean-cycle ledger (P2.3) consumes journal `history` events; the drill runbook's tempfail→respawn observation rides the launcher this phase extends (T7 touches launcher.sh).
- **Loose with `deploy.sh`** — bootstrap vs staged modes (D3); shared conventions (live guard pattern, `_probe`, bare PyInstaller) are pattern-reuse, not code coupling.
- **Independent of** daemon internals — no daemon code changes except the launcher sweep (T7, a script). ⟪SEAM: if the architect enriches the journal schema (e.g. per-project pause markers for future drain integration), extend D4's sketch before implementation — awaiting architect enrichment for the drain-initiative handoff only; P2.1 as specced is self-contained.⟫

---

## Risks (phase-specific — full register: sibling `risk-register.md`, W2)

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1.1 | Flip succeeds but daemon won't boot new release (exit 78 refuse — launcher must NOT loop) | Medium | Launcher already refuses to loop on 78; promote's gate detects unreachable → auto-rollback (T5); sweep covers orchestrator death (T7) |
| R1.2 | Rollback onto schema-drifted DB (daemon_meta absent — umbrella U1) | High | Manifest `rollback_safe` gate (M5); `known_schema_gen` recorded per release; halt-for-human on unsafe; W2 risk-register owns the register entry |
| R1.3 | Concurrent promote + launcher sweep race | Medium | `rollback.lock.d` mkdir-lock serializes (D5/D-FA5.1); sweep only acts on `in_flight` >10 min old (owner presumed dead); heartbeat refreshed by live owners |
| R1.4 | Cooldown/cap arithmetic bugs (window rollover, sweep-counted rollbacks — umbrella U6) | Medium | Journal records sweep rollbacks distinctly (`kind: sweep_rollback`); unit pack covers window rollover, cap boundary, cooldown expiry |
| R1.5 | Demo demo-drill knob leaves degraded readiness (P7: knob requires restart to clear, readiness.py:48-67) | Low | Induced-failure drill (T5 acceptance) clears knob + restarts, restoring green; P2.3 runbook documents the restore step verbatim |

---

## Exit Criterion

**All of the following, objectively verifiable:**

1. **Clean cycle on demo:** `bash scripts/upgrade/promote.sh demo` (explicit VERSION) completes; `releases/state.json` `history` ends with `commit`; `curl :7979/livez` `version` == manifest `binary_version`; probe transcript archived (T10).
2. **Failure cycle:** induced gate failure → journal shows `rollback` + `quarantine` + `cooldown_until` set + counter incremented; daemon serving previous version's `/livez` version; cap exhaustion (3rd) → `halt` event + promotes refused (T5).
3. **Sweep:** stale `in_flight`+`flipped` journal → launcher start performs sweep-rollback, journal event + cap increment recorded (T7).
4. **Integrity:** tamper detection exit 1 with named file; no-`.env`-in-release assertion green (T2/T3).
5. **Launcher suite green** (extended pack incl. new sweep tests) + core packs per `test-strategy.md` (W3); **live pids byte-identical across all checkpoints** (T10 evidence).

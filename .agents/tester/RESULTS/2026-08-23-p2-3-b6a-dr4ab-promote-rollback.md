# P2.3 B6a — DR-4(a) clean promote (REAL build, S1 redeploy) + DR-4(b) induced-failure auto-rollback

- **Date:** 2026-08-23 · **Recorded by:** worker (B6a dispatch)
- **Branch:** `feature/self-restart-p2p3-ladder-drills` @ `c0993119` (runbook @ same HEAD, executed as written; disclosed deviations in §8 friction log)
- **Runbook:** `docs/runbooks/upgrade-drills.md` §0 (prereqs) + §5 DR-4(a)/(b)
- **Verdict lines:**
  - `DR-4(a) PASS: clean promote REAL build v0.10.6-p2.3-dr4a — gates green in budget, version verify OK, 300s soak clean, journal commit; F-DR1-1 frozen-binary proof captured (differential PYZ); redeploy checkpoints asserted; live untouched`
  - `DR-4(b) PASS: induced readyz-gate failure on v0.10.6-p2.3-dr4b → auto-rollback to v0.10.5-p2.1-e2e2 within window, re-gate green, journal rollback+quarantine, cooldown 19:43:22Z, counter 2→3 (cap-armed as pre-declared), knob removed bit-exact; live untouched`

**Redaction rule:** the live port is rendered `<live-port>` throughout — zero live-port literals in this file or any evidence file under `/tmp/b6a-ev/` (redaction applied at capture time). Demo port 7979 is not restricted.

---

## 1. Fresh DR-0 — inline S1–S5-shaped re-inventory (FL-1; minted at this batch boundary by this dispatch)

| Item | Observed (19:10:28–19:10:55Z) | Match vs expectation |
|---|---|---|
| `status.sh demo` triple + journal | `target=demo dir=/Users/nguyenminhkha/agents-ensemble-demo port=7979 db=ensemble_demo`; journal healthy, `current -> releases/v0.10.5-p2.1-e2e2`, `previous=v0.10.5-p2.1-e2e1`, `in_flight:null`, lock free, `version smoke: OK (0.10.5 == manifest binary_version)` | ✓ §0.1 |
| Demo probes | `/livez` 200 `{"status":"alive",…,"version":"0.10.5"}` (uptime 714s ⇒ born 18:58:34Z = DR-1 re-run's restore daemon); `/readyz` 200 `reasons:[]` | ✓ green baseline |
| **Demo family (re-derived, not trusted)** | wrapper **8394** (`/bin/sh -c`, DR-1's D1.4 relaunch wrapper, ppid 1) → launcher **8396** (`/bin/bash ./launcher.sh`) → bootloader **8415** → daemon **8416** (listener 127.0.0.1:7979); lstart all `Mon Aug 24 01:58:30/31 2026` local (+0700) = 18:58:30Z; all cwd = demo install | ✓ dispatch expectation 8394→8396→8416 (task named 3 key members; bootloader 8415 recorded) |
| **Live pid baseline** | listener pid **31150** / ppid **31130**, lstart `Sat Aug 22 10:04:07 2026`, `./ensemble-prod` — resolved read-only (port from live install's own `.env`; lsof sed-redacted to `<live-port>` BEFORE capture) | ✓ all-day baseline |
| Demo `.env` | md5 `1ba30c018078a60281cba4baeacc03c4` (== DR-1/DR-3 records); 5 `POSTGRES_*` part lines; `ENSEMBLE_SELF_ENV=demo` at L82 | ✓ |
| `.launcher-state` pre-batch | `last_exit=75 crash_count=4 window_start=1787507522 last_backoff=60 notified_75=1 last_uptime=2` — DR-1 re-run residue; burst budget untouched through this batch (final state byte-identical, §6) | ✓ recorded, not asserted |
| **Journal pre-batch snapshot** (read-only cp `state.json.pre-batch`, md5 `5a66749ae0eebdfa0f94232263ebee16`) | `current=e2e2, previous=e2e1, in_flight:null`, `rollback_window_count {24h: 2, window_start: 2026-08-22T22:49:24Z}`, `cooldown_until 2026-08-22T22:59:25Z` (long expired), `quarantined [v0.10.5-p2.1-e2e-bad2]`, 8 history events (P2.1 e2e set) | ✓ prior drill residue, recorded as baseline — **[op-notice] the count=2 window was STILL ACTIVE at (b) time (expires 22:49:24Z); consequences pre-declared in §5/§7** |
| `uv sync --extra dev` | Not run — no repo tests this batch (per DR-1 re-run precedent; §0.3 applies only when packs run) | per precedent |

Releases on disk pre-batch: `v0.10.5-p2.1-e2e-bad2 [QUARANTINED]`, `v0.10.5-p2.1-e2e1`, `v0.10.5-p2.1-e2e2` (retention keep-3).

---

## 2. DR-4(a) — clean promote, REAL BUILD (S1 redeploy)

### 2.1 Setup (disclosed, all authorized)

| Step | Detail |
|---|---|
| Tag-guard precondition (F-DR2-1) | LOCAL lightweight tag `v0.10.6-p2.3-dr4a` created at `c0993119` (19:11:03Z); `git describe --tags --exact-match HEAD` → `v0.10.6-p2.3-dr4a` ✓ |
| Stale-binary removal | repo `dist/ensemble-prod` (mtime Aug 23 05:17 local = Aug 22 22:17Z, **predates `91ace51c`** Aug 23 18:51Z) **moved aside** to `/tmp/b6a-ev/dist-stale-pre-fdr11-ensemble-prod` (sha `d4e84933…`) — REQUIRED: stage.sh reuses any existing `dist/ensemble-prod` ("using existing binary") without rebuilding, which would silently violate the REAL-build intent and the forward-ref proof. Friction FL-1 |
| Stage cmd | `VERSION=v0.10.6-p2.3-dr4a ENSEMBLE_BINARY_VERSION=0.10.5 ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh demo` |
| **Override uses (recorded per D-FA4.5)** | `ENSEMBLE_ROLLBACK_SAFE=1` ×1 (stage (a)) — drill-release intra-set rollback override; derived value would be `false` (`contains_contract_phase=true` from DROP TABLE in migration set). `ENSEMBLE_BINARY_VERSION=0.10.5` ×1 — `daemon/__init__.py:3 __version__="0.10.5"` at HEAD; tag-strip default (`0.10.6-p2.3-dr4a`) would fail version-verify spuriously (P2.1 e2e precedent: `.agents/tester/RESULTS/2026-08-22-p2-1-demo-e2e-release-pipeline.md:14`). Both are stage.sh's documented author-call knobs |
| Build | REAL: stage.sh ran `rm -rf build/ && uv run python -m PyInstaller ensemble.spec` (full log in `stage-dr4a.txt`; "Building Analysis because Analysis-00.toc is non existent") — fresh binary sha `fd7c1ac0efd4dbfeaf7a11f0cf375efdce5c79051e0a0be5f4032d18879b9f0c` ≠ stale `d4e84933…`; staged copy byte-identical (`shasum` match) |
| Stage result | exit 0 (19:11:11→19:12:01Z, 50s); manifest: `binary_version=0.10.5`, `rollback_safe=true`, `contains_contract_phase=true`, `known_schema_gen=20260819_000001_…marker.sql`, `launcher_sha256=1e6a35fc…`, `staged_at=2026-08-23T19:11:51Z`; `.env` md5 UNCHANGED after staging (marker sed no-op, L82) |

### 2.2 Gate timing table (promote 19:13:08 → 19:18:27Z = 319s ≪ 600s outer window)

| Phase | Window (UTC) | Duration | Budget | Result |
|---|---|---|---|---|
| Preflight (lock, integrity ×2, entry checks, txn open) | 19:13:08 → 19:13:18 | 10s | — | ✓ (CURRENT e2e2 + TARGET dr4a integrity green; count 2<3, cooldown expired, no quarantine on target) |
| Stop (SINGLE-TERM 8394+8396, daemon drains) | 19:13:18 → 19:13:21 | ~3s | WAIT_S 70 bound | ✓ |
| Launcher swap + atomic flip (`current -> releases/v0.10.6-p2.3-dr4a`) | 19:13:21 | instant | — | ✓ |
| Restart → `/livez` 200 | 19:13:21 → 19:13:25 | **~4s** | ≤60s | ✓ `{"status":"alive",…,"version":"0.10.5"}` |
| `/readyz` 200 | 19:13:25 | **same probe round** | ≤120s | ✓ `reasons:[]` |
| Version verify | 19:13:25 | instant | — | ✓ `0.10.5 == manifest binary_version` |
| **Soak (re-probe /livez + /readyz every 30s)** | 19:13:25 → 19:18:26 | **300s green** | 300s | ✓ |
| Commit + retention | 19:18:27 | — | — | ✓ journal `commit`; retention evicted `v0.10.5-p2.1-e2e1` (4>keep-3, pinned pair safe) |

Promote exited via the exit-0 path ("promote complete — demo serves 0.10.5 on :7979", the line printed only immediately before `exit 0`; the wrapper's captured `PROMOTE_EXIT=` line was lost to FL-3's pipeline hang — exit-0 independently proven by the commit journal event + `in_flight:null` + lock free + "COMMITTED" transcript).

### 2.3 Journal event verbatim

```text
2026-08-23T19:18:27Z | commit | promote v0.10.6-p2.3-dr4a committed (gate+soak green; previous=v0.10.5-p2.1-e2e2)
```

### 2.4 Forward-reference closure — frozen-binary proof of `91ace51c` (user-ruled)

The booted release's frozen entry (`run_app`, a direct CArchive entry in `current/ensemble-prod`) — PYZ-extracted via `PyInstaller.archive.readers.CArchiveReader`, strings-scanned in the marshalled bytecode (`/tmp/b6a-ev/fwdref-pyz-proof.txt`):

```text
[NEW-dr4a]            run_app extracted: 1673 bytes (magic=b'\xe3\x00\x00\x00')
[NEW-dr4a]            '_boot_db_preflight' present in run_app bytecode: True
[NEW-dr4a]            'run_preflight' present in run_app bytecode: True
[NEW-dr4a]            str: '_boot_db_preflight' / 'run_preflight)' / 'daemon.__main__' / 'main'
[STALE-pre-91ace51c]  run_app extracted: 1563 bytes (magic=b'\xe3\x00\x00\x00')
[STALE-pre-91ace51c]  '_boot_db_preflight' present in run_app bytecode: False
[STALE-pre-91ace51c]  'run_preflight' present in run_app bytecode: False
```

**Differential proof:** the deployed dr4a binary's frozen entry contains the explicit `_boot_db_preflight()` call + `main(run_preflight=False)` hand-off (`91ace51c`, F-DR1-1 hardening); the stale pre-`91ace51c` binary contains neither (plain `main()` delegation). Boot-log side-evidence (launcher.log 02:13:21–24 local): T7 sweep ran BEFORE binary resolution — `journal sweep: in_flight promote txn (target=v0.10.6-p2.3-dr4a) is fresh (3s ≤ 600s) — leaving alone` — then `starting: …/current/ensemble-prod` → `Starting Ensemble v0.10.5` → `Creating PostgreSQL engine: localhost:5432/ensemble_demo` (the preflight itself is silent on success by design; DR-1 re-run proved its failure-path lines on this same lineage).

### 2.5 Redeploy checkpoint table — the ONLY planned demo changes (each asserted-EXPECTED)

| Checkpoint | BEFORE (19:10:42Z) | AFTER promote (19:18:27Z) | AFTER (verified 19:29:31Z) | Asserted |
|---|---|---|---|---|
| `current` symlink | `releases/v0.10.5-p2.1-e2e2` | `releases/v0.10.6-p2.3-dr4a` (flip 19:13:21) | `releases/v0.10.6-p2.3-dr4a` | ✓ EXPECTED flip, exactly once |
| `INSTALL_DIR/launcher.sh` sha256 | `37d538b2…` (P2.1 e2e2 payload) | swapped 19:13:21 from dr4a payload | `1e6a35fc…` == dr4a payload `launcher_sha256` | ✓ EXPECTED swap |
| Demo daemon family | 8394/8396/8415/8416 (lstart 01:58:30 local) | promote's own restart: launcher 40046 → daemon 40173 (launcher.log 02:13:21; journal-sweep line above) | relaunch family 67046→67050→67067→67071 (lstart 02:29:11 local) — see FL-3: my tool-call timeout killed the 40046 tree at 19:28:08Z (graceful exit 143), executor artifact AFTER commit; relaunch 19:29:11Z booted the SAME committed dr4a | ✓ new family pids each restart — EXPECTED |
| `.env` md5 | `1ba30c01…` | unchanged | `1ba30c01…` | ✓ no drift |
| **Live (redeploy-point A)** | 31150/31130, lstart `Sat Aug 22 10:04:07 2026` | — (not probed mid-soak; next capture 19:29:31Z) | **identical** (§6) | ✓ NOT changed |

---

## 3. DR-4(b) — induced-failure auto-rollback

### 3.1 Setup (disclosed)

| Step | Detail |
|---|---|
| Fresh version + tag rotation | LOCAL tag `v0.10.6-p2.3-dr4b` at `c0993119`; **dr4a tag deleted first** (19:30:12Z) — `git describe --tags --exact-match HEAD` returns the FIRST-created tag when multiple sit at HEAD (empirically verified pre-drill), so the tag-guard would otherwise refuse dr4b with `v0.10.6-p2.3-dr4a ≠ v0.10.6-p2.3-dr4b`. dr4a re-created at close (§6). Friction FL-2 |
| Stage cmd | `VERSION=v0.10.6-p2.3-dr4b ENSEMBLE_BINARY_VERSION=0.10.5 ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh demo` (19:30:12→19:30:28Z, 16s) — "using existing binary at …" → payload binary byte-identical to dr4a (`fd7c1ac0…`, D-FA4.5 intra-set discipline) |
| **Stub-build choice (friction-logged, task-permitted)** | NOT used. Rationale: the runbook's (b) induction requires a REAL target daemon that boots `/livez` green and fails only `/readyz`; a §1-fixture stub cannot serve `/livez`, which would shift the failure class from readiness-gate to livez-unreachable — a different leg. Runbook §5(b) as written stages real (no `--skip-build`); the fresh dist binary from (a) made this identical-bytes and cheap |
| Induction (runbook primary) | `.env.dr4-backup` taken (md5 `1ba30c01…` == baseline); FL-4 trailing-newline pre-check PASS (bare `\n` at EOF); `ENSEMBLE_READINESS_FORCE_DEGRADED=1` appended → working md5 `41a18a72…`, diff = exactly one new line L83 |
| Clearance watcher | Dual-trigger: (1) PRIMARY — target `/livez`=200 ∧ `/readyz`=503 observed → clear immediately (technique deviation FL-5: earlier than the runbook's literal GATE-FAILED-line point; same invariant — knob gone before PREVIOUS re-gates — with ~2min margin instead of ~3s); (2) runbook-literal fallback `auto-rollback initiating`; (3) safety net on promote exit. **TRIGGER-1 fired** |

### 3.2 Execution timeline (promote 19:30:55 → 19:33:24Z = 149s ≪ 600s outer window)

| Time (UTC) | Event |
|---|---|
| 19:30:55 | promote start; preflight integrity (CURRENT dr4a + TARGET dr4b) |
| ~19:31:04 | txn open `promote target=v0.10.6-p2.3-dr4b`; entry checks pass (count 2<3, cooldown expired) |
| 19:31:05–08 | stop SINGLE-TERM family 67046/67050; launcher swap from dr4b; flip `current -> releases/v0.10.6-p2.3-dr4b` |
| 19:31:09–10 | restart via launcher (76877); T7 sweep: `in_flight promote txn (target=v0.10.6-p2.3-dr4b) is fresh (3s ≤ 600s) — leaving alone`; target boots |
| ~19:31:10 | gate `/livez` OK (uptime 0.52s) — target HEALTHY on livez |
| 19:31:13 | target `/readyz` **503** — `[Readiness] degraded: readiness: degraded forced by ENSEMBLE_READINESS_FORCE_DEGRADED (drill)` (launcher.log 02:31:13 local) — **TRIGGER-1: knob CLEARED** (`.env` md5 back to `1ba30c01…` same second) |
| ~19:33:11 | readyz gate budget exhausted (120s of 503) → `GATE FAILED: /readyz unreachable >120s — auto-rollback initiating (ADR-005)` |
| 19:33:12–16 | rollback: stop target (launcher 76877 TERM; daemon 76923 drains) → launcher swap from PREV e2e2 → flip `current -> releases/v0.10.5-p2.1-e2e2` |
| 19:33:17 | restart previous (family 81333-wrapper→81334→81378→81382, lstart 02:33:17 local) |
| 19:33:20 | re-gate: `rollback livez OK` + `rollback readyz OK` (`reasons:[]`, checked_at 19:33:20.92) — **knob was gone ~2min07s before this boot** |
| 19:33:22–24 | journal: `rollback` → `quarantine` → `halt` (cap) — see §3.3; `PROMOTE_EXIT=1` |

**Flip-back within the 10-min window:** txn open ~19:31:04 → previous serving green 19:33:20 = **~2min16s** ≪ 600s. ✓

### 3.3 Journal events verbatim (from `state.json`; counter/cooldown stamped by `journal_count_rollback 1`)

```text
2026-08-23T19:33:22Z | rollback    | auto-rollback v0.10.6-p2.3-dr4b → v0.10.5-p2.1-e2e2 (gate fail: /readyz unreachable >120s; re-gate green)
2026-08-23T19:33:23Z | quarantine  | v0.10.6-p2.3-dr4b quarantined after gate failure (skipped by future promotes)
2026-08-23T19:33:24Z | halt        | rollback cap 3/24h reached (count=3) — halt-for-human; promotes refused until the window resets
```

State after (b): `current=v0.10.5-p2.1-e2e2` (flip-back ✓), `previous=v0.10.6-p2.3-dr4a`, `in_flight:null`, `rollback_window_count {24h: 3, window_start: 2026-08-23T19:33:22Z}`, `cooldown_until=2026-08-23T19:43:22Z` (stamp = 19:33:22+600s ✓), `quarantined [v0.10.5-p2.1-e2e-bad2, v0.10.6-p2.3-dr4b]`.

**[op-notice — EXPECTED, pre-declared]** The cap-`halt` event is the documented rollback-#3 behavior (runbook §5(c) "Rollback #3 arms the cap") arriving EARLY because the pre-batch residue count was 2-in-active-window (window would have expired 2026-08-23T22:49:24Z). The cap branch (promote.sh:366-373) replaces the usual terminal `rollback complete: … window count N/3` history line with the `halt` line and exits 1 either way; env recovery, cooldown, counter, and quarantine are IDENTICAL to the non-cap path. Promote exit = **1** (rolled back, env recovered) as the runbook expects.

### 3.4 Pass-criteria evidence (b)

| # | Criterion | Evidence | Result |
|---|---|---|---|
| B1 | Journal `rollback` event | §3.3 verbatim (19:33:22Z) | **PASS** |
| B2 | Flip-back within 10-min window | ~2min16s txn-open → previous green (§3.2); `current` → e2e2 | **PASS** |
| B3 | Cooldown stamped (10min) | `cooldown_until 2026-08-23T19:43:22Z` = rollback ts +600s | **PASS** |
| B4 | Counter +1 | 2 → 3 (`window_start` re-stamped 19:33:22Z) — cap-armed halt documented above | **PASS** |
| B5 | Previous release green post-flip | promote re-gate `rollback readyz OK` (19:33:20) + independent probe 19:34:41Z `/livez` 200 v0.10.5 + `/readyz` 200 `reasons:[]` | **PASS** |
| B6 | Knob removed | TRIGGER-1 clearance 19:31:13Z; final `.env` md5 `1ba30c018078a60281cba4baeacc03c4` == baseline; `diff` vs `.env.dr4-backup` EMPTY; `grep -c ENSEMBLE_READINESS_FORCE_DEGRADED` = 0 | **PASS** |
| B7 | Promote exit 1 (rolled back, env recovered) | `PROMOTE_EXIT=1` (19:33:24Z) | **PASS** |

### 3.5 Pass-criteria evidence (a)

| # | Criterion | Evidence | Result |
|---|---|---|---|
| A1 | Journal `commit` | §2.3 verbatim (19:18:27Z) | **PASS** |
| A2 | Gates within budgets | livez ~4s ≤60s; readyz same-round ≤120s; all inside 319s ≪ 600s outer (§2.2) | **PASS** |
| A3 | Version verify | `version verify OK: 0.10.5` == manifest `binary_version` (promote transcript + status.sh smoke OK) | **PASS** |
| A4 | 300s soak clean | `soak complete (300s green)` 19:13:25→19:18:26 (re-probe/30s) | **PASS** |
| A5 | Redeploy checkpoints asserted | §2.5 — symlink flip, launcher swap (sha==payload), new family pids; live unchanged | **PASS** |
| A6 | Forward-ref proof | §2.4 differential PYZ proof | **PASS** |

---

## 4. Cooldown realism — remaining window (do NOT fudge/force-clear)

- `cooldown_until = 2026-08-23T19:43:22Z` (armed by rollback #3).
- Remaining at close of evidence capture: **405s at 19:36:36Z** (monotonically decreasing; B6b should re-derive at its start).
- **The cooldown is NOT the binding constraint for B6b** — the **cap (count=3/3) is**: entry-side promotes are refused (`reason=cap`, exit 78) until EITHER the R3.2-style journal reset (archive + re-init; runbook §5 R3.2 — mandatory post-DR-4 anyway before T9) OR the 24h window rollover at **2026-08-24T19:33:22Z** (window_start re-stamped by this rollback). B6b's cap legs must schedule the journal reset FIRST.

---

## 5. State left behind (for B6b planning)

| Item | State |
|---|---|
| Demo serving | v0.10.5-p2.1-e2e2 (`current` symlink), binary self-report 0.10.5, `/readyz` 200 `reasons:[]`; family 81333(wrapper, carries promote.sh cmdline — F-DR1-5 class)→81334(launcher)→81378→81382(daemon), lstart 02:33:17 local; launcher.sh sha `37d538b2…` (e2e2 payload, restored by rollback) |
| Journal | current=e2e2, previous=dr4a, in_flight:null, count 3/3 (halt-armed), cooldown until 19:43:22Z, quarantined [bad2, dr4b]; 12 history events (8 pre-batch + commit(a) + rollback/quarantine/halt(b)) |
| Releases on disk | 4 dirs: bad2 [QUARANTINED], e2e2, dr4a, dr4b [QUARANTINED] — retention_evict is SKIPPED in the cap branch (promote.sh exits at :372 before :379); next commit/reset evicts. Neither pinned release affected |
| `.env` | bit-exact baseline `1ba30c01…`; `.env.dr4-backup` kept as evidence; knob line count 0 |
| `.launcher-state` | byte-identical to pre-batch (`last_exit=75 crash_count=4 …`) — both legs restart via stop-script TERM path, no crash-track writes |
| Repo | local tags `v0.10.6-p2.3-dr4a` + `v0.10.6-p2.3-dr4b` BOTH at `c0993119`, left in place (dr4a deleted-then-recreated for the tag-guard, FL-2); `dist/ensemble-prod` = fresh build `fd7c1ac0…` (gitignored); NO commits; stale pre-F-DR1-1 binary preserved at `/tmp/b6a-ev/dist-stale-pre-fdr11-ensemble-prod` |
| Evidence dir | `/tmp/b6a-ev/` — 20+ files: dr0 inventory, stage/promote transcripts ×2, fwdref-pyz-proof, journal pre/post, knob-set/clearance, redeploy checkpoints ×4, live pid start/mid/end, cooldown, boot logs |

---

## 6. Live pid checkpoint table (read-only; zero live contact)

| Checkpoint | Moment (UTC) | pid/ppid | lstart | Diff vs start |
|---|---|---|---|---|
| start (§0.4) | 19:10:55 | 31150 / 31130 | Sat Aug 22 10:04:07 2026 | — (baseline) |
| redeploy-point A (post-(a) promote) | 19:29:31 | 31150 / 31130 | same | **identical ✓** |
| end (post-(b), close) | 19:35:4x | 31150 / 31130 | same | **identical ✓** (ps rows AND redacted lsof lines byte-identical, `LSOF-IDENTICAL`) |

Resolve method per §0.4: port from live install's own `.env`; lsof lines sed-redacted to `<live-port>` before capture. No signal, no HTTP, no write to live at any point.

---

## 7. Constraint compliance

- **Live:** READ-ONLY throughout (`ps`, `lsof`, own-`.env` PORT read for the redacted resolve). Invariance proven §6 at every checkpoint. Live-pid-checkpoint invariant satisfied.
- **Demo:** mutations exactly the runbook DR-4(a)+(b) sets — 2 stages, 2 promotes, 1 knob set + 1 clearance (TRIGGER-1), launcher swaps + flips all pipeline-owned; PLUS one disclosed manual launcher relaunch at 19:29:11Z recovering MY executor artifact (FL-3: tool-timeout killed the freshly-committed daemon tree at 19:28:08Z — graceful exit 143; NOT a pipeline event; no journal trace, correctly). `ENSEMBLE_ROLLBACK_SAFE=1` used exactly twice, both at STAGE time, both recorded (§2.1, §3.1). `ENSEMBLE_UPGRADE_LIVE` never set.
- **PID discipline:** every stop was stop-ensemble.sh-issued (SINGLE-TERM launcher tiers; transcripts captured); no bare-pid kill by me at any point (the FL-3 kill was the harness's process-group timeout, not a drill action).
- **Repo:** single artifact written (this file). `git status --porcelain` at close: ` M .agents/approver/active.md` (pre-existing, untouched) + this new file + 2 local tags (untracked-by-status). NO commits.
- **Port literals:** zero live-port literals in this file or evidence files (capture-time redaction). Demo 7979 only.

---

## 8. Operator friction log (T1 signal)

| # | Where | Doc says | Observed | Classification |
|---|---|---|---|---|
| FL-1 | §5(a) stage | "REAL (no --skip-build)" | stage.sh silently REUSES an existing `dist/ensemble-prod` (stale, pre-`91ace51c`) — "no --skip-build" ≠ "fresh build". Had to mv dist/ aside (stage.sh's own hint: "or rm dist/"). Without this, leg (a) would stage a stale binary and the forward-ref proof would fail | **MINOR gap** — runbook should say "rm/mv dist/ first when a fresh build is required" |
| FL-2 | §5 tag discipline | tag-guard precondition (F-DR2-1) | `git describe --tags --exact-match HEAD` returns the FIRST-created tag when multiple tags sit at HEAD (verified empirically) — consecutive same-commit drill legs MUST rotate tags (delete old → stage → re-create at close). P2.1 e2e rotated silently; runbook never states why | **MINOR gap** — one sentence would prevent a confusing exit-78 |
| FL-3 | executor technique | — | Wrapping promote in `cmd \| while read \| tee` left the pipe open (fd held in the daemonized grandchild chain); the tool call hung until timeout, whose process-group SIGKILL TERMed the freshly-committed daemon tree 10min after commit (graceful 143; demo dark ~65s until relaunch). Known harness behavior ("daemons spawned inside a bash call die with the tool's process tree on timeout") — mitigation: run promote via the background-process runner (used for (b), zero issues) | **executor-side** — documented for future drill runners; NOT a pipeline defect |
| FL-4 | §5(b) version knob | runbook examples assume tag == daemon `__version__` | Tag-strip default `binary_version` (`0.10.6-p2.3-dr4b`) ≠ daemon self-report (`0.10.5`) — version-verify would fail legs spuriously without `ENSEMBLE_BINARY_VERSION=0.10.5` (P2.1 precedent; stage.sh comment documents the drift hazard). Both legs needed it | **OK-but-noted** — runbook §5 examples should mention the override when drill-tag ≠ `daemon/__init__.py` |
| FL-5 | §5(b) halt-trap clearance | "clear the moment the target's gate failure appears — the repoint→restart window is the clearance window" | Added an earlier PRIMARY trigger (target `/readyz` observed 503 → clear immediately): same invariant (knob gone before PREVIOUS re-gates), margin ~2min instead of ~3s; runbook-literal trigger kept as fallback (never needed — TRIGGER-1 fired at 19:31:13Z). Recommend the runbook adopt the early trigger as primary | **technique deviation, disclosed** (strictly safer) |
| FL-6 | harness | `/bin/sh` rejects `<(…)` process substitution in diff lines | Same DR-1 portability nit; worked around with temp files | **carried** |
| FL-7 | §5(b) expected outputs | journal `rollback` + cooldown + counter | With residue count 2-in-window, leg (b) lands rollback #3 → cap branch replaces the terminal "rollback complete … window count N/3" history line with the `halt` line (exit still 1; state identical). Expected per code; runbook (b) written against a fresh journal won't show it. Also: retention_evict is skipped in the cap branch (4 dirs remain) | **OK-but-noted** + op-notice §4 |

**Friction summary:** the corrected runbook executed cleanly on both legs (induction, halt-trap clearance, gates, journal semantics each landed as documented). The two real gaps are build-freshness (FL-1) and tag-rotation (FL-2); FL-3 is an executor technique note; FL-4/FL-7 are documentation sharpness items.

---

## 9. Findings

1. **F-B6a-1 (op-notice, binding for B6b):** journal is cap-armed (3/3, window re-stamped 2026-08-23T19:33:22Z → rollover 2026-08-24T19:33:22Z) + cooldown until 19:43:22Z. B6b MUST run the R3.2-style reset (archive state.json + re-init via a stage) before any further promote; cooldown alone is not the blocker.
2. **F-B6a-2:** retention_evict skipped on the cap-halt path → 4 release dirs on disk (2 quarantined). Harmless; next commit evicts (keep-3, pinned pair safe).
3. **F-B6a-3:** the S1 redeploy itself is CLEAN — the only unplanned demo restart was MY tool-timeout kill (FL-3) 10min post-commit; the release stayed committed and re-booted identically. The T7 launcher sweep correctly left the fresh in_flight txn alone on both boots (log lines captured §2.4/§3.2).
4. **F-B6a-4:** F-DR1-1 forward-reference CLOSED: the frozen binary deployed by this batch carries the explicit `_boot_db_preflight()` call site (differential PYZ proof §2.4). The DR-1 re-run file's §7 forward-reference is discharged here.

---

## 10. Verdicts

`DR-4(a) PASS: clean promote REAL build v0.10.6-p2.3-dr4a — gates green in budget, version verify OK, 300s soak clean, journal commit; F-DR1-1 frozen-binary proof captured (differential PYZ); redeploy checkpoints asserted; live untouched`

`DR-4(b) PASS: induced readyz-gate failure on v0.10.6-p2.3-dr4b → auto-rollback to v0.10.5-p2.1-e2e2 within window (~2min16s), re-gate green, journal rollback+quarantine, cooldown 19:43:22Z, counter 2→3 (cap-armed as pre-declared), knob removed bit-exact; live untouched`

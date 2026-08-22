# Demo End-to-End Validation: P2.1 Release & Upgrade Pipeline (T10)

- **Date:** 2026-08-22 (drill window 22:16:58Z–22:59:25Z+) · **Executor:** coder agent (working-lead), T10 dispatch
- **Branch:** `feature/self-restart-p2p1-release-pipeline` @ `50a85ab9` (pipeline T1–T9 as implemented + review-hardened; packs 124/124 launcher, 121/121 journal, 95/95 drills)
- **Target:** demo ONLY (`~/agents-ensemble-demo`, :7979, `ensemble_demo`) — started FLAT (deploy.sh layout from ar-phase1, daemon `0.10.5` up 13.6h); transitions to staged mode mid-drill (D3 seam)
- **Verdict: ✅ PASS** — clean cycle ×2 (stage→promote→commit, `previous` updated, version-verify OK, 300s soaks green) AND induced-failure cycle (auto-rollback + quarantine + cooldown + counter) all proven live on demo; live pids byte-identical across all 6 checkpoints; demo ends green on the last-committed release.

## 0. Version/tag protocol (user ruling: local-checkout-only, never push)

All releases built from the SAME tagged tree (`50a85ab9`, lightweight LOCAL tags, created+deleted around each stage — `git describe --tags --exact-match HEAD` enforced by stage.sh; bare `uv run python -m PyInstaller ensemble.spec`, dist cleared first so the binary provably came from the tagged tree):

| Release | Tag (local, lifecycle) | `binary_version` in manifest | Role |
|---|---|---|---|
| `v0.10.5-p2.1-e2e1` (vA) | `v0.10.5-p2.1-e2e1` (created 22:17:19Z; deleted 22:35:0x; recreated for re-stage 22:42:0x; deleted 22:48:4x) | `0.10.5` via `ENSEMBLE_BINARY_VERSION=0.10.5` (daemon self-report on this tree is `0.10.5`, `daemon/__init__.py:3`) | clean promote #1; rollback target in final pairing |
| `v0.10.5-p2.1-e2e2` (vB) | `v0.10.5-p2.1-e2e2` (created 22:35:0x, deleted 22:40:4x) | `0.10.5` | clean promote #2 (evidences `previous` update); final serving release; auto-rollback target |
| `v0.10.5-p2.1-e2e-bad` (vC) | `v0.10.5-p2.1-e2e-bad` (created 22:40:4x, deleted 22:42:0x) | `0.99.0-gate-fail-drill` (poisoned — can never equal daemon self-report) | first induced failure → halted by rollback_safe gate (see §3a); later EVICTED by retention (evidence §5) |
| `v0.10.5-p2.1-e2e-bad2` (vD) | `v0.10.5-p2.1-e2e-bad2` (created 22:48:4x; deleted post-evidence 23:0x) | `0.99.0-gate-fail-drill` | induced failure → full auto-rollback path (§3b) |

**Failure induction mechanism (deterministic, sanctioned):** `ENSEMBLE_BINARY_VERSION=0.99.0-gate-fail-drill` stages a manifest whose `binary_version` the healthy daemon can never self-report → the D2/ADR-027 version-verify gate fails deterministically while `/livez` + `/readyz` stay green. No env knob left behind (avoids the R1.5/P7 knob-restore hazard entirely — the readiness drill knob in shared `INSTALL_DIR/.env` would have degraded the ROLLBACK target's re-gate too, forcing a halt; the version-lie variant keeps the daemon healthy throughout). `ENSEMBLE_BINARY_VERSION` and `ENSEMBLE_ROLLBACK_SAFE` are stage.sh's documented author-call knobs.

## 1. Clean cycle on demo (Exit Criterion 1 + T10 item 1) — PASS

**Promote vA (first-ever promote; flat→staged seam):** transcript `/tmp` capture, verbatim key lines:

```
upgrade-promote[demo]: resolved env: target=demo dir=/Users/nguyenminhkha/agents-ensemble-demo port=7979 db=ensemble_demo
upgrade-promote[demo]: txn open: promote target=v0.10.5-p2.1-e2e1 pid=90505 (outer window 600s from txn start)
stop-ensemble: launcher-owned stop: TERMinG launcher(s) ONLY (single TERM, forwarded to daemon): 12146
stop-ensemble: done — /Users/nguyenminhkha/agents-ensemble-demo is stopped
upgrade-promote[demo]: launcher swapped from release v0.10.5-p2.1-e2e1 (stopped window)
upgrade-promote[demo]: current -> releases/v0.10.5-p2.1-e2e1 (atomic flip)
upgrade-promote[demo]: livez OK:  {"status":"alive","uptime_seconds":1.98…,"version":"0.10.5"}
upgrade-promote[demo]: readyz OK: {"status":"ready",…,"checked_at":"2026-08-22T22:19:03.05…+00:00","draining":false}
upgrade-promote[demo]: version verify OK: 0.10.5
upgrade-promote[demo]: soak complete (300s green)
upgrade-promote[demo]: COMMITTED: current=v0.10.5-p2.1-e2e1 previous=<none>   [journal commit ts 2026-08-22T22:24:05Z]
```

**Promote vB (clean #2 — `previous` update evidenced):**

```
upgrade-promote[demo]: integrity: verifying CURRENT release v0.10.5-p2.1-e2e1 (drift detection)   ← T3 preflight on current
upgrade-promote[demo]: txn open: promote target=v0.10.5-p2.1-e2e2 pid=23912
stop-ensemble: SIGTERM 18867/18869 → done — stopped
upgrade-promote[demo]: current -> releases/v0.10.5-p2.1-e2e2 (atomic flip)
upgrade-promote[demo]: livez OK (uptime 1.35s) / readyz OK (checked_at 2026-08-22T22:35:38.95Z) / version verify OK: 0.10.5
upgrade-promote[demo]: soak complete (300s green)
upgrade-promote[dem8]: COMMITTED: current=v0.10.5-p2.1-e2e2 previous=v0.10.5-p2.1-e2e1   [journal commit ts 2026-08-22T22:40:41Z]
upgrade-promote[demo]: promote complete — demo serves 0.10.5 on :7979    EXIT:0
```

**Post-cycle assertions (both):** `curl :7979/livez` `version` == manifest `binary_version` (`0.10.5` — status.sh `version smoke: OK`); journal `history` ends `commit`; lock released (`pipeline lock: free`); `current` symlink → `releases/v0.10.5-p2.1-e2e2`.

**Flat→staged seam (D3, T10 validation point):** stage landed `releases/` beside the flat install with `current` absent pre-promote; after the first flip every boot resolves `…/current/ensemble-prod` (launcher.log `starting:` lines, below) while the flat 47,985,824-byte binary at `~/agents-ensemble-demo/ensemble-prod` remained UNTOUCHED (mtime 15:08) as fallback. The very boot that flipped also logged the sweep deferral (same launcher start):

```
2026-08-23T05:18:58+0700 launcher[92032]: journal sweep: in_flight promote txn (target=v0.10.5-p2.1-e2e1) is fresh (2s ≤ 600s) — leaving alone
2026-08-23T05:18:58+0700 launcher[92032]: starting: /Users/nguyenminhkha/agents-ensemble-demo/current/ensemble-prod
(prior flat-era boots: "…launcher[12146]: starting: /Users/nguyenminhkha/agents-ensemble-demo/ensemble-prod")
```

**Staged launcher deployed + sweep wiring live:** promote-time `launcher_swap` made deployed launcher ≡ staged release launcher ≡ repo tip — sha256 `37d538b2df506748946552e35bb6b1f4578217dc97b7dd7c832526b4a0a216b4` ×3 (deployed / `releases/v0.10.5-p2.1-e2e2/launcher.sh` / repo; pre-drill deployed launcher was `b52169ab…` sweep-stub era). 7 sweep-deferral lines across the drill's restarts, covering both txn kinds:

```
05:18:58 launcher[92032]: journal sweep: in_flight promote txn (target=…e2e1)  is fresh (2s ≤ 600s) — leaving alone
05:35:36 launcher[26701]: journal sweep: in_flight promote txn (target=…e2e2)  is fresh (3s ≤ 600s) — leaving alone
05:41:26 launcher[43078]: journal sweep: in_flight promote txn (target=…e2e-bad)  is fresh (2s ≤ 600s) — leaving alone
05:42:54 launcher[49620]: journal sweep: in_flight rollback txn (target=…e2e2)  is fresh (2s ≤ 600s) — leaving alone
05:43:24 launcher[53109]: journal sweep: in_flight promote txn (target=…e2e1)  is fresh (3s ≤ 600s) — leaving alone
05:49:14 launcher[69457]: journal sweep: in_flight promote txn (target=…e2e-bad2) is fresh (3s ≤ 600s) — leaving alone
05:49:20 launcher[69871]: journal sweep: in_flight promote txn (target=…e2e-bad2) is fresh (9s ≤ 600s) — leaving alone
```

(The drill's fresh-txn deferrals + the pack's kill-aged-txn sweep-rollback (below, §6) jointly cover the sweep decision table live.)

## 2. Failure cycle on demo (Exit Criterion 2 + T10 item 2) — PASS (via vD; vC yielded a bonus halt)

### 2a. First induced failure (vC) → rollback_safe gate HALT (T5 halt path, live on demo)

```
upgrade-promote[demo]: WARN: version verify MISMATCH: running=0.10.5 expected=0.99.0-gate-fail-drill (manifest binary_version)
upgrade-promote[demo]: GATE FAILED: version verify mismatch — auto-rollback initiating (ADR-005)
upgrade-promote[demo]: WARN: HALT-FOR-HUMAN: previous v0.10.5-p2.1-e2e1 is NOT rollback_safe — daemon stays on v0.10.5-p2.1-e2e-bad
                        (degraded) rather than flipping into schema drift. NO repoint.    EXIT:78
journal: {"ts":"2026-08-22T22:41:33Z","event":"halt","detail":"gate fail (version verify mismatch) but previous v0.10.5-p2.1-e2e1
         has rollback_safe=false — halt-for-human, NO repoint (schema-drift guard D-FA4.5)"}
```

Root cause of the halt (drill-side, not a script bug): vA had been staged WITHOUT the `ENSEMBLE_ROLLBACK_SAFE` author override, so D-FA4.5 derivation stamped `rollback_safe=false` (repo's SQLite migration set contains DROP TABLE → `contains_contract_phase=true`). The 3-release chain made vA — not vB — the rollback target. **Net effect: the T5 "previous-unsafe → halt-for-human, NO repoint" acceptance row was proven LIVE on demo** (previously drill-cited only). Recovery per ADR-028: re-stage vA with `ENSEMBLE_ROLLBACK_SAFE=1` (payloads byte-identical across drill releases — a rollback inside the drill set is schema-neutral; author's call is stage.sh's sanctioned knob), manual rollback to vB (§2b), promote vA through the standard gate (commit 22:48:29Z).

### 2b. Manual rollback (T6, live on demo) — halt recovery leg

```
upgrade-rollback[demo]: target: explicit v0.10.5-p2.1-e2e2
… stop → launcher swapped from release v0.10.5-p2.1-e2e2 → current -> releases/v0.10.5-p2.1-e2e2 (atomic flip) → restart
upgrade-rollback[demo]: livez OK / readyz OK (checked_at 2026-08-22T22:42:57.83Z)
upgrade-rollback[demo]: rollback complete: current=v0.10.5-p2.1-e2e2 serving 0.10.5; window count 1/3    EXIT:0
```

T6 design points evidenced: counts toward cap (`24h: 1`), NO cooldown armed (`cooldown_until: null` after), never refused on cap/cooldown (ran while count pre-existed).

### 2c. Second induced failure (vD) → FULL auto-rollback (the T10-required cycle)

```
upgrade-promote[demo]: livez OK (uptime 0.10s) / readyz OK (checked_at 2026-08-22T22:49:18.08Z)
upgrade-promote[demo]: WARN: version verify MISMATCH: running=0.10.5 expected=0.99.0-gate-fail-drill (manifest binary_version)
upgrade-promote[demo]: GATE FAILED: version verify mismatch — auto-rollback initiating (ADR-005)
… second SINGLE-TERM stop → launcher swapped from release v0.10.5-p2.1-e2e2 → current -> releases/v0.10.5-p2.1-e2e2 (atomic flip) → restart
upgrade-promote[demo]: rollback livez OK (uptime 1.21s) / rollback readyz OK (checked_at 2026-08-22T22:49:23.55Z)
upgrade-promote[demo]: ROLLBACK COMPLETE: current=v0.10.5-p2.1-e2e2 serving 0.10.5; quarantine=v0.10.5-p2.1-e2e-bad2; cooldown 600s; count 2/3
upgrade-promote[demo]: retention: 4 releases > keep=3 — evicting 1 oldest … evicting v0.10.5-p2.1-e2e-bad …
PROMOTE-D EXIT:1        (promote failed; environment recovered — exact exit contract)
```

**Journal state after (all four required elements):**

```json
"rollback_window_count": {"24h": 2, "window_start": "2026-08-22T22:49:24Z"},   ← counter incremented
"cooldown_until": "2026-08-22T22:59:25Z",                                      ← cooldown set (600s)
"quarantined": ["v0.10.5-p2.1-e2e-bad2"],                                      ← quarantine
history: [… {"rollback","auto-rollback v0.10.5-p2.1-e2e-bad2 → v0.10.5-p2.1-e2e2 (gate fail: version verify mismatch; re-gate green)"},
            {"quarantine","v0.10.5-p2.1-e2e-bad2 quarantined after gate failure"},
            {"rollback","rollback complete: serving 0.10.5 on :7979; cooldown armed (600s); window count 2/3"}]
```

Daemon serving PREVIOUS version's `/livez` version: `{"status":"alive",…,"version":"0.10.5"}` == vB manifest `binary_version` `0.10.5` ✓.

**Entry-side refusals (both probed live, both exit 78, neither touched the daemon):**

```
# inside cooldown (22:49:5xZ):
COOLDOWN-PROBE EXIT: 78 — "promote refused: rollback cooldown active until 2026-08-22T22:59:25Z (ADR-005: 10-min anti-flapping)"
# journal untouched by the refusal (8 history entries before == after; in_flight null; lock released via EXIT trap)
# after cooldown expiry (§4): promote v0.10.5-p2.1-e2e-bad2 → refused QUARANTINED (see §4)
```

Cap-exhaustion (3rd rollback → `halt` + promotes refused) and the launcher sweep-rollback: **NOT forced on demo** (per dispatch instruction — cited from drill/pack evidence, §6).

## 3. Live-pid checkpoints (T10 item 4; Exit Criterion 5, Phase-1 §5 method) — PASS, byte-identical ×6

Reference class: live install's `ensemble-prod` process set + :9797 listener. Captured before/after every cycle phase:

| CP | Time (UTC) | live ensemble-prod set | :9797 listener | demo daemon (informational) | demo /livez |
|---|---|---|---|---|---|
| 0 baseline | 22:16:58 | `31130 ./ensemble-prod` `31150 ./ensemble-prod` | 31150 | 12158/12245 (flat) | 0.10.5 |
| 1 post-stage vA | 22:18:42 | `31130` `31150` (identical) | 31150 | 12158/12245 | 0.10.5 |
| 2 post-promote vA | 22:34:09 | `31130` `31150` (identical) | 31150 | (down — harness kill, §Deviations; recovered 22:34:4x) | — then 0.10.5 |
| 3 post-promote vB | 22:40:50 | `31130` `31150` (identical) | 31150 | 26730/26734 (current/) | 0.10.5 |
| 4 post-halt (vC) | 22:42:30 | `31130` `31150` (identical) | 31150 | 43108/43109 (current/) | 0.10.5 |
| 5 post-auto-rollback (vD) | 22:49:40 | `31130` `31150` (identical) | 31150 | 69901/69918 (current/) | 0.10.5 |
| 6 final settle | 23:0x | `31130` `31150` (identical) | 31150 | final pids (current/) | 0.10.5 |

**Verdict: live pids byte-identical across ALL checkpoints** (`31130` + `31150`, listener `31150`); live port 9797 never contacted by any drill action; `~/agents-ensemble`, `ensemble_prod`, `ENSEMBLE_DEPLOY_LIVE`/`ENSEMBLE_UPGRADE_LIVE` never touched (demo resolves via `scripts/upgrade/lib.sh` topology table; all invocations positional `demo`).

## 4. Final state + quarantine refusal after cooldown expiry

(probe run 22:59:53Z, after `cooldown_until` 22:59:25Z expired — a pre-expiry probe at 22:51:23Z correctly refused on cooldown instead, double-evidencing the cooldown gate:)

```
QUARANTINE-PROBE EXIT: 78 — "promote refused: version 'v0.10.5-p2.1-e2e-bad2' is QUARANTINED (prior gate failure) —
                              quarantine is cleared only by re-staging the version"
```

Final: demo green — `/livez` 200 `version 0.10.5`, `/readyz` 200 `reasons: []`, `status.sh demo --verify` exit 0 (`integrity OK … trio + launcher + config + trees`; `version smoke: OK`); journal idle (`in_flight: null`), lock FREE, `current → releases/v0.10.5-p2.1-e2e2` (last-committed release), serving version NOT quarantined (quarantined vD ≠ serving vB). Rollback window count rests at 2/3 (expires/reset per 24h window rule); `cooldown_until` is a past timestamp (passive, expired).

## 5. Retention (T8, live on demo)

After the vD rollback: 4 releases > keep=3 → `retention: evicting v0.10.5-p2.1-e2e-bad (staged 1787438456) — neither current (v0.10.5-p2.1-e2e2) nor previous (v0.10.5-p2.1-e2e1)` → final inventory exactly `v0.10.5-p2.1-e2e1` (previous, pinned), `v0.10.5-p2.1-e2e2` (current), `v0.10.5-p2.1-e2e-bad2` (quarantined, kept — newest). Eviction ran ON the rollback path; `previous` never evicted.

## 6. Drill/pack-cited rows (not forced on demo, per dispatch)

| Row | Evidence source |
|---|---|
| Cap exhaustion (3rd rollback → `halt`, promotes refused) | journal + drills pack 95/95 (`test/packs/…` @ 50a85ab9; T5 acceptance sandbox scenarios incl. 3rd-rollback halt + subsequent-promote refusal exit 78) |
| Launcher journal sweep (stale `in_flight`+`flipped` → sweep-rollback, cap increment) | commit `454c3002` cross-writer sweep drill — REAL promote kill → REAL launcher rollback; journal pack 121/121; launcher pack 124/124 |
| Integrity tamper detection (exit 1, named file) | T3 pack scenarios (`status.sh --verify` mismatch naming); live GREEN path run here (§4 exit 0) |
| no-`.env`-in-release | live-asserted per stage this drill (`find releases/<ver> -name '.env'` empty ×4) + pack |

## 7. Deviations & harness incidents (explicit)

1. **Tool-timeout kill (harness, not pipeline):** promote vA ran piped through `tee`; the nohup'd launcher kept the pipe open so the tool call hit its 900s cap and SIGKILLed the process group — ~10 min AFTER the promote had committed (22:24:05Z; kill ≈22:33:46Z). Journal/commit unaffected (durable before the kill); demo recovered by starting the launcher (standard path, 22:34:4x; daemon booted from `current/ensemble-prod` — incidentally evidencing staged-mode recovery-from-death). All later promotes ran fully detached (`nohup … >log 2>&1 </dev/null`) with zero recurrence.
2. **vC halt instead of rollback (drill sequencing error, §2a):** first induced failure halted because the rollback target (vA) was staged without `ENSEMBLE_ROLLBACK_SAFE=1`. Recovery consumed §2b/§2c. No script bug; net effect = extra live coverage of the halt path + manual rollback + ADR-028 recovery.
3. **`ENSEMBLE_ROLLBACK_SAFE=1` author override used for vA(re-stage)/vB:** D-FA4.5 derivation stamps `false` for ANY release from this repo (migration set contains DROP TABLE). All drill releases carry byte-identical daemon payloads → intra-set rollback is schema-neutral; override is stage.sh's sanctioned author's call. **P2.3 note:** real-world staged releases from this repo will derive `rollback_safe=false` unless authored otherwise — auto-rollback will halt (by design) until the migration set is delta-scoped or releases are authored.
4. **No script bugs found; zero fixes needed.** No `daemon/` changes. Only repo mutation = this evidence file (+ tag lifecycle: local tags created/deleted, none pushed).

## 8. Constraint compliance

TARGET=demo exclusively; live triple (dir/port/DB) untouched and pid-asserted ×6+; no `git push` of any ref/tag; tags local-only and deleted post-evidence; `.agents/approver/active.md` modified pre-drill by others — left unstaged; conventional commit with explicit `git add` of the evidence file only.

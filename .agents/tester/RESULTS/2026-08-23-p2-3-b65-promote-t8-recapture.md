# P2.3 B6.5 — promote of the T8 fix to demo (REAL build) + T8 re-capture (settled tool-driven path) — the single evidence commit

- **Date:** 2026-08-23 · **Recorded by:** worker (B6.5 dispatch)
- **Branch:** `feature/self-restart-p2p3-ladder-drills` @ `8a0f252c` (HEAD carries the T8 seam fix: `_journal_refusal_event` rider inside `_refusal()`, live carve-out — F-B6c-1 closure)
- **Runbook:** `docs/runbooks/upgrade-drills.md` §0 (prereqs) + §5 DR-4(a) shape (clean promote) + §6 T8 alert-capture intent; §4.1 eligibility per `test-strategy.md` (canonical clauses) + `phase3-plan.md` D1 (c1–c7 evidence decomposition)
- **Verdict lines:**
  - `B6.5-PROMOTE PASS: pipeline deploy of the T8 fix to demo — REAL build v0.10.7-p2.3-b65 @ 8a0f252c staged (57s, fresh-binary guard FL-1 honored: stale B6a dist moved aside first) and promoted clean: livez ~3s ≤60s, readyz ~4s ≤120s, version verify OK (0.10.5 == manifest), 300s soak green, journal commit 21:39:13Z (exactly one commit event, zero rollback/sweep/halt); deployed-fix assert PROVEN by PYZ-differential (deployed upgrade_tools bytecode carries _journal_refusal_event + journal_history_append + the warning literal; superseded dr4r1 binary carries none) + boot-log marker; §4.1 c1–c7 evidence captured WITH the clause-1 script-vs-ari flag (dispatcher rules on eligibility — not self-ruled); live untouched at every checkpoint`
  - `T8 PASS: refusal→journal→SSE captured end-to-end on the deployed fix — ari JAFP job → system_upgrade(dry_run=false, target_env=demo, version=v0.10.6-p2.3-dr4r2 NOT staged) → tool output verbatim "Error: UPGRADE REFUSED — reason=target-not-staged: releases/v0.10.6-p2.3-dr4r2 not found. Run release_info(section=releases)."; journal gained EXACTLY ONE refusal event (21:42:35Z, token in the lib.sh _refuse detail shape); SSE capture holds notification event_type=upgrade_promote_refusal with reason=target-not-staged (arrival 21:42:35Z); classifier mapping refusal→upgrade_promote_refusal cross-checked against ALERT_KIND_BY_EVENT; timestamps coherent to the second`

**Redaction rule:** the live port is rendered `<live-port>` throughout — zero live-port literals in this file or any evidence file under `/tmp/b65-ev/` (lsof output sed-redacted BEFORE capture). Demo port 7979 is not restricted.

---

## 1. Inline DR-0 (FL-1 — within-batch re-inventory; the B6c record is the batch DR-0)

| Item | Observed (21:31:25–21:31:40Z) | Match vs B6c handoff |
|---|---|---|
| `status.sh demo` triple | `target=demo dir=/Users/nguyenminhkha/agents-ensemble-demo port=7979 db=ensemble_demo` | ✓ |
| Journal | ALL-ZERO clean (post R3.2-bis): `current:null, previous:null, in_flight:null`, `24h:0`, cooldown null, quarantined `[]`, history `[]` | ✓ F-B6c-2 |
| Demo probes | `/livez` 200 `{"status":"alive",…,"version":"0.10.5"}` (uptime 2226s ⇒ born ≈20:54Z — continuous since B6c's reset window); `/readyz` 200 `reasons:[]` | ✓ green |
| Demo family (re-derived) | wrapper **65092** (`/bin/sh -c`, ppid 1, B6b sweep-relaunch wrapper) → launcher **65094** (`./launcher.sh`) → bootloader **65488** → daemon **65491** (listener 127.0.0.1:7979), lstart `Mon Aug 24 03:54:07/15 2026` local (+0700) = 20:54Z | ✓ B6c §8 "65491-family" |
| Live baseline | listener pid **31150** / ppid **31130**, lstart `Sat Aug 22 10:04:07 2026`, `./ensemble-prod` — resolved read-only from the live install's own `.env` port; lsof sed-redacted to `<live-port>` BEFORE capture | ✓ all-day baseline |
| Demo `.env` | md5 `1ba30c018078a60281cba4baeacc03c4` == B6a/B6b/B6c baseline | ✓ |
| Tag check at HEAD (FL-17, FIRST) | `git tag --points-at 8a0f252c` → **empty**; `git describe --tags --exact-match HEAD` → fatal 128 (no tag) — zero tags at HEAD, no rotation needed | ✓ dispatch expectation |
| `.launcher-state` | `last_exit=75 crash_count=4 …` — DR-1 re-run residue, unchanged through this batch (recorded, not asserted; B6a precedent) | ✓ recorded |
| `uv sync --extra dev` | Not run — no repo packs this batch (per B6a/B6c precedent; §0.3 applies only when packs run) | per precedent |

## 2. B6.5 promote — REAL build, §4.1-evidence rehearsal

### 2.1 Setup (disclosed, all authorized)

| Step | Detail |
|---|---|
| Local tag | `v0.10.7-p2.3-b65` created at `8a0f252c` (21:31Z, after the zero-tag check); `git describe --tags --exact-match HEAD` → `v0.10.7-p2.3-b65`; tag-count at HEAD = 1 (FL-17 guard) |
| Stale-dist guard (FL-1) | repo `dist/ensemble-prod` (mtime Aug 24 02:11 local = B6a's 19:11Z build, sha `fd7c1ac0efd4dbfeaf7a11f0cf375efdce5c79051e0a0be5f4032d18879b9f0c`) **moved aside** to `/tmp/b65-ev/dist-stale-pre-b65-ensemble-prod` — stage.sh would otherwise reuse it ("using existing binary") and silently ship a fix-less build. This stale binary is byte-identical to the then-deployed dr4r1 release binary ⇒ kept as the PYZ-differential baseline (§2.3) |
| Stage cmd | `VERSION=v0.10.7-p2.3-b65 ENSEMBLE_BINARY_VERSION=0.10.5 ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh demo` |
| **Override uses (recorded per D-FA4.5)** | `ENSEMBLE_ROLLBACK_SAFE=1` ×1 (derived value would be `false` — migration set contains DROP TABLE → `contains_contract_phase=true`); `ENSEMBLE_BINARY_VERSION=0.10.5` ×1 (`daemon/__init__.py:3 __version__="0.10.5"` at HEAD; tag-strip default `0.10.7-p2.3-b65` would fail version-verify spuriously — P2.1 e2e precedent). Both stage.sh's documented author-call knobs, same as B6a |
| Build | REAL: `rm -rf build/` + `uv run python -m PyInstaller ensemble.spec` ("Building PKG because PKG-00.toc is non existent", "Building EXE because EXE-00.toc is non existent" — transcript `stage-b65.txt`); 21:32:00→21:32:57Z (57s), exit 0. Fresh binary sha `c07e2598cf9b33b30076f0de041eff20d9b012905a584410281bf3924fc5727c` ≠ stale `fd7c1ac0…`; staged copy byte-identical (shasum match); manifest `binary_version=0.10.5`, `rollback_safe=true`, `staged_at=2026-08-23T21:32:47Z`; `.env` md5 UNCHANGED after staging (`ENSEMBLE_SELF_ENV=demo` marker sed no-op) |

### 2.2 Gate timing table (promote ≈21:34:05 → 21:39:13Z ≈ 310s ≪ 600s outer window; `PROMOTE_EXIT=0`)

| Phase | Window (UTC) | Duration | Budget | Result |
|---|---|---|---|---|
| Preflight (lock, integrity, txn open pid=43574, entry checks) | ≈21:34:05 → 21:34:06 | ~1s | — | ✓ (count 0<3, no cooldown, no quarantine, lock free) |
| Stop (SINGLE-TERM 65092+65094 via stop-ensemble.sh, WAIT_S 70 bound) | 21:34:07 → 21:34:08 | ~1s | — | ✓ "done — …stopped" |
| Launcher swap (from b65 payload) + atomic flip `current -> releases/v0.10.7-p2.3-b65` | 21:34:08 | instant | — | ✓ |
| Restart → `/livez` 200 | 21:34:08 → ≈21:34:11 | **~3s** | ≤60s | ✓ `{"status":"alive","uptime_seconds":0.80,…,"version":"0.10.5"}` (new family 45232→45274→**45277**, lstart 04:34:08 local) |
| `/readyz` 200 | ≈21:34:12 | **~4s from flip** | ≤120s | ✓ `reasons:[]`, `checked_at 21:34:12.06` |
| Version verify | 21:34:12 | instant | — | ✓ `0.10.5 == manifest binary_version` |
| **Soak (re-probe /livez + /readyz every 30s)** | 21:34:12 → 21:39:12 | **300s green** | 300s | ✓ "soak complete (300s green)" |
| **Commit** | **21:39:13Z** (journal ts) | — | — | ✓ `COMMITTED: current=v0.10.7-p2.3-b65 previous=<none>` |
| Retention (8 > keep-3 → evict 5) + exit 0 | 21:39:13 → ≈21:39:20 | — | — | ✓ evicted dr4r1/dr4r2/dr4b2/dr4b3/dr4c; "promote complete — demo serves 0.10.5 on :7979"; `PROMOTE_EXIT=0` (direct file redirect — no FL-3 pipeline shape) |

### 2.3 Journal event verbatim (post-promote)

```text
2026-08-23T21:39:13Z | commit | promote v0.10.7-p2.3-b65 committed (gate+soak green; previous=none)
```

State: `current=v0.10.7-p2.3-b65, previous=null` (fresh journal re-anchor — previous was null pre-promote), `in_flight:null`, `24h:0`, cooldown null, quarantined `[]`, history = exactly 1 event.

### 2.4 Deployed-fix assert — PYZ-differential (B6a forward-ref precedent, module-level variant)

`daemon/tools/upgrade_tools` extracted from each binary's PYZ (`PYZ.pyz` CArchive entry → `ZlibArchiveReader.extract` → marshal.dumps), strings-scanned (`/tmp/b65-ev/pyz-differential-deployed.txt`; identical result pre-promote on the repo dist — `pyz-differential-pre-promote.txt`):

```text
[NEW-b65  — DEPLOYED current/ensemble-prod, sha c07e2598…] daemon.tools.upgrade_tools extracted: 98128 bytes
[NEW-b65] _journal_refusal_event        present: True
[NEW-b65] journal_history_append        present: True
[NEW-b65] upgrade_promote_refusal       present: True
[NEW-b65] "refusal journal append FAILED" present: True   (the never-raises warning literal)
[STALE-dr4r1 — superseded, sha fd7c1ac0…]                  daemon.tools.upgrade_tools extracted: 95539 bytes
[STALE-dr4r1] all four markers                          present: False
DIFFERENTIAL: _journal_refusal_event NEW-only: True · PROOF DONE
```

**Deployed-path chain:** `current -> releases/v0.10.7-p2.3-b65`; deployed binary sha `c07e2598…` == fresh dist == manifest `binary_sha256` — the PYZ-proven module IS the running daemon's code (the running process 45277 executes `current/ensemble-prod`).

**Boot-log marker (side-evidence, launcher.log local +0700):** `04:34:08 journal sweep: in_flight promote txn (target=v0.10.7-p2.3-b65) is fresh (2s ≤ 600s) — leaving alone` → `04:34:10 Starting Ensemble v0.10.5` → `04:34:11 Creating PostgreSQL engine: localhost:5432/ensemble_demo`.

### 2.5 Target-triple + daemon-family assertions (each change asserted-EXPECTED)

| Checkpoint | Before | After promote | Asserted |
|---|---|---|---|
| Target triple | demo / `~/agents-ensemble-demo` / 7979 / `ensemble_demo` (promote transcript line 1) | same (status.sh 21:40Z) | ✓ no drift |
| `current` symlink | `releases/v0.10.6-p2.3-dr4r1` | `releases/v0.10.7-p2.3-b65` (flip 21:34:08, exactly once) | ✓ EXPECTED flip |
| Demo daemon family | 65092/65094/65488/65491 (born 20:54Z) | 45232→45274→45277 (born 21:34:08Z; uptime continuous through end-of-batch — 563s at 21:43:35Z, no further restart) | ✓ EXPECTED restart, exactly once |
| Releases on disk | 7 + archives (dr4b2…dr4reset) | keep-3: `dr4d`, `dr4reset`, `b65` (5 evicted by retention) | ✓ EXPECTED (see FL-21 note) |
| Demo `.env` md5 | `1ba30c01…` | `1ba30c01…` unchanged at every check | ✓ no drift |
| `version smoke` | OK (0.10.5 == manifest) | OK (0.10.5 == manifest, on b65) | ✓ |

## 3. §4.1 cycle-#1 eligibility — per-clause evidence (EVIDENCE ONLY; dispatcher rules)

> **⚠ FLAG — clause 1 provenance (recorded verbatim per dispatch):** §4.1 clause 1 as canonically written requires an **ARI-DRIVEN upgrade cycle** (`system_upgrade` → promote → gates → soak → version verify → no rollback → committed). **This cycle is SCRIPT-driven** (`stage.sh` + `promote.sh` from the repo, dispatcher-invoked) — the ari-driven lane is exercised separately (T8 §4 uses `system_upgrade`, but as a refusal capture, not a completing cycle). Whether a script-driven clean cycle may credit the §7 ledger is **the dispatcher's ruling — NOT self-ruled here.** The journal-checkable machine evidence (ledger_check.py: `cycle 1: version=v0.10.7-p2.3-b65 txn=2026-08-23T21:39:13Z verdict=CLEAN`; f2-open ⇒ BLOCKED / f2-closed ⇒ NOT-READY "2 more needed") is recorded either way.

| Clause (phase3-plan D1 ↔ §4.1 canonical) | Evidence | Verdict |
|---|---|---|
| c1 — promote completes end-to-end: preflight → stop → flip → gate → **commit** (§4.1 cl.1) | §2.2 table + journal terminal `commit` event 21:39:13Z (`/tmp/b65-ev/journal-post-promote.txt`) | **PASS** ⚠ script-driven flag above |
| c2 — gates green in budget: livez ≤60s, readyz ≤120s, version verify, 300s soak (§4.1 cl.1) | livez ~3s, readyz ~4s, verify OK, soak "300s green"; promote exit 0 (`promote-b65.txt`) | **PASS** |
| c3 — no auto-rollback / sweep / halt in the cycle (§4.1 cl.1) | journal window = exactly 1 event (`commit`); readiness log-scan from the b65 boot anchor: **0** `Readiness] degraded` lines, **0** ` 503 ` lines (launcherlog-b65-window.txt, 101 lines) | **PASS** |
| c4 — post-cycle healthy: `/readyz` 200 `reasons:[]` after restart-less settle (§4.1 cl.3) | 21:40:12Z `checked_at` ready `[]`; 21:43:35Z ready `[]`; daemon uptime continuous (no restart post-commit) | **PASS** |
| c5 — no unintended work loss (§4.1 cl.4) | jobs before stop: exactly 1 (B6c's `b80abb8e`, completed) — **zero in-flight** at the promote stop; jobs after commit: same 1, zero lost/changed (`jobs-pre-promote.json` ↔ `jobs-post-promote.json`) | **PASS** (trivially — idle demo) |
| c6 — zero live contact (§4.1 cl.5) | §5 checkpoint table — ps + redacted-lsof byte-identical at all 4 points | **PASS** |
| c7 — restart cycle clean: respawn → gates green → no degradation attributable (§4.1 cl.2) | promote's own restart: SINGLE-TERM stop (~1s) → respawn 21:34:08 → livez ~3s / readyz ~4s green → 0 degraded lines in the boot window; T8 §4 then ran a full ari job + SSE session on the restarted daemon with zero anomalies | **PASS** (drill-restart, not ari-driven restart — same flag class as c1) |

## 4. T8 re-capture — the settled tool-driven refusal, end-to-end (the B6.5 fix's acceptance on the deployed binary)

### 4.1 SSE client (subscribed FIRST)

Endpoint `GET /api/notifications/stream` (B6c FL-14: alerts ride the generic `notification` event name, kind inside `data.event_type`). Client = B6c's transcript-logging stdlib client, every line arrival-stamped. Connected **21:41:38Z**: `event: connected` + `data: {"status": "connected"}` (`/tmp/b65-ev/sse-capture.log`).

### 4.2 The ari job (JAFP public path — B6c FL-15 precedent)

- Journal pre-capture: md5 `d9a912324c7edbf99b3d5a8aa8721ac4`, history 1 event (`commit`) — the CLEAN-journal baseline.
- `POST /api/jobs` (demo-authorized), `agent_id=ari`, `idempotency_key=b65-t8-recapture-1`, message instructing exactly ONE tool call, self-executed, no dispatch, no bash — refusal expected verbatim. Job `418af936-a2c4-4bed-b1bf-32f8e900cd01`: queued 21:41:54Z → processing 21:42:15Z (instance `00c224e4`, `agent_dir` = the **b65** release's ari — the fix-bearing deployment) → **completed 21:42:43Z**, `{"success": true}` (49s end-to-end incl. LLM turn).
- **Induction choice (cheapest legal refusal on a CLEAN journal):** `version=v0.10.6-p2.3-dr4r2` — genuinely NOT on disk (evicted by this promote's retention minutes earlier), so `_target_release_state` → `target-not-staged` fires at source order (upgrade_tools.py: explicit-version path → quarantine(empty) → not-staged, BEFORE the cap/cooldown entry checks — cap/cooldown refusals are unavailable at count 0 anyway and were NOT chased by dirtying the journal).
- **The tool call (raw, from the instance messages — `/tmp/b65-ev/ari-messages-raw.json`):**

```json
{"id": "call_-7323223907480101861", "name": "system_upgrade",
 "arguments": {"dry_run": false, "target_env": "demo", "version": "v0.10.6-p2.3-dr4r2"},
 "output": "Error: UPGRADE REFUSED — reason=target-not-staged: releases/v0.10.6-p2.3-dr4r2 not found. Run release_info(section=releases)."}
```

ari's final reply (21:42:40Z) echoed the refusal verbatim, as instructed.

### 4.3 The FOUR-WAY assert (all legs PASS)

| # | Leg | Observed | ✓ |
|---|---|---|---|
| 1 | **ari tool output** carries the token | `reason=target-not-staged` verbatim (§4.2 record) — the REAL `_refusal()` return shape `Error: UPGRADE REFUSED — reason=<token>: <msg>` | ✓ |
| 2 | **Journal** gains exactly ONE `refusal` event with the token | history 1 → 2; new event `{"ts":"2026-08-23T21:42:35Z","event":"refusal","detail":"releases/v0.10.6-p2.3-dr4r2 not found. Run release_info(section=releases). (reason=target-not-staged)"}` — the lib.sh `_refuse` detail twin, `_reason_token`-parseable; md5 `d9a91232…` → `4809b4d4e322c906014df156bc68934d` | ✓ |
| 3 | **SSE capture** holds the alert | `[21:42:35Z] event: notification` → `data.event_type == "upgrade_promote_refusal"`, `data.data`: `kind=upgrade_promote_refusal, source_event=refusal, reason=target-not-staged, detail=<verbatim>, version=v0.10.7-p2.3-b65, counters={24h:0}, cooldown_until=null, quarantined=[], run_id=null, ts=2026-08-23T21:42:35Z` | ✓ |
| 4 | **Classifier-spelling cross-check** | journal event name `refusal` ↔ SSE kind `upgrade_promote_refusal` ↔ `ALERT_KIND_BY_EVENT["refusal"]="upgrade_promote_refusal"` (upgrade_journal.py:378-384, deployed HEAD == repo HEAD `8a0f252c`) — single spelling, no variants, three-way match | ✓ |

**Timestamp coherence:** journal `ts` 21:42:35Z == SSE payload `ts` 2026-08-23T21:42:35Z == SSE client arrival stamp `[2026-08-23T21:42:35Z]` == tool-message `created_at` 21:42:35.086 — coherent to the second (the sink fires ON the append, by construction).

**Distinguished non-upgrade events in the same capture** (never to be mistaken for the alert): `instance_created` (21:42:12Z, the ari instance) and the `instance-completion` notification (21:42:40Z) — the broadcaster's pre-existing non-upgrade lane, consistent with F-B6c-3.

### 4.4 Post-capture journal state + cycle-cleanliness reasoning (for the ledger)

Journal now: `current=v0.10.7-p2.3-b65, previous=null, in_flight:null, 24h:0, cooldown:null, quarantined:[]`, history = [`commit` 21:39:13Z, `refusal` 21:42:35Z]. **The refusal is a HISTORY ENTRY ONLY — it does NOT affect cycle cleanliness:** §4.1 c3 (and `ledger_check.py VIOLATION_EVENTS`) counts `rollback` / `sweep_rollback` / `halt` only; a refusal is none of these. Post-capture checker (f2-closed): `cycle 1: … verdict=CLEAN`, `consecutive clean: 1`, gate `NOT-READY — 2 more clean cycle(s) at version v0.10.7-p2.3-b65 needed`; f2-open ⇒ `BLOCKED` (§9 hard block, unchanged). Evidence: `ledger-check-f2-{open,closed}.txt` re-run post-capture.

## 5. Live pid checkpoints (read-only, zero live contact)

| Checkpoint | Moment (UTC) | pid/ppid | lstart | Diff |
|---|---|---|---|---|
| DR-0 / drill start | 21:31:25 | 31150/31130 | Sat Aug 22 10:04:07 2026 | baseline |
| immediately before the flip | 21:33:53 | same | same | identical ✓ |
| immediately after the flip | 21:34:40 | same | same | identical ✓ |
| post-commit | 21:40:26 | same | same | identical ✓ |
| final (post-T8) | 21:43:35 | same | same | **identical ✓ (ps + redacted lsof byte-identical: `diff dr0-live-lsof-redacted.txt live-lsof-final-redacted.txt` empty)** |

## 6. Constraint compliance

- **Zero live contact:** every pipeline command targeted demo by install-dir anchor (`~/agents-ensemble-demo` — the §0.5/§0.6 discipline; live dir patterns anchored, lsof redacted pre-capture); §5 checkpoints byte-identical; no signal ever sent to a live pid.
- **Port-literal rule:** zero live-port literals in this file and all `/tmp/b65-ev/` evidence (grep-gated pre-commit); demo 7979 written freely.
- **Demo `.env`:** md5 `1ba30c01…` unchanged start→end (no knob set at any point — the §5(b) knob induction was NOT used this batch).
- **Repo writes:** this RESULTS file + the local drill tag `v0.10.7-p2.3-b65` (single tag at HEAD) only; `dist/ensemble-prod` gitignored (fresh build left in place — it is the byte-identical twin of the deployed release binary); `.agents/approver/active.md` (pre-existing dirty) NEVER staged.
- **Overrides (D-FA4.5):** `ENSEMBLE_ROLLBACK_SAFE=1` ×1, `ENSEMBLE_BINARY_VERSION=0.10.5` ×1 — both stage.sh author-call knobs, recorded §2.1.
- **No mid-drill debugging:** the B6c T8 failure was fixed by the PROMOTED CODE, not by any drill-side patch; the capture ran against the deployed binary exactly as dispatched.

## 7. Friction log — B6.5 additions

| # | Where | Doc/ruling says | Observed | Classification |
|---|---|---|---|---|
| FL-19 | PYZ-differential technique | B6a precedent scans the `run_app` CArchive entry directly | The B6.5 fix lives in a PYZ module: entry is `PYZ.pyz` (not `PYZ-00.pyz`) and `ZlibArchiveReader.extract` returns a code object (marshal.dumps needed before byte-scan). Proof script preserved (`/tmp/b65-ev/pyz_diff.py`) — B8 doc note for the next module-level proof | **technique note** |
| FL-20 | c5 job-list capture | D1 c5: "job id list before stop ↔ terminal states after" | `GET /api/jobs?limit=100` is the working basis (list key `job_id`); `GET /api/tasks?status=PROCESSING` returns `{"error":"Not found"}` (different route shape). Demo was idle so c5 was trivial — a busy-demo cycle needs the correct tasks route documented | **doc gap (minor)** |
| FL-21 | retention with previous=null | runbook R3.2 note: "journal current/previous re-anchor on the next commit" | With the fresh journal's `previous=null`, commit set `previous=<none>` and keep-3 retention evicted the ENTIRE prior drill set including the just-superseded dr4r1 — i.e. post-commit there is NO on-disk rollback target (rollback.sh would have nothing previous to re-gate; recovery is ADR-028 flip-forward territory). Expected per keep-3 semantics, but T9 cycle planners should know each clean cycle on a fresh-anchored journal strands the superseded release | **OK-but-noted (T9 planning input)** |
| FL-22 | job detail API cosmetics | — | `GET /api/jobs/{id}` renders `created_at: 2026-08-24T04:42:12.706468+00:00` (instance-creation time, local-rendered with a +00:00 suffix) vs the POST response's correct `2026-08-23T21:41:54.544367+00:00` — cosmetic inconsistency only; status/result fields correct | **OK-but-noted** |

## 8. Findings + final state handoff

1. **F-B6.5-1 (T8 seam closure ACCEPTED on the deployed binary):** the B6c gap (tool-refusal lane alert-silent: `_refusal()` pure formatter, zero `journal_history_append` in upgrade_tools.py) is closed END-TO-END in production shape: the deployed b65 binary journals the refusal in-process (exactly one event, lib.sh detail twin) and the B3 sink → broadcaster → SSE chain delivers `upgrade_promote_refusal` with the D-FA2.2 token, timestamps coherent to the second. The B6c verdict's precondition ("structurally unpassable until the seam is added") is discharged by the seam itself riding `_refusal()` — no doc re-scope needed.
2. **F-B6.5-2 (cycle-#1 evidence, ELIGIBILITY DISPATCHER-RULED):** journal-derived cycle 1 = CLEAN at `v0.10.7-p2.3-b65` (txn 21:39:13Z); c1–c7 all evidenced (§3) WITH the clause-1 script-vs-ari flag — §4.1 clause 1 canonically requires an ari-driven upgrade cycle; this rehearsal was script-driven. Ledger machine state: consecutive-clean 1; gate BLOCKED (f2-open) / NOT-READY (f2-closed, need 2 more at the same version).
3. **F-B6.5-3 (final demo state, T9 handoff):** demo green on `v0.10.7-p2.3-b65` (serving 0.10.5 per manifest binary_version; family 45232→45277, uptime continuous); journal `current=b65, previous=null`, counters 0, cooldown null, quarantine [], history [commit 21:39:13Z, refusal 21:42:35Z] — the refusal entry is cycle-neutral (§4.4); lock free; releases on disk `dr4d / dr4reset / b65`; `.env` bit-exact; single tag `v0.10.7-p2.3-b65` @ `8a0f252c`; live untouched throughout.
4. **Evidence dir:** `/tmp/b65-ev/` (25 items: promote/stage transcripts, PYZ proofs ×2, sse-capture.log, ari-messages-raw.json, job-*.json ×4, journal snapshots ×3, ledger-check ×2, live checkpoints ×3, launcherlog window, pyz_diff.py, sse_client.py, dist-stale binary).

## 9. Verdicts (final)

`B6.5-PROMOTE PASS: REAL build v0.10.7-p2.3-b65 @ 8a0f252c promoted clean to demo — livez ~3s/≤60s, readyz ~4s/≤120s, version verify OK, 300s soak green, journal commit 21:39:13Z (single commit event, zero violations); deployed-fix assert PROVEN (PYZ-differential on the deployed current binary + boot-log marker); §4.1 c1–c7 evidence captured, clause-1 script-vs-ari FLAGGED for the dispatcher's eligibility ruling; live untouched at all checkpoints`

`T8 PASS: refusal→journal→SSE captured end-to-end — ari → system_upgrade(dry_run=false, version NOT staged) → tool output reason=target-not-staged verbatim → journal +exactly ONE refusal event (21:42:35Z, lib.sh detail shape) → SSE notification event_type=upgrade_promote_refusal reason=target-not-staged (arrival 21:42:35Z) → classifier mapping cross-checked (refusal↔upgrade_promote_refusal↔ALERT_KIND_BY_EVENT); timestamps coherent to the second; capture-broken-again contingency NOT triggered`

# P2.3 B6c — T8 SSE capture (tool-driven) + R3.2-bis FINAL reset + the single B6 evidence commit

- **Date:** 2026-08-23 · **Recorded by:** worker (B6c dispatch)
- **Branch:** `feature/self-restart-p2p3-ladder-drills` @ `c0993119` (runbook @ same HEAD)
- **Runbook:** `docs/runbooks/upgrade-drills.md` §5 T8 (SSE alert capture) + §5 R3.2 (final reset → T9 baseline)
- **Verdict lines:**
  - `T8 FINDING-RECORDED: the tool-driven in-daemon refusal FIRED exactly as designed (ari → system_upgrade(dry_run=false) → verbatim "Error: UPGRADE REFUSED — reason=rollback-cap-exceeded: (3/24h) … ADR-005 D2.") but NO refusal event was journaled in-process and NO SSE upgrade alert materialized — the subscribed client saw only the ari instance_created + instance-completion notifications. Root cause (source-cited, HEAD c0993119 == deployed binary fd7c1ac0): the tool layer never calls journal_history_append — _refusal() (upgrade_tools.py:634-637) is a pure string formatter and the cap-refusal return (:1728-1737) performs no journal write, so the B3 sink (registered, wired, correctly spelled) is unreachable from the tool lane. Per the B6 ruling: recorded, NOT debug-fixed mid-drill.`
  - `R3.2-bis PASS: FINAL journal reset executed on the real demo journal — exact-path assert, archive-then-fresh (state.json.archive-dr4-final-20260823-2105, md5 9793404f… preserved), fresh init via the dr4reset stage precedent (binary reused, authorized), counters 24h=0 / cooldown null / quarantined [] / history [] (halt CLEARED), daemon uptime CONTINUOUS (journal-file-only reset, no restart, livez 740s at verify); ledger_check.py captured for BOTH f2-states (open ⇒ BLOCKED, closed ⇒ NOT-READY — the FL-12 token pair); demo green on dr4r1; live untouched.`

**Redaction rule:** the live port is rendered `<live-port>` throughout — zero live-port literals in this file or any evidence file under `/tmp/b6c-ev/` (lsof output sed-redacted BEFORE capture). Demo port 7979 is not restricted.

---

## 1. T8 input state (inherited, verified)

F-B6b-10 handoff confirmed by direct read at 21:02:40Z (journal md5 `9793404f653e21f6b4c1309cf7d97378`, 18 history events):

| Item | Observed | Match |
|---|---|---|
| Journal | `current=dr4r1, previous=dr4r1 (degenerate-equal), in_flight:null`, `rollback_window_count {24h:5}`, cooldown `2026-08-23T21:04:11Z`, quarantined `[dr4b1, dr4b2, dr4b3, dr4c]` | ✓ F-B6b-10 |
| Freshest halt | `halt — sweep-rollback reached cap 3/24h (count=5)` @ 20:54:14Z | ✓ standing |
| Demo serving | `/livez` 200 v0.10.5, `/readyz` 200 `reasons:[]`, `current -> releases/v0.10.6-p2.3-dr4r1`, lock free, version smoke OK | ✓ green |
| Live baseline | pid **31150**/ppid 31130, lstart `Sat Aug 22 10:04:07 2026`, `./ensemble-prod` — identical to the all-day baseline | ✓ read-only |
| Ari on demo | `system_upgrade` present in deployed `agents/ari/meta.json` tools.allow (P2.2 wiring live in the dr4r1 release) | ✓ reachable |

## 2. T8 execution — tool-driven path (B3-settled)

### 2.1 SSE client (subscribed FIRST)

- Endpoint discovered from source: `GET /api/notifications/stream` (daemon/routers/notifications.py; mounted at api.py:1689; the NotificationBroadcaster singleton is wired at api.py:607 and the B3 sink `register_alert_sink(broadcaster_alert_sink(...))` at api.py:613-615).
- Client: transcript-logging stdlib Python client (`/tmp/b6c-ev/sse_client.py`), every line arrival-stamped. Connected **21:02:45Z**: `event: connected` + `data: {"status": "connected"}` (log: `/tmp/b6c-ev/sse-capture.log`, kept connected through the §4 reset window).

### 2.2 The ari job (JAFP public path)

- `POST /api/jobs` (demo-authorized), `agent_id=ari`, `idempotency_key=b6c-t8-sse-capture-1`, message instructing exactly ONE tool call: `system_upgrade(target_env="demo", version="v0.10.6-p2.3-dr4r2", dry_run=false)`, self-executed, no dispatch, no bash — refusal expected and wanted verbatim.
- Job `b80abb8e-e014-4480-99da-20e8afed9b83`: queued 21:03:07Z → active 21:03:23Z (instance `5d50289f`, titled "System Upgrade Drill") → **completed 21:04:13Z**, `result_summary {"success": true}`.
- **The tool call (raw, from the instance messages — `/tmp/b6c-ev/ari-messages-raw.json`):**

```json
{"id": "call_-7323266582275155693", "name": "system_upgrade",
 "arguments": {"dry_run": false, "target_env": "demo", "version": "v0.10.6-p2.3-dr4r2"},
 "output": "Error: UPGRADE REFUSED — reason=rollback-cap-exceeded: (3/24h) — halted-for-human; see release_info(section=journal). ADR-005 D2."}
```

ari's final reply (21:04:04Z) echoed the refusal verbatim, as instructed. The in-daemon refusal fired at the cap check (count 5 ≥ 3), exactly the D-FA2.2 token `rollback-cap-exceeded`, before cooldown/dry-run/confirmation branches are reachable (upgrade_tools.py:1728-1737 precedes them).

### 2.3 The three-way evidence check (the heart of T8)

| Evidence | Result |
|---|---|
| SSE upgrade alert | **ABSENT.** The capture log's only substantive events: `instance_created` (21:03:19Z, the ari instance) and `notification` (21:04:04Z) — the latter is the **instance-completion** notification (`status: "COMPLETED"`, agent ari), NOT an upgrade alert. Zero `upgrade_promote_refusal` / `upgrade_cap_halt` / `upgrade_auto_rollback` event_types; only pings otherwise. |
| Journal after the tool call | **byte-identical** — md5 `9793404f…` before AND after, 18 → 18 history events, no new `refusal` entry. The tool lane wrote nothing. |
| Alert chain (source, HEAD == deployed build `fd7c1ac0`) | Registered and correctly spelled (§3) — but its only daemon-process trigger points never fired (§2.4). |

### 2.4 Root cause — code-faithful, recorded for B8 (NOT fixed mid-drill)

The dispatch's expected chain was: *tool refuses → refusal journaled IN-PROCESS → B3 sink fires → SSE captured*. The gap is at the second link:

1. `upgrade_tools.py` contains **zero** `journal_history_append` calls (grep-verified). Every refusal — including the cap refusal — returns via `_refusal(label, reason, message)` (upgrade_tools.py:634-637), a **pure string formatter**. The tool layer surfaces D-FA2.2 tokens to the caller but persists nothing.
2. The B3 alert emitter `_emit_terminal_class_alert` (upgrade_journal.py:471-516) fires ONLY from `journal_history_append` (upgrade_journal.py:294). Within the daemon process the only callers are `consume_pending_action` (event `nonce_consumed`) and `reconcile_pending_op` (event `sweep`) — **neither is in `ALERT_KIND_BY_EVENT`**, so by construction no daemon-process write currently reaches the sink.
3. The `refusal` events seen in B6b (20:41:15-19Z) came from promote.sh's shell-side `journal_refusal` (lib.sh:1234-1241) — a direct journal-file write from bash that structurally cannot call the Python sink. So NO refusal lane (shell or tool) currently produces an SSE alert; the sink is live but unreachable from any refusal path.
4. Consequence for the alert design: the 3 SSE kinds are today reachable only if a future daemon-process code path appends `halt`/`rollback`/`sweep_rollback`/`quarantine`/`refusal` via the Python helper — at HEAD, none does. (halt/rollback events are all written shell-side by promote/rollback/sweep scripts.)

**B8 fix-shape pointer (one line, not a fix):** either the tool refusal paths call `uj.journal_history_append(install_dir, "refusal", …)` before returning (making the dispatch's expected chain true), or the docs re-scope T8's expectation to "SSE fires for daemon-process terminal writes" and a different trigger is chosen.

## 3. Classifier-spelling assert (mandatory step 3)

Cross-checked BOTH spellings against the deployed binary's source (HEAD `c0993119`, the exact commit of the real build `fd7c1ac0` staged into dr4r1 → the running daemon):

- **Mapping (upgrade_journal.py:378-384):** `ALERT_KIND_BY_EVENT = {"halt": "upgrade_cap_halt", "refusal": "upgrade_promote_refusal", "rollback": "upgrade_auto_rollback", "sweep_rollback": "upgrade_auto_rollback", "quarantine": "upgrade_auto_rollback"}` — journal event `refusal` ⇒ SSE kind `upgrade_promote_refusal`. ✓ correctly spelled, single spelling, no variants.
- **Emit shape (broadcaster_alert_sink, upgrade_journal.py:408-446):** `{"event_type": "<kind>", "data": {kind, source_event, reason, detail, version, counters, cooldown_until, quarantined, run_id, ts}, "timestamp": <ts>}`; the stream layer renders non-`instance_created` events as SSE `event: notification` with that dict as data (routers/notifications.py:75-80).
- **Observed:** no `event_type` matching any alert kind arrived (log verbatim §2.3), and no `refusal` journal event was appended by the attempt (journal byte-identical) — so there is no journaled-vs-SSE spelling divergence to reconcile; the cross-check result is **"mapping correct, chain never invoked"** (consistent with §2.4). The one `notification` event that DID arrive is `instance-completion` (a different, pre-existing SSE consumer of the broadcaster) — deliberately distinguished here so it is never mistaken for the T8 alert.

## 4. R3.2-bis FINAL reset — transcript (this reset is LAST; T9 baseline)

| Step | Command / check | Result |
|---|---|---|
| 1. Pre-touch asserts | `status.sh demo`: in_flight null, lock free; probes green; journal = F-B6b-10 state | ✓ |
| 2. **EXACT-PATH assertion** | `$JP = /Users/nguyenminhkha/agents-ensemble-demo/releases/state.json`; case-anchored under `$HOME/agents-ensemble-demo/`; explicit NOT-under-`$HOME/agents-ensemble/` check | ✓ PASS before ANY touch |
| 3. Stale-break artifact (FL-13 handoff) | `mv releases/rollback.lock.d.stale.65094 → /tmp/b6c-ev/` (mv, never rm — sweep evidence preserved; releases/ clean) | ✓ |
| 4. **Archive-then-fresh** | `mv state.json → state.json.archive-dr4-final-20260823-2105` (21:05Z); canonical path verified ABSENT after; archive md5 `9793404f…` == pre-reset | ✓ |
| 5. Stage attempt 1 | `VERSION=v0.10.6-p2.3-dr4reset … stage.sh demo` → **exit 78**: tag-guard refused — `git describe --exact-match HEAD` returns `dr4a` (FOUR tags sat at HEAD after B6b's legs: dr4a/dr4b/dr4d/dr4reset) | FL-2 recurrence, disclosed |
| 6. Tag rotation (B6b precedent) | `git tag -d v0.10.6-p2.3-dr4a v0.10.6-p2.3-dr4b v0.10.6-p2.3-dr4d` (all @ c0993119 — same commit, no history change) → describe = `v0.10.6-p2.3-dr4reset` (single-tag HEAD) | ✓ |
| 7. Fresh init via stage | `VERSION=v0.10.6-p2.3-dr4reset ENSEMBLE_BINARY_VERSION=0.10.5 ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh demo` (21:05:57→21:06:30Z, exit 0) — **binary REUSED** (`dist/ensemble-prod` sha `fd7c1ac0…` == B6a's fresh real build; "using existing binary"; task-authorized real-build reuse) | ✓ journal auto-init |
| **Override uses (D-FA4.5)** | `ENSEMBLE_ROLLBACK_SAFE=1` ×1 + `ENSEMBLE_BINARY_VERSION=0.10.5` ×1 (both stage.sh author-call knobs, same as B6b's dr4reset precedent) | recorded |
| 8. **Post-reset verification** | raw journal: `{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}` — counters **0**, cooldown **null**, quarantined **[]**, history **[]** ⇒ **halt CLEARED**; `current -> releases/v0.10.6-p2.3-dr4r1` unchanged; `/livez` 200 v0.10.5 **uptime 740s** (same daemon — booted 20:54:08Z, NO restart; journal-file-only reset); `/readyz` ready; version smoke OK | ✓ all four R3.2 verify clauses + uptime continuity |
| 9. Demo `.env` | md5 `1ba30c018078a60281cba4baeacc03c4` == B6a/B6b baseline; knob lines 0 | ✓ untouched |

Post-reset releases on disk (7 + 2 archives): `dr4b2 [QUAR]`, `dr4b3 [QUAR]`, `dr4c [QUAR]`, `dr4d`, `dr4r1`, `dr4r2`, `dr4reset` + `state.json` + archives `dr4-20260823-1948` / `dr4-final-20260823-2105`. Same op-notice as B6b §2: fresh journal ⇒ `[QUARANTINED]` labels cleared (quarantine is journal state, not disk state); none of those versions is a T9 target.

### 4.1 `ledger_check.py` — BOTH f2-states, verbatim

```text
2026-08-23T21:06:52Z — uv run python scripts/upgrade/ledger_check.py \
    --journal ~/agents-ensemble-demo/releases/state.json --f2-state open   (exit 0)

ledger-check: journal=/Users/nguyenminhkha/agents-ensemble-demo/releases/state.json
f2-state: open
cycles: 0
staleness: none (all cycles at the current version)
current version: None
consecutive clean: 0 (need 3, ADR-021)
gate verdict: BLOCKED
  - F2-open: the unauthenticated loopback API user-origin forge lane is open — gate hard-blocked regardless of cycle count (runbook §9)
note: coverage: journal-checkable clauses of test-strategy.md 4.1 only; clauses 3-5 (readiness log-scan, work-loss resume evidence, live-pid checkpoint) are external evidence audited in RESULTS files — the gate consumer folds both
```

```text
2026-08-23T21:06:53Z — same journal, --f2-state closed   (exit 0)

ledger-check: journal=/Users/nguyenminhkha/agents-ensemble-demo/releases/state.json
f2-state: closed
cycles: 0
staleness: none (all cycles at the current version)
current version: None
consecutive clean: 0 (need 3, ADR-021)
gate verdict: NOT-READY
  - no clean cycle credited (consecutive-clean 0 < N=3)
  - 3 more clean cycle(s) at version None needed
note: coverage: journal-checkable clauses of test-strategy.md 4.1 only; clauses 3-5 (readiness log-scan, work-loss resume evidence, live-pid checkpoint) are external evidence audited in RESULTS files — the gate consumer folds both
```

**FL-12 closed as a pointer:** both tokens captured on the SAME fresh journal — `--f2-state open` ⇒ **BLOCKED** (§9 hard block regardless of count), `--f2-state closed` ⇒ **NOT-READY** (count < N). The checker's own tokens are authoritative; docs must match (B8).

## 5. Live pid checkpoints (read-only, zero live contact)

| Checkpoint | Moment (UTC) | pid/ppid | lstart | Diff |
|---|---|---|---|---|
| B6c start | 20:57:5x | 31150/31130 | Sat Aug 22 10:04:07 2026 | identical ✓ |
| post-T8 capture | 21:05:0x | same | same | identical ✓ |
| final (post-reset) | 21:06:5x | same | same | **identical ✓ (ps + redacted lsof byte-identical)** |

## 6. Friction log — B6c additions

| # | Where | Doc/ruling says | Observed | Classification |
|---|---|---|---|---|
| FL-14 | T8 SSE endpoint | runbook names no route | Endpoint is `GET /api/notifications/stream`; alert notifications ride SSE event name `notification` (generic) with the kind in `data.event_type` — only `instance_created` gets a dedicated event name. A capture client must parse data payloads, not event names (B8 doc note) | **doc gap** |
| FL-15 | T8 ari-job mechanics | runbook says "dispatch an ari job" | JAFP `POST /api/jobs` with plain `agent_id`+`message` works; ari executed the single tool call itself (~55s end-to-end incl. LLM turn); `idempotency_key` recommended for dedup safety. A tightly-scoped "ONE tool call, refusal expected, return verbatim" message held ari on-target | **OK-but-noted** |
| FL-16 | T8 expectation chain | dispatch/runbook imply tool refusal ⇒ journaled in-process ⇒ SSE | The chain's second link does not exist at HEAD (§2.4) — tool refusals never journal; the T8 SSE capture is structurally unpassable until the seam is added (or the expectation re-scoped) | **runbook/plan gap → F-B6c-1** |
| FL-17 | R3.2-bis stage | B6b precedent: single-tag HEAD | After B6b's legs, FOUR tags sat at HEAD (dr4a/dr4b/dr4d/dr4reset) → stage.sh tag-guard exit 78 (describe picks one). Fix = B6b's own rotation (delete extras, all @ same commit). B8: the reset procedure should mandate checking `git tag --points-at HEAD` count first | **FL-2 recurrence, proceduralized** |
| FL-18 | reset evidence hygiene | runbook restore: "no stray lock dirs" | FL-13's stale-break artifact owned by this reset via mv-to-evidence-dir (no rm) — releases/ now clean for T9 | **closed** |

## 7. Findings

1. **F-B6c-1 (T8 FINDING, seam gap):** the tool-driven refusal lane is alert-silent by construction at HEAD: `_refusal()` is a pure formatter, `upgrade_tools.py` has zero `journal_history_append` calls, and the only daemon-process appenders write non-terminal-class events (`nonce_consumed`, `sweep`). The B3 sink + `ALERT_KIND_BY_EVENT` chain is correctly wired and spelled but currently unreachable from ANY refusal path (shell refusals journal via file-write, bypassing the Python sink). Evidence: `/tmp/b6c-ev/` (sse-capture.log, ari-messages-raw.json, job-*.json, journal md5s). Fix-shape pointer in §2.4 — decision belongs to B8, not mid-drill.
2. **F-B6c-2 (T9 starting baseline, final):** journal all-zero (`current/previous` null — re-anchor happens on T9's first commit), halt cleared, quarantine empty, cooldown null, lock free, releases/ clean of artifacts; demo green on dr4r1 (v0.10.5 serving, uptime continuous through the reset); `.env` bit-exact, knob 0 lines; checker baseline captured both ways (BLOCKED f2-open / NOT-READY f2-closed, cycles 0, consecutive clean 0). Single-tag HEAD `v0.10.6-p2.3-dr4reset` @ `c0993119`.
3. **F-B6c-3 (incidental, disclosed):** the ari instance-completion SSE notification (21:04:04Z) confirms the broadcaster's NON-upgrade lane is live end-to-end on the deployed binary — the T8 gap is specific to the upgrade alert seam, not the SSE infrastructure.

## 8. Final state handoff (to T9 / commit step)

- Journal: fresh/zeroed (§4 step 8); demo daemon pid 65491-family serving dr4r1 since 20:54:08Z, untouched by the reset.
- Live: pid 31150 byte-identical at every checkpoint; zero live contact; live port rendered `<live-port>` everywhere.
- Repo: B6a+B6b+B6c RESULTS files staged+committed as the single B6 evidence commit (this file is the third); no other repo writes; `dist/ensemble-prod` gitignored; tags single at HEAD.
- Evidence: `/tmp/b6c-ev/` (13 items: sse-capture.log, sse_client.py, job-payload/create-resp/final.json, ari-transcript.txt, ari-messages-raw.json, ledger-check-f2-{open,closed}.txt, stage-dr4reset-b6c{,-attempt2}.txt, rollback.lock.d.stale.65094/).

## 9. Verdicts (final)

`T8 FINDING-RECORDED: tool-driven refusal captured verbatim from the standing halt (rollback-cap-exceeded, cap 5/3) via ari → system_upgrade(dry_run=false); NO in-process refusal journaling and NO SSE upgrade alert — root cause source-cited (tool layer never appends journal history; sink unreachable from refusal lanes at HEAD), full transcript preserved, NOT debug-fixed per the B6 ruling; classifier-spelling assert: mapping correct (refusal → upgrade_promote_refusal), chain never invoked`

`R3.2-bis PASS: FINAL reset — exact-path assert, archive-then-fresh (md5-preserved), dr4reset stage fresh init (binary reuse authorized), counters 0 / cooldown null / quarantine [] / history [] (halt CLEARED), daemon uptime continuous, checker captured BOTH f2-states (BLOCKED / NOT-READY), demo green on dr4r1, .env bit-exact; live untouched`

# FORENSICS — promote-executor preflight hang, 2026-10-28 (board wi-fc1786a2)

**Lane:** diagnosis-only, read-only forensics. **No fix. No commit. No live writes.**
**Subject:** run_id `r-20261028-170150-2d54`, transient unit `ensemble-upgrade-r-20261028-170150-2d54.service`
**Window:** 2026-10-28 17:05:01Z → 17:42:44Z (37 min 43.31 s wall)
**Executed script:** `/usr/bin/bash /home/nea/ensemble-src/scripts/upgrade/promote.sh live --version v0.27.11 --f2-verified-closed`
**Report written:** 2026-10-29 · **NOT COMMITTED** (per commission)

---

## 0. VERDICT IN ONE PARAGRAPH

The executor did **not** block on I/O, a pipe, the database, or a stale lock. It **burned 100 % of one core for the entire 37 min 43 s** inside a **single, unbounded, per-character bash JSON walk** — the legacy `journal_read` balance scan in `scripts/upgrade/lib.sh` (blob `f81bcf6ba`) reading a **455,783-byte / 2,949-entry** `state.json`. The call site is `promote.sh:225` (`J="$(journal_read)"`), which sits in preflight **after** the last log line the executor ever wrote and **before** any journal txn was opened. The walk is **O(N²)** in bash (`${json:i:1}` per character) and was measured at **k ≈ 1.3–1.6 × 10⁻⁸ s/byte²** ⇒ a full walk over that journal costs **45–56 min**; SIGTERM landed ~70 % of the way through. The "lost stdout" was **not** lost to a broken pipe: the payload's stdout is owned by systemd (`StandardOutput=append:…/data/upgrade.log`), it worked for exactly **69 ms** (2 lines), and then there was nothing left to write because the payload produced no output. The 64 `Broken pipe` printf errors in `upgrade.log` are **historic** (lines 3–585, earlier attempts) and belong to **none** of the Oct-28 attempt (lines 610–611). The root cause is a **partial splice**: the P2 "O(1) python splice" replaced the journal **write** path only; the **read** path (`journal_read`) and the **read-modify-write** path (`journal_update`) remained the legacy bash walk — and the promote preflight calls the read path.

---

## 1. TIMELINE — 2026-10-28 16:50Z → 17:50Z

| time (UTC) | source | event |
|---|---|---|
| 17:04:20 | journal (pre-manual backup) | `nonce_consumed` — run_id `r-20261028-170150-2d54`, kind=upgrade env=live target=v0.27.11 |
| 17:04:21 | metadata / operator | Ari arms the scripted promote (3-factor nonce ceremony complete) |
| ~17:04:2x | lock artifact | orphaned `rollback.lock.d` (owner 2182592, run `run-20261028-095749-2182592`, heartbeat `1793181886` = 10:04:46Z) house-stale-broken → moved to `rollback.lock.d.stale.2480459` (heartbeat 25,165 s > 300 s, owner pid dead) |
| **17:05:01.279951** | journald `Started` | transient unit starts (`systemd-run --wait --collect`), `bash …/promote.sh live --version v0.27.11 --f2-verified-closed` |
| 17:05:01.28x | `upgrade.log:610` | `upgrade-promote[live]: resolved env: target=live dir=/home/nea/agents-ensemble port=9797 db=ensemble_prod` |
| **17:05:01.349146** | `upgrade.log:611` (**last write ever**) | `upgrade-promote[live]: preflight: lock · integrity · journal txn · entry checks` |
| 17:05:01.35x | inferred (code) | `lock_acquire` → `mkdir` succeeds (orphan already broken) → `owner`/`run_id`/`heartbeat` written; `_trap_install_signal_handlers "promote:preflight"` (`:179`); `lock_heartbeat` (`:182`) → heartbeat epoch written **once**; floor probe (`:209`) → python3 OK (silent) |
| **17:05:01.4x** | inferred (code) | **ENTER `journal_read` (promote.sh:225) — legacy O(N²) brace-walk over 455 KB / 2,949 entries. No output. 100 % of one core.** |
| 17:05:11.240351 | daemon log | `[system-execution] fired promote executor run_id=r-20261028-170150-2d54 pid=2480938 service=… (unit in flight; reaper observes unit exit via --wait client)` |
| **17:16:11** | journal row | `executor_still_running` — "run_id=r-20261028-170150-2d54 pid=2480938 still running after 660s — **benign-detach (no kill**; own process group; correlate with data/upgrade.log)" → **the daemon observed the stall and deliberately did nothing** |
| 17:16:11 → 17:42:44 | journald | **no output of any kind**; journal **unchanged**; lock heartbeat **frozen** at its 17:05:01 value |
| **17:42:44.415964** | journald `Stopping` | systemd SIGTERMs the unit (operator DevOps lane) |
| 17:42:44 | journal row | `halt` — "killed by SIGTERM at phase **promote:preflight** (…death-anchored journaling; **signal handler installed 2026-09-28**)" ⇒ the TERM trap **was** installed ⇒ the executor was past `promote.sh:179`; the trap journalled the halt and released the lock (lock dir removed) |
| 17:42:44.590524 | journald | `Main process exited, code=exited, status=143/n/a` (trap → `exit 143`) |
| 17:42:44.591262 | journald | **`Consumed 37min 43.148s CPU time, 7.6M memory peak, 0B memory swap peak`** |
| 17:43:19.589053 | daemon log | `upgrade_journal_sweep: reconcile — pending_op r-20261028-170150-2d54 closed — terminal event 'halt' at 2026-10-28T17:42:44Z` |
| 17:44:05 | journal | manual release (operator DevOps lane) — `current → releases/v0.27.11`, daemon restarted |
| 17:49 / 17:52 | state.json | journal hard-trimmed (2,949 → 163 entries); backups preserved the pre-trim state |

**Rows in the window — exactly FOUR** (from `state.json.pre-manual-v02711.20261028T174405Z.bak`, 455,783 B, 2,949 entries): `nonce_consumed` 17:04:20 · `executor_still_running` 17:16:11 · `halt` 17:42:44 · `sweep` 17:43:19. **No `python_floor_probe` row** ⇒ the executor never reached `promote.sh:235` ⇒ it was still inside `journal_read` at `:225` when killed.

---

## 2. EXPLICIT ANSWERS TO THE COMMISSION'S QUESTIONS

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | **Did the heartbeat progress during the window?** | **NO.** The lock heartbeat was written **once** at 17:05:01 (`lock_heartbeat`, `promote.sh:182`) and never refreshed — the executor never reached another `lock_heartbeat` call site. The journal's `in_flight.last_heartbeat` was never written either (no txn was ever opened). The lock dir was deleted by the trap's `lock_release` at 17:42:44, so no promote-side lock artifact survives. | `upgrade.log` unchanged after 17:05:01.349; the 4 journal rows; surviving lock artifact is the *morning stage's*, not the promote's |
| 2 | **When did the stdout reader die relative to spin start?** | **PREMISE FALSIFIED — there is no daemon-side stdout reader.** The payload's stdio is owned by systemd (`--property=StandardOutput=append:<install>/data/upgrade.log`); the daemon's client `Popen` of the `--wait` unit uses `stdout=subprocess.DEVNULL`, `stderr=subprocess.PIPE` (`upgrade_journal.py:2743-2751`, comment: "The CLIENT's stdio is not the payload's stdio"). The writer worked for **69 ms** (2 lines) and then had nothing to write. **STALL-FIRST**, and the stall was the payload's, not a reader's. | `upgrade_journal.py:2536-2537`, `:2743-2751`, `manager.py:5428`; `upgrade.log` lines 610-611 |
| 3 | **What phase was the executor in at SIGTERM?** | **Preflight step 1b — inside `journal_read` (the legacy brace-walk) called from `promote.sh:225`.** Before: journal init, floor probe. After (never reached): the `python_floor_probe` journal row (`:235`), `adopt_stale_txn` (`:240`), `promote_entry_check` (`:245`), `supervision_preflight` (`:251`), integrity verification (`:289`), `txn open` (`:346`). **No txn was opened, no release was flipped, the daemon was never touched.** | absence of the `python_floor_probe` / `integrity:` / `txn open:` rows+log lines; `halt` row says "phase promote:preflight" |
| 4 | **Was any journal/lock row written during the 37 minutes?** | **NO.** Zero journal rows between 17:05:01 and 17:42:44 (the only window row is the daemon's own `executor_still_running` at 17:16:11). Zero lock-heartbeat progressions. The `halt` row (17:42:44) is written by the death-anchored trap **at** the kill, not during the spin. | the four rows enumerated above |
| 5 | **Was PG / pg_dump involved?** | **NO.** The spin was a **local file read** of `state.json`; the executor never reached a DB call, never opened a txn, never touched `ensemble_prod`. journald's postgres unit has **no entries** for 16:55–17:45; `journalctl -k` has **no entries** for 17:00–17:45; `sar` shows the box 66.9–81.1 % idle. **No `pg_dump` exists on the promote preflight path** at the executed revision (see §4, candidate 3). | §3.5 host evidence; code grep at blob `f81bcf6ba` |
| 6 | **What do the 09:51 / 09:54 stage SIGTERMs share with this failure?** | **Same symptom class, different code path.** Both are "silent 100 %-CPU burn inside the upgrade pipeline's early phase, with no journal/lock progression and no deadline enforcement". The two stage halts are journalled as `halt … at phase **stage:lock**` (09:51:36 and 09:54:59) and their last log marker was **"computing manifest checksums (per-file sha256 + tree aggregates)"** — i.e. sha256 over ~5,300 payload files, also unbounded and also unlogged during the burn. **Shared root architectural defect: the pipeline's timeout/ceiling machinery is boundary-only** (`PROMOTE_TOTAL_DEADLINE_S=900` is checked *between* phases, never *inside* one), and the daemon's 660 s liveness probe classified a >11-minute preflight as "benign-detach (no kill)". | §3.4; `stage-v02712` logs; journal rows 09:51:36 / 09:54:59; the same class recurs on 2026-10-19 (14:00:44), 2026-10-23 (06:25:16) |

---

## 3. VERBATIM ARTIFACT EXCERPTS

### 3.1 journald — the transient unit
```
Oct 28 17:05:01 ensemble-vm systemd[1]: Started ensemble-upgrade-r-20261028-170150-2d54.service - /usr/bin/bash /home/nea/ensemble-src/scripts/upgrade/promote.sh live --version v0.27.11 --f2-verified-closed.
Oct 28 17:42:44 ensemble-vm systemd[1]: Stopping ensemble-upgrade-r-20261028-170150-2d54.service - /usr/bin/bash /home/nea/ensemble-src/scripts/upgrade/promote.sh live --version v0.27.11 --f2-verified-closed...
Oct 28 17:42:44 ensemble-vm systemd[1]: ensemble-upgrade-r-20261028-170150-2d54.service: Main process exited, code=exited, status=143/n/a
Oct 28 17:42:44 ensemble-vm systemd[1]: ensemble-upgrade-r-20261028-170150-2d54.service: Failed with result 'exit-code'.
Oct 28 17:42:44 ensemble-vm systemd[1]: Stopped ensemble-upgrade-r-20261028-170150-2d54.service - /usr/bin/bash /home/nea/ensemble-src/scripts/upgrade/promote.sh live --version v0.27.11 --f2-verified-closed.
Oct 28 17:42:44 ensemble-vm systemd[1]: ensemble-upgrade-r-20261028-170150-2d54.service: Consumed 37min 43.148s CPU time, 7.6M memory peak, 0B memory swap peak.
```
**Arithmetic (from `-o json` `__REALTIME_TIMESTAMP`):** Started 1793207101280078 → Stopped 1793209364590624 ⇒ wall **2,263.310546 s**; CPU **2,263.148 s** ⇒ **99.9928 %** of one core. **Only 0.163 s of the window was not CPU-busy.**

### 3.2 `data/upgrade.log` — the payload's own log (path `/home/nea/agents-ensemble/data/upgrade.log`, root:root, 69,788 B, **mtime 2026-10-28 17:05**)
```
610: upgrade-promote[live]: resolved env: target=live dir=/home/nea/agents-ensemble port=9797 db=ensemble_prod
611: upgrade-promote[live]: preflight: lock · integrity · journal txn · entry checks
```
(File has 611 lines total; lines 610-611 are the Oct-28 attempt. The 64 `Broken pipe` printf errors are at lines **3–585 only**, e.g. `/home/nea/ensemble-src/scripts/upgrade/lib.sh: line 4413: printf: write error: Broken pipe` … `line 4439` — i.e. **earlier attempts**, from `_supervision_owned_pids`' `while read` loop; the Oct-28 attempt has none.)

### 3.3 the journal (pre-trim backup — the authoritative source)
```
file: /home/nea/agents-ensemble/releases/state.json.pre-manual-v02711.20261028T174405Z.bak
bytes: 455783   history entries: 2949   in_flight: null   pending_op: null
current: v0.27.8   previous: v0.27.4

{"ts": "2026-10-28T17:04:20Z", "event": "nonce_consumed", "detail": "nonce for run_id=r-20261028-170150-2d54 consumed by message bf75d733-2ff5-4462-a96c-4b8121d45c4e (kind=upgrade env=live target=v0.27.11)"}
{"ts": "2026-10-28T17:16:11Z", "event": "executor_still_running", "detail": "run_id=r-20261028-170150-2d54 pid=2480938 still running after 660s — benign-detach (no kill; own process group; correlate with data/upgrade.log)"}
{"ts": "2026-10-28T17:42:44Z", "event": "halt", "detail": "killed by SIGTERM at phase promote:preflight (r-20260928-005506-f82e: death-anchored journaling; signal handler installed 2026-09-28)"}
{"ts": "2026-10-28T17:43:19Z", "event": "sweep", "detail": "pending_op run_id=r-20261028-170150-2d54 closed by reconcile: terminal event 'halt' at 2026-10-28T17:42:44Z (killed by SIGTERM at phase promote:preflight …)"}
```
Historic comparison — successful promotes at this same code path record `python_floor_probe` then `intent_flip` **2.5–3.5 min later** (2026-10-21: 10:33:10 → 10:35:39; 2026-10-26: 20:33:18 → 20:36:41). On Oct-28 there is **no** `python_floor_probe` row ⇒ the preflight never got past the read.

### 3.4 lock artifacts
```
/home/nea/agents-ensemble/releases/rollback.lock.d.stale.2480459/
  drwxr-xr-x  4096  2026-10-28 09:57:49.770096429 +0000  .
  -rw-r--r--    11  2026-10-28 10:04:46.433634133 +0000  heartbeat   -> 1793181886  (= 2026-10-28T10:04:46Z)
  -rw-r--r--     8  2026-10-28 09:57:49.764096502 +0000  owner      -> 2182592
  -rw-r--r--     8  2026-10-28 09:57:49.764096502 +0000  run_id     -> run-20261028-095749-2182592
```
This is the **morning stage attempt's** lock (created 09:57:49, last heartbeat 10:04:46) — it was house-stale-broken before the 17:04:21 arm. **No live `rollback.lock.d` exists**; the promote's own lock was released by its trap at 17:42:44.

### 3.5 host load (no anomaly)
```
sar -u -f /var/log/sysstat/sa28 (8 CPU):  17:10:04 all 13.46%user 81.07%idle | 17:40:04 all 22.68%user 66.91%idle
journalctl -k --since "2026-10-28 17:00" --until "2026-10-28 17:45"  ->  -- No entries --
journalctl -u postgresql --since "2026-10-28 16:55" --until "2026-10-28 17:45"  ->  -- No entries --
uptime: up 10 days, 11:14 | last reboot: Mon Oct 19 07:26 (no reboot on Oct-28)
```

---

## 4. RANKED ROOT-CAUSE HYPOTHESES (Ari's four candidates)

### ★1 — Journal brace-walk on the promote path — **CONFIRMED IN SUBSTANCE (with a correction)**
**FOR**
- The executed `lib.sh` (blob `f81bcf6ba`, byte-verified = `scripts/upgrade/lib.sh` at dev-checkout HEAD `013895287`, working tree clean) contains `journal_read` as a **per-character bash loop** (`for ((i=0;i<${#json};i++)); do c="${json:i:1}`) — O(N²).
- **Measured** (sandbox, /tmp copies of the real journal): 32,768 B → 16.81 s · 65,536 B → 68.38 s · 131,072 B → 278.07 s ⇒ **4.07× per doubling (exponent 2.02)**; on the real 455,783 B file, 20,000 characters cost **119.4 s**. Fit: **T(N) = k·N², k ≈ 1.3–1.6 × 10⁻⁸ s/byte²** ⇒ a full walk over that journal = **45–56 min**; the executor was killed at 37 m 43 s = **~70 % through**.
- The journal at hang time was **455,783 B / 2,949 entries** (the "splice removed" comment cites 126 KB / 500 entries ≈ 139 s/call — the journal had **5.9×** that size).
- `promote.sh:225` (`J="$(journal_read)"`) is the **first** legacy read on the promote path after the last log line; every later step (`:235` probe row, `:240` adopt_stale_txn, `:245` entry_check, `:251` supervision, `:289` integrity, `:346` txn open) produced **no output and no journal row** ⇒ the executor never passed `:225`.
- `adopt_stale_txn` (lib.sh:5429) and `promote_entry_check` (lib.sh:5348) each perform a **further** full walk — the pattern repeats on every promote.
- The executed code itself documents the hazard: *"the ~280 s/event at the 126 KB / 500-entry fixture"*, *"the interim guard called journal_in_flight → journal_read — a FULL bash brace-walk … which … re-introduced the very ~139 s/call cost the splice removed"*.
**AGAINST / correction**
- The executed script **does** contain the O(1) python splice (`_splice_journal` + `journal_splice.py`, present in the dev checkout). So "un-spliced" is imprecise: **the splice covers the WRITE path (`journal_history_append`) only**; `journal_read` (read) and `journal_update` (read-modify-write, lib.sh:861) still call the legacy walk. Ari's hypothesis is right in substance and wrong in scope.
- The `journal_heartbeat` path *was* fixed (cheap `grep -q '"in_flight":{'` guard) — so the heartbeat is not the cost centre.

### 2 — Broken-pipe stdout write blocking — **REFUTED as the cause**
**FOR (weak):** 64 `Broken pipe` printf errors do exist in `upgrade.log`; the commission's premise mentions them.
**AGAINST (decisive):**
- Those errors are at lines **3–585** — *earlier attempts* (2026-10-26 era). The Oct-28 attempt is lines **610–611** and has **zero** EPIPE.
- The payload's stdout is systemd-owned (`StandardOutput=append:…/data/upgrade.log`); the daemon's client stdout is `DEVNULL`. **There is no daemon-side reader to die.**
- A process blocked on a full pipe consumes **no CPU**; this one consumed **99.99 %** of a core.
- The writer demonstrably worked (2 lines, 69 ms) and then had nothing to write.
- `lib.sh:4413/4439` (`printf '%s\n' "$pid"` in `_supervision_owned_pids`' `while read` loop) is a **separate recurring artifact**, present even in successful runs.

### 3 — pg_dump preflight racing DB load — **REFUTED (code proof: zero occurrences)**
- **The executed artifacts contain no `pg_dump` at all**: `grep -c pg_dump` → **0** in `promote.sh` (blob `f81bcf6ba`, 42,170 B) and **0** in `lib.sh` (blob `f81bcf6ba`, 296,297 B). No `PGASSWORD` handling either. There is no pg_dump preflight to race anything.
- The spin was a **local file read** of `state.json`; the executor never reached a DB call (no txn opened, no daemon contact; `db=ensemble_prod` appears only in the env banner line).
- postgres journal: **no entries** 16:55–17:45. Kernel journal: **no entries** 17:00–17:45. `sar`: box 66.9–81.1 % idle.
- No `pg_dump` on the promote preflight path at the executed revision — **proved by zero-hit code grep on the byte-verified blobs** (`promote.sh` 0 hits, `lib.sh` 0 hits), not merely by absence of evidence.

### 4 — Interaction with the pre-existing orphaned lock — **REFUTED as causal (but it is a real, separate defect)**
**AGAINST:**
- The orphan was already broken **before** the arm (~17:04:2x, heartbeat 25,165 s > 300 s, owner pid dead) ⇒ `lock_acquire`'s `mkdir` **succeeded** ⇒ the promote held a clean, self-owned lock.
- `lock_acquire` cannot spin: `LOCK_WAIT_S_DEFAULT=15` (bounded) and **every** busy/stale branch calls `_log` first — no such line exists in `upgrade.log` after 611.
- The promote's lock was released cleanly by its own trap at 17:42:44.
**FOR (as a *different* defect):** the orphaned lock is real and **stage-side**: attempt #2 (pid 2179546, 09:54:37) died leaving `rollback.lock.d`, and attempt #3 (pid 2182592) acquired it at 09:57:49 and **left it behind** (last heartbeat 10:04:46) — i.e. `stage.sh` leaks its lock dir on completion. That orphan then forced the ~17:04 manual stale-break before the promote could arm.

### 5 — (added) Architectural gaps that let a 37-minute stall persist — **CONFIRMED**
- `PROMOTE_TOTAL_DEADLINE_S=900` is enforced **only at phase boundaries** (`_enforce_deadline`, called at `promote.sh:218` for `preflight`). The walk sat **inside** one boundary ⇒ the 15-minute ceiling could not fire.
- The daemon **detected** the stall at 17:16:11 (660 s) and classified it "benign-detach (no kill)" ⇒ no watchdog, no escalation.
- The sweep only closed `pending_op` 35 s **after** the unit exited.
- The journal grew to 2,949 entries / 455 KB because the trim cadence let it accumulate; the O(N²) walk scales with that growth, so the same code path that took ~3 min in October took ~50 min on Oct-28.

---

## 5. THE CODE PATH, VERBATIM (blob `f81bcf6ba` — byte-verified = dev-checkout HEAD `013895287`)

`promote.sh` (executed):
```
167  _log "preflight: lock · integrity · journal txn · entry checks"      <-- LAST log line ever written
170  if ! lock_acquire; then exit 78; fi                                   <-- mkdir succeeded (orphan already broken)
179  _trap_install_signal_handlers "promote:preflight"                     <-- TERM trap (proved by the 'halt' row)
182  lock_heartbeat                                                        <-- heartbeat written ONCE, then frozen
209  if ! _splice_python_floor_probe; then … exit 78; fi                   <-- python3 >= 3.7 probe, silent on success
218  _enforce_deadline "$promote_deadline" preflight                       <-- 900 s ceiling: passes at t=0.2 s
225  J="$(journal_read)" || { … exit 78; }                                 <-- *** THE HANG: legacy O(N^2) walk ***
235  _splice_journal append-history python_floor_probe …                   <-- NEVER REACHED (no such journal row)
240  adopt_stale_txn || _refuse txn-busy …                                 <-- never reached (2nd walk)
245  promote_entry_check "$VERSION"                                       <-- never reached (3rd walk)
251  supervision_preflight                                                <-- never reached
289  _log "integrity: verifying CURRENT release $CUR …"                   <-- never reached
346  _log "txn open: promote target=$VERSION pid=$$ …"                    <-- never reached
```
`lib.sh` — the hot function (executed blob `f81bcf6ba`):
```
796  journal_read() {
       …
       json="$(cat "$jp" 2>/dev/null)" || return 1
       …
       for ((i = 0; i < ${#json}; i++)); do
           c="${json:i:1}"                      <-- O(N) substring extraction, per character
           …
       done
       printf '%s' "$json"
     }
861  journal_update() { … json="$(journal_read)" || return 1 … }          <-- RMW path inherits the same walk
5429 adopt_stale_txn() { … json="$(journal_read)" || return 0 … }
5348 promote_entry_check() { … until="$(_json_field "$(journal_read)" cooldown_until)" … }
```
And the splice that *does* exist (write path only):
```
941  journal_history_append() { _splice_journal append-history "$1" "$2"; }
996  _splice_journal() { … "$py" "$helper" "$jp" "$op" "$@" 2>&1 … }
```
**Provenance:** `f81bcf6ba` is a **blob**, not a commit — `git cat-file -t f81bcf6ba` ⇒ `blob` (296,297 B); it is `scripts/upgrade/lib.sh` at the dev checkout HEAD `013895287` (working tree clean for `scripts/upgrade`). The extracted `promote.sh` and `daemon/tools/upgrade_journal.py` scratch copies are **byte-identical** to HEAD's versions (sha256 verified). Live `.env` pins the scripts dir to the dev checkout: `ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src/scripts/upgrade`, `ENSEMBLE_UPGRADE_LIVE=1`, `ENSEMBLE_SELF_ENV=live`, `PORT=9797` ⇒ **live promote behaviour rides an unpinned working tree** (the v0.27.11 soak-gate `lib.sh` rewrite was NOT exercised).

---

## 6. EXPLICIT GAPS (evidence that does not exist)

1. **The executor's own stdout beyond line 611 does not exist.** The payload wrote to `data/upgrade.log` via systemd `append:`; journald holds only the unit's lifecycle lines (no payload stdout/stderr). Nothing between 17:05:01.35 and 17:42:44 was captured anywhere.
2. **The promote's own lock dir was deleted** by the trap's `lock_release` at 17:42:44 ⇒ no artifact survives to show the heartbeat file's frozen value; the "no heartbeat progression" conclusion rests on (a) `upgrade.log` silence, (b) the four journal rows, (c) the code path (no `lock_heartbeat` call site is reached after `:182` before the walk).
3. **The Oct-28 `data/wedge-captures/` directories were deleted after the fact** ⇒ their per-process census (which would have shown the executor's threads) is unavailable. (Also: the wedge sampler reads `/proc/self` — the **daemon's** process, not the executor's — so those readings were never a proxy for the executor anyway.)
4. **Exact journal byte-size at 17:05:01 is inferred** (455,783 B at 17:43 minus ~700 B of the 17:16:11 + 17:42:44 rows ⇒ ≈455 KB, 2,947 entries). The cost model has ±20 % spread (k = 1.3–1.6e-8), so the walk's total is 45–56 min; the observed 37 m 43 s is a consistent interruption point in that range.
5. **`/var/log/syslog` does not cover Oct-28** (zero lines); journald is authoritative and complete for the window.
6. **`pg_dump` absence on the promote preflight path** was verified by code grep on the executed blob; the actual `pg_dump` invocation, if any, lives in a different (stage/backup) path — not exercised here.
7. **Board item `wi-fc1786a2` full body + notes** were collected by the parallel PG lane (SELECT-only) — see the companion handoff; this report does not restate them (avoiding any DB write and any secret echo).
8. **The `pending_op` payload for this run is unrecoverable.** The only pre-halt snapshot of the journal is the 17:43:05 backup, taken *after* the 17:43:19 reconcile had already closed it (`pending_op: null`); no snapshot exists between 17:04:21 (arm) and 17:43:05. The arm itself is therefore evidenced only by `nonce_consumed` (17:04:20) and the daemon-side `[system-execution] fired promote executor … pid=2480938` log line (17:05:11), not by a durable pending_op row.
9. **`data/upgrade.log` carries no per-line timestamps** (its lines are `upgrade-promote[live]: …` with no time prefix), so the two Oct-28 lines are dated by the file's mtime (17:05) and the journald `Started`/`Stopped` timestamps, not by their own content.

---

## 7. RECOMMENDED NEXT ACTIONS (diagnosis only — no fix applied here)

- **R1 (root):** the promote preflight must not call the legacy `journal_read`. Either extend the P2 splice to cover the **read** path (`journal_splice.py` read op) or replace `promote.sh:225` with a cheap field query. Same for `journal_update` (lib.sh:861), `adopt_stale_txn` (5429), `promote_entry_check` (5348).
- **R2:** bound *inside* a phase, not only between phases — a watchdog on the promote's own long operations, so `PROMOTE_TOTAL_DEADLINE_S` can actually fire.
- **R3:** the daemon's 660 s `executor_still_running` "benign-detach" must escalate (it saw this stall and did nothing).
- **R4:** `stage.sh` leaks `rollback.lock.d` on completion (owner 2182592's dir survived its own successful run) — separate defect, surfaced here.
- **R5:** the journal reached 2,949 entries / 455 KB untrimmed; the O(N²) walk scales with journal size, so the *same* code degrades silently over time. Bound the journal size or make reads O(1)/O(log n).
- **R6:** live promote behaviour rides the **dev checkout** (`ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src/scripts/upgrade`) — an unpinned working tree. This incident executed v0.27.8-era `lib.sh` against a v0.27.11 target.

---

## 8. BOARD ITEM `wi-fc1786a2` — VERBATIM (commission item 1; live PG, SELECT-only)

`SELECT count(*) FROM board_items` → **25**. Tags: `defect` · `promote-pipeline` · `root-cause`. Status `in_progress`; created `2026-10-28T19:51:09Z` (instance `830bc908`), updated `2026-10-29T18:14:07Z`.

**Title (verbatim):**
> Promote executor hung 37 min in preflight (lock/journal stage) + lost log lines to broken pipe — root-cause before the next ceremony

**body_md (verbatim, 2,858 B):**
> ROOT-CAUSE ITEM (new, surfaced by the 2026-10-28 v0.27.11 promote attempt).
>
> **The defect:** the scripted promote executor (run_id=r-20261028-170150-2d54, armed 17:04:21Z via the system_upgrade tool lane, nonce ceremony complete) **spun in PREFLIGHT — the lock/journal stage — from 17:05:01Z for 37 minutes and never reached the integrity phase.** The operator DevOps lane had to SIGTERM it at 17:42:44Z and perform a manual release (journaled 17:44:05Z). The promote's stdout log lines were ALSO lost to a broken pipe (lib.sh:4439 printf write errors — the parent reader had died), so the log tail shows nothing between "preflight: lock · integrity · journal txn · entry checks" and the halt.
>
> **Why it matters now:** the soak-gate lane's rewritten lib.sh (GREEN/HELD/RESET counters, grace, A3 hold) went LIVE with v0.27.11 — the NEXT promote will exercise the new gate code, and a preflight hang would block it the same way. Also: two SIGTERM-killed stage attempts earlier the same day (09:51:36Z, 09:54:59Z at phase stage:lock) left the orphaned rollback.lock.d that Ari later house-stale-broke — same phase family.
>
> **Evidence available:** journal history rows: halt@17:42:44Z (killed by SIGTERM at phase promote:preflight), sweep@17:43:19Z (pending_op closed), manual_release@17:44:05Z (full caveats in the entry) · upgrade.log tail (the old Oct-26 lines + the single new preflight line, then broken-pipe errors) · The 37-minute window 17:05:01→17:42:44 with NO journal/lock heartbeat progression (the lock WAS held + heartbeated by the executor per the operator's note — it was alive but stuck) · The two earlier stage:lock SIGTERM kills the same morning
>
> **Candidate causes to investigate (Ari's list, unverified):** 1. The lock/journal preflight stage doing a full journal brace-walk (the journal_heartbeat comment in lib.sh mentions a "~139s/call cost the splice removed" for the OLD brace-walk — is there an un-spliced walk on the promote path, possibly quadratic on the 123-entry history?) 2. The broken-pipe on stdout (lib.sh:4439) causing a blocked write — a full pipe buffer would HANG a printf in a shell script with no timeout 3. pg_dump preflight under the loaded DB (the plan's first step) racing the readiness degradation 4. Interaction with the pre-existing orphaned lock state at arm time (Ari broke it at 17:03; the executor acquired a fresh one at 17:05 and spun)
>
> **Scope:** root-cause the spin + the lost log lines; add a preflight WATCHDOG/timeout so a stuck executor fails loudly instead of hanging silently; fix the broken-pipe logging so promote output survives parent death (write to a file, not just the inherited pipe). The A5(iv)/grace work made the SOAK observable — the PREFLIGHT is now the unobservable phase.
>
> **Constraints:** stage-only; no live promote without the nonce ceremony; house gates.

**Note (verbatim, id=193, 2026-10-29T18:14:07Z, author `830bc908`):**
> DISPATCHED 2026-10-29T18:13Z — job 29ea7080-9bc1-47d7-9429-d0940d2b5488, leader, priority 6, watched. Trigger: v0.27.13 staged — the NEXT ceremony will exercise the new lib.sh, and this defect (37-min preflight hang + broken-pipe log loss) blocks exactly that path. The dispatch carries the full evidence trail + the four candidate causes + the scope (root-cause + preflight watchdog + file-based logging) + the sandbox-reproduction ground rule (N4 real-promote.sh precedent). Status: in_progress.

**Premise corrections this investigation establishes against the item's own text:**
1. *"stdout log lines were ALSO lost to a broken pipe"* — **REFUTED**: the payload's stdout is systemd-owned (`StandardOutput=append:`) and demonstrably worked (2 lines, 69 ms); the 64 EPIPE errors are from *earlier* attempts; the lines were never lost — they were **never produced**.
2. *"the lock WAS held + heartbeated by the executor … alive but stuck"* — **REFINED**: the lock was held, but the heartbeat was written **once** at 17:05:01 and **frozen** thereafter; no `lock_heartbeat` call site was ever reached again.
3. *"quadratic on the 123-entry history"* — **CORRECTED SCALE**: the journal held **2,949 entries / 455,783 bytes**, 5.9× the fixture the "~139 s/call" comment cites.
4. *"pg_dump preflight … the plan's first step"* — **REFUTED**: zero `pg_dump` occurrences in the executed `promote.sh` and `lib.sh`.


## 9. EVIDENCE INVENTORY

| artifact | path |
|---|---|
| Worker B raw logs (journald + daemon logs + arithmetic) | `/tmp/preflight-forensics-20261029/raw-logs.md` (sha256 `3e52df5647792dc08b0768ab1389c01eee2bb2aecc1bee0276f67e12e9ced3fa`) |
| journald extracts | `/tmp/preflight-forensics-20261029/daemon_main_1650_1750.txt`, `daemon_main_0920_1015.txt`, `daemonfile_1650_1750.txt`, `daemonfile_0920_1015.txt` |
| executed code (byte-verified copies) | `/tmp/preflight-forensics-20261029/lib.sh.f81bcf6ba`, `promote.sh.f81bcf6ba`, `upgrade_journal.py.f81bcf6ba`, `upgrade_journal_sweep.py.f81bcf6ba` |
| brace-walk benchmark harness + samples | `/tmp/preflight-forensics-20261029/measure_bracewalk.sh`, `micro.sh`, `samp_{32768,65536,131072}.json` |
| this report | `/tmp/preflight-forensics-20261029/FORENSICS.md` |

**Boundaries honoured:** no writes to `/home/nea/agents-ensemble` by this lane (verified by `find -newermt`; the only new paths under the install dir are the **daemon's own** `data/logs/` and `data/wedge-captures/` writes, which are not this lane's), no writes to the repo working tree by this lane (`.git/index` mtime still `2026-10-26 02:40:26`); note that `git status` in the shared checkout **does** show modified `.agents/**` bookkeeping files — those belong to **other concurrently-running lanes** (approver / leader sweep / tester-docs), not to this forensic lane. No journal appends, no state.json edits, no symlink flips, no `promote.sh`/`stage.sh`/`rollback.sh` invocation, no daemon restart/boot, no process kills, no `env -u POSTGRES_*`, no DB writes (SELECT-only), all scratch under `/tmp/preflight-forensics-20261029/`. **Nothing committed.**

**Meta-observation (independent corroboration, surfaced during this investigation):** while this lane was measuring the brace-walk cost, one of its own worker `bash` calls wedged at **0 % CPU for 29 minutes** on a measurement that should have taken ~30 s — i.e. **blocked on I/O, not computing**. The failure mode under investigation (a pipeline child whose stdout reader is not draining) reproduces inside the very tooling used to investigate it. Left in place rather than killed, so the in-flight tool result is not corrupted; flagged for the pipeline-hygiene lane.

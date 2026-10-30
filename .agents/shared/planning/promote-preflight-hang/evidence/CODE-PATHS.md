# Promote-executor preflight hang (`r-20261028-170150-2d54`) — CODE-PATH MAP

**Commission:** board `wi-fc1786a2` · read-only code-path mapping · no fix, no edits, no commits
**Investigation date:** 2026-10-29 · **Report:** `/tmp/preflight-codemap-20261029/CODE-PATHS.md`

---

## 0. Executive summary

1. **ROOT CAUSE (rank 1):** `journal_read()`'s brace/bracket **balance scan is an O(N²) per-byte bash loop** (`lib.sh:809-828`, hung rev) — `for ((i=0;i<${#json};i++)); do c="${json:i:1}"` — where `${json:i:1}` is an *offset* substring ⇒ cost ∝ N per iteration.
2. On the **455,783-byte** journal that existed at hang time (retained artifact `releases/state.json.pre-manual-v02711.20261028T174405Z.bak`), **one** `journal_read` ≈ **3,670 s (≈61 min)** of pure-bash CPU (quadratic fit, slope 2.00); the executor was SIGTERM'd at **2,262 s = 62 % through that single scan**.
3. The blocking call is **`promote.sh:225` — the FIRST `journal_read` in preflight** (the executor never reached `:235`, `:251`, `:276`, `:289` or `:346` — all proven absent).
4. The scan is **byte-identical at TIP** (`diff` = 0) ⇒ **the defect SURVIVES into the next ceremony**; the tip diff is only the inherited v0.27.11 soak-gate lineage, and `fix/promote-preflight-hang` has landed **zero** commits.
5. **Candidate 1 (journal brace-walk) — CONFIRMED, but mis-specified:** it is not the *heartbeat* walk (that was spliced at `lib.sh:1139-1181`) but the **`journal_read` balance scan**, still un-spliced.
6. **Candidate 2 (broken-pipe stdout blocking) — DISPROVEN:** payload stdio is `StandardOutput=append:<file>` (systemd-owned), and the observed `lib.sh:4439` EPIPE is the benign `| head -1` early-close race — a red herring.
7. **Candidate 3 (pg_dump racing DB) — DISPROVEN:** `pg_dump|psql|pg_ctl` = **0 hits** in `scripts/upgrade/*.sh` at both revs; the promote path runs no DB client at all.
8. **Candidate 4 (orphaned lock/stale txn) — DISPROVEN:** `stage.sh` opens **no** journal txn; the stale-break moves only the lock dir; `adopt_stale_txn` has **no wait branch** (adopt-or-`exit 78`).
9. **Structural amplifier (not on Ari's list):** `_enforce_deadline` is a **phase-boundary** check — the entire preflight body (`:220`–`:372`) is outside every deadline check ⇒ the 900 s ceiling **cannot** fire against a preflight overrun; and the **only** preflight `lock_heartbeat` is `:182`, so the lock heartbeat froze at 17:05:01.
10. **Journal bloat driver:** 2,775 of 2,949 history entries are `mcp_server_state` (423,767 B = **89 %** of the journal) — the MCP init storm grew a file the shell reads in O(N²). **No history cap / rotation exists.**

---

## 1. Rev identification — the `f81bcf6ba` trap (read this first)

`f81bcf6ba` is **a blob sha, not a commit**. `git show f81bcf6ba:scripts/upgrade/lib.sh` fails ("exists on disk, but not in 'f81bcf6ba'"). Resolution:

| Rev | Location | Identity |
|---|---|---|
| **HUNG** | `/home/nea/ensemble-src/scripts/upgrade/` (live dev checkout, `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pins it) | `git hash-object lib.sh` = **`f81bcf6baf0b5a4acf52143e01f29a5d58d98a8d`** ✔ exact match. Dev-checkout HEAD `013895287` (2026-10-26, v0.27.8-era). File mtime 2026-10-20 04:16 — **unchanged since the incident**. `promote.sh` = `9a230f098`, `stage.sh` = `fc80b38c`, `journal_splice.py` = `9b373314f`. |
| **TIP** | `/home/nea/ensemble-src-wt-preflight-hang/` | `b1183a637` ("chore(release): bump version to 0.27.13"), clean tree. |

**Proof the executor ran the HUNG rev** (systemd journal, read-only):
```
17:05:01 systemd[1]: Started ensemble-upgrade-r-20261028-170150-2d54.service -
   /usr/bin/bash /home/nea/ensemble-src/scripts/upgrade/promote.sh live --version v0.27.11 --f2-verified-closed.
```
⟹ the promote ran from the **dev checkout working tree**, not the release payload (known house hazard: live promote behaviour rides an unpinned tree).

**Rev delta:** `promote.sh`, `stage.sh`, `daemon/tools/upgrade_journal.py`, `daemon/services/upgrade_journal_sweep.py` are **byte-identical** across revs. `lib.sh` differs by 303–343 diff lines, **all** in the soak/soak-gate regions. **No preflight, lock, deadline or journal-read construct changed at tip.**

---

## 2. Deliverable 1 — PREFLIGHT CALL GRAPH (banner → integrity)

HUNG `promote.sh` ≡ TIP `promote.sh` (identical line numbers):

| # | Line | Call | Def (hung `lib.sh`) | Blocking? |
|---|---|---|---|---|
| 0 | `:123` | `. lib.sh` | — | no |
| 0a | `:128`/`:129`/`:145` | `resolve_env` / `require_live_guard` / `echo_env_triple` | `:591` / `:715` / `:709` | no — "resolved env:" printed ✔ |
| 1 | **`:167`** | `_log "preflight: lock · integrity · journal txn · entry checks"` | `:188` | banner (last log line) |
| 2 | `:170` | `lock_acquire` | `:1464` | **no** — bounded 15 s (`LOCK_WAIT_S_DEFAULT=15`, `:178`) |
| 3 | `:179` | `_trap_install_signal_handlers "promote:preflight"` | `:1919` | no — sets `_PIPELINE_PHASE="promote:preflight"` |
| 4 | `:182` | `lock_heartbeat` | `:1564` | no — **the only preflight heartbeat** |
| 5 | `:197` | `promote_deadline = now + PROMOTE_TOTAL_DEADLINE_S(900)` | `:109` | no — **armed only here** |
| 6 | `:209` | `_splice_python_floor_probe` | `:278` | no (python3 floor probe, ~0.1 s) |
| 7 | `:218` | `_enforce_deadline "$promote_deadline" preflight` | `:221` | no — **the ONLY deadline check inside preflight** |
| 8 | `:224` | `journal_init` | `:779` | no (file existed) |
| 9 | **`:225`** | **`J="$(journal_read)"`** | **`:796`** | **★ THE HANG — O(N²) balance scan, unbounded** |
| 10 | `:235` | `_splice_journal append-history python_floor_probe` | `:996` | never reached |
| 11 | `:240` | `adopt_stale_txn` | `:5429` | never reached (1 journal_read + decision tree) |
| 12 | `:245` | `promote_entry_check` | `:5348` | never reached (3 journal_reads) |
| 13 | `:251` | `supervision_preflight` → `supervision_classify` + `supervision_dualfight_check` | `:4938` / `:4533` / `:4846` | never reached — but **latent 2nd blocker** (see §6) |
| 14 | `:272` | `promote_plugin_staleness_check` | `:2400` | never reached |
| 15 | `:276` | `journal_read` (2nd) | `:796` | never reached |
| 16 | `:290`/`:296` | `integrity_verify` | `:2789` | never reached — **no `integrity:` log line exists** |
| — | `:327`/`:338`/`:344`/`:346` | `journal_open_txn` / `journal_mark_f2_verified` / `journal_mark_supervision` / `_log "txn open:"` | `:1040`/`:337`/`:4965` | never reached — **no txn, no "txn open:" line** |

### What runs between the banner and the blocker
`lock_acquire` → `_trap_install_signal_handlers` → `lock_heartbeat` → deadline arm → `_splice_python_floor_probe` → `_enforce_deadline` → `journal_init` → **`journal_read`**.

### Proof the executor died inside `:225` (four independent markers)
1. **Missing durable marker:** `:235` writes a `python_floor_probe` history event. In the 455 KB journal (2,949 entries) that event appears **16×**, newest `2026-10-26T20:33:18Z` — **no 2026-10-28 entry** ⟹ `:235` never ran.
2. **Missing log lines:** `data/upgrade.log` (mtime **Oct 28 17:05**) ends at the banner. The prior *successful* promote in the same file shows the banner followed by `PLUGIN-STALENESS OBSERVED` → `integrity: verifying CURRENT` → `integrity: verifying TARGET` → `txn open:` — **none** for the hung run.
3. **Missing machine line:** `ENSEMBLE_SUPERVISION_RESULT`/`OUTCOME` (printed unconditionally at `lib.sh:4951`) absent ⟹ `supervision_preflight` (`:251`) never completed.
4. **Journal has no txn:** `in_flight: null` in the incident journal snapshot; the 17:42:44 halt event is labelled `phase promote:preflight`.

### Cost projection if the scan had completed
6 `journal_read` calls on the promote path (`:225`, `:276`, `:5431` in `adopt_stale_txn`, ×3 in `promote_entry_check` `:1395`/`:1417`/`:1241`) × ≈61 min ≈ **≈6 h to reach integrity** — the ceremony was never going to complete at that journal size.

---

## 3. Deliverable 2 — LOCK STAGE

**Protocol** (documented `lib.sh:1427-1440`, implemented `:1464-1594`):

- Lock = **mkdir** `$INSTALL_DIR/releases/rollback.lock.d` (`lock_dir_path` `:1440`) — atomic acquire; contents `owner` (pid), `run_id`, `heartbeat` (epoch). **No `flock` anywhere** in `scripts/upgrade/` (house constraint: BSD portability, `lib.sh:57`).
- `lock_acquire` (`:1464`): on `mkdir` success writes `owner`/`run_id`/`heartbeat` (`:1472-1474`) → `return 0`.
- **Lock exists at start → three outcomes, never a silent steal:**
  - `:1505-1517` heartbeat age > `LOCK_STALE_S=300` (`:177`) **AND** owner pid not alive (`_pid_alive` `:1453`, EPERM=alive) → stale-break `mv "$lock" "${lock}.stale.$$"` (`:1515`).
  - `:1524-1529` owner pid present + **dead** → stale-break even with a fresh heartbeat.
  - `:1530-1535` otherwise → wait ≤ `LOCK_WAIT_S_DEFAULT=15` → `return 1` → caller `exit 78` (pipeline-busy).
- `lock_release` (`:1580`): `rm -rf` **only if owner == `$$`**, else warn.

**Heartbeat writer.** `lock_heartbeat` (`:1564-1577`): ownership-guarded (`:1569`), writes `$lock/heartbeat` (`:1570`), then `journal_heartbeat` (`:1576` → `:1139` → `_splice_journal set-in-flight-field last_heartbeat`).
`LOCK_HEARTBEAT_S=30` (`:176`) is a **cadence contract, not a background loop** — there is **no** background heartbeat process in `promote.sh`/`stage.sh`/`lib.sh` (only `_run_bounded`'s child spawn at `:3364`/`:3371`). All refresh sites: `promote.sh:182, 355, 413, 518, 639`; `lib.sh:3074, 3230, 3286` (probe gates), `:3834` (hand-back), `:5132, 5156` (`gate_soak`).

🔴 **Premise correction:** the lock was **held** and the **owner alive** (pid 2480938 — journal `executor_still_running … pid=2480938 still running after 660s`), but the **heartbeat was FROZEN at ~17:05:01** (no `lock_heartbeat` between `promote.sh:182` and `:355`). It was stale-by-age yet **correctly not breakable** (owner alive) — exactly the rule at `:1480-1490` ("A stale heartbeat ALONE must NOT break the lock"). **The lock was not the blocker; it was the *evidence*.**

**House stale-break artifact (read-only, live):** `releases/rollback.lock.d.stale.2480459/` → `owner=2182592`, `run_id=run-20261028-095749-2182592`, `heartbeat=1793181886` (2026-10-28 10:04:46Z). The suffix `2480459` is the **acquiring** pid ⇒ produced by `lib.sh:1515`'s `mv "$lock" "${lock}.stale.$$"`. Both gates satisfied (age ≈6.9 h > 300 s; owner dead) ⇒ **house-conformant**.

**Stale-break residue (candidate 4):** the break moves **only the lock dir** (3 files). It touches **no** journal state (no `journal_write` in the break path). `stage.sh` opens **no** journal txn (zero `journal_open_txn` call sites; it calls `journal_init` `:615` only) and the 455 KB snapshot shows `in_flight: null` ⟹ **the killed stage attempts left no txn for a later preflight to trip on.** `adopt_stale_txn` (`:5429-5586`) has **no wait/loop branch**: stale+owner-dead → **ADOPT and proceed**; fresh (age ≤ `SWEEP_STALE_S=600`) → **refuse exit 78**; `kind=restart` → exit 78; unparseable `started_at` → exit 78 (fail-closed). It was never reached in this incident.

---

## 4. Deliverable 3 — JOURNAL TXN + ENTRY CHECKS; BRACE-WALK CENSUS

### 4.1 The un-spliced construct (the hang)

`journal_read` — hung `lib.sh:796-834` (byte-identical at tip):

```bash
journal_read() {
    ...
    json="$(cat "$jp" 2>/dev/null)" || return 1          # :803
    ...
    local ob=0 cb=0 os=0 cs=0 i c in_str=0 esc=0
    for ((i = 0; i < ${#json}; i++)); do                  # :810  ← per-BYTE loop
        c="${json:i:1}"                                  # :811  ← OFFSET substring ⇒ O(N) per access
        ...                                              # :812-827  escape/string/bracket bookkeeping
    done
    if [ "$ob" != "$cb" ] || [ "$os" != "$cs" ] || [ "$in_str" = "1" ]; then ... fi   # :829
    printf '%s' "$json"                                  # :833
}
```

**Cost model:** N iterations × O(N) offset-substring scan ⇒ **O(N²)**. Measured slope **2.00** (log-log).

### 4.2 Which sites are spliced vs not

| Site | Mechanism | Cost class |
|---|---|---|
| `journal_heartbeat` (`:1139-1181`) | `_splice_journal set-in-flight-field` → **python** `journal_splice.py` | **SPLICED** (O(N)); the old bash walk was "**~139 s/call**" (`lib.sh:1160-1162`) and "**~280 s** on the 87 KB state.json" (`:1553-1563`) — *removed* |
| `journal_history_append` (`:941-943`) | pure delegate to `_splice_journal append-history` — **no bash fallback** | **SPLICED** |
| `journal_open_txn` (`:1040`), `journal_set_current`, `journal_quarantine`, `journal_close_txn` | `journal_update` → `journal_read` + bash pattern splice | **INHERITS the O(N²) read** |
| **`journal_read` (`:796-834`)** | **the balance scan itself** | **❌ NOT SPLICED — this is the hang** |
| `journal_update` (`:859-916`) | `journal_read` + `%`/`#` pattern splice (O(N) matcher) | inherits the read cost |
| `journal_write` (`:837-853`) | temp + `mv -f` (atomic) | O(N), no lock |
| `_provenance_sidecar_ok` (`:2194-2204`) | same per-byte char-walk anti-pattern (on a small sidecar) | 🟡 latent 2nd site |

**The codebase already fixed this class for the heartbeat path but left `journal_read` itself on the walk** — the shell *write* path was migrated to python (C-2/C-3) while the shell *read* path stayed bash.

### 4.3 Is any walk inside a heartbeating loop?
**No** — and that is the point: the preflight contains **no** `lock_heartbeat` between `:182` and `:355`, so the walk at `:225` ran with **no** heartbeat refresh, **no** deadline check (next is `:373`), and **no** timeout.

### 4.4 Measured cost model (this host, GNU bash 5.2)

| journal bytes | entries | `journal_read` wall |
|---|---|---|
| 22,763 | 125 | **9.15 s** |
| 25,160 | 163 (today's live journal) | **11.02 s** |
| 108,499 | 600 | *(quadratic fit)* |
| 270,944 | 1,500 | **1,296.24 s (21.6 min)** |
| 455,783 | 2,949 (**the incident artifact**) | **>600 s — did not complete**; quadratic fit ⇒ **≈3,670 s (≈61 min)** |

Fit from the two measured points: **T = 1.77e-8·N²** (slope 2.00; ratio check: N×11.9 ⇒ T×141.6 ≈ 11.9²).
**Cross-check:** the same 455,783-byte file through the python splice path (`journal_splice.py` `_read_journal` `:269` / `_atomic_write` `:319`) = **0.024 s** ⇒ **~140,000× gap** between the two twins of the same file.

**Timeline fit:** executor start 17:05:01 → SIGTERM 17:42:44 = **2,262 s** = **62 %** of the ≈3,670 s scan. systemd's own accounting: *"Consumed 37min 43.148s CPU time, 7.6M memory peak"* ⇒ **100 % of one core, pure bash, flat RSS** — exactly the incident doc's ps signature ("pure bash at 99.9 % CPU, syscall=running, **no children**, **no open fds**, flat RSS").

### 4.5 Why the journal was 455 KB (the bloat driver)
- 2,949 history entries; **2,775 = `mcp_server_state`** = **423,767 B = 89 %** of the file; written 2026-10-27T14:58Z→2026-10-28T09:15Z at up to ~661 entries/hour.
- Writer: `daemon/mcp/init_guard.py:150` (`JOURNAL_EVENT = "mcp_server_state"`) → `daemon/tools/upgrade_journal.journal_history_append(...)` → appended to `<install>/releases/state.json`.
- `init_guard.py:76-86` suppresses daemon-side writes **during** a live promote txn — so the bloat accumulates *between* ceremonies.
- **There is no history cap, no rotation, no size guard.** The operator's 2026-10-28 response was a **manual trim**: 474,557 B (17:49) → 435,464 B (17:52) → 21,737 B (22:26) → 25,160 B (today). At 25 KB `journal_read` ≈ 11 s (fine); at 455 KB it is ≈61 min (fatal).

---

## 5. Deliverable 4 — STDOUT WIRING (and the broken-pipe red herring)

**fd topology (ground truth):**
```
daemon → spawn_executor()                      daemon/tools/upgrade_journal.py:2656
   └─ build_service_argv()                     :2462 → :2501-2542
        --unit=ensemble-upgrade-<run_id>  --wait  --collect
        --property=Restart=no                                   :2501
        --property=StandardOutput=append:<install>/data/upgrade.log   :2536
        --property=StandardError=append:<install>/data/upgrade.log   :2537
        --setenv=K=V …  --  <inner argv>
   └─ Popen(..., stdout=DEVNULL, stderr=PIPE)   :2743-2759
        └─> systemd transient unit → payload stdout/stderr → append:<log file>
```

- **The payload's stdio is a FILE, not a pipe** (`upgrade_journal.py:2486-2488`: *"Payload stdio rides StandardOutput/StandardError=append:<log_path> — the payload's stdio is owned by systemd now"*). ⟹ **a dead daemon-side reader cannot lose payload log lines and cannot cause a blocked write.**
- **The log is one shared file, append-only, no rotation, no per-run path** (`executor_log_path` `:2179-2180` → `<install_dir>/data/upgrade.log`; the daemon reads a ≤256 KB tail via `upgrade_tools.py:941-966`). The only per-run correlation key is the **unit name** / `run_id`.
- `_log` → **stdout** (`lib.sh:188`), `_warn` → **stderr** (`:190`), both with `2>/dev/null` ⇒ **neither can emit `printf: write error`**.
- **lib.sh:4439 is not a log write:** it is `printf '%s\n' "$pid"` inside `_supervision_owned_pids` (`:4412-4441`), whose only unredirected-stderr path is the caller's `pid="$(_supervision_owned_pids | head -1)"` (`:4563`, `:4619`). `head -1` exits after one line → the pipe's read end closes → the *next* write gets EPIPE. **Self-inflicted, benign, non-stalling.**

**Bash semantics (measured on this host, GNU bash 5.2.21):**
- **Does `printf` to a FULL pipe BLOCK? → YES.** `write(2)` on a full 64 KiB pipe blocks until the reader drains; bash's builtin sets no `O_NONBLOCK` and applies no timeout.
- **Does `printf` to a CLOSED pipe raise EPIPE and let the script CONTINUE? → The write fails fast with EPIPE.** With `SIGPIPE` ignored, bash prints `bash: line 1: printf: write error: Broken pipe`, the builtin returns 1, and the script continues (process rc 0). With SIGPIPE at default disposition, a *forked pipeline element* is silently SIGPIPE-killed (rc −13) and its own process likewise.
- **Are "blocked-write stall" and "the observed broken-pipe errors" the SAME condition? → NO — mutually exclusive states of the same pipe.** Blocked = reader ALIVE but not draining; EPIPE = reader DEAD. **Kernel ordering ⇒ reader-death precedes EPIPE** (a blocked write can only become EPIPE *after* the read end closes). The broken-pipe lines are therefore a *consequence* of a reader close, never a precursor — and for this incident the "reader" was `head -1`, not the daemon.

**Consequence for the evidence:** the log's silence after the banner is **REAL** (systemd-owned file), not lost output. The premise "stdout log lines were lost to a broken pipe (the parent reader had died)" is **refuted**.

---

## 6. Deliverable 5 — EXTERNAL COMMAND HAZARD CENSUS (preflight path, both revs)

| Construct | Site (hung → tip) | Timeout? | Verdict |
|---|---|---|---|
| `mkdir` lock acquire | `lib.sh:1471` | atomic | bounded |
| `sleep 1` busy-wait loop | `lib.sh:1534-1535` | ≤ `LOCK_WAIT_S_DEFAULT=15` | bounded |
| **`journal_read` cat + per-byte bash scan** | **`lib.sh:803-828`** | **none** | **❌ UNBOUNDED — O(N²); THE HANG** |
| `journal_update` (read-modify-write) | `lib.sh:859-916` | inherits the read | **❌ UNBOUNDED (same root)** |
| `journal_write` temp+`mv -f` | `lib.sh:837-853` | — | bounded (no lock ⇒ lost-update risk, not a stall) |
| `_splice_journal` → `python3 journal_splice.py` | `lib.sh:996-1011` | **no timeout wrapper** | 🟡 bounded in practice (0.024 s) but unguarded |
| `_splice_python_floor_probe` → `python3 -c` | `lib.sh:278` | none | 🟡 fast, unguarded |
| `promote_plugin_staleness_check` → `python3` (+PyYAML) | `lib.sh:2400-2470` | none | 🟡 fast (manifest-only), unguarded |
| `systemctl is-active` / `show Restart` / `show MainPID` / `show ControlGroup` | **`lib.sh:4852, 4853, 4858, 4859`** | **NONE (no `_run_bounded`)** | **❌ UNBOUNDED — latent 2nd blocker in the same phase** |
| `systemctl show -p MainPID` (pre-stop) | `lib.sh:3637` | none | ❌ unbounded (post-preflight) |
| `systemctl show -p MainPID` (hand-back loop, **inside a `while :` + `lock_heartbeat`**) | `lib.sh:3835` (loop `:3833`) | none | ❌ unbounded (post-preflight) |
| `systemctl reset-failed` / `is-active` / `start` (hand-back) | `lib.sh:3895, 3917, 3940` | ✅ `_run_bounded "$SYSTEMCTL_PROBE_BUDGET_S"` (5 s) / `"$HANDBACK_START_BUDGET_S"` (60 s) | bounded — **the pattern the preflight fails to use** |
| `systemctl is-active` / `start` (scope-arm) | `lib.sh:4196, 4232` | ✅ `_run_bounded` (5 s / 10 s) | bounded |
| `pgrep` / `ps -axo` / `lsof -a -p` | `lib.sh:4418-4436` | none | bounded (measured 0.51 s on the live host) |
| `curl` (livez/readyz/version probes) | `lib.sh:2979 (--max-time 5)`, `:3138, 3145, 3253 (--max-time 2)` | ✅ | bounded (post-preflight) |
| `shasum -a 256` | `lib.sh:386` | none | bounded |
| `find`/`sort`/`diff` (tree manifests) | `lib.sh:1957`, `:2863` | none | bounded (external, minutes at worst) |
| `wait` on background pid | only `_run_bounded`'s watchdog `:3373-3384` | ✅ 5 s grace then KILL | bounded |
| `flock` | **absent** (house: BSD portability, `lib.sh:57`) | — | n/a |
| FIFO / named-pipe reads | **absent** (the `/run/ensemble/inflight_turns` file was explicitly NOT created — `lib.sh:162`, `:3129`) | — | n/a |

**House note:** the house deliberately avoids GNU `timeout(1)` (BSD portability, `lib.sh:16`) and uses `_run_bounded` (`:3360-3386`: child + background watchdog → SIGTERM → 5 s → SIGKILL → rc collapse 137/143 → 124).

---

## 7. Deliverable 6 — PG_DUMP (candidate 3)

**DISPROVEN for the promote path.** `grep -E "pg_dump|psql |pg_ctl"` over `scripts/upgrade/*.sh` = **0 hits at BOTH revs**. The promote path runs **no DB client at all** — it touches only `$INSTALL_DIR/releases/*` (symlink, manifest, state.json, lock dir) and systemd. Nothing in the preflight can race DB load. (The DB-pool leak family that the 2026-10-26 soak failure implicated is a *daemon-side* concern, orthogonal to this hang.)

---

## 8. Deliverable 7 — STAGE:LOCK FAMILY (the two 09:51/09:54 SIGTERMs)

- `stage.sh:363-376`: `lock_acquire` → `_trap_install_signal_handlers "stage:lock"` → `lock_heartbeat`; further heartbeats at `:426`, `:558`, `:737`.
- **"stage:lock" is the phase LABEL** (`lib.sh:1921` sets `_PIPELINE_PHASE`), installed once at `stage.sh:375` and **never re-labelled** ⟹ "died at stage:lock" means *SIGTERM'd anywhere after the lock was taken*, **not** "died inside a lock phase". The journal shows exactly that (`09:51:36Z` and `09:54:59Z` `halt … at phase stage:lock`), and a **third** attempt (`run-20261028-095749-2182592`, started 09:57:49Z) whose heartbeat froze at 10:04:46Z — i.e. it died too (and left the orphaned lock Ari stale-broke).
- **Shared machinery: YES** — the identical `lib.sh` functions (`lock_acquire`, `_trap_install_signal_handlers`, `lock_heartbeat`, `journal_init`, `journal_history_append` → `journal_splice.py`, `journal_quarantine_clear` `:1224` → `journal_read` `:1241` + `journal_update` → `journal_read` again).
- **Same hang class? PARTIALLY.** Stage's dominant long phase is payload assembly + the 5,361-entry tree/checksum walk (external I/O). **But** stage's tail `journal_quarantine_clear` performs **two** O(N²) `journal_read` scans on the same 455 KB journal ⇒ **≈2 h** of pure bash scanning at the end of a stage run — the *same* class, same construct, equally unguarded. 🟡 The 09:5x kills are more consistent with the checksum/assembly phase (long tree walks) than with the journal scan; the label is too coarse to discriminate.

---

## 9. Deliverable 8 — INTERACTIONS (can anything hold the preflight?)

| Subsystem | Verdict | Evidence |
|---|---|---|
| `daemon/services/promote_txn_hold.py` (575 L) | **Holds nothing the preflight needs.** Read-only by construction (`:36-39`); never writes/splices/locks the journal (`_read_state` `:171-187`); a torn read ⇒ predicate **fails open** (a release, not a hold). It *reacts to* a live promote txn; it cannot create, extend or block one. | `promote_txn_hold.py:36-39, 76-81, 171-203, 237` |
| `UpgradeJournalSweepService` 90 s tick | **Cannot block the preflight** (no journal lock on either side ⇒ neither can wait on the other; only silent lost updates). **Can** clear the daemon-side `pending_op` of a *pre-txn* hang via the expiry branch (`expires_at + RECONCILE_GRACE_S=600`) because the `in_flight` guard (`:1959-1961`) only protects promotes that got past `journal_open_txn`. The shell mutex still prevents a double-flip. | `upgrade_journal_sweep.py:96, 1937-2006, 2008-2110`; `upgrade_journal.py:1943-2024` |
| boot-epoch / halt-for-human | **Not in the promote path at all** — `boot_epoch.py` is the message-queue stop-frozen-heartbeat amnesty (`capture_boot_epoch` `:91-133`). Every halt path in the preflight is a **refuse (exit 78)**, never a wait: `_enforce_deadline` `:221-248`, `promote_entry_check` `:5348-5379`, `adopt_stale_txn` `:5429-5571`, `supervision_dualfight_check` `:4909-4918`, `_refuse` (promote.sh:142, 222). | as cited |
| systemd transient unit | **No `RuntimeMaxSec`/`TimeoutSec`/`WatchdogSec`** — the only `--property` lines are `Restart=no` + the two `Standard*Output=append:` ones. The unit is an independent object that **survives the client** (`--wait` only attaches the client; `--collect` unloads after termination). ⟹ **a client death cannot stop the payload and there is no bound ⇒ the mechanical cause of the 37-minute orphan.** | `upgrade_journal.py:2462-2542, 2673-2675, 2198-2209` |
| Daemon-side "executor still running" reaper | Fires at 660 s and **deliberately does not kill**: journal `2026-10-28T17:16:11Z \| executor_still_running \| … still running after 660s — benign-detach (no kill; own process group)`. ⟹ **by design, nothing would ever stop the spin.** | journal history (read-only copy) |

---

## 10. Deliverable 9 — RANKED ROOT-CAUSE HYPOTHESES

### 🥇 #1 — `journal_read`'s O(N²) balance scan on a 455 KB journal (`lib.sh:809-828`, reached at `promote.sh:225`)
**FOR:** pure-bash CPU burn (systemd: *"Consumed 37min 43.148s CPU time, 7.6M memory peak"* ≈ 100 % of one core) · incident-doc ps signature (*"pure bash at 99.9 % CPU, syscall=running, **no children**, no open fds, flat RSS"*) · measured slope 2.00 and **did not complete in 600 s on the exact incident artifact** · four independent "never reached" markers (`:235`, `:251`, `:289`, `:346`) · log silence (the step prints nothing until it finishes) · lock heartbeat frozen at `:182` with no refresh until `:355` · timeline fit 2,262 s = 62 % of the predicted 3,670 s · **survives byte-identically at tip**.
**AGAINST:** the journal size at 17:05:01 is inferred from the 17:44:05 snapshot (the executor wrote nothing, but third-party writers in that window are not separable) — 🟡 on that sub-point only; ≥ ~290 KB would still be required for the 37-min fit.

### 🥈 #2 — unbounded `systemctl` in `supervision_dualfight_check` (`lib.sh:4852/4853/4858/4859`)
**FOR:** 5 sibling systemctl sites use `_run_bounded`; these 4 do not → contract drift · a wedged D-Bus blocks forever · it sits inside the same unguarded window.
**AGAINST (decisive):** the run **never reached `:251`** (no `ENSEMBLE_SUPERVISION_RESULT` line in the log) · systemd was demonstrably **alive and scheduling** throughout the window (the `ensemble-treecheck.service` timer started+deactivated every 65 s across 17:05→17:42) · measured live `systemctl` latency = 20 ms. **Latent 2nd blocker, NOT this incident.**

### 🥉 #3 — `_enforce_deadline` is boundary-only (the amplifier)
Not the root cause, but the reason the hang was **unbounded and invisible**: the preflight phase contains exactly **one** deadline check (`:218`, *before* the expensive work); the next is `:373`; `promote_deadline` is not even armed until `:197` (after `lock_acquire`). The 900 s ceiling therefore **could not** fire — the executor ran ~23 min past its intended ceiling.

### Candidate verdicts (Ari's list)
| # | Candidate | Verdict | Decisive evidence |
|---|---|---|---|
| **1** | Un-spliced journal brace-walk | ✅ **CONFIRMED — but mis-specified**: the un-spliced walk is **`journal_read`'s balance scan**, *not* the heartbeat walk (the heartbeat was spliced at `:1139-1181`, old cost "**~139 s/call**" at `:1160-1162`). | `:809-828` + 600 s non-completion on the real artifact |
| **2** | Broken-pipe stdout write blocking | ❌ **DISPROVEN** | payload stdio = `append:` **file** (`upgrade_journal.py:2536-2537`, `:2486-2488`); the EPIPE at `:4439` is the `\| head -1` early-close race; a blocked write and an EPIPE are mutually exclusive and EPIPE always *follows* reader death |
| **3** | `pg_dump` racing DB load | ❌ **DISPROVEN** | `pg_dump\|psql\|pg_ctl` = **0 hits** in `scripts/upgrade/*.sh` at both revs; the promote path runs no DB client |
| **4** | Interaction with the pre-existing orphaned lock | ❌ **DISPROVEN as a blocker** | Ari's stale-break (`:1515` pattern, artifact name matches exactly) had already moved the lock aside before arming; `stage.sh` opens **no** txn; `adopt_stale_txn` has **no wait branch**; `lock_acquire` is bounded at 15 s |

### The construct NOT on Ari's list, ranked FIRST alongside #1
**Journal growth is unbounded and the shell's reader is quadratic** — the daemon-side MCP init storm appends `mcp_server_state` events to `releases/state.json` with **no cap, no rotation, no size guard** (89 % of the file), while the shell reads that same file in **O(N²)**. The house fixed the *write* twin (python splice, 0.024 s) and left the *read* twin on the walk — a **140,000× cost asymmetry on one file**. Both halves are needed for the hang: a 25 KB journal is fine (11 s); a 455 KB journal is fatal (61 min).

---

## 11. Deliverable 10 — FIX-SURFACE MAP (analysis only; no implementation)

### (a) Root cause
| # | File · function | Hung → tip | Fix direction (analysis) |
|---|---|---|---|
| **A1** | `scripts/upgrade/lib.sh` · **`journal_read`** | `:796-834` → same | Replace the per-byte balance scan with an **O(N)** check — the tree already contains a python precedent (`journal_splice.py`, 0.024 s on the same 455 KB file) and a bash port of the same scanner at `:2194-2204`; alternatively add a `verify` op to `journal_splice.py` and keep the shell free of char-loops. **This is the one change that removes the hang.** |
| **A2** | `scripts/upgrade/lib.sh` · `journal_update` (`:859-916`), `journal_quarantine_clear` (`:1224`), `journal_set_current`, `journal_close_txn` | — | All inherit `journal_read`; fixed transitively by A1. |
| **A3** | `daemon/mcp/init_guard.py` (`:150`, `_write_journal`) → `daemon/tools/upgrade_journal.journal_history_append` | — | **Cap/rotate journal history** (entries or bytes), rate-limit or coalesce `mcp_server_state` events, and/or make them non-append. Without a cap, A1 only moves the cliff from 455 KB to ~1 GB. |
| **A4** | `scripts/upgrade/lib.sh` · `journal_read` | — | Add a **size guard** (refuse + journal-warn above N bytes) so the failure is loud, not a 61-minute stall. |

### (b) A preflight watchdog / timeout that fails LOUDLY
| # | File · function | Hung → tip | Reuse |
|---|---|---|---|
| **B1** | `scripts/upgrade/promote.sh` preflight body `:167-346` | identical | Insert **in-phase** `_enforce_deadline` calls (after `:225`, `:235`, `:240`, `:245`, `:251`, `:276`, `:289`) — the `:218`→`:373` gap is the structural hole. |
| **B2** | `scripts/upgrade/promote.sh` `:197` | identical | Arm `promote_deadline` **before** `lock_acquire` (`:170`) so the whole script is covered. |
| **B3** | `scripts/upgrade/lib.sh` · `supervision_dualfight_check` `:4852/4853/4858/4859` → tip `:5023/5024/5029/5030` | — | Wrap the 4 `systemctl` calls in `_run_bounded "$SYSTEMCTL_PROBE_BUDGET_S"` (`:3360-3386`, budget `:127`); on rc=124 **fail loud** (journal halt + exit 78), not "treat as not-active". |
| **B4** | `daemon/tools/upgrade_journal.py` · `build_service_argv` `:2462-2542` | identical | Add **`--property=RuntimeMaxSec=<N>`** (the `unit_properties` extension point already exists at `:2468` and the `Restart` guard at `:2502-2512` shows the pattern) — the only fix that bounds the unit at the systemd level. |
| **B5** | `scripts/upgrade/lib.sh` · `lock_heartbeat` sites | — | Refresh the heartbeat inside preflight (or run a heartbeat loop) so a long preflight never freezes the lock heartbeat and makes the lock look breakable. Reuse `HEARTBEAT_STALE_S=300` (`:183`) / `LOCK_HEARTBEAT_S=30` (`:176`). |
| **B6** | `daemon/services/upgrade_journal_sweep.py` · `_executor_alive_recent` `:2008-2110` + `daemon/tools/upgrade_journal.py` · `reconcile_pending_op` `:1943-2024` | identical | The liveness predicate needs a **pre-`journal_open_txn`** signal (the current heartbeat only exists after the txn opens); the reaper's "benign-detach, no kill" at 660 s should become a real kill for a stalled preflight. |

### (c) Logging that survives parent death
| # | File · function | Hung → tip | Note |
|---|---|---|---|
| **C1** | `daemon/tools/upgrade_journal.py` · `executor_log_path` `:2179-2180` | identical | **No per-run log file exists.** Introduce `data/upgrade-<run_id>.log` (preserving the `StandardOutput/StandardError=append:` properties at `:2535-2537`); today's single shared file is the forensic gap (this incident's log had to be disambiguated by mtime). |
| **C2** | `scripts/upgrade/lib.sh` · `_log` `:188` / `_warn` `:190` | identical | Both carry `2>/dev/null` ⇒ **they cannot report a write failure**. A write-error path would need to go to stderr *without* the redirect (or to the journal). |
| **C3** | `scripts/upgrade/lib.sh` · `_supervision_owned_pids` `:4412-4441` (printf `:4439`) + callers `:4563`/`:4619` | tip `:4583`/`:4610`, callers `:4734`/`:4790` | Redirect the helper's stderr (or use a bounded read) so the benign `head -1` EPIPE stops masquerading as a dead-parent symptom. |
| **C4** | `daemon/tools/upgrade_journal.py` `:2750-2751` + `:2762` | identical | The client's stderr pipe is only drained on the fast-fail path; a long-lived client can fill the 64 KiB pipe and block the **client** (daemon-side latent stall — not this incident, but the same family). |

### Existing house primitives available for reuse (no new mechanism)
`_run_bounded` (`:3360-3386`) · `SYSTEMCTL_PROBE_BUDGET_S=5` (`:127`) · `HANDBACK_START_BUDGET_S=60` (`:126`) · `SCOPE_START_BUDGET_S=10` (`:128`) · `_enforce_deadline` (`:221-248`, journals `deadline-breach-<phase>` + `lock_release` + exit 78) · `PROMOTE_TOTAL_DEADLINE_S=900` (`:109`) · `HEARTBEAT_STALE_S=300` (`:183`) / `LOCK_STALE_S=300` (`:177`) / `LOCK_WAIT_S_DEFAULT=15` (`:178`) / `SWEEP_STALE_S=600` (`:173`) · `_trap_install_signal_handlers` (`:1919`) · `journal_splice.py` (the O(N) python twin) · `journal_splice.py` `verify`-op slot · `build_service_argv(unit_properties=…)` (`:2468`) · `promote_txn_hold.evaluate_promote_txn()` (read-only liveness cross-check) · `executor_log_path` (per-run path slot).

---

## 12. Evidence appendix

### Artifacts read (read-only; no live install mutation)
- `/home/nea/agents-ensemble/releases/state.json.pre-manual-v02711.20261028T174405Z.bak` — **455,783 B**, 2,949 history entries (2,775 `mcp_server_state` = 423,767 B = 89 %), `in_flight: null` ← **the journal as the executor saw it**
- `.pre-trim.20261028T174902Z.bak` (474,557 B) · `.pre-hardtrim.20261028T175234Z.bak` (435,464 B) · `.pre-manual-v02712.20261028T222632Z.bak` (21,737 B) · live `state.json` (25,160 B, Oct 29 16:54)
- `/home/nea/agents-ensemble/data/upgrade.log` — **mtime Oct 28 17:05**, last line = the banner (root-owned, systemd `append:` target)
- `releases/rollback.lock.d.stale.2480459/` — `owner=2182592`, `run_id=run-20261028-095749-2182592`, `heartbeat=1793181886`
- host journal: unit `Started … promote.sh live --version v0.27.11 --f2-verified-closed` at 17:05:01Z; `Stopping` + `Main process exited … status=143` at 17:42:44Z; **"Consumed 37min 43.148s CPU time, 7.6M memory peak, 0B memory swap peak"**; `ensemble-treecheck.service` cycling every ~65 s throughout (systemd alive)
- journal history (read-only copy): `nonce_consumed 17:04:20Z` · `executor_still_running 17:16:11Z (no kill)` · `halt 17:42:44Z "killed by SIGTERM at phase promote:preflight"` · `sweep 17:43:19Z` · `manual_release 17:44:05Z`
- `/home/nea/ensemble-src/.agents/shared/incident-20261028-v02711-journal-wedge-manual-release-sweep-loop.md` — house RCA naming the **journal quadratic-walk wedge**, the 474 KB journal, and the *"pure bash at 99.9 % CPU, no children, no open fds, flat RSS"* signature

### Measurements taken (all read-only; scratch under `mktemp -d`)
| Measurement | Result |
|---|---|
| `journal_read` @ 22,763 B | 9.15 s |
| `journal_read` @ 25,160 B (live copy) | 11.02 s |
| `journal_read` @ 270,944 B | 1,296.24 s |
| `journal_read` @ 455,783 B (incident artifact) | **>600 s — did not complete** (quadratic fit ≈3,670 s) |
| quadratic fit (2 points) | slope 2.00; T ≈ 1.77e-8·N² |
| python splice on the same 455,783 B file | 0.024 s (**~140,000× gap**) |
| full preflight replay (sandbox, real journal copy, FAKE_HOME + escape guard) | 68 s wall / 22 s CPU — no hang at 25 KB |
| `_supervision_owned_pids` (real install) | 0.51 s (3 pids) |
| `supervision_classify` (real install) | 0.52 s → mode=unit, unit=ensemble-main.service |
| 4× `systemctl` in the dualfight path (real) | 17–25 ms each |
| `lsof -ti:9797` | 1 pid |
| bash pipe semantics | full pipe → blocks (no timeout); closed pipe → EPIPE fast, script continues (rc 1) with SIGPIPE ignored, or silent SIGPIPE death (rc −13) otherwise |

### House rules honoured
Read-only throughout. **No** repo file edited, **no** commit, **no** branch op. `promote.sh`/`stage.sh`/`rollback.sh` **never executed in any mode**. Live install dir **read only** (`ls`/`stat`/`cp` of a log + journal snapshots; no writes). No DB access (`pg_dump`/`psql` never invoked). All scratch under `mktemp -d /tmp/preflight-codemap-20261029/…`; sandbox used a `mktemp` FAKE_HOME with an explicit escape guard (`case "$INSTALL_DIR" in /home/nea/agents-ensemble*) refuse`) — no live-shaped path was ever resolved or deleted. No `env -u POSTGRES_*` idiom used.

---

## 13. Gaps / unverified items

1. **Exact journal byte-size at 17:05:01** — inferred from the 17:44:05 snapshot (455,783 B). The executor wrote nothing; third-party writers in the 39-minute window are not separable. 🟡 (≥ ~290 KB is required for the 37-min fit — consistent with the retained snapshots.)
2. **Which process wrote the 2,775 `mcp_server_state` entries** — writer code identified (`daemon/mcp/init_guard.py:150`); writing process not observed (the bloat predates the hung rev's `daemon/`, so a **v0.27.11-era binary resolving the live install root** is the inferred writer — consistent with the known staged-boot path-derivation hazard). 🟡
3. **Sub-phase of the two 09:5x stage kills** — the `stage:lock` label spans the whole stage run; checksum/assembly phase vs the tail `journal_quarantine_clear` scans cannot be discriminated from the label. 🔴
4. **`_provenance_sidecar_ok` (`:2194-2204`)** — same per-byte char-walk anti-pattern in a second site; harmless today (small sidecar), flagged for the fix lane. 🟡
5. **Worker-lane failure (closed, no gap):** the delegated journal-hazard-census lane failed with an upstream **rate-limit** error before returning; it was terminated at report time and did **not** produce a report. Its scope (brace-walk site enumeration + the "139 s/call" splice comments, external-command hazard census, `pg_dump` check, journal write-path locking, tip-survival diff) was **independently completed in this report** (§4, §6, §7, §9) from primary sources, and is **cross-confirmed by the completed call-graph/lock lane's independent findings**. No finding in this report depends on the failed lane, and no item is uncovered — **no revive or replacement was dispatched** (re-deriving already-delivered content against a rate-limited upstream would add no evidence).
6. Not investigated (out of lane): the MCP init storm's own root cause (why context7 init failed ~2,775 times) — that is the *journal-growth* driver and belongs to the `init_guard` lane, not this code-path map.

---

*Investigation performed read-only by Wanderer. Deliverable 10 is analysis only — no fix was implemented, proposed as a patch, or tested.*

# Phase 0 — Baseline reproduction & census lock

**Deliverables covered:** none directly — this phase *enables* D1–D4 and supplies the evidence every later phase cites.
**Depends on:** — · **Blocks:** P1, P2, P3, P4, P5.

---

## 1. Objective

Produce the three artefacts every later phase depends on:

1. **A sandbox reproduction of the original symptom at BASE** (the preflight spinning on a large journal),
   with the exact command, fixture and fence recorded.
2. **A complete, verified census** of every per-character / unbounded-walk construct in `scripts/upgrade/*`
   that touches the journal string or an unbounded payload — so D1 is not a point fix on one call site.
3. **A verified anchor index** (tip `b1183a637`) for every file:line this plan set cites, so the
   implementation does not chase stale line numbers from the HUNG rev.

Nothing in this phase changes production code. It is read + measure + fixture-construct only.

---

## 2. Decisions

| ID | Decision | Rationale | Rejected alternatives |
|----|----------|-----------|---------------------|
| **P0-D1** | The BASE reproduction uses the **455,783-byte incident fixture** (`state.json.pre-manual-v02711.20261028T174405Z.bak`, read-only) as the journal, in a **scratch install root** under `mktemp`. | It is the *exact* artifact the executor read; the cost model (≈ 45–61 min for one `journal_read`) is measured, not inferred (`evidence/FORENSICS.md` §4-1; `evidence/CODE-PATHS.md` §4.4). A synthetic fixture would not reproduce the reported wall. | A synthetic 455 KB JSON — rejected: it would not carry the real `history` shape (2,949 entries, 2,775 `mcp_server_state`) that the extractors actually traverse. |
| **P0-D2** | The BASE leg is **bounded by an external watchdog** (never run unbounded): the pack wraps the BASE call in the house `_run_bounded` shape (child + background watcher → SIGTERM → 5 s → SIGKILL → rc collapse to 124) with a **short** budget (default 60 s) and asserts *"the BASE call did NOT complete within the budget"*. | The BASE call takes ≈ 45–61 min. A pack that runs it to completion is a 1-hour CI job. The **assertion of non-completion** is the symptom; the exact wall is already measured in the evidence. | Running BASE to completion — rejected (CI-hostile, and the measurement already exists). A 5-minute BASE budget — rejected (still 5 min of CPU per run). |
| **P0-D3** | The **census is a machine-readable artefact** (`tests/packs/preflight_census.txt`, one repo-relative path per line, `#` comments allowed) plus a **generated** census table — not prose. | House convention (PACKS.md: *"THE PACK FILE LISTS ARE MACHINE-READABLE AND COMMITTED … the enumeration cannot drift from the invocation"*). A prose census drifts; a machine-read one cannot. | A prose table in the plan — rejected (drift risk, and the plan already carries the verified table below as the snapshot). |
| **P0-D4** | The anchor index is **re-verified by a script** (`grep -n` against the working tree at the implementation rev), not by re-reading the plan. | The plan's anchors were verified at `b1183a637`; by implementation time the rev may have moved. A verification script turns "the anchor is right" into a **runnable assertion**. | Trusting the plan's numbers — rejected (HUNG vs TIP numbers already differ by 150+ lines in `lib.sh`). |
| **P0-D5** | The corpus fixtures live under `tests/fixtures/journal/` (committed), **not** in `/tmp` and **not** in the repo root. | Fixtures must be reproducible across machines and reviewers; `/tmp` fixtures vanish. The large fixture (455 KB) is *derived* from the retained incident artifact at pack runtime (read-only) and is **not** committed (size + provenance). | Committing the 455 KB artifact — rejected (it's a live-install artifact; committing live state into the repo is a hygiene violation). |
| **P0-D6** | **The corpus grows 15 → 22 cases** and every expected verdict/digest is captured from the **true BASE**, with the stdout oracle being **command-substitution-processed bytes** (all trailing `\n` stripped, NUL bytes dropped; the NUL-drop stderr warning class is matched on **class/prefix**, never the exact string — this host's bash emits `…ignored null byte in input`) — not raw file bytes. **[fold: A15, A14]** | MEASURED by the architect: `journal_read`'s stdout is command-substitution-processed — a canonical 74 B journal echoes as 73 B, and a NUL byte is silently dropped (with bash's `warning: command substitution: ignored null byte` as a third stderr class). A raw-dump helper fails stdout parity on **every real journal** (every real journal ends in exactly one `\n` — `journal_write`'s `printf '%s\n'`, `lib.sh:1007`). | A raw-file-byte oracle — rejected (fails parity on every real journal). Authoring the expected verdicts by hand — rejected (the parity test's whole value is that BASE is the oracle). |
| **P0-D7** | **Differential harness**: true BASE is the oracle for every small case; the chunked-bash variant (Option D) may proxy for BASE on the 455 KB fixture **only after a proxy-validation leg** (proxy ≡ true BASE on the full corpus + one 32 KB fixture where true BASE ≈ 13 s); the bounded-60 s true-BASE "did-not-complete" symptom leg is retained; **timing claims cite only true-BASE/HEAD numbers**. **[fold: A17]** | The BASE oracle at 455 KB costs 45–61 min; the proxy is only admissible once proven equivalent. Timing claims must never rest on a proxy. | Using the proxy unconditionally — rejected (an unvalidated proxy can hide a divergence). Timing claims from the proxy — rejected (explicitly forbidden). |
| **P0-D8** | The census is published **twice**: the existing site table (§4) plus a **companion table (§4.2)** carrying the **failure-mode class** and the **stderr contract** per site, and the no-`set -e` fact plus the `promote.sh:276`/`:588` continue-on-empty rc-preservation pins. **[fold: A18]** | MEASURED/verified by the architect: **no `set -e` anywhere** in the pipeline (`set -u` only) ⇒ `promote.sh:276`/`:588` continue-on-empty, and the helper must preserve the rc **exactly** there; two sites deliberately suppress stderr (`:1803`, `:1876`). | Folding the two columns into the existing table — rejected (the Mode-2 additive rule forbids restructuring a committed table; a companion table carries the same information without churn). |
| **P0-D9** | **Phase 0 re-measures on the target host what the architect's analysis extrapolated**: the real `integrity_verify` wall time on a staged release, the shasum-batch parity, the transient-unit kill properties, and the plugins walk **post-batching**. **[fold: A13, A10, A7 — architect §6 evidence discipline]** | The architect's numbers are MEASURED on synthetic fixtures in `mktemp` scratch; the house requires the numbers that size budgets to come from the **target host's real shapes**. | Importing the architect's synthetic numbers as the budget inputs — rejected (explicitly superseded by the architect's own §6 instruction). |

---

## 3. The verified anchor index (TIP `b1183a637`)

This is the **authoritative** index for the whole plan set. All lines verified by direct inspection in
`/home/nea/ensemble-src-wt-preflight-hang` at HEAD `b1183a637649f94c23b94dfc745df44381dc54ab`.

### 3.1 The hang site and the read path

| Anchor (TIP) | Construct | HUNG-rev number (evidence) |
|---|---|---|
| `scripts/upgrade/promote.sh:225` | `J="$(journal_read)"` — **the hang** (first read in preflight, after the last log line) | `promote.sh:225` (identical) |
| `scripts/upgrade/promote.sh:170` | `lock_acquire` | `:170` |
| `scripts/upgrade/promote.sh:179` | `_trap_install_signal_handlers "promote:preflight"` | `:179` |
| `scripts/upgrade/promote.sh:182` | `lock_heartbeat` — the **only** preflight heartbeat; frozen for 37 min | `:182` |
| `scripts/upgrade/promote.sh:197` | `promote_deadline` armed (900 s) | `:197` |
| `scripts/upgrade/promote.sh:209` | `_splice_python_floor_probe` — **python3 ≥ 3.7 is already required before the hang site** | `:209` |
| `scripts/upgrade/promote.sh:218` | `_enforce_deadline "$promote_deadline" preflight` — the **only** in-preflight deadline check | `:218` |
| `scripts/upgrade/promote.sh:235` | `_splice_journal append-history python_floor_probe` — never reached (absence is a proof marker) | `:235` |
| `scripts/upgrade/promote.sh:240` | `adopt_stale_txn` (2nd read) — never reached | `:240` |
| `scripts/upgrade/promote.sh:245` | `promote_entry_check` (3rd read) — never reached | `:245` |
| `scripts/upgrade/promote.sh:251` | `supervision_preflight` — never reached | `:251` |
| `scripts/upgrade/promote.sh:276` | `CUR_JSON="$(journal_read)"` (4th read) — never reached | `:276` |
| `scripts/upgrade/promote.sh:290` / `:296` | `integrity_verify` — never reached (no `integrity:` log line exists) | `:290`/`:296` |
| `scripts/upgrade/promote.sh:327` / `:346` | `intent_flip` / `txn open:` — never reached (no txn was opened) | `:327`/`:346` |
| `scripts/upgrade/promote.sh:373` | next `_enforce_deadline` (phase `stop`) — the structural gap `:218 → :373` | `:373` |
| `scripts/upgrade/lib.sh:961` | `journal_read()` — **the O(N²) balance scan** | `:796` |
| `scripts/upgrade/lib.sh:968` | `json="$(cat "$jp" 2>/dev/null)"` | `:803` |
| `scripts/upgrade/lib.sh:975` | `for ((i = 0; i < ${#json}; i++)); do` — the per-character loop | `:810` |
| `scripts/upgrade/lib.sh:976` | `c="${json:i:1}"` — the **offset substring** (O(N) per access ⇒ O(N²) total) | `:811` |
| `scripts/upgrade/lib.sh:994`-`:997` | the malformed verdict (unbalanced braces/brackets or unclosed string ⇒ rc 1) | `:829` |
| `scripts/upgrade/lib.sh:998` | `printf '%s' "$json"` — the stdout contract | `:833` |
| `scripts/upgrade/lib.sh:1024` | `journal_update()` — the RMW path | `:859` (hung) / `:861` (evidence prose) |
| `scripts/upgrade/lib.sh:1026` | `json="$(journal_read)" || return 1` — the RMW inherits the walk | — |
| `scripts/upgrade/lib.sh:1046`-`:1070` | `journal_update`'s own per-character value scanner (`c="${rest:0:1}"`, `rest="${rest:1}"`) — **a second O(N²) on the same string** | — |
| `scripts/upgrade/lib.sh:589` | `_json_sub()` — nested-object bracket counter | — |
| `scripts/upgrade/lib.sh:600`-`:620` | `_json_sub`'s per-character loop (`char="${rest:0:1}"`, `out="$out$char"`) — **a third O(N²) on the same string** | — |
| `scripts/upgrade/lib.sh:508`-`:524` | `_json_escape()` — per-character loop, but only ever applied to **short** strings (file names, notes) ⇒ **not a scaling risk**; documented, out of scope | — |
| `scripts/upgrade/lib.sh:2357`-`:2400` | `_provenance_sidecar_ok()` — the same char-walk anti-pattern, applied to a **small sidecar** ⇒ latent, documented, out of scope | `:2194`-`:2204` |

### 3.2 The write path (already O(N) — DO NOT REGRESS)

| Anchor (TIP) | Construct |
|---|---|
| `scripts/upgrade/lib.sh:1106` | `journal_history_append()` — a single `_splice_journal append-history` call (the P2 migration) |
| `scripts/upgrade/lib.sh:1161` | `_splice_journal()` — the python bridge; rc 78 → 1, others propagate |
| `scripts/upgrade/lib.sh:312` | `_splice_python_floor_probe()` — python3 ≥ 3.7 gate |
| `scripts/upgrade/journal_splice.py:269` | `_read_journal()` — the O(N) twin (`json.loads`, UTF-8 strict) |
| `scripts/upgrade/journal_splice.py:319` | `_atomic_write()` |
| `scripts/upgrade/journal_splice.py:513`-`:520` | `_OPS` dispatch table — **the extension point for a read op** |
| `scripts/upgrade/journal_splice.py:549`-`:560` | the **live-install refusal** (refuses a path under the live install unless the pipeline lock is held) |
| `scripts/upgrade/journal_splice.py:564`-`:568` | the **missing-journal refusal** |
| `scripts/upgrade/journal_splice.py:158` | `_pipeline_lock_held()` — the waiver predicate |

### 3.3 Lock / deadline / budget primitives (all reusable, no new mechanism needed)

| Anchor (TIP) | Construct |
|---|---|
| `scripts/upgrade/lib.sh:109` | `PROMOTE_TOTAL_DEADLINE_S=900` |
| `scripts/upgrade/lib.sh:126`-`:128` | `HANDBACK_START_BUDGET_S=60`, `SYSTEMCTL_PROBE_BUDGET_S=5`, `SCOPE_START_BUDGET_S=10` |
| `scripts/upgrade/lib.sh:131` | `SOAK_S_DEFAULT=300` — **the long healthy phase** |
| `scripts/upgrade/lib.sh:146`-`:149` | `ADAPTIVE_SOAK_FLOOR_S=60`, `ADAPTIVE_SOAK_CAP_S=120`, `ADAPTIVE_SOAK_CONSECUTIVE=3`, `ADAPTIVE_SOAK_CADENCE_S=15` |
| `scripts/upgrade/lib.sh:207` | `SWEEP_STALE_S=600` |
| `scripts/upgrade/lib.sh:210`-`:212` | `LOCK_HEARTBEAT_S=30`, `LOCK_STALE_S=300`, `LOCK_WAIT_S_DEFAULT=15` |
| `scripts/upgrade/lib.sh:217` | `HEARTBEAT_STALE_S=300` |
| `scripts/upgrade/lib.sh:255`-`:282` | `_enforce_deadline()` — journals `deadline-breach-<phase>`, `_warn`, `lock_release`, **exit 78** |
| `scripts/upgrade/lib.sh:1629` | `lock_acquire()` |
| `scripts/upgrade/lib.sh:1670`-`:1694` | the stale-break rules (stale-heartbeat **and** dead owner; a LIVE owner is **never** broken) |
| `scripts/upgrade/lib.sh:1729` | `lock_heartbeat()` (ownership-guarded; refreshes the lock heartbeat + the journal heartbeat) |
| `scripts/upgrade/lib.sh:1745` | `lock_release()` (owner-guarded) |
| `scripts/upgrade/lib.sh:2034`-`:2039` | `_trap_safe_exit()` — the EXIT trap body (respects `_LOCK_RELEASED`) |
| `scripts/upgrade/lib.sh:2058`-`:2078` | `_signal_handler()` — journals a `halt` row, releases the lock, exits 143/129/130 |
| `scripts/upgrade/lib.sh:2084`-`:2090` | `_trap_install_signal_handlers()` — installs TERM/HUP/INT + swaps the EXIT trap |
| `scripts/upgrade/lib.sh:3531`-`:3557` | `_run_bounded()` — **the house watchdog primitive**: child + background watcher → SIGTERM → 5 s → SIGKILL → rc 124/137/143 |
| `scripts/upgrade/lib.sh:222` / `:224` | `_log()` / `_warn()` — both carry `2>/dev/null` ⇒ **they cannot report a write failure** (D3) |
| `scripts/upgrade/lib.sh:941` | `journal_path()` → `<INSTALL_DIR>/releases/state.json` |
| `scripts/upgrade/lib.sh:4583` | `_supervision_owned_pids()` — the benign `| head -1` EPIPE source (tip; evidence cites HUNG `:4412`-`:4441`) |
| `scripts/upgrade/lib.sh:4734` / `:4790` | the two `pid="$(_supervision_owned_pids | head -1)"` callers (tip; HUNG `:4563`/`:4619`) |

### 3.4 Stage path (the same symptom class)

| Anchor (TIP) | Construct |
|---|---|
| `scripts/upgrade/stage.sh:364` | `lock_acquire` |
| `scripts/upgrade/stage.sh:375` | `_trap_install_signal_handlers "stage:lock"` — installs the EXIT trap ⇒ a **normal** exit releases the lock |
| `scripts/upgrade/stage.sh:376` | `lock_heartbeat` |
| `scripts/upgrade/stage.sh:426` | `lock_heartbeat # payload copies done; checksum walks start (long phase)` |
| `scripts/upgrade/stage.sh:436` | `_log "computing manifest checksums (per-file sha256 + tree aggregates)"` — **the last marker before the 09:51/09:54 SIGTERMs** |
| `scripts/upgrade/stage.sh:437`-`:446` | the sha256 + tree-manifest walks (`_sha256`, `_tree_manifest`, `_tree_hash_of_lines`) |
| `scripts/upgrade/stage.sh:558` | `lock_heartbeat # full-tree verify walk (long phase)` |
| `scripts/upgrade/stage.sh:618` | `journal_quarantine_clear "$VERSION"` → **two** `journal_read` scans on the same journal (the same O(N²) class, at the tail of a stage run) |
| `scripts/upgrade/stage.sh:738`-`:741` | the second full-tree verify walk + `integrity_verify` (`:738`) |
| `scripts/upgrade/stage.sh:744` | `exit 0` — the success path (**no explicit `lock_release`**; the EXIT trap covers it) |

### 3.5 Daemon side

| Anchor (TIP) | Construct |
|---|---|
| `daemon/tools/upgrade_journal.py:2179`-`:2180` | `executor_log_path()` → `<install>/data/upgrade.log` — **one shared file, no per-run path** |
| `daemon/tools/upgrade_journal.py:2462` | `build_service_argv()` |
| `daemon/tools/upgrade_journal.py:2468` | `unit_properties: list[str] | None = None` — **the extension point** for `RuntimeMaxSec` |
| `daemon/tools/upgrade_journal.py:2501`-`:2512` | the `Restart=no` load-bearing guard + the property loop (a `Restart` override is refused) |
| `daemon/tools/upgrade_journal.py:2518`-`:2519` | `--wait` / `--collect` |
| `daemon/tools/upgrade_journal.py:2536`-`:2537` | `--property=StandardOutput=append:<log>` / `StandardError=append:<log>` — the payload's stdio is a **file** |
| `daemon/tools/upgrade_journal.py:2656` | `spawn_executor()` |
| `daemon/tools/upgrade_journal.py:2738` | the `build_service_argv` call site inside `spawn_executor` |
| `daemon/tools/upgrade_journal.py:2743`-`:2759` | the client `Popen` — client stdout is `DEVNULL`, client stderr is a `PIPE` |
| `daemon/tools/upgrade_journal.py:1943` | `reconcile_pending_op()` |
| `daemon/services/upgrade_journal_sweep.py:96` | `DEFAULT_UPGRADE_JOURNAL_SWEEP_INTERVAL_SECONDS = 90` |
| `daemon/services/upgrade_journal_sweep.py:98`-`:102` | the reaper-budget derivation: *livez 60 + readyz 120 + soak 300 + overhead ≈ 490 s observed minimum → 660 s headroom*; `DEFAULT_REAPER_TIMEOUT_SECONDS = 660` |
| `daemon/services/upgrade_journal_sweep.py:109` | `EXECUTOR_STALENESS_WINDOW_S = 900` |
| `daemon/services/upgrade_journal_sweep.py:2008` | `_executor_alive_recent()` — the liveness predicate |
| `daemon/services/upgrade_journal_sweep.py:2281`-`:2291` | `_journal_executor_still_running()` — the *"benign-detach (no kill … correlate with data/upgrade.log)"* row |
| `daemon/mcp/init_guard.py:150` | `JOURNAL_EVENT = "mcp_server_state"` — the 89 % bloat writer |
| `daemon/mcp/init_guard.py:561`-`:565` | the daemon-side `journal_history_append` call |
| `daemon/tools/upgrade_tools.py:208`-`:209` | `_UPGRADE_LOG_REL = data/upgrade.log`; `_UPGRADE_LOG_TAIL_CHARS = 256 KiB` |
| `daemon/tools/upgrade_tools.py:941`-`:966` | `_upgrade_log_tail()` — the bounded tail reader |

---

## 4. The complete `journal_read` census (verified at TIP)

**17 lib.sh call sites + 8 script call sites = 25 distinct call sites.** Every one of them pays the O(N²) scan today.

| # | Site | Enclosing function | Class | Phase |
|---|------|--------------------|-------|-------|
| 1 | `lib.sh:1026` | `journal_update` | RMW | P1 |
| 2 | `lib.sh:1086` | `journal_in_flight` — **no caller anywhere in the tree** ⇒ a **latent** site (migrated for symmetry; it pays **no** live cost today) | read (latent) | P1 |
| 3 | `lib.sh:1207` | `journal_open_txn` | RMW | P1 |
| 4 | `lib.sh:1232` | `journal_mark_flipped` | RMW | P1 |
| 5 | `lib.sh:1251` | `journal_mark_flipped` | RMW | P1 |
| 6 | `lib.sh:1373` | `journal_quarantine` | RMW | P1 |
| 7 | `lib.sh:1391` | `journal_quarantine_clear` | RMW | P1 |
| 8 | `lib.sh:1406` | `journal_is_quarantined` | read | P1 |
| 9 | `lib.sh:1511` | `journal_count_rollback` | read | P1 |
| 10 | `lib.sh:1560` | `journal_count_rollback` (second call) | read | P1 |
| 11 | `lib.sh:1582` | `journal_cooldown_active` | read | P1 |
| 12 | `lib.sh:1803` | `_pipeline_current_target` | read | P1 |
| 13 | `lib.sh:1876` | `pipeline_settled` | read | P1 |
| 14 | `lib.sh:5138` | `journal_mark_supervision` | RMW | P1 |
| 15 | `lib.sh:5528` | `retention_evict` | read | P1 |
| 16 | `lib.sh:5630` | `promote_entry_check` | read | P1 |
| 17 | `lib.sh:5703` | `adopt_stale_txn` | read | P1 |
| 18 | `promote.sh:225` | preflight step 1b | read | **P1 (the hang)** |
| 19 | `promote.sh:276` | preflight current-version read | read | P1 |
| 20 | `promote.sh:558` | commit path | read | P1 |
| 21 | `promote.sh:588` | gate-fail path | read | P1 |
| 22 | `rollback.sh:90` | preflight (**before** lock acquisition) | read | P1 |
| 23 | `rollback.sh:157` | target resolution | read | P1 |
| 24 | `restart.sh:133` | preflight (**before** lock acquisition) | read | P1 |
| 25 | `status.sh:52` | status print (**no lock at all**) | read | P1 |
| 26 | `stage.sh:618` → `journal_quarantine_clear` (`lib.sh:1389`) → its internal read at `lib.sh:1391` (row #7) **+** its `_json_sub` at `lib.sh:1392` (quadratic **twice**) | stage tail — **THREE** quadratic mechanisms on the same journal (1 read + 1 `_json_sub` ×2) | read | P1 |

> The table lists **26 rows** but **25 distinct call sites**: row 26 is a cross-reference (the stage tail reaches
> `journal_quarantine_clear` → `lib.sh:1391`, which is row 7 — it is the *same* read, paid twice by the stage path).

### 4.2 Failure-mode class + stderr contract per site **[fold: A18]**

Companion to §4 (the §4 site table is unchanged). **No `set -e` anywhere in the pipeline** (`set -u` only) — so
`promote.sh:276`/`:588` **continue-on-empty**, and the helper must preserve the rc **exactly** there. Two sites
deliberately suppress stderr (`:1803`, `:1876`) and must keep doing so.

| Site | Failure-mode class | stderr contract | rc to preserve |
|------|--------------------|-----------------|----------------|
| `promote.sh:225` | **safety-critical** (the hang site; feeds the preflight entry checks) | inherits `journal_read`'s wording class (`:970` empty / `:995` malformed) | rc 1 on refuse; the caller's `exit 78` on failure |
| `lib.sh:5630` (`promote_entry_check`) | **safety-critical** (cooldown ⇒ refuse) | same class | rc 1 ⇒ the caller's refusal path |
| RMW + extract sites `lib.sh:1026` / `:1086` / `:1207` / `:1232` / `:1251` / `:1373` / `:1391` / `:1406` / `:1511` / `:1582` / `:1803` / `:1876` / `:5138` / `:5528` / `:5703` | mixed: the RMW sites are **safety-critical** (they write the journal); the extract sites are **tolerant** | `:1803` and `:1876` suppress stderr (`2>/dev/null`) — **keep suppressing** | rc must be byte-identical to BASE |
| `promote.sh:558` | **loud-abort** (`journal_fail_loud` on read failure) | loud | rc → `journal_fail_loud` |
| `promote.sh:588` / `:276` | **loud-abort** at `:588`; **continue-on-empty** at `:276` (no `set -e`) | loud at `:588` | **exact** rc preservation (empty ⇒ continue) |
| `restart.sh:133` | **loud-abort** | loud | rc 1 ⇒ `exit 78` |
| `rollback.sh:90` | **safety-critical** (halt-for-human on a torn journal) | loud | rc 1 ⇒ `exit 78` |
| `rollback.sh:157` | **safety-critical** | loud | rc 1 |
| `status.sh:52` | **cosmetic** (a read-only diagnostic; never mutates the journal) | warns on an unreadable journal | rc 0 with a warning note; **never** 78, never a journal write |
| **the ADOPT-path transitive caller** — `adopt-unit.sh:201` → `pipeline_settled` (`lib.sh:1845`) → `journal_read` (`lib.sh:1876`) | **no interpreter today** (a *transitive* caller — hence outside the direct-call census, which stays exactly 25) | n/a (it inherits `lib.sh`'s behaviour) | after D1 it needs the **floor probe + a bounded fallback** (D1-9 / `phase1-plan.md` T1.7 / AC8); the adopt path is **outside** the armed window (`phase2-plan.md` §3.3b) |


**Additional O(N²) sites on the same journal string** (not `journal_read` call sites, but they make a
"fixed read" still slow):

| Site | Construct | Scaling | Phase |
|------|-----------|---------|-------|
| `lib.sh:1046`-`:1070` | `journal_update`'s per-character value scanner | O(N²) on the full journal | P1 |
| `lib.sh:600`-`:620` | `_json_sub`'s bracket counter (`out="$out$char"` per char) | O(N²) on the extracted object | P1 |
| `lib.sh:2361`-`:2379` | `_provenance_sidecar_ok`'s balance scan | O(N²) but on a **small** sidecar | **out of scope** (documented) |
| `lib.sh:515`-`:522` | `_json_escape`'s per-char loop | O(N²) but on **short** strings | **out of scope** (documented) |
| `lib.sh:4852`/`:4853`/`:4858`/`:4859` (HUNG) → tip `supervision_dualfight_check`'s four **unbounded** `systemctl` calls | no `_run_bounded` wrapper, unlike 5 sibling sites | unbounded (latent 2nd blocker) | **P2 (D2)** |

**The stage-path checksum burn (D1 scope per the commission):** `stage.sh:437`-`:446` — `_sha256` on the
binary/launcher/config plus `_tree_manifest` over `agents/` + `frontend/` + `plugins/` (5,361 entries at the
last census). The walks are **external** (`find`/`sort`/`shasum`) and therefore O(N) in practice, but they are
**unbounded and unlogged** for the duration — the exact shape that produced the 09:51:36Z / 09:54:59Z SIGTERMs
(`evidence/FORENSICS.md` §2-6). D1 therefore **bounds** it (a budget + a heartbeat + a log line), it does not
rewrite it.

---

## 5. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks | AC |
|----|------|--------------------|-------------|------------|-------|----|
| **T0.1** | Re-verify the anchor index of §3 against the implementation rev with a **script** (not by eye): a `grep -n`-driven checker that asserts each cited line still contains the cited construct. **The checker MUST also cover the daemon-side anchors** — `daemon/tools/upgrade_tools.py:1953`-`:1966` (the arm-time scripts-dir resolution), `:3714` (the arm marker), `daemon/manager.py:5494`-`:5496` (the argv construction) and `daemon/tools/upgrade_journal.py:2156`-`:2162` (the executor-env allowlist) — they are load-bearing for phase 6's refusal design and were not mechanically verified before (approver-002 N-24). | The checker exits 0 on the implementation rev; on a deliberately shifted file it exits non-zero. | `tests/packs/preflight_anchor_index_test.sh` (new) | — | — | AC1 |
| **T0.2** | Build the **fixture corpus** under `tests/fixtures/journal/`: the **22 corpus cases + G1–G5 = 27 fixtures** listed in `phase1-plan.md` §3.3 (valid, empty, truncated-at-N, unbalanced `{`, unbalanced `[`, unclosed string, escaped quote inside a string, brace inside a string, backslash escape at EOF, multibyte UTF-8, invalid UTF-8 bytes, single-quoted pseudo-JSON, trailing comma, top-level array, NUL-free binary garbage, whitespace-only, newline-only, brace-free trailing garbage, brace garbage, NUL-in-string, duplicate-key, escape-then-multibyte ×2 locales + the invalid-UTF-8+escape row, and the threshold−1/threshold/threshold+1 guard trio). **[fold: W4 — the stale ‘≥ 14 cases’ text is retired]** | Every case exists, is committed, and has a **recorded expected verdict + expected stdout digest** captured from the **BASE** bash scanner (command-substitution-processed bytes, per D1-9). | `tests/fixtures/journal/*` (new) | — | — | AC2 |
| **T0.3** | Derive the **large fixture** at pack runtime from the retained incident artifact (read-only), with a **sha256 assertion** so a changed artifact is a loud failure rather than a silent drift. | The pack asserts the artifact's sha256 == the recorded value and fails loudly on mismatch. | `tests/packs/preflight_journal_scale_test.sh` (new) | — | R6 | AC3 |
| **T0.4** | Implement the **BASE symptom reproduction**: a fenced sandbox that points a scratch install at the large fixture and calls the pipeline's read path, wrapped in the `_run_bounded` shape with a **60 s** budget. | The BASE leg asserts *"did not complete within the budget"* (rc 124 / SIGKILL) and the sandbox artifacts show the spin. | `tests/packs/preflight_repro_test.sh` (new) | T0.3 | R6 | AC4 |
| **T0.5** | Produce the **machine-readable census** of every per-character scan in `scripts/upgrade/*` (a `grep`-driven generator that emits the site list) and register it as `tests/packs/preflight_census.txt`. | The generator's output matches the §4 tables **exactly** (25 distinct `journal_read` call sites + the 5 extra O(N²) sites); the pack fails when a new site appears. | `tests/packs/preflight_census_test.sh` (new), `tests/packs/preflight_census.txt` (new) | — | — | AC5 |
| **T0.6** | Enumerate the **unbounded external-command sites** on the preflight path (the `systemctl` quartet in `supervision_dualfight_check`, the `stage.sh` checksum walks) and record the `_run_bounded` gap per site. | A table maps each unbounded site to its bounded sibling (or states that it has none). | `tests/packs/preflight_census.txt` (comments) | T0.5 | — | AC6 |
| **T0.7** | Implement the **sandbox fence** helper — the **CANONICAL fence contract is `phase5-plan.md` §3** (FAKE_HOME + escape guard + per-variable `POSTGRES_*` scrub + explicit PORT + scratch DB + the no-live-promote guard); this phase does **not** restate it — it implements the helper and **asserts the fence inside the pack** (a deliberate live-shaped path ⇒ the pack refuses). | The pack refuses to run when `INSTALL_DIR` resolves under `/home/nea/agents-ensemble*`; the refusal is itself asserted (a self-test leg). | `tests/helpers/preflight_pack_common.sh` (new) | — | R6 | AC7 |
| **T0.8** | Record the **healthy-baseline measurements** the watchdog budgets will be sized from: full-preflight sandbox wall at the live-sized journal, the stage checksum wall, and the promote-phase timeline from the successful-promote journal rows (`python_floor_probe` → `intent_flip` = 2.5–3.5 min, `evidence/FORENSICS.md` §3.3). | A measured baseline table lands in the plan set (the canonical phase-file ids are `phase2-plan.md` D2-4…D2-15; the pre-fold text cited an unqualified `Phase 2 §2 D2-3`, an id that lives only in `decisions.md` — see `plan-overview.md` §11.1) with the command that reproduces each number. | `tests/packs/preflight_baseline_test.sh` (new) + plan update | T0.4, T0.6 | — | AC8 |
| **T0.9** | **Grow the corpus 15 → 22** cases + the **guard-boundary trio** (threshold−1 / threshold / threshold+1) wired to the T1.6 size-guard constant. Every expected verdict + stdout digest is captured from the **true BASE** with the **command-substitution oracle** (trailing `\n` stripped, NULs dropped, the NUL-drop stderr warning class recorded). The duplicate-key case pins the **exact MEASURED corrupt bytes** as BASE-bug-preserving. **[fold: A15, A14]** | 22 committed cases + 3 guard rows; each carries a BASE verdict, a BASE stdout digest (command-substitution-processed) and the stderr class; the duplicate-key case's expected output is the corrupt byte string, explicitly labelled BASE-bug-preserving. | `tests/fixtures/journal/*` (new) | T0.2 | R-N1, R-N2 | AC9 |
| **T0.10** | Build the **differential harness**: true BASE is the oracle for every small case; the chunked-bash variant (Option D) may proxy for BASE on the 455 KB fixture **only after a proxy-validation leg** (proxy ≡ true BASE on the full **22 corpus cases + G1–G5 = 27 fixtures** + one 32 KB fixture where true BASE ≈ 13 s); the bounded-60 s true-BASE "did-not-complete" symptom leg is retained; **all timing claims cite only true-BASE/HEAD numbers**. **[fold: A17, W4, approver-002 blocker 7 — the fixture count is 27]** | The proxy-validation leg is a pack assertion (proxy ≡ BASE on **all 27 fixtures**); the timing assertion refuses proxy-derived numbers (a grep-assertion on the RESULTS writer). | `tests/packs/preflight_differential_test.sh` (new) | T0.9 | — | AC10 |
| **T0.11** | **Runtime-probe the transient unit's kill properties** (`KillMode` / `KillSignal` / `TimeoutStopUSec`) on a sandbox transient unit and record the result; install the **cgroup-wide-kill INVARIANT verbatim** in the plan. If the probe shows a non-default `KillMode`, the implementation MUST set `KillMode=control-group` explicitly on the executor unit. **[fold: A10]** | The probe prints the effective properties; the invariant text is present verbatim in `phase2-plan.md`; the probe's result is recorded in the tester RESULTS (the architect's confidence condition (a)). | `tests/packs/preflight_unit_kill_probe_test.sh` (new) | — | R13 | AC11 |
| **T0.12** | Measure the **target-host baselines** the budgets need: per-file fork cost (`shasum`+`awk` spawns per file), the **plugins double-walk** wall time, and the agents+frontend walk on the **release payload shape** (431 files) — plus the real `integrity_verify` wall time on a staged release. **[fold: A13, A12]** | Four measured numbers land in `phase2-plan.md` §3.2 with the command that reproduces each; the budget derivations cite **these** numbers, not the architect's synthetic ones. | `tests/packs/preflight_baseline_test.sh` (extended) | T0.8 | R12 | AC12 |
| **T0.13** | Measure the **batched-shashing** candidate: one `shasum -a 256` per tree over many files, and prove **byte-parity** against the current per-file output (the A8/A7 pin). **[fold: A7]** | The batched output is byte-identical to the per-file output on the plugins tree (or the difference is enumerated and adjudicated); the measured post-batching plugins walk is the input to the stage budget derivation. | `tests/packs/preflight_shasum_batch_parity_test.sh` (new) | T0.12 | R12 | AC13 |
| **T0.14** | Publish the **census companion table** (§4.2: failure-mode class + stderr contract per site), record the **no-`set -e`** fact and the `promote.sh:276`/`:588` **continue-on-empty rc-preservation** pins, and keep the two stderr-suppressing sites (`:1803`, `:1876`) suppressing. **[fold: A18]** | §4.2 exists with all 26 rows classed; the rc-preservation pins are in the pack; a planted `set -e` in a pipeline script turns the pack RED. | `tests/packs/preflight_census_test.sh` (extended), `phase0-plan.md` §4.2 | — | R-N5 | AC14 |

---

## 6. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| **AC1** | The anchor checker passes on the implementation rev and **fails** when a cited line is shifted (mutation-verified: shift one line ⇒ non-zero exit). |
| **AC2** | The fixture corpus has **22 corpus cases + G1–G5 = 27 fixtures**; each carries a recorded BASE verdict and a BASE **command-substitution-processed** stdout digest; the corpus is committed under `tests/fixtures/journal/`. **[fold: W4]** |
| **AC3** | The large fixture's sha256 is asserted at pack runtime; a mismatch is a **loud** failure (not a silent skip). |
| **AC4** | The BASE leg demonstrates the original symptom: the read path does **not** complete within the 60 s budget on the large fixture, and the sandbox artifacts (CPU spin signature, zero journal rows written) are captured. |
| **AC5** | The census generator's output equals the §4 tables (25 distinct `journal_read` call sites + 5 extra O(N²) sites); a planted extra site makes the pack **RED**. |
| **AC6** | Every unbounded external-command site on the preflight/stage path is listed with its `_run_bounded` status. |
| **AC7** | The sandbox fence (the canonical contract: `phase5-plan.md` §3) refuses a live-shaped `INSTALL_DIR` and the refusal is asserted by a self-test leg; the `env -u POSTGRES_*` idiom appears **nowhere** in the lane's test code (grep-asserted). |
| **AC8** | The healthy-baseline measurements are reproducible by a documented command, and the numbers used to size the Phase 2 budgets cite those measurements. |
| **AC9** | The corpus has **22 corpus cases + G1–G5 = 27 fixtures**; every case carries a BASE verdict, a BASE **command-substitution-processed** stdout digest and its stderr class; the duplicate-key case's expected bytes are the MEASURED corrupt string, labelled BASE-bug-preserving. |
| **AC10** | The proxy-validation leg proves proxy ≡ true BASE on all 27 corpus fixtures (22 corpus + G1–G5) + the 32 KB timing fixture; the symptom leg stays bounded at 60 s and asserts non-completion; no timing claim cites the proxy. |
| **AC11** | The transient-unit kill-property probe reports the effective `KillMode` / `KillSignal` / `TimeoutStopUSec` on a sandbox transient unit, and the cgroup-wide-kill INVARIANT is present **verbatim** in `phase2-plan.md`. |
| **AC12** | Four target-host baselines are measured and reproducible (fork cost, plugins double-walk, agents+frontend walk on the release payload shape, real `integrity_verify` wall time); the Phase-2 budget derivations cite them. |
| **AC13** | The batched `shasum` output is **byte-parity** with the current per-file output on the plugins tree (differences enumerated if any), and the post-batching plugins walk is measured and recorded as the stage-budget input. |
| **AC14** | §4.2 classes all 26 census rows by failure-mode class + stderr contract; the `promote.sh:276`/`:588` rc-preservation pins pass; `:1803`/`:1876` still suppress stderr; a planted `set -e` turns the pack RED. |


---

## 7. Fold log (architect → Phase 0), 2026-10-29

| A# | Subject | Status | Where |
|----|---------|--------|-------|
| A10 | Transient-unit kill-property runtime probe + cgroup-wide INVARIANT | folded | P0-D9, T0.11, AC11 |
| A13 | Target-host measured baselines (fork cost, plugins double-walk, agents+frontend walk) | folded | P0-D9, T0.12, AC12 |
| A14 | stdout oracle = command-substitution emulation (feeds the fixture digests) | folded | P0-D6, T0.9, AC9 |
| A15 | Corpus 15 → 22 + guard-boundary trio + duplicate-key BASE-bug-preserving | folded | P0-D6, T0.9, AC9 |
| A17 | Differential harness (true-BASE oracle; Option-D proxy only after validation) | folded | P0-D7, T0.10, AC10 |
| A18 | Census companion columns + no-`set -e` + rc-preservation pins | folded | P0-D8, §4.2, T0.14, AC14 |
| A7 | Stage-tree budget derivation (the batched-hashing parity measurement) | folded | P0-D9, T0.13, AC13 |
| A12 | Budget justification re-derivation (the inputs) | folded | T0.12, AC12 (the derivation itself lands in `phase2-plan.md` §3.2) |

**Superseded in Phase 0:** none. **Already present:** P0-D5's fixture-location rule (extended, not replaced, by A15's
corpus growth); P0-D1's fixture provenance rule (extended by A15's oracle rule).

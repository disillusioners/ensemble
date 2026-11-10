# Launcher journal-sweep fix — RE-GATE verification report

**Date:** 2026-11-09/10 (local +07) · **Verifier:** tester (test leader)
**Gated commit:** **`361cba6b3498f33cece4ce714501adee2fc95970`** · BASE `818411107`
**Branch:** `feature/launcher-journal-sweep-fix` · **Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-launcher-fix`
**Chain:** 818411107 → d9274053d → 14d342bda → 510e72f64 → e5545b458 → f7eccfb26 → c535ab89f → f2414f8ec → 59cc868c9 → **361cba6b3**
**Supersedes:** the earlier report against `14d342bda` (that commit's blocking defect — the `_js_json_sub` `j <= tn` truncation bug — is **fixed and pinned** in this HEAD; this document replaces that file's content so the last word in the repo is the current gate).

---

# VERDICT: 🔴 **FAIL** — 2 release-blocking O(n²) boot-path defects remain unfixed at `361cba6b3`

**Counts:** 9 legs dispatched · **8 adjudicated** · 1 in flight (corroboration only) · **6 PASS · 2 FAIL (blocking)** · 0 timeouts · 0 re-dispatches · 0 worktree writes by the tester · 0 live-path contacts.

**The branch fixed the journal READ path only. The boot sweep's WRITE path and the per-ENTRY readers are still quadratic and still on the boot path. Merging as-is leaves the user's ~30-minute boot wedge in place on the journal sizes that trigger it.**

| Claim | Verdict |
|---|---|
| Launcher pack green, no regressions, §13 family discriminates | ✅ PASS |
| Journal cap correct, cross-generation compatible, F-2 fixed | ✅ PASS |
| `_js_journal_read` / `_js_json_sub` are O(n) | ✅ PASS (measured) |
| **`_js_journal_update` (write path) is O(n)** | ❌ **FAIL — BLOCKING** |
| **`_js_history_has_defer_reason` (per-entry reader) is O(n)** | ❌ **FAIL — BLOCKING** |
| The branch removes the ~30 min boot wedge | ❌ **NOT SUBSTANTIATED** |

---

## 1. The two blocking defects

### B1 — `_js_journal_update` — `launcher.sh:730` — per-CHARACTER walk of the whole `history` value

Per-character strips at `:761 / :765 / :766 / :779`; loops at `:749 / :762 / :769`; idiom `rest="${rest:1}"` per character.

**Instrumented proof of the walk:** `rest=` assignments = **8,074 for an 8,071-byte history array** (exactly one per character). Loop iterations == history byte length at every size (8,071 / 16,275 / 32,676 / 65,478).
**Bytes copied:** 0.033 → 0.132 → 0.534 → 2.143 GB — ratios **4.07 / 4.03 / 4.01** = exactly **O(n²)**.

**Boot path (static call graph from the HEAD blob, all 58 functions):**
`main(:3364)` → `_journal_sweep(:2714)` → `_js_history_append(:796)` → `_js_journal_update(:828)`.
`main`→`_journal_sweep` is **unconditional** — before binary resolution and before `run_loop`.

**Measured:** 110 KB → **exceeded the 300 s bound**; isolated verbatim loop **230.02 s**. 220 KB → **exceeded the 300 s bound** with **25.35 GB** copied ⇒ **extrapolated ~910–1280 s (15–21 min)** — i.e. the original wedge. Per the repo's `verify-02-scaling-not-incident-size` rule, the *measurement* is "exceeds the 300 s bound"; the 220 KB figure is an extrapolation.

### B2 — `_js_history_has_defer_reason` — `launcher.sh:1216` — per-ENTRY walk (loop body `:1236-1268`)

Idiom `rest="${rest#*'{"ts":'}"` / `entry="${rest%%'{"ts":'*}"` — one copy of the remaining history per entry.

**Boot path:** `main(:3364)` → `_journal_sweep(:2714)` → `_js_sweep_flipped_disposition(:2338)` → size-guard branch `:2460` → `_js_serving_defer(:2028)` → `_js_history_has_defer_reason(:1216)`. **Before** `resolve_binary` and **before** the `starting:` line (`:3192`).

**Measured — end-to-end sandbox boot at 225,608 B / 1,521 entries** (inert stub binary, no daemon, no port):

| locale | wall → `starting:` | total | exit |
|---|---|---|---|
| `LC_ALL=C` | **5.892 s** | 12.417 s | 0 |
| `LC_ALL=en_US.UTF-8` (the daemon's env) | **46.656 s** | 96.586 s | 0 |

**Trace-proven reachability** (`bash -x`):
```
t=0.875  + _js_serving_defer … journal-size-guard …
t=0.875  + _js_history_has_defer_reason … journal-size-guard
t=48.234 + _js_serving_defer … journal-size-guard …        ← 2nd call = T15 run-loop hook
t=48.234 + _js_history_has_defer_reason … journal-size-guard
```
**Re-echo per boot:** `rest=` **3,044** / `entry=` **3,042** (= 1,522 / 1,521 per scan × **2 scans**: boot sweep + T15 hook); `rest` trace payload **343,728,198 B (344 MB)** for 2 scans; single scan 172 MB. Journal sha256 unchanged ⇒ the write-once branch fired ⇒ the 44 s is **pure scan, no append**.

**Quadratic in user CPU** (UTF-8, 1 scan): 190 / 380 / 760 / 1521 / 3040 entries → **0.51 / 2.05 / 9.56 / 45.53 / 186.23 s** (t/n² ≈ 1.4–2.0e-5, ≈4× per doubling).

### Both are UNFIXED halves of the same defect, not regressions
`_js_journal_update`'s per-character loop and both per-entry readers are **byte-identical to BASE `818411107`**. The branch rewrote `_js_journal_read` and `_js_json_sub` only. **Rewriting the READ did not make the boot path linear.**

### Aggravating facts
1. **The 96 KB size guard does not bound the quadratic.** `SWEEP_JOURNAL_SIZE_GUARD_BYTES` (default 98304, `:133`) is checked **only** inside `_js_sweep_flipped_disposition` (`:2458`), and when it fires it calls `_js_serving_defer` → `_js_history_has_defer_reason` — **another quadratic**. A ≥96 KB journal routes the flipped path straight into it.
2. **`_journal_sweep`'s pre-flip branch (`:2714-2926`) has NO size guard at all** — it calls `_js_history_append` → `_js_journal_update "history"` on a journal of any size.
3. **Locale multiplier.** bash glob expansion (`${var#p}` / `${var%%p}`) is **~7× slower under UTF-8 than `LC_ALL=C`**; the awk passes pin `LC_ALL=C`, the bash matcher does not. Same 1,521-entry scan: **6.4 s user CPU (C) vs 45.5 s (UTF-8)**. The daemon env sets `LC_CTYPE=UTF-8`, so production pays the UTF-8 cost.

### Non-blocking (measured, boot-path, but tiny input)
`_js_sanitise_run_id` (`:1275`, O(k²) over a ~40-char run_id → 0.007 s) · `_js_quarantine_remove` (`:1613`, O(k²) over `quarantined[]` → 0.03–0.04 s at k=3200). **Next bottleneck** after B1/B2: `_js_history_has_event_for_target` (`:1144`) has the identical per-entry idiom on the same path (rung-3 `:2621/:2624`), O(n·m) with a ~130× smaller constant (bytes copied 0.26→1.04→4.15→16.56 MB).

**Complete loop census:** every `while`/`for` across all 58 functions (22 loops) — no other bash loop over journal data.

---

## 2. Required changes (NOT applied — quick-fix not authorised)

1. Rewrite **`_js_journal_update`'s per-character value scan (`:749-789`)** with the same single-pass awk extractor the branch already uses in `_js_json_sub`.
2. Rewrite the per-entry readers **`_js_history_has_defer_reason` (`:1216`)** and **`_js_history_has_event_for_target` (`:1144`)** — same extractor pattern.
3. **Extend the size guard to `_journal_sweep`'s pre-flip branch (`:2714-2926`)** or route the guard's DEFER through a non-walking reader.
4. **Pin `LC_ALL=C`** for the bash matcher paths (partial mitigation; also makes perf gates host-comparable).

Each rewrite needs a **§13-style pin**: a boot-path gate at ≥220 KB with a bounded watchdog, plus a size-guard leg that proves the ≥96 KB regime does not route into a quadratic reader.

---

## 3. Per-item gate results

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Launcher pack @ `361cba6b3` | ✅ **PASS** | `296 passed, 0 failed, 1 skipped` / `ALL PASS`, exit 0, 80 s (cap 300 s). `bash -n launcher.sh` rc=0. HEAD unmovable. 27 section headers, 0 `FAIL:` lines. Skip = `8b-r2 repo _journal_sweep body ≡ live-install copy` (env-gated; `ENS_TEST_LIVE_LAUNCHER` deliberately unset). **§13k executed, 20 assertions green** (4 torn shapes × {rc=1, EMPTY stdout}; 3 named cases × {byte-exact 77/27/103 B, `SUB_history\|1\|`, empty stdout, torn read refused}); non-vacuity confirmed outside the pack (all torn shapes → rc=1 with 0 stdout bytes; well-formed control `{"current":"v1","history":[]}` → rc=0 stdout `[]`). `j < tn` bound at `:586`, rationale `:572/:581`. |
| 2 | Journal-history cap pack | ✅ **PASS** | `27 passed / 0 failed / 0 skipped`, exit 0, 3 s. `daemon.__file__` → inside the worktree (proof). **F-2 pins present and green**: `test_trim_read_failure_is_logged_not_silent`, `test_trim_write_failure_is_logged_not_silent` — they assert the phase-qualified message (`(read failed)`/`(write failed)`) **and** the failure class (`JournalTorn`/`OSError`). Source: `init_guard.py:628` `"MCP journal history trim skipped (%s failed): %s: %s"`. Composition **20 pre-existing + 5 cap (`d9274053d`) + 2 trim pins (`e5545b458`) = 27**; BASE collects only **20** in this file. |
| 3 | `concurrency_atomic_unit_test` | ✅ **PASS** (quarantine-adjudicated) | raw **94P / 5F / 74S / 0E**, exit 1 (bare — no `RESULT:` line; read the exit code), 20 s. The 5 reds are exactly `tests/test_instance_metadata_atomic.py` (`TestSetMetadataFunctional::test_set_first_key`, `::test_set_overwrites_existing_key`, `::test_set_preserves_sibling_keys`, `TestUpdateTitleFunctional::test_update_title_for_existing_instance`, `::test_update_title_overwrites_previous`), all `DetachedInstanceError` ⇒ matches QUARANTINE.md's *Phase-C merge gate 2026-10-22* row (A/B-proven at `639b7129b`, an ancestor of both BASE and HEAD). **Zero branch reach**: the branch touches 5 files, none under `daemon/repositories/`, no root `tests/test_*.py`; `grep init_guard` across all 13 pack files → 0 hits; all 13 pack test files sha-identical base↔HEAD. PG scratch delta **0** (662 → 662, same sha256). |
| 4a | BASE dual-side leg (818411107) | ✅ **PASS** | `216 passed / 0 failed / 1 skipped`, exit 0, 80 s. **FAILED list EMPTY** (triple proof: no `failed:` block, no `✗`, exit 0). ⇒ **net-new failures = 0**; the HEAD leg's FAILED list is entirely branch-attributible. Historical BASE reds (207P/3F/1S, BSD-`date`-on-GNU) do not reproduce on this Darwin host. Mechanism confirmed: the pack hardcodes `LAUNCHER="$REPO_ROOT/launcher.sh"` (line 23) — **no `LAUNCHER_SH` override exists** — so the 4-file scratch tree (launcher.sh at the tree **root**) IS the BASE-leg mechanism. §13 is **entirely branch-new**: BASE 217 nodes → HEAD 296 (**+79**). |
| 4b | Discrimination cross leg (HEAD pack + BASE launcher) | ✅ **PASS** | **TIMEOUT exit 124** at exactly 300 s — the expected outcome. Verbatim: `FAIL: 13f 250KB journal read completes inside the 2s watchdog / expected: 0 / actual: 124`. §13a–§13e PASS on BASE; **§13f FAIL+HANG**; §13g–§13k never reached. Hang localised 4 ways: mechanism (pack `:1277` bounded read → BASE maps SIGKILL to 124; pack `:1280` unbounded read hung); BASE code (`818411107:launcher.sh:382-383` `for ((i=0;i<${#json};i++)); c="${json:i:1}"`); fixture artefacts (`state.json` 312,468 B, `read.out` **0 bytes** ⇒ killed inside the char loop); process trace (state **R**, **100 % CPU**, bash interpreter hot path). BASE quadratic measured: 16 KB→3 s · 32 KB→11 s · 65 KB→41 s · 131 KB→166 s · 312 KB→124 (cap) — ratios **3.67 / 3.73 / 4.05**; 131→312 KB extrapolates ≈ **940 s ≈ 15.7 min**. |
| 5 | `test_journal_splice.sh` | ✅ **PASS** (fail-closed as designed) | **FAIL-CLOSED FATAL F1 abort at preflight stage 3**, exit 1, 1.946 s: `FATAL F1: resolved LIVE_PARENT escapes /tmp (/private/tmp/ens-upglib-home.TS2SCV/agents-ensemble) — refusing to run live-install cells`. Only 2 section banners emitted (§1, §2) — **no §3–§9, no cell ran**. Abort provably precedes every creating/destructive op: `preflight_guard` at `:213-214` vs the only such ops at `:218 mkdir -p` / `:222 rm -rf`. Stage 2 passed (`$HOME` literally `/tmp/…`); stage 3 failed because macOS `pwd -P` canonicalises `/tmp`→`/private/tmp` ⇒ **no HOME under /tmp can satisfy a literal `/tmp/*` prefix on macOS** (decorative-fence defect, ticket **6ca4043d** — NOT fixed, not shimmed). Zero live contact: `~/agents-ensemble` mtime `00:31:48`→unchanged, 8 entries→8; `~/agents-ensemble-demo` mtime `02:11:14`→unchanged; the fake-HOME sandbox removed by the lib's EXIT trap; the refused path never created. Pack byte-identical BASE↔HEAD (`5ff3cb30…`); `upgrade_test_lib.sh` identical both ends (`df21f371…`). **PACKS.md: NOT registered** (0 hits repo-wide) — a registration gap, not a broken row. |
| **4(a)** | Forced deferred-serving boot @ 220 KB | 🔴 **FAIL — BLOCKING** | See **B2** above. Trace-proven reach; 5.892 s (C) / **46.656 s (UTF-8)** to `starting:` at 225,608 B; quadratic in user CPU (4× per doubling). Also proves the developer's 220 KB → `starting:` in 0.164 s was **vacuous**: `in_flight` `null` / absent / `kind=restart` all short-circuit (`:2744` / `:2743` / `:2757-2760`) before any history scan; only `kind=promote` + `flipped:true` reaches it. |
| **4(d)** | Boot-path audit of remaining bash history walkers | 🔴 **FAIL — BLOCKING** | See **B1** above. Full 10-row inventory produced: function \| line \| idiom \| rebuilds \| size \| boot-path (static call graph **and** executed `bash -x`) \| t@110 KB \| t@220 KB \| ratio \| verdict. Controls all O(n): `_js_journal_read` 1.72 · `_js_json_sub` 2.13 · `_js_json_field` 1.87 · `_js_journal_update` scalar control 2.14 (0.340→0.729 s — proves the cost is the *value* size, not the function). 22-loop census: no other bash loop over journal data. |
| 4(b)/4(c) | Per-function 220 KB timings + `_js_history_append` output byte-parity | ⏳ **IN FLIGHT at commit time** | 4(a) and 4(d) already establish the verdict independently; this leg adds corroboration of the per-reader ratios and the append byte-parity. It was NOT run against OLD at 220 KB (the ~30-min wedge); OLD was scaled down and the 220 KB figure labelled an EXTRAPOLATION, per `verify-02-scaling-not-incident-size`. |

---

## 4. Carried findings

| ID | Sev | Finding |
|---|---|---|
| B1 | 🔴 blocking | `_js_journal_update` `:730` still O(n²) on the boot path (write side). |
| B2 | 🔴 blocking | `_js_history_has_defer_reason` `:1216` still O(n²) on the boot path (per-entry reader). |
| G1 | 🟠 merge blocker | `.agents/tidier/notes.md` carries an **uncommitted** +10 EOF-append **outside** the branch's file set ⇒ repo convention-(o) trap: `git stash push -- .agents/tidier/notes.md` → `git merge --no-ff` → `git stash pop` (conflict EXPECTED) → `git checkout --ours .agents/tidier/notes.md && git add` → **never drop the stash**. |
| G2 | 🟠 hygiene | `tests/test_launcher.sh` has **no Layer-2 (script-internal) timeout** — only per-command `_js_run_bounded` watchdogs, no global self-timer/trap. It ran under Layer 1 only (300 s outer, finished in 80 s). Pre-existing (the pack predates the branch). |
| G3 | 🟡 pre-existing | `tests/test_launcher.sh:458-465` `_ts_off()` emits `date -u -v"${off}"S`; BSD `date` rejects the unsigned form, so §8f's `cooldown_until` seeds as the **empty string** ⇒ the "cooldown-active" shape is never exercised (the pins pass only because `_journal_sweep` never *reads* `cooldown_until`). Byte-identical at BASE. |
| G4 | 🟡 test-design | §13h **cannot** isolate M2 from M3 (they differ only on odd backslash runs, so any even-run fixture kills both) — inherent. The §13h comment should state that §13h+§13i are the *pair* that separates them. |
| G5 | 🟡 test-design | §13i adds **no** mutant-killing power beyond §13a/§13d for M3 — its value is crisp single-cause attribution. Do not double-count. |
| G6 | ⚪ naming | The §13f fixture is **312,468 B (~305 KB)**, not the nominal 250 KB (the pack's own assertion only enforces `>= 240000`) — the gate exercises a **larger** input than its name implies. |
| G7 | ⚪ tooling | `git diff --stat` renders **combined** change counts — cite `--numstat`. |
| G8 | ⚪ tooling | Comparing sha256 of a git blob path that **does not exist** yields the empty-input hash `e3b0c442…` on both sides ⇒ a naive base↔HEAD identity check prints "IDENTICAL" and proves nothing. Guard every blob-identity claim (existence check + explicit empty-hash rejection + ADDED/REMOVED/CHANGED distinction + a third leg at the A/B-proven commit). |

## 5. UNVERIFIED

1. **Real Ubuntu/mawk leg** — every measurement ran on macOS `onetrue awk` (bash 3.2.57); mawk/gawk **never executed**.
2. **Real systemd boot** — no systemd unit; the boot proofs were a direct `bash launcher.sh` with an inert stub binary. No `make deploy-live`, no real daemon start.
3. **The original ~215 KB production journal** — the live `~/agents-ensemble/releases/state.json` was **42,121 B** (not ~215 KB) and the install was redeployed during the window; the ≥215 KB claim rests on synthetic journals.
4. **§13g–§13k discrimination against BASE** — never reached in the cross leg (it times out at §13f), so discrimination is proven **up to §13f only**. A BASE variant with a bounded §13f fixture would be a separate leg.
5. **The 220 KB figure for the OLD implementation** — extrapolation only (measured at 36.9 KB and 41.1 KB; 131 KB→166 s; 312 KB→exceeded the 300 s bound).
6. **`concurrency_atomic_unit_test`'s 74 skipped nodes** — not individually re-audited; only the 5 reds were adjudicated.
7. **Post-`361cba6b3` work** — anything landed after this commit is unverified by this report.

## 6. Release notes

- **Verdict: the v0.29.9 release GO is BLOCKED** by B1 and B2. The branch is a correct, well-pinned fix to the journal READ path; the boot sweep's WRITE path and per-ENTRY readers remain quadratic.
- `v0.29.8` is **TAKEN** (`c113531f2` — consumed by a build **without** the fix). Merge the fix branch to `latest` first; the next tag is **v0.29.9**.
- The fix is **not** in the live install: the recreated `~/agents-ensemble/launcher.sh` was sha `c4f640e1…` = BASE `818411107` = `origin/latest` (pre-fix), so the wedge risk persists in prod until merge **and** redeploy.
- Merge hygiene: **G1** must be handled (stash-by-path dance) before any `--no-ff` merge.

## 7. Hygiene attestation

Worktree + `/tmp` only throughout. Live paths `/Users/nguyenminhkha/agents-ensemble` and `/Users/nguyenminhkha/agents-ensemble-demo` never written, never `cp`-ed into, never executed against. No `make install|uninstall|deploy-live|deploy-demo`, no `scripts/deploy.sh live|demo`, no `scripts/stop-ensemble.sh`, no `scripts/upgrade/*` against a live dir, no `rm -rf` under `~/agents-ensemble*`. No process killed or restarted; no daemon signalled; port 8088 untouched; ensemble-vm untouched; no `.env` read; no secret printed. No source file edited by the tester; no quick fix applied (quick-fix not authorised). Every pack run carried a command-level `timeout 300`; none was relaxed.

# Launcher journal-sweep fix — RE-GATE verification report

**Date:** 2026-11-10 (local +07) · **Verifier:** tester (test leader)
**Gated commit:** **`a444c2b94a1927948ad02a8b8895fc55b9d2c7b7`** · BASE `818411107`
**Branch:** `feature/launcher-journal-sweep-fix` · **Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-launcher-fix`
**Chain:** `818411107` → `d9274053d` → `14d342bda` → `510e72f64` → `e5545b458` → `f7eccfb26` → `c535ab89f` → `f2414f8ec` → `59cc868c9` → **`361cba6b3`** → `a444c2b94` (tester RESULTS doc only)
**Upstream note:** `origin/latest` has moved to `424494082`; this gate is of the BRANCH, not of a merge.
**Supersedes:** the earlier report against `14d342bda`, and the prior re-gate against `361cba6b3` (both FAIL). This document replaces that file's content so the repo's last word is the current gate.

---

# VERDICT: 🔴 **FAIL** — the branch fixes the READ path, but the boot wedge survives on the defer and write paths

**Legs:** 10 workers dispatched (11 dispatches incl. one re-dispatch of the critical 7a leg) · **10 of 11 adjudicated** · **6 PASS · 1 PASS-WITH-CLASSIFIED · 1 EXPECTED-FAIL-CLOSED · 3 FAIL · 1 PARTIAL** · 0 timeouts by the tester · 0 source edits by the tester · 0 live-path writes.

**One-line rationale:** the branch is *safe* and is a *large, real* improvement — the journal READ and JSON extraction are now genuinely O(n) (measured, 392× on the same real file) and the output is byte-identical to BASE at every feasible size. But the O(n²) **write** and **per-entry read** paths that the same boot walks were **not** rewritten, the ≥96 KB size guard **routes into** them rather than bounding them, and the user's incident shape (a stale deferred serving txn) still boots in **minutes** at 220 KB. Merging this does **not** close the ~30-minute wedge the release GO is being requested for.

| Claim | Verdict |
|---|---|
| No regressions in the changed packs | ✅ **PASS** — NET-NEW ∅ across three A/B pairings |
| Journal READ (`_js_journal_read`) is O(n) | ✅ **PASS** (measured) — 0.128 s @ 225,381 B vs BASE 85.290 s @ 32,883 B |
| JSON extraction (`_js_json_sub`) is O(n) | ✅ **PASS** (measured) — 0.168 s @ 225,381 B vs BASE 110.754 s @ 32,883 B |
| New output is byte-identical to BASE | ✅ **PASS** at every feasible size (≤ 32,862 B) — sha256 **and** `diff` agree |
| Cap `JOURNAL_HISTORY_MAX_PER_SERVER=20` correct | ✅ **PASS** — 27/27 |
| **`_js_history_has_defer_reason` is O(n)** | ❌ **FAIL — BLOCKING** — ×3.9–4.3 per ×2 bytes |
| **`_js_history_has_event_for_target` is O(n)** | ❌ **FAIL — BLOCKING** — ×3.8–4.1 per ×2 bytes |
| **`_js_history_append` is O(n)** | ❌ **FAIL — BLOCKING** — ×3.9–5.6 per ×2 bytes; >300 s at 220 KB |
| The ≥96 KB size guard bounds the quadratic | ❌ **FAIL — BLOCKING** — it **routes into** it |
| The branch removes the ~30-minute boot wedge | ❌ **NOT SUBSTANTIATED** |

---

## 1. Scope decision

Full gate items 1–7 as specified by the release brief; **no expansion to the Release Gate** and **no whole-tree leg**. Rationale: the change set vs BASE `818411107` is 4 files + the pack registry + this report —

```
M  launcher.sh                          (+708 / −96)
M  daemon/mcp/init_guard.py             (+104)
M  tests/test_launcher.sh                (+428)
M  tests/unit/tools/test_mcp_init_guard.py (+329)
M  .agents/tester/PACKS.md
A  .agents/tester/RESULTS/2026-11-09-launcher-journal-sweep-fix-verification.md
```

— touching **no core-seam path** (`daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`, `daemon/services/task_processor.py`, `daemon/services/job_queue_service.py`), so per `.agents/tester/rules/ensure.md` § Gate Tiers this is a **Tier 1 blast-radius gate** (scoped dual-side FAILED-list diff), not Tier 2. Tier-1 budget: legs + instruments ≈ 17 min, inside the 30-min hard cap.

**Gated-commit note (important).** The brief named `361cba6b`. The worktree HEAD is `a444c2b9`, which **is** a descendant of `361cba6b`, and `git diff --name-status 361cba6b..HEAD` shows exactly **one** changed file: this RESULTS document. `launcher.sh` is byte-identical (`sha256 008f88909bd401f0541323f3e4eb3e08c92c70144077d936a445d026a438254e`) across `361cba6b` and `a444c2b9`, and all ten workers independently confirmed that sha. **The code under test is therefore exactly the code the brief asked to gate.**

---

## 2. What the branch genuinely fixes (the good half)

The branch rewrote two functions from per-character bash loops to single POSIX-awk passes:

| function | BASE (per-char bash) | HEAD (awk) | verdict |
|---|---|---|---|
| `_js_journal_read` (628–709) | `for ((i=0; i<${#json}; i++)); do c="${json:i:1}"` | one awk pass — **0.128 s @ 225,381 B** (BASE: 85.290 s @ 32,883 B) | **O(n)** ✅ |
| `_js_json_sub` (483–627) | `rest="${rest:1}"` + `out="$out$char"` | one awk pass — **0.168 s @ 225,381 B** (BASE: 110.754 s @ 32,883 B) | **O(n)** ✅ |

Independently re-measured at four sizes with a harness baseline subtracted: `_js_journal_read` and `_js_json_sub` scale at **1.05× and ~1.0× per doubling** (0.012–0.015 s at 55 KB) — the O(n) claim holds.

**Byte-parity is preserved.** `_js_history_append` output, OLD (`818411107`) vs NEW (`a444c2b9`), each leg in its own isolated `/bin/bash` process on byte-identical inputs, `date` shim-frozen so `ts` is deterministic, escaping arms exercised (`quote="`, `back=\`, tab, CR, newline, `é`):

| input B | entries | OLD s | OLD len | OLD sha256 | NEW s | NEW len | NEW sha256 | verdict |
|---|---|---|---|---|---|---|---|---|
| 89 (empty history) | 0 | 0.115 | 343 | `0f05d3671f91d5f4…` | 0.230 | 343 | `0f05d3671f91d5f4…` | **IDENTICAL** |
| 153 (escape-heavy) | 1 | 0.143 | 268 | `c724188af4a1defe…` | 0.191 | 268 | `c724188af4a1defe…` | **IDENTICAL** |
| 6,256 | 36 | 3.262 | 6,512 | `7a5730b92dc3598d…` | 0.530 | 6,512 | `7a5730b92dc3598d…` | **IDENTICAL** |
| 10,000 | 58 | 20.618 | 10,129 | `355495d2dc76b27e…` | 4.726 | 10,129 | `355495d2dc76b27e…` | **IDENTICAL** |
| 16,431 | 97 | 18.160 | 16,687 | `c16ca712ae3b125f…` | 4.473 | 16,687 | `c16ca712ae3b125f…` | **IDENTICAL** |
| 32,862 | 195 | 83.576 | 33,118 | `7bd78c2e56fcbc58…` | 17.811 | 33,118 | `7bd78c2e56fcbc58…` | **IDENTICAL** |

`diff -u` reports **no differences** for every pair — not merely matching hashes. Two NEW runs on identical input with different PIDs produced identical sha256 (`9da02478…`).

> **Correction to a relayed claim.** The 7b/c leg's report stated the HEAD `_js_history_append` is "~10× **slower** than the OLD one at 220 KB". Its own table contradicts that: NEW is **4.1–6.2× faster** than OLD at every comparable size (83.576 s → 17.811 s at 32,862 B). Both legs exceed 300 s at 220 KB, so no ordering can be established there. The correct statement: **the O(n²) was removed from the read/extract, which speeds the append up ~4–6×, but the residual per-character write keeps both implementations past the 300 s bound at the user's journal size.**

**220 KB byte-parity was NOT executed** — stated plainly, with no implication of a full parity: NEW @220,000 B → `rc 124` at 300.152 s, no output bytes; OLD @220,000 B → `rc 124` at 300.221 s, file unchanged. Parity is proven **only up to 32,862 B**. Both 220 KB kills left the target **byte-identical to its input** (`cmp` clean) — the atomic temp-file + `mv` discipline held: a blocked append commits nothing.

---

## 3. THE BLOCKING DEFECTS — 3 quadratic boot-path walkers survive at HEAD

The branch rewrote the READ and EXTRACT functions only. `grep -n 'rest='` over HEAD returns exactly **two surviving idiom clusters** — per-CHARACTER (748–789) and per-ENTRY (1166–1174, 1236–1244).

| # | function | lines | idiom | on boot path? | cost @ 220 KB | verdict |
|---|---|---|---|---|---|---|
| **B1** | `_js_journal_update` | 730–792 (757–780) | `rest="${rest:1}"` per character; `c="${rest:0:1}"` | **YES** — `3364 _journal_sweep` → `2915 _js_history_append` → `828 _js_journal_update` → `730`; and `3364` → `2896 _js_sweep_flipped_disposition` → `2460 _js_serving_defer` → `2041 _js_history_append` → `828` | **>300 s (exit 124), reproduced 3×** incl. an independent pure-bash replica. At 32,748 B: **528,726,821 chars copied, 94.376 s** | **O(n²)** |
| **B2** | `_js_history_has_defer_reason` | 1216–1270 (1236–1245) | `rest="${rest#*'{"ts":'}"` / `entry="${rest%%'{"ts":'*}"` | **YES** — `3364` → `2896` → `2458` size-guard arm → `2460 _js_serving_defer` → `2028` → `1216` | **149,865,172 chars copied**; 4.17 s (LC_ALL=C, net) / 28.492 s least-contended full-function | **O(n²)** |
| **B3** | `_js_history_has_event_for_target` | 1144–1198 (1166–1175) | same per-entry idiom | **YES** — `2896` → `2495/2506/2621/2624` → `1144` (reachable only below 96 KB; the guard returns earlier at ≥96 KB) | identical counters to B2; 4.61 s (LC_ALL=C, net) | **O(n²)** |

### 3.1 The size guard ROUTES INTO the quadratic — it does not bound it

There is exactly **one** guard site in the whole file (`grep -n SWEEP_JOURNAL_SIZE_GUARD_BYTES` → 133, 134, 2020 (comment), 2445, 2451, 2454, 2455), and it lives inside `_js_sweep_flipped_disposition`:

```bash
2458:    if [ "$jsize" -ge "$jguard" ]; then
2459:        _log "WARN: journal sweep: journal is ${jsize}B ≥ ${jguard}B (the launcher-side size guard) — CONSERVATIVE DEFER (never a rollback in this regime; …)"
2460:        _js_serving_defer "$journal" "$target" "$run_id" "$age" \
2461:            "journal-size-guard" \
2462:            "the journal exceeds the launcher-side size guard — the launcher never rolls back in this regime"
2463:        _js_sweep_release "$install_dir" "$lock_held"
2464:        return 0
2465:    fi
```

Its only action is `_js_serving_defer`, whose **first act** (`:2028`) is the per-entry O(n²) reader B2 — and, when the write-once check misses, `_js_history_append` (`:2041`) → the per-character O(n²) writer B1. **The guard is a disposition switch (defer instead of rollback), not a size bound.** A ≥96 KB journal therefore routes *into* the quadratic, twice over.

And `_journal_sweep` (2714–2926) contains **no** `jsize` / `jguard` / `GUARD` reference at all — `sed -n '2714,2926p' launcher.sh | grep -E 'jsize|jguard|GUARD'` → **NONE**. Its pre-flip branch is **completely unguarded**.

### 3.2 Scaling evidence (ratio-based, load-robust)

Untraced per-function timings, `LC_ALL=C` (launcher-realistic — launchd inherits no locale), fresh `timeout` subprocess per rep, `python3 perf_counter`, harness baseline subtracted, min-of-N:

| input B | entries | `_js_history_has_defer_reason` net s | per ×2 | `_js_history_has_event_for_target` net s | per ×2 | `_js_history_append` net s | per ×2 |
|---|---|---|---|---|---|---|---|
| 6,256 | 36 | 0.0145 | – | 0.0112 | – | 0.5231 | – |
| 16,431 | 97 | 0.0373 | ×2.58 | 0.0374 | ×3.34 | 2.9585 | ×5.66 |
| 32,862 | 195 | 0.1274 | ×3.42 | 0.1153 | ×3.08 | 11.6207 | ×3.93 |
| 55,000 | 326 | 0.2462 | ×1.93 | 0.2976 | ×2.58 | 41.4462 | ×3.57 |
| 110,000 | 654 | 0.9730 | ×3.95 | 1.1296 | ×3.80 | 231.3367 | ×5.58 |
| **220,000** | **1,308** | **4.1648** | **×4.28** | **4.6053** | **×4.08** | **— (rc 124, >300 s)** | budget exceeded |

`net / bytes²` is **flat to within noise** — 8.6e-11 / 9.5e-11 / 1.1e-08 — i.e. a quadratic term dominates; exponent fit `t ∝ bytes^2.09`. A second independent run agreed on the ratios (55k→110k→220k: ×3.45 then ×4.32).

**Per-entry walker scaling series** (counters are the load-independent evidence): 6,256 → 225,280 B is a **6.86× size step but a 46.4× copy step** (linear would be 6.86×) ⇒ quadratic.

### 3.3 Mechanism (isolated microbenchmark, `LC_ALL=C`)

| operand | one `${v#*'{"ts":'}` marker **ABSENT** | marker at fixed offset 300 | one `${v%'{"ts":'*}` | one `${v:1}` |
|---|---|---|---|---|
| 27,500 | 0.388 s | 0.0055 s | 0.0131 s | 0.0040 s |
| 55,000 | 1.636 s | 0.0047 s | 0.0407 s | 0.0047 s |
| 110,000 | 6.978 s | 0.0064 s | 0.1830 s | 0.0082 s |
| 220,000 | **39.387 s** | 0.0090 s | 0.8961 s | 0.0106 s |

`${v#*PAT}` with the pattern absent scans O(n) suffixes, each costing O(n) to materialise ⇒ **O(n²) per single operation**. Traced reachability (`bash -x`, 6,256 B): **6,170** `+ c="${rest:0:1}"` executions against a 6,256-byte journal — one full pass over the array text per append.

**Attribution (decomposition, `LC_ALL=C`):**

| bytes | `_js_journal_read` | `_js_json_sub history` | `_js_journal_update` | `_js_history_append` |
|---|---|---|---|---|
| 6,256 | 0.0040 | 0.0035 | 0.3778 | 0.4353 |
| 16,431 | 0.0058 | 0.0055 | 2.6912 | 2.8531 |
| 32,862 | 0.0080 | 0.0076 | 13.6060 | 12.0607 |
| 55,000 | 0.0147 | 0.0112 | **62.9310** | 111.7399 |

**100 % of `_js_history_append`'s cost is `_js_journal_update`'s per-character value-consume loop** (762–780). The two rewrites contribute ~0.012–0.015 s at 55 KB.

**Locale is a 5× multiplier and must be pinned.** `_js_history_append` @32,862 B = 53.3 s unpinned / **10.1 s `LC_ALL=C`** / 75.0 s `LC_ALL=en_US.UTF-8` / 65.0 s `LANG=en_US.UTF-8`. `_js_history_has_defer_reason` @220 KB = 29.5 s unpinned vs **4.17 s** under `LC_ALL=C`. All figures above are `LC_ALL=C`; the daemon's own environment is not guaranteed C, so production cost is the UTF-8 figure or worse.

**Entry-count sensitivity at constant bytes (110,000 B):** 50 → 600 entries moves the reader from 0.7261 s to 1.1249 s (only ~1.5×) — cost tracks **bytes**, not entries. On this HEAD the readers' O(n²) is the per-entry `${rest#*…}` suffix-strip being O(n²) per call, **not** an entry × array-length product. The fix must therefore target the *suffix-strip*, not merely the loop trip count.

---

## 4. The deferred-path boot probe (item 7a) — the gate's decisive measurement

The brief's fixture prescription (`in_flight` null/absent) was **wrong**: `in_flight: null` makes `_journal_sweep` early-exit at `case ''|null) return 0` **before any history read**. The first 7a leg measured 0.09 s and correctly reported that the defer path was never entered (census: 0 hits) — that leg was re-dispatched rather than accepted. The reaching shape was then found by enumerating candidates with a traced entry census:

| shape | `in_flight` | census (`_js_serving_defer` / `_js_history_has_defer_reason` / any `_js_history*`) | reached |
|---|---|---|---|
| null | `null` | 0 / 0 / 0 | **N** — sweep early-exits |
| restart control | `kind=restart` | 0 / 0 / 0 | **N** — early exit |
| pre-flip control | `kind=promote`, no `flipped` | 0 / 0 / 0 | **N** — writer only, no scanners |
| **REACH** | `kind=promote`, `flipped:true`, stale 13,998,126 s, no owner → `owner_dead` | **2 / 2 / 10** | **Y** |

Entry #1 is **inside the boot sweep, BEFORE `starting:`**; entry #2 is inside the **T15 post-exit hook, AFTER**. Trace excerpt:

```
20246:+ [4s]   _js_serving_defer … v9.9.9-stub@2026-06-01T00:00:00Z 13999623 observation-not-qualifying …
20249:+ [4s]   _js_history_has_defer_reason … "observation-not…"     ← scan #1 (boot sweep, pre-banner)
247168:+[103s] _log 'starting: /private/tmp/g7-item7a/current/ensemble-prod'
265852:+[107s] _js_serving_defer … (T15 hook pass)                    ← scan #2 AFTER starting:
```

**Timings at the reaching shape** (pristine master per leg, `timeout 300` outer):

| size × locale | wall → `starting:` | total wall (incl. hook) | exit | journal sha | census |
|---|---|---|---|---|---|
| 55 KB × C | **63.433 s** | 68.223 s | 0 | **CHANGED (+419 B)** — append fired | 2/2/10 |
| 55 KB × UTF-8 | **TIMEOUT @ 300.016 s** (exit −9); orphan reached `starting:` at ~315 s | >300 s | −9 | unchanged at kill | — |
| 110 KB × C | 2.130 s | 4.044 s | 0 | unchanged | 2/2/0 |
| 110 KB × UTF-8 | 13.604 s | 28.467 s | 0 | unchanged | 2/2/0 |
| **220 KB × C** | **10.375 s** | 20.828 s | 0 | unchanged | 2/2/0 |
| **220 KB × UTF-8** | **62.331 s** | **125.653 s** | 0 | unchanged | 2/2/0 |

**Scaling (ratio-based):** scan-only path 110→220 KB = **×4.87 (C) / ×4.58 (UTF-8)** per doubling ⇒ **O(n²) confirmed in both locales**. Locale factor **~6.0–6.4×**.

**The two regimes matter and both are bad:**
- **write-once hit** (the journal already contains a matching deferred row): the scan costs 10.4 s (C) / 62.3 s (UTF-8) to `starting:` — seconds, but 125.7 s total in the daemon's UTF-8 environment, and quadratic in size.
- **append fires** (no matching pre-seeded row — the realistic case for a user whose journal grew): the per-character write dominates. At **55 KB** it is already **63.4 s (C) / >300 s (UTF-8)**; the function-level bench puts 220 KB at **~1,013 s ≈ 17 min** (C), and the UTF-8 figure is ~5× worse. The 7b/c leg independently hit the 300 s bound at 220 KB on the same path.

> **Against the brief's acceptance criterion** ("Seconds = PASS; minutes = FAIL"): the *write-once-hit* shape at 220 KB is seconds-scale to `starting:`; the *append-firing* shape at the same size is **minutes**. The user's incident (655 MB kill-trace from `_js_serving_defer`, i.e. the write path) is the second regime. **Verdict: FAIL**, with the named function and line: `_js_journal_update` `launcher.sh:762–780`, reached from `_js_history_append` (`:828`, `:2041`) ← `_js_serving_defer` (`:2028/2041`) ← `_js_sweep_flipped_disposition` (`:2458–2462`) ← `_journal_sweep` (`:2896`) ← `main` (`:3364`).

---

## 5. Item-by-item results

| # | Gate item | Result | Evidence |
|---|---|---|---|
| 1 | Launcher pack `tests/test_launcher.sh` | ✅ **PASS** | `launcher tests: 296 passed, 0 failed, 1 skipped` / `ALL PASS`; exit 0; **83 s**; the 1 skip is id **`8b-r2`** ("repo `_journal_sweep` body ≡ live-install copy — no live-install path configured (set `ENS_TEST_LIVE_LAUNCHER` to opt in)"), env-gated at `tests/test_launcher.sh:927`/`:951`; zero delta vs both the brief and the PACKS.md registration |
| 2 | Cap pack `tests/unit/tools/test_mcp_init_guard.py` | ✅ **PASS** | `27 passed in 0.74s`; import proof clean (`daemon/__init__.py` and `daemon/mcp/init_guard.py` both inside the worktree — no wrong-tree import); `27 tests collected` verified separately, so the 27 is a real collection |
| 3 | Concurrency pack `test/packs/concurrency_atomic_unit_test.sh` | ✅ **PASS-WITH-CLASSIFIED** | raw **5F / 94P / 74S** — identical in the registered form and the fully-scrubbed form; all 5 reds are `DetachedInstanceError` in `tests/test_instance_metadata_atomic.py`, matching **QUARANTINE.md row 134** (`test_instance_metadata_atomic.py` ×5). **0 branch-caused**: the failing test file is byte-identical base↔HEAD and absent from the diff; `daemon/repositories/instance/` is sha256-equal base↔HEAD; the branch's only daemon file (`daemon/mcp/init_guard.py`) is **not imported at all** by the failure path. Pack exits **1 bare** under `set -euo pipefail` (never prints `RESULT:`) — status read from the exit code |
| 4 | Journal splice `tests/test_journal_splice.sh` | ✅ **EXPECTED-FAIL-CLOSED** | `FATAL F1: resolved LIVE_PARENT escapes /tmp (/private/tmp/ens-upglib-home.0u2HPj/agents-ensemble) — refusing to run live-install cells` at **preflight stage 3**; exit 1 in 1 s (not 124). The abort precedes any mutation — `grep -cE '^\+ LIVE_PARENT=|^\+ LIVE_DIR='` → **0**, i.e. the variables `mkdir -p "$LIVE_DIR"` / `rm -rf "$LIVE_PARENT"` depend on were **never assigned**; the fake-HOME and both live-install-shaped children do not exist post-run. Pre-existing fence defect (macOS `pwd -P` canonicalizes `/tmp`→`/private/tmp`; maintenancer report `6ca4043d`): both `tests/upgrade_test_lib.sh` (`15bc1597…`) and `tests/test_journal_splice.sh` (`b9917b96…`) are **byte-identical at BASE and HEAD** |
| 5 | Syntax `bash -n launcher.sh` | ✅ **PASS** | exit **0**, empty output at HEAD; BASE control also exit 0 / empty |
| 6 | BASE dual-side legs (Tier-1 blast radius) | ✅ **PASS (no regressions)** — §6 | NET-NEW **∅** across three pairings; L3 cross-leg discrimination **holds** |
| 7a | Deferred-path boot @ 220 KB | ❌ **FAIL (blocking)** | §4 |
| 7b | Per-function 220 KB timings | ❌ **FAIL (blocking)** | §3.2 — all three functions O(n²), not O(n) |
| 7c | `_js_history_append` byte-parity OLD vs NEW | ⚠️ **PARTIAL** | Identical at ≤ 32,862 B (sha256 + `diff`); **220 KB not executed for either leg** (both `rc 124`) — see §2 |
| 7d | Boot-path walker audit | ❌ **FAIL (blocking)** | §3 — 3 quadratic walkers remain, all boot-path reachable; the size guard routes into them |
| 7e | 3 named acceptance cases | ✅ **PASS** | `case_0124_trunc_torn_flat_077` (77 B, `c3bbc42e…`), `case_0154_trunc_torn_hist_027` (27 B, `f91ba233…`), `case_0230_trunc_torn_hist_103` (103 B, `3bbf62de…`) — **read rc=1** (refused, never 0) and extraction exactly **`SUB_history\|1\|`** each; rc class 1 = absent-or-rejected; **no rc=2** fired. Fixtures regenerated from `gen_cases.py` and `cmp`-identical. OLD ≡ NEW on all three. **Corpus differential: 0 of 833 differ** (3332 records per launcher, identical sha256 `08b0253a…`, diff file 0 bytes) |

---

## 6. Item 6 detail — the regression gate and the discrimination proof

Recipe loaded before running (per ensure.md § Gate Tiers): `blast-radius-leg` v1.0.3 and `failed-digest-equality-gate` v1.0.2.

**D1 / D2 declaration.** The pack has **no** `LAUNCHER_SH` override — read from source (`tests/test_launcher.sh:19` `REPO_ROOT=…`, `:23` `LAUNCHER="$REPO_ROOT/launcher.sh"`) — so the scratch tree *is* the mechanism. D2 identity is byte-proven: BASE lines 1–1166 ≡ HEAD lines 1–1166, sha256 `aef5286d84adb7e7e610a4e9ca29373d003b1ed574207c44e9fa968bc3e11444` on both sides. **D1 = 80 HEAD-only §13 cases** (the brief named 50; the pack diff vs BASE is a single hunk `@@ -1167,12 +1167,438 @@`, so the *entire* §13 family 13a–13k is HEAD-only — declared as 80). The HEAD leg runs D1+D2 = **297**; the BASE leg runs D2 only = **217**; the command *shape* is identical — only the file list differs, by construction.

| Leg | Files | rc | Elapsed | Counts | Failed ids | Digest (form, sha256) |
|---|---|---|---|---|---|---|
| L1 BASE | BASE launcher + BASE pack | 1 | 85 s | 215P/1F/1S = 217 | `8b-c2` | BARE-ID `4df9d5c9…` |
| L1r2 / L1r3 adjudication | same | 0 | 83 s ×2 | 216P/0F/1S = 217 | — | `e3b0c442…` (empty) |
| **L2 HEAD** | HEAD launcher + HEAD pack | **0** | 80 s | **296P/0F/1S = 297** | — | **`e3b0c442…` (empty)** |
| **L3 CROSS** | **BASE launcher + HEAD pack** | **124** | **300 s** | killed mid-flight | `13f` | `9969d36b…` |

**NET-NEW = ∅ in all three pairings.** The one BASE-only red (`8b-c2 slow command: rc 124, SIGKILLed within deadline`) is **BASE-only-flaky** (1/3 over the recipe's 3× sequential adjudication; it asserts a 1-second wall-clock deadline observed at 2 s) — **not** a lane-introduced regression and **not** a lane fix. The adjudication legs ran *after* the gate legs in separate scratch trees so they could not perturb that timing-sensitive cell.

**L3 — the discrimination proof holds.** The cross leg (HEAD pack + BASE launcher) **timed out at §13f** (`250KB journal read completes inside the 2s watchdog`): the bounded cell FAILed (expected 0, actual 124) and the *unbounded* read then wedged on the OLD quadratic launcher; rc=124 at 300 s, killed inside the wrapper, **no orphan processes** (GNU `timeout --kill-after=10s` group-kill). §13a–§13e all **passed** against the OLD launcher (small fixtures behave identically), so **§13f's scale gate is the sole discriminator** — which is exactly what makes the HEAD pins discriminating.

**Slicing proof (mandatory) — two independent methods agreeing exactly:** the pack's own totals (BASE 217 / HEAD 297) and an independent `bash -x` emission trace (BASE 217 = 213+3+1, HEAD 297 = 294+2+1). **D1 80 == 80 exact** (§13a 2, 13b 2, 13c 5, 13d 4, 13e 8, 13f 5, 13g 4, 13h 4, 13i 4, 13j 22, 13k 20). D2 residual +3, fully localized to env-gated mutually-exclusive branches — not coverage gaps.

**Recipe finding worth banking:** a shell pack emits `FAIL: `/`ERROR: ` **with a colon** — the pytest digest strip `s/^(FAILED|ERROR) //` **silently no-ops** and yields a plausible-but-wrong digest. The correct form is `s/^(FAIL|ERROR): //`, cross-checked against the pack's own `failed:` block.

---

## 7. Independent verification — adjudication of the prior (terminated) tester's claims

The prior report against `361cba6b3` is treated as a **claim set**, not ground truth. Each limb was verified or refuted:

| Prior claim | Verdict |
|---|---|
| `_js_history_has_defer_reason` is still O(n²) | ✅ **CONFIRMED** — ×4.87 (C) / ×4.58 (UTF-8) per doubling, independently measured |
| It is reached twice per boot (boot sweep + T15 hook) | ✅ **CONFIRMED** — 2 entries per boot, bracketing `starting:` |
| 5.892 s to `starting:` @ LC_ALL=C at ~225 KB | ✅ **plausible** — mine 10.375 s @ 220 KB (1.76×; machine load + fixture deltas) |
| 46.656 s @ en_US.UTF-8 | ✅ **plausible** — mine 62.331 s @ 220 KB (1.34×) |
| locale sensitivity 7.9× | ✅ **CONFIRMED** — mine 6.0× |
| "344 MB of `rest=` trace payload" | ✅ **CONSISTENT** — my 220 KB traced run exceeded a 200 MB cap inside the same `rest=` expansions |
| `_js_journal_update` / `_js_history_has_defer_reason` are **byte-identical to BASE** | ⚠️ **REFUTED *literally*** — 14 and 13 lines differ (the rc-2 refuse headers). **The walking cores ARE byte-identical** (46-line and 41-line diffs empty) |
| both are on the boot path | ✅ **CONFIRMED** — `3364 → 2915 → 828 → 730` and `3364 → 2896 → 2458 → 2460 → 2028 → 1216` |
| `_journal_sweep`'s pre-flip branch has **no** size guard | ✅ **CONFIRMED** |
| the ≥96 KB guard routes the flipped path **into** the quadratic | ✅ **CONFIRMED** — the guard's only action is `_js_serving_defer`, whose first act is the O(n²) reader |
| the *scan* is the 220 KB blocker | ❌ **REFUTED** — with a write-once hit the scan costs 10.4 s (C); the **append** is the true 220 KB killer |

---

## 8. ensure.md validation

| Requirement | Tier | Status | Evidence |
|---|---|---|---|
| No regressions in changed packs — every pack in the change set PASS | Core / Critical | ✅ **PASS** | §5 items 1, 2, 6 — NET-NEW ∅, 296P/0F/1S, 27P/0F |
| Deadlock / concurrency integrity — `concurrency_atomic_unit_test` PASS | Core / Critical | ✅ **PASS-WITH-CLASSIFIED** | 5F ≡ QUARANTINE row 134, 0 branch-caused |
| No sync DB calls on the asyncio event loop | Core / Critical | ✅ **PASS** | covered by the same pack |
| `dev.sh` includes `--timeout-graceful-shutdown 10` | Core / Critical | ✅ **PASS** | `dev.sh:155` — the **live** uvicorn invocation carries `--timeout-graceful-shutdown 10` (fixed-string match on the value **10**; the doc comment at `:152` also matches). `scripts/` contains 0 files with the flag; `dev_with_mock.sh:133` carries it identically |
| All callers of converted async functions properly await | Core / Important | ✅ **PASS** | AST-classified (raw grep conflates definitions / docstrings / comments with real calls and would miscount): **9 call sites, 9 awaited, 0 not-awaited** — `_get_system_prompt_tokens` 3/3, `_compute_context_usage` 1/1, `get_queue_stats` 5/5. **Blast-radius OUT OF SCOPE** — the three symbols live in the daemon/compaction area and `git diff 818411107..a444c2b9` has **zero** hits for them; reported for ensure.md completeness only |
| No dead code from the fix | Core / Nice-to-have | ✅ **PASS** | Removal proven BASE-vs-HEAD (grepping HEAD alone cannot distinguish "removed" from "never existed"): `out="$out$char"` (BASE :355/:364) and the `for ((i=0;i<${#json};i++)); do c="${json:i:1}"` char loop (BASE :382) are **gone** at HEAD — only a comment at `:487/:488` and one awk program remain. Caller counts: `_js_journal_read` **13** production call sites, `_js_json_sub` **12** ⇒ neither is dead code |

**Release Gate: not run.** Per ensure.md § Gate Tiers the blast radius is not big/critical/architecture, so the slow full-suite and live-daemon E2E items do not apply. **No ensure.md contradiction was encountered** — every validation was pack-mapped or a static check, scoped to the change set, with dual-layer timeouts; no bare `pytest`, no `-x`, no full-suite run. **The `ensure.md` file itself was not modified** (user-owned, read-only).

**PACKS.md registration integrity** (registry check, read-only): `tests/test_launcher.sh` → registered as `test_launcher` (2 rows, PACKS.md :4465 and :4517); `tests/unit/tools/test_mcp_init_guard.py` → registered as `test_mcp_init_guard` (:4609, added by this branch); `test/packs/concurrency_atomic_unit_test.sh` → registered (29 rows, canonical :1699). All three scripts exist on disk, and **no registry row points at a missing path**. ⚠️ `tests/test_journal_splice.sh` — the pack the gate ran for item 4 — is **NOT registered** in PACKS.md: `grep -F` → 0 hits and `git log -S 'test_journal_splice' -- .agents/tester/PACKS.md` → **empty**, i.e. **never registered**; it is invoked only by the aggregator `tests/test_upgrade_overhaul_all.sh:152-153`. **Pre-existing condition; PACKS.md was NOT edited** (read-only fence).

**No ensure.md contradiction encountered**: every validation was pack-mapped or a static check, scoped to the change set, with dual-layer timeouts; no bare `pytest`, no `-x`, no full-suite run. **The `ensure.md` file itself was not modified** (user-owned, read-only).

---

## 9. Disclosures and caveats

1. 🔴 **Operator action — credential exposure.** The item-3 worker echoed the **values** of `POSTGRES_PASSWORD` (ensemble_prod) and `PLANE_API_KEY` into the transcript during an ambient-env census that only needed *presence*. Self-disclosed; anomaly `ea971ba6-3d4a-43d7-9dc2-52f36f8b4140`; **no credential was used** (the pack is filesystem-only — no DB connection, no Plane call). Both are now transcript-recoverable, and the Plane key is an **independent second exposure** of the class already tracked at commit `560c2e90`. **Rotation is an operator action outside the tester's mandate** — it was not attempted.
2. 🟡 **Live-daemon contact (one run).** The first item-7a census run resolved `PORT=9797` from the inherited ambient env and made two **read-only** `GET /livez` requests to the live prod daemon (200, v0.29.8). No state change, no signal. Remediated immediately (`PORT=19999` pinned; all later runs show zero hits on 9797). Anomaly `7918790f-ba9c-42bb-b02c-1326c0db3afa`. **Lesson: a sandbox probe must pin its own PORT, never inherit it.**
3. 🟡 **Item 8f is vacuous on BSD hosts (test-only, pre-existing).** `tests/test_launcher.sh:462` `_ts_off` builds `date -u -v"${off}"S`; BSD date rejects an **unsigned positive** offset (`-v300S` invalid, `-v+300S` valid), so its only positive-offset caller (`:555`) seeds the 8f fixture's `cooldown_until` as an **empty string** instead of a future timestamp (GNU hosts take the `-d "@$((now+off))"` branch and are unaffected — the same bug class was already fixed for GNU at `:511`). Impact is contained (`launcher.sh:260-261` states cap/cooldown are entry-side only and the sweep never *reads* `cooldown_until`), so 8f's three assertions still hold — but 8f's stated precondition "cooldown_until in the FUTURE" is **not established on macOS**, so 8f degenerates toward plain 8a. Anomaly `d4a3134c-1b9c-4175-9d5d-1e5a8677da0e`. One-line sign normalisation fixes it when the tree is unlocked.
4. 🟡 **Item 8b-c2 is host-timing-flaky (BASE-only, 1/3).** It asserts a 1-second wall-clock deadline observed at 2 s. Classified BASE-only-flaky; **not** a lane regression. **Recommended follow-up: add a QUARANTINE row** — deliberately **not** done in this gate, because the brief restricts the commit to this one path and editing the ledger would leave the gated tree dirty. Recorded as POK `gate-02-launcher-8b-c2-flaky`.
5. ⚠️ **220 KB byte-parity not executed** for either implementation (§2). Not implied, not extrapolated as a pass.
6. ⚠️ **Absolute seconds vary with host load** (load 4→70 during the session); the **ratios** are the load-robust evidence and are stable at ×3.8–4.3 for the readers. The two independent per-entry measurements of the same function at 225 KB differ (4.17 s LC_ALL=C net vs 28.492 s least-contended full-function) because of locale, harness baseline and contention — the quadratic conclusion is identical in both.
7. ⚠️ **220 KB OLD-at-scale was executed only once, under a 300 s bound** (rc 124, file unchanged). It was never run unbounded. The OLD reference points in the brief (6,256 B → 3.140 s; 32,862 B → 57.143 s) are for the *per-char validator* under a different locale; under `LC_ALL=C` this gate measured 3.262 s / 83.576 s. **Locale is the dominant confound and was pinned.**
8. **Harness defects disclosed by workers:** (a) `timeout`-SIGKILL cannot forward signals, so two killed legs' bash children orphaned and ran to completion — all writes stayed inside `/tmp/`, no orphans remain; (b) early census attempts raced on a shared journal path (detected by sha-chain mismatch, discarded and rerun sequentially); (c) the item-6 digest strip is pytest-shaped and silently no-ops on shell packs (see §6).
9. **Two toolchain anomalies filed:** `92b26e88-df44-4c92-ab5d-996574eaedd6` — instance metadata `last_activity_at` is not refreshed during a long tool call, which makes the hang detector fire falsely and would have caused a healthy worker to be terminated.

---

## 10. Action needed

- [ ] **Fix the per-character writer** `_js_journal_update` (`launcher.sh:730–792`, hot loop `762–780`) — the only remaining per-character walker; it is 100 % of `_js_history_append`'s cost and is >300 s at 220 KB.
- [ ] **Fix the two per-entry readers** `_js_history_has_defer_reason` (1216–1270) and `_js_history_has_event_for_target` (1144–1198). The mechanism is the per-entry `${rest#*'{"ts":'}` **suffix-strip being O(n²) per call** (not the trip count) — cost tracks bytes, not entries, so a rewrite that only reduces iterations will not help. An awk pass over the array text (as the branch already did for `_js_journal_read` / `_js_json_sub`) is the indicated shape.
- [ ] **Make the size guard a bound, not a router.** Today `>= 98304 B` routes into `_js_serving_defer` → the quadratic readers and writer. It must either skip the history scan on the guard arm, or the guard must be checked *before* the scan rather than *into* it. `_journal_sweep`'s pre-flip branch (2714–2926) additionally has **no** guard at all.
- [ ] **Re-gate item 7a/7b/7c after the fix**, with the reaching fixture (stale `flipped:true` promote txn) and the append-firing shape, at ≥3 sizes × both locales, with the per-doubling ratio as the acceptance criterion.
- [ ] **Consider pinning `LC_ALL=C` for the launcher's history operations** — locale is a 5–6.4× multiplier on these paths.
- [ ] **Rotate the exposed credentials** (§9.1) — operator action.
- [ ] **Fix `_ts_off`'s BSD sign handling** (§9.3) so item 8f is not vacuous on macOS — one line, test-only.
- [ ] **Add a QUARANTINE row for `8b-c2`** (§9.4) — ledger-owner action.
- [ ] **Register `tests/test_journal_splice.sh` in PACKS.md** (§8) — the pack the gate ran for item 4 has never had a registry row (pre-existing); unregistered packs are invisible to future gates.

**Merge posture:** the branch is **regression-clean and a genuine improvement** on the read/extract path, and the byte-parity evidence shows it does not change the journal format. It may be merged on those grounds. It must **not** be released on the claim that it fixes the boot wedge — on the defer and write paths it does not.

---

## 11. Evidence index

| Artefact | Location |
|---|---|
| Launcher pack log | `/tmp/g7-item1/pack.log` (1,930 B) |
| Cap pack log + import proof | `/tmp/g7-item2/pack_run.log`, `/tmp/g7-item2/import_proof.log` |
| Concurrency pack + base-file leg | `/tmp/g7-item3/` |
| Journal-splice trace | `/tmp/g7-item4/` |
| BASE dual-side legs, digests, traces | `/tmp/g7-item6/`, `/tmp/g7-probe/` |
| Deferred-path fixtures, traces, timings | `/tmp/g7-item7a/` (`gen_reach.py`, `boot_run.py`, `runs/*.result.json`) |
| Per-function timings + parity | `/tmp/g7-item7bc/` (165 artefacts, 45 MB) |
| Walker audit harness | `/tmp/g7-item7d/` (1.8 MB) |
| Acceptance corpus + differential | `/tmp/g7-item7e/out/` (`oldnew_verdicts.diff` = 0 bytes) |
| Prior-lane corpora reused | `/tmp/ljf-perf/out/`, `/tmp/ljf-boot/`, `/tmp/ljf-boot-ab/`, `/tmp/sweepdiff/`, `/tmp/diffproof/` |

**Gated-tree integrity.** `git status --porcelain` = exactly one line (` M .agents/tidier/notes.md` — a **pre-existing foreign** tidier-lane edit, untouched by every worker and by the tester) before and after every leg; `git diff --cached` empty; no untracked files; `launcher.sh` sha256 `008f88909bd401f0541323f3e4eb3e08c92c70144077d936a445d026a438254e` unchanged throughout; no worktree added; no push, merge or tag; **no live install path written or executed against**; no daemon signalled; port 8088 untouched.

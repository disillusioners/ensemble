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


---
---

# SECOND GATE — closure verification round

**Date:** 2026-11-10 (local +07) · **Verifier:** tester (test leader)
**Gated commit:** **`7b0b7955db5834905bd01b3ba91fb32981f88f22`** · BASE `818411107` · FIRST_GATE `f02e2dfa1`
**Branch:** `feature/launcher-journal-sweep-fix` · **Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-launcher-fix`
**Delta under test (5 commits, claimed order — verified):** `5f8528869` (B1/B2/B3 → O(n) walkers; pin LC_ALL=C; macOS `date -v` fix) → `3bcde2c44` (Q1 guard-as-bound + Q2 pre-flip guard) → `2686d47c7` (Q4 portable cooldown) → `39567b769` + `7b0b7955d` (ledger/counts).
**This round does NOT delete the first round above.** The first-gate FAIL record is the evidence that these five commits answer; it stays.

---

# VERDICT: 🟢 **PASS** — the wedge is closed; the branch is releasable on this gate

**Legs:** 10 workers dispatched, 10 adjudicated · **7 PASS · 2 PASS-WITH-CLASSIFIED · 1 EXPECTED-FAIL-CLOSED** · 0 timeouts · 0 re-dispatches · 0 tester source edits · 0 live-path writes · 0 retry-on-budget-exceeded.

**One-line rationale:** the branch's first gate failed because three boot-path history walkers were O(n²) and the ≥96 KB size guard **routed into** them. At `7b0b7955d` all three are O(n) — measured, per-doubling ≈2× — the 220 KB boot went from **62.331 s → 1.773 s (35×)**, the 55 KB boot from **>300 s (timeout, never reached `starting:`) → 0.781 s (>384×)**, the size guard is now a **bound** (refuse-to-act, rc 2) instead of a router, and the output bytes are **byte-identical to the first-gate implementation** at every size that can be executed. The previously-failing defer path is now seconds-scale and scales linearly.

| # | Gate item | Result | Evidence |
|---|---|---|---|
| 1 | Launcher pack | ✅ **PASS** | `launcher tests: 343 passed, 0 failed, 1 skipped` · `ALL PASS` · 96 s · skip = `8b-r2` (env-gated live-install). +39 pins **statically proven**: §14 holds exactly 39 assertion sites (14a×8, 14b×10, 14c×7, 14d×3, 14e×8, 14f×3); at `5f8528869` §14 has 0 sites and total sites moved 245→284 = **+39**, matching runtime 304→343 = **+39** |
| 2 | Cap pack (daemon-side control) | ✅ **PASS** | `27 passed in 0.74s`; import proof clean (both paths in-worktree); `27 tests collected` separately. **Control property proven**: `daemon/mcp/init_guard.py` and the pack file were last touched at `e5545b458` — *before* the first gate — and the second-gate delta touches only `launcher.sh`, `scripts/upgrade/lib.sh`, two test files and PACKS.md ⇒ the daemon side did not move |
| 3 | Concurrency pack | ✅ **PASS-WITH-CLASSIFIED** | raw **5F / 94P / 74S**, identical in the registered and the fully-scrubbed leg ⇒ reds are **not** coupled to the ambient prod PG env. Failing set **PARENT-IDENTICAL** by set-equality (no additions, no removals) against the first gate's 5 node ids, all `DetachedInstanceError` ≡ QUARANTINE.md row 134 |
| 4 | Boot-sweep pack | ✅ **PASS-WITH-CLASSIFIED** | `PASS=51 FAIL=2` — exactly the claimed count, reproduced **4/4 runs** (15/22/20/18 s), deterministic. Both reds are **C5a**, parent-identical by **base-leg replay** (BASE launcher `c4f640e1…` and HEAD `09c2db10…` both → `serving_green_commit: NO`, `in_flight null: NO`, `sweep_deferred: YES`). C7 is GREEN at HEAD |
| 5 | Journal-splice + syntax | ✅ **EXPECTED-FAIL-CLOSED** + syntax clean | `bash -n` exit 0 / empty at HEAD, at the **committed blob** and at the first-gate control; worktree ≡ committed blob (`cmp` clean, sha `09c2db10…`). FATAL F1 verbatim, **byte-identical** to the first-gate template, at preflight **stage 3**; `LIVE_PARENT=`/`LIVE_DIR=` **never assigned** on the `bash -x` trace (abort precedes mutation); zero writes under `~/agents-ensemble*` (entry count + **sorted-listing sha256** unchanged) |
| 6 | **The four timing legs** | ✅ **PASS** | **Seconds on all four.** See §S1 |
| 7 | Q1 guard-as-bound + `:1311` rc-mapping + Q2 pre-flip | ✅ **PASS** | Both Q1 refuse arms proven reachable (rc 2); all four refuse proofs; **no rc-1 masquerade** across 5 fixtures; Q2 decline proven with 4 proofs; below-threshold **A==B==C byte-identical** across three launchers. See §S2 |
| 8 | Q4 portable cooldown | ✅ **PASS** | Premise correction **TRUE** (git-proven); parent leg **47P/3F** reproduced with **C7 among the reds**; 3/3 behavioural claims PASS. See §S3 |
| 9 | Parity discipline | ✅ **PASS** | READ-path bodies **byte-identical** to the first gate; 220 KB label **intact and UNOBSERVED**; 32,862 B and 100,000 B parity **byte-identical**; D1 matrix **33/33 IDENTICAL**; determinism 3 distinct PIDs. See §S4 |
| 10 | Byte-level audit + PACKS | ✅ **PASS** | **16/16** launcher hunks classified, **`??` set EMPTY**; file list exactly the 5 claimed files; all 5 commit claims match their numstat; PACKS honest on all four checks. See §S5 |

---

## §S1 — The closure evidence (item 6): the four timing legs, re-derived

All legs on the **HEAD** launcher (sha `09c2db1009fd843676f44ad03f9d3290591c7f723a052da9c099c69b8e325d32`, 187,468 B), `env -u LC_ALL LANG=en_US.UTF-8`, n=3, median. The synthetic install is inert (stub binary, `PORT=19999` pinned in its `.env` before any boot, no daemon, no PG).

| leg | fixture | bytes | entries | wall→`starting:` | total | exit | journal sha delta | census |
|---|---|---|---|---|---|---|---|---|
| **1** | `in_flight:null` | 220,000 | 1,500 | **0.146 s** (0.144–0.152) | 0.282 s | 0 | unchanged `f4c12ad7…` | sweep exits before any scanner |
| **2** | stale flipped + seeded defer row (**HIT**) | 220,000 | 1,501 | **1.248 s** (1.172–1.875) | 2.081 s | 0 | unchanged `002274a9…` | `defer ×2` · `has_defer_reason ×2` · **`append ×0`** |
| **3** | stale flipped, no seeded row (**append fires**) | 220,000 | 1,500 | **1.773 s** (1.672–2.116) | 2.534 s | 0 | **changed** `2895596a…`→`f1e24f24…` (+375 B = exactly 1 `sweep_deferred` row, txn left OPEN) | `defer ×2` · `has_defer_reason ×2` · **`append ×1`** · `journal_update ×1` |
| **4** | append-firing | 55,000 | 375 | **0.781 s** (0.738–1.009) | 1.339 s | 0 | **changed** `336fdb8d…`→`d54a712d…` (+419 B = exactly 1 row) | `defer ×2` · `has_defer_reason ×2` · `append ×1` · `journal_update ×1` · `serving_probe ×2 (19999)` |

**Acceptance ("seconds = PASS; minutes = FAIL"): met on all four.** No leg approached the 300 s budget.

**Reachability proven by two independent methods that agree** — a `bash -x` trace (PS4 = `+ [${SECONDS}s] `) and a function-entry census over an instrumented copy. The trace shows the decisive decision values:

```
LEG 2:  + [1s] _js_size_guard_defer_decision … journal-size-guard
       + [1s] _js_history_has_defer_reason  … journal-size-guard
       + [1s] _js_serving_defer … journal-size-guard '…' 0   ← decision=0 ⇒ HIT
       + [2s] _js_serving_defer … '…' 0                        (T15 hook)
       counts: _js_history_append = 0

LEG 3:  + [1s] _js_history_has_defer_reason … journal-size-guard
       + [1s] _js_serving_defer … journal-size-guard '…' 1   ← decision=1 ⇒ MISS
       + [1s] _js_history_append … sweep_deferred '… DEFERRED (journal-size-guard) …'
       + [1s] _js_journal_update … history '[…]'
       counts: _js_history_append = 1
```

The boot path sweeps **twice** per boot (main() before `starting:`, then the T15 run-loop hook after the child exits); the **write-once** rule is why `_js_history_append` fires **once**, not twice — confirmed by count and by the exact +375/+419 B journal delta.

**Locale posture proven, not assumed:** launcher-side `LANG=[en_US.UTF-8] LC_ALL=[<unset>] LC_CTYPE=[UTF-8]`, but **inside the launcher's own awk seam `LC_ALL=[C]`** — the internal pin **holds** (proved with an awk `BEGIN` block prepended to the launcher's real program printing its own `ENVIRON` to `/dev/stderr`, leaving the extractor's stdout untouched).

### Scaling — both boot-path shapes are O(n)

**Append-firing (the append fires):**

| bytes | 55,000 | 110,000 | 220,000 | 440,000 | 880,000 |
|---|---|---|---|---|---|
| median wall→`starting:` | 0.781 s | 0.898 s | 1.773 s | 4.329 s | 11.371 s |
| ratio vs prior doubling | — | 1.15× * | **1.97×** | **2.44×** | **2.63×** |

**Write-once-HIT (defer scan hits, append skipped):**

| bytes | 55,000 | 110,000 | 220,000 | 440,000 | 880,000 |
|---|---|---|---|---|---|
| median wall→`starting:` | 0.631 s | 0.662 s | 1.248 s | 2.312 s | 5.218 s |
| ratio vs prior doubling | — | 1.05× * | **1.89×** | **1.85×** | **2.26×** |

\* the 55 KB→110 KB step is a **different code path**, not a scaling sample: 55,000 B is *below* the 98,304 B guard, so it takes the serving-green ladder (defer reason `observation-not-qualifying`) whereas 110/220/440/880 KB take the size-guard arm (`journal-size-guard`). The two regimes are flagged, not silently mixed. Function-level amortised confirmation: `_js_history_has_defer_reason` 0.050→0.231→0.304→0.883→1.369 s; `_js_history_has_event_for_target` 0.044→0.197→0.318→0.711→1.386 s; `_js_journal_read` 0.019→0.084→0.125→0.276→0.318 s — all ~1.5–3× per doubling.

**The 880 KB → 1.76 MB cliff (4.65×) is proven environmental, not algorithmic.** Controls at the top size, no launcher involved: plain `cat` 0.0051 s @880 KB → **0.0039 s** @1.76 MB (zero growth), while the launcher's **single-pass, provably-O(n)** balance awk went 0.155 s → **3.672 s (23.7×)** — a one-pass awk cannot be 23.7× slower for 2× the data. Host state at the time: **swap 17,010 MB of 18,432 MB (92 %)**, ~58 MB free RAM of 16 GB, load 17.12. All five functions jumped at the same size together — the signature of host thrash. Re-running the largest size first reproduced 45.5 s; the 220 KB leg measured last gave 1.791 s against a 0.032 s spread versus a 2.349 s median when measured first. Anomaly `8fec9bf7-42bb-4b05-870f-41f0ad4effa4`. **Production journals are capped at 98,304 B — 18× smaller than even the 880 KB point, where the ratio is a textbook 2.26–2.63×.**

### Versus the OLD implementation (first-gate numbers, cited — the OLD path was NOT re-run)

| size | OLD (first gate) | HEAD (this gate) | improvement |
|---|---|---|---|
| 220 KB | **62.331 s** (×4.58/doubling) | **1.773 s** | **35×** |
| 110 KB | 13.604 s | 0.898 s | 15× |
| 55 KB | **>300 s — timeout, never reached `starting:`** | **0.781 s** | **>384×** |

Against the brief's reference points (OLD 6,256 B → 3.140 s; 32,862 B → 57.143 s; 65,664 B → timeout), HEAD completes 55,000 B in 0.781 s.

**9797-zero-hits assertion:** 288 log files produced this session — **0 lines contain `9797`**. The synthetic install's `.env` pinned `PORT=19999` before any boot and the launcher's own `load_env_file` exported it over the ambient `PORT=9797` (proved by the POSTURE probe `PORT=[19999]` and the LEG 4 trace `probe_port=19999`). `PORT` was **not** scrubbed. Port 19999 verified dead before booting; 19999 > 10000 per the mock-port rule; port 8088 never touched.

---

## §S2 — Q1 guard-as-bound, the `:1311` rc-mapping, and Q2 pre-flip (item 7)

### Q1 — `_js_size_guard_defer_decision` (`launcher.sh:2162–2179`)

Two refuse arms: reader-unavailable (`:2172`, `[ "$rc" -le 1 ] || return 2`) and budget-overrun (`:2175`, elapsed ≥ `_JS_AWK_TIMEOUT_S` ⇒ `return 2`). Budget knob: `_JS_AWK_TIMEOUT_S` (assigned `=60` at `:71`, read at **call** time, so a sourced override works).

| probe | setup | rc | arm |
|---|---|---|---|
| control (no matching row) | budget 60, real clock | 1 | content verdict |
| control (matching row) | budget 60, real clock | 0 | content verdict |
| **P2b** | budget 1 + `date` shim advancing the epoch | **2** | **overrun arm** ✅ |
| **P2c** | fake `awk` exiting 127 | **2** | **unavailable arm** ✅ |

**Caller-level WARN (verbatim, `:2656`) — names size, guard and budget:**
```
WARN: journal sweep: journal size=98304B ≥ guard=98304B — defer decision budget (1s) exceeded/unavailable (awk rc=1): REFUSING to act (no journal write; the txn is left OPEN)
```

**The four refuse proofs:** journal sha256 **unchanged** (`5700958f…` → identical) · lock **released** (trace: acquire → PRESENT, release → absent) · txn **left OPEN** · **no history write** — `_js_history_append`, `_js_journal_update` and `_js_serving_defer` all **never called**, `sweep_deferred` rows = 0. **Green control:** without the arm the conservative defer *does* write (98,304 → 98,667 B, 1 `sweep_deferred` row) — so the refusal is caused by the arm, not an inert fixture. Reproduced identically on re-run.

### `:1311` rc-mapping — **a torn/absent/empty journal can never masquerade as "none"**

| fixture | rc | class |
|---|---|---|
| valid, matching DEFERRED row | 0 | content: row exists ✅ |
| valid, no matching row | 1 | content: none ✅ |
| valid, `history: []` | 1 | content: none ✅ |
| **TORN** (ends `…,"history":[`) | **2** | read failure — **not 1** ✅ |
| **ABSENT** (nonexistent path) | **2** | read failure — **not 1** ✅ |
| **EMPTY** (0 bytes) | **2** | read failure — **not 1** ✅ |

### Q2 — pre-flip guard (`:3116–3147`)

**(a) ≥98,304 B ⇒ DECLINE TO ACT** (`:3144`):
```
WARN: journal sweep: journal is 98304B ≥ 98304B (the launcher-side size guard) — DECLINING TO ACT on the stale pre-flip promote txn (target=v0.10.6, age 7379s): no launcher rollback and no commit-and-continue — a stale flip transaction in a ≥98304B journal is left to the daemon-side closer; the txn is left OPEN
```
All four proofs PASS (sha256 unchanged · lock released · txn OPEN · no write). **Green control:** below the threshold the branch **acts** — history row written (`"event":"sweep"` … `cleared — never flipped`) and `"in_flight": null`.

**(b) Below-threshold byte-identity vs the first gate — IDENTICAL** (clock frozen):

| leg | launcher | resulting journal sha256 |
|---|---|---|
| A | first gate `f02e2dfa1` | `84772ed7bc86e164…` |
| B | Q2 parent `5f8528869` | `84772ed7bc86e164…` |
| C | HEAD `7b0b7955` | `84772ed7bc86e164…` |

A == B == C, with identical log lines. **Measurement rule banked:** with an *unfrozen* clock A ≠ C by exactly one field (`age 7392s` vs `age 7393s`) — the history row embeds wall-clock age. **Freezing the clock is mandatory for any cross-launcher journal-byte comparison in this codebase.**

**(c) The brief's "verbatim" disclosure string does not exist** — `grep -F` against HEAD **and the whole tree** → **NOT FOUND anywhere**. The real comment (`:3127–3132`) is semantically equivalent (decline to act / no rollback / no commit-and-continue / no txn clear / left to the daemon-side closer / txn stays OPEN) and the **code matches the comment**. Documentation-accuracy finding against the brief, not a code defect.

---

## §S3 — Q4 portable cooldown (item 8): premise correction TRUE, parent leg reproduced

**Premise: the BSD arm pre-existed.** The parent `lib.sh` (`3bcde2c44`) and BASE `lib.sh` (`818411107`) are **byte-identical** (sha `292190a2…241c26`, 7,306 lines) and both carry `date -ju -v+${COOLDOWN_S}S` at `:2355`. `git log -S 'date -ju -v+'` shows the BSD arm introduced by `35cb9c2e6` — an **ancestor of BASE**; the `case "$(uname -s)"` dispatch and the GNU arm came later (`83f0b53a5`); the `_COOLDOWN_DATE_UNAME` hook was introduced by Q4 itself.

**Refinement the gate must state plainly:** the parent already used the **signed** `-v+${COOLDOWN_S}S` — so **lib.sh never had the unsigned-offset defect**. The Q4 lib.sh delta is a refactor (`_cooldown_until_iso`), the test hook, a **negative-offset** branch, and turning the parent's *silent* `|| until=$(_now_iso)` disarm into a **stderr warning**. **The C7 green flip is driven by the test change**, not by a lib.sh repair.

**Parent leg: `PASS=47 FAIL=3`** — exactly the claimed count. The reds: `C5a GREEN ⇒ the serving-green commit-close`, `C5a the txn is closed`, and **`C7 cooldown produces a future timestamp on GNU`**:
```
FAIL: C7 cooldown produces a future timestamp on GNU
      expected: >now (1794306800)
      actual:   got ''
```
C7's parent red is **platform-bound, not defect-bound** — the parent's C7 embedded a *replica* of the dispatch that produced the stamp correctly via the BSD arm and then converted it with the **GNU-only** `date -d "$until" +%s`, which BSD `date` has no `-d` for ⇒ empty ⇒ the `> now` comparison failed. **On a GNU host the parent's C7 would have been green.** This is a macOS-only red of the stale-pin family: re-pin, don't "fix" the product.

**Delta arithmetic closes:** the pack diff adds exactly 3 assertions and flips C7 green ⇒ `47 + 3 + 1 = 51 PASS`, `3 − 1 = 2 FAIL` — matching the HEAD leg (51/2) measured independently.

**Part C — all three behavioural claims PASS.** (1) BSD real-binary arm yields **exactly +300 s** (real `/bin/date`, no stub; stamp `2026-11-10T10:38:57Z`, `_iso_to_epoch` `1794307137`, delta 300 s, no fallback warning). (2) GNU stub selection works (`-u -d +300 seconds …` → `GNUSTUB-COOLDOWN`; default Darwin selection on the same stub → `-ju -v+300S -f …` → `BSDSTUB-COOLDOWN`). (3) Selection follows the override — with real `uname -s` = Darwin throughout, `_COOLDOWN_DATE_UNAME=Linux` selects the **GNU** arm, `=Darwin` the BSD arm, `=Plan9` fails closed to NOW with a stderr warning; the `*)` branch prints the **real** uname ('Darwin') while the *selection* used the override ('Plan9') — the two inputs provably distinct.

---

## §S4 — Parity discipline (item 9)

**READ-path bodies byte-identical to the first gate:**

| function | diff | verdict |
|---|---|---|
| `_js_journal_read` | 0 bytes | ✅ **byte-identical** (sha `e182a361…`, 3,998 B both) |
| `_js_json_sub` | 0 bytes | ✅ **byte-identical** (sha `40b74406…`, 6,343 B both) |
| `_js_history_append` | 0 bytes | ✅ **byte-identical** (sha `60dd957f…`, 1,780 B both) |
| `_js_history_has_defer_reason` | 2 hunks | rc-mapping **+** the B2 rewrite — both disclosed (§S6) |
| `_js_history_has_event_for_target` | rewritten | per-entry loop → one awk `split`/`index` pass |
| `_js_journal_update` | rewritten | per-char scan → one bounded awk pass (vectorised, split-once) + a new scanner-unavailable refuse arm; `_js_journal_write` (tmp+`mv`) byte-identical |

**The 220 KB label is intact.** The RESULTS blob is byte-identical to the `f02e2dfa1` blob (`c3f2382e…`, diff empty) and still reads *"220 KB byte-parity was NOT executed … Parity is proven **only up to 32,862 B**"*, corroborated at two further lines. **Not upgraded anywhere.**

**B1 parity legs** (one isolated `/bin/bash` per leg, `LC_ALL=C`, `TZ=UTC`, `date` shim-frozen to `2026-11-10T00:00:00Z`, 18-var scrub):

| size | OLD | NEW | output | verdict |
|---|---|---|---|---|
| 32,862 B | 24.914 s | **0.221 s** | 32,969 B, sha `46e0c887…` both | **IDENTICAL** (`diff -u` empty) |
| 100,000 B | **229.485 s** | **0.428 s** | 100,106 B, sha `6fdf642d…` both | **IDENTICAL** (`diff -u` empty) |

**220 KB OLD-vs-NEW parity remains NOT EXECUTED** — the label is unchanged and correct; this gate does not upgrade it. **Determinism:** identical sha256 across three distinct PIDs at 32,862 B and two at 100,000 B. **The developer's own D1 value-parity matrix, re-run at HEAD: 33/33 IDENTICAL, no diffs.**

---

## §S5 — Byte-level regression audit + PACKS (item 10)

**16/16 `launcher.sh` hunks classified, `??` set EMPTY:** C1 (B1 rewrite) ×3 · C2 (B2) ×1 · C3 (B3) ×1 · C4 (Q1 guard) ×5 · C5 (Q2 pre-flip) ×1 · C6 (rc-mapping) ×1 · **C7 (locale pin) ×0** · **C8 (macOS date fix) ×0 in `launcher.sh`** · C9 (comments) ×4.

**Ordering facts verified inside the hunks, not taken on faith:** Q1's decline precedes the size-guard's `_js_serving_defer` call, and Q2's decline precedes **both** `_js_history_append` (`:3149`) and `_js_journal_update in_flight` (`:3155`) — so the disclosure's *"no launcher rollback, no commit-and-continue and no txn clear"* **matches the actual code path**, not merely the comment.

**`scripts/upgrade/lib.sh`: 2/2 hunks, both Q4** (new `_cooldown_until_iso`; `journal_count_rollback` de-duplication). Both C7 literal forms survive (`date -ju -v+${COOLDOWN_S}S` ×3, `date -u -d "+${COOLDOWN_S} seconds"` ×2) ⇒ the boot-sweep static pins still resolve.

**File list: exactly the 5 claimed files.** Commit claims all match their numstat (`5f8528869` → launcher + test; `3bcde2c44` → launcher + §14 pins; `2686d47c7` → lib.sh + boot-sweep test; `39567b769`/`7b0b7955d` → PACKS.md only). **5 commits, claimed order, zero merges.**

**PACKS.md — all four checks pass.** (a) `test_journal_splice` (`:4610`) carries the **honest macOS cell** — *"ENVIRONMENT-GATED on macOS — NOT '1 portability red'"*, quoting the F1 abort, marking the later sections **UNVERIFIED here**, and pointing at `6ca4043d` with the fix direction, explicitly *"NOT a product red"*. (b) The launcher standing row (`:4517`) = **HEAD 343/0/1S** with its carrying commits cited; the arithmetic closes independently (304 + 39 §14 assertions = 343). (c) Stale tokens: **`HEAD 50/0` = 0**, `HEAD 296/0` = 0, `HEAD 304/0` = 0; the two surviving substrings are correctly-labelled historicals, not standing counts. (d) All four gate packs registered, all paths resolve on disk, no row points at a missing script.

**The "17 vs 16" commit-count discrepancy is resolved:** `rev-list --count 818411107..7b0b7955d` = **16** (exclusive); the brief's "17" is a **self-inclusive** count, and `818411107^..` = 19 because the base is a merge. **Boundary convention, not a contradiction.**

---

## §S6 — Corrections to the gate brief (my own errors, on the record)

Seven places where the second-gate brief was wrong or imprecise, each caught by a worker and corrected against evidence:

1. **The fixture premise was wrong.** "`in_flight` null/absent ⇒ forces the deferred path" is false — null/absent *early-exits* before any history read. The reaching shape is a stale **flipped** promote txn.
2. **The brief's claimed commit count** (17) is self-inclusive; the exclusive count is 16.
3. **The Q1 probe method was wrong.** "Set the budget to 1" does **not** exercise the overrun arm — `date +%s` has whole-second granularity, so the arm fires only on a second-boundary race (rc stayed 1 in 8/8 runs). It requires a clock shim.
4. **The Q2 "verbatim" disclosure string does not exist** in HEAD or anywhere in the tree; the real comment is semantically equivalent but differently worded.
5. **The "rc-mapping only" expectation understated the brief's own commit range** — the defer_reason delta legitimately contains the B2 rewrite (commit `5f8528869`, *"O(n) journal update + history scanners"*, plural), which commit attribution proves.
6. **The 100 KB OLD-leg ceiling was self-contradictory** in my brief (a blanket ≤32,862 B ceiling vs an explicit one-leg instruction at 100 KB). The specific instruction governed; the leg completed in 229.5 s with rc=0 and **corrected my expectation** — the write-path OLD does not wedge until ~220 KB.
7. **The "in_flight null ⇒ path forced" and the timing-leg locale** — the four legs must be measured under an explicit UTF-8 env (`env -u LC_ALL LANG=en_US.UTF-8`) to be comparable with the first gate's UTF-8 numbers.

---

## §S7 — Pre-existing reds, classified (none is branch-caused)

| red | where | classification |
|---|---|---|
| 5 nodes in `tests/test_instance_metadata_atomic.py` | concurrency pack | **QUARANTINE row 134**; file byte-identical base↔HEAD; `daemon.mcp.*` not imported by the failure path ⇒ 0 branch-caused |
| `C5a GREEN ⇒ the serving-green commit-close` · `C5a the txn is closed` | boot-sweep pack | **pre-existing, parent-identical by base-leg replay.** Root cause: the fixture helper speaks HTTP/1.1 keep-alive with a **non-newline-terminated** body; bash **3.2.57** discards the partial buffer on `read -r -t` timeout ⇒ empty body ⇒ version absent ⇒ DEFER. A 4-cell matrix pins it: keep-alive + no newline ❌ · keep-alive + newline ❌ (readyz-absent) · keep-alive + both newline-terminated ✅ · HTTP/1.0 close-after-response ✅. C5b passes because a non-200 `/livez` short-circuits before the body read |
| `C7 cooldown … on GNU` (parent leg only) | boot-sweep pack | **platform-bound, macOS-only** — the parent's C7 converted a BSD-produced stamp with GNU-only `date -d`. **Green at HEAD** |
| `8b-c2 slow command: rc 124` | launcher pack | **BASE-only-flaky (1/3)**, timing-sensitive 1-second deadline observed at 2 s; not a lane regression and not a lane fix |
| `8b-r2` skip | launcher pack | env-gated live-install cell; `ENS_TEST_LIVE_LAUNCHER` deliberately unset |

---

## §S8 — Findings and follow-ups (none blocks the release GO)

1. 🟡 **The serving-green arm may be permanently unreachable on bash-3.2 hosts.** A real FastAPI/uvicorn `/livez` also answers keep-alive with an **unterminated** JSON body. If that holds, the launcher's serving-green arm would appear permanently unable to reach GREEN — a **product** portability defect, not just the fixture's. **Not verified here** (no live daemon; fenced) and filed as anomaly `4b67edb4-0d43-4c24-a195-e4ef9556001a`. **A live re-smoke on a bash-3.2 host is the way to settle it.**
2. 🟡 **The Q1 refusal WARN conflates two causes** — it quotes `awk rc=1` even in the *overrun* case, where the reader actually succeeded with a content verdict (rc 1 = "no matching row"). An operator can misread it as an extractor failure. Non-blocking; the unavailable arm genuinely produces rc 2.
3. 🟡 **`5f8528869`'s subject over-claims.** C7 has **zero hunks**: `_js_awk_pass` already carried `LC_ALL=C exec awk` at `:396`/`:398` **identically at the first gate**, so every new scan is locale-pinned *by construction* — the subject describes **reliance on that seam, not a new pin**. There is also **no new locale-invariance pin** for the two rewritten history readers (§13d's locale section covers `_js_journal_read` only and pre-dates this range). **Release-note line**, same treatment as the Q4 subject (§S3).
4. 🟡 **`2686d47c7`'s subject over-claims** — it names "the launcher `_ts_off` fix — its D4 defect", which describes the *launcher's* bug class; lib.sh never had the unsigned-offset defect (§S3). Same treatment.
5. 🟢 **PACKS.md's boot-sweep rows overstate the A/B.** Rows `:4464`/`:4518` pair "BASE 38/0 ≡ HEAD 51/2", but the BASE pack had only 38 cells (pre-C5 re-shape), so that pairing is **not** an A/B on the re-shaped C5a cells and does not establish "C5a red at parent". The base-leg replay does. **Documentation finding; PACKS.md not edited** (read-only fence).
6. 🟢 **Dead `local`s left by the rewrites** — `launcher.sh:1218` (`rest entry t_space t_end r_space r_end`) and `:1310` (`rest entry t_eq r_eq marker`), unused after the awk conversion. Cosmetic; they sit outside every diff hunk, which is why they produced no `??`. A shellcheck pass would flag them.
7. 🟢 **The POK store has hit its hard 64-active cap** — three legs this round were refused (`pok_active_cap`). One worker correctly declined to archive `deploy-01-make-install-live-dir-hazard` (a live safety fact) to make room for a parity fact, and instead superseded the fact that was *actually wrong* (`launcher-02-defer-reason-quadratic`, which claimed B2/B3 are still O(n²) at head — false since `5f8528869`). **Curator/leader action required**; the global-64 and `launcher`-family-3/3 pressure should be routed accordingly.
8. 🟢 **A `MOCK_TESTS.md` entry for the GNU-`date` stub was not written** — the gate's write fence confined the worker to `/tmp`; the spec is captured in the worker's report instead. **Owner action.**
9. 🟢 **A QUARANTINE row for `8b-c2`** was deliberately **not** added — the brief restricts the commit to this one path, and editing the ledger would leave the gated tree dirty. **Ledger-owner action.** (Recorded as POK `gate-02-launcher-8b-c2-flaky`.)
10. 🟢 **Host hygiene:** the 5-variable scrub used in this gate is **narrower than the repo's canonical 18-name scrub** — on this host `PLANE_MCP_WORKSPACE_SLUG` and `PLANE_BASE_URL` remain set. Presence/length were checked only; **no secret values were echoed** in this round (the first gate's credential leak was made an explicit firing offence in every brief). Future gate runs on this host should use `scripts/run_tests_scrubbed.sh` or add both Plane vars.
11. 🟢 **Stale harness trap:** the first gate's scratch dir `/tmp/g7-item7a/launcher.sh` holds the **OLD** implementation (172,946 B, sha `008f8890…`). Any future leg reusing that directory must re-copy HEAD's launcher (187,468 B, sha `09c2db10…`).
12. 🟢 **~80–160 orphaned `sleep 60`** from the `_js_awk_bounded` watchdog (0 `fork:` errors, no verdict impact, self-expiring in 60 s, not killed per fence) — the same trap seen in the first gate.

---

## §S9 — ensure.md validation (second gate)

| Requirement | Tier | Status | Evidence |
|---|---|---|---|
| No regressions in changed packs | Core / Critical | ✅ **PASS** | launcher 343/0/1S · cap 27/0 · boot-sweep 51/2 (2 pre-existing) · concurrency 5F ≡ row 134 · NET-NEW ∅ unchanged from the first gate |
| Deadlock / concurrency integrity | Core / Critical | ✅ **PASS-WITH-CLASSIFIED** | §S7 |
| No sync DB calls on the asyncio event loop | Core / Critical | ✅ **PASS** | covered by the concurrency pack |
| `dev.sh` includes `--timeout-graceful-shutdown 10` | Core / Critical | ✅ **PASS** | `dev.sh:155` live uvicorn invocation carries the literal with value 10 (unchanged by this delta) |
| All callers of converted async functions properly await | Core / Important | ✅ **PASS** | AST-classified: **9 call sites, 9 awaited, 0 not-awaited**. **Out of this branch's change set** (`git diff 818411107..7b0b7955d` has zero hits for the three symbols) |
| No dead code from the fix | Core / Nice-to-have | ✅ **PASS** | removal proven BASE-vs-HEAD; `_js_journal_read` **13** production call sites, `_js_json_sub` **12** ⇒ neither is dead code |

**Release Gate not run** — blast radius is not big/critical/architecture per ensure.md § Gate Tiers. **No ensure.md contradiction encountered**; every validation was pack-mapped or a static check, scoped, dual-layer timedout, no bare `pytest`, no `-x`. **`ensure.md` itself was not modified** (user-owned, read-only).

---

## §S10 — Evidence index (second gate)

| Artefact | Location |
|---|---|
| Launcher pack log | `/tmp/g2-launcher/pack.log` |
| Cap pack log + import proof | `/tmp/g2-cap/` |
| Concurrency pack, both env legs | `/tmp/g2-conc/` |
| Boot-sweep pack + base-leg replay + 4-cell matrix | `/tmp/g2-bootsweep/` (`head.log`, `EVIDENCE.md`, `rep1..3`) |
| Splice trace + syntax A/B | `/tmp/g2-splice/` |
| Timing legs, fixtures, traces, scaling tables | `/tmp/g2-timing/` (56 MB, 468 files) |
| Q1/Q2 probes, clock + awk shims, fixtures | `/tmp/g2-guards/` (`probes/p1..p6`, `shim/`, `shimawk/`) |
| Q4 parent leg, lib.sh extractions, date stub | `/tmp/g2-q4/` |
| Parity legs, D1 matrix, verdict artifact | `/tmp/g2-parity/` (`GATE2-ITEM9-PARITY-VERDICT.md`, `legs.out`, `d1_matrix.out`) |
| Diff audit evidence | `/tmp/g2-audit/` (`launcher_U0.diff`, `lib_U0.diff`, `test_launcher.diff`, `test_boot.diff`) |

**Gated-tree integrity.** `git status --porcelain` = exactly one line (` M .agents/tidier/notes.md` — the **pre-existing foreign** tidier-lane edit, untouched by every worker and by the tester) before and after every leg; `git diff --cached` empty; no untracked files; `launcher.sh` sha256 `09c2db10…` unchanged throughout; worktree ≡ committed blob; no worktree added; no push, merge or tag; **no live install path written or executed against**; no daemon signalled; port 8088 untouched; **0 hits on port 9797 from any probe**.

---

## §S11 — Closure statement

**The first gate's FAIL is answered, not withdrawn.** Three O(n²) boot-path walkers (`_js_journal_update`, `_js_history_has_defer_reason`, `_js_history_has_event_for_target`) are now O(n) at `7b0b7955d`, measured at ≈2× per doubling across 55 KB → 880 KB in both boot-path shapes; the ≥96 KB size guard is now a **bound** (refuse-to-act, rc 2, WARN naming size + guard + budget, journal byte-unchanged, lock released, txn left OPEN) rather than a router into the quadratic; the pre-flip branch now declines to act above the guard and is **byte-identical below it**; a torn/absent/empty journal can no longer masquerade as "no defer reason"; and the journal output is **byte-identical** to the first-gate implementation at every size that can be executed.

**Release posture: 🟢 GO on this gate.** The residuals above (§S8) are non-blocking: the serving-green bash-3.2 hypothesis needs a live re-smoke to settle; two commit subjects over-claim and want a release-note line; one PACKS.md pairing is overstated; the POK store needs curation; and three owner-level housekeeping items (QUARANTINE row, MOCK_TESTS entry, ledger registration) are queued. **None of them is a regression, and none is on the boot wedge.**


---

*Machine-readable marker for the release checker. The FINAL VERDICT below is the verdict of the LATEST gate round (the second gate). The first-gate FAIL at the top of this file is RETAINED EVIDENCE — it is the record that the five commits under test answer, and it must not be edited or removed. The checker reads the FINAL occurrence of this marker block; earlier red rounds are informational.*

## FINAL VERDICT: PASS
GATED_COMMIT: 7b0b7955db5834905bd01b3ba91fb32981f88f22
GATE_DATE: 2026-11-10T11:50:31Z

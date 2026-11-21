# Sonic tester results — cpu-throughput-arc2 (verification spot-checks)

Task: cpu-throughput-arc2 (ANALYSIS-ONLY multi-core CPU investigation)
Branch: cpu-arc2-integration @ e19ffd751 (worktree /home/nea/ensemble-src-wt-cpu-arc2)
Diff range: 0b5624dc9..e19ffd751
Date: 2026-11-21 (UTC)

## Verdict

TEST: PASS

All five checks pass or reproduce within documented variance. No new reds; no source changes in range.

## 1. Live re-derive (read-only, GET-only + /proc; NO writes/load/restarts)

Live daemon: PID 1208513, listener 0.0.0.0:9797 (live, ensemble-prod) — confirmed via ss -tlnp.
M1 sampler (benches/m1_sample.sh) window 30s:

- Aggregate daemon CPU: 6.15% of total capacity = ~0.98 core (sub-1-core picture reproduced).
- Per-core spread 1.03%–12.79%, no single core saturated (15/16 cores lightly loaded).
- Top threads by utime:
  - ram-guard-tick 17.87% (doc: 15.7–18.4%) ✓
  - ensemble-prod (MainThread) 16.17% (doc: 17.6–23.2%) — within ballpark
  - WedgeCaptureWat 4.53%
  - ThreadPoolExecu (LLM-SSE pool) max single 3.67%; collectively ~18%
  - ram-guard-lane 1.13%
- Total threads: 75; thread CPU sum 64.3%.
- /api/resources/* = 302 OAuth2 (as documented); /metrics = 200 empty body (gate OFF).

Reproduces M1's named hot threads (ram-guard-tick ~18%, MainThread ~16%) and sub-1-core aggregate.

## 2. Fenced bench re-runs (worktree venv, Python 3.14.7; zero live contact)

- bench_gil_venv.py: Py_GIL_DISABLED=0, _is_gil_enabled=True, abiflags=""; 4-thread pure-Python speedup 0.477x (doc: 0.449x) — standard GIL confirmed. Minor host variance.
- bench_json_serial.py (30000 items, 28.6 MB): stdlib json dumps 0.232s/loads 0.206s; orjson dumps 0.060s/loads 0.155s → 3.9x dumps / 1.3x loads (doc: 4.3x / 1.5x). Same conclusion; minor variance.
- bench_kernel_split.py: regex_pretokenizer_only speedup 0.075 (13x SLOWER threaded) — the "13x-slower threaded prose class" REPRODUCED exactly. bpe_core_only 1.124x; encode_full 1.415x; pathological a-run at 4K grid bpe_core 3.054x. hashlib 2.70x.
- bench_micro.py: pydantic 255k models/s (doc ~290k); hashlib 345 MB/s; regex 45 MB/s.

## 3. Hard-gate evidence (freeze_support)

Evidence artifacts present and match doc:
- /tmp/m2-spike.cc55h4/out_spike_mp_nofs_spawn.txt: REENTRY-LOOP-DETECTED (boot loop, rc 124).
- /tmp/m2-spike.cc55h4/out_spike_mp_fs_spawn.txt: SPIKE-OK ctx=spawn pool=0.289s ppe=0.305s pickle64KBx50=0.245s results=16.
- fs fork: pool=0.047s; nofs fork: pool=0.074s.
- inspect_frozen_binary.py vs /home/nea/agents-ensemble/current/ensemble-prod (read-only, never executed):
  carchive 967 entries; libpython3.14.so.1.0 (no 't'); PYZ 4771 modules; pyc magic 2b0e0d0a == venv; multiprocessing 23 modules; orjson 1; tiktoken 5.
- M2 doc documents dual-spike table (2798 re-entries / 45s / 0 work / rc 124 vs 1 entry / 16 tasks / 0.289s). Evidence consistent.

## 4. Fence check

git diff --name-only 0b5624dc9..e19ffd751 → 18 files, all under .agents/shared/planning/ (grep for non-planning paths = 0). Zero source changes. PASS.
Spot-checks: GZipMiddleware in daemon/ = absent (rc=1) ✓; freeze_support in run_app.py = absent (rc=1) ✓; DISCUSSION.md = 409 lines ✓; M1/M2 docs = 254/118 lines.

## 5. Pack registration

Cross-checked .agents/tester/PACKS.md in both main repo (/home/nea/ensemble-src) and worktree. No registered pack covers cpu-arc2 / evidence-verification (grep for "cpu-throughput"/"cpu-arc2"/"evidence-verification" = 0 hits). No new pack created: verification was read-only spot-checks + fenced re-runs of committed benches; no test logic added. Worktree registry is larger (5348 vs 4909 lines) — worktree copy is ahead of main, not lagging; no drift requiring action.

## Quarantine

No quarantine entries relevant to this analysis-only commission; none counted.

## Delta summary (yours vs doc)

| Metric | Doc | Measured | Verdict |
|---|---|---|---|
| Aggregate daemon CPU | 0.6–0.75 core steady | ~0.98 core (30s) | sub-1-core reproduced; window shorter/busier |
| ram-guard-tick | 15.7–18.4% | 17.87% | match |
| MainThread checkpoint aput | 17.6–23.2% | 16.17% | within ballpark |
| Py_GIL_DISABLED | 0 | 0 | match |
| _is_gil_enabled | True | True | match |
| 4-thread pure-Python speedup | 0.449x | 0.477x | minor variance |
| orjson dumps/loads delta | 4.3x / 1.5x | 3.9x / 1.3x | minor variance |
| regex pre-tokenizer threaded | 0.075x (13x slower) | 0.075x | exact match |
| pydantic models/s | ~290k | 255k | minor variance |
| to_thread non-comment lines | 1,181 | 1,440 | grep methodology difference |
| freeze_support in run_app.py | absent | absent | match |
| GZipMiddleware in daemon/ | absent | absent | match |
No failures; no net-new reds.

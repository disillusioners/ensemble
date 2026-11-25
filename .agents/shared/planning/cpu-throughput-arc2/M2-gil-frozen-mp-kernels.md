# M2 — GIL reality, frozen multiprocessing hard gate, hot kernels (ANALYSIS-ONLY)

- **Base**: `0b5624dc9` (v0.31.22) · branch `cpu-arc2-m2-gil-gate` · worktree `/home/nea/ensemble-src-wt-cpu-m2`
- **Date**: 2026-11-21 · **Host**: 16 cores, Intel Xeon E5-2680 v4 @ 2.40 GHz (`nproc`, `/proc/cpuinfo`)
- **Fences held**: worktree `.venv` only (`uv sync` first); live binary `/home/nea/agents-ensemble/current/ensemble-prod` **read-only, never executed**; every frozen-spike run pinned `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly, `DAEMON_PORT=8079`, `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_*` scrubbed **per-variable** (`env -u POSTGRES_*` is a silent no-op); scratch marker dirs; no source edits; no system installs.

## A. GIL REALITY — standard GIL in both build classes

### A1 — worktree venv interpreter (3.14.7; `pyproject.toml:18` requires-python class, same as frozen)

`benches-m2/bench_gil_venv.py` measured:

| Probe | Result |
|---|---|
| `sys.version` | `3.14.7 (main, Aug 14 2026, 15:35:28) [Clang 22.1.3]`, cpython |
| `sysconfig.get_config_var('Py_GIL_DISABLED')` | `0` |
| `sys._is_gil_enabled()` | `True` |
| `sys.abiflags` | `""` (free-threaded builds report `t`) |
| CPU-bound 1 thread vs 4 threads | serial best **1.381 s**, 4-thread best **3.077 s** → **0.449x** (slower: GIL + contention) |

### A2 — shipped frozen binary (CArchiveReader audit, zero execution)

`benches-m2/inspect_frozen_binary.py` (POK `pyinst-01/02` route: `CArchiveReader(binary).toc` names-only; PYZ header magic + module census; `PYTHONHASHSEED=0`):

- CArchive: **967** entries; runtime lib = **`libpython3.14.so.1.0`** (typecode `b`, compressed). No `t` in the name ⇒ **not free-threaded** (free-threaded CPython ships `libpython3.14t.so.1.0`).
- PYZ: **4771** modules; header pyc magic `2b0e0d0a` **==** worktree venv `importlib.util.MAGIC_NUMBER` `2b0e0d0a` ⇒ same 3.14 bytecode class.
- **`multiprocessing` IS bundled**: 23 modules incl. `spawn`, `popen_spawn_posix`, `pool`, `resource_tracker`; `concurrent.futures.process` also present. The missing piece is NOT the library — it is the entry-point bootstrap.
- Also bundled: `tiktoken` (5), `pydantic` (105), `orjson` (1), `daemon` (721); entry script `run_app` is a CArchive entry.
- `grep -rn freeze_support run_app.py daemon/ ensemble.spec` → **rc=1, zero hits** (brief's FACT confirmed at this base).
- `ensemble.spec:5` Analysis entry `run_app.py`; `ensemble.spec:132` `EXE(...)` with binaries/datas inline and no `COLLECT` ⇒ **onefile**; `ensemble.spec:143` `upx=True`; `ensemble.spec:146` `console=True`.

**Verdict A**: standard GIL build in both classes. No free-threading option on this build line; CPU scaling requires processes, not threads (except GIL-releasing native kernels — see C1).

## B. HARD GATE — multiprocessing in the frozen build

`benches-m2/spike_mp_nofs.py` and `spike_mp_fs.py`: identical ~90-line standalone apps; the ONLY delta is `multiprocessing.freeze_support()` as the first statement of the `__main__` guard. Both built onefile with the **worktree venv's** PyInstaller 6.19.0 (`pyproject.toml:81` declares `pyinstaller>=6.0.0`), 13.3 MB each, into gitignored `benches-m2/spike-build/`. `benches-m2/run_frozen_spikes.sh` runs each variant under `timeout 45` with the fences above, writing markers to scratch.

| Spike binary | ctx | rc | `main` entries | worker tasks | Pool(2) | ProcessPoolExecutor | 64 KB × 50 pickling |
|---|---|---|---|---|---|---|---|
| **no freeze_support** | **spawn** | **124 (hang)** | **2798** | **0** | never ran | never ran | never ran |
| no freeze_support | fork | 0 | 1 | 16 | 0.074 s | 0.021 s | 0.017 s |
| **with freeze_support** | **spawn** | **0** | **1** | **16** | **0.289 s** | **0.305 s** | **0.245 s** |
| with freeze_support | fork | 0 | 1 | 16 | 0.047 s | 0.020 s | 0.012 s |
| unfrozen venv baseline | spawn | 0 | 1 | 16 | 0.309 s | 0.281 s | 0.200 s |
| unfrozen venv baseline | fork | 0 | 1 | 16 | 0.088 s | 0.028 s | 0.013 s |

**`nofs`+spawn failure anatomy** (marker evidence, scratch `/tmp/m2-spike.cc55h4`): every child argv shape re-executed the WHOLE entry — `[]` (plain), `['-B','-S','-I','-c','from multiprocessing.resource_tracker import main;main(7)']`, and `['--multiprocessing-fork','tracker_fd=8','pipe_handle=14']`. Each re-entered `main()` spawned more pools → **2798 re-entries in 45 s**; stderr shows onefile teardown race `FileNotFoundError: /tmp/_MEIcxgCeW/base_library.zip` ([PYI-…:ERROR] Failed to execute script 'pyi_rth_inspect'). My in-spike re-entry guard (`os._exit(3)` after >4 mains) bounded the blast radius to a hang rather than an unbounded fork bomb. `worker_tasks=0`: no work ever executed.

**Mechanism (CPython 3.14 stdlib, venv install)**: `multiprocessing/spawn.py:61` `is_forking()` matches `argv[1] == '--multiprocessing-fork'`; `spawn.py:67-75` `freeze_support()` parses `tracker_fd`/`pipe_handle`, calls `spawn_main(**kwds); sys.exit()`; `spawn.py:83-95` `get_command_line()` **special-cases `sys.frozen`** to launch children with exactly that marker argv. The frozen bootloader does not consume it: without `freeze_support()` at entry, nothing calls `spawn_main`, so the bootloader runs the entry script again. With it, the child jumps directly to the worker function (which must be picklable/importable from the PYZ) and does **not** re-run `.env` loading, DB preflight, or uvicorn.

**Verdict B**: a process pool **works in this frozen pattern**, but `spawn` requires `freeze_support()` as the FIRST statement of `run_app.py` (before the `.env` block and `daemon.__main__._boot_db_preflight()`); `fork` works without it on Linux but is not portable and is unsafe in a multi-threaded process. Cost of enabling: ~4 lines at entry, **zero spec changes** (all mp modules already bundled), spawn child boot ~0.29 s (vs 0.05 s fork) and ~0.25 s per 50×64 KB pickling roundtrips. **Never enable without the entry fix**: in the REAL app each boot-loop child would re-run DB preflight against the configured database — far worse than this DB-free spike.

## C. HOT KERNELS (worktree venv, bounded payloads)

### C1 — tiktoken on OUR post-arc1 shape (`daemon/loader.py:1121-1122,1158-1208,1211-1254`: cl100k_base, 16 KB threshold, 4 KB whitespace-aware chunks, `encoder.encode(chunk)`)

Real function `daemon.loader.estimate_tokens` called on 4 independent 512 KB texts:

| Shape | Serial | 4 threads | Speedup |
|---|---|---|---|
| normal prose (chunked) | 0.270 s | 0.253 s | **1.067x** (repeat run: 0.339 → 0.515 s = 0.658x; unstable, no gain) |
| pathological `a` run (chunked hard splits) | 4.630 s | 1.450 s | **3.192x** |
| LF runs (shape-aware cuts) | 0.505 s | 0.374 s | 1.353x |
| small 8 KB (exact path) | 0.011 s | 0.006 s | 1.735x (thread-start overhead polluted) |

Kernel split (`benches-m2/bench_chunker_share.py`, `bench_kernel_split.py`):

- Pure-Python `_iter_count_chunks` is only **0.3%** (normal) / **2.1%** (a-run) of shipped-path time — the chunker is NOT the bottleneck.
- Normal prose: `regex` pre-tokenizer (`enc._pat_str`, compiled with tiktoken's `regex` dep) **0.531 s serial → 7.043 s with 4 threads = 0.075x (13x SLOWER)**; BPE core alone 0.412 → 0.395 s = 1.043x. The pre-tokenizer holds/thrashes the GIL on many small matches — this is why threaded normal-shape counting does not scale.
- a-run at the 4 KB grid: BPE core **4.960 → 1.577 s = 3.145x** (GIL released), pre-tokenizer 0.033 → 0.013 s = 2.525x (few matches, no thrash).
- **Verdict C1**: the native BPE core releases the GIL (≈3x on pathological runs); the Python-side regex pre-tokenizer does not and actively degrades under threads on normal prose. Do **not** thread the normal token-count path; threading only pays on BPE-bound pathological shapes.

### C2 — big-payload serialization (`bench_json_serial.py`, 30,000 items, 28.6 MB)

| Codec | dumps | loads |
|---|---|---|
| stdlib json | 0.236 s (121 MB/s) | 0.189 s (152 MB/s) |
| orjson 3.11.7 | 0.055 s (515 MB/s) | 0.124 s (231 MB/s) |
| **delta** | **4.3x faster** | **1.5x faster** |

Correction to brief: `orjson` is **not a direct** `pyproject.toml` dep, but **is installed transitively** (`langgraph-checkpoint-postgres`, `langgraph-sdk`, `langsmith`) and **already bundled in the shipped PYZ** (A2). The swap costs no new dependency; only call-site semantics (no custom `default=` hooks, float/None encoding differences) need review.

### C3 — remaining micro kernels (`bench_micro.py`, `bench_kernel_split.py`)

- pydantic `RootModel[list[Item]].model_validate` on 100,000 dicts: **0.345 s** (~290k models/s) — not a bottleneck.
- hashlib sha256 512 MB: **1.715 s (298 MB/s)**; 4×128 MB **2.80x** threaded — already GIL-releasing.
- `re` UUID scan over 20 MB: **0.468 s (42 MB/s)**; 4-thread **0.906x** — GIL-bound/contended, same class as C1's regex pre-tokenizer.
- **Remaining hot CPU kernels post-2A/2B/2C**: native BPE encode on pathological runs (≈3x threadable), regex pre-tokenizer / `re` scans (GIL-bound, threading harmful), stdlib json (4.3x available via already-shipped orjson). hashlib and pydantic validation are not worth optimizing here.

## Verdicts for M3 synthesis

1. No free-threaded interpreter on this build class → threads cannot scale pure-Python CPU; only native GIL-releasing kernels or processes can.
2. Frozen process pools are viable but `freeze_support()` at the very first entry line is MANDATORY for spawn; without it the measured result is a catastrophic boot loop, not a graceful failure.
3. Cheapest measured win: switch big (de)serialization to orjson (4.3x dumps), already in the venv and the shipped binary.
4. Do not thread normal-shape token counting (regex pre-tokenizer thrash); only pathological BPE-bound shapes scale.
5. Never call the exact `estimate_tokens` path on a large single-piece run: O(n²) BPE — observed >420 s hang during this lane (bench bug, now bounded to the shipped 4 KB grid).

## Data integrity notes

- First spike runner draft hung because `timeout 60 <binary> | tail` left boot-loop children holding the pipe write end; fixed with file redirect + post-run child cleanup. `pkill -f` initially matched its own shell command line and killed the tool shell; fixed with the bracket-pattern (`spike_mp_[nof]s`).
- Two C1 bench attempts accidentally fed 512 KB single-piece `'a'` to the raw BPE core (the O(n²) pathology) and timed out; both were corrected to the shipped 4 KB chunk grid. All reported numbers come from the corrected scripts.
- Onefile `_MEIxxxxx` teardown race appears only in the nofs+spawn boot-loop case; it is a symptom, not a separate product defect.
- All scripts are committed under `benches-m2/`; build artifacts are excluded by `benches-m2/.gitignore`. No source edits, no live daemon boot, no push/merge.

## Reproduce

```bash
cd /home/nea/ensemble-src-wt-cpu-m2
.venv/bin/python .agents/shared/planning/cpu-throughput-arc2/benches-m2/bench_gil_venv.py
PYTHONHASHSEED=0 /home/nea/ensemble-src-wt-sonic-maint-m3/.venv/bin/python \
  .agents/shared/planning/cpu-throughput-arc2/benches-m2/inspect_frozen_binary.py
.venv/bin/python .agents/shared/planning/cpu-throughput-arc2/benches-m2/bench_tiktoken_threads.py
.venv/bin/python .agents/shared/planning/cpu-throughput-arc2/benches-m2/bench_chunker_share.py
.venv/bin/python .agents/shared/planning/cpu-throughput-arc2/benches-m2/bench_kernel_split.py
.venv/bin/python .agents/shared/planning/cpu-throughput-arc2/benches-m2/bench_json_serial.py 30000
.venv/bin/python .agents/shared/planning/cpu-throughput-arc2/benches-m2/bench_micro.py
bash .agents/shared/planning/cpu-throughput-arc2/benches-m2/run_frozen_spikes.sh
```

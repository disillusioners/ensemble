# HANDOFF — CPU-throughput arc 2 (multi-core investigation) — ANALYSIS ONLY

Commission (sonic-leader, Emperor directive 2026-11-20): multi-core CPU optimization investigation,
NO code changes; deliverable = measured evidence + ONE discussion table with effort estimates.
Base: v0.31.22 = 0b5624dc9 (= origin/latest at plan-open). Integration branch: cpu-arc2-integration.
Nothing shipped, nothing pushed, no release/promote/live contact (fences honored). The merged
discussion doc: .agents/shared/planning/cpu-throughput-arc2/DISCUSSION.md (+ M1/M2 evidence docs and
all bench/spike scripts in the same tree). Complexity verdict: MEDIUM (assessed after explore+plan,
before dispatch; no override).

## Files touched across modules
- M1 (cpu-arc2-m1-live-picture @ c0551260, merged slot 1 → 2cb2e18a2):
  .agents/shared/planning/cpu-throughput-arc2/M1-live-cpu-picture.md; benches/m1_sample.sh; benches/m1_metrics_get.sh; benches/m1_wedge_harvest.sh
- M2 (cpu-arc2-m2-gil-gate @ 779fcec7f, merged slot 2 → bca2e046b):
  .agents/shared/planning/cpu-throughput-arc2/M2-gil-frozen-mp-kernels.md; benches-m2/{.gitignore, inspect_frozen_binary.py, bench_gil_venv.py, bench_tiktoken_threads.py, bench_chunker_share.py, bench_kernel_split.py, bench_json_serial.py, bench_micro.py, spike_mp_nofs.py, spike_mp_fs.py, run_frozen_spikes.sh}
- M3 (cpu-arc2-m3-synthesis @ 34e847b1e, merged slot 3 → fefad3794):
  .agents/shared/planning/cpu-throughput-arc2/DISCUSSION.md (409 lines)
- sonic-developer: this HANDOFF.md. Source tree: ZERO changes (17 files / 1709 insertions, all docs scope).

## What changed
No behavior change; analysis-only knowledge, integrated:
1. MEASURED CPU PICTURE (M1): live v0.31.22 daemon (PID 1208513 at capture, live API listener) uses
   ~0.6–0.75 of ONE core steady, 1.43 cores peak, out of 16 vCPU — with 5–7 agent turns in flight
   (direct evidence, not idle-window). Hot threads: MainThread checkpoint aput 17.6–23.2%;
   ram-guard-tick full-/proc scan 15.7–18.4% (~0.17 core, ~25% of daemon CPU — NEW row absent from
   the audit); LLM-SSE pool thread hot in 14/20 durable captures; db_offload lazy 1/6; DB pools idle
   (waiting 0). 15 of 16 vCPUs idle w.r.t. this daemon.
2. GIL REALITY (M2): standard GIL PROVEN three ways — venv 3.14.7 Py_GIL_DISABLED=0 /
   _is_gil_enabled=True / 4-thread pure-Python 0.449x; shipped binary bundles libpython3.14.so.1.0
   (no 't' = not free-threaded; PYZ magic == venv). Kernel map: tiktoken BPE core releases GIL
   (3.145x on pathological a-runs) but regex pre-tokenizer THRASHES (0.075x = 13x SLOWER on normal
   prose — never thread normal token counting); hashlib 2.80x; pydantic not a bottleneck
   (~290k models/s); orjson ALREADY transitive+bundled: 4.3x dumps / 1.5x loads.
3. FROZEN-MP HARD GATE (M2): multiprocessing WORKS in the frozen build ONLY with
   multiprocessing.freeze_support() as the FIRST statement of run_app.py (absent at base). Without
   it, spawn = catastrophic boot loop (2,798 entry re-runs / 45 s, 0 work, rc 124); with it, 1 entry,
   16 tasks, Pool(2) 0.289 s, ProcessPoolExecutor 0.305 s, 50x64KB pickling 0.245 s — no spec change
   needed (all mp modules bundled). Fork works in the spike but is UNTESTED in a multithreaded
   frozen process (60–69 threads live) — treat fork as unsafe.
4. ONE RANKED TABLE (M3, DISCUSSION.md §3): 11 rows folding the audit's remaining rows (r3 bridges →
   #4, r4 R-3 → #3, r5 Option E → #8, r6 BFS → #6, r8 retention → #11, r9 stall → #7, r10 micro →
   #5 + short-IO worklist) plus NEW rows (#1 ram-guard, #2 orjson, #2b gzip, #9 freeze_support,
   #10 process pool). Top: #1 ram-guard tick S(2–4h) ~0.17-core steady; #2 orjson S(2–4h) 4.3x/1.5x
   kernel; #2b gzip XS; #3 R-3 executor split M(6–12h) after L1; #4 bridges M. PREMISE CORRECTIONS:
   audit Option F (gzip) NEVER landed (no GZipMiddleware in daemon/); Option E largely landed in
   v0.31.15 (residual: watch read unbounded); audit anchor drift graph.py:9539→9559.
5. SEQUENCE (§5): L0 verify+retention (#11 gates arc-3 evidence) → L1 cheap overhead (#1+#2+#5+#2b,
   ~6–11h) → L2 executor split (config-sensitive, no lane sharing) → L3 bridges+BFS (parallel OK) →
   L4 process pool PARKED (lever proven, demand < 1 core — capacity without workload).

## What is UNTESTED
- (M1) /api/resources/* JSON bodies — OAuth2-302-gated; minting a bearer needs a live DB write
  (forbidden) → substituted public /readyz + durable wedge corpus (documented in M1 doc). Live
  Prometheus metrics endpoint = valid empty body (gate OFF); resource_daemon_bucket ring history not
  directly retrievable → wedge corpus as proxy. Version mix: 19x v0.31.10 + 1x v0.31.22 captures —
  M1 CANNOT confirm arc-1 blocker classes (tiktoken/pydantic) are gone from live (captures predate
  .16/.17; steady-state <1-core number IS current-version direct measurement).
- (M2) A real frozen release binary rebuilt WITH freeze_support was not produced (the dual spike
  proves the option class on identical machinery: pyinstaller 6.19.0, same spec pattern, onefile);
  fork-in-multithreaded-frozen untested; orjson swap not wired into the daemon (kernel-level numbers
  only); O(n^2) exact-estimate on large single-piece runs bounded at >420 s hang in bench (not run
  to completion).
- (M3) No benches re-run (synthesis per brief); M1/M2 numbers taken as committed; ranked impact for
  rows 2b/3/4 is BOUNDED-ESTIMATE, not end-to-end measurement. Version labels, to avoid confusion:
  interpreter = 3.14.7 everywhere; orjson PACKAGE = 3.11.7 (same version the shipped PYZ bundles).
- (integration) No integration seams beyond doc composition — zero source, zero merge conflicts
  (disjoint file sets held exactly); DISCUSSION.md cites were spot-checked at bca2e046b by M3.

## Known risks
- Evidence base is version-mixed and CPU-bias-selected (audit r8: reason-blind retention, 318 fires
  vs 20 survivors) — EVERY future perf arc inherits this until #11 lands; flagged as L0-first.
- ram-guard fix (row #1) touches a SAFETY surface (wedge kill decisions) — fail-closed must survive;
  medium risk, needs the ram-guard test surface pinned.
- orjson semantics differ from stdlib json (NaN→null, non-str keys raise, default= exists but call
  sites rely on implicit coercions) — every swapped site needs a value-parity test (row #2 risk).
- freeze_support placement is order-critical: FIRST statement of run_app.py, before .env loading and
  DB preflight; anywhere later = child re-runs preflight (row #9 risk CRITICAL if misplaced).
- Frozen-spike boot smokes ran under explicit fences (SWEEP=0 pinned, per-variable POSTGRES_*
  scrub, scratch markers, timeout 45) — any re-run MUST repeat those fences (house rules).
- Module worktrees removed + branches deleted after merge (house lifecycle); continuation = NEW
  worktree from cpu-arc2-integration tip (this commit or later).

## How to verify
On the merged tree (integration worktree /home/nea/ensemble-src-wt-cpu-arc2):
1. Read: .agents/shared/planning/cpu-throughput-arc2/DISCUSSION.md (start §1 headline answers).
2. Provenance: git log --oneline 0b5624dc9..cpu-arc2-integration (3 module merges + this commit);
   git diff --stat 0b5624dc9..HEAD — expect 17 files, all under .agents/shared/planning/cpu-throughput-arc2/ (+ this HANDOFF).
3. M1 re-run (read-only vs live): bash .agents/shared/planning/cpu-throughput-arc2/benches/m1_sample.sh
   (60s+ windows; re-verify the live daemon PID/listener first via ss -tlnp before touching it).
4. M2 re-run (fenced): uv sync (worktree .venv; /home/nea/.local/bin/uv); benches-m2/run_frozen_spikes.sh
   — MUST keep ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 + per-variable POSTGRES_* scrub + timeout.
5. Spot-checks M3 made at base: grep -rn GZipMiddleware daemon/ (empty); grep -n freeze_support
   run_app.py (empty); grep -c "to_thread" -r daemon/ (1,181 non-comment lines).

## Bash-raises used
none — every command ran under the default <=600 s bound; the 300 s M1 sampling window and the
45 s-bounded frozen spikes were background-free single commands within the cap.

## Complexity self-assessment
medium — three bounded analysis modules (~90/90/60 min), disjoint docs-scope file sets, zero source
changes; the riskiest empirical piece (frozen-mp gate) resolved by a dual minimal spike on identical
machinery. Verdict assessed after explore+plan and BEFORE dispatch (workflow §2.5); no override
raised, no handover.

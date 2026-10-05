# Test Report: worktree-uniform-flow commission — FINAL gate @ bf827ee6

Date: 2026-10-05 (UTC)
Branch: `feature/worktree-uniform-flow` (worktree `/home/nea/ensemble-src-wt-uniform-flow`), base `39036550`, 7 commits.
Worker instances:
- `f1c38926-413c-4de9-a8ea-d4d8347e820f` (wtgate-static-t2) — static gates + pre-merge evidence, no skill loaded
- `d047412c-c4a3-438e-a790-810082b059bb` (wtguard-empirical-t1) — empirical guard pack, `load_skill=test-pack-execution`

## Overall Verdict: ✅ READY

### Summary
- T1 empirical guard pack: PASS (16 runs, 79s harness runtime, ≤15s per run, 280s internal budget)
- T2 static gates: 12/12 PASS
- T3 evidence: captured; scratch cleanup confirmed by both workers
- Quick fixes applied: 0 (evidence-only gate — no code modified by gate)
- Quarantined tests affected: 0
- Safety: no daemon boot, no port binds (only pre-existing listeners 5432/8079/7979/9797/7456/22/53 observed), no DB contact, no writes to either checkout, `rm` confined to /tmp scratch dirs (both removed, evidence captured)

### Scope Decision
Change set = 11 files (10 prompt/doc `.md` + `dev.sh`), ZERO `daemon/` code. From ensure.md Core only the `dev.sh` static check is in blast radius. `concurrency_atomic_unit_test` + async-await greps map to daemon code — untouched, scoped out. Release Gate (daemon-boot E2E) not warranted: docs + shell-guard change, no daemon/architecture change; additionally this commission explicitly forbids boot (see Improvement Notices).

### T1 — dev.sh guard empirical behavior (scratch /tmp repos/worktrees only)
Guard block at `dev.sh:66-104`; load-bearing: linked-worktree detection `git rev-parse --git-dir` vs `--git-common-dir` (l.85-91), fence regex l.94 `^[[:space:]]*([[:space:]]*export[[:space:]]+)?ENSEMBLE_SELF_ENV[[:space:]]*=[[:space:]]*[^[:space:]#]+`, refuse+exit 1 l.95-100, warn+proceed l.103.

| Test | Expected | Observed | Verdict |
|---|---|---|---|
| T1.1 plain repo, no .env | guard PASS → next stage, abort safely | ALLOW; reached `Starting server with auto-reload…` then natural-abort `No module named uvicorn` (no venv in scratch); exit 1; no listener | PASS |
| T1.2 linked worktree, no .env | REFUSE, actionable msg, exit 1, pre-boot | `Error: linked git worktree detected without a fenced .env` + `Refusing to boot…` + fix hint; exit 1 | PASS |
| T1.3 linked wt + fenced .env | ALLOW, abort pre-boot | `Loading environment from .env…` → server-start stage → natural-abort; exit 1 | PASS |
| T1.4(i) non-repo dir | WARN + PROCEED | `Warning: could not determine worktree status…proceeding WITHOUT the worktree env-fence check.`; proceeds | PASS |
| T1.4(ii) git-unavailable | WARN + PROCEED | PATH-shim `git` stub (stderr + exit 1, scratch-only); same warning; proceeds | PASS |
| T1.5 v1 bare `ENSEMBLE_SELF_ENV=dev` | ACCEPT | regex MATCH; boot stage reached | PASS |
| T1.5 v2 `export ENSEMBLE_SELF_ENV=dev` | ACCEPT | regex MATCH; boot stage reached | PASS |
| T1.5 v3 `export  ENSEMBLE_SELF_ENV = dev` | ACCEPT (regex) | regex MATCH standalone; dev.sh end-to-end exits 1 EARLIER at `set -a; source .env` (l.57-64) — bash rejects spaced `export X = Y`. Fail-closed; see Improvement Notice 2 | PASS (regex) + caveat |
| T1.5 v4 `ENSEMBLE_SELF_ENV=dev # why` | ACCEPT | regex MATCH; boot stage reached | PASS |
| T1.5 v5 `ENSEMBLE_SELF_ENV=` | REFUSE | regex NO_MATCH; full refusal | PASS |
| T1.5 v6 `# ENSEMBLE_SELF_ENV=dev` | REFUSE | regex NO_MATCH; full refusal | PASS |
| T1.5 v7 `export OTHER_VAR=` | REFUSE | regex NO_MATCH; full refusal | PASS |
| T1.5 v8 whitespace-padded line | ACCEPT | regex MATCH; boot stage reached | PASS |
| T1.6(i) wt, no .env, var exported in shell | still REFUSE (file-based) | full refusal — env var does NOT satisfy guard | PASS |
| T1.6(ii) plain repo, no .env, var exported | ALLOW (main-checkout reason) | boot stage reached; identical shape to T1.1 (no var) → passes via plain-checkout branch, not via var | PASS |

Method: allow-cases aborted via natural module-miss (dev.sh uses `$PYTHON -m uvicorn daemon.api:app` l.142; scratch has no venv/daemon → fast exit, zero port binds) — verified cleaner than PATH shim. Worktree detection empirically confirmed (`--git-dir` ≠ `--git-common-dir` in scratch wt). All runs `timeout -k 2 15`; pack internal budget 280s.

### T2 — Static gates (12/12 PASS)
- **T2.1** `bash -n dev.sh` → exit 0. PASS
- **T2.2** markdown_it 4.2.0 (main-checkout .venv python, NO daemon imports, `timeout 120`) → 10/10 files OK. PASS
- **T2.3(a)** dropped "≥2 committing editors" trigger remnant hunt: 8 grep hits, ALL classified OK — 7 historical/ledger entries in `decisions.md` (supersession records, fork-verdict tables) + 1 unrelated `<20 lines` size note at `agents/tester/workflow.md:530`. Encoded prompts contain ZERO remnants — live encoding reads "ALWAYS … no concurrency threshold" (`giter/workflow.md:78`, `giter/rule.md:89`). PASS
- **T2.3(b)** hand-off loop 6/6 FOUND with file:line (leader census pre-write + giter-first + wt_path context @ `leader/workflow.md:94`, `leader/tools_note.md:22-23`; giter creates wt + fenced .env + lazy venv @ `giter/workflow.md:79-87`, `giter/tools_note.md:88-89`; editors wt-only + no-commit/no-merge @ `developer[v2]/workflow.md:46-47`, `coder/workflow.md:22`; giter `--no-ff` merge + chained cleanup wt→branch→KV @ `giter/workflow.md:90`, quiescence @ `giter/rule.md:90`). PASS
- **T2.3(c)** fence spec: same var `ENSEMBLE_SELF_ENV` both sides; guard accepts bare AND export forms (empirical). PASS (nuance → Improvement Notice 1)
- **T2.4** `diff 39036550..bf827ee6 --stat` = EXACTLY 11 files, `+117/−15` exact; `-- agents/developer agents/tidier` = 0 bytes (v2-only honored; v2 path is literal `agents/developer[v2]/workflow.md`); zero `.env` paths in all 7 commits (`log --name-status` full list verified). PASS
- **Item 12 (ensure.md Core)** `--timeout-graceful-shutdown 10` present @ `dev.sh:142` (comment @139). PASS

### T3 — Evidence
- Pre-merge snapshot: `status --porcelain` = 0 bytes (clean); HEAD = `bf827ee61a32b455c4e63ca82ed04e9a6236f359`; 7 commits listed (`3f931242`…`bf827ee6`); `worktree list` (from main checkout) shows uniform-flow @ bf827ee6 AND wt-durability @ 3a2bbdf8 registered (19 entries).
- Scratch cleanup: static worker `/tmp/wtgate-static-wtuf-905853` removed (ls → ENOENT evidence); T1 worker `/tmp/wtgate-t1-906070` + sentinel removed; `pgrep -af uvicorn` empty; `ss -ltn` = pre-existing listeners only.

### ensure.md Validation Results (scoped)
- **Critical**: dev.sh static check PASS. "No regressions in changed packs" → the only executable surface changed is `dev.sh`, covered by the T1 ad-hoc pack (PASS); `concurrency_atomic_unit_test` maps to daemon code (out of blast radius). No ensure.md critical failures.
- **Release Gate**: not run — not warranted by blast radius AND commission forbids boot.

### ensure.md Improvement Notices
- ⚠️ Release-Gate prerequisites ("Daemon running: `./dev.sh`", health at `localhost:8079`) contradict this commission's absolute no-boot/no-port constraint. Intent honored MY way: dev.sh's decision paths (guard allow/refuse/warn) exercised empirically T1.1–T1.6 with fail-closed aborts BEFORE uvicorn; zero port binds. Suggested ensure.md addition: a "no-boot commission" variant note (guard-level validation acceptable when the change set is dev.sh-only and boot is forbidden).

### Improvement Notices for commission owner (non-blocking, 🟢)
1. **Fence marker comment not load-bearing**: `giter/workflow.md:80-84` mandates marker comment + assignment; dev.sh regex (l.94) checks ONLY the assignment line — a marker-less fence boots. Permissive direction, giter's write path emits both lines. Suggest: tighten regex to require the marker OR reword workflow.md ("marker comment is human-readable documentation; the assignment is the machine check").
2. **Sourcing layer fails closed on spaced forms**: `set -a; source .env` (dev.sh:57-64) runs BEFORE the guard and bash-rejects `export X = Y` (spaces around `=`). Guard regex itself accepts it (T1.5 v3). Fail-closed (exit 1, no boot), giter never emits that form. Suggest: document that fence lines must be shell-sourceable, or tolerate source errors deliberately.
3. T1 pack was an ad-hoc one-shot commission gate (harness lived in /tmp, deliberately NOT registered in PACKS.md — ephemeral by design). No PACKS.md update made.

### Gaps
None. All dispatched packs reported; no re-dispatch needed; no incomplete nodes.

### Documentation Updated
- [x] RESULTS/2026-10-05-worktree-uniform-flow-gate.md (this file; new untracked file only — no tracked/main-checkout files touched by the gate)
- No QUICK fixes, no QUARANTINE changes, no MOCK_TESTS changes, no PACKS.md changes (see notice 3)

### Code Changes Summary
None by tester/gate workers (evidence-only). Gated state = committed `bf827ee6`, tree clean.

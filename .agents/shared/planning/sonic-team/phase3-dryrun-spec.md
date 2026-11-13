# Phase 3 — Dry-run E2E: PARALLEL architecture re-proof (COMBINED v1.1+v1.2)

**Plan set:** ENSEMBLE-31 "sonic team" · **Phase:** 3 of 4 (0 Preflight, 1 Content build, 2 Verification, 3 Dry-run E2E, 4 Staging/promote-ready/NO promote)
**Status:** SPEC — REWRITTEN 2026-11-13 for the COMBINED v1.1+v1.2 architecture (Emperor ruling: one release, one ceremony). This file SUPERSEDES the v1.1-only presence-assertion version of the V-criteria (V9–V11) — the superseded text lives in this branch's git history; the safety pins (§3) and the procedure audit (§2) carry over UNCHANGED.
**Branch:** `feature/sonic-team-v1-1` (combined rework lane)
**What is being proven:** the FIVE-agent parallel architecture end-to-end on a scratch, FENCED daemon booted from this worktree with the REAL agent definitions: `sonic-leader` intake + route → `sonic-developer` (ONE explore → module split → PARALLEL sonic-coders → dependency-order merge → HANDOFF-v2) → `sonic-reviewer` + `sonic-tester` at INTEGRATION level → `sonic-leader` verdict + write-once-at-end knowledge moment.
**Inputs:** `dryrun-boot-procedure.md` (VERIFIED; + its AMENDMENT blocks) + helpers `fenced-launch.py`, `dryrun-verify.py` · `phase1-implementation-spec-v1.1.md` (the combined frozen strings) · the v1.1 §3 safety pins (unchanged, §3 here).

## 1. Purpose, scope, gate semantics

- **PASS ⇒** Phase 4 (staging per the V1-1-AMENDMENT ceremony recipe — PLAN ONLY) MAY proceed. **FAIL ⇒** nothing is staged; write an honest failure report with the evidence bundle.
- **Exactly ONE run.** No retry loops. (A single pre-flight retry is allowed only for an evidenced environment/config defect that prevented the daemon from ever becoming healthy — F1 class — never for an agent-behaviour failure.)
- **Zero live contact** is a hard invariant: the live daemon, the demo daemon, the live DB, and the live install dirs are untouched — asserted pre/post (§3 pins + `dryrun-verify.py`). Pids are point-in-time: re-read them immediately before the run.
- Out of scope: snapshot warm-start chain, performance/cost measurement (kb-writer CHEAP verdict is report-only — `V1-1-AMENDMENT.md`).

## 2. Procedure source — audited, unchanged requirements

**`dryrun-boot-procedure.md` remains the AUTHORITATIVE boot/teardown recipe**; the implementer re-audits the P1–P11 checklist before running anything; any violation ⇒ report the gap, do not improvise. The P1–P11 properties (env fence, scratch ports 8797/8798, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicit, health probes, supervision+logging, teardown, HOME fence, no live contact, exactness, **FD fence via `fenced-launch.py` — MANDATORY for any boot**, LLM credential forwarding per the batch-2 amendment) all still apply verbatim. The parallel run ADDS work-item topology (§4 D1') but does NOT change any boot property.

**Bash-overlay guard (binding):** the agent bash tool fail-closes any command whose TEXT contains live tokens; all liveness/isolation checks use unfiltered `ss -ltnp`, PID-based checks, and `dryrun-verify.py`. Never put a literal live port token in a command and never work around the refusal.

## 3. SAFETY PINS — VERBATIM, non-negotiable (carried over unchanged)

1. **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned EXPLICITLY** in the daemon's environment. NEVER `env -u ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` — the `-u` form strips the ambient `=0` guard and re-arms the sweep. Pin `=0`; never unset.
2. **Launch ONLY through `fenced-launch.py`.** Agent bash/`proc_run` shells are children of the live daemon and inherit its non-CLOEXEC listener fd; `env -i` does NOT close inherited fds — only the launcher's `closerange(3,4096)` does. Any boot without the launcher is F3 (CRITICAL).
3. **NEVER `env -u POSTGRES_*`** — bash does not glob env names; the glob form is a silent NO-OP. Scrub per variable, or `env -i` + explicit `POSTGRES_*` assignment.
4. **Test/dry-run scripts must NEVER resolve live-shaped HOME-derived install paths.** Fake HOME + escape guard.
5. **Scratch ports only** — 8797/8798 (fallbacks 8897/8898, 8997/8998); free-check before boot; never 9797/9798/7979/8079.
6. **Zero live contact** — live daemon, live DB, live install dirs untouched; asserted pre/post via `dryrun-verify.py` + claim files.
7. **Kill only by exact PID.** Broad `pkill` is BANNED; the authoritative scratch pid is the `pid` in the port-claims file.
8. **Bash-overlay guard compliance** — guard-safe checks only.
9. **Worktree discipline for the work item:** module worktrees are `<repo>-wt-<slug>` siblings created by the developer per FROZEN `[sonic-merge-strategy-v1]`; pathspec-limited commits; `merge --no-ff`; NO `git stash`; teardown removes every module worktree/branch it created.
10. **`ENSEMBLE_DB_AMBIENT_ALLOW=1`** — REQUIRED on the first boot (documented sanctioned first-boot flag).
11. **`umask 077` at harness start** — applies to ALL scratch files; re-assert `chmod 600` on credentials-adjacent artifacts.
12. **Env capture is permitted ONLY as a redacted block** (`name + sha256-prefix`); no raw dumps, no `set -x` around credentials.
13. **Scrub before ANY artifact leaves the scratch dir** — grep the evidence bundle for the key's sha256-prefix and the `sk-(proj|ant)-` needle; record the scrub result.
14. **LLM credential forwarding (MF-1)** — the four `OPENAI_*` NAMES only, via the 0600 scratch env file; never argv/echo/`set -x`. Provider-proxy contact is C1-safe (not live contact).
15. **RAM-guard note:** if the host carries the staged RAM guard, large `uv sync`/pytest trees may be tree-killed; re-run only the killed step's remainder with identical inputs; never widen a budget silently.

## 4. Run outline (ordered — PARALLEL shape)

| Step | Action |
|---|---|
| D0 | **Pre-flight + harness fence** (unchanged): `umask 077`; scratch dirs; 0600 scratch LLM env file; re-read live/demo pids; re-scan scratch ports; record the commission-worktree `git status --porcelain` (must stay clean) and quiesce it for D2 |
| D1 | **Scratch work item + seed (giter lane):** create the scratch work-item worktree `/home/nea/ensemble-src-wt-sonic-dryrun` on scratch branch `dryrun/sonic-parallel-e2e-<YYYYMMDD>` from this branch's tip. Seed a TWO-module feature with DISJOINT files (the parallel design's precondition): `sonic_dryrun/__init__.py` (empty) · `sonic_dryrun/mathx.py` = `def add(a, b): """Return the sum of two integers.""" return a - b  # SEEDED BUG` + `sonic_dryrun/test_mathx.py` · `sonic_dryrun/strix.py` = `def slurp(s): """Upper-case a string.""" return s.lower()  # SEEDED BUG` + `sonic_dryrun/test_strix.py`. Verify: `pytest sonic_dryrun/ -q` ⇒ **2 failed** (the work items) |
| D2 | **Boot per the procedure + amendments** from the commission worktree — `fenced-launch.py` MANDATORY, `env -i` block + four `OPENAI_*` by name, scratch dirs, throwaway DB `ensemble_sonic_dryrun` |
| D3 | **Health probe + isolation:** `/livez` + `/readyz`; `dryrun-verify.py --scratch-port 8797 --live-pid <live> --demo-pid <demo>` ⇒ `RESULT: ALL PASS` |
| D3.5 | **LANE PROBE (MF-1) — MUST pass before D4:** OPENAI_API_KEY presence count == 1 (non-echoing) or one trivial provider round-trip |
| D4 | **Dispatch `sonic-leader` on the scratch daemon** with: *fix both seeded bugs in work item `/home/nea/ensemble-src-wt-sonic-dryrun` so `pytest sonic_dryrun/` passes; expect the STANDARD path (two disjoint modules ⇒ parallel coders); the developer creates per-module worktrees as `<repo>-wt-sonic-dryrun-mod{a,b}` siblings cut from the scratch branch, merges in dependency order with `merge --no-ff`, authors HANDOFF-v2 at the scratch work-item root; feature integration happens ON the scratch branch; blast radius = `sonic_dryrun/**` only.* **DESIGNED LEARNING (V13 precondition):** the dispatch context carries a non-obvious fake convention the lanes cannot derive from the code — e.g. "house convention: every `sonic_dryrun` module docstring must name its merge-order slot" — so the leader has a genuine, non-obvious correction to record at completion |
| D5 | `sonic-developer` turn: exactly ONE `explore()` at plan-open → module split (2 briefs) → PARALLEL coder dispatch → merges → HANDOFF-v2 → `SONIC-INTEGRATION: DONE` report |
| D6 | ≥2 `sonic-coder` turns (module A, module B) — one-shot per module: source exploration → change → quick-test → commit on the module branch → compact module report |
| D7 | `sonic-reviewer` + `sonic-tester` INTEGRATION verdicts on HANDOFF-v2 + `base..merged` (ONE wave) — both PASS required |
| D8 | **Teardown per procedure §6 + parallel additions:** SIGTERM exact scratch pid → ports freed → `DROP DATABASE` → remove claim file → remove BOTH module worktrees/branches AND the scratch work-item worktree/branch (giter lane) → capture the evidence bundle |
| D9 | **Evidence bundle:** transcripts for ALL FIVE lanes + `created_at` timings + HANDOFF-v2 + verdicts + `git log --oneline` of the scratch branch (module commits + merge commits) |

Polling discipline: `proc_logs`/short polls, never a blocking call approaching 1800 s. Timebox: hard wall 210 min for D0–D9 (the parallel architecture adds one integration hop vs the v1 outline's 180; per-step budgets otherwise unchanged: D5 ≤ 30, D6 ≤ 60 outer bound, D7 ≤ 20, D8 ≤ 15, D9 ≤ 10).

## 5. Pass criteria (mechanical — V0–V14, superseding V0–V11)

Transcript evidence contract (applies to V9–V14): fetch `GET /api/instances/{id}/messages` for each lane instance; tool calls appear on `assistant`-role messages under the FLAT key `tool_calls`, each entry carrying `name`, and every message carries `created_at`. **A verdict without per-message `created_at` timing attribution is incomplete evidence, not a PASS.**

| # | Criterion | Check |
|---|---|---|
| V0 | **Lane probe PASS (D3.5)** — unchanged | recorded BEFORE D4 |
| V1 | All steps D0–D9 complete | step log with timestamps |
| V2 | **HANDOFF-v2 present** at the scratch work-item root with all 7 frozen headers — `## Files touched across modules`, `## What changed`, `## What is UNTESTED`, `## Known risks`, `## How to verify`, `## Bash-raises used`, `## Blast-radius self-assessment vs predicate` (exact Title-Case, `grep -Fq` per header) | grep each header |
| V3 | Reviewer verdict = PASS **at integration level** — its transcript shows it consumed the HANDOFF path AND ran `git diff <base>..HEAD` (the merged diff), not per-module reports | verdict text + transcript evidence |
| V4 | Tester = PASS (2 passed, 0 failed) **on the merged tree** | pytest output captured |
| V5 | **Feature branch carries the integration:** ≥2 module commits + ≥1 `merge --no-ff` merge commit on the scratch branch; module branches exist per module | `git log --oneline --graph` + `git branch` |
| V6 | **Zero live contact** — `dryrun-verify.py` RESULT: ALL PASS (unchanged) | helper output |
| V7 | **FD isolation + no boot-sweep kills** (unchanged) | helper `FD-ISOLATION` + log grep |
| V8 | Teardown residue-free incl. BOTH module worktrees/branches | procedure §6 outputs |
| V9 | **sonic-developer: exactly ONE RAG `explore` in its FIRST working turn AND a silent knowledge-write lane** — explore count == 1 in the whole turn, timestamped BEFORE its first coder dispatch; AND in the SAME transcript `experience` count == 0 AND zero kb-writer `spawn_instance` entries. More than one explore is a FAIL (D-G violation); zero is a FAIL (the mandatory read never happened); any `experience()` call or kb-writer spawn is a FAIL (never-experience violation — the developer is mechanically able to do both via the knowledge lane, so a runtime violation MUST red here, not false-PASS) | transcript counts (`explore`/`experience`/`spawn_instance`) + `created_at` ordering vs first dispatch |
| V10 | **≥2 sonic-coders RUNNING CONCURRENTLY** — the two coder instances' turn windows (first→last message `created_at`) TEMPORALLY OVERLAP; a strictly sequential coder pair is a FAIL (the parallel dispatch did not happen) | interval overlap from the two transcripts' created_at min/max |
| V11 | **Integration merge on the feature branch** — the developer's transcript shows `merge --no-ff` merges it ran (bash tool calls) and the HANDOFF authoring; conflict-free here, but the merge ORDER must match the briefs' slots | transcript + `git log --merges` |
| V12 | **Coder transcripts: ZERO RAG explore calls AND ≥1 file-exploration calls — BOTH required per coder.** `explore` count == 0 AND (`read_file` + `grep_files` + `glob_files` + `list_directory`) count ≥ 1, for EACH coder instance — `bash` is EXCLUDED from the census (tautological: coders must bash to commit). One-sided evidence (zero RAG but also zero file reads, or file reads with an explore) is a FAIL — this is the Emperor clarification's two-sided shape | transcript counts per coder |
| V13 | **Leader: exactly-one `experience()` at commission completion** — count == 1, timestamped AFTER the verification verdicts resolve; the scenario is DESIGNED so a learning OCCURS (D4's fake convention), making exactly-one the expected shape — **a silent zero is a FAIL** (the designed-learning scenario removes the v1.1 zero-branch) | transcript count + `created_at` ordering vs verdict timestamps |
| V14 | **Reviewer/tester ZERO knowledge calls** — `explore` == 0 AND `experience` == 0 in BOTH verification lanes | transcript counts == 0 |

## 6. Failure taxonomy (additions to the v1 taxonomy F1–F11 — all v1 classes stand)

| ID | Failure | Class | Action |
|---|---|---|---|
| F12 | Leader routed FAST on a two-module commission (D-E gate violation) | routing | Fail; record the intake text |
| F13 | Developer explore count ≠ 1 (0 or ≥2), not at plan-open, OR its transcript shows an `experience()` call / a kb-writer `spawn_instance` entry (never-experience violation) | D-G violation | Fail; transcript evidence |
| F14 | Coders ran strictly sequentially (no temporal overlap) | parallel dispatch violation | Fail; interval evidence |
| F15 | Any coder transcript shows a RAG `explore` call OR zero file-exploration calls | clarification violation | Fail; transcript evidence |
| F16 | Leader `experience` count ≠ 1, or the call precedes the verdicts | write-once violation | Fail (designed-learning scenario ⇒ zero is a FAIL, not a valid branch) |
| F17 | Reviewer/tester transcript shows any knowledge call | zero-knowledge violation | Fail |
| F18 | HANDOFF-v2 authored by anyone but the developer, or the coder wrote a handoff file | authorship violation | Fail |

## 7. Acceptance criteria (phase-level)

- AC-P3-0: harness fence pins (§3, items 1–15) present in the executed harness; D3.5 recorded PASS before D4 (V0).
- AC-P3-1: `dryrun-boot-procedure.md` re-audited P1–P11 before D0; else BLOCKED.
- AC-P3-2: **V0–V14 all PASS**; evidence bundle saved with per-call `created_at` attribution; path recorded in the report.
- AC-P3-3: safety pins present in the executed commands (grep the evidence bundle); no `env -u POSTGRES_*` glob; no broad `pkill`; no raw env dumps; no raw `OPENAI_*` values.
- AC-P3-4: exactly one run recorded; the commission worktree `git status --porcelain` empty before and after.
- AC-P3-5: PASS ⇒ Phase 4 unblocked (ceremony recipe stays PLAN ONLY in `V1-1-AMENDMENT.md`); FAIL ⇒ nothing staged, honest report.

## 8. Transcript-fetch pin (literal commands)

```sh
# scratch daemon on $PORT (8797); $INSTANCE_ID = the lane instance id
curl -s "http://127.0.0.1:${PORT}/api/instances/${INSTANCE_ID}/messages" > /tmp/ensemble-sonic-dryrun/evidence/messages-${INSTANCE_ID}.json

# count a tool by name across a transcript (flat tool_calls[].name):
python3 -c 'import json,sys; ms=json.load(open(sys.argv[1])); \
print(sum(1 for m in ms for c in (m.get("tool_calls") or []) if c.get("name")==sys.argv[2]))' \
  /tmp/ensemble-sonic-dryrun/evidence/messages-${INSTANCE_ID}.json explore   # expect: developer 1, AND its experience count == 0 AND its kb-writer spawn_instance entries == 0 (V9/F13); coders 0; reviewer/tester 0

# file-exploration census for a coder (V12 second shape — must be >= 1):
python3 -c 'import json,sys; ms=json.load(open(sys.argv[1])); \
names={c.get("name") for m in ms for c in (m.get("tool_calls") or [])}; \
print(sum(n in ("read_file","grep_files","glob_files","list_directory") for n in names))' \
  /tmp/ensemble-sonic-dryrun/evidence/messages-${INSTANCE_ID}.json

# timing attribution (per-call created_at — REQUIRED in the evidence bundle):
python3 -c 'import json,sys; ms=json.load(open(sys.argv[1])); \
print([(c.get("name"), m.get("created_at")) for m in ms for c in (m.get("tool_calls") or []) if c.get("name") in ("explore","experience","send_message","spawn_instance")])' \
  /tmp/ensemble-sonic-dryrun/evidence/messages-${INSTANCE_ID}.json

# temporal overlap of the two coder turns (V10):
python3 -c 'import json,sys;
a=json.load(open(sys.argv[1])); b=json.load(open(sys.argv[2]));
wa=(min(m["created_at"] for m in a), max(m["created_at"] for m in a));
wb=(min(m["created_at"] for m in b), max(m["created_at"] for m in b));
print("OVERLAP" if wa[0] <= wb[1] and wb[0] <= wa[1] else "SEQUENTIAL", wa, wb)' \
  /tmp/ensemble-sonic-dryrun/evidence/messages-${CODER_A}.json \
  /tmp/ensemble-sonic-dryrun/evidence/messages-${CODER_B}.json
```

Guard-safety: the fetch targets the SCRATCH daemon port only; no literal live-port tokens in command text (§2 note).

**kb-writer lane cost (facts):** `explore()` runs the explorer agent (`llm_model: "quick"`); `experience()` is a zero-LLM caller turn plus an async kb-writer spawn (also `quick`). Edge: explorer's legacy `caller_model_overrides {"coder": null}` matches ONLY the literal caller agent_id `coder` — sonic-developer/sonic-coder never match it; no override path is exercised by the sonic lane. Verdict CHEAP — report-only, `V1-1-AMENDMENT.md`.

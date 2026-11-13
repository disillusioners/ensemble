# Phase 3 — Dry-run E2E: Bounded Spec (S10)

**Plan set:** ENSEMBLE-31 "sonic team v1" · **Phase:** 3 of 4 (0 Preflight & input gate, 1 Content build, 2 Verification, 3 Dry-run E2E, 4 Staging/promote-ready/NO promote)
**Owner:** W3 · **Status:** SPEC — the dry-run is executed ONCE, by an implementer, never by this plan lane
**Branch:** `feature/sonic-team-v1` @ base `ac9849bf7` (= origin/latest at plan time)
**Inputs:** `findings-B-infra.md` §2 (recursion caps; 125-min task cap `daemon/config.py:2096`), §4 (worker pool = 5, handoff latency p50/p90, avoid `invoke_agent_and_wait`), §6 (giter Worktree Mode, `agents/giter/workflow.md:78-103`) · `findings-C-risk.md` §4 (bash 1800 s cap; `bash_spawn_registry` is NOT a duration source) · **`dryrun-boot-procedure.md` (PRESENT — VERIFIED 2026-11-12, triple-exercised; plus the batch-2 AMENDMENT)** + helpers `fenced-launch.py`, `dryrun-verify.py` · **`plan-review.md`** (council APPROVE-WITH-NOTES; batch-2 MF-1/MF-2 folded here)
**[Leg A] runtime numbers (verbatim):** median feature commission 8–41 h wall; dispatch p50 **775 s** / p90 **4,258 s**; 125-min task cap; bash 1800 s cap fired ~79×/window, >10 min = 0.106% of 75k runs; coding-lane 402 ACTIVE until 2026-11-17T11:43Z (agentic/vision CLEAN); 6× recursion-limit-300 terminal events.

## 1. Purpose, scope, gate semantics

Prove the sonic loop end-to-end on a **scratch, fenced** daemon booted from this worktree with the REAL agent definitions: `sonic-leader` intake → `sonic-coder` one-shot coding turn → `HANDOFF.md` (7 frozen fields) → `sonic-reviewer` verdict → `sonic-tester` scoped run → `giter` commit on a scratch branch.

- **PASS ⇒** Phase 4 (staging) MAY proceed. **FAIL ⇒** nothing is staged; write an honest failure report with the evidence bundle (§6).
- **Exactly ONE run.** No retry loops. (A single pre-flight retry is allowed only for an evidenced environment/config defect that prevented the daemon from ever becoming healthy — §6 F1 — never for an agent-behaviour failure.)
- **Zero live contact** is a hard invariant: the live daemon (`:9797` + metrics `:9798`, live pid observed **2147839** at procedure-write time), the demo daemon (`:7979`, pid **3547188**), the live DB, and `/home/nea/agents-ensemble` are untouched — asserted pre/post (§5). Pids are point-in-time: **re-read them immediately before the run** (they are inputs to `dryrun-verify.py`), never trust these literals.

Out of scope: snapshot warm-start chain (R15 toggle default OFF; v1 uses the handoff file — `decisions.md` D2; not exercised — §8.4), performance/cost measurement (no token telemetry daemon-side — `findings-C-risk.md` §5a).

## 2. Procedure source — PRESENT and audited

**`.agents/shared/planning/sonic-team/dryrun-boot-procedure.md` — STATUS AT PLAN TIME (final): PRESENT, marked VERIFIED 2026-11-12, exercised end-to-end three times** (DB create → boot → `dryrun-verify.py` → SIGTERM → DROP). (At first draft of this file it was absent/pending devops; it landed during plan writing — recorded in §8.1.) The implementer consumes it as the AUTHORITATIVE boot/teardown recipe and re-audits the P1–P10 checklist before running anything; any violation ⇒ report the gap, do not improvise.

### 2.1 Minimum required properties — audit result against the delivered procedure

| # | Property | Required | Delivered procedure | Audit |
|---|---|---|---|---|
| P1 | Explicit env fence | `ENSEMBLE_SELF_ENV=dev`; full `POSTGRES_*` set; `POSTGRES_DB != ensemble_prod`; fenced `.env` before any daemon run | sources the worktree `.env` (`set -a; . ./.env`) then passes an explicit `env -i` block incl. `ENSEMBLE_SELF_ENV=dev` and every `POSTGRES_*`; DB = `ensemble_sonic_dryrun` | **PASS** |
| P2 | Scratch ports only | free scratch port(s), verified; none of 9797/9798/7979/8079 | **8797/8798** (metrics = API+1 at this base, `daemon/services/metrics_listener.py`), free-check + fallbacks 8897/8898, 8997/8998; `PORT` set explicitly — **`DAEMON_PORT` is INERT** (config.yaml `port: ${PORT:-8088}`) | **PASS** |
| P3 | Boot-sweep pin | `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicit | in the `env -i` block | **PASS** |
| P4 | Health probes | `/livez` 200 + `/readyz` 200 within deadline | 2 s poll loop; observed ready **≈17 s warm / ≈23 s cold** | **PASS** |
| P5 | Supervision + logging | background start, known log path, graceful stop | `proc_run` preferred (portable `setsid nohup` fallback); logs `/tmp/ensemble-sonic-dryrun/logs/boot.log` | **PASS** |
| P6 | Teardown | stop → DB drop → dirs/claims cleanup, every exit path | SIGTERM by exact pid, port-freed check, `DROP DATABASE ensemble_sonic_dryrun`, claim-file removal | **PASS** |
| P7 | HOME fence | no HOME-derived live install paths | `HOME=/tmp/ensemble-sonic-dryrun/home` (mode 700); all writes under `/tmp` via `ENSEMBLE_DATA_DIR`/`DAEMON_LOG_DIR` | **PASS** |
| P8 | No live contact | no `:9797` request, no live DSN, no live-install writes | stated + verified (§5/§8 of the procedure) | **PASS** |
| P9 | Exactness | copy-paste commands | every block is copy-paste executable | **PASS** |
| **P10** | **FD fence (load-bearing, non-obvious)** | the launcher must close inherited fds — agent shells are children of the LIVE daemon and inherit its non-CLOEXEC listening socket (fd=6 = 9797) | **`fenced-launch.py`** (`os.closerange(3,4096)` then `execv`) is MANDATORY; direct `python -m daemon` was PROVEN to hold the live 9797 listener fd (wi-2454ede3 port-capture class); post-fence scratch pid appears 0× on 9797/9798/7979 | **PASS** |
| **P11** | **LLM credential forwarding (MF-1)** | the four `OPENAI_*` names (`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL_BACKUP`) forwarded into the `env -i` block **by name** — values from a 0600 scratch env file sourced from the invoking shell (never argv/echo/`set -x`); **D3.5 lane probe PASSES before D4** | **AMENDMENT (batch 2)** appended to `dryrun-boot-procedure.md`: OPENAI_* forwarding + scratch-env mechanism + D3.5 + pins (j)(k)(l). The original §4 block forwards only POSTGRES_*/PORT/HOME and `daemon/config.py` reads `OPENAI_*` from process env (no `load_dotenv` in the boot path) — the boot-path-only validation (`:5-6`) proves nothing about the lane | **PASS via amendment** |

**Conclusion: the procedure satisfies P1–P10.** Two of its own pins are extra (not in my original checklist) and are now binding here: the FD fence (P10) and the **bash-overlay guard** — the agent bash tool fail-closes any command whose TEXT contains live tokens (`ensemble-prod`, `9797`, …), so all liveness/isolation checks must use unfiltered `ss -ltnp`, PID-based checks, and `dryrun-verify.py` (which reads `ss` inside Python). **Never** put a literal live port token in a command and never "work around" the refusal by targeting live processes.

## 3. SAFETY PINS — VERBATIM, non-negotiable

1. **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned EXPLICITLY** in the daemon's environment. A daemon-child shell inherits the ambient guard; with the var absent the boot sweep can SIGTERM/SIGKILL live processes (2026-10-31: a staged smoke killed the LIVE launcher + ensemble-prod pair). **NEVER `env -u ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`** — the `-u` form strips the ambient `=0` guard and re-arms the sweep. Pin `=0`; never unset.
2. **Launch ONLY through `fenced-launch.py`.** Agent bash/`proc_run` shells are children of the live daemon and inherit its non-CLOEXEC listener fd (fd=6 = 9797); a direct launch holds the live listener without binding it. `env -i` does NOT close inherited fds — only the launcher's `closerange(3,4096)` does.
3. **NEVER `env -u POSTGRES_*`** — bash does not glob env names; the glob form is a silent NO-OP and an ambient `POSTGRES_DB=ensemble_prod` stays visible. Scrub per variable, or (as the procedure does) `env -i` + explicit `POSTGRES_*` assignment.
4. **Test/dry-run scripts must NEVER resolve live-shaped HOME-derived install paths.** Fake HOME (`/tmp/ensemble-sonic-dryrun/home`) + escape guard; the 2026-10-12 incidents deleted a live install twice via an unfenced live-shape fixture.
5. **Scratch ports only** — 8797/8798 (fallbacks 8897/8898, 8997/8998); free-check before boot; never 9797/9798/7979/8079.
6. **Zero live contact** — live daemon, live DB, live install dirs untouched; asserted pre/post via `dryrun-verify.py` + claim files (§5).
7. **Kill only by exact PID.** Broad `pkill` is BANNED (wi-2454ede3); the authoritative scratch pid is the `pid` in the port-claims file (`/tmp/ensemble-port-claims-1000/*.json`, `install_dir` = this worktree).
8. **Bash-overlay guard compliance** (§2.1 note): guard-safe checks only; literal live tokens in command text are refused and must not be circumvented.
9. **giter's Worktree Mode contract** applies to every commit step: worktree per committing task, fenced `.env` before any daemon run, quiescence before any merge, merge IS the commit point (`agents/giter/workflow.md:78-103`). No merge tonight.
10. **(i) `ENSEMBLE_DB_AMBIENT_ALLOW=1`** — REQUIRED on the first boot (no `data/ensemble.json` yet with `POSTGRES_HOST`+`POSTGRES_DB` present → the DB-resolution guard exits 78 without it). It is the *documented sanctioned* first-boot flag (`dryrun-boot-procedure.md:56-60`), not a guard bypass; no longer needed once `ensemble.json` exists (tier 1). Restated here so AC-P3-3 asserts it from the spec, not only from the procedure (review advisory #7).
11. **(j) `umask 077` at harness start** — applies to ALL of `/tmp/ensemble-sonic-dryrun/**` (data/home/logs/evidence); this SUPERSEDES the lone `chmod 700 home` as the first rule (keep the chmod, umask-first). Re-assert `chmod 600` on every file written into the scratch tree.
12. **(k) env capture is permitted ONLY as a redacted block** — `name + sha256-prefix` per variable; NEVER raw values in logs/evidence; no `env`/`printenv` dumps; no `set -x` around credential handling.
13. **(l) scrub before ANY artifact leaves the scratch dir** — grep the evidence bundle for the key's sha256-prefix and the `sk-(proj|ant)-` needle; scrub or exclude hits; record the scrub result in the report. (F7 / wi-2454ede3 rule: the F7 finding was a live key sitting in 4 world-readable scratch files for ~25 min.)
14. **(m) LLM credential forwarding (MF-1)** — the four `OPENAI_*` NAMES only; values are sourced at harness start from the INVOKING shell into a scratch env file (mode 0600, inside the umask-077 scratch dir), then `set -a; . <scratch-env>; set +a` before the `env -i` exec. **Never** `env VAR=value` argv, never echoed, never `set -x`. **C1-safety line:** contact with the shared LLM proxy (provider lane) is NOT live daemon/DB/install contact — the proxy call is C1-safe.

## 4. Run outline (ordered)

| Step | Action |
|---|---|
| D0 | **Pre-flight + harness fence:** `umask 077`; create `/tmp/ensemble-sonic-dryrun/{data,home,logs,evidence}`; build the scratch LLM env file `/tmp/ensemble-sonic-dryrun/scratch-llm.env` (mode 0600) from the INVOKING shell's four `OPENAI_*` vars — values never echoed / never argv / never `set -x` (pin m); re-read live/demo pids (claims files / `ss -ltnp`), re-scan scratch ports free, record the commission-worktree state (`git status --porcelain` — must stay clean; it is the staging source later) and **quiesce it for D2** (#14: no concurrent agent/commit work in that worktree while the daemon boots and `uv venv` runs there; the venv is gitignored, confirm Phase-4 C5 cleanliness after) |
| D1 | **Scratch worktree + seed (giter lane):** create `/home/nea/ensemble-src-wt-sonic-dryrun` on scratch branch `dryrun/sonic-team-e2e-<YYYYMMDD>` from the `feature/sonic-team-v1` tip; commit the tiny feature seed: `sonic_dryrun/__init__.py` (empty), `sonic_dryrun/tiny_feature.py` = `def add(a, b): """Return the sum of two integers.""" return a - b  # SEEDED BUG`, `sonic_dryrun/test_tiny_feature.py` = `from sonic_dryrun.tiny_feature import add` / `def test_add(): assert add(2, 3) == 5`. Verify: `uv run python -m pytest sonic_dryrun/test_tiny_feature.py -q` ⇒ **1 failed** (the work item). (Lazy-venv rule: `uv sync` inside the scratch worktree when a test must run there — never reuse another checkout's `.venv`, which pins its own tree.) |
| D2 | **Boot per `dryrun-boot-procedure.md` §1–§4 + its AMENDMENT (batch 2)** from the commission worktree (`/home/nea/ensemble-src-wt-sonic-v1`, the validated path): lazy venv (`uv venv --python 3.14 .venv && uv sync --frozen --no-dev`), throwaway DB `ensemble_sonic_dryrun` (owner `ensemble_dev_app`), boot via `proc_run` + `fenced-launch.py` with the procedure's exact `env -i` block **plus the four `OPENAI_*` forwarded by name** (values loaded from the 0600 scratch env file — pin m), scratch dirs `/tmp/ensemble-sonic-dryrun/{data,home,logs}`. |
| D3 | **Health probe + isolation:** `/livez` 200 + `/readyz` 200 (≈17–23 s); then `.venv/bin/python .agents/shared/planning/sonic-team/dryrun-verify.py --scratch-port 8797 --live-pid <live> --demo-pid <demo>` ⇒ `RESULT: ALL PASS` (FD-isolation, port ownership, live/demo alive). |
| D3.5 | **LANE PROBE (MF-1) — MUST pass before D4:** non-echoing presence check on the scratch pid environ — `tr '\0' '\n' < "/proc/$SCRATCH_PID/environ" \| grep -c '^OPENAI_API_KEY='` ⇒ `1` (prints a COUNT only; alternative: one trivial provider round-trip against `OPENAI_BASE_URL`). A green `/readyz` proves nothing about the LLM lane. Failure is **F1 environment/pre-flight** and does NOT consume the single permitted run. |
| D4 | **Dispatch `sonic-leader` on the scratch daemon** (operator API / job lane; exact payload VERIFY-AT-IMPL from the procedure's daemon surface) with: *fix `sonic_dryrun/tiny_feature.py` in worktree `/home/nea/ensemble-src-wt-sonic-dryrun` so `sonic_dryrun/test_tiny_feature.py` passes; one-shot; produce the frozen 7-field HANDOFF; blast radius = `sonic_dryrun/**` only; pre-approval: giter commit-only in that worktree, no merge, no branch ops*. Intake records the coder-estimate (≤ ~90 min per S7). |
| D5 | `sonic-coder` one-shot turn (real definition, `recursion_limit_multiplier:7`) — edits `tiny_feature.py`, writes `HANDOFF.md` at the frozen path with all 7 fields. |
| D6 | `sonic-reviewer` verdict on handoff + diff — PASS required. |
| D7 | `sonic-tester` SCOPED run — only `sonic_dryrun/test_tiny_feature.py` (`ensure.md` scoped-test doctrine) ⇒ `1 passed`. |
| D8 | `giter` commit on the scratch branch (commit-only, worktree supplied, no merge) ⇒ `git log --oneline -1` + `git show --stat` (touches `sonic_dryrun/tiny_feature.py` + the handoff artifact). |
| D9 | **Teardown per procedure §6:** SIGTERM exact scratch pid → ports freed → `DROP DATABASE ensemble_sonic_dryrun` → remove claim file → remove scratch worktree/branch (giter lane) → capture the evidence bundle (`/tmp/ensemble-sonic-dryrun/logs/*`, verify output, verdicts, `git show`). |

Polling discipline: drive waits with `proc_logs`/short polls — **never a single blocking `bash` call approaching 1800 s** (tool cap, `findings-C-risk.md` §4a; `[Leg A]` shows real 1800 s timeouts). Never derive durations from `bash_spawn_registry.reaped_at` (99.98% GC-reaped — `findings-C-risk.md` §4b); use log timestamps. **RAM-guard note (#15):** if the host carries the staged v0.29.21 guard, large `uv sync`/pytest process trees may be tree-killed (`ram_breach_observed` fires even on ordinary commands); the step timeboxes partially bound exposure — re-run only the killed step's remainder with identical inputs, never widen a budget silently.

**Worktree split (decision, see §8.3):** the daemon boots from the COMMISSION worktree (the only path the procedure validated, with its fenced `.env` + `.venv`); the WORK ITEM lives in the scratch worktree so the commission worktree stays clean for Phase 4 (C5). If the agent tool layer cannot operate on the scratch worktree path (recorded check at D4), FAIL as **F11** — never dirty the commission worktree to recover.

## 5. Pass criteria (mechanical)

| # | Criterion | Check |
|---|---|---|
| V0 | **Lane probe PASS (D3.5, MF-1)** | scratch pid environ carries `OPENAI_API_KEY=` (count == 1, non-echoing) OR one trivial provider round-trip succeeded; recorded BEFORE D4 |
| V1 | All steps D0–D9 complete | step log with timestamps |
| V2 | Handoff present with all 7 frozen field names — exact Title-Case headers, `grep -Fq` per header (review #8): `## Files touched`, `## What changed`, `## What is UNTESTED`, `## Known risks`, `## How to verify`, `## Bash-raises used`, `## Blast-radius self-assessment vs predicate` | grep each exact header in the artifact |
| V3 | Reviewer verdict = PASS | verdict text in the evidence bundle |
| V4 | Tester = PASS (1 passed, 0 failed) | pytest output captured |
| V5 | Scratch commit exists on the scratch branch | `git log --oneline` + `git branch --contains <sha>` |
| V6 | **Zero live contact** — `dryrun-verify.py` RESULT: ALL PASS, incl. live pid owns live API+metrics ports (pid unchanged from D0) and demo pid owns 7979; no write under the live install dir in the window (guard-safe checks only — no literal live tokens in commands, §2.1 note) | helper output + claim-file inspection |
| V7 | **FD isolation + no boot-sweep kills** — scratch pid appears 0× on live API/metrics/demo ports; no sweep-kill lines in `/tmp/ensemble-sonic-dryrun/logs/boot.log`; live/demo processes continuously alive | helper output (`FD-ISOLATION` line) + log grep |
| V8 | Teardown residue-free — scratch ports free, DB count 0, claim file removed, scratch worktree/branch removed; only the evidence bundle remains | procedure §6 outputs |
| V9 | **(v1.1) sonic-coder explore-at-start PRESENT** — the coder instance's transcript shows an `explore` tool call within its FIRST working turn; exactly ONE such call in the whole turn (the bound), and the message timestamp of the call is recorded as evidence | transcript fetch (§9) — `tool_calls[].name == "explore"` count == 1 in the coder instance |
| V10 | **(v1.1) sonic-leader write-once-at-end** — the leader instance's transcript shows AT MOST ONE `experience` call, positioned at commission completion (after the giter commit dispatch resolves); the scenario is DESIGNED so a learning OCCURS (see §9), making exactly-one the expected shape — zero is valid ONLY when nothing was learned (record which branch fired) | transcript fetch (§9) — `tool_calls[].name == "experience"` count ≤ 1 + call timestamp |
| V11 | **(v1.1) reviewer/tester knowledge-FORBIDDEN** — zero `explore` and zero `experience` calls in BOTH verification lanes' transcripts | transcript fetch (§9) — counts == 0 for both instances |

## 6. Failure taxonomy, evidence, timebox

Record for EVERY failure: step id, timestamp, exact command, full error text, relevant log slice, and the pre/post live-liveness snapshot (helper output).

| ID | Failure | Class | Action |
|---|---|---|---|
| F1 | Daemon never healthy (no `/livez` 200 within ~80 s) **OR the D3.5 lane probe fails** | environment/config | Diagnose from `boot.log` (procedure §7 playbook: exit 78 port-claim / exit 78 DB-resolution / exit 75 PG) and the lane check (missing `OPENAI_*`). ONE pre-flight retry permitted if evidenced; no agent ran ⇒ the run has not started, and a missing key can never burn the single permitted run as an F5 |
| F2 | **Boot sweep fired / a live process died** | **CRITICAL** | Abort immediately; containment report; no re-run without an operator decision |
| F3 | **Any live contact detected / FD inheritance observed** | **CRITICAL** | Abort; containment report |
| F4 | Leader intake misroutes / rejects the task | agent behaviour | Fail; record intake text |
| F5 | Coder turn terminal (recursion limit, 125-min task cap, loop-breaker exhaustion) | agent behaviour | Fail; record which ceiling fired (`findings-B-infra.md` §2b/§2c) |
| F6 | Handoff missing/incomplete (any of 7 fields) | contract | Fail; attach artifact |
| F7 | Reviewer REJECT | quality | Fail; attach verdict |
| F8 | Tester FAIL | quality | Fail; attach pytest output |
| F9 | No commit created | process | Fail; attach giter report |
| F10 | Teardown residue (listener/DB/claim/worktree remain) | hygiene | Fail; record residue |
| F11 | Agent tool layer cannot operate on the scratch worktree path | tooling scope | Fail; report the limitation (never dirty the commission worktree) |

**Timebox (bounded):** hard wall **180 min** for D0–D9: D0 ≤ 10 · D1 ≤ 15 · D2–D3 ≤ 20 · D3.5 ≤ 2 · D4 ≤ 15 · D5 ≤ 60 (outer bound = the 125-min task cap; coder-estimate rule ≤ ~90 min) · D6 ≤ 20 · D7 ≤ 15 · D8 ≤ 15 · D9 ≤ 10. (D5's "≤ 60" is the DRY-RUN's own coder-step budget — deliberately tighter because the tiny feature is minutes-scale; the ≤ ~90-min intake ceiling is the COMMISSION-SIZE rule (S7/`decisions.md` D7). Both stand; no contradiction.) Basis: `[Leg A]` p50 775 s/hop × ~6 hops ≈ 78 min; p90 4,258 s/hop exceeds any budget ⇒ hard wall 180 min, exceeding it is a recorded failure (do not extend). Worker pool = 5 daemon-wide (`daemon/constants.py:91`) ⇒ leader fans out in ONE wave and ENDs TURN (`findings-B-infra.md` §4); `invoke_agent_and_wait` is forbidden (300 s default, terminates the child).

## 7. Acceptance criteria (phase-level)

- AC-P3-0: **MF-1/MF-2 harness fence** — pins (i)–(m) present in the executed harness: `ENSEMBLE_DB_AMBIENT_ALLOW=1` (i); `umask 077` + 0600 scratch env file (j); no raw env dump — redacted `name+sha256-prefix` only if env evidence exists (k); evidence-bundle scrub recorded (l); the four `OPENAI_*` forwarded by name via the scratch env file with **no value in argv/echo/`set -x`** (m); and **D3.5 recorded PASS before D4** (V0).
- AC-P3-1: `dryrun-boot-procedure.md` present and re-audited P1–P11 (checklist filled, each PASS — P11 via the AMENDMENT batch 2) before D0; else BLOCKED (no run).
- AC-P3-2: V0–V8 all PASS; evidence bundle saved under `.agents/shared/planning/sonic-team/evidence/` (or `/tmp` with an index file) and its path recorded in the morning report.
- AC-P3-3: safety pins §3 present in the executed commands (grep the evidence bundle for `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, `ENSEMBLE_DB_AMBIENT_ALLOW=1`, `fenced-launch.py`, `dryrun-verify.py`, `umask 077`; assert NO `env -u POSTGRES_*` glob, NO broad `pkill`, NO raw `env`/`printenv` dump, and NO raw `OPENAI_*` value in any artifact).
- AC-P3-4: exactly one run recorded (no retry beyond the F1 carve-out); the commission worktree `git status --porcelain` is empty before and after.
- AC-P3-5: PASS ⇒ Phase 4 unblocked with the evidence path recorded; FAIL/BLOCKED ⇒ Phase 4 not started, honest report.

## 8. Deviations & evidence conflicts

1. **Procedure status changed during plan writing: ABSENT → PRESENT + VERIFIED.** It landed at 19:58Z (devops) and is marked triple-exercised; `decisions.md` DEV-5 says "PENDING INPUT from devops; Phase 3 hard-blocked" — that ledger row is now STALE. This spec's §2 audit supersedes it; the morning report must note the status change (the decision itself, D10, is unchanged: consume the procedure, hard-block without it).
2. **`DAEMON_PORT` is INERT at this base** — the listen port resolves from bare `PORT` (config.yaml `port: ${PORT:-8088}`; YAML→init kwargs beat `DAEMON_*` env). The giter `.env` template writes `DAEMON_PORT=8079` (valid for `dev.sh`); the dry-run procedure correctly sets `PORT=8797` in the `env -i` block. Do not "fix" the `.env` template — set `PORT` at boot.
3. **Boot tree vs work-item tree are deliberately split.** The delivered procedure boots from the COMMISSION worktree (its validated `.env`, `.venv`, and claim identity `install_dir=…-wt-sonic-v1`). The work item lives in a separate scratch worktree so the commission worktree stays clean for Phase 4 (C5) and the scratch branch never touches the feature branch. F11 covers the case where agent file tools cannot reach the scratch worktree.
4. **`fenced-launch.py` is load-bearing and its absence is not survivable.** A direct launch was PROVEN to inherit the live 9797 listener fd (`env -i` does not close fds). Any boot without the launcher is F3 (CRITICAL), never a warning.
5. **Live/demo pids are point-in-time** (2147839 / 3547188 at procedure-write time). Re-read them at D0; never let a hardcoded pid turn a liveness check into a false PASS.
6. **Snapshot warm-start is NOT exercised.** The neutralized-snapshot handoff is the v2 upgrade path (`decisions.md` D2; R15 toggle default OFF; explicit-`snapshot_id` relay mandatory). v1 proves the HANDOFF-artifact path instead — record the snapshot chain as an UNTESTED surface in the morning report; do not claim it works.
7. **giter's approval gates add hops** (`findings-B-infra.md` §6 caveat); the D4 task message carries pre-approval (commit-only, named worktree, no merge). If giter still stops for approval, that is F9-class evidence, not a reason to bypass giter (`decisions.md` D17).
8. **MF-1/MF-2 folded via a clearly-marked AMENDMENT block appended to `dryrun-boot-procedure.md`** (devops's validated steps unchanged, per the batch-2 policy): the four `OPENAI_*` forwarded by name, the 0600 scratch-env-file mechanism, the **D3.5 lane probe**, and pins (i)–(m). This protects the single-run budget: a credential/environment fault is F1 pre-flight and can never consume the one permitted pipeline run. The original `env -i` block (`:150-168`) forwards only POSTGRES_*/PORT/HOME and `daemon/config.py` reads `OPENAI_*` from process env with no `load_dotenv` in the boot path — the procedure's boot-path-only validation (`:5-6`) proves nothing about the LLM lane; the amendment is load-bearing, not cosmetic.
9. **Execution notes (#14/#15):** quiesce the giter-owned commission worktree during D2 (boot + `uv venv` happen there; the venv is gitignored — confirm Phase-4 C5 cleanliness after); if the host carries the staged v0.29.21 RAM guard, large `uv sync`/pytest trees may be tree-killed (`ram_breach_observed` fires even on ordinary commands) — re-run the killed step's remainder with identical inputs; never widen a budget silently.

## 9. AMENDMENT 2026-11-13 — v1.1 knowledge-lane presence assertions (V9–V11)

**Supersedes the v1 absence assertions.** The v1 spec asserted knowledge ABSENCE (zero `experience()` calls, zero kb-writer spawns) — that predicate is gone: the v1.1 amendment (Emperor ruling 2026-11-13; `decisions.md` knowledge-grant amendment; `V1-1-AMENDMENT.md`) grants bounded knowledge to sonic-coder (explore-at-start) and sonic-leader (explore-on-decomposition + write-once-at-end). The dry-run re-proof judges PRESENCE/SHAPE, not absence.

**V9 — coder explore-at-start (presence).** The sonic-coder instance's transcript MUST show an `explore` tool call within its FIRST working turn — before the first file edit. Exactly ONE `explore` in the turn (the prompt bound); more than one is a FAIL (research-loop violation). Reviewer/tester transcripts MUST show zero knowledge calls (V11).

**V10 — leader write-once-at-end (shape).** AT MOST ONE `experience` call, made at commission completion (after the giter commit dispatch resolves — never mid-run). The scenario MUST be designed so a learning OCCURS: e.g. the D4 dispatch context carries a non-obvious gotcha the leader could not have derived from the code (a fake constraint worth remembering), so exactly-one is the EXPECTED shape. Zero `experience` calls is a valid outcome ONLY when the leader's final report records that nothing non-obvious was learned — the report and the transcript must agree (a silent zero is a FAIL).

**Timing attribution (required in the evidence bundle).** The Sunday sonic review judges explore-via-RAG cleanly from instance messages: record for each asserted call the message `created_at` timestamp — (a) the coder's `explore` call timestamp AND the coder turn's start timestamp (proves at-start ordering), (b) the leader's `experience` call timestamp AND the commit-dispatch resolution timestamp (proves at-end ordering). A verdict without timestamps is incomplete evidence, not a PASS.

**Literal transcript-fetch command (pin).** The messages API is `GET /api/instances/{id}/messages`; tool calls appear on `assistant`-role messages under the FLAT key `tool_calls`, each entry carrying `name` (`daemon/utils.py serialize_message`: `message_id, role, content, thinking, tool_calls, images, created_at`):

```sh
# scratch daemon on $PORT (8797); $INSTANCE_ID = the lane instance id
curl -s "http://127.0.0.1:${PORT}/api/instances/${INSTANCE_ID}/messages" > /tmp/ensemble-sonic-dryrun/evidence/messages-${INSTANCE_ID}.json

# count a tool by name across the transcript (flat tool_calls[].name):
.venv/bin/python -c 'import json,sys; ms=json.load(open(sys.argv[1])); \
print(sum(1 for m in ms for c in (m.get("tool_calls") or []) if c.get("name")==sys.argv[2]))' \
  /tmp/ensemble-sonic-dryrun/evidence/messages-${INSTANCE_ID}.json explore   # expect 1 for sonic-coder, 0 for reviewer/tester

# timing attribution (per-call created_at):
.venv/bin/python -c 'import json,sys; ms=json.load(open(sys.argv[1])); \
print([ (c.get("name"), m.get("created_at")) for m in ms for c in (m.get("tool_calls") or []) if c.get("name") in ("explore","experience") ])' \
  /tmp/ensemble-sonic-dryrun/evidence/messages-${INSTANCE_ID}.json
```

Guard-safety: the fetch targets the SCRATCH daemon port only; no literal live-port tokens in command text (§2.1 note).

**kb-writer lane cost (facts, for judging the lane CHEAP):** `explore()` runs the explorer agent (its own `llm_model: "quick"`); `experience()` is a zero-LLM caller turn plus an async kb-writer spawn (also `quick`). One edge recorded in `V1-1-AMENDMENT.md`: explorer's legacy `caller_model_overrides {"coder": null}` matches only the literal caller agent_id `coder` — sonic-coder NEVER matches it (its id is `sonic-coder`), so no override path is exercised by the sonic lane.

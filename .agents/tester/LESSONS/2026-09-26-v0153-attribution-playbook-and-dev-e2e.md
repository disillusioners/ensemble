# v0.15.3 Acceptance Gate — Attribution Playbook & Environment Notes (2026-09-26)

Gate: `feature/upgrade-tool-lane-fix` @ `a95b7028` vs base `139ba352`. Verdict PASS. Full evidence: `RESULTS/2026-09-26-v0153-tool-lane-fix-acceptance.md`.

## 1. The attribution ladder that settled 4 "NEW red" false alarms in one gate

Every initially-NEW red this gate was resolved by the same cheap ladder — no full-suite runs needed:

1. **Mission-footprint diff-stat FIRST** (`git diff --stat <base>..<head> -- daemon/`): today it returned exactly 6 files (+938/−12). Any red whose test file AND production surface are absent from that footprint is already 90% exonerated. This single command killed the `watch_job_mission_terminal` "NEW BLOCKING" misclassification instantly (test file + `daemon/tools/{missions,job_queue,instance}.py` all empty-diff).
2. **Determinism check (solo ×2)** before any A/B — separates order/xdist flakes from real reds.
3. **Base worktree A/B with import-root parity** — the decisive leg. `git worktree add --detach /tmp/<name> <base>` + `PYTHONPATH=/tmp/<name>` + verify `python -c "import daemon; print(daemon.__file__)"` resolves INSIDE the worktree before any pytest (the main `.venv` editable-pins `daemon` to the main checkout; without the pin you test HEAD code against BASE tests — silent wrong answer). Confirmed again today (2 legs).
4. **Byte-identity proofs for bash suites**: sha256 of the shared library (`lib.sh` identical HEAD↔base) + `diff` of the test script (only-delta = the new section) converts "extra failure on run 1" into a load-flake adjudication without a third leg.

Lesson: step 1 costs one command and should ALWAYS precede "NEW BLOCKING" classification — workers instinctively classify vs a stale baseline list, and the stated baseline is usually incomplete (today: 2 missed Mock-await nodes + an entire unquarantined lineage).

## 2. Worker path-hold discipline paid off (commission path errors)

Commission specified `tests/unit/test_api.py`; that file does not exist (canonical = top-level `tests/test_api.py`, the file `bump_version.py` pins). The worker REFUSED silent substitution and asked — correct behavior. Dispatcher correction is one message; a silent substitution would have invalidated the blast-radius rationale. Keep encoding "do not silently substitute" in dispatch contracts.

## 3. B2b `_probe` heartbeat = load-sensitive flake (release-journal suite, GNU host)

`B2b _probe keeps the lock heartbeat fresh` failed on the first HEAD run, passed on identical rerun. Mechanism: `_probe` (lib.sh:1074-1087) loops `deadline = now + budget(5)`; each iteration curls port 1 (connect-refused — latency varies with host load), refreshes heartbeat, `sleep 2`. Under load, curls eat the budget → ≤1 in-loop heartbeat refresh → `AGE ≥ LOCK_STALE_S (3)` → exit 20. Same lib.sh + same test + different latency = flake. Quarantined as a family member; do not re-litigate at future gates.

## 4. Dev daemon bring-up gotchas (tool-lane e2e)

- **Dev has no LLM primary upstream**: first boot's turn HA-failover latched onto a dead backup (`localhost:4001` → connection refused) IN-MEMORY; the daemon never recovers without restart. Fix: run a scripted tool-call-emitting mock LLM BEFORE dispatching the turn (the repo's mock-llm is text-only — cannot emit `tool_calls`) + daemon restart. Error lane itself behaved correctly (job finalized `status=error`, locks released).
- **Env scrub MUST be a standalone `#!/bin/bash` wrapper** (never `source`d under `/bin/sh`/dash — the 2026-09-26 incident class). Echo-verify `POSTGRES_SURVIVORS=0` in-band; the boot log's checkpointer line (`localhost:5432/ensemble_dev`, not `10.44.0.2/ensemble_prod`) is the definitive in-band proof the fence held.
- **release_info/upgrade_status have NO HTTP routes** — they are agent tools; the read surface IS the tool lane (exercise via a real agent instance over `POST /api/instances/{id}/messages`). Dev correctly reports `dir=none` and CANNOT see the live install's v0.15.1/v0.15.2 state (env-self-match fence D-FA2.3) — judge mirror correctness against the DEV env's true state, not the live install's.
- **Port convention deviation to avoid repeating**: the e2e's mock LLM used :4124 (dev range) instead of the 10000-19999 mock range. Free port, no conflict, but use ≥10000 for future mock fixtures per tester port rules.
- Teardown: identity-by-port (`ss -ltnp`) → SIGTERM → verify tree gone + ports free; live 9797 / demo 7979 pid-stability is the untouched proof.

## 5. Anchor drift in commissions

The commission's "M-2 pins :3677/:3724" pointed (post-landing) at run_id-binding attestation pins; the thread-name isolation pins actually live at `test_upgrade_journal.py:1444/:1473`. Always re-locate by grepping semantics (thread_name_prefix, executor name), not by line number, when auditing.

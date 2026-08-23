# DR-0 Pre-Flight — P2.3 Ladder & Drills (Batch B0 execution record)

- **Date:** 2026-08-23 · **Recorded by:** worker (P2.3 batch B1, doc-only dispatch)
- **Branch:** `feature/self-restart-p2p3-ladder-drills` @ `74040e64` (base == `latest`)
- **Form:** user-approved DR-0 mini-record (condensed pre-flight; the F3 clear is recorded here)
- **Runbook:** `docs/runbooks/upgrade-drills.md` §0 (prerequisites) — this file is the §0.2 DR-0 reference for the first drill batch
- **Verdict line: `DR-0: CONTINUE`**

**Redaction rule applied:** the live port is written `<live-port>` everywhere in this file — zero live-port literals. Demo port (7979) is not restricted.

---

## 1. Per-step results

| Step | Check | Result | Evidence summary |
|---|---|---|---|
| **S1** | Live baseline (read-only) | ✅ PASS | Live listener port `<live-port>`, pid **31150** (ppid **31130**); `ps -o pid,ppid,lstart,command` baseline captured at start, re-captured at end — **byte-identical**. Zero live HTTP requests, zero signals, zero writes for the entire pre-flight. |
| **S2** | Demo inventory | ✅ PASS | `GET :7979/livez` → 200 `alive`, version **0.10.5**; `GET :7979/readyz` → 200, `reasons: []`, `draining:false`; releases layout **5/5** healthy incl. `current → releases/v0.10.5-p2.1-e2e2`; `ENSEMBLE_SELF_ENV=demo` marker present in the demo install `.env`. |
| **S3** | F3 guarded clear | ✅ PASS (no action taken) | F3 subject pids **69870/69871** (stuck `promote.sh` + child, premerge-verification §7 F3) **self-exited before any action**. Identity assertion on pid 69871 **failed**: the pid was **recycled to the ACTIVE demo launcher** (lstart 2026-08-23 05:49:20, supervising daemon 69918). Kill correctly **withheld** per the identity-assertion protocol — **zero signals sent**. F3 closed **self-resolved**. |
| **S4** | Dev environment | ✅ PASS | `uv sync --extra dev` clean (bare `uv sync` strips the dev extra — project venv gotcha); `pytest-timeout 2.4.0` registered under `pytest 9.0.2`. |
| **S5** | tmp cleanup | ✅ PASS | `/tmp/launcher-*` absent (prior F5 concern clear); `/tmp/journal-timing.log` (mtime 2026-08-23 03:47) **left in place** per the zero-risk rule — pre-existing, not ours, removal has no upside mid-batch. |

**All 5 steps PASS · zero BLOCKS-DRILLS findings · live state untouched (S1 invariance held).**

---

## 2. Baseline data (for drill checkpoints)

- **Live pid baseline (S1):** pid 31150, ppid 31130 — byte-identical start/end. This is the standing baseline for every drill's live-pid checkpoint (runbook §0.4); any drift aborts the drill.
- **Demo daemon:** version 0.10.5 serving on 7979; `/readyz` green with empty reasons; not draining.
- **Demo layout:** 5/5 entries healthy; `current → releases/v0.10.5-p2.1-e2e2` (the P2.1 demo-e2e endpoint release).
- **Demo launcher binary:** md5 `54320be21f0a1aec966071231876268e` vs repo HEAD launcher md5 `bc65594c74bd9f1c82907f80fe62d1b1` — **NOT-EQUAL**. **Informational, expected skew:** the launcher travels with the staged release payload (ADR-030), and repo HEAD has moved past the deployed demo payload. Drill evidence still records a script/launcher version checkpoint per R3.5.
- **Repo porcelain at DR-0 close:** exactly one pre-existing line — ` M .agents/approver/active.md` — not DR-0's, recorded, untouched.

---

## 3. Anomaly table (5 findings — all INFORMATIONAL; zero BLOCKS-DRILLS)

| # | Finding | Class | Disposition |
|---|---|---|---|
| A1 | Demo launcher md5 NOT-EQUAL vs repo HEAD (`54320be2…` vs `bc65594c…`) | INFORMATIONAL | Expected skew (ADR-030 launcher-in-payload; HEAD ahead of deployed demo release). No action. |
| A2 | F3 pid 69871 recycled to the ACTIVE demo launcher — identity assertion failed before any signal | INFORMATIONAL | Kill withheld per protocol; zero signals sent; F3 closed self-resolved. Protocol worked as designed. |
| A3 | `/tmp/journal-timing.log` (mtime 2026-08-23 03:47) present | INFORMATIONAL | Pre-existing; left per zero-risk rule. |
| A4 | Dev venv pairing recorded: pytest-timeout 2.4.0 under pytest 9.0.2 (no suite executed in DR-0 itself) | INFORMATIONAL | Recorded for the drill batches' pack runs; `uv sync --extra dev` is the standing pre-run rule. |
| A5 | Repo porcelain carries pre-existing ` M .agents/approver/active.md` | INFORMATIONAL | Not ours; left exactly as-is per dispatch constraint. |

---

## 4. Verdict

`DR-0: CONTINUE` — preconditions for the P2.3 drill batch hold; no blocking findings; live environment untouched throughout (S1 byte-identical invariance is the compliance proof).

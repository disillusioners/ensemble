# W-1 — SLO-3 derivation companion evidence

**Lane:** Engine Arc P7 · Item 2 (W-1)
**Date:** 2026-11-22
**Author:** tester (P7)
**Scope:** companion evidence for the SLO-3 rehearsal figure `23.966s`; no file modified except this document.

---

## 1. FINDING (verbatim-stated)

> The SLO-3 figure **`23.966s` lives ONLY in the commit SUBJECT of `82aa36862`**
> (`docs(engine): P4 SLO-3 rehearsal transcript (host-frame delta 23.966s, boot_epoch telemetry)`).
> It appears in **NO file** in the tree, and it is **NOT derivable** from the committed transcript
> `.agents/coder/RESULTS/p4-closure/boot_rehearsal_slo3.txt` (328 lines), because that transcript
> contains **NO host-frame start anchor** — no `systemctl ActiveEnterTimestamp`, no launcher
> `starting:` line — which `phase4-plan.md` §4.3 / AMD-6 requires for the SLO-3 host-frame delta
> `readyz_green − boot_start`.

Verification performed at the live worktree (tip `b6887c244`):

| Check | Command | Result |
|---|---|---|
| figure in the transcript | `grep -n "23.966" .agents/coder/RESULTS/p4-closure/boot_rehearsal_slo3.txt` | **NO MATCH** |
| figure in any tracked file | `git grep -n "23.966"` | **NO MATCH** (only unrelated false positives: `…23a9663…`, `…2369667…`) |
| figure in any commit subject (all branches, fixed-string) | `git log --all --format="%H %s" \| grep -F "23.966"` | **exactly one**: `82aa36862a5c10cab59c51afed4d3472234783fd` |
| figure in commit diffs | `git log --oneline -S "23.966" --all` | **NO MATCH** (no diff ever added/removed the string) |
| host-frame start anchor in transcript | `grep -n -i "ActiveEnterTimestamp\|starting:\|launcher\|ExecStart"` | **NONE** |
| readiness end anchor in transcript | `grep -n -i "readyz\|readiness_composite\|composite"` | only L198/L261 (a *metrics source bundle* listing `readiness_composite` as present) and L231 (`[Readiness] Refresher started`) — **no first-write timestamp, no `/readyz` 200** |

The commit `82aa36862` adds exactly one file — the 328-line transcript (`git show --stat 82aa36862` → `1 file changed, 328 insertions(+)`). Nothing else.

**Consequence:** the SLO-3 gate input (host-frame `readyz_green − boot_start`) is **not computable** from the committed artifact. Both required anchors are absent: the **start** anchor (host-frame unit/launcher stamp) and the **end** anchor (first `readiness_composite` write or `/readyz` 200). This companion is the W-1 closure as companion evidence — the mission's sanctioned alternative.

---

## 2. Derivation table — what IS derivable from the transcript

All quotes are verbatim from `.agents/coder/RESULTS/p4-closure/boot_rehearsal_slo3.txt`; line numbers are 1-based.

| # | Start anchor (line · verbatim) | start ts | End anchor (line · verbatim) | end ts | Arithmetic | Frame | Classification |
|---|---|---|---|---|---|---|---|
| 1 | **L1** `00:42:41 - daemon.services.bind_retry - INFO - Bound 0.0.0.0:8088 on attempt 1 after 0.0s` | 00:42:41 | **L258** `00:43:00 - __main__ - INFO - Uvicorn running on http://0.0.0.0:8088 (Press CTRL+C to quit)` | 00:43:00 | 19s | host log clock → host log clock (same frame) | **DERIVABLE** — bind → uvicorn-ready |
| 2 | **L26** `00:42:48 - daemon.api - INFO - [Auth] route classes tagged (public/module-lane/protected)` | 00:42:48 | **L258** `00:43:00 - __main__ - INFO - Uvicorn running on http://0.0.0.0:8088 (Press CTRL+C to quit)` | 00:43:00 | 12s | host log clock → host log clock (same frame) | **DERIVABLE (approximate)** — see note (a) |
| 3 | **L70** `00:42:51 - daemon.services.boot_epoch - INFO - Boot epoch captured (DB clock): 2026-11-22T00:42:51.589866` | 00:42:51.589866 (DB clock) | **L258** `00:43:00 - __main__ - INFO - Uvicorn running on http://0.0.0.0:8088 (Press CTRL+C to quit)` | 00:43:00 (host log clock) | ≈ 8.41s | **DB clock → host log clock (MIXED frame)** | **TELEMETRY ONLY** — never a gate input per AMD-6 |
| 4 | **L1** `00:42:41 - daemon.services.bind_retry - INFO - Bound 0.0.0.0:8088 on attempt 1 after 0.0s` | 00:42:41 | **L261** `00:43:09 - daemon.services.metrics_query.wiring.sources - INFO - [Metrics] runtime source bundle resolved: present=census,dsn,manager,mcp_snapshot,mcps_gateway_service,readiness_composite,resource_monitor,sweeps,wedge_watchdog absent=db_connect,install_dir` | 00:43:09 | 28s | host log clock → host log clock (same frame) | **DERIVABLE but NOT a readiness span** — L261 is a periodic metrics source re-resolve, not a readiness event |

**Notes**

- **(a) Row 2 precision.** The uvicorn line **L27** `INFO:     Started server process [2498818]` carries **no timestamp** of its own (nor do L28 `INFO:     Waiting for application startup.` or L257 `INFO:     Application startup complete.`). The `00:42:48` start for row 2 is the **preceding timestamped line (L26)**, not a stamp on L27 itself. The 12s figure is therefore an *adjacency-derived* approximation, not a stamped measurement.
- **(b) Row 3 is the mixed-frame row.** `boot_epoch` is stamped on the **DB clock** (`daemon/services/boot_epoch.py:132`, naive-UTC) while L258 is a **host log clock** line. Subtracting across frames is exactly what §4.3 / AMD-6 forbids; the row is recorded as TELEMETRY ONLY.
- **(c) The transcript's full timestamp set** (sorted, unique) is: `00:42:41, 00:42:44, 00:42:45, 00:42:46, 00:42:47, 00:42:48, 00:42:50, 00:42:51, 00:42:52, 00:42:53, 00:42:55, 00:42:58, 00:42:59, 00:43:00, 00:43:09, 00:43:20, 00:43:29, 00:43:37`. There is **no `00:43:04`** and no timestamp between `00:43:00` and `00:43:09`.
- **(d) No readiness end anchor exists.** The only `readiness_composite` mentions (L198, L261) are metrics *source-bundle* listings; the only `[Readiness]` line (L231) is `Refresher started`. Neither is the first `app_state.readiness_composite` write, and there is no `/readyz` 200 poll anywhere in the transcript.

---

## 3. The `23.966s` reconciliation attempt

Honest statement: **no anchor pair in the transcript yields `23.966s`.**

| Candidate span | Value | Δ vs 23.966s |
|---|---|---|
| L1 (00:42:41) → L258 (00:43:00) — first line → uvicorn-ready | **19s** | −4.966s |
| L1 (00:42:41) → L261 (00:43:09) — first line → next timestamped line | **28s** | +4.034s |
| L26 (00:42:48) → L258 (00:43:00) | 12s | −11.966s |
| L70 boot_epoch (00:42:51.589866) → L258 (00:43:00) | ≈ 8.41s (mixed frame) | −15.556s |

- The **closest** span is the first line → uvicorn-ready at **19s** (row 1). It does **not** match `23.966s`.
- The task brief referenced a "last startup-adjacent `00:43:04`-ish region"; **no `00:43:04` anchor exists**. The startup-adjacent tail is `00:43:00` (L257/L258), and the next timestamped line is `00:43:09` (L261) — a periodic metrics re-resolve, not a readiness event. Nothing in the `00:43:00`–`00:43:09` region is a readiness anchor.
- The `boot_epoch → readyz` span (≈ 8.41s) is the mixed-frame value the commit subject labels "boot_epoch telemetry"; it is **not** the host-frame delta and cannot be combined with a host-frame start to produce `23.966s`.

**Conclusion:** `23.966s` is **not derivable** from the committed transcript. It is asserted only in the commit subject of `82aa36862`. If the number came from a host-frame measurement taken outside the committed artifact (e.g. a harness-captured `ActiveEnterTimestamp` that was never written to a file), that provenance is **not recorded anywhere in the tree** and cannot be audited from the commit.

---

## 4. Required anchor form for the next rehearsal (per `phase4-plan.md` §4.3 / AMD-6)

The SLO-3 host-frame delta is `readyz_green − boot_start`. To make it derivable, the next rehearsal must capture **both** anchors in **ONE host frame**, with `boot_epoch` recorded alongside as telemetry only:

| Anchor | Required form | Where it comes from |
|---|---|---|
| **start** | **Host-frame anchor owned by the harness/promote lane:** unit `ActiveEnterTimestamp` (`systemctl show -p ActiveEnterTimestamp`) for systemd-managed boots; the launcher `starting: <binary>` line write time (`data/launcher.log:3` shape) for the bare-restart class | captured by the gate pack / harness at rehearsal time |
| **end** | first write of `app_state.readiness_composite` in `_periodic_readiness_refresh_loop` (`daemon/api.py:5309`), **or** a `/readyz` 200 poll against the booting daemon | the composite's first non-`None` timestamp, or the poll result |
| *telemetry* | `boot_epoch` (`capture_boot_epoch(manager.engine)`, `daemon/api.py:969-971`; logged `daemon/services/boot_epoch.py:132`) | **TELEMETRY ONLY — never a gate input** |

Binding constraints (§4.3 / AMD-6):
- **ONE host frame** for the subtraction — never mix the DB-naive-UTC `boot_epoch` clock into the host-frame delta.
- The start anchor must be a **host-frame** stamp (`ActiveEnterTimestamp` / launcher `starting:`), **not** `boot_epoch`.
- The end anchor must be the **first `readiness_composite` write** (or `/readyz` 200), not the uvicorn `Application startup complete` / `Uvicorn running` lines (those are process-ready, not readiness-composite-ready).
- Record `boot_epoch` → first-ready alongside as telemetry, clearly labelled.

---

## 5. Recommendation

Two options for the leader:

1. **Re-run the rehearsal with anchor capture** (preferred for a clean SLO-3 gate input): capture `ActiveEnterTimestamp` (or launcher `starting:` write time) and the first `readiness_composite` write / `/readyz` 200 in one host frame, and record `boot_epoch` alongside as telemetry. This yields a derivable, auditable host-frame delta.
2. **Accept this companion as closure** (the mission's sanctioned alternative): the `23.966s` figure is documented here as **asserted-only-in-commit-subject and not derivable from the committed transcript**; the transcript is retained as-is (unmodified), and the SLO-3 gate input is flagged as **not established by this artifact**.

This document does not modify the transcript or any planning doc. It records the derivation analysis only.

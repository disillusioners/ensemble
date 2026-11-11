# Phase 2 Measurements — the RAM guard substrate (T18 / T19 / T20)

> **The recorded numeric source for the Phase 7 operator memo** (phase2-plan
> T19: "The re-published table is the Phase 7 operator memo's numeric source").
> Every number below was measured or derived on this host on **2026-11-11**
> with the harness at `scripts/ram_guard_measurements.py` (the worktree
> interpreter; the worktree's own `.venv`).
>
> **Binding source of truth:** `architecture-recommendation.md` §3.3/§3.4 (the
> overshoot law + the FROZEN arithmetic frame), §1 E6 (the host-measured metric
> costs), §10 item 3 + §16.3 (the T20 pre-freeze gate's method / owner /
> bounded fallback), §15 B5/B16/B21/B23.  Where this file and that document
> disagree, **that document wins**.

---

## 1. T18 — the per-tick shared-scan cost (the SCAN-POPULATION axis)

The shared scan is **O(host pids) per tick** and serves ALL live spawns
(per-spawn scans cost 9.49 ms each and overrun the tick — E6, arch §1.1 P9).

**Method:** a fake procfs tree at each population (the reader's own parse path;
the kernel's `/proc/<pid>/stat` shape is identical), 25 iterations per
population, median + p95.

| population | median | p95 | per-pid |
|---|---|---|---|
| 250 | **9.07 ms** | 9.55 ms | 0.036 ms |
| 1000 | **36.28 ms** | 37.13 ms | 0.036 ms |
| 1250 | **45.85 ms** | 46.50 ms | 0.037 ms |
| 2000 | **74.18 ms** | 76.26 ms | 0.037 ms |

The 250-pid figure reproduces **E6's 9.49 ms median** (measured 9.07 ms here) —
the reader's cost model matches the host measurement.

**The host's live pid count at the measurement: 251** ⇒ the per-tick scan cost
on this host is **≈9–10 ms**.

### The 200 ms interval: CONFIRMED

* At the host's population (251 pids): the scan is ≈9.5 ms of the 200 ms tick —
  **≈190 ms of headroom**.
* The scan is linear at ≈0.037 ms/pid ⇒ the scan alone would overrun the 200 ms
  interval at ≈**5 400 pids**.
* The per-member READER cost (the RSS trigger and the PSS confirm) measured on
  the fake tree: **≈0.029 ms/member** for both.  **CAVEAT — the fake tree
  UNDERSTATES the real cost:** it measures the reader's open+parse overhead,
  not the kernel's `smaps_rollup` generation.  The kernel-side per-member cost
  remains E6's host-measured model `≈0.10 ms + 0.0115 ms/MiB × RSS`
  (≈120 ms at a 1 000-pid host) ⇒ the confirm's 250 ms deadline is the
  binding constraint at large populations, not the tick.
* **The interval is CONFIRMED at 200 ms for this host** and needs no amendment.

---

## 2. T19 — the allocation rate R (a BOUNDED synthetic allocator)

**The allocation is explicitly bounded:** a synthetic allocator with a **capped**
total allocation (**256 MiB**) inside a **bounded** observation window
(**2.0 s**), touching every page so the memory is really resident, and
**releasing the allocation before exit**.  It is a measurement — never a load
injection, never a repro attempt on the live unit.

| run | allocated | elapsed | R |
|---|---|---|---|
| 1 | 256.0 MiB | 0.2432 s | 1052.7 MiB/s |
| 2 | 256.0 MiB | 0.2263 s | 1131.0 MiB/s |
| 3 | 256.0 MiB | 0.2057 s | 1244.4 MiB/s |
| 4 | 256.0 MiB | 0.1994 s | 1283.7 MiB/s |
| **median** | | | **1187.7 MiB/s = 1.1599 GiB/s** |

**Provenance (D-5 correction, phase-6 pin review):** the table above prints
FOUR runs; their true arithmetic median is `(1131.0 + 1244.4) / 2 =
1187.7 MiB/s = 1.1599 GiB/s`.  The earlier `1177.9 MiB/s = 1.1503 GiB/s`
figure was a transcription slip (~0.8 % low) and is CORRECTED here.  Every
downstream verdict below is unchanged at either value (the 1.034 GiB/s
threshold is exceeded by both; the derived-default/GEN-feasibility "NO"
holds at any R ≥ 0).

**The measured R = 1.1599 GiB/s sits INSIDE the assumed band** (arch §3.4:
R ∈ [1, 3] GiB/s) — near its lower edge.

**The overshoot law (§3.3, unchanged), at the measured R:**

```
Omega_typ        = R x 0.45 s = 0.522 GiB
Omega_worst      = R x 0.70 s = 0.812 GiB
Omega_contended(2) = R x 1.21 s = 1.403 GiB
```

---

## 3. The §3.4 interaction table, RE-PUBLISHED with the measured R

**The arithmetic frame is FROZEN (arch §3.4):**

```
F_max(R)  = C + A + Omega(R)              (A = 160 MiB = 0.15625 GiB)
floor(R)  = D_max + F_max(R) + O + M_c    (O = 1.0 GiB, M_c = 0.5 GiB)
box       = MemTotal T - others 1.79 - M_k 1.0   =>  X <= T - 2.79
```

On this host: `T = 11.67 GiB` ⇒ the **box bound is `X <= 8.88 GiB`**; the
derived default ceiling `C = 0.7 x T = 8.171 GiB` (8 367 MiB).

### 3a. The table as the frame computes it (D_max = 3.5 GiB, the arch's value)

| Scenario | C | floor @ measured R (Omega_worst = 0.805) | A feasible unit cap? |
|---|---|---|---|
| **Derived default on THIS host (T = 11.67 GiB)** | 8.171 GiB | **14.139 GiB** | **NO — at any R** (gap **5.259 GiB** at the measured R; **4.447 GiB even at R = 0**) |
| Knob lowered to 3072 MiB (the demoted guidance) | 3.0 GiB | **8.968 GiB** | **NO at the measured R** — feasible iff `R <= 1.034 GiB/s`; the measured **1.1599 GiB/s is ABOVE that** ⇒ the 7G/8700M pair does **not** restore the window at the measured rate |
| **General feasibility at the default** (C = 0.7 T) | 0.7 T | 0.7 T + 5.968 | iff `T >= 26.49 + 2.333 R` GiB ⇒ **T >= 29.19 GiB** at the measured R (≈28.9 GiB at R = 1 GiB/s) |

**The N = 2 contended disclosure row travels with it:** at the measured R,
`Omega_contended(2) = 1.403 GiB` ⇒ the N=2 floor is **14.731 GiB > 8.88 GiB**
⇒ **the N = 2 unit-cap window is EMPTY** (as the arch states, it closes above
R ≈ 0.598 GiB/s; the measured R is well above it).  The recommended pair is
explicitly a **single-tree claim**.

### 3b. The CONSERVATIVE table (D_max = 4.5 GiB — the T20 fallback's value)

Per the T20 bounded fallback (§10 item 3), the conservative `D_max` is the
operative one for the memo:

| Scenario | C | floor @ measured R | A feasible unit cap? |
|---|---|---|---|
| **Derived default on THIS host** | 8.171 GiB | **15.139 GiB** | **NO — at any R** (gap 6.259 GiB; 5.447 GiB even at R = 0) |
| Knob lowered to 3072 MiB | 3.0 GiB | **9.968 GiB** | **NO — at ANY R** (the floor exceeds the 8.88 GiB box bound even at R = 0) |
| **General feasibility at the default** | 0.7 T | 0.7 T + 6.968 | iff `T >= 29.82 + 2.333 R` GiB ⇒ **T >= 32.53 GiB** at the measured R |

### 3c. One arithmetic observation on the arch's own table (disclosed, not a re-design)

The arch's §3.4 table's first row prints a floor of **13.44 GiB** for the
derived default, but its own **frozen frame** yields
`8.171 + 5.15625 + 0.70 = 14.027 GiB` at R = 1 — and the arch's own gap
figure for that row ("≈ 4.45 GiB even at R = 0") reproduces EXACTLY under the
frame (`8.171 + 5.15625 − 8.88 = 4.447`).  The frame is the contract; the row's
floor cell is therefore reproduced here **frame-derived** (14.027 at R = 1; the
numbers above use the measured R).  This is the same class of slip as the
withdrawn 16-vs-18 code count — recorded, not silently absorbed.

---

## 4. T20 — the pre-freeze gate (a): the 8.11 GiB unit-peak attribution

**Method (read-only, ONE measurement session, no load injection, no repro
attempt, no daemon restart) — arch §10 item 3 / §16.3.**
**Owner:** this phase executed it; Phase 7 carries it into the operator memo.
**Bounded fallback:** inconclusive ⇒ treat the unattributed residue as
daemon-side (`D_max` shifts up by `min(residue, 1.0 GiB)`) and re-publish the
§3.4 table with the conservative `D_max`; **the freeze is NEVER blocked on
inconclusiveness.**

### Leg (i) — the unit's cgroup (`/sys/fs/cgroup/system.slice/ensemble-main.service`)

| field | value |
|---|---|
| `memory.max` | **`max`** (NO cap — E1 re-verified) |
| `memory.current` | 6 015 369 216 B (5.60 GiB) at the first sample → 11 330 924 544 B (10.56 GiB) at the end of the session |
| `memory.peak` | 11 558 744 064 B (**10.766 GiB**) |
| `memory.stat` `anon` | 4 671 410 176 B (**4.350 GiB — 78 %**) |
| `memory.stat` `file` | 1 078 743 040 B (1.005 GiB — 18 %) |
| `memory.stat` `kernel` / `slab` | 0.237 GiB / 0.217 GiB |

**⇒ the footprint is ANONYMOUS HEAP, not page cache.**  The "daemon page
cache" attribution is **REFUTED** at the sampled instant (file is 18 % of the
current).  The unit's peak has also **grown past the arch's 8.11 GiB figure**
(10.766 GiB now) — consistent with the concurrent work observed in leg (ii).

### Leg (ii) — the unit's process rollup (`cgroup.procs` → `ps`)

50 processes in the unit:

* the daemon itself (`ensemble-prod`) = **995 360 KiB RSS (0.95 GiB)**;
* the other 49 = **6.28 GiB RSS** — dominated by **two pytest-xdist farms
  belonging to OTHER lanes** (32 of the 50 processes; scratch venvs
  `/tmp/gate3-base-*/venv-314` and `/tmp/gate3-head-*/venv-314`, each worker
  holding 230–297 MiB RSS);
* **NO process in the unit has `ppid 1`** ⇒ **no orphans** at the sampled
  instant (the E8 "untracked/orphaned" attribution is NOT supported now).

### Leg (iii) — the Oct-30 incident windows

The retained kernel journal carries **no** OOM-kill record for 2026-10-29 …
2026-11-01 (the arch's E3/E8 citation falls outside the retained window) ⇒ the
incident-side attribution is **NOT re-derivable** in this session.

### The verdict

**INCONCLUSIVE for the historical 8.11 GiB peak** — leg (iii) cannot be
re-derived (journal retention) and `memory.stat` is a snapshot, not a
time-series, so the peak's composition is not recoverable from it.  What the
session *does* establish: the unit's memory is **anon-dominated** (the
page-cache hypothesis is refuted) and its largest current consumers are
**foreign concurrent workloads sharing the unit's cgroup**, not the daemon's
own tracked children and not orphans.

**⇒ The bounded fallback applies.**  The unattributed residue (the unit's
footprint in excess of the daemon's own 0.95 GiB baseline — several GiB, and
certainly ≥ 1.0 GiB) is treated as **daemon-side**, so
**D_max shifts 3.5 → 4.5 GiB** and the §3.4 table is re-published with the
conservative value (§3b above).  **The freeze is NOT blocked.**

---

## 5. What this means for the shipped default (carried to Phase 7)

* The default is **DYNAMIC**: 70 % of the detected effective total RAM
  (USER RULING #2, arch §4.7).  On this host that is **8 367 MiB (8.171 GiB)**,
  resolved once at boot; `ceiling_source` = `meminfo` (no cgroup limit —
  `memory.max = max`).
* The derived value is **never upper-capped** (§4.7c) — on hosts > ~91 GiB it
  legitimately exceeds 65 536 MiB.
* **Honest consequence (retained, user-accepted):** at the derived default on
  THIS host the guard fires at-or-after daemon death — at fire time the demand
  is ≈ 4.79 + 8.17 = **12.96 GiB > 11.67 GiB physical**.  The guard is a
  disaster line, not a constraint on legitimate heavy work (USER RULING #1's
  intent).
* **The operator levers** (Phase 7's memo): (a) ship **without** a unit cap —
  the kernel global OOM is the outer layer (today's state); (b) **lower the
  knob** to 3072 MiB — but at the MEASURED R = 1.16 GiB/s the 7G/8700M pair is
  **NOT feasible** (it needs R ≤ 1.034 GiB/s) unless the conservative D_max is
  wrong in the daemon's favour; (c) **resize the host** to ≥ 29.2 GiB (the
  arch's D_max) / ≥ 32.5 GiB (the conservative D_max).
* The **N = 2 contended row** travels with all of it: the contended window is
  EMPTY at the measured R.

---

## 6. How to re-run

```bash
cd /home/nea/ensemble-src-wt-ram-auto-termination
./.venv/bin/python scripts/ram_guard_measurements.py --populations 250 1000 1250 2000 --iterations 25
./.venv/bin/python scripts/ram_guard_measurements.py --json
```

The harness is read-only with respect to the host except for the **bounded**
256 MiB allocation, which is released before it exits.

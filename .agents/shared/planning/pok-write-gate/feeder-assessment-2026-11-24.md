# Feeder Assessment — experiencer + kb-importer (POK write gate, Phase 9 T9.5)

**Mandate:** ASSESS-AND-REPORT ONLY (design-amendments-v2 §10 / addendum-3 item 5). The
plan carries an assessment task; **the REPORT recommends, the Emperor decides**. **NO
unilateral grant change** was made by this lane — the grants are exactly as they were.

**Lane:** Wave 2 Phase 9 (the census surface + the feeder assessment) ·
**Branch:** `feature/pok-write-gate` @ `103b96b3c` (worktree
`/home/nea/ensemble-src-wt-pok-write-gate`) · **Assessment produced:** 2026-11-24 (UTC).

**Snapshot discipline:** every figure below carries its source and its snapshot time. Bare
counts are never quoted. The DEV-store figures are read-only SELECTs against
`ensemble_dev` @ `127.0.0.1:5432` (the worktree `.env`); the store-shape figures are cited
from the branch-verifiable census artifacts with their sha256.

---

## 1. The grant posture (MEASURED, 2026-11-24)

`agents/<agent>/meta.json` → `tools.allow`:

| agent | holds `pok`? | KB-read surface | prompt mentions of `pok` |
|---|---|---|---|
| `experiencer` | **YES** | the `rag` category | **0 files** |
| `kb-importer` | **YES** | the `rag` category | **0 files** |
| `kb-writer` (the reference case) | **NO** (removed 2026-11-24, Phase 4 rider, `fd7dc6480`) | `rag_insert_text` | **0 files** |

**The silent-grant shape holds for BOTH feeders**: each holds the `pok` grant while none of
its prompt files (`.md` / `.yaml`) mentions POK even once. That is the EXACT shape the
kb-writer rider removed on 2026-11-24 (the grant was live but unmentioned — a grant that
nobody exercises by instruction).

> The dispatcher pre-flight re-verified this at 2026-11-24T10:3xZ; this lane re-verified it
> again at the timestamps above (`grep` over `agents/<agent>/meta.json` + `agents/<agent>/*.md`
> and `*.yaml`).

## 2. The write volume (MEASURED)

### 2.1 The store's own census (branch-verifiable artifact)

Source: `.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv`
(sha256 `6737281cfc7eddfb2f82a1af3317c1560ca465225162b503292a2ddd5d075ab1`) — the
**499-active-row** store, snapshot **2026-11-23 ~12:29Z**. The `created_by_agent` column
(16) gives the creator census:

| author | active rows (of 499) |
|---|---:|
| worker | 148 |
| **kb-writer** | **131** |
| leader | 49 |
| giter | 27 |
| project-manager | 24 |
| tester | 22 |
| developer | 20 |
| coder | 19 |
| skill-keeper / devops | 7 / 7 |
| reviewer / maintenancer | 6 / 6 |
| **kb-importer** | **1** |
| **experiencer** | **0** (absent from the 499) |
| (17 further authors, 1–5 rows each) | 42 |

**The plan's "≤53 combined" bound (the whole long tail) is an UPPER bound, not the feeders'
own volume.** The feeders' OWN measured volume in the 499-active snapshot is
**kb-importer = 1 row, experiencer = 0 rows** — two orders of magnitude below kb-writer's
131.

### 2.2 The DEV store (this lane's own read-only measurement)

`ensemble_dev` @ `127.0.0.1:5432`, read-only session, measured 2026-11-24 ~11:0xZ:

```
SELECT status, count(*) FROM project_operating_knowledge GROUP BY status;
  active 10 | superseded 1          (11 rows total, project 3f401aa9)
SELECT created_by_agent, count(*) ... GROUP BY 1;
  leader 9 | worker 2
```

→ **kb-writer 0, kb-importer 0, experiencer 0** in the DEV store. The DEV store is a
different (small) project than the censused one; it is quoted as the dev-lane's own
measurement, not as a substitute for the census.

### 2.3 The live-store view (a concurrent lane's artifact — cited, not claimed)

`.agents/shared/planning/pok-write-gate/evidence-rederiv-2026-11-24/fig1-method-and-limits.md`
(snapshot **2026-11-24T11:01:01Z**): **kb-writer-authored POK rows = 138**, all created
2026-11-09 → 2026-11-24 (after the 2026-11-07 grant sweep).

## 3. The same-run dual-write mechanism (MEASURED — and the plan's figure is NOT reproducible)

### 3.1 What the concurrent lane re-derived (2026-11-24T11:01:01Z)

From `fig1-method-and-limits.md` (RAG corpus reachable via `POST /documents/paginated`,
workspace `agents_ensemble`; **13,110 docs** reported by the API, vs the plan's cited
12,968 — a +142 delta, the count having been taken at an earlier snapshot):

* the RAG API exposes only `content_summary` (a ~253-char preview) + `content_length` — no
  full text, no content hash ⇒ **the "81 RAG-duplicate POK rows" figure is NOT re-derivable**
  from live data with this method; only **10 rows** are reproducibly classifiable as overlap
  (exact-hash 0, slug-identity 6, jaccard-only 4);
* over those 10: instance-linkage 9, time-proximity (|Δ| ≤ 60 s) 7, **same-instance-same-second
  (|Δ| ≤ 1 s) 2 — both of them kb-writer**;
* per-author overlap: kb-writer 6, maintenancer 2, developer 1, worker 1;
* **neither `experiencer` nor `kb-importer` appears in the overlap set at all**;
* the MECHANISM is confirmed structurally: kb-writer held BOTH grants (`rag_insert_text` +
  `pok`) simultaneously from 2026-11-07 to 2026-11-24 — the `pok` grant was removed by
  this lane's Phase 4 rider (`fd7dc6480`), i.e. **the mechanism is now closed at the source**.

### 3.2 The day-granularity dual-write evidence (`tool_usage_daily`, retention 2026-11-22..24)

Same artifact. Rows where the agent called `pok_upsert` AND `rag_insert_text` on the same
day and project:

```
2026-11-22  kb-importer  a5f20438   pok_upsert 1  / rag_insert_text 13
2026-11-23  kb-importer  a5f20438   pok_upsert 3  / rag_insert_text  1
2026-11-24  kb-importer  83da04de   pok_upsert 2  / rag_insert_text  4
2026-11-24  kb-importer  a5f20438   pok_upsert 4  / rag_insert_text  1
2026-11-22/23/24  kb-writer  83da04de   32/124, 15/66, 16/79
```

**NEW FACT (not in the plan): `kb-importer` DOES dual-write** — it is the only FEEDER with
day-level evidence of writing to both stores. `experiencer` has **no** `pok_upsert` and
**no** `rag_insert_text` in the 3-day retention window. LIMIT (disclosed): the telemetry
window is only 3 days, so this is a lower bound, and it covers two projects.

### 3.3 The plan's figure, stated honestly

The addendum's "65 (80%) of the 81 RAG-DUPLICATE POK rows were written into BOTH stores in
the SAME kb-writer run (±0s)" is **NOT reproducible** from live data with the available
method (§3.1) and has **no in-branch source** — the plan itself flags it as one of the
three "kb-writer trio" figures with no in-branch source (the S-1 hard gate, plan-overview
T12.2). The sibling lane's independent T12.2 record reaches the SAME verdict: the figure
is **not re-stampable**. What IS reproducible: the mechanism (dual grants held
simultaneously) and the 10-row overlap set with its 2 same-second pairs. **The 65-of-81
figure is therefore DISCLOSED as unbacked, not repeated as a finding.**

## 4. The per-lane refusal transition map (T9.2 — the map this assessment carries)

Implemented as `CONTENT_CLASS_ROUTES` + `REFUSAL_FAMILY_CLASSES` in `daemon/tools/pok.py`
(the product surface); the table:

| content class | destination | tool category (the grant-check) |
|---|---|---|
| knowledge (what something IS) | the knowledge base (RAG) | `rag` |
| operational (this project's how-to / rule / trap) | POK | `pok` |
| governance (a curated project contract agents must read) | the critical notes, or the board | `critical_notes`, `board` |
| episode (lane state, a dated count, a fingerprint) | the board, or the shared KV | `board`, `shared_meta_kv` |

The gate's five families map onto the classes: `boundary` → knowledge, `reason` /
`redundancy` / `scope` → operational, `durability` → episode. A refusal envelope now names
its route, its destination and whether that destination is **reachable** by the caller
(`destination_available`), and a refused write whose destination is unreachable is **parked**
(never dropped).

## 5. The assessment (what the evidence says)

1. **The grant posture is the same for both feeders as it was for kb-writer**: the `pok`
   grant is held, the prompt never mentions it, and the write volume is minimal. The
   kb-writer precedent (grant removed, capture duty moved to the operational agents) is
   the closest structural analogue.
2. **The volume is genuinely long-tail** — but the plan's "≤53 combined" is the whole
   long tail's size, not the feeders' own: measured **kb-importer = 1** active row and
   **experiencer = 0** active rows in the 499-active census @2026-11-23 12:29Z, versus
   kb-writer's **131** (and 138 in the live view @2026-11-24T11:01Z).
3. **The dual-write mechanism is real but CONCENTRATED**: only kb-writer shows the
   same-second dual-writes (2 of 10 overlap rows), and the mechanism was closed at the
   source on 2026-11-24 (the grant removal). `kb-importer` shows day-level dual-writes
   (4 day-rows in the 3-day window) at a low volume; `experiencer` shows none.

## 6. The RECOMMENDATION (the Emperor decides — no change was made)

**RECOMMEND: remove the `pok` grant from BOTH feeders (`experiencer`, `kb-importer`),
mirroring the kb-writer rider — but treat the two cases DIFFERENTLY, and do it as ONE
sanctioned change with the capture duty already migrated.**

*Why the recommendation is "remove":* the grant is unexercised by instruction (0 prompt
mentions), the volume is negligible (1 and 0 active rows), and the kb-writer precedent
established both the mechanism (dual grants ⇒ dual writes) and the remedy (the grant
leaves; the capture duty belongs to the operational agents). The `pok` grant on a
knowledge writer is the very shape the write gate exists to prevent.

*Why the two cases differ:* `kb-importer` demonstrably DOES write to POK (4 day-rows of
`pok_upsert` in the 3-day telemetry window; 1 active row in the census) — it is a
low-volume writer, not a non-writer. `experiencer` shows NO POK write at all in the
measured window. If the Emperor wants ONE step, `experiencer` is the clean case (remove
the grant, zero operational loss) and `kb-importer` is the case that needs a look at
whether its POK writes are wanted (they are few, but they exist).

*What this assessment does NOT do:* it changes no grant, edits no meta.json, and proposes
no tool-category change. The decision is the Emperor's (parked follow-up **P3** — "Feeder
grant changes (experiencer/kb-importer) — Emperor decides on assessment (Phase 9)"), and
the change would ride the same rail as the kb-writer rider: the grant census pin
(`tests/unit/test_pok_grant_pins.py`, 53 explicit grants) + a resolved-tool-set re-pin +
the routing/capture-duty guidance in the operational agents' prompts.

## 7. Provenance

| artifact | what it carries |
|---|---|
| `evidence-pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv` (sha256 `6737281c…`) | the 499-active creator census @2026-11-23 12:29Z — the feeders' own volume |
| `evidence-rederiv-2026-11-24/fig1-method-and-limits.md` (snapshot 2026-11-24T11:01:01Z) | the RAG inventory (13,110 docs), the 10-row overlap set, the same-run legs, the dual-grant history, the day-level dual-write table — **a sibling lane's artifact, now COMMITTED in-branch (`c134df2f3`)** |
| `evidence-rederiv-2026-11-24/kb-writer-trio.md` (the T12.2 hard-gate re-derivation record, sibling lane) | the independent restatement of the trio: the "65 of 81 (±0s)" figure is **NOT RE-STAMPABLE** — the live lower bound is a 10-row overlap set (kb-writer 6), of which 2 fall within ±1 s. **This record AGREES with this assessment's stance** (§3.3) |
| `evidence-rederiv-2026-11-24/fig2-census-extract.md` (2026-11-24T10:51Z) | the census-artifact inventory + the label vocabulary (the 83.5% figure re-derived as 66/79 = 83.54% under a stated mechanical rule) |
| this lane's DEV read (2026-11-24 ~11:0xZ) | the dev-store author census (leader 9 / worker 2) |

**NOTE on the sibling artifact:** `evidence-rederiv-2026-11-24/` was written into this
worktree by a sibling lane WHILE this lane worked (files timestamped 10:57–11:01Z) and
landed in-branch as `c134df2f3` (the sibling lane's own commit — this lane did NOT commit
it). This assessment cites it with its snapshot time; the figures it carries are the
sibling lane's, and §3.3's stance (the "65 of 81" figure is unbacked) is corroborated by
their independent `kb-writer-trio.md` record.

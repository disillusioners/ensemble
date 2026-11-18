# POK cap 256→512 + curator slug→row resolver — TEST + OPERATIONAL VERIFICATION GATE

**Date:** 2026-11-18 · **Lane:** `feature/pok-cap-curator-resolver` @ HEAD `47ecca4c6e74126ef9ae4d0cd51daeaf7cb478b9`, base `eca98397d9e0109611764ac418132c9d35afde2c` (11 commits)
**Verdict: ✅ PASS — all six commissioned tasks green; net-new unit regressions = 0; cap boundary exact on both code versions; LIVE admission probe accepted at the saturated store and restored byte-identical; resolver 4/4 MATCH read-only.**

Workers: recon `e46cbcb2` · T1+T2 `3d2b13a1` · T3 `a03bd4a7` · T4 `dd504ff1` · T5 `bcef005d` · T6 `31ca46b3`. Evidence root: `/tmp/pokcap_gate/` (63 files, 28 logs).

---

## Scope Decision

Tier 1 (blast-radius), **not** whole-tree: the diff touches `daemon/services/pok_service.py`, `daemon/repositories/pok/repository.py`, `daemon/tools/pok.py`, 2 unit test files, docs/planning only — **no core-seam path** (`graph.py`/`manager.py`/`api.py`/`instance_messaging.py`/`task_processor.py`/`job_queue_service.py`). No schema change: `models.py` + `daemon/migrations/versions/` are **byte-identical base↔HEAD**; 512 is one Python constant (`pok_service.py:104`). Scoped set = the six registered POK unit packs + the PG pack + the commissioned T3/T4/T5 operational legs.

**ensure.md Core (scoped):** changed-packs PASS ✅ (6/6 green, both legs) · `dev.sh --timeout-graceful-shutdown 10` ✅ (static grep, `dev.sh:159`) · concurrency pack **scoped out** — the lane adds no async/concurrency surface (service is sync; "zero new awaits" pinned inside `pok_trust_tier_unit`, green); no deadlock-relevant code touched. No Release Gate (not a release/architecture change).

---

## T1 — Unit red-set diff (six packs × both legs): **PASS, net-new = 0**

Identical registered commands, byte-identical across legs; only cwd/tree differs (HEAD = worktree, BASE = `/tmp/pokcap-base` detached @ `eca98397d`, own venv, prefix verified).

| Pack | HEAD | BASE | net-new | base-only |
|---|---|---|---|---|
| `pok_service_unit` | **57/57** | 49/49 | ∅ | ∅ |
| `pok_injection_unit` | **42/42** | 42/42 | ∅ | ∅ |
| `pok_ari_seam_unit` | **13/13** | 13/13 | ∅ | ∅ |
| `pok_tools_unit` | **24/24** | 22/22 | ∅ | ∅ |
| `pok_metadata_cap_unit` | **23/23** | 23/23 | ∅ | ∅ |
| `pok_trust_tier_unit` | **40/40** | 40/40 | ∅ | ∅ |

Count deltas are exactly the commissioned new legs, `--collect-only`-verified: P1 +8 = `TestSlugOwnerResolution`; P4 +2 = `TestTypedEchoTaxonomy::test_curate_reaches_the_owner_row_of_a_lineage` + `::test_curate_unknown_slug_surfaces_the_not_found_code`. Dual-layer timeout present in both trees (`timeout 300` + pytest-timeout 30s/thread). Logs: `/tmp/pokcap_t1t2/*.log` (12 files).

## T2 — Injection budget non-movement: **PASS**

`test_pok_injection_unit.py` 42/42 and `test_pok_metadata_cap_unit.py` 23/23 at HEAD (same at base) — `POK_BLOCK_MAX_ENTRIES=8` / 2900-char block cap and the metadata allowlist pins are untouched by the lane.

## T3 — CAP BOUNDARY PROOF (scratch PG, both code versions, service-layer writes): **PASS**

DB `ensemble_dev_scratch_pokcap_cap` (local cluster, owner `ensemble`), identical harness both legs (md5 `04bf0658…`), probes = genuine `POKService.upsert()` calls; bulk seeding via SQLAlchemy core (disclosed); N verified by COUNT immediately before each probe; in-harness assert `"10.44.0.2" not in DB_URL`.

| Leg | cap | N before | Expected | Measured | Active after |
|---|---|---|---|---|---|
| BASE `eca98397d` | 256 | 254 | ACCEPTED | **ACCEPTED** (`created`) | 255 |
| BASE | 256 | 255 | ACCEPTED | **ACCEPTED** (lands 256th) | 256 |
| BASE | 256 | 256 | REFUSED | **REFUSED `pok_active_cap`** | 256 |
| HEAD `47ecca4c6` | 512 | 510 | ACCEPTED | **ACCEPTED** | 511 |
| HEAD | 512 | 511 | ACCEPTED | **ACCEPTED** (lands 512th) | 512 |
| HEAD | 512 | 512 | REFUSED | **REFUSED `pok_active_cap`** + ACTIVE cure + `pok_curate` | 512 |

Rule confirmed exactly `accept while active_count < cap; refuse at >= cap` — no off-by-one either direction. Anti-vacuity seed: 20 archived + 30 superseded rows dated **2020** (older than every active row) — a naive "oldest/any row" cure pick would have named a dead row; it did not (`oldest_least_verified_active` filters `status='active' AND NOT pinned`).

**Cure-target verification (HEAD N=512):** message names `'bulkactive-0001'` inline == `details['oldest_least_verified']`; row by id `8fb5a565…` re-fetched **status='active'**; literal `pok_curate` present; the "archive a superseded fact" wording is **gone** (BASE: present).

**Before/after refusal text (verbatim):**
- BASE: `…CURABLE: archive a superseded fact via pok_curate` — advertises a **dead-class remedy** (superseded rows are curated via their successor ⇒ un-actionable), names no slug in text.
- HEAD: `…CURABLE: archive the named ACTIVE entry 'bulkactive-0001' via pok_curate, by slug — the slug resolves to that ACTIVE row`.

**Supplementary (lineage cure, synthetic project, both legs):** with a superseded ancestor + active successor on the cure slug, BASE's `get_by_slug(status=None)` returned the **dead ancestor** ⇒ `curate(archived=True)` refused `pok_not_found` ⇒ cap still locked; HEAD's `resolve_slug_row(scope='owner')` reached the successor ⇒ archived ⇒ cured. **This is the resolver fix proven on the CURATE path, not just reads.**

## T4 — LIVE-STORE ADMISSION PROBE: **SUCCESS** (the commission's explicit demand)

Run from the **isolated** tree `/tmp/pokcap-head` (detached @ `47ecca4c6`, own venv, clean; cap 512 + resolver import-verified). Live DSN resolved from `/home/nea/agents-ensemble/.env` (never printed); identity gate: `current_database()=ensemble_prod`, `current_user=ensemble`, **PostgreSQL 18.4** (local cluster is 16.x — not conflated).

1. Pre-count: **active 256 / archived 8 / superseded 54** (318) — *256 is exactly the OLD cap; the store is saturated at the old boundary.*
2. Probe-slug absence verified (0 rows) → upsert via the NEW service path: **action=`created`**, row **`b0031789-80c6-40a2-8b9d-125f28e00d54`**, active, `pok-probe-admission-20261118`, @ 02:42:09Z. Active → **257**. **Accepted at 256/256-old-cap — the exact state where the old rule refuses (mechanically proven at T3-BASE; the live daemon refused a real `pok_upsert` with `pok_active_cap` at 256/256 earlier today).**
3. Verified active by id → cure via the NEW slug-keyed curate path (`agent_id='leader'`) → **archived, attempt 1**.
4. Zero collateral: active count **restored 256**; before/after active-id sets **sha256-identical** (`d614c798da85a0ce5ac938ca25d66434fc7f58ac8d880439f39c20e424e65487` both), comm-diff empty; per-status after 256/9/54 — **total 319 = +1, exactly the probe row persisting as archived** (see Deviation D1); the 9 T5-recorded slugs re-read — **zero drift**. Secret scan over `/tmp/pokcap_gate`: **0 hits**. No DDL, no daemon, no promote surface.

## T5 — RESOLVER LIVE EVIDENCE (read-only): **PASS 4/4 MATCH**

NEW `resolve_slug_row` (scope=owner) against live; 3-layer read-only hardening (`default_transaction_read_only=on` + statement_timeout + keyword audit — zero write statements).

| slug | resolved (NEW) | expected | OLD naive returned |
|---|---|---|---|
| decision-client-contract-invariant-185-v187 | `31162c8d…` active | ✅ | `8a35389f…` **superseded** |
| staged-dirs-v0309-inventory | `5b20d029…` active | ✅ | `f1aaaa56…` **superseded** |
| wt-merge-v02917-stale-index-ref-move | `6b8398a2…` active | ✅ | `33485c57…` **superseded** |
| auth-lane-scrub-ensemble-auth-env | `74245f23…` active | ✅ | `a1ef2053…` **superseded** |

Consequence reproduced on all 4: OLD ⇒ `pok_not_found` on every curate of the live successor. PM-KEPT statuses recorded unchanged (`sealmap-03` = **no row** — factual; `sqlmodel-select-import-gotcha` = **archived** and IS the owner per active>archived>superseded precedence). Zero-mutation: before/after 256-id sets sha256-identical. Tie-break branch proven on throwaway SQLite (6 legs) — see D2.

## T6 — PG tables pack: **PASS 10/10** (exit 0, 15.7s)

Registered `pok_pg_materialization` invocation + PG_TEST_* to the fenced scratch DB; import provenance worktree-local, **cap constant 512 confirmed**; both timeout layers active. All 10 nodes observed green (tables materialize, 21-column set, `.sql`↔PG agreement, 5 CHECK vocabularies present **and firing**, `uq_pok_active_slug` binding, zero FKs). CWD-relative `.sql` read confirmed under the worktree.

---

## Deviations & premise corrections (all classified; nothing silently skipped)

- **D1 · DEVIATION-FROM-COMMISSION (wording) · BENIGN** — §4c's "NO other row touched / total unchanged" cannot hold literally: an inserted-then-archived probe row necessarily persists. The invariant that matters (active set/count byte-identical, exactly one row's status changed) holds. Total 318→319 = the probe row.
- **D2 · PREMISE CORRECTION (mine)** — the commission's (and my dispatch's) "two active rows for `decision-client-contract-invariant-185-v187`" was **false**: `1ecd9402…` belongs to project `54debc61…` (llm-supervisor-proxy). Root cause: the tester's recon query omitted the `project_id` filter. Live dual-active slugs on `83da04de…` = 0 (`uq_pok_active_slug` enforced). Tie-break proven on SQLite instead.
- **D3 · PREMISE CORRECTION** — live active count is **256**, not "~253-254": exactly the old cap. Strengthens T4 (the probe is genuinely discriminating).
- **D4 · OPEN EVIDENCE ITEM (honest gap)** — no live **plan flip** was observed (3 forced plan shapes returned the same naive row at 314 rows). The old defect's plan-*dependence* stands argued from code (no ORDER BY) + heap order, not from a measured flip. The superseded-ancestor consequence itself IS measured on all 4 slugs.
- **D5 · RCA refinement** — the OLD path's pick is **heap/ctid order**, not "oldest"; "oldest" was coincidence. Supersedes "oldest row" phrasing in any future retelling.
- **D6 · MATERIAL NUANCE on 7b6d4a646's narrative** — the "hard lock at cap" is **lineage-scoped, not universal**: measured at T3, BASE's cure succeeds when the named cure slug has a single generation; the lock reproduces only when the cure slug carries ≥2 rows (ancestor+successor). Fix remains correct and necessary — real corpora are lineage-heavy — but the failure mode is conditional.
- **D7 · recon §0 "concurrent mutation" = FALSE POSITIVE (filesystem-adjudicated)** — claimed `MUTATION M2` planted mid-gate; disproven: no source file written after the lane's own commit (01:55:40Z), `repository.py`/`pok.py` mtimes 01:54:17/01:54:36 (pre-gate), marker absent from all ~130 worktrees, canonical checkout is legitimately pre-fix (untouched since 2026-11-14). Likely mechanism: CWD-default trap reading the canonical/base (pre-fix) file. Gate-window evidence (T1/T3/T5/T6) intact; T4 additionally ran from an isolated tree.
- **D8 · fence trap (environment)** — `ensemble_dev_app` owns live-shaped `ensemble_dev` ⇒ the PG-lane ownership rejection REFUSES `ensemble_dev_app`-owned scratch DBs (incl. the obvious-looking `ensemble_dev_scratch`). Both lane DBs were created owned by `ensemble` (owns 0 forbidden DBs) and dropped post-gate.

## Gaps / open risks

- **None unrun.** Six commissioned tasks, all executed to measured outcomes. The two non-producible observations (live plan flip D4; live dual-active tie-break D2) are stated as unproven with named substitute evidence, not skipped.
- Live daemon still runs OLD code (v0.31.0, cap 256) until the Emperor's promote — every live statement here says so honestly: T4/T5 prove **the new code against the real store**, not the live daemon's config.

## Housekeeping

Scratch DBs `ensemble_dev_scratch_pokcap(_cap)` **dropped** (0 remain). Worktrees `/tmp/pokcap-base`, `/tmp/pokcap-head` left in place (evidence re-runs; `/tmp`-ephemeral). Live store final state: 256 active / 9 archived / 54 superseded / 319 total — probe row archived, nothing else touched.

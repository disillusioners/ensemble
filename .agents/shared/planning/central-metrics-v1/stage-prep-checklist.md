# Stage-prep checklist — central-metrics-v1

> **Date:** 2026-11-02 (UTC) · **Lane:** `feature/central-metrics-v1` (worktree
> `/home/nea/ensemble-src-wt-central-metrics-v1`) · **Phase 6 task 6.7**.
> **Status:** **COMPLETE except version + promote — both are handed OVER, not performed.**
> Every item below was **verified in this phase** (the command that verified it is named), not
> asserted.

**The two hand-overs.** This lane mints **no version** (W9: giter derives it at stage time) and
performs **no promote** (C-7: the user's 3-factor nonce ceremony owns it). Both boxes below are
therefore **deliberately unticked** — they are the lane's gift to the next actor, not an omission.

---

## 1. The three EMPTY deltas

### 1.1 The migration delta — **EMPTY** ✅

| Check | Result |
|---|---|
| `git diff 941cb6321..HEAD -- daemon/migrations/versions/` | **EMPTY** (no file added, changed or removed) |
| the migration file count | **119 → 119** (base 119, HEAD 119) |
| the lane-authored `.sql` files | **ZERO** |

**Why it is empty:** the lane adds **no SQLModel table** and changes no column. The query
substrate (provider · ring · sampler · wiring) is **in-RAM**; the new census-backed families read
the **existing** resource-monitor spine tables. There is nothing to migrate.

### 1.2 The backend dependency delta — **EMPTY** ✅

| Check | Result |
|---|---|
| `git diff 941cb6321..HEAD -- pyproject.toml` | **EMPTY** |
| `git diff 941cb6321..HEAD -- uv.lock` | **EMPTY** |
| `git diff 941cb6321..HEAD -- daemon/requirements.txt requirements.txt` | **EMPTY** |

**Why it is empty:** the trend buffer is **stdlib-only** — the array-backed ring uses `array`
from the standard library, the exposition is hand-rendered Prometheus classic text, and the
query door uses the daemon's **existing** FastAPI/auth/DB plumbing. **No new backend dependency
is added** (and no `prometheus_client` / OTel / statsd / psutil is pulled in — the registry was
already stdlib-only).

### 1.3 The npm dependency delta — **EMPTY** ✅

| Check | Result |
|---|---|
| `git diff 941cb6321..HEAD -- frontend/package.json` | **EMPTY** (0 lines changed) |
| `git diff 941cb6321..HEAD -- frontend/package-lock.json` | **EMPTY** |

**Why it is empty:** the FE charts page is drawn by a **hand-rolled SVG primitive** — no charting
library, no date library, no state library. The scale/axis functions are **pure and exported**
(the house `svg-timeline` pattern) and are unit-assertable without a dependency.

---

## 2. The vendored inventory — present, with provenance ✅

| Check | Result |
|---|---|
| the vendored files exist | **4** — `PROVENANCE.md` · `README.md` · `metrics-inventory-backend-producers.md` · `metrics-inventory-surfaces-fe-deps.md` |
| the provenance header on each vendored copy | **present** (an 8-line HTML-comment header: source path · source status · evidence ref `origin/latest@62627265f` · vendoring date) |
| the bodies are byte-identical to the sources | **YES, all three** (independently recomputed sha256 — the source sha256 and the vendored-body sha256 are equal per file) |
| the sha256 table + the verification recipe | in `inventory-vendor/PROVENANCE.md` |
| the source is still untracked and unmodified | **YES** — `git status --porcelain` still reports `??` for all three; `git log --all` over the directory returns **0 commits**; the source mtimes (2026-11-01 19:46–19:59Z) are **older** than the vendoring (2026-11-02 00:39Z) |
| the shared main checkout was modified | **NO** — nothing was created, modified or deleted there |

**The size discrepancy, resolved.** The vendored copies are **larger** than their sources by
**exactly the 8-line provenance header** (+743 / +772 / +771 bytes) — the transformation is
**prepend-only** and the bodies are byte-identical. **No re-copy was needed and none was made.**
The full verification is in `inventory-vendor/PROVENANCE.md` (§"Verification round 2").

---

## 3. The FE-manifest delta — **DECLARED** ✅

| Field | Value |
|---|---|
| what the staged `frontend` tree is | the **BUILT** bundle (`frontend/dist/frontend/browser`), not the source tree — the chunk names are content-hashed, so any source change renames the whole chunk set |
| the `frontend_manifest` entry count | **93 → 95** (+2 `chunk-*.js`: the metrics page's lazy chunk — **25 938 B** in the local production build, carrying the `metrics-empty-panel` marker — plus one shared chunk split by the new import graph) |
| `frontend_tree_sha256` | **CHANGES** (recomputed at stage time — it cannot be predicted pre-stage) |
| `fe_index_sha256` | **CHANGES** (`index.html` references the new `main-*.js` hash) |
| the per-file map | a full chunk-hash cascade — **83 added / 81 removed / 1 changed** against the v0.28.5 staged manifest. **The cascade is the hash naming, NOT a payload change** |
| `agents/` and `plugins/` trees | **UNCHANGED** by this phase |

> **⚠ AS-OF `c8310a89b` — RE-DERIVE AT STAGE TIME.** The figures in this table
> (**93 → 95**, the **25 938 B** metrics lazy chunk, the **83 added / 81 removed / 1 changed**
> chunk-hash cascade, and the `frontend_tree_sha256` / `fe_index_sha256` changes) were measured at
> the **Phase-6 close-out** commit **`c8310a89b`**. **The FE surface has changed SINCE** — the
> fix-cycle 2 FE commit (**`cd06d2a0b`**: the W-6 declared/concrete label-type split, the A-5
> door-error copy branches, the W-2/W-3/W-4 truncated + cursor mirror, the A-7 harness restore +
> pre-run manifest + interruption pins) is part of the FE surface, and the A-6 rename
> (**`cf12edcfb`**) is **daemon-side** and does not touch the FE tree.
> **Therefore: every FE-manifest number in this section is STALE and MUST be re-derived at stage
> time** — the `frontend_manifest` entry count, the chunk set and its sizes, the per-file
> chunk-hash cascade, `frontend_tree_sha256` and `fe_index_sha256`. **Do not promote against these
> figures**; the stage-time re-derivation supersedes them. The `agents/` and `plugins/` trees are
> re-checked at the same time (the fix cycles touched neither, but the re-derivation must confirm
> it rather than assume it).
>
> **What is NOT stale:** the three EMPTY deltas of §1 (migration · backend dependency · npm
> dependency) — they are **diff-based**, not figure-based, and the fix cycles added no `.sql`, no
> backend dependency and no npm dependency. Re-confirm them at stage time by the same diff, but
> they are not affected by the FE change.

**The build legs.** dev AOT **exit 0**; production **exit 0** with **10 budget WARNINGS** — every
one **pre-existing** and in **another component's SCSS**; **none** of the lane's own components is
over the 8 kB warn budget. They are **recorded findings, never auto-fixed**.

**The live count — one ceremony-time observation.** At the staged boot smoke, read
`/api/metrics/families` **ONCE** from the staged artefact and record the **live** count in the
disclosure before the flip (**ceremony-time observation; owner: the staged-verification lane**).
The disclosure carries the **measured closed label space (425)** as-is until then — the live total
is observation-dependent and is not asserted by the lane (the running daemons do not carry the
lane).

---

## 4. The live surface — **UNTOUCHED** by this lane ✅

| Check | Result |
|---|---|
| the live release symlink | `current -> releases/v0.28.4` (mtime 2026-11-01 22:31) — **unchanged by this lane** |
| the promote journal's promote fields | `current = v0.28.4` · `previous = v0.28.3` · `in_flight = None` · `pending_op = None` · `quarantined = ['v0.28.5']` — **intact** |
| the live daemon | `{"status":"alive", …, "version":"0.28.4"}` — **uptime-continuous** (no restart by this lane) |
| the lane's commit surface | **the repository only** — the lane's diff touches `daemon/`, `frontend/src/`, `tests/`, `docs/` and the planning directories; it writes **nothing** under the install directory |
| the journal's recent rows | the only writes since the lane started are the live daemon's **own** `mcp_server_state` rows (the `context7` init/quarantine cycle — the C-part machinery working live). A grep of the journal for `central-metrics` / `metrics_query` returns **0 hits** ⇒ **the lane left no trace in the promote journal** |

---

## 5. The plan-file manifest — matches the tree ✅

The plan set's manifest (`plan-overview.md` §Plan-file manifest) was **refreshed** in this phase to
list exactly the files that exist — including the new artefacts this phase produced:
`evidence-pack.md`, the `evidence/` directory, and the three published docs under `docs/architecture/`.

---

## 6. The two hand-overs — **NOT performed, by design** ⬜

| Item | Status | Owner |
|---|---|---|
| **The version slot** | ⬜ **NOT chosen.** No version string for this lane appears anywhere in the plan set or in the published docs (W9; P-R12). | **giter** — derives it at stage time |
| **The live promote** | ⬜ **NOT performed.** This lane stages nothing, promotes nothing, and touches no live surface. | **the user** — the 3-factor nonce ceremony |

---

## 7. The gate this checklist feeds

| Item | Value |
|---|---|
| the lane's own gate | **P6-G1** — Tier **0** (a docs-only diff → changed-pack PASS) |
| the registered pack id | `central_metrics_ledger_reconciliation` (declared by `phase6-plan.md`) |
| **who registers it** | **the tester lane** — `.agents/tester/PACKS.md` is the **tester's** file, and this lane is forbidden from touching `.agents/tester/**`. The pack's **registration and execution** are therefore the tester lane's to make. |
| what this phase produced for it | the **docs** the pack asserts against (the catalog's §8.10 / §14 values reconciled with the shipped runtime, the published docs, the disclosure text, the evidence pack) |
| the declared node count | ~12 (a projection — the tester lane re-derives it with `--collect-only -q`) |
| the declared mutations | `P6-M1…P6-M12` (12) — each with its RED node in `phase6-plan.md` |

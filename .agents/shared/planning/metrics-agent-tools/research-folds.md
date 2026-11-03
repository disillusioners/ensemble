# Research findings — metrics-agent-tools (fold research)

**Lane:** `feature/metrics-agent-tools` · **Worktree:** `/home/nea/ensemble-src-wt-metrics-tools`
**Base:** `origin/latest` @ `03a2297c3180e79c6b5c26a92b2caca7c0452725` (per shared-meta-KV `wt.active.feature/metrics-agent-tools.metrics-agent-tools`)
**Read-only investigation.** Nothing was written outside this directory; nothing was committed; no daemon was run; no live/install path was touched.
**Upstream lane:** central-metrics-v1 (lane #1, v0.28.6) — plan set at `.agents/shared/planning/central-metrics-v1/`.

> **Tooling note for the planner:** `glob_files` / `grep_files` in this environment are bounded to the *main* checkout unless an explicit `workdir` is passed. Every citation below was read from the **worktree** via `workdir=/home/nea/ensemble-src-wt-metrics-tools`. `grep_files` with a **file** path returns nothing (use a directory path + `include=`), which is why several searches below are directory-scoped.

---

## 0. Headline answers

| Question | Answer |
|---|---|
| Q1 — is the FE `registry_read_ok` mirror a real gap? | **YES, confirmed.** BE publishes 4 activation members; the FE mirror declares 3; a failed registry read currently renders as the **empty-catalog** panel. Full detail §1. |
| Q2 — the six tidier Mediums (items 3, 5, 10, 16, 17, 21) | **NOT RESOLVABLE from the repo.** The tidier's per-item list does not exist in the tree. Only item **(a) the FE `registry_read_ok` mirror** is resolvable (§2). |
| Q3 — verification conventions | Fully mapped: 9 declared gate-pack ids, 15 D1 rows over `tests/unit/metrics/`, 2 in-tree gate scripts, the FE e2e pack, the ND-5 mutation row, the DERIVED blast-radius composition rule (§3). |
| Q4 — exclusion boundary | The **verbatim** routed list is captured (§4). `fe_registry_read_ok_mirror` is **IN-THIS-LANE (fold in)** — quoted evidence. |
| Q5 — lane #1 plan-set shape | 22-file plan set; `plan-overview.md` + `phase1..6-plan.md` + `decisions.md` + `fold-ledger.md` + `requirements.md` + `research-findings.md` + `evidence-pack.md` + `stage-prep-checklist.md` (§5). |

---

## 1. Q1 — FOLD (a): the FE `registry_read_ok` mirror

### 1.1 The BE publishes FOUR activation members; the FE mirror declares THREE

**BE — the 4-member `activation` block** — `daemon/services/metrics_query/envelope.py:56-71`:

```python
def activation_block(provider: MetricsQueryProvider) -> dict[str, Any]:
    """The ``activation`` block — the posture of the three surfaces.

    ``registry_read_ok`` is the DEGRADED flag (H5): it is ``False`` only
    when the provider's registry read raised.  A failed read answers
    ``200`` with an EMPTY body — byte-identical to a legitimately empty
    registry — so without this flag a client cannot tell the two apart.
    It is ADDITIVE and DEFAULT-SAFE: a provider that does not carry the
    fact (a duck-typed fake, any pre-H5 provider) reads as ``True``.
    """
    return {
        "listener_enabled": bool(getattr(provider, "listener_enabled", False)),
        "census_enabled": bool(getattr(provider, "census_enabled", False)),
        "query_api": "auth-gated-always-on",
        "registry_read_ok": bool(getattr(provider, "registry_read_ok", True)),
    }
```

- 4th member = **`registry_read_ok`** (`envelope.py:70`). It is emitted on **all three routes** — the block is spread into every envelope by `envelope_header()` (`envelope.py:74-82`), which is called by `build_families_envelope` (`:85-106`), `build_snapshot_envelope` (`:109-132`) and `build_series_envelope` (`:154+`).
- **Where it flips to `False`:** `daemon/services/metrics_query/provider.py:152-160` — the provider's `_metrics()` helper. A raising registry read is swallowed (`except Exception`), sets `self._registry_read_ok = False` (`:156`), logs `"metrics query provider: registry read failed"` (`:157`) and returns `[]` (`:158`). A successful read sets it back to `True` (`:159`).
- **Provider property:** `provider.py:99-107` (`registry_read_ok` → `self._registry_read_ok`); initialised `True` at `provider.py:83`.
- **BE-side pin that already exists (do not break it):** `tests/unit/metrics/test_metrics_query_provider.py:647-663` — `test_the_activation_block_names_the_three_surfaces` asserts the **exact 4-member set** (`set(activation) == {listener_enabled, census_enabled, query_api, registry_read_ok}`) and `activation["registry_read_ok"] is True`. Its docstring records the AM-7 additive-member rule: *"an additive change (a new optional field) does NOT bump `schema_version` and consumers must ignore unknown members."*
- A second BE pin: `tests/unit/metrics/test_metrics_query_routes.py:176` — a route-level fixture carrying `"registry_read_ok": True`.

**FE — the 3-member mirror** — `frontend/src/app/models/metrics.model.ts:111-119`:

```ts
/** The posture block — the same on all three routes (AM-18). */
export interface MetricsActivation {
  /** Process fact: the exposition socket is bound. */
  readonly listener_enabled: boolean;
  /** `not census.gated` — the honest switch-derived bit. */
  readonly census_enabled: boolean;
  /** The door's own posture — always `auth-gated-always-on`. */
  readonly query_api: string;
}
```

The FE envelope carries it at `frontend/src/app/models/metrics.model.ts:129-131` (`restart_epoch` + `readonly activation: MetricsActivation;` inside `MetricsEnvelope`).

### 1.2 How a failed registry read renders TODAY (the empty-catalog bug)

The FE shell **never reads `payload.activation`** — the load handler copies only `families` and `truncated`:

- `frontend/src/app/pages/metrics/metrics.component.ts:186-216` — `loadCatalog()`:
  - success branch `:192-203` → `this.families.set(payload.families)` (`:196`), `catalogError.set(null)` (`:197`), `catalogStale.set(false)` (`:198`), `catalogLoaded.set(true)` (`:199`), `catalogTruncated.set(payload.truncated === true)` (`:202`).
  - error branch `:204-214` → `catalogError.set(decodeMetricsDoorError(err).copy)` (`:211`), `catalogStale.set(this.families().length > 0)` (`:212`), `catalogLoaded.set(true)` (`:213`).
  - **Neither branch reads `payload.activation`.**
- The empty panel's ONLY condition — `metrics.component.ts:127-129`:
  ```ts
  readonly showEmptyPanel = computed(
    () => this.catalogLoaded() && this.catalogError() === null && !this.anyFamilyLive(),
  );
  ```
  → with a failed registry read the door answers **200 + empty `families`** ⇒ `catalogError === null`, `anyFamilyLive === false` ⇒ **the empty panel renders**.
- Rendering: `frontend/src/app/pages/metrics/metrics.component.html:33-41` → `<section class="metrics-empty" data-test="metrics-empty-panel">` with `METRICS_EMPTY_PANEL_COPY = 'No metrics available yet'` (`metrics.component.ts:42-49`).
- The Families tab's own empty line: `frontend/src/app/pages/metrics/families/families-tab.component.ts:90-92` → `<p class="tab-hint" data-test="families-empty">No families are registered.</p>` — **also** indistinguishable from a legitimately empty registry.
- **Net effect:** a failed registry read is byte-for-byte indistinguishable, in the UI, from "no families registered yet". The BE tells the truth in the envelope; the FE discards it.

### 1.3 The FE's EXISTING pattern for "explicit failure" vs "empty dataset" (name it — it exists)

The metrics page **already has** a three-way state vocabulary, and the failure arm is the pattern to reuse:

| State | Signal | Where |
|---|---|---|
| **Failure** (door unreachable / refused) | `catalogError` → banner `data-test="metrics-catalog-error"` + a Retry button | `metrics.component.html:9-21`; set at `metrics.component.ts:211` |
| **Stale** (last-good catalog retained) | `catalogStale` → badge `data-test="metrics-catalog-stale"` | `metrics.component.html:23-25`; set at `metrics.component.ts:212` |
| **Bounded page** | `catalogTruncated` → hint `data-test="metrics-catalog-truncated"` | `metrics.component.html:27-31`; set at `metrics.component.ts:202` |
| **Empty (legitimate)** | `showEmptyPanel` → panel `data-test="metrics-empty-panel"` | `metrics.component.html:33-41`; computed at `metrics.component.ts:127-129` |

The copy vocabulary lives in the model (dependency-free, no Angular import) — `frontend/src/app/models/metrics.model.ts:335-411`:
- `MetricsDoorErrorCode` — a **CLOSED** union `'scope_insufficient' | 'auth_not_configured' | 'registry_unavailable' | 'network' | 'unexpected'` (`:345-350`).
- `METRICS_DOOR_ERROR_COPY` — the per-code human copy map (`:352-362`).
- `decodeMetricsDoorError(err)` (`:385-411`) — the body-first decode; the named house precedent is `mcps-gateway.service.ts` `decodeError` (cited in the docstring at `:377-380`).

**The gap in that vocabulary:** `registry_unavailable` (`:358-359`) means *"The metrics door has no provider wired — the metrics service is unavailable on this daemon."* — that is the **503** case (a provider that was never constructed, `daemon/api.py:2540-2543` sets `app.state.metrics_query = None` on boot failure). A **registry READ failure** (provider present, read raised) is a *different* fact and must not wear that copy.

### 1.4 The frozen contract doc — what it does and does not say (a planning constraint)

- The **frozen** activation shape in the contract doc lists **THREE** members: `docs/architecture/metrics-query-contract.md:104-106`
  ```
  activation: { listener_enabled: bool,              # process fact — the exposition socket is bound
                census_enabled: bool,                # = not census.gated (AM-18; honest)
                query_api: "auth-gated-always-on" }  # literal — the door's own posture
  ```
  ⇒ the BE's 4th member is an **additive** member under the contract's AM-7 bump policy (no `schema_version` bump). The tidier records this as *"Contract-compliant per §4"* (`.agents/tidier/notes.md:760`).
- The **frozen family shape** is at `metrics-query-contract.md:122-130` (§4.3) — the single source for the FE model mirror.
- **The FE model spec's field-set pin parses §4.3 (the FAMILY shape), NOT the activation block** — `frontend/src/app/models/metrics.model.spec.ts:1-45` (P5-M5: *"The expectation is NOT a hand-written copy of the field list: it is PARSED OUT OF THE FROZEN CONTRACT DOC"*), with `splitTopLevel()` (`:44-45`), the family-field parse (`:93`) and the per-name field parse (`:102-105`). ⇒ **adding a 4th activation member does NOT break the P5-M5 pin.**
- **But** the contract doc is itself frozen-and-pinned by `tests/unit/metrics/test_central_metrics_contract_doc.py` (see §3.6) ⇒ if the lane wants the contract doc to *document* `registry_read_ok`, that doc pin's expectations must be extended in the same commit (the doc-pack is Tier-0 / docs-only — see §3.6).

### 1.5 Minimal fix shape (fold (a)) — 4 sub-tasks, exact target files

**F1 — model.** `frontend/src/app/models/metrics.model.ts:111-119` — add a 4th member to `MetricsActivation`.
- *Required* (`readonly registry_read_ok: boolean;`) → the BE always emits it (`envelope.py:70`, default-`True` for a duck-typed provider), so required is contract-true; **but every FE fixture carrying an `activation` block must then add it** (see the fixture list below), otherwise the FE type-check leg (`tsc --noEmit -p tsconfig.metrics-fe.json`, the P5-M9 leg) breaks.
- *Optional* (`readonly registry_read_ok?: boolean;`) → the tolerant-mirror shape (the contract's own rule: "consumers must ignore unknown members"), no fixture churn; the FE then reads `payload.activation?.registry_read_ok === false` (treat *absent* as healthy, mirroring the BE's `getattr(..., True)` default).
- **Recommendation:** optional, with `=== false` as the failure predicate. It matches the BE's own default-safe semantics (`envelope.py:64` "a provider that does not carry the fact … reads as `True`") and keeps the AM-7 additive-member doctrine honest. Record the choice in `decisions.md`.

**F2 — shell state.** `frontend/src/app/pages/metrics/metrics.component.ts:186-216` — in the success branch, read the flag and route it to the **failure** arm, not the empty arm:
- add a signal (e.g. `catalogRegistryDegraded = signal(false)`) beside `catalogError`/`catalogStale`/`catalogLoaded`/`catalogTruncated` (`:91-99`);
- in `loadCatalog()`'s `next` branch (`:192-203`), set it from `payload.activation?.registry_read_ok === false`;
- when true → the **empty panel must NOT render** ⇒ `showEmptyPanel` (`:127-129`) must gain the guard (currently it keys on `catalogError() === null` only);
- when a previous good catalog exists → `catalogStale` (`:212`) should also be set, matching the existing "last known catalog" semantics.

**F3 — copy.** `frontend/src/app/models/metrics.model.ts:352-362` — add a dedicated copy constant for the degraded-registry condition (do **not** reuse `registry_unavailable`, which means "no provider wired"). House precedent for a page-local copy constant outside the HTTP error vocabulary: `METRICS_EMPTY_PANEL_COPY` (`metrics.component.ts:42-49`) and `METRICS_DOOR_ERROR_COPY` (`metrics.model.ts:353-362`).
- Render it through the **existing** failure surface: `metrics.component.html:9-21` (the `data-test="metrics-catalog-error"` banner). **Minimal shape = reuse the banner, add a distinct `data-test` (e.g. `metrics-registry-degraded`) so the failure-vs-empty distinction is assertable.**

**F4 — fixtures.** Every FE fixture carrying an `activation` block must gain the member (if F1 is *required*):
- `frontend/src/app/models/metrics.model.spec.ts:265` and `:302` (two payload fixtures).
- `frontend/src/app/pages/metrics/metrics.component.spec.ts:47-59` (`function envelope()` → `activation` at `:53-57`).
- `frontend/e2e/metrics-gate.spec.ts:33-43` (`const ENVELOPE` → `activation` at `:38-42`).

**F5 — BE analogue (the agent-tool half).** The same honesty defect exists on the **agent** side of the door and is squarely in this lane's remit: an agent tool that reads the door must not report "no metrics" when the registry read failed. The BE fact is already available on the provider (`provider.py:99-107`) and in the envelope (`envelope.py:70`). The agent-tool surface must carry `registry_read_ok` through to the agent's answer (a typed refusal/degraded marker, house vocabulary per the door's 12-code D9 vocabulary) rather than returning an empty list. **This is the tool-side twin of fold (a) and is NOT an exclusion.**

**F6 — optional: contract-doc amendment.** `docs/architecture/metrics-query-contract.md:104-106` — document the 4th member so the doc matches the wire (and extend `tests/unit/metrics/test_central_metrics_contract_doc.py` in the same commit if the doc's machine-checkable block is expected to carry it). The doc-pack is Tier-0 docs-only (`test_central_metrics_contract_doc.py:13-14`).

### 1.6 The verbatim evidence that this fold belongs to THIS lane

> `.agents/tidier/notes.md:760` —
> *"Known residual for the next lane: H5's additive `registry_read_ok` is NOT carried by the FE mirror (MetricsActivation is 3 members; the BE now sends 4). Contract-compliant per §4, but the FE still renders a failed registry read as an empty catalog."*

> `.agents/tester/RESULTS/2026-11-02-central-metrics-v1-functional-gate.md:107-111` —
> *"## Known-and-routed (verified still disclosed, NOT gated)
> R-i census gated polarity (metrics-collection lane) · R-ii F1/F3 capture_active unwired · R-iii F7
> histogram publish · R-iv stale_pause_sweep publication · FE `registry_read_ok` mirror (next lane)."*

**This lane IS the "next lane"** for the metrics sequence (item #2 of the ratified sequence = *agent tool surface over central metrics*; the v0.28.6 lane record lists item #1 = central-metrics-v1, item #2 = agent-tool surface, item #3 = Grafana, deferred). The lane #1 plan set also names this lane as the owner of the deferred scope work: `decisions.md:641-658` (**§D6**) and `fold-ledger.md:119` (**DEF-3**) — see §4.4.

---

## 2. Q2 — the six tidier Mediums (items 3, 5, 10, 16, 17, 21)

### 2.1 What the tidier pass actually produced (the only in-repo record)

`.agents/tidier/notes.md:752-763` — the central-metrics-v1 craftsmanship pass entry:

| Line | Content |
|---|---|
| `:752` | `## 2026-11-02 — central-metrics-v1 craftsmanship pass (worktree feature/central-metrics-v1 @ 1497bf51f..c3a003668, 307 files +82,439/−55), Iteration 001` |
| `:753` | Commission: quality/conventions gate BEFORE the tester functional gate; the code council had already DELTA-3-CLEARED correctness. |
| `:754` | Dispatch: 3 parallel read-only workers (readable `a5b578f8`, hygiene `99575d98`, robustness `cbd30572`) + 3 corroborating lanes. |
| **`:755`** | **Verdict: NEEDS WORK. 51 findings: 6 High / 27 Medium / 18 Low, plus 1 correctness escalation to Reviewer.** |
| `:756` | Fixed in-lane (5 commits, none pushed): `2fb9a014d` refactor(metrics) (the 5 Highs + 8 dead-export deletions), `0b9de2797` test(metrics) (the routed F-f ND-5 cursor-precedence pin — 3 legs, mutation-proven non-vacuous), `50d1cc086` test(metrics) (the 3 pins the fold's own changes moved), `c3a003668` docs(metrics) (12 stale router line citations re-pointed after the H3 docstring shift). |
| `:757` | H4 premise CORRECTED by the fix worker: the condition was a tautology that ALWAYS FIRES … the intended duplicate-name guard is unimplemented and routed to Reviewer. |
| `:758` | Routed item F-f: PIN — the ND-5 precedence (expired cursor + unknown family ⇒ 400 invalid_cursor) is now behaviourally pinned in `test_metrics_query_pagination.py::TestCursorPrecedence`; pack nodes 38→41. |
| `:759` | **ESCALATED to Reviewer (correctness, not a Tidier finding): the census `gated` polarity is INVERTED against the contract truth table in BOTH switch states (`metrics_census.py:323-335` documents "gated OFF" but returns True when work is ALLOWED; `provider.py:104-109` implements the docstring) … `capture_active` is likewise never wired.** |
| **`:760`** | **Known residual for the next lane: H5's additive `registry_read_ok` is NOT carried by the FE mirror …** |
| `:761` | Tester-owned, NOT edited: PACKS.md carries ZERO rows for `tests/unit/metrics/` (16 modules), the two `test/packs/metrics_*` scripts, or the FE e2e gate; the ND-5 P3 mutation row belongs in PACKS.md per the contract's §8 mutation-label rule. |
| `:762-763` | House lessons: "a Protocol that no consumer references guarantees nothing …"; "a fake collaborator cannot carry the semantic contract it stands in for …". |

### 2.2 The item list (3, 5, 10, 16, 17, 21) is NOT RESOLVABLE from the repository — explicit statement

**The tidier's per-finding numbered list does not exist in this tree.** Searches performed and their results:

| Search | Scope | Result |
|---|---|---|
| `glob_files "**/*tidier*"` (worktree root) | whole tree | Found `.agents/shared/planning/{report-obligation-visibility,promote-preflight-hang,db-pool-idle-txn}/tidier-report.md` — **NO `central-metrics-v1/tidier-report.md`**. |
| `grep "tidier"` in `.agents/shared/planning/central-metrics-v1/` | lane plan set | Only `plan-overview.md:5` (the gate-chain mention) + unrelated inventory hits. **No findings list.** |
| `grep "registry_read_ok"` in `.agents/**.md` | whole `.agents` | **Exactly 2 hits** — `notes.md:760` and the functional-gate RESULTS `:109`. Nothing else carries the tidier's item table. |
| `grep "Medium\|tidier\|High"` in `evidence-pack.md` | lane evidence pack | **No matches** — the evidence pack does not carry the tidier's findings. |
| `grep "first-trip\|first trip"` (repo-wide `.md`) | whole repo | No match on the lane. |
| `grep "T-M[0-9]+"` in `daemon/services/metrics_query/` | shipped code | No match — no `T-M<n>` markers were burned into the code. |
| `grep "fe_registry_read_ok_mirror"` | whole `.agents` | **No match** — that key is a project-KV lane-record name, not an in-repo artifact. |
| `list_directory .agents/tidier/` | tidier home | Only `notes.md` + `.scratch_watchover_pass.md` — **no per-lane report**. |

**Why:** the tidier's declared write scope is `.agents/tidier/` only (`agents/tidier/rule.md:22` — *"My write scope is `.agents/tidier/` only — I never write, edit, or commit source"*), and the house convention that a tidier report is committed under `.agents/shared/planning/<feature>/tidier-report.md` was **not** followed for this lane (no such file exists). The full 51-item list therefore never landed in the repo; only the 12-line `notes.md` summary did.

**Consequence for the planner (do NOT invent mappings):** items **3, 5, 10, 16, 17, 21** cannot be mapped to findings from repo evidence. The two admissible resolutions are:
1. **Request the item list from the tidier lane / the leader** (the pass was `Iteration 001`, dispatch ids at `notes.md:754`) — ask for the 27 Mediums verbatim; or
2. **Re-derive the candidate set** by re-running the tidier lens over the lane's diff range `1497bf51f..c3a003668` — with the explicit caveat that a re-run will not reproduce the original numbering.

**Closest matching finding list that DOES exist** (offered as the substitute, with its exact provenance):

| # | Finding text (verbatim or near-verbatim) | Target file:line | Severity class | Resolvable? |
|---|---|---|---|---|
| A | "H5's additive `registry_read_ok` is NOT carried by the FE mirror (MetricsActivation is 3 members; the BE now sends 4) … the FE still renders a failed registry read as an empty catalog." | `frontend/src/app/models/metrics.model.ts:111-119` (mirror) ↔ `daemon/services/metrics_query/envelope.py:70` (BE) | Medium (tolerated; a "known residual for the next lane") | **YES — this is fold (a).** |
| B | "the census `gated` polarity is INVERTED against the contract truth table in BOTH switch states" | `daemon/services/metrics_census.py:323-335`; `daemon/services/metrics_query/provider.py:104-109` | **Correctness** → escalated to Reviewer (NOT a Tidier finding) | Yes, but it is the **R-i exclusion**, not a fold item. |
| C | "`capture_active` is likewise never wired" | `daemon/api.py:2527-2533` (the provider is constructed **without** `capture_active=`) → `provider.py:71,75` (default `False`) | Correctness → Reviewer / R-ii | Yes — **R-ii exclusion**. |
| D | "H4 premise CORRECTED … the intended duplicate-name guard is unimplemented and routed to Reviewer." | (the H4 site — not cited in the notes) | Medium/High; routed to Reviewer | Partially — routed OUT. |
| E | "PACKS.md carries ZERO rows for `tests/unit/metrics/` (16 modules), the two `test/packs/metrics_*` scripts, or the FE e2e gate; the ND-5 P3 mutation row belongs in PACKS.md per the contract's §8 mutation-label rule." | `.agents/tester/PACKS.md` | Tester-owned | **Already closed by the tester** — PACKS.md now carries the whole block at `:1-91` (§3.1). |

**Size of the one resolvable item (fold (a)):** ~4 files, ~25-40 FE lines + 3-4 fixture lines + 1 BE-side tool change ⇒ **trivial-to-substantive (small)**.

---

## 3. Q3 — verification conventions (the house test/pack layout for the metrics surface)

### 3.1 The pack registry — `.agents/tester/PACKS.md` (3502 lines; the metrics block is lines **1-91**, at the very top)

The block header (`:1-19`) states the rules a later verification outline must follow:

- `:3-7` — **why the block exists**: the frozen contract `docs/architecture/metrics-query-contract.md` §8 declares **nine gate-leg pack ids** and says *"today `.agents/tester/PACKS.md` carries 0 of the nine"*; a leg invokes its D2 set **by pack id**, so an unregistered id makes the leg unrunnable.
- `:9-14` — **COMPOSITION RULE (load-bearing)**: *"Where the plan's DECLARED composition was **short of the blast-radius DERIVATION**, the registered composition below is the **DERIVED** one"* — the declared sets were run as disclosed supplements; the registrations carry the wider set so the next lane inherits the coverage.
- `:16-19` — **node counts are MECHANICAL** `pytest --collect-only -q` counts; the 2026-11-02 gate re-derived every figure; where a registered number moved the row carries `(registered N; +M via <commit>)`; **the gate leg's own `--collect-only` slicing proof is authoritative** and any delta is a *recorded finding*, not a silent correction.
- `:21-31` — **the fences (identical in every row)**: `WT=<worktree>` + the worktree-local `.venv/bin/python` (py 3.14.7, **never** the main checkout's); **explicit per-variable** `env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT` scrubs (the `POSTGRES_*` GLOB form is a **SILENT NO-OP**); `-p no:randomly`; dual-layer timeout (`timeout 300` outer + `--timeout=120` inner); **PG rows** add `--override-ini="addopts=" -m postgres` (the default addopts deselect the marker ⇒ vacuous green) + `PG_TEST_*` scratch role/DB + **serial** (schema races); **BASE legs** run the same command from a throwaway detached worktree at the true base.

### 3.2 The nine declared leg ids (contract §8 `gate-pack-ids`) — 9/9 registered

`PACKS.md:33-43`. Row shape: `| pack id | invocation | Scope (leg id + tier + composition + node count) | Last run | Status |`.

| # | Pack id | Line | Leg / Tier | Composition & nodes |
|---|---|---|---|---|
| 1 | `central_metrics_contract_doc` | `:37` | P1-G1 (Tier 0) | `tests/unit/metrics/test_central_metrics_contract_doc.py` — **21 nodes**; *"This pack is the reason the nine-id block must NOT be widened — it pins the count."* |
| 2 | `metrics_p2_dual_side` | `:38` | P2-G1 (Tier 1) | 9 declared files **WIDENED** by `tests/unit/persistence/test_checkpoint_perf_logging.py` → mechanical total **326** |
| 3 | `metrics_p3_dual_side` | `:39` | P3-G1 (Tier 2) | declared 302 **WIDENED by 13 files / 226 nodes** → mechanical **528**; ⚠️ gate-correction note: the 13 widened files' integration members are **not** `@pytest.mark.integration`-tagged ⇒ *"Assert on the collected/deselected COUNTS, never on the presence of an override flag."* |
| 4 | `metrics_scope_cage_dual_side` | `:40` | P3-G2 (Tier 0/1) | `tests/unit/test_router_scope_manifest.py` + `tests/integration/test_router_class_manifest.py` — 12 nodes; its D1 pin lives in its OWN file on purpose (a D1 pin inside a D2 file would change that file's node count between legs and break the slicing proof) |
| 5 | `metrics_p4_w1_dual_side` / `_w2_` / `_w3_` | `:41` | P4-G1/G2/G3 (Tier 1 ×3) | 21-file DERIVED set — 494 registered / 626 mechanical; *"the plan's declared composition was … ≈ 4 files / ~120 nodes; the DERIVATION reaches 21 files / 494 nodes"* |
| 6 | `metrics_fe_dual_side` | `:42` | P5-G1 (Tier 1) | `bash test/packs/metrics_fe_dual_side_test.sh` — D1 jest 141 nodes / D2 declared 138-137 / D2 **derived 1801 (HEAD) / 1791 (BASE)** / scoped type-check / 2 build legs / `package.json` diff |
| 7 | `central_metrics_ledger_reconciliation` | `:43` | P6-G1 (Tier 0) | **static**: `grep -c …` over the ledger/contract token sets — the ledger-vs-evidence reconciliation: case counts, **the R-i..R-v routing table**, the disclosure set. Docs-only ⇒ changed-pack PASS; no dual-side leg. |

### 3.3 The D1 (lane-owned, HEAD-only) pack rows — `tests/unit/metrics/` (PACKS.md `:45-67`)

> `:47-49`: *"D1 exists only at HEAD; its absence at BASE is **expected** and is neither a net-new failure nor a fix (the D1/D2 asymmetry statement, contract §8). HEAD runs D1 + D2; BASE runs D2 only, identical command shape."*

**15 pack rows over 16 modules** (`PACKS.md:45` says *"tests/unit/metrics/ 16 modules"*; the 16th module is `test_central_metrics_contract_doc.py`, which is its own pack id #1):

| Pack id | Module | Nodes (PACKS.md line) |
|---|---|---|
| `metrics_query_provider` | `test_metrics_query_provider.py` | **70** (registered 66; +4 via `61e5c6b30` +2, `b9d07dd4d` +2) `:53` |
| `metrics_history_ring` | `test_metrics_history_ring.py` | **32** (reg. 28; +4) `:54` |
| `metrics_sampler` | `test_metrics_sampler.py` | **21** (reg. 20; +1) `:55` |
| `metrics_registry_narrowing` | `test_metrics_registry_narrowing.py` | 12 `:56` |
| `metrics_vocabulary_amendment` | `test_metrics_vocabulary_amendment.py` | 11 `:57` |
| `metrics_query_routes` | `test_metrics_query_routes.py` | **31** (reg. 30; +1) `:58` |
| `metrics_query_auth_matrix` | `test_metrics_query_auth_matrix.py` | 25 `:59` |
| `metrics_query_pagination` | `test_metrics_query_pagination.py` | **41** — ⚠️ bookkeeping delta 38 → 41 `:60` |
| `metrics_query_switch_invariance` | `test_metrics_query_switch_invariance.py` | 18 `:61` |
| `metrics_query_api_wiring` | `test_metrics_query_api_wiring.py` | **24** (reg. 13; +8, +3) `:62` |
| `metrics_query_scope_cage` | `test_metrics_query_scope_cage.py` | 7 `:63` |
| `metrics_family_w1` | `test_metrics_family_w1.py` (F0.3·F1·F3·F4/F5·F6; class-scoped ids `metrics_family_f0_3`, `_f1`, `_f3`, `_f4_f5`, `_f6`) | 18 `:64` |
| `metrics_family_w2` | `test_metrics_family_w2.py` (F7·F8·F9·F10·F11·F12·F13) | 20 `:65` |
| `metrics_family_w3` | `test_metrics_family_w3.py` (F17·F18 partial·F21 partial) | 11 `:66` |
| `metrics_family_adjudication_w1/w2/w3` | `test_metrics_family_adjudication.py` | **26** (reg. 24; +2, +1) `:67` |
| *(not a pack row)* | `test_central_metrics_contract_doc.py` | 21 — its own id `central_metrics_contract_doc` `:37` |

Also present in the tree (not registered as their own rows): `tests/unit/metrics/baselines/` (a directory), `tests/unit/metrics/__init__.py`.

> **Bookkeeping-delta discipline (house rule, `PACKS.md:69-76`):** *"The contract §8 P3-G1 table records **24** for this file as a `--collect-only` count at P3-G1 time … The three figures are three different moments in the lane's life, not three different packs. The registered figure is 41, verified by the gate leg's collect-only slicing proof; the contract's 24 is a STALE P3-G1-time snapshot and must not be used for sizing. Same class of bookkeeping drift … — **record the moment, never the memory.**"* ⇒ **our lane must register its own pack rows with a re-derived `--collect-only` count, never a remembered one.**

### 3.4 The two in-tree gate scripts (`PACKS.md:78-83`)

| Pack id | Invocation | Scope |
|---|---|---|
| `metrics_fe_dual_side_test` | `cd $WT && timeout 900 bash test/packs/metrics_fe_dual_side_test.sh` | The Phase-5 FE gate: D1 jest / D2 declared / D2 derived / type-check / **both AM-23 build legs** / `package.json` diff. Fences: **no dependency install** (a missing `node_modules` is a LOUD FAIL, never a green skip), no daemon, no live port. `:82` |
| `metrics_census_pg_test` | `cd $WT && timeout 300 env -u POSTGRES_USER … PG_TEST_DB=<scratch> PG_TEST_USER=<role> PG_TEST_PASSWORD=<pw> PG_TEST_ALLOWED_DBS=<scratch> .venv/bin/python -m pytest tests/postgres/test_metrics_census_pg.py --override-ini="addopts=" -m postgres -q --tb=short -p no:randomly` | The real-PG half of the census sampler. **Serial only.** Scratch role+DB, DROP at cleanup. `:83` |

`test/packs/metrics_census_pg_test.sh` (208 lines) is the house **self-contained scratch-DB** exemplar:
- `:6-16` — the scope it covers (direct non-pooled connection; `application_name='ensemble-metrics-census'` + `statement_timeout=5 s` + `connect_timeout=3 s`; live `pg_stat_activity` read; self-exclusion rule; server-side fragment→site map; **SCRATCH-DB FENCE ordering** — a disallowed DB name errors **BEFORE any connect attempt**).
- `:18-22` — **marker flags are mandatory** (the default addopts EXCLUDE the `postgres` marker ⇒ a run without them collects 0 tests and reports a **VACUOUS GREEN**).
- `:24-34` — the scratch discipline: the pack CREATES its own throwaway DB + LOGIN role and DROPS both in the EXIT trap; the DB name must be one of the module's EXACT-membership allowlist names (`tests/postgres/test_metrics_census_pg.py: ALLOWED_SCRATCH_DBS`) — **asserted in the script itself** so a rename fails loudly there; an AMBIENT `PG_TEST_DB` is NEVER the target.
- `:36-42` — **exit-code contract**: per-leg pytest rc `0 ok / 1 test failure / 5 nothing collected / 124 leg timeout`; pack exit `0 PASS / 1 FAIL / 124 TIMEOUT`; FAIL covers **ANY** rc≠0 (incl. 5) **AND** a summary missing "N passed" with N≥1 — *"GREEN-AT-ZERO IS A FAILURE"*.
- `:53-57` — toolchain preflight (`psql`/`createdb`/`dropdb` on PATH; a missing binary ⇒ explicit FAIL, never a skip).

### 3.5 The FE e2e gate + the mutation row (`PACKS.md:85-90`)

| Pack id | Invocation | Scope |
|---|---|---|
| `metrics_fe_e2e` | `cd $WT/frontend && npx playwright test --config=playwright.metrics-gate.config.ts` (own proxy config + gate pattern; door routes stubbed pre-network ⇒ **zero daemon**) **plus** the live-daemon leg (same journey against a fenced daemon on `:8079`, `ENSEMBLE_SELF_ENV=dev` / `POSTGRES_DB=ensemble_dev` — the Release-Gate-sanctioned exception to the no-daemon rule) | `frontend/e2e/metrics-gate.spec.ts`: gear menu → `/metrics` → Overview stat cards → Charts → Families (a dark family **WITH its reason**) + the deep-link. **The load-bearing assertion is STEP-RENDERING:** a counter's path carries `data-render-mode="step"` with `H`/`V` commands and **no** diagonal segment; a gauge carries `data-render-mode="line"` with `L` and neither `H` nor `V`. `:89` |
| `metrics_p3_nd5_mutation` | plant the ND-5 defect (move the family check BEFORE the cursor-expiry probe) → run `metrics_query_pagination` → expect RED on exactly the ND-5 pin → `git checkout --` → expect GREEN | The ND-5 mutation row (P3 mutation table). Commit `0b9de2797` added the precedence pin; *"a pin that cannot catch its own defect is the class this row exists to exclude."* Restore asserted BYTE-IDENTICAL. `:90` |

`test/packs/metrics_fe_mutation_matrix_test.py` (621 lines) is the **mutation-matrix** exemplar:
- `:1-8` — *"Every mutation in the plan's table (**P5-M1 .. P5-M11**) is planted INDEPENDENTLY on the production source, the intended pack is run, and the result must be RED; the file is then restored BYTE-IDENTICALLY and the pack is re-run and must be GREEN."*
- `:9-17` — **the RED surface differs by design**: the jest corpus catches the behavioural mutations (P5-M1..M8, M10, M11); the **SCOPED TYPE-CHECK leg** (`tsc --noEmit -p tsconfig.metrics-fe.json`) catches **P5-M9** — the availability union is a TYPE-level property and jest does not type-check. *"That is exactly why the type leg exists."*
- `:19-24` — invocation: `python3 test/packs/metrics_fe_mutation_matrix_test.py` / `--verify` (detect + attribute a dirty tree) / `--recover` (restore from the recovery journal) / `--selftest` (the restore/interruption pins).
- `:26-42` — **restore discipline (fix cycle 2, A-7 — NO `git checkout --`)**: each mutation is restored from an **in-memory byte copy** taken immediately before the mutation; before the first mutation the harness records a pre-run manifest (HEAD sha + the sha256 of every file it will touch) to `LOG_DIR/pre_run_manifest.json` and journals the pre-mutation BYTES to `LOG_DIR/recovery/<label>.blob` while a mutation is in flight ⇒ a killed run leaves the tree DIRTY but DETECTABLE and ATTRIBUTABLE; after the LAST restore the whole manifest is re-verified — a post-run mismatch is a loud FAIL.
- `:55-58` — **the working tree MUST be clean when the matrix starts**; the script refuses to run on a dirty tree.

`test/packs/metrics_fe_dual_side_test.sh` (303 lines) is the **gate-leg** exemplar:
- `:5-26` — the **D1 / D2 two-class declaration** of the blast-radius recipe: D1 = the lane's own FE specs (HEAD-only; a base-side run is **impossible by construction** — recorded as `SKIPPED-BY-CONSTRUCTION`, never a pass and never a failure); D2 = the dual-side comparable set, **DERIVED at run time** (the specs that import the MODELS BARREL — `frontend/src/app/models/index.ts:1059` carries `export * from './metrics.model';` — a name collision breaks any of them — plus the specs that read the route table / gear menu); the declared composition runs as its own leg.
- `:27-38` — the **seven legs**, each printing its exit code: 1 D1 jest · 2 D2 declared jest · 3 D2 derived jest · 4 scoped type-check · 5 `ng build --configuration development` (AOT) · 6 `ng build --configuration production` (the only configuration carrying `budgets` + `outputHashing`; any budget WARN is a **RECORDED FINDING**, never an auto-fix and never a silent pass) · 7 `package.json` dependency diff (the D2 zero-dependency guard: the diff against BASE_REF must be EMPTY).
- `:40-45` — fences + `PACK_BUDGET_S=900` self-cap + exit codes `0 PASS / 1 FAIL / 124 TIMEOUT`.
- `:51-55` — the dual-side BASE leg runs **this script** (the HEAD tree's copy) against a throwaway detached base worktree; `METRICS_FE_PROJECT_DIR` overrides the project dir.

### 3.6 The contract-doc pin test — `tests/unit/metrics/test_central_metrics_contract_doc.py` (221 lines) — a FREEZE guard

- `:1-15` — *"The contract is a DOCUMENT, so its pins are document pins: the pack **parses the machine-checkable fenced blocks** the contract doc carries and asserts their exact contents."* The doc is repo-only (`docs/` is not part of the release payload) ⇒ this pack is a **Tier-0 changed-pack leg — it needs no dual-side leg.**
- `:8-11` — the mutation table: **P1-M1** delete an activation-surface row ⇒ the surfaces pin reds; **P1-M2** widen the availability reason set ⇒ the closed-set pin reds; **P1-M3** drop a truth-table invariant ⇒ the invariants pin reds.
- `:24` — `CONTRACT_DOC = Path("docs/architecture/metrics-query-contract.md")`.
- `:26-34` — `EXPECTED_SURFACES` (six: `listener_body`, `api_metrics_text`, `query_routes`, `census_db_work`, `registry_accumulation`, `fe_charts_polling`).
- `:36-41` — `EXPECTED_ROUTES` (the three query routes).
- `:43-50` — `EXPECTED_AVAILABILITY_REASONS` (the **CLOSED** five-reason set).
- `:52-66` — `EXPECTED_ERROR_VOCABULARY` (six entries: `404 unknown_family`, `400 invalid_label_filter`, `400 invalid_cursor`, `503 registry_unavailable`, `503 AUTH_NOT_CONFIGURED`, `403 SCOPE_INSUFFICIENT` — the 403 token is the SHIPPED UPPERCASE spelling per the council's D-c ruling).
- `:68-75` — `EXPECTED_INVARIANTS` (the **five** truth-table invariants: `auth_behaviour_identical_across_switch_states`, `registry_series_set_identical_for_non_switch_derived_families`, `only_permitted_body_differences`, `check_site_census_equals_two`, `no_direct_switch_read_in_query_package`).
- `:77-80+` — `EXPECTED_PACK_IDS` — the **nine registered gate-pack ids** (approver N-a).

⇒ **The doc-pin is a FREEZE guard.** If our lane amends the contract doc, this pack's expectations must move in the same commit — and the pack's own count (21 nodes) is itself pinned by the nine-id block (`PACKS.md:37`: *"This pack is the reason the nine-id block must NOT be widened — it pins the count."*).

### 3.7 The auth matrix + scope cage packs (what is currently pinned)

**`tests/unit/metrics/test_metrics_query_auth_matrix.py` (137 lines)** — the door's POSTURE is the contract's truth table and every row is a pin (`:3-15`):
- auth OFF ⇒ `503 AUTH_NOT_CONFIGURED`, and the refusal happens **BEFORE any provider read** (a refusal never leaks a registry value);
- auth ON, no session ⇒ `401`;
- auth ON, a session **without** `maintenance:read` ⇒ `403`;
- auth ON, a session **with** `maintenance:read` ⇒ `200`;
- every one of the three routes carries the **SAME** scope — the family's EXISTING taxonomy entry, never a new one.
- Mutation table (`:14-15`): **P3-M1** (the fail-closed auth seam moved after the registry read) · **P3-M2** (one route's scope dependency dropped).
- Implementation shape: `TestAuthOffIsFailClosed` (`:33-`), with the load-bearing leg `test_auth_off_never_reads_the_registry` (`:42-51`, asserts `provider.reads == []`) and `test_auth_off_does_not_leak_a_registry_value` (`:53-69`).

**`tests/unit/metrics/test_metrics_query_scope_cage.py` (166 lines)** — the **three-way cage form** (`:1-19`):
- the **declared-route** census reads **111** (the three query routes joined the family);
- the **mount** census stays **37** (the query routes EXTEND an existing mount, they add none);
- the **class-manifest** family count stays **33**;
- plus the family's scope: the query routes carry the family's EXISTING `maintenance:read` — **no new scope is minted**;
- constants at `:36-42` (`DECLARED_ROUTE_CENSUS = 111`, `MOUNT_CENSUS = 37`, `CLASS_MANIFEST_FAMILIES = 33`, `FAMILY_SCOPE = "maintenance:read"`);
- `:17-18` — *"This pack is **D1 (HEAD-only)**: it asserts the three-way form at once, so a cage that drifted in ANY direction reds."*
- `:45-55` — `_route_required_scopes(route)` reads the scope set in EITHER the wrapper form (`endpoint.required_scopes`) OR the dependency form (`route.dependant[].dependencies[].call.required_scopes`);
- `:57-67` — the `app_routes` module fixture builds the **AUTH-ON** app (`create_app(auth_config=on_auth_config())`) — the auth-OFF app mounts fewer families.

### 3.8 The shared helpers

**`tests/helpers/metrics_query_fixtures.py` (268 lines)** — *"The query door is ONE provider behind THREE routes, so every pack needs the same two things: a MOUNTED app … and a duck-typed provider that **RECORDS** what the door read — so a pack can assert the READ CONTRACT (no registry read before the fail-closed seam; the body built OFF the event loop) and not only the response shape."* (`:3-13`)
- `:31-37` — `API_PREFIX = "/api"`, `READ_SCOPE = "maintenance:read"` (the EXISTING taxonomy entry).
- `:39-45` — `QUERY_PATHS` (the three paths).
- `:47-75` — `RecordingProvider` — a duck-typed fake that records `reads` (the view each call touched) and `threads` (the THREAD each read ran on — the off-loop build assertion reads the latter: a `db-offload*` worker thread, never the event loop). It is a first-class consumer of the `MetricsQueryProvider` PROTOCOL (`:14-17`).
- ⚠️ **House lesson to respect** (`.agents/tidier/notes.md:763`): *"a fake collaborator cannot carry the semantic contract it stands in for — a switch-invariance pack built on a fake census proved the PROVIDER's inversion and never the census's polarity."* ⇒ when a pack asserts a polarity, the assertion must bite the **real** implementation, not only the fake.
- Also imported by the auth matrix: `tests.helpers.auth_fixture.on_auth_config` (`test_metrics_query_scope_cage.py:63`).

**`tests/helpers/metrics_exposition_baseline.py` (134 lines)** — the AM-16 / D-11a **structural-identity** baseline:
- `:3-16` — the Prometheus text exposition must stay STRUCTURALLY IDENTICAL to the lane's base artifact; **exactly THREE carriers may differ in VALUE** (the allowlist) and every other byte must match; `mask_exposition` replaces those three with a fixed placeholder so the masked bodies of the two legs are directly comparable; anything else that moved is a gate RED.
- `:37-52` — `ALLOWLIST` (the three carriers: the `# frozen vocabulary version: N` footer; the `ensemble_metrics_vocabulary_version` gauge value; the `# HELP ensemble_checkpoint_op_seconds …` text — with BOTH spellings masked, because the contract names the alias and the canonical name carries the identical HELP text, *"an allowlist that covered only one spelling would be incomplete against its own carrier"*).
- `:18-23` — **THE CAPTURE IS A SUBPROCESS on purpose**: the registry is a PROCESS-WIDE singleton, so inside the pytest process the rendered body would depend on the test's import order; a fresh interpreter importing ONLY the metrics modules renders a DETERMINISTIC body.

### 3.9 How gate legs, mutation matrices and DERIVED compositions are expressed — one example each

| Concept | Expression | Citation |
|---|---|---|
| **A gate leg** | A PACKS.md row: `\| pack id \| full invocation \| Scope (leg id + tier + declared-vs-derived composition + node count + a prose rationale) \| Last run \| Status \|` — the invocation is a **complete, copy-pasteable command** including the fences (`WT=`, per-variable `env -u`, `-p no:randomly`, `--timeout=120`, `--override-ini="addopts=" -m postgres` for PG). | `PACKS.md:35-43` (row shape `:35`, example row `:37`) |
| **A mutation matrix** | A **harness script** under `test/packs/` that plants each mutation independently, runs the intended pack, requires RED, restores BYTE-IDENTICALLY, re-runs, requires GREEN; each mutation carries an **id** (`P5-M1`..`P5-M11`, `P3-M1/M2`, `P1-M1..M3`, `ND-5`) that appears in the pack's docstring AND in the PACKS.md mutation row; the restore is journaled (pre-run manifest + per-mutation blob) so an interrupted run is DETECTABLE and ATTRIBUTABLE; the working tree must be CLEAN at start. | `test/packs/metrics_fe_mutation_matrix_test.py:1-58`; the PACKS.md mutation row `PACKS.md:90` |
| **A DERIVED blast-radius composition** | The row states the **declared** composition, then the **DERIVED** one that the blast-radius recipe reaches, then the **mechanical** node count, and records the delta with its commit (`registered N; +M via <commit>`); the declared set is still run **as a disclosed supplement**. The recipe is: the three passes over the stage diff (here `daemon/persistence.py`, `daemon/services/metrics_query/sampler.py`, the new `wiring/` package) reach files the declared set misses (e.g. a barrel re-export). **Node counts are re-derived by `--collect-only -q` at run time — "a hand-maintained count is a finding"** (`fold-ledger.md:79`). | `PACKS.md:9-14` (the rule), `:41` (a DERIVED example), `:16-19` (the counts rule); `fold-ledger.md:79` (the executor-re-derives annotation) |

### 3.10 FE unit/e2e conventions for the metrics surface

- **Spec house pattern**: a **logic-mirror class** + **production-source pins** (reading the `.ts`/`.html` as text) + a light TestBed render — `frontend/src/app/pages/metrics/metrics.component.spec.ts:1-15` (mirrors `resources.component.spec.ts`); the source-text reads are `:34-36` (`readFileSync` of `metrics.component.ts` / `.html`).
- **The FE model spec's field-set pin** — `frontend/src/app/models/metrics.model.spec.ts:1-16`: the expectation is **parsed out of the frozen contract doc** (`docs/architecture/metrics-query-contract.md` §4.3), so a rename in the mirror turns the spec RED **and** a rename in the contract turns it RED (the two sides are compared, never one asserted against itself); plus the widened-availability-union pin (P5-M9) which only compiles while the union is widened.
- **The FE component spec's load-bearing pins** — `metrics.component.spec.ts:7-15`: the availability-driven EMPTY STATE (the panel appears EXACTLY when no family is live — a catalog whose only family is DARK shows the panel; a catalog with a LIVE family does NOT); the panel COPY (never "disabled", never "error"); **and "a 200-EMPTY response never renders the error banner"** — ⚠️ **that last pin is the one a `registry_read_ok` fold must reconcile** (a failed registry read IS a 200-empty response, so the new degraded state needs its own distinct assertion, not a re-use of the "200-empty ⇒ no banner" rule).
- **The e2e spec** — `frontend/e2e/metrics-gate.spec.ts:1-28` (the stub architecture cloned from `resources-gate.spec.ts`; the catch-all `**/api/**` route is registered FIRST = lowest precedence, Playwright routes are LIFO, so the specific stubs shadow it); fixtures at `:33-171`; `installStubs` at `:180-223`; the journey assertion at `:235-296`.
- The e2e config exists: `frontend/playwright.metrics-gate.config.ts` (own proxy config + gate pattern).

---

## 4. Q4 — the EXCLUSION boundary (what must be fenced OUT of this lane)

### 4.1 The VERBATIM routed list (the authoritative in-repo source)

`.agents/tester/RESULTS/2026-11-02-central-metrics-v1-functional-gate.md:107-111`:

```
## Known-and-routed (verified still disclosed, NOT gated)
R-i census gated polarity (metrics-collection lane) · R-ii F1/F3 capture_active unwired · R-iii F7
histogram publish · R-iv stale_pause_sweep publication · FE `registry_read_ok` mirror (next lane).
The FE e2e live leg observed all three availability reasons (`capture_off`, `series_absent`,
`census_gated`) on real data — consistent with the disclosed dark-family set.
```

(The same gate's addendum, `:130-144`, records the R1/R2/R3/R4/R5 dispositions — all closed; the R5 addendum names the 3rd `FAMILY_F_SITES` entry `wiring/db.py`.)

### 4.2 The routed items — one row each

| Id | One-line description | Target file:line | Owning lane | IN / OUT of THIS lane |
|---|---|---|---|---|
| **R-i** | The census `gated` polarity is INVERTED against the contract truth table in BOTH switch states — the docstring says "gated OFF" but it returns `True` when work is ALLOWED; a family publishing 15 live series reports dark/`census_gated` when the switch is ON. Zero behavioural pins (the switch-invariance pack pins against a FAKE census). | `daemon/services/metrics_census.py:323-335`; the provider implements the docstring at `daemon/services/metrics_query/provider.py:104-109` | **metrics-collection lane** (named in the functional gate) | **OUT** |
| **R-ii** | `capture_active` is never wired — the provider is constructed without it, so it is permanently `False`. | construction site `daemon/api.py:2527-2533` (no `capture_active=` passed) → `daemon/services/metrics_query/provider.py:71,75` (default `False`); the F1/F3 families therefore report `capture_off` | metrics-collection / mechanical-file lane | **OUT** |
| **R-iii** | The F7 non-internal histogram never publishes a snapshot. | the F7 saver-op BYTES histogram family: `daemon/services/metrics_query/wiring/w2.py:13` (the family declaration), `:56` (the bucket set), `:74` (`kind="histogram"`); the registry's histogram registration at `daemon/services/metrics_query/wiring/registry.py:141-142`; the adjudication row "F7 PARTIAL — the bytes histogram only (the write hook is OUT)" at `wiring/adjudication.py:27` | canonical-registry lane | **OUT** |
| **R-iv** | `stale_pause_sweep` never publishes to the app state / its counters readers are missing (the family renders no series). | the family is registered at `daemon/services/metrics_query/wiring/sources.py:224` (`("stale_pause", "stale_pause_sweep")`); the sweep service is `daemon/services/stale_pause_sweep.py` (counters at `:436`); the boot/wiring site `daemon/api.py:3058-3074` and `daemon/services/pool_orchestrator.py:341-350` | sweeps + mechanical-file lanes | **OUT** |
| **`fe_registry_read_ok_mirror`** | The FE `MetricsActivation` mirror declares 3 members; the BE sends 4 (`registry_read_ok`). A failed registry read renders as an empty catalog. | FE `frontend/src/app/models/metrics.model.ts:111-119`; shell `frontend/src/app/pages/metrics/metrics.component.ts:186-216` + `:127-129`; template `frontend/src/app/pages/metrics/metrics.component.html:9-41`; BE `daemon/services/metrics_query/envelope.py:56-71`, `provider.py:152-160` | **THIS LANE** | **IN (fold in)** — evidence §1.6 |

### 4.3 Additional routed items (NOT the R-i..R-v family) — also fenced OUT

| Id | Description | Target | Source | IN/OUT |
|---|---|---|---|---|
| `family_a_dbpool_baselines` | The family-A DB-pool baselines are **stale at base**: baseline `438/439` vs observed `466/467`; the baselines were last set `9d9c0b71f` **2026-10-29 (pre-lane)**; the lane's diff to that file is exactly the `FAMILY_F_SITES` addition; **zero family-A sites in `metrics_query`** (`Session|sessionmaker` grep = 0). Disclosed not-gated, untouched. | the baselines file (family A); `wiring/adjudication.py` (`FAMILY_F_SITES`) | functional-gate RESULTS `:141`; v0.28.6 lane record | **OUT** (base-debt lane) |
| `pg_census_fence_shadow` | The PG-census pack's 1 red — `test_the_fence_rejects_a_disallowed_db_name_before_connecting` — is **pre-existing at base** (a shared autouse fence shadows the module marker), routed to the owning lane with a fix shape. | `tests/postgres/test_metrics_census_pg.py` (the autouse fence) | functional-gate RESULTS `:99-101` | **OUT** |
| `tidier_mediums` (27 standing) | 27 standing tidier Medium findings; **items 3, 5, 10, 16, 17, 21** were flagged as "first-trip for the agent-tool lane". | — (the item list is not in the repo) | v0.28.6 lane record; tidier notes `:755` | **IN for the six named items — BUT NOT RESOLVABLE** (§2.2) |
| `R-v` (the house canon) | The comparators/files-first + `LC_ALL=C` + FORM-tagged canon, the EMPTY-verdict FAIL-LOUD exit 2, the VACUOUS guard — landed **in-repo** and rides v0.28.6. | `.agents/tester/PACKS.md` (the canon block) | functional-gate RESULTS `:124`; v0.28.6 record | **already landed — inherit, do not re-do** |
| `fe_registry_read_ok_mirror` (restatement from the v0.28.6 lane record) | *"FE MetricsActivation 3 members vs BE 4 (renders failed registry read as empty catalog) → next lane"* | see §4.2 | v0.28.6 lane record `routed_followups` | **IN (fold in)** |

### 4.4 The D6 / DEF-3 scope question — a DECISION this lane must take explicitly

Lane #1 left a **named commitment** addressed to this lane:

- `decisions.md:641-658` — **§D6 — Deferred scope split (`metrics:read`) — NAMED COMMITMENT (leader LR-1)**:
  - `:643-645` — *"**Status: NAMED COMMITMENT, not built now.** v1 keeps `maintenance:read` … This section is **binding guidance for the deferred lanes** (deferred item #2 agent tools, deferred item #3 Grafana) — it is not a 'revisit later'."*
  - `:647-653` — the **retrofit map (4 lines + manifest)**: 1 taxonomy line (`daemon/auth/scopes.py:60-101`) · 1 manifest line (`"/api/metrics": "maintenance:read|metrics:read"`) · 4 decorator lines (the three new query routes declare `metrics:read`; the shipped text route keeps `maintenance:read`) · cage note (the scope cage is a **subset check**, and `SESSION_OPERATOR_SCOPES` auto-grants, so no operator re-grant is needed).
  - `:655-656` — **the TRIGGER**: *"a non-operator machine lane reads the door in this lane's lifetime (e.g. a Grafana / deferred-#3 token) ⇒ mint `metrics:read` at that point."*
  - `:657-658` — until then, v1 stays on `maintenance:read` and the query-API docs disclose the over-grant.
- `fold-ledger.md:119` — **DEF-3**: the `metrics:read` scope split — *"a named commitment (LR-1), not built now — the retrofit map (1 taxonomy line + 1 manifest line + 4 decorator lines) is recorded in `decisions.md` §D6 as binding guidance for the deferred lanes (items #2/#3)"* — **Owner: "the deferred agent-tool / Grafana lanes."**
- `decisions.md:361-368` — **AM-20 over-grant disclosure**: `maintenance:read` ALSO confers the checkpoint-cleanup-execute power; the scope split is a named commitment; *"Until that retrofit lands, v1 keeps `maintenance:read` — see the D6 section below for the binding guidance to the deferred lanes."*

⇒ **An agent-tool lane that lets a non-operator agent read the door is exactly the D6 trigger.** The plan must rule: (a) land the 4-line retrofit in this lane, or (b) explicitly decline with the disclosure carried forward, recording the ruling in `decisions.md`. (Note the coupling: the scope cage pins are `metrics_query_scope_cage` (7 nodes) + `metrics_scope_cage_dual_side` (12 nodes) — the retrofit MOVES the scope set ⇒ the cage's `FAMILY_SCOPE` constant (`test_metrics_query_scope_cage.py:42`) and the manifest rows change ⇒ the pin's node set / the derived composition must be re-derived.)

### 4.5 The lane's own surface (for reference, NOT a fold item)

- The BE provider is constructed once, at boot: `daemon/api.py:2502-2539` (`_metrics_query_provider = RegistryMetricsQueryProvider(...)` at `:2527-2533`; `app.state.metrics_query = ...` at `:2533`; the sampler at `:2534-2539`; the boot-failure path sets `app.state.metrics_query = None` at `:2540-2543`).
- The door's routes: `daemon/routers/metrics.py` — the shipped Prometheus text route at `:140-168`; the query door at `:171+` with `/families` at `:558-599` (`scope_required(_SCOPE_READ)` at `:560`), `/snapshot` at `:602-619` (`:604`), `/series` at `:670` (`:672`); the fail-closed `_require_auth_on` seam at `:131-137`.
- The query package: `daemon/services/metrics_query/` — `__init__.py` (exports `RegistryMetricsQueryProvider` at `:58`, `:101`), `availability.py`, `constants.py`, `downsample.py`, `envelope.py`, `protocol.py`, `provider.py`, `ring.py`, `roster.py`, `sampler.py`, `selectors.py`, and `wiring/` (`__init__.py`, `accumulate.py`, `adjudication.py`, `db.py`, `registry.py`, `sources.py`, `vocab.py`, `w1.py`, `w2.py`, `w3.py`, `ws_a.py`).
- **There is NO metrics agent-tool category today.** `daemon/tools/_tool_registry.py:969` holds `CATEGORY_MODULES`; the categories are per-module `CATEGORY_NAME` constants (e.g. `daemon/tools/chart_tools.py:315` `"Chart"`, `daemon/tools/infra.py:74` `"Infrastructure"`, `daemon/tools/ens_db_tools.py:111` `"Ensemble DB"`). ⚠️ **Naming collision hazard:** `skill_get_metrics` already exists (`daemon/tools/_tool_registry.py:1534`, defined in `daemon/tools/skill_evolution_tools.py:39-40,64` — category `"Skill Evolution"`). A new metrics tool must not shadow it.
- The house scope of a tool category + the agent allow-lists are the `meta.json` `tools.allow` mechanism (per the v0.26.x lane records' "pin ceremony" pattern).

---

## 5. Q5 — lane #1's plan-set shape (the house convention our lane must match)

### 5.1 The files (`.agents/shared/planning/central-metrics-v1/` — 22 entries)

| File | Lines | Purpose (per `plan-overview.md:610-637`, the plan-file manifest) |
|---|---|---|
| `plan-overview.md` | 677 | the master — objective, dispatcher adjudications (AD1..AD4), scope fences + out-list, phase map, coupling, the frozen contract, gate-doctrine application, disagreement flags, re-derived budget, risk register, success criteria, provenance, plan-file manifest, adjudicated open questions |
| `phase1-plan.md` | — | Posture, activation & the frozen contract envelope |
| `phase2-plan.md` | — | Query substrate: provider, ring buffer, sampler, registry narrowing |
| `phase3-plan.md` | — | Query API routes + manifest cages + lifespan wiring |
| `phase4-plan.md` | — | Extension-subset registration (W1/W2/W3 clusters) |
| `phase5-plan.md` | 112 | FE charts page (zero-dep SVG) |
| `phase6-plan.md` | 90 | Docs, evidence & stage-prep closure |
| `decisions.md` | 682 | the technical-analysis decisions record — D1–D6, each with Evidence/Options/Ruling/Disclosure; **§D6 is the named commitment to the deferred lanes** |
| `fold-ledger.md` | 383 | the fold ledger — §A the 27 architect amendments, §B the 11 council warnings, §C the R-a/R-b/R-c dispositions, §D the advisory register, §E the deferred items (DEF-1..DEF-3), §F the verification record. *"**This is the artefact the fresh approver cites.**"* |
| `requirements.md` | — | FR-A..G, NFR-1..10, C-1..C-10, AC-A.1..F.4, the Theme-B adjudication table, the Theme-G out-list (plan-set-owned per LR-3) |
| `research-findings.md` | 223 | the wave-0 research digest (3 explorer reports) — an INPUT, unmodified |
| `architecture-recommendation.md` | — | architect v1 — **untouched** (retained for diff provenance) |
| `architecture-recommendation-v2.md` | 239 | architect v2 — **untouched** (the SOUND-WITH-AMENDMENTS fold source) |
| `evidence-pack.md` | 609 | the evidence pack (Phase 6 task 6.6): per-leg results (tier · D1/D2 split · slicing proof · pre-exclusion · expected-skip census), the mutation→test tables with observed digests, the measured budget table, the F7c reconciliation, the WS-A verdict, the F21 conditionality, the AM-16 structural diff, the vendor provenance table, the FE-manifest delta, and a disposition for every flag and every deviation |
| `stage-prep-checklist.md` | — | the stage-prep checklist (Phase 6 task 6.7): the three EMPTY deltas, the vendored inventory, the declared FE-manifest delta, the untouched live surface, the two hand-overs (version slot → giter; promote → the user's nonce ceremony) |
| `evidence/` | dir | `am16-exposition-base.txt`, `am16-exposition-head.txt` (the AM-16 structural-diff inputs), `c1-runtime-observation.md`, `c2-fe-gate/`, `c2-fe-gate-preservation-manifest.txt`, `d-c-contract-token-pin.txt`, `delta-3-gate/`, `fix-cycle-1-gate/`, `p1-mutations-final.log`, `p1-p2-mutation-logs.sha256.txt`, `p2-gate/`, `p2-gate-digests.txt`, `p2-mutations-final.log`, `p3-adjudication/`, `p3-adjudication.sha256.txt`, `p3-gate-final/`, `p3-preservation-manifest.txt`, `p3-scratch/`, `p3-sweep-crash/`, `p3-sweep-sharded/`, `p3-verdict-lines.txt`, `p4-failed-digests.txt`, `p4-gate/`, `p4-mutations/`, `p4-preservation-manifest.txt`, `p5/`, `p5-preservation-manifest.txt`, `p5-verdict-lines.txt`, `set-digests.txt` |
| `inventory-vendor/` | dir | AM-24 vendored copies of the untracked `metrics-unification` inventory + `PROVENANCE.md` (sha256 table + verification recipe) |

### 5.2 The phase grouping and the shape conventions to copy

- **Phases are grouped by SUBSYSTEM, not by file**: P1 posture/contract · P2 substrate · P3 routes+cages · P4 family registration · P5 FE · P6 docs/closure.
- **Every phase file carries the same header shape**: title, `Status: pending · Tier **N** (…),` a `**FOLDED <date>:**` line naming the fold source and the amendment ids, and a `Feeds:` line naming the gate chain (`phase6-plan.md:1-5`):
  ```
  # Phase 6: Docs, Evidence & Stage-Prep Closure
  Status: pending · Tier **0** (docs-only diff → changed-pack PASS, plus the ledger reconciliation check)
  **FOLDED 2026-11-02:** architect v2 amendments folded — ids cited inline as `(AM-n)`; leader rulings as `(LR-n)`. **Tier unchanged**.
  Feeds: architect → plan council → fresh approver → implementation → giter staging
  ```
- **Every phase file has a `## Tasks` table** with columns `| # | Task | Depends On | Acceptance | Traceability |` (`phase6-plan.md:17-21`).
- **Success criteria are per-phase AND lane-level** (`plan-overview.md:548-575`): per-phase (P1..P6) with *criterion / how to measure / threshold*; lane-level L1..L11 with the same triple.
- **The gate chain is named in the plan header**: `plan-overview.md:5` — *"architect verdict SOUND-WITH-AMENDMENTS; AM-1..AM-27 folded (2026-11-02) with leader rulings LR-1..LR-3 → feeds plan council → fresh approver → implementation (tiered-gate doctrine) → code council → tidier → tester functional gate → giter staging"*.
- **Version discipline** (`plan-overview.md:8`): the plan names NO version; the version slot is derived by giter at stage time (W9); *"no implementation lane may mint one early."*
- **The plan-file manifest is a REQUIRED section** (`plan-overview.md:608-637`) listing EVERY file in the lane dir with its purpose — and `fold-ledger.md:78` (R-c(ii)) records that omitting files from it was itself a council finding.

### 5.3 Recommended plan set for THIS lane (to match the house convention)

```
.agents/shared/planning/metrics-agent-tools/
├── plan-overview.md          # objective, scope fences + OUT-LIST (the exclusions of §4),
│                             #   the fold-task map (§1.5 + §2), phase map, risk register,
│                             #   success criteria, provenance, plan-file manifest
├── phase1-plan.md            # the agent-tool surface (tools, category, allow-lists, scope/routing)
├── phase2-plan.md            # the FE registry_read_ok fold (F1..F4) + the BE degraded-flag carry (F5)
├── phase3-plan.md            # verification: packs, legs, mutations, derived compositions
├── decisions.md              # the D6 ruling, the registry_read_ok optional-vs-required ruling,
│                             #   the exclusion list as BINDING rulings
├── fold-ledger.md            # every fold/advisory with its exact fold site
├── requirements.md           # (optional) FR/AC for the tool surface
├── research-findings.md      # this file
└── evidence/                 # (created by the implementation lane)
```
Minimum: `plan-overview.md` + `phaseN-plan.md` + `decisions.md` + `fold-ledger.md` (the four the house cites). `research-findings.md` and `evidence-pack.md` are the lane #1 pattern.

---

## 6. Open questions the planner must adjudicate (with the evidence each needs)

1. **The six tidier Mediums** — the item list is not in the repo (§2.2). Either obtain the tidier's verbatim 27-item Medium list (dispatch ids at `notes.md:754`) or re-derive the candidate set by re-running the tidier lens over `1497bf51f..c3a003668` **with an explicit caveat that the numbering will not reproduce**. **Do not invent mappings.**
2. **D6 / DEF-3** — does this lane land the `metrics:read` retrofit (4 lines + 1 manifest line) or decline with the disclosure carried forward? The D6 trigger text (`decisions.md:655-656`) fires when a non-operator machine lane reads the door — which this lane does by construction. If it lands, the scope-cage pins move (§4.4).
3. **The `registry_read_ok` mirror shape** — required vs optional member on the FE mirror (§1.5 F1). Optional (`=== false` predicate) matches the BE's default-safe semantics and avoids touching 3 FE fixtures; required is contract-true but costs 4 fixture edits and turns the FE type-check leg into a fixture-completeness gate.
4. **The agent-tool half of the fold (F5)** — how does a metrics tool express "the registry read failed" in the door's typed error vocabulary? The door's vocabulary is closed (contract §5; `metrics.model.ts:335-411` mirrors it on the FE side) — the tool surface needs its own frozen vocabulary, or it must reuse the existing `registry_unavailable` token with a distinguishable meaning.
5. **Contract-doc amendment** — does the lane amend `docs/architecture/metrics-query-contract.md:104-106` to document the 4th member (and extend the doc pin in the same commit), or leave the doc at 3 and rely on the additive-member clause? Leaving it leaves a doc/wire divergence that the FE model spec does NOT catch (its pin parses §4.3, the FAMILY shape, not the activation block — `metrics.model.spec.ts:1-45`).
6. **The empty-vs-failed FE pin** — the existing spec asserts *"a 200-EMPTY response never renders the error banner"* (`metrics.component.spec.ts:15`). A failed registry read IS a 200-empty response. The new state needs a distinct assertion (and probably a distinct `data-test`), not a re-use of that rule.
7. **The new tool category's name + allow-list** — no metrics category exists today; `skill_get_metrics` already occupies a nearby name (`daemon/tools/_tool_registry.py:1534`). Which agents get the tool, and under which `tools.allow` category (the pin-ceremony pattern from the v0.26.x lanes)?

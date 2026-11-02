# Metrics promote-time disclosure + ceremony checklist (central-metrics-v1)

> **Status:** **FINAL** — published by the `central-metrics-v1` lane (Phase 6, task 6.5). It
> replaces the Phase-1 draft that lived in
> [`docs/architecture/metrics-query-contract.md`](metrics-query-contract.md) §7.
> **Date:** 2026-11-02 (UTC).
> **Lane:** `feature/central-metrics-v1`.
> **Verification rule (task 6.5):** every number in the disclosure text below was checked
> **against the shipped code's constants** — this is a *verification*, not a copy of the draft.
> Each item carries its verification anchor.

---

## 1. The final promote-time disclosure text

> central-metrics-v1 ships the metrics query API (`GET /api/metrics/families`,
> `GET /api/metrics/snapshot`, `GET /api/metrics/series`) **AUTH-GATED ONLY** — scope
> `maintenance:read`, **503 `AUTH_NOT_CONFIGURED` fail-closed before any registry read** — and
> deliberately does **NOT** consult `ENSEMBLE_METRICS_ENABLED`, consistent with the shipped
> `/api/metrics` text-exposition route. The unauthenticated `:9798` exposition listener REMAINS
> default-OFF (`limits.metrics_enabled=False` unchanged) — **at the BODY level: the socket still
> binds and serves a valid empty 200 while OFF**. Enabling it for Prometheus scraping is a
> **live managed-key flip** (`limits.metrics_enabled=true`, ≤ 5 s TTL, **no restart**).
> **The query door has NO kill-switch of its own — its emergency-off lever is token revocation of
> the `maintenance:read` grant.** Switch-OFF does NOT blank the query API: the registry
> accumulates ungated, so the query routes serve live data in both switch states; only the
> listener body goes empty and the census sampler's DB work stops (the F6 `ensemble_pg_*` families
> go dark with `availability.reason="census_gated"`). Emergency-off semantics are UNCHANGED and
> NARROWER than they appear: the switch never touches authenticated surfaces.
>
> **Managed keys:** the defaults, bounds, types and restart columns of the **seven** metrics
> managed keys are untouched; **one** managed key (`limits.metrics_sampler_interval_s`) receives a
> **description-only** amendment because this lane consumes it — the key is the trend-sampler's
> TICK (boot-applied), and the envelope's `window.interval_s` reports the **OBSERVED** effective
> spacing `max(sampler, publisher)` = 15 s at defaults, never the configured cadence.
>
> **Trend storage is IN-MEMORY ONLY:** an array-backed ring buffer, 16 B/point,
> `POINT_BUDGET = 750 000` ⇒ 11.4 MiB worst case; lost on restart by design — a restart is
> visible via `restart_epoch`, never a silent gap.
>
> **Scope disclosure (AM-20):** `maintenance:read` ALSO confers checkpoint-cleanup-EXECUTE
> capability (`daemon/routers/maintenance.py`). The scope split (`metrics:read`) is a NAMED
> COMMITMENT (LR-1, `decisions.md` §D6), not built in v1.
>
> **Cardinality budget (AM-26/LR-2):** the extension subset wires **16 of the 26** daemon-origin
> families (10 full + 6 partial) — the plan's pinned budget is **+274 series → post-lane 363**
> (F7c-inclusive; 361 on the ledger-rows basis). The lane's **measured closed label space** is
> **336** lane series ⇒ **425** against the 89 baseline — that is the **CEILING** the shipped gate
> measures, and it is what the gate asserts against the **global ≤2 000 cap**; the **live** count
> is at or **under** the pinned figure, because three families render short (F12's
> `ensemble_sweep_cycle_seconds` is OUT — no sweep service publishes an observed cycle duration;
> 10 of the 13 sweep lanes have no `counters()` reader and render no series; F4's manager-pool
> `max`/`available` are absent). `VOCABULARY_VERSION` bumps **1→2** (the F7 checkpoint-op
> observable-set fold + the F18 audit-outcome amendment).
>
> **FE panel copy:** "no metrics available yet" — never "metrics disabled" (AM-19(d)).
>
> **Zero migrations, zero new backend dependencies, zero new npm dependencies. No live promote in
> this lane** — the user's 3-factor nonce ceremony owns it.

### 1.1 The eight cross-cutting disclosure items, verified

| # | Item | The shipped fact | Verified against |
|---|---|---|---|
| 1 | **Query-API posture** | auth-gated only (`maintenance:read`); **503 `AUTH_NOT_CONFIGURED`** fail-closed **before any provider read**; the door never consults the switch; the listener's default-OFF is **body-level** (socket bound, empty 200); the door's emergency-off lever is **token revocation** | `daemon/routers/metrics.py:95` (`_require_auth_on`), `:452` (the gate runs before `_query_provider`); `daemon/services/metrics_query/` reads the switch **nowhere**; `daemon/config.py:785-793` (`metrics_enabled` default `False`) |
| 2 | **Zero FE deps** | the FE adds **no** npm dependency — the charts are a hand-rolled SVG primitive | `git diff 941cb6321..HEAD -- frontend/package.json` ⇒ **0 lines changed** |
| 3 | **In-RAM buffer envelope** | array-backed ring, **16 B/point**, `POINT_BUDGET = 750 000` ⇒ **11.4 MiB** worst case; window **2 h**; lost on restart by design | `daemon/services/metrics_query/constants.py` — `POINT_BUDGET: int = 750_000`, `WINDOW_SECONDS: int = 7200` |
| 4 | **Route/cage arithmetic** | the three routes **extend** the existing `/api/metrics` mount: the **declared-route** census **108 → 111**, the **mount** census stays **37**, the **class-manifest** family count stays **33**; all three carry `maintenance:read` | `tests/unit/metrics/test_metrics_query_scope_cage.py:37-40` (`DECLARED_ROUTE_CENSUS = 111`, `MOUNT_CENSUS = 37`, `CLASS_MANIFEST_FAMILIES = 33`); the lane's diff adds **exactly 3** scope-decorated `@router.get` routes (`git diff 941cb6321..HEAD -- daemon/routers/metrics.py`) ⇒ the pre-lane census was 108 |
| 5 | **Extension subset + `VOCABULARY_VERSION` 2** | **16 of 26** families (10 full + 6 partial); the exclusions and each excluded metric's named seam are published in [`docs/architecture/metrics-query-api.md`](metrics-query-api.md) §6; `VOCABULARY_VERSION` = **2** (F7's op set → `{aget, other}`; F18's audit outcome → `{ok, error, timeout}`); the catalog's §14 F7c reconciliation to **582** | `daemon/services/metrics_query/wiring/adjudication.py` — `BASELINE_SERIES = 89`, `BUDGET_SERIES = 363`, `GLOBAL_SERIES_CAP = 2000`, `REGISTERED_FAMILIES` (16); `daemon/checkpoint_metrics.py` — `VOCABULARY_VERSION` |
| 6 | **The now-wired `metrics_sampler_interval_s`** | the key was previously unwired; it is now the trend sampler's TICK. Its **description** names **both** consumers (the host-side samplers **and** the in-RAM trend-history sampler); its default (10), bounds (1–60), type (int) and restart column (boot-applied) are **unchanged** | `git diff 941cb6321..HEAD -- daemon/config.py` ⇒ a **description-only** change to `metrics_sampler_interval_s`; `daemon/config.py:825-834` |
| 7 | **The C17 docstring correction** | **DEFERRED, not shipped.** `daemon/services/job_queue_service.py:828-836` documents `orphan_released` as a cumulative counter; the code returns it **per-tick** (`orphan_released = 0`, reset each call). The one-line comment fix is disproportionate against a whole-tree Tier-2 leg (plan council W5), so it is **recorded in the fold ledger (`DEF-1`) and the deferred list** instead. The metrics layer's own accumulation is the disambiguator, so the F12 accumulation is correct regardless | `daemon/services/job_queue_service.py:828-836`; the plan set's `fold-ledger.md` (`DEF-1`) |
| 8 | **No promote in this lane** | the lane stages nothing, promotes nothing, and touches no live surface. The version slot belongs to **giter** (W9) and the flip belongs to the user's **3-factor nonce ceremony** | the plan set's `plan-overview.md` (version-slot discipline, P-R12) and the stage-prep checklist |

---

## 2. The ceremony checklist

### 2.1 Before the flip — the operator's pre-promote checks

- [ ] **The staged artifact exists and is verified.** giter's stage output is present, with the
      binary sha256 matching on disk / manifest / provenance sidecar (all three ways).
- [ ] **The migration delta is EMPTY.** No new `.sql` ships — the catalog and the plan set amend
      **no** schema. (`daemon/migrations/versions/` is unchanged.)
- [ ] **The backend dependency delta is EMPTY** and **the npm dependency delta is EMPTY**
      (`frontend/package.json` — 0 lines changed).
- [ ] **The FE-manifest delta is declared**, not silent — the frontend manifest gained the
      metrics lazy chunk (and its per-file hash cascade). The tester lane's staged verification
      re-hashes the whole payload.
- [ ] **The live release and the promote journal are untouched by the lane.** The lane's fence:
      `current ->` unchanged, `state.json`'s promote fields byte-identical.
- [ ] **No version string was minted by the lane.** The plan set names no version; giter derives
      it at stage time (W9).
- [ ] **The disclosure text above has been read.** It is the ceremony's record of what is
      changing, including the scope over-grant and the C17 deferral.

### 2.2 The flip

- [ ] **The 3-factor nonce ceremony runs** — the user confirms; the lane does not flip on its
      own authority.
- [ ] **The promote uses the staged artifact AS-IS.** A re-stage is refused by the tip-identity
      guard (exit 78) when the tip has drifted past the staged tree — that refusal is the guard
      working, not an obstacle to override.
- [ ] **The promotion runs the standard promote path** (`scripts/upgrade/promote.sh live`, with
      the version supplied from the staged artifact — **not** from this document).

### 2.3 After the flip — the watch items

- [ ] **`/livez` and `/readyz` return 200** and the reported version is the promoted one.
- [ ] **The query door answers on all three routes** with a valid envelope:
      `schema_version`, `vocabulary_version` (2), `generated_at_utc` (aware UTC),
      `restart_epoch`, `activation.query_api = "auth-gated-always-on"`.
- [ ] **The auth seam is fail-closed** — an unauthenticated call gets **401**, and an
      auth-not-configured deployment gets **503 `AUTH_NOT_CONFIGURED`** (never a 200 body).
- [ ] **The listener is still body-level OFF** — `:9798` answers **200 with an empty body**, and
      the socket is bound. Enabling it is a **separate, deliberate** managed-key flip.
- [ ] **The FE Metrics page loads** at `/metrics` (gear entry present), the three tabs render,
      and the Overview/Charts polls return data.
- [ ] **The F6 census families** report `availability` honestly — `census_not_running` on a
      non-PostgreSQL runtime, `census_gated` only when the switch is OFF. They must **never**
      report a zero-as-a-value.
- [ ] **A restart is visible** — after a daemon restart the `restart_epoch` changes, and the
      trend window starts empty rather than continuing a stale line.
- [ ] **The discovery check** — the 16 wired families appear in `/api/metrics/families`, and
      every OUT metric from the published OUT-list is **absent** from the registry and from the
      `/metrics` body.
- [ ] **The scope over-grant is understood by the operator** — `maintenance:read` confers
      checkpoint-cleanup-execute; the emergency-off lever is **token revocation**, not a
      kill-switch.

### 2.4 If something must be undone

- [ ] **The door's emergency-off is token revocation** of the `maintenance:read` grant — there is
      no kill-switch to flip, and none should be invented.
- [ ] **Rolling the release back** is the promote-machinery's path (the auto-rollback is armed
      by the promote transaction); the metrics surface itself needs no rollback-specific step —
      the query door is additive and the in-RAM buffer holds no durable state.

---

## 3. Where the evidence for each claim lives

| Claim class | Evidence |
|---|---|
| Per-leg gate results, the mutation→test tables with observed digests, the re-derived budget | the lane's evidence pack — `.agents/shared/planning/central-metrics-v1/evidence-pack.md` |
| The frozen contract's own evidence blocks | [`docs/architecture/metrics-query-contract.md`](metrics-query-contract.md) §8 and the per-phase sections |
| The catalog's names, vocabularies and counts | `.agents/shared/planning/metrics-collection/metric-catalog.md` §14 (the ledger) |
| The vendored pull-seam evidence | `.agents/shared/planning/central-metrics-v1/inventory-vendor/` (+ `PROVENANCE.md`) |
| The plan, the rulings and the fold record | `.agents/shared/planning/central-metrics-v1/{plan-overview,decisions,fold-ledger}.md` |

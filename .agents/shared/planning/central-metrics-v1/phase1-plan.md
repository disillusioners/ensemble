# Phase 1: Posture, Activation & the Frozen Contract Envelope

Status: pending · Tier **0** (docs-only diff; the declared D1 pins execute in Phases 2/3)
**FOLDED 2026-11-02:** architect v2 (`architecture-recommendation-v2.md`) amendments folded — amendment ids cited inline as `(AM-n)`; leader rulings as `(LR-n)`. Tier **unchanged**.
Feeds: architect → plan council → fresh approver → implementation

## Objective

Freeze the **activation posture** for all three metrics surfaces and the **contract envelope** the door will
serve, without shipping a single configuration change. This phase produces the two artefacts every later
phase implements against: (a) the posture/off-state matrix plus the promote-time disclosure text, and (b) the
machine-checkable contract for the three JSON routes (envelope shape, error vocabulary, pagination bounds,
availability reasons, restart-epoch semantics).

**Nothing in this phase flips a flag.** D1's ruling is that `limits.metrics_enabled` keeps `default=False`
(`daemon/config.py:785-793`), the seven managed keys are untouched, and the query routes never consult
`ENSEMBLE_METRICS_ENABLED`. Activation in this lane is **disclosure + FE OFF-state semantics**, not a
posture change.

## Tasks

| # | Task | Depends On | Acceptance | Traceability |
|---|---|---|---|---|
| 1.1 | Write the **per-surface activation matrix**: listener body (switch-gated, default-OFF **at the body level — the socket still binds and serves a valid empty 200 while OFF**, AM-19(a)), `/api/metrics` text (auth/scope only, no switch consultation), query routes (auth/scope only, **never switch-gated — D-11b/D1(b) wins**), census DB work (switch-gated), registry accumulation (ungated, always), and the **FE charts polling** surface (the third activation surface, leader F6): the FE polls the door's `snapshot` (10 s) and `series` (15 s) routes; when the door is unreachable or the session has expired the tab **degrades to its cached/last-good view, marked stale** (per `phase5-plan.md` task 5.4) rather than polling harder or blanking; the FE never polls the listener surface. Cite the switch's **complete check-site census** (two sites) as the evidence; demote the two-class rule to **supporting precedent** and state the load-bearing basis instead (intra-family consistency + the verified census — AM-19). | — | The doc names **all six surfaces** (listener body · `/api/metrics` text · query routes · census DB work · registry accumulation · **FE charts polling**) with file:line anchors; it states that the switch never touches authenticated surfaces; and it states the door's OFF semantics as **availability, not emptiness** | FR-D1, FR-D2, FR-D3, FR-D4 · **D-11 (AM-17)** · D1(a), D1(b), D1(c) · AC-D.1, AC-D.2 · research §1c/§1d · seam: `metrics_gate.py:488-497`, `metrics_listener.py:408-419` |
| 1.2 | Write the **kill-switch blast-radius statement** (narrower than it appears) + the emergency-off semantics for the *new* door. | 1.1 | States that switching OFF does not blank the query door, that only the listener body empties and the census sampler stops, and that F6 goes dark with `availability.reason="census_gated"` | D1(d) disclosure · FR-D3 · AC-D.1 |
| 1.3 | Resolve **FR-E5 vs D1** (flag D-8, CONFIRMED by the architect) as an **availability-driven** FE empty state: the FE shows the panel when **no family is live**, with the envelope's `activation` block naming the listener/census state. **Copy fix (AM-19(d)): the panel says "no metrics available yet" — never "metrics disabled"** (the old copy was itself the false banner D-8 forbids). | 1.1 | The contract states the panel's trigger precisely, forbids the FE from inferring "disabled" from an empty series set alone, and fixes the copy | FR-E5, AC-E.3, NFR-9 · D1 · flag D-8 · AM-19(d) |
| 1.4 | Specify the **envelope**: `schema_version` (QUERY_SCHEMA_VERSION = 1), `vocabulary_version` (mirror of `VOCABULARY_VERSION`), `generated_at_utc` (**aware-UTC via the house producer `timestamps.now_utc_iso()` — AM-5 drops the naive-UTC deviation**), `restart_epoch` (**= `bash_spawn_reaper.CURRENT_BOOT_ID`; `boot_epoch.get_boot_epoch()` is NOT used — DB-clock domain, None-able — AM-14**), `activation` block, and the three route payload shapes with their filters — **including the AM-5 staleness fields `last_sample_age_s` and `stale`** (the `/series` envelope's freshness metadata: a wedged sampler surfaces as staleness, never a silently frozen line) — including the `/families` **`buckets`** array (approver B3) and the per-name **`chart_role`** that carries the chart-primary roster (approver B2; the roster itself is enumerated in Phase 2 task 2.7b). Specify the **series identity** (AM-1): `(name, labels)`, labels the complete discriminator, canonical serialization = the registry's `_label_key`; the door therefore exposes the **label-aware selector** `?series=<name>{<k>=<v>,…}` alongside the name-level `?names=`. Specify **typing honesty** (AM-4): `kind ∈ {gauge, counter, histogram}` (no `summary`), `monotonic: bool` within one `restart_epoch`, histogram `value: null` + `histogram:{count,sum,buckets}` **as stored**. | — | A single contract section with one JSON shape per route, every field documented, no ambiguous field; the identity, timestamp, epoch, and typing rules each name their source | FR-A1…A5, FR-C5 · D4 · AM-1, AM-4, AM-5, AM-14 · AC-A.1, AC-A.2, AC-A.3, AC-A.4, AC-C.4 |
| 1.5 | Specify the **error vocabulary** (404 unknown family / 400 invalid label filter / **400 `invalid_cursor`** / 503 registry unavailable / 503 `AUTH_NOT_CONFIGURED` fail-closed before any registry read / 403 scope miss) and the **pagination bounds** (max families, max samples, byte ceiling) with the **cursor semantics** (AM-6: `next_cursor: string\|null`, opaque, pinning the evaluation instant + position; invalid/expired ⇒ 400 `invalid_cursor`; a byte-ceiling response is bounded + carries a cursor, never a silent truncation). Also specify the **window semantics** (AM-5: `window_s` anchors at the server's `generated_at_utc`; a cursor pins its evaluation instant; `max_points` downsample is **bucket-max**, named, never every-kth) **and the staleness fields** (`last_sample_age_s` + `stale: bool` — stale when `last_sample_age_s > 3 × effective interval`) and the **cardinality class** (AM-2: `closed\|open\|none`; `open` names render `labels: null`). | 1.4 | Every expected condition has a typed status + body; no expected condition maps to a 500; the cursor is fully specified (AC-A.8 is unsatisfiable without it) | FR-A10, FR-A11 · D4 · AM-2, AM-5, AM-6 · AC-A.7, AC-A.8 |
| 1.6 | Adopt the **normative kill-switch truth table** (architect v2 §6) as the contract for all three surfaces, with its **five pinned invariants**: (i) auth behaviour identical across switch states; (ii) registry-sourced series set identical for non-switch-derived families; (iii) the only permitted body diffs = F6 `availability` → `census_gated`, `activation.census_enabled` → false, `last_publish_age_s` growth; (iv) the check-site census over `daemon/` remains exactly **2**; (v) **no direct switch read in the query package/routes** (`census_gated` is sourced from the public `CensusSampler.gated` property reading the EXISTING gate site — AM-18). Include the auth matrix: auth-OFF ⇒ 503 `AUTH_NOT_CONFIGURED` on all three routes **before any provider read**; scope-miss ⇒ 403; no-session ⇒ 401; non-PG ⇒ F6 `census_not_running` (the switch state is NOT inferable); listener body empty-200 while OFF with the socket still bound. Specify the availability reason set with the FE's widened-union + `unknown(<value>)` fallback (AM-7). | 1.1, 1.4 | The five invariants are stated verbatim and each maps to a Phase-3 pin; no route or package in the plan reads the switch | FR-A7, FR-A8, FR-D3, FR-D4 · **D-11 (AM-17)** · D1 · AM-7, AM-18 · AC-A.6, AC-D.1, AC-D.2 |
| 1.7 | Write the **promote-time disclosure text** (the D1/D2/D3/D4/D5 disclosure set + the cross-cutting list of 8 items from `decisions.md`) with the **AM-19 corrections**: (a) `:9798` "default-OFF" is **body-level** (the socket binds and serves empty 200 while OFF); (b) "the seven managed keys are untouched" → "defaults, bounds, types and restart columns untouched; **one** managed key receives a **description-only** amendment"; (c) the door has **no kill-switch** — its emergency-off is **token revocation** (the runbook names the lever); (d) FE panel copy **"no metrics available yet"**. Include the **AM-20 scope disclosure**: `maintenance:read` also confers checkpoint-cleanup-execute capability. Include the **gate-pack declaration** (pack names, D1/D2 split, node-count projections, mutation-label namespace `P{n}-Mn`, slicing-proof obligation, pre-exclusion registry, expected-skip census) **and the PACK-REGISTRATION OBLIGATION (approver N-a)**: all **nine** gate-leg pack ids must be **registered in `.agents/tester/PACKS.md`** before any leg is executable — a leg invokes its D2 set **by pack id**, so an unregistered id makes the leg unrunnable (today `.agents/tester/PACKS.md` carries **0** of the nine). The registration ids are: `central_metrics_contract_doc` · `metrics_p2_dual_side` · `metrics_p3_dual_side` · `metrics_scope_cage_dual_side` · `metrics_p4_w1_dual_side` · `metrics_p4_w2_dual_side` · `metrics_p4_w3_dual_side` · `metrics_fe_dual_side` · `central_metrics_ledger_reconciliation`. **House division of labour:** this plan set **commits the pack ids and their declared node scopes**; the actual `PACKS.md` rows land **with the lane's evidence** (the tester lane owns the file), and the lane's evidence pack records the registered ids + their collect-only node counts. | 1.1–1.6 | The disclosure text is complete and verbatim-compatible with the decisions record; the pack declaration names every leg this lane will run | D1(d), D2, D3, D4, D5 disclosure blocks · decisions.md §Cross-cutting disclosure block · FR-F5 · AM-19, AM-20 |

**Explicitly NOT in this phase:** any `daemon/config.py` edit, any managed-key addition, any kill-switch
consultation site, any route or package code. The posture is *documented*, not *changed*.

### The normative kill-switch truth table (architect v2 §6 — adopted verbatim)

| Surface | Auth configured | Switch | Runtime | Scope | Result |
|---|---|---|---|---|---|
| `/api/metrics/{families,snapshot,series}` | OFF | any | any | any | **503 `AUTH_NOT_CONFIGURED`** — before any provider read (fail-closed) |
| the three query routes | ON | ON | any | `maintenance:read` | **200** — registry-sourced series live |
| the three query routes | ON | OFF | any | `maintenance:read` | **200** — registry-sourced series **identical** to the switch-ON case; only the permitted diffs below appear |
| the three query routes | ON | any | any | scope miss | **403** |
| the three query routes | ON | any | any | no session | **401** |
| the three query routes | ON | any | non-PG | `maintenance:read` | **200** — F6 `availability` → `census_not_running` (the switch state is NOT inferable) |
| `:9798` listener body | n/a (unauthenticated, loopback-only) | OFF | any | n/a | **200 valid empty** — no `ensemble_metrics_up`, no family series; the socket is still bound |
| `:9798` listener body | n/a | ON | any | n/a | **200** — full exposition body |

**Five pinned invariants (every one becomes a Phase-3 pin):**
1. **Auth behaviour is identical across switch states** (status codes, error vocabulary, scope decision, fail-closed seam).
2. **The registry-sourced series set is identical** for every family whose availability reason is not switch-derived.
3. **The only permitted body differences** are (i) F6 `availability` → `census_gated`, (ii) `activation.census_enabled` → false, (iii) `last_publish_age_s` growth — all by design and excluded from the invariance claim.
4. **The check-site census over `daemon/` remains exactly 2** (the listener body; the census tick).
5. **No direct switch read in the query package or the routes** — `census_gated` is sourced from the public `CensusSampler.gated` property — **created by phase-2 task 2.4a**
(a read-through of the EXISTING private `_gate()`; **no new check site**, so the census stays == 2) — and the
query package NEVER reads the switch.

 

## Gate legs

| Leg | Tier | Scope | D1 (lane-owned) | D2 (dual-side) | Mutation labels |
|---|---|---|---|---|---|
| **P1-G1** | 0 | docs-only diff (this plan set + the posture/contract doc + the disclosure text) | `central_metrics_contract_doc` — asserts the contract doc exists and names all five surfaces, all three routes, the closed availability set, the error vocabulary **and the five truth-table invariants** (≈16 nodes) | none (docs-only) | `P1-M1` (delete a surface row ⇒ pin red) · `P1-M2` (widen the availability set ⇒ pin red) · `P1-M3` (drop a truth-table invariant ⇒ pin red) |

**Node-count discipline:** the figures above are **PROJECTIONS** (AM-22); the executor re-derives them with
`pytest --collect-only -q` and records the derived numbers. Hand-maintained node counts are forbidden.

**Slicing proof obligation:** before any comparison, produce `--collect-only -q` output proving the
selected node set equals the declared pack scope.

**Pre-exclusion registry (applies here and to every later leg):** `regression_performance` (>300 s) ·
e2e specs requiring a live daemon on `:8079` (→ Release Gate) · `tests/packs/g7` (module-level `sys.exit`
collection trap — invoke via packs, never bare pytest).

**Invocations (W9/W10):** the D1 pack is the **registered pack `central_metrics_contract_doc`** (registered
in `.agents/tester/PACKS.md`, invoked by pack id — never a bare `pytest <file>`). **Mandatory recipe load:**
Tier 0 ⇒ changed-pack PASS (no regression leg); where a digest comparison is needed it is loaded from
`.agents/tester/skills/failed-digest-equality-gate/failed-digest-equality-gate.md`.

## Coupling

- **Tight with Phase 2** — the provider/envelope implementation is written against this contract; a shape
  change here re-opens the Phase-2 pins.
- **Tight with Phase 3** — the routes are thin adapters over exactly these shapes.
- **Loose with Phase 5** — the FE mirrors the envelope; the FE model is a verbatim mirror, so a shape
  change propagates (Phase 5 exit).
- **Tight with Phase 6** — the disclosure text produced here is the source for the stage annotation.
- **Independent of Phase 4** — family wiring does not depend on the contract.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| A later gate reopens a contract decision (e.g. the architect prefers a separate `/api` family) | Rework of Phases 2/3/5 + a class-manifest bump | The contract is frozen here so the change is cheap to detect; the class-manifest stays 33 **only** under D4 Option A — if the architect picks Option B, the bump is one line plus a new scope row (flag D-9). |
| The disclosure text drifts from the code it describes | Ceremony-time contradiction | The disclosure is written **after** the shape is frozen and re-verified in Phase 6 against the shipped envelope constants. |
| The contract implies a switch consultation somewhere ("return empty when OFF") | Contradicts D1; truth-table regression | The contract states explicitly: the query door never consults the switch; the OFF state is expressed as **availability**, not as an empty response. |

## Exit criterion

The posture matrix, the contract (identity/typing/timestamp/epoch/bump-policy/cursor rules included), the
error/pagination vocabulary, the availability model, the **normative kill-switch truth table with its five
invariants**, the disclosure text (AM-19-corrected) and the gate-pack declaration exist as reviewable
documents; the P1-G1 leg is green; **zero** shipped code changed (the diff is docs-only); and the architect's
adjudications (v2 §2/§3 + LR-1..LR-3) are recorded as folded, with no open question left unowned.

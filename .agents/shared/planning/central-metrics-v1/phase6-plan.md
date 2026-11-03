# Phase 6: Docs, Evidence & Stage-Prep Closure

Status: pending · Tier **0** (docs-only diff → changed-pack PASS, plus the ledger reconciliation check)
**FOLDED 2026-11-02:** architect v2 amendments folded — ids cited inline as `(AM-n)`; leader rulings as `(LR-n)`. **Tier unchanged**.
Feeds: architect → plan council → fresh approver → implementation → giter staging

## Objective

Close the **ledger** (the catalog is the single source of truth for names, vocabularies and counts), publish
the **docs** (the door's contract, the posture, and the OUT-list with each excluded metric's named greenfield
seam), assemble the **evidence** every downstream gate expects, and prepare the **stage** — with two
hard prohibitions: **no version is chosen** (W9: giter derives it at stage time) and **no promote is
performed** (the user's 3-factor nonce ceremony owns it).

## Tasks

| # | Task | Depends On | Acceptance | Traceability |
|---|---|---|---|---|
| 6.1 | **Vendor the evidence (AM-24)**: copy the three `metrics-unification` inventory files from the shared main checkout (READ-ONLY — **copy out, never modify**) into `.agents/shared/planning/central-metrics-v1/inventory-vendor/`, each with a **provenance header** (source path, untracked status, evidence ref `origin/latest@62627265f`, vendoring date) and byte-identical content below the header; record the per-file sha256 table + the verification recipe in `inventory-vendor/PROVENANCE.md`. Confirm the source directory is still untracked and unmodified (`git status --porcelain` + unchanged mtimes). This is already staged in the plan set; the implementation lane verifies it. | none (6.1 is a PRECONDITION of 6.6 — the vendored evidence must exist before the evidence pack cites it; approver N4) | The vendored copies exist with provenance headers; each body is byte-identical to its source; the sha256 table matches; the shared checkout is untouched | AM-24 · M-10 · plan-overview §Provenance |
| 6.2 | **Derivation recipe for the 89/87 baseline (R-a — standalone, no archaeology).** (1) Read catalog `§1`'s **daemon-origin total** (`:224` → **582** = "F0.1→F21 + F7b + F7c") and its **§1 reconciliation trail** (`:251`): `538 (retired pre-reconciliation baseline) + 1 (F0.2) − 15 (F10) + 14 (F11) − 2 (F13) − 1 (F15) − 1 (F21) + 46 (F7b) + 2 (F7c) = 582`; confirm §1's **phase row-sum** (`:232`: P0 28 + P1a 63 + P1b **265** + P1c 226 = 582). (2) Read catalog **§14.1**'s total row (`:1057`) — it enumerates "F0.1–F21 + F7b" (**no F7c**) — and **§14.2**'s per-phase rows (`:1783-1790`: P1b = **263**, i.e. 265 − 2) and trail (`:1075`, no `+2 (F7c)`) ⇒ **the §14 ledger is stale-by-2**; §14.3 has no F7c row. (3) **Which number wins: 582** — §1 (the canonical name registry) carries an explicit **F7c row worth 2 series** (`:206`) and its own arithmetic includes the `+2 (F7c)` term; the 580 is the F7c-omitting carry-forward from the F7b retro-cataloguing (C95). (4) **How 89/87 fall out:** the *registered-today* set = F0.1 16 + F0.2 3 + F0.4 1 + F7-today 21 + F7b 46 + **F7c 2** = **89** (F7c-inclusive, the §1 basis); the same set **minus F7c's 2** = **87** (the §14-ledger basis). **Both denominators are pinned and both are published.** Then update the **§8 annex / §7 roll-up / §7.1 task-owner rows** for `VOCABULARY_VERSION` 1→**2** (the freeze-marker bump) and the **§14.1/§14.2/§14.3 ledger** with the measured post-lane delta. **F7c RECONCILIATION (AM-26 — the prerequisite; outcome folded here):** §1's canonical table carries an explicit **F7c row (2 series)** and its own delta trail sums to **582** (it includes the `+2 (F7c)` term; §1's phase row-sum is P0 28 + P1a 63 + P1b **265** + P1c 226 = 582), while §14.1's total row enumerates "F0.1–F21 + F7b" (F7c **absent**), §14.2's P1b row is **263** and its trail omits the `+2 (F7c)` term, and §14.3 has no F7c row ⇒ **582 WINS** (the ledger rows are stale-by-2: the F7b retro-cataloguing carried F7b's 46 but never F7c's 2). **Amendment:** §14.1 total → **582** with the family enumeration "F0.1–F21 + F7b + F7c"; §14.2 P1b 263 → **265** and the trail gains `+2 (F7c)`; §14.3 gains the F7c row (2). **Baseline denominators pinned: 89 (F7c-inclusive) / 87 (ledger-rows basis)** — both appear in the docs. Counts are written as **pointers** into §14 (the committed register's Rule 2 / M-2 discipline). | Phases 2 & 4 | The ledger reconciles with the measured per-family counts; §14.1/§14.2/§14.3 carry F7c; both baseline denominators are stated; the §8 annex records the vocabulary bump | FR-F7, NFR-5 · D5 · **AM-26** · plan-overview *Re-derived budget* |
| 6.3 | **SUPERSEDED-BY-6.2** (plan council W3) — this task was a near-duplicate of 6.2 (both said "update the §8 annex / §7 roll-up / §7.1 task-owner rows for VOCABULARY_VERSION 1→2 and the §14 ledger"); the pre-AM-26 artifact is folded into **6.2**, which now carries the full §8/§7/§7.1/§14.1/§14.2/§14.3 amendment set **plus the standalone 89/87 derivation recipe (R-a)**. **No separate work, no separate gate weight; the task id is retained for traceability only.** | 6.2 | Nothing is lost: every element of the original 6.3 is inside 6.2; a reviewer diffing 6.2 vs 6.3 finds no uncovered item | plan council W3 · R-a |
| 6.4 | Publish the **docs**: (a) the query API page — the three routes, the envelope, the error vocabulary, the pagination bounds + cursor, the availability reasons, the `restart_epoch` semantics, the **non-Prometheus deviations clause** (AM-8), and the Prometheus-text coexistence statement (the door is **not** Prometheus-compatible by design); (b) the **posture/runbook** section — **the three activation surfaces, named together: the query API (auth-gated, always serving), the trend buffer (in-RAM, accumulating in both switch states), and the FE charts polling (degrading to its cached/stale view, never blanking)** — plus per-surface activation, how to enable the exposition listener for Prometheus (a live managed-key flip, ≤5 s TTL, no restart), the statement that the door is auth-gated and always-on, that the listener's default-OFF is **body-level** (the socket binds and serves empty 200 while OFF — AM-19(a)), that **the door has no kill-switch and its emergency-off lever is token revocation** (AM-19(c)), and that the seven managed keys keep their defaults/bounds/types/restart columns with **one description-only amendment** (AM-19(b)); (c) the **scope over-grant disclosure** (AM-20): `maintenance:read` also confers checkpoint-cleanup-execute capability — with the **LR-1 named commitment** (the deferred `metrics:read` split + its retrofit map) stated as binding guidance for the deferred lanes; (d) the **OUT-list** with each excluded metric's named greenfield seam (F2 with its verbatim flip condition, F7 `_charge_checkpoint_write`, F8 `versions_per_thread`, F11 claim latency, F12 cycle seconds, F13 wake/lifecycle, F18 `session_total`, F21 op families, F19/F20/F22, the Tier-3 LLM families, breakers/token buckets, badge latency); (e) the **citation rule**: committed docs cite the **vendored** `inventory-vendor/` copies (AM-24), never the untracked main-checkout path alone. | Phases 1–5 | The docs are linked from the catalog and the plan set; the OUT-list names a seam for every excluded metric; the vendored-citation rule is applied | FR-G1…G4 · D1(d), D2, D3, D4, D5 disclosures · **AM-8, AM-19, AM-20, AM-24, AM-25** · LR-1 |
| 6.5 | Finalise the **promote-time disclosure text** (Phase 1 draft → final) and the ceremony checklist (the eight cross-cutting disclosure items: query-API posture · zero FE deps · in-RAM buffer envelope · route/cage arithmetic · extension subset + `VOCABULARY_VERSION` 2 · the now-wired `metrics_sampler_interval_s` · the C17 docstring correction · **no promote in this lane**). | 6.4 | The text matches the shipped code's constants (a verification step, not a copy) | decisions.md §Cross-cutting disclosure block · FR-D1 · D1(d) |
| 6.6 | Assemble the **evidence pack**: per-leg tester results with the declared tier / D1-D2 split / slicing proof / pre-exclusion record / **expected-skip census** (AM-22); the mutation→test tables with the observed digests; the re-derived budget table (measured, not projected); the **F7c reconciliation outcome** (582 wins; the ledger rows were stale-by-2); the F0.3 **WS-A ancestry verdict** (rc + branch: PASS / defer+disclose −8 / STOP); the F21 **conditionality evidence**; the disposition of every plan-overview flag (D-1…D-11) **and every disclosed adjudicated deviation — all EIGHT rows, DEV-1…DEV-8** (approver NB3) — with the architect's / approver's ruling; the two frozen-contract **structural** diffs (with the 3-carrier allowlist applied); the **vendor provenance table** (AM-24); and the FE **manifest delta** (AM-23). | Phases 2–5 + **6.1** (the vendored evidence must exist before the pack cites it — approver N4) | Every leg has evidence; every flag and deviation has a disposition; the structural diffs are attached; the vendor provenance and FE-manifest delta are recorded | FR-F5, FR-F6 · plan-overview *Disagreement flags* · L2–L10 lane criteria |
| 6.7 | **Stage-prep checklist** (no version, no promote): confirm the migration delta is **empty** (no new `.sql` — AD1), the backend dependency delta is **empty**, the npm dependency delta is **empty** — **HISTORICAL (the central-metrics-v1 lane): the npm-dependency delta is no longer empty against a post-D-1 base; the FE dependency posture changed deliberately (D-1, 2026-11-03 — `chart.js` + `jest-canvas-mock`, governance amendment in the same commit). Take the diff against THIS lane's base; confirm the **vendored inventory** is present with its provenance headers and hash table (AM-24) and that the **FE-manifest delta** is declared (AM-23); confirm the live release and the promote journal are **untouched** by the lane; confirm the plan-file manifest matches the tree; hand the version slot to giter (W9). | 6.6 | Every checklist item ticked **except** version + promote (both are explicitly handed over, not performed) | C-7 · AD1 · **AM-23, AM-24** · L1, L5, L8, L9 · house rules (no version before stage) |
| 6.8 | **Reconcile the plan set with reality**: update `plan-overview.md`'s re-derived budget with the **measured** numbers (replacing the projections), record the final verdict for every open question, and refresh the plan-file manifest. | 6.7 | The plan set's numbers match the shipped measurement; the manifest lists exactly the files that exist | plan-overview *Re-derived budget* + *Plan-file manifest* · L3 |

## Gate legs

| Leg | Tier | Scope | D1 (lane-owned, HEAD-only) | D2 (dual-side, comparable) | Expected D1 / D2 nodes |
|---|---|---|---|---|---|
| **P6-G1** | **0** | docs-only diff (catalog + docs + plan set) | `central_metrics_ledger_reconciliation` — asserts the catalog §8.10/§14.4/§14 values equal the shipped code's runtime values and the plan set's measured budget (≈12 nodes) | the doc/link checks for the touched pages (if the repo runs any) | ~12 / ~0 |

**Mutation→test table (stub):**

| Label | Mutation | Must go RED |
|---|---|---|
| `P6-M1` | catalog §8.10 reverts to `{ok, error}` | `central_metrics_ledger_reconciliation` |
| `P6-M2` | catalog §14.4 reverts to the stale 4-value set | `central_metrics_ledger_reconciliation` |
| `P6-M3` | the §14 ledger total is edited to a stale number | `central_metrics_ledger_reconciliation` |
| `P6-M4` | the disclosure text drops the "no promote in this lane" line | `central_metrics_ledger_reconciliation` |
| `P6-M5` | the OUT-list loses a named seam | `central_metrics_ledger_reconciliation` |
| `P6-M6` | the stage-prep checklist gains a "promote" tick | `central_metrics_ledger_reconciliation` |
| `P6-M7` | a version string is added to the plan set | `central_metrics_ledger_reconciliation` |
| `P6-M8` | the migration delta becomes non-empty without an amendment | `central_metrics_ledger_reconciliation` |
| `P6-M9` | §14.1/§14.2/§14.3 left at 580 / P1b 263 / no F7c row (the stale-by-2 carry-forward) | `central_metrics_ledger_reconciliation` (AM-26) |
| `P6-M10` | a vendored inventory file's body altered (sha mismatch vs the source) | `central_metrics_ledger_reconciliation` (AM-24) |
| `P6-M11` | the docs cite the untracked main-checkout path instead of the vendored copy | `central_metrics_ledger_reconciliation` (AM-24) |
| `P6-M12` | the FE-manifest delta missing from the stage-prep checklist | `central_metrics_ledger_reconciliation` (AM-23) |

**Slicing proof:** the reconciliation pack's selected node set equals its declared scope.

**Pre-exclusion registry:** `regression_performance` (>300 s) · e2e needing live `:8079` (→ Release Gate) ·
`tests/packs/g7` (packs only).

**Invocations (W9/W10):** the reconciliation pack is the **registered pack
`central_metrics_ledger_reconciliation`** (registered in `.agents/tester/PACKS.md`, invoked by pack id).
**Mandatory recipe load:** Tier 0 ⇒ changed-pack PASS (no regression leg, no digest comparison);
where a digest comparison is needed it is loaded from
`.agents/tester/skills/failed-digest-equality-gate/failed-digest-equality-gate.md`.

## Coupling

- **Tight with Phase 1** — the disclosure text and the posture docs.
- **Tight with Phase 4** — the ledger numbers and the vocabularies come from the measured wiring.
- **Loose with Phases 2, 3, 5** — the docs describe their surfaces; a change upstream re-opens 6.4.
- **Terminal phase** — it hands the lane to giter.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| A version string leaks into the plan set or the docs | Ceremony confusion (the version is giter's to derive at stage time) | `P6-M7`'s pin + the plan-overview's explicit no-version rule. |
| The ledger drifts from the code | Frozen-contract rot | The reconciliation pack compares the **runtime** values, not the docs' self-consistency; **12/12** mutations (P6-M1…P6-M12 — approver N1). |
| A promote is attempted "to check" | Violates C-7 and the ceremony contract | The stage-prep checklist explicitly excludes it; `P6-M6`'s pin fails if a promote tick appears. |
| The disclosure text is stale relative to the shipped constants | Ceremony-time contradiction | 6.5 verifies against the shipped constants rather than copying the draft. |
| The evidence pack omits a flag disposition | The architect/approver inherits an unresolved conflict | 6.6 requires a disposition per flag (D-1…D-11) with the architect's ruling. |

## Exit criterion

The catalog §8.10 / §8 annex / **§14.1 (→582, "F0.1–F21 + F7b + F7c")** / **§14.2 (P1b →265, trail +2 (F7c))** /
**§14.3 (F7c row)** / §14.4 are amended and reconciled with the shipped runtime values, with **both** baseline
denominators (89 / 87) pinned; the query-API, posture (AM-19-corrected), scope-over-grant (AM-20) and
OUT-list docs are published; the **vendored inventory** carries its provenance headers, hash table and
verification recipe; the promote-time disclosure text is final and verified against the shipped constants;
the evidence pack carries every leg, every mutation digest, the measured budget, the F7c reconciliation
outcome, the WS-A verdict, the F21 evidence, the FE-manifest delta and a disposition for every flag and
every disclosed deviation; the stage-prep checklist is complete **except** version and promote (both handed
over, not performed); the migration, backend-dependency and npm-dependency deltas are all empty; the live
release and the promote journal are untouched; and the plan set's numbers match the shipped measurement.

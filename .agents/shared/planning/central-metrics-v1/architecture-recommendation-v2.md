# Architecture Recommendation — central-metrics-v1 (v2, ARCHITECT-ENRICHED)

Date: 2026-11-02 (UTC)
Status: **SOUND-WITH-AMENDMENTS** — fold AM-1..AM-27 before the plan council.
Supersedes: `architecture-recommendation.md` (PLANNER-PRELIMINARY, 271 L — retained unmodified for diff provenance; this v2 is the deliverable the plan folds).
Provenance: architect review via 4-dimension worker fan-out, all read-only, evidence line-verified at worktree HEAD `941cb6321`:
`architect-worker-contract` (structural-design, 448de8b2) · `architect-worker-dataflow` (data-flow-design, 40f1d7a4) · `architect-worker-security` (security-design, 20c4f92a) · `architect-worker-tradeoff` (trade-off-analysis, 07736722). All four skills loaded (first-line confirmed); zero gaps.

---

## 0. Verdict and headline

**SOUND-WITH-AMENDMENTS.** The architecture direction is correct and now evidence-verified end-to-end: the one-door/provider-strategy seam (D4) is the right pattern; the auth-only posture (D1) survives scrutiny on stronger grounds than the plan stated; the in-RAM quick-view tier (D3/AD1) is the right split; the zero-dep FE (D2) and the strict subset rule (D3/AD3) hold. **Four defects are gate-red-on-arrival and must fold before implementation** (AM-9, AM-16, AM-21, AM-3); two are regression-class (AM-11 idle-in-transaction, AM-12 event-loop discipline); one is a value ruling (AM-25, F2 drop).

The four blocking defects:
1. **FR-C4 breach**: the pinned deque ring costs 129–161 B/point, not 64 — 92.6–115 MiB at 750 k points, **3.6× over the Must-tier 32 MiB ceiling** the phase itself cites (AM-9).
2. **AC-A.5 ↔ AC-F.4 are mutually unsatisfiable**: the VOCABULARY_VERSION bump changes the exposition body in ≥3 places; the byte-diff gate reds on arrival (AM-16).
3. **Wrong cage target**: "route-count pin 34→37" edits a docstring; the real pin is the declared-route census **108→111** — P3-M7 is vacuous and the real pin goes red (AM-21).
4. **Family-metadata Must-gap**: FR-A1/A8/FR-C2 (Must) require per-family metadata the frozen `/families` shape does not carry and the registry cannot express — "family" has no home; every later wave would edit the door (AM-3).

---

## 1. Dimension findings (aggregated, deduped by severity)

### 1.1 Contract / structure (W-A)
- The provider protocol is the correct strategy seam; FE renders dynamically from `/families` (no hard-coded list) — onboarding-friendly **once** family metadata lives at registration (AM-3).
- `?names=` cannot address a single labeled series (name-level only) — item #2's watch tool needs a label-aware selector; the provider method signatures are unspecified yet called in-process by #2 (🟡).
- `kind` advertises `summary`, which the registry never produces (`register_summary` ⇒ kind `histogram`, checkpoint_metrics.py:985); nothing declares monotonicity; histogram `value` shape undefined (🟡 → AM-4).
- No `schema_version` bump policy exists anywhere in the plan set (🟡 → AM-7).
- Cursor declared but unspecified while AC-A.8 requires it (🟡 → AM-6).
- FE availability-reason literal union will break compile on the 7th reason (🟢 → AM-7).
- D-4's verdict (F21 OUT) is right; its cited evidence is wrong — the journal event vocabulary is broader than the 5 alert kinds; phase names reach the twin only as free text (🟢 correction, AM-26 rides).

### 1.2 Data flow (W-B)
- Lock-free published read CONFIRMED (`published_snapshot()` returns an immutable fresh-dict pointer, checkpoint_metrics.py:847-904).
- Memory math OVERTURNED (measured on-host: tuple 56 B +16 GC +16 pymalloc rounding; int 32; float 24→32; deque slot 8; ≈9 B/pt block overhead ⇒ **129–161 B/pt**). FR-C4's own "16 bytes" arithmetic anticipated the array representation the plan never considered (🔴 → AM-9).
- Depth formula: wrong divisor convention (catalog-count vs registry-native ring count — 42 today over 18 live objects) and wrong interval (sampler tick 10 s, but points appear at the publisher cadence 15 s ⇒ a 720-deep ring retains **3 h**, not 2 h) (🟡 → AM-10).
- Census precedent MIS-CITED: `metrics_census.py:226-240/:418-432` opens a **one-shot `psycopg.connect` per tick and closes it** — the plan's "dedicated non-pooled connection per adapter" is a persistent connection = pool-in-disguise that recreates the v0.27.13 idle-in-transaction class (🔴 → AM-11).
- Body build + JSON encode on the event loop (phase3 3.1 as written): 36 k points ⇒ ≈8–15 ms stall per poll; house precedent serializes off-loop (`/api/work`, daemon/routers/work.py:181-193, `db_offload` seam) (🔴 → AM-12).
- `last_publish_age_s` is unimplementable as scoped — `_last_publish` is private with no accessor; P2 must add `Metric.last_publish_monotonic()` (🟡 → AM-13).
- Start-order instruction un-implementable: the listener starts FIRST (api.py:2434-2438), census second — "after census, before listener" would silently reorder the listener boot block (🟡 → AM-13).
- `restart_epoch`: the in-repo boot id is `bash_spawn_reaper.CURRENT_BOOT_ID` (uuid4().hex, import-safe, already imported by api.py); `boot_epoch.get_boot_epoch()` is a DB-clock datetime, None-able — wrong shape/clock domain (🟡 → AM-14).
- House wire-timestamp rule is **aware-UTC** (`timestamps.now_utc_iso`, the sanctioned producer); the plan's naive-UTC envelope deviates (🟢 → AM-5).
- Identity-dedupe premise correct but per-metric; GC rule must key on published-entry existence, not identity change (idle-but-live families must not drop) (🟢 → AM-13).

### 1.3 Security / posture (W-C)
- D1's OUTCOME confirmed (auth-only, no switch consultation) — check-site census verified: exactly two behavioural gate reads (metrics_listener.py:449 body; metrics_census.py:447 census tick).
- The two-class-rule citation DEMOTED to supporting precedent: it is the ProjectSkillSourceConfig rationale scoped to boot-time knobs of a different feature, and D1(c) rejects the very pattern that rule prescribes — the load-bearing basis is intra-family consistency (the existing `/api/metrics` text route is auth-only; gating three siblings would create same-prefix split-brain) plus the blast-radius census (🟡 → AM-19).
- 🔴 **Hidden Must-contradiction (D-11b)**: FR-A7/FR-D3/AC-A.6 REQUIRE switch-OFF ⇒ 200-empty on the query API — the exact opposite of D1(b); phase3 3.7 cites those requirements as traceability for a pin asserting the opposite. D1(b) WINS; requirements must be re-scoped to the listener surface (AM-17).
- 🔴 **`census_gated` forces a third check site** unless sourced from the census sampler's EXISTING gate site (public `CensusSampler.gated` property reusing `_gate()`) — otherwise the query package reads the switch, D1's two-site census goes false, and P3.7's own grep pin reds (AM-18).
- `maintenance:read` over-grant: it also gates destructive checkpoint deletion (maintenance.py:255/:279-283). Retrofit is cheap (1 taxonomy line + 1 manifest line + 4 decorator lines; cage is a subset check) — split decision is a **Decisions Pending** item (AM-20).
- No emergency-off lever for the new door; disclosure must name token revocation as the lever; `:9798` "REMAINS default-OFF" is body-only — the socket still binds and serves empty 200 while OFF (🟡 → AM-19).
- Label sweep CLEAN: every in-scope label set is a closed low-cardinality enum; no instance/job/user/thread identifiers. But Phase 4 lacks a mandatory `allowed_values`-declaration obligation (fold protection is opt-in) and F2's `thread_group` must be bucketed, never raw thread names (🟡 → AM-2/AM-27 ride; F2 dropped by AM-25).
- FE panel copy "metrics disabled" is itself the false banner D-8 forbids → "no metrics available yet" (🟢 → AM-19).

### 1.4 Trade-offs / gates / FE (W-D)
- Mechanical Tier-2 for P3 CONFIRMED verbatim against the canon (ensure.md:19 — `daemon/api.py` on the list); P2 stays Tier 1 even with AM-3's registration-API addition (additive kwargs with defaults).
- Tier-2 provisions INCOMPLETE: the v0.27.11 async-hang class (base chunks surviving `--timeout=120`) is not covered — hang-survival clause + compensating-evidence recipe must be written into phase3 NOW; budget expectation should cite the 137-min advisory-overrun precedent (AM-22).
- FE AOT mitigation REAL but budget check VACUOUS: the application builder compiles AOT unconditionally (dev config only disables optimization), but `budgets` + `outputHashing` live only under `configurations.production` — task 5.9's SCSS-budget assertion via the dev build checks nothing → add one production-config leg (AM-23).
- FE determinism: DISMISSED (content-hashed output; same-tree byte-identity is the convention; fe_index legitimately changes when the FE tree changes).
- Lifespan start-order contradiction independently confirmed (M-5); expected-skip declarations missing (no_xdist self-skip conftest.py:323-336; PG-gated packs) (AM-22).
- WS-A ancestry check: rc=1 ⇒ defer+disclose, rc≠0/1 ⇒ STOP; F0.3's family pack must be conditional (AM-24).
- Untracked-citation provenance: committed docs citing untracked main-checkout paths dangle in other checkouts → vendor or SHA-cite (AM-24).
- W-A's three findings independently re-verified against code — all reproduced.

---

## 2. Disagreement-flag adjudication (D-1 .. D-11)

| Flag | Question | Plan default | ARCHITECT RULING |
|---|---|---|---|
| **D-1** | cadence key | reuse `metrics_sampler_interval_s` | **CONFIRM-WITH-AMENDMENT** (AM-15): the key stays as the sampler TICK (boot-applied, contract-consistent — W-D), but `window.interval_s` must report the **observed effective spacing** `max(sampler, publisher)` and the ring evicts **by age** so the 2 h window stays truthful when either cadence changes (W-B's truthfulness). D5/AD4(d)'s implicit "interval_s = configured sampler cadence" is OVERTURNED — the envelope would report 10 s while spacing is 15–25 s. |
| **D-2** | budget arithmetic | plan re-derivation governs | **CONFIRM + AMEND** (AM-26): 18/38(closed, 15-obj)/217/79/+296 confirmed; D5's printed 78/+295/≈384 OVERTURNED (its own rows sum to 76); baseline 89 requires the catalog §14 F7c reconciliation (580 vs 582) FIRST; conditional rows added. |
| **D-3** | F8 prune-runs | OUT | **CONFIRM** (checkpoint_prune.py:91 is a dry-run dataclass field, not an outcome counter). |
| **D-4** | F21 phase families | OUT (conditional resolved) | **CONFIRM verdict, AMEND evidence**: the twin's `event` vocabulary is broader than the 5 alert kinds; phase names live in promote.sh free text / narrative logs, never structured in `history[]`. OUT stands, +30 not claimed. |
| **D-5** | F18 partial | PARTIAL | **CONFIRM.** |
| **D-6** | F18 vocab target | `{ok,error,timeout}` | **CONFIRM** (OUTCOME_DENIED declared+exported, never written — service.py writers all emit ok/error/timeout; over-promise exclusion applied consistently with D-7). |
| **D-7** | F7 enum | `{aget, other}` | **CONFIRM** (sole producer persistence.py:632; aput/adelete cannot fire; alist walk deleted persistence.py:628; `other` stays as the fold target — contract-honesty requirement, derivation RULE recorded so J-4 re-derives rather than re-litigates). |
| **D-8** | FR-E5 vs D1 | availability-driven empty state | **CONFIRM + copy fix** ("no metrics available yet", never "metrics disabled"). |
| **D-9** | route-count pin | 34→37 | **OVERTURN the target** (AM-21): 34→37 edits a DOCSTRING; the real pin is `test_declared_route_count_pinned` **108→111**; the mount census stays 37 (static); `api_families` stays 33. |
| **D-10** | vocab bump rides | yes | **CONFIRM the bump, OVERTURN the byte-identity premise** → folded into D-11a. |
| **D-11 (NEW)** | (a) AC-A.5 byte-identical exposition vs AC-F.4 version bump; (b) FR-A7/FR-D3/AC-A.6 Must-empty-OFF vs D1(b) serves-live | — | **ADJUDICATED** (AM-16/AM-17): (a) exposition contract = structural identity modulo an enumerated 3-carrier value-delta allowlist (version footer, version gauge, F7 HELP); (b) **D1(b) wins** — the door is never switch-gated; FR-A7/FR-D3/AC-A.6 are re-scoped to the listener surface; traceability columns re-pointed at D-11. |

## 3. Open questions adjudicated

| # | Question | Ruling |
|---|---|---|
| Q1 | F7 enum membership | **`{aget, other}`** (see D-7). |
| Q2 | F18 amendment target | **`{ok, error, timeout}`** (see D-6). |
| Q3 | F8 / F21 re-admission | **Both stay OUT** (see D-3/D-4). Re-admission = named-seam commissions with budget amendments. |
| Q4 | FR-E5 vs D1 FE contract | Availability-driven panel; copy per AM-19; no false disabled/error banners. |
| Q5 | Which convention governs ≤2000 | **Catalog convention CONFIRMED** — and strengthened: `DEFAULT_CARDINALITY_CAP=2000` is a **per-metric** label-combination cap (checkpoint_metrics.py:629-664) and **cannot** enforce a global budget; R6/P-R7 enforcement wording corrected (AM-27). |
| Q6 | P3 Tier-2 D2 scope | **ACCEPT-WITH-AMENDMENTS** (AM-22): composition is scope-faithful; add hang-class provisions, expected-skip census, 137-min budget precedent, projections marked advisory. |
| (decisions OQ-3) | F2 sequencing | **DROP F2 from v1** (AM-25): 5-axis 4.55 (drop) vs 3.40 (ship-dark); prod is Darwin-primary so darkness is Certain; re-admission is one family pack once AM-3 lands. Flip condition recorded (Linux-primary ops surface / operator need). |
| (decisions OQ-4) | sampler key | AM-15 synthesis (see D-1). |

---

## 4. Amendments (fold BEFORE plan council)

### Group A — frozen contract (Phase 1 owns the envelope; Phases 2/3/5 implement)

**AM-1 — Series identity + label-aware selector.** Series identity = `(name, labels)`, labels the complete discriminator; canonical serialization = the registry's `_label_key` (sorted `(k,v)` pairs, checkpoint_metrics.py:430). The door adds `?series=<name>{<k>=<v>,…}` (RFC-3986-escaped) alongside `?names=`; `?names=` stays name-level and returns every label variant. Ring keys and any #2 watch-tool reference use the same serialization.
*Fold:* plan-overview §frozen-contract; phase1 1.4; phase3 3.3; phase2 2.5-2.6.

**AM-2 — Cardinality class.** Each name carries `cardinality_class: closed|open|none`; `open` names (the two guard counters) render `labels: null` and are never value-enumerated. Phase 4 MUST declare `allowed_values` for every label key of every new family (a key without `allowed_values` is char-checked only and can reach the per-metric cap with raw values).
*Fold:* plan-overview envelope; phase1; phase4 4.3/4.5/4.7 (mandatory obligation + pin).

**AM-3 — Family metadata lives at registration (unblocks 4 Must requirements).** New P2 task 2.7b: registration-time keyword metadata on the registry seam — `family`, `source_idiom`, `durability`, `cardinality_weight`, `chart_role` — with derived defaults so the 18 existing registrations are untouched. `/families` serves `{id, names[…], live_series_count, availability, role, source_idiom, durability, cardinality_contribution}`. NO second family table (a static table would make every wave edit the door — breaks the incremental-onboarding fence; as scoped, `/families` would serve zero families at P3). Stays Tier 1 (additive kwargs, not on the mechanical list).
*Fold:* phase2 (new 2.7b); phase4 4.3/4.5/4.7 pass values; plan-overview envelope + AC-A.1; decisions D4.

**AM-4 — Typing honesty.** `kind ∈ {gauge, counter, histogram}` (no `summary` — the registry never produces it). Each series carries `monotonic: bool` (counters non-decreasing within one `restart_epoch`). Histogram series: `value: null` + `histogram: {count, sum, buckets}` **as stored** (not Prom-cumulative).
*Fold:* plan-overview envelope; phase1; phase2 2.3.

**AM-5 — Timestamps, window semantics, /series staleness.** All wire timestamps are **aware-UTC ISO-8601 (+00:00)** via the house producer `timestamps.now_utc_iso()` (drop the naive-UTC deviation; FE model parses aware UTC). `window_s` anchors at the server's `generated_at_utc`; a cursor pins its evaluation instant. `max_points` downsample policy is **bucket-max** (named, never every-kth). The `/series` envelope carries `last_sample_age_s` + `stale: bool` (> 3 × effective interval) — a wedged sampler must surface as staleness, never a silently frozen line. Ring duplication semantics (publish 15 s > sampler 10 s ⇒ repeated values expected) stated in the envelope doc.
*Fold:* plan-overview envelope; phase1 1.4/1.5; phase5 5.1 (model mirror).

**AM-6 — Cursor specified (minimal, opaque).** `next_cursor: string|null`; pins evaluation instant + position; invalid/expired (older than the buffer's oldest point) ⇒ 400 `invalid_cursor`; byte ceiling ⇒ bounded body + cursor, never silent truncation; families route bounded the same way. AC-A.8 is unsatisfiable without this.
*Fold:* plan-overview envelope; phase1 1.5; phase3 3.3.

**AM-7 — schema_version bump policy + tolerant FE mirrors.** Breaking envelope change (field removal/rename/type/semantic) ⇒ bump; additive (new optional field/filter/enum value) ⇒ no bump, consumers must ignore unknowns. `vocabulary_version` is a separate axis (label VALUES, never envelope shape). FE mirrors: `vocabulary_version: number` (never a literal), availability reason as widened union + `unknown(<value>)` fallback — mirrors verbatim on field sets, never values.
*Fold:* plan-overview envelope; phase1; phase5 5.1.

**AM-8 — Non-Prometheus deviations clause.** Declare explicitly: `kind` borrows Prom vocabulary but semantics are local (buckets as-stored; monotonicity within a restart_epoch only; durations in seconds). No exposition-format leakage into the JSON envelope (verified).
*Fold:* plan-overview envelope; phase1; decisions D4.

### Group B — query substrate & data flow

**AM-9 — Array-backed ring (fixes the FR-C4 breach).** Replace `deque(maxlen)` with parallel preallocated arrays — `array('d')` values + `array('q')` timestamps, head/tail ring index, O(1) drop-oldest — at **16 B/point** ⇒ 750 k points = **11.4 MiB** (2.8× under FR-C4's 32 MiB; 4.2× under the plan's own 48 MB envelope). Deque REJECTED on measured evidence: 129–161 B/pt ⇒ 92.6–115.0 MiB. Histogram point layout `(ts f64, count i64, sum f64, buckets i64[N])` = 24+8N B (N=17 ⇒ 160 B). Unit pin asserts **measured** `sys.getsizeof`-based bytes ≤ 32 MiB, not a nominal constant. (Histogram series today = 3, not "~7-9".)
*Fold:* decisions D3 sizing table; phase2 2.5; plan-overview R7.

**AM-10 — Depth formula + eviction + recompute rule.** `depth = clamp(POINT_BUDGET // max(1, ring_series), 60, window_s // effective_interval_s)` where `ring_series` = **registry-native label-combination count** (42 today over 18 live objects — NOT the catalog-convention count), `effective_interval_s = max(sampler_cadence, publisher_cadence)` (15 s at defaults; a 720-deep count-bound would otherwise retain 3 h). The ring ADDITIONALLY evicts by age (`ts < now − window_s`) so the window stays truthful under any cadence change; depth recomputes when `ring_series` changes (registration events), with the rule written down.
*Fold:* decisions D3; phase2 2.5; plan-overview §budget table.

**AM-11 — DB-pull pattern corrected (regression-class).** Each DB-backed adapter opens a **one-shot direct `psycopg.connect` per tick** (connect_timeout + application_name + statement_timeout) and closes it inside the tick — the census precedent verbatim (metrics_census.py:226-240/:418-432). **NEVER a persistent per-adapter connection** (pool-in-disguise; recreates the idle-in-transaction class cured in v0.27.13).
*Fold:* plan-overview P-R14; phase4 4.2; phase2 2.4.

**AM-12 — Off-loop body construction + serialization.** Route handlers build and encode OFF the event loop (`await db_offload.offload(...)`, the `/api/work` precedent, daemon/routers/work.py:181-193); `/series` additionally caps `names × points` (default `max_points` ≤ 120) so the off-loop body stays bounded. Threshold rationale: > ≈5 000 points ≈ > 1.5 ms on-loop; the pinned max (36 k) is 7× over.
*Fold:* phase3 3.1; plan-overview P-R1.

**AM-13 — Lifecycle order, accessor, dedupe/GC semantics.** Sampler starts **LAST** of the metrics trio (after the census block, api.py:2467-2483 — delete "before the listener start"; the listener starts first and must NOT be reordered); stops FIRST (before the listener stop, :3700-3709). P2 adds public `Metric.last_publish_monotonic()` (the envelope's `last_publish_age_s` is unimplementable against the private `_last_publish`). The "no lock" pin allowlists `registered_metrics()` (registry-level `_METRICS_LOCK`, not a metric lock). Identity-dedupe is per-metric; ring GC keys on "metric still has a published entry" (idle-but-live families are never dropped).
*Fold:* phase2 2.3/2.6; phase3 3.4.

**AM-14 — restart_epoch source.** `restart_epoch` = `daemon.services.bash_spawn_reaper.CURRENT_BOOT_ID` (per-process uuid4().hex, import-side-effect-free, already imported by api.py). `boot_epoch.get_boot_epoch()` is NOT used (DB-clock domain, None-able).
*Fold:* decisions D3/D4; phase2 2.6; plan-overview envelope.

**AM-15 — Cadence synthesis (D-1).** Sampler tick = `limits.metrics_sampler_interval_s` (boot-applied, contract-consistent). `window.interval_s` = **observed effective spacing** (measured max(sampler, publisher)), never the configured cadence. Description amended to name BOTH consumers (host-side samplers + in-RAM trend history sampler). P-R5 extended to three disclosures: (i) the sampler key is boot-applied AND is the /proc-samplers' key — cadence changes silently change count-bound retention; (ii) the publisher cadence is live-adjustable and is the BINDING rate; (iii) `interval_s` is observed. Age-eviction (AM-10) is the structural backstop.
*Fold:* decisions D3/AD4(d); plan-overview D-1/P-R5; phase2 2.6/2.8.

### Group C — posture, security & disclosure

**AM-16 — D-11a: exposition contract re-scoped.** The `:9798` and `/api/metrics` bodies are **structurally identical** to base (same series set, TYPE lines, label vocabularies, HELP structure) **except the enumerated 3-carrier value-delta allowlist**: (1) `# frozen vocabulary version: N` footer, (2) `ensemble_metrics_vocabulary_version` gauge value, (3) `# HELP ensemble_checkpoint_op_seconds …` text. Any delta outside the allowlist = gate red. Rewrites: AC-A.5 (requirements.md — commission-annotated), plan-overview objective :19-23 + L4, phase3 3.8 + P3-M8. *Fence note:* the Prom-text exposition door remains intact alongside — the version carriers are intrinsic to the ratified AD5 bump; this amendment makes the gate honest about them, it does not touch the fence.

**AM-17 — D-11b: D1(b) wins; requirements re-scoped.** The query door is NEVER switch-gated (auth-only). FR-A7/FR-D3/AC-A.6 are re-scoped to the **listener** surface (the door's OFF semantics are availability, not emptiness). New flag D-11 recorded in the plan-overview flag table; phase1 1.1 and phase3 3.7 traceability columns cite **D-11**, not the overridden requirements.
*Fold:* plan-overview (flag table, phase1/phase3 cross-refs); requirements.md AC/FR rows; phase1 1.1; phase3 3.7.

**AM-18 — Switch-invariance pin corrected + census_gated source.** The corrected pin: **auth behaviour byte-identical ON/OFF** (status codes, error vocabulary, scope decision, fail-closed seam); **registry-sourced series set identical** for every family whose availability reason is not switch-derived; the ONLY permitted body differences are (i) F6 `availability`→`census_gated`, (ii) `activation.census_enabled`→false, (iii) `last_publish_age_s` growth — all by design and excluded from the invariance claim. `census_gated` is sourced from a public `CensusSampler.gated` property reading the EXISTING gate site (metrics_census.py:332-340) — the query package NEVER reads the switch (no third check site; P3.7 grep pin passes). `activation.census_enabled = not census.gated` (honest; `.running` is switch-independent). Check-site census remains exactly 2.
*Fold:* plan-overview R4/L6; phase1 1.6; phase2 2.4/2.9; phase3 3.7.

**AM-19 — D1 evidence demotion + disclosure corrections.** Two-class rule → supporting precedent (scoped to another feature's boot-time knobs; D1(c) rejects its prescription); load-bearing basis = intra-family consistency with the shipped auth-only text route + the verified blast-radius census. Disclosure edits: (a) `:9798` "default-OFF" is **body-level** — the socket binds and serves a valid empty 200 while OFF; (b) "the seven managed keys are untouched" → "defaults, bounds, types and restart columns untouched; one managed key receives a description-only amendment"; (c) the door has NO kill-switch — its emergency-off is **token revocation** (runbook names the lever); (d) FE panel copy "no metrics available yet". Disclosure SSOT lives in P1; phase6 6.5 verifies against shipped constants.
*Fold:* decisions D1 (evidence, (c), (d)); phase1 1.7; phase5 5.7; phase6 6.4(b).

**AM-20 — Scope posture + retrofit map.** v1 keeps `maintenance:read` (zero-amendment, cage-trivial). The plan must (i) document the retrofit map (1 taxonomy line + 1 manifest line + 4 decorator lines; cage is a subset check; SESSION_OPERATOR_SCOPES auto-grants), (ii) state in the query-API docs that `maintenance:read` ALSO confers checkpoint-cleanup-execute capability, (iii) convert "revisit later" into the named commitment per Decisions-Pending #1.
*Fold:* decisions D4; plan-overview (scope note); phase6 docs.

### Group D — gates, FE & process

**AM-21 — Cage-pin correction (108→111).** Replace every "route-count pin 34→37" with: declared-route census **108→111** (`test_router_scope_manifest.py::test_declared_route_count_pinned`, :333-396); mount-census assert (==37) must show zero diff; `api_families` stays 33. P3-M7 re-pointed ("census left at 108 ⇒ RED at HEAD").
*Fold:* phase3 3.5/P3-M7/P3-G2/exit; plan-overview D-9; decisions D4 (Evidence, cage accounting, disclosure #4).

**AM-22 — Tier-2 execution provisions.** Hang-class clause (shard surviving its cap with no FAILED digest ⇒ v0.27.11 compensating-evidence recipe: scoped net-new EMPTY + HEAD digest sha pins + preserved replay); budget reported against the 137-min advisory-overrun precedent; expected-skip census (no_xdist conftest.py:323-336; PG-gated packs without PG_TEST_ALLOWED_DBS) declared per leg; node-count figures marked PROJECTION (re-derived via --collect-only).
*Fold:* phase3 whole-tree section + gate table; plan-overview §gate-doctrine.

**AM-23 — FE build legs.** Keep the dev-build AOT leg (real — the application builder compiles AOT unconditionally) and ADD one `ng build --configuration production` leg (budgets + outputHashing live only there); any budget WARN is a reported finding; declare the expected FE-manifest delta in the P6 stage-prep checklist. Determinism confirmed (content-hashed output; no action).
*Fold:* phase5 5.9; phase6 checklist.

**AM-24 — Provenance & misc hygiene.** Vendor the 3 `metrics-unification` inventory files into the lane dir (or cite by origin/latest@sha) — committed citations of untracked main-checkout paths dangle; WS-A ancestry rc semantics (rc=1 ⇒ defer+disclose −8; rc≠0/1 ⇒ STOP) and F0.3's family pack made conditional on the PASS verdict; declare the query provider is NOT a /readyz component (soak-gate grace discipline — so no implementer "helpfully" adds one).
*Fold:* plan-overview provenance; phase4 4.4 + P4-G1 row; phase3 3.4 note; phase6 6.1.

### Group E — subset & budget

**AM-25 — F2 dropped from v1.** 5-axis: drop 4.55 vs ship-dark 3.40 (dominant: Maintainability — permanently-dark shipped code is standing disclosure debt; prod is Darwin-primary so darkness is Certain; the FE dark-with-reason infrastructure ships anyway for F6/F0.3). Subset becomes **16/26** (10 full + 6 partial), delta **+274**, post-lane **363** (F7c-inclusive baseline). Re-admission later = one family pack + budget amendment (cheap once AM-3 lands). **Override hook:** if the ops surface becomes Linux-primary or the operator confirms near-term per-thread CPU need, re-admit (+22, one pack). The sampler-key-is-/proc-samplers'-key disclosure (AM-15 i) is retained for that future.
*Fold:* decisions D5 (F2 row → OUT-by-value-ruling); plan-overview AD4(c) hook + budget; phase4 cluster W1 (F2 removal); disclosure list.

**AM-26 — Budget table replaced + reconciliation prerequisite.** Pin the corrected table (below); the catalog §14 ledger must reconcile F7c (§1/§7 say 582; §14.1/§14.2 say 580 — F7c absent from §14.3's rows) BEFORE the baseline is pinned; conditional rows mandatory. D5's 78/+295/≈384 corrected to 76-rows/79-summed/+296/385→(with AM-25) +274/363.
*Fold:* decisions D5; plan-overview §Re-derived budget; phase6 6.2 (ledger).

**AM-27 — Cap-enforcement wording.** `DEFAULT_CARDINALITY_CAP=2000` is a **per-metric** label-combination cap; the global ≤2000-series budget is a **catalog §14 planning convention** enforced only by per-family label-space assertions. R6/P-R7 reworded accordingly.
*Fold:* plan-overview R6/P-R7; decisions D5.

---

## 5. Corrected budget arithmetic (authoritative, post-AM-25)

| Quantity | Plan (as written) | Corrected |
|---|---|---|
| Registered objects today | 18 (15+3) | **18** ✔ |
| Registry-native closed label-space | 38 (15 objects) | **38 over 15; 42 over 18 live** — pin BOTH denominators |
| Catalog-convention baseline | 89 | **89 (F7c-incl) / 87 (ledger rows)** — reconcile F7c first (PREREQUISITE) |
| Full-IN delta | +217 (10 fams) | **+217 ✔** (9 fams after AM-25 — F2 was partial) |
| Partial-IN delta | +79 (7 fams) | **+57 (6 fams after AM-25)**: F7 10 + F8 4 + F11 16 + F13 19 + F18 5 + F21 3 |
| Total extension delta | +296 | **+274** |
| Post-lane (catalog conv.) | 385 ≈19.3% | **363 ≈18.2%** (F7c-incl) / 361 (ledger) / 364 (D-6 4-value) / 355 (WS-A absent) |
| Bytes/point | 64 B (deque) | **16 B (array)** — deque rejected at 129–161 B measured |
| Buffer worst case | ≈48 MB | **11.4 MiB** (750 k pts, array) vs FR-C4's 32 MiB ceiling |
| Effective point interval | 10 s (sampler) | **max(publish 15 s, sampler 10 s) = 15 s**; observed 15–25 s |
| Points per 2 h window | 720 | **≈480**; 720-deep count-bound retains 3 h (age-eviction fixes) |
| Live histogram series | "~7-9" | **3** (op_seconds, read_size_bytes, exposition_seconds) |
| Cap that mechanically binds | "≤2000 series" | **per-metric 2000 label-combos**; global budget = §14 convention |
| Fully-wired catalog ceiling | ≈1145 | unchanged; registry-native proxy 822 (§14.1) — re-measure at family-pack time |

## 6. Corrected kill-switch truth table (normative)

Adopted verbatim from the security worker's verified table (auth-configured × switch × runtime × scope), with five pinned invariants: (i) auth behaviour identical across switch states; (ii) registry-sourced series set identical for non-switch-derived families; (iii) only permitted body diffs = F6 availability flip, `activation.census_enabled`, `last_publish_age_s`; (iv) check-site census over daemon/ remains exactly **2**; (v) no direct switch read in the query package/routes (`census_gated` via `CensusSampler.gated`). Auth-OFF ⇒ 503 `AUTH_NOT_CONFIGURED` on all three routes before any provider read; scope-miss 403; no-session 401; non-PG ⇒ F6 `census_not_running` (switch state NOT inferable); listener body empty-200 while OFF with the socket still bound. Full table in the security worker report; fold into phase1 1.6 + phase3 3.7.

## 7. Planner defaults — confirmed vs overturned

**Confirmed (17):** D1 posture outcome (auth-only, no switch) · D2 zero-dep FE · D3 in-RAM/no-migrations (representation amended) · D4 one-family/three-routes/provider (metadata amended) · D5 subset rule + every family verdict (D-3/D-4/D-5) · D-6 {ok,error,timeout} · D-7 {aget,other} · D-8 availability-driven FE · D-10 bump rides · AD1 in-RAM · AD2 same-release FE · AD3 strict subset · AD4(a) amend-not-fold · AD4(b) F21 conditional resolved OUT · AD4(d) key reuse (as synthesized) · AD5 bump (premise corrected) · Q5 catalog convention · Q6 accept-with-amendments.

**Overturned / materially amended (10):** D-9 target (108→111, not 34→37) · D-1's interval_s semantics (observed, not configured) · D3 sizing (deque→array; FR-C4 breach) · P-R14 pattern (one-shot per tick, not persistent) · byte-identity premise (D-11a structural + allowlist) · FR-A7/FR-D3/AC-A.06 as applied to the door (D-11b: D1(b) wins, requirements re-scoped) · AD4(c) F2 (dropped from v1) · R4/L6 pin (corrected invariance) · "seven managed keys untouched" phrasing · restart_epoch source + start-order instruction + D5's 78/+295/≈384 arithmetic.

## 8. Risk register deltas (additions/changes)

M-1 Tier-2 hang survival 🔴 → AM-22 · M-2 byte-contract contradiction 🔴 → AM-16 · M-3 pin target 🔴 → AM-21 · M-4 family metadata 🔴 → AM-3 · M-5 lifespan order 🟡 → AM-13 · M-6 FE budget vacuity 🟡 → AM-23 · M-7 disclosure divergence 🟡 → AM-19 (SSOT) · M-8 ancestry rc semantics 🟡 → AM-24 · M-9 expected-skips 🟢 → AM-22 · M-10 untracked citations 🟢 → AM-24 · M-11 /readyz exclusion 🟢 → AM-24 · M-12 FE manifest delta 🟢 → AM-23. Plus from W-B: FR-C4 breach 🔴 → AM-9; idle-in-txn regression 🔴 → AM-11; on-loop stall 🔴 → AM-12; census_gated third-site 🔴 → AM-18; window-truthfulness traps 🟡 → AM-10/15.

## 9. Decisions pending (leader/council) & open questions

**Pending:**
1. **metrics:read scope split — now or named commitment?** Retrofit is cheap (4 lines + manifest), but grant hygiene bites iff a non-operator machine lane (e.g. a Grafana/#3 token) will read the door in this lane's lifetime. If yes → mint `metrics:read` now (family manifest `"maintenance:read|metrics:read"`, three new routes declare it, text route keeps `maintenance:read`). If no → v1 keeps `maintenance:read` with AM-20's documented retrofit map + docs disclosure.
2. **F2 drop confirmation** — my ruling is DROP (AM-25) with the flip condition stated; AD4(c) reserved this as a value call, so the leader/council may re-admit (+22, one pack).
3. **requirements.md re-scope ownership** — AC-A.5/FR-A7/AC-A.6 edits (AM-16/17) are plan-set files the planner folds; if the commission treats requirements as frozen inputs, the deviations must instead be recorded as disclosed, adjudicated deviations in the gate evidence.

**Open (non-blocking):** post-lane registry-native ring count (re-measure at family-pack time; 822 proxy until then) · exact per-request cost of the 36 k-point encode (order-of-magnitude only; the off-loop amendment makes it moot for the loop).

## 10. Confidence

**High** on all Confirm/Overturn rulings (every load-bearing claim independently line-verified by ≥1 worker; W-A's three headline findings reproduced by W-D). **Medium** on AM-25 (F2) — flip condition stated. The recommendation flips from SOUND-WITH-AMENDMENTS to REJECT only if the requirements doc is immutable AND the deviations cannot be recorded — an administrative, not architectural, condition.

*End of v2. The planner folds AM-1..AM-27 into the plan set before the plan council; this file is the architect's record of what was verified, what was wrong, and what must change.*

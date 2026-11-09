# Architecture Recommendation — skill-system-revival Phase 1 (3-Ruling Addendum)

**Date:** 2026-11-09
**Base:** `ae5bbafed` (= `origin/latest`), worktree `/home/nea/ensemble-src-wt-skill-revival-phase1`
**Scope:** three architectural rulings only (A2 / B / E4). E3/A3/A4 are explicitly out of scope (planner revises those on the census).
**Instances:** `93b5b90b` (data-flow-design), `5c7eeb62` (security-design), `2c9c1d17` (trade-off-analysis)
**Evidence standard:** every claim carries `file:line` from the worktree; claims contradicting the plan are re-verified by the architect and marked **[VERIFIED BY ARCHITECT]** where so.

---

## Verdict Summary

| # | Ruling | Verdict in one line |
|---|--------|---------------------|
| 1 | **A2 — double inversion** | Fix at the **fact owner** `metrics_census.py:335` (`return not self._gate()`) — NOT at `availability.py`; the plan's layer is right, the plan's "no pin flips" and "five doc-pins require the tokens" claims are **wrong** and must be amended. |
| 2 | **B — confinement** | All three named pieces are **NOT BUILT / DEFERRED** (not "landed-but-unwired"). Phase-1 completion = **BUILD one opt-in slice** (filesystem_scope), **KEEP two deferrals** (master switch, direction quarantine), **WIRE one gate** (inline `skill_evolve` at the lane gate). The exit criterion is satisfiable **at promote** via B1+B2 without overturning D6/D7/D8. |
| 3 | **E4 — deactivation** | Adopt the plan's **(b) boot reconciliation + (c) operator receipt + E4.3 injection filter**, with four mandatory refinements: dual-column writer, **file** receipt, **process-once suppression** (reactivation-vs-next-boot), and an **A/B-strand refusal guard**. Substantive set is **R1 (2 rows) + R3**, not "~10". |

---

# RULING 1 — A2: the double inversion, fixed at the resolution layer

## The question

Families report `dark/census_gated` while publishing live series. Fix the polarity at the "resolution layer, not the label"; correct the swapped R-i/R-ii references so code and disclosure agree.

## Evidence (chain, verified)

| # | Site | Code | Value |
|---|------|------|-------|
| 1 | `daemon/services/metrics_census.py:322-335` | `gated` property; docstring "gated OFF" (`:324`); body `return self._gate()` (`:335`) | returns the **ENABLED** sense — **[VERIFIED BY ARCHITECT]** |
| 2 | `daemon/services/metrics_census.py:347-355` | `_gate()` → `metrics_gate.metrics_enabled()` (`:355`) | `True` = census ENABLED (source of truth, not inverted) — **[VERIFIED BY ARCHITECT]** |
| 3 | `daemon/services/metrics_query/provider.py:109-122` | `return not bool(self._census.gated)` (`:120`) | pre-fix: enabled → `census_enabled=False` — **[VERIFIED BY ARCHITECT]** |
| 4 | `daemon/services/metrics_query/provider.py:142-148` | `census_gated=not self.census_enabled` (`:145`) | pre-fix: `True` while the census runs — **[VERIFIED BY ARCHITECT]** |
| 5 | `daemon/services/metrics_query/availability.py:89-97` | `if facts.census_gated: dark/census_gated` (`:91-92`) | consumes the inverted fact — **[VERIFIED BY ARCHITECT]** |

**Naming correction (premise conflict resolved).** The commission brief calls `availability.py` "the resolution layer". The CODE says otherwise:
- **Fact owner (resolution layer):** `CensusSampler.gated` — the public read-through of `_gate()`, explicitly built to add **no third check site** (`metrics_census.py:326-333`).
- **Label producer (disclosure layer):** `availability.py:65-98` — reads `AvailabilityFacts`, emits `live` / `dark`+reason.
- `availability.py:83-88` itself states the fix is out of lane ("this lane only reads the PUBLIC property; the two-check-sites invariant"). Fixing there would be **fixing the label** — exactly what the commission forbids — and would leave `census_enabled`/`census_gated` inverted on the wire for every other consumer.

## Ruling

**R1.1 — Fix site.** `daemon/services/metrics_census.py:335`: `return self._gate()` → `return not self._gate()`. One line. Adds no check site (still calls the existing `_gate()`), so the behavioural check-site census stays at **2** (`metrics_census.py:355` + the listener site, worker-cited `metrics_listener.py:419` — the second site is worker-cited, not independently re-opened by the architect).

**R1.2 — Wrong-site consequences.**
- **provider.py fix** (`census_enabled = bool(gated)`): flips every `_FakeCensus`-based pin; the fakes already encode the corrected contract (`gated=True` ⇒ gated-off ⇒ dark). Requires rewriting the metrics suite; leaves the lying property in place.
- **availability.py fix** (flip the `if facts.census_gated`): label-only; leaves the inverted facts on the wire (`envelope.py` exposure) and violates the lane's own invariant note. It would "go green" while the fact stays wrong — the definition of a dishonest fix.

**R1.3 — R-i / R-ii.** Definitions: **R-i = wire `capture_active`** (never wired: `daemon/api.py:2571-2576` omits `capture_active=`); **R-ii = census `gated` polarity**. Authoritative assignment: `docs/architecture/metrics-promote-disclosure.md:86-95`.
- **Swapped references:** `availability.py:83-88` labels the polarity **R-i** → must say **R-ii** **[VERIFIED BY ARCHITECT]**. Also swapped in developer comments: `tests/unit/metrics/test_metrics_query_provider.py:367, :370, :389, :403, :420` (all say R-i for the polarity) **[VERIFIED BY ARCHITECT — grep]**. `tests/unit/metrics/test_metrics_read_personas.py:26` uses R-ii correctly.
- **Corrected wording (availability.py:83-88):** *"The POLARITY of `census.gated` is R-ii — FIXED in this change (`metrics_census.py:335` now returns `not self._gate()`). R-i (`capture_active` wiring, `api.py:2571-2576`) is a separate out-of-lane defect."*

**R1.4 — Doc-pin direction (PLAN DEFECT).** Plan §A2 (:139) says the five doc-pin tests "assert their presence". **The opposite is true:** the tests assert the tokens are **ABSENT from shipped agent-facing strings** — `assert token not in caveat` at `tests/unit/metrics/test_metrics_read_tools.py:251-255`, and the same `for token in (...)` shape at `test_metrics_read_registration.py:525`, `test_anomaly_report_tool_registration.py:1013`, `test_metrics_read_docs_surface.py:126`, `test_metrics_read_personas.py:359` **[VERIFIED BY ARCHITECT at :251-255; grep-verified at the other four]**. Consequence: **M-A2d (delete the R-tokens from the disclosure) is NOT discriminating** — no test reads the disclosure. The tokens must *stay in the disclosure* (operator-facing) and *stay out of shipped strings* (pinned).

## Blast radius

| Consumer | Site | Effect of the fix |
|---|---|---|
| `census_enabled` | `provider.py:120` | corrects (no code change) |
| `census_gated` | `provider.py:145`, `availability.py:91` | corrects |
| wire exposure | `envelope.py:68` (worker-cited) | now honest |
| availability callers | `provider.py:251` (worker-cited) | unchanged |
| routers | `daemon/routers/metrics.py:214-215` (comments only) | none |
| api wiring | `api.py:2508` (sampler), `:2571` (provider) | none |
| **tests** | `test_metrics_query_provider.py:399-421` | **ONE PIN FLIPS** (see below) |

No compensating second-negation exists beyond `provider.py:120/145`, which become correct rather than compensating.

## Proof obligation

- **Fake-based pins do not flip:** `_FakeCensus.gated=True` means "gated OFF" (worker-verified against `test_metrics_query_switch_invariance.py:51,:189-196,:237-242`). Decisive order test: `test_the_permitted_differences_are_exactly_those_three` (`test_metrics_query_switch_invariance.py:154-170`).
- **ONE REAL-SAMPLER PIN FLIPS (PLAN DEFECT):** `test_metrics_query_provider.py:399-421` `test_a_real_running_census_reports_the_gate_state` seeds `CensusSampler(gate_enabled=lambda: True)` + `_started=True` and asserts `dark/census_gated` **[VERIFIED BY ARCHITECT at :399-421]**. Post-fix, `gated=False` → `census_enabled=True` → `census_gated=False` → the family resolves on `live_series_count` (→ `live`). **Plan §A2's "No existing pin flips" is false for this pin.** It encodes the inversion through the real sampler and must be updated in the same change (either assert `live` for the enabled+running case, or add a `gate_enabled=False` dark-case sibling).
- **Mutation legs:**
  - **M-A2a** (revert `:335` to `return self._gate()`) → RED on the real-sampler pin + the new polarity pin. **Discriminating — keep.**
  - **M-A2b** (consumer-side `bool(gated)` mutation) → RED only if a consumer-side pin exists; otherwise it is vacuous. The plan's consumer-side pin must assert the *resolution layer* value (that `gated` is the gated-off sense), not the provider expression.
  - **M-A2c** (swap labels back) → RED **only with a NEW label-agreement pin**; no existing test reads `availability.py`'s comment. Add one (source-text assertion that `availability.py` uses `R-ii` for the polarity and the disclosure uses `R-ii` for the polarity).
  - **M-A2d** (delete tokens from the disclosure) → **non-discriminating as written**; replace with (i) the label-agreement pin above, and (ii) keep the existing five absence-pins untouched.
- **Observable that must NOT change:** census DISABLED (`gated=True` post-fix) still yields `dark/census_gated`; non-running census still yields `dark/census_not_running`; empty series still yields `dark/series_absent`.

## Masking — the critical question

**No masking.** **[VERIFIED BY ARCHITECT]**
- The metrics registry has **18 live families, none `skill-*`**. "Skill families report dark while 15 live series are published" is a **separate defect — skill families are not registered at all** — not a polarity artifact. The polarity fix touches only the `postgres_census` idiom (F6).
- After the fix, a genuinely gated census still reports dark because `gated` is a **direct read-through of the live gate**: `gated=True` (off) → `census_enabled=False` → `census_gated=True` → `dark/census_gated`. Honest.
- **F6 has THREE independent dark inputs** (`availability.py:89-97`): `census_not_running`, `census_gated`, `series_absent`. `capture_active` does **NOT** affect F6 — the `elif` at `:93-95` is `SOURCE_IDIOM_WEDGE_CAPTURE` only. Therefore the acceptance criterion must read: **F6 reports `live` iff the census is ENABLED *and* RUNNING *and* publishing series.** Polarity alone does not guarantee `live`.

## Rejected alternatives

- **Fix at `availability.py`** — label-only; leaves inverted facts on the wire; violates the two-check-sites invariant; "greener, not honest".
- **Fix at `provider.py`** — rewrites every fake/pin; leaves the lying property.
- **Rename the property** — larger blast radius; no functional gain.

---

# RULING 2 — B: confinement completion

## The question (premise conflict)

Commission: the three pieces are "landed-but-unwired" and must be wired. Prior art: D6 defers direction quarantine; D7 names the master switch deferred; D8 item 9 files `filesystem_scope` NOT BUILT; D8 item 3 leaves inline `skill_evolve` UNGATED. Reconcile per piece; define the enforcement point; design the refusal proof; resolve the ordering hazard.

## Per-piece TRUE status (evidence)

| Piece | TRUE status | Evidence | Phase-1 action |
|---|---|---|---|
| `filesystem_scope` | **NOT BUILT** | `grep filesystem_scope daemon/` → 0 hits (worker); read reach = `read_file` (`filesystem.py:670`) + `list_directory` (`:596`) honouring the absolute-path skip in `_resolve_within_workdir` (`:197-225`); `glob/grep` already fenced (`_resolve_search_root` `:252-283`) | **BUILD** opt-in, default-OFF slice, keeper-only (B3). This **narrows** D8 res.9 (fleet-wide default stays deferred) — it does **not** overturn it: absent ⇒ every other agent byte-identical. |
| Master switch | **DEFERRED** | `grep` → 0 hits in `daemon/` (worker); D7 / HANDOVER P3 | **STILL DEFERRED** (B4). Its one load-bearing duty (inline mutation) is delivered at the lane gate (B2) — the correct chokepoint. No overturn. |
| Direction quarantine | **DEFERRED** | D6 (`skill-keeper-confinement/decisions.md:95-98`); free text enters at `skill_fix` (`skill_tools.py:816-820`) → `enqueue_evolution` formats `job.message` (`skill_job_dispatcher.py:659`) → passed to `enqueue_message` (`job_processor.py:1600`) | **STILL DEFERRED** (B5); only the observability half ships. No approval authority exists; regex scanning is false safety (D6). No overturn. |
| Inline `skill_evolve` | **UNGATED HOLE** | `daemon/tools/skill_evolution_tools.py:230-252` — no lane check (worker-quoted code); keeper's allow-list includes the `skill-evolution` category **[VERIFIED BY ARCHITECT — meta.json:11]** | **WIRE** the lane gate (B2) — the only reachable mutation path. |

## Enforcement point (verified call path)

1. `agents/skill-keeper/meta.json:11-12` — `allow` = `[read_file, list_directory, glob_files, grep_files, self, help, knowledge, dynamic-skill, skill-evolution, shared_meta_kv, pok, nest-report]`; `deny` = `[bash, proc, service, write_file, edit_file, inner_soul]` **[VERIFIED BY ARCHITECT]**.
2. `create_instance_tools` → `resolve_tool_filter` (`daemon/tools/instance.py:430`), **deny-wins** at `allowed_tools -= denied_tools` (`:584`) **[VERIFIED BY ARCHITECT]**.
3. Tool stripping: only names in `allowed_tools` are bound (`instance.py:7451-7481`) **[VERIFIED BY ARCHITECT]** — denied/unlisted tools never reach a handler; a model-issued `edit_file`/`bash` call is an unknown-tool refusal at the runtime.
4. **Boundary if a write tool were ever granted:** in-bounds writes are only the 9 DB-versioned prompt artifacts guarded by `check_write` (`daemon/tools/prompt_artifacts.py`, worker-cited `:119-135, :265-274`); **out-of-bounds and unguarded**: `daemon/**`, `tests/**`, `.agents/shared/planning/**`, `agents/*/meta.json`, `agents/*/skill-set.yaml`, all absolute paths. **The allow-list strip is the sole barrier** — which is why the proof must be capability-based, not name-based.

## Behavioral proof (refusal, not detection)

- **B1a resolver leg:** run the production `resolve_tool_filter` against the production `_tool_registry.py`; assert the resolved set contains **no** write/shell/proc/service/memory capability — by **registry capability metadata**, not by tool name.
- **B1b invocation leg:** drive the production tool factory; attempt the `b7da3ed0` sequence (`edit_file`×24, `bash` git add/commit, `write_file`, `inner_soul`); assert each is refused at the tool layer and a **filesystem digest before/after is identical**; `git status --porcelain` empty.
- **False-pass risks:** (i) warning-logged-but-write-proceeded = detection, not refusal → assert SHA-256/mtime + git cleanliness; (ii) name-based checks miss renamed write tools → capability metadata; (iii) stubbed resolver → must use production resolution.
- **Mutation semantics (clarify plan M-B1b):** adding `edit_file` to `tools.allow` while retaining `tools.deny` must keep the **deny-wins structural pin GREEN** (it asserts deny-wins). It goes RED only when the ordering breaks (allow-after-deny — M-B1c). The tester must not implement M-B1b as "must be red".
- **The red→green leg (the exit criterion):** M-B1a (add `edit_file` to allow, deny unchanged) → the invocation leg **writes a file** → RED; restore → refusal → GREEN.

## Ordering hazard — resolved

**Enforced at promote (staged payload):**
1. Allow-list strip — **already live** in v0.29.7 (D3 / plan P8).
2. `SkillEvolutionConfig.evolution_lane_enabled = False` (D1 default OFF) — keeper Tier-3 jobs cannot be admitted.
3. Queued-execution terminal refusal (D1/M-2/F1).
4. **B2's inline lane check** in `skill_evolution_tools.py` (fail-closed `getattr` guard).

**Enforced only at the Phase-2 switch flip:** Tier-3 job admission/execution; capture lane.

**Ruling:** the commission's exit criterion — *"confinement proven by a red→green behavioral leg" in Phase 1* — **is satisfiable at promote via B1 + B2 alone.** B1 proves the keeper cannot mutate project code or shell; B2 proves inline evolution refuses while the lane is OFF (the live state since 2026-10-22); D1 proves queued jobs are terminally refused. **The master switch and direction quarantine are not prerequisites**, and keeping D6/D7/D8 deferred is the correct posture — no deferral is overturned.

## Rejected alternatives

- **Build all three** — re-opens three recorded deferrals; the fleet-wide read-scope default and the approval authority are design commissions, not Phase-1 work.
- **Honor all deferrals and do nothing** — leaves the inline `skill_evolve` hole (the only reachable mutation path) open with no shipped control owning it.

---

# RULING 3 — E4: the deactivation mechanism

## The question (with census facts as given)

Retire the stale/shadow DB skill rows, reversibly, before F can inject. Mechanisms: (a) `.sql` migration, (b) boot-time reconciliation, (c) operator-run update with receipt.

## Stale set by rule (census-applied)

| Rule | Rows | State |
|---|---|---|
| **R1** same-name active shadowing in an unresolved A/B group | 2 (gen-1 + gen-2), `is_active=true`, `status='ab_testing'`, shared `ab_test_group`, `skill_ab_tests.resolved_at IS NULL` | **LIVE target** |
| **R2** captured-origin never used | 6, all already `(False,'inactive')` | **NO-OP** (identify + record only) |
| **R3** legacy gate-runner copies | query-first; `76e3e1a7a` does not exist; `9e79ccad` not enumerated | **LIVE target (unidentified)** |

**Injectability predicate:** `SkillRepository.list(active_only=True)` filters **only** `Skill.is_active == True` (`daemon/repositories/skill/repository.py:296-299`) **[VERIFIED BY ARCHITECT]**; `status` is not consulted; the search path calls `list(..., active_only=True)` (`skill_search_service.py:459, :1243`). ⇒ an `(is_active=True, status='ab_testing')` row **is injectable** — the E4.3 hole is real.

## Mechanism failure modes

- **(a) `.sql` — REJECT.** The runner is a documented **no-op on non-SQLite** (`daemon/migrations/runner.py:816-839`: *"intentionally a NO-OP for non-SQLite engines … Skipping migrations"*) **[VERIFIED BY ARCHITECT]**. Fresh-SQLite boot dies at `20260714_000001_widen_job_queue_type_constraint.sql:35` (`ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS …`) — the file's own DUAL-DRIVER note (`:24-27`) claiming SQLite ≥3.35 support is **false** **[VERIFIED BY ARCHITECT]**. Both roads lead nowhere.
- **(b) boot reconciliation — ADOPT (shipped half).** Self-heals at next boot; rule-based; idempotent. Constraints: must run before injection can serve; must not sit inside `_ensure_postgres_columns` (one transaction, no per-statement isolation ⇒ abort-boot).
- **(c) operator script — ADOPT (pre-flight + reversal).** House precedent (`scripts/remediate_pause_report_orphans.py`: dry-run default, `--apply`).

## 5-axis matrix (weights per house standard; Risk & Cost inverted)

| Option | Complexity 20% | Scalability 20% | Maintainability 25% | Risk 20% | Cost 15% | Total |
|---|---|---|---|---|---|---|
| A: `.sql` migration | 2 | 1 | 2 | 1 | 5 | **2.05** |
| B: boot recon alone | 3 | 4 | 4 | 3 | 3 | **3.45** |
| C: operator script alone | 3 | 2 | 3 | 4 | 4 | **3.15** |
| **B+C (+E4.3)** | **4** | **5** | **5** | **5** | **3** | **4.50** |

## Ruling

**R3.1 — Mechanism.** **B+C combined** (endorses the plan's DS-24 ruling), with E4.3's injection filter as part of the safety property. Dominant axes: Maintainability (single sanctioned writer + shared receipt) and Risk (defence in depth; safety independent of boot timing).

**R3.2 — Dual-column writer (MANDATORY).** All deactivation goes through `SkillRepository.deactivate()` (`repository.py:383-396` → `update(is_active=False, status="inactive")`) **[VERIFIED BY ARCHITECT]**, or a `deactivate_by_rule()` that delegates to it. **Two ad-hoc UPDATEs are forbidden** (census: one real `(False,'ab_testing')` desync exists).

**R3.3 — Receipt = FILE, not a DB table.** `<data_dir>/skill_stale_deactivation_receipt.json` (atomic temp+rename; rotated copies `<receipt>.<run_id>.json`, keep last 5). A DB table would trigger the house rule "every new SQLModel table needs its own `.sql`" on a **doubly-broken** migration vehicle. Entry schema: `{skill_id, name, rule_id, before:{is_active,status}, after:{is_active:false,status:'inactive'}, touched_at_iso, run_id, reactivated_at?}`.

**R3.4 — Run cadence.** E4.1 runs **every boot**, no-op in steady state; E4.2 on demand (dry-run default; `--apply`; `--reactivate <receipt>`).

**R3.5 — Reactivation-vs-next-boot (THE hazard).** A rule-based every-boot sweep would re-deactivate an operator's reversal. **Fix: process-once suppression keyed by `(skill_id, rule_id)` persisted in the receipt.** Boot algorithm: load receipt → build suppression set → skip suppressed rows → deactivate the rest via `deactivate()` → append entries → atomic write. `--reactivate` restores the exact `before` pair **and records `reactivated_at`, keeping the suppression** (audit trail; a genuinely re-stale row is a new incident requiring a new receipt entry). Idempotence proof: second run = zero deactivations.

**R3.6 — A/B-strand guard (MANDATORY).** **REFUSE** to touch any row whose A/B test has `resolved_at IS NULL` (`SkillABTest.resolved_at`, `daemon/repositories/skill/models.py:689` **[VERIFIED BY ARCHITECT]**; repository filters `resolved_at IS NULL` at `:2160-2174`). Justification: deactivating a variant of an unresolved A/B test silently picks a winner. Record the refusal as `skipped_open_ab_test` in the receipt (deliberate, visible, reversible).

**R3.7 — Timing.** Sequential synchronous call in manager startup **after** `_ensure_postgres_columns()` + table creation and **before** the injection service can serve. **Never inside the ensure list** (abort-boot hazard).

**R3.8 — Sizing correction.** R2 is a state-wise **no-op** (6 captured already inactive); the substantive set is **R1 (2 rows) + R3 (query-first)** — much smaller than "~10 rows". The commission's "116 skill_evolution rows" are **job rows**; there is no `skill_evolution` table.

## Tester proof (discriminating; a pin green with the guard removed is worthless)

| Leg | Assertion | Mutation that must go RED |
|---|---|---|
| Deactivation | seed R1-shaped fixture → recon → **both** columns `(False,'inactive')` via `deactivate()` | M-E4a no-op; M-E4b delete; **M-E4h (NEW) helper writes only `is_active`** |
| Reversibility | `--reactivate` → exact `before` pair; **second recon run does NOT re-deactivate** | M-E4c recon re-activates; **M-E4g (extended) remove suppression → second run re-deactivates** |
| A/B guard | unresolved-test row untouched; `skipped_open_ab_test` recorded | remove guard → row deactivated with open test |
| E4.3 filter | R1-shaped row absent from `injected` **and** `low_match` even with recon not run | M-E4d remove filter |
| Dry-run default | no `--apply` → zero writes, no receipt | M-E4e |
| Receipt completeness | every touched row has an entry; drop one → RED | M-E4f |

## Rejected alternatives

- **A alone** — silent no-op on the only DB that matters; fresh-SQLite boot never reaches it.
- **B alone** — timing hole between promote and reconciliation; registry keeps lying.
- **C alone** — not automatic; the live DB stays stale until an operator runs it.
- **DB-table receipt** — requires a migration that can never land; violates the house `.sql` rule by construction.

---

# PLAN AMENDMENTS (minimal, specific)

## A2 (plan §5, `phase1-plan.md:112-152`)

1. **`:132` — correct "No existing pin flips".** ONE pin flips: `tests/unit/metrics/test_metrics_query_provider.py:399-421` (`test_a_real_running_census_reports_the_gate_state`, `gate_enabled=lambda: True`, `_started=True`) asserts `dark/census_gated`; post-fix it must assert `live` (enabled+running+series) or gain a `gate_enabled=False` sibling. Add this to the A2 change list and acceptance.
2. **`:139` — correct the doc-pin direction.** The five tests assert the tokens' **ABSENCE** from shipped agent-facing strings (`test_metrics_read_tools.py:251-255` et al.), not their presence. Keep the tokens in the disclosure; keep them out of shipped strings. **M-A2d is non-discriminating** — replace it with a docs/label-agreement pin.
3. **`:134` — extend the label fix to test comments.** `test_metrics_query_provider.py:367, :370, :389, :403, :420` use R-i for the polarity (swapped); correct them to R-ii in the same change (developer prose, not pins).
4. **`:142` acceptance 1 — condition on enabled AND running AND publishing.** `availability.py:89-97` has three independent dark inputs (`census_not_running`, `census_gated`, `series_absent`); `capture_active` does not affect F6.
5. **`:124` — keep the layer wording, add the naming note.** The fact owner is `CensusSampler.gated`; `availability.py` is the label layer. Record this so a reviewer does not "correct" it backwards to `availability.py` (the commission brief's phrasing is inverted relative to the code).

## B (plan §6, `phase1-plan.md:219-300`; §3 P3/P8)

6. **`§6 B1` — record the enforcement call path** (`create_instance_tools` → `resolve_tool_filter` `instance.py:430`, deny-wins `:584` → stripping `:7451-7481`; refusal = unknown-tool at the runtime, never a handler) and the **boundary table** (in-bounds = 9 `check_write`-guarded prompt artifacts; out-of-bounds = `daemon/**`, `tests/**`, `.agents/shared/planning/**`, `agents/*/meta.json`, `agents/*/skill-set.yaml`, absolute paths).
7. **`§6 B1` — fix M-B1b's semantics.** Add `edit_file` to allow while retaining deny ⇒ the deny-wins **structural pin stays GREEN**; RED only when the ordering breaks (M-B1c). Do not implement M-B1b as "must be red".
8. **`§6 B2` — add the fail-closed `getattr` guard** around `manager.is_skill_evolution_lane_enabled()` (absent/raising accessor ⇒ refuse), matching the D1 F3 fail-closed posture.
9. **`§6 B4/B5` — state the exit-criterion resolution explicitly:** met at promote by B1 (live allow-list strip) + B2 (lane gate OFF default) + D1 queued-execution terminal refusal; the master switch and direction quarantine are **not prerequisites** and D6/D7/D8 are **not overturned**.

## E4 (plan §9, `phase1-plan.md:440-490`)

10. **`§9 E4` — add the 5-axis matrix** (B+C = 4.50 vs 3.45 / 3.15 / 2.05) as the justification for the adopted mechanism.
11. **`§9 E4` — mandate the dual-column writer** (`repository.deactivate()`, `repository.py:383-396`); forbid ad-hoc UPDATEs; add mutation **M-E4h** (helper writes only `is_active` → RED).
12. **`§9 E4` — specify the receipt** (file at `<data_dir>/skill_stale_deactivation_receipt.json`, atomic, rotated, schema above) and **replace "writes an audit receipt" with the concrete location/format**.
13. **`§9 E4` — add the process-once suppression design** (R3.5) and extend **M-E4g** to prove the second boot does not re-deactivate after `--reactivate`.
14. **`§9 E4` — add the A/B-strand refusal guard** (`resolved_at IS NULL` ⇒ skip + `skipped_open_ab_test`) and its mutation leg.
15. **`§9 E4` — correct the sizing and the "116 rows" premise:** R2 is a state-wise no-op; substantive set = R1 (2) + R3 (query-first); the 116 rows are job rows, not an evolution table.
16. **`§9 E4` timing — pin the call site:** after `_ensure_postgres_columns()` + table creation, before the injection service can serve; never inside the ensure list.

---

# Open questions (not blocking)

1. **R3 identities** — the legacy gate-runner copies must be identified by a live-DB signature query before any deactivation (`9e79ccad` is not in the Phase-0 enumeration; `76e3e1a7a` has zero repo hits). No row is deactivated until identified (plan's hard rule stands).
2. **Receipt visibility trade-off** — `data_dir` keeps the receipt out of the repo (no secret/planning churn) but is less discoverable to operators; if the Emperor wants it in the planning dir, that is a one-line path change, but it would make each boot write into a tracked tree — recommend `data_dir`.
3. **Second check-site anchor** — `metrics_listener.py:419` is worker-cited; the architect re-verified `metrics_census.py:355` only. The invariant claim (exactly 2) is consistent but the listener anchor should be re-opened at implementation start.
4. **"15 live series"** — taken from the disclosure, not re-derived; irrelevant to the polarity fix (the registry has no `skill-*` family at all).

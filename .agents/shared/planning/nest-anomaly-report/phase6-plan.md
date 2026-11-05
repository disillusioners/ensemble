# Phase 6: Lane Test/Mutation Strategy, Gate Ladder, and Release Posture

Date: 2026-11-04T22:40Z
Author: plan-creation worker (nest-anomaly-report lane, board item wi-2d691ed2)
Status: Draft — plan of record for phase 6
Worktree: `/home/nea/ensemble-src-wt-nest-anomaly-report` (base `1f6898ae2`)
Claim: `wt.claim.nest-anomaly-report`
Risk register: `phase6/risk-register.md` (same directory tree; path per `plan-overview.md:83`)

## Objective

The lane's correctness claim is **proven in both directions** — the original symptom (an agent
has no channel to report an anomaly) is demonstrated **FAILING at the base tree** and
**PASSING at HEAD** by the same script, every shipped behaviour has a **mutation-proven** pin,
the gate ladder is executed in the house order, and the release is **staged but never promoted**.

**Testable completion sentence:** the both-directions script reds at base `1f6898ae2` and greens
at HEAD; the net-new-red adjudication reports **EMPTY**; every registered pack is green on both
legs; and `scripts/upgrade/stage.sh` has produced a staged release directory with a manifest and
**both** provenance sidecars — with `promote.sh` never invoked.

## Scope

### In Scope

- The lane-level **both-directions** gate script (the original-symptom closure).
- The **pin-pack inventory** across all six phases, each with ≥1 named mutation leg.
- The **net-new-red adjudication** protocol (FAILED-list diff vs the TRUE pre-fix base).
- The **gate ladder** (implementation → code-review council → tidier → functional gate → staged
  verification → STAGE) and the exact ceremony command shape for the user's morning report.
- The **release posture**: migration delta, payload shape, manifest field implications, FE
  determinism, `known_schema_gen`, version-slot derivation, and the disclosure list.

### Out of Scope (with reason)

- **Promote / nonce / live symlink / `state.json` promote fields** — the user's 3-factor nonce
  ceremony owns the flip (`plan-overview.md:58-60`; the lane's claim ruling).
- **A second metrics query path** — the metrics door stays frozen; anomaly reads use the new
  domain route (`plan-overview.md:61-63`, ruling R8).
- **Fixing the routed F23 registry defects (R_i / R_ii)** — live defects in the registry idiom
  family, owned by the routed metrics lanes. The lane must **disclose** that other families
  render dark while our F23 family publishes, and must **verify** that F23 actually publishes
  rather than merely registers (`plan-overview.md:102-121`).
- **Fixing the secret-survey scrub gaps** (nomask `daemon/system.py:357-422`,
  env-inherit `daemon/tools/bash.py:287-302`, `daemon/tools/proc_tools.py:496`) — disclosed
  anchors, cited-not-fixed (`plan-overview.md:65-66`).

## (a) The Original Symptom, Proven in BOTH Directions

**Statement.** At the base tree `1f6898ae2` an agent witnessing an incident has **no channel**:
the tool does not exist, no storage exists, no aggregation exists, no read surface exists, no
duty line tells the agent it *could* report, and no grant gives it the tool. The failure is
**silent** — the only anomaly path is a human reading a transcript. At HEAD the same script
must find every one of those surfaces present **and** the end-to-end behaviour working.

**The checkable assertion set** — one command/query per surface. The script is written once and
run against both trees (BASE via a separate worktree at `1f6898ae2`, HEAD via the lane tree).

| # | Surface | BASE assertion (must FAIL) | HEAD assertion (must PASS) | Probe |
|---|---|---|---|---|
| A1 | The write tool exists | `grep -rc "report_anomaly" daemon/` = **0 hits** | ≥1 hit; the tool is registered under the `nest-report` category | `grep -rn "report_anomaly" daemon/tools/` |
| A2 | The tool is reachable by an agent | the category key is absent from `daemon/tools/_tool_registry.py` | `nest-report` ∈ `CATEGORY_MODULES` ∪ `@register_tool_category` sites | `python3 -c "from daemon.tools import _tool_registry as r; print('nest-report' in r.CATEGORY_MODULES)"` |
| A3 | The storage exists | `SELECT to_regclass('anomaly_reports')` → **NULL** | both tables present | read-only SELECT on a scratch PG DB |
| A4 | The migrations exist | census = **119** files, none anomaly-shaped | census = **121** files, 121 distinct prefixes, 2 anomaly-named | `ls daemon/migrations/versions/ \| wc -l`; `ls … \| cut -d_ -f1-2 \| sort \| uniq -d` (empty) |
| A5 | The clustering exists | no `anomaly_clusters` table, no cluster service | cluster service present; 3 sibling reports → **1** cluster row, `report_count = 3` | DB query after 3 sibling reports |
| A6 | The board sink exists | no board item is ever created by an anomaly report | a critical cluster creates **exactly 1** board item; a second critical on the same cluster appends **1 note**, no second item | `SELECT count(*) FROM board_items WHERE …` |
| A7 | The metrics family exists | F23 absent from the registry | F23 present in the registry **and publishing** (a series is actually returned, not just registered) | registry inspection + a real query-door read; the family pack is **`tests/unit/metrics/test_metrics_family_w4.py`** (the phase-3 file that CREATES the pack — the w4.py-matching name wins, council recommendation adopted) |
| A8 | The read surface exists | no `nest-report-read` category, no `/api/nest-reports/*` route | the privileged trio resolves; the route answers (200 under auth, 503 `AUTH_NOT_CONFIGURED` without) | route probe |
| A9 | The FE surface exists | the metrics page has no anomalies tab | the tab is present in the FE tab manifest and renders the degradation state honestly under auth-off | FE spec / manifest inspection |
| A10 | **The duty prompt exists** | the duty marker is absent from **every** file under `agents/` | the marker is present in **41 = 39 walk + 2 underscore specials** agents' canonical homes (the pin walks DIRS, not collapsed ids — the id-collapsed walk reaches only 34) | `grep -rl "## Reporting Anomalies" agents/ \| wc -l` → **0** at BASE, **41** at HEAD |
| A11 | **The grant exists** | `"nest-report"` is absent from **every** `tools.allow` array | present in **40/40** non-empty arrays; `watcher` inherits; the three phase-3-touched files carry **both** `nest-report-read` (phase 3) and `nest-report` (phase 5) | `grep -rl '"nest-report"' agents/*/meta.json \| wc -l` → **0** at BASE, **40** at HEAD |
| A12 | The vocabulary artifact is current | 57 categories, `vocabulary_version` 3 | **end state 59 categories @ vocabulary_version 5** — per-bump **3→4 (C2)**, **4→5 (C5)**; lane-total **3→5** (the architect's "3→5" is the **lane total**; the per-bump numbers are the plan's, and are correct — `architecture-recommendation.md` §7/§8). The agents prompt baseline is **161/161 re-stamped EXACTLY ONCE**, in **C8** — **no re-stamp from C5 or C9** (the baseline covers versioned `.md` only; `meta.json` is not in the set) | `.agents/shared/metrics/tool-categories.json` + the artifact drift test + the prompt-artifact baseline `--check` |
| A13 | **End-to-end behaviour** | impossible — no tool to call | a spawned worker reports an anomaly → the row persists with harvested **and scrubbed** evidence → 3 sibling reports collapse to one cluster row → a critical cluster creates exactly one board item → F23 publishes a closed-label series → the read tools return decision-ready clusters → the FE tab renders the degradation state honestly under auth-off. **With AM-5's new outcome token, this leg MUST also cover `unclustered` rows**: an orphan row (`outcome='accepted' AND cluster_id IS NULL`) is **TERMINALIZED** as `unclustered` by the bounded age-gated sweep — **never reassigned** — and `unclustered` rows are **excluded from `accepted`-filtered counts**. The leg asserts that exclusion explicitly | the full E2E script |

**Both-directions rule.** The same script, the same assertions, two trees. A script that is
merely *skipped* at BASE is **vacuous** — the gate must **FAIL** at BASE. The A10/A11 legs are
the ones that make the phase-5 ceremony part of the lane's original-symptom closure rather
than a cosmetic prompt change.

## (b) Pin-Pack Inventory and Mutation Legs

**Convention.** Every pack is registered in `.agents/tester/PACKS.md` with its mechanical node
count, and every pack carries **≥1 mutation leg**: plant the defect → observe the pack RED →
restore → observe GREEN, with the failing-test digest recorded. A pack with no mutation leg is
not evidence (the house "false-confidence pin" rule).

### Phase 1 — storage / scrub / category registration

| Pack (proposed path) | Pins | Mutation legs |
|---|---|---|
| `tests/unit/test_anomaly_report_models.py` | table/column shapes; index naming `idx_<table>_<cols>`; the write-once `excerpt_sha256` + `excerpt_length` pair; the `category` closed-vocabulary column + `category_raw` preservation | **M-1a** — drop the `category_raw` column → red. **M-1b** — widen the excerpt cap by 1 byte → red |
| `tests/unit/test_anomaly_report_scrub.py` | the planted secret is ABSENT from every persisted TEXT column; the excerpt sha256 matches the stored excerpt; `redact_exc_str` never raises (`daemon/_redact.py:74-98`); the limit is applied **after** redaction | **M-1c — swap the scrub order** (redact → cap → scrub becomes cap → redact) → red (the planted secret survives in the capped path). **M-1d** — replace `redact_exc_str` with `str(exc)` → red (an exception whose `__str__` raises would escape the never-raises contract) |
| `tests/unit/test_anomaly_report_tool_registration.py` | the category key; the `KNOWN_TOOL_NAMES` / `DYNAMIC_TOOL_NAMES` entries; the registration-site **order** (the list-append must precede `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter`, `daemon/tools/upgrade_tools.py:150-184`); the artifact drift check (`tests/unit/tools/test_tool_category_artifact.py:14-23`) | **M-1e — drop the category fold** (remove the key from the registry) → the artifact drift test reds. **M-1f — skip the artifact regen** (leave the committed artifact at 57 categories) → the drift test reds (the F-1 trap from the metrics-agent-tools lane) |
| `tests/unit/test_anomaly_report_transcript_harvest.py` | the bounded read discipline: harvest reads are **bounded** (never an unbounded transcript walk); the harvest cost is capped per report | **M-1g — remove the read cap** → red (the O(N²) walk family of the v0.27.15 incident) |

### Phase 2 — clustering / rate limits / board / retention

| Pack | Pins | Mutation legs |
|---|---|---|
| `tests/unit/test_anomaly_cluster_assignment.py` | write-time assignment; `cluster_id` assigned **once** and never re-derived; the sliding window comparison (`W = 15 min`); 3 reports → 1 cluster | **M-2a — break the cluster-window comparison** (`<=` → `<`, or drop the window term) → red (a report outside the window collapses into the cluster). **M-2b** — re-cluster on write (recompute the cluster id) → red (id instability) |
| `tests/unit/test_anomaly_rate_limits.py` | the 5-per-10-min minor+major cap; critical exempt to 20/hour; the refusal is typed (`rate-limited`); the ledger row carries `outcome=rate_limited` with the harvest columns **NULL**; cluster counts unchanged on refusal. **With AM-5's new outcome token, this pack MUST cover `unclustered` rows**: the outcome vocabulary is `accepted | rate_limited | persist-failed | unclustered`, and `unclustered` rows are **excluded from `accepted`-filtered counts** (assert it) | **M-2c — remove the pre-harvest gate** (harvest before the rate check) → red (the ledger row would carry evidence, violating the refusal contract). **M-2d — drop the critical exemption** → red (the 6th minor is refused, the cap no longer matches) |
| `tests/unit/test_anomaly_board_sink.py` | exactly **1** board item per cluster; a repeat critical appends **1 note** and creates no second item; `board_item_id` is set on the cluster; `notify_user` is **never** invoked (doctrine `daemon/tools/notify_user.py:32-39`) | **M-2e — make the board write unconditional** (create an item on every critical report) → red (2 items for 2 criticals on one cluster). **M-2f — call `notify_user`** → red (the doctrine violation) |
| `tests/unit/test_anomaly_retention.py` | the 30-day batched retention registered on the existing `MaintenanceService.register` seam (`daemon/services/maintenance.py:141`); batching (not one DELETE per row); the seam registration is present. **With AM-5's new outcome token, this pack MUST cover `unclustered` rows**: an orphan (`outcome='accepted' AND cluster_id IS NULL`) is **TERMINALIZED** as `unclustered` by the bounded age-gated sweep — **never reassigned** — and `unclustered` rows are **excluded from `accepted`-filtered counts** (assert it) | **M-2g — unregister the maintenance job** → red. **M-2h — make the delete unbatched** → red. **M-2i — REASSIGN an orphan instead of terminalizing it** → red (the `orphan_terminalized_not_reassigned` leg) |

### Phase 3 — F23 family / read tools / route

| Pack | Pins | Mutation legs |
|---|---|---|
| `tests/unit/metrics/test_metrics_family_w4.py` | F23 registered with `source_idiom="registry"`, `durability="durable"`; the **closed** `allowed_values` (AM-2 hard-raise at `daemon/services/metrics_query/wiring/registry.py:102-110`); the family **actually publishes** a series (not merely registers) | **M-3a — widen `allowed_values` to free text** → red (the AM-2 raise). **M-3b — register without wiring the sampler** → red (the publish assertion — this is the F1/F3/F6 "dark family" trap) |
| `tests/unit/test_nest_report_read_tools.py` | the TWO read tools (`anomaly_clusters`, `anomaly_report_detail`) resolve through the anomaly service (never raw engine handles); the output-budget clamp; the freshest-first sort; the degraded-read block | **M-3c — read the engine handle directly** → red (the read-path purity pin). **M-3d — remove the output clamp** → red |
| `tests/unit/test_nest_report_rest_route.py` | the route is gated identically to the metrics door (`daemon/routers/metrics.py:106-137`); 503 `AUTH_NOT_CONFIGURED` fail-closed with no auth; 200 with auth; the route accepts EXACTLY the door's any-of query scope {maintenance:read | metrics:read} — nothing broader (planner ruling on worker OQ1, 2026-11-04) | **M-3e — make the route open** (drop the gate) → red (the fail-closed pin). **M-3f — broaden the scope beyond the any-of set** (accept an unrelated scope or a single broader/unrelated token) → red |

### Phase 4 — FE tab

| Pack | Pins | Mutation legs |
|---|---|---|
| `frontend/src/app/pages/metrics/metrics.nest-reports.spec.ts` | the tab appears in the tab manifest; the empty state is honest (no fake data); the door-state union renders 401 / 403 / 503 / network distinctly; polling supersede tokens; tab-hidden polling stops | **M-4a — remove the tab from the manifest** → red. **M-4b — render a placeholder series on 503** → red (the "renders the degradation state honestly" pin) |
| `frontend/src/app/pages/metrics/metrics.bundle-guard.spec.ts` | the **existing** bundle guard is extended (not bypassed): the new tab's vendor bytes in ANY static fetch set == 0; the chart.js-vendor invariant stays GREEN | **M-4c — static-import the chart vendor in the new tab** → the existing guard reds (the v0.28.11 N5 invariant) |

### Phase 5 — duty prompt / grant / ceremony (this plan)

| Pack | Pins | Mutation legs |
|---|---|---|
| `tests/unit/test_anomaly_report_duty_prompts.py` (new) | the duty-prompt completeness walk over DIRS — **41 = 39 walk + 2 underscore specials** (the id-collapsed walk reaches only 34); the 5 v2 `rule.md` pairs byte-identical; the forbidden-token closure; the seal re-pin correctness (17 new values + 18 untouched) | **M-P5-1** — remove the duty block from one agent → red. **M-P5-4** — add a new agent dir with no duty line → red. **M-P5-6** — perturb one v2 mirror → red |
| `tests/unit/test_anomaly_report_grant_pins.py` (new) | the grant completeness walk (40/40 explicit + the `watcher` inheritance exception); the array-order pin; **the three re-pinned allow-array constants** (ari trailing set `tests/unit/test_board_registration.py:285`, `GAIA_TOOLS_ALLOW` `tests/unit/test_gaia_agent.py:30`, sketcher `EXPECTED_ALLOW` `tests/unit/agents/test_sketcher_agent.py:72`) | **M-P5-2** — remove one grant → red. **M-P5-3** — move the entry off the last position → red. **M-P5-5** — perturb one edited `rule.md` byte → the sealed tripwire and the baseline `--check` red. **M-P5-7** — revert the ari trailing set to its 6-entry value → the ari pin reds (proves the re-pin is load-bearing, not cosmetic). **M-P5-8** — remove one `meta.json` grant from a phase-3-touched file (`agents/devops/meta.json`) → the grant walk reds (proves the two-phase edit order is verified) |

**Mutation-leg count floor: ≥ 18 legs across the lane.** **THE TABLES ARE THE SOURCE OF TRUTH**
— the per-pack mutation columns above enumerate **33 legs** (phase 1: 7 · phase 2: 9 · phase 3: 6
· phase 4: 3 · phase 5: 8). The arithmetic in this sentence is a **floor statement only**; the
authoritative count is whatever the tables enumerate, and the floor is met either way (33 ≥ 18).
Each leg is executed, its failing-test digest recorded, and the restore verified GREEN.

## (c) Net-New-Red Adjudication

**Protocol.**

1. **Freeze the FAILED list at the TRUE pre-fix base.** The base is the tree at `1f6898ae2`
   with the lane's commits reverted — *not* a clean checkout, because the base must carry the
   same dependency state. The base leg runs the **same** pytest invocation as the HEAD leg
   (same file set, same flags, same timeout) and its `FAILED`/`ERROR` node ids are captured.
2. **Diff.** `net_new = FAILED_head − FAILED_base` and `fixed = FAILED_base − FAILED_head`.
   The lane is clean when `net_new` is **EMPTY** (or every member is adjudicated with a
   written ruling and a fix commit).
3. **Flaky adjudication.** Every node in the symmetric difference is re-run **3× in isolation**
   on **both** legs. A node that is RED on HEAD in isolation and GREEN in isolation is
   **flaky**, not a regression — it is adjudicated with the 3× evidence attached, never
   silently accepted.
4. **Pre-existing red families to expect** (base-proven, NEVER this lane's):
   - the **9 stale timezone reds** (settings/timezone debt lane — 9 base-proven TZ reds plus 2
     settings `d2_derived` reds, blob-identical base↔HEAD);
   - the **4 context7 base reds** (the context7 family — red at base, unrelated to this lane);
   - the **agent-base-v2 planner blob debt** (a whole-tree collection red from the foreign
     `agent-base-v2` lane; `agents/planner/meta.json` is byte-identical at every rev — it is a
     test-expectation debt, not tree drift);
   - the **`g7` module-level `sys.exit(0)` collection error** and the **checkpoint_pool_config
     field-default reds** (release-lane origin, base-proven).
   These are recorded in the gate report as **base-proven**, with their base-leg digests, and
   are excluded from `net_new` by construction — never by hand-editing the FAILED list.

**Rule.** A red is only "this lane's" if it is red at HEAD and green at base. Anything else is
adjudicated with evidence, not with a claim.

## (d) The Gate Ladder (house order)

### AM-17 — the COMMIT choreography (binding; an ordered atomic series, NEVER one giant commit)

The architect's adjudication 6 (`architecture-recommendation.md` §7) rules the lane into **nine
ordered commits**, each leaving the tree **GREEN** at its boundary:

| # | Commit | Atomic because |
|---|--------|----------------|
| C1 | P1 code: models + migrations + `NestReportConfig` + vocab constants + service + repository | code exists, nothing registers — all green |
| C2 | P1 registration: tool + `_tool_registry` + `instance.py` extend + loader + **artifact regen + VOCAB 3→4** | the SAME-PR class: the AST scan finds the decorator site the moment the tool file lands; the artifact embeds version+set — all in ONE commit or the drift test reds |
| C3 | P2 service: clustering + rate limits + board sink + retention + manager registration | one behavioral unit |
| C4 | P3 F23: family + roster 12→13 + adjudication census + packs | registering without the census verdict reds the two-sided census pin; roster without pack reds the count pin |
| C5 | P3 `nest-report-read`: category + **ALL 18 privileged-set sites** + 3 meta.json + artifact + VOCAB 4→5 | the SAME-PR rule (`_tool_registry.py:324-366`) — a missed site is red BETWEEN commits |
| C6 | P3 route: `/api/nest-reports/*` + the two router cages | an undeclared mount reds both cages loudly |
| C7 | P4 FE: tab + specs + bundle-guard extension | the guard and the tab co-land |
| C8 | P5a duty: 41 prompt `.md` + baseline re-stamp + 17 seal re-pins | the baseline row and the file content it pins must never disagree across a boundary (house rule, ari-voice precedent) |
| C9 | P5b grants: 40 meta.json + the 3 allow-array pins + sketcher doc amendment | the grant-completeness walk is all-or-nothing |

**The 81-file single ceremony commit is REJECTED** (bisect-hostile, and it is unnecessary: the
duty pin and the grant pin are independent — two green boundaries beat one giant commit). Two
facts make the series safe (both code-verified): the tool-categories artifact carries **ZERO
per-agent grant info** (C9 needs no regen — §4), and the prompt-artifact baseline covers **ONLY
the versioned `.md` artifacts** (`prompt_artifacts.py:119-140` — `meta.json` is not in the set),
so C5's three meta.json edits need **NO** re-stamp.

**PER-COMMIT SMOKE SET (binding)** — after **every** commit, all four of these must be green:
`test_frozen_tool_name_discovery` + `test_tool_category_artifact` + the privileged-set pins + a
boot-scan. This is the ladder **between** commits, not only at the end.

### AM-18 — the phase-order confirmation (no re-order, no fusion)

Verified by the architect against the coupling map and the code facts (`architecture-recommendation.md` §8):

- **P3 after P1+P2** — confirmed (P3's `allowed_values` source is P1's `vocab.py`; F23's aggregate reads P2's cluster columns; the read tools need the service's read half). The tools' degraded shape (`anomaly-unavailable`) means P3 could *register* before the tables exist, but its packs could not pass — **keep the order**.
- **P4 after P3** — confirmed (the tab consumes P3's route + family; FE compiles against string paths but the spec and data need P3).
- **P5 after P1** (the tool exists) and **P5's appends after P3's 3 `meta.json` edits** — already ruled by the trailing-entry pin; consistent.
- **The VOCAB double-bump** (P1: 3→4 in C2; P3: 4→5 in C5) is ordered by a **hard dependency** (P3 imports P1's vocabulary) — **two commits, never one 3→5 jump**.

### The gate ladder (stages)

| Stage | Owner | What runs | Gate condition |
|---|---|---|---|
| G0 — implementation | phase owners | each phase's own Tier-1 pack, run per phase; **plus the per-commit smoke set after every one of C1..C9** | the phase's packs green in the phase worktree; the smoke set green at **every** commit boundary |
| G1 — code-review council | reviewer council | the merged tree's diff | 0 criticals; advisories folded |
| G2 — tidier | tidier | hygiene pass over the merged tree | PASS; findings folded or routed with the receipt |
| G3 — functional gate | tester | the both-directions script (§a) + every registered pack, base leg and HEAD leg | both-directions script **RED at base / GREEN at HEAD**; `net_new` EMPTY; every mutation leg observed RED→GREEN; **the AM-16 security-AM item green (§(e) disclosures 12)** |
| G4 — staged verification | tester / giter | the staged artifact: 4-way sha (release dir == worktree dist == manifest == sidecar), the migration census, the prompt-artifact baseline, the FE build determinism, an **isolated** boot smoke | all lanes green; the live fence HELD (`state.json` promote fields byte-identical pre/mid/post; the live symlink unchanged) |
| G5 — STAGE | giter | `scripts/upgrade/stage.sh` with a **v-prefixed** `VERSION` derived LIVE | a staged release dir + manifest + **both** provenance sidecars; the tag pushed per the push convention (`f13a4f59e` — push `HEAD` first, then the tag) |

**G3/G4 environment discipline (house, non-negotiable).** Every pytest leg runs with the
per-variable env scrub, NOT `env -u POSTGRES_*` (that idiom is a **silent NO-OP** — bash does
not glob env names):

```
cd $WT && timeout 300 env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT \
  -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT \
  .venv/bin/python -m pytest <files> -q --tb=line -rfE -p no:randomly --timeout=120
```

**G4 boot-smoke discipline.** Any frozen-binary boot smoke MUST pin
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **explicitly** (`env -u` on it strips the ambient
guard and the sweep kills **live** processes — the 2026-10-31 incident), pin
`ENSEMBLE_SELF_ENV=sandbox`, copy the binary into a scratch dir, and scrub **per variable**.
A staged boot smoke is **not** isolated by default (the binary's path derivation resolves the
live install root), so the compensating evidence — scratch dir, scratch DB, byte-fences,
`state.json` PRE==MID==POST — is mandatory.

**G5 — the ceremony command shape (FOR THE USER'S MORNING REPORT ONLY; NEVER RUN BY THIS LANE):**

```bash
# derive the version slot LIVE at ceremony time — never fix it in the plan
ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.<n> bash scripts/upgrade/promote.sh live --f2-verified-closed
```

The lane stages with `scripts/upgrade/stage.sh` (v-prefixed `VERSION`, and the tag at HEAD
must match `git describe --tags --exact-match` or stage.sh exits 78 —
`scripts/upgrade/stage.sh:137-148`). **This lane never runs `promote.sh`, never arms a nonce,
never touches the live symlink or the `state.json` promote fields.**

## (e) Release Posture

### Migrations

| Property | Value | Evidence |
|---|---|---|
| Files today | **119** | `ls daemon/migrations/versions/ \| wc -l` |
| Files after this lane | **121** | +2 (phase 1 owns both) |
| Distinct prefixes | 121 | `uniq -d` empty |
| Shape | additive `CREATE TABLE IF NOT EXISTS`, **zero FK** | house additive posture |
| Down sections | present but **never executed** | `rollback_migration()` has zero production callers; the runner is SQLite-only; on PG the runner early-returns |
| `known_schema_gen` | the lane's **2nd** `.sql` file (lexically later than the 1st) | the manifest field names the generation head |

**`rollback_safe = true`, source = explicit.** Semantic adjudication (the house standing rule
for a non-empty delta): (a) the UP section is `CREATE TABLE IF NOT EXISTS` with zero FK and
zero `ALTER`/`DROP`; (b) the old tree references **neither** new table — `git grep anomaly_reports`
and `git grep anomaly_clusters` at the previous release tag return **zero** files, so the
tables are **inert on rollback**; (c) rollback performs no down-migration, and the old binary's
ORM metadata has no model for either table, so `create_all` on a new boot simply ignores them;
(d) the old binary is compatible with the post-release schema in both directions. Consequence:
an **auto-rollback is safe** — the rollback posture is FIX-LOSS (the lane's features disappear),
not incompatibility.

### Payload — this is a FULL-payload release, NOT daemon-only

| Tree | Changes in this lane | Manifest implication |
|---|---|---|
| `daemon/` | phase 1 (tool, models, repository, service, category registration) + phase 2 (clustering, rate limits, board sink, retention) + phase 3 (F23 family, read tools, route) + phase 4 (FE only) | binary sha changes |
| `agents/` | **phase 5**: 41 duty files + 40 grant files + the baseline manifest (`.prompt-artifacts.sha256`) + the seal file (a test file, not shipped) | **`agents_tree_sha256` CHANGES** |
| `frontend/` | phase 4 (the anomalies tab) | **`frontend_tree_sha256` CHANGES** |
| `plugins/` | nothing | `plugins_tree_sha256` unchanged |

**The manifest field implications must be stated explicitly at stage time** (the house
staged-verification compares the manifest against the previous release field-by-field):
`version`, `binary_version`, `binary_sha256`, `staged_at`, `agents_tree_sha256`,
`frontend_tree_sha256` change; `plugins_tree_sha256`, `config_sha256`, `launcher_sha256`,
`known_schema_gen` (except the +2 migration head) behave as declared. The `agents/` tree
change is what makes this a **full-payload** release — a daemon-only release is **not**
achievable for this lane, and any tooling that assumes daemon-only must not be applied here.

**FE determinism.** The frontend tree is rebuilt deterministically; `fe_index_sha256` is
re-hashed from the rebuilt `index.html` and must be recorded. The `frontend_tree_sha256`
covers the whole frontend tree, and a hashed-chunk rebuild may churn chunk filenames without
changing source content — the manifest delta must be reported as **file-count + changed-file
names**, not as a source diff, so a chunk rename is not mistaken for a content change.

**Version slot.** Derived **LIVE at stage time** from the release census (the highest tag, the
`releases/` directory contents, and the next free bump) — **never fixed in this plan**, because
other lanes may take slots concurrently. The version slot is NOT reserved by this lane.

**Config-namespace disclosure — see the authoritative block.** The naming-collision disclosure
lives in **§(e) Disclosures, item 8** (the `nest-` namespace ruling: `nest_report_*` /
`ENSEMBLE_NEST_REPORT_*`; the earlier `anomaly_report_*` form is **superseded**). It is
**not** restated here — one statement, one home.

**Retention.** `releases/` over-retention is machinery-owned (the known GNU retention-eviction
defect makes eviction a no-op on this host). This lane evicts nothing and protects the live
symlink target plus the pending-ceremony candidates.

### Disclosures (carried into the morning report)

1. **R_i / R_ii — the F23 registry idiom defects are LIVE, not fixed by this lane.** The
   census-polarity inversion and the unwired `capture_active` affect other families. The FE
   will show **other** families as dark while ours publishes. The ceremony text must say this
   plainly, and the gate must assert that **F23 publishes** (not merely registers).
2. **The secret-survey scrub gaps are cited, not fixed** — nomask (`daemon/system.py:357-422`),
   env-inherit (`daemon/tools/bash.py:287-302`, `daemon/tools/proc_tools.py:496`). Our harvest
   path uses the existing scrub discipline (redact-first, excerpt caps, sha256+length, never
   raises) and does not widen any gap, but it does not close them either.
3. **The DB-masking caveat — RESOLVED by the fresh census** (architect §9): `prompt_versions`
   has **ZERO** `approved`+`is_selected` rows anywhere and **ZERO** `artifact='rule'` rows of
   any state — **no planned `rule.md` edit can be shadowed**. One latent caveat remains:
   `(reviewer, workflow)` carries a `pending_approval` v2 since 2026-10-19; it shadows ONLY if
   the lane edits `reviewer/workflow.md` **and** that row is later approved (the lane's duty
   home for `reviewer` is `reviewer/rule.md`, which has zero rows ⇒ no shadow today). The
   phase-5 census **re-run at landing** + the morning-report disclosure cover it; the
   **disposition is user-owned** (morning item **U-2b**).
4. **The retention-seam fallback** — if the 30-day retention cannot be hosted on the existing
   `MaintenanceService` seam, the fallback is a **read-only** age filter on read paths with
   **no** deletion (a scheduled delete without a registered seam is not shipped silently);
   this is disclosed, not silently degraded.
5. **The `tools_note.md` scope-out** — 35 agent reference blocks are **not** updated; the tool
   help surface serves discovery.
6. **The first-non-privileged-write-category consequence** — `nest-report` is the **first
   non-privileged WRITE-tool category** in the empty-allow default universe. Tools-less custom
   agents inherit it. This IS the user requirement ("all agents"); the mitigations are the
   per-agent rate limits, the clustering, and the kill-switch.
7. **The `nest-report-read` privileged category is NOT granted to any agent by phase 5** — it is a
   read surface for operators. Phase 3 registers it as **entry #18** of
   `PRIVILEGED_TOOL_CATEGORIES` and updates **all 18** privileged-set assertion sites in ONE
   commit; **phase 5 touches ZERO of them**. If a later phase needs a per-agent `nest-report-read`
   grant, it is a separate ceremony.
8. **The naming collision with the existing resource-monitor anomaly engine** — the codebase
   already has `anomaly_enabled` / `anomaly_interval_s` / `anomaly_mad_threshold` /
   `anomaly_min_baseline_points` config keys (`daemon/config.py:905-954`) and a resource
   `ResourceAnomaly` shape (`daemon/routers/resources.py:870, 1024-1031`). The new tables
   (`anomaly_reports`, `anomaly_clusters`) do **not** collide with any existing table (no
   `anomaly*` table exists today), but the **config-key namespace and the FE label namespace
   do**. **Per R9's `nest-` namespace, phase 1 must namespace the new kill-switch distinctly (`nest_report_*` / env surface `ENSEMBLE_NEST_REPORT_*` — e.g. `ENSEMBLE_NEST_REPORT_ENABLED`, `ENSEMBLE_NEST_REPORT_DISABLED_AGENTS`); the earlier `anomaly_report_*` form is SUPERSEDED and must not be used.** The FE tab must not reuse the resource-anomaly label. **Disclosed for the sibling phases.** *This block is the authoritative naming-collision disclosure; the release-posture section points here.*
10. **The three allow-array pins are re-pinned in the same commit as the grant** (the ari
    trailing set, `GAIA_TOOLS_ALLOW`, sketcher's `EXPECTED_ALLOW`) — each grows by exactly one
    entry. The sketcher's documented "no write surface" fence is **amended honestly** in the
    same commit and disclosed as a morning item.
11. **Phase 3 and phase 5 both edit 3 `meta.json` files** (`maintenancer`, `devops`,
    `wanderer`). The fixed order is **phase 3 first, phase 5 appends after**; the grant pin
    asserts the entry is the **last** element, so a mis-ordered array is caught mechanically.
12. **The privileged-count literal is hardcoded as a STRING in a mutation-matrix pack.**
    `test/packs/metrics_read_mutation_matrix_test.py:92-93` (MT-01-count-pin) plants the
    literal `"assert len(PRIVILEGED_TOOL_CATEGORIES) == 17"` as a find/replace pair against
    `tests/unit/test_board_registration.py`. When phase 3 moves the count to 18, that `find`
    string **stops matching** — the mutation becomes a **silent NO-OP** and the pack's
    "red marker" assertion can never be satisfied: a false-confidence pack. The house LESSONS
    entry `.agents/tester/LESSONS/2026-10-20-v0261-tip-verification-gotchas.md:11` already
    warns that grepping only `== frozenset` under-counts the pin set. **Cross-lane finding for
    phase 3:** the privileged-count sweep must cover the 6 live `len(...) == 17` assertions,
    the 11 exact-set comparison sites, the production definition
    (`daemon/tools/_tool_registry.py:367`), the **comment block** at
    `daemon/tools/_tool_registry.py:327-366` (it documents the count-assertion sites and
    goes stale), **and** the mutation-matrix pack's string literal.
    Measured site census: **6** count assertions + **11** comparison sites + 1 production
    definition + 1 pack string + 1 comment block.
12. **AM-16 — the default-ON posture is CONDITIONAL, and the condition is a named gate item.**
    `ENSEMBLE_NEST_REPORT_ENABLED` stays default **True** — the commission's "all agents"
    requirement plus the phase-5 duty prompt make default-OFF incoherent (the duty line would
    instruct every agent to call a tool that refuses) — but **only if the four security AMs have
    landed in this lane AND are verified by the phase-6 tester gate**:

    | AM | What it binds | The pin that must be visible at gate time |
    |---|---|---|
    | **AM-9** | flood fences — refusal-ledger cap, row caps, per-hour accepted cap, retention capacity trim | **"ledger INSERT never takes TOTAL rows above `max_rows_global`"** — the ceiling fires **ON THE REFUSAL PATH** (accepted **and** refused rows both count against the total); the **per-tree** cap is **inapplicable to NULL-`tree_root` refusal rows** and therefore is **not** part of this leg. The sibling is folding the fence itself into phase 1/2 — the leg must be visible at gate time against the landed implementation |
    | **AM-11** | scrub hardening — `redact_secret_shapes` + fail-closed excerpt on scrubber-absent + excerpt only for major/critical | the planted-**non-stored**-secret pin + the per-pattern mutation legs |
    | **AM-12** | projection + injection pins — list excludes `excerpt`; detail behind `?include_excerpt=true`; FE no `innerHTML`/`bypassSecurityTrust`; deterministic `top_description` | the list-has-no-`excerpt`-key pin; the FE no-innerHTML pin; the determinism pin |
    | **AM-13** | board ceilings — per-tree-root and global board-item ceilings + note-append + suppression counter | the board-ceiling pin + the suppression-counter pin |

    **D-5 (source-of-truth for the board ceiling).** The ceremony text and the morning-report text must derive the board ceiling from **`decisions.md:116`** (authoritative: `max_board_items_per_hour_global` = **20/h** after the W-6 correction) and **NEVER** from `architecture-recommendation.md:330` (the historical adjudication record, whose pre-W-6 value still reads "default 50").

    **GATE ITEM (named, LEADER-DECIDED): "the four security AMs are present and pinned."** Each
    of the four must be **present in the code and pinned by a visible test** at gate time. **If
    any of the four is missing or unverified at gate time, the lane ships default-OFF
    (`ENSEMBLE_NEST_REPORT_ENABLED=False`) and the morning report carries a MANDATORY
    disclosure line** — the duty prompt then instructs agents to call a tool that refuses, which
    is why the default-OFF fallback is a **disclosure obligation**, not a silent downgrade.

    **AM-16 also lands the per-writer deny list** `ENSEMBLE_NEST_REPORT_DISABLED_AGENTS`
    (comma-separated agent-type ids; checked **pre-rate-gate** → `tool-disabled` for that writer
    only; **restart-required**, documented) — the surgical lever the whole-surface kill-switch
    lacks.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Write the both-directions script covering A1-A13 | phases 1-5 landed | the script **fails** at base `1f6898ae2` and **passes** at HEAD; both runs recorded with output |
| 2 | Execute every mutation leg in §(b) | the phase packs exist | each leg observed RED→GREEN with the failing-test digest recorded; ≥18 legs |
| 3 | Run the base leg and the HEAD leg of the full registered pack set | 2 | `net_new` EMPTY or every member adjudicated in writing |
| 4 | Re-run each symmetric-difference node 3× in isolation on both legs | 3 | every node adjudicated flaky-or-regression with evidence |
| 5 | Run G1 (code-review council) and G2 (tidier) on the merged tree | 3 | 0 criticals; tidier findings folded or routed |
| 6 | Run G3 (functional gate) | 5 | both-directions proof + all packs green + mutation ledger complete |
| 7 | Run G4 (staged verification) after staging | 6 | 4-way sha match; migration census 121/121; baseline clean; FE deterministic; isolated boot smoke green; the live fence HELD |
| 8 | Derive the version slot LIVE and stage | 6 | a staged release dir + manifest + both sidecars; the tag pushed per `f13a4f59e` |
| 9 | Assemble the morning report | 8 | the disclosures list above, the mutation ledger, the census result, the ceremony command shape, and the outstanding user items — **the user is NOT woken** |

## Coupling

- **Tight with every phase** — phase 6 gates the merged tree; its packs and mutation legs
  cover all six phases' surfaces. A phase that lands without a registered pack leaves a hole
  that phase 6 cannot fill after the fact.
- **Tight with phase 5** — the `agents/` tree change makes this a full-payload release; the
  manifest field expectations and the baseline/seal fences are phase-6 verification items.
- **Tight with phase 3** — the F23 **publish** assertion depends on phase 3's sampler wiring
  (the F1/F3/F6 dark-family trap).
- **Loose with phase 2** — the board-sink and rate-limit assertions are phase-2 behaviours
  observed through phase 6's E2E script.
- **Independent of the promotion machinery** — phase 6 touches `stage.sh` only; `promote.sh`
  is never invoked by this lane.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | The both-directions script is vacuous at BASE (skipped rather than failed) | High (the lane's central claim is unproven) | Medium | Assert the BASE run's exit code is **non-zero** and that each A-leg independently reports its BASE failure; a vacuous skip is a red |
| 2 | `net_new` is non-empty and is waved away as "pre-existing" | High (a real regression ships) | Medium | The diff is computed against the TRUE base with the SAME command; no hand-editing of the FAILED list |
| 3 | A mutation leg is claimed but not executed | High (a false-confidence pin ships) | Medium | Every leg needs a recorded failing-test digest + a restore-verified GREEN |
| 4 | The FE bundle budget is exceeded by the new tab | Medium | Medium | The existing bundle guard is extended, not bypassed; the vendor-bytes-in-any-fetch-set invariant stays enforced |
| 5 | The `agents/` tree change is staged but the payload is built daemon-only | High (the duty prompt never ships) | Medium | The staged verification compares `agents_tree_sha256` against the previous release and the manifest's agents map |
| 6 | The version slot is taken by a concurrent lane mid-stage | Medium | Medium | Derive the slot LIVE at stage time; a re-stage after a slot collision is normal, not a failure |
| 7 | The live daemon is contacted during a boot smoke | High (a live incident) | Low | The scratch-dir + per-var-scrub + explicit `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` + `ENSEMBLE_SELF_ENV=sandbox` discipline; the live fence asserted PRE==MID==POST |
| 8 | The F23 family registers but never publishes (dark family) | Medium (the metrics surface is hollow) | Medium | The gate asserts F23 **publishes** a series, not merely that it registers |

## Exit Criterion

Phase 6 is done when:

1. The both-directions script is **RED at base `1f6898ae2`** and **GREEN at HEAD**, with both
   outputs recorded.
2. `net_new` is **EMPTY** (or every member is adjudicated with a written ruling + a fix
   commit), and every symmetric-difference node has a 3×-isolated verdict on both legs.
3. Every registered pack is green on both legs, and **≥18 mutation legs** are recorded
   RED→GREEN.
4. G1, G2, G3 and G4 are complete with 0 criticals and the live fence HELD.
5. A staged release directory exists with a manifest and **both** provenance sidecars; the tag
   is pushed per `f13a4f59e`; `promote.sh` was **never** invoked; the live symlink and the
   `state.json` promote fields are byte-identical to their pre-lane values.
6. The morning report carries the disclosures list, the mutation ledger, the DB-masking census
   result, and the ceremony command shape — with the user **not** woken.

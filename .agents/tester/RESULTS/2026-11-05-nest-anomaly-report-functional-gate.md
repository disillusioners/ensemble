# nest-anomaly-report — INDEPENDENT FUNCTIONAL GATE (pre-staging)

**Commission:** wi-2d691ed2 (nest-anomaly-report) · **Lane:** `feature/nest-anomaly-report` @ `1bcff208b` (21 lane commits + merge of `origin/latest 97b76e20e` + 11 tidier commits) · **Lane base:** `1f6898ae2` (v0.28.11) · **Merge base for attribution:** `97b76e20e`
**Gate date:** 2026-11-05 · **Tester:** independent functional gate (10 worker legs) · **Live release during gate:** v0.28.12 (untouched; `:9797` uptime continuous throughout; zero live-DB contact — ambient `POSTGRES_DB=ensemble_prod @10.44.0.2` neutralized per-variable on every command)

---

## VERDICT: **FAIL — NOT READY TO STAGE AS-IS** (all blockers are small and precisely located; no architectural risk found)

The product **works end-to-end** (G1/G2/G9 green; real daemon boots; the tool lands durable, harvested, clustered, published, readable rows). The failures are **one small production defect**, **one conditionality failure**, and **net-new ≠ 0 caused entirely by stale test expectations**, plus disclosures. Nothing found invalidates the lane's design.

### Blocker table

| # | Severity | Gate | Finding | Root cause | Fix shape |
|---|---|---|---|---|---|
| B1 | 🔴 critical | G5 | Concurrent per-tree SHED drifts `cluster.report_count` by −1 — **40/40 rounds** on real PG 16.15 (`report_count=3`, 4 surviving accepted members). The lane's own pinned invariant `report_count == member rows` is broken under the exact race it pins. | `daemon/repositories/anomaly/repository.py:138-150` — `shed_members` decrements from the **pre-delete** `groups` count (`:149`) while the `DELETE` (`:144`) can affect **0 rows** when two transactions select the same oldest victim; rollup at `:315-324`; trigger selection `anomaly/services/anomaly_service.py:819-829`. Control proof: 20 concurrent same-key accepts with **no shed** stay exact (20/20) ⇒ plain `+1` rollups compose; only the mixed shed drifts. | Decrement by the **actual** `DELETE … RETURNING` row count, or serialize shed+assignment atomically (~10–20 lines). Affects: FE/ops `report_count` sums + board note count (understated). NOT affected: `ensemble_anomaly_clusters_open` (reads `COUNT(*)`). |
| B2 | 🟠 important | G3/G6/G10 | **Net-new deterministic reds = 10** (vs lane base; all green at merge base `97b76e20e`). **Zero are production regressions** — all 10 are stale test expectations against legitimate, leader-ruled surface changes. | 5 × aegis-family pins (`test_aegis_allow_set_exact`, `test_children_allow_set_exact[cracker|hacker|sweeper]`, `test_worker_untouched` — grant added `"nest-report"`, pin sets never updated; `grep -c nest-report tests/unit/agents/test_aegis_security_family.py` = 0) + critic expected-set (`test_designer_rewire.py:313`) + kb-writer expected-set (`test_kb_writer_tools.py:204`) + the 37-entry router-order pin (`test_post_restart_arm_notify_no_parallel.py` — 38th entry `nest_reports_router` at api.py, intentional) + 2 × webfetch fixture ERROR (`test_webfetch_builtin.py ~:492` — `MagicMock(spec=Config)` lacks the new `nest_report` field wired at `manager.py:1749`). | **Test-side only**: 5+5 small expectation/fixture updates (exact lines in G10 table). Until folded, the pins stay red *by expectation, not behavior*. |
| B3 | 🟠 important | G7 | **AM-16 default-ON NOT supported** ⇒ per the plan's own conditional, the lane must ship **default-OFF + morning disclosure**, unless remediated first: (a) **AM-12** pins exist and are mutation-meaningful (MUT-C/D RED→GREEN) but run in **no registered pack** (`test_nest_report_read_tools.py`, `test_nest_report_read_registration.py`, FE tab specs — zero PACKS.md rows); (b) **AM-13 shipped values (3/tree/h, 20/h global) are NOT pinned** — planting the stale `50` left **89 nodes GREEN** (MUT-A). Mechanism IS pinned (MUT-B, 2 RED). | Registration/pin gap, not a code gap: `config.py:3379/:3389` correctly ship 3/20 (docstring even says "W-6 correction: 50 → 20"). | Register 3 packs; add 2 default-value asserts (precedent `test_metrics_family_w4.py:340`); fix the stale **50** in `decisions.md:635` + `architecture-recommendation.md:330`. ~5 lines + 3 PACKS rows, then re-run G7's two legs. |
| B4 | 🟡 disclose | G5-B | TOCTOU flood overshoot is **outside the disclosure**: single-wave 20 concurrent accepts vs `max_rows_global=5` → **+15 accepted**; per-type cap 2 → **+18**. `decisions.md:299-300` discloses only the per-type best-effort with **no numeric bound**. No silent loss: every refusal ledgered (20 rows) or counted (counter +20). | Read-then-insert by design. | Add a numeric overshoot bound to the disclosure (e.g. "overshoot ≤ concurrent in-flight writers") or tighten with the same atomicity fix as B1. |

---

## Gate-by-gate results

### G1 — BOTH-DIRECTIONS SYMPTOM PROOF — **PASS** (G1a clean; G1b PASS-with-residual)
- **G1a base `1f6898ae2` (verified daemon 0.28.11):** `report_anomaly` absent from both name universes; no `nest-report`/`nest-report-read` category (privileged set = 17); no `anomaly_*` tables in metadata (migrations 119); no F23 (16 families, roster 12); zero `/api/nest-reports` routes; zero FE files. **HEAD `1bcff208b` (0.28.12):** both categories wired (`nest-report` non-privileged, `nest-report-read` privileged #18), both tables + 6 named indexes, migrations **121/121 distinct** incl. `20261105_020500/020501`, F23 with its **5 metrics**, roster 13, 2 REST paths, 6 FE files; empty-allow agents inherit the write tool through the default universe.
- **G1b (sandboxed, scratch PG, mock-LLM stub):** daemon booted (`livez` 200 v0.28.12, 43 agents, 0 parse failures); **`report_anomaly` present in the live 76-tool LLM array with full schema** (`category`+`description` required; `severity` default `major`; `suspected_cause` nullable); privileged read tools correctly ABSENT from `worker`'s surface. Invoking the resolved `StructuredTool` through the real factory + `_apply_tool_filter`: **38/38 assertions** — durable row (`agent_id` = type per AM-10; canonical + raw category; fixed-width stamp), **harvest populated** (parent_chain, tree_root, boot_epoch, registry sha/captured_at, wedge ids), **excerpt captures the planted tool error**, cluster `report_count=1`, **F23 snapshot/history publish the series**, ops read tools return it decision-ready. Streaming-mode attempt showed the graph **does invoke `report_anomaly`** (loop breaker 660×).
- **Residual (disclosed):** a full LLM-decided turn did not land a row — the harness stub's non-streaming `tool_calls` JSON was not parsed by the streaming client (`tool_calls=0`); a transport mismatch in the harness, not a lane defect. All other links executed on real paths.

### G2 — FENCE REALITY — **PASS**
All plants on an isolated worktree (`/tmp/nest-gate-mut-1bcff208b`, removed clean), sha256-verified restores: **W1** (`if False and total_rows >= …` @ `anomaly_service.py:813`) → exactly **1 RED** on the predicted node; **⑦** (w4 cutoff reverted to `interval '1 hour'`) → **1 RED** (`test_the_open_clusters_cutoff_is_bound_not_hardcoded`) — the brief's "2 REDs" was over-broad (helper pin unreachable by a SQL-only revert); fence verified; **CondA** (`_is_benign_token_name → False`) → metering-**VALUE** pin REDs (200000 masked — asserts value, not name); **CondB** (`_key` removed / `_auth` removed) → RED each. Truth table (11/11 SSOT-conformant): all with-value `*_key`/`*_auth` shapes MASK (`encryption_key=AKIA…`, `SIGNING_KEY=`, `ENCRYPTION_KEY=`, `SSH_KEY=`, `MCP_AUTH=` → `***`); JSON shapes mask (`api_key`, `private_key`, and the **multi-word passphrase whole**: `{"password": "correct horse battery staple"}` → `***`); benign prose (`monkey`, `keyword`, `primary key: id`, `auth flow is healthy`, `private channel closed`) and E3 metering fields stay CLEAR. **`{"key": …}` is CLEAR by deliberate SSOT design** (name-based, suffix-anchored; bare `key` excluded to protect diagnostics) — record the exclusion in `decisions.md` so it is not re-opened. F-1 refusal-path ceiling and per-tree shed behavior verified green via the owning packs (the concurrency hole is B1).

### G3 — WHOLE-TREE CENSUS — **PARTIAL / net-new ≠ 0** (see B2)
Per-leg (identical slicing both sides; closure ≈99.9%; `daemon.__file__` proven per leg; LLM dead-port fence + per-variable env scrub everywhere):

| Leg | collected/deselected | P/F/E/S/xf | digest (BARE-ID) |
|---|---|---|---|
| BASE `1f6898ae2` | 33,324 / 1,145 | 32,495 / 450 / 42E / 337 / 6 | `53843e17…2dd62` (476 unique reds) |
| HEAD `1bcff208b` | 34,156 / 1,145 | 33,305 / 467 / 44E / 340 / 6 | `f4335dc7…56123` (495 unique) |
| MBASE `97b76e20e` | probe-only (head-red ids) | 8 red / 21 green | — |

`sweep_digest_verdict.sh` → **exit 1** both forms. Net-new 29 → **8 foreign-lane debt** (red at mbase; incl. the prompt-section param-id-shift family — one underlying fence defect, three index maps) + **11 deterministic nest-lane** (→ B2: 10 collateral + 1 flake, see below) + **10 composition artifacts** (all passed in isolation at HEAD — parallel-shard load flakes). RESOLVED = 10 (base-red → head-green; not isolation-verified — disclosed).
- **F reclassification (G10):** `test_auto_kill_integration::…sibling_alive_row_skipped_by_terminal_lane` is **flake/env, NOT lane-caused** — 3F/7P at the MERGE BASE under identical conditions; scrubbed runs pass at both revs; `git log 97b76e20e..HEAD` on the owning files is EMPTY. Remove from the net-new set; ledger as flake.
- **Corpus deviation (disclosed, fail-loud):** the brief mandated `-o addopts=` (35,301 corpus); the run used the **default-addopts form** (33,324/34,156 — 96.7%; deselect-set symmetry proven: exactly 1,145 both legs). The quoted `-m ""` execution cross-check was **not run** (form verified elsewhere: G6's privileged corpus, quoted `-m ""` == `-o addopts=`). **Compensated:** the only integration-marked lane file never executed by the census (`test_service_tool_flag_off_byte_identical.py`, 9 nodes) was executed green in G10; the other two lane integration files are unmarked and were in-corpus; **zero** postgres/slow-marked lane tests exist (grep-proven).
- Whole-tree single-invocation xdist INTERNALERROR-crashed on both legs; recovered by file-chunk strategy (recipe §5 bisection not needed at chunk granularity).
- **Base-proven census AS MEASURED: 476 unique BARE-ID reds at base** (~100 files; top: `unit/routers` 55, `test_injection_api` 22, `test_spawn_team_members` 21, integration 38, `test_memory_integration` 16 …) — **diverges from the dispatch's claimed ~86/13 families**; their per-id list was never provided; my measured set is the evidence.

### G4 — INHERITED RED — **base-proven (adjudicated, not fixed)**
`test_metrics_family_adjudication::test_the_daemon_diff_is_confined_to_the_allowed_files` — RED at BASE and at HEAD (both digests + isolated re-runs). Provenance mechanical: `git diff 644c2bfa 1f6898ae2 -- daemon/__init__.py` = exactly the version string (`0.28.5`→`0.28.11`). Hardcoded `LANE_BASE="644c2bfa"` vs tree v0.28.12 ⇒ red by construction on every release. **Routing: re-point or derive `LANE_BASE` dynamically — owner metrics-family lane; it will redden every future gate until then.**

### G5 — REAL-PG CONCURRENCY — **FAIL** (B1 + B4; details above). No negative counts; no dangling `cluster_id`; no silent loss; E4 empty-RETURNING path not triggered by the harness (disclosed).

### G6 — CEREMONY + CENSUS — **PASS-with-disclosure**
Duty pack 19/19; grant pack 15/15; 47 agent dirs; **44/44 explicit grants, exactly once, LAST position**; `watcher` empty-allow inheritance exception verified (only empty-allow meta); duty 45 = 43 walk + 2 underscore specials; aegis/cracker/hacker/sweeper each carry grant **and** duty; 5/5 v2 mirror pairs byte-identical; baseline `--check` **OK — 177**; seal tripwire green (the 4 drift-stale rows closed via `SEAL_REPINNED_BASE_VALUES`); `len(PRIVILEGED_TOOL_CATEGORIES) == 18` by runtime import. **Real fenced boot scan**: banner v0.28.12, **43 registered** (38 ids + 5 v2 variants; the loader skips the 2 underscore dirs — the brief's "47 agents" is the dir count, not the registration count), **0 parse failures**, fence proven from `/proc/<pid>/environ` + `pg_stat_activity` + live uptime; SIGTERM clean, scratch DB dropped. Privileged-pin census reconciled: **1,121 collected / 9 deselected (default) / 0 (override)**; the 9 = integration-marked `test_service_tool_flag_off_byte_identical.py` nodes; the brief's **293/302 figure is STALE** (mission-watch 293P/1F/12S; metrics declared 302 — not reproducible on this tree). The corpus's 11 reds split **5 lane-caused (B2) + 6 base-proven** (maintenancer KB-doc memory.md family — matching the "6 integration memory.md reds" claim).

### G7 — AM-16 CONDITIONALITY — **FAIL** (B3). AM-9 PASS (anchors + value-level pins, registered, green); AM-11 PASS (masker + E1 passphrase-whole + E3 denylist, mutation-proven). Disposition per the plan's own conditional: **default-OFF + morning disclosure unless B3's remediation lands and re-verifies.**

### G8 — FE — **PASS** (one standing caveat)
B4b `/metrics` lazy-only fetch set **184,866 / 185,000 B (headroom 134 B)** — passes, but any further FE addition to this route breaches the frozen bound (real add 19.7 kB vs the 8–14 kB estimate). B1 initial Δ −1,301 B (within ±2,048); 0 chart.js in initial; 1 dynamic-only import site; warning set 9==9; guard GREEN. Jest: lane + metrics page **32 suites / 509 tests PASS**; **full FE suite 158 suites / 4,786 tests, 9 reds — all attributable to base-proven families** (5 × hardcoded `UTC-04:00` DST; 4 × documented standing reds); transitive import closure of every red ∩ lane-changed set = **∅** ⇒ **FE net-new = ZERO (attributable)**. Degradation honest at 503/403/network (never blanks, per-source state); severity shows `critical (peak)` + rank; clustered-vs-raw distinct; empty state honest; 7/7 pixel geometry green re-run from HEAD's own build; **0 `innerHTML`/bypass sinks**.

### G9 — NO-REGRESSION ON TOUCHED SURFACES — **PASS**
11 registration packs (328 nodes) + tool_filter (80) green — the 17 pre-existing categories resolve, #18 purely additive. Metrics registry 16→17 families, roster 12→13, well-formed both sides; adjudication 26/27 (the 27th = G4, base-proven). **`daemon/routers/metrics.py` byte-identical base↔HEAD (sha `68544d…`)** — the frozen-door claim proven by hash; route pins 41 green. FE other tabs render (199 nodes). Deny-wins + privileged strip verified on concrete agent metas.

### G10 — NET-NEW ATTRIBUTION + CORPUS COMPLETION — **PASS**
Table in B2; **zero (ii) real-regression classifications**; the 9 never-executed integration nodes green; no pg/slow lane tests exist.

---

## Disclosures the ceremony text must carry
1. **B4b at the wall** — 134 B headroom on `/metrics`; the plan forbids raising the threshold.
2. **AM-16 disposition** — default-OFF unless B3 is folded and re-verified (else the AM-12/AM-13 gaps ship unpinned).
3. **TOCTOU overshoot unbounded** (B4) — a concurrent flood can exceed `max_rows_global`/per-type caps by the in-flight count; refusals are never silently lost.
4. **`report_count` drift (B1)** if the lane stages before the fix: cluster counts can under-report by the number of racing sheds.
5. `/api/metrics/*` returns **503 AUTH_NOT_CONFIGURED** until auth is enabled — the FE nest-reports tab degrades honestly (by design).
6. The brief's **293/302 privileged-pin figure is stale**; the attested census is 1,121/9/1,112.
7. `{"key": …}` JSON values are **not masked by deliberate SSOT design** (name-based, suffix-anchored masker).
8. G1b residual: LLM-decided turn not exercised end-to-end (harness transport); all other links executed.
9. Boot scan registers **43** agents (dir count 47; loader skips `_inner_soul`/`_prompt_system`, v2 mirrors fold).
10. G4 inherited red will redden every future gate until `LANE_BASE` is re-pointed (metrics-family lane).

## Could NOT verify (with reason)
- Full `-o addopts=` corpus census (96.7% measured; compensated for the lane's own files — G3 deviation).
- `-m ""` execution cross-check inside G3 (form equality verified on G6's corpus instead).
- RESOLVED-direction (10 base-only reds) isolation at HEAD — not run (time budget); treat as unconfirmed "fixed".
- E4 empty-RETURNING terminalization under real concurrency (not triggered by the harness).
- Multi-minute sustained flood (single-wave only).
- LLM-decided end-to-end turn (transport mismatch; schema visibility + graph invocation proven instead).

## Remediation checklist (implementer fold; all small)
- [ ] **B1** production fix: shed decrement from `DELETE … RETURNING` rowcount (`repository.py:138-150`); re-run the G5 harness (40 rounds, invariant exact).
- [ ] **B2** 10 test-side pin updates (aegis ×5 incl. `CHILD_ALLOW`/worker-untouched baseline; `test_designer_rewire.py:313`; `test_kb_writer_tools.py:204`; router-order pin insert `nest_reports_router`; webfetch `mock_config` += `nest_report` field).
- [ ] **B3** register 3 packs (read-tools, read-registration, FE tab); pin AM-13 defaults 3/20; fix stale 50 at `decisions.md:635` + `architecture-recommendation.md:330`.
- [ ] **B4** numeric TOCTOU-overshoot disclosure (one paragraph).
- [ ] Flake ledger: `test_sibling_alive_row_skipped_by_terminal_lane` (base-rev + env A/B proof in G10); doc the `{"key": …}` exclusion.
- Re-gate after fold: G5 harness, G7 legs, scoped net-new re-verify (the 10 ids), G6 privileged census unchanged.

## Worker roster (all evidence under `/tmp/nest-gate/*`; lane tree untouched, porcelain 0 throughout; every worktree removed)
G1a `4ea004b1` · G1b `48a838f7` · G2 `b08e49ac` · G3 `37695d14` · G5 `79c73a28` · G6 `b9bd99b1` · G7 `3c1f1a91` · G8 `9bf0a1a9` · G9 `25bd3a4e` · G10 `52f6fb62`

*This file is UNCOMMITTED by instruction (the gate brief forbids commits to the feature branch). Fold via the standard leader evidence sweep.*

---

# RE-GATE (scoped) — 2026-11-05, fixed HEAD `c2289f3b5`

**Trigger:** implementer closed B1–B4 in four commits on `1bcff208b` → `c2289f3b5`. Scope held to exactly the four legs the gate recommended; nothing else re-run (FE, registry, ceremony counts, boot scan unchanged and not re-executed).
**Method:** three context-warm workers + one fresh infra worker; every plant in an isolated scratch worktree with sha256 restore; lane tree byte-clean throughout (only this artifact untracked); per-variable env scrub + dead-port LLM fence on every command; scratch DBs dropped.

## VERDICT: **PASS-WITH-DISCLOSURES — READY TO STAGE**

| Blocker (first gate) | Re-gate result |
|---|---|
| **B1** `report_count` drift under concurrent shed | ✅ **CLOSED** — implementer pin 2/2 with a real lock rendezvous (`pg_stat_activity.wait_event_type='Lock'`, victim rows held uncommitted); my harness **0/40** Scenario A + **0/20** no-shed control; decrement now driven by `DELETE … RETURNING cluster_id` tally, R-b zero-count deletion preserved. **Mutation re-plant RED at round 0** (`victim cluster count 0 != 2`; harness `2` vs `3`), restore byte-identical `cb0328b4…93c1` ⇒ GREEN. Discriminating shape survived the fix. |
| **B2** net-new = 10 | ✅ **CLOSED** — all **10/10 ids green** under the same census invocation (10 collected / 10 passed); full five owning files **488 collected / 488 passed / 0 failed / 0 skipped** (395 + 25 + 23 + 8 + 37). Re-points assert **real** names/positions (critic 6 incl. `report_anomaly`; kb-writer 4; router 38 at its true api.py slot; webfetch real `NestReportConfig()`). |
| **B3** AM-16 default-ON unsupported | ✅ **CLOSED — disposition now default-ON** — 4 packs registered **and executed** with matching collected counts (`anomaly_read_tools` 26, `anomaly_read_registration` 21, `nest_reports_fe_tab` 37/3 suites, `anomaly_board_sink` 16); the previously-unregistered AM-12 pins demonstrably run inside them; **AM-13 values pinned** (`TestAM13ShippedCeilingValues` asserts a fresh config: 3 / 20) and mutation-proven — plant `20→50` REDs only `test_global_ceiling_ships_20`, bonus plant `3→4` REDs only `test_per_tree_ceiling_ships_3` (independent, non-vacuous). AM-9 16/16 and AM-11 25/25 still green at HEAD. |
| **B4** overshoot undisclosed | ✅ **CLOSED** — `decisions.md:301-305` states the measured envelope (+15 global / +18 per-type, single wave of 20) with the bounded-why and no-silent-loss clauses; mirrored verbatim in `CATEGORY_DOC` (`nest_report.py:113-117`). Exact match to the numbers this gate measured. |

## Fix-commit integrity — CLEAN
4 commits, 11 files, **527 insertions / 28 deletions — reconciliation exact**. **B1 is the only production-behavior change**, confined to `shed_members()` (+33/−11; no config/service/manager/api drift). B2 = 5 test files; B3 = 2 docs + 1 test. **B4's `daemon/tools/nest_report.py` (+6/−1) adjudicated (a): module-docstring text only** (lines 110–119 inside the module doc, surfaced solely via `CATEGORY_DOC = __doc__` → `get_category_doc()` → `tool_help`) — zero executable delta. `decisions.md` AM-13 row now reads **20 global** with the W-6 authority citation + `config.py ships 3/20` + pin name (line 635 at B3, 640 at HEAD after B4's insert). `architecture-recommendation.md` **byte-identical** (`fd5bbe49…ccb0` both revs) — its stale `50` remains as the frozen superseded record, which is acceptable now that the corrigible device (the decisions amendment trail) is corrected.

## NEW FINDING (non-blocking; test-side follow-up)
**RG3-P3 — `test_worker_untouched`'s rule.md dimension under-enforces its own docstring.** The pin asserts (a) changed-file set == exactly `[meta.json, rule.md]`, (b) zero removed lines, (c) ≥1 added line containing `## Reporting Anomalies`. A pure addition **outside** the duty section therefore stays **GREEN** (planted probe: sentence after `## Must`, no removals ⇒ 1 passed), while the docstring claims "additions-only = the duty section; any other hunk shape still fails". meta.json is fully tight (stray key ⇒ RED; grant moved off last position ⇒ RED). **Assessment:** the pin is strictly stronger than the zero-diff pin it replaced on the file-set/removal/meta dimensions, but weaker than its own documentation on one rule.md dimension. The tree is verified correct; this is a future-drift guard gap. **Fold (test-side, ~10 lines):** assert each added hunk's new-file start ≥ the `## Reporting Anomalies` heading (parse `git diff -U0` hunk headers), or compare added lines to the exact expected duty block — or correct the docstring to match the asserts.

## Disclosures the ceremony/composition record MUST carry
1. **B4b bundle at the wall** — `/metrics` lazy-only 184,866 / 185,000 B (**134 B headroom**); the plan forbids raising the threshold.
2. **AM-16 = default-ON** (supported by the re-gate evidence above); no AM-16 default-OFF disclosure is required.
3. **TOCTOU overshoot is a disclosed design property** — bounded by concurrent in-flight writer width; measured +15 (global) / +18 (per-type) on a single wave of 20; refusals always ledgered or counted (no silent loss).
4. **G1b residual** — the LLM-*decided* end-to-end turn was not exercised (harness transport mismatch); proven instead: `report_anomaly` in the live 76-tool LLM array with full schema, graph invocation in streaming mode, 38/38 assertions over the real tool surface + service + harvest + cluster + F23 + read path.
5. **G4 inherited red** — `test_metrics_family_adjudication::test_the_daemon_diff_is_confined_to_the_allowed_files` is base-proven (hardcoded `LANE_BASE=644c2bfa`); it will redden future gates until the metrics lane re-points/derives it.
6. **Base-proven census as measured: 476 unique red nodes** at base (the dispatch's "~86 / 13 families" figure was never substantiated and diverges ~5.5×).
7. Boot scan registers **43 agents** (47 dirs; loader skips `_inner_soul`/`_prompt_system`, v2 mirrors fold).
8. `/api/metrics/*` returns **503 AUTH_NOT_CONFIGURED** until auth is enabled; the FE nest-reports tab degrades honestly (by design).
9. **`{"key": …}` JSON values are not masked by deliberate SSOT design** (name-based, suffix-anchored masker) — record the exclusion in `decisions.md`.
10. **Privileged-pin census is 1,121 / 9 / 1,112** — the brief's 293/302 figure is stale (mission-watch 293P/1F/12S; metrics declared 302).
11. **G3 corpus deviation** — census ran the default-addopts form (96.7% of corpus, symmetric 1,145 deselections); compensated: the lane's 9 integration-marked nodes executed green, no postgres/slow lane tests exist. RESOLVED-direction (10 base-only reds) not isolation-verified.
12. **`nest_reports_fe_tab` pack uses `timeout 600`** (jest measured 3.8 s) rather than the house 300; dual-layer intact.
13. **Two `anomaly_board_sink` rows coexist** (C3 freeze at 14, operative at 16) under the keep-both convention.
14. **This RESULTS artifact is untracked** by instruction — fold via the standard leader evidence sweep.
15. **Flake ledger entry** — `test_auto_kill_integration::…sibling_alive_row_skipped_by_terminal_lane` is a pre-existing flake (3F/7P at merge base, empty `git log` on its owning files), not lane-caused.

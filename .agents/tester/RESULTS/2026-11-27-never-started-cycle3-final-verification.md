# FINAL Independent Verification — fix cycle 3 (the LAST gate): `never-started-running` detector + `report_anomaly` scrub-path (INV-1…INV-5)

- **Date**: 2026-11-27 (05:34Z → in progress)
- **Branch**: `fix/never-started-detector-anomaly-truncation` · **Worktree**: `/home/nea/ensemble-src-wt-never-started`
- **Base**: `4a17398d2` · **Cycle-2 tip**: `73ea42ce2` (verification commit `a0d23b93b`) · **TIP under test**: `258d389e4` (full `258d389e4344b1e33f0eb2a4729f733f4ad3b771`)
- **Cycle-3 range**: `a0d23b93b..258d389e4` — 1 commit, **4 files** +292/−33 (`daemon/services/anomaly_service.py` +62/−30 · `tests/unit/test_anomaly_report_scrub.py` +218 · `tests/unit/test_anomaly_report_transcript_harvest.py` +11/−3 · `CHANGELOG.md` +1). `origin/latest` is at `d7c15d45f` — NOT rebased (as instructed).
- **Role**: independent test + verification (tester). **No product code modified, nothing pushed, no release cut, no daemon booted** (8079/8088 untouched). `/home/nea/ensemble-src` (detached) never touched.
- **Workers dispatched**: 18 (13 in wave A — 8 packs + 5 proofs; then the regression leg, the ledger-pin worker, and two replacements: one for a worker that wedged on a hidden DB prompt, one for a worker whose finished report was never delivered). Every leg returned measured numbers.
- **Verdict**:
  - **Defect (1) `never-started-running` detector — ✅ PASS.** FINAL and untouched since cycle 1 (`git diff --stat 73ea42ce2..HEAD -- daemon/tools/instance_read.py` EMPTY, also for `0f5af2a5a..HEAD`); 22/22 class green; **live leg carried forward from cycle 1** — there is STILL no read-only path to `ensemble_prod` (F-C7) and no privileged fallback was used (§G9).
  - **Defect (2) `report_anomaly` scrub-path corrections (INV-1…INV-5) — ✅ PASS on product behaviour; all FIVE mutation proofs RED→GREEN with T-c now biting (F-C1 closed); ONE test-side gap found and CLOSED by this verification (the refusal-ledger strip had ZERO pin coverage — now pinned); read-only proof on the NEW scrub path: ✅ 3/3 connections read-only, 9/9 fingerprints identical, writes refused with SQLSTATE 25006, no escaping exceptions, the withheld placeholder carrying the PRE-scrub length (§G6).**
  - **Lane regression — ✅ PASS: 0 net-new reds under BOTH censuses** (27 files/677 nodes cycle-3 census; 28 files incl. `module_level_inputs_b` cycle-2 census).
  - **Lane: READY to merge**, with the open items in the Findings register (none a product defect in this lane).

## Scope decision

The branch diff touches **6 production modules** (`daemon/config.py`, `daemon/repositories/anomaly/models.py`, `daemon/services/anomaly_service.py`, `daemon/services/metrics_query/wiring/w2.py`, `daemon/tools/instance_read.py`, `daemon/tools/nest_report.py`, plus `daemon/services/secret_persistence_scrub.py` from cycle 2) + test files + `CHANGELOG.md` + tester docs, and **NO core seam** (`daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`, `daemon/services/task_processor.py`, `daemon/services/job_queue_service.py` — `git diff --name-only 4a17398d2..HEAD -- <seams> frontend/` = empty) ⇒ **ensure.md Gate Tier 1** (≤ 30 min, fail-loud), not Tier 2; no web check. The blast-radius leg (§G8) is declared **mechanically** (recipe `.agents/tester/skills/blast-radius-leg/`): whole-branch-touched ∪ cycle-3-touched ∪ `tests/unit` files referencing `anomaly_service` / `secret_persistence_scrub` / `AnomalyService` / `report_anomaly` ∪ the anomaly family. ensure.md Core: Critical "no regressions in changed packs" = §G1 + §G8; `dev.sh --timeout-graceful-shutdown 10` static check = §G9 step 5 (PASS, `dev.sh:156/:159`); `concurrency_atomic_unit_test` scoped OUT (the diff touches no lock/thread/asyncio code — `anomaly_service.py` is synchronous scrub logic; disclosed, not run).

## Results per task

| # | Task | Result | Evidence |
|---|---|---|---|
| G1 | Families on tip `258d389e4`, per-file counts vs claims | ✅ **8/8 packs — every claimed count reproduced exactly** | §G1 |
| G2 | Five mutation proofs, independently re-run (RED→GREEN, tree byte-identical) | ✅ **5/5**; C3-M2 reddens the fail-closed excerpt pin T-e (the `_ProbeFalseScrubber` pins cannot reach the mutated branch by construction); C3-M4 reddens **T-c itself**; extra probe: the refusal-ledger strip was UNPINNED | §G2 |
| G3 | H1-O1 refusal-ledger `category_raw` strip — direct probe + permanent pin | ✅ **strip proven live; 2-node pin added** (`2 passed`) | §G3 |
| G4 | Census reconciliation 28/689 vs 27/677 | ✅ **28 paths = 27 test files + one 0-node JSON fixture; cycle-2's +17 = `test_module_level_inputs_b.py` (best fit); identity holds under either census** | §G4 |
| G5 | PACKS.md (tester-owned) | ✅ cycle-3 block + 10 cells refreshed; F-C1 marked RESOLVED; `_TEMPLATE`/`scrub_unavailable_placeholder(n)` replaces the removed alias | §G5 |
| G6 | Read-only proof on the NEW tip (detector + scrub path) | ✅ **PASS** — 3/3 RO connections `SHOW default_transaction_read_only → on`; 9/9 table fingerprints identical; write probes refused with SQLSTATE 25006; **no escaping exceptions**; the withheld placeholder carries the **pre‑scrub** length | §G6 |
| G7 | Fixture render (supplied marker in description + `category_raw`) | ✅ **all renders matched; captured verbatim** | §G7 |
| G8 | Bounded regression tip vs BASE | ✅ **0 net-new under both censuses**; digests equal | §G8 |
| G9 | Live render disposition | ⚠️ **NOT EXECUTED — NO RO PATH**; read path unchanged since cycle 1; cycle-1 citation | §G9 |
| G10 | Verdict | **READY** (with the register below) | §Overall |

## §G1 — families at `258d389e4` (per-file counts)

House invocation (one pack per worker, `timeout 300` outer + pytest-timeout inner, 9 per-variable env scrubs, sibling-env fence before every run, `git status --porcelain` identical before/after — empty — on every run):

```
cd /home/nea/ensemble-src-wt-never-started && env -u ENSEMBLE_SELF_ENV -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_DISCOVERY_URL -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_CLIENT_ID -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE -u DAEMON_PORT timeout 300 ./scripts/run_tests_scrubbed.sh <TARGET> -q --tb=short -rfE
```

| Pack | Collected | Passed | Failed | Wall | Claim | Note |
|---|---|---|---|---|---|---|
| `tests/unit/test_anomaly_report_scrub.py` | **44** | 44 | 0 | 16.77 s | 44/44 ✓ | +5 cycle-3 pins: `test_h1_supplied_legacy_marker_stripped`, `test_d1_category_raw_strips_supplied_marker`, `test_h5_secret_does_not_rescrub_inside_emitted_token`, `test_d2_probe_false_scrubber_withheld`, `test_d2_probe_false_excerpt_withheld` |
| `tests/unit/test_anomaly_report_description_roundtrip.py` | 3 | 3 | 0 | 20.29 s | 3/3 ✓ | unchanged by cycle 3 |
| `tests/unit/test_anomaly_report_tool_registration.py` | 56 | 56 | 0 | 12.65 s | 56/56 ✓ | `git diff --stat 73ea42ce2..258d389e4 -- <file>` empty |
| `tests/unit/test_anomaly_report_transcript_harvest.py` | 17 | 17 | 0 | 1.31 s | 17/17 ✓ | modified by cycle 3 (+11/−3) |
| `…shakedown_defects.py::TestNeverStartedRunningDetector` (CLASS) | 22 | 22 | 0 | 82.54 s (under 13 concurrent siblings; 38.84 s solo at cycle 2) | 22/22 ✓ | all 22 node ids PASSED |
| `tests/unit/test_instance_ops_behaviour.py` | 58 | 58 | 0 | 147.76 s (122.12 s solo at cycle 2) | 58/58 ✓ | slowest 4.84 s call; 4 of top 5 are `setup` |
| `tests/unit/tools/test_module_level_inputs_{a,b,c,d}.py` | 43/17/18/10 = **88** | 88 | 0 | 1.24/0.70/0.85/0.61 s | 88/88 ✓ | `test_second_build_payloads_match_base_fixture` PASS; fixture JSON `+1/−1` vs base = cycle-1 refresh (`fdbfc1255`, cycle-1 §D6) |
| `tests/unit/test_nest_report_rest_route.py` | 22 | 19 | **3** | 9.79 s | 19/22 ✓ | the 3 = QUARANTINE row 11 `TestAM1TheRouteSetPin` ×3 — **0 net-new**; the file's `+13/−1` vs base is the cycle-1 anti-vacuous hardening (`a75d3a759`, predicate unchanged) |

**Totals**: 310 collected · 307 passed · 3 failed (all quarantined) · 0 net-new.

## §G2 — five mutation proofs (independently re-run)

Venue: isolated **detached scratch worktree** `/tmp/ns-c3-mut-*` at `258d389e4` with its own venv (`uv venv --python 3.13 && uv sync --frozen`; `uv.lock` sha256 `e08fa6a0…` identical before/after; import isolation proven — `daemon.__file__` inside the scratch). The lane tree was never mutated. Pack per leg = `test_anomaly_report_scrub.py` + `test_anomaly_report_transcript_harvest.py` (baseline **`61 passed in 20.39s`**; `BASE_SHA` of `anomaly_service.py` = `1a7440e2781bdb3bb3e8f1f30103e27d8cdadabec3b63a74cac2bb565e8de82d`).

| Leg | Mutation (verbatim diff recorded by the worker) | RED | Named pin — assertion line (verbatim) | Restore → GREEN |
|---|---|---|---|---|
| **C3-M1** H1 strip → identity | `LEGACY_SECRET_MARKER_RE = re.compile(r"(?!x)x")` — the `.sub()` stripper is identity on every path (scrub `:697`, accept `:1094`, ledger `:1390`) | `2 failed, 59 passed` | **H1 pin** `test_h1_supplied_legacy_marker_stripped` `:961` — `assert "__ENS_SECRET__" not in out` → `AssertionError: before __ENS_SECRET__a41f6210-1bea-4219-9604-bd2ae506c0a7__ after`; **and the D1 `category_raw` pin** `:1268` — `before __ENS_SECRET__61351155-…__ afte` | sha256 == BASE_SHA · `61 passed` |
| **C3-M2** H3 legacy branch → `return True` | `_scrubber_effective`: `if not hasattr(scrubber, "_current_rows"): return False` → `return True` | `1 failed, 60 passed` | **fail-closed excerpt pin** `test_t_e_excerpt_store_unavailable_withheld` `:1097` — `assert row.excerpt is None` → `AssertionError: assert 'ERROR: boom' is None` (via `_BrokenScrubber`) | sha256 == BASE_SHA · `61 passed` |
| **C3-M3** H5 one-pass → multi-pass `replace` | the single `re.sub` alternation replaced by sequential per-plaintext `str.replace` passes | `1 failed, 60 passed` | **H5 pin** `test_h5_secret_does_not_rescrub_inside_emitted_token` `:1159` — `AssertionError: __ENS___ENS_REDACTED__139590670a3e____da8d9817f4b0__ …` (the `REDACTED` plaintext fired INSIDE the emitted token) | sha256 == BASE_SHA · `61 passed` |
| **C3-M4** D1 token → redeemable | token mint → `"__ENS_SECRET__" + str(uuid.uuid4()) + "__"` | `4 failed, 57 passed` | **T-c ITSELF** `test_t_c_no_redeemable_handle_or_bisected_token` `:944` — `assert _re.match(r"__ENS_SECRET__[0-9a-f-]{36}__", tail)` → `AssertionError: bisected legacy handle: '__ENS_SECRET__[TRUNCATED]'` (the H7-widened guard); + siblings `:583` `stored-secret substitution must fire`, T-a `:917`, H5 `:1159` | sha256 == BASE_SHA · `61 passed` |
| **C3-M5** D1 accept-site `raw_category` → cap-only | `raw_category = category[:CATEGORY_RAW_MAX_CHARS]` (no strip; ledger site intact) | `1 failed, 60 passed` | **D1 pin** `test_d1_category_raw_strips_supplied_marker` `:1268` — `before __ENS_SECRET__5686c496-…__ afte` | sha256 == BASE_SHA · `61 passed` |
| **C3-M6** (extra probe) ledger site only → cap-only | `category_raw=category[:CATEGORY_RAW_MAX_CHARS]` at `:1388` | **`61 passed`** — and `16 passed` in `test_anomaly_rate_limits.py`, `152 passed` across 5 anomaly files; the 5 reds in the 6 remaining anomaly files are identical at clean baseline (pre-existing) | **NO pin catches it** — the refusal-ledger strip had ZERO coverage | sha256 == BASE_SHA |

Tree identity after all legs: scratch `anomaly_service.py` sha256 == BASE_SHA, porcelain empty, HEAD `258d389e4`; lane tree HEAD/porcelain identical before/after; `worktree list | grep -c ns-c3-mut` = 0; `/tmp/ns-c3-mut-*` gone.

**Reading of C3-M2 (do not soften):** the commission predicted the `_ProbeFalseScrubber` pins would redden. They cannot — that scrubber exposes `is_effective`, so `_scrubber_effective` returns at the PROBE branch (`:770`) and never reaches the legacy branch (`:776`). The legacy branch is reachable only by a scrubber with neither probe nor load path (`_BrokenScrubber`), whose pin is T-e — and T-e DID go RED (`'ERROR: boom'` persisted). The authored-description path stays fail-closed under the flip through a second, independent seam (`_current_rows` AttributeError → `_ScrubUnavailableError` → placeholder), so the only observable regression is the excerpt — exactly what T-e pins. The fix is load-bearing and pinned; the single-pin coverage of the legacy branch is noted (F-C3-2).

## §G3 — H1-O1: the refusal-ledger `category_raw` strip (direct probe + permanent pin)

Motivated by C3-M6 (the ledger site is unpinned). Direct probe — in-process SQLite, real `AnomalyService`, refusal tripped by filling the 5/600 s window then sending the 6th report with a marker-bearing category:

```
PROBE1 marker = '__ENS_SECRET__19bc7b96-1199-4c76-b761-2ba8b315a2c7__'        (unregistered id)
[PROBE1-unregistered] refused_token=rate-limited
[PROBE1-unregistered] category_raw repr = 'tool-failure [marker-stripped]'
[PROBE1-unregistered] category repr     = 'other'
[PROBE1-unregistered] len(category_raw) = 30
PROBE2 registered marker = '__ENS_SECRET__81968a58-430e-481e-abba-702153ce3566__' id= 81968a58-430e-481e-abba-702153ce3566
[PROBE2-registered] category_raw repr = 'tool-failure [marker-stripped]'
[PROBE2-registered] len(category_raw) = 30
CONTROL (LEGACY_SECRET_MARKER_RE -> re.compile(r"(?!x)x"), in-process):
[CONTROL-no-strip] category_raw repr = 'tool-failure __ENS_SECRET__8169f757-3d33-4c67-8be2-fae1845aa2f6_'   (len 64 — the cap trims the trailing __)
```

⇒ the stored ledger text depends on the strip (the probe is live, non-vacuous). **Permanent pin added** — NEW file `tests/unit/test_anomaly_report_category_raw_ledger_strip_pin.py` (sha256 `627278edb52c0ee1a2b6727b8bd1915503e2506cbe69f23175c4cee6662c8a47`, 96 lines, 2 nodes): `TestRefusalLedgerCategoryRawStrip::test_refusal_ledger_category_raw_strips_supplied_marker` (positive) and `::test_control_without_the_strip_the_marker_would_survive` (negative control). House invocation: **`2 passed in 1.36s`**; `--collect-only`: `2 tests collected`. The file is committed with this verification's tester artifacts (not part of the gated tip `258d389e4`; it is test-only and was itself proven sensitive by its control leg).

## §G4 — census reconciliation

Mechanical rule applied at both revs with read-only git: **L_tip == L_c2 — the same 28 PATHS** (`L_c2 △ L_tip = ∅`): 27 test files + `tests/unit/tools/fixtures/module_level_inputs_b_base.json` (0 nodes, branch-touched in cycle 1).

| file (tip) | nodes | in L_c2 | reasons |
|---|---:|:---:|---|
| tests/unit/metrics/test_metrics_family_w4.py | 24 | Y | R |
| tests/unit/plugin_subsystem/test_designer_rewire.py | 25 | Y | R |
| tests/unit/services/test_long_tool_nudge_secret_typed_args.py | 12 | Y | R |
| tests/unit/test_anomaly_board_sink.py | 16 | Y | R,A |
| tests/unit/test_anomaly_cluster_assignment.py | 13 | Y | R,A |
| tests/unit/test_anomaly_rate_limits.py | 16 | Y | R,A |
| tests/unit/test_anomaly_report_description_roundtrip.py | 3 | Y | T,R,A (D1 — absent at base) |
| tests/unit/test_anomaly_report_duty_prompts.py | 19 | Y | R,A |
| tests/unit/test_anomaly_report_grant_pins.py | 15 | Y | R,A |
| tests/unit/test_anomaly_report_models.py | 25 | Y | A |
| tests/unit/test_anomaly_report_scrub.py | 44 | Y | T,C3,R,A |
| tests/unit/test_anomaly_report_tool_registration.py | 56 | Y | T,R,A |
| tests/unit/test_anomaly_report_transcript_harvest.py | 17 | Y | T,C3,R,A |
| tests/unit/test_anomaly_retention.py | 19 | Y | R,A |
| tests/unit/test_checkpoint_pool_knob_threading.py | 4 | Y | R |
| tests/unit/test_instance_ops_behaviour.py | 58 | Y | T |
| tests/unit/test_instance_read_shakedown_defects.py | 108 | Y | T |
| tests/unit/test_kb_writer_tools.py | 23 | Y | R |
| tests/unit/test_mcp_secret_spawn.py | 10 | Y | R |
| tests/unit/test_nest_report_read_tools.py | 26 | Y | R |
| tests/unit/test_nest_report_rest_route.py | 22 | Y | T,R |
| tests/unit/test_persistence_prepare_threshold.py | 1 | Y | R |
| tests/unit/test_secret_persistence_scrub.py | 39 | Y | R |
| tests/unit/test_skill_keeper_confinement.py | 19 | Y | R |
| tests/unit/test_skill_revival_wave2_pins.py | 7 | Y | R |
| tests/unit/test_webfetch_builtin.py | 37 | Y | R |
| tests/unit/tools/fixtures/module_level_inputs_b_base.json | **0** | Y | T |
| tests/unit/tools/test_pok_census_route_unit.py | 19 | Y | R |
| **TOTAL** | **677** | | |

Anomaly family (`tests/unit/test_anomaly_*.py`, 11 files) = 243 at the tip (cycle 2 recorded 222 at `73ea42ce2`; the scrub file grew 39→44 and other family files changed upstream).

**Reconciliation.** (1) *Cycle 3's 27/677 and 26/633 are exactly the mechanical rule with the 0-node JSON fixture excluded* — the "28th file" relative to the mechanical path set is `tests/unit/tools/fixtures/module_level_inputs_b_base.json` (0 nodes; a fixture, not a test). (2) *Cycle 2's 28/689 (`73ea42ce2`) and 27/650 (BASE) are +17 over the mechanical count on BOTH legs* (mechanical at `73ea42ce2` = 677 − 5 = 672; at BASE = 633). A constant +17 on two legs is a 17-node file carried by cycle 2's census and not by the mechanical rule: the only consistent candidate is **`tests/unit/tools/test_module_level_inputs_b.py`** (17 nodes, unchanged at both revs, present in cycle 2's F1 family list). Cycle 2's file list was not recorded, so this is the best-fit explanation, not a proof; the census worker's alternative ("stale/inflated base count") does not explain a constant +17 on both legs. (3) *Red-set identity holds under either census* — §G8 ran the 27-file leg AND the extra 17-node file separately on both legs.

## §G5 — PACKS.md (tester-owned)

`.agents/tester/PACKS.md` — **+36/−9 lines in the pre-existing content (exactly 10 rows: `:16`, `:19–24`, `:1207–1208`) + a 27-line cycle-3 block prepended** (markers `never-started-detector-cycle3-final-20261127-begin/end`), applied by an anchor script that asserts each replaced phrase occurs exactly once (15/15). Pre-edit sha256 prefix `aead6e9dc2fc9fb8`; integrity proof: `diff` of everything below the new block vs the pre-edit copy shows ONLY those 10 rows. Specifically: `anomaly_report_scrub` node count **39→44** (cycle-2 block row and the canonical row) with the 5 cycle-3 pins named; `gap2 … SCRUB_UNAVAILABLE_PLACEHOLDER` → `scrub_unavailable_placeholder(n)` / `SCRUB_UNAVAILABLE_PLACEHOLDER_TEMPLATE` (both rows); **F-C1 marked RESOLVED** (H7; C3-M4 reddens T-c itself at `:944`); the **28/689 vs 27/677 derivation** noted on the blast-radius row; detector/nest/module/ops/mutation rows re-stamped at `258d389e4`; the canonical `anomaly_report_transcript_harvest` row re-stamped (17/17, file modified by cycle 3). New rows: `never_started_cycle3_blast_radius_leg`, `anomaly_scrub_mutation_proof` (C3-M1…M6), **`anomaly_category_raw_ledger_strip_pin`** (the new pin file), `anomaly_readonly_proof_cycle3`, `anomaly_fixture_render_cycle3`.

## §G6 — read-only proof on the NEW tip — ✅ **PASS** (2 disclosed caveats + 1 open shape question)

Evidence: an INDEPENDENT re-run (the first G6 worker produced its report at 06:15:24Z but the completion never registered/delivered — nest anomaly `1f4b9b02`; the re-run's durable artefact is `/tmp/ns-c3-g6-report-2.md`, sha256 `8adacbf00c001e8a61f572ef85b8114940751f8693d68be0e11d5d7e29430183`, 1031 lines).

**Venue + fence.** VENUE 1 — scratch PostgreSQL `ens_ns_c3_ro_bcb03013` (owner `ensemble_dev_app`), created/dropped through the OS role `nea` over the local socket (disclosed: `nea` is a superuser on the LOCAL dev server; used ONLY for CREATE/DROP DATABASE + listing — no `ALTER ROLE`, no grants, no prod contact). Read-only via a **connection-level** fence, `options="-c timezone=UTC -c default_transaction_read_only=on"` (no ALTER ROLE needed). Every RO physical connection printed its FIRST statement: `SHOW default_transaction_read_only -> on` — 3/3 (one per phase: Leg A, Leg A2, Leg B; `pool_pre_ping=False` so the SHOW is the literal first statement). Schema built by the real `InstanceManager` boot path (108 base tables; 0 SQLModel tables missing).

**Leg A — detector under RO: all MATCH.**
- A1 render: `count: 1`, `evidence_legs: "ok"`, `scope: "complete"`, `len(queued_jobs_behind_queue) == 50` exactly, `queued_jobs_total == 60`, `queued_jobs_capped == true`; detail verbatim: *"never started: running, zero checkpoint rows, first task 7 still pending with started_at NULL; pending task age 3627s (threshold 2700s) … 50 job(s) queued behind the held queue(s) (capped sample — 60 queued in total)"*; envelope `scan_truncated: false`, `scan_stop_reason: "end_of_set"`.
- Fingerprints before→after: instances · task · checkpoints · job_locks · job_queue_items · job_queues · instance_hierarchy · message_queue · secrets = **9/9 identical**.
- A2 negative control (one checkpoint row, writable step): `count: 0` — the ONLY fingerprint delta in the whole proof is `checkpoints 0→1` (sha `e3b0c442… → 76076dd4…`); fixture restored (9/9 identical again).
- A3 raising probe (`_never_started_candidates` → RuntimeError, in-process): `count: 0`, `evidence_legs: "degraded"`, **ESCAPED EXCEPTION: None**; warning verbatim: `instance_read: never-started candidate probe failed — the never-started-running detector will NOT flag: NS-C3-RO probe: simulated candidate-probe failure`.
- A4 write probe: `psycopg.errors.ReadOnlySqlTransaction` — **SQLSTATE 25006** — `cannot execute INSERT in a read-only transaction`; probe row landed = **0**.
- Statement-level proof: 15 statements during the detector call, **0 write verbs**.

**Leg B — scrub path under RO: MATCH (one shape divergence, D‑1).**
- B1: `__ENS_REDACTED__cdd0470c94a4__`, 12hex == `sha256(secret_id)[:12]` (id `f3071d06-e04f-4712-96cd-1da69a831978`); plaintext absent; `__ENS_SECRET__` absent; `truncated: False`.
- B2 (H1): input carrying `__ENS_SECRET__96447a25-1111-2222-3333-444455556666__` → `the agent supplied [marker-stripped] in the report body`.
- B3 withheld: authored field **VERBATIM** `'[REDACTED: secret scrub unavailable; field withheld; original 109 chars]'` == `scrub_unavailable_placeholder(109)`; it carries the **PRE-scrub** length (109; post-scrub would be 113); `truncated: True`; **ESCAPED EXCEPTION: None**; no plaintext. (i‑a) `is_effective()→False` ⇒ excerpt `None`; (i‑b) H3 legacy branch ⇒ excerpt `None`; (ii) TOCTOU ⇒ excerpt NOT `None` (D‑1). Withhold warning verbatim: `anomaly: excerpt withheld — the value scrubber is absent or ineffective (fail-closed); the report persists without it`. Positive control (the real RO-backed scrubber) yields the D1 token in BOTH the field and the excerpt ⇒ the withhold is not vacuous.
- B4: the report path's first write `INSERT INTO anomaly_clusters …` → **SQLSTATE 25006**; `anomaly_reports` 0→0, `anomaly_clusters` 0→0.
- B fingerprints: **9/9 identical** incl. `secrets` (`e5b2b596c54c4813…` unchanged); ledger: 173 statements, 3 write attempts (2 boot-path + the B4 insert) — **the scrub path issued no write**.

**Leg C — static (AST-based).** `anomaly_service.py`: **0** SQL literals; `secret_persistence_scrub.py`: **0** (its three `"update"` hits are a LangGraph `Command.update` attribute, not SQL); `instance_read.py`: **9, all SELECT** (497 · 1638 · 1709 · 2045 · 2152 · 2200 · 2218 · 2578 · 3077), **zero DML/DDL**. (Cycle 2's grep counted 10 — a counting-method difference, not a product difference.)

**Teardown**: `DROP DATABASE` OK; `pg_database LIKE 'ens_ns_c3_ro_%'` = **0**; `pg_namespace LIKE 'ns_c3_ro_%'` = **0**; the scratch Fernet key and work dir removed; no daemon booted; 8079/8088 untouched; no prod contact.

**D‑1 🟠 OPEN SHAPE QUESTION (not a proven defect).** In the **TOCTOU** branch (scrubber effective at the probe, raises mid-scrub) the EXCERPT is not withheld: it persists the placeholder string `'[REDACTED: secret scrub unavailable; field withheld; original 90 chars]'` (sha256 `6d166242…`) instead of `None`. INV‑2 still holds (no plaintext, no redeemable handle, no escaping exception, no typed refusal), and cycle 2's own text attached "excerpt = None (withheld)" only to the GAP‑2 (ineffective scrubber) case — so this is a divergence from the **commission's wording**, not a demonstrated regression. Lane decision needed: should the TOCTOU branch withhold the excerpt entirely (`None`) as well, or is a bounded, secret-free placeholder in the excerpt column acceptable?

**D‑2 🟡 DISCLOSED (harness/product-shape, not a lane defect).** The manager **BOOT path** is not read-only: `queue.discard_on_startup` wipes pending task rows, `_ensure_blueprint_g7_unique_index` and the infra-asset-type bootstrap write. All three write attempts were **refused by the fence (25006)**; the proof therefore ran with `config.queue.discard_on_startup = False` (a production knob, boot path only) and the four `_ensure_postgres_*` helpers stubbed (schema pre-built). **The detector and the scrub path were never stubbed, patched or exempted.** An early attempt with the knob at default deleted the fixture task row **in the scratch DB only** (the dev DB was verified untouched).

**D‑3/D‑4 🟡 fixture limits.** `wedge_captures` does not exist on PostgreSQL (SQLite-only `.sql` runner) ⇒ the wedge-harvest leg degrades (unrelated to this tip); `instance_hierarchy` / `message_queue` are empty by the §F6(a) recipe (fingerprinted empty) — a fixture coverage limit, not a detector limit.

## §G7 — fixture render on `258d389e4` (verbatim; synthetic secret `SYNTH-SECRET-FRAGMENT-9931`, id `4e15d241-d514-4a27-adec-b7ce13657f46`, token `__ENS_REDACTED__cf7c394da9c4__` = `__ENS_REDACTED__` + sha256(secret_id)[:12])

Venue: throwaway PostgreSQL `ensemble_fix_ns_c3_01af50bdfea8` (created `OWNER ensemble_dev_app` via the OS user's own `nea` role over the local socket — disclosed: `nea` is a superuser on the LOCAL dev server and was used for CREATE/DROP DATABASE only; every data connection ran as `ensemble_dev_app`), real `SecretService` + real `AnomalyService.accept_report`, raw-SQL read-back through psycopg AND psql (byte-identical, sha256 `86850f5f…`). Config in force: description 8000 · suspected_cause 2000 · excerpt 500/msg · `CATEGORY_RAW_MAX_CHARS` 64. Script sha256 `76e1cc4f…` (`/tmp/ns-c3-render-7KdrNw/render_g7.py`).

**R1 — supplied markers + registered secret + expansion cut** (pre-scrub 8000 == cap; post-scrub 8134 > cap: 51 plaintexts 26→30 chars (+4 each), 2 markers 52→17 (−35 each)). Stored `description` (8000 chars, `truncated = True`):
```
HEAD(220) = 'R1 expansion-cut fixture: the tool echoed handle A=[marker-stripped] and handle B=[marker-stripped]; the credential value __ENS_REDACTED__cf7c394da9c4__ appeared in the transcript; leak00 __ENS_REDACTED__cf7c394da9c4__; '
TAIL(200) = 'ppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp\n[TRUNCATED: original 8000 chars; content cut after redaction: pre-scrub 8000 chars, post-redaction 8134 chars; stored prefix only]'
suspected_cause = None ; excerpt = None ; excerpt_sha256 = None ; excerpt_length = None   (fake transcript has no 'ERROR:' tool message — fixture design, NOT withheld)
category = 'tool-failure' ; category_raw = 'tool-failure' ; outcome = 'accepted' ; created_at = '2026-11-27T06:44:36.662690+00:00'
cluster_id = 'f367e25b-be2a-421c-ac20-816e78c06626' ; cluster_report_count = 1 ; cluster_first_report_at = '2026-11-27T06:44:36.662690+00:00' ; result.truncated = True
```
| check | value |
|---|---|
| plaintext in stored / `[:6]` / `[-6:]` | False / False / False |
| `'__ENS_SECRET__' in stored` (registered / unregistered marker) | False (False / False) |
| `'[marker-stripped]' in stored` | True (count 2) |
| token in stored · token == `__ENS_REDACTED__`+sha256(secret_id)[:12]+`__` | True (count 51) · True |
| `'[TRUNCATED: original 8000' in stored` · endswith `'stored prefix only]'` | True · True |
| `len(stored)` · `== 8000` · `truncated` | 8000 · True · True |

**R2 — fits after scrubbing (no cut)**: `description = 'R2 fits-after-scrub fixture: handle [marker-stripped] and value __ENS_REDACTED__cf7c394da9c4__ then padding ' + 'a'*3000` (len 3108; pre 3139 − 35 + 4 = 3108 = measured); `truncated = False`; `'[TRUNCATED' in stored` False; `'__ENS_SECRET__' in stored` False; plaintext False; `stored == expected verbatim` True.

**R3 — `category_raw` strip-THEN-cap at the accept site**:
| raw category | stored `category` | stored `category_raw` | len |
|---|---|---|---|
| `'tool-failure __ENS_SECRET__691985c5-3d28-49f3-ae25-a7570127f9f9__'` (65, unregistered) | `'other'` | `'tool-failure [marker-stripped]'` | 30 |
| `'tool-failure __ENS_SECRET__4e15d241-d514-4a27-adec-b7ce13657f46__'` (65, REGISTERED id) | `'other'` | `'tool-failure [marker-stripped]'` | 30 |
| `'tool-failure ' + 'x'*70` (83, benign) | `'other'` | `'tool-failure xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'` | **64** (verbatim first 64 chars) |
All: `'__ENS_SECRET__' in category_raw` False ×3; `len ≤ 64` True ×3; `outcome = 'accepted'`.

**R4 — `suspected_cause` + transcript excerpt carrying a supplied marker** (fake transcript = one `ToolMessage('ERROR: write failed; handle __ENS_SECRET__<uuid4>__ value SYNTH-SECRET-FRAGMENT-9931 (synthetic)')`):
```
suspected_cause  = 'the tool echoed handle [marker-stripped] and the value __ENS_REDACTED__cf7c394da9c4__ into stdout; it must not persist'   (118)
excerpt          = 'ERROR: write failed; handle [marker-stripped] value __ENS_REDACTED__cf7c394da9c4__ (synthetic)'   (94)
excerpt_sha256   = '3c609637b6ebf2d33b7877293d59435f82b436a67e23859acf0d365a23feef9d' ; excerpt_length = 94   (== sha256/len of the stored excerpt)
category = 'tool-failure' ; category_raw = 'tool-failure' ; outcome = 'accepted' ; truncated = False
```
excerpt is None (withheld)? **False** — the scrubber is effective, so the fail-closed withhold did not fire.

**R5 — the REFUSAL-LEDGER site (bonus)**: 6th `worker` report → `AnomalyRefusalError(token='rate-limited', detail='rate-limited: 600s 5/5')`; ledger row `category_raw = 'tool-failure [marker-stripped]'` (30), `category = 'other'`, `description = 'R5 ledger fixture: handle [marker-stripped] value __ENS_REDACTED__cf7c394da9c4__ (sixth worker report — over the 5/600s budget)'`, `outcome = 'rate_limited'`, `refusal_reason = 'rate-limited: 600s 5/5'`, harvest columns NULL.

**Whole-store sweep** (psql, independent client): 7 `anomaly_reports` rows, 1 `secrets` row; `__ENS_SECRET__` hits **0**, synthetic-plaintext hits **0** across every text/json column (incl. `parent_chain`/`parent_chain_agents`/`dispatch_chain`/`wedge_capture_ids`).

Observations (F-C3-3, F-C3-4): a marker-bearing category folds to `other` (pre-existing exact-match canonicaliser; `category_raw` keeps the visible stripped text) and the result envelope's `fold_notice` echoes the lowercased raw category — marker included — back to the SUPPLYING agent (`nest_report.py:338-340`; not persisted, not logged; the agent's own tool call already carries the marker ⇒ no new exposure). The commission's arithmetic figures were off: the plaintext is 26 chars → token 30 (+4), the marker 52 → `[marker-stripped]` 17 (−35). Teardown: `DROP DATABASE`; `pg_database LIKE 'ensemble_fix_ns_c3_%'` = 0; no roles left; lane porcelain before/after identical (the only delta in the tree is the tester-added pin file).

## §G8 — bounded regression, HEAD `258d389e4` vs BASE `4a17398d2` (Tier 1, recipe-loaded)

Slicing proofs: HEAD (D1 + D2, 27 files) `677 tests collected in 7.67s`; BASE (D2, 26 files — `test_anomaly_report_description_roundtrip.py` absent by construction) `633 tests collected in 9.25s`; extra `test_module_level_inputs_b.py` 17 on both legs. BASE leg = a REAL detached worktree at `4a17398d2` with its own `.venv` (`uv.lock` sha256 `e08fa6a0…` identical; `daemon.__file__` inside the base tree). Projection: 677 nodes at ~6.8 nodes/s ⇒ ≈ 100 s/leg (under the 30-min cap). Pre-exclusions: none of the declared files is in the recipe's excluded families.

| Leg | Files | Nodes | Result | Wall |
|---|---|---|---|---|
| HEAD `258d389e4` (`-n 8`) | 27 | 677 | `12 failed, 665 passed` | 109.53 s |
| BASE `4a17398d2` (`-n 8`) | 26 | 633 | `12 failed, 621 passed` | 92.26 s |
| extra HEAD `module_level_inputs_b` | 1 | 17 | `17 passed` | 0.89 s |
| extra BASE `module_level_inputs_b` | 1 | 17 | `17 passed` | 0.98 s |

Masking check (both legs VALID): progress-char sum = collected (677/633); `grep -cE '^(FAILED|ERROR)'` = 12 = junit failures+errors; junit `<testcase>` = 677/633.

Red-set digests: **GREP-FULL** sha256 `e51df04d0a2e2abbbfdfc067d7355d62136b17fad4d940d602578d454848d302` on BOTH legs; **BARE-ID** sha256 `98d3aa58186cda2d642222d7f6601ef8606c65f6008c90752791120fab46f428` on BOTH legs. **HEAD-only ∅ · BASE-only ∅ · both = 12** — under the 27-file census AND under the census ∪ `module_level_inputs_b` (the extra file is 17/17 on both legs).

| # | Red (identical on both legs) | Family | Classification |
|---|---|---|---|
| 1–4 | `test_instance_read_shakedown_defects.py::TestF12StatusForwardSeam` ×4 | `_RecordingRepo` double lacks `list_child_ids_for_parents` | pre-existing on base |
| 5–7 | `test_nest_report_rest_route.py::TestAM1TheRouteSetPin` ×3 | QUARANTINE row 11 | pre-existing (quarantined) |
| 8 | `test_anomaly_report_duty_prompts.py::TestTheSealRePin::test_every_sealed_row_matches_the_real_bytes` | stale seal baselines | pre-existing |
| 9–11 | `test_anomaly_report_grant_pins.py` ×3 | grant-pins family | pre-existing |
| 12 | `test_anomaly_report_models.py::TestTheMigrationCensus::test_the_census_is_121_files_with_121_distinct_prefixes` | migration-census pin (141 files at tip) | pre-existing |

Per-file (HEAD / BASE): scrub 44/0 vs 25/0 · roundtrip 3/0 vs absent · registration 56/0 vs 56/0 · harvest 17/0 vs 17/0 · shakedown 104/4 vs 82/4 · instance_ops 58/0 vs 58/0 · nest 19/3 vs 19/3 · module_level_b 17/0 vs 17/0 — matching the §G1 solo runs. No 3× isolation needed (empty diff). Teardown: base worktree removed (`worktree list | grep -c ns-c3-base` = 0); lane HEAD/porcelain unchanged.

## §G9 — live render disposition — **NOT EXECUTED (NO RO PATH)**; read path unchanged

```
git diff --stat 0f5af2a5a..HEAD -- daemon/tools/instance_read.py   → (EMPTY)
git diff --stat 73ea42ce2..HEAD -- daemon/tools/instance_read.py   → (EMPTY)
git diff --stat 4a17398d2..HEAD -- daemon/tools/instance_read.py   → 955 lines changed (the cycle-1 detector work only)
```

RO-path evidence (DEV reads only; no privileged fallback — no `sudo -u postgres`, no superuser, no `ALTER ROLE`, no writable role, no write to prod):

| Source | State |
|---|---|
| dev `db_connections` registry (`ensemble_dev`, column `connection_name`) | **EMPTY (0 rows)** — no `ensemble_prod_ro` row |
| dev `secrets` store (`ensemble_dev`) | **EMPTY (0)** |
| `~/.pgpass` | absent |
| `/tmp/fetch_ro.py` (cycle-1 helper) | absent |
| ambient env var names matching `prod` / `_ro$` / `PGPASS` | none |

Cycle 1's live render therefore still describes this tip's detector (read path byte-unchanged since `0f5af2a5a`), with its caveat intact (verbatim, RESULTS/2026-11-26-never-started-delta-regate.md §D9):
```
{"detector": "never-started-running", "instances": [], "count": 0, "evidence_legs": "degraded", "scope": "complete", "disagreement_pin": null}
```
> **This is a LOWER BOUND, not a confirmed 0** — `ensemble_prod_ro` has no SELECT on `checkpoints`, so the checkpoint leg degrades and the detector deliberately refuses to flag what it cannot verify (correct fail-closed behaviour).

The gap, plainly: **no fresh live render at `258d389e4` was obtainable**; the 0-flagged result is cycle 1's. The cycle-3 changes touch only the scrub path (verified end-to-end on a real PostgreSQL scratch DB in §G7 and under a read-only session in §G6), not the detector.

ensure.md static check: `grep -n -- '--timeout-graceful-shutdown 10' dev.sh` → `156:# --timeout-graceful-shutdown 10 ensures uvicorn forces exit after 10s even` · `159:$PYTHON -m uvicorn daemon.api:app … --timeout-graceful-shutdown 10` — **PASS**. H4 symbols: `SCRUB_UNAVAILABLE_PLACEHOLDER_TEMPLATE` at `anomaly_service.py:269`, `scrub_unavailable_placeholder(n)` at `:274`.

## Findings register

| ID | Sev | Finding | Location | Action |
|---|---|---|---|---|
| **F-C3-1** | 🟠 important (TEST-side) — **CLOSED by this verification** | The refusal-ledger `category_raw` strip (H1's second site) had **ZERO pin coverage**: mutation C3-M6 (strip deleted at the ledger site) left `61 passed` + `152 passed` across the anomaly surface. The H1 claim listed the site as covered; it was not. | `daemon/services/anomaly_service.py` ~:1388-1392 | Pinned: `tests/unit/test_anomaly_report_category_raw_ledger_strip_pin.py` (2 nodes, positive + non-vacuity control) — committed with this verification's tester artifacts. |
| **F-C3-2** | ℹ️ info | The H3 legacy-branch flip is caught by exactly ONE pin (`test_t_e_excerpt_store_unavailable_withheld`, via `_BrokenScrubber`); the `_ProbeFalseScrubber` pins cannot reach that branch by construction (they return at the probe branch). Authored fields stay fail-closed under the flip via a second seam. | `anomaly_service.py:766-777` | Disclose only; the fix is load-bearing and pinned. |
| **F-C3-3** | ℹ️ info | A marker-bearing category folds to `other` (pre-existing exact-match canonicaliser) and the result envelope's `fold_notice` echoes the raw category — marker included — to the SUPPLYING agent (not persisted; the agent's own call already carries it ⇒ no new exposure). | `anomaly_vocab.py:160-186`, `nest_report.py:338-340` | Disclose; optional hygiene follow-up (strip markers in the notice) — not a cycle-3 defect. |
| **F-C3-4** | ℹ️ info | Commission arithmetic figures corrected by measurement: plaintext 26 → token 30 chars (+4), marker 52 → `[marker-stripped]` 17 (−35). Behaviour exactly as expected. | — | Recorded. |
| **F-C3-5** | 🟠 important (ENVIRONMENT) — carried from cycle 2 (F-C7) | **No read-only path to `ensemble_prod`** (dev registry and secrets empty; no pgpass; peer auth refuses `ensemble_prod_ro`) ⇒ the live leg cannot be re-run by ANY lane until the RO credential is re-provisioned. No privileged fallback used. | dev `ensemble_dev` · `/tmp` | Operator/DB lane: re-provision `ensemble_prod_ro`. |
| **F-C3-6** | ℹ️ info | Census: the mechanical path set includes a 0-node JSON fixture (28 paths = 27 test files); cycle 2's 28/689 carried `test_module_level_inputs_b.py` (+17) — red-set identity holds under either census. | PACKS.md blast-radius row | Recorded in PACKS.md. |
| **F-C3-7** | ℹ️ info | `instance_ops_behaviour` 147.76 s under contention (122 s solo) — ~49 % of the 300 s guard; setup-dominated. | PACKS.md row | Watch the trend. |
| **F-C3-8** | ℹ️ info (harness) | One worker wedged on a hidden DB password prompt (terminated, replaced; anomaly `1193da97`); one worker's finished report never registered/delivered — 200-char truncation on the subtree read path (anomaly `1f4b9b02`; independent re-run supplied the evidence). | harness | Non-interactive DB rule (`psql -w`, `timeout`) + report-to-file added to worker briefs. |
| **F-C3-9** | 🟢 open shape question (NOT a defect) | In the TOCTOU branch (scrubber effective at the probe, raises mid-scrub) the EXCERPT is not withheld — it persists the placeholder `'[REDACTED: secret scrub unavailable; field withheld; original 90 chars]'` instead of `None`. INV‑2 holds (no plaintext, no handle, no escaping exception, no typed refusal); cycle 2 attached "excerpt = None" only to the GAP‑2 case. | `daemon/services/anomaly_service.py` (scrub_text → excerpt harvest) | Lane decision: withhold the excerpt entirely on TOCTOU, or accept a bounded secret-free placeholder in the excerpt column? |
| **F-C3-10** | ℹ️ info | The manager BOOT path is not read-only (`queue.discard_on_startup` wipes pending task rows; `_ensure_blueprint_g7_unique_index` + infra-asset-type bootstrap write). Under the RO fence all such writes are REFUSED (25006); the proof ran with `queue.discard_on_startup = False` and the four `_ensure_postgres_*` helpers stubbed. The detector and scrub paths were never stubbed. | `daemon/manager.py` boot path | Disclose; relevant to any future "prove read-only" work — the boot path needs its own fence story. |

## Side effects of this verification

- **No product code modified; no commit to any product path; nothing pushed; no release cut; no daemon booted; ports 8079/8088 untouched.** Tester-owned changes (committed by the tester after this report): `.agents/tester/PACKS.md`, this RESULTS file, a new LESSONS file, and the NEW test-only pin `tests/unit/test_anomaly_report_category_raw_ledger_strip_pin.py` (not part of the gated tip `258d389e4`).
- Scratch artefacts removed and verified: the mutation worktree (`ns-c3-mut-*`, count 0), the BASE worktree (`ns-c3-base-*`, count 0), the render scratch DB (`ensemble_fix_ns_c3_%` = 0), `/tmp/ns-c3-*` work dirs. The `nea` PostgreSQL role (a superuser on the LOCAL dev server) was used ONLY for `CREATE DATABASE` / `DROP DATABASE` of the throwaway render DB — disclosed; no sudo, no `postgres` role, no `ALTER ROLE`.
- **LIVE DB: untouched.** No connection to `ensemble_prod` / 10.44.0.2 was attempted (no credential source exists); no DSN, password or key was ever printed (sha256 12-hex prefixes only).
- **Never touched**: `/home/nea/ensemble-src` (detached), other lanes' branches and worktrees, `agents/ari/tools_note.md`.

## Overall status

- **Defect (1) `never-started-running` detector — ✅ PASS.** Untouched since cycle 1; 22/22; read path byte-identical since `0f5af2a5a`; live leg carried forward from cycle 1 (no RO path — F-C3-5).
- **Defect (2) `report_anomaly` scrub-path corrections — ✅ PASS on product behaviour.** INV-1 (whole-text scrub before any cut) ✅; INV-2 (no plaintext fragment; TOCTOU ⇒ placeholder) ✅ (§G7 R1/R2/R4: 0 plaintext, 0 `__ENS_SECRET__` across the store; §G6 B3: placeholder verbatim with the pre-scrub length, no escaping exception); INV-3 (storage-absolute: no redeemable or partial handle, incl. `category_raw` at BOTH sites) ✅ — mutation-proven at every site, **and the one unpinned site is now pinned (F-C3-1)**; INV-4 (documented ceiling, loud cut with the PRE-scrub length + reason) ✅ verbatim; INV-5 (excerpt withheld when the scrub cannot be trusted) ✅ mutation-proven (C3-M2) and read-only-proven (§G6 B3). **F-C1 closed** (T-c reddens itself, C3-M4). **Read-only proof on the new scrub path: ✅ PASS (§G6)** — with the D‑1 excerpt-shape question carried as an open item (F-C3-9).
- **Lane regression — ✅ PASS: 0 net-new under both censuses** (27 files/677 HEAD vs 26/633 BASE; digests equal).
- **Registry — ✅ DONE** (cycle-3 block + 10 refreshed cells + the new pin registered).
- **Lane: READY to merge.** Open items are environment (F-C3-5) and informational; none is a product defect in this lane.

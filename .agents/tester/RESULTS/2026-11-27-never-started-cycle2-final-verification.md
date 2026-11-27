# FINAL Independent Verification — `never-started-running` detector + `report_anomaly` scrub-path security (fix cycle 2)

- **Date**: 2026-11-27 (2026-11-27T00:20Z → 2026-11-27T02:30Z)
- **Branch**: `fix/never-started-detector-anomaly-truncation` · **Worktree**: `/home/nea/ensemble-src-wt-never-started`
- **Base**: `4a17398d2` · **Cycle-1 tip**: `0f5af2a5a` · **TIP under test**: `73ea42ce2` (full sha `73ea42ce2758506a4624781f4d049815629ab49d`)
- **Cycle-2 range**: `0f5af2a5a..73ea42ce2` — **4 commits** (`fd5df128f`, `004216694`, `07cd7d5ae`, `73ea42ce2`), **5 files** (+556/−43)
- **Role**: independent test + verification (tester). **No product code modified, nothing pushed, no release cut, no daemon booted** (8079/8088 untouched).
- **Workers dispatched**: 16 (9 packs / legs + 7 bespoke proofs). Every leg returned measured numbers.
- **Verdict**:
  - **Defect (1) `never-started-running` detector — ✅ PASS** (with one coverage caveat: the **live** leg could not be re-run at this tip — the read-only credential path to `ensemble_prod` no longer exists and no privileged fallback was permitted; the detector's read path is unchanged since cycle 1, so cycle 1's live render still describes this tip — §F7).
  - **Defect (2) `report_anomaly` scrub-path corrections (INV-1…INV-5) — ✅ PASS on product behaviour, with ONE CONFIRMED TEST-SIDE PIN HOLE (F-C1).** The product satisfies every invariant; the pin named for INV-3 (`T-c`) does **not** discriminate a token swap (§F3 leg ii). The fix itself remains load-bearing (two sibling pins go RED).
  - **Lane regression — ✅ PASS: 0 net-new reds.**
  - **Lane: READY to merge**, with F-C1 disclosed as a test-coverage gap (not a product defect) and the "9 failed / 412 passed" figure shown to be **unreproducible** at this tip (deterministic truth at the census-matched 421-node set = **8 failed / 413 passed**).

## Scope decision

The branch diff touches **5 production/test/doc files** (2 production modules + 2 test files + `CHANGELOG.md`) and **no core seam** (`daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`, `daemon/services/task_processor.py`, `daemon/services/job_queue_service.py`) ⇒ **ensure.md Gate Tier 1** (≤ 30 min, fail-loud), not Tier 2. No frontend file is touched ⇒ no web check (F10). The blast-radius leg (F8) is declared **mechanically** (cycle-2-touched ∪ whole-branch-touched ∪ `tests/unit` files referencing the changed modules ∪ the anomaly family) rather than hand-picked — 28 files / 689 nodes at HEAD.

## Results per task

| # | Task | Result | Evidence |
|---|---|---|---|
| F1 | Targeted runs at the tip, per-file counts | ✅ **8 packs, 0 net-new reds** | §F1 |
| F2 | Invariant pins T-a…T-g — locate, run, assertion per pin | ✅ **8/8 PASS**; 2 pins carry a caveat | §F2 |
| F3 | Mutation proofs (i)(ii)(iii), red→green | ⚠️ **2/3 named pins discriminate; T-c does not (F-C1)** | §F3 |
| F4 | D1 token semantics (parser + redeem) | ✅ **non-resolvable CONFIRMED** | §F4 |
| F5 | Strict read-only proof (detector + scrub path) | ✅ **CONFIRMED both; fail-closed proven** | §F5 |
| F6 | Fixture renders (detector cap 50/60 + scope; scrubbed anomaly row) | ✅ **both captured verbatim** | §F6 |
| F7 | LIVE read-only render vs `ensemble_prod` | ⚠️ **NOT EXECUTED — NO RO PATH** (dev credential registry empty; no privileged fallback used) | §F7 |
| F8 | Bounded wider regression HEAD vs BASE | ✅ **0 net-new**; "9/412" unreproducible | §F8 |
| F9 | Registry (PACKS.md) | ✅ **block added + 2 stale cells refreshed** | §F9 |
| F10 | Frontend/UI surface | ✅ **none touched — no web check required** | §F10 |

## §F1 — targeted runs at `73ea42ce2` (per-file counts)

House invocation, one pack per worker, `timeout 300` command-level guard + pytest's own `--timeout` (the
runner is a leak gate, not a timer — see L-7), 9 per-variable env scrubs, `git status --porcelain` recorded
before/after every run (**identical** in all cases — 4 pre-existing tester-artifact lines, no new dirt):

| Pack (PACKS.md row) | Collected | Passed | Failed | Runtime | Note |
|---|---|---|---|---|---|
| `tests/unit/test_nest_report_rest_route.py` (`nest_report_rest_route_unit_test`) | 22 | 19 | **3** | 5.82 s | all 3 = QUARANTINE row 11 `TestAM1TheRouteSetPin` (`/metrics/capture-lane` + metrics-router diff vs `22c144000`) — **pre-existing, 0 net-new**; file unchanged by cycle 2 |
| `tests/unit/test_anomaly_report_tool_registration.py` | 56 | 56 | 0 | 10.15 s | node count unchanged vs cycle 1 |
| `tests/unit/test_anomaly_report_description_roundtrip.py` | 3 | 3 | 0 | 17.87 s | file MODIFIED (±6 lines) by cycle 2; node count unchanged |
| `tests/unit/test_instance_ops_behaviour.py` | 58 | 58 | 0 | 122.12 s | matches the phase-0 base census (58 passed); +13 s vs the 2026-11-26 solo run — still 2.5× under the guard |
| `tests/unit/test_anomaly_report_scrub.py` | **39** | 39 | 0 | 15.40 s | **+8 vs cycle 1** = the cycle-2 `TestIteration2Pins` class |
| `tests/unit/tools/test_module_level_inputs_b.py` | 17 | 17 | 0 | 0.55 s | `test_second_build_payloads_match_base_fixture` PASS; fixture + test file UNCHANGED by cycle 2 |
| `tests/unit/tools/test_module_level_inputs_{a,c,d}.py` | 71 | 71 | 0 | 0.82/0.54/0.57 s | 43 + 18 + 10 |
| `tests/unit/test_instance_read_shakedown_defects.py::TestNeverStartedRunningDetector` | 22 | 22 | 0 | 38.84 s | all 22 node ids PASSED (T-g) |

**F1 totals**: 288 collected, 285 passed, **3 failed — all 3 quarantined pre-existing**, 0 net-new.
**NEW test files added by fix cycle 2: NONE** (`git diff --name-status --diff-filter=A 0f5af2a5a..HEAD -- tests/`
= empty; the branch's only new file, `test_anomaly_report_description_roundtrip.py`, arrived in cycle 1 and
was already registered).

## §F2 — invariant pins T-a…T-g

All 8 cycle-2 pins live in `tests/unit/test_anomaly_report_scrub.py::TestIteration2Pins` and PASS:

```
tests/unit/test_anomaly_report_scrub.py::TestIteration2Pins::test_t_a_description_straddle_no_fragment_survives PASSED
tests/unit/test_anomaly_report_scrub.py::TestIteration2Pins::test_t_c_no_redeemable_handle_or_bisected_token PASSED
tests/unit/test_anomaly_report_scrub.py::TestIteration2Pins::test_t_d_honest_accounting PASSED
tests/unit/test_anomaly_report_scrub.py::TestIteration2Pins::test_t_b_excerpt_straddle_no_fragment PASSED
tests/unit/test_anomaly_report_scrub.py::TestIteration2Pins::test_t_e_excerpt_store_unavailable_withheld PASSED
tests/unit/test_anomaly_report_scrub.py::TestIteration2Pins::test_gap2_description_store_unavailable_placeholder PASSED
tests/unit/test_anomaly_report_scrub.py::TestIteration2Pins::test_inv2_scrub_exception_path_withheld PASSED
tests/unit/test_anomaly_report_scrub.py::TestIteration2Pins::test_inv4_withheld_distinguishable_from_cap_cut PASSED
============================== 8 passed in 7.27s ===============================
```

Whole file: `39 passed in 15.07s`.

| Pin | Node / line | Key assertion (verbatim) | INV | Verdict |
|---|---|---|---|---|
| **T-a** | `:882` | `content = "x" * 45 + secret` (62 chars, cap 60 — the secret straddles the boundary); `for i in range(len(secret) - 7): gram = secret[i:i+8]; assert gram not in out`; control `assert _re.search(r"__ENS_REDACTED__[0-9a-f]{12}__", out2) is not None` | INV-1/2 (description) | ✅ discriminates (mutation leg i) |
| **T-b** | `:946` | `msg_content = "ERROR: " + "x" * 483 + secret` (507 > the 500-char per-message cap); 8-grams absent from `row.excerpt` | INV-1/2 (excerpt) | ✅ discriminates |
| **T-c** | `:900` | `assert "[TRUNCATED]" in out`; every `__ENS_REDACTED` prefix must `_re.match(r"__ENS_REDACTED__[0-9a-f]{12}__", tail)`; `assert _re.search(r"__ENS_SECRET__[0-9a-f-]{36}__", out) is None` | INV-3 | ⚠️ **PASSES but does not DISCRIMINATE the token swap — F-C1** (§F3 leg ii) |
| **T-d** | `:921` | `assert "original 300 chars" in marker or "pre-scrub 300 chars" in marker`; `assert "post-redaction" in marker`; `assert "original 9000" not in marker`; normal case `"original 500 chars"` | INV-4 | ✅ honest accounting (PRE-scrub length labelled "original"; expansion reason stated) |
| **T-e** | `:1001` | `monkeypatch.setattr(anomaly_service, "get_process_scrubber", lambda: _BrokenScrubber())` (scrubber PRESENT, store unavailable) ⇒ `assert row.excerpt is None` | INV-5 | ✅ discriminates (mutation leg iii) |
| gap2 | `:1055` | `out, _ = scrub_text("hello world", 100, mark_truncated=False)` ⇒ `assert out == SCRUB_UNAVAILABLE_PLACEHOLDER` | INV-5 | ✅ |
| inv2 | `:1067` | `_ToctouScrubber` (effective at probe, raises mid-scrub) ⇒ `assert out == SCRUB_UNAVAILABLE_PLACEHOLDER`; `assert raw not in out`; `assert truncated is True` | INV-2 | ✅ |
| inv4 | `:1086` | cap-cut ⇒ `"[TRUNCATED" in out_cut`; withheld ⇒ `out_withheld == SCRUB_UNAVAILABLE_PLACEHOLDER`; `out_withheld != out_cut` | INV-4/5 | ✅ |
| **T-f** | families | anomaly trio 3/3 + 39/39 + 56/56 + detector 22/22 + ops 58/58 + module-level 88/88 — all green; the 3 reds are the quarantined AM1 family | — | ✅ |
| **T-g** | detector | 22/22, same node ids as cycle 1 | — | ✅ no regression |

**Production map** (for the mutation worker's targeting):

- `daemon/services/anomaly_service.py`
  - `_scrubber_effective` **:716** — presence + effectiveness probe (INV-5); a mock without the probe is treated as effective.
  - `scrub_text` **:753** — pipeline entrypoint; fail-closed withhold at **:816-828**.
  - `_anomaly_exact_scrub` **:651** — exact-match substitution over the **FULL** text (INV-1); raises `_ScrubUnavailableError` on any load/probe failure (INV-2).
  - `_token_safe_cut` **:613** — backs the cut up to a token start so a `__ENS_REDACTED__<12hex>__` is never bisected (INV-3).
  - ORDER in the description path **:823-859**: `_anomaly_exact_scrub(source)` → `redact_secret_shapes(limit=10**9)` → `redact_exc_str(limit=10**9)` → `_token_safe_cut(content_limit)` + loud marker. **No cap/slice runs before the exact-match scrub.**
  - Expansion allowance **:834-838**: `expansion_case = (original_len <= cap) and (post_scrub_len > cap)`.
  - Token mint **:705-709**: `sha256(seed)[:12]` where `seed = secret_id if secret_id else "redacted"` — **the secret ID, never the plaintext** (L-1).
  - `_harvest_excerpt` **:2182**, gate at **:2233-2240**: `if not _scrubber_effective(scrubber): … return` ⇒ excerpt stays **None** + a WARNING.
- `daemon/services/secret_persistence_scrub.py` — `get_process_scrubber` :263 · `is_effective` :345 · `_current_rows` :415 · `_plaintext_pairs` :499 (longest-first, marker-shape exclusion).
- Ceilings (`daemon/config.py`): description **8000** · suspected_cause **2000** · excerpt per message **500** · excerpt total **2000**.

## §F3 — MUTATION PROOFS (red → green) — 2 of 3 named pins discriminate

Venue: an isolated **detached** scratch worktree at the tip (`git worktree add --detach "$SCRATCH" 73ea42ce2`)
with its own venv (`uv venv --python 3.13 && uv sync --frozen`; `uv.lock` sha256 identical before/after:
`e08fa6a014377e2b…`); import isolation proven (`daemon.__file__` inside `$SCRATCH`); the lane tree was never
mutated. Baseline GREEN at the scratch: `39 passed in 15.74s`.

| Leg | Mutation | RED run | Named pin | Restore → GREEN |
|---|---|---|---|---|
| **(i)** re-introduce a PRE-SCRUB cap | `source = source[:cap]` inserted immediately before `_anomaly_exact_scrub(source, actor=actor)` in `scrub_text` | `4 failed, 35 passed in 14.03s` — **T-a RED**: `tests/unit/test_anomaly_report_scrub.py:892 … assert gram not in out, f"fragment survived: {gram!r} in {out!r}"` → `AssertionError: fragment survived: 'STRADDLE' in 'xxxx…STRADDLESECRET1'` (+ T-b and both limit-after-redaction pins) | ✅ **discriminates** | sha256 equal to baseline → `39 passed in 14.41s` |
| **(ii)** token swapped back to a redeemable one | `token = _mint(secret_id)` (the module's own `mint_marker` ⇒ `__ENS_SECRET__<uuid4>__`); second attempt the literal `"__ENS_SECRET__" + str(uuid.uuid4()) + "__"` — **identical red set** | `2 failed, 37 passed in 8.48s` — sibling pins RED: `test_scrubbed_longer_than_cap_while_original_le_cap_marker_present:575` (`assert "__ENS_REDACTED__" in out` → `stored-secret substitution must fire`) and `test_t_a_description_straddle_no_fragment_survives:896` (`assert _re.search(r"__ENS_REDACTED__[0-9a-f]{12}__", out2) is not None`); **the named pin `T-c` stayed GREEN** | ❌ **vacuous — F-C1** | sha256 equal to baseline → `39 passed in 14.44s` |
| **(iii)** excerpt gate made PRESENCE-ONLY | `if not _scrubber_effective(scrubber):` → `if scrubber is None:` in `_harvest_excerpt` | `1 failed, 38 passed in 15.02s` — **T-e RED**: `tests/unit/test_anomaly_report_scrub.py:1051 … assert row.excerpt is None` → `AssertionError: assert 'ERROR: boom' is None` (the excerpt persisted **unscrubbed** — exactly the loss the gate exists to prevent) | ✅ **discriminates** | sha256 equal to baseline → `39 passed in 15.75s` |

Teardown: `worktree remove --force` + `prune` → `git worktree list | grep -c ns-c2-mut` = **0**; no `/tmp/ns-c2-mut-*` residue.
Lane `git status --porcelain` **identical before and after** (4 pre-existing tester-artifact lines), HEAD still `73ea42ce2`.

### F-C1 (🟠 TEST-side, NOT a product defect) — `T-c` cannot detect a redeemable-token regression

`test_t_c_no_redeemable_handle_or_bisected_token` uses `content_limit = 30`, while the legacy redeemable
marker `__ENS_SECRET__<uuid4>__` is **52 chars** ⇒ under that geometry the swapped token is **bisected** to
`xxxxx__ENS_SECRET__` before the assertion runs. The pin's full-token regex
(`__ENS_SECRET__[0-9a-f-]{36}__`) cannot match a bisected token, and its bisect guard scans
`__ENS_REDACTED` **only** ⇒ the partial legacy handle slips through the pin.

- **Product impact: NONE.** The product keeps minting the non-resolvable D1 token (§F4), the sibling pins
  catch the regression, and the real render shows the correct token (§F6).
- **Coverage impact: real.** INV-3's "no redeemable **or partial** handle" clause is pinned against
  `__ENS_REDACTED` partials but **not** against `__ENS_SECRET` partials. A future change that re-introduced
  a legacy marker in a geometry where it survives whole would be caught by T-c; one where it is bisected
  would not.
- **Suggested fix (test-side, < 20 lines, NOT applied here — this is a verification lane and the evidence
  must stay intact):** widen T-c's bisect/substring guard to scan `__ENS_SECRET`, `ENS_SECRET__` and
  `ENS_SECRET` as well as `__ENS_REDACTED` (or raise the fixture's `content_limit` to ≥ 52 so the forbidden
  token can survive whole). Filed by the mutation worker as nest anomaly `765be3c0`.

## §F4 — D1 token semantics (`__ENS_REDACTED__<sha256[:12]>__`)

Static (`daemon/services/secret_service.py:151-174`):

```
ENS_SECRET_MARKER_PREFIX = "__ENS_SECRET__"
ENS_SECRET_MARKER_SUFFIX = "__"
ENS_SECRET_MARKER_RE     = ^__ENS_SECRET__([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})__$
anomaly REDACTED_PREFIX  = __ENS_REDACTED__
anomaly REDACTED_RE      = __ENS_REDACTED__[0-9a-f]{12}__
```

Repo-wide `__ENS_REDACTED__` census — **10 source hits, ZERO parsers/resolvers**: 1 constant
(`anomaly_service.py:276`), 1 regex (`:278-280`, used at exactly ONE site — the cut-boundary guard
`_token_safe_cut` `:625`), 2 docstrings, 1 comment, 5 test assertions. `secret_persistence_scrub.py` contains
**no** `__ENS_REDACTED__` hit. The redemption surface funnels through
`secret_resolver.resolve_marker` / `resolve_secret_markers`; the DB layer `SecretService.resolve_marker`
(`:1854`) has exactly one production caller (`secret_resolver.py:267`); `daemon/tools/bash.py:278-281` is a
transcript leak-guard, not a redemption path.

Fenced probe (synthetic value `SYNTH-PROBE-VALUE-1234567890`, scratch SQLite, fake HOME, no DB contact):

| Entry point | D1 token `__ENS_REDACTED__f8b27df7be69__` | Legacy `__ENS_SECRET__96447a25-…__` (control) |
|---|---|---|
| marker regex `findall` | `[]` | `['96447a25-…']` |
| marker regex `fullmatch` | `None` | match (span 0-52) |
| `parse_marker` | `None` | `'96447a25-…'` |
| `is_secret_marker` | `False` | `True` |
| `secret_resolver.resolve_marker` | returned **unchanged** (byte-identical) | resolved to the SYNTH plaintext (sha12 `f8b27df7be69`, equality True) |
| `resolve_secret_markers({'K': …})` | `{'K': '__ENS_REDACTED__f8b27df7be69__'}` (unchanged) | `{'K': 'SYNTH-PROBE-VALUE-1234567890'}` |
| `SecretService.resolve_marker(secret_id=d1)` | raises `SecretMarkerResolutionError: secret_not_found` | resolved to the SYNTH plaintext |
| `mint_marker(d1)` | raises `ValueError: secret_id must be a UUID4 string` | — |

⇒ **D1 token: non-matchable by the parser AND non-redeemable through every public entry point**; the
synthetic plaintext is NOT recoverable from it (no returned value ever equals it). The negative control
proves the probe path is live.

**Disclosed divergence from the commission's wording:** production seeds the token with
`sha256(secret_id)[:12]` — the secret's **UUID** — not `sha256(plaintext)[:12]` (`anomaly_service.py:705-709`).
This is deliberate (a plaintext digest would be an offline verification oracle for low-entropy secrets —
L-1). Both forms were probed; both are shape-driven and non-resolvable.

## §F5 — STRICT READ-ONLY PROOF

**Session fence (role-level, stronger than per-connection):** `ALTER ROLE "nsc2_ro_…" SET default_transaction_read_only = on`
+ `engine.dispose()`. Every one of the 24 physical connections printed its FIRST statement as
`SHOW default_transaction_read_only -> 'on'`; the provisioning (writable) connection printed `off` and is
named as such.

**(A) Detector**
- **A1 static**: `daemon/tools/instance_read.py` contains **10 SQL literals, all `SELECT`** (lines 497, 1638, 1709, 2045, 2152, 2200, 2202, 2218, 2578, 3077) — the other 5 keyword hits are prose/comments. **Zero DML/DDL.**
- **A2 run under RO**: the real `_build_health_report` produced a **positive render (`count: 1`)** and all 8 tables (instances, task, checkpoints, job_locks, job_queue_items, job_queues, instance_hierarchy, message_queue) show **count delta 0 AND identical sha256 content fingerprints** before/after.
- **A3 write probe**: `psycopg.errors.ReadOnlySqlTransaction` — **SQLSTATE 25006** — `cannot execute INSERT in a read-only transaction`; the probe row did **not** land (`count = 0`).
- **A4 raising probe** (`_never_started_candidates` → `RuntimeError`): `count: 0`, `evidence_legs: "degraded"`, **ESCAPED EXCEPTION: None**, plus the code's own warning `instance_read: never-started candidate probe failed — the never-started-running detector will NOT flag: …`.

**(B) New scrub path**
- **Static**: `anomaly_service.py` and `secret_persistence_scrub.py` contain **0 SQL literals of any verb** (ORM only). The ONE write on the report path is `AnomalyRepository.insert_cluster` (`:233`) / `insert_report` (`:221`).
- **B1 under RO**: the DB-backed secret store read through the RO session still scrubbed correctly —
  `ERROR: tool failure …; the credential was __ENS_REDACTED__406934af05f3__ and it must not survive`
  (token == `sha256(secret_id)[:12]`), **no plaintext, no redeemable handle**; the `secrets` table and all 8 detector tables: **count delta 0, fingerprints identical**.
- **B2 raising store ⇒ FAIL-CLOSED**: GAP-2 (scrubber present but ineffective) ⇒ authored field = `[REDACTED: secret scrub unavailable; field withheld]`, `truncated = True`, excerpt = **None (withheld)**; INV-2 (store drops MID-scrub, TOCTOU) ⇒ the same placeholder, `truncated = True`, **no plaintext, no escaping exception, no typed refusal**. A positive control first proved the good RO-backed scrubber yields `'ERROR: boom __ENS_REDACTED__35e1c3f3bb2d__'` (excerpt len 42, not None) so a withheld excerpt cannot be confused with a broken transcript read.
- **B3** `accept_report` under the same RO session ⇒ the report path's first write is rejected: `INSERT INTO anomaly_clusters …` → SQLSTATE **25006**, 0 rows written.

Teardown: `pg_database LIKE 'ens_nsc2_ro_%'` = **0**, `pg_roles LIKE 'nsc2_ro_%'` = **0** (re-verified as superuser).

## §F6 — FIXTURE RENDERS (verbatim, synthetic secrets only)

### (a) Detector row — cap visibly exercised (50 of 60), `scope` present

```json
{
  "detector": "never-started-running",
  "instances": [
    {
      "instance_id": "inst-neverstarted-001",
      "agent_id": "worker",
      "status": "running",
      "idle_seconds": 21600,
      "pause_marker": null,
      "detail": "never started: running, zero checkpoint rows, first task 1 still pending with started_at NULL; pending task age 3600s (threshold 2700s), instance age 21600s; holds 1 job lock(s) [lock-ns-001] on queue(s) [default], 50 job(s) queued behind the held queue(s) (capped sample — 60 queued in total)",
      "age_minutes": 360.0,
      "pending_task_id": "1",
      "pending_task_age_seconds": 3600.068112,
      "pending_task_age_minutes": 60.0,
      "pending_task_is_background": false,
      "pending_task_is_deferred": false,
      "pending_task_type": "PROCESS_MESSAGE",
      "held_job_locks": [
        {
          "lock_id": "lock-ns-001",
          "queue_id": "q-default-ns",
          "queue_name": "default",
          "job_id": "job-ns-001",
          "project_id": "proj-ns",
          "agent_id": "worker",
          "acquired_at": "2026-11-27T01:41:58.571000+00:00",
          "lock_slot": 0
        }
      ],
      "queued_jobs_behind_queue": [
        { "job_id": "queued-job-60", "queue_id": "q-default-ns", "queue_name": "default", "agent_id": "worker", "admission_state": "queued", "created_at": "2026-11-27T01:45:58.571000+00:00", "age_seconds": 60 },
        { "job_id": "queued-job-59", "queue_id": "q-default-ns", "queue_name": "default", "agent_id": "worker", "admission_state": "queued", "created_at": "2026-11-27T01:45:59.571000+00:00", "age_seconds": 59 },
        "… 47 more entries (queued-job-58 … queued-job-12, ages 58..12) …",
        { "job_id": "queued-job-11", "queue_id": "q-default-ns", "queue_name": "default", "agent_id": "worker", "admission_state": "queued", "created_at": "2026-11-27T01:46:47.571000+00:00", "age_seconds": 11 }
      ],
      "queued_jobs_total": 60,
      "queued_jobs_capped": true,
      "context_state": "ok",
      "context_degraded": false
    }
  ],
  "count": 1,
  "evidence_legs": "ok",
  "scope": "complete",
  "disagreement_pin": null
}
```

Envelope: `scan_truncated: false` · `scan_stop_reason: "end_of_set"` · `evidence_legs: "ok"`.
`len(queued_jobs_behind_queue)` = **50 exactly**, `queued_jobs_total` = **60**, `queued_jobs_capped` = **true**.
**Negative control** (one checkpoint row added → re-run): `{"detector": "never-started-running", "instances": [], "count": 0, "evidence_legs": "ok", "scope": "complete", "disagreement_pin": null}`.

### (b) Anomaly row AFTER the scrub — D1 token, loud marker with the PRE-scrub length, no plaintext

Registered synthetic secret: value `SYNTH-SECRET-FRAGMENT-9931` (stored secret id `6b911ca4-5079-4393-930e-b30ee047b489`); `pre_scrub_len` = **7999**.

Stored description — HEAD (300): `'a' * 300`; TAIL (300): `'b' * (300 - len(marker)) + '\n[TRUNCATED: original 7999 chars; content cut after redaction: pre-scrub 7999 chars, post-redaction 8003 chars; stored prefix only]'`; `len(stored)` = **8000**; `suspected_cause` = None; `excerpt` = None; `excerpt_sha256` = None; `excerpt_length` = None; `category` = `tool-failure`; `category_raw` = `tool-failure`; `outcome` = `accepted`; result `truncated = True`.

| check | value |
|---|---|
| `synthetic_plaintext_in_stored` | **False** |
| `secret[:6] in stored` | **False** |
| `secret[-6:] in stored` | **False** |
| `'__ENS_SECRET__' in stored` | **False** |
| `sha256(plaintext)[:12]` token in stored (the commission's literal) | **False** — production seeds from the secret ID |
| `__ENS_REDACTED__f3b384aeffb9__` (== `sha256(secret_id)[:12]`) in stored | **True** |
| `'[TRUNCATED: original ' + str(pre_scrub_len)` in stored | **True** |
| `len(stored) == 8000` | **True** |

**Second row** (3000 chars + the secret, fits after scrubbing): `truncated = False`; `len(stored) = 3004` (token substitution expansion, no cut); token present; plaintext absent; `'TRUNCATED' in stored2` = **False**; `secret[:6] in stored2` = **False**.

⇒ INV-4 confirmed end-to-end: the payload is bounded by the documented ceiling (8000), the cut is LOUD and
carries the **PRE-scrub** length labelled "original" plus the expansion reason ("content cut after
redaction: pre-scrub 7999 chars, post-redaction 8003 chars"), and the redaction expansion has an explicit
allowance.

## §F7 — LIVE read-only render vs `ensemble_prod` — **NOT EXECUTED (NO RO PATH)**

The live leg could **not** be executed at this tip. The read-only path to `ensemble_prod` as
`ensemble_prod_ro` no longer exists, and per the fence (and the cycle-1 §D9 precedent — "no privileged path
used") **no privileged or writable fallback was attempted**.

**Attempt (verbatim):**

```
1a — unix socket /var/run/postgresql:
psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed:
        FATAL:  Peer authentication failed for user "ensemble_prod_ro"          (exit 2)

1b — 127.0.0.1:5432:
psql: error: connection to server at "127.0.0.1", port 5432 failed:
        fe_sendauth: no password supplied                                          (exit 2)
```

**Credential-source facts (the evidence for "no RO path"):**

| Source | State |
|---|---|
| `~/.pgpass` | **does not exist** |
| dev `db_connections` registry (`ensemble_dev`) | **EMPTY (0 rows)** — the `ensemble_prod_ro` row that cycle 1 (2026-11-26 §D9) Fernet-decrypted is **gone** |
| dev `secrets` store (`ensemble_dev`) | **EMPTY (0 rows)** |
| `/tmp/fetch_ro.py` (the cycle-1 helper) | **gone** (aggressive cleanup) |
| any 5-field pgpass line naming `ensemble_prod_ro` under `/home/nea` or `/tmp` | **none** |
| env var carrying the RO password | **none** (only `ENSEMBLE_SELF_ENV` + `SYSTEM_ENCRYPTION_KEY`) |
| local-socket peer/trust auth | **refused** (OS user mismatch) |
| shared context / POK | **no documented credential path** |

**Explicitly NOT used** (forbidden for this leg): the local `postgres` superuser (`sudo -n -u postgres psql`),
any `ALTER ROLE` / password reset on prod, any writable role, any write to prod.

**What this does and does not change.** The detector's read path is **UNCHANGED by cycle 2** —

```
git diff --stat 0f5af2a5a..HEAD -- daemon/tools/instance_read.py     → EMPTY
```

cycle 2 touched only `daemon/services/anomaly_service.py`, `daemon/services/secret_persistence_scrub.py`,
`CHANGELOG.md` and two test files. Therefore the cycle-1 live render still describes this tip's detector
behaviour, with its own caveat intact:

```json
{"detector": "never-started-running", "instances": [], "count": 0, "evidence_legs": "degraded", "scope": "complete", "disagreement_pin": null}
```

— 0 flagged, a **LOWER BOUND** (the `ensemble_prod_ro` role lacks SELECT on `checkpoints`, so the detector
deliberately refuses to flag what it cannot verify). The live leg is therefore **carried forward from cycle 1,
not re-verified at this tip**. The live-facing behaviour of the cycle-2 changes (the scrub path) is unrelated
to the detector and was verified end-to-end on a real PostgreSQL scratch DB in §F5/§F6.

**Teardown**: scratch dir `/tmp/f7-ro-path.wkDRQAU2` removed (no 0600 credential file remains); lane porcelain
unchanged; HEAD unchanged.

## §F8 — BOUNDED WIDER REGRESSION (HEAD vs BASE) — 0 net-new

**SET (mechanical)**: cycle-2-touched ∪ whole-branch-touched ∪ every `tests/unit` file referencing
`anomaly_service` / `secret_persistence_scrub` / `AnomalyService` / `report_anomaly` ∪ the anomaly family ⇒
**28 files / 689 nodes** at HEAD; **27 files / 650 nodes** at BASE (`test_anomaly_report_description_roundtrip.py`
does not exist at `4a17398d2` — dropped and disclosed).

BASE leg = a **REAL detached worktree** at `4a17398d2` with its own venv (`uv venv --python 3.13 && uv sync --frozen`;
`uv.lock` sha256 identical before/after) and proven import isolation (`daemon.__file__` inside the base tree) — a
`git archive` extraction was NOT used (it makes git-shelling asserts vacuous and makes the freeze pin SKIP).

| Leg | Nodes | Result | Wall |
|---|---|---|---|
| HEAD `73ea42ce2` | 689 | `12 failed, 677 passed in 101.27s` | 107 s |
| BASE `4a17398d2` | 650 | `12 failed, 638 passed in 86.57s` | 93 s |

Identical command shape on both legs (`-n 8` after the serial projection came to ~606 s > the 280 s budget).
**xdist masking check PASSED on both legs**: HEAD banner `8 workers [689 items]`, summed outcome-characters
= **689** = collected, gw-failure-sum **12** = failed; BASE outcome-characters **650** = collected, gw-sum **12**.

**Red-set diff: HEAD-only ∅ · BASE-only ∅ · both = 12 ⇒ 0 NET-NEW.**

| # | Red | Family | Classification |
|---|---|---|---|
| 1–4 | `test_instance_read_shakedown_defects.py::TestF12StatusForwardSeam` ×4 | `_RecordingRepo` double lacks `list_child_ids_for_parents` | **pre-existing on base** (identical on both legs) |
| 5–7 | `test_nest_report_rest_route.py::TestAM1TheRouteSetPin` ×3 | QUARANTINE row 11 — metrics door drifted vs the `22c144000` freeze pin | **pre-existing on base** (quarantined) |
| 8 | `test_anomaly_report_duty_prompts.py::TestTheSealRePin::test_every_sealed_row_matches_the_real_bytes` | stale seal baselines | **pre-existing on base** |
| 9–11 | `test_anomaly_report_grant_pins.py` ×3 | grant-pins family | **pre-existing on base** |
| 12 | `test_anomaly_report_models.py::TestTheMigrationCensus::test_the_census_is_121_files_with_121_distinct_prefixes` | migration-census pin | **pre-existing on base** |

Introduced: **none** ⇒ no 3× flaky re-run was required (nothing to separate).

### The "V3 family / 9 failed, 412 passed" figure — NOT REPRODUCIBLE

- **Census**: the anomaly family is **222** nodes; the secret family (`test_secret_service` 84,
  `test_secret_resolver` 40, `test_secret_persistence_scrub` 39, `test_log_secret_static_audit` 14,
  `test_secret_do_not_touch_lane_static` 12, `test_mcp_secret_spawn` 10, `test_auth_secret_hygiene` 8) is
  **207**. 222 + 199 = 421 ⇒ exactly ONE subset of the secret family sums to 199 (all minus
  `test_auth_secret_hygiene`).
- **Run of that census-matched 421-node set**: HEAD **`8 failed, 413 passed in 40.28s`** (masking check: outcome-chars 421, gw-sum 8 ✓); BASE (404 nodes — the roundtrip file is absent there) `8 failed, 396 passed in 35.19s`; red diff **HEAD-only ∅ · BASE-only ∅ · both = 8** — the same 8 stale-pin reds (§F8 table minus the 4 F12 nodes, which live outside both families).
- **Conclusion**: at this tip the deterministic truth at the 421-node census-matched combination is
  **8 failed / 413 passed**, not 9/412. A 9-failure observation would require a different commit tip
  (the branch moved: scrub 25→39 nodes, shakedown 86→108) or a file outside both families — neither is
  inside this tip's evidence. The figure also carries the **incomplete-shard signature** the cycle-1 record
  already flagged for a sibling "2408 passed" string (a shard can print a valid-looking summary while nodes
  are lost) ⇒ the figure is **not a citable census**.

## §F9 — REGISTRY (`.agents/tester/PACKS.md`)

- **No new test file in cycle 2** ⇒ no new pack row is required; the branch's only new file was registered in cycle 1.
- **NEW block prepended** (house newest-first convention, markers `never-started-detector-cycle2-final-20261127-begin/end`): 9 rows — `anomaly_report_scrub` (39) · `anomaly_report_description_roundtrip` (3) · `anomaly_report_tool_registration` (56) · `never_started_detector_unit_test` — CLASS leg (22) · `nest_report_rest_route_unit_test` (22) · `module_level_inputs_unit_test` (88) · `instance_ops_behaviour_unit_test` (58) · **NEW** `never_started_cycle2_blast_radius_leg` (28 files / 689 nodes) · **NEW** `anomaly_scrub_mutation_proof` (the three-leg scratch-worktree venue). The block preamble records the cycle-2 commit list, the file census, the node-count method, the fence and the machine facts (`.venv` is not in the repo; `uv sync --frozen`).
- **2 stale canonical cells REFRESHED in place** (the cycle-1 convention): `anomaly_report_scrub` — 31 nodes / 31 passed @ `0f5af2a5a` → **39 nodes / 39 passed @ `73ea42ce2`** (+ the 8-pin scope text + the F-C1 caveat); `anomaly_report_tool_registration` — run stamp advanced to `73ea42ce2` (node count unchanged at 56; the file is untouched by cycle 2).
- **Integrity**: `diff` against a pre-edit backup shows **only** the 25 new block lines and the 2 refreshed rows — the other 5 752 lines are byte-identical. `QUARANTINE.md` needs no change (row 11 still matches the 3 AM1 reds).
- **New LESSONS**: `.agents/tester/LESSONS/2026-11-27-cycle2-anomaly-scrub-invariants-and-pin-geometry.md` — 11 durable lessons (L-1 token seed is the secret ID, not the plaintext; L-2 a cap-smaller-than-the-token geometry makes a full-pattern assertion vacuous; L-3 the xdist masking check; L-4 seed AFTER `InstanceManager` builds; L-5 the first report-path write is the CLUSTER row; L-6 role-level read-only; L-7 the house runner is a leak gate, not a timer; L-8 `env -u POSTGRES_*` is a no-op; L-9 frozen-fixture refresh legitimacy; L-10 the anchored fullmatch-only marker regex; L-11 the harness `/tmp` namespace split).

## §F10 — FRONTEND / UI SURFACE

`git diff --name-only 4a17398d2..HEAD -- frontend/` → **empty**; `git diff --name-only 0f5af2a5a..HEAD -- frontend/` → **empty**.
The only file outside `daemon/` / `tests/` / `.agents/` / `scripts/` is `CHANGELOG.md`. **No frontend/UI surface is touched ⇒ a focused web check is NOT required.**

## Findings register

| ID | Sev | Finding | Location | Action |
|---|---|---|---|---|
| **F-C1** | 🟠 important (TEST-side) | `test_t_c_no_redeemable_handle_or_bisected_token` does NOT discriminate a redeemable-token regression: `content_limit=30` bisects the 52-char `__ENS_SECRET__<uuid4>__` and the bisect guard scans `__ENS_REDACTED` only ⇒ the pin stays GREEN under the swap. **Product impact: none** (the fix is load-bearing — two sibling pins go RED; the product keeps minting the non-resolvable D1 token). **Coverage impact: INV-3's "no partial handle" clause is unpinned against the legacy marker.** | `tests/unit/test_anomaly_report_scrub.py:900` (class `TestIteration2Pins`) | Suggested test-side fix (< 20 lines): widen the bisect/substring guard to `__ENS_SECRET` / `ENS_SECRET__` / `ENS_SECRET`, or raise the fixture's `content_limit` ≥ 52. Nest anomaly `765be3c0`. Not applied here (verification lane — the evidence must stay intact). |
| **F-C2** | ℹ️ info | The commission's token formula (`sha256(plaintext)[:12]`) does not match production: the seed is `sha256(secret_id)[:12]` (`anomaly_service.py:705-709`) — deliberate (no offline oracle for low-entropy secrets). Any future pin that asserts the plaintext-derived token will fail against correct code. | `daemon/services/anomaly_service.py:705-709` | Documented (L-1). No code change. |
| **F-C3** | ℹ️ info | The "9 failed / 412 passed" V3 figure is not reproducible at this tip — the census-matched 421-node set yields **8 failed / 413 passed**, with identical red sets on HEAD and BASE. | — | Do not cite the figure as a census; use §F8's numbers. |
| **F-C4** | ℹ️ info | `test_instance_ops_behaviour.py` runtime drifted 108.8 s → 122.12 s (+13 s) between cycles — still 2.5× under the 300 s guard. | PACKS.md row | Watch the trend; split the pack if it approaches 300 s. |
| **F-C5** | ℹ️ info | T-a/T-b use a SMALL cap (60 / 500) to place the straddling secret, not the historical 1000. The straddle property is scale-invariant and the mutation proves the pin discriminates the ordering, but the literal "old cap" boundary is not exercised. | `tests/unit/test_anomaly_report_scrub.py:882`, `:946` | Acceptable; disclose only. |
| **F-C6** | ℹ️ info | `_scrubber_effective` (`anomaly_service.py:716`) treats a scrubber object that lacks the `is_effective` probe as EFFECTIVE (a test-affordance). The process scrubber always implements the probe in production. | `daemon/services/anomaly_service.py:716` | Informational; no action in this lane. |
| **F-C7** | 🟠 important (ENVIRONMENT) | **The dev credential registry is EMPTY** (`db_connections` 0 rows, `secrets` 0 rows in `ensemble_dev`), the cycle-1 RO helper `/tmp/fetch_ro.py` is gone, and local-socket peer auth refuses `ensemble_prod_ro` ⇒ **there is currently NO read-only path to `ensemble_prod`**, so every lane's live-prod verification leg (not just this one) is blocked until the `ensemble_prod_ro` row is re-provisioned. This cycle used no privileged path and no writable fallback, by design. | dev `ensemble_dev` DB · `/tmp` | Operator/DB lane: re-provision the `ensemble_prod_ro` credential row (or document a peer/trust path) so live RO verification is possible again. Until then the live leg is carried forward from cycle 1. |

## Side effects of this verification

- **No product code modified; no commit to any product path; nothing pushed; no release cut; no daemon booted; ports 8079/8088 untouched.** The lane tree's only modified files are tester-owned (`.agents/tester/PACKS.md`, plus the pre-existing `.agents/tester/QUARANTINE.md` modification and the two pre-existing untracked artifacts, all folded into the RESULTS commit).
- Scratch artefacts removed and verified: the mutation worktree (`ns-c2-mut-*`, `worktree list | grep -c` = 0) and the BASE worktree (`ns-c2-base-*`, count = 0); scratch PostgreSQL databases/roles for the `nsc2_ro_` and `nsc2_fx_` prefixes — all dropped, `pg_database`/`pg_roles` counts re-verified as 0.
- **LIVE DB: READ-ONLY.** No write to `ensemble_prod`; no write probe on prod; the RO credential (if a file was needed) is shredded; no DSN, password or key was ever printed.
- **Never touched**: `/home/nea/ensemble-src` (detached `bf4bfde6e`), other lanes' branches and worktrees, `agents/ari/tools_note.md`.

## Overall status

- **Defect (1) `never-started-running` detector — ✅ PASS.** 22/22 class green; C-1's TASK clock structurally honoured; read-only proven 4 ways (static DML inventory, 8-table fingerprint delta 0 under a role-level RO session, the 25006 write probe, and the raising-probe fail-closed leg); the cap (50 of 60) and `scope` exercised on a real render with a negative control. **Live leg NOT re-verified at this tip (§F7 — no RO path; the detector read path is unchanged since cycle 1, whose live render (0 flagged, lower bound) still describes it).**
- **Defect (2) `report_anomaly` scrub-path corrections — ✅ PASS on product behaviour.** INV-1 (whole-text scrub before any cut) ✅ mutation-proven; INV-2 (no plaintext fragment; TOCTOU ⇒ placeholder) ✅; INV-3 (non-resolvable D1 token; no partial token) ✅ on the product — with **F-C1** as the single disclosed TEST-side hole; INV-4 (documented ceiling, loud cut with the PRE-scrub length + reason, explicit redaction-expansion allowance) ✅ end-to-end on a real stored row; INV-5 (excerpt withheld when the scrub cannot be trusted) ✅ mutation-proven and live-proven.
- **Lane regression — ✅ PASS: 0 net-new** (28 files / 689 nodes HEAD vs 27 / 650 BASE; red sets identical, 12 pre-existing).
- **Registry — ✅ DONE** (new cycle-2 block + 2 refreshed cells + a new LESSONS file).
- **ensure.md Tier-1 gate — ✅ scoped dual-side leg executed** (mechanical set, identical command shape, xdist masking check on both legs).
- **Testing complete: ✅ READY to merge** — with **F-C1** disclosed (a test-coverage gap, ~1 line of test change, for the owning lane), **F-C3** (the 9/412 figure must not be cited), and the standing non-blocking items from cycle 1 (the `agents/ari/tools_note.md` "seven detector classes" prompt-artifact drift — explicitly out of this branch's remit — and the optional §2 budget-visibility hardening).

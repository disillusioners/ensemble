# TEST REPORT: agent-secrets P1 — INDEPENDENT VERIFICATION

Date: 2026-10-17
Commission surface: agent-secrets P1 (plan `.agents/shared/planning/agent-secrets/plan-overview.md` @ f11965039)
Worktree: `/home/ensemble-worktrees/agent-secrets-p1` (branch `agent-secrets-p1`)
Target HEAD: `0181f689b` (21 commits over base `fae1af3fb`, tree clean at start) — branch advanced during verification with **2 test-only commits authored by this verification** (`43a1a4528`, `f2b1e0537`); final classification anchor `f2b1e0537`. `daemon` import verified resolving INSIDE the worktree before any run.
Workers: 16 instances (1 recon, 11 pack, 3 probe, 1 quality-map after 1 stall re-dispatch). 0 pack timeouts. 0 re-runs needed on packs.

---

## VERDICT: 🟢 PASS-with-preexisting

- **0 commission-caused failures** across 12 pack runs (6 commission + 6 sibling/ensure) and 4 invariant probes.
- **2 deterministic pre-existing base failures** (instance-tools pack, `agents/planner/meta.json` subtree opt-in drift — byte-identical base↔HEAD on both test file and meta; NOT secrets-related) → ledgered in QUARANTINE.md this date.
- Reviewer gaps 4a/4b **confirmed missing then closed durably** (test-only commits `43a1a4528` + `f2b1e0537`).
- Reviewer seam-mirror flag on the 4 boot pins **confirmed** (all 4 are mirrors, quoted evidence) and **closed durably** via real-manager-boot no-key pin (`f2b1e0537`).

### Scope Decision
Commission-defined scope executed in full (commission suite + sibling sweep + 4 invariant probes + 2 PG gaps + test-quality assessment). ensure.md Release Gate NOT run — feature-branch verification, not a release promote; no contradiction notices (all validations ran as scoped packs with dual-layer timeouts).

---

## 1. Commission suite (independent reproduction; dev claimed 141 pass)

| Pack | Files | Result | Counts | Runtime |
|---|---|---|---|---|
| A secrets-service unit | tests/unit/test_secret_service.py | ✅ PASS | 57/57 (incl. 4 boot pins) | 17s |
| B registration family unit | tests/unit/tools/test_secrets_registration.py + test_upgrade_registration.py + test_attestation_registration.py | ✅ PASS | 71/71 (26+21+24) | 27s |
| C migration parity | tests/migration/test_secrets_index_parity.py | ✅ PASS | 5/5 | 0.31s |
| D PG integration | tests/integration/test_secrets_v1_basic.py | ✅ PASS | 8/8 baseline (7 PG + 1 SQLite) → 10/10 post-gap-pins → 11/11 post-boot-pin | 13.5s → 16.6s → 18.6s |
| E hygiene + static audit | tests/unit/test_auth_secret_hygiene.py + test_log_secret_static_audit.py | ✅ PASS | 22/22 (8+14) | 0.51s |
| G SAME-PR triple-pin (integration legs) | tests/integration/test_maintenancer_spawn_resolves_tools.py + test_service_tool_flag_off_byte_identical.py | ✅ PASS | 22/22 (13+9); septet pin verified on BOTH sites | 6.7s/6.0s |

**Independent total at HEAD: 185 pass, 0 fail** (A+B+C+D-baseline+E+G). Dev's "141" reconciles as a narrower selection of the same corpus — my sweep is a strict superset, entirely green. PG scratch discipline throughout: own throwaway roles/DBs (`ens_t_*`, zero collisions verified), `env -u` scrub of ambient LIVE `POSTGRES_*` (ensemble_prod @10.44.0.2) + fence assertion, hard-abort guard on `ensemble_prod` respected, all roles/DBs dropped and verified.

## 2. Sibling regression sweep

| Pack | Result | Counts | Note |
|---|---|---|---|
| H instance-tools (tests/unit/tools/test_instance_tools.py) | ✅ PASS-with-preexisting | 205/207 | 2 failures = planner subtree opt-in drift, base-attributed deterministic (see §6); secrets registration surface: 0 failures |
| I tool-registry discovery (frozen-name + dynamic toolset) | ✅ PASS | 18/18 | behavior alone can't prove secrets enumeration — closed via quality-map D3 |
| J KMS-Lite sanity (kms_lite + resolver + roundtrip + attach_env_ref) | ✅ PASS | 90/90 | §5.4 do-not-touch contract holds; distinct marker namespaces exercised |
| K context-injection (FULL FILE — sibling-slice discipline) | ✅ PASS | 90/90 | gotcha test `test_always_include_highest_match_even_at_zero` PASSED in full-file context (as documented; no misreport) |
| L concurrency_atomic (registered pack; ensure.md Core critical) | ✅ PASS | 99P/0F/74S, 74s | exact PACKS.md baseline match; thread-identity/to_thread surface green — new SecretService DB helpers introduced no sync-on-loop regression |

## 3. Invariant probes (mocks-vs-reality verification)

| Probe | Verdict | Evidence highlights |
|---|---|---|
| a — private-material confinement (REAL service + REAL tool closures, PG scratch) | ✅ PASS | 12 surfaces (5 tool payloads, logs, 4 audit rows, 3 public-column rows): `"PRIVATE KEY"` 0, `"gAAAA"` 0, raw plaintext 0; `token_preview` exactly 4 chars; structural scan 9 candidates ≥24ch, 0 unclassified. Run-1 single flag = probe-self-injected actor string — investigated verbatim, clean re-run, disclosed. |
| b — privileged gating (REAL `create_instance_tools`, full depth) | ✅ PASS | worker/tester/leader/ari → 6/6 tools post-filter; designer/sketcher/critic/charter → **0/6 post-filter with 6/6 pre-filter** (spy proves stripping through real `_apply_tool_filter`/`resolve_tool_filter` seam, not registration omission). Meta diff = exactly 4 files × one-line `"secrets"` addition. `secrets` = PRIVILEGED_TOOL_CATEGORIES entry #7. |
| c — marker format + zero resolution | ✅ PASS | All 3 minted markers fullmatch BOTH commission-frozen `^__ENS_SECRET__[0-9a-f-]{36}__$` AND repo `ENS_SECRET_MARKER_RE` (strictly tighter UUID4) — zero divergence. `grep -rn ENS_SECRET daemon/` = 21 hits, ALL mint/constants/wiring/docstrings; `parse_marker` has zero runtime callers; no substitution path exists (only KMS-Lite's distinct `__KMS_REF__`/`__KMS_ENV__` namespace resolves, untouched). |
| d — boot tolerance, REAL manager seam | ✅ PASS (FULL-BOOT) | Real `InstanceManager` + real PG engine, `SYSTEM_ENCRYPTION_KEY` verified absent: (a) no crash; (b) `_secret_service` wired (real SecretService, manager.py:1268); (c) `is_available=False`; (d) generate raises typed `SecretCipherUnavailable`, fail-closed, class-name-only envelope (M2 verified). Positive control (2nd scratch DB + Fernet key): `is_available=True`, token minted, `full_token_leaked=false`. All 4 dev pins confirmed seam-mirrors (quoted). |

## 4. Reviewer-flagged PG gaps — CLOSED (test-only commits on branch)

Label mapping per the AUTHORITATIVE committed docstrings in `43a1a4528` (F-3 correction — initial report followed the commission prompt's inverse numbering):
- **4a — PG index parity**: grep-confirmed missing (parity file is SQLite-reflection + regex-over-SQL; PG parity only transitive via shared model metadata). ADDED `test_idx_secrets_kind_active_exists_on_pg` — queries `pg_indexes WHERE tablename='secrets'`, asserts index name AND `retired_at IS NULL` partial predicate in `indexdef`. Commit `43a1a4528`.
- **4b — PG atomicity**: grep-confirmed missing (M1 pin was SQLite-only). ADDED `test_store_name_conflict_no_audit_row_on_pg` — duplicate `store` raises `SecretNameConflictError`, asserts NO orphan audit row AND NO orphan secret row (single-transaction rollback on PG). Commit `43a1a4528`.
- **Bonus closure**: `f2b1e0537` adds `TestRealManagerNoKeyBootTolerance::test_real_manager_construction_with_no_key_succeeds` (real `InstanceManager` no-key boot on PG scratch, 5.98s) — closes the seam-mirror flag with a durable pin.

## 5. Test-quality assessment (read-only; worker p1-quality-map-2)

- **D1 edge cases**: token boundaries urlsafe(1/2/3)→raise + urlsafe(4)→6-char OK **COVERED** (:489-502); preview guard ≤4 **COVERED**; double-retire **COVERED** (:745-766, weakly pinned); name collision **COVERED on 3 surfaces** (unit :780-806, PG pin :540-587, atomicity :1569-1613); cipher fail-closed **COVERED ×3** (no-key :270-284, invalid-key :286-294, bad-ciphertext :306-320); marker format freeze **COVERED** (7 pins :156-229 + 3 integration fullmatches); pubkey-only **COVERED** (:648-685 + integration); audit actions create/read_public/retire + `read_value` reserved-unused **COVERED** (:821-884). **MISSING: concurrent-mint uniqueness** (zero threading/gather tests in corpus) — the one commission-listed edge case without a pin.
- **D2 mutation-meaningfulness**: `e1bc445f0` (UNIQUE dual-emission, 5 parity pins) ✅ mutation-meaningful; `4280c8e5a` (NF-1 boundary) ✅ mutation-meaningful (layered double-guard; honest recipe). No decoration-only pins found.
- **D3**: `test_frozen_tool_name_discovery.py` does NOT pin secrets tool names (co-located static-set drift check passes under lockstep renames). The real enumeration pin = `test_secrets_registration.py:82-89` `SECRETS_TOOL_NAMES` + 6 hard-fail pins incl. per-persona resolution.
- **D4 ranked gaps (test debt for P2/backlog, none commission-blocking)**: 🔴 length-cap upper bound untested (256-OK/257-raises unpinned; only 0/-1 pinned); 🔴 no concurrent-mint test; 🟠 double-retire re-stamp/audit-count unpinned; 🟠 migration-parity regex misses table-level UNIQUE refactors; 🟠 secrets-tool enumeration single-file dependency; 🟠 audit temporal ordering unpinned; 🟢 empty-actor unpinned; 🟢 `store(kind="ssh_keypair")` accepts raw PEM via generic path (pubkey-only invariant pinned only on the generate lane — worth a service-boundary rejection); 🟢 retired-row read shape unpinned; 🟢 marker regex oracle imported from production (lockstep loosening undetected — literal-string pin recommended).

## 6. Failures + classification

| Failure | Class | Evidence |
|---|---|---|
| test_instance_tools.py:4423 `test_planner_and_tester_resolve_subtree_messages_via_registry` | **Pre-existing base (deterministic)** | Test file byte-identical base↔HEAD; `agents/planner/meta.json` untouched by commission + byte-identical base↔HEAD; planner allow lacks `subtree_messages`/`subtree_status` (leader/tester have them, pass). Root cause: Phase-2 expectations never applied to planner meta. |
| test_instance_tools.py:5309 `test_leader_planner_tester_opt_in_via_production_seam` | **Pre-existing base (deterministic)** | Same family/cause as above. |

Both ledgered in QUARANTINE.md (new family: "planner subtree opt-in drift ×2", base-attributed). Known base defect (migration 20260714 fresh-SQLite boot break) never triggered — all PG-touching lanes used PG scratch per commission discipline.

## 7. ensure.md status (53-line pack-mapped doc, grep-verified)

- Core Critical: changed-packs PASS ✅ · concurrency_atomic ✅ (99P/0F/74S baseline-matched) · no-sync-DB-on-loop ✅ (same pack) · dev.sh `--timeout-graceful-shutdown 10` ✅ (recon grep :142)
- Core Important: historical async-await callers — outside this change set (scoped out per blast radius)
- Release Gate: not warranted (feature-branch verification, not promote)
- Contradictions: none — all validations executed as scoped packs under dual-layer timeouts

## 8. Commits authored by this verification (test-code ONLY)

| Commit | Content |
|---|---|
| `43a1a4528` | PG gap pins 4a/4b (+88 lines, tests/integration/test_secrets_v1_basic.py) |
| `f2b1e0537` | Real-manager-boot no-key tolerance pin (+110 lines, same file) — closes seam-mirror flag |

Production code: ZERO modifications by verification. Worktree clean of probe artifacts (probe scripts lived in mktemp, deleted; `install-audit.jsonl` kms side-effect restored by probe-b worker).

## 8b. Final pre-merge fix round (2026-10-17 — reviewer items F-1/B-1/B-2/C-1/F-3; test-code + artifacts only)

- **F-1 ✅** — real-manager pin fenced IN-TEST (`TestRealManagerNoKeyBootTolerance`): 5-var `monkeypatch.delenv` cluster (POSTGRES_HOST/PORT/DB/USER/PASSWORD — verified as the COMPLETE engine read-set at `daemon/repositories/factory.py:256-260`; `DATABASE_URL`/`DATABASE_URL_POSTGRES` NOT read by the builder) before `EnsembleConfig` construction, plus load-bearing assert `manager_engine.url.database == pg_engine.url.database`. Fence proof: (a) scrubbed env → PASS 4.53s; (b) INERT poison (127.0.0.1:59999 / poison_guard_db) WITH fence → PASS 4.73s (in-test scrub precedes construction; resolved-db = scratch); (c) same inert poison with fence temporarily disabled → **FAIL** `psycopg.OperationalError: connection to server at "127.0.0.1", port 59999 failed: Connection refused` (4.01s) — fence proven load-bearing; temporary mutation reverted byte-identical (grep `DELIBERATELY DISABLED` = 0, diff MATCH).
- **B-1 ✅** — length-cap boundary pins: service layer `test_api_token_length_above_cap_rejected` (257/512/1024 → `SecretValidationError`; 256 → PASS with preview len 4 + marker fullmatch) + helper mirror `test_generate_api_token_material_rejects_length_above_cap` (ValueError, matches NF-1 pattern). Unit pack 57 → **59**.
- **B-2 ✅** — concurrent-mint race pin: N=8 threads + `threading.Barrier(8)`, same name on scratch PG → exactly 1 success, exactly 7 typed `SecretNameConflictError`, ZERO untyped leaks (PB-F1 envelope holds under race), final state exactly 1 `secrets` row + exactly 1 `create` audit row. PG pack 11 → **12**.
- **C-1 ✅** — 4 tester artifacts committed on branch (this file, PACKS.md, QUARANTINE.md, LESSONS entry) with F-3 §4 relabel + quarantine base-leg reword ("derived from byte-identity, not executed").
- Test-code commit: `542fa73b7` (2 files, +211: `tests/unit/test_secret_service.py` +70, `tests/integration/test_secrets_v1_basic.py` +141).
- Commission-suite total after fix round: **187** (59+71+5+8+22+22); PG file full total 12/12 (20.5s).
- Deferred by reviewer classification (do-not-do): O1-O4, G1, F-2, F-4, F-5, C-3, C-4, cosmetics.

## 9. Worker roster

recon 5b2859eb · A b0665002 · B b7af4f6e · C 90b7c969 · D 00eff785 · E d71a5fa2 · G be9e2e61 · H 7ad2bafa · I 2a018b4d · J 2360acfa · K f98938ec · L bd2db78c · probe-ac 94b0f78d · probe-b a6c1bca9 · probe-d 025b2a61 · quality 8484e2e4 (stalled 1h16m, terminated) → 37b77f33 (replacement, delivered). 1 re-dispatch total (within Fan-In limit).

## 10. Gaps

None. All 14 todo-graph nodes done; no incomplete nodes; no worker lost twice.

---

### Overall Status
- Commission suite: ✅ PASS (185/185 independent, superset of dev's 141)
- Sibling sweep: ✅ PASS (4 clean + 1 PASS-with-preexisting, quarantined)
- Invariant probes: ✅ 4/4 PASS
- Reviewer gaps 4a/4b: ✅ closed (commits 43a1a4528, f2b1e0537)
- ensure.md Core: ✅ 4/4 critical
- Test-quality: strong; 2 critical test-debt items flagged for backlog (length-cap pin, concurrent-mint pin)
- **VERDICT: PASS-with-preexisting** — agent-secrets P1 @ 0181f689b independently verified.

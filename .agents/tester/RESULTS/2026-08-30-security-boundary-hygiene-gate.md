# Test Gate: Security + Hygiene Batch — feature/security-boundary-hygiene @ a77647bf

Date: 2026-08-30
Gate: Independent merge gate (inherited, revived from checkpoint after v0.11.3 restart terminated the tester)
Branch: `feature/security-boundary-hygiene` @ `a77647bf` (+ test-infra commit `ac2c3091` landed during gate)
RE-GATE (same day): blocker fix `16c59375` applied (option A) and verified — verdict FLIPPED to ✅ PASS @ `16c59375` (+ test-infra probe commit `e25e6bce`). Disposition in §8; re-gate record in §12.
Range: `ba55eabc..a77647bf` = 974b06de (BATCH-1: reserved-origin gate + 422 surface + dict-attr fix) + a77647bf (BATCH-2: str(task_id) PG fix + fail-safe flips + doc truthing + PACKS repairs). EXCLUDED: 5f5791c7 (user config.yaml coding2, zero overlap — verified).
Worker dispatches: 24, ≤3 concurrent (+4 in re-gate, single parallel wave).

## VERDICT: ✅ PASS — CLEARED FOR MERGE (re-gate 2026-08-30 @ fix `16c59375`)

Original verdict at `a77647bf`: ❌ FAIL on exactly ONE census blocker (unreserved durable daemon origin `scheduler`). Fix `16c59375` (option A) resolved it; scoped re-gate verified 4/4 GREEN (§12). All 7 verification scopes GREEN.

Everything the gate was asked to prove was proven — including the 🔴 headline real-PG runtime proof — EXCEPT the origin census, where re-derivation (the blocker direction the forward census could not cover) found **`scheduler`**: a real, durable, daemon-minted origin that is neither reserved nor user-family, forgeable via POST /api/jobs. The batch's own charter (reserved-origin contract) fails with a known forgeable daemon identity in its protected class. Remediation is minutes-sized; re-gate is cheap and scoped (§8).

---

## 1. 🔴 Real-PG probe (THE HEADLINE) — PROD-REPRO-CONFIRMED + FIX-SHAPE-CONFIRMED
- **Read-only prod session** (ensemble_prod, `SET default_transaction_read_only=on` + 5s statement timeout, zero writes, zero commits):
  - Real column: `public.dependency_watchers.source_task_id` = `character varying` (UNBOUNDED — model `max_length=64` is ORM-side only; 🟢 DDL-drift note)
  - **REPRO (int param, SQLAlchemy bind shape)**: verbatim `operator does not exist: character varying = smallint` (sqlstate 42883; PG picked smallint for 26303 — same root cause as prod log's integer wording)
  - **FIX SHAPE (str param)**: binds clean, count=0; str-literal clean
  - Dormancy blast radius: **2 PENDING watchers** (state dist FIRED=6910 / CANCELLED=213 / PENDING=2) — the live consequence of f2 skipping every cycle in prod v0.11.3
- **Env repair**: ensemble_test public-schema GRANTs restored to documented LESSONS-2026-07-29 state (had f|f after schema recreate; now t|t) — local PG test env un-broken for all PG packs
- **Repeatable pack**: `test/packs/pg_pending_watchers_probe_test.{py,sh}` — scratch schema `gate_pg_probe` mirroring prod's unbounded varchar, int-bind → asserted UndefinedFunction, str-bind → clean, self-cleaning DROP CASCADE. RESULT: PASS.
- **Deploy implication**: fix verified at runtime; f2 stays DORMANT in prod until merge+restart (reminder stays urgent).

## 2. Origin contract — 422 surface 30/30 PASS; census: forward NO-GAP, reverse ONE GAP (blocker) — GAP CLOSED in re-gate @ 16c59375: 'scheduler' reserved (17-member set), e2e 422 + JobValidationError envelope + enqueue-not-called confirmed (§8/§12)
- **422 matrix (real ASGI + real Pydantic + real gate, downstream stubbed only)**: omitted→201 w/ effective source='api' (default applied, captured at stub); null→422 string_type; ''→422 string_too_short (the previously-reached-enqueue case); all 16 reserved members→422 gate envelope (JobValidationError, distinguishable from Pydantic shape); **mixed-case (System:/AGENT:/Watchover_next_command) → 201 = deliberate case-sensitivity pinned**; near-misses (systemx:/system/auto-scanx) → 201 (prefix colon-boundary + exact semantics correct); legitimate (api/telegram:/webhook:/custom) → 201.
- **Forward census**: all 16 members have ≥1 real mint site (NO-GAP, no over-reservation).
- **USER_ORIGIN_SOURCES ∩ RESERVED = ∅** — verified from live code (6 vs 16).
- **REVERSE census — 🚨 GAP**: `daemon/sources/adapters/scheduler.py:765` mints `source="scheduler"` → durable in BOTH `job_queue_items.source` (job_type='message' JAFP mirror) and `message_queue.source` (instance_messaging.py:1401). Not in the 16; not in USER_ORIGIN_SOURCES; forgeable via POST /api/jobs (201-confirmed). Unlike telegram:<user> origins, `scheduler` is a pure daemon identity — same class as `admin-endpoint`/`auto-scan` which ARE reserved. Zero sink privilege today (no dispatch sink branches on it) → provenance/audit forgery only, but the gate rule is explicit: unreserved durable daemon origin = blocker. Constants.py:408-409 documents the exclusion as channel-adapter framing — that framing conflates user-bearing channel origins with a userless daemon identity.
- **Adjacent 🟠**: source registration (`registry.py:867`, SourceCreate.source_id `^[a-zA-Z0-9_-]+$`) permits reserved-word source_ids (`system`, `internal_report`, …) → registered-channel messages would mint sources matching reserved dispatch prefixes. Operator-authenticated config-time only. Recommend rejecting reserved-word source_ids at registration.
- **Boundary claim held**: POST /messages confirmed source-less (MessageCreate = content/images/queue_id); DLQ replay preserves recorded source (no second gate bypass).

## 3. Dict-attr 500 fix — PROVEN
Red-green @ ba55eabc: all 3 new real-path tests RED with VERBATIM `AttributeError: 'dict' object has no attribute 'pending_count'` @ messages.py:604 (real traceback = real path; MagicMock masking defeated by real-dict fixture). GREEN @ a77647bf. Revert-proof delivered.

## 4. Fail-safe flips — PASS 5/5 (probe `test/packs/pattern_f_failsafe_flips_probe_test.{py,sh}`)
FS1 exception→skip (ACTIVE, lock held, helper WARNING verbatim; label reuses `orphan_active_skipped_retry_child_live` — 🟢 no dedicated label); FS2 unwired-repo→skip (both helpers True; genuine-pending still skips with correct label); FS3 no-instance_id→skip (sweep-level guard, DEBUG verbatim); **FS4 re-check-next-cycle: skip is a deferral — next sweep with restored lookup finalizes via real boundary (done/failed/failed_at/lock released)**; FS5 healthy-shape negative unaffected. No-main-path-change: prior-gate packs re-ran unchanged — killpath 5/5, capstone 4/4, defer-bus 3/3. 🟢 observations: no dedicated fail-safe label; legacy fallback (job_queue_service=None) drops terminal fields — prod-unreachable (api.py always wires it).

## 5. Red-green (worktree, resolution-proven) — PARTIAL-honest
- R1 source_reservation @ ba55eabc: **20/27 RED** (ImportError shapes + SENTINEL RuntimeErrors + 500-vs-422), 7 pre-fix passes = absence-of-gate/schema pins by design (incl. the deliberate case-sensitivity pin). GREEN 27/27 @ fix.
- R1 message-status 3/3 RED (verbatim dict-attr) → GREEN.
- R2 param pins @ 974b06de: **2/4 RED** (str-when-int + captured-arg-is-str carry the signal); 2 weak (str-when-str idempotency, docstring pin) — honestly classified by the branch's own docstrings.
- GREEN side 45/45 @ a77647bf.

## 6. Mock fidelity — CLEAN
0 vacuous, 0 internal-mocks, 0 blockers (7 info). Real Pydantic+gate+router in all three files; sentinel = legitimate test-injected downstream tripwire (bidirectional: 422-no-call vs 500-with-call + kwargs preserved); real repos at the pin seam; probes sandboxed (no prod DSN, scratch schemas, downstream-only stubs). Old MagicMock-stats tests upgraded to real-dict — the original bug-survivor gap closed.

## 7. Regression — 16/16 GREEN, 0 new failures
job_queue 1569P/0F/38S (base 1565 + 4 exact) · security_boundary_hygiene_suites (NEW) 45P/0F · api 213P/8S exact · core 713P/41F all-quarantine-matched 0-new (RESULT-echo flaw manifested as predicted — adjudicated via exit code) · concurrency 98P/74S exact · claim 178P · jqt 80P/0-des · child_reports 15P · watchdog 47P · wedge 78P · turntrans 48P+1-des · sw2 15P · pausedrace 8P · completion 96P/37S/1-des (StaticPool quarantine deselect held — no re-manifest) · orphan pack 41P (repaired header @ ac2c3091; +2 pre-batch tests, all green under new flip semantics).

## 8. Blocker DISPOSITION (re-gate 2026-08-30) — ✅ RESOLVED
- **Fix applied: option A, commit `16c59375`** — exactly 2 files (+27/−16): `daemon/constants.py` (+1 frozenset member `"scheduler"`, 16→17; framing comment de-conflated — scheduler moved out of the "legitimate user origins" block into the userless daemon-identity class alongside `admin-endpoint`/`auto-scan`) + `tests/unit/routers/test_source_reservation.py` (membership pin 17; `is_reserved_source("scheduler") is True`; accepts-test renamed → `test_create_job_rejects_scheduler_source` with 500→422 and `assert_called_once()` → `assert_not_called()`).
- **Diff-shape verification (read-only, 7/7 checks PASS)**: daemon delta = constants.py ONLY; mint site `scheduler.py` untouched (forward census NO-GAP preserved — every reserved member still has ≥1 real mint site); zero new durable `source=` mint sites; USER_ORIGIN_SOURCES untouched (6 vs 17, overlap ∅). **Carry-over verdict: all 6 other scopes' GREEN conclusions CARRY unchanged — zero production-behavior delta outside the reservation.**
- **Re-gate executed per prescription — 4/4 GREEN** (§12): security suites 45/45 (0.62s) · api 213P/8S/0F exact-zero-delta (14s) · origin e2e probe 30/30 — case 8e `scheduler` → **422 + JobValidationError gate envelope (fields=['source']) + enqueue-not-called at stub** (3s) · targeted re-census CLEAN (17 reserved, 17 mint sites, 0 missing/over-reserved; USER_ORIGIN ∩ RESERVED = ∅).
- Option B (re-mint `scheduler:<id>` + prefix reservation + row migration) not needed.
- Original remediation options (historical): option A as above; option B — re-mint `scheduler:<schedule_id>` at scheduler.py:765 + reserve the `scheduler:` prefix (migration concern for existing rows — heavier).

## 9. ensure.md
Core Critical #1 (no regressions in changed packs): ✅ all green. #2/#3 concurrency: ✅. #4 dev.sh static: ✅ (recon :102). Important: 1/1 + 1 N/A-scoped. Release Gate NOT TRIGGERED. Contradictions: NONE. (ensure.md itself passes; the gate FAIL is the census blocker above, not a pack failure.)

## 10. Follow-ups (non-blocking)
- 🟠 Reserved-word source_id rejection at registration (§2 adjacent)
- 🟢 ensemble_test GRANT fragility (schema recreate drops them — consider a conftest preflight or DB-level default privileges)
- 🟢 Stale "60s cycle" strings at job_recovery_service.py:2031/:2045/:2193 (one runtime log string says 60s; cycle is 300s)
- 🟢 Prod column unbounded varchar vs model max_length=64 (ORM/DDL drift)
- 🟢 Fail-safe skip label reuse (no dedicated `orphan_active_lineage_lookup_failed`)
- 🟢 Repo-wide pack `set -e` RESULT-echo flaw (manifested again this gate on core FAIL exit; maintenance batch overdue)
- 🟢 2 weak param pins naming (idempotency/doc-drift, not red-green)
- 🔐 Prod PG password exposed in one recon tool output (masked in reports) — consider rotation

## 11. Code changes (this gate)
- `ac2c3091` — test: security_boundary_hygiene branch-suite pack + orphan-recovery pack header count repair (committed)
- Untracked probe scripts (carry to re-gate; commit with eventual merge): pg_pending_watchers_probe, origin_contract_e2e_probe, pattern_f_failsafe_flips_probe (.py+.sh each)
- PACKS.md repairs from the branch: adjudicated and ACCEPTED (2 torn-tail repairs real; gate annotation accurate)

## 12. Re-gate record (2026-08-30, inherited scope)
- Scope: §8 prescription only — security suites + api pack + origin e2e probe + targeted re-census + carry-over confirmation. 4 parallel workers, 4/4 PASS, total wall-clock ≈ 3 min.
- Worker evidence: `584e87be` (suites, `test-pack-execution`) · `4bf97ee9` (api, `test-pack-execution`) · `398bf5d8` (probe, `test-pack-execution`) · `22505136` (census, read-only).
- Probe pin flip committed `e25e6bce` (test-infra, parent `16c59375`): case 8e scheduler `201`→`gate_422` (label: reserved exact — daemon-minted), census binding 16→17, other 29 case pins untouched, 30/30 PASS.
- Merge chain for giter (knowingly, full chain): `ba55eabc` → `974b06de` (batch) → `a77647bf` (rework) → `5f5791c7` (user config.yaml — excluded from gate, zero overlap, verified) → `ac2c3091` (predecessor's pack-script rider) → `16c59375` (blocker fix) → `e25e6bce` (origin-probe commit, test-infra).
- 🟢 non-blocking: docstring cites mint site `scheduler.py:765`; live tree line is `:763` (cosmetic pre-existing drift carried into the new docstring — boundary enforced by set membership, not the citation).
- Untracked leftovers (carry to merge): `pattern_f_failsafe_flips_probe_test.*` + `pg_pending_watchers_probe_test.*` still untracked.
- Deploy urgency UNCHANGED: f2 fix verified at runtime (§1) but DORMANT in prod until merge + restart; 2 PENDING watchers waiting.

### Overall Status (RE-GATE FLIP 2026-08-30 @ `16c59375`)
- Regression ✅ · PG headline ✅ PROD-REPRO+FIX-SHAPE · 422 surface ✅ 30/30 · dict-attr ✅ · fail-safe ✅ 5/5 · red-green ✅ (honest partials) · mockfid ✅
- Census: ✅ REVERSE GAP CLOSED (`scheduler` reserved, 17-member set; e2e 422 + enqueue-not-called confirmed; forward NO-GAP 17/17 mint sites; USER_ORIGIN overlap ∅)
- **Testing Complete: ✅ READY — CLEARED FOR MERGE (full chain knowingly, incl. user commit `5f5791c7` + riders `ac2c3091`/`e25e6bce`). Deploy of the f2 fix remains URGENT.**

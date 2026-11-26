# Phase 1: Implement the validation leg

## Objective

Land the promote-time backup-artifact validation leg as a single, surgical change set across the daemon tool lane, the journal seam, the tool signature, the refusal taxonomy, the test suite, and the PACKS registration — with zero change to the shell ceremony, zero new argv flag, zero dump leg, zero k8s object, zero new DB table, zero migration, and zero promote/stage/release.

## Tasks

### T1 — New module `daemon/tools/upgrade_db_preflight.py`

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create the new module with a module docstring naming the normative source (arch §3-§5, §10) + the pins it covers; `from __future__ import annotations`; stdlib → third-party (`sqlalchemy`) → absolute `daemon.*` imports, never relative | none | Module imports cleanly; docstring names pins (a)-(f) |
| 2 | Define constants: `DB_PREFLIGHT_LANE = "pgdumpall"`; `DB_PREFLIGHT_MAX_AGE_S` default 172800 via `_env_int("ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S", 172800)`; `DB_PREFLIGHT_MODES = {"validation-only", "full-dump"}` | #1 | Constants present and importable |
| 3 | Implement `fetch_newest_artifact(manager)` — one parameterised SELECT against `public.dump_lane_artifacts` (`WHERE lane = :lane ORDER BY finished_at DESC, artifact DESC LIMIT 1`), columns `lane, artifact, bytes, finished_at, integrity_ok, first_seen_at, last_seen_at`; sync call inside `manager.engine.begin()` wrapped in `asyncio.to_thread` + `asyncio.wait_for` with a server-side `statement_timeout` (house precedent `ens_db_tools.py:779-830`); pooled connection reused, never a new one. PRIVILEGE (M3, measured by **devops** via a role-privilege probe on `ensemble_prod_ro`, 2026-11-26; recorded in POK `dump-lane-01-registry-shape` id `f21ed5db-3dd1-44b0-818f-147b76649304`, last verified 2026-11-26T04:10:39Z — **second-hand to this planning lane; no SQL was executed by this lane**): the read needs TABLE-level SELECT — the daemon's engine role `ensemble` OWNS the table; `has_table_privilege(ensemble_prod_ro, public.dump_lane_artifacts, SELECT)` = **False**, `has_table_privilege(ensemble_prod_ro, public.dump_lane_heartbeat, SELECT)` = **True**, and a direct SELECT on the table raises `InsufficientPrivilegeError` (the only grant on the RO role is `GRANT SELECT ON public.dump_lane_heartbeat TO ensemble_prod_ro`). A privilege failure is the `db-preflight-registry-unavailable` refusal — it is NEVER "fixed" by falling back to the heartbeat VIEW, which filters `WHERE integrity_ok` and would MASK a broken newest artifact (D2, pin (a)) | #1 | SELECT uses the pooled engine; `first_seen_at`/`last_seen_at` are diagnostics only; the view is never substituted for the table |
| 4 | Implement the D2 selection rule: newest row OVERALL regardless of `integrity_ok`, then assert `integrity_ok` on THAT row; refuse `db-preflight-artifact-broken` naming the artifact | #3 | Pin (a) passes (mutation-proven) |
| 5 | Implement the FIVE conditions (registry-availability precondition + four arch assertions) in order (m5): (1) relation absent/unreadable/query-failed ⇒ `db-preflight-registry-unavailable`; (2) zero rows ⇒ `db-preflight-no-backup`; (3) `integrity_ok` false on the selected newest row ⇒ `db-preflight-artifact-broken` naming the artifact; (4) `bytes <= 0` ⇒ `db-preflight-artifact-empty`; (5) `finished_at` older than bound ⇒ `db-preflight-artifact-stale` naming artifact + age + bound. Pin (a)'s fixture carries `bytes > 0` so a broken newest artifact reports `db-preflight-artifact-broken` (not `db-preflight-artifact-empty`). Condition (6) (`artifact name timestamp ≠ finished_at ⇒ db-preflight-identity-conflict`) is DROPPED (M1) | #3, #4 | All five refusal paths present and named |
| 6 | Implement the fingerprint per the C3 adjudication: `finished_at_utc = finished_at.astimezone(timezone.utc) if finished_at.tzinfo else finished_at.replace(tzinfo=timezone.utc)`; `finished_at_iso = finished_at_utc.isoformat().replace("+00:00", "Z")`; `fingerprint_sha256 = sha256(json.dumps([lane, artifact, bytes, finished_at_iso], separators=(",", ":")).encode()).hexdigest()` and the identity tuple `(lane, artifact, bytes, finished_at)` (normalised ISO used for the fingerprint; the tuple keeps the raw datetime). The IDENTITY COMPARISON against the remembered record compares the NORMALISED forms on BOTH sides (`finished_at` as `finished_at_iso`, `bytes` as `int`) — the remembered value is persisted as the `<ISO-8601 Z>` string per the D3 record shape, so a raw `datetime` compared against that string is ALWAYS unequal and the skip would NEVER fire | #4 | Fingerprint matches decisions.md §PREDICATE LEDGER character-for-character; `sort_keys=True` dropped (no-op on a list); microseconds preserved |
| 7 | Implement the skip predicate: identity unchanged AND fresh ⇒ skip; unchanged identity that has aged past the bound ⇒ does NOT skip, refuses `db-preflight-artifact-stale`. The age check runs on EVERY invocation unconditionally | #5, #6 | Pins (b) third case passes |
| 8 | Implement the mode gate: `full-dump` ⇒ `db-preflight-full-dump-unavailable`; unrecognised ⇒ `db-preflight-mode-unknown`; absent ⇒ validation-only proceeds. The gate is evaluated at BOTH the DRY-RUN (BEFORE any nonce is minted — M5) and the arm path, with the SAME token; the dry-run text carries the arm-refusal wording so the human is never shown a green preflight for a request the arm will refuse | #1 | Pin (d) passes |
| 8a (M2) | Implement the ENVIRONMENT gate: the verdict function receives `self_env`; when `self_env != "live"` it returns a `skipped-by-env` result with NO DB read, NO registry refusal, NO journal row, and NO `db_preflight` record write (the D3 record is left untouched). The MODE gate still applies in EVERY environment — a `full-dump`/unknown mode is refused wherever it is requested, because that refusal does not depend on the registry | #1, #8 | Non-live ⇒ the registry validation is a documented no-op, never a refusal (the registry lives in `ensemble_prod`; dev/demo/sandbox have no such relation, so a non-live check would ALWAYS refuse) |
| 9 | Implement the verdict function returning a structured result (validated/skipped + the artifact record + the refusal token when refused) — injectable/patchable seam (module-level function the tests patch; the suite has zero `create_engine`/`sessionmaker`/`Engine` matches) | #1-#8 | Hermetic tests can patch the seam |
| 10 | Every fixture `tmp_path`-scoped; never a real install dir, never live | #1-#9 | House style satisfied |

**Files:** `daemon/tools/upgrade_db_preflight.py` (NEW).

### T2 — `daemon/tools/upgrade_journal.py` changes

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | `ensure_extensions`: seed `db_preflight` as `None` (the placeholder; the written value is the ONE compact object in §D3) — mirror the `pending_op`/`pending_restart` shape (`None` default, not the `pending_wakes` `{}` shape): `if "db_preflight" not in data: data["db_preflight"] = None / changed = True` | none | Key seeded on torn/absent journal init |
| 2 | New helper `record_db_preflight(install_dir, record)` modelled on `arm_pending_wake` (:1825-1853): `data = ensure_extensions(install_dir)` → mutate → `journal_write(install_dir, data)`. Propagates `JournalTorn` (never writes over a torn journal) | #1 | Helper writes atomically under caller-held lock; torn journal ⇒ refuses `db-preflight-journal-torn` |
| 2a (C2) | Add `db_preflight: str | None = None` to the `PendingOp` dataclass (`upgrade_journal.py` :1564-1625). `to_json`/`from_json` need NO change (`asdict` + `__dataclass_fields__` carry it). Round-trip assertion: `write_pending_op` → `read_pending_op` → the field survives | #1 | Field survives a write/read round trip (hand-inserted dict key would be silently dropped — pin (d)(ii) would pass on write and vanish on the next read) |
| 3 | Docstring update naming `db_preflight` as the seventh key. Target: the MODULE docstring's "Extensions this module may add" list (`upgrade_journal.py` :44-50), which is STALE at 3 keys (`pending_op`, `pending_restart`, `pending_actions`) and omits `pending_wakes`, `restart_audits`, `restart_window` — bring it to 7, do NOT append to 3. The `ensure_extensions` docstring (:770-788) enumerates NO keys | #1 | Docstring accurate (target named explicitly) |
| 4 (S2) | Implementation constraint: `TestADR034SpliceDiscipline` (test_upgrade_journal.py:312) imposes an AST BAN inside `upgrade_journal.py` on `.count(` calls and on the identifiers `occurrences` / `occurrence_count`. `record_db_preflight` (and any helper) MUST NOT use `.count(` | #1, #2 | AST lint clean |

**Files:** `daemon/tools/upgrade_journal.py` (MODIFY — lines :769-811 + new helper after :1853 + PendingOp dataclass :1564-1625).

### T3 — `daemon/tools/upgrade_tools.py` arm-preflight check

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Insert the new check between the `stale-scripts-dir` refusal block (closing at :4375) and the Step 0c comment header (:4376), at 12-space indentation matching the Step 0c `try:` (:4389) | T1, T2 | Semantic anchor holds |
| 1a (M10) | Step 0 (PRE-WRITE TORN PROBE): read the journal (`ensure_extensions` or `journal_read`) and catch `JournalTorn` BEFORE any write; on torn ⇒ release lock ⇒ refuse `db-preflight-journal-torn`. Pairs with C1; makes the torn verdict reachable — `journal_init` is a NO-OP on an existing-but-torn file (`if jp.is_file(): return`, `upgrade_journal.py:682-688`), so the FIRST journal touch would otherwise swallow the torn condition and execution would continue. The probe therefore precedes EVERY write, `ensure_extensions` included | #1 | Torn path refuses before any write |
| 2 | Under the held lock: call the new module's verdict function; on refusal, write the journal-ALWAYS row `uj.journal_history_append(install_dir, "db_preflight_refused", f"token=<token> artifact={artifact} age={age}s bound={bound}s")` WITH THE LOCK STILL HELD, BEFORE `journal_lock_release(install_dir)` — mirroring the sibling shape at :4232-4247 (the write itself is :4233-4237). This row is the ONLY audit trail for a live refusal because `_refusal` → `_journal_refusal_event` SKIPS journaling when `self_env == "live"` (:1970-1979). Then `journal_lock_release(install_dir)` FIRST, then `return _refusal(label, "<token>", "arm preflight: ... — nonce NOT burned (the arm simply did not happen; nothing consumed)")` — sibling shape. NOTE: this path does NOT copy the sibling's `JournalTorn → journal_init + retry` recovery (:4238-4247): `journal_init` is a no-op on an existing-but-torn file, so the retry re-raises and the row is LOST. Here a `JournalTorn` raised by the write itself is treated as the torn verdict — release the lock, refuse `db-preflight-journal-torn`, write NO row | #1 | SEVEN of the eight refusal paths write the row; the eighth (`db-preflight-journal-torn`) writes NO row — the journal is the unavailable substrate on that path. Release the lock, then return the refusal. Refusal detail uses the artifact-bearing format (`token= artifact= age= bound=`) for broken/empty/stale and the artifact-less format (`token= reason=<free-text>`) for the other five (M7) |
| 3 | On validation success: write the journal-ALWAYS row `uj.journal_history_append(install_dir, "db_preflight_validated", f"artifact={artifact} finished_at={finished_at} age={age}s bound={bound}s verdict=validated")` (sibling write shape at :4233-4237; the sibling's `JournalTorn → init+retry` recovery is NOT copied — see #2) | #1 | Row appended naming the artifact |
| 4 | On skip: write `uj.journal_history_append(install_dir, "db_preflight_skipped", ...)` naming the artifact; advance only `last_checked_at` and `verdict` in the `db_preflight` record, leaving the validated tuple/fingerprint untouched | #1, T2 | Skip row + stamp advance |
| 5 | Write the `db_preflight` record via `record_db_preflight` (ensure_extensions → mutate → journal_write) — the D3 write. The record is written ONLY on `validated`/`skipped` verdicts, never on a refusal (the D3 record shape has no `refused` verdict) (m8) | #1, T2 | Record round-trips |
| 6 | On torn journal: refuse `db-preflight-journal-torn` (release lock first). Writes NO `db_preflight_refused` row — the journal is the unavailable substrate (C1) | #1 | Refusal path present; no row written |
| 7 | Surface the effective data-loss age (artifact `finished_at` + age + bound) in the success output | #1 | Disclosure in output |

**Files:** `daemon/tools/upgrade_tools.py` (MODIFY — insert between :4375 and :4376).

### T4 — D6 transport in `daemon/tools/upgrade_tools.py`

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | `system_upgrade` signature gains `db_preflight: str \| None = None` (decorator + signature :3609-3617; narrow to :3611-3617 if excluding the `@register_tool_category` decorator at :3609) | none | Signature updated |
| 2 | Dry-run/confirmation surfacing (:3763-3784): when `db_preflight` is non-default, surface it in the minted-nonce branch text so the human reads what they are attesting | #1 | Text surfaced |
| 2a (M5) | Refuse `full-dump` / unknown modes at DRY-RUN TIME TOO (same token, NO nonce minted); put the arm-refusal wording into the dry-run text | #1 | Dry-run refuses before nonce mint |
| 3 | PendingOp capture (:4463-4477): pass `db_preflight=db_preflight` at the `PendingOp(...)` construction :4463-4477 (the dataclass field added in T2 #2a). Round-trip assertion: `write_pending_op` → `read_pending_op` → the field survives | #1, T2 | Field present in PendingOp and survives round trip |
| 4 | Correct the false dry-run PLAN string at :3759-3761 (`"PLAN: pg_dump preflight → stop (SINGLE-TERM) → flip …"`) to match reality (constraint 8) | none | String corrected |
| 5 | Ensure `db_preflight` does NOT appear in `EXECUTOR_ENV_ALLOWLIST` / `EXECUTOR_ENV_PREFIXES`, does NOT become an argv flag, does NOT require a `promote.sh` `case` arm, does NOT add to `_verified_arm_extras` | #1 | Boundary pins pass |

**Files:** `daemon/tools/upgrade_tools.py` (MODIFY — :3609-3617, :3759-3761, :3763-3784, :4463-4477).

### T5 — Refusal-token taxonomy

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add all eight tokens to `TestRefusalTaxonomy.UPGRADE_TOKENS` in `tests/unit/tools/test_upgrade_tools.py` (:4019-4049) | none | Set updated |
| 2 | Name all eight tokens as `reason=<token>` in `system_upgrade._full_doc_` in `daemon/tools/upgrade_tools.py` (the docstring block at :4527+; the `preflight-argv-malformed` mention is at :4588 — EXACT, verified; `_full_doc_` block is :4527-4595) | #1 | Docstring updated; `test_documented_tokens_cover_the_exercised_matrix` passes |

**Files:** `tests/unit/tools/test_upgrade_tools.py` (MODIFY), `daemon/tools/upgrade_tools.py` (MODIFY docstring).

### T6 — Tests

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | NEW `tests/unit/tools/test_upgrade_db_preflight.py` — hermetic unit nodes: masking pin, skip + stamp-advance pin (incl. aged-past-bound third case), loud-failure pin (zero rows / relation absent / torn journal), opt-in pin (full-dump / unknown / absent + boundary pins), durability pin (round-trip + ensure_extensions seed + ADR-034 round-trip). Module docstring naming normative source + pins; `from __future__ import annotations`; stdlib → third-party → absolute `daemon.*`; `class TestXxx:` PascalCase; `def test_<snake_verb>(...) -> None:`; async via `@pytest.mark.asyncio`; module-level `@pytest.fixture` with docstrings; every fixture `tmp_path`-scoped (S1) | T1 | All hermetic nodes green |
| 2 | NEW arm-path class in `tests/unit/tools/test_upgrade_tools.py` alongside `TestArmPreflightBeforeBurn` — ordering anchor (check after lock acquire, before nonce burn), lock-release-then-refuse on every refusal path, journal-always row, no `pending_op`, nonce unspent. Plus a POSITIVE ordering assertion: patch `journal_lock_acquire` to record, patch the new verdict seam to record, assert `lock_acquire < verdict < consume_pending_action` (M8). Plus the PRE-LOCK INVARIANT test (M4): a refusal that fires BEFORE the lock acquire (e.g. the 3-factor gate) still writes NO journal row and mutates nothing — preserving `test_live_refusals_never_mutate_pipeline`'s invariant — while the post-lock db-preflight refusal writes `db_preflight_refused` | T3 | Arm-path nodes green |
| 3 | Env-isolation pin mirroring `tests/unit/tools/test_agent_restart.py:188-193` (T0c shape) — assert `ENSEMBLE_PROMOTE_DB_PREFLIGHT` ∉ `EXECUTOR_ENV_ALLOWLIST` and ∉ the `EXECUTOR_ENV_PREFIXES` match | T4 | Pin green |
| 3a (M2) | ENV MATRIX (live vs non-live): live ⇒ the registry validation RUNS (DB read, verdict/refusal path exercised, journal row, D3 record); dev / demo / sandbox ⇒ the registry validation is SKIPPED entirely — no DB read, no registry refusal, no `db_preflight_*` journal row, no `db_preflight` record write; the arm proceeds. The MODE gate still applies in every environment (a `full-dump`/unknown mode is refused wherever requested). Assert the skip is EXPLICIT in the arm's output text (e.g. `db-preflight: SKIPPED (self_env=dev)`), never a silent pass | T1 | Matrix green |
| 4 | Shell-untouched pin — path-scoped diff against base commit for `promote.sh` and `lib.sh` (NEW test; precedent shape `tests/packs/preflight_anchor_index_test.sh:7,:54` pinned-base + `tests/packs/preflight_journal_parity_test.sh:326` name-only). pytest unit test using `subprocess` to invoke `git diff --quiet <base> -- scripts/upgrade/promote.sh scripts/upgrade/lib.sh`. The base is computed at test time as `git merge-base HEAD origin/latest` (NOT a hardcoded constant — a hardcoded `8b646ae37` FALSE-REDS the moment upstream changes); add a skip-guard for non-git exports (the house runs some suites from `git archive` exports — see PACKS.md:202) (m6) | none | Pin green (or skip with reason) |
| 5 | C-1 lint run — `tests/shell_continuation_scan.py <repo-root> [--quiet]` exit 0; no new test needed (the lane touches zero shell files) | none | Exit 0 |
| 6 | Run the existing interlock pack `test/packs/upgrade_tool_interlock_unit_test.sh` (runs `test_upgrade_journal.py` + `test_upgrade_tools.py`) to confirm no regression | T1-T5 | Pack green |

**Files:** `tests/unit/tools/test_upgrade_db_preflight.py` (NEW); `tests/unit/tools/test_upgrade_tools.py` (MODIFY).

### T7 — PACKS.md registration row

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add a row to `.agents/tester/PACKS.md` using the CANONICAL 5-col table format `| Pack / leg | Invocation | Scope | Last run | Status |` with a SEMANTIC MARKER instead of a stale line number. PRECISE MARKERS (measured at origin/latest `d7c15d45f`, PACKS.md = 5960 lines): the canonical header `| Pack / leg | Invocation | Scope | Last run | Status |` occurs 42 times — its FIRST occurrence is `:10`, whose first data row is `:12` = `pok_gate_llm_integration_named_test` (⇒ "the canonical-format table whose header is at :10 and whose first row is `pok_gate_llm_integration_named_test`"); the canonical example row `secret_do_not_touch_lane_static_unit_test` is at `:202` (NOT `:16` — `:16` is the persona-neutrality heading) and is the first data row of the table headed at `:200`. The legacy interlock rows are at `:3593` / `:3707` / `:4173` (`upgrade_tool_interlock_unit_test`) — LEGACY-shape examples only (registration precedent, not canonical format); `:3391` is `e2e_workflows_ensure_test` and `:3505` is a 4-col legacy header — neither is a citation target. Registration is by row, not by a name-driven runner (PACKS.md has no parser); the `Invocation` cell is the command a tester runs VERBATIM. The row carries the node-census + freeze-sha convention — `**N nodes** (mechanical --collect-only census at this freeze <sha>: N collected, RC=0, 0 collection errors)` (shape precedent: the `persona_neutrality_pins_unit_test` row at `:26`) — plus the `Last run` cell's date + base sha. Note: a `test/packs/*.sh` leg is NOT mandatory (row-only precedent exists at :8) | T6 | Row present; `grep -c "upgrade_db_preflight" .agents/tester/PACKS.md` > 0 |
| 2 | State the `cn 2039b459` chain as TO-VERIFY (`grep -rn "2039b459" .agents/`) — not a verified repo anchor | #1 | Documented as TO-VERIFY |

**Files:** `.agents/tester/PACKS.md` (MODIFY).

## §CANONICAL PREDICATE STRINGS (verbatim from decisions.md §PREDICATE LEDGER)

The body below reproduces every ledger predicate character-for-character. Self-check #6 is a REAL DIFF: extract each canonical string from decisions.md and grep it here; report the counts.

### Freshness-bound predicate
```
finished_at_age_s = (now_utc - finished_at).total_seconds()
finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S (default 172800)
```

### Identity tuple + fingerprint formula (C3)
```
identity = (lane, artifact, bytes, finished_at)
finished_at_utc = finished_at.astimezone(timezone.utc) if finished_at.tzinfo else finished_at.replace(tzinfo=timezone.utc)
finished_at_iso = finished_at_utc.isoformat().replace("+00:00", "Z")
fingerprint_sha256 = sha256(json.dumps([lane, artifact, bytes, finished_at_iso], separators=(",", ":")).encode()).hexdigest()
```

### Integrity assertion
```
integrity_ok IS TRUE on the newest-overall row (selected by finished_at DESC, artifact DESC)
```

### Selection predicate
```sql
SELECT lane, artifact, bytes, finished_at, integrity_ok,
       first_seen_at, last_seen_at
FROM public.dump_lane_artifacts
WHERE lane = :lane
ORDER BY finished_at DESC, artifact DESC
LIMIT 1
```

### Skip predicate
```
identity_unchanged ⇔ (lane, artifact, bytes, finished_at, fingerprint_sha256) == remembered
skip ⇔ (identity_unchanged AND finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S)
```
The comparison is made on the **NORMALISED** forms: `finished_at` is compared as `finished_at_iso` on BOTH sides (the remembered value is persisted as the `<ISO-8601 Z>` string per the D3 record shape, so a raw `datetime` — which never equals its own stored string — must never be compared to it directly); `bytes` compares as `int`. The age check is evaluated on every invocation; the skip may short-circuit ONLY the immutable checks (integrity, byte count) and NEVER the age check.

### Environment gate (M2)
```
self_env == "live"  ⇒ the registry validation RUNS (DB read, verdict, journal row, D3 record write)
self_env != "live"  ⇒ the registry validation is SKIPPED (no DB read, no registry refusal, no journal row, no D3 record write)
the MODE gate applies in EVERY environment (full-dump / unknown mode is refused wherever it is requested)
```
Live-gating is FORCED by the data topology, not merely preferred: the registry lives in `ensemble_prod`; dev/demo/sandbox have no such relation, so a non-live check would ALWAYS refuse. There is deliberately **no** `ENSEMBLE_PROMOTE_DB_PREFLIGHT_ENABLED` kill-switch — the direct shell path is already the documented escape (F1), and a kill-switch is a silent-downgrade vector of exactly the kind this lane exists to prevent ("Simple always better").

### Seed value
```
db_preflight = None
```
The seeded placeholder (before any validation has run) mirrors the `pending_op` / `pending_restart` shape (`None`), NOT the `pending_wakes` `{}` shape.

### Refusal detail format (M7)
Two formats, determined by whether the refusal carries an artifact:

```
token=<token> artifact=<name> age=<s>s bound=<s>s
token=<token> reason=<free-text>
```

- **Artifact-bearing** (token + artifact + age + bound): `token=<token> artifact=<name> age=<s>s bound=<s>s` — used by `db-preflight-artifact-broken`, `db-preflight-artifact-empty`, `db-preflight-artifact-stale`.
- **Artifact-less** (token + free-text reason): `token=<token> reason=<free-text>` — used by `db-preflight-registry-unavailable`, `db-preflight-no-backup`, `db-preflight-journal-torn`, `db-preflight-full-dump-unavailable`, `db-preflight-mode-unknown`.

### Refusal-token family (EIGHT)
```
db-preflight-registry-unavailable
db-preflight-no-backup
db-preflight-artifact-broken
db-preflight-artifact-empty
db-preflight-artifact-stale
db-preflight-journal-torn
db-preflight-full-dump-unavailable
db-preflight-mode-unknown
```

### Journal-always row names
```
db_preflight_validated   # on a fresh validation
db_preflight_skipped     # on a skip
db_preflight_refused     # on EVERY refusal path EXCEPT `db-preflight-journal-torn` (the journal is the unavailable substrate on that path — no row is written); written while the lock is still held, before release
```

### Constants
```
DB_PREFLIGHT_LANE = "pgdumpall"
DB_PREFLIGHT_MAX_AGE_S = _env_int("ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S", 172800)
DB_PREFLIGHT_MODES = {"validation-only", "full-dump"}
```

### D3 record shape
```json
"db_preflight": {
  "lane":         "pgdumpall",
  "artifact":     "<artifact name>",
  "bytes":        <int>,
  "finished_at":  "<ISO-8601 Z>",
  "fingerprint_sha256": "<sha256 over canonicalised tuple>",
  "last_checked_at":    "<ISO-8601 Z>",
  "verdict":      "validated" | "skipped"
}
```
The seeded placeholder is `None`; the written value is the compact object above. The record is written ONLY on a `validated`/`skipped` verdict — never on a refusal (the D3 record shape has no `refused` verdict) (m8).

### Mode gate
```
mode == "full-dump"  ⇒ refuse db-preflight-full-dump-unavailable
mode not in DB_PREFLIGHT_MODES ⇒ refuse db-preflight-mode-unknown
mode is None/absent  ⇒ validation-only proceeds
```
The gate is evaluated at BOTH the dry-run (before any nonce is minted — M5) and the arm path, with the SAME token.

### Opt-in boundary (D6)
```
ENSEMBLE_PROMOTE_DB_PREFLIGHT ∉ EXECUTOR_ENV_ALLOWLIST
ENSEMBLE_PROMOTE_DB_PREFLIGHT ∉ EXECUTOR_ENV_PREFIXES match
db_preflight ∉ _verified_arm_extras
db_preflight is NOT an argv flag (no promote.sh case arm)
```

### Disclosure pointer
```
D8 disclosure text = arch §15 with five adjudicated corrections (C4 one-producer/one-publisher rewrite, C4 publisher-mechanism sentence, m7 lag+margin sentence, M2 non-live sentence, M3 privilege sentence); reproduced in plan-overview.md §"D8 disclosure text"
```

## TEST MATRIX (pin → test file → test name → status flag)

### Architect pins (a)-(f)

| Pin | Asserts | Test file | Test name | Status |
|---|---|---|---|---|
| (a) masking | Newest overall artifact `integrity_ok=false` while older verified ⇒ refuses `db-preflight-artifact-broken` naming newest; mutation-proven (reintroduce view-based read ⇒ RED) | `tests/unit/tools/test_upgrade_db_preflight.py` | `test_masking_newest_broken_refuses_naming_newest` | NEW |
| (b) skip + stamp-advance | Two consecutive invocations, unchanged fresh artifact ⇒ second records `verdict="skipped"`, `last_checked_at` advances, `db_preflight_skipped` row naming artifact; third case: same artifact aged past bound ⇒ skip does NOT fire, refuses `db-preflight-artifact-stale` | `tests/unit/tools/test_upgrade_db_preflight.py` | `test_skip_advances_last_checked_at_and_appends_row` + `test_skip_does_not_fire_when_aged_past_bound` | NEW |
| (c) loud-failure | Zero rows ⇒ `db-preflight-no-backup`; relation absent ⇒ `db-preflight-registry-unavailable`; torn journal ⇒ `db-preflight-journal-torn`; all leave nonce unspent + no `pending_op` | `tests/unit/tools/test_upgrade_db_preflight.py` (hermetic) + `tests/unit/tools/test_upgrade_tools.py` (arm-path) | `test_zero_rows_refuses_no_backup` / `test_relation_absent_refuses_unavailable` / `test_torn_journal_refuses_journal_torn` (hermetic) + `TestArmPreflightDbPreflight` arm-path class | NEW |
| (d) opt-in | `full-dump` ⇒ `db-preflight-full-dump-unavailable`; unrecognised ⇒ `db-preflight-mode-unknown`; absent ⇒ validation-only proceeds; (i) `ENSEMBLE_PROMOTE_DB_PREFLIGHT` ∉ allowlist/prefixes (T0c shape); (ii) value appears in PendingOp; (iii) the SAME token fires at DRY-RUN time, BEFORE any nonce is minted, and the dry-run text carries the arm-refusal wording (M5) | `tests/unit/tools/test_upgrade_db_preflight.py` (mode) + `tests/unit/tools/test_upgrade_tools.py` (boundary + PendingOp + dry-run) | `test_full_dump_refuses_unavailable` / `test_unknown_mode_refuses_mode_unknown` / `test_absent_mode_proceeds_validation_only` (hermetic) + `test_db_preflight_opt_in_not_in_executor_env` / `test_db_preflight_opt_in_captured_in_pending_op` / `test_dry_run_refuses_full_dump_before_nonce_mint` (arm-path) | NEW |
| (e) transaction | (1) `promote.sh` + `lib.sh` byte-unchanged (path-scoped diff vs base); (2) refusal at new check leaves nonce unspent + no `pending_op`; (3) check runs after lock acquire, before nonce burn (structural ordering) | `tests/unit/tools/test_upgrade_tools.py` | `test_promote_shell_untouched_by_this_lane` (NEW) + `test_arm_preflight_refuses_*` (existing ordering anchor `TestArmPreflightBeforeBurn`) + `test_live_refusals_never_mutate_pipeline` (:3022) | NEW + EXISTING |
| (f) durability | Record round-trips a simulated restart (fresh process reads journal, skip fires); `ensure_extensions` seeds the key; ADR-034 round-trip (unrelated extra key survives) | `tests/unit/tools/test_upgrade_db_preflight.py` | `test_record_round_trips_simulated_restart` / `test_ensure_extensions_seeds_db_preflight` / `test_adr034_round_trip_preserves_extra_key` | NEW |
| (M2) environment gate | live ⇒ the registry validation runs (DB read, verdict, journal row, D3 record); dev/demo/sandbox ⇒ SKIPPED (no DB read, no registry refusal, no `db_preflight_*` row, no D3 write) and the arm proceeds with an EXPLICIT skip line; the MODE gate still fires in every environment | `tests/unit/tools/test_upgrade_tools.py` (arm-path, env matrix — T6 #3a) | `test_db_preflight_skipped_when_not_live` / `test_db_preflight_runs_when_live` | NEW |

### R1-R8 coverage

| Req | Coverage | Test |
|---|---|---|
| R1 stop dumping; validate latest daily backup | T1 (registry read + verdict) | pins (a), (c) |
| R2 cheap + meaningful; corrupted/truncated FAILS LOUDLY naming artifact | T1 (integrity assertion names artifact) | pin (a) |
| R3 remember identity durably; NOT POK | T2 (journal key) + T3 (record write) | pin (f) |
| R4 skip when unchanged; log LOUDLY; stamp MUST ADVANCE on skip | T1 (skip predicate) + T3 (history row + stamp advance) | pin (b) |
| R5 no backup / broken ⇒ FAIL LOUDLY; never silently proceed; never fall back to dumping | T1 (gates) + T3 (refusal) | pin (c) |
| R6 full dump behind explicit opt-in | T4 (transport) + T1 (mode gate) | pin (d) |
| R7 do not weaken the transaction | T3 (lock-first refusal) + T6 (shell-untouched pin) | pin (e) |
| R8 C-1 class discipline | T6 (lint run) | — (trivially satisfied, no shell code added) |

### Existing tests re-used (verified paths)

| Test file | Anchor | Role |
|---|---|---|
| `tests/unit/tools/test_upgrade_tools.py` | `TestArmPreflightBeforeBurn` :3045 (`test_arm_preflight_passes_nonce_burns_after_checks` :3143 — ordering anchor) | Ordering verification |
| `tests/unit/tools/test_upgrade_tools.py` | `TestScriptsDirProvenanceArmPath` :4849 (`test_w1_probe_error_refuses_and_releases_the_lock` :4852 — lock-release + refusal anchor) | Lock-release pattern |
| `tests/unit/tools/test_upgrade_tools.py` | `TestRefusalTaxonomy` :4014 (`UPGRADE_TOKENS` :4019-4049; `test_documented_tokens_cover_the_exercised_matrix` :4079) | Taxonomy update |
| `tests/unit/tools/test_upgrade_tools.py` | `assert data.get("pending_op") is None` at :1722 and :2169; `test_live_refusals_never_mutate_pipeline` :3022; `test_replay_after_consume_refused` :2998 | Non-mutation verification |
| `tests/unit/tools/test_upgrade_journal.py` | `TestTornSafeRead` :180; `TestAtomicWriteKillSafety` :221; `TestUpdateFieldSemantics` :290 (`test_unknown_field_raises_keyerror` :291); `TestLockProtocol` :410; `TestLibShInterop` :1368 | Journal invariants |
| `tests/unit/tools/test_post_restart_arm_notify_journal.py` | `test_arm_pending_wake_writes_to_pending_wakes_key` :194; `test_ensure_extensions_adds_pending_wakes_default` :793; `test_ensure_extensions_preserves_existing_pending_wakes` :803 | ensure_extensions + arm_pending_wake shape |
| `tests/unit/tools/test_agent_restart.py` | :188-193 (T0c env-isolation pin shape; the allowlist asserts are at :192-193, the `EXECUTOR_ENV_PREFIXES` match at :200-203) | Boundary pin shape |
| `tests/unit/test_checkpoint_lane_docs_pins.py` | :65-85 (kubectl-ban pins) | Daemon-side string-ban shape |

### TO-VERIFY items (explicit commands)

| Item | Command | Why TO-VERIFY |
|---|---|---|
| `cn 2039b459` PACKS chain anchor | `grep -rn "2039b459" .agents/` | CLOSED — not a repo anchor; `git grep -n "2039b459" origin/latest` = 0 hits |
| Actual docstring block line for `preflight-argv-malformed` | `grep -n "preflight-argv-malformed" daemon/tools/upgrade_tools.py` | CLOSED — :4588 EXACT; `_full_doc_` block is :4527-4595 |
| Base commit for the shell-untouched pin if the implementation branch moves | `git merge-base HEAD origin/latest` | CLOSED — computed at test time, not a constant (m6) |
| `tests/unit/tools/test_upgrade_preflight_daemon_side.py` placement | `ls tests/unit/tools/test_upgrade_preflight_daemon_side.py` | EXISTS and the rejection is FACTUALLY CORRECT (it is the promote-preflight-hang module, Pack H), but the replacement path deviates from house convention (S1): the hermetic module lives under `tests/unit/tools/`, not `tests/unit/` (S1) |

### TO-VERIFY closures (this revision)

- (i) `cn 2039b459` is NOT a repo anchor (`git grep -n "2039b459" origin/latest` = 0 hits) — framing confirmed; closed as stated.
- (ii) `preflight-argv-malformed` is at `:4588` — EXACT; the `_full_doc_` block is `:4527-4595`; closed.
- (iii) The shell-untouched pin's base is `git merge-base HEAD origin/latest` computed at test time, NOT a constant (m6); a pytest unit test is the right home, a pack leg is NOT required; closed.
- (iv) `test_upgrade_preflight_daemon_side.py` EXISTS and the rejection is factually correct, but the replacement path deviates from convention (S1) — closed via S1 (hermetic module under `tests/unit/tools/`).

## Self-check against constraints 1-10

1. Every acceptance pin (a)-(f) maps to ≥1 named test (or explicit TO-VERIFY with command) — **yes** (see matrix above).
2. Every file proposed to change is either NEW or an explicitly listed line range; NO `promote.sh` / `lib.sh` edit — **yes** (T1-T7 touch only `daemon/tools/upgrade_db_preflight.py` (NEW), `daemon/tools/upgrade_journal.py`, `daemon/tools/upgrade_tools.py`, `tests/unit/tools/test_upgrade_db_preflight.py` (NEW), `tests/unit/tools/test_upgrade_tools.py`, `.agents/tester/PACKS.md`).
3. NO argv flag added; no `case` arm added to promote.sh; no `EXECUTOR_ENV_ALLOWLIST`/`PREFIXES` change — **yes** (T4 #5).
4. No dump leg, no `pg_dump`/`pg_dumpall`/`pg_restore`, no k8s object, no CronJob, no new DB table, no migration — **yes**.
5. The D8 disclosure text is present in plan-overview.md (arch §15 with five adjudicated corrections: C4 one-producer/one-publisher rewrite, C4 publisher-mechanism sentence, m7 lag+margin sentence, M2 non-live sentence, M3 privilege sentence) — **yes**; the C4 claim is TRUE and is NOT struck (the cycle-1 strike is WITHDRAWN — see decisions.md §DISCLOSURE CORRECTIONS).
6. The ledger in decisions.md matches the body of phase1-plan.md CHARACTER-FOR-CHARACTER on every predicate string — **verified 2026-11-26T17:3xZ by a mechanical diff** (the canonical blocks are EXTRACTED from decisions.md §PREDICATE LEDGER and counted in phase1-plan.md §CANONICAL PREDICATE STRINGS; the script is a text diff over the two .md files — no repo code is executed): **15 canonical blocks / 52 canonical lines, 0 MISMATCHES**; every whole block is present in the body byte-for-byte AND contiguous (body=1 for all 15). Breakdown — freshness-bound 2/2 (incl. the age-measurement basis `finished_at_age_s = (now_utc - finished_at).total_seconds()`), identity tuple + fingerprint 4/4, integrity 1/1, selection SQL 7/7 (block contiguous), skip 2/2 (incl. `identity_unchanged`), environment gate 3/3, seed value 1/1, refusal detail format 2/2, token family 8/8 (the family block body=1), journal-always row names 3/3, constants 3/3, D3 record shape 9/9 (block contiguous), mode gate 3/3, opt-in boundary 4/4, disclosure pointer 1/1 (decisions.md count / phase1-plan.md count). `db-preflight-identity-conflict` = 0 in the canonical family in BOTH docs (Deletion Record / Departures / T1 #5 "DROPPED" only). Token family = 8 in both docs.
7. The three files are committed docs-only on `feature/promote-db-preflight-validation`; the commit sha is reported in the delivery message. **RESOLVABLE ANCESTOR CHAIN:** `3d49b6063` (architect design of record) → `ab0aaefd6` (cycle-1 revision) → `482486fc7` (cycle-2 verify-and-repair) → **this commit** — every sha above is an ancestor of HEAD (`git merge-base --is-ancestor <sha> HEAD` = rc 0). HISTORICAL POINTER ONLY: `259e12d6` (the original plan delivery) is a **dangling pre-amend object, NOT an ancestor** — `git merge-base --is-ancestor 259e12d6 HEAD` = **rc 1**; the plan was re-created as `ab0aaefd6`. A self-check that names an unresolvable sha is a defect, so the ancestor sha is `ab0aaefd6`, not `259e12d6`.
8. Every code anchor cited in this plan was RE-VERIFIED at origin/latest `d7c15d45f` (measured 2026-11-26T17:2xZ: `git ls-remote origin refs/heads/latest` = `d7c15d45f65037844471c3d2ff2a06d4ed9afeec`; `git merge-base HEAD origin/latest` = `8b646ae37`; `git diff --stat 8b646ae37 d7c15d45f -- daemon/ scripts/` empty) — the anchor table is in the delivery report.

## Departures

See plan-overview.md §Departures for the full list: (a) live-refusal journal row (M4), (b) condition (6) + token drop (M1), (c) disclosure REWRITE (C4 — the claim is TRUE; the cycle-1 strike is WITHDRAWN), (d) live-gating (M2), (e) anchor corrections (incl. the `journal_open_txn` call-site/definition correction). The plan adopts D1-D8, F1, F2 as its baseline.

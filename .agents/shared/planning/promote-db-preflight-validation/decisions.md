# Decisions: Promote DB Preflight — backup-artifact validation leg

Date: 2026-11-26
Author: planner[v2] via plan-creation worker
Status: Draft (Ready for Review)
Worktree: `/home/nea/ensemble-src-wt-preflight-validation` — branch `feature/promote-db-preflight-validation` (arch design of record `3d49b6063`; original plan `259e12d6`; cycle-1 revision `ab0aaefd6`; cycle-2 revision = this commit)

## §PREDICATE LEDGER

One canonical string per predicate. The body of `phase1-plan.md` MUST match these character-for-character. Critical-note doctrine: a plan whose ledger asserts FIXED while its body still gates on the old predicate is a defect — body wins; diff body vs ledger.

### Freshness-bound predicate

```
finished_at_age_s = (now_utc - finished_at).total_seconds()
finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S (default 172800)
```
Measured on the artifact's OWN `finished_at`, never on `first_seen_at` / `last_seen_at`. Evaluated on EVERY invocation unconditionally. `DB_PREFLIGHT_MAX_AGE_S = _env_int("ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S", 172800)`.

### Identity tuple + fingerprint formula

```
identity = (lane, artifact, bytes, finished_at)
finished_at_utc = finished_at.astimezone(timezone.utc) if finished_at.tzinfo else finished_at.replace(tzinfo=timezone.utc)
finished_at_iso = finished_at_utc.isoformat().replace("+00:00", "Z")
fingerprint_sha256 = sha256(json.dumps([lane, artifact, bytes, finished_at_iso], separators=(",", ":")).encode()).hexdigest()
```

Notes (C3 adjudication): `sort_keys=True` is DROPPED — it is a no-op on a list (the tuple serialised as a JSON array). `finished_at` arrives from SQLAlchemy as a `datetime` for the timestamptz column and is not JSON-serialisable, so it is normalised to an ISO-8601 UTC string (`...Z`) first; microseconds are PRESERVED (never truncated). The normalised `<ISO-8601 Z>` is exactly what the D3 record shape's `finished_at` field requires, so the fingerprint and the persisted identity agree character-for-character.

### Integrity assertion

```
integrity_ok IS TRUE on the newest-overall row (selected by finished_at DESC, artifact DESC)
```
i.e. select the newest row OVERALL regardless of `integrity_ok`, then assert `integrity_ok = TRUE` on that row. Reading the VIEW would mask a broken newest artifact — forbidden.

### Selection predicate

```sql
SELECT lane, artifact, bytes, finished_at, integrity_ok,
       first_seen_at, last_seen_at
FROM public.dump_lane_artifacts
WHERE lane = :lane
ORDER BY finished_at DESC, artifact DESC
LIMIT 1
```
with `:lane` bound to `DB_PREFLIGHT_LANE = "pgdumpall"` (parameterised, never interpolated).

### Skip predicate

```
identity_unchanged ⇔ (lane, artifact, bytes, finished_at, fingerprint_sha256) == remembered
skip ⇔ (identity_unchanged AND finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S)
```
The comparison is made on the **NORMALISED** forms: `finished_at` is compared as `finished_at_iso` on BOTH sides (the remembered value is persisted as the `<ISO-8601 Z>` string per the D3 record shape, so a raw `datetime` — which never equals its own stored string — must never be compared to it directly); `bytes` compares as `int`. The age check is evaluated on every invocation; the skip may short-circuit ONLY the immutable checks (integrity, byte count) and NEVER the age check.

### Refusal-token family (one greppable kebab-case family)

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
All three naming the artifact (and the refusal token + age + bound for `db_preflight_refused`) in the detail string. The `refusal` event (via `_refusal` → `_journal_refusal_event`) is a SECONDARY trace — it SKIPS journaling when `self_env == "live"` (:1970-1979) — so the journal-always row `db_preflight_refused` is the AUTHORITATIVE audit trail for a live refusal.

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

`db-preflight-journal-torn` carries NO `db_preflight_refused` row at all (the journal is the unavailable substrate).

### Disclosure pointer

```
D8 disclosure text = arch §15 with five adjudicated corrections (C4 one-producer/one-publisher rewrite, C4 publisher-mechanism sentence, m7 lag+margin sentence, M2 non-live sentence, M3 privilege sentence); reproduced in plan-overview.md §"D8 disclosure text"
```

### Constants

```
DB_PREFLIGHT_LANE = "pgdumpall"
DB_PREFLIGHT_MAX_AGE_S = _env_int("ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S", 172800)
DB_PREFLIGHT_MODES = {"validation-only", "full-dump"}
```

### D3 record shape

Seeded placeholder (before any validation has run): `db_preflight = None` (mirrors `pending_op`/`pending_restart`, NOT the `pending_wakes` `{}` shape). Written value (after a validation or a skip — NEVER after a refusal; the D3 record shape has no `refused` verdict): ONE compact object:

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

The seeded placeholder is `None`; the written value is the compact object above. Both this ledger and T2 #1 use `None` character-for-character.

### Mode gate

```
mode == "full-dump"  ⇒ refuse db-preflight-full-dump-unavailable
mode not in DB_PREFLIGHT_MODES ⇒ refuse db-preflight-mode-unknown
mode is None/absent  ⇒ validation-only proceeds
```

### Opt-in boundary (D6)

```
ENSEMBLE_PROMOTE_DB_PREFLIGHT ∉ EXECUTOR_ENV_ALLOWLIST
ENSEMBLE_PROMOTE_DB_PREFLIGHT ∉ EXECUTOR_ENV_PREFIXES match
db_preflight ∉ _verified_arm_extras
db_preflight is NOT an argv flag (no promote.sh case arm)
```

## §ATTESTATION

This attestation names the changed rows and matches the ledger CHARACTER-FOR-CHARACTER. Body wins; the ledger is the canonical reference.

**Changed rows (implementation scope):**

| File | Change | Lines (re-verified at origin/latest `d7c15d45f` — measured 2026-11-26T17:2xZ via `git ls-remote`; `daemon/**` and `tests/**` anchors hold, no product-code change base..tip (`git diff --stat 8b646ae37 d7c15d45f -- daemon/ scripts/` empty); PACKS.md anchors re-derived at cut time) |
|---|---|---|
| `daemon/tools/upgrade_db_preflight.py` | NEW module | entire file |
| `daemon/tools/upgrade_journal.py` | `ensure_extensions` seeds `db_preflight` (7th key) | :769-811 (modify; `return data` at 811) |
| `daemon/tools/upgrade_journal.py` | `PendingOp` dataclass gains `db_preflight: str \| None = None` (C2) | :1564-1625 (modify; `to_json` :1627-1628 / `from_json` :1634 need NO change) |
| `daemon/tools/upgrade_journal.py` | MODULE docstring "Extensions this module may add" list brought to 7 keys (m4) | :44-50 (modify) |
| `daemon/tools/upgrade_journal.py` | new `record_db_preflight` helper | after :1853 (`arm_pending_wake` is :1825-1853) |
| `daemon/tools/upgrade_tools.py` | arm-preflight check inserted | between :4375 and :4376 (Step 0c header at :4376) |
| `daemon/tools/upgrade_tools.py` | `system_upgrade` signature gains `db_preflight` | decorator + signature :3609-3617 (narrow to :3611-3617 if excluding the decorator) |
| `daemon/tools/upgrade_tools.py` | dry-run PLAN string corrected | :3759-3761 |
| `daemon/tools/upgrade_tools.py` | dry-run/confirmation surfacing | :3763-3784 |
| `daemon/tools/upgrade_tools.py` | PendingOp capture | :4463-4477 |
| `daemon/tools/upgrade_tools.py` | `_full_doc_` names the eight tokens | :4527-4595 (verified; the `preflight-argv-malformed` mention is at :4588) |
| `tests/unit/tools/test_upgrade_db_preflight.py` | NEW hermetic module | entire file |
| `tests/unit/tools/test_upgrade_tools.py` | `UPGRADE_TOKENS` +8; new arm-path class | :4019-4050 (modify; closing brace at :4050); new class |
| `.agents/tester/PACKS.md` | new registration row | row added |

**Predicate strings asserted in phase1-plan.md body:** freshness-bound (incl. the age-measurement basis), identity tuple + fingerprint, integrity assertion, selection, skip (incl. `identity_unchanged`), refusal-token family, journal-always row names (incl. `db_preflight_refused`), refusal detail format, disclosure pointer, constants, D3 record shape, seed value, mode gate, environment gate, opt-in boundary. Each appears verbatim in both this ledger and the plan body. Diff check: body wins on any mismatch.

## §DISCLOSURE CORRECTIONS (D8 — what changed against arch §15, and on what evidence)

| # | Correction | Basis |
|---|---|---|
| 1 | **C4 — the "Two lanes exist" paragraph is REWRITTEN** to the one-producer/one-publisher truth | Read-only cluster verification (devops, 2026-11-26) PROVED: ONE producer refactored IN PLACE (`psql18-postgresql-pgdumpall` — the same CronJob object since 2026-08-08, same Helm labels, same PVC, `generation=5`; command ownership moved `helm` → `kubectl-patch` at 03:47Z 11-25 when the zstd script landed) plus ONE read-only publisher (`psql18-pgdumpall-heartbeat`, created 2026-11-25T20:37:37Z, schedule `30 0,6,12,18 * * *` Asia/Ho_Chi_Minh = 17:30Z/23:30Z/05:30Z/11:30Z, `backoffLimit=2`, digest-pinned image, READ-ONLY mount, "this job never creates, writes or deletes a file there"); the 60 Gi expansion at EVERY layer (PVC `spec.requests.storage`/`status.capacity` = 60 Gi, `phase=Bound`, created 2026-08-08T13:23:31Z; PV `pvc-19c2551d…` `spec.capacity` = 60 Gi, `driver.longhorn.io`, ext4, 3 replicas; Longhorn `spec.size` = 64,424,509,440 B, `actualSize` = 24,882,995,200 B, `state=detached`; in-filesystem `df -B1` size = 63,224,987,648 B); **NOT ENOSPC** (`df -B1` used/avail/size = 23:30Z 11-25 `23692828672/39515381760/63224987648` = 37.5 %; 05:30Z 11-26 `5741096960/57467113472/63224987648` = 9.1 %; 11:30Z 11-26 identical); the 2026-11-25 prune (pre-prune 95 regular files / 23,692,684,774 B → post-prune used 5,741,096,960 B; the residual ≈2,988,969,724 B matches the deliberately-kept `pg_dumpall-2026-08-31-17-00.pgdump` (2,988,907,270 B) within 62 KB ⇒ ONE uncompressed artifact remains BY DESIGN); retention ENABLED (`RETENTION_ENABLED="true"`, `KEEP_N_COMPRESSED=14`); producer GREEN (`lastScheduleTime=2026-11-25T17:00:00Z`, `lastSuccessfulTime=2026-11-25T21:52:58Z`, no `active` job, `suspend=false`, `concurrencyPolicy=Forbid`, `@daily` 17:00Z — four consecutive successes since the zstd refactor) |
| 2 | **C4 caveat (1) — the post-recovery SCHEDULED track record is ONE run** | `status.lastSuccessfulTime` (2026-11-25T21:52:58Z) is byte-identical to the MANUAL proof job's `completionTime`, while the last SCHEDULED success is 2026-11-25T17:04:41Z; the 2026-11-26T17:00Z scheduled run had not fired at verification time (13:22Z) ⇒ 3 manual + 1 scheduled. "green since 2026-11-25" MUST carry this qualifier |
| 3 | **C4 caveat (2) — the ORIGINAL 20 Gi is INFERRED, NOT RETAINED** | The expansion was TWO steps on 2026-11-25 (fs size 42,091,216,896 B = a 40 GiB volume observed at 20:42Z → 63,224,987,648 B = 60 GiB by 21:54Z) |
| 4 | **C4 — the `integrity_ok` claim is phrased "strongly indicated", NOT "observed"** | The read-only role cannot see the table (M3); the heartbeat view counts ONLY `integrity_ok` rows and reports 4, and the table holds 4 live rows |
| 5 | **C4 — the publisher-mechanism sentence** | `integrity_ok` is LITERALLY the publisher's `zstd -t -q "$f"` verdict (run on every new/changed artifact AND on the newest artifact every run — a prior verification is reused only for an unchanged non-newest artifact; D4's premise, confirmed at the source); the write shape is ONE transaction — `INSERT … ON CONFLICT (lane, artifact) DO UPDATE SET bytes=…, finished_at=…, integrity_ok=…, last_seen_at=now()`; it NEVER DELETEs and never writes the view ⇒ `last_seen_at` advances on EVERY publisher run (every 6 h) while `(bytes, finished_at, integrity_ok)` change only if the file changes ⇒ the identity tuple `(lane, artifact, bytes, finished_at)` is the STABLE one and the skip predicate must NEVER key on `last_seen_at` |
| 6 | **m7 — the publication-lag sentence and the REAL margin** | Measured lags `1h37m28s` / `3h43m13s` / `16h19m54s` / `16h56m03s`; the visible age sawtooths to ~41 h ⇒ the REAL margin against the 48 h bound is ~7 h |
| 7 | **M2 — the non-live sentence** | The check is gated on `self_env == "live"`; the registry lives in `ensemble_prod` and dev/demo/sandbox have no such relation, so a non-live check would ALWAYS refuse (live-gating is forced by the data topology). No `ENSEMBLE_PROMOTE_DB_PREFLIGHT_ENABLED` kill-switch is added — the direct shell path is already the documented escape (F1), and a kill-switch is a silent-downgrade vector |
| 8 | **M3 — the privilege sentence, WITH the measurement** | `has_table_privilege(ensemble_prod_ro, public.dump_lane_artifacts, SELECT)` = **False**; `has_table_privilege(ensemble_prod_ro, public.dump_lane_heartbeat, SELECT)` = **True**; a direct SELECT on the table raises `InsufficientPrivilegeError`; the only grant on the read-only role is `GRANT SELECT ON public.dump_lane_heartbeat TO ensemble_prod_ro`. The daemon's engine role `ensemble` OWNS the table, so the check works today; a role change is a hard blocker |

**WITHDRAWN — the cycle-1 strike of arch §15 ¶3.** The prior instruction to strike the clause "expanded to 60 Gi and recovered" is WITHDRAWN: that strike rested on the ABSENCE of a dump-PVC expansion on record and is contradicted by the measurement above. The clause STANDS (rewritten, not struck).

## §DELETION RECORD (withdrawn predicates)

Every withdrawn predicate is named here so no implementer resurrects it.

| Withdrawn predicate | Superseded by | Why withdrawn |
|---|---|---|
| The false dry-run PLAN string `"PLAN: pg_dump preflight → stop (SINGLE-TERM) → flip current→{version} → start → gate …"` (upgrade_tools.py:3759-3761) | A corrected string matching reality (no dump leg) | The string claims a dump leg that does not exist (forensic: `git log --all -S'pg_dump' -- scripts/upgrade/` = 0 commits) |
| Architect's withdrawn justification (i): "violates NO NETWORK FETCH (ADR-009 D3)" | The strengthened B1-B3 credential-boundary reasoning | False — lib.sh:68-69 is VCS-scoped ("no VCS pull/fetch/clone"); ADR-009 D3 is "no auto git pull"; the same ADR paragraph lists "promote VERSION=x (pg_dump preflight → …)" |
| Architect's withdrawn justification (ii): "atomicity without flock(1) is a shell-side problem" | The credential-boundary reasoning (atomicity was never a blocker) | False — `lock_acquire` (lib.sh:2951) is a POSIX-atomic `mkdir` acquire (lib.sh:15-17); `journal_write` is temp + `mv -f` |
| 36 h freshness bound proposal | 48 h (Q1) | 36 h blocks normal morning promotes on scanner lag alone (visible age ≈ 41 h worst case) |
| v1 D6 transport: `os.environ` (`ENSEMBLE_PROMOTE_DB_PREFLIGHT`) | Tool-call parameter `system_upgrade(..., db_preflight=...)` bound into PendingOp | `os.environ` is boot-time-only on this deployment (systemd zero-env contract; launcher.sh mints runtime env at spawn; config reads os.environ ONCE) — cannot express a per-promote opt-in |
| VIEW-based read (`public.dump_lane_artifacts` view) | TABLE read | The view filters `WHERE integrity_ok` and masks a broken newest artifact |
| `journal_update_field` for the new key | `ensure_extensions` → `journal_write` shape | `journal_update_field` raises `KeyError` for unknown fields ("new fields must be introduced via ensure_extensions") |
| Arch/citation Step-0c line numbers (:4381 / :4388) | Verified :4376 (semantic anchor authoritative) | Both were off at this commit; verified by direct read |
| Any `tests/shell_contract/`, `tests/shell_continuation/`, `test/shell_contract/`, `test/shell/` path | `tests/shell_continuation_scan.py` + `test/packs/shell_continuation_comment_lint_test.sh` | These directories DO NOT EXIST |
| `cn 2039b459` as a repo anchor | TO-VERIFY via `grep -rn "2039b459" .agents/` | Not a repo anchor; lives in the project DB |
| Stamp-and-compare belt (F2) | Validate at arm time, accept the seconds-wide window, disclose it | Requires a new shell-side comparison, a new refusal path in `promote.sh`, a second schema — a second failure surface and a change to the very file R7 wants untouched |
| Cluster-side dump Job (Q2) | Filed future option for the Emperor | Re-adds a per-promote dump the ruling removed; the PVC prune/expand it was waiting on is DONE (measured 2026-11-25/26 — see §DISCLOSURE CORRECTIONS), so the only remaining objection is the ruling itself |
| Direct-operator shell-side belt (Q3 alternative) | Arm-path only + disclosed | Shell has no DB credential path (B1-B3); could only check a daemon stamp (F2, rejected) |
| `tests/unit/tools/test_upgrade_preflight_daemon_side.py` as the home for the new db-preflight tests | `tests/unit/tools/test_upgrade_db_preflight.py` (hermetic) + `tests/unit/tools/test_upgrade_tools.py` (arm-path class) | That module is the promote-preflight-hang lane (Pack H), not a general arm-preflight home (S1) |
| The cycle-1 STRIKE of the disclosure clause "expanded to 60 Gi and recovered" (arch §15 ¶3) | The claim STANDS — rewritten as the one-producer/one-publisher paragraph with the measurements inline | The strike rested on the ABSENCE of a dump-PVC expansion on record and is WITHDRAWN: the read-only cluster verification of 2026-11-26 PROVED the 60 Gi expansion and the recovery at every layer, plus the 2026-11-25 prune and the green producer (C4 — see §DISCLOSURE CORRECTIONS) |
| Condition (6) `artifact name timestamp ≠ finished_at ⇒ db-preflight-identity-conflict` (and its token) | Dropped entirely (8-token family) | Unimplementable: no format/parse rule/tolerance; a name carries a second resolution while `finished_at` is sub-second ⇒ a literal inequality fires ALWAYS; the identity tuple already carries both the artifact name and `finished_at`, so any misattribution changes the identity and forces re-validation (M1) |
| `sort_keys=True` in the fingerprint formula | Dropped (no-op on a list) | `json.dumps` of a list ignores `sort_keys` (C3) |
| "every refusal path" in the journal-always row (incl. journal-torn) | "EVERY refusal path EXCEPT `db-preflight-journal-torn`" | The journal is the unavailable substrate on the torn path; no row is written (C1, M10) |
| The nine-token family | Eight-token family | Condition (6) + token dropped (M1) |
| PACKS anchors `:16` / `:3391` / `:3505` | `:202` / `:3593` / `:3707` / `:4173` | Re-verified at d7c15d45f; `:16` is the persona-neutrality heading, `:3391` is `e2e_workflows_ensure_test`, `:3505` is a 4-col legacy header (C5) |
| "arch §15 verbatim (six paragraphs)" claim | "arch §15 with five adjudicated corrections" | C4 one-producer/one-publisher rewrite, C4 publisher-mechanism sentence, m7 lag+margin sentence, M2 non-live sentence, M3 privilege sentence (cross-cutting) |

## §OPEN QUESTIONS FOR THE ORCHESTRATOR

None — the Emperor's ruling, the architect's rulings (D1-D8, F1, F2), and the leader's three adjudications (Q1/Q2/Q3) are all decided and adopted verbatim. The plan does not re-litigate them.

If the reviewer cannot confirm a path or test name cited in the matrix, the corresponding entry is marked TO-VERIFY with the exact command; an honest blocker is worth more than a plan that fails at review.

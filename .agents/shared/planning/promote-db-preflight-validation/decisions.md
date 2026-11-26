# Decisions: Promote DB Preflight — backup-artifact validation leg

Date: 2026-11-26
Author: planner[v2] via plan-creation worker
Status: Draft (Ready for Review)
Worktree: `/home/nea/ensemble-src-wt-preflight-validation` — branch `feature/promote-db-preflight-validation` (arch design of record `3d49b6063`; **review-cycle-1 revision** `ab0aaefd6`; intra-cycle verify-and-repair `482486fc7`; intra-cycle supersession banner + attribution `a419fc2c0` — its commit SUBJECT says "cycle-3", an immutable label that this ledger relabels as an INTRA-cycle revision, cycle-2 F18; **review-cycle-2 revision = this commit** — applies the cycle-2 review findings F1–F18). HISTORICAL POINTER ONLY: the original plan-delivery sha `259e12d6` is a **dangling pre-amend object, NOT an ancestor** of HEAD — `git merge-base --is-ancestor 259e12d6 HEAD` = rc 1; the plan was re-created as `ab0aaefd6`)

## §PREDICATE LEDGER

One canonical string per predicate. The body of `phase1-plan.md` MUST match these character-for-character. Critical-note doctrine: a plan whose ledger asserts FIXED while its body still gates on the old predicate is a defect — body wins; diff body vs ledger.

### Freshness-bound predicate (cycle-2 F13 — computed on the NORMALISED instant; a future-dated row is refused beyond a stated skew tolerance)

```
now_utc = datetime.now(timezone.utc)
raw_age_s = (now_utc - finished_at_utc).total_seconds()
raw_age_s < -DB_PREFLIGHT_CLOCK_SKEW_TOLERANCE_S (default 300) ⇒ refuse db-preflight-registry-unavailable
finished_at_age_s = max(raw_age_s, 0)
finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S (default 172800)
```
Measured on the artifact's OWN `finished_at` — normalised to `finished_at_utc`, the SAME normalisation the fingerprint applies (§Identity tuple), so the age and the identity can never disagree about the timezone — never on `first_seen_at` / `last_seen_at`. Evaluated on EVERY invocation unconditionally. **Clock-skew guard (cycle-2 F13):** a cluster/VM clock skew can place `finished_at` in the FUTURE, and a raw negative age would PASS `age <= bound`. A negative age WITHIN the tolerance (≤ 300 s) is CLAMPED to `0` for the bound comparison ONLY (the identity/fingerprint keep the raw normalised `finished_at_iso`); a negative age BEYOND the tolerance is REFUSED — fail-closed, and with NO new token (the family stays EIGHT): it maps to `db-preflight-registry-unavailable` (a row whose timestamp cannot be trusted is a registry-truth failure; artifact-less detail `token=db-preflight-registry-unavailable reason=finished_at in the future beyond skew tolerance (age=<negative>s tolerance=<s>s)`). `DB_PREFLIGHT_MAX_AGE_S = _env_int("ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S", 172800)`; `DB_PREFLIGHT_CLOCK_SKEW_TOLERANCE_S = 300` is a MODULE CONSTANT, not an env knob (no new operator surface). `_env_int` is MODULE-PRIVATE (`upgrade_journal.py:3752`) and is reached as `uj._env_int(...)` — see T1 #2.

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

### Skip predicate (D4 AMENDMENT — cycle-2 F1; evaluated on the FETCHED newest-overall row)

```
identity_unchanged ⇔ (lane, artifact, bytes, finished_at, fingerprint_sha256) == remembered
skip ⇔ (identity_unchanged AND finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S AND integrity_ok IS TRUE AND bytes > 0)
```
EVERY gate is evaluated on EVERY invocation — the registry-availability precondition, `integrity_ok`, `bytes > 0` and the freshness bound; "skip" ONLY selects the `skipped` verdict and limits the record mutation to `last_checked_at` + `verdict`. Nothing is short-circuited. The comparison is made on the **NORMALISED** forms: `finished_at` is compared as `finished_at_iso` on BOTH sides (the remembered value is persisted as the `<ISO-8601 Z>` string per the D3 record shape, so a raw `datetime` — which never equals its own stored string — must never be compared to it directly); `bytes` compares as `int`. **Why the amendment (cycle-2 F1):** `integrity_ok` is the ONLY registry field that can MOVE UNDER A FIXED identity tuple — the publisher's `INSERT … ON CONFLICT (lane, artifact) DO UPDATE SET bytes=…, finished_at=…, integrity_ok=…, last_seen_at=now()` re-verifies the NEWEST artifact on EVERY run, so it can flip `integrity_ok` to `false` with `(lane, artifact, bytes, finished_at)` UNCHANGED (same-size corruption, verifier regression, transient read fault). The former predicate (`identity_unchanged AND fresh`) would SKIP on exactly that row and let the arm proceed on a backup the lane's own verifier marks BROKEN — the silent-proceed R5 forbids. The row is ALREADY fetched to evaluate the identity, so the former short-circuit saved nothing. Mutation proof: `test_skip_does_not_fire_when_integrity_flips_false_with_unchanged_identity` (reintroduce the short-circuit ⇒ RED). This amends arch §5 `:168` ("may short-circuit only the immutable checks (integrity, byte count, name consistency)") — recorded in §DISCLOSURE CORRECTIONS; it is FAITHFUL to D4's stated intent ("the lane's own `zstd -t` verdict + bytes>0 + 48h freshness"), which the skip predicate simply failed to carry.

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
db_preflight_refused     # on every ARM-PATH refusal UNDER THE HELD LOCK except journal-torn (the journal is the unavailable substrate on that path — no row is written); written while the lock is still held, before release. Dry-run-time mode refusals write NO journal-always row (pre-lock; `_refusal` output only, consistent with the existing dry-run refusals at `upgrade_tools.py:3642-3714`) — cycle-2 F3
```
All three naming the artifact (and the refusal token + age + bound for `db_preflight_refused`) in the detail string. The `refusal` event (via `_refusal` → `_journal_refusal_event`) is a SECONDARY trace — it SKIPS journaling when `self_env == "live"` (:1970-1979) — so the journal-always row `db_preflight_refused` is the AUTHORITATIVE audit trail for a live refusal. The contract is counted by SITE, not by token (cycle-2 F3): the two mode tokens each have TWO refusal sites — dry-run (pre-lock, NO row) and arm (under the lock, row) — and only the arm-path site writes it.

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

`db-preflight-journal-torn` carries NO `db_preflight_refused` row at all (the journal is the unavailable substrate). **RACE-WINDOW-ONLY (cycle-2 F2):** an arm-entry torn journal NEVER reaches the new check — it is refused by the PRE-EXISTING `journal-unavailable` path (`upgrade_tools.py:3648-3655`, right after the `_journal_read` at `:3640`; BEFORE `if dry_run:` `:3738`, BEFORE the lock `:4046`, BEFORE the slot `:4375`; pre-lock, nonce unspent) — loud-failure holds via THAT token. `db-preflight-journal-torn` is reachable only when the journal tears (or is hand-edited) inside the `:3640→:4375` window, or when the check's own history write raises `JournalTorn`.

### Disclosure pointer

```
D8 disclosure text = arch §15 with five adjudicated corrections (C4 one-producer/one-publisher rewrite, C4 publisher-mechanism sentence, m7 lag+margin sentence, M2 non-live sentence, M3 privilege sentence); reproduced in plan-overview.md §"D8 disclosure text"
```

### Constants

```
DB_PREFLIGHT_LANE = "pgdumpall"
DB_PREFLIGHT_MAX_AGE_S = _env_int("ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S", 172800)
DB_PREFLIGHT_MODES = {"validation-only", "full-dump"}
DB_PREFLIGHT_CLOCK_SKEW_TOLERANCE_S = 300
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

The seeded placeholder is `None`; the written value is the compact object above. The record is written ONLY on a `validated`/`skipped` verdict — never on a refusal (the D3 record shape has no `refused` verdict) (m8). On a skip the mutation is LIMITED to `last_checked_at` + `verdict` (the validated tuple/fingerprint are left untouched — T3 #4). Both this ledger and T2 #1 use `None` character-for-character.

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
ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S ∉ EXECUTOR_ENV_ALLOWLIST
ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S ∉ EXECUTOR_ENV_PREFIXES match
db_preflight ∉ _verified_arm_extras
db_preflight is NOT an argv flag (no promote.sh case arm)
```
The first two lines are the **v1-transport REGRESSION GUARD** (cycle-2 F4): `ENSEMBLE_PROMOTE_DB_PREFLIGHT` is the WITHDRAWN v1 `os.environ` transport (§DELETION RECORD) — v2 never introduces it, so that pin cannot fail for any implementation of this plan; it is kept ONLY so a future resurrection of the v1 transport trips it. The two `MAX_AGE_S` lines pin the knob that DOES exist (Q1) — the daemon reads it at import; it must never cross the executor boundary. `_verified_arm_extras` (`upgrade_journal.py:2341-2358`) returns a FIXED `["--f2-verified-closed"]` plus a FIXED env dict with no field iteration, so `db_preflight` can reach the executor only by an explicit code change — pinned by `test_db_preflight_opt_in_not_in_verified_arm_extras` (on a VERIFIED op, baseline non-empty asserted — T4 #5).

## §ATTESTATION

This attestation names the changed rows and matches the ledger CHARACTER-FOR-CHARACTER. Body wins; the ledger is the canonical reference.

**Changed rows (implementation scope):**

| File | Change | Lines (RE-VERIFIED at origin/latest `44853186e` — measured 2026-11-26T19:4xZ via `git ls-remote origin refs/heads/latest` = `44853186ef756dca4d60b9f6ef34d5715a58bd1d` (the tip MOVED from `d7c15d45f`, which IS an ancestor; `git diff --stat d7c15d45f 44853186e` touches 36 files, NONE under `daemon/tools/upgrade_*`, `daemon/tools/ens_db_tools.py`, `daemon/__main__.py`, `scripts/upgrade/`, `tests/unit/tools/test_upgrade_*`, `tests/unit/tools/test_agent_restart.py`, `tests/unit/test_checkpoint_lane_docs_pins.py`, `test/packs/shell_continuation_*`, `docs/runbooks/upgrade-overhaul-watch.md` ⇒ every code anchor in this plan holds at the new tip; ONLY `.agents/tester/PACKS.md` moved among cited files — 5960 → 5980 lines — so its rows are cited by LEADING TEXT (POK `packs-cite-01-leading-text-anchors`), line numbers diagnostic only)) |
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
| `tests/unit/tools/test_upgrade_tools.py` | `UPGRADE_TOKENS` +8; new arm-path class `TestArmPreflightDbPreflight` | `:4019-4049` (modify; the set's closing brace is at `:4049` — `:4050` is `RESTART_TOKENS = {`; an insert "at :4050" lands INSIDE `RESTART_TOKENS`, cycle-2 F6); new class alongside `TestArmPreflightBeforeBurn` (`:3045`) |
| `.agents/tester/PACKS.md` | new registration row (under a canonical 5-col header; rows cited by OPENING CELL — POK `packs-cite-01-leading-text-anchors`) | row added |
| `docs/runbooks/upgrade-overhaul-watch.md` | new `§F.1c` Backup-artifact validation (Check 5) after `§F.1b` — token table, `db-preflight-journal-torn` marked RACE-WINDOW-ONLY (cycle-2 F2) | new section, docs-only |

**Predicate strings asserted in phase1-plan.md body:** freshness-bound (incl. the age-measurement basis), identity tuple + fingerprint, integrity assertion, selection, skip (incl. `identity_unchanged`), refusal-token family, journal-always row names (incl. `db_preflight_refused`), refusal detail format, disclosure pointer, constants, D3 record shape, seed value, mode gate, environment gate, opt-in boundary. Each appears verbatim in both this ledger and the plan body. Diff check: body wins on any mismatch.

## §DISCLOSURE CORRECTIONS (D8 — what changed against arch §15, and on what evidence)

### §SUPERSEDED TEXT — the architect's original sentences, VERBATIM, with their corrections

Source of every quotation below: the architect's design of record — `.agents/shared/planning/promote-db-preflight-validation/architecture-recommendation.md`, commit **`3d49b6063`**, §15 "DISCLOSURE (D8)" (heading at line 423; the superseded paragraph at line **429**; the §17 correction at line **472**). Quoted here so the delta is legible **without the arch artifact open**. **NOTHING in the arch artifact was edited by this plan** — it is the record of what was believed and when.

**S1 — arch §15 ¶3, quoted VERBATIM (line 429):**

> **Two lanes exist.** The registry lane (`pgdumpall`, compressed `.pgdump.zst`, `zstd -t`-verified, green since 2026-11-25) is **working**. The legacy PVC lane named in the original brief (uncompressed `pg_dumpall --clean`, 20 Gi, ENOSPC-failing since ~Sep 1) has since been **expanded to 60 Gi and recovered** — the brief's description of it is **out of date**. The ceremony validates the **registry lane**.

→ **CORRECTION (three deltas inside one sentence).** (a) **"Two lanes exist" is SUPERSEDED** — there is **ONE** producer (the same CronJob `psql18-postgresql-pgdumpall` since 2026-08-08, refactored IN PLACE when the zstd script landed at 03:47Z 2026-11-25) plus **ONE read-only publisher** (`psql18-pgdumpall-heartbeat`, created 2026-11-25T20:37:37Z) that writes the registry ROWS only; the registry lane and the PVC file lane are the SAME lane at two states, not two lanes. (b) **"green since 2026-11-25" is SUPERSEDED (qualified, not deleted)** — the post-recovery **SCHEDULED** track record is **ONE run** ⇒ 3 manual + 1 scheduled; see S2. (c) **"expanded to 60 Gi and recovered" is CONFIRMED TRUE — NOT struck**: the volume WAS expanded 40 → 60 GiB in two steps on 2026-11-25, the legacy set was pruned, retention is ENABLED. The cycle-1 instruction to strike this clause is **WITHDRAWN** — it rested on the ABSENCE of evidence. The "uncompressed `pg_dumpall --clean`, 20 Gi, ENOSPC-failing since ~Sep 1" description is the PRE-refactor state of the same producer, not a separate lane; and the original 20 Gi is **INFERRED, NOT RETAINED** in cluster state.
→ **EVIDENCE.** devops, read-only cluster verification, 2026-11-26 (second-hand to this planning lane): PVC `spec.requests.storage`/`status.capacity` = 60 Gi, `phase=Bound`; PV `pvc-19c2551d…` `spec.capacity` = 60 Gi, `driver.longhorn.io`, ext4, 3 replicas; Longhorn `spec.size` = 64,424,509,440 B, `actualSize` = 24,882,995,200 B, `state=detached`; `df -B1` size = 63,224,987,648 B; used 9.1 % at 11:30Z 2026-11-26; producer `lastScheduleTime=2026-11-25T17:00:00Z`, `lastSuccessfulTime=2026-11-25T21:52:58Z`, `suspend=false`, `concurrencyPolicy=Forbid`; expansion observed 42,091,216,896 B (40 GiB) at 20:42Z → 63,224,987,648 B (60 GiB) at 21:54Z. Corroborated by POK `pgdumpall-01-one-producer-one-publisher` (id `54625abe-66da-4f26-8aa4-add63aa96a94`; devops; observed; last verified 2026-11-26T13:27:24Z) and POK `pg-host-clockwork-landscape-01` (id `6e9f8041-285f-4c91-bace-92c13b059b10`; "dump PVC 20->40->60Gi expanded 2026-11-25, retention ENABLED, legacy set pruned, vol 9.1% used").

**S2 — the phrase "green since 2026-11-25", quoted VERBATIM (it occurs exactly ONCE in the arch artifact — line 429, inside S1):**

> green since 2026-11-25

→ **CORRECTION.** SUPERSEDED as an unqualified claim. The lane was green, but the post-recovery **SCHEDULED** track record is **ONE run**: the CronJob's `status.lastSuccessfulTime` (2026-11-25T21:52:58Z) is byte-identical to the MANUAL proof job's `completionTime`, while the last SCHEDULED success is 2026-11-25T17:04:41Z; the 2026-11-26T17:00Z scheduled run had not fired at verification time (13:22Z) and would be the first with retention armed ⇒ **3 manual + 1 scheduled**. The plan's D8 text therefore never asserts "green since 2026-11-25" without that qualifier.
→ **EVIDENCE.** devops, read-only cluster verification, 2026-11-26 — **second-hand to this planning lane and NOT recorded in any POK row**; it must be read from the verification receipt, not from the ledger.

**S3 — arch §17 "Corrections carried into this artifact", item 1, quoted VERBATIM (line 472):**

> 1. The brief's *"legacy PVC lane failing ENOSPC since ~Sep 1"* is **out of date** — POK `pg-host-clockwork-landscape-01` (corrected 2026-11-25) records the lane **green again**, PVC expanded 20→60 Gi, retention disabled.

→ **CORRECTION.** "retention disabled" is **SUPERSEDED**, and "20→60 Gi" is refined to "**20 → 40 → 60 Gi**" (two steps, 2026-11-25). The POK row the arch cites (id `99306ba8-0ca7-4591-bea3-8c0cc51deb2a`, "retention DISABLED so it re-fills ~0.9GiB/d") is **itself now superseded** by the current row `pg-host-clockwork-landscape-01` (id `6e9f8041-285f-4c91-bace-92c13b059b10`): "retention ENABLED, legacy set pruned, vol 9.1% used". The re-fill risk the arch flagged under §14 is therefore bounded by an ENABLED retention policy (`RETENTION_ENABLED="true"`, `KEEP_N_COMPRESSED=14`). **Coverage (cycle-2 F9):** the "retention disabled" wording lives at FOUR arch lines — `:195` (§5 ceremony consequence), `:419` (§14 "standing re-fill risk"), `:447` (§16 leader item (b)) and `:472` (§17 correction 1) — ALL FOUR are superseded; they are enumerated in §Errata beyond §15 below and in the plan-overview.md supersession banner.
→ **EVIDENCE.** POK lineage (current row + the two superseded ancestors, both quoted above), plus devops read-only cluster verification, 2026-11-26: `RETENTION_ENABLED="true"`, `KEEP_N_COMPRESSED=14`; `df -B1` used 23,692,828,672 B (37.5 %) at 23:30Z 2026-11-25 → 5,741,096,960 B (9.1 %) at 05:30Z 2026-11-26; pre-prune listing 95 regular files / 23,692,684,774 B; post-prune residual ≈2,988,969,724 B matches the deliberately-kept `pg_dumpall-2026-08-31-17-00.pgdump` (2,988,907,270 B) within 62 KB ⇒ ONE uncompressed artifact remains BY DESIGN.

### Per-correction basis (what changed against arch §15, and on what evidence)

| # | Correction | Basis |
|---|---|---|
| 1 | **C4 — the "Two lanes exist" paragraph is REWRITTEN** to the one-producer/one-publisher truth | Read-only cluster verification (devops, 2026-11-26) PROVED: ONE producer refactored IN PLACE (`psql18-postgresql-pgdumpall` — the same CronJob object since 2026-08-08, same Helm labels, same PVC, `generation=5`; command ownership moved `helm` → `kubectl-patch` at 03:47Z 11-25 when the zstd script landed) plus ONE read-only publisher (`psql18-pgdumpall-heartbeat`, created 2026-11-25T20:37:37Z, schedule `30 0,6,12,18 * * *` Asia/Ho_Chi_Minh = 17:30Z/23:30Z/05:30Z/11:30Z, `backoffLimit=2`, digest-pinned image, READ-ONLY mount, "this job never creates, writes or deletes a file there"); the 60 Gi expansion at EVERY layer (PVC `spec.requests.storage`/`status.capacity` = 60 Gi, `phase=Bound`, created 2026-08-08T13:23:31Z; PV `pvc-19c2551d…` `spec.capacity` = 60 Gi, `driver.longhorn.io`, ext4, 3 replicas; Longhorn `spec.size` = 64,424,509,440 B, `actualSize` = 24,882,995,200 B, `state=detached`; in-filesystem `df -B1` size = 63,224,987,648 B); **NOT ENOSPC** (`df -B1` used/avail/size = 23:30Z 11-25 `23692828672/39515381760/63224987648` = 37.5 %; 05:30Z 11-26 `5741096960/57467113472/63224987648` = 9.1 %; 11:30Z 11-26 identical); the 2026-11-25 prune (pre-prune 95 regular files / 23,692,684,774 B → post-prune used 5,741,096,960 B; the residual ≈2,988,969,724 B matches the deliberately-kept `pg_dumpall-2026-08-31-17-00.pgdump` (2,988,907,270 B) within 62 KB ⇒ ONE uncompressed artifact remains BY DESIGN); retention ENABLED (`RETENTION_ENABLED="true"`, `KEEP_N_COMPRESSED=14`); producer GREEN (`lastScheduleTime=2026-11-25T17:00:00Z`, `lastSuccessfulTime=2026-11-25T21:52:58Z`, no `active` job, `suspend=false`, `concurrencyPolicy=Forbid`, `@daily` 17:00Z — four consecutive successes since the zstd refactor) |
| 2 | **C4 caveat (1) — the post-recovery SCHEDULED track record is ONE run** | `status.lastSuccessfulTime` (2026-11-25T21:52:58Z) is byte-identical to the MANUAL proof job's `completionTime`, while the last SCHEDULED success is 2026-11-25T17:04:41Z; the 2026-11-26T17:00Z scheduled run had not fired at verification time (13:22Z) ⇒ 3 manual + 1 scheduled. "green since 2026-11-25" MUST carry this qualifier |
| 3 | **C4 caveat (2) — the ORIGINAL 20 Gi is INFERRED, NOT RETAINED** | The expansion was TWO steps on 2026-11-25 (fs size 42,091,216,896 B = a 40 GiB volume observed at 20:42Z → 63,224,987,648 B = 60 GiB by 21:54Z) |
| 4 | **C4 — the `integrity_ok` claim is phrased "strongly indicated", NOT "observed"** | The read-only role cannot see the table (M3); the heartbeat view counts ONLY `integrity_ok` rows and reports 4, and the table holds 4 live rows. **ATTRIBUTION:** devops, read-only cluster/registry verification, 2026-11-26 — second-hand to this planning lane |
| 5 | **C4 — the publisher-mechanism sentence** | `integrity_ok` is LITERALLY the publisher's `zstd -t -q "$f"` verdict (run on every new/changed artifact AND on the newest artifact every run — a prior verification is reused only for an unchanged non-newest artifact; D4's premise, confirmed at the source); the write shape is ONE transaction — `INSERT … ON CONFLICT (lane, artifact) DO UPDATE SET bytes=…, finished_at=…, integrity_ok=…, last_seen_at=now()`; it NEVER DELETEs and never writes the view ⇒ `last_seen_at` advances on EVERY publisher run (every 6 h) while `(bytes, finished_at, integrity_ok)` change only if the file changes ⇒ the identity tuple `(lane, artifact, bytes, finished_at)` is the STABLE one and the skip predicate must NEVER key on `last_seen_at` |
| 6 | **m7 — the publication-lag sentence and the REAL margin** | Measured lags `1h37m28s` / `3h43m13s` / `16h19m54s` / `16h56m03s`; the visible age sawtooths to ~41 h ⇒ the REAL margin against the 48 h bound is ~7 h. **ATTRIBUTION:** devops, from the lane's own publisher/registry cadence, 2026-11-26 — second-hand to this planning lane, and **not** recorded in any POK row |
| 7 | **M2 — the non-live sentence** | The check is gated on `self_env == "live"`; the registry lives in `ensemble_prod` and dev/demo/sandbox have no such relation, so a non-live check would ALWAYS refuse (live-gating is forced by the data topology). No `ENSEMBLE_PROMOTE_DB_PREFLIGHT_ENABLED` kill-switch is added — the direct shell path is already the documented escape (F1), and a kill-switch is a silent-downgrade vector |
| 8 | **M3 — the privilege sentence, WITH the measurement** | `has_table_privilege(ensemble_prod_ro, public.dump_lane_artifacts, SELECT)` = **False**; `has_table_privilege(ensemble_prod_ro, public.dump_lane_heartbeat, SELECT)` = **True**; a direct SELECT on the table raises `InsufficientPrivilegeError`; the only grant on the read-only role is `GRANT SELECT ON public.dump_lane_heartbeat TO ensemble_prod_ro`. The daemon's engine role `ensemble` OWNS the table, so the check works today; a role change is a hard blocker. **ATTRIBUTION:** devops, role-privilege probe on `ensemble_prod_ro`, 2026-11-26; POK `dump-lane-01-registry-shape` (id `f21ed5db-3dd1-44b0-818f-147b76649304`, last verified 2026-11-26T04:10:39Z) — second-hand to this planning lane; **no SQL was executed by this lane** |

### §D4 AMENDMENT — the skip predicate (cycle-2 review F1; amends arch §5 `:168`)

**Arch §5 `:168`, quoted VERBATIM:** "**Skip / bound interaction — the trap, closed:** the freshness bound is evaluated on **every** invocation, **unconditionally**. The identity-skip (R4) may short-circuit only the *immutable* checks (integrity, byte count, name consistency); it may **never** short-circuit the age check."

→ **AMENDED (the Emperor's ruling, cycle 2 — "I RULE THIS AS A D4 AMENDMENT. IT IS NOT OPTIONAL.").** The identity-skip may short-circuit NOTHING. The predicate becomes, on the FETCHED newest-overall row: `skip ⇔ (identity_unchanged AND finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S AND integrity_ok IS TRUE AND bytes > 0)`. EVERY gate is evaluated on EVERY invocation; "skip" ONLY selects the `skipped` verdict and limits the record mutation to `last_checked_at` + `verdict`.
→ **WHY.** (1) `integrity_ok` is the ONE registry field that can MUTATE IN PLACE without changing the identity tuple — this plan's own verified publisher mechanism proves it: `INSERT … ON CONFLICT (lane, artifact) DO UPDATE SET bytes=…, finished_at=…, integrity_ok=…, last_seen_at=now()`, and the publisher RE-VERIFIES the NEWEST artifact EVERY RUN ⇒ it can flip `integrity_ok` to `false` with `(lane, artifact, bytes, finished_at)` UNCHANGED (same-size corruption, verifier regression, transient read fault). Under the arch predicate the next promote sees identity unchanged + age ≤ 48 h ⇒ SKIP ⇒ the arm proceeds on a backup the lane's own verifier marks BROKEN — the silent-proceed R5 forbids — defeating pin (a)'s entire purpose. (2) The arch's label "the immutable checks (integrity, byte count, name consistency)" is a MISNOMER for this purpose: `bytes` and `finished_at` are IN the identity tuple (so the short-circuit was a no-op for them), name consistency is the dropped condition (6) (M1), and `integrity_ok` — the only field that moves under a fixed identity — is exactly the one being skipped. (3) The row is ALREADY fetched to evaluate the identity, so the short-circuit saved nothing.
→ **FAITHFUL TO D4.** D4's stated intent is "the lane's own `zstd -t` verdict + bytes>0 + 48h freshness on `finished_at`" (arch §5 and the five assertions) — the skip predicate simply failed to carry the verdict assertion into the skip branch. The amendment changes NO token, NO record shape, NO shell surface and NO executor boundary; it adds two conjuncts to one predicate and one mutation-proof test (pin (b): `test_skip_does_not_fire_when_integrity_flips_false_with_unchanged_identity`).
→ **WHERE IT IS PINNED.** This ledger (§Skip predicate), the body (phase1-plan.md §Skip predicate + T1 #7 + matrix (b)) and plan-overview.md D4 carry the identical predicate string character-for-character (mechanical diff — phase1-plan.md self-check #6).

### §Errata beyond §15 — arch lines OUTSIDE §15 that this plan supersedes (cycle-2 F8, F9)

The arch artifact was NOT edited (by design — it is the record of what was believed and when), and it carries no forward pointer by design; a fresh approver reading the arch outside §15 would therefore still carry these FALSE live risks. Each is enumerated here so the supersession is complete, not §15-local:

| Arch line | Verbatim fragment | Superseded by |
|---|---|---|
| §5 `:195` | "retention DISABLED so it re-fills ~0.9GiB/d" | retention ENABLED (`RETENTION_ENABLED="true"`, `KEEP_N_COMPRESSED=14`), legacy set pruned 2026-11-25 — S3 evidence above |
| §5 `:195` | "**Two** consecutive missed dailies would block" | **ONE** missed daily already opens a refusal window (cycle-2 F8 — Risk #8 in plan-overview.md): refusals run from +48 h after the last successful artifact until the next successful artifact PUBLISHES — ~26 min at the typical cadence (the 17:30Z publisher run sees the 17:00Z daily), up to ~17 h under the measured worst-case publication lag (`16h56m03s`) — even though the following daily succeeds; two consecutive misses extend the window by ~24 h |
| §14 `:419` | "The lane's PVC re-fill. POK … records "retention DISABLED so it re-fills ~0.9GiB/d" against a 20→60 Gi PVC — a standing re-fill risk." | NOT a standing risk: retention is ENABLED and the volume sat at 9.1 % used at 11:30Z 2026-11-26; the 20→60 Gi is refined to 20 → 40 → 60 Gi (two steps, 2026-11-25) |
| §16 `:447` | "(b) the lane's retention-disabled re-fill, which belongs to `ca665904`." | MOOT — retention is enabled; no leader item remains for it (the `artifact_sha256` item (a) is unchanged) |
| §17 `:472` | "retention disabled" | S3 above (retention ENABLED) |
| §5 `:168` | "may short-circuit only the immutable checks (integrity, byte count, name consistency)" | §D4 AMENDMENT above — NOTHING is short-circuited |

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
| VIEW-based read (the VIEW `public.dump_lane_heartbeat` — the relation the read-only role IS granted on; a view cannot share the table's name `public.dump_lane_artifacts` in the same schema — cycle-2 F7) | TABLE read | The view filters `WHERE integrity_ok` and masks a broken newest artifact |
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
| PACKS anchors by LINE NUMBER (`:16` / `:3391` / `:3505`, then `:202` / `:3593` / `:3707` / `:4173`, then `:3613` / `:3727` / `:4193` …) | ROW-LEADING-TEXT anchors — cite each target row's OPENING CELL verbatim (POK `packs-cite-01-leading-text-anchors`, id `e50f31c1-e18b-44e0-8e44-44840e785556`); line numbers are DIAGNOSTIC ONLY, valid only at a named freeze sha | Every inserted block renumbers PACKS.md: 5758 → 5960 (v0.31.61 review M2) → 5980 lines at `44853186e`; the interlock rows moved `:3593/:3707/:4173` → `:3613/:3727/:4193` between two measurements on the SAME DAY, and `:16` was the persona-neutrality heading, `:3391` was `e2e_workflows_ensure_test`, `:3505` a 4-col legacy header (C5 + cycle-2 supplement). Positional pointers rot by construction; leading text does not |
| The skip short-circuit `skip ⇔ (identity_unchanged AND finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S)` — and arch §5 `:168`'s "may short-circuit only the immutable checks" | `skip ⇔ (identity_unchanged AND finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S AND integrity_ok IS TRUE AND bytes > 0)` | `integrity_ok` is the only field that can move under a fixed identity; the short-circuit let a verifier-flipped-broken newest artifact SKIP (cycle-2 F1 — §D4 AMENDMENT) |
| Raw `finished_at` as the age basis, with no bound on a FUTURE `finished_at` | Age on `finished_at_utc`; negative age beyond 300 s ⇒ `db-preflight-registry-unavailable`, within ⇒ clamp to 0 | Cluster/VM clock skew yields a negative age that passes `age <= bound` (cycle-2 F13) |
| T3 #1a's former rationale — "the FIRST journal touch would otherwise swallow the torn condition and execution would continue" | Arm-entry torn ⇒ the PRE-EXISTING `journal-unavailable` (`:3648-3655`); `db-preflight-journal-torn` is RACE-WINDOW-ONLY | MEASURED: `_journal_read` `:3640` → `_refusal("journal-unavailable")` `:3648-3655` fires before dry-run `:3738`, lock `:4046`, slot `:4375` — the torn condition is never swallowed. The probe is KEPT as defence-in-depth for the `:3640→:4375` window (cycle-2 F2) |
| "Every refusal path writes the journal-always row" read as "every refusal SITE" | Every ARM-PATH refusal under the held lock except torn; dry-run-time mode refusals write NO row | The dry-run branch `:3738-3790` holds NO lock (nonce minted `:3767`, stored `:3773`; `journal_lock_acquire` is `:4046`) — an unlocked append would race the live holder's read-modify-write, a lost UPDATE (cycle-2 F3) |
| "arch §15 verbatim (six paragraphs)" claim | "arch §15 with five adjudicated corrections" | C4 one-producer/one-publisher rewrite, C4 publisher-mechanism sentence, m7 lag+margin sentence, M2 non-live sentence, M3 privilege sentence (cross-cutting) |

## §OPEN QUESTIONS FOR THE ORCHESTRATOR

None — the Emperor's ruling, the architect's rulings (D1-D8, F1, F2), and the leader's three adjudications (Q1/Q2/Q3) are all decided and adopted verbatim. The plan does not re-litigate them.

If the reviewer cannot confirm a path or test name cited in the matrix, the corresponding entry is marked TO-VERIFY with the exact command; an honest blocker is worth more than a plan that fails at review.

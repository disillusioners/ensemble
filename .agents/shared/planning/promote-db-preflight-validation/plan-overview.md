# Plan Overview: Promote DB Preflight — backup-artifact validation leg

Date: 2026-11-26
Author: planner[v2] via plan-creation worker
Status: Draft (Ready for Review)
Worktree: `/home/nea/ensemble-src-wt-preflight-validation` — branch `feature/promote-db-preflight-validation` @ 3d49b6063
Anchor base: product base `8b646ae37` (v0.31.60); tip has moved to `d7c15d45f`. The `daemon/**` and `tests/**` anchors hold (no product-code change base..tip — verified: `git diff --stat 8b646ae37 d7c15d45f -- daemon/ scripts/` is empty); PACKS.md anchors are re-derived at cut time.

## Objective

Add a daemon-tool-lane preflight check to the `system_upgrade` arm path that **validates the newest daily backup artifact** (from the cluster-side dump lane) by reading the lane's own registry metadata over the sanctioned PostgreSQL channel — instead of dumping the live database per promote. The check remembers the validated identity in a new top-level key `db_preflight` in `releases/state.json`, skips re-validation when the identity is unchanged and fresh, and refuses loudly (with eight distinct kebab-case tokens) when the registry relation is absent, no backup exists, the newest artifact is broken/empty/stale, the journal is torn, a `full-dump` opt-in is requested, or the mode is unknown. The shell ceremony (`promote.sh` / `lib.sh`) is **not touched at all**; the guarantee attaches to the daemon-armed ceremony and is disclosed as such.

## The Emperor's ruling (verbatim, arch §1)

> "the postgres on K8S with longhorn will have daily backup (not dump every promote). ceremony's preflight is only a validation: yes, this correct, don't waste more resource just to backup before each promote, we in rapid development so each day may have lot of new version, backup everytime is slow and waste. Also don't need to validation all the time, we backup one a day, so check the lastest backup, remember it, if that backup already checked, skip."

## Scope

### In Scope

- A new daemon-side module `daemon/tools/upgrade_db_preflight.py` holding the registry read, the D2 selection rule, the D4 bound, the identity/fingerprint, the skip predicate, the five gates, and the mode gate.
- A new arm-preflight check in `daemon/tools/upgrade_tools.py`, inserted **between the `stale-scripts-dir` refusal block and the Step 0c comment header** (verified anchor :4376), under the held lock, before the nonce burn.
- A new top-level key `db_preflight` seeded by `ensure_extensions` in `daemon/tools/upgrade_journal.py`, plus a `record_db_preflight` helper modelled on `arm_pending_wake`.
- The `system_upgrade` signature gains `db_preflight: str | None = None`; the value is surfaced in the dry-run/confirmation text and captured into the `PendingOp`.
- A corrected dry-run PLAN string (the false `PLAN: pg_dump preflight → stop (SINGLE-TERM) → flip …` at :3759-3761).
- The refusal-token taxonomy update (`UPGRADE_TOKENS` in the test module + the `system_upgrade._full_doc_` docstring).
- New tests: a hermetic module `tests/unit/tools/test_upgrade_db_preflight.py`, a new arm-path class in `tests/unit/tools/test_upgrade_tools.py`, the env-isolation pin, the shell-untouched pin, and the C-1 lint run.
- A `PACKS.md` registration row.

### Out of Scope (NON-GOALS — do not violate)

- **No `promote.sh` / `lib.sh` edit** (D7/R7). The shell preflight is untouched BY CONSTRUCTION — the new leg lives entirely in the daemon tool lane.
- **No new argv flag** to `promote.sh` (constraint 2). Check 3 (`upgrade_tools.py:4177-4200`, token `preflight-argv-malformed`) requires every argv flag to have a `case` arm in `promote.sh` — a new flag would force a `promote.sh` edit and violate D7. D6's opt-in is a tool-call parameter that never crosses the executor boundary.
- **No DB dump leg.** No `pg_dump` / `pg_dumpall` / `pg_restore` anywhere in the implementation.
- **No Kubernetes object, no CronJob change, no PVC mutation.** No cluster-side dump Job (Q2 — filed future option for the Emperor).
- **No new DB table and no migration.** D3 = a new top-level key `db_preflight` in `releases/state.json` (file-backed JSON; `shared_meta_kv` is tree-scoped; `app_settings` is registry-gated with a ≤10-row budget).
- **No promote / stage / release.** No k8s, no live mutate.
- **No refactor** of the 3-factor gate, the nonce TTL, the lock discipline, the journal schema, or any token in the preserve-list (arch §12: `promote.sh:211-655`, `lock_acquire` `lib.sh:2951`, `journal_open_txn` `lib.sh:660-671`, `journal_update` `lib.sh:1774-1803`, `journal_path` `lib.sh:1537`, `_refuse` `lib.sh:10932-10951`, `JOURNAL_SIZE_GUARD_BYTES` `lib.sh:1587`, and the whole daemon-side arm-path list in arch §12).
- **No stamp-and-compare belt** (F2 rejected). No shell-side comparison, no second schema.
- **No cluster-side dump Job.** The cluster-side one-shot Job stays a filed future option for the Emperor (needs the PVC prune/expand first; re-adds a per-promote dump the ruling removed).

## Architecture rulings adopted (verbatim, arch §3-§9)

- **D1 — WHERE THE VALIDATION LIVES:** the DAEMON TOOL LANE — a new check in the `system_upgrade` arm preflight, immediately after the scripts-provenance guard closes and immediately before the Step 0c comment header. Downstream of the 3-factor live gate and every busy/cooldown/lock refusal; under the held lock; precedes the nonce burn and the `pending_op` write; upstream of the executor spawn (gated on a matching durable `pending_op`, so a daemon-side refusal halts the ceremony entirely).
- **D2 — HOW "THE LATEST BACKUP" IS IDENTIFIED:** the newest row overall for the lane, by `finished_at DESC, artifact DESC`, then assert `integrity_ok` on that row. Read the TABLE, not the view (the view filters `WHERE integrity_ok` and would MASK a broken newest artifact). Identity tuple `(lane, artifact, bytes, finished_at)` + a `fingerprint_sha256` over the canonicalised tuple. Lane value constant `DB_PREFLIGHT_LANE = "pgdumpall"`.
- **D3 — WHERE THE REMEMBERED IDENTITY LIVES:** a new top-level key `db_preflight` in `releases/state.json` (`journal_path = install_dir / "releases" / "state.json"`), the seventh key added through `ensure_extensions`. One compact object (current-value slot), not an unbounded history.
- **D4 — WHAT "CHEAP AND MEANINGFUL" MEANS:** the lane's own verdict + non-empty bytes + age within bound + the newest-overall artifact being verified. Five assertions, one SELECT. Freshness bound 48 h (172800 s) measured on `finished_at`, env-configurable via `ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S`. The bound is evaluated on EVERY invocation — the identity-skip may short-circuit only the immutable checks, never the age check.
- **D5 — WHAT STALENESS DOES:** staleness FAILS the promote loudly. Nine distinct refusal tokens (one greppable family). No new exit code is claimed (the house's 0/75/76/78 vocabulary belongs to `scripts/audit/dump_lane_staleness.py`, a repo-only probe).
- **D6 — THE R6 OPT-IN:** the opt-in cannot mean "dump from the ceremony" — a VM-side full dump is impossible on five independent grounds. `full-dump` is recognised and refused loudly with `db-preflight-full-dump-unavailable`; an unrecognised value is refused (`db-preflight-mode-unknown`). The opt-in is a tool-call parameter `system_upgrade(..., db_preflight=...)`, bound into the `PendingOp`, never crosses the executor boundary, never appears in `EXECUTOR_ENV_ALLOWLIST` / `EXECUTOR_ENV_PREFIXES`, never becomes an argv flag, never requires a `promote.sh` `case` arm. Check 3 is unaffected.
- **D7 — THE SHELL PREFLIGHT IS UNTOUCHED BY CONSTRUCTION:** the new leg is daemon-side only.
- **D8 — THE DISCLOSURE:** the text in §"D8 disclosure text" below (arch §15 with four adjudicated corrections).
- **F1 — THE DIRECT-OPERATOR GAP:** arm-path only + disclosed. A deliberate direct `ENSEMBLE_UPGRADE_LIVE=1 bash promote.sh live` bypasses the validation entirely.
- **F2 — THE TOCTOU:** do NOT build the stamp-and-compare belt. Validate at arm time, accept the seconds-wide window, disclose it.

## Leader's adjudications (Q1/Q2/Q3 — decided, build to these)

- **Q1 BOUND:** default 48 h on the artifact's `finished_at`, env-configurable (`ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S`) so it can be tightened in production without a code change (a `.env` edit + daemon restart; the knob is a daemon-level risk-appetite setting, NOT a per-promote attested parameter — contrast D6's opt-in which MUST be a tool-call parameter). The ceremony MUST DISCLOSE the effective data-loss age (the age of the restore point it relies on) in its output.
- **Q2 CLUSTER-SIDE DUMP JOB:** NOT built in this commission. The lane ships validation-only. No k8s object, CronJob change, or dump leg.
- **Q3 DIRECT-OPERATOR PATH:** arm-path only + disclosed. The shell has no DB credential path (B1-B3), so a shell-side belt could not read the registry at all; it could only check a daemon stamp, which is F2 (rejected). Reasoning and disclosure stated, not silently omitted.

## Anchor base and re-anchor rule

- Product base: `8b646ae37` (v0.31.60). Tip at read time: `d7c15d45f` (6 intervening commits are test/packs only). The `daemon/**` and `tests/**` anchors hold (no product-code change base..tip — verified: `git diff --stat 8b646ae37 d7c15d45f -- daemon/ scripts/` is empty); PACKS.md anchors are re-derived at cut time.
- The implementation branch will be cut from `origin/latest` AFTER approval, so every anchor must be marked with its base commit and re-verifiable there. The implementation tasks anchor on **semantic markers** (comment headers, function names, refusal blocks) plus the verified line numbers, never line numbers alone.
- **Anchor correction recorded (decisions.md §DELETION RECORD):** the architect artifact cites Step 0c at `:4381` and the commission says `:4388`; the verified value at 3d49b6063 / d7c15d45f is **`:4376`** (the `# ── Step 0c — arm-time journalctl capture (AC-OBS-1 / C-7) ────` comment header). The semantic anchor is authoritative: "immediately after the `stale-scripts-dir` refusal block closes, immediately before the Step 0c comment header".

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Registry relation is externally owned with no repo contract — absent as recently as 2026-11-25; a schema change or drop blocks every promote | High | Medium | Fail loud with named token `db-preflight-registry-unavailable`; absence is never green |
| 2 | Metadata-only guarantee — a publisher whose `zstd -t` is buggy or whose row is written before the artifact is durable would pass a broken backup | Medium | Low | Disclosed verbatim (D8); integrity is the lane's own verdict, not bytes |
| 3 | Direct-operator bypass (F1) — a deliberate direct shell invocation receives no validation | Medium | Low (operator override) | Disclosed; arm-path only |
| 4 | Publication lag creates a residual window — a backup that finished but is not yet published is invisible; the bound upper-bounds the true data-loss window | Medium | Medium | Bound = 48 h; residual window disclosed; skip identity changes when the newer artifact publishes, so the next promote re-validates |
| 5 | Registry DB at 99.7 % of its 4 GiB cgroup — a new connection may be refused where an established one survives | High | Low | Reuse the pooled connection (`manager.engine`); server-side `statement_timeout` as the real cap |
| 6 | Live-refusal-journal carve-out — `_refusal` SKIPS journaling when `self_env == "live"`; the journal-always row is the only audit trail for a live refusal | Medium | Certain (by design) | The new check writes a journal-ALWAYS row (`db_preflight_validated` / `db_preflight_skipped` / `db_preflight_refused`) on validated, skipped AND refused outcomes (sibling shape at `upgrade_tools.py:4233-4247`), written while the lock is still held; on refusal it lands BEFORE `journal_lock_release` |
| 7 | Externally-owned relation absence on first run — every promote blocks until the publisher is green | High | Medium | Fail loud; documented in disclosure; not a silent green |
| 8 | Two consecutive missed dailies block the ceremony — intended behaviour but operationally surprising | Medium | Low | Disclosed; two missed dailies mean the safety net is genuinely absent |

## D8 disclosure text (arch §15 with four adjudicated corrections: C4 struck clause, m7 lag sentence, M2 non-live sentence, M3 privilege sentence)

> **Backup validation at promote time is a metadata validation, not a byte-level one.** The ceremony reads the dump lane's own registry over the sanctioned PostgreSQL channel and asserts that the **newest artifact row** for lane `pgdumpall` carries the publisher's `integrity_ok = true` (a cluster-side `zstd -t` verdict), a **non-zero** byte count, and a `finished_at` within the **48-hour** freshness bound, measured on the artifact's own completion time — never on `first_seen_at`/`last_seen_at`, which measure the scanner's cadence rather than the backup's age. **The bytes are never re-read, checksummed, or restored by the ceremony**; a corrupted or truncated artifact that the publisher marked verified **will pass**. The registry publishes **no checksum**, so the remembered identity is `(lane, artifact, bytes, finished_at)` plus a fingerprint over that tuple.
>
> **The live database's dumpability at promote time is no longer verified by the ceremony.** The daily backup covers that risk instead. **A promote may now happen against a database whose last backup is up to ~48 hours old** — a rollback to that backup can lose that much data. Staleness **fails the promote loudly**; it is never a warning.
>
> **Two lanes exist.** The registry lane (`pgdumpall`, compressed `.pgdump.zst`, `zstd -t`-verified, green since 2026-11-25) is **working**. The legacy PVC lane named in the original brief (uncompressed `pg_dumpall --clean`, 20 Gi, ENOSPC-failing since ~Sep 1) is **not validated by this ceremony**; its current capacity state was **not verified in this lane** (the only 60 Gi figure on record is the POSTGRES DATA volume `pvc-770d03c4`, a different object). The ceremony validates the **registry lane**.
>
> **The registry is externally owned.** The relation has **no repo contract** (zero references in the tree), no migration, and no publisher-side implementation in the repo — it was **absent on 2026-11-25**. Its schema and its `integrity_ok` semantics are the cluster publisher's. **Its absence fails the preflight loudly; it is never green.**
>
> **The guarantee attaches to the daemon-armed ceremony.** A deliberate direct `ENSEMBLE_UPGRADE_LIVE=1 bash scripts/upgrade/promote.sh live` invocation bypasses the validation entirely — the shell has no DB credential path, and a VM-side dump is impossible on five independent grounds (no dump leg exists in any commit; the PVC is not mounted; the promote child has no credential path; new connections are refused at the 99.7 % cgroup ceiling; the VM's `pg_dump` 16.15 aborts against the 18.4 server). The `full-dump` opt-in is therefore shipped as an **explicit refusal** (`db-preflight-full-dump-unavailable`), never a silent fallback. The only real dump capability is **cluster-side** and is the Emperor's decision.
>
> **Live-gating (M2).** The check is gated on `self_env == "live"`: the registry lives in `ensemble_prod`, and dev/demo/sandbox have no such relation, so a non-live environment skips the check entirely (no refusal, no block). There is **no** `ENSEMBLE_PROMOTE_DB_PREFLIGHT_ENABLED` kill-switch — the direct shell path is already the documented escape (F1), and a kill-switch would be a silent-downgrade vector of exactly the kind this lane exists to prevent.

> **Privilege (M3).** The TABLE read requires TABLE-level SELECT on `public.dump_lane_artifacts`, which the daemon's owner role `ensemble` holds. The view `public.dump_lane_heartbeat` is readable by `ensemble_prod_ro`, but the **table is not** — a role change is a hard blocker, surfaced as `db-preflight-registry-unavailable`.

> **Publication lag (m7).** The measured lags are `1h37m28s`, `3h43m13s`, `16h19m54s`, `16h56m03s`; the visible age sawtooths to ~41 h. The real margin against the 48 h bound is ~7 h.

> **A seconds-wide window** exists between the arm-time validation and the executor run. It is accepted and disclosed: the property re-checked (artifact freshness) has an hour-scale bound, and the shell's preflight is **not** modified to close it.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Implement the validation leg | New module + arm-preflight check + journal key + helper + signature + taxonomy + tests + PACKS row | 7 task families (T1-T7) | tight (shared journal lock, journal schema) | pending |

## Coupling Map

| | Phase 1 |
|---|---|
| Phase 1 | — |

(Single phase — the leg is small and surgical; decomposition below is by task family, not by phase.)

## Risks (summary)

See the Risks table above. Critical-path risks: #1 (registry relation absent blocks every promote) and #5 (cgroup ceiling ⇒ pooled connection mandatory).

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | The check runs under the held lock, after the scripts-provenance guard, before the nonce burn | Structural ordering test (`TestArmPreflightBeforeBurn` ordering anchor) | Pass |
| 2 | A registry whose newest overall artifact has `integrity_ok=false` while an older verified artifact exists ⇒ refuses `db-preflight-artifact-broken` naming the newest | Hermetic test in `tests/unit/tools/test_upgrade_db_preflight.py` | Pass (mutation-proven: reintroduce view-based read ⇒ RED) |
| 3 | Two consecutive invocations with an unchanged, fresh artifact ⇒ second records `verdict="skipped"`, `last_checked_at` advances, `db_preflight_skipped` row appended naming the artifact | Hermetic test | Pass |
| 4 | Same unchanged artifact aged past the bound ⇒ skip does NOT fire, refuses `db-preflight-artifact-stale` | Hermetic test | Pass |
| 5 | Zero rows ⇒ `db-preflight-no-backup`; relation absent ⇒ `db-preflight-registry-unavailable`; torn journal ⇒ `db-preflight-journal-torn`; all leave nonce unspent and no `pending_op` | Hermetic + arm-path tests | Pass |
| 6 | `full-dump` ⇒ `db-preflight-full-dump-unavailable`; unrecognised ⇒ `db-preflight-mode-unknown`; absent ⇒ validation-only proceeds | Hermetic + arm-path tests | Pass |
| 7 | `ENSEMBLE_PROMOTE_DB_PREFLIGHT` ∉ `EXECUTOR_ENV_ALLOWLIST` and ∉ `EXECUTOR_ENV_PREFIXES` match | Env-isolation pin (T0c shape) | Pass |
| 8 | `promote.sh` and `lib.sh` byte-unchanged (path-scoped diff against base) | New pin test | Pass |
| 9 | Refusal at the new check leaves nonce unspent and no `pending_op` | Arm-path test | Pass |
| 10 | Record round-trips a simulated restart; `ensure_extensions` seeds the key; ADR-034 round-trip holds | Hermetic test | Pass |
| 11 | C-1 lint clean (no new shell continuation occurrences) | `tests/shell_continuation_scan.py` exit 0 | Pass |
| 12 | All eight refusal tokens appear in `UPGRADE_TOKENS` and as `reason=<token>` in `system_upgrade._full_doc_` | `test_documented_tokens_cover_the_exercised_matrix` | Pass |

## Research Insights

- The registry is a SCANNER's view of the volume, not the writer's record; `first_seen_at`/`last_seen_at` are bulk re-touches — freshness MUST be measured on `finished_at` (standing POK rule `backup-freshness-on-finished-at-not-seen-at`).
- Publication lag measured: 1h37m28s, 3h43m13s, 16h19m54s, 16h56m03s — a 36 h bound would block normal morning promotes on scanner lag alone; 48 h is the smallest bound that does not.
- The view `WHERE integrity_ok` filters the newest broken artifact — reading the TABLE is mandatory; this is the single most important technical point.
- The pooled engine (`manager.engine`) survives the 99.7 % cgroup ceiling where new connections are refused; the read must reuse it (house precedent `ens_db_tools.py:779-830`).
- `journal_update_field` raises `KeyError` for unknown fields ("new fields must be introduced via ensure_extensions") — the new key MUST be seeded via `ensure_extensions` and written via the `arm_pending_wake` shape.
- The `_refusal` carve-out skips journaling on live env — the journal-always row is the only audit trail; the sibling pattern at `upgrade_tools.py:4233-4237` is the template.
- `system_upgrade` is a closure inside `create_upgrade_tools(manager, current_instance_id, agent_id, agent_tag)` — `manager.engine` is directly accessible.
- No daemon-side reader of `dump_lane_artifacts` / `dump_lane_heartbeat` exists (zero `dump_lane` hits under `daemon/`). The repo-only probe `scripts/audit/dump_lane_staleness.py` queries only the heartbeat view and is NOT shipped in the release payload.

## Open Questions

None — the Emperor's ruling, the architect's rulings (D1-D8, F1, F2), and the leader's three adjudications (Q1/Q2/Q3) are all decided and adopted verbatim. The plan does not re-litigate them.

## Departures

The plan adopts D1-D8, F1, F2 as its baseline; the following are recorded departures / refinements, each with its precedent:

- **(a) Live-refusal journal row (M4).** The arm-preflight check writes a journal-always row `db_preflight_refused` on every refusal path while the lock is still held. This departs from the "live refusals are journal-read-only" contract (upgrade_tools.py:1971-1979; `test_live_refusals_never_mutate_pipeline` is the enforced acceptance), and is defensible via the `arm-scripts-provenance` sibling (:4232-4237, written on every arm before any refusal). A test asserts the new row does not regress `test_live_refusals_never_mutate_pipeline`'s invariant for refusals that fire BEFORE the lock.
- **(b) Condition (6) + token drop (M1).** Departs from arch D2's name-vs-column refusal: condition (6) (`artifact name timestamp ≠ finished_at ⇒ db-preflight-identity-conflict`) and its token are dropped entirely; the identity tuple already carries both the artifact name and `finished_at`, so any misattribution changes the identity and forces re-validation. Family becomes EIGHT tokens.
- **(c) Disclosure correction (C4).** Departs from arch §15's text: the "expanded to 60 Gi and recovered" clause is struck and reworded to "not validated by this ceremony; current capacity state not verified in this lane."
- **(d) Live-gating (M2).** A scoping refinement: the check is gated on `self_env == "live"`; dev/demo/sandbox skip it. No kill-switch is added.
- **(e) Anchor corrections.** Step 0c line number (arch :4381 / commission :4388 ⇒ verified :4376); `ensure_extensions` :769-811; `arm_pending_wake` :1825-1853; `system_upgrade` signature decorator+signature :3609-3617.

See decisions.md §DELETION RECORD for the withdrawn-predicate detail.

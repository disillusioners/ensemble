# Phase 4 — Runbook Verification (T4.5 deliverable)

> Date: 2026-09-04 (UTC) | v2 HEAD: `a1ae0f91` (post-T4.4 cherry-pick pair)
> Branch: `feature/langgraph-checkpoint-perf-v2`
> Source: v1 `f89ccacc` (base runbook) + v1 `7a7998fe` (race disclosure fold)
> Port method: cherry-pick pair (clean add for `f89ccacc`; `7a7998fe` modifies §7)

## Acceptance

| Plan §T4.5 acceptance | Status |
|----------------------|--------|
| Runbook exists | ✓ `docs/runbooks/checkpoint-blob-prune-restore.md` (224 lines, byte-identical to v1 `fc908945`) |
| All 7 sections present | ✓ (see table below) |
| Format matches v2 conventions | ✓ (no v2 conventions.md exists; format matches the sibling `docs/runbooks/upgrade-drills.md` style: `# Title` + Component / Code owners / Risk class header + structured PRE-ENABLE CHECKLIST + ROLLBACK) |
| Result recorded | ✓ this file |

## 7 sections verification (per C-19)

| Plan § | Topic | Runbook location | Verified |
|--------|-------|------------------|----------|
| §1 | pre-enable checklist | `## PRE-ENABLE CHECKLIST` (line 35) — full 7-item checklist `[ ] 1 … [ ] 7` | ✓ line 35 |
| §2 | prod `channel_versions` JSONB shape verification query (per FR-11) | `### [ ] 2. Verify the ACTUAL \`channel_versions\` shape in PROD (§36-style layout check — MANDATORY)` (line 63) — 2 SQL queries: `jsonb_pretty(...)` shape check + reader-relation round-trip | ✓ lines 63-103 |
| §3 | destructive flip gate | `### [ ] 7. Flip the ladder (only after 1–6 are ALL green)` (line 153) + `export CHECKPOINT_BLOB_PRUNE_DRY_RUN=0; export CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` (lines 193-197) + "restart the daemon with both vars in its environment" | ✓ lines 153-201 |
| §4 | backup-as-recovery of record | `## ROLLBACK (post-enable breakage)` (line 205) — steps 1-5: unset both flags, restore from backup, count-verify, liveness-verify, drop backup | ✓ lines 205-224 |
| §5 | idle-gate precondition | §7 disclosure paragraph: "The idle gate is a PRECONDITION, not a lock: it makes this overlap rare, not impossible." (line 174) — also referenced in §6's "≥ 7 days at the 15-min cadence" (line 105) | ✓ line 174 |
| §6 | backup covers recovery | `### [ ] 6. Snapshot PROD \`checkpoint_blobs\` BEFORE the first destructive cycle` (line 142) — `CREATE TABLE checkpoint_blobs_prune1_backup AS SELECT * FROM checkpoint_blobs;` + sanity row-count + hold ≥ 7 days | ✓ lines 142-151 |
| §7 | intra-process race disclosure | §7 paragraph (lines 163-191) — cites `langgraph-checkpoint-postgres` `aio.py:82`, `aio.py:280-304`, `aio.py:393-399`; "Honest scope of this rule" + "Residual intra-process race disclosure (PR4 external review, 2026-08-26)." + "DB-level hardening shipped with this disclosure" — the entire retract-and-disclose block from v1 `7a7998fe` | ✓ lines 163-191 |

## Verbatim verification

| Source | v2 (post-cherry-pick) | Diff |
|--------|----------------------|------|
| v1 `fc908945:docs/runbooks/checkpoint-blob-prune-restore.md` (224 lines) | `docs/runbooks/checkpoint-blob-prune-restore.md` (224 lines) | `diff -q`: BYTE-IDENTICAL (no output = identical) |

## Verbatim §7 (race disclosure) sanity-check

The §7 block (lines 163-191) contains the full retract-and-disclose text from `7a7998fe`. Key sentences verified:

- ✓ "**Honest scope of this rule:** it mitigates the CROSS-process variant of the race. It does NOT by itself eliminate the intra-process window described below — that window exists even with exactly one daemon process."
- ✓ "**Residual intra-process race disclosure (PR4 external review, 2026-08-26).**"
- ✓ "DEFAULT `AsyncPostgresSaver` path on PG14+ (psycopg autocommit + pipeline) commits each `aput`'s blob upsert and checkpoint upsert as SEPARATE implicit transactions"
- ✓ Citations: `aio.py:82`, `aio.py:280-304`, `aio.py:393-399` (all three present)
- ✓ "DB-level hardening shipped with this disclosure" — references `daemon/checkpoint_adapter.py::delete_blobs_anti_join` + `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES`
- ✓ "Verified empirically on PG 14.22 that this abort-and-retry works when SSI's two-edge condition holds."
- ✓ "Equally verified: a lone READ COMMITTED aput racing the DELETE does NOT trip SSI"
- ✓ "Do not arm destructive without it."

## Verbatim §2 (channel_versions verification) sanity-check

The §2 block (lines 63-103) contains both SQL queries verbatim:

Query 1 (line 67-72): `jsonb_pretty(checkpoint->'channel_versions')` shape check with LIMIT 5
Query 2 (line 78-93): reader-relation round-trip (correlated subquery on `checkpoint_blobs` with `(channel, version)` resolution)

Both queries are READ-ONLY SELECTs — no destructive operations. Safe for runbook §2 verification at D-1 time (Phase 5 T5.9).

## Format conformance

- ✓ H1 `# Runbook: ...`
- ✓ Component / Code owners / Risk class header lines (matching v1 source verbatim)
- ✓ Mode table (`## How the ladder works`)
- ✓ Numbered checklist items (`### [ ] 1. ...` through `### [ ] 7. ...`)
- ✓ ROLLBACK section (`## ROLLBACK (post-enable breakage)`)
- ✓ SQL code fences (```sql … ```)
- ✓ Bash code fences (```bash … ```)
- ✓ No external-image or media references (portable across UI surfaces)
- ✓ No protected-path references (no `.agents/approver/active.md`, no `QUARANTINE.md`)

## Pre-merge readiness

The runbook is operator-facing — it documents the contract between "PR4 ships" and "destructive arm flipped". Per C-19 + FR-8, the formal re-review of the SERIALIZABLE wrap + retraction + race tests will happen in Phase 5 T5.7 (a reviewer instance dispatched against the artifact), not here. The Phase 4 work is the LANDING of the runbook + its code counterpart; the re-review is Phase 5.

**Phase 4 T4.5 complete.**
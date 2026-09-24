# Independent Staging Verification Report: v0.14.1 — release/v0.14.1 @ 4cb9b8a3
Date: 2026-09-24
Branch: release/v0.14.1 (bump `4cb9b8a3` + CHANGELOG `02ab698f` + base `a79aba7a`)
Staged artifact: /tmp/ens-sbx-v0.14.1/releases/v0.14.1/ (sandbox install dir, fence-compliant)
Method: read-only git/file/manifest inspection + non-booting binary version smoke + sha256/mtime recompute. No fixes; no commits beyond this artifact; no live/demo contact.

## FINAL VERDICT: **READY-TO-TAG — YES**, with **one release-plan decision surfaced** (item 6 lineage wrinkle).

| # | Item | Verdict |
|---|---|---|
| 1 | Bump commit shape (`4cb9b8a3` vs `6ed47fca` mirror) | ✅ PASS — 4-file set identical (md5-equal); every version site reads `0.14.1` |
| 2 | CHANGELOG accuracy + overclaim scan (02ab698f) | ✅ PASS — both merges covered; exactly the four user-visible fixes; no overclaim (settled-shape uniformity NOT claimed; instrumentation propagation gap honestly disclosed) |
| 3 | Layout parity (staged vs v0.14.0 ref) | ✅ PASS — 6 top-level items identical; agents/ 304=304; frontend/ 68=68; zero one-side-only files |
| 4 | Manifest verbatim fields | ✅ PASS — `version="v0.14.1"`, `binary_version="0.14.1"`, `rollback_safe=true`, `known_schema_gen="20260915_212810_create_service_tracking.sql"` **byte-identical to v0.14.0** (no-drift proof), `contains_contract_phase=true`, all 6 checksum fields present (5 flat + 2 per-file maps) |
| 5 | Binary freshness + integrity | ✅ PASS — recomputed `4e220a37c74f63da6bc9cece6704a5bd0e5e438995736a384fea022832a84ea4` == manifest `binary_sha256` == repo `dist/ensemble-prod`; binary mtime post-dates bump by 246s |
| 6 | `rollback_safe` semantics | ⚠️ **PASS-WITH-FINDING** — script-blessed override (stage.sh:172-179 + post-write integrity_verify); semantically honest for zero-drift pair; **BUT** on-disk v0.14.0 manifests (demo + live install dirs) are `rollback_safe=false` (default-derived), so rollback v0.14.1→v0.14.0 REFUSES on the target flag — decision needed |
| 7 | Schema gate (migrations/persistence/factory) | ✅ PASS — empty diffs across all three paths; `known_schema_gen` identical to v0.14.0; no alembic anywhere in range |
| 8 | Tag/push/tree hygiene | ✅ PASS — no `v0.14.1` tag local or remote; both release commits unpushed; tracked tree clean (only 5 known untracked `.agents` scratch files) |
| 9 | Version smoke (non-booting) | ✅ PASS — binary printed `Starting Ensemble v0.14.1` verbatim before sqlite migration preflight fail (pre-existing v0.14.0-era PG-only defect, rc=3); no port bind; 9797/7979/8088 untouched; POSTGRES scrub effective |
| 10 | Sandbox durability note | ✅ informational — `/tmp` is throwaway; rebuildable from tagged source via validated pipeline |

## Item 6 FINDING (release-plan decision — for sign-off before giter tags)

The task brief expected: *"v0.14.0 manifest's rollback_safe should also be true with the same override lineage"* — **on-disk reality differs**:
- `~/agents-ensemble-demo/releases/v0.14.0/manifest.json`: `rollback_safe=false`, `contains_contract_phase=true`
- `~/agents-ensemble/releases/v0.14.0/manifest.json`: `rollback_safe=false` (byte-identical payload)
- Both reflect default-derived false (destructive DROPs in `daemon/migrations/versions/` ⇒ `contains_contract_phase=true`); no `ENSEMBLE_ROLLBACK_SAFE` override at stage time.

The staged v0.14.1 was staged WITH `ENSEMBLE_ROLLBACK_SAFE=1` (manifest = `true`) — script-blessed at stage.sh:172-179, integrity-verified at stage.sh:299/376. Semantically honest (zero schema drift ⇒ v0.14.1→v0.14.0 rollback is code-only).

**Consumer impact (read from scripts/upgrade/rollback.sh:98-101 + promote.sh:330-336):**
- Manual rollback `v0.14.1 → v0.14.0`: **REFUSES** because the **target's** manifest (`v0.14.0`) says `rollback_safe=false`. The new flag does not enable the rollback net.
- Failed v0.14.1 promote auto-rollback: **halts-for-human** because the **PREVIOUS** release (`v0.14.0`) is `rollback_safe=false`. v0.14.1 promote itself is NOT blocked.

**Options for sign-off:**
- **X (status-quo)**: restage v0.14.1 with default `rollback_safe=false` — posture unchanged from v0.14.0; rollback net remains closed identically.
- **Y (current staged)**: keep v0.14.1 `rollback_safe=true` — honest about code-rollback safety, but the net stays closed by v0.14.0's flag.
- **Z (deepest)**: restage v0.14.0 with override + re-anchor journal — out of scope for a patch-release gate.

## Minor informational findings (non-blocking)

- **Reference path**: task brief's `/home/nea/ensemble-src/releases/v0.14.0/` doesn't exist in-repo (the `releases/` tree lives in INSTALL dirs only). Worker used the demo install-dir reference; parity conclusions stand.
- **Dispatch literal typo**: task brief's expected sha256 `…fea022832832a84ea4` was 67 chars (impossible — duplicated `83` segment). True digest is 64-hex `…fea022832a84ea4`; artifact integrity intact.
- **`version` field shape**: manifest `version="v0.14.1"` (v-prefixed) — pipeline convention, identical to v0.14.0's `"v0.14.0"`.
- **Smoke log additive**: ~10 lines appended to `/home/nea/ensemble-src/data/logs/ensemble.log` from the version-smoke run (CWD-relative daemon logging) — additive only, no corruption.

## Safety ledger
- No live (9797) / demo (7979) / self (8088) contact throughout — verified byte-stable pre/post every operation.
- Ambient-LIVE POSTGRES scrubbed and verified-empty pre-smoke; smoke fail-fast at sqlite migration (pre-existing, not new) prevented any DB connection.
- 9797 (pid 819745), 7979 (pid 800677) socket owners byte-unchanged before and after smoke.
- No commits beyond this artifact; tracked tree clean (5 known untracked `.agents` scratch files only).
- Evidence preserved: `/tmp/ens-sbx-v0.14.1/` (sandbox artifact) + `/tmp/e2eg-*-{baseline,post}.txt` snapshots.
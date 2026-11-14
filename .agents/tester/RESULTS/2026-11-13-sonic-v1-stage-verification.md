# ENSEMBLE-31 — Stage Verification v0.29.23 (artifact-only; tester lane addendum to 2026-11-13-sonic-v1-gate.md)

- **Date:** 2026-11-12T23:1xZ · **Executor:** stage-verify worker (reads-only; no boots; umask 077; logs `/tmp/stageverify/`)
- **Release:** `/home/nea/agents-ensemble/releases/v0.29.23` · tag `v0.29.23` (object `0ade29c3c…`, peeled `3db6ba97e…` == `origin/latest`) · staged_at 2026-11-12T23:03:37Z (manifest)
- **VERDICT: ✅ PASS — promote-ready from the tester lane (promote remains the Emperor's ceremony).**

## Legs
| Leg | Result | Evidence |
|---|---|---|
| S1 4-way sha | PASS | **all four legs identical** `ff14fdcbaa2234b296b89710a09cec12077a927e64ad0793626ebcd01dbadfde` (release binary == stage dist == manifest.binary_sha256 == sidecar.artifact_sha256; independently re-hashed); sidecar `git_head=3db6ba97e…` == tag peel, `git_dirty=False`, `describe --exact-match`=v0.29.23 |
| S2 prompt-baseline | PASS | `--check` vs RELEASE tree **rc=0, 197/197**; 4 sonic meta `id` asserts ok; ari rule.md 3/3 normalized anchors + heading + plane count 1; **identity short-circuit: `diff -r` release-agents ≡ gated-tree-agents → empty** |
| S3 manifest/tag/deltas | PASS | `v0.29.23 / 0.29.23 / rollback_safe=True / known_schema_gen==migration head (20261112_124200_add_snapshot_agent_space_key.sql)`; **migrations delta ac9849bf7..v0.29.23 = 0**; daemon delta = exactly `daemon/__init__.py` version-string hunk (0.29.22→0.29.23) — **adjudicated semantically-content-only** (sanctioned bump mechanism); scripts delta = 0 |
| S3f AC-P4-6 | PASS | `readlink current` = **releases/v0.29.22 unchanged**; release dirs = 4 (v0.29.20–23; over-retention vs keep-3, recorded) |
| S3g install .env | PASS | `ENSEMBLE_SELF_ENV=live` count=1; mode 600 (count-grep only; file never read) |
| S5 steady-team integrity | PASS | content diff vs `git archive ac9849bf7:agents` = **exactly the 6 expected lines** (4 × `Only in …: sonic-*`, `ari/rule.md` differ, `.prompt-artifacts.sha256` differ); zero unexpected |
| S3h receipt token line | NOT-FOUND (by design) | `stage.sh:927` emits the line to stdout only — nothing persists it (`releases/state.json` journals promotes, not stages; its history ends at the v0.29.22 promote; `in_flight`/`pending_op` null). Every FACT the line asserts is independently artifact-verified (rollback_safe, contains_contract_phase, known_schema_gen, staged_at, NO-flip via unchanged `current`); only the `source=explicit=true` clause (stage-time env) is not artifact-verifiable |

## Disclosures
1. **Staged-binary boot NOT exercised** (artifact-only choice): content-only release with 0-code delta (daemon delta = version string only), agent content is filesystem dir-scan data verified ≡ gated tree, binary integrity proven by 4-way sha + provenance re-hash; booting the staged binary would resolve the LIVE install root and risk promote-journal/nonce writes against the ceremony — information gain ~zero.
2. Receipt stdout line not durable (S3h) — future stage verifications should verify via manifest + sidecar + symlink (KB recorded).
3. RAM-guard ambient telemetry on 2 git-heavy calls (refused kills only; anomaly `e924b44d`) — consistent with the staged v0.29.21 guard profile; no effect on results.

— tester (Test Leader), ENSEMBLE-31 stage-verification lane

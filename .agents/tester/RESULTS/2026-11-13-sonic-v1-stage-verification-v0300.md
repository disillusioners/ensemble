# ENSEMBLE-31 — Stage Verification v0.30.0 (artifact-only; supersedes the v0.29.23 verification as the ceremony candidate)

- **Date:** 2026-11-13T04:42–04:46Z · reads-only · no boots · logs `/tmp/stageverify-v0300/` (12 files)
- **Release:** `/home/nea/agents-ensemble/releases/v0.30.0` · tag `v0.30.0` (object `3a2444804…`, peeled `bb4daa59…` == `origin/latest`) · staged_at 2026-11-13T04:35:03Z (manifest)
- **VERDICT: ✅ PASS on all legs — promote-ready; promote is the Emperor's ceremony.**

| Leg | Result |
|---|---|
| S1 4-way sha | PASS — all four legs `322e1370…73c` (release == dist == manifest == sidecar); sidecar `git_head=bb4daa59…` == tag peel == origin/latest; `git_dirty=False` |
| S2 baseline | PASS — `--check` rc=0, 197/197; ari anchors 3/3 (ws-normalized; raw anchor-3 wraps — normalization load-bearing), heading ×1, plane count 1 |
| **S2c pins** | PASS — RELEASE tree: leader=agentic · **coder=coding** · reviewer=agentic · **tester=coding** |
| **S2d gap-fix** | PASS — coder: 3-line HANDOFF-commit mandate PRESENT; leader: bullet (d) prior-commission pointer + §5 commit-envelope INCLUDES `HANDOFF.md` — both grep-verbatim in `$REL` |
| S2f identity | PASS — `$REL/agents` ≡ stage-wt agents (empty diff) and ≡ pins-wt agents (no drift) |
| S3 manifest/deltas | PASS — `v0.30.0/0.30.0/rollback_safe=True/known_schema_gen==migration head`; migrations `ac9849bf7..v0.30.0`=0 AND tag-to-tag `v0.29.23..v0.30.0`=0; daemon delta = exactly `daemon/__init__.py` `__version__` line (reads 0.29.22→0.30.0 across the two-bump range; tag-to-tag shows the clean 0.29.23→0.30.0 4-file bump set); scripts delta 0; tag-to-tag 13 files fully decomposed (bump 4 + amend 5 + docs 3, nothing unexpected) |
| S3e AC-P4-6 | PASS — `readlink current` = **releases/v0.29.22 UNCHANGED**; release dirs = 5 (v0.29.20/21/22/23/30.0 — over-retention disclosure stands) |
| S3f .env | PASS — marker count 1, mode 600 (count-grep only) |
| S5 steady-team | PASS — exactly the 6 expected diff lines; zero unexpected |

**Disclosures:** staged-binary boot NOT exercised (artifact-only, same justification as v0.29.23: content-only release, 0-code delta, agent content = dir-scan filesystem data ≡ gated/pins tree; 4-way sha + provenance carry integrity; the pin re-proof dry-run already booted this exact tree content end-to-end with runtime model attribution). Receipt token line stdout-only by design (facts corroborated via manifest + unchanged `current`). RAM-guard ambient telemetry only (nothing killed).

— tester (Test Leader), ENSEMBLE-31 v0.30.0 stage-verification lane

# Test Report: agent-base-v2 migration — INDEPENDENT VERIFICATION

Date: 2026-10-13
Instance IDs (verification workers): b276adab (A static-git) · 2732691c (B base==v2) · 2a61e553 (C gates) · f8457105 (D scrubber) · 35ca80b9 (E drift) · dfc93c4a (F flakes) · 1b693d94 (G coder) · e7d16e3f (H R1/R9)
Branch: feature/agent-base-v2 @ HEAD 6307612ea92bd90f8c650c516e9af6c49b1c7163 (base 885a19d4f)
Verifier stance: every proof re-run from scratch; committed evidence never trusted blindly.

## OVERALL: READY-TO-MERGE

All 8 binding constraints PASS (two with documented, adjudicated deviations that make the tree *stricter* than the constraint text assumed). Zero merge-blocking findings.

## Per-constraint verdicts

| # | Constraint | Verdict | One-line evidence |
|---|---|---|---|
| 1 | v2 zero-touch | ✅ PASS | `git diff 885a19d4f..HEAD -- ':(literal)agents/{developer,reviewer,tidier,approver,planner}[v2]'` = 0 lines; `git status --porcelain` same = 0; pathspecs proven non-vacuous (dirs exist; vacuous char-class form also recorded = 0); corroborated by B: fresh v2 manifests == committed post-mission manifests (0 diff × 5), v2 pre⇔post empty |
| 2 | base==v2 | ✅ PASS | Fresh manifests (shasum -a 256), canonical sed recipe v2→base normalize: 0 diff lines on ALL 5 pairs (binding required ≥2); base→v2 cross-direction also 0 on developer+reviewer |
| 3 | migrations append-only | ✅ PASS | `git diff --stat 885a19d4f..HEAD -- daemon/migrations/` = 0 lines; full diff = 0 |
| 4 | conftest.py:443 zero-touch | ✅ PASS | `git diff 885a19d4f..HEAD -- tests/conftest.py` = 0 lines; 0 commits touched it — whole file untouched, :443 fixture included |
| 5 | R10 v5 opencode-free gate | ✅ PASS (gate of record) | Literal 48-pathspec Step 13 command → 15 hits / 2 files, byte-identical to committed `r10-gate-output.txt` (which honestly recorded them); v5 = verbatim + `:(exclude).gitignore` + `:(exclude)tests/unit/test_wanderer_agent.py` → independently re-run: **0 matches** (rc=1); both additions carry justified allowlist rows (pre-declared intentional-keep + string-is-the-assertion negative-guard classes); case-sensitive supplement also green; no post-10210c74f commits touched the 2 hit files |
| 6 | secret-scan gate + scrubber | ✅ PASS | `evidence/secret-scan.sh` re-run: exit 0, `SECRET-SCAN: CLEAN`, 12/12 targets scanned, 0 skips; `_scrubbed_env()` (test_empty_guard_checkpoint_roundtrip.py:114, families :124-131) filters `BLUEPRINT_EMBEDDING_API_KEY` ✓; poisoned run output contains 0 `VERIFYPROBE` fragments ✓ (also 0 dummy-pw fragments). DEVIATION (favoring security): `POSTGRES_PASSWORD` is ALSO scrubbed by design (whole `POSTGRES_*` family — docstring: interactive-shell env must never leak into pg_ctl/initdb); the constraint's "survivor" expectation was wrong, the tree is stricter |
| 7 | test wall (focused, no full re-run) | ✅ PASS | (a) 18 drift ids: 5 spot-runs → 3 FAIL with pure content-drift assertions (team_members `['coder','worker']`≠`['explorer']`; planner tools.allow lacks `subtree_messages`; bare `rule.md`/`soul.md` tokens), 2 PASS serially; opencode-grep = 0 in all logs; classification files have proven SET EQUALITY with the raw wall log (353+18+3 = 374). (b) 3 flakes all PASS single serial re-run (0.41s/1.09s/0.69s). (c) coder guards 39/39 PASS (1.55s). (d) `tests/test_tool_filter.py` 55/55 green (R1 sites clean); `tests/test_innate_skills_refactoring.py` 12/13 — the single :75 failure is pre-existing non-opencode drift (leader meta gained `'question'` via 07eacda1a; pin expects older 3-skill list) |
| 8 | fresh-user equivalence | ✅ PASS | (a) 5 base dirs = exact v2 inventory + content: counts 9/8/11/14/10 = 52/52, comm-3 identical, committed base-vs-v2 diffs all 0 bytes, digests verified. (b) Census: 38 untagged (34 base + 4 infra `_baby_template/_inner_soul/_mother/_prompt_system`) + 5 tagged `[v2]` = 43 total; five pairs present exactly once. (c) .gitignore: `evidence/*.log` covered by global `*.log` (:54, check-ignore rc=0; full-wall-xdist.log correctly untracked+ignored); DEVIATION: no `legacy` ignore rule exists — legacy/ is TRACKED-by-commit (68 files, Phase A 97f3ad43c) which is the correct mechanism for an archive that must survive the merge; intent met |

## Guardrail item — touch-up count discrepancy: RESOLVED
True unique FAILED∪ERROR universe at HEAD's wall evidence = **374** (= 353 pre-existing + 18 drift + 3 flakes; set-equality against raw log proven). The log's own headline **344 is FAILED-only** (its 25 errors sit on a separate summary line: 25 test-error ids + 5 pytest logging-plugin `ERROR daemon.*` records). `summary.md`'s denominator note is arithmetically consistent and honest. Precision nit: the ~5-id gap is logging-plugin records, not "xdist output normalization"; test-id-only universe would be 369.

## Findings (non-blocking)

### Hygiene notes (fold before merge ceremony, optional)
1. 🟠 2 dirty tracked files in the worktree — `.agents/tidier/notes.md` (+11 lines, 2026-10-12 tidy-pass from another commission) and `.agents/shared/planning/designer-agent/install-audit.jsonl` (+100 lines, KMS audit events; `secret_ref` = KMS_HANDLE refs, no plaintext; secret-scan CLEAN). These are the executor-documented "2 foreign runtime writes, inspected, NOT git-added". Decide: commit or restore before the merge ceremony (never `git add -A`).
2. 🟢 6 tracked `.log` files under `evidence/phaseC/` override the global `*.log` ignore (tracked status wins) — deliberate evidence capture; fine to keep, just be aware the ignore rule does not apply to them.
3. 🟢 Allowlist annotation off-by-one: `r10-allowlist.md` row for `tests/unit/test_wanderer_agent.py` says 13 hits; actual (both committed and fresh runs) = 14.
4. 🟢 `summary.md` ~5-id attribution nit (above).
5. 🟢 `tests/integration/test_empty_guard_checkpoint_roundtrip.py:89` hardcodes macOS `PG_BIN_DIR=/opt/homebrew/opt/postgresql@14/bin` → FileNotFoundError on Linux hosts; pre-existing, orthogonal to the scrubber fix. Candidate follow-up: host-dispatch for PG_BIN_DIR (OUT of mission scope).
6. 🟢 The verification recipe in the binding constraints had two dead-letter artifacts, corrected by workers with no impact: the cross-check sed carried a dead `^` anchor (manifest lines are sha-prefixed); `tests/unit/test_tool_filter.py` path is actually `tests/test_tool_filter.py` (content-matched on R1 line numbers).

### Contradictions between constraint text and tree state (both favor the tree)
- **C5**: the literal 48-pathspec command does NOT return zero (15 allowlisted hits); the operative green claim is the v5 50-pathspec set, documented in the committed gate output and independently reproduced. Active tree IS opencode-free modulo the two justified, allowlisted survivor classes.
- **C6**: `POSTGRES_PASSWORD` does not survive `_scrubbed_env()` — scrubbed by design (stricter than the constraint assumed).
- **C8c**: no `legacy` gitignore rule — legacy/ is tracked-by-commit (correct for a merge-surviving archive).

## Scope Decision
Focused re-verification per the independent-verifier mandate — NO full wall re-run (executor + 2/2 deep review already audited internal consistency). Spot-depth: 5/18 drift ids re-run (4 of 9 drift families; e2e pause/answer family ×4 scoped out — daemon-dependent, classification reviewed textually), 3/3 flakes, coder guards, both R1/R9 files. All static proofs re-computed fresh (manifests, gates, censuses) rather than trusted from the evidence pack.

## Documentation Updated
- [x] RESULTS/2026-10-13-agent-base-v2-independent-verification.md — this report (intentionally UNCOMMITTED: the verifier adds no commits to the branch under verification; fold at merge time if desired)
- [ ] PACKS.md — no new packs (ad-hoc verification runs only; no repo state mutation)

## Code Changes Summary
None. Zero tracked files modified by any verification worker (git status delta = 0 new entries beyond the 2 pre-existing runtime writes). No commits made. No daemon booted. No live-system contact. v2 dirs never written. Migrations never touched.

### Overall Status
- CONSTRAINTS: 8/8 PASS (2 with adjudicated deviations favoring stricter behavior)
- Merge-blocking findings: **0**
- **VERDICT: READY-TO-MERGE**

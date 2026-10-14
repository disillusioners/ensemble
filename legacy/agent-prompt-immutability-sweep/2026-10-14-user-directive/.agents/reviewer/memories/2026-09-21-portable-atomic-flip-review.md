# 2026-09-21 — Portable Atomic Flip Review (a89c56cb)

**Target:** commit `a89c56cb` @ `fix/portable-atomic-flip-linux` (base `latest` @ 246b7325)
**Mode:** Deep-Review council (governor `0204fa74`, 2 councilors: agentic + coding, skill `code-review`)
**Verdict:** APPROVED-WITH-NOTES — 7/7 PASS, 0 CRITICAL / 0 WARNING / 7 NOTE. Safe to merge to `latest`.

## Key facts (for future reviews of this area)
- Shared helper `atomic_flip` @ `scripts/upgrade/lib.sh:1147`; standalone launcher mirror `_js_flip_current` @ `launcher.sh:578` (launcher never sources lib.sh — verified :59-60, :468-469).
- All 5 flip sites route through: promote.sh:235, promote.sh:353, rollback.sh:141, lib.sh:1460, launcher.sh:813. Zero residual executable `mv -h` repo-wide.
- Arms: BSD `mv -h -f` (semantics preserved) / GNU `mv -T -f` / unknown → WARN + `return 1` before any FS op.
- Atomicity syscall-verified: single `renameat()=0` (strace, GNU host). No rm-then-ln.
- Launcher recovery block byte-identical to base (line shift only) — ADR-033 intact.

## Test tallies (GNU host)
| Suite | Base | Post-fix |
|---|---|---|
| test_atomic_flip.sh (new) | n/a | 36/36 |
| test_release_journal.sh | 213/55 | 226/42 (all 42 pre-existing BSD-date fixtures; +14 repoint tests fixed) |
| test_deploy.sh | 53/0 | 53/0 |

## Open NOTEs (non-gating backlog)
1. `DragonFly` (uname has no "BSD" substring) matches no arm → refuse. Fix: explicit glob.
2. `GNU/kFreeBSD` arm-order ambiguity (`*BSD*` may first-match before `GNU*` in case stmt) — either direction fails closed. Fix: order `GNU*` before `*BSD*` or tighten globs.
3. Drift guard (test:347-355) compares uname-glob sets only, not arm→`mv_args` mapping.
4. `mv`-failure cleanup branch (lib.sh:1165-1167) untested (only `ln`-failure covered).
5. Pre-existing stale comment promote.sh:17 ("plain mv -f (rename(2))").

## Review-process lesson
Council disagreement on GNU/kFreeBSD dispatch direction was surfaced and adjudicated (case first-match order wins); classified non-gating since all paths fail closed. Good pattern: require councilors to run tests from `git archive` extractions with scrubbed env (worktree untouched; live/demo installs untouchable — exit-78 guard expected).

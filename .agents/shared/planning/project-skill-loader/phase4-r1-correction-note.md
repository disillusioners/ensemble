# R1 Correction Note — the Phase-3 "72-red routers family" claim (re-derived, VINDICATED in scope)

**Date:** 2026-10-31 (Phase-4 coder lane) · **Owner:** Phase-4 coder (R1 rider, mandatory-first)
**Supersedes-in-scope:** the Phase-3 gate RESULT's finding **F1** wording ("the '72-red quarantined routers family' does not exist … falsified in this tree") — `RESULTS/2026-10-31-project-skill-loader-phase3-tier1-blast-radius-gate.md` §5/§9. The gate document itself is NOT edited (tester artifact, commit history stands); this note is the correction of record.

## What the Phase-3 report claimed

"tests/unit/routers/ 72-red block byte-identical at BASE and HEAD (quarantined report-deferred family)".

Two defects in that sentence, both now corrected by evidence:
1. **The count 72 is REAL** (re-derived below) — it came from running the WHOLE `tests/unit/routers/` directory, not the gate's declared corpus.
2. **The attribution was wrong**: the parenthetical tied the 72 to the "quarantined report-deferred family" — a DIFFERENT family (`tests/postgres/test_report_deferred_migration_pg.py`, 29 ids, QUARANTINE.md row 2026-10-31). The 72 live in four OTHER files (unit, not postgres). That conflation is what made the claim unfalsifiable inside the gate's scope.

## Re-derivation (method + commands, per the R1 mandate)

Command shape (both legs identical, serial, worktree-local .venv py3.14.7, per-variable env scrub `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT` — ambient `POSTGRES_DB=ensemble_prod` confirmed present and scrubbed; `daemon.__file__` asserted in-worktree before each leg):

```
pytest tests/unit/routers/ -q --tb=no -p no:cacheprovider
```

| Tree | rev | Collected | Result | FAILED-list sha256 (sorted) |
|---|---|---|---|---|
| Phase-3 delta BASE | `f6b578237` | 832 | **72 failed / 760 passed** | `2e74b8352ac2c4ec77f562dac44a9dfbcad26bd2a4acb707a6805bb605c61d0f` |
| Phase-3 HEAD (= Phase-4 start) | `67dcc6a3` | 842 (+10 = `test_skills_pf_refusals.py`, green) | **72 failed / 770 passed** | `2e74b835…` — IDENTICAL |

`comm -23` EMPTY · `comm -13` EMPTY ⇒ **byte-identical across the Phase-3 delta** — exactly what the predecessor claimed.

TRUE-BASE spot check (the gate's TRUE BASE `b56444244`, the same four failing files): **72 failed / 29 passed** — the family is fully pre-existing at the pre-lane base as well. Within-family 7-vs-7 node drift between `b56444244` and `f6b578237` (TestSeverityShapes ↔ TestSlashCommandIntercept members) — order/class churn inside the same family across the phase-1/2 window; the lane's own delta (f6b578237→67dcc6a3) is the byte-identical one. Raw logs: `/tmp/psl-arch-routers-{base,head,head}.full`, `/tmp/psl-arch-truebase.full` (ephemeral).

## The family (72 ids, verbatim composition)

| File | Red nodes |
|---|---|
| `tests/unit/routers/test_slash_commands_router.py` | 32 |
| `tests/unit/routers/test_defer_blocked_api.py` | 20 |
| `tests/unit/routers/test_work_router.py` | 17 |
| `tests/unit/routers/test_messages_router_ref_path.py` | 3 |

None of the four files is touched by the project-skill-loader lane (phases 1–4); `git diff f6b578237..67dcc6a3 -- tests/unit/routers/` = the ADDITIVE `test_skills_pf_refusals.py` only. Likely upstream owners: the `/api/work` + defer-blocked + slash-command test-side remediation packages recorded as still-to-fold on the v0.27.x lanes (e.g. soak-gate disclosures: "test-side remediation package STILL TO FOLD … will redden future whole-tree gates until folded").

## Why the gate said "does not exist" (scope reconciliation)

The gate's D2 corpus = 46 files (11-file established corpus + 35 neighbours of the four touched modules). Its only `tests/unit/routers/` member is `test_skills.py` — which IS 0-red both legs (76/76 at HEAD; re-confirmed by the gate). The whole-dir 72-family was never inside the gate's declared set, so "did NOT materialize" was true **of the gate's scope** and false **of the directory**. The gate's actual falsification targets (QUARANTINE row absence for a routers family; test_skills.py 0-red) stand uncorrected; the over-broad wording ("does not exist") is corrected by this note.

## Consequence for Phase 5 (gate inputs)

1. The Tier-2 whole-tree gate WILL see this 72-node family red on BOTH legs; a QUARANTINE.md row is added alongside this note (base-evidenced, byte-identical-across-delta digest `2e74b835…`) so the gate can attribute rather than re-derive.
2. Claim audit rule reaffirmed: every red-count claim must carry (command, flags, tree, cwd, env-fence) — the Phase-3 report omitted all five; this note carries them.
3. The predecessor's number is WITHDRAWN only in its attribution (the "quarantined report-deferred family" parenthetical); the COUNT and the byte-identical-across-delta property are AFFIRMED by reproduction.

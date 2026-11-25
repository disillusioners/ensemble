# SONIC-MODULE-BRIEF — pok-family-strict-corpus

## TASK
Re-point the POK family test corpus from the OVERTURNED prefix-superset semantics to STRICT
family-key membership, and add the three commissioned pins:
(a) a MUTATION-PROVEN strict-count pin, (b) a distilled census-structure pin covering all 21
reported over-cap families, (c) a strict=3 refusal pin naming the family's TRUE members only.
Also register the enlarged pack in the shared pack registry.

Module `pok-family-strict-core` (merge slot 1) implements the strict semantics and lands BEFORE
you (slot 2). You edit NO source file.

## FILES (exact — touch nothing else)
- `tests/unit/services/test_pok_service_unit.py`      — RECONCILE the existing superset-encoded assertions
- `tests/unit/services/test_pok_family_strict_unit.py` — NEW file
- `.agents/tester/PACKS.md`                            — register the enlarged pack

## FENCES (hard)
- DO NOT edit ANY file under `daemon/` — the implementation is module core's surface. You may READ it.
- DO NOT edit `daemon/tools/pok.py`, `daemon/services/pok_injection.py`, `docs/guidelines/*`, `CHANGELOG.md`.
- DO NOT wire the tests to the LIVE store, to the 499-row census TSV, or to any network/DB. The engine
  is a throwaway in-memory SQLite (`create_engine("sqlite://")` + `SQLModel.metadata.create_all` over
  the two POK tables) — copy the existing `svc` fixture from `test_pok_service_unit.py` VERBATIM.
- The `.agents/shared/planning/pok-sweep/` artifacts are READ-ONLY evidence. Never modify, never commit.
- NO migration, NO schema change.
- NEVER `git add -A`; NEVER `git stash`. `.agents/tester/PACKS.md` is high-traffic: commit
  pathspec-limited and IMMEDIATELY after your edits (a foreign commit discards uncommitted edits).

## WORKTREE
`/home/nea/ensemble-src-wt-pok-family-strict-corpus`
branch `feature/pok-family-strict-corpus`, base `52c9bfc838596cad8bdf75f9aa12e7f14aec6f1a`.
A worktree venv already exists at `<worktree>/.venv` (uv 0.12.5, pytest 9.0.2).
ALWAYS run python/pytest with CWD = the worktree (import resolution is CWD-first; from another
directory `import daemon` resolves to `/home/nea/ensemble-src` — the WRONG tree).

## MERGE ORDER
Slot 2 — merge AFTER module `pok-family-strict-core`.

## THE MEASURED CENSUS (census vintage: `census-rows-active-2026-11-23.mech-labels.tsv`,
499 active rows, written 2026-11-23 14:16Z)
21 families whose PREFIX-SUPERSET count exceeds 3. `strict` = canonical family-key equality count;
`superset` = the OLD `startswith` count. MEASURED by the leader, re-derivable read-only with:
```
python3 - <<'PY'
import re,csv,collections
CANON=re.compile(r"^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$")
rows=list(csv.DictReader(open("/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv",encoding="utf-8"),delimiter="\t"))
slugs=[r["slug"] for r in rows]
strict=collections.Counter(); rowsby=collections.defaultdict(list)
for s in slugs:
    m=CANON.match(s)
    if m: strict[m.group(1)]+=1; rowsby[m.group(1)].append(s)
for f in sorted(strict, key=lambda x: -sum(1 for s in slugs if s.startswith(x+"-"))):
    sup=sum(1 for s in slugs if s.startswith(f+"-"))
    if sup>3: print(f"{f:16s} strict={strict[f]} superset={sup}")
PY
```
| family | strict | superset | census note |
|---|---|---|---|
| skill | 3 | 13 | phantom |
| stage | 2 | 10 | phantom |
| verify | 2 | 9 | phantom |
| promote | 3 | 7 | phantom |
| worktree | 3 | 7 | phantom |
| guard | 1 | 6 | phantom |
| mutation | 3 | 6 | phantom |
| pack | 1 | 6 | phantom |
| prompt-assent | 4 | 6 | **GENUINELY OVER** |
| release | 3 | 6 | phantom |
| gc-p4 | 3 | 5 | phantom |
| packs | 1 | 5 | phantom |
| skillab | 3 | 5 | phantom |
| gc-gate | 3 | 4 | phantom |
| git | 2 | 4 | phantom |
| group-chat | 3 | 4 | phantom |
| guard-inv | 4 | 4 | **GENUINELY OVER** |
| regate | 2 | 4 | phantom |
| review | 3 | 4 | phantom |
| rogue | 1 | 4 | phantom |
| sonic | 2 | 4 | phantom |

Totals: 21 over-cap under the old prefix rule; 2 genuinely over under strict (guard-inv 4,
prompt-assent 4); 10 at strict==3; 9 at strict<=2 => **19 of the 21 are PHANTOM**. 34 families sit
at strict==3 overall (10 of them inside the 21).

## PART 1 — RECONCILE `tests/unit/services/test_pok_service_unit.py`
Line numbers are at base `52c9bfc83` and are APPROXIMATE ANCHORS — locate every site by its
TEST NAME (they are unique in the file) and re-derive the exact line with grep. Change ONLY what
the strict semantics invalidate; keep every other assertion byte-identical.

1. `:426` and `:481` — `assert "prefix-sharing active rows" in msg` ->
   `assert "active family members" in msg`. (2 sites; the third site at `:966` is inside the test
   rewritten in item 6 and its whole premise dissolves.)
2. `:729-755` — `class TestCountActiveInFamilyBoundary:` docstring REWRITE: it currently states the
   prefix-superset as the CORRECT verdict. It must now state that family membership is EXACT
   family-key equality, that a longer family never inflates a shorter one, and that the old
   one-directional leak is GONE. Keep the class name.
3. `:757-760` `test_hyphenated_tails_counted_in_same_family` — UNCHANGED (`dep-01-backup-first` and
   `dep-02-backup-extra` are both family `dep`; count 2 under both rules).
4. `:762-768` `test_prefix_family_does_not_collide` — UNCHANGED (`dep`=1, `deploy`=1 under both rules).
5. `:770-783` `test_prefix_sharing_leaks_one_direction_only` — REWRITE. Under strict semantics there
   is NO leak: `skill-01-a` is family `skill`, `skill-e6-01-x` is family `skill-e6`. Assert
   `count_active_in_family("p1","skill") == 1` and `count_active_in_family("p1","skill-e6") == 1`.
   RENAME the test (e.g. `test_prefix_sharing_no_longer_leaks_across_families`) and rewrite its
   docstring: the OLD one-directional leak (long family -> short family) is FIXED.
6. `:785-799` `test_non_matching_slug_excluded` — UNCHANGED.
7. `:801-802` `test_empty_family_returns_zero` — UNCHANGED.
8. `:813-830` `class TestMultiTokenFamilyKey:` docstring REWRITE: it says the pack pins "the
   PREFIX-SUPERSET leak in BOTH directions (W2)". That is now false — it pins STRICT membership.
9. `:831-841` `test_family_key_pattern_literal_pin` — UNCHANGED (the canonical PATTERN is unchanged).
10. `:842-871` `test_multi_token_gate_refuses_fourth_member` — UNCHANGED.
11. `:873-891` `test_family_key_regex_pins_direct` — UNCHANGED.
12. `:894-899` `test_unnumbered_prefix_counting` — REWRITE. `verify-03-a` is family `verify`;
    `verify-something-unnumbered` has NO family (no `-NN-` ordinal). Strict count is 1, not 2.
    Assert `count_active_in_family("pu","verify") == 1` and say in the docstring that the unnumbered
    row is EXCLUDED because it has no family. RENAME accordingly.
13. `:901-909` `test_unnumbered_prefix_pushes_over_cap` — REWRITE, its premise DISSOLVES: the
    unnumbered row no longer occupies a slot, so `verify-03-d` is ADMITTED, not refused. Assert
    `out.action == "created"` and that NO `pok_family_full` is raised. RENAME (e.g.
    `test_unnumbered_row_no_longer_pushes_over_cap`).
14. `:911-925` `test_supersede_exemption_multi_token` — UNCHANGED.
15. `:927-946` `test_other_family_rows_do_not_refuse_a_different_family` — UNCHANGED (its assertion
    `count_active_in_family("pw","skill-e6") == 1` holds under both rules).
16. `:948-967` `test_prefix_leak_refuses_the_short_family_and_surfaces_it` — REWRITE, premise
    DISSOLVES: `skill-01-a` + `skill-02-b` are the only true members of `skill`, so `skill-03-z` is
    ADMITTED. Assert `out.action == "created"` and that no `pok_family_full` is raised. RENAME.

## PART 2 — NEW FILE `tests/unit/services/test_pok_family_strict_unit.py`
Copy the `svc` fixture + imports VERBATIM from `test_pok_service_unit.py` (same in-memory SQLite
engine, `asyncio_mode=auto`, no InstanceManager). Three classes:

### Class A — `TestStrictFamilyCount` (the MUTATION-PROVEN legs)
- `test_longer_family_never_inflates_a_shorter_family`: seed `skill-01-a`, `skill-02-b`,
  `skill-e6-01-x`; assert `count_active_in_family(p,"skill") == 2` (the old prefix counter returns
  3 — THIS LEG IS THE MUTATION PROOF) and `count_active_in_family(p,"skill-e6") == 1`.
- `test_unnumbered_slug_has_no_family_and_never_counts`: seed `verify-01-a` +
  `verify-something-unnumbered`; assert `count_active_in_family(p,"verify") == 1`.
- `test_hyphenated_head_is_its_own_family`: `prompt-assent-01-x` -> family `prompt-assent`,
  `prompt-01-y` -> family `prompt`; counts 1 and 1 respectively.
- `test_family_key_helper_agrees_with_the_service_helper`: over a battery of >= 12 slugs (the
  census shapes: `skill-01-a`, `skill-e6-01-x`, `prompt-assist-01-x`, `snapshot-l3-02-y`,
  `verify-something-unnumbered`, `artifact-is-container-image`, `over-cap-01-m`, `gc-p4-03-x`,
  `guard-inv-04-y`, `packs-01-z`, `skillab-02-b`, `model-catalogs-not-drift`, ...) assert
  `daemon.repositories.pok.models.pok_family_key(s) == daemon.services.pok_service._slug_family(s)`
  for every one. (The two derivations MUST agree.)
- `test_family_key_helper_returns_none_without_ordinal`.

### Class B — `TestCensusStructureStrictMembership` (commission 3b)
A DISTILLED fixture table over ALL 21 families. For each family seed into a FRESH project id:
  - its TRUE members: `N` slugs `<family>-<NN>-<tail>` with N = the census STRICT count above;
  - plus DISTILLED prefix-sharing rows from LONGER families, e.g. `<family>-<longer>-<NN>-<tail>`
    (for `skill`: `skill-e6-01-x`, `skill-e6-foo-02-y`; for `gc-p4`: `gc-p4-gate-01-x`; for
    `guard`: `guard-inv-01-x`; for `prompt-assent`: `prompt-assent-extra-01-x`; etc. — pick a
    longer head for each family so its canonical key DIFFERS from the family under test).
Then assert, per family:
  - `count_active_in_family(project, family) == census_strict[family]` (EXACT — this is the
    census-structure pin);
  - the fixture is DISCRIMINATING: the old prefix-superset count over the same fixture
    (`sum(1 for s in fixture_slugs if s.startswith(family + "-"))`) is STRICTLY GREATER than the
    strict count — so this leg WOULD have failed under the bug (non-vacuous);
  - a write into a family whose census strict count is < 3 is ADMITTED (`out.action == "created"`),
    whereas the old counter refused it — this is the PHANTOM DISSOLUTION (covers the 9 families at
    strict<=2: stage, verify, guard, pack, packs, git, regate, rogue, sonic);
  - a write into a family whose census strict count == 3 is REFUSED with `pok_family_full`, and
    the message names the LIVE count 3 (not the old inflated count) — the at-3 families are ONE
    WRITE AWAY from refusing (covers the 10 at strict==3 inside the 21);
  - the census table's own shape: exactly 2 families with strict > 3 and 19 with strict <= 3
    ("19 of 21 breaches are PHANTOM").

### Class C — `TestStrictRefusalBehaviour` (commission 3c + the commission's explicit Q4)
- `test_fourth_member_into_a_family_holding_exactly_three_refuses`: seed exactly 3 true members;
  the 4th upsert raises `POKError` with `code == "pok_family_full"`; `details["family"]`,
  `details["active_count"] == 3`, `details["archive_needed"] == 1`; the message names all 3 TRUE
  members.
- `test_refusal_names_true_members_only`: seed 3 true members PLUS a longer-family row sharing the
  prefix (e.g. `strictdemo-x-01-y`). The refusal message and `details["members"]` MUST contain the
  3 TRUE members and MUST NOT contain `strictdemo-x-01-y`. (This is the "TRUE members only" pin.)
- `test_genuinely_over_families_still_refuse`: for `guard-inv` and for `prompt-assent`, seed 4 true
  members (bypassing the gate via `svc._repo.insert(...)` exactly as the existing over-cap test does)
  and assert the next write is REFUSED with `pok_family_full`, `details["active_count"] == 4`,
  `details["archive_needed"] == 2`, and the message says "over the bound by 1". (This answers the
  commission's question (i): with the count fixed, the guardrail STILL refuses for the 2
  genuinely-over families.)
- `test_at_three_families_are_one_write_away`: a family holding exactly 2 admits its 3rd
  (`action == "created"`); a family holding exactly 3 refuses its 4th. (Commission question (ii).)
- `test_supersede_remains_count_preserving`: superseding a member at the bound does NOT free a slot
  (the successor carries the same slug -> same family) — the refusal still fires.

## PART 3 — `.agents/tester/PACKS.md`
- Extend the `pok_family_key_scoped_pack` row: add `tests/unit/services/test_pok_family_strict_unit.py`
  to its file list (7 files), update its node-count cell to your MEASURED count, update its verdict
  cell and date cell to your MEASURED result. Keep the row's exact pipe-table shape and its scrub
  command verbatim (the per-variable `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_HOST -u POSTGRES_PORT ENSEMBLE_SELF_ENV=dev` scrub is load-bearing; the GLOB form is a
  silent NO-OP).
- ADD a new row registering the STRICT-COUNT MUTATION pack (the house `pok_family_key_mutation`
  pattern: scratch worktree, revert ONLY the counter's strict filter, re-run the 7-file pack, expect
  RED, restore, sha256 PRE==POST). Follow the neighbouring rows' format exactly. Put it next to the
  `pok_family_key_mutation` row.
- Do NOT reformat, renumber, or edit any OTHER row. Do NOT touch any other pack's row.

## VERIFICATION — TWO PROOFS YOU MUST RUN AND REPORT
The implementation (module core) is NOT in your branch. To prove GREEN you apply the CORE CONTRACT
LOCALLY in your worktree as UNCOMMITTED edits, then restore.

Step 1 — GREEN proof. Apply the core contract locally (uncommitted):
  - `daemon/repositories/pok/models.py`: add `import re`; after `POK_FAMILY_SLUG_PATTERN` add
    `POK_FAMILY_RE = re.compile(POK_FAMILY_SLUG_PATTERN)` and
    `def pok_family_key(slug): m = POK_FAMILY_RE.match(slug); return m.group(1) if m else None`;
  - `daemon/repositories/pok/repository.py`: `count_active_in_family` selects the active rows and
    returns `sum(1 for r in rows if pok_family_key(r.slug) == family)`;
  - `daemon/services/pok_service.py`: `_slug_family` delegates to `pok_family_key`; the step-8 member
    listing filters `if _slug_family(r.slug) == family`; the refusal string reads
    `f"{head}; active family members: "`.
  Run the 7-file pack -> expect ALL GREEN. Capture the measured node count.
Step 2 — RESTORE the source files so ONLY your test files (+ PACKS.md) are modified:
  `git restore daemon/` then `git status --porcelain` must list ONLY
  `tests/unit/services/test_pok_service_unit.py`, `tests/unit/services/test_pok_family_strict_unit.py`,
  `.agents/tester/PACKS.md`.
Step 3 — RED proof A (the base IS the prefix-superset counter): with the source files at BASE, run
  `tests/unit/services/test_pok_family_strict_unit.py` -> the strict-count legs MUST FAIL. Capture
  the failing node ids verbatim.
Step 4 — RED proof B (the explicit mutation): re-apply the core contract, then mutate ONLY the
  counter's filter back to the prefix superset (`r.slug.startswith(f"{family}-")`), re-run the new
  file -> the strict-count legs MUST FAIL. Capture the failing node ids verbatim. Then restore.
Step 5 — confirm the worktree is clean of source edits (`git diff --stat -- daemon/` empty) and that
  `git status --porcelain` lists only your three files.

## COMMIT (pathspec-limited — NEVER `git add -A`, NEVER `git stash`)
```
git add tests/unit/services/test_pok_service_unit.py tests/unit/services/test_pok_family_strict_unit.py .agents/tester/PACKS.md
git commit --only -m "test(pok): strict family-membership corpus + census-structure pins" -- tests/unit/services/test_pok_service_unit.py tests/unit/services/test_pok_family_strict_unit.py .agents/tester/PACKS.md
```

## REPORT
Reply to sonic-developer with the FROZEN [sonic-module-report-v2] shape: files touched, what
changed, the MEASURED node counts, BOTH red proofs verbatim (failing node ids), what is UNTESTED,
known risks.

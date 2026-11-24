# HANDOFF — pok-family-count-strict (sonic-developer lane)

Commission: fix the POK slug-FAMILY COUNT BUG (the store's sprawl engine, anomaly dbdfba2f).
Complexity verdict: **MEDIUM**. Release: **MERGED + PUSHED + STAGED, NO PROMOTE** (tag `v0.31.50`).

## Files touched across modules

**Module `pok-family-strict-core`** (branch `feature/pok-family-strict-core` @ `f90b3ce70`; merged as `819496817`) — merge slot 1
- `daemon/repositories/pok/models.py`
- `daemon/repositories/pok/repository.py`
- `daemon/services/pok_service.py`

**Module `pok-family-strict-corpus`** (branch `feature/pok-family-strict-corpus` @ `2fd75c00f` + amendments `50cd6aafd`, `2ae427933`; merged as `5f0cc9f1d` + `cb25cd536`) — merge slot 2
- `tests/unit/services/test_pok_service_unit.py`
- `tests/unit/services/test_pok_family_strict_unit.py` (NEW, 271 lines)
- `.agents/tester/PACKS.md`

**Integration-layer files** (committed by sonic-developer on `feature/pok-family-strict-int`)
- `.agents/shared/planning/sonic-team/pok-family-count-strict/HANDOFF.md` (this file)
- `.agents/shared/planning/sonic-team/pok-family-count-strict/BRIEF-core.md`
- `.agents/shared/planning/sonic-team/pok-family-count-strict/BRIEF-corpus.md`
- `CHANGELOG.md` (version-bump commit)

Union = 6 commissioned files + 4 integration files. **Zero overlap** between the two modules' file
sets; both merges were clean (`merge --no-ff`, no conflict, no bounce).

## What changed

**1. The counter — prefix-superset → strict family-key equality.**
`POKRepository.count_active_in_family` (`daemon/repositories/pok/repository.py`) no longer filters on
`slug.startswith(f"{family}-")`. It selects the project's ACTIVE rows and counts those whose
canonical family key EQUALS the requested family. The "DELIBERATE SUPERSET (documented, not a
bug)" docstring — the doctrine this commission OVERTURNS — is removed and replaced by the strict
rule. `POK_FAMILY_MAX = 3` and `POK_ACTIVE_CAP_PER_PROJECT = 1024` are **untouched**; only the
COUNT's meaning changed.

**2. The canonical derivation — one source, no divergent parser.**
`daemon/repositories/pok/models.py` gains `POK_FAMILY_RE` (compiled from the **unchanged** canonical
`POK_FAMILY_SLUG_PATTERN`) and `pok_family_key(slug) -> str | None`. `pok_service._slug_family` now
delegates to it, so the count, the member listing and the admission-side family derivation all read
the same key. A pin asserts the two derivations agree over a 14-slug battery.

**3. The refusal — TRUE members only.**
The step-8 guard lists members by `family_key(row.slug) == family` (was the same prefix filter), and
the message reads `active family members:` (was `prefix-sharing active rows:`). The count-aware
cure text, `archive_needed`, `over_by`, the `head` wording and the `details` keys are byte-identical.

**4. The corpus — the doctrine is re-pointed, and three pins are added.**
Six superset-encoded assertions reconciled; FOUR of them had premises that **dissolve** under strict
semantics and now assert ADMISSION instead of refusal (`test_unnumbered_row_no_longer_pushes_over_cap`,
`test_no_prefix_leak_admits_third_member`, and the rewritten
`test_prefix_sharing_no_longer_leaks_across_families` / `test_unnumbered_slug_excluded_from_strict_count`).
Two class docstrings rewritten (they stated the superset as the correct verdict). Two stale comments
above the changed assertions corrected. NEW `tests/unit/services/test_pok_family_strict_unit.py`
(+51 nodes): Class A mutation-proven strict-count legs, Class B distilled census-structure pin over
all 21 reported over-cap families, Class C refusal behaviour (at-3 refusal naming TRUE members only,
the two genuinely-over families, the at-3 one-write-away boundary, supersede count-preservation).

**5. The registry — verdicts corrected to the sha where they were measured.**
Two `.agents/tester/PACKS.md` rows (`pok_family_key_scoped_pack`, `pok_family_strict_count_mutation`)
now cite `5f0cc9f1d` (where the 7-file pack is green) instead of `52c9bfc83` (the base, where the
pack is RED and the new file does not exist), and the mutation row records RED proof C.

### ANSWER (i) — does the guardrail STILL refuse for the 2 genuinely-over families? **YES.**
The guard trips at `family_active >= POK_FAMILY_MAX` with `family_active` now being the TRUE count.
Measured at the merged tip: `guard-inv` seeded with 4 true members → the 5th write is refused with
`pok_family_full`, `details["active_count"] == 4`, `details["archive_needed"] == 2`, and the message
carries `over the bound by 1`. Identical result for `prompt-assent` (4 true members). The refusal
does not weaken for a genuinely-over family — it now refuses for the RIGHT reason and names the right
count. Their cure is a **separate curation step** (count-preserving supersede — never an archive to
make room) and is **NOT** part of this change; the archive wave was gated off.

### ANSWER (ii) — are the at-3 families now one write away from refusing? **YES.**
A family owning exactly 3 rows is at the bound: the 4th write into it is refused (`pok_family_full`,
`active_count == 3`, `archive_needed == 1`). 34 families sit at exactly 3 true rows (10 of them
inside the 21), and the census-structure pin proves the refusal message names the LIVE count `3`,
not the old inflated count (which was 4–13 for those same families). Boundary behaviour is pinned
both ways: a family owning 2 admits its 3rd. The family-surgery pass for the at-3 families is board
item `wi-dce9a1ac` — a separate lane, untouched here.

### BLAST RADIUS — 19 dissolve, 2 need a real merge
Census vintage: `census-rows-active-2026-11-23.mech-labels.tsv` (499 active rows, written
2026-11-23 14:16Z), re-derived read-only from the canonical regex; `census-vs-canonical family
mismatches: 0`. The 21 families reported over-cap under the prefix rule split as:

- **19 PHANTOM — DISSOLVE** (the strict count is ≤ 3, so the refusal was never justified):
  `skill` (3), `stage` (2), `verify` (2), `promote` (3), `worktree` (3), `guard` (1), `mutation` (3),
  `pack` (1), `release` (3), `gc-p4` (3), `packs` (1), `skillab` (3), `gc-gate` (3), `git` (2),
  `group-chat` (3), `regate` (2), `review` (3), `rogue` (1), `sonic` (2).
- **2 GENUINELY OVER — still refuse; they need a real merge (NOT this lane's job)**:
  `guard-inv` (4 true rows) and `prompt-assent` (4 true rows). The merge must be **count-preserving
  (supersede)** — archiving to make room is forbidden.
- Of the 21: 2 are strictly over, 10 are at strict==3 (still one write away from refusing), 9 are at
  strict≤2. Overall, **34 families sit at strict==3**.
- Vintage reconciliation: the "21 over-cap / 36 at-cap" figures are the **08:32Z** census under the
  OLD prefix rule; the census-mechanics doc's later line ("over-cap dropped from 21 to 2, at-cap 36 →
  34") describes the **strict** count on the 14:16Z tree. This handoff reports the 14:16Z strict
  numbers, and the "12 families at strict=3" figure is the decision-brief vintage — the current
  census has **34** at strict==3 (10 of them inside the 21). No POK row was mutated, archived or
  superseded by this lane (read-only on the live store).

## What is UNTESTED

- **No PostgreSQL materialization of the strict count.** The whole corpus runs on a throwaway
  in-memory SQLite engine. The count is dialect-agnostic (a plain select + a Python filter), but the
  house's real-PG pack `pok_family_key_behavioral` (real `POKService`+`POKRepository` on
  `ensemble_dev_scratch`, `search_path`-fenced) was **not re-run** by this lane.
- **The census fixture is DISTILLED** (21 families × ~5 rows, in-memory) — the live 499-row store was
  not wired into any test, by commission fence.
- **The live effect on the real store is INFERRED from the census, not observed live** — no POK
  writes, no archiving, no superseding were performed against the live store.
- **No boot-smoke / livez / readyz leg** for this change (the daemon boot path is untouched).
- **The Tier-1 whole-scope regression (217 files, 12 LPT shards) was NOT re-run.** The scoped
  POK-adjacent scope WAS run (`tests/unit/repositories/` + `tests/unit/test_pok_grant_pins.py`):
  **503 passed, 37 skipped, 1 failed** — that 1 is a PRE-EXISTING red (see Known risks).
- The **injection / metadata-cap / trust-tier / ari-seam / tools** legs are unchanged and green, but
  they are inherited from the base — they were not extended for this change.

## Known risks

- **The refusal RELAXES.** 19 families that were spuriously refused become admissible, and 9 of them
  were refused outright with a count that was never theirs. The sprawl engine's feedback loop
  (refusal → "family is full" → invent a fresh family slug → inflate the prefix → refuse again)
  stops here. The residual exposure: an agent that learned to route around a refusal may now write
  into a family that is at its TRUE bound — bounded by `POK_FAMILY_MAX = 3`, which is unchanged.
- **The 2 genuinely-over families still refuse** (see Answer (i)). Their count-preserving merge is
  board work, not this lane's. Until it lands, writes into `guard-inv` and `prompt-assent` keep
  refusing with `archive_needed == 2` — correct behaviour, but it is a standing operational debt.
- **PRE-EXISTING red in the POK-adjacent scope (NOT caused by this lane, NOT fixed):**
  `tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_the_frontend_ui_lane_drift_is_confined_to_the_allowlist_at_the_tree_level`.
  PROVEN pre-existing: the guard's input (`git diff 858a85e31..HEAD -- frontend/`) is
  **byte-identical at the base (52c9bfc83) and at the merged tip (61 files each)** and this lane
  touched **0** frontend files. Disclosed, not fixed — it belongs to the FE-UI lane's drift budget.
- **PRE-EXISTING stale anchor (not caused by this lane):**
  `.agents/shared/planning/maintenance-system/phase3-plan.md:110` cites `pok_service.py:117` for
  `POK_FAMILY_MAX` (actually `:118`) — identical at base and tip. The fenced guideline's anchors are
  all CORRECT (see below).
- **`pok_service.py` grew by 1 line inside the guard region** (the strict-membership comment is one
  line longer), so line anchors BELOW `:480` in that file shift by +1. No LIVE anchor sits below
  `:471`; the planning/evidence artifacts citing e.g. `:538-541` are point-in-time evidence and are
  read-only under the commission's fence.
- **The import line `POK_PROVENANCE_VALUES, pok_family_key,` is deliberately packed onto ONE line**
  so the guideline's line-anchors stay byte-stable. A future one-name-per-line edit at that import
  WILL drift `pok_service.py:105 :121 :123 :241 :311 :461 :462 :470` and will require a guideline
  re-derivation — a surface this commission fenced off.
- **Four reconciled assertions now assert ADMISSION.** If the prefix counter is ever re-introduced
  they turn RED (measured: 44 RED at the merged tip) — that is the intended tripwire, but it means
  the corpus is now coupled to the strict semantics and cannot be relaxed back silently.
- **No live-store verification.** The blast-radius numbers are census-derived; the live store still
  holds the old rows and the live daemon still runs the OLD code until the Emperor's promote
  ceremony. Nothing in this lane changes live behaviour before then.

## How to verify

On the MERGED tree — release tip `d55f2875898b1e94ad13bbc06c9844520a9b190f` (the `v0.31.50` tag's
peeled commit, == `origin/latest` at stage time), branch `feature/pok-family-strict-int`
(the commission surface is fully contained in `cb25cd536`; `d55f28758` = `cb25cd536` + the
`! New version 0.31.50` bump commit + the `origin/latest` re-merge):

```
cd <worktree> && env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT ENSEMBLE_SELF_ENV=dev timeout 300 .venv/bin/pytest -p no:randomly --tb=short -q -rfE \
  tests/unit/services/test_pok_service_unit.py \
  tests/unit/services/test_pok_family_strict_unit.py \
  tests/unit/services/test_pok_injection_unit.py \
  tests/unit/services/test_pok_metadata_cap_unit.py \
  tests/unit/services/test_pok_trust_tier_unit.py \
  tests/unit/services/test_pok_ari_seam_unit.py \
  tests/unit/tools/test_pok_tools_unit.py
```
MEASURED: **263 passed / 0 failed** (base `52c9bfc83` = 212 passed / 0 failed; +51 new nodes).

MUTATION PROOF (revert ONLY the counter's filter; the pack must go RED):
```
git restore daemon/repositories/pok/repository.py   # after the edit below
# in daemon/repositories/pok/repository.py:
#   return sum(1 for r in rows if pok_family_key(r.slug) == family)
#   -> return sum(1 for r in rows if r.slug.startswith(f"{family}-"))
```
MEASURED: **44 failed / 219 passed** = the 40 new-file legs + the 4 reconciled legs in
`tests/unit/services/test_pok_service_unit.py`. Restore verified: sha256 of
`daemon/repositories/pok/repository.py` PRE == POST
(`e58ce94ff3797eba1c7b553ddc64a82bc5153bea029855a72fdeca9976cda5ac`), `git status --porcelain
--untracked-files=no` empty, re-run after restore = 263 passed.

BEHAVIOURAL SPOT-CHECK (in-memory SQLite, no live store): `count(skill)` = 2 when the rows are
`skill-01-a`, `skill-02-b`, `skill-e6-01-x` (the old counter returned 3); `count(verify)` = 1 when
the rows are `verify-01-a` + `verify-something-unnumbered` (old: 2); `prompt-assent` and `prompt`
are distinct families; a refusal at strict=3 names the 3 TRUE members and NOT the longer-family row
`strictdemo-x-01-y`; `guard-inv` at 4 refuses with `archive_needed == 2` and `over the bound by 1`.

GUIDELINE ANCHOR RE-VERIFICATION (must be ZERO drift):
```
for L in 105 121 123 241 311 461 462 470; do
  diff <(git show 52c9bfc83:daemon/services/pok_service.py | sed -n "${L}p") \
       <(sed -n "${L}p" daemon/services/pok_service.py) || echo "DRIFT at :$L"
done
git diff --stat 52c9bfc83 HEAD -- daemon/services/pok_injection.py   # must be EMPTY
```
MEASURED: all eight anchors byte-identical to base; `pok_injection.py` untouched; no guideline
anchor points into `models.py` or `repository.py`.

RELEASE RECEIPTS (all MEASURED):
- Tag `v0.31.50` (annotated) — annotated object `e2995b460156ccd75c8402ae338328bf00825b67`, peeled
  commit `d55f2875898b1e94ad13bbc06c9844520a9b190f`. `git ls-remote --tags origin 'refs/tags/v0.31.50*'`
  shows BOTH lines (the exact-name pattern `refs/tags/v0.31.50` alone does NOT match the `^{}` peeled
  ref — use the wildcard, or ask for `refs/tags/v0.31.50^{}` explicitly).
- Push order as mandated: `git push origin feature/pok-family-strict-int:latest` FIRST
  (`ca806cfe2..d55f28758`), THEN `git push origin v0.31.50`. Never `git push --tags`.
- `origin/latest` == `d55f28758` == the tag's peeled commit == local HEAD (tip-identity satisfied).
- Staged from an isolated clean worktree detached at the tag
  (`/home/nea/ensemble-src-wt-stage-v03150-pokfamily`; `git describe --tags --exact-match` = v0.31.50;
  working tree clean; `dist/` + `frontend/dist/` absent before the build).
- Staged release: `/home/nea/agents-ensemble/releases/v0.31.50` — manifest `source_commit` and
  `scripts_pin_commit` = `d55f28758`, `binary_version` 0.31.50, `staged_at` 2026-11-24T03:53:09Z,
  `rollback_safe=true` (`source=explicit=true` — see below), `known_schema_gen`
  `20261121_120001_create_engine_runs.sql`, `contains_contract_phase=true`,
  `pipeline_scripts_floor_commit`/`_rev` = `46d55b002d35092c29c391fc76e6b897c0819e11`.
- 4-WAY SHA MATCH: release-dir `ensemble-prod` == `dist/` build == manifest `binary_sha256` ==
  provenance sidecar `artifact_sha256` = `72717e1efc5725d395bccd797e813e517f0145beb0e593b31630d1c396d8e905`.
- FE provenance sidecar binds to the pushed tip: `git_head=d55f2875898b`, `git_dirty=false`.
- Prompt-artifact baseline clean: `PROMPT-BASELINE: OK — 217 versioned artifact(s) match`.
- **NO PROMOTE**: `current` still -> `releases/v0.31.49`; the stage log records "NO flip performed
  (promote.sh owns current)". The live daemon is untouched.
- STAGE OPERATOR CHOICE (disclosed): the stage refused to DERIVE `rollback_safe` silently because
  destructive DDL (`DROP TABLE|DROP COLUMN`) exists somewhere under `daemon/migrations/versions`. The
  guard's own criterion selects `ENSEMBLE_ROLLBACK_SAFE=1` — "ONLY if the migration delta between the
  previous release tag and v0.31.50 is EMPTY". MEASURED: `git diff --name-status v0.31.49 v0.31.50 --
  daemon/migrations/` is EMPTY (no migration added, removed or changed by this release), and the
  destructive DDL is PRE-EXISTING (present in the v0.31.49 tree, already applied before v0.31.50).
  The stage was re-run with `ENSEMBLE_ROLLBACK_SAFE=1` and recorded `source=explicit=true`.

FROZEN-BINARY PROOF (the fix IS in the staged payload). METHOD DISCLOSURE: literal `strings` on the
frozen binary is USELESS here — PyInstaller's PYZ compression blocks even symbol greps (the house's
known S3 caveat). The proof therefore EXTRACTS the module from the PYZ and inspects its code object
(`PyInstaller.archive.readers` -> `ZlibArchiveReader.extract`), which is the house's disclosed
method adjustment (module symbols + constants instead of literals):
- `daemon.services.pok_service` — contains the NEW constant `'; active family members: '` and does
  NOT contain `prefix-sharing active rows`; names `pok_family_key` and `POK_FAMILY_RE` present.
- `daemon.repositories.pok.models` — names `pok_family_key` and `POK_FAMILY_RE` present.
- `daemon.repositories.pok.repository` — name `pok_family_key` present (the strict count calls it).
NON-VACUOUS and bidirectional: the NEW wording is present AND the OLD wording is absent.

## Bash-raises used

**none.** Every command ran under the default 600 s cap. The two long operations — the FE/PyInstaller
build and the ~4.6-minute POK-adjacent regression scope — were run as background processes (`proc`),
not as blocking bash calls, and consumed no raise.

## Complexity self-assessment

**MEDIUM.** The change is surgical (3 source files, ~45 net lines) with no schema, no migration and
no new dependency, but it overturns a documented doctrine in a LIVE admission guard, ships a 51-node
new test corpus plus 6 reconciled assertions, touches a shared pack registry, and required a
measured census re-derivation plus a two-round correction pass on the registry's truthfulness.
It is NOT high/extremely-high: the file sets were disjoint and both merges were clean on the first
attempt, the behavioural blast radius is a RELAXATION (19 phantom refusals dissolve) rather than a
tightening, the bound constant is untouched, and every gate ran green at the merged tip. No handover
was raised and no verdict was softened.

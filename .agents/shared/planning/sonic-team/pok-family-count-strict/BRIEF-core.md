# SONIC-MODULE-BRIEF — pok-family-strict-core

## TASK
Make the POK family COUNT strict (exact family-key EQUALITY) instead of a prefix-superset, and
make the `pok_family_full` refusal name the family's TRUE members only.

Today `count_active_in_family` counts every active slug that merely `startswith("<family>-")`.
So a LONGER family's rows inflate a SHORTER family (`skill-e6-*` counts toward `skill`) and an
UNNUMBERED slug with no family at all (`verify-something-unnumbered`) counts too. The refusal then
lists those non-members as "prefix-sharing active rows". This module changes the COUNTER + the
MEMBER LISTING + the refusal wording ONLY. `POK_FAMILY_MAX = 3` is UNCHANGED.
`POK_ACTIVE_CAP_PER_PROJECT = 1024` is UNCHANGED.

## FILES (exact — touch nothing else)
- `daemon/repositories/pok/models.py`
- `daemon/repositories/pok/repository.py`
- `daemon/services/pok_service.py`

## FENCES (hard)
- DO NOT touch `daemon/tools/pok.py` — another lane owns it (FENCED).
- DO NOT touch `daemon/services/pok_injection.py` — FENCED (guideline anchors live there).
- DO NOT touch `POK_ACTIVE_CAP_PER_PROJECT` (pok_service.py:105) — stays 1024.
- DO NOT touch `POK_FAMILY_MAX` (pok_service.py:118) — stays 3.
- DO NOT EDIT `POK_FAMILY_SLUG_PATTERN` (models.py:132) — it is CANONICAL; you REUSE it.
- DO NOT touch any test file, `.agents/`, `docs/`, or `CHANGELOG.md`.
- READ-ONLY on the live store: no POK writes, no archiving, no superseding.
- NO migration, NO schema change — pure Python counting semantics.

## WORKTREE
`/home/nea/ensemble-src-wt-pok-family-strict-core`
branch `feature/pok-family-strict-core`, base `52c9bfc838596cad8bdf75f9aa12e7f14aec6f1a`.
A worktree venv already exists at `<worktree>/.venv` (uv 0.12.5, pytest 9.0.2).
ALWAYS run python/pytest with CWD = the worktree. Import resolution is CWD-first: run from another
directory and `import daemon` resolves to `/home/nea/ensemble-src` — the WRONG tree.

## MERGE ORDER
Slot 1 — merge FIRST, before module `pok-family-strict-corpus`.

## THE EXACT EDITS

### E1 — `daemon/repositories/pok/models.py`
(a) Add `import re` to the stdlib import block, BEFORE `import uuid` (alphabetical):
```
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional
```
(b) Immediately AFTER `POK_FAMILY_SLUG_PATTERN = r"^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$"`
(models.py:132) append:
```python

#: Compiled form of the canonical pattern — the SINGLE source every
#: family-membership decision derives from.
POK_FAMILY_RE = re.compile(POK_FAMILY_SLUG_PATTERN)


def pok_family_key(slug: str) -> str | None:
    """The canonical family key of a slug, else ``None``.

    THE single derivation: the maximal run of letter-led tokens before
    the leftmost ``-NN-`` ordinal (``prompt-assent-01-x`` -> ``prompt-assent``).
    Family MEMBERSHIP is decided by comparing this key for EQUALITY —
    never by a prefix match.
    """
    m = POK_FAMILY_RE.match(slug)
    return m.group(1) if m else None
```
(c) Add `"POK_FAMILY_RE"` and `"pok_family_key"` to the module `__all__` (the list already holding
`"POK_FAMILY_SLUG_PATTERN"`).

### E2 — `daemon/repositories/pok/repository.py`
(a) Add `pok_family_key` to the existing `from daemon.repositories.pok.models import (...)` block.
(b) REPLACE the whole of `count_active_in_family` (base repository.py:221-253, method + long
docstring) with:
```python
    def count_active_in_family(self, project_id: str, family: str) -> int:
        """Active rows whose FAMILY KEY EQUALS ``family`` (§4.5).

        STRICT MEMBERSHIP (was a PREFIX SUPERSET — the doctrine is OVERTURNED by
        commission pok-family-count-strict, 2026-11-24): every active row's
        canonical family key is derived with ``pok_family_key`` and compared
        for EQUALITY.

        Consequences (MEASURED on the 2026-11-23 census, 499 active rows): a
        LONGER family never inflates a SHORTER one (``skill-e6-*`` no longer
        counts toward ``skill``); an UNNUMBERED slug (``verify-something-
        unnumbered``) has NO family and never counts; a family's count is the
        number of rows it actually OWNS. The bound (POK_FAMILY_MAX) is
        unchanged — only the COUNT's meaning is corrected.
        """
        stmt = select(ProjectOperatingKnowledge).where(
            ProjectOperatingKnowledge.project_id == project_id,
            ProjectOperatingKnowledge.status == "active",
        )
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
        return sum(1 for r in rows if pok_family_key(r.slug) == family)
```
The OLD "DELIBERATE SUPERSET (documented, not a bug)" paragraph is REMOVED. Leave NO sentence
claiming the superset is deliberate, conservative, or by design.

### E3 — `daemon/services/pok_service.py`
(a) `_slug_family` (base :151-154) delegates to the canonical helper. The replacement MUST be
EXACTLY FOUR lines so every guideline line-anchor at or below :155 stays valid:
```python
def _slug_family(slug: str) -> str | None:
    """The family key for a family-ordered slug, else ``None``."""
    # Delegates to models.pok_family_key — the single canonical derivation.
    return pok_family_key(slug)
```
Add `pok_family_key` to the existing `from daemon.repositories.pok.models import (...)` block
(base :54). Keep `POK_FAMILY_RE` defined and exported in this module — do NOT delete it.

(b) The step-8 guard member listing (base :476-484) becomes STRICT:
```python
                # STRICT MEMBERSHIP (was a prefix superset): the rows
                # listed below are the family's OWN members — each row's
                # canonical family key must EQUAL ``family``. A LONGER
                # family (e.g. ``skill-e6-*``) never counts toward
                # ``skill``; an UNNUMBERED slug has no family at all.
                members = sorted(
                    r.slug
                    for r in self._repo.list_entries(project_id, statuses=("active",))
                    if _slug_family(r.slug) == family
                )
```
(c) EXACTLY ONE refusal string changes (base :526):
`f"{head}; prefix-sharing active rows: "` -> `f"{head}; active family members: "`
Nothing else in the message changes: the count-aware cure text, `archive_needed`, `over_by`, the
`head` texts and the `details` dict keys stay byte-identical. `details["members"]` now carries the
TRUE member list.

(d) ANCHOR NEUTRALITY — HARD FENCE. The only lines of `pok_service.py` you may change are
:151-154 (the `_slug_family` body, kept at exactly 4 lines) and lines at or after :471 (the
step-8 guard). These guideline anchors must keep pointing at the same code:
`pok_service.py:105` (cap constant), `:121` (POK_BLOCK_MAX_CHARS), `:123` (POK_BLOCK_MAX_ENTRIES),
`:241` (upsert outcome labels), `:311` (supersede-or-insert), `:461-470` and `:462` (the
`pok_active_cap` refusal envelope).
VERIFY before finishing:
- `git diff -U0 daemon/services/pok_service.py` shows NO hunk starting above line 471 except the
  `_slug_family` hunk;
- that hunk is line-count NEUTRAL (4 lines in -> 4 lines out);
- `sed -n '105p;121p;123p;241p;311p;461p;462p;470p' daemon/services/pok_service.py` still prints the
  same statements as at base (compare against `git show 52c9bfc83:daemon/services/pok_service.py`).

## VERIFICATION
Run the house pack (CWD = the worktree):
```
cd /home/nea/ensemble-src-wt-pok-family-strict-core && env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT ENSEMBLE_SELF_ENV=dev timeout 300 .venv/bin/pytest -p no:randomly --tb=short -q -rfE tests/unit/services/test_pok_service_unit.py tests/unit/services/test_pok_injection_unit.py tests/unit/services/test_pok_metadata_cap_unit.py tests/unit/services/test_pok_trust_tier_unit.py tests/unit/services/test_pok_ari_seam_unit.py tests/unit/tools/test_pok_tools_unit.py
```
(the per-variable scrub is load-bearing — `env -u POSTGRES_*` with a GLOB is a silent NO-OP.)

EXPECTED: the pack goes RED in EXACTLY the assertions that encode the OLD superset semantics.
Module `pok-family-strict-corpus` reconciles them and merges AFTER you (slot 2). Report the
exact list of failing node ids + their failure lines. ANY OTHER red is YOUR bug — fix it.
Also: `./.venv/bin/python -m py_compile` all three files; `git status --porcelain` must show ONLY
those three files.

## COMMIT (pathspec-limited — NEVER `git add -A`, NEVER `git stash`)
```
git add daemon/repositories/pok/models.py daemon/repositories/pok/repository.py daemon/services/pok_service.py
git commit --only -m "fix(pok): strict family-key membership in the family count" -- daemon/repositories/pok/models.py daemon/repositories/pok/repository.py daemon/services/pok_service.py
```

## REPORT
Reply to sonic-developer with the FROZEN [sonic-module-report-v2] shape: files touched, what
changed, what you ran + the measured numbers, what is UNTESTED, known risks.

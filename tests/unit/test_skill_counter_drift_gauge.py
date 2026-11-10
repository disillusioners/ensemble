"""A4 — the COUNTER-DRIFT GAUGE pack (registered pack
``skill_counter_drift_unit_test``; GAUGE PINS ONLY).

SCOPE NOTE.  A4's dispatched scope is the GAUGE REDEFINITION (plan §18
row 11b).  The counter-bump fix (row 11, ``skill_metrics_service.py``)
is out of this lane's fence and out of A4's dispatched scope, so its
mutation legs (M-A4a/M-A4b) and the reconciliation-script legs
(M-A4e/M-A4f/M-A4g) are REPORTED, not implemented here.

THE GAUGE AND WHY IT WAS REDEFINED.  ``skills.name`` is INTENTIONALLY
non-unique — every project gets its own ``bank_clone`` generation-0 row,
so one name spans many ``project_id`` values BY DESIGN.  The retired
gauge (``names-with-diverging-content / total skills``) therefore
measured a NON-DEFECT and could never reach 0 without destroying the
per-project clone model.  The honest gauge measures divergence WITHIN
one identity key ``(name, project_id)``: ``count(DISTINCT content) > 1``
per key (the plan §16.1 C-5 reproduction recipe).

BASELINE (measured, carried from the census — the pack environment
cannot re-measure production, there is no promote and no live DB write
in Phase 1)::

    dev  (ensemble_dev, 15 rows)  : diverging_keys = 0
    prod (ensemble_prod, 630 rows): diverging_keys = 32

...caused by the 27 stranded A/B pairs (Ruling E).  The 0 target is
BLOCKED-ON the A/B resolution decision; this pack pins the GAUGE, not
the production number.

Mutation table:

* ``M-A4c`` — leave the gauge defined as GLOBAL-name divergence (group
  by ``name`` only) ⇒ the identity-key pins red;
* ``M-A4d`` — make the within-identity-key filter a no-op (drop the
  ``project_id`` grouping) ⇒ the same red.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.repositories.skill.models import (  # noqa: F401 — create_all needs them
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)
from daemon.repositories.skill.repository import SkillRepository


@pytest.fixture()
def skill_repo():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield SkillRepository(engine)
    finally:
        engine.dispose()


def _skill(
    repo: SkillRepository,
    *,
    name: str,
    content: str,
    project_id: str | None,
    generation: int = 0,
    is_active: bool = True,
) -> None:
    repo.create(
        name=name,
        description=f"desc {name}",
        content=content,
        project_id=project_id,
        generation=generation,
        is_active=is_active,
    )


class TestTheGaugeMeasuresWithinIdentityKey:
    """The redefined gauge: divergence INSIDE ``(name, project_id)``."""

    def test_a_divergent_identity_key_is_counted(self, skill_repo) -> None:
        _skill(skill_repo, name="code-fix", content="A", project_id="p1", generation=0)
        _skill(skill_repo, name="code-fix", content="B", project_id="p1", generation=1)
        gauge = skill_repo.within_identity_key_divergence()
        assert gauge["diverging_keys"] == 1
        assert gauge["rows_in_diverging_groups"] == 2
        assert gauge["keys"][0]["name"] == "code-fix"
        assert gauge["keys"][0]["project_id"] == "p1"
        assert gauge["keys"][0]["distinct_contents"] == 2

    def test_one_name_across_projects_with_the_SAME_content_is_not_divergence(
        self, skill_repo
    ) -> None:
        """THE POINT OF THE REDEFINITION — per-project clones are a NON-DEFECT.

        The retired global-name gauge counted this as "diverging"; the
        honest gauge must report 0.  ``M-A4c``/``M-A4d`` break exactly
        this assertion.
        """
        for project in ("p1", "p2", "p3"):
            _skill(
                skill_repo,
                name="git-commit",
                content="identical body",
                project_id=project,
            )
        gauge = skill_repo.within_identity_key_divergence()
        assert gauge["diverging_keys"] == 0, (
            "one name cloned per project with identical content is the "
            "INTENDED model — reporting it as drift is the defect the "
            "gauge redefinition fixes"
        )
        assert gauge["total_skills"] == 3

    def test_one_name_across_projects_with_DIFFERENT_content_is_still_not_divergence(
        self, skill_repo
    ) -> None:
        """Even differing cross-project content is legal (per-project evolution)."""
        _skill(skill_repo, name="review", content="proj-1 flavour", project_id="p1")
        _skill(skill_repo, name="review", content="proj-2 flavour", project_id="p2")
        gauge = skill_repo.within_identity_key_divergence()
        assert gauge["diverging_keys"] == 0, (
            "global-name divergence is a NON-DEFECT — it is never reported "
            "as one"
        )

    def test_identical_duplicates_inside_one_key_are_not_divergence(
        self, skill_repo
    ) -> None:
        """2 of the 27 A/B pairs carry identical content — harmless duplicates."""
        _skill(skill_repo, name="plan-creation", content="same", project_id="p1", generation=0)
        _skill(skill_repo, name="plan-creation", content="same", project_id="p1", generation=1)
        gauge = skill_repo.within_identity_key_divergence()
        assert gauge["diverging_keys"] == 0
        assert gauge["rows_in_diverging_groups"] == 0

    def test_a_null_project_id_forms_its_own_key(self, skill_repo) -> None:
        """``NULL`` ``project_id`` (global skills) groups with itself."""
        _skill(skill_repo, name="global-skill", content="v1", project_id=None, generation=0)
        _skill(skill_repo, name="global-skill", content="v2", project_id=None, generation=1)
        _skill(skill_repo, name="global-skill", content="v1", project_id="p1")
        gauge = skill_repo.within_identity_key_divergence()
        assert gauge["diverging_keys"] == 1
        assert gauge["keys"][0]["project_id"] is None

    def test_inactive_generations_are_in_scope(self, skill_repo) -> None:
        """The census population is ALL rows — the residual-7 explanation.

        A key whose single ACTIVE row coexists with a differing INACTIVE
        generation (a resolved A/B's loser, or a superseded generation)
        diverges.  That is why 32 > 25 and it is deliberate: the gauge
        measures the same population the baseline was measured on.
        """
        _skill(
            skill_repo,
            name="dev-strategy",
            content="loser body",
            project_id="p1",
            generation=0,
            is_active=False,
        )
        _skill(
            skill_repo,
            name="dev-strategy",
            content="winner body",
            project_id="p1",
            generation=1,
            is_active=True,
        )
        gauge = skill_repo.within_identity_key_divergence()
        assert gauge["diverging_keys"] == 1
        assert gauge["keys"][0]["rows"] == 2


class TestTheGaugeReportsTheShape:
    """The payload shape the acceptance reads."""

    def test_the_counts_and_the_enumeration_agree(self, skill_repo) -> None:
        _skill(skill_repo, name="a", content="1", project_id="p1", generation=0)
        _skill(skill_repo, name="a", content="2", project_id="p1", generation=1)
        _skill(skill_repo, name="b", content="1", project_id="p1", generation=0)
        _skill(skill_repo, name="b", content="2", project_id="p1", generation=1)
        _skill(skill_repo, name="b", content="3", project_id="p1", generation=2)
        _skill(skill_repo, name="c", content="only", project_id="p1")
        gauge = skill_repo.within_identity_key_divergence()
        assert gauge["diverging_keys"] == 2
        assert gauge["rows_in_diverging_groups"] == 5
        assert gauge["total_skills"] == 6
        assert [key["name"] for key in gauge["keys"]] == ["b", "a"], (
            "widest divergence first"
        )
        assert gauge["truncated"] is False

    def test_the_enumeration_is_bounded_and_says_so(self, skill_repo) -> None:
        for index in range(5):
            _skill(
                skill_repo, name=f"s{index}", content="1", project_id="p1", generation=0
            )
            _skill(
                skill_repo, name=f"s{index}", content="2", project_id="p1", generation=1
            )
        gauge = skill_repo.within_identity_key_divergence(limit=2)
        assert gauge["diverging_keys"] == 5, "the COUNTS are never truncated"
        assert len(gauge["keys"]) == 2
        assert gauge["truncated"] is True

    def test_an_empty_registry_reports_zero(self, skill_repo) -> None:
        gauge = skill_repo.within_identity_key_divergence()
        assert gauge == {
            "diverging_keys": 0,
            "rows_in_diverging_groups": 0,
            "total_skills": 0,
            "keys": [],
            "truncated": False,
        }


class TestTheGaugeIsDialectSafe:
    """A4's dialect-safety requirement — pure ORM, no raw SQL."""

    def test_the_gauge_uses_the_orm_only(self) -> None:
        source = inspect.getsource(SkillRepository.within_identity_key_divergence)
        for forbidden in ("text(", "text (", "execute(", "GROUP BY name HAVING"):
            assert forbidden not in source, (
                f"the gauge must stay dialect-safe (PostgreSQL primary, SQLite "
                f"compatible) — {forbidden!r} found"
            )
        assert "group_by" in source

    def test_the_gauge_groups_by_both_identity_columns(self) -> None:
        """M-A4d — dropping ``project_id`` from the grouping collapses the gauge."""
        source = inspect.getsource(SkillRepository.within_identity_key_divergence)
        assert "Skill.name" in source
        assert "Skill.project_id" in source
        group_clause = source[source.index("group_by") :]
        assert "Skill.name" in group_clause and "Skill.project_id" in group_clause, (
            "the grouping must be (name, project_id) — grouping on name alone "
            "is the retired global-name gauge"
        )


class TestTheBaselineAndTheBlockedTargetAreStated:
    """A4 acceptance 4/5 — the baseline, the cause, and the BLOCKED target."""

    def test_the_docstring_carries_the_measured_baseline(self) -> None:
        doc = inspect.getdoc(SkillRepository.within_identity_key_divergence) or ""
        assert "32" in doc, "the measured production baseline must be stated"
        assert "630" in doc, "the census population must ride with the number"

    def test_the_docstring_names_the_cause_and_the_blocked_target(self) -> None:
        doc = inspect.getdoc(SkillRepository.within_identity_key_divergence) or ""
        assert "27" in doc and "A/B" in doc, "the 27-pair cause must be named"
        assert "BLOCKED-ON" in doc.upper(), (
            "the 0 target must be explicitly marked BLOCKED-ON the A/B "
            "resolution decision — Phase 1 does NOT achieve 0"
        )

    def test_the_docstring_carries_the_32_vs_27_reconciliation(self) -> None:
        """The approver's arithmetic gap must be closed in prose, not ignored."""
        doc = inspect.getdoc(SkillRepository.within_identity_key_divergence) or ""
        assert "residual" in doc.lower(), (
            "the 32-vs-25 gap must be reconciled: 2 of the 27 pairs carry "
            "identical content, so the backlog explains at most 25 of the 32"
        )
        assert "25" in doc
        assert "identical" in doc.lower()

    def test_the_docstring_retires_the_global_name_gauge(self) -> None:
        doc = inspect.getdoc(SkillRepository.within_identity_key_divergence) or ""
        assert "non-defect" in doc.lower() or "NON-DEFECT" in doc, (
            "global-name divergence must be stated as a non-defect"
        )
        assert "dedupe" in doc.lower(), (
            "the docstring must warn against name-level dedupe"
        )

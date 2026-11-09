"""E4 — the stale/shadow DB skill deactivation mechanism (plan §9 E4).

Workstream E4, skill-system-revival Phase 1.

WHAT IS PINNED HERE

* the DUAL-COLUMN WRITER — one flag, both columns, one repository call;
* the A/B-STRAND REFUSAL — a real attempted flip is refused, the refusal is
  enumerated in the receipt with the BLOCKING TEST id, and the R1 rows stay
  ACTIVE;
* the REVERSAL — ``--reactivate`` restores the exact prior PAIR (both
  columns), and the PROCESS-ONCE SUPPRESSION keeps the next boot from
  undoing it;
* the RECEIPT — its location (the data dir, never the repo), its
  completeness (every touched row has an entry) and its dry-run default;
* E4.3 — an R1-shaped row is NEVER injected (not in ``injected``, not in
  ``low_match``) even when E4.1 has not run;
* the PAIR-RESOLVER — available, but it REFUSES to act without an explicit
  winner decision (NO winner is declared in Phase 1);
* the E-7 BOOT HOOK — the reconciliation runs AFTER ``_ensure_postgres_columns``
  and NEVER inside it;
* the ARTIFACT SKILL — the plan-verifier tooling artifact carries a declared
  disposition and is EXCLUDED from injection in CODE.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.skill.models import Skill
from daemon.repositories.skill.repository import (
    SkillABTestRepository,
    SkillRepository,
)
from daemon.manager import InstanceManager
from daemon.services import skill_deactivation as e4
from daemon.services.skill_deactivation import (
    CANONICAL_INACTIVE,
    REFUSAL_NO_WINNER_DECISION,
    REFUSAL_OPEN_AB_TEST,
    RULE_DECLARED,
    RULE_R1,
    RULE_R2,
    RULE_R3,
    RULE_R5,
    SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV,
    PairResolutionRefused,
    SkillStaleDeactivator,
    deactivate_skill,
    is_open_ab_shadowed,
    is_stale_deactivation_enabled,
    load_receipt,
    resolve_receipt_path,
    write_skill_pair,
)
from daemon.services.skill_deactivation_manifest import (
    match_declared_disposition,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
E4_MODULE = REPO_ROOT / "daemon" / "services" / "skill_deactivation.py"
MANIFEST_MODULE = (
    REPO_ROOT / "daemon" / "services" / "skill_deactivation_manifest.py"
)
INJECTION_MODULE = (
    REPO_ROOT / "daemon" / "services" / "skill_injection_service.py"
)
MANAGER_MODULE = REPO_ROOT / "daemon" / "manager.py"
OPERATOR_SCRIPT = REPO_ROOT / "scripts" / "audit" / "skill_stale_deactivate.py"

#: The plan's canonical R1 instance (the PROOF fixture — the RULE stays
#: rule-based; DS-11 forbids an id list as the deactivation predicate).
R1_SKILL_A = "08a4332f-c059-4886-92d2-9d4aea191ff8"
R1_SKILL_B = "fd1e01dc-37d5-4a0b-a05e-1933d18a006d"
R1_GROUP = "2600329e-0000-4000-8000-000000000001"
R1_TEST_ID = "083d5a58-0000-4000-8000-000000000002"


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture()
def db():
    """An in-memory SQLite skill DB (the house unit-test shape)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def file_db(tmp_path):
    """A FILE-backed SQLite skill DB (the operator script opens its own)."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'skills.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def repos(db):
    """(skill_repo, ab_test_repo) over the fixture DB."""
    return (SkillRepository(db), SkillABTestRepository(db))


@pytest.fixture()
def data_dir(tmp_path):
    """An explicit data dir (the receipt must land here, never in the repo)."""
    target = tmp_path / "data"
    target.mkdir()
    return target


def _make_skill(
    skill_repo: SkillRepository,
    *,
    name: str = "regression-ab-partition-run",
    project_id: str = "agents-ensemble",
    ab_test_group: str | None = None,
    status: str = "active",
    is_active: bool = True,
    lineage_origin: str = "imported",
    content: str = "body",
    generation: int | None = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "status": status,
        "is_active": is_active,
    }
    if ab_test_group:
        kwargs["ab_test_group"] = ab_test_group
    if generation is None:
        # The ``skills`` table is unique on ``(project_id, name, generation)``
        # — auto-assign the next generation for the name so a fixture can
        # create same-name rows (the R1/R3 shadowing shape).
        with Session(skill_repo.engine) as session:
            existing = list(
                session.exec(
                    select(Skill).where(
                        Skill.project_id == project_id,
                        Skill.name == name,
                    )
                )
            )
        generation = len(existing)
    return skill_repo.create(
        name=name,
        description="d",
        content=content,
        project_id=project_id,
        lineage_origin=lineage_origin,
        generation=generation,
        **kwargs,
    )


def _set_created_at(skill_repo: SkillRepository, skill_id: str, value: str):
    """Force a row's ``created_at`` (the R3 'newer sibling' discriminator)."""
    with Session(skill_repo.engine) as session:
        row = session.get(Skill, skill_id)
        row.created_at = value
        session.commit()


def _seed_r1_pair(skill_repo: SkillRepository, ab_test_repo: SkillABTestRepository):
    """Seed the plan's canonical R1 instance (two active same-name rows)."""
    older = _make_skill(
        skill_repo,
        name="regression-ab-partition-run",
        ab_test_group=R1_GROUP,
        status="ab_testing",
        lineage_origin="evolved",
        generation=1,
    )
    newer = _make_skill(
        skill_repo,
        name="regression-ab-partition-run",
        ab_test_group=R1_GROUP,
        status="ab_testing",
        lineage_origin="evolved",
        generation=2,
    )
    _set_created_at(skill_repo, older.id, "2026-10-21T17:45:48.000Z")
    _set_created_at(skill_repo, newer.id, "2026-10-22T15:58:20.000Z")
    test = ab_test_repo.create_ab_test(R1_GROUP, older.id, newer.id)
    _set_test_id(ab_test_repo, test.id, R1_TEST_ID)
    return (older, newer, test)


def _set_test_id(ab_test_repo: SkillABTestRepository, test_id: str, value: str):
    """Force the A/B test row's id (the plan's canonical blocking-test id)."""
    from daemon.repositories.skill.models import SkillABTest

    with Session(ab_test_repo.engine) as session:
        row = session.get(SkillABTest, test_id)
        row.id = value
        session.commit()


def _deactivator(repos, data_dir) -> SkillStaleDeactivator:
    skill_repo, ab_test_repo = repos
    return SkillStaleDeactivator(
        skill_repo=skill_repo,
        ab_test_repo=ab_test_repo,
        data_dir=data_dir,
    )


# ============================================================
# The dual-column writer
# ============================================================


class TestTheDualColumnWriter:
    """R3.2 / DS-39 — ONE flag writes BOTH columns; never one alone."""

    def test_deactivate_sets_both_columns_from_one_flag(self, repos):
        skill_repo, _ = repos
        row = _make_skill(skill_repo, name="dual-column")
        assert (row.is_active, row.status) == (True, "active")

        deactivate_skill(skill_repo, row.id)

        after = skill_repo.get(row.id)
        assert after.is_active is False
        assert after.status == "inactive"

    def test_the_pair_writer_issues_exactly_one_repository_call(self, repos):
        skill_repo, _ = repos
        row = _make_skill(skill_repo, name="one-call")
        calls: list[dict[str, Any]] = []

        original = skill_repo.update

        def _spy(skill_id, **fields):
            calls.append({"skill_id": skill_id, **fields})
            return original(skill_id, **fields)

        skill_repo.update = _spy  # type: ignore[method-assign]
        try:
            write_skill_pair(
                skill_repo, row.id, is_active=False, status="inactive"
            )
        finally:
            skill_repo.update = original  # type: ignore[method-assign]

        assert len(calls) == 1
        assert calls[0]["is_active"] is False
        assert calls[0]["status"] == "inactive"

    def test_no_code_path_writes_one_column_without_the_other(self):
        """Source scan: every E4 write of ``is_active``/``status`` pairs them."""
        for path in (E4_MODULE, MANIFEST_MODULE):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                keywords = {
                    kw.arg for kw in node.keywords if kw.arg is not None
                }
                if not keywords & {"is_active", "status"}:
                    continue
                assert keywords >= {"is_active", "status"}, (
                    f"{path.name}:{node.lineno} writes {sorted(keywords)} "
                    f"— a single-column skill write is forbidden"
                )

    def test_the_sanctioned_repository_writer_sets_both_columns(self):
        """The house writer (``SkillRepository.deactivate``) sets both."""
        source = (
            REPO_ROOT
            / "daemon"
            / "repositories"
            / "skill"
            / "repository.py"
        ).read_text(encoding="utf-8")
        assert 'update(skill_id, is_active=False, status="inactive")' in source


# ============================================================
# The A/B-strand refusal
# ============================================================


class TestTheAbStrandRefusal:
    """R1 — an unresolved A/B row is REFUSED, not flipped."""

    def test_a_real_attempted_flip_is_refused(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        older, newer, _test = _seed_r1_pair(skill_repo, ab_test_repo)

        report = _deactivator(repos, data_dir).reconcile(dry_run=True)

        refused_ids = {item.skill_id for item in report.refusals}
        assert older.id in refused_ids
        assert newer.id in refused_ids
        for row_id in (older.id, newer.id):
            after = skill_repo.get(row_id)
            assert after.is_active is True
            assert after.status == "ab_testing"

    def test_the_receipt_enumerates_the_refusal_with_the_blocking_test_id(
        self, repos, data_dir
    ):
        skill_repo, ab_test_repo = repos
        _seed_r1_pair(skill_repo, ab_test_repo)

        report = _deactivator(repos, data_dir).reconcile(dry_run=False)

        assert report.refused_count == 2
        for refusal in report.refusals:
            assert refusal.reason == REFUSAL_OPEN_AB_TEST
            assert refusal.blocking_test_id == R1_TEST_ID
            assert refusal.blocking_test_group == R1_GROUP

        receipt = load_receipt(_deactivator(repos, data_dir).receipt_path)
        assert len(receipt.refusals) == 2
        assert {r.skill_id for r in receipt.refusals} == {
            r.skill_id for r in report.refusals
        }
        assert all(r.blocking_test_id == R1_TEST_ID for r in receipt.refusals)

    def test_the_r1_rows_are_still_active_after_an_applied_run(
        self, repos, data_dir
    ):
        skill_repo, ab_test_repo = repos
        older, newer, _test = _seed_r1_pair(skill_repo, ab_test_repo)

        _deactivator(repos, data_dir).reconcile(dry_run=False)

        for row_id in (older.id, newer.id):
            row = skill_repo.get(row_id)
            assert row.is_active is True
            assert row.status == "ab_testing"

    def test_the_canonical_fixture_ids_are_covered_by_the_rule(
        self, repos, data_dir
    ):
        """The plan's canonical ids are the PROOF fixture for the RULE."""
        skill_repo, ab_test_repo = repos
        older, newer, _test = _seed_r1_pair(skill_repo, ab_test_repo)

        shadowed = e4.find_open_ab_shadowed_skills(skill_repo, ab_test_repo)

        assert {item.skill_id for item in shadowed} == {older.id, newer.id}


# ============================================================
# No hard delete / reversibility / suppression
# ============================================================


class TestReversibility:
    """E4.4 — every touched row is recorded and exactly reversible."""

    def test_the_mechanism_never_hard_deletes(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        # A newer active same-name sibling makes it a superseded legacy copy.
        newer = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        _set_created_at(skill_repo, row.id, "2026-01-01T00:00:00.000Z")
        _set_created_at(skill_repo, newer.id, "2026-02-01T00:00:00.000Z")

        _deactivator(repos, data_dir).reconcile(dry_run=False)

        assert skill_repo.get(row.id) is not None  # NOT deleted
        source = E4_MODULE.read_text(encoding="utf-8")
        assert ".delete(" not in source

    def test_reactivate_restores_the_exact_prior_pair(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="ab_testing",
        )
        newer = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        _set_created_at(skill_repo, row.id, "2026-01-01T00:00:00.000Z")
        _set_created_at(skill_repo, newer.id, "2026-02-01T00:00:00.000Z")

        deactivator = _deactivator(repos, data_dir)
        report = deactivator.reconcile(dry_run=False)
        assert [entry.skill_id for entry in report.entries] == [row.id]

        after = skill_repo.get(row.id)
        assert (after.is_active, after.status) == CANONICAL_INACTIVE

        deactivator.reactivate(apply=True)

        restored = skill_repo.get(row.id)
        assert restored.is_active is True
        assert restored.status == "ab_testing"

    def test_apply_then_reactivate_then_boot_does_not_re_deactivate(
        self, repos, data_dir
    ):
        """The process-once suppression survives the reversal."""
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        newer = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        _set_created_at(skill_repo, row.id, "2026-01-01T00:00:00.000Z")
        _set_created_at(skill_repo, newer.id, "2026-02-01T00:00:00.000Z")

        deactivator = _deactivator(repos, data_dir)
        deactivator.reconcile(dry_run=False)
        deactivator.reactivate(apply=True)

        # A NEW process (a fresh deactivator = a fresh boot) reads the
        # receipt from the data dir and honours the suppression.
        second = _deactivator(repos, data_dir)
        report = second.reconcile(dry_run=False)

        assert report.touched_count == 0
        assert row.id in report.suppressed
        restored = skill_repo.get(row.id)
        assert restored.is_active is True
        assert restored.status == "active"

    def test_the_receipt_keeps_the_suppression_after_a_reactivation(
        self, repos, data_dir
    ):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        newer = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        _set_created_at(skill_repo, row.id, "2026-01-01T00:00:00.000Z")
        _set_created_at(skill_repo, newer.id, "2026-02-01T00:00:00.000Z")

        deactivator = _deactivator(repos, data_dir)
        deactivator.reconcile(dry_run=False)
        deactivator.reactivate(apply=True)

        receipt = load_receipt(deactivator.receipt_path)
        assert receipt.entries[0].reactivated_at is not None
        assert receipt.is_suppressed(row.id, RULE_R3)


# ============================================================
# The rules themselves
# ============================================================


class TestTheRules:
    """R1/R2/R3/R5 — rule-based, never an id list."""

    def test_r2_identifies_the_captured_origin_never_used_rows(
        self, repos, data_dir
    ):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="captured-never-used",
            lineage_origin="captured",
            is_active=False,
            status="inactive",
        )

        report = _deactivator(repos, data_dir).reconcile(dry_run=True)

        observed = [o for o in report.observations if o.rule_id == RULE_R2]
        assert [o.skill_id for o in observed] == [row.id]
        assert observed[0].outcome == "already_inactive"

    def test_r2_never_re_activates_a_captured_row(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="captured-never-used",
            lineage_origin="captured",
            is_active=False,
            status="inactive",
        )

        _deactivator(repos, data_dir).reconcile(dry_run=False)

        after = skill_repo.get(row.id)
        assert after.is_active is False

    def test_r3_deactivates_a_superseded_legacy_gate_runner_copy(
        self, repos, data_dir
    ):
        skill_repo, ab_test_repo = repos
        older = _make_skill(
            skill_repo,
            name="regression-ab-partition-run",
            content="the legacy gate-runner copy",
            status="active",
        )
        newer = _make_skill(
            skill_repo,
            name="regression-ab-partition-run",
            content="the legacy gate-runner copy",
            status="active",
        )
        _set_created_at(skill_repo, older.id, "2026-01-01T00:00:00.000Z")
        _set_created_at(skill_repo, newer.id, "2026-02-01T00:00:00.000Z")

        report = _deactivator(repos, data_dir).reconcile(dry_run=False)

        touched = {entry.skill_id for entry in report.entries}
        assert older.id in touched
        assert newer.id not in touched
        after = skill_repo.get(older.id)
        assert (after.is_active, after.status) == CANONICAL_INACTIVE

    def test_r5_normalises_the_desynced_inactive_pair(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="desynced",
            is_active=False,
            status="ab_testing",
        )

        report = _deactivator(repos, data_dir).reconcile(dry_run=False)

        assert [entry.skill_id for entry in report.entries] == [row.id]
        assert report.entries[0].rule_id == RULE_R5
        assert report.entries[0].before.status == "ab_testing"
        after = skill_repo.get(row.id)
        assert (after.is_active, after.status) == CANONICAL_INACTIVE

    def test_r5_never_flips_an_active_row(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo, name="active-ab", is_active=True, status="ab_testing"
        )

        _deactivator(repos, data_dir).reconcile(dry_run=False)

        after = skill_repo.get(row.id)
        assert after.is_active is True
        assert after.status == "ab_testing"


# ============================================================
# The receipt
# ============================================================


class TestTheReceipt:
    """E4.4 — the receipt is the reversible record of every touched row."""

    def test_the_receipt_lives_in_the_data_dir_not_the_repo(
        self, data_dir, monkeypatch
    ):
        monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(data_dir))
        path = resolve_receipt_path()
        assert path.parent == data_dir
        assert "site-packages" not in str(path)

    def test_a_dry_run_writes_no_row_and_no_receipt(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        newer = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        _set_created_at(skill_repo, row.id, "2026-01-01T00:00:00.000Z")
        _set_created_at(skill_repo, newer.id, "2026-02-01T00:00:00.000Z")

        deactivator = _deactivator(repos, data_dir)
        report = deactivator.reconcile(dry_run=True)

        assert report.dry_run is True
        assert not deactivator.receipt_path.exists()
        after = skill_repo.get(row.id)
        assert after.is_active is True

    def test_every_touched_row_has_a_receipt_entry(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        rows = []
        for index in range(3):
            project = f"project-{index}"
            row = _make_skill(
                skill_repo,
                name="failed-digest-equality-gate",
                project_id=project,
                content="a legacy gate-runner copy",
                status="active",
            )
            newer = _make_skill(
                skill_repo,
                name="failed-digest-equality-gate",
                project_id=project,
                content="a legacy gate-runner copy",
                status="active",
            )
            _set_created_at(
                skill_repo, row.id, f"2026-01-0{index + 1}T00:00:00.000Z"
            )
            _set_created_at(skill_repo, newer.id, "2026-06-01T00:00:00.000Z")
            rows.append(row)

        deactivator = _deactivator(repos, data_dir)
        report = deactivator.reconcile(dry_run=False)

        receipt = load_receipt(deactivator.receipt_path)
        receipt_ids = {entry.skill_id for entry in receipt.entries}
        assert receipt_ids == {entry.skill_id for entry in report.entries}
        assert receipt_ids == {row.id for row in rows}

    def test_the_second_run_changes_nothing(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        newer = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        _set_created_at(skill_repo, row.id, "2026-01-01T00:00:00.000Z")
        _set_created_at(skill_repo, newer.id, "2026-02-01T00:00:00.000Z")

        deactivator = _deactivator(repos, data_dir)
        first = deactivator.reconcile(dry_run=False)
        second = deactivator.reconcile(dry_run=False)

        assert first.touched_count == 1
        assert second.touched_count == 0

    def test_the_refusal_list_is_replaced_not_appended_across_boots(
        self, repos, data_dir
    ):
        """The every-boot sweep must not grow the receipt without bound."""
        skill_repo, ab_test_repo = repos
        _seed_r1_pair(skill_repo, ab_test_repo)

        deactivator = _deactivator(repos, data_dir)
        deactivator.reconcile(dry_run=False)
        deactivator.reconcile(dry_run=False)

        receipt = load_receipt(deactivator.receipt_path)
        assert len(receipt.refusals) == 2

    def test_the_receipt_entry_carries_the_prior_pair(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="ab_testing",
        )
        newer = _make_skill(
            skill_repo,
            name="failed-digest-equality-gate",
            content="a legacy gate-runner copy",
            status="active",
        )
        _set_created_at(skill_repo, row.id, "2026-01-01T00:00:00.000Z")
        _set_created_at(skill_repo, newer.id, "2026-02-01T00:00:00.000Z")

        deactivator = _deactivator(repos, data_dir)
        deactivator.reconcile(dry_run=False)

        payload = json.loads(
            deactivator.receipt_path.read_text(encoding="utf-8")
        )
        entry = payload["entries"][0]
        assert entry["before"] == {"is_active": True, "status": "ab_testing"}
        assert entry["after"] == {"is_active": False, "status": "inactive"}
        assert entry["rule_id"] == RULE_R3
        assert entry["skill_id"] == row.id
        assert entry["run_id"]
        assert entry["touched_at_iso"]


# ============================================================
# E4.3 — the injection-side defence
# ============================================================


class _FakeSkill:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.name = kwargs.get("name")
        self.project_id = kwargs.get("project_id")
        self.is_active = kwargs.get("is_active", True)
        self.status = kwargs.get("status", "active")
        self.ab_test_group = kwargs.get("ab_test_group")
        self.description = kwargs.get("description", "")


class _FakeAbTestRepo:
    def __init__(self, tests):
        self._tests = tests

    def get_by_group(self, group):
        return self._tests.get(group)


class _FakeSkillRepo:
    def __init__(self, variants=None, active_variant=None):
        self._variants = variants or []
        self._active_variant = active_variant

    def get_ab_variants(self, group):
        return list(self._variants)

    def get_active_variant(self, project_id, name):
        return self._active_variant


class _FakeSearchService:
    def __init__(self, injected, low_match):
        self._injected = injected
        self._low_match = low_match

    async def search(self, *args, **kwargs):
        return {
            "injected": list(self._injected),
            "low_match": list(self._low_match),
        }


class _FakeConfig:
    max_inject_skills = 2


class TestTheInjectionSideDefence:
    """E4.3 — nothing stale can be injected, even before E4.1 runs."""

    @pytest.mark.asyncio
    async def test_an_r1_shaped_row_is_never_injected(self):
        from daemon.services.skill_injection_service import (
            SkillInjectionService,
        )

        row = _FakeSkill(
            id=R1_SKILL_A,
            name="regression-ab-partition-run",
            project_id="agents-ensemble",
            is_active=True,
            status="ab_testing",
            ab_test_group=R1_GROUP,
        )
        sibling = _FakeSkill(
            id=R1_SKILL_B,
            name="regression-ab-partition-run",
            project_id="agents-ensemble",
            is_active=True,
            status="ab_testing",
            ab_test_group=R1_GROUP,
        )
        test = _FakeSkill(
            id=R1_TEST_ID, name="ab", ab_test_group=R1_GROUP, resolved_at=None
        )
        service = SkillInjectionService(
            search_service=_FakeSearchService(
                [{"skill": row, "score": 0.9}],
                [{"name": row.name, "score": 0.2, "description": "d"}],
            ),
            config=_FakeConfig(),
            ab_test_repo=_FakeAbTestRepo({R1_GROUP: test}),
            skill_repo=_FakeSkillRepo(
                variants=[row, sibling], active_variant=row
            ),
        )

        text, ids = await service.inject_skills(
            "hello", "agents-ensemble", "inst-1", "msg-1"
        )

        assert ids == []
        assert text is None

    @pytest.mark.asyncio
    async def test_an_inactive_row_is_never_injected(self):
        from daemon.services.skill_injection_service import (
            SkillInjectionService,
        )

        row = _FakeSkill(
            id="desynced", name="desynced", is_active=False, status="ab_testing"
        )
        service = SkillInjectionService(
            search_service=_FakeSearchService(
                [{"skill": row, "score": 0.9}], []
            ),
            config=_FakeConfig(),
            ab_test_repo=_FakeAbTestRepo({}),
            skill_repo=_FakeSkillRepo(),
        )

        text, ids = await service.inject_skills(
            "hello", "p", "inst-1", "msg-1"
        )

        assert ids == []
        assert text is None

    @pytest.mark.asyncio
    async def test_the_declared_artifact_skill_is_never_injected(self):
        from daemon.services.skill_injection_service import (
            SkillInjectionService,
        )

        row = _FakeSkill(
            id="c0c427a0-1111-2222-3333-444444444444",
            name="adversarial-plan-ledger-verification",
            is_active=True,
            status="active",
        )
        service = SkillInjectionService(
            search_service=_FakeSearchService(
                [{"skill": row, "score": 0.9}], []
            ),
            config=_FakeConfig(),
            ab_test_repo=_FakeAbTestRepo({}),
            skill_repo=_FakeSkillRepo(),
        )

        text, ids = await service.inject_skills(
            "hello", "p", "inst-1", "msg-1"
        )

        assert ids == []
        assert text is None

    def test_the_declared_disposition_is_carried_by_the_manifest(self):
        row = _FakeSkill(
            id="c0c427a0-1111-2222-3333-444444444444",
            name="adversarial-plan-ledger-verification",
        )
        declared = match_declared_disposition(row)
        assert declared is not None
        assert declared.injection == "excluded"
        assert declared.deactivation == "operator_path_only"
        assert "not a skill" in declared.disposition


# ============================================================
# The candidate-set manifest
# ============================================================


class TestTheCandidateManifest:
    """The manifest carries every candidate WITH its disposition."""

    def test_the_manifest_carries_the_artifact_with_its_disposition(
        self, repos, data_dir
    ):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="adversarial-plan-ledger-verification",
            status="active",
        )

        deactivator = _deactivator(repos, data_dir)
        report = deactivator.reconcile(dry_run=False)

        assert deactivator.candidate_manifest_path.exists()
        manifest = json.loads(
            deactivator.candidate_manifest_path.read_text(encoding="utf-8")
        )
        declared_rows = [
            item
            for item in manifest["candidates"]
            if item.get("rule_id") == RULE_DECLARED
        ]
        assert len(declared_rows) == 1
        assert declared_rows[0]["skill_id"] == row.id
        assert declared_rows[0]["injection"] == "excluded"
        assert declared_rows[0]["deactivation"] == "operator_path_only"

    def test_a_declared_artifact_is_never_deactivated(self, repos, data_dir):
        skill_repo, ab_test_repo = repos
        row = _make_skill(
            skill_repo,
            name="adversarial-plan-ledger-verification",
            is_active=False,
            status="ab_testing",
        )

        _deactivator(repos, data_dir).reconcile(dry_run=False)

        after = skill_repo.get(row.id)
        assert after.is_active is False
        assert after.status == "ab_testing"


# ============================================================
# The pair resolver
# ============================================================


class TestThePairResolver:
    """The mechanism exists — and REFUSES without a winner decision."""

    def test_the_pair_resolver_refuses_without_a_winner_decision(
        self, repos, data_dir
    ):
        skill_repo, ab_test_repo = repos
        _seed_r1_pair(skill_repo, ab_test_repo)

        deactivator = _deactivator(repos, data_dir)
        with pytest.raises(PairResolutionRefused) as excinfo:
            deactivator.resolve_pair(ab_test_group=R1_GROUP)

        assert excinfo.value.reason == REFUSAL_NO_WINNER_DECISION

    def test_the_pair_resolver_applies_the_pair_as_one_reversible_unit(
        self, repos, data_dir
    ):
        skill_repo, ab_test_repo = repos
        older, newer, test = _seed_r1_pair(skill_repo, ab_test_repo)

        deactivator = _deactivator(repos, data_dir)
        result = deactivator.resolve_pair(
            ab_test_group=R1_GROUP,
            winner_skill_id=newer.id,
            apply=True,
        )

        assert result["applied"] is True
        resolved = ab_test_repo.get_by_group(R1_GROUP)
        assert resolved.resolved_at is not None
        assert resolved.winner_skill_id == newer.id
        loser = skill_repo.get(older.id)
        assert (loser.is_active, loser.status) == CANONICAL_INACTIVE
        winner = skill_repo.get(newer.id)
        assert winner.is_active is True

    def test_no_winner_is_declared_by_the_shipped_code(self):
        """No shipped call site supplies a winner (Phase 1 declares none)."""
        sources = [
            E4_MODULE.read_text(encoding="utf-8"),
            INJECTION_MODULE.read_text(encoding="utf-8"),
            OPERATOR_SCRIPT.read_text(encoding="utf-8"),
        ]
        for source in sources:
            assert "winner_skill_id=" not in source or (
                "winner_skill_id: Optional[str] = None" in source
            )


# ============================================================
# The E-7 boot hook
# ============================================================


class TestTheBootHook:
    """E-7 — the reconciliation's wiring site and its ordering."""

    def test_the_hook_runs_after_the_ensure_columns_call(self):
        source = MANAGER_MODULE.read_text(encoding="utf-8")
        ensure_at = source.index("self._ensure_postgres_columns()")
        migrations_at = source.index("run_pending_migrations()")
        hook_at = source.index(
            "self._run_skill_stale_deactivation_reconciliation()"
        )
        assert hook_at > ensure_at
        assert hook_at > migrations_at

    def test_the_hook_is_adjacent_to_the_injection_capability_gate(self):
        source = MANAGER_MODULE.read_text(encoding="utf-8")
        gate_line = next(
            index
            for index, line in enumerate(source.splitlines())
            if "self._wire_skill_injection_capability_gate()" in line
        )
        hook_line = next(
            index
            for index, line in enumerate(source.splitlines())
            if "self._run_skill_stale_deactivation_reconciliation()" in line
        )
        assert 0 < hook_line - gate_line < 60

    def test_the_hook_is_never_inside_the_ensure_columns_list(self):
        source = MANAGER_MODULE.read_text(encoding="utf-8")
        ensure_at = source.index("def _ensure_postgres_columns")
        ensure_end = source.index("\n    def ", ensure_at + 10)
        hook_at = source.index(
            "self._run_skill_stale_deactivation_reconciliation()"
        )
        assert not (ensure_at < hook_at < ensure_end)

    def test_the_boot_reconciliation_is_fail_soft(self):
        source = MANAGER_MODULE.read_text(encoding="utf-8")
        method_at = source.index(
            "def _run_skill_stale_deactivation_reconciliation"
        )
        block = source[method_at: method_at + 3000]
        assert "except Exception" in block
        assert "return" in block


# ============================================================
# The operator script
# ============================================================


class TestTheOperatorScript:
    """E4.2 — dry-run by default, --apply to commit, --reactivate to undo."""

    def test_the_script_refuses_to_guess_a_connection(self):
        source = OPERATOR_SCRIPT.read_text(encoding="utf-8")
        assert '"--db-url"' in source
        assert "required=True" in source

    def test_the_script_is_dry_run_by_default(self):
        source = OPERATOR_SCRIPT.read_text(encoding="utf-8")
        assert 'action="store_true"' in source
        assert "reconcile(dry_run=not args.apply)" in source

    def test_the_script_exposes_the_reactivation_path(self):
        source = OPERATOR_SCRIPT.read_text(encoding="utf-8")
        assert '"--reactivate"' in source
        assert "deactivator.reactivate(" in source

    def test_the_script_dry_run_writes_no_row_and_no_receipt(
        self, file_db, data_dir, capsys
    ):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "skill_stale_deactivate_script", OPERATOR_SCRIPT
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        skill_repo = SkillRepository(file_db)
        row = _make_skill(
            skill_repo,
            name="desynced-script",
            is_active=False,
            status="ab_testing",
        )
        db_url = skill_repo.engine.url.render_as_string(
            hide_password=False
        )

        rc = module.main(
            ["--db-url", db_url, "--data-dir", str(data_dir)]
        )
        assert rc == 0
        assert not (data_dir / "skill_stale_deactivation_receipt.json").exists()
        after = skill_repo.get(row.id)
        assert after.status == "ab_testing"
        assert after.is_active is False

    def test_the_script_reactivate_restores_the_prior_pair(
        self, file_db, data_dir
    ):
        """The operator reversal path: apply → --reactivate → the pair is back."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "skill_stale_deactivate_script_reactivate", OPERATOR_SCRIPT
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        skill_repo = SkillRepository(file_db)
        row = _make_skill(
            skill_repo,
            name="desynced-script-reactivate",
            is_active=False,
            status="ab_testing",
        )
        db_url = skill_repo.engine.url.render_as_string(
            hide_password=False
        )

        assert (
            module.main(
                ["--db-url", db_url, "--data-dir", str(data_dir), "--apply"]
            )
            == 0
        )
        assert skill_repo.get(row.id).status == "inactive"

        receipt = data_dir / "skill_stale_deactivation_receipt.json"
        assert (
            module.main(
                [
                    "--db-url",
                    db_url,
                    "--data-dir",
                    str(data_dir),
                    "--apply",
                    "--reactivate",
                    str(receipt),
                ]
            )
            == 0
        )
        restored = skill_repo.get(row.id)
        assert restored.is_active is False
        assert restored.status == "ab_testing"

    def test_the_script_apply_writes_the_receipt_and_the_row(
        self, file_db, data_dir
    ):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "skill_stale_deactivate_script_apply", OPERATOR_SCRIPT
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        skill_repo = SkillRepository(file_db)
        row = _make_skill(
            skill_repo,
            name="desynced-script-apply",
            is_active=False,
            status="ab_testing",
        )
        db_url = skill_repo.engine.url.render_as_string(
            hide_password=False
        )

        rc = module.main(
            [
                "--db-url",
                db_url,
                "--data-dir",
                str(data_dir),
                "--apply",
            ]
        )
        assert rc == 0
        receipt = data_dir / "skill_stale_deactivation_receipt.json"
        assert receipt.exists()
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        assert payload["entries"][0]["skill_id"] == row.id
        after = skill_repo.get(row.id)
        assert (after.is_active, after.status) == CANONICAL_INACTIVE


# ============================================================
# The stale-deactivation brake (Task 1 / DS-54)
# ============================================================


class TestTheBrake:
    """The ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED brake (default 0 / off)."""

    def test_brake_resolution_order_and_tokens(self, monkeypatch):
        """Resolver checks env, defaults to False, recognizes on/off tokens, raises on invalid."""
        monkeypatch.delenv(
            SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, raising=False
        )
        assert is_stale_deactivation_enabled() is False

        for empty in ("", "   ", "\t"):
            monkeypatch.setenv(SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, empty)
            assert is_stale_deactivation_enabled() is False

        for off_token in ("0", "false", "no", "off", "FALSE", "Off"):
            monkeypatch.setenv(
                SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, off_token
            )
            assert is_stale_deactivation_enabled() is False

        for on_token in ("1", "true", "yes", "on", "TRUE", "Yes", "On"):
            monkeypatch.setenv(
                SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, on_token
            )
            assert is_stale_deactivation_enabled() is True

        for bad in ("2", "disabled", "enabled", "maybe"):
            monkeypatch.setenv(SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, bad)
            with pytest.raises(ValueError, match="Invalid value for"):
                is_stale_deactivation_enabled()

    def test_brake_absent_and_off_tokens_render_boot_pass_inert(
        self, file_db, data_dir, monkeypatch
    ):
        """When the brake is absent or OFF, the boot pass is FULLY INERT:

        - no receipt file created;
        - no row touched;
        - SkillStaleDeactivator is NOT constructed.

        This pin is also the target of mutation M-E4k (default flipped to ON -> RED).
        """
        skill_repo = SkillRepository(file_db)
        ab_test_repo = SkillABTestRepository(file_db)

        # Create a candidate row that R5 would touch if the pass ran
        row = _make_skill(
            skill_repo,
            name="inert-boot-candidate",
            is_active=False,
            status="ab_testing",
        )

        class DummyManager:
            _run_skill_stale_deactivation_reconciliation = (
                InstanceManager._run_skill_stale_deactivation_reconciliation
            )

            def __init__(self, s_repo, a_repo):
                self._skill_repo = s_repo
                self._skill_ab_test_repo = a_repo

        mgr = DummyManager(skill_repo, ab_test_repo)
        monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(data_dir))

        # Spy on SkillStaleDeactivator constructor to verify it is NEVER called
        constructed = []
        orig_init = SkillStaleDeactivator.__init__

        def spy_init(self_deactivator, *args, **kwargs):
            constructed.append(True)
            orig_init(self_deactivator, *args, **kwargs)

        monkeypatch.setattr(
            "daemon.services.skill_deactivation.SkillStaleDeactivator.__init__",
            spy_init,
        )

        receipt = data_dir / "skill_stale_deactivation_receipt.json"

        for off_val in (None, "0", "false", "off", "no"):
            if off_val is None:
                monkeypatch.delenv(
                    SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, raising=False
                )
            else:
                monkeypatch.setenv(
                    SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, off_val
                )

            mgr._run_skill_stale_deactivation_reconciliation()

            # The pass must be fully inert
            assert len(constructed) == 0, (
                f"SkillStaleDeactivator was constructed with brake={off_val!r}"
            )
            assert not receipt.exists(), (
                f"Receipt was written with brake={off_val!r}"
            )
            after = skill_repo.get(row.id)
            assert after.status == "ab_testing", (
                f"Row was modified with brake={off_val!r}"
            )
            assert after.is_active is False

    def test_brake_on_runs_boot_pass_and_writes_receipt(
        self, file_db, data_dir, monkeypatch
    ):
        """When the brake is ON, the pass runs as designed (receipt written, row normalized)."""
        skill_repo = SkillRepository(file_db)
        ab_test_repo = SkillABTestRepository(file_db)

        row = _make_skill(
            skill_repo,
            name="on-boot-candidate",
            is_active=False,
            status="ab_testing",
        )

        class DummyManager:
            _run_skill_stale_deactivation_reconciliation = (
                InstanceManager._run_skill_stale_deactivation_reconciliation
            )

            def __init__(self, s_repo, a_repo):
                self._skill_repo = s_repo
                self._skill_ab_test_repo = a_repo

        mgr = DummyManager(skill_repo, ab_test_repo)
        monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(data_dir))
        monkeypatch.setenv(SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, "1")

        receipt = data_dir / "skill_stale_deactivation_receipt.json"
        assert not receipt.exists()

        mgr._run_skill_stale_deactivation_reconciliation()

        assert receipt.exists()
        after = skill_repo.get(row.id)
        assert after.status == "inactive"
        assert after.is_active is False

    def test_brake_invalid_value_renders_pass_inert_and_boot_continues(
        self, file_db, data_dir, monkeypatch
    ):
        """An invalid env value raises ValueError inside resolver, which is caught fail-soft:

        boot continues, no exception escapes, and the pass remains inert.
        """
        skill_repo = SkillRepository(file_db)
        ab_test_repo = SkillABTestRepository(file_db)

        row = _make_skill(
            skill_repo,
            name="invalid-brake-candidate",
            is_active=False,
            status="ab_testing",
        )

        class DummyManager:
            _run_skill_stale_deactivation_reconciliation = (
                InstanceManager._run_skill_stale_deactivation_reconciliation
            )

            def __init__(self, s_repo, a_repo):
                self._skill_repo = s_repo
                self._skill_ab_test_repo = a_repo

        mgr = DummyManager(skill_repo, ab_test_repo)
        monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(data_dir))
        monkeypatch.setenv(
            SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, "invalid_typo_val"
        )

        constructed = []
        orig_init = SkillStaleDeactivator.__init__

        def spy_init(self_deactivator, *args, **kwargs):
            constructed.append(True)
            orig_init(self_deactivator, *args, **kwargs)

        monkeypatch.setattr(
            "daemon.services.skill_deactivation.SkillStaleDeactivator.__init__",
            spy_init,
        )

        receipt = data_dir / "skill_stale_deactivation_receipt.json"

        # Must not raise: fail-soft catch renders it inert
        mgr._run_skill_stale_deactivation_reconciliation()

        assert len(constructed) == 0
        assert not receipt.exists()
        after = skill_repo.get(row.id)
        assert after.status == "ab_testing"
        assert after.is_active is False

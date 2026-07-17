"""PostgreSQL parity check for the Skill Evolution System.

Phase 6 verifier. Verifies that:
1. SQLModel.metadata.create_all() works against a fresh PostgreSQL DB
2. SkillSeedService.seed_all() correctly inserts the 9 tester skill
   templates into the skill_bank table on PG (with project_id=NULL)
3. The seeding is idempotent — re-running does not duplicate rows
4. SkillCloneService.clone_on_miss_sync() correctly clones a template
   into a project-scoped skill row on PG (with all expected fields,
   including auto_load propagation from template)
5. Clone idempotency — second clone returns the same row

Connection: postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/ensemble_test

Run with: .venv/bin/python test/pg_skill_schema_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure project root is on sys.path so daemon.* imports work when
# invoked as a standalone script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import all SQLModel tables so metadata.create_all() picks them up.
# We need both the skill evolution tables and the skill_bank table.
from sqlmodel import SQLModel, create_engine  # noqa: E402

# Import models so they register on SQLModel.metadata
from daemon.repositories.skill import models as _skill_models  # noqa: F401, E402
from daemon.repositories.skill.repository import (  # noqa: E402
    SkillRepository,
)
from daemon.repositories.skill.skill_bank_repository import (  # noqa: E402
    SkillBankRepository,
)
from daemon.services.skill_clone_service import SkillCloneService  # noqa: E402
from daemon.services.skill_seed_service import SkillSeedService  # noqa: E402

PG_URL = "postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/ensemble_test"
AGENTS_DIR = PROJECT_ROOT / "agents"
TESTER_AGENT = "tester"


def _section(title: str) -> None:
    """Print a section banner."""
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def check_pg_connection() -> bool:
    """Verify we can connect to PG."""
    _section("STEP 0: PostgreSQL connection check")
    try:
        engine = create_engine(PG_URL, echo=False)
        with engine.connect() as conn:
            result = conn.execute(__import__("sqlalchemy").text("SELECT 1 AS ok"))
            row = result.first()
            print(f"PG connect OK: {row}")
        return True
    except Exception as e:
        print(f"PG connect FAILED: {e}")
        return False


def recreate_schema(engine) -> None:
    """Drop and recreate all tables for a clean test."""
    _section("STEP 1: Recreate schema (drop_all + create_all)")
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    # List created tables
    from sqlalchemy import inspect

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(f"Created {len(tables)} tables:")
    for t in sorted(tables):
        if "skill" in t:
            print(f"  - {t}")


def check_seeding(engine) -> bool:
    """Phase 3 verification: seed_all() inserts 9 tester templates."""
    _section("STEP 2: SkillSeedService.seed_all() against PG")

    bank_repo = SkillBankRepository(engine)
    service = SkillSeedService(skill_bank_repo=bank_repo, agents_dir=AGENTS_DIR)

    summary = service.seed_all()
    print(f"Seed summary: {summary}")

    expected_new = summary["new"]
    if expected_new == 0:
        print("WARN: 0 new templates seeded — tester/skill-set.yaml may be malformed.")
        return False

    items = bank_repo.list_items(category="tester-skill-set")
    print(f"\ntester-skill-set items: {len(items)}")
    for item in items:
        print(
            f"  - {item.name:35s} "
            f"auto_load={item.auto_load!s:5s}  "
            f"version={item.template_version:6s}  "
            f"agent_id={item.agent_id!r:12s}  "
            f"project_id={item.project_id!r}"
        )

    if len(items) < 9:
        print(f"FAIL: expected at least 9 tester-skill-set items, got {len(items)}")
        return False

    # Check that all items have project_id=None (global templates)
    non_global = [it for it in items if it.project_id is not None]
    if non_global:
        print(
            f"FAIL: {len(non_global)} seeded items have non-NULL project_id — "
            f"templates should be global."
        )
        return False

    print("PASS: 9+ templates seeded with project_id=NULL (global)")
    return True


def check_seeding_idempotency(engine) -> bool:
    """Re-run seed_all() — should add 0 new rows (W4 version guard)."""
    _section("STEP 3: SkillSeedService seed idempotency")

    bank_repo = SkillBankRepository(engine)
    items_before = bank_repo.list_items(category="tester-skill-set")
    count_before = len(items_before)

    service = SkillSeedService(skill_bank_repo=bank_repo, agents_dir=AGENTS_DIR)
    summary = service.seed_all()
    print(f"Re-seed summary: {summary}")

    items_after = bank_repo.list_items(category="tester-skill-set")
    count_after = len(items_after)

    if count_after != count_before:
        print(f"FAIL: re-seed changed count {count_before} -> {count_after}")
        return False

    if summary["new"] != 0:
        print(f"FAIL: re-seed reported {summary['new']} new (expected 0)")
        return False

    print(f"PASS: idempotent — {count_after} entries stable, {summary['unchanged']} unchanged")
    return True


def check_clone_on_miss(engine) -> bool:
    """Phase 4 verification: clone a template into project scope."""
    _section("STEP 4: SkillCloneService.clone_on_miss_sync() against PG")

    bank_repo = SkillBankRepository(engine)
    skill_repo = SkillRepository(engine)
    clone_service = SkillCloneService(
        skill_repo=skill_repo,
        skill_bank_repo=bank_repo,
    )

    test_project_id = "pg-parity-test-project"
    cloned = clone_service.clone_on_miss_sync(
        name="test-strategy",
        agent_id=TESTER_AGENT,
        project_id=test_project_id,
    )

    if cloned is None:
        print("FAIL: clone_on_miss_sync returned None (template not found?)")
        return False

    print(f"Cloned skill: name={cloned.name}, project_id={cloned.project_id}")
    print(f"  id={cloned.id}")
    print(f"  lineage_origin={cloned.lineage_origin!r}")
    print(f"  source_skill_bank_id={cloned.source_skill_bank_id!r}")
    print(f"  auto_load={cloned.auto_load}")
    print(f"  status={cloned.status!r}")
    print(f"  generation={cloned.generation}")
    print(f"  is_active={cloned.is_active}")

    failures = []
    if cloned.lineage_origin != "bank_clone":
        failures.append(f"lineage_origin expected 'bank_clone', got {cloned.lineage_origin!r}")
    if not cloned.source_skill_bank_id:
        failures.append("source_skill_bank_id should be set (soft FK)")
    if cloned.project_id != test_project_id:
        failures.append(
            f"project_id expected {test_project_id!r}, got {cloned.project_id!r}"
        )
    if not cloned.is_active:
        failures.append("is_active expected True for newly-cloned skill")
    if cloned.status != "active":
        failures.append(f"status expected 'active', got {cloned.status!r}")
    if cloned.generation != 0:
        failures.append(f"generation expected 0, got {cloned.generation}")
    # auto_load should come from template (test-strategy has auto_load=true in tester skill-set.yaml)
    template = bank_repo.get_by_name_and_agent("test-strategy", TESTER_AGENT)
    if template and cloned.auto_load != template.auto_load:
        failures.append(
            f"auto_load mismatch: cloned={cloned.auto_load} template={template.auto_load}"
        )
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return False

    print("PASS: clone_on_miss_sync produced a valid skill row")
    return True


def check_clone_idempotency(engine) -> bool:
    """Second clone call returns the same row (no duplicate)."""
    _section("STEP 5: Clone idempotency")
    bank_repo = SkillBankRepository(engine)
    skill_repo = SkillRepository(engine)
    clone_service = SkillCloneService(
        skill_repo=skill_repo,
        skill_bank_repo=bank_repo,
    )
    test_project_id = "pg-parity-test-project"

    first = clone_service.clone_on_miss_sync(
        name="test-strategy",
        agent_id=TESTER_AGENT,
        project_id=test_project_id,
    )
    second = clone_service.clone_on_miss_sync(
        name="test-strategy",
        agent_id=TESTER_AGENT,
        project_id=test_project_id,
    )

    if first is None or second is None:
        print(f"FAIL: clone returned None (first={first}, second={second})")
        return False

    print(f"First  id={first.id}")
    print(f"Second id={second.id}")

    # Count rows for this (project_id, name, generation)
    rows, _total = skill_repo.list(project_id=test_project_id, active_only=False, limit=1000)
    matching = [
        r
        for r in rows
        if r.name == "test-strategy" and r.generation == 0
    ]
    print(f"Matching rows in skills table: {len(matching)}")

    if len(matching) != 1:
        print(f"FAIL: expected exactly 1 row, got {len(matching)}")
        return False

    if first.id != second.id:
        print(
            f"FAIL: clone did not return same id (first={first.id}, second={second.id})"
        )
        return False

    print("PASS: idempotent — second clone returned the same row")
    return True


def check_on_demand_clone(engine) -> bool:
    """Verify auto_load=False templates clone with auto_load=False (C2 fix)."""
    _section("STEP 6: On-demand clone preserves auto_load=False")
    bank_repo = SkillBankRepository(engine)
    skill_repo = SkillRepository(engine)
    clone_service = SkillCloneService(
        skill_repo=skill_repo,
        skill_bank_repo=bank_repo,
    )
    test_project_id = "pg-parity-test-project-ondemand"

    # mock-test has auto_load=false in tester skill-set.yaml
    template = bank_repo.get_by_name_and_agent("mock-test", TESTER_AGENT)
    if template is None:
        print("SKIP: no mock-test template found")
        return True
    print(f"Template mock-test: auto_load={template.auto_load}")

    cloned = clone_service.clone_on_miss_sync(
        name="mock-test",
        agent_id=TESTER_AGENT,
        project_id=test_project_id,
    )
    if cloned is None:
        print("FAIL: on-demand clone returned None")
        return False

    print(f"Cloned mock-test: auto_load={cloned.auto_load}")
    if cloned.auto_load != template.auto_load:
        print(
            f"FAIL: auto_load propagation broken — cloned={cloned.auto_load} "
            f"template={template.auto_load}"
        )
        return False

    print("PASS: auto_load propagated correctly from template to clone")
    return True


def main() -> int:
    overall_start = time.time()
    print("=== PostgreSQL Parity Check: Skill Evolution System ===")
    print(f"Target: {PG_URL}")
    print(f"Agents dir: {AGENTS_DIR}")

    if not check_pg_connection():
        return 2  # PG unavailable

    engine = create_engine(PG_URL, echo=False)

    recreate_schema(engine)

    results: dict[str, bool] = {
        "seeding": check_seeding(engine),
        "seeding_idempotency": check_seeding_idempotency(engine),
        "clone_on_miss": check_clone_on_miss(engine),
        "clone_idempotency": check_clone_idempotency(engine),
        "on_demand_clone": check_on_demand_clone(engine),
    }

    _section("SUMMARY")
    for name, ok in results.items():
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {name}")

    all_passed = all(results.values())
    elapsed = time.time() - overall_start
    print(f"\nElapsed: {elapsed:.2f}s")
    print(f"Overall: {'PASS' if all_passed else 'FAIL'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
"""A4 — the COUNTER-TRANSITION + RECONCILE pins (registered pack
``skill_counter_drift_unit_test``).

SCOPE. The gauge arm lives in ``tests/unit/test_skill_counter_drift_gauge.py``.
This module is the COUNTER arm the leader's extended fence added:

  * S1b — the transition-based ``total_applied`` bump
    (``SkillMetricsService.record_feedback``): first filing +1, repeat
    filing +0, on-miss insert still +1 exactly once;
  * S1c — the audited, reversible, DRY-RUN-FIRST operator reconcile
    (``scripts/audit/skill_counter_reconcile.py``): dry run writes
    nothing, ``--apply`` converges the counter to the ledger and emits a
    receipt, ``--revert`` restores the pre-values, and the convergence
    evaluator refuses to claim convergence for two readings closer than
    24 h.

WHY EACH PIN IS NON-VACUOUS (the mutation legs, all in the pack script):

  * M-A4a — remove the ``not prev_applied`` guard ⇒ a repeat filing bumps
    again ⇒ ``test_a_repeat_filing_does_not_bump`` REDs.
  * M-A4b — revert to the pre-fix call-counting bump (drop the guard AND
    the transition read) ⇒ the same pins RED.
  * M-A4e — make the reconcile script dry-run-only (refuse to write even
    under ``--apply``) ⇒ ``test_apply_converges_and_writes_a_receipt`` REDs.
  * M-A4f — make the written value off-by-one (``after + 1``) ⇒ the
    convergence pin REDs.
  * M-A4g — skip the >= 24 h leg (always report "converged") ⇒
    ``test_convergence_refuses_two_close_readings`` REDs.

The reconcile script is exercised against a SCRATCH SQLite database built
in ``tmp_path`` — never a live/dev database, and no PostgreSQL.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
from daemon.repositories.skill.repository import (
    SkillABTestRepository,
    SkillRepository,
    SkillTriggerRepository,
    SkillUsageRepository,
)
from daemon.services.skill_metrics_service import SkillMetricsService

REPO_ROOT = Path(__file__).resolve().parents[2]
RECONCILE_PATH = REPO_ROOT / "scripts" / "audit" / "skill_counter_reconcile.py"


class _Config:
    ab_sample_size = 10
    ab_min_difference = 0.15
    max_extensions = 3


def _service():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    skill_repo = SkillRepository(engine)
    usage_repo = SkillUsageRepository(engine)
    service = SkillMetricsService(
        usage_repo=usage_repo,
        skill_repo=skill_repo,
        trigger_repo=SkillTriggerRepository(engine),
        ab_test_repo=SkillABTestRepository(engine),
        config=_Config(),
    )
    return engine, service, skill_repo, usage_repo


def _skill(skill_repo: SkillRepository, name: str = "counter-skill"):
    return skill_repo.create(
        name=name, description="d", content="c", project_id="p-counter"
    )


async def _feedback(service, skill_id: str, instance_id: str, applied=True):
    return await service.record_feedback(
        skill_id=skill_id,
        instance_id=instance_id,
        agent_id="a",
        project_id="p-counter",
        applied=applied,
        note="",
    )


class TestTheBumpIsTransitionBased:
    """S1b — exactly one bump per usage ROW, never per CALL."""

    @pytest.mark.asyncio
    async def test_the_first_filing_bumps_once(self) -> None:
        _, service, skill_repo, usage_repo = _service()
        skill = _skill(skill_repo)
        usage_repo.create(
            skill_id=skill.id,
            project_id="p-counter",
            instance_id="i-first",
            agent_id="a",
        )
        assert await _feedback(service, skill.id, "i-first") is True
        assert skill_repo.get(skill.id).total_applied == 1

    @pytest.mark.asyncio
    async def test_a_repeat_filing_does_not_bump(self) -> None:
        """THE LEAK PIN — M-A4a/M-A4b red here."""
        _, service, skill_repo, usage_repo = _service()
        skill = _skill(skill_repo)
        usage_repo.create(
            skill_id=skill.id,
            project_id="p-counter",
            instance_id="i-repeat",
            agent_id="a",
        )
        await _feedback(service, skill.id, "i-repeat")
        assert skill_repo.get(skill.id).total_applied == 1

        # The same feedback filed again and again — the ledger still holds
        # ONE applied row, so the counter must stay at 1.
        for _ in range(3):
            assert await _feedback(service, skill.id, "i-repeat") is True
        assert skill_repo.get(skill.id).total_applied == 1, (
            "a repeat filing of the same feedback must bump +0 — counting "
            "calls instead of rows is exactly the +480 drift"
        )

    @pytest.mark.asyncio
    async def test_the_on_miss_insert_path_still_bumps_once(self) -> None:
        """Council F-4's trap: the FIRST legitimate bump must not be suppressed."""
        _, service, skill_repo, _usage_repo = _service()
        skill = _skill(skill_repo)
        assert await _feedback(service, skill.id, "i-onmiss") is True
        assert skill_repo.get(skill.id).total_applied == 1

        # …and a second filing against that now-existing row bumps +0.
        assert await _feedback(service, skill.id, "i-onmiss") is True
        assert skill_repo.get(skill.id).total_applied == 1

    @pytest.mark.asyncio
    async def test_the_counter_equals_the_ledger_after_mixed_filing(self) -> None:
        """The ACCEPTANCE shape: counter == applied=True rows, per skill."""
        _, service, skill_repo, usage_repo = _service()
        skill = _skill(skill_repo)
        for index in range(3):
            usage_repo.create(
                skill_id=skill.id,
                project_id="p-counter",
                instance_id=f"i-{index}",
                agent_id="a",
            )
        for index in range(3):
            await _feedback(service, skill.id, f"i-{index}")
            await _feedback(service, skill.id, f"i-{index}")  # duplicate
        records, _total = usage_repo.get_by_skill(skill.id)
        ledger = sum(1 for record in records if record.applied)
        assert skill_repo.get(skill.id).total_applied == ledger == 3

    @pytest.mark.asyncio
    async def test_applied_false_never_bumps(self) -> None:
        _, service, skill_repo, usage_repo = _service()
        skill = _skill(skill_repo)
        usage_repo.create(
            skill_id=skill.id,
            project_id="p-counter",
            instance_id="i-neg",
            agent_id="a",
        )
        await _feedback(service, skill.id, "i-neg", applied=False)
        await _feedback(service, skill.id, "i-neg", applied=False)
        assert skill_repo.get(skill.id).total_applied == 0


# ─────────────────────────────────────────────────────────────────────────────
# the reconcile script (S1c)
# ─────────────────────────────────────────────────────────────────────────────


def _load_reconcile():
    """Import the operator script as a module (no install, no side effects)."""
    spec = importlib.util.spec_from_file_location(
        "skill_counter_reconcile_under_test", RECONCILE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _scratch_db(tmp_path: Path):
    """A FILE-BACKED scratch SQLite DB with a seeded, drifting counter."""
    db_path = tmp_path / "scratch.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    skill_repo = SkillRepository(engine)
    usage_repo = SkillUsageRepository(engine)

    drifted = skill_repo.create(
        name="drifted", description="d", content="c", project_id="p"
    )
    in_sync = skill_repo.create(
        name="in-sync", description="d", content="c", project_id="p"
    )
    for index in range(2):
        record = usage_repo.create(
            skill_id=drifted.id,
            project_id="p",
            instance_id=f"d-{index}",
            agent_id="a",
        )
        usage_repo.update_feedback(record.id, True, "ok")
    # The historical drift: counter says 5, the ledger holds 2.
    skill_repo.update(drifted.id, total_applied=5)

    record = usage_repo.create(
        skill_id=in_sync.id, project_id="p", instance_id="s-0", agent_id="a"
    )
    usage_repo.update_feedback(record.id, True, "ok")
    skill_repo.update(in_sync.id, total_applied=1)

    engine.dispose()
    return f"sqlite:///{db_path}", drifted.id, in_sync.id


class TestTheReconcileScriptIsDryRunFirst:
    """S1c — the write path is unreachable without ``--apply``."""

    def test_dry_run_reports_drift_and_writes_nothing(self, tmp_path) -> None:
        module = _load_reconcile()
        url, drifted_id, _ = _scratch_db(tmp_path)

        engine = create_engine(url)
        before = module.counters_by_skill(engine)
        receipt = module.apply_repair(
            engine, module.drift_rows(engine), apply=False
        )
        after = module.counters_by_skill(engine)
        engine.dispose()

        assert receipt["dry_run"] is True
        assert receipt["touched_count"] == 1
        assert receipt["touched"][0]["skill_id"] == drifted_id
        assert receipt["touched"][0]["before"] == 5
        assert receipt["touched"][0]["after"] == 2
        assert before == after, "a dry run must not write"

    def test_the_cli_default_is_a_dry_run(self, tmp_path) -> None:
        """The DEFAULT (no ``--apply``) leaves the DB byte-identical."""
        module = _load_reconcile()
        url, drifted_id, _ = _scratch_db(tmp_path)

        engine = create_engine(url)
        before = module.counters_by_skill(engine)
        engine.dispose()

        assert module.main(["--db-url", url, "--json"]) == 0

        engine = create_engine(url)
        after = module.counters_by_skill(engine)
        engine.dispose()
        assert after == before
        assert after[drifted_id] == 5, "the drift is still there after a dry run"

    def test_a_missing_db_url_is_a_usage_error(self) -> None:
        module = _load_reconcile()
        with pytest.raises(SystemExit) as excinfo:
            module.main([])
        assert excinfo.value.code == 2


class TestTheApplyPathConvergesAndWritesAReceipt:
    """S1c — M-A4e / M-A4f red here."""

    def test_apply_converges_and_writes_a_receipt(self, tmp_path) -> None:
        module = _load_reconcile()
        url, drifted_id, in_sync_id = _scratch_db(tmp_path)
        receipt_path = tmp_path / "receipt.json"

        assert (
            module.main(
                ["--db-url", url, "--apply", "--receipt", str(receipt_path)]
            )
            == 0
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

        # The receipt records every touched row, before AND after.
        assert receipt["dry_run"] is False
        assert receipt["touched_count"] == 1
        entry = receipt["touched"][0]
        assert entry["skill_id"] == drifted_id
        assert (entry["before"], entry["after"]) == (5, 2)
        assert receipt["post_repair_converged"] is True

        # …and the DB really converged: counter == ledger for BOTH skills.
        engine = create_engine(url)
        counters = module.counters_by_skill(engine)
        ledger = module.ledger_by_skill(engine)
        engine.dispose()
        assert counters[drifted_id] == ledger[drifted_id] == 2
        assert counters[in_sync_id] == ledger[in_sync_id] == 1

    def test_the_written_value_equals_the_ledger_exactly(self, tmp_path) -> None:
        """OFF-BY-ONE GUARD (M-A4f)."""
        module = _load_reconcile()
        url, drifted_id, _ = _scratch_db(tmp_path)
        module.main(["--db-url", url, "--apply"])
        engine = create_engine(url)
        counters = module.counters_by_skill(engine)
        ledger = module.ledger_by_skill(engine)
        engine.dispose()
        assert counters[drifted_id] == ledger[drifted_id], (
            "the reconcile must write the LEDGER value, not ledger±1"
        )

    def test_the_receipt_carries_the_pre_values_for_reversal(self, tmp_path) -> None:
        module = _load_reconcile()
        url, drifted_id, _ = _scratch_db(tmp_path)
        receipt_path = tmp_path / "receipt.json"
        module.main(["--db-url", url, "--apply", "--receipt", str(receipt_path)])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

        # Reverse it from the receipt alone.
        assert module.main(["--db-url", url, "--revert", str(receipt_path)]) == 0

        engine = create_engine(url)
        counters = module.counters_by_skill(engine)
        engine.dispose()
        assert counters[drifted_id] == 5, "the revert must restore the pre-value"


class TestTheCanonicalInstrument:
    """S1c acceptance 8 — the SAME instrument that reported +480."""

    def test_the_measure_reading_carries_the_canonical_fields(self, tmp_path) -> None:
        module = _load_reconcile()
        url, _drifted, _ = _scratch_db(tmp_path)
        engine = create_engine(url)
        reading = module.measure_snapshot(engine)
        engine.dispose()
        for key in (
            "taken_at",  # the clock_timestamp()/strftime header
            "counter_total",
            "ledger_total",
            "drift",
            "skills_total",
            "mismatched_skills",
            "mismatch_rows",
        ):
            assert key in reading, f"the canonical reading lost {key!r}"
        assert reading["counter_total"] - reading["ledger_total"] == reading["drift"]
        assert reading["drift"] == 3, "5 + 1 counters vs 2 + 1 ledger rows"

    def test_the_clock_is_the_dialect_expression(self, tmp_path) -> None:
        module = _load_reconcile()
        url, _d, _s = _scratch_db(tmp_path)
        engine = create_engine(url)
        expression = module._now_expression(engine)
        engine.dispose()
        assert "strftime" in expression, "SQLite uses strftime for the clock"


class TestTheConvergenceEvaluatorRefusesShortElapsed:
    """S1c acceptance 7 + M-A4g — the >= 24 h leg cannot be faked."""

    def _reading(self, taken_at: str, drift: int) -> dict:
        return {"taken_at": taken_at, "drift": drift}

    def test_convergence_refuses_two_close_readings(self) -> None:
        module = _load_reconcile()
        verdict = module.evaluate_convergence(
            self._reading("2026-11-09T10:00:00+00:00", 0),
            self._reading("2026-11-09T10:00:05+00:00", 0),
        )
        assert verdict["converged"] is False
        assert verdict["status"] == "insufficient_elapsed"

    def test_convergence_holds_across_24h_both_zero(self) -> None:
        module = _load_reconcile()
        verdict = module.evaluate_convergence(
            self._reading("2026-11-09T10:00:00+00:00", 0),
            self._reading("2026-11-10T10:00:01+00:00", 0),
        )
        assert verdict["converged"] is True
        assert verdict["status"] == "converged"

    def test_persistent_drift_across_24h_is_not_convergence(self) -> None:
        module = _load_reconcile()
        verdict = module.evaluate_convergence(
            self._reading("2026-11-09T10:00:00Z", 0),
            self._reading("2026-11-10T11:00:00Z", 4),
        )
        assert verdict["converged"] is False
        assert verdict["status"] == "drift_persists"

    def test_the_compare_cli_reports_insufficient_elapsed(self, tmp_path) -> None:
        module = _load_reconcile()
        first = tmp_path / "a.json"
        second = tmp_path / "b.json"
        first.write_text(
            json.dumps(self._reading("2026-11-09T10:00:00+00:00", 0)),
            encoding="utf-8",
        )
        second.write_text(
            json.dumps(self._reading("2026-11-09T11:00:00+00:00", 0)),
            encoding="utf-8",
        )
        assert (
            module.main(["--db-url", "sqlite://", "--compare", str(first), str(second), "--json"])
            == 3
        )


class TestTheReconcileWritesOnlyTheOneColumn:
    """A refusal-direction pin: the script can never touch anything else."""

    def test_the_writable_column_constant_is_total_applied(self) -> None:
        module = _load_reconcile()
        assert module.WRITABLE_COLUMN == "total_applied"

    def test_the_script_issues_no_delete_and_no_other_update(self) -> None:
        source = RECONCILE_PATH.read_text(encoding="utf-8").lower()
        assert "delete from" not in source
        assert "drop table" not in source
        assert "update skills set total_applied" in source
        # The ONLY other write verb allowed is the same column in the revert.
        assert source.count("update skills set") == 2

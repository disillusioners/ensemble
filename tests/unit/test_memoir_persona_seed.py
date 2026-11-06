"""The persona seed — byte identity, idempotence, and failure tolerance.

Phase 1 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase1-plan.md`` AC4; pack
``test/packs/memoir_seed_unit_test.sh``).

What this suite pins:

* the seed constant is BYTE-EQUAL to the fenced block in ``design.md``
  §11 (the plan's "shipped VERBATIM" — a paraphrase is a defect);
* the seeded row is ``source='seed'``, ``status='approved'``,
  ``version=1``;
* a re-boot is a NO-OP (one row, never a duplicate);
* the seed is KNOB-INDEPENDENT (flag #13): it runs with the knob OFF —
  the kill-switch gates the SURFACES, not the substrate;
* a seed FAILURE never aborts the boot: it logs
  ``WARNING [Memoir] persona seed failed: <exc>`` and returns.

The two MUTATION legs live in the pack: the seed text altered by one
character, and a seed failure that propagates instead of degrading.
"""

from __future__ import annotations

import logging
import pathlib
import re

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.memoir.models import MemoirPersona, MemoirPersonaVersion
from daemon.repositories.memoir.seed import (
    MEMOIR_PERSONA_SEED,
    seed_persona_if_absent,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DESIGN_DOC = (
    REPO_ROOT / ".agents" / "shared" / "planning" / "ari-memoir-store" / "design.md"
)
SEED_LOGGER = "daemon.repositories.memoir.seed"


@pytest.fixture()
def seeded_engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _design_seed_block() -> str:
    """The fenced markdown block of ``design.md`` §11, verbatim."""
    text = DESIGN_DOC.read_text(encoding="utf-8")
    match = re.search(
        r"## 11\. Persona seed text.*?\n```markdown\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match, "design.md §11 no longer carries the fenced seed block"
    return match.group(1)


# ── byte identity ─────────────────────────────────────────────────────────


def test_the_seed_constant_is_byte_equal_to_the_design_block():
    """MUTATION-SENSITIVE: one altered character makes this RED."""
    block = _design_seed_block()
    assert MEMOIR_PERSONA_SEED.encode("utf-8") == block.encode("utf-8")


def test_the_seed_constant_carries_the_design_heading():
    assert MEMOIR_PERSONA_SEED.startswith("# Who We Are\n")
    assert "Empyrea" in MEMOIR_PERSONA_SEED
    assert "wi-30d19abe" in MEMOIR_PERSONA_SEED


# ── the seeded row ────────────────────────────────────────────────────────


def test_the_seed_installs_one_approved_seed_version(seeded_engine):
    assert seed_persona_if_absent(seeded_engine) is not None
    with Session(seeded_engine) as session:
        versions = list(session.exec(select(MemoirPersonaVersion)).all())
        pointers = list(session.exec(select(MemoirPersona)).all())

    assert len(versions) == 1
    version = versions[0]
    assert version.agent_id == "ari"
    assert version.version == 1
    assert version.status == "approved"
    assert version.source == "seed"
    assert version.body == MEMOIR_PERSONA_SEED
    assert version.submitted_at is not None
    assert version.approved_at is not None

    assert len(pointers) == 1
    assert pointers[0].agent_id == "ari"
    assert pointers[0].current_version_id == version.id


def test_the_seed_is_idempotent_across_reboots(seeded_engine):
    """A re-boot never duplicates the seed (get-or-create)."""
    first = seed_persona_if_absent(seeded_engine)
    second = seed_persona_if_absent(seeded_engine)
    third = seed_persona_if_absent(seeded_engine)

    assert first is not None
    assert second is not None
    assert third is not None
    assert first.id == second.id == third.id

    with Session(seeded_engine) as session:
        versions = list(session.exec(select(MemoirPersonaVersion)).all())
        pointers = list(session.exec(select(MemoirPersona)).all())
    assert len(versions) == 1
    assert len(pointers) == 1


def test_the_seed_never_overwrites_an_existing_persona(seeded_engine):
    """A boot never clobbers a newer persona."""
    seed_persona_if_absent(seeded_engine)
    with Session(seeded_engine) as session:
        version = session.exec(select(MemoirPersonaVersion)).first()
        version.body = "# A newer self"
        session.commit()

    seed_persona_if_absent(seeded_engine)
    with Session(seeded_engine) as session:
        versions = list(session.exec(select(MemoirPersonaVersion)).all())
    assert len(versions) == 1
    assert versions[0].body == "# A newer self"


# ── knob independence ─────────────────────────────────────────────────────


def test_the_seed_runs_with_the_knob_off(seeded_engine, monkeypatch):
    """Flag #13 — the knob gates the SURFACES, not the substrate."""
    monkeypatch.setenv("ENSEMBLE_MEMOIR_ENABLED", "false")
    version = seed_persona_if_absent(seeded_engine)
    assert version is not None
    assert version.source == "seed"
    with Session(seeded_engine) as session:
        assert len(list(session.exec(select(MemoirPersonaVersion)).all())) == 1


# ── failure tolerance ─────────────────────────────────────────────────────


def test_a_seed_failure_logs_a_warning_and_does_not_raise(caplog):
    """MUTATION-SENSITIVE: a seed failure must never abort the boot.

    The engine has NO tables, so the INSERT fails. The seed must degrade
    to a WARNING and return None — the boot continues (D1.13 / AM-39).
    """
    bare_engine = create_engine("sqlite:///:memory:")
    try:
        with caplog.at_level(logging.WARNING, logger=SEED_LOGGER):
            result = seed_persona_if_absent(bare_engine)
        assert result is None
        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        ]
        assert len(warnings) == 1
        assert "[Memoir] persona seed failed:" in warnings[0]
    finally:
        bare_engine.dispose()


def test_a_seed_failure_is_not_an_availability_failure():
    """The caller's boot path is unaffected by a seed failure."""
    bare_engine = create_engine("sqlite:///:memory:")
    try:
        # No exception escapes the seed call — the boot continues.
        assert seed_persona_if_absent(bare_engine) is None
        # The engine is still usable for the rest of the boot (the
        # failure was CONTENT-scoped — it never poisoned the session).
        with Session(bare_engine) as session:
            from sqlalchemy import text

            assert session.execute(text("SELECT 1")).scalar() == 1
    finally:
        bare_engine.dispose()

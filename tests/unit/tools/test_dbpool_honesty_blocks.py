"""F2-i pins — the honesty blocks state the measured truth (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F2.7 / F4.9 / §8.6 and §5.2;
phase2-plan T2.11 / AC22 / D-21 / D-47 / DR-8 / DR-36.

What these pins assert — BOTH honesty blocks
(``daemon/constants.py`` and ``daemon/persistence.py``) state:

* the RETENTION / TRANSACTION-HYGIENE class is REPAIRED by this lane
  (branch + date) — no block still names the lane as the owner of an
  UNFIXED defect (the former "SEPARATE defect owned by a follow-up
  commission" wording is gone);
* the DWELL / OCCUPANCY class is NOT REPAIRED (A9-v);
* the engine-level ``MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`` backstop
  ships DEFINED BUT NOT APPLIED (DISABLED) with its enablement
  mechanism + the boot log line (DR-8);
* the knob VALUES in ``daemon/constants.py`` did not move
  (``CHECKPOINT_POOL_MAX_SIZE_DEFAULT`` / ``CHECKPOINT_POOL_TIMEOUT_S_DEFAULT``
  stay 10 / 5.0).

Plantable mutations:

* restore the false honesty-block wording ("the P1 leak is REPAIRED"
  / "SEPARATE defect owned by a follow-up commission") ⇒ RED;
* delete the ``MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED`` status
  wording ⇒ RED;
* move a checkpoint-pool knob value ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_honesty_blocks.py --tb=short -q
"""
from __future__ import annotations

import re
from pathlib import Path

from daemon.constants import (
    CHECKPOINT_POOL_MAX_SIZE_DEFAULT,
    CHECKPOINT_POOL_TIMEOUT_S_DEFAULT,
    MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 7

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONSTANTS_SRC = (_REPO_ROOT / "daemon" / "constants.py").read_text()
_PERSISTENCE_SRC = (_REPO_ROOT / "daemon" / "persistence.py").read_text()

#: The lane identifier both blocks must cite (branch + year).
_LANE_TOKEN = re.compile(r"fix/db-pool-idle-txn")

#: The RETIRED false wordings — a restored instance is a RED (the
#: block would again name an unfixed defect this lane repaired).
_RETIRED_WORDINGS = (
    "SEPARATE defect",
    "owned by a follow-up commission",
)


def _honesty_block(source: str, anchor: str) -> str:
    """The comment block around ``anchor`` (±40 lines)."""
    lines = source.splitlines()
    for idx, line in enumerate(lines):
        if anchor in line:
            start = max(0, idx - 40)
            return "\n".join(lines[start : idx + 41])
    raise AssertionError(f"anchor {anchor!r} not found in source")


class TestConstantsHonestyBlock:
    """F2-i — daemon/constants.py."""

    def test_retention_class_stated_repaired_with_lane_and_date(self):
        block = _honesty_block(
            _CONSTANTS_SRC, "Manager-engine pool hardening"
        )
        assert "REPAIRED" in block
        assert "RETENTION / TRANSACTION-HYGIENE" in block
        assert _LANE_TOKEN.search(block), (
            "F2-i: the honesty block must cite the repairing lane "
            "(branch) and its date"
        )
        assert "2026-10-28" in block

    def test_dwell_occupancy_class_stated_not_repaired(self):
        block = _honesty_block(
            _CONSTANTS_SRC, "HONESTY (db-pool-idle-txn"
        )
        assert "DWELL / OCCUPANCY class is NOT REPAIRED" in block
        # The recurrence precondition is named honestly.
        assert "SYNC DB WORK ON THE EVENT LOOP" in block

    def test_backstop_disabled_status_with_mechanism_and_log_line(self):
        block = _honesty_block(
            _CONSTANTS_SRC, "MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS: int"
        )
        assert "DEFINED BUT NOT APPLIED" in block
        assert "DISABLED" in block
        # The boot log line (verbatim intent).
        assert (
            "MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED" in block
        )
        # The enablement mechanism.
        assert "iff" in block
        assert "trustworthy" in block.lower()

    def test_retired_false_wording_is_gone(self):
        for source, name in (
            (_CONSTANTS_SRC, "daemon/constants.py"),
            (_PERSISTENCE_SRC, "daemon/persistence.py"),
        ):
            for wording in _RETIRED_WORDINGS:
                assert wording not in source, (
                    f"F2-i: {name} still carries the retired false "
                    f"honesty wording {wording!r} — the lane repaired "
                    "that class; the block must not name it as an "
                    "unfixed defect"
                )

    def test_checkpoint_knob_values_did_not_move(self):
        """AC22 — the honesty refresh changes WORDING, never a knob
        value."""
        assert CHECKPOINT_POOL_MAX_SIZE_DEFAULT == 10
        assert CHECKPOINT_POOL_TIMEOUT_S_DEFAULT == 5.0
        assert MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS == 60_000


class TestPersistenceHonestyBlock:
    """F2-i — daemon/persistence.py."""

    def test_persistence_block_states_both_classes(self):
        block = _honesty_block(_PERSISTENCE_SRC, "POOL SIZING")
        assert "RETENTION / TRANSACTION-HYGIENE class" in block
        assert "REPAIRED" in block
        assert "DWELL / OCCUPANCY class is NOT REPAIRED" in block

    def test_persistence_block_states_backstop_disabled(self):
        block = _honesty_block(_PERSISTENCE_SRC, "POOL SIZING")
        assert "MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS" in block
        assert "DEFINED" in block and "NOT APPLIED" in block
        assert "DISABLED" in block

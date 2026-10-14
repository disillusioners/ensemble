"""M16 — loader empty-content preservation tests.

Pins the M16 fix-cycle finding: the loader's
``resolve_prompts_from_db`` used a truthy ``or`` chain that treated
an empty string ``""`` as missing and silently fell through to the
template file. The fix replaces ``or`` with explicit ``is not None``
chains so an empty-content DB row is served as ``""``.

Coverage:

* CORE class — ``selected_content=""`` → loader returns ``""`` (NOT
  the template file content).
* CORE class — ``latest_approved_content=""`` with no selection →
  loader returns ``""`` (NOT the template file content).
* MEMORY class — ``selected_content=""`` → loader returns ``""``
  (NOT the template file content).
* MEMORY class — ``latest_content=""`` with no selection → loader
  returns ``""`` (NOT the template file content).
* None rows (the existing happy path) → fall through to template
  unchanged.

Fence discipline: source ``.smoke/test-fence.sh`` before any
invocation; ``FAKE_HOME`` mktemp + ``ENSEMBLE_SELF_ENV=dev``.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.loader import resolve_prompts_from_db


# ────────────────────────────────────────────────────────────────────
# Fence
# ────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session():
    allowed_dbs = {"ensemble_dev_test_only", "ensemble_test_only"}
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"
    if os.environ.get("POSTGRES_DB") in (None, "", "ensemble_prod"):
        os.environ["POSTGRES_DB"] = "ensemble_dev_test_only"
    elif os.environ.get("POSTGRES_DB") not in allowed_dbs:
        pytest.exit(
            f"HARD ABORT: this test session targets "
            f"POSTGRES_DB={os.environ.get('POSTGRES_DB')!r}; "
            f"refuse to point M16 tests at a non-dev DB.",
            returncode=3,
        )
    yield


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _row(
    *,
    artifact: str,
    artifact_class: str = "CORE",
    selected_content: Any = None,
    latest_approved_content: Any = None,
    latest_content: Any = None,
) -> Any:
    """Build a duck-typed ``EffectiveRow`` (the loader reads attrs,
    not the dataclass type)."""
    row = MagicMock()
    row.artifact = artifact
    row.artifact_class = artifact_class
    row.selected_content = selected_content
    row.latest_approved_content = latest_approved_content
    row.latest_content = latest_content
    return row


# ────────────────────────────────────────────────────────────────────
# CORE class — empty selected_content / latest_approved_content
# ────────────────────────────────────────────────────────────────────


class TestCoreClassEmptyContent:
    """CORE rows: ``selected_content=""`` and
    ``latest_approved_content=""`` are SERVED as ``""`` (NOT
    silently overwritten by the template file)."""

    def test_selected_empty_string_preserved(self):
        """A CORE row with ``selected_content=""`` → output ``out``
        (NOT the template file content). The OLD truthy-``or``
        chain would have fallen through and returned the template."""
        rows = [_row(artifact="soul", selected_content="")]
        prompts = {"soul": "# Template Soul"}
        result = resolve_prompts_from_db(
            prompts,
            agent_id="dev",
            version_tag="",
            repo=MagicMock(),
            rows=rows,
        )
        assert result["soul"] == "", (
            f"M16 regression: empty CORE selected_content was "
            f"overwritten by template; got {result['soul']!r}"
        )

    def test_latest_approved_empty_string_preserved(self):
        """A CORE row with no selection + ``latest_approved_content=""``
        → ``""`` (NOT the template)."""
        rows = [_row(artifact="soul", latest_approved_content="")]
        prompts = {"soul": "# Template Soul"}
        result = resolve_prompts_from_db(
            prompts,
            agent_id="dev",
            version_tag="",
            repo=MagicMock(),
            rows=rows,
        )
        assert result["soul"] == "", (
            f"M16 regression: empty CORE latest_approved_content "
            f"was overwritten by template; got {result['soul']!r}"
        )


# ────────────────────────────────────────────────────────────────────
# MEMORY class — empty selected_content / latest_content
# ────────────────────────────────────────────────────────────────────


class TestMemoryClassEmptyContent:
    """MEMORY rows: ``selected_content=""`` and ``latest_content=""``
    are SERVED as ``""`` (NOT silently overwritten by the template
    file)."""

    def test_selected_empty_string_preserved(self):
        rows = [_row(
            artifact="memory",
            artifact_class="MEMORY",
            selected_content="",
        )]
        prompts = {"memory": "# Template Memory"}
        result = resolve_prompts_from_db(
            prompts,
            agent_id="dev",
            version_tag="",
            repo=MagicMock(),
            rows=rows,
        )
        assert result["memory"] == "", (
            f"M16 regression: empty MEMORY selected_content was "
            f"overwritten by template; got {result['memory']!r}"
        )

    def test_latest_empty_string_preserved(self):
        rows = [_row(
            artifact="memory",
            artifact_class="MEMORY",
            latest_content="",
        )]
        prompts = {"memory": "# Template Memory"}
        result = resolve_prompts_from_db(
            prompts,
            agent_id="dev",
            version_tag="",
            repo=MagicMock(),
            rows=rows,
        )
        assert result["memory"] == "", (
            f"M16 regression: empty MEMORY latest_content was "
            f"overwritten by template; got {result['memory']!r}"
        )


# ────────────────────────────────────────────────────────────────────
# Negative — non-empty content STILL wins (the happy path)
# ────────────────────────────────────────────────────────────────────


class TestNonEmptyContentWins:
    """Sanity: non-empty DB content STILL wins over the template
    file (the M16 fix must not regress the happy path)."""

    def test_core_selected_non_empty_wins(self):
        rows = [_row(artifact="soul", selected_content="# DB Soul")]
        prompts = {"soul": "# Template Soul"}
        result = resolve_prompts_from_db(
            prompts,
            agent_id="dev",
            version_tag="",
            repo=MagicMock(),
            rows=rows,
        )
        assert result["soul"] == "# DB Soul"

    def test_memory_latest_non_empty_wins(self):
        rows = [_row(
            artifact="memory",
            artifact_class="MEMORY",
            latest_content="# DB Memory",
        )]
        prompts = {"memory": "# Template Memory"}
        result = resolve_prompts_from_db(
            prompts,
            agent_id="dev",
            version_tag="",
            repo=MagicMock(),
            rows=rows,
        )
        assert result["memory"] == "# DB Memory"


# ────────────────────────────────────────────────────────────────────
# None rows — fall through to template (the existing contract)
# ────────────────────────────────────────────────────────────────────


class TestNoneRowsFallThrough:
    """A CORE / MEMORY row whose DB content is ``None`` MUST fall
    through to the template file unchanged (the M16 fix preserves
    the existing None contract)."""

    def test_core_none_falls_through(self):
        rows = [_row(artifact="soul")]
        prompts = {"soul": "# Template Soul"}
        result = resolve_prompts_from_db(
            prompts,
            agent_id="dev",
            version_tag="",
            repo=MagicMock(),
            rows=rows,
        )
        assert result["soul"] == "# Template Soul"

    def test_memory_none_falls_through(self):
        rows = [_row(artifact="memory", artifact_class="MEMORY")]
        prompts = {"memory": "# Template Memory"}
        result = resolve_prompts_from_db(
            prompts,
            agent_id="dev",
            version_tag="",
            repo=MagicMock(),
            rows=rows,
        )
        assert result["memory"] == "# Template Memory"
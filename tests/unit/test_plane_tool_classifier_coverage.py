"""P2-K / FC11 — the read/write classifier coverage pin (lane ``plane-pm-issue-tools`` Phase 2).

Asserted against the **committed gate-(i) catalog fixture**
(``tests/fixtures/plane_catalog.json``, 177 verbs — loaded via
``tests/helpers/plane_catalog_fixture.py``), never against a live server:

* every catalog verb ∈ read ∪ write ∪ documented-unclassified, with the
  documented-unclassified set asserted **EMPTY** (FC11);
* the pattern set matches the ruled disposition (``add_``/``edit_``/
  ``assign_`` dropped; ``set_``/``remove_`` kept; ``retrieve_``/``count_``/
  ``read_`` added to read; the 9 write patterns added);
* the **R18 set arithmetic**: the non-full-access kept set is 73 verbs, the
  dropped set is 104, the kept set is a STRICT SUPERSET of the pre-fix
  classifier's 48, and ``today − corrected`` is EMPTY — no verb that was
  kept is dropped anywhere.

Classification is performed by the REAL ``daemon.mcp.resilience.is_read_tool``
(the same function ``McpService._get_read_only_tools`` consults), on the
DAEMON-shaped tool names (``plane_<raw>``) — the first-segment strip the
production path applies.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

from helpers.plane_catalog_fixture import (
    catalog_names,
    load_catalog,
    sorted_names_sha256,
)

from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
from daemon.mcp.resilience import is_read_tool

# The PRE-fix on-disk classifier (plane.py @ base 941cb6321) — kept as a
# literal so the R18 arithmetic is computed against a frozen "today", not
# against whatever the module happens to say if someone reverts a pattern.
TODAY_READ_PATTERNS: tuple[str, ...] = ("list_", "get_", "search_")
TODAY_WRITE_PATTERNS: tuple[str, ...] = (
    "create_", "update_", "delete_", "add_", "remove_", "set_", "edit_", "assign_",
)


class _Patterns:
    """A minimal ResilienceConfig-shaped holder for is_read_tool."""

    def __init__(self, read: tuple[str, ...], write: tuple[str, ...]) -> None:
        self.read_tool_patterns = read
        self.write_tool_patterns = write


@pytest.fixture(scope="module")
def raw_names() -> list[str]:
    catalog = load_catalog()
    names = catalog_names(catalog)
    assert len(names) == 177
    return names


@pytest.fixture(scope="module")
def classifier():
    return PlaneServerDefinition().resilience_config


def _classify(patterns, raw_names):
    """(kept, dropped) for a non-full-access agent, via the REAL is_read_tool."""
    kept, dropped = set(), set()
    for raw in raw_names:
        daemon_name = f"plane_{raw}"
        if is_read_tool(daemon_name, patterns):
            kept.add(raw)
        else:
            dropped.add(raw)
    return kept, dropped


# ── FC11: coverage — zero unclassified catalog verbs ────────────────────────


class TestClassifierCoverage:
    def test_fc11_every_verb_is_read_or_write(self, raw_names, classifier):
        kept, dropped = _classify(classifier, raw_names)
        unclassified = set(raw_names) - (kept | dropped)
        # kept ∪ dropped is everything is_read_tool partitions; the real
        # FC11 check is that "neither read nor classified-write" is empty,
        # i.e. the dropped set contains ONLY verbs that match a WRITE
        # pattern (not verbs that fell through both).
        for raw in dropped:
            stripped = f"plane_{raw}".split("_", 1)[1]
            assert any(
                stripped.startswith(p) for p in classifier.write_tool_patterns
            ), (
                f"verb {raw!r} is neither read nor an explicitly-classified "
                f"write — the documented-unclassified set is NOT empty (FC11)"
            )
        assert not unclassified
        assert len(kept) + len(dropped) == len(raw_names) == 177

    def test_corrected_census_73_read_104_write_0_unclassified(
        self, raw_names, classifier
    ):
        kept, dropped = _classify(classifier, raw_names)
        assert len(kept) == 73
        assert len(dropped) == 104
        assert len(kept) + len(dropped) == 177

    def test_fixture_digest_is_the_gate_i_capture(self):
        """FC10 — the fixture's sorted-name sha256 is the drift detector's
        anchor; the coverage pin runs against exactly that catalog."""
        assert (
            sorted_names_sha256(load_catalog())
            == "0df212f44bc11cda84c1fb92171eef3c999436d03f978fff28fcb4f0b66e745a"
        )


# ── The pattern-level disposition ───────────────────────────────────────────


class TestPatternDisposition:
    def test_dead_patterns_dropped(self, classifier):
        for dead in ("add_", "edit_", "assign_"):
            assert dead not in classifier.read_tool_patterns
            assert dead not in classifier.write_tool_patterns

    def test_live_write_patterns_kept(self, classifier):
        assert "set_" in classifier.write_tool_patterns
        assert "remove_" in classifier.write_tool_patterns

    def test_read_additions(self, classifier):
        for added in ("retrieve_", "count_", "read_"):
            assert added in classifier.read_tool_patterns

    def test_write_additions(self, classifier):
        for added in (
            "manage_",
            "transfer_",
            "complete_",
            "link_",
            "import_",
            "attach_",
            "detach_",
            "upload_",
            "resolve_",
        ):
            assert added in classifier.write_tool_patterns

    def test_the_real_assignee_verb_is_classified_write(self, classifier):
        assert is_read_tool("plane_manage_work_item_assignee", classifier) is False
        assert any(
            "manage_work_item_assignee".startswith(p)
            for p in classifier.write_tool_patterns
        )


# ── The R18 set arithmetic ──────────────────────────────────────────────────


class TestR18SetArithmetic:
    """For a non-full-access agent the CR-3 strip keeps exactly the
    is_read_tool == True schemas. Prove: 48 → 73 (+25 restored, 0 removed)
    — the corrected read set is a STRICT SUPERSET of today's."""

    def test_kept_superset_and_counts(self, raw_names, classifier):
        today = _Patterns(TODAY_READ_PATTERNS, TODAY_WRITE_PATTERNS)
        kept_today, _ = _classify(today, raw_names)
        kept_fixed, dropped_fixed = _classify(classifier, raw_names)

        assert len(kept_today) == 48, (
            f"the frozen on-disk classifier must keep exactly 48 — got {len(kept_today)}"
        )
        assert len(kept_fixed) == 73
        assert len(dropped_fixed) == 104

        restored = kept_fixed - kept_today
        removed = kept_today - kept_fixed
        assert len(restored) == 25
        assert removed == set(), (
            f"the corrected read set DROPS verbs that were kept: {sorted(removed)}"
        )
        assert kept_today <= kept_fixed  # 48 ⊂ 73

        # The +25 is exactly retrieve_×23 + count_×1 + read_×1.
        by_prefix = {
            "retrieve_": [v for v in restored if v.startswith("retrieve_")],
            "count_": [v for v in restored if v.startswith("count_")],
            "read_": [v for v in restored if v.startswith("read_")],
        }
        assert {k: len(v) for k, v in by_prefix.items()} == {
            "retrieve_": 23,
            "count_": 1,
            "read_": 1,
        }
        assert len(by_prefix["retrieve_"]) + len(by_prefix["count_"]) + len(
            by_prefix["read_"]
        ) == 25

    def test_the_r18_trap_verbs_are_restored(self, raw_names, classifier):
        """The concrete R18 symptom: every ``retrieve_*`` verb was silently
        dropped from every non-full-access agent's schema list."""
        retrieves = [n for n in raw_names if n.startswith("retrieve_")]
        assert len(retrieves) == 23
        for raw in retrieves:
            assert is_read_tool(f"plane_{raw}", classifier) is True
        assert is_read_tool("plane_count_work_items", classifier) is True
        assert is_read_tool("plane_read_work_item_attachment", classifier) is True

    def test_sub_counts_match_the_reconciled_census(self, raw_names):
        """The drifted sub-counts, corrected: manage_ = 14, retrieve_ = 23."""
        assert len([n for n in raw_names if n.startswith("manage_")]) == 14
        assert len([n for n in raw_names if n.startswith("retrieve_")]) == 23
        for zero in ("add_", "edit_", "assign_"):
            assert [n for n in raw_names if n.startswith(zero)] == []

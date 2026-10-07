"""Slice ⑦ — OpenDesign MCP server retirement sentinel (CI guard).

After slice ⑦'s "retire the seam" work, the ``open-design-mcp`` stdio
MCP server (npm package ``open-design-mcp@0.16.1``) is deregistered
from the ensemble's MCP config surface. The runtime dependency on
the upstream package is gone — the OD mockup lane now ships as a
tier-1 native plugin family (REC §1.2 components 8/12/14) under
``plugins/opendesign/`` with four ``od.*`` Port tools and the
``opendesign.list_systems`` plugin-skill (slice ④).

This test is the post-⑦ CI guard that the retirement is durable:

  * **Zero ``open-design-mcp`` references in ``daemon/`` outside the
    license-attribution carve-out.** A re-registration of the
    upstream MCP server (deliberate or accidental) would re-introduce
    the dependency, regress the retirement, and silently break the
    parity-tested native-only chain. The scan catches that.

  * **Attribution carve-out** — the four remaining matches live in
    ``daemon/plugin_subsystem/opendesign/`` (port file headers +
    one vendored path comment) and are LOAD-BEARING for the
    Apache-2.0 license that the vendored OD resources are released
    under (4(a) "give any other recipients a copy of this License;
    and (c) Retain ... attribution notices from the Source form of
    the Work"). Removing them is a license violation, not a
    housekeeping nit. The carve-out is depth-bounded to exactly
    ``daemon/plugin_subsystem/opendesign/`` — a future attribution
    match outside that depth is a leak flag.

  * **No shrunken-attribution guard** — the carve-out files MUST
    still contain the attribution text, otherwise a future "tidy"
    pass silently strips it (vacuous-allowlist tripwire, mirroring
    the existing ``TestVocabularyConfinement::test_allowed_zones_actually_carry_the_vocabulary``
    pattern).

Walking is a single targeted rglob of ``daemon/`` (the affected
zone); plugins/, agents/, tests/, docs/ are out of scope for this
sentinel (those zones have their own sentinels + the Apache-2.0
attribution is license-required in vendored plugin trees).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # tests/unit/<file>.py → repo root
DAEMON_ROOT = REPO_ROOT / "daemon"

# The package name as it appears in source / comments. The sentinel
# matches the literal hyphenated string (``open-design-mcp``) — the
# sibling ``opendesign`` (no hyphen) is the new plugin's name and
# legitimately appears throughout the codebase.
RETIRED_PACKAGE_NEEDLE = "open-design-mcp"

# License-attribution carve-out. Depth-bounded to exactly
# ``daemon/plugin_subsystem/opendesign/`` (3 path parts). The vendored
# OD resources are released under Apache-2.0; the four header
# references there are license-required, not stray imports.
ATTRIBUTION_CARVEOUT_PARTS = ("daemon", "plugin_subsystem", "opendesign")

# Allowed attribution substrings — the vendored OD resources we
# legally carry. Pinning these (rather than a free-form carve-out)
# means a future accidental widening of the needle class still gets
# caught if the new match is in a different file.
ATTRIBUTION_REFERENCE_FILES = frozenset(
    {
        "daemon/plugin_subsystem/opendesign/__init__.py",
        "daemon/plugin_subsystem/opendesign/ports.py",
        "daemon/plugin_subsystem/opendesign/compose_brief.py",
    }
)


def _iter_daemon_python_text_files():
    """Walk ``daemon/`` for python files (the only zone that
    historically carried the retired package's runtime references)."""
    for path in DAEMON_ROOT.rglob("*.py"):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO_ROOT)
        yield relative, path


def _is_in_attribution_carveout(relative: Path) -> bool:
    return relative.parts[: len(ATTRIBUTION_CARVEOUT_PARTS)] == ATTRIBUTION_CARVEOUT_PARTS


class TestOpenDesignMcpRetirementSentinel:
    """Slice ⑦ — post-retirement CI guard for the open-design-mcp package."""

    def test_zero_references_outside_attribution_carveout(self):
        """The retired ``open-design-mcp`` package string is absent from
        every ``daemon/`` file outside the Apache-2.0 attribution
        carve-out. A hit is a re-registration regression (the
        parity-tested native-only chain would silently re-acquire a
        runtime dep on the npm package)."""
        violations = []
        for relative, path in _iter_daemon_python_text_files():
            if _is_in_attribution_carveout(relative):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover - unreadable file
                continue
            if RETIRED_PACKAGE_NEEDLE in text:
                violations.append(str(relative))
        assert violations == [], (
            "open-design-mcp reference re-introduced in daemon/ "
            "(slice ⑦ retired this package — re-registration is a "
            "regression; the OD mockup lane is now native-only via "
            "the plugin subsystem):\n" + "\n".join(violations)
        )

    def test_attribution_carveout_is_exactly_the_expected_files(self):
        """The carve-out is depth-bounded — pin the exact set of files
        that LEGITIMATELY carry the attribution. A future file added
        to ``daemon/plugin_subsystem/opendesign/`` that needs the
        attribution (legitimate) must extend this set deliberately,
        not silently widen the allowlist.
        """
        seen = set()
        for relative, path in _iter_daemon_python_text_files():
            if not _is_in_attribution_carveout(relative):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if RETIRED_PACKAGE_NEEDLE in text:
                seen.add(str(relative))
        assert seen == ATTRIBUTION_REFERENCE_FILES, (
            f"attribution carve-out drifted: expected "
            f"{sorted(ATTRIBUTION_REFERENCE_FILES)}, got {sorted(seen)}"
        )

    def test_attribution_files_still_carry_license_marker(self):
        """Vacuous-allowlist tripwire: the carve-out files MUST still
        contain an Apache-2.0 license reference. If a future "tidy"
        pass strips the attribution (or replaces the needle class),
        this test fires — a license-required comment is not
        housekeeping to remove.
        """
        for rel in sorted(ATTRIBUTION_REFERENCE_FILES):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "Apache-2.0" in text or "Apache 2.0" in text, (
                f"{rel}: attribution carve-out active but license marker "
                f"missing — vacuous-allowlist risk; either re-add the "
                f"attribution or remove the file from the carve-out set."
            )

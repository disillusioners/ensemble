"""Heading-presence check: the 7 FR-35 sections exist in the OAuth2 runbook.

Phase 6 T6.1 / AC-35 (mechanizable form per phase6-plan §4). The
runbook follows the live-views v-pattern (D16); FR-35 mandates seven
operator sections (a)-(g). §7 Troubleshooting is an additive section
beyond the seven and is asserted separately.
"""

from __future__ import annotations

from pathlib import Path

RUNBOOK = (
    Path(__file__).resolve().parents[2] / "docs" / "runbooks" / "oauth2-auth.md"
)

# (FR-35 letter, required heading fragment)
FR35_SECTIONS: list[tuple[str, str]] = [
    ("a", "Enable procedure"),
    ("b", "Default-OFF verification"),
    ("c", "Token mint"),
    ("d", "Rotation & maintenance"),
    ("e", "Upgrade & rollout"),
    ("f", "CORS notes"),
    ("g", "Cross-references"),
]


def test_runbook_exists() -> None:
    assert RUNBOOK.is_file(), f"runbook missing: {RUNBOOK}"


def test_runbook_has_all_seven_fr35_sections() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    headings = [
        line.strip() for line in text.splitlines() if line.startswith("## ")
    ]
    missing = [
        f"({letter}) {fragment}"
        for letter, fragment in FR35_SECTIONS
        if not any(fragment in h for h in headings)
    ]
    assert not missing, f"FR-35 sections missing from runbook: {missing}"


def test_runbook_has_troubleshooting_additive_section() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert any("Troubleshooting" in h for h in text.splitlines() if h.startswith("## "))

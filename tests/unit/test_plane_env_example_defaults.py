"""P30 — the ``.env.example`` no-active-default static check.

Lane ``plane-pm-issue-tools`` Phase 4 (``phase4-plan.md`` §2.4 P4-A / G2 fold
NB6 + NB16 / §5 pin **P30** / §6 exit criteria **X4.9 + X4.11**).

The hazard being pinned (NB16, prod-safety, pre-existing and disclosed):
``.env.example`` shipped **ACTIVE** placeholder Plane lines —
``PLANE_MCP_URL=<real production MCP endpoint>``,
``PLANE_MCP_API_KEY=plane_api_your_api_key_here`` and
``PLANE_MCP_WORKSPACE_SLUG=your-workspace-slug``. The builtin Plane MCP
availability gate is **presence-only** (``daemon/mcp/builtin_servers/plane.py``
``is_available()``), so a fresh install that copies ``.env.example`` verbatim
registers a ``plane`` row against the **real production MCP endpoint** with a
**placeholder bearer** — the placeholder is indistinguishable from a
configured credential to the daemon. And under the adjudicated **ADJ-1
live-exempt** rule (live permits **any configured slug**), an active
``e2e`` default would be accepted **on live too**, silently pointing fresh
installs at the TEST workspace. The remediation: **all Plane sample lines are
COMMENTED OUT** and the doctrine prose carries the per-environment rule
(live requires ``nea``; non-live requires ``e2e`` when armed).

Also pinned here (P4-A item 1): the pre-Phase-2 doctrine sentence *"MCP
silently targets the account-default (prod) workspace"* is **stale and must
stay gone** — after Phase 2 the MCP lane **fails closed** on a non-live env
with an unset slug (typed reason, no row, no tools), which is pollution path
P2 closed by E3. An operator reading the old sentence would believe an unset
slug is harmless on dev when it was exactly the prod-pollution path.

RED baseline leg (X4.11): against the pre-Phase-4 file (active ``:207-209``
assignments + the stale sentence) every repo-state assertion below goes RED —
captured in the lane report BEFORE the edit, never fabricated after the fact.
The in-test mutation legs (tmp copies with an active line restored, or the
stale sentence reintroduced) keep the RED side of the check alive forever.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = _REPO_ROOT / ".env.example"

# Any ACTIVE (uncommented) PLANE_* assignment, anywhere in the file. X4.9
# demands "no active PLANE_MCP_WORKSPACE_SLUG= assignment anywhere in the
# file"; P30/NB16 extends it to the whole Plane block — this pattern covers
# both (PLANE_MCP_URL / PLANE_MCP_API_KEY / PLANE_MCP_WORKSPACE_SLUG /
# PLANE_BASE_URL / PLANE_API_KEY / any future PLANE_* name).
_ACTIVE_PLANE_ASSIGNMENT = re.compile(r"^PLANE_[A-Z0-9_]+=", re.MULTILINE)

# The required COMMENTED sample (G2 fold NB6 binding form) with the
# per-environment doctrine in the adjacent comment.
_COMMENTED_E2E_SAMPLE = "#PLANE_MCP_WORKSPACE_SLUG=e2e"

# Doctrine phrases the corrected prose MUST carry (P4-A + NB6).
_REQUIRED_PHRASES = (
    # live ⇒ nea; non-live ⇒ e2e when armed
    "live requires `nea`",
    "non-live (dev/demo/sandbox) requires `e2e` when armed",
    # the corrected daemon-layer behaviour: unset on non-live ⇒ refused
    "refused",
)

# The stale pre-Phase-2 sentence that must NEVER come back (P4-A item 1).
_STALE_SENTENCE = "silently targets the account-default"


def scan_env_example(text: str) -> list[str]:
    """Return the list of P30 violations in ``text`` (empty ⇒ compliant).

    Pure function over the file content so the mutation legs can drive it
    with tmp copies — the repo-state test drives it with the real file.
    """
    violations: list[str] = []

    for match in _ACTIVE_PLANE_ASSIGNMENT.finditer(text):
        violations.append(f"active PLANE_* assignment: {match.group(0)[:-1]}=")

    if _COMMENTED_E2E_SAMPLE not in text:
        violations.append(
            f"missing commented sample: {_COMMENTED_E2E_SAMPLE!r}"
        )

    lowered = text.lower()
    for phrase in _REQUIRED_PHRASES:
        if phrase.lower() not in lowered:
            violations.append(f"doctrine prose missing phrase: {phrase!r}")

    if _STALE_SENTENCE in lowered:
        violations.append(
            f"stale pre-Phase-2 sentence present: {_STALE_SENTENCE!r} "
            "(post-Phase-2 the MCP lane fails closed on non-live, "
            "unset slug ⇒ typed refusal, no row, no tools)"
        )

    return violations


class TestPlaneEnvExampleDefaults:
    """P30 — repo-state assertions (all RED against the pre-Phase-4 file)."""

    def test_no_active_plane_assignment_and_doctrine_present(self):
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        violations = scan_env_example(text)
        assert violations == [], (
            "P30 violations in .env.example:\n  - " + "\n  - ".join(violations)
        )

    def test_kill_switch_comment_stays(self):
        """NR-side pin: the PLANE_MCP_ENABLED kill-switch doc line stays.

        Phase 4 §2.4 requires the kill-switch comments at the (former)
        ``:444-445`` block kept intact — ``PLANE_MCP_ENABLED=false`` is a
        *deliberate* off (the Phase-3 observability layer distinguishes it
        from an unexpected skip), so the comment must survive this phase's
        edit of the same file.
        """
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert "# PLANE_MCP_ENABLED" in text, (
            "the PLANE_MCP_ENABLED kill-switch comment line was removed"
        )


class TestP30MutationLegs:
    """RED legs on tmp copies — restore an active line ⇒ violations ≠ [].

    These keep the check's RED side provable forever (house discipline:
    a pin that cannot go RED is vacuous).
    """

    @staticmethod
    def _compliant_text() -> str:
        """A minimal compliant block (the mandated end-state shape)."""
        return (
            "# Plane workspaces: live requires `nea`; non-live "
            "(dev/demo/sandbox) requires `e2e` when armed. Unset on "
            "non-live is refused (typed reason, no row, no tools).\n"
            "#PLANE_MCP_URL=https://mcp.example.invalid/plane/mcp\n"
            "#PLANE_MCP_API_KEY=plane_api_your_api_key_here\n"
            "#PLANE_MCP_WORKSPACE_SLUG=e2e\n"
        )

    def test_compliant_block_is_green(self):
        """Positive control — the mandated end-state shape passes clean."""
        assert scan_env_example(self._compliant_text()) == []

    def test_active_slug_restored_goes_red(self):
        """X4.9's mutation: an active PLANE_MCP_WORKSPACE_SLUG assignment."""
        text = self._compliant_text().replace(
            "#PLANE_MCP_WORKSPACE_SLUG=e2e", "PLANE_MCP_WORKSPACE_SLUG=e2e"
        )
        violations = scan_env_example(text)
        assert any(
            "active PLANE_* assignment: PLANE_MCP_WORKSPACE_SLUG=" in v
            for v in violations
        ), f"expected the active-slug violation, got: {violations}"

    def test_active_api_key_restored_goes_red(self):
        """NB16's mutation: the placeholder bearer becomes active again."""
        text = self._compliant_text().replace(
            "#PLANE_MCP_API_KEY=plane_api_your_api_key_here",
            "PLANE_MCP_API_KEY=plane_api_your_api_key_here",
        )
        violations = scan_env_example(text)
        assert any(
            "active PLANE_* assignment: PLANE_MCP_API_KEY=" in v
            for v in violations
        ), f"expected the active-key violation, got: {violations}"

    def test_stale_sentence_reintroduced_goes_red(self):
        """P4-A item 1's mutation: the pre-Phase-2 lie returns."""
        text = (
            self._compliant_text()
            + "# MCP silently targets the account-default (prod) workspace.\n"
        )
        violations = scan_env_example(text)
        assert any(
            "stale pre-Phase-2 sentence present" in v for v in violations
        ), f"expected the stale-sentence violation, got: {violations}"

    def test_missing_commented_sample_goes_red(self):
        """NB6's mutation: the commented e2e sample line is dropped."""
        text = self._compliant_text().replace(
            "#PLANE_MCP_WORKSPACE_SLUG=e2e\n", ""
        )
        violations = scan_env_example(text)
        assert any(
            "missing commented sample" in v for v in violations
        ), f"expected the missing-sample violation, got: {violations}"
